"""A synced timesheet batch has to belong to the contract it is stored against.

Fixtura draws its whole scenario from `seed + opts` — award, PIID, CLINs, and only
then a roster crewed off that award's own labor lines. So a sync run against a seed
this contract never recorded does not return *noisy* data, it returns a different
contract's labor: real people, real hours, charging CLIN numbers this award does not
contain, under LCATs it never priced. Nothing downstream can tell that apart from a
genuine pricing gap, which is why six of nine contracts in the dev DB sat under a
standing pile of unmatched-LCAT flags that no amount of LCAT mapping could fix.

`contract_no` is the one field that says so. These tests cover the comparison
(`sources.provenance`), the gate on the sync route, and the seed/opts pairing the
route pins once a batch has been verified as this contract's.

Fixtura is stubbed throughout: the point under test is what Runway does with a batch,
not what Fixtura draws, and a test that needs a live generator on :8000 to prove a
guard clause would be skipped exactly when it mattered.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, main, sources  # noqa: E402
from app.schemas import CLIN, ContractHeader, Extraction, LaborRate  # noqa: E402

PIID = "N66048-24-C-7647"
OTHER = "GS-31F-2774F"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with TestClient(main.app) as c:
        yield c


def _rows(contract_no, n=3, lcat="Business Analyst", clin="1001"):
    return [
        {
            "employee": f"Person {i}",
            "employee_id": f"E-{i:05d}",
            "week_ending": "2026-08-07",
            "contract_no": contract_no,
            "charge_code": clin,
            "labor_category": lcat,
            "total_hours": 40.0,
        }
        for i in range(n)
    ]


def _save(client, piid=PIID, seed=None):
    extraction = Extraction(
        contract=ContractHeader(piid=piid),
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
    q = f"?seed={seed}" if seed is not None else ""
    r = client.post(f"/api/contracts/confirm{q}", json=extraction.model_dump())
    assert r.status_code == 200
    return r.json()


def _fixtura(monkeypatch, batches):
    """Stub Fixtura, keyed by the seed asked for. Records every (seed, opts) pair the
    route requested, which is how the pinning tests see what a re-sync replayed."""
    calls = []

    def fake(rows=None, seed=None, opts=None):
        calls.append({"seed": seed, "opts": opts})
        return batches[seed]

    monkeypatch.setattr(sources, "fetch_timesheets", fake)
    return calls


# --- the comparison itself -------------------------------------------------


def test_matching_batch_is_clean():
    check = sources.provenance(_rows(PIID), PIID)
    assert (check["checked"], check["matched"], check["foreign_rows"]) == (True, 3, 0)


def test_foreign_batch_names_the_contract_it_belongs_to():
    check = sources.provenance(_rows(OTHER, n=5), PIID)
    assert check["foreign"] == {OTHER: 5}
    assert check["foreign_rows"] == 5
    assert check["matched"] == 0


def test_case_and_padding_are_not_a_mismatch():
    """The same contract typed two ways is still the same contract."""
    check = sources.provenance(_rows(f"  {PIID.lower()} "), PIID)
    assert check["foreign_rows"] == 0
    assert check["matched"] == 3


def test_dashes_are_structural_and_not_folded_away():
    """A DoD PIID's dashes separate the issuing office, fiscal year, type and serial.
    Two contracts that differ only in where the dashes fall are two contracts."""
    check = sources.provenance(_rows("N6604-824-C-7647"), PIID)
    assert check["foreign_rows"] == 3


def test_rows_without_a_contract_number_are_unattributed_not_foreign():
    """Only Fixtura fills this field in. A hand-built CSV that omits it is not
    evidence of anything, and refusing it would gate real uploads on a generator
    artefact."""
    check = sources.provenance(_rows(None) + _rows(""), PIID)
    assert (check["unattributed"], check["foreign_rows"]) == (6, 0)


def test_a_contract_with_no_piid_cannot_be_checked():
    """Manual entry doesn't require a PIID, and a verdict invented from a blank would
    refuse every sync those contracts ever make."""
    check = sources.provenance(_rows(OTHER), None)
    assert check["checked"] is False
    assert check["foreign_rows"] == 0


def test_several_foreign_contracts_report_the_biggest_first():
    rows = _rows(OTHER, n=2) + _rows("W45983-24-C-1675", n=7)
    check = sources.provenance(rows, PIID)
    assert list(check["foreign"]) == ["W45983-24-C-1675", OTHER]


# --- the gate on the sync route --------------------------------------------


def test_sync_refuses_a_foreign_batch_and_stores_nothing(client, monkeypatch):
    saved = _save(client)
    seed = sources.seed_for_piid(PIID)
    _fixtura(monkeypatch, {seed: _rows(OTHER, n=4)})

    r = client.post(f"/api/contracts/{saved['id']}/timesheets/sync")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert OTHER in detail and PIID in detail
    assert "Data seed" in detail  # points at the fix, not just the failure
    assert db.get_timesheets(saved["id"]) == []


def test_a_refused_sync_leaves_the_rows_already_there(client, monkeypatch):
    """The gate runs before replace_timesheets' delete-then-insert. Wiping a good
    batch to store nothing would be a worse outcome than the mismatch it guards."""
    saved = _save(client, seed=7)
    calls = _fixtura(monkeypatch, {7: _rows(PIID, n=3), 99: _rows(OTHER, n=4)})

    assert (
        client.post(f"/api/contracts/{saved['id']}/timesheets/sync").status_code == 200
    )
    assert len(db.get_timesheets(saved["id"])) == 3

    r = client.post(f"/api/contracts/{saved['id']}/timesheets/sync?seed=99")
    assert r.status_code == 409
    assert len(db.get_timesheets(saved["id"])) == 3
    assert [c["seed"] for c in calls] == [7, 99]


def test_allow_mismatch_stores_but_says_so(client, monkeypatch):
    saved = _save(client)
    seed = sources.seed_for_piid(PIID)
    _fixtura(monkeypatch, {seed: _rows(OTHER, n=4)})

    r = client.post(f"/api/contracts/{saved['id']}/timesheets/sync?allow_mismatch=true")
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == 4
    assert body["provenance"]["foreign"] == {OTHER: 4}
    assert body["warning"] and OTHER in body["warning"]
    # It must not read like the refusal — these rows ARE stored and being priced.
    assert "allow_mismatch" not in body["warning"]


def test_a_waved_through_mismatch_is_never_pinned(client, monkeypatch):
    """Pinning the pairing that produced foreign rows would make the wrong seed this
    contract's new baseline, and every later auto-sync would reproduce it."""
    saved = _save(client)
    seed = sources.seed_for_piid(PIID)
    _fixtura(monkeypatch, {seed: _rows(OTHER)})

    client.post(f"/api/contracts/{saved['id']}/timesheets/sync?allow_mismatch=true")
    assert db.get_contract(saved["id"]).get("sync_seed") is None


def test_a_clean_batch_reports_its_provenance_too(client, monkeypatch):
    saved = _save(client, seed=7)
    _fixtura(monkeypatch, {7: _rows(PIID, n=3)})

    body = client.post(f"/api/contracts/{saved['id']}/timesheets/sync").json()
    assert body["warning"] is None
    assert body["provenance"]["matched"] == 3
    assert body["seed"] == 7


def test_an_unattributed_batch_still_syncs(client, monkeypatch):
    saved = _save(client, seed=7)
    _fixtura(monkeypatch, {7: _rows(None, n=3)})

    r = client.post(f"/api/contracts/{saved['id']}/timesheets/sync")
    assert r.status_code == 200
    assert r.json()["provenance"]["unattributed"] == 3


# --- seed provenance -------------------------------------------------------


def test_confirm_records_the_seed_and_echoes_it(client):
    saved = _save(client, seed=42)
    assert saved["sync_seed"] == 42
    assert db.get_contract(saved["id"])["sync_seed"] == 42


def test_confirm_without_a_seed_records_none(client):
    saved = _save(client)
    assert saved["sync_seed"] is None


def test_the_recorded_seed_is_what_the_sync_asks_for(client, monkeypatch):
    """The whole point of recording it: a later sync must not fall back to the
    PIID hash, which draws somebody else's award."""
    saved = _save(client, seed=7)
    derived = sources.seed_for_piid(PIID)
    assert derived != 7
    calls = _fixtura(monkeypatch, {7: _rows(PIID), derived: _rows(OTHER)})

    assert (
        client.post(f"/api/contracts/{saved['id']}/timesheets/sync").status_code == 200
    )
    assert [c["seed"] for c in calls] == [7]


def test_a_clean_sync_pins_the_seed_it_used(client, monkeypatch):
    """A derived seed that happens to draw the right contract is worth recording:
    the pairing is known-good now, and pinning it stops a later change to the
    derivation from quietly re-baselining the contract."""
    saved = _save(client)
    seed = sources.seed_for_piid(PIID)
    _fixtura(monkeypatch, {seed: _rows(PIID)})

    client.post(f"/api/contracts/{saved['id']}/timesheets/sync")
    assert db.get_contract(saved["id"])["sync_seed"] == seed


def test_a_pinned_contract_replays_its_opts_not_freshly_derived_ones(
    client, monkeypatch
):
    """Seed and opts are one pairing — Fixtura draws the contract from both — so a
    re-sync that replayed the seed against re-derived opts could still land on a
    different award. Editing the award after a clean sync must not silently move the
    batch."""
    saved = _save(client, seed=7)
    calls = _fixtura(monkeypatch, {7: _rows(PIID)})
    client.post(f"/api/contracts/{saved['id']}/timesheets/sync")
    pinned = db.get_contract(saved["id"])["sync_opts"]
    assert pinned == calls[0]["opts"]

    # An edit that would change what derive_scenario_opts returns.
    blob = {
        k: v
        for k, v in db.get_contract(saved["id"]).items()
        if k not in ("id", "piid", "created_at")
    }
    blob["contract"] = {**(blob.get("contract") or {}), "contract_type": "CPFF"}
    db.update_contract(saved["id"], blob)

    client.post(f"/api/contracts/{saved['id']}/timesheets/sync")
    assert calls[1]["opts"] == pinned


def test_an_explicit_seed_repairs_the_pairing(client, monkeypatch):
    """The documented way out of a refusal: a caller naming a seed is asking for a
    new pairing, so the opts are re-derived and both are re-pinned."""
    saved = _save(client, seed=7)
    calls = _fixtura(monkeypatch, {7: _rows(PIID), 21: _rows(PIID)})
    client.post(f"/api/contracts/{saved['id']}/timesheets/sync")

    client.post(f"/api/contracts/{saved['id']}/timesheets/sync?seed=21")
    assert db.get_contract(saved["id"])["sync_seed"] == 21
    assert calls[1]["opts"] == sources.derive_scenario_opts(
        db.get_contract(saved["id"])
    )
