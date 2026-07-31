"""Runway Drafts (v1) — stream the *prose* for a generated GovCon document.

The Drafts view builds each document's numbers/dates/IDs deterministically on the
client from the burn payload; this endpoint only writes the narrative sentences.
It reuses ask.build_grounding (the same portfolio + per-contract burn context Ask
Runway uses) and the provider-configured client/model, and is told never to emit
dollar figures so a number can never come from the model.
"""

import json

from . import ask

DRAFT_DOC_TYPES = {"funding", "invoice", "cdrl"}

# What narrative each document needs. Numbers are filled on the client, so the
# model is told to write words only.
_DOC_GUIDANCE = {
    "funding": (
        "an incremental-funding request memo to the contracting officer: write the "
        "justification narrative — why continued funding is needed and the impact of "
        "a lapse."
    ),
    "invoice": (
        "an SF-1034 public voucher (invoice): write only a one-sentence cover remark. "
        "Keep it minimal; the figures and certification are supplied separately."
    ),
    "cdrl": (
        "a monthly CDRL status report: write the accomplishments-this-period and "
        "plan-for-next-period narrative."
    ),
}


def draft_system_prompt(doc_type: str) -> str:
    """Prose-only instructions for one document type. Raises KeyError if unknown."""
    guidance = _DOC_GUIDANCE[doc_type]
    return (
        "You are Runway's GovCon documentation assistant. The user is drafting "
        f"{guidance}\n\n"
        "Rules:\n"
        "- Write PROSE only — flowing sentences a program manager could send.\n"
        "- Do NOT state any dollar figures, amounts, percentages, dates, CLIN "
        "numbers, or the contract number. Those numbers are filled in separately "
        "and must never come from you. Refer to them generically ('the funded "
        "amount', 'this period') instead of inventing values.\n"
        "- No markdown, headings, or bullet asterisks — just short paragraphs.\n"
        "- Ground the tone and substance in the <data> block (the contract's burn "
        "and funding picture); never contradict it.\n"
        "- Keep it concise and professional."
    )


def stream_draft(contract_id, doc_type: str):
    """Yield the document's narrative prose in chunks. Numbers stay on the client."""
    grounding = ask.build_grounding(contract_id)
    system = (
        draft_system_prompt(doc_type)
        + "\n\n<data>\n"
        + json.dumps(grounding, default=str)
        + "\n</data>"
    )
    with ask.client.messages.stream(
        model=ask.ASK_MODEL,
        max_tokens=700,
        system=system,
        messages=[{"role": "user", "content": "Write the narrative now."}],
    ) as stream:
        for text in stream.text_stream:
            yield text
