"""A seed and its generation opts are one pairing, and the user has to be able to
state both (#136).

Fixtura draws the whole scenario — award, PIID, CLINs, roster — from seed + opts, and
the PIID's fiscal-year segment comes off the award's effective date, which
`pop_in_progress` moves back a year per preceding option period. So one seed generates
`W45983-24-C-1675` as a historical contract and `W45983-25-C-1675` as an in-progress
one. `normalize_piid` deliberately does not fold that digit, so the provenance gate
(#130) refuses the second batch against the first contract — correctly, and with no way
out: re-deriving opts from the award produces the same wrong guess every time, and
`allow_mismatch` only makes the contract permanently disagree with its own timesheets.

These tests cover the way in: opts recorded at `confirm` so the FIRST sync replays the
pairing instead of guessing it, and `?opts=` on the sync so an already-stuck contract
can be repaired without a re-ingest.

Fixtura is stubbed. What is under test is which pairing Runway asks for, not what
Fixtura draws from it.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, main, sources  # noqa: E402
from app.schemas import CLIN, ContractHeader, Extraction, LaborRate  # noqa: E402

PIID = "N66048-24-C-7647"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with TestClient(main.app) as c:
        yield c


def _rows(contract_no, n=3):
    return [
        {
            "employee": f"Person {i}",
            "employee_id": f"E-{i:05d}",
            "week_ending": "2026-08-07",
            "contract_no": contract_no,
            "charge_code": "1001",
            "labor_category": "Business Analyst",
            "total_hours": 40.0,
        }
        for i in range(n)
    ]


def _save(client, query=""):
    extraction = Extraction(
        contract=ContractHeader(piid=PIID),
        periods=[],
        clins=[
            CLIN(
                clin="1001",
                title="Engineering services",
                is_labor=True,
                labor_rates=[LaborRate(lcat="Business Analyst", rate=125.34)],
            )
        ],
    )
    r = client.post(f"/api/contracts/confirm{query}", json=extraction.model_dump())
    return r


def _fixtura(monkeypatch, batches):
    calls = []

    def fake(rows=None, seed=None, opts=None):
        calls.append({"seed": seed, "opts": opts})
        return batches[seed]

    monkeypatch.setattr(sources, "fetch_timesheets", fake)
    return calls


# --- reading a stated pairing ----------------------------------------------


def test_key_value_pairs_are_read_as_the_types_they_look_like():
    """The form spelling. `pop_in_progress=true` has to arrive as a boolean, not the
    string "true" — Fixtura tests the knob for truth, and every non-empty string is
    true, so a string would turn `pop_in_progress=false` into the opposite of what was
    typed."""
    assert sources.parse_opts("pop_in_progress=true, option_years=1, staffing=1.0") == {
        "pop_in_progress": True,
        "option_years": 1,
        "staffing": 1.0,
    }
    assert sources.parse_opts("pop_in_progress=false") == {"pop_in_progress": False}
    assert sources.parse_opts("contract_type=T&M") == {"contract_type": "T&M"}


def test_json_objects_are_read_too():
    """The spelling that can carry a list — `lcat_lines` has no key=value form."""
    assert sources.parse_opts('{"option_years": 2, "shared_pool": true}') == {
        "option_years": 2,
        "shared_pool": True,
    }


def test_saying_nothing_is_not_an_error():
    """Blank means "fall back to what you would have done", which is every existing
    caller. An empty string is what an untouched form field sends."""
    assert sources.parse_opts(None) == {}
    assert sources.parse_opts("") == {}
    assert sources.parse_opts("   ") == {}


def test_an_unknown_knob_is_refused_and_lists_the_real_ones():
    """The whole point of validating: Fixtura ignores a knob it does not recognise, so
    a typo generates the wrong contract in silence and lands the user back at the
    refusal they were trying to escape — with nothing new on screen."""
    with pytest.raises(ValueError) as e:
        sources.parse_opts("pop_inprogress=true")
    assert "pop_inprogress" in str(e.value)
    assert "pop_in_progress" in str(e.value)  # the knob they meant


def test_unparseable_text_says_so():
    with pytest.raises(ValueError):
        sources.parse_opts("pop_in_progress")  # no '='
    with pytest.raises(ValueError):
        sources.parse_opts("{not json")
    with pytest.raises(ValueError):
        sources.parse_opts("[1, 2]")  # JSON, but not a knob set


def test_contract_shaping_knobs_are_allowed_when_stated():
    """`derive_scenario_opts` refuses to INFER `active_period` and `lcat_lines`
    because inferring them rewrites the contract. Stating them is the opposite: if the
    award was generated with them, they are the only way to reproduce it."""
    assert sources.parse_opts('{"lcat_lines": [["Analyst", 2]]}') == {
        "lcat_lines": [["Analyst", 2]]
    }
    assert sources.parse_opts("active_period=2") == {"active_period": 2}


# --- recording it at ingest ------------------------------------------------


def test_confirm_records_the_stated_opts_alongside_the_seed(client):
    saved = _save(client, "?seed=60254&opts=pop_in_progress=true,option_years=1").json()
    assert saved["sync_opts"] == {"pop_in_progress": True, "option_years": 1}
    assert db.get_contract(saved["id"])["sync_opts"] == saved["sync_opts"]
    assert db.get_contract(saved["id"])["sync_seed"] == 60254


def test_the_first_sync_replays_recorded_opts_instead_of_deriving(client, monkeypatch):
    """The bug, in one test. The award has an in-progress period, so the derivation
    sets `pop_in_progress` and Fixtura renumbers the contract; the user knows it was
    generated historical and says so. Recording that has to beat deriving it, on the
    very first sync — there is no clean batch yet for the #130 pin to have saved."""
    saved = _save(client, "?seed=7&opts=pop_in_progress=false").json()
    calls = _fixtura(monkeypatch, {7: _rows(PIID)})

    assert (
        client.post(f"/api/contracts/{saved['id']}/timesheets/sync").status_code == 200
    )
    assert calls[0]["opts"] == {"pop_in_progress": False}


def test_confirm_refuses_a_mistyped_knob(client):
    r = _save(client, "?seed=7&opts=pop_inprogress=true")
    assert r.status_code == 400
    assert "pop_inprogress" in r.json()["detail"]


def test_stating_nothing_leaves_the_contract_deriving_as_before(client, monkeypatch):
    """No opts recorded is still a valid state — most awards are not Fixtura-generated
    at all, and nothing about this may change what they sync."""
    saved = _save(client, "?seed=7").json()
    assert saved["sync_opts"] is None
    calls = _fixtura(monkeypatch, {7: _rows(PIID)})

    client.post(f"/api/contracts/{saved['id']}/timesheets/sync")
    assert calls[0]["opts"] == sources.derive_scenario_opts(
        db.get_contract(saved["id"])
    )


# --- repairing one at sync -------------------------------------------------


def test_stated_opts_beat_a_pin_and_are_pinned_in_turn(client, monkeypatch):
    """The no-re-ingest repair. A contract that pinned a pairing still has to be able
    to correct it, and the corrected pairing has to stick — otherwise the next
    auto-sync, which passes nothing, walks straight back into the refusal."""
    saved = _save(client, "?seed=7&opts=option_years=1").json()
    calls = _fixtura(monkeypatch, {7: _rows(PIID)})
    client.post(f"/api/contracts/{saved['id']}/timesheets/sync")

    r = client.post(f"/api/contracts/{saved['id']}/timesheets/sync?opts=option_years=2")
    assert r.status_code == 200
    assert r.json()["opts"] == {"option_years": 2}
    assert calls[1]["opts"] == {"option_years": 2}
    assert db.get_contract(saved["id"])["sync_opts"] == {"option_years": 2}

    client.post(f"/api/contracts/{saved['id']}/timesheets/sync")
    assert calls[2]["opts"] == {"option_years": 2}


def test_a_refused_batch_does_not_pin_the_opts_that_drew_it(client, monkeypatch):
    """Same rule the seed already follows: a pairing that produced a stranger's labor
    must not become the contract's baseline just because the caller stated it."""
    saved = _save(client, "?seed=7&opts=option_years=1").json()
    _fixtura(monkeypatch, {7: _rows("GS-31F-2774F")})

    r = client.post(f"/api/contracts/{saved['id']}/timesheets/sync?opts=option_years=2")
    assert r.status_code == 409
    assert db.get_contract(saved["id"])["sync_opts"] == {"option_years": 1}


def test_opts_and_a_scenario_together_are_refused(client):
    """A demo scenario IS a stated (seed, opts) pairing, so the two inputs contradict
    each other. Ranking them silently is how a bundle stops reproducing itself."""
    saved = _save(client, "?seed=7").json()
    r = client.post(
        f"/api/contracts/{saved['id']}/timesheets/sync?scenario=red&opts=option_years=2"
    )
    assert r.status_code == 400
    assert "pairing" in r.json()["detail"]


def test_a_mistyped_knob_is_refused_before_fixtura_is_asked(client, monkeypatch):
    saved = _save(client, "?seed=7").json()
    calls = _fixtura(monkeypatch, {7: _rows(PIID)})

    r = client.post(f"/api/contracts/{saved['id']}/timesheets/sync?opts=staffing_=1.0")
    assert r.status_code == 400
    assert calls == []


def test_every_sync_reports_the_opts_it_used(client, monkeypatch):
    """A refusal is only diagnosable if both halves of the pairing are visible. The
    seed has always been reported; the opts were invisible from outside the route."""
    saved = _save(client, "?seed=7").json()
    _fixtura(monkeypatch, {7: _rows(PIID)})

    body = client.post(f"/api/contracts/{saved['id']}/timesheets/sync").json()
    assert body["opts"] == sources.derive_scenario_opts(db.get_contract(saved["id"]))
