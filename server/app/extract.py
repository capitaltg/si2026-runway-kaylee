import base64
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from . import confidence
from .schemas import Extraction, Modification, RateAgreement

# Load the server's dotenv files before any credential lookup. Without this the
# app only saw credentials that happened to be exported in the shell that
# launched it, so a server started from a fresh terminal 502'd the ingest routes
# with "could not resolve credentials from session" while the keys sat unread in
# server/.env.local.
#
# Precedence, highest first (override=False means the first value loaded wins,
# so anything already in the real environment beats both files):
#   1. the process environment  — CI, one-off `VAR=... uvicorn ...`
#   2. .env.local               — machine-specific secrets, gitignored
#   3. .env                     — shared defaults, gitignored
_SERVER_DIR = Path(__file__).resolve().parents[1]
for _env_file in (".env.local", ".env"):
    load_dotenv(_SERVER_DIR / _env_file, override=False)

# Provider switch: "bedrock" (default — classic AWS credentials) or "anthropic"
# (direct API key). Set RUNWAY_PROVIDER=anthropic to route through the Anthropic
# API instead — nothing else in the app changes.
PROVIDER = os.getenv("RUNWAY_PROVIDER", "bedrock").lower()

if PROVIDER == "bedrock":
    from anthropic import AnthropicBedrock

    # Classic AWS credentials: standard access key / secret / session token, or a
    # named profile (AWS_PROFILE). Falls back to the default boto3 credential
    # chain (env, shared config, instance/role) when these aren't set.
    client = AnthropicBedrock(
        aws_region=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN"),
        aws_profile=os.getenv("AWS_PROFILE"),
    )
    # Standard Bedrock inference-profile ID. The Opus models are not subscribed
    # on this account's Bedrock (Marketplace access denied); Sonnet 4.5 is the
    # strongest model that actually works here. Override via env if needed.
    MODEL = os.getenv(
        "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    )
else:
    from anthropic import Anthropic

    client = Anthropic()
    MODEL = "claude-opus-4-8"

SYSTEM = (
    "You are a contract-ingestion assistant for a GovCon post-award financial "
    "tracking tool. Extract the structured award data exactly as written in the "
    "document: the contract header, every period of performance, and the full CLIN "
    "schedule. When a labor CLIN prints a Labor Rate Table (LCAT, loaded/burdened "
    "rate per hour, estimated hours, qualifications), capture it in that CLIN's "
    "`labor_rates`; leave it null when the document prints no such table. "
    "When the award or an SF-30 prints an ACCOUNTING AND APPROPRIATION DATA / ACRN "
    "block, capture each CLIN's obligated amount into that CLIN's `obligated` and "
    "its Accounting Classification Reference Number into `acrn`. This funded amount "
    "is distinct from the CLIN's not-to-exceed `ceiling` — do not copy one into the "
    "other, and leave `obligated` null when only a ceiling is printed. "
    "A CLIN can be cited on more than one line of that block, because real awards "
    "routinely fund one line item from two appropriations. That is still ONE CLIN: "
    "sum every row's dollars into that CLIN's `obligated`, and record all of the "
    "citations in `acrn` comma-separated, e.g. 'AA, AB'. Never emit the same CLIN "
    "twice, and never report one row's dollars as if they were the line's total. "
    "A CLIN the accounting block *does* name but shows no money against — $0, "
    "$0.00, 0, a dash or a blank in the obligated column — is obligated 0.0, not "
    "null: an unfunded line on a funded award is a real, reportable state, and "
    "null means only 'the document does not say'. Reserve null for CLINs the "
    "accounting block never mentions. "
    "A cost-reimbursement or incentive CLIN does not price as one number. Its "
    "Section B exhibit states cost and fee as separate priced lines footing to the "
    "CLIN total, and each goes in its own field: 'Total Estimated Cost' or 'Target "
    "Cost' -> `estimated_cost`; 'Fixed Fee' -> `fixed_fee`; 'Base Fee' -> `base_fee` "
    "with 'Award Fee Pool' -> `award_fee_pool`; 'Target Fee' -> `target_fee` with "
    "'Minimum Fee'/'Maximum Fee' -> `min_fee`/`max_fee`; 'Target Profit' -> "
    "`target_profit` with 'Price Ceiling' -> `ceiling_price`. A Government/Contractor "
    "share ratio goes in `share_ratio` as printed ('50/50'). Take the DOLLAR figure, "
    "never the percentage a label may show in parentheses. `ceiling` remains the "
    "CLIN's total not-to-exceed amount (cost plus fee) — never put a fee figure in "
    "it, never subtract fee from it, and never add cost and fee together yourself to "
    "produce one. A firm-fixed-price CLIN states a single price: put it in `ceiling` "
    "and leave every cost and fee field null. "
    "When the award prints an indirect-rate disclosure on its face — 'Indirect "
    "Rates: Fringe X% | Overhead Y% | G&A Z%' or similar — capture the three as "
    "DECIMAL FRACTIONS on the header: `indirect_fringe`, `indirect_overhead`, "
    "`indirect_gna` (32.5% is 0.325, not 32.5). Leave them null when no such line is "
    "printed; do not derive them from a rate table. "
    "On a cost-buildup exhibit that prints an unburdened 'Direct Rate/Hr' column "
    "alongside the labor categories, capture it as each labor line's `direct_rate`, "
    "keeping `loaded_rate` for a fully-burdened billing rate. A sheet printing only "
    "one rate per category is printing the loaded rate — leave `direct_rate` null "
    "rather than guessing which one it is. "
    "A period's `exercised` flag is read from what the document marks, not from "
    "what an award form is normally able to say. Award documents annotate the "
    "periods NOT yet in effect — '(option not exercised)', 'unexercised', "
    "'reserved' or similar against that period in the schedule — and print the "
    "ones already in effect plain. So set exercised=true for the base period and "
    "for every option period the document does not mark as un-exercised, even "
    "when the award's own signature date predates that option: you are reading a "
    "document that may have been reissued or conformed mid-performance, and a "
    "period whose dates have already begun is in effect. Set exercised=false only "
    "where the document says so. "
    "Use null for any field not present in the document — never invent or "
    "estimate values. Money is in US dollars as a number (no '$' or commas). "
    "For every field, also report your extraction confidence as a 0.0-1.0 number. "
    "You MUST populate `field_confidence` on the contract header with one entry per "
    "field you filled, using exactly these keys where present: piid, agency, "
    "contractor, contract_type, total_ceiling, total_obligated, total_estimated_cost, "
    "total_fee, effective_date, contracting_officer. Also set `confidence` on every "
    "CLIN, and never set `confidence_note` — the server owns that field. Assess each field "
    "independently and do NOT return the same value for everything: rate a field near "
    "0.97-1.0 only when it is printed plainly in one place, drop toward 0.85 when it "
    "is legible but formatted awkwardly, and below 0.80 when the value spans a page "
    "break, is handwritten/stamped, or had to be pieced together across sections."
)

INSTRUCTION = (
    "Extract the contract header, periods of performance, and CLIN schedule from "
    "this federal award document (e.g. an SF-26 / SF-1449)."
)

# Modification (SF-30) extraction. A mod documents one dated funding action
# against an existing contract; the money and cumulative live in the block-14
# narrative ("increased by $X, from $prev to $cumulative"), the PIID in block
# 10A, the mod number in block 2. Extract exactly one action per document.
SYSTEM_MOD = (
    "You are a contract-modification ingestion assistant for a GovCon financial "
    "tracking tool. You are given ONE modification document — a Standard Form 30 "
    "(SF-30, 'Amendment of Solicitation / Modification of Contract'). Extract the "
    "single funding action it records, exactly as written. The contract number "
    "being modified is in block 10A; the modification number (e.g. P00001) is in "
    "block 2; the effective date is stated on the form. The dollar figures live "
    "in the block-14 description narrative — capture the amount obligated BY THIS "
    "action, the resulting CUMULATIVE total obligated, and (if the 'increased "
    "from $X to $Y' phrasing is present) the previous cumulative. Classify the "
    "action_type from the narrative: 'incremental_funding' (FAR 52.232-22 "
    "Limitation of Funds), 'option_exercise' (FAR 52.217-9), or 'administrative'. "
    "Capture the per-CLIN breakout of this action's money into `funding_lines`: "
    "one entry per CLIN named, with its ACRN and the dollars obligated to that "
    "CLIN by this action. It is printed in the ACCOUNTING AND APPROPRIATION DATA "
    "block and usually restated in the narrative ('funds are obligated by CLIN as "
    "follows: CLIN 1001 (ACRN AB) $...'); the two say the same thing, so read "
    "either and do not list a CLIN twice. Each line's amount is this action's "
    "increment for that CLIN, not a running total — the lines should sum to "
    "amount_obligated. Leave `funding_lines` null when the mod states only a "
    "contract-level figure. "
    "When action_type is 'option_exercise', put the period the mod brings into "
    "effect in `period_exercised`, named as the document names it (e.g. 'Option "
    "Year 1'); leave it null otherwise. "
    "Money is a number in US dollars (no '$' or commas). Use null for anything "
    "the document does not state — never invent or estimate."
)

INSTRUCTION_MOD = (
    "Extract the single funding action recorded on this SF-30 contract "
    "modification: which contract (PIID) it modifies, the mod number, effective "
    "date, dollars obligated by this action (in total and broken out by CLIN), "
    "the resulting cumulative obligated, and — if it exercises an option — which "
    "period of performance it brings into effect."
)


def _parse_schema(
    content, system: str, output_format, max_tokens: int, constrained=True
):
    """Extract `output_format` from `content`, enforcing the schema whichever way
    the provider supports.

    Preferred path is constrained decoding (`messages.parse`), where the schema is
    enforced during generation. Bedrock refuses to compile this app's `Extraction`
    grammar and answers `400 Grammar compilation timed out` — the CLIN ->
    labor_rates nesting plus the free-form `field_confidence` map exceed what its
    decoder will build, on every model this account can reach (the Opus tier,
    which might manage it, is Marketplace-denied). So on that specific failure,
    ask for plain JSON against the same schema and validate it here instead.

    The guarantee moves from the decoder to `model_validate_json`, so a malformed
    response still raises rather than returning half-populated data.

    `constrained=False` skips the parse attempt and goes straight to plain JSON.
    Callers pass this when they already know the grammar won't compile, so we don't
    pay the full grammar-compilation *timeout* on every ingest just to prove a call
    that always fails. On Bedrock that is now *both* schemas: `Modification` was
    flat enough to compile until `funding_lines` gave it a nested object list too.
    Everything on the RUNWAY_PROVIDER=anthropic path still enforces its schema in
    the decoder.

    Treat this as the rule rather than two special cases: any nested object list
    added to a schema here costs a dead grammar-compilation timeout per call on
    Bedrock until its caller passes constrained=False.
    """
    if constrained:
        try:
            resp = client.messages.parse(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=output_format,
            )
            return resp.parsed_output
        except Exception as e:
            if "grammar" not in str(e).lower():
                raise

    blocks = (
        content if isinstance(content, list) else [{"type": "text", "text": content}]
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[
            {
                "role": "user",
                "content": blocks
                + [
                    {
                        "type": "text",
                        "text": "Return ONLY a JSON object conforming to this JSON "
                        "Schema. No prose and no code fence.\n\n"
                        + json.dumps(output_format.model_json_schema()),
                    }
                ],
            }
        ],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    # Strip a ``` / ```json fence if the model added one despite the instruction.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    return output_format.model_validate_json(text)


def normalize_obligations(parsed: Extraction) -> Extraction:
    """Coerce "named in the accounting block, no dollars printed" to 0.0.

    A CLIN carries an `acrn` only because the Accounting and Appropriation Data
    block named it, so an ACRN with a null `obligated` is a line the award funded
    at zero — not a line the award is silent about. The distinction is not
    cosmetic: `burn.py` treats a CLIN with any figure of its own as attributed,
    and only uses the by-name figures directly when *every* active CLIN has one.
    A single null drops the whole period onto the header pro-rata path, so one
    dropped zero changes how every other CLIN's funding is read.

    Deterministic on purpose. The SYSTEM prompt asks for the same thing, but a
    zero is the easiest value for a model to read as "nothing to report", and
    this is cheap to guarantee here rather than hope for per call.
    """
    for clin in parsed.clins:
        if clin.acrn and clin.obligated is None:
            clin.obligated = 0.0
    return parsed


def _parse(content) -> Extraction:
    # 16000, not 8000: adaptive thinking is on by default on current models and
    # max_tokens caps thinking plus response text together, so a budget sized for
    # the JSON alone can truncate a large award mid-object.
    #
    # constrained only on providers that can compile the Extraction grammar. On
    # Bedrock it never compiles, so the parse attempt is a guaranteed
    # grammar-compilation timeout on every award ingest — skip straight to plain
    # JSON and save that dead wait.
    parsed = normalize_obligations(
        _parse_schema(
            content, SYSTEM, Extraction, 16000, constrained=(PROVIDER != "bedrock")
        )
    )
    try:
        return confidence.apply(parsed)
    except Exception:
        # Confidence scoring is best-effort — never let it break an otherwise
        # good extraction.
        return parsed


def extract_from_text(text: str) -> Extraction:
    return _parse(f"{INSTRUCTION}\n\n<document>\n{text}\n</document>")


def extract_from_pdf(pdf_bytes: bytes) -> Extraction:
    b64 = base64.standard_b64encode(pdf_bytes).decode()
    return _parse(
        [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64,
                },
            },
            {"type": "text", "text": INSTRUCTION},
        ]
    )


def _parse_mod(content) -> Modification:
    # 8000, not 2000: see the max_tokens note in _parse — thinking shares the
    # budget, and 2000 leaves little room for it plus the JSON.
    #
    # Unconstrained on Bedrock, same as _parse and for the same reason. Modification
    # used to be flat enough to compile there, so it asked for constrained decoding
    # and got it; `funding_lines` made it a schema with a nested object list — the
    # shape Bedrock's decoder won't build (see _parse_schema) — so the parse attempt
    # became a guaranteed grammar-compilation timeout paid before every single mod
    # ingest, then thrown away for the plain-JSON fallback that always answers.
    return _parse_schema(
        content, SYSTEM_MOD, Modification, 8000, constrained=(PROVIDER != "bedrock")
    )


def extract_mod_from_text(text: str) -> Modification:
    return _parse_mod(f"{INSTRUCTION_MOD}\n\n<document>\n{text}\n</document>")


def extract_mod_from_pdf(pdf_bytes: bytes) -> Modification:
    b64 = base64.standard_b64encode(pdf_bytes).decode()
    return _parse_mod(
        [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64,
                },
            },
            {"type": "text", "text": INSTRUCTION_MOD},
        ]
    )


# Indirect rate agreement (#78 slice 3b). The document that states what a burden
# percentage actually is: an FPRA (FAR 15.407-3), a provisional billing rate letter
# (FAR 42.704), or a final determination (FAR 42.705). Unlike the award face — which
# prints the three percentages and nothing else — this one carries each pool's
# application base and whether the rates are provisional or settled, which is why it
# exists as a separate upload at all.
SYSTEM_RATE_AGREEMENT = (
    "You are a rate-agreement ingestion assistant for a GovCon financial tracking "
    "tool. You are given ONE indirect rate agreement: a Forward Pricing Rate "
    "Agreement (FAR 15.407-3), a provisional billing rate letter (FAR 42.704), or a "
    "final rate determination (FAR 42.705). Extract the indirect cost pools it "
    "states, exactly as written. "
    "Each pool has three parts and all three matter: which pool it is (fringe, "
    "overhead or G&A), its rate, and the base the rate applies to. Normalise the pool "
    "to exactly 'fringe', 'overhead' or 'gna', and the base to exactly "
    "'direct_labor', 'labor_plus_fringe' or 'total_cost_input'. A rate is a DECIMAL "
    "FRACTION: 32.5% is 0.325, never 32.5. "
    "A letter often states TWO sets of rates for the same fiscal year — the "
    "provisional rates billed during the year and the final rates determined "
    "afterwards. When it does, put the provisional set in `pools` with "
    "status='provisional' and the determined set in `final_pools`. When it states "
    "only one set, put it in `pools`, set `status` to whichever it is, and leave "
    "`final_pools` null. Do not copy one set into both. "
    "If the letter prints a variance or change column between the two sets, ignore "
    "it — it is derived from the two rates, and reading it as a rate would record a "
    "delta as though it were a burden. "
    "Use null for anything the document does not state — never invent or estimate a "
    "rate, a base or a fiscal year."
)

INSTRUCTION_RATE_AGREEMENT = (
    "Extract the indirect cost pools from this rate agreement: the fiscal year, "
    "whether the rates are provisional or final, the cognisant agency, and each "
    "pool's rate and application base."
)


def _parse_rate_agreement(content) -> RateAgreement:
    # Unconstrained on Bedrock for the reason in _parse_schema: `pools` is a nested
    # object list, so the grammar never compiles and the attempt is a dead timeout
    # paid before every upload.
    return _parse_schema(
        content,
        SYSTEM_RATE_AGREEMENT,
        RateAgreement,
        8000,
        constrained=(PROVIDER != "bedrock"),
    )


def extract_rate_agreement_from_text(text: str) -> RateAgreement:
    return _parse_rate_agreement(
        f"{INSTRUCTION_RATE_AGREEMENT}\n\n<document>\n{text}\n</document>"
    )


def extract_rate_agreement_from_pdf(pdf_bytes: bytes) -> RateAgreement:
    b64 = base64.standard_b64encode(pdf_bytes).decode()
    return _parse_rate_agreement(
        [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64,
                },
            },
            {"type": "text", "text": INSTRUCTION_RATE_AGREEMENT},
        ]
    )
