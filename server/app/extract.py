import base64
import os

from .schemas import Extraction

# Provider switch: "anthropic" (direct API key) or "bedrock" (AWS creds).
# Set RUNWAY_PROVIDER=bedrock to route through Amazon Bedrock — nothing else
# in the app changes.
PROVIDER = os.getenv("RUNWAY_PROVIDER", "anthropic").lower()

if PROVIDER == "bedrock":
    from anthropic import AnthropicBedrockMantle

    client = AnthropicBedrockMantle(aws_region=os.getenv("AWS_REGION", "us-east-1"))
    MODEL = "anthropic.claude-opus-4-8"  # Bedrock model IDs take the anthropic. prefix
else:
    from anthropic import Anthropic

    client = Anthropic()
    MODEL = "claude-opus-4-8"

SYSTEM = (
    "You are a contract-ingestion assistant for a GovCon post-award financial "
    "tracking tool. Extract the structured award data exactly as written in the "
    "document: the contract header, every period of performance, and the full CLIN "
    "schedule. Use null for any field not present in the document — never invent or "
    "estimate values. Money is in US dollars as a number (no '$' or commas)."
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
    return resp.parsed_output


def extract_from_text(text: str) -> Extraction:
    return _parse(f"{INSTRUCTION}\n\n<document>\n{text}\n</document>")


def extract_from_pdf(pdf_bytes: bytes) -> Extraction:
    b64 = base64.standard_b64encode(pdf_bytes).decode()
    return _parse(
        [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
            },
            {"type": "text", "text": INSTRUCTION},
        ]
    )
