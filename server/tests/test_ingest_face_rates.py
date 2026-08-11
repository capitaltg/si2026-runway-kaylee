"""Indirect rates read off the award face become the contract's rate set (#78).

A cost-type award prints `Indirect Rates: Fringe X% | Overhead Y% | G&A Z%` above
its pricing exhibit, because those are terms of the contract. So confirming such an
award is enough to turn on the cost side of the engine (#77) — nobody has to type a
percentage, and nobody has to find the FPRA first.

What the face does *not* state is each pool's application base or whether the rates
are provisional or final, so those are filled with the conventional bases and
`provisional`. That default is load-bearing: calling a face rate `actual` would let
#87 skip a year-end true-up that is genuinely owed.

The same exhibit prices each labor category at an unburdened direct rate, and #138
covers the second half: confirm stores those too, so a cost buildup on the page is
enough to reach Level 2 without re-uploading the award as a rate schedule.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, main, rates  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with TestClient(main.app) as c:
        yield c


def _extraction(**header):
    return {
        "contract": {"piid": "FA8750-26-C-0009", **header},
        "periods": [
            {
                "name": "Base",
                "pop_start": "2026-01-01",
                "pop_end": "2026-12-31",
                "exercised": True,
                "ceiling": 1_080_000.0,
            }
        ],
        "clins": [
            {
                "clin": "0001",
                "period": "Base",
                "title": "Engineering services",
                "type": "CPFF",
                "is_labor": True,
                "ceiling": 1_080_000.0,
                "estimated_cost": 1_000_000.0,
                "fixed_fee": 80_000.0,
            }
        ],
    }


def test_face_rates_are_stored_as_a_provisional_rate_set(client):
    r = client.post(
        "/api/contracts/confirm",
        json=_extraction(
            effective_date="2026-03-02",
            indirect_fringe=0.31,
            indirect_overhead=0.22,
            indirect_gna=0.09,
        ),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["indirect_rates_stored"] is True
    # March 2026 is FY2026 — the federal year runs Oct 1 to Sep 30.
    assert body["indirect_rates_fiscal_year"] == "2026"

    model = client.get(f"/api/contracts/{body['id']}/rate-model").json()
    got = {p["pool"]: p for p in model["pools"]}
    assert {k: round(v["rate"], 4) for k, v in got.items()} == {
        "fringe": 0.31,
        "overhead": 0.22,
        "gna": 0.09,
    }
    # Conventional bases, and provisional until a determination says otherwise.
    assert got["overhead"]["base"] == rates.BASE_LABOR_FRINGE
    assert got["gna"]["base"] == rates.BASE_TOTAL_COST_INPUT
    assert all(p["status"] == rates.PROVISIONAL for p in model["pools"])


def test_october_effective_date_lands_in_the_next_fiscal_year(client):
    body = client.post(
        "/api/contracts/confirm",
        json=_extraction(effective_date="2026-10-15", indirect_fringe=0.30),
    ).json()
    assert body["indirect_rates_fiscal_year"] == "2027"


def test_an_award_with_no_rate_line_stores_nothing(client):
    """The normal case. A fixed-price award prints no indirect rates, and inventing
    a rate set for it would put the app at Level 2 on figures nobody supplied."""
    body = client.post(
        "/api/contracts/confirm", json=_extraction(effective_date="2026-03-02")
    ).json()
    assert body["indirect_rates_stored"] is False
    assert body["indirect_rates_fiscal_year"] is None
    assert client.get(f"/api/contracts/{body['id']}/rate-model").json()["pools"] == []


def test_a_partial_disclosure_stores_only_what_was_printed(client):
    body = client.post(
        "/api/contracts/confirm",
        json=_extraction(
            effective_date="2026-03-02", indirect_fringe=0.31, indirect_gna=0.09
        ),
    ).json()
    pools = client.get(f"/api/contracts/{body['id']}/rate-model").json()["pools"]
    assert sorted(p["pool"] for p in pools) == ["fringe", "gna"]


def test_an_unparseable_effective_date_still_stores_the_rates(client):
    """A rate set with no fiscal year is storable and useful; refusing to keep the
    rates because a date was misread would throw away the harder-won figures."""
    body = client.post(
        "/api/contracts/confirm",
        json=_extraction(effective_date="March 2026", indirect_overhead=0.22),
    ).json()
    assert body["indirect_rates_stored"] is True
    assert body["indirect_rates_fiscal_year"] is None
    pools = client.get(f"/api/contracts/{body['id']}/rate-model").json()["pools"]
    assert [p["pool"] for p in pools] == ["overhead"]


def _with_direct_rates(lines, **header):
    """An extraction whose CLIN carries a cost buildup — a direct rate per labor
    category and no loaded rate, which is how a cost-type exhibit prices work."""
    e = _extraction(**header)
    e["clins"][0]["labor_rates"] = [
        {"lcat": name, "direct_rate": rate, "est_hours": 1_000.0}
        for name, rate in lines
    ]
    return e


def test_a_cost_buildup_reaches_level_two_at_ingest(client):
    """#138: confirming the award is the whole ceremony. Before this, the direct
    rates sat on the CLIN unread and the only way to store them was to re-upload the
    same PDF as a supplemental rate schedule."""
    body = client.post(
        "/api/contracts/confirm",
        json=_with_direct_rates(
            [("Senior Software Engineer", 97.63), ("Business Analyst", 61.86)],
            effective_date="2026-03-02",
            indirect_fringe=0.272,
            indirect_overhead=0.449,
            indirect_gna=0.08,
        ),
    ).json()
    assert body["direct_rates_stored"] == 2

    model = client.get(f"/api/contracts/{body['id']}/rate-model").json()
    assert {r["lcat"]: r["rate"] for r in model["direct_rates"]} == {
        "Senior Software Engineer": 97.63,
        "Business Analyst": 61.86,
    }
    # The point of storing them: margin is now a real figure on this contract.
    assert model["model"]["level"] == rates.LEVEL_CATEGORY_COST
    assert model["model"]["margin_available"] is True


def test_a_loaded_rate_only_award_stores_no_direct_rates(client):
    """A fixed-price schedule prices the work without disclosing our cost, so there
    is nothing to store and Level 1 is the correct, undegraded answer."""
    e = _extraction(effective_date="2026-03-02", indirect_fringe=0.31)
    e["clins"][0]["labor_rates"] = [
        {"lcat": "Program Manager", "loaded_rate": 194.00, "est_hours": 500.0}
    ]
    body = client.post("/api/contracts/confirm", json=e).json()
    assert body["direct_rates_stored"] == 0
    assert (
        client.get(f"/api/contracts/{body['id']}/rate-model").json()["direct_rates"]
        == []
    )
