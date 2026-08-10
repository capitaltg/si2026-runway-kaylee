"""A cost-buildup rate schedule populates the direct-rate table (#78 slice 2).

`rates.CostModel` has read `direct_rates` since #77, but nothing wrote to it except
a human typing into the rates view — so a contractor who uploaded the exhibit that
prints their direct rates still sat at Level 1. This is that wiring.

Two properties, and the second is the one worth defending: a loaded-rate-only sheet
(the normal case on a real award) must write nothing and change no pricing, and a
schedule upload must never delete the per-person rates behind Level 3.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, extract, main, rates  # noqa: E402
from app.schemas import CLIN, ContractHeader, Extraction, LaborRate  # noqa: E402

PDF = b"%PDF-1.4 fake schedule"


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
            "contract": {"piid": "FA8750-26-C-0078", "effective_date": "2026-03-02"},
            "periods": [],
            "clins": [
                {
                    "clin": "0001",
                    "period": "Base",
                    "title": "Engineering",
                    "type": "CPFF",
                    "is_labor": True,
                    "ceiling": 1_000_000.0,
                }
            ],
        },
    ).json()["id"]


def _stub_schedule(monkeypatch, *labor_rates):
    """Stand in for the model. What this test is about is what happens to the
    parsed direct rates, not whether a PDF can be read."""
    parsed = Extraction(
        contract=ContractHeader(piid="FA8750-26-C-0078"),
        periods=[],
        clins=[
            CLIN(
                clin="0001",
                title="Engineering",
                is_labor=True,
                ceiling=1_000_000.0,
                labor_rates=list(labor_rates),
            )
        ],
    )
    monkeypatch.setattr(extract, "extract_from_pdf", lambda _b: parsed)


def _upload(client, contract):
    return client.post(
        f"/api/contracts/{contract}/rates",
        files={"file": ("schedule.pdf", PDF, "application/pdf")},
    ).json()


def test_cost_buildup_sheet_populates_direct_rates(client, contract, monkeypatch):
    _stub_schedule(
        monkeypatch,
        LaborRate(lcat="Senior Software Engineer", direct_rate=81.98, est_hours=1000),
        LaborRate(lcat="Software Engineer (Mid)", direct_rate=54.66, est_hours=2000),
    )
    body = _upload(client, contract)
    assert body["direct_rates_stored"] == 2

    model = client.get(f"/api/contracts/{contract}/rate-model").json()
    got = {r["lcat"]: r["rate"] for r in model["direct_rates"]}
    assert got == {"Senior Software Engineer": 81.98, "Software Engineer (Mid)": 54.66}


def test_loaded_rate_only_sheet_stores_nothing(client, contract, monkeypatch):
    """The normal case, and it must stay a no-op: a schedule with no buildup is not
    a degraded document, and inventing a direct rate from a loaded one would put the
    app at Level 2 on a number nobody negotiated."""
    _stub_schedule(
        monkeypatch,
        LaborRate(lcat="Senior Software Engineer", loaded_rate=189.44, est_hours=1000),
    )
    body = _upload(client, contract)
    assert body["direct_rates_stored"] == 0
    model = client.get(f"/api/contracts/{contract}/rate-model").json()
    assert model["direct_rates"] == []
    # And the rates it *did* carry still merged onto the CLIN, as before.
    assert body["clins_updated"] == 1


def test_a_schedule_upload_does_not_delete_per_person_rates(
    client, contract, monkeypatch
):
    """Level 3 is opt-in and expensive to set up. A delete-then-insert that only
    wrote what this sheet carried would quietly cost someone their payroll-grade
    cost model."""
    db.save_direct_rates(
        contract, "2026", [{"employee_id": "E-1", "rate": 70.0}], rates.PROVISIONAL
    )
    _stub_schedule(
        monkeypatch, LaborRate(lcat="Senior Software Engineer", direct_rate=81.98)
    )
    _upload(client, contract)

    model = client.get(f"/api/contracts/{contract}/rate-model").json()
    assert {r["employee_id"] for r in model["direct_rates"] if r["employee_id"]} == {
        "E-1"
    }
    assert any(r["lcat"] == "Senior Software Engineer" for r in model["direct_rates"])


def test_a_reissued_sheet_replaces_the_category_it_renames(
    client, contract, monkeypatch
):
    """Same category, differently abbreviated. Two rows for one LCAT would be two
    answers to the same pricing question (#64)."""
    db.save_direct_rates(
        contract,
        "2026",
        [{"lcat": "Senior Software Engineer", "rate": 60.0}],
        rates.PROVISIONAL,
    )
    _stub_schedule(
        monkeypatch, LaborRate(lcat="Sr. Software Engineer", direct_rate=81.98)
    )
    _upload(client, contract)

    rows = client.get(f"/api/contracts/{contract}/rate-model").json()["direct_rates"]
    assert len(rows) == 1
    assert rows[0]["rate"] == 81.98


def test_direct_rates_reach_the_cost_model(client, contract, monkeypatch):
    """The point of the whole slice: the contract can now compute a real margin."""
    _stub_schedule(
        monkeypatch, LaborRate(lcat="Senior Software Engineer", direct_rate=81.98)
    )
    db.save_rate_pools(
        contract,
        "2026",
        [{"pool": "fringe", "rate": 0.268, "base": rates.BASE_DIRECT}],
        rates.PROVISIONAL,
    )
    _upload(client, contract)

    model = main._cost_model(contract)
    assert model.level == rates.LEVEL_CATEGORY_COST
    assert model.margin_available is True
    resolved = model.cost_for("Senior Software Engineer", billing_rate=189.44)
    assert resolved.known is True
    assert resolved.source == rates.SOURCE_LCAT
