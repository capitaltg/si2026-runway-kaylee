"""Ask Runway (#15) — natural-language Q&A grounded in the burn engine's numbers.

The math already lives in burn.py; this layer never recomputes it. It gathers the
same figures the Flight Deck and Portfolio views render — the portfolio aggregate
plus every contract's full burn payload and funding history — hands them to the
model as read-only context, and streams back a plain-language answer. The model is
told to answer ONLY from those numbers (simple arithmetic on them is fine) so a
financial figure can always be traced back to what's on screen.

Reuses extract.py's provider-configured `client` (Bedrock or direct Anthropic,
creds already loaded from .env.local) rather than wiring a second one. Answers run
on Haiku 4.5 for a snappy chat feel — a zero-code model swap from ingest's Sonnet.
"""

import json
import os

from . import burn, db
from .extract import PROVIDER, client

# Fast chat model. Ingest uses Sonnet for careful extraction; a Q&A over
# already-computed numbers wants latency over raw strength, so default to Haiku.
# Opus is Marketplace-denied on this account's Bedrock — don't route here.
if PROVIDER == "bedrock":
    ASK_MODEL = os.getenv(
        "BEDROCK_ASK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    )
else:
    ASK_MODEL = os.getenv("RUNWAY_ASK_MODEL", "claude-haiku-4-5")

SYSTEM_ASK = (
    "You are Runway's financial analyst assistant for GovCon post-award contract "
    "tracking. Answer the user's question about their contract burn, runway, and "
    "funding using ONLY the numbers in the <data> block below.\n\n"
    "How to answer:\n"
    "- Talk like a helpful colleague looking at the numbers with them — warm, "
    "natural, conversational. Write in plain flowing sentences, not a report.\n"
    "- Do NOT use any markdown formatting: no ** for bold, no # headings, no "
    "backticks, no bullet-point asterisks. If you mention a few things, keep them "
    "in sentences (a plain dash for a short aside is fine).\n"
    "- Every figure you state must come from <data>. Simple arithmetic on those "
    "numbers (sums, differences, ratios, weeks-to-days) is fine; never invent or "
    "estimate a value that isn't there. If <data> lacks what's needed, just say so "
    "and point to what you do have.\n"
    "- Naturally work in which CLIN or contract a number comes from so it's easy to "
    "trace (e.g. 'CLIN 0001 has about six weeks of runway left').\n"
    "- Money is US dollars; say large figures readably ($1.2M, $450K) and round "
    "weeks and days sensibly.\n"
    "- Keep it short and lead with the answer — usually a sentence or two, no "
    "preamble.\n\n"
    "Data shape: `portfolio` is the cross-contract aggregate and per-contract "
    "summary cards. `contracts` holds each contract's full burn payload — per-CLIN "
    "spend/runway/status, tripwires (over ceiling), underburn (too slow), funding "
    "(funded slice runs out early but ceiling holds), and its obligation history. "
    "`focused_contract_id` is the contract the user currently has open; resolve "
    "'this contract' / 'here' to it. Status meanings: over=projected to blow its "
    "budget before PoP end; watch=close; funding=incremental funding due, not a "
    "breach; under=burning too slowly; ok=on pace; paused=no recent charges.\n\n"
    "Answering 'when does charging stop / when do we run out': each CLIN carries "
    "`stop_date`, the projected calendar date its spend reaches the binding budget "
    "at the current pace — use it directly rather than adding `runway_days` to "
    "today, since the clock is anchored to the latest synced timesheet week, not to "
    "today. `stop_reason` says which limit produces it: funding=the obligated money "
    "runs out (a mod is needed) or ceiling=the contract's own ceiling. If "
    "`stop_date_passed` is true the binding budget is already spent through, so the "
    "money is gone now — say charging should stop today and cite the date it ran "
    "out, never as though it were still ahead. That flag is a spend fact, not a "
    "calendar one: check `sync.as_of` and `sync.data_age_days`, because on a "
    "contract that has not synced recently `stop_date` can be behind us with the "
    "flag still false. Never present a date that has already passed as a future "
    "deadline — say the projection is measured as of that timesheet week. Both are "
    "null on a paused/unpriced "
    "CLIN and on non-labor CLINs (no pace to project). Hedge the date ('around "
    "Mar 14') — it's a projection off a 4-week trailing pace. Runway never blocks a "
    "charge; that hard stop lives in the accounting system, so this is the date to "
    "act before, not an enforcement action."
)


def build_grounding(focused_id=None) -> dict:
    """Assemble the read-only numeric context for a question.

    Complete by construction: the portfolio aggregate plus every contract's full
    burn payload and dated obligation history. With a handful of contracts this is
    a few KB, so cross-contract questions ('which contracts are at risk?') and
    drill-downs ('runway on contract 9's CLIN 0001?') both answer from the same
    context with no per-question data fetch or tool-use round trip.
    """
    contracts = db.list_contracts()
    # The cost model travels with each contract for the same reason it does on the
    # portfolio endpoint: without it every figure here is a Level-1 read, so Ask
    # Runway would answer with dollars the Flight Deck does not show.
    pairs = [
        (
            c,
            db.get_timesheets(c["id"]),
            db.list_expenses(c["id"]),
            db.get_rate_schedule(c["id"]),
        )
        for c in contracts
    ]
    portfolio = burn.portfolio(pairs)

    detail = []
    for c, rows, expenses, cost_model in pairs:
        b = burn.compute(c, rows, expenses, cost_model)
        b["obligation_history"] = c.get("obligation_history") or []
        detail.append(b)

    return {
        "focused_contract_id": focused_id,
        "portfolio": portfolio,
        "contracts": detail,
    }


def _messages(question: str, history=None):
    """Prior turns plus the new question, with the grounding data pinned to the
    latest user turn so the model always answers against current numbers."""
    msgs = []
    for turn in history or []:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": question})
    return msgs


def stream_answer(question: str, history=None, focused_id=None):
    """Yield the answer text in chunks as the model generates it.

    The grounding block is appended to the system prompt (not the user message) so
    it stays out of the visible conversation and isn't echoed back in history.
    """
    grounding = build_grounding(focused_id)
    system = (
        SYSTEM_ASK + "\n\n<data>\n" + json.dumps(grounding, default=str) + "\n</data>"
    )
    with client.messages.stream(
        model=ASK_MODEL,
        max_tokens=1024,
        system=system,
        messages=_messages(question, history),
    ) as stream:
        for text in stream.text_stream:
            yield text
