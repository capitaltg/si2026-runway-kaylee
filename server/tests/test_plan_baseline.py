"""The active baseline (#67 item 1) — one saved plan a contract is committed to.

A saved plan is a what-if until somebody says "this is what we're running". That
designation is what everything downstream measures against: drift vs actuals, the
Flight Deck breach card, the staffing brief. So the property worth pinning hardest
is that a contract can never have two baselines — two answers to "what did we
commit to?" would surface as a plausible-looking drift number rather than an error.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, main  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with TestClient(main.app) as c:
        yield c


PLAN = {"draft": {"7": {"0001": 24}}, "added": [], "removed": [], "absences": []}


def _contract(piid="FA8750-26-C-0001"):
    return db.save_contract(piid, {"contract": {"piid": piid}})


def _plan(cid, name):
    return db.save_plan(cid, name, PLAN)["id"]


def test_a_saved_plan_starts_out_not_the_baseline(client):
    """Saving is not committing. A plan only becomes the baseline when asked."""
    cid = _contract()
    saved = db.save_plan(cid, "Q3 crew-up", PLAN)
    assert saved["is_baseline"] is False
    assert db.get_baseline_plan(cid) is None
    assert client.get(f"/api/contracts/{cid}/plans").json()[0]["is_baseline"] is False


def test_designating_marks_exactly_one_plan(client):
    cid = _contract()
    a, b = _plan(cid, "Q3 crew-up"), _plan(cid, "Lean crew")

    row = client.put(f"/api/contracts/{cid}/plans/{a}/baseline").json()
    assert row["is_baseline"] is True and row["name"] == "Q3 crew-up"

    listed = client.get(f"/api/contracts/{cid}/plans").json()
    assert [p["is_baseline"] for p in listed if p["id"] == a] == [True]
    assert [p["is_baseline"] for p in listed if p["id"] == b] == [False]


def test_designating_a_second_plan_stands_the_first_one_down(client):
    """The swap is the whole point — without it the unique index would reject the
    second designation and the user would be told the app is broken."""
    cid = _contract()
    a, b = _plan(cid, "Q3 crew-up"), _plan(cid, "Lean crew")

    client.put(f"/api/contracts/{cid}/plans/{a}/baseline")
    client.put(f"/api/contracts/{cid}/plans/{b}/baseline")

    assert db.get_baseline_plan(cid)["id"] == b
    listed = client.get(f"/api/contracts/{cid}/plans").json()
    assert sum(1 for p in listed if p["is_baseline"]) == 1


def test_designating_the_same_plan_twice_is_idempotent(client):
    cid = _contract()
    a = _plan(cid, "Q3 crew-up")
    client.put(f"/api/contracts/{cid}/plans/{a}/baseline")
    assert client.put(f"/api/contracts/{cid}/plans/{a}/baseline").status_code == 200
    assert db.get_baseline_plan(cid)["id"] == a


def test_the_baseline_sorts_first(client):
    """It is the plan being run, not the oldest row in a list of eleven."""
    cid = _contract()
    first = _plan(cid, "Q3 crew-up")
    for n in ("Lean crew", "Surge", "Option year 2"):
        _plan(cid, n)

    client.put(f"/api/contracts/{cid}/plans/{first}/baseline")
    listed = client.get(f"/api/contracts/{cid}/plans").json()
    assert listed[0]["id"] == first
    # Everything else keeps newest-first order.
    assert listed[1]["name"] == "Option year 2"


def test_clearing_stands_the_baseline_down_but_keeps_the_plan(client):
    cid = _contract()
    a = _plan(cid, "Q3 crew-up")
    client.put(f"/api/contracts/{cid}/plans/{a}/baseline")

    assert client.delete(f"/api/contracts/{cid}/plans/{a}/baseline").json() == {
        "baseline": None
    }
    assert db.get_baseline_plan(cid) is None
    assert [p["name"] for p in client.get(f"/api/contracts/{cid}/plans").json()] == [
        "Q3 crew-up"
    ]


def test_clearing_a_plan_that_is_not_the_baseline_is_a_404(client):
    """A menu left open since before someone else re-designated must not be able to
    stand down a baseline it isn't looking at."""
    cid = _contract()
    a, b = _plan(cid, "Q3 crew-up"), _plan(cid, "Lean crew")
    client.put(f"/api/contracts/{cid}/plans/{a}/baseline")

    assert client.delete(f"/api/contracts/{cid}/plans/{b}/baseline").status_code == 404
    assert db.get_baseline_plan(cid)["id"] == a


def test_a_baseline_cannot_point_at_another_contracts_plan(client):
    """Cross-contract designation would produce drift numbers that look real."""
    mine, theirs = _contract(), _contract("FA8750-26-C-0002")
    other_plan = _plan(theirs, "Their crew")

    r = client.put(f"/api/contracts/{mine}/plans/{other_plan}/baseline")
    assert r.status_code == 404
    assert db.get_baseline_plan(mine) is None
    # And the other contract's plan was not quietly designated either.
    assert db.get_baseline_plan(theirs) is None


def test_each_contract_has_its_own_baseline(client):
    """The uniqueness rule is per contract, not global — the partial index has to be
    keyed on contract_id or designating on one award would reject the other."""
    a_c, b_c = _contract(), _contract("FA8750-26-C-0002")
    a, b = _plan(a_c, "Crew A"), _plan(b_c, "Crew B")

    assert client.put(f"/api/contracts/{a_c}/plans/{a}/baseline").status_code == 200
    assert client.put(f"/api/contracts/{b_c}/plans/{b}/baseline").status_code == 200
    assert db.get_baseline_plan(a_c)["id"] == a
    assert db.get_baseline_plan(b_c)["id"] == b


def test_deleting_the_baseline_leaves_the_contract_without_one(client):
    """Deleting the plan we committed to is allowed — it just means there is no
    commitment any more, which is a state the app has to be able to hold."""
    cid = _contract()
    a = _plan(cid, "Q3 crew-up")
    client.put(f"/api/contracts/{cid}/plans/{a}/baseline")

    client.delete(f"/api/contracts/{cid}/plans/{a}")
    assert db.get_baseline_plan(cid) is None
    # And the slot is free for another plan to take.
    b = _plan(cid, "Lean crew")
    assert client.put(f"/api/contracts/{cid}/plans/{b}/baseline").status_code == 200


def test_saving_over_the_baseline_keeps_it_the_baseline(client):
    """Editing the committed staffing is normal — re-baselining the plan must not
    silently un-commit it, or the drift comparison would vanish on the next save."""
    cid = _contract()
    a = _plan(cid, "Q3 crew-up")
    client.put(f"/api/contracts/{cid}/plans/{a}/baseline")

    updated = db.update_plan(cid, a, "Q3 crew-up", {**PLAN, "removed": ["7"]})
    assert updated["is_baseline"] is True
    assert db.get_baseline_plan(cid)["data"]["removed"] == ["7"]


def test_the_baseline_survives_a_database_that_predates_the_column(
    client, tmp_path, monkeypatch
):
    """The column arrives by migration on an existing database, so an old plans row
    has to read as "not the baseline" rather than as NULL/unknown."""
    cid = _contract()
    a = _plan(cid, "Q3 crew-up")
    conn = db.get_conn()
    conn.execute("UPDATE plans SET is_baseline = NULL WHERE id = ?", (a,))
    conn.commit()
    conn.close()

    assert client.get(f"/api/contracts/{cid}/plans").json()[0]["is_baseline"] is False
    assert db.get_baseline_plan(cid) is None
