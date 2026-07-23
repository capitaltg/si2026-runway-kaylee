import base64
import os

from . import confidence
from .schemas import Extraction

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
    "schedule. Use null for any field not present in the document — never invent or "
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


def _parse(content) -> Extraction:
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=Extraction,
    )
    parsed = resp.parsed_output
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
