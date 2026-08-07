"""The HTTP half of #30: the routes that keep, list and serve a source document.

Split from `test_contract_documents.py` (which owns the storage rules) because these
are about the wiring — that confirm claims the ingest upload, that the rate-schedule
import keeps its own file, that a download comes back as the right bytes with headers
a browser will open rather than mangle, and that nothing leaks across contracts.

Extraction is stubbed throughout. These tests are about the document lifecycle, and
calling a model to parse a PDF would make them slow, non-deterministic and dependent
on Bedrock credentials to tell us something that has nothing to do with extraction.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, documents, extract, main  # noqa: E402
from app.schemas import CLIN, ContractHeader, Extraction, LaborRate  # noqa: E402

PDF = b"%PDF-1.4 fake award bytes"
PIID = "FA8750-26-C-0001"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with TestClient(main.app) as c:
        yield c


def _extraction(with_rates=False):
    return Extraction(
        contract=ContractHeader(piid=PIID),
        periods=[],
        clins=[
            CLIN(
                clin="0001",
                title="Engineering services",
                is_labor=True,
                labor_rates=(
                    [LaborRate(lcat="Systems Engineer", rate=185.0)]
                    if with_rates
                    else None
                ),
            )
        ],
    )


def _save_contract(client, document_id=None):
    q = f"?document_id={document_id}" if document_id is not None else ""
    r = client.post(
        f"/api/contracts/confirm{q}", json=_extraction().model_dump(mode="json")
    )
    assert r.status_code == 200
    return r.json()


def test_a_contract_with_no_source_document_lists_an_empty_set(client):
    """Every contract ingested before this feature is in this state, and the panel
    has to be able to say so rather than error."""
    cid = _save_contract(client)["id"]
    r = client.get(f"/api/contracts/{cid}/documents")
    assert r.status_code == 200
    assert r.json() == {"id": cid, "documents": []}


def test_documents_for_a_contract_that_does_not_exist_are_a_404(client):
    assert client.get("/api/contracts/9999/documents").status_code == 404


def test_confirming_an_extraction_attaches_the_upload_ingest_stashed(client):
    pending = db.save_document(
        None, documents.AWARD, "award.pdf", documents.PDF_TYPE, PDF
    )
    saved = _save_contract(client, document_id=pending["id"])
    assert saved["source_document_stored"] is True

    listed = client.get(f"/api/contracts/{saved['id']}/documents").json()["documents"]
    assert [d["filename"] for d in listed] == ["award.pdf"]
    assert listed[0]["kind"] == documents.AWARD
    assert listed[0]["sha256"] == documents.digest(PDF)


def test_a_contract_still_saves_when_its_document_id_is_stale(client):
    """The source panel is evidence, not a gate — manual entry has no document at
    all, and a contract that refused to save over a swept upload would be worse than
    one saved without its PDF."""
    saved = _save_contract(client, document_id=4242)
    assert saved["id"]
    assert saved["source_document_stored"] is False
    assert (
        client.get(f"/api/contracts/{saved['id']}/documents").json()["documents"] == []
    )


def test_downloading_a_document_returns_the_original_bytes_and_opens_inline(client):
    pending = db.save_document(
        None, documents.AWARD, "award.pdf", documents.PDF_TYPE, PDF
    )
    cid = _save_contract(client, document_id=pending["id"])["id"]

    r = client.get(f"/api/contracts/{cid}/documents/{pending['id']}")
    assert r.status_code == 200
    assert r.content == PDF
    assert r.headers["content-type"] == documents.PDF_TYPE
    assert r.headers["content-disposition"] == 'inline; filename="award.pdf"'
    assert r.headers["x-document-sha256"] == documents.digest(PDF)


def test_one_contracts_document_cannot_be_downloaded_through_another(client):
    pending = db.save_document(
        None, documents.AWARD, "award.pdf", documents.PDF_TYPE, PDF
    )
    mine = _save_contract(client, document_id=pending["id"])["id"]
    theirs = _save_contract(client)["id"]

    assert (
        client.get(f"/api/contracts/{theirs}/documents/{pending['id']}").status_code
        == 404
    )
    assert (
        client.get(f"/api/contracts/{mine}/documents/{pending['id']}").status_code
        == 200
    )


def test_downloading_a_document_that_isnt_there_is_a_404(client):
    cid = _save_contract(client)["id"]
    assert client.get(f"/api/contracts/{cid}/documents/9999").status_code == 404


def test_importing_a_rate_schedule_keeps_the_schedule_it_read(client, monkeypatch):
    """The loaded rates a schedule supplies are the figures an accountant is least
    willing to take on faith, and this upload is the only copy Runway ever sees."""
    monkeypatch.setattr(
        extract, "extract_from_pdf", lambda data: _extraction(with_rates=True)
    )
    cid = _save_contract(client)["id"]

    r = client.post(
        f"/api/contracts/{cid}/rates",
        files={"file": ("rates.pdf", PDF, documents.PDF_TYPE)},
    )
    assert r.status_code == 200
    assert r.json()["clins_updated"] == 1
    assert r.json()["source_document_note"] is None

    listed = client.get(f"/api/contracts/{cid}/documents").json()["documents"]
    assert [(d["kind"], d["filename"]) for d in listed] == [
        (documents.RATE_SCHEDULE, "rates.pdf")
    ]
    assert (
        client.get(f"/api/contracts/{cid}/documents/{listed[0]['id']}").content == PDF
    )


def test_a_schedule_that_parsed_to_no_rates_leaves_no_document_behind(
    client, monkeypatch
):
    """A 422 means nothing was merged, so a stored document would imply an audit
    trail for numbers that never moved."""
    monkeypatch.setattr(
        extract, "extract_from_pdf", lambda data: _extraction(with_rates=False)
    )
    cid = _save_contract(client)["id"]

    r = client.post(
        f"/api/contracts/{cid}/rates",
        files={"file": ("rates.pdf", PDF, documents.PDF_TYPE)},
    )
    assert r.status_code == 422
    assert client.get(f"/api/contracts/{cid}/documents").json()["documents"] == []


def test_a_failed_extraction_leaves_no_document_behind(client, monkeypatch):
    def boom(data):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(extract, "extract_from_pdf", boom)
    cid = _save_contract(client)["id"]

    r = client.post(
        f"/api/contracts/{cid}/rates",
        files={"file": ("rates.pdf", PDF, documents.PDF_TYPE)},
    )
    assert r.status_code == 502
    assert client.get(f"/api/contracts/{cid}/documents").json()["documents"] == []


def test_re_importing_the_same_schedule_does_not_store_it_twice(client, monkeypatch):
    monkeypatch.setattr(
        extract, "extract_from_pdf", lambda data: _extraction(with_rates=True)
    )
    cid = _save_contract(client)["id"]
    files = {"file": ("rates.pdf", PDF, documents.PDF_TYPE)}
    assert client.post(f"/api/contracts/{cid}/rates", files=files).status_code == 200
    assert client.post(f"/api/contracts/{cid}/rates", files=files).status_code == 200

    assert len(client.get(f"/api/contracts/{cid}/documents").json()["documents"]) == 1


def test_an_oversized_schedule_is_read_but_says_it_was_not_kept(client, monkeypatch):
    """The rates still merge — refusing the whole import over a storage limit would
    lose the user real work — but the response says the source wasn't kept, because
    a dashboard with no auditable source must not look like one that has it."""
    monkeypatch.setattr(
        extract, "extract_from_pdf", lambda data: _extraction(with_rates=True)
    )
    cid = _save_contract(client)["id"]

    r = client.post(
        f"/api/contracts/{cid}/rates",
        files={
            "file": ("huge.pdf", b"x" * (documents.MAX_BYTES + 1), documents.PDF_TYPE)
        },
    )
    assert r.status_code == 200
    assert r.json()["clins_updated"] == 1
    assert r.json()["source_document_id"] is None
    assert "25 MB" in r.json()["source_document_note"]
    assert client.get(f"/api/contracts/{cid}/documents").json()["documents"] == []


def test_deleting_a_contract_removes_its_documents_from_the_api_too(client):
    pending = db.save_document(
        None, documents.AWARD, "award.pdf", documents.PDF_TYPE, PDF
    )
    cid = _save_contract(client, document_id=pending["id"])["id"]

    assert client.delete(f"/api/contracts/{cid}").status_code == 200
    assert (
        client.get(f"/api/contracts/{cid}/documents/{pending['id']}").status_code == 404
    )
    conn = db.get_conn()
    assert conn.execute("SELECT COUNT(*) FROM contract_documents").fetchone()[0] == 0
    conn.close()
