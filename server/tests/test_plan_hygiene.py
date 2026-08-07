"""Plan hygiene (#67 item 5) — the storage half.

A saved plan only becomes load-bearing if you can tell what it is: when it was
written, whether it has been edited since, and what assumptions it was scored
against. These pin the parts of that which live in the database.

The scoring snapshot itself is built and compared on the client (`web/src/plans.js`,
with its own tests) — the server stores it verbatim inside `data`, and the thing
worth pinning here is that it survives a round trip, including through an update.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A throwaway database. DB_PATH is resolved at import time, so patch the
    module rather than the environment."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return db


def _contract(store):
    return store.save_contract("FA8750-26-C-0001", {"contract": {"piid": "x"}})


PLAN = {
    "draft": {"7": {"0001": 24}},
    "added": [{"id": "added-3f2a", "name": "New BA", "rates": {"0001": 150}}],
    "removed": [],
    "absences": [],
    "scored_against": {
        "period": "Base",
        "clins": {"0001": {"budget": 900000, "ceiling": 1200000}},
        "rates": {"7": {"0001": 168.25}},
    },
}


def test_a_new_plan_is_saved_not_updated(store):
    cid = _contract(store)
    row = store.save_plan(cid, "Q3 crew-up", PLAN)

    assert row["created_at"]
    # NULL, not created_at: the menu draws "Saved" vs "Updated" off this, and a
    # default would claim a brand-new plan had already been edited.
    assert row["updated_at"] is None


def test_saving_over_a_plan_stamps_it_updated(store):
    cid = _contract(store)
    saved = store.save_plan(cid, "Q3 crew-up", PLAN)

    edited = {**PLAN, "draft": {"7": {"0001": 32}}}
    row = store.update_plan(cid, saved["id"], "Q3 crew-up", edited)

    assert row["updated_at"] is not None
    assert row["created_at"] == saved["created_at"]

    listed = store.list_plans(cid)
    assert len(listed) == 1, "an update must not fork the plan (#62)"
    assert listed[0]["updated_at"] == row["updated_at"]
    assert listed[0]["data"]["draft"] == {"7": {"0001": 32}}


def test_the_scoring_snapshot_round_trips(store):
    cid = _contract(store)
    saved = store.save_plan(cid, "Q3 crew-up", PLAN)

    listed = store.list_plans(cid)[0]
    assert listed["data"]["scored_against"] == PLAN["scored_against"]

    # And it must follow an edit, or a plan updated after a mod would keep claiming
    # the terms it was first written under.
    moved = {**PLAN["scored_against"], "clins": {"0001": {"budget": 1050000}}}
    store.update_plan(cid, saved["id"], "Q3 crew-up", {**PLAN, "scored_against": moved})
    assert store.list_plans(cid)[0]["data"]["scored_against"] == moved


def test_a_plan_saved_before_snapshots_existed_still_loads(store):
    cid = _contract(store)
    legacy = {"draft": {"7": {"0001": 24}}, "added": [{"id": "added-0"}]}
    store.save_plan(cid, "Old plan", legacy)

    row = store.list_plans(cid)[0]
    assert row["data"] == legacy
    assert "scored_against" not in row["data"]
    assert row["updated_at"] is None


def test_updated_at_arrives_on_a_database_that_predates_it(
    store, tmp_path, monkeypatch
):
    """The additive-migration half: an existing plans table gains the column."""
    conn = store.get_conn()
    conn.execute("DROP TABLE plans")
    conn.execute(
        """CREATE TABLE plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER,
            name TEXT,
            data TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute("INSERT INTO plans (contract_id, name, data) VALUES (1, 'Old', '{}')")
    conn.commit()
    conn.close()

    store.init_db()

    rows = store.list_plans(1)
    assert rows[0]["name"] == "Old"
    assert rows[0]["updated_at"] is None
