"""The source document behind a contract's numbers (#30).

The feature's whole claim is "you can get back to the award this figure came from",
so the tests here are about that claim surviving the ways it can quietly stop being
true: a document that never got attached, one attached to the wrong contract, one
that outlives the contract it evidenced, and the pre-existing contracts that have no
document at all and must keep working regardless.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, documents  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A throwaway database. The app resolves DB_PATH at import time, so it is
    patched on the module rather than through an env var."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return db


def _contract(store, piid="FA8750-26-C-0001"):
    return store.save_contract(piid, {"contract": {"piid": piid}})


PDF = b"%PDF-1.4 fake award bytes"


def test_a_stored_award_comes_back_with_its_bytes_and_metadata(store):
    cid = _contract(store)
    row = store.save_document(cid, documents.AWARD, "award.pdf", "application/pdf", PDF)

    listed = store.list_documents(cid)
    assert [d["filename"] for d in listed] == ["award.pdf"]
    assert listed[0]["kind"] == documents.AWARD
    assert listed[0]["size_bytes"] == len(PDF)
    assert listed[0]["sha256"] == documents.digest(PDF)
    # The list is metadata only — a blob on every contract read is the thing the
    # separate table exists to avoid.
    assert "blob" not in listed[0]

    fetched = store.get_document(cid, row["id"])
    assert fetched["blob"] == PDF


def test_a_contract_from_before_this_feature_has_no_documents_and_still_reads(store):
    cid = _contract(store)
    assert store.list_documents(cid) == []
    assert store.get_contract(cid)["piid"] == "FA8750-26-C-0001"


def test_re_uploading_the_same_file_does_not_store_a_second_copy(store):
    cid = _contract(store)
    first = store.save_document(
        cid, documents.AWARD, "award.pdf", "application/pdf", PDF
    )
    again = store.save_document(
        cid, documents.AWARD, "award.pdf", "application/pdf", PDF
    )

    assert again["duplicate"] is True
    assert again["id"] == first["id"]
    assert len(store.list_documents(cid)) == 1


def test_a_corrected_award_is_kept_alongside_the_one_it_supersedes(store):
    """A different file is new evidence, not an overwrite: the earlier award is what
    earlier numbers were derived from."""
    cid = _contract(store)
    store.save_document(cid, documents.AWARD, "award.pdf", "application/pdf", PDF)
    store.save_document(
        cid, documents.AWARD, "award-rev2.pdf", "application/pdf", PDF + b" rev2"
    )
    assert len(store.list_documents(cid)) == 2


def test_a_document_cannot_be_read_through_another_contract(store):
    mine, theirs = _contract(store), _contract(store, "FA8750-26-C-0002")
    row = store.save_document(
        mine, documents.AWARD, "award.pdf", "application/pdf", PDF
    )
    assert store.get_document(theirs, row["id"]) is None
    assert store.get_document(mine, row["id"]) is not None


def test_a_missing_document_id_reads_as_missing_rather_than_raising(store):
    cid = _contract(store)
    assert store.get_document(cid, 9999) is None


def test_an_ingest_upload_is_attached_by_confirming_it(store):
    """Bytes arrive before the contract exists, so they land unowned and confirm
    claims them."""
    pending = store.save_document(
        None, documents.AWARD, "award.pdf", "application/pdf", PDF
    )
    cid = _contract(store)
    assert store.list_documents(cid) == []

    assert store.claim_document(pending["id"], cid) is True
    assert [d["id"] for d in store.list_documents(cid)] == [pending["id"]]


def test_a_document_already_claimed_is_never_re_parented(store):
    pending = store.save_document(
        None, documents.AWARD, "award.pdf", "application/pdf", PDF
    )
    mine, theirs = _contract(store), _contract(store, "FA8750-26-C-0002")
    assert store.claim_document(pending["id"], mine) is True

    assert store.claim_document(pending["id"], theirs) is False
    assert store.list_documents(theirs) == []
    assert len(store.list_documents(mine)) == 1


def test_an_abandoned_upload_is_swept_and_a_fresh_one_is_left_alone(store):
    """Closing the review screen without confirming must not leak bytes — but a
    review still open must not have its document deleted out from under it."""
    stale = store.save_document(
        None, documents.AWARD, "old.pdf", "application/pdf", PDF
    )
    fresh = store.save_document(
        None, documents.AWARD, "new.pdf", "application/pdf", PDF + b"new"
    )
    conn = store.get_conn()
    conn.execute(
        "UPDATE contract_documents SET created_at = datetime('now','-3 days') WHERE id = ?",
        (stale["id"],),
    )
    conn.commit()
    conn.close()

    assert store.purge_unclaimed_documents() == 1
    cid = _contract(store)
    assert store.claim_document(stale["id"], cid) is False
    assert store.claim_document(fresh["id"], cid) is True


def test_deleting_a_contract_takes_its_documents_with_it(store):
    cid = _contract(store)
    award = store.save_document(cid, documents.AWARD, "a.pdf", "application/pdf", PDF)
    store.save_document(
        cid, documents.RATE_SCHEDULE, "r.pdf", "application/pdf", PDF + b"rates"
    )
    other = _contract(store, "FA8750-26-C-0002")
    kept = store.save_document(other, documents.AWARD, "b.pdf", "application/pdf", PDF)

    assert store.delete_contract(cid) is True
    conn = store.get_conn()
    remaining = [
        r["id"] for r in conn.execute("SELECT id FROM contract_documents").fetchall()
    ]
    conn.close()
    assert remaining == [kept["id"]]
    assert award["id"] not in remaining


# --- the limits (no database needed) ----------------------------------------


def test_an_empty_upload_is_refused_with_a_reason():
    assert "empty" in documents.rejection("award.pdf", b"").lower()


def test_an_oversized_upload_is_refused_and_says_how_big_the_limit_is():
    note = documents.rejection("award.pdf", b"x" * (documents.MAX_BYTES + 1))
    assert note and "25 MB" in note


def test_an_upload_at_the_limit_is_accepted():
    assert documents.rejection("award.pdf", b"x" * documents.MAX_BYTES) is None


def test_only_the_formats_ingest_can_actually_read_are_stored():
    assert documents.rejection("award.pdf", PDF) is None
    assert documents.rejection("award.txt", b"text award") is None
    assert documents.rejection("award.docx", PDF) is not None
    assert documents.rejection("award", PDF) is not None


def test_the_extension_decides_the_served_type_not_the_uploads_own_claim():
    """The declared content type is client-supplied and ends up on the download
    response — trusting it would let an upload choose how its own bytes are read."""
    assert documents.content_type("award.pdf", "text/html") == documents.PDF_TYPE
    assert documents.content_type("notes.txt", "application/pdf") == documents.TEXT_TYPE
    assert documents.content_type("mystery.bin", None) == "application/octet-stream"


def test_a_filename_cannot_break_out_of_the_download_header():
    assert documents.safe_filename("../../etc/passwd") == "passwd"
    assert documents.safe_filename('aw"ard\r\n.pdf') == "award.pdf"
    assert documents.safe_filename("   ") == "source-document"
    assert documents.safe_filename(None) == "source-document"


def test_the_hash_is_the_one_an_auditor_would_recompute():
    import hashlib

    assert documents.digest(PDF) == hashlib.sha256(PDF).hexdigest()
