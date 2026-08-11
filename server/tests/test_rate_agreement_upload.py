"""The FPRA / provisional billing rate letter upload (#78 slice 3b).

The award face gives three percentages. This document is what says what they *are*:
each pool's application base, the fiscal year, and whether they are provisional
billing rates (FAR 42.704) or a final determination (FAR 42.705). So it is allowed to
overwrite the face read, and it is the input #87 trues up.

The documented limitation asserted here: `rate_sets` keys on (scope, fiscal year) and
cannot hold both a provisional and a final set for one year, so a letter printing
both stores the FINAL — what the year settled to — and *reports* that the provisional
set was there. Silently keeping the superseded rates would price every hour wrong.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, documents, extract, main, rates  # noqa: E402
from app.schemas import IndirectPool, RateAgreement  # noqa: E402

PDF = b"%PDF-1.4 fake rate letter"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def contract(client):
    return client.post(
        "/api/contracts/confirm",
        json={
            "contract": {
                "piid": "FA8750-26-C-0078",
                "effective_date": "2026-03-02",
                # The face read this slice is allowed to supersede.
                "indirect_fringe": 0.30,
                "indirect_overhead": 0.20,
                "indirect_gna": 0.10,
            },
            "periods": [],
            "clins": [],
        },
    ).json()["id"]


def _stub(monkeypatch, **kw):
    kw.setdefault("fiscal_year", "2026")
    monkeypatch.setattr(
        extract, "extract_rate_agreement_from_pdf", lambda _b: RateAgreement(**kw)
    )


def _pool(pool, rate, base=None):
    return IndirectPool(pool=pool, rate=rate, base=base)


def _upload(client, contract, allow_mismatch=False):
    query = "?allow_mismatch=true" if allow_mismatch else ""
    return client.post(
        f"/api/contracts/{contract}/rate-agreement{query}",
        files={"file": ("fpra.pdf", PDF, "application/pdf")},
    )


PROVISIONAL_SET = [
    _pool("fringe", 0.268, "direct_labor"),
    _pool("overhead", 0.17, "labor_plus_fringe"),
    _pool("gna", 0.137, "total_cost_input"),
]


def test_a_provisional_letter_stores_a_fiscal_year_keyed_set(
    client, contract, monkeypatch
):
    _stub(
        monkeypatch,
        status="provisional",
        pools=PROVISIONAL_SET,
        cognisant_agency="DCMA",
    )
    body = _upload(client, contract).json()
    assert body["status"] == rates.PROVISIONAL
    assert body["fiscal_year"] == "2026"
    assert body["pools_stored"] == 3
    assert body["final_determination_found"] is False

    model = client.get(f"/api/contracts/{contract}/rate-model").json()
    got = {
        p["pool"]: (round(p["rate"], 4), p["base"], p["status"]) for p in model["pools"]
    }
    assert got["fringe"] == (0.268, rates.BASE_DIRECT, rates.PROVISIONAL)
    assert got["overhead"] == (0.17, rates.BASE_LABOR_FRINGE, rates.PROVISIONAL)
    assert got["gna"] == (0.137, rates.BASE_TOTAL_COST_INPUT, rates.PROVISIONAL)


def test_a_final_only_letter_stores_an_actual_set(client, contract, monkeypatch):
    """A final determination may be the only rate set printed in the letter."""
    _stub(monkeypatch, status="final", pools=PROVISIONAL_SET, final_pools=None)
    body = _upload(client, contract).json()
    assert body["status"] == rates.ACTUAL
    assert body["final_determination_found"] is True

    model = client.get(f"/api/contracts/{contract}/rate-model").json()
    assert all(p["status"] == rates.ACTUAL for p in model["pools"])


def test_the_letter_supersedes_the_award_face_read(client, contract, monkeypatch):
    """The face states rates but not their base or status. This document states all
    three, so it wins on its own subject rather than sitting beside the face read."""
    _stub(monkeypatch, status="provisional", pools=PROVISIONAL_SET)
    _upload(client, contract)
    pools = client.get(f"/api/contracts/{contract}/rate-model").json()["pools"]
    assert len(pools) == 3  # not six
    assert round(next(p["rate"] for p in pools if p["pool"] == "fringe"), 4) == 0.268


def test_a_pair_letter_stores_the_final_set_and_reports_the_provisional(
    client, contract, monkeypatch
):
    _stub(
        monkeypatch,
        status="provisional",
        pools=PROVISIONAL_SET,
        final_pools=[
            _pool("fringe", 0.271, "direct_labor"),
            _pool("overhead", 0.201, "labor_plus_fringe"),
            _pool("gna", 0.146, "total_cost_input"),
        ],
        determination_date="2027-03-29",
    )
    body = _upload(client, contract).json()
    assert body["final_determination_found"] is True
    assert body["provisional_pools_found"] == 3
    assert body["status"] == rates.ACTUAL
    assert body["determination_date"] == "2027-03-29"

    model = client.get(f"/api/contracts/{contract}/rate-model").json()
    # The settled rates, not the ones that were billed against during the year.
    assert (
        round(next(p["rate"] for p in model["pools"] if p["pool"] == "gna"), 4) == 0.146
    )
    assert all(p["status"] == rates.ACTUAL for p in model["pools"])


def test_an_unrecognised_base_falls_back_rather_than_dropping_the_pool(
    client, contract, monkeypatch
):
    """A typo in a base name must not silently delete an overhead pool from the cost
    — the same posture `rates.burden` takes."""
    _stub(
        monkeypatch,
        status="provisional",
        pools=[_pool("overhead", 0.17, "value_added")],
    )
    _upload(client, contract)
    pools = client.get(f"/api/contracts/{contract}/rate-model").json()["pools"]
    assert [(p["pool"], p["base"]) for p in pools] == [
        ("overhead", rates.BASE_LABOR_FRINGE)
    ]


def test_a_pool_we_cannot_apply_is_skipped_not_guessed(client, contract, monkeypatch):
    _stub(
        monkeypatch,
        status="provisional",
        pools=[_pool("material_handling", 0.05), _pool("fringe", 0.268)],
    )
    body = _upload(client, contract).json()
    assert body["pools_stored"] == 1


def test_a_letter_with_no_readable_rates_is_a_422(client, contract, monkeypatch):
    _stub(monkeypatch, status="provisional", pools=[])
    r = _upload(client, contract)
    assert r.status_code == 422
    # And nothing was kept implying otherwise.
    assert client.get(f"/api/contracts/{contract}/documents").json()["documents"] == []


def test_the_letter_is_kept_as_its_own_document_kind(client, contract, monkeypatch):
    """An accountant asks for the rate agreement by name. A source panel that cannot
    tell it from a pricing schedule is not evidence."""
    _stub(monkeypatch, status="provisional", pools=PROVISIONAL_SET)
    _upload(client, contract)
    docs = client.get(f"/api/contracts/{contract}/documents").json()["documents"]
    assert [d["kind"] for d in docs] == [documents.RATE_AGREEMENT]


def test_a_company_wide_letter_names_no_contract_and_is_not_a_mismatch(
    client, contract, monkeypatch
):
    _stub(monkeypatch, status="provisional", pools=PROVISIONAL_SET, piid=None)
    assert _upload(client, contract).json()["piid_mismatch"] is False


def test_a_letter_for_another_contract_can_be_explicitly_allowed(
    client, contract, monkeypatch
):
    _stub(
        monkeypatch,
        status="provisional",
        pools=PROVISIONAL_SET,
        piid="N00019-26-C-9999",
    )
    body = _upload(client, contract, allow_mismatch=True).json()
    assert body["piid_mismatch"] is True
    assert body["pools_stored"] == 3


def test_no_fiscal_year_still_stores(client, contract, monkeypatch):
    _stub(monkeypatch, status="provisional", pools=PROVISIONAL_SET, fiscal_year=None)
    body = _upload(client, contract).json()
    assert body["fiscal_year"] is None
    assert body["pools_stored"] == 3
