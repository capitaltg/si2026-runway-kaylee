"""Indirect rates read off the award face become the contract's rate set (#78).

A cost-type award prints `Indirect Rates: Fringe X% | Overhead Y% | G&A Z%` above
its pricing exhibit, because those are terms of the contract. So confirming such an
award is enough to turn on the cost side of the engine (#77) — nobody has to type a
percentage, and nobody has to find the FPRA first.

What the face does *not* state is each pool's application base or whether the rates
are provisional or final, so those are filled with the conventional bases and
`provisional`. That default is load-bearing: calling a face rate `actual` would let
#87 skip a year-end true-up that is genuinely owed.
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
