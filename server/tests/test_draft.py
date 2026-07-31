import pytest

from app.draft import DRAFT_DOC_TYPES, draft_system_prompt


def test_doc_types():
    assert DRAFT_DOC_TYPES == {"funding", "invoice", "cdrl"}


@pytest.mark.parametrize("doc_type", ["funding", "invoice", "cdrl"])
def test_prompt_is_prose_only(doc_type):
    p = draft_system_prompt(doc_type).lower()
    # It must tell the model to write prose only and NOT to emit figures/amounts,
    # since every number is filled deterministically on the client.
    assert "prose" in p or "narrative" in p
    assert "do not" in p or "never" in p
    assert "figure" in p or "dollar" in p or "amount" in p or "number" in p


def test_prompt_mentions_the_document_kind():
    assert "funding" in draft_system_prompt("funding").lower()
    assert (
        "invoice" in draft_system_prompt("invoice").lower()
        or "voucher" in draft_system_prompt("invoice").lower()
    )
    assert (
        "status" in draft_system_prompt("cdrl").lower()
        or "cdrl" in draft_system_prompt("cdrl").lower()
    )


def test_unknown_doc_type_raises():
    with pytest.raises(KeyError):
        draft_system_prompt("nope")
