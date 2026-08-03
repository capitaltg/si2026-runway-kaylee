import base64
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from . import confidence
from .schemas import Extraction, Modification

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
    "Use null for any field not present in the document — never invent or "
    "estimate values. Money is in US dollars as a number (no '$' or commas). "
    "For every field, also report your extraction confidence as a 0.0-1.0 number. "
    "You MUST populate `field_confidence` on the contract header with one entry per "
    "field you filled, using exactly these keys where present: piid, agency, "
    "contractor, contract_type, total_ceiling, total_obligated, effective_date, "
    "contracting_officer. Also set `confidence` on every CLIN. Assess each field "
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
    "Money is a number in US dollars (no '$' or commas). Use null for anything "
    "the document does not state — never invent or estimate."
)

INSTRUCTION_MOD = (
    "Extract the single funding action recorded on this SF-30 contract "
    "modification: which contract (PIID) it modifies, the mod number, effective "
    "date, dollars obligated by this action, and the resulting cumulative "
    "obligated."
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
    Callers pass this when they already know the grammar won't compile (the award
    `Extraction` schema on Bedrock), so we don't pay the full grammar-compilation
    *timeout* on every ingest just to prove a call that always fails. Providers
    that *can* enforce the schema keep `constrained=True` and still do — the
    smaller `Modification` schema compiles on Bedrock, as does everything on the
    RUNWAY_PROVIDER=anthropic path.
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


def _parse(content) -> Extraction:
    # 16000, not 8000: adaptive thinking is on by default on current models and
    # max_tokens caps thinking plus response text together, so a budget sized for
    # the JSON alone can truncate a large award mid-object.
    #
    # constrained only on providers that can compile the Extraction grammar. On
    # Bedrock it never compiles, so the parse attempt is a guaranteed
    # grammar-compilation timeout on every award ingest — skip straight to plain
    # JSON and save that dead wait.
    parsed = _parse_schema(
        content, SYSTEM, Extraction, 16000, constrained=(PROVIDER != "bedrock")
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
    return _parse_schema(content, SYSTEM_MOD, Modification, 8000)


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
