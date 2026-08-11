"""A refused sync has to blame the half that actually disagrees (#137).

The 409 detail is the entire UI for this failure — nothing else on the screen
explains it — so the dial it names is the whole debugging path. The first version
always named the seed, which is wrong for the case that matters most: Fixtura builds
a PIID's fiscal-year digits from the award's effective date, and `pop_in_progress`
moves that date back a year per option period, so one seed draws `-24-` as historical
and `-25-` as in-progress. On that batch the seed is correct, recorded, and nothing a
user types into `?seed=` can reconcile it — the opts can.

These tests cover the structural verdict (`sources.piid_relation`), the opts text a
refusal hands back (`sources.format_opts`), and what the message and the sync response
say about where each half of the pairing came from.

Fixtura is stubbed: the point under test is Runway's message, not Fixtura's draw.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, main, sources  # noqa: E402
from app.schemas import CLIN, ContractHeader, Extraction, LaborRate  # noqa: E402

PIID = "N66048-24-C-7647"
RENUMBERED = "N66048-25-C-7647"  # same seed, drawn as in-progress
STRANGER = "GS-31F-2774F"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with TestClient(main.app) as c:
        yield c


def _rows(contract_no, n=3):
    return [
        {
            "employee_id": f"E{i}",
            "employee_name": f"Person {i}",
            "lcat": "Business Analyst",
            "clin": "1001",
            "week_ending": "2026-01-02",
            "hours": 40,
            "contract_no": contract_no,
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


def _fixtura(monkeypatch, rows):
    def fake(rows_=None, seed=None, opts=None, **kw):
        return rows

    monkeypatch.setattr(sources, "fetch_timesheets", fake)


# --- which half disagrees --------------------------------------------------


def test_a_fiscal_year_difference_is_a_renumbering():
    assert sources.piid_relation(PIID, RENUMBERED) == "renumbered"


def test_a_different_serial_is_a_different_contract():
    """The serial is what the seed decides. If it differs, re-deriving the opts will
    never land on this contract and the seed is the honest suspect."""
    assert sources.piid_relation(PIID, "N66048-24-C-1111") == "unrelated"


def test_a_wholly_different_piid_is_unrelated():
    assert sources.piid_relation(PIID, STRANGER) == "unrelated"


def test_two_segments_apart_is_not_a_renumbering():
    """Narrow on purpose: the verdict's whole value is that it points at the one dial
    that can help, so anything less specific than a single FY digit stays unrelated."""
    assert sources.piid_relation(PIID, "N66048-25-F-7647") == "unrelated"


def test_a_non_numeric_second_segment_is_not_a_fiscal_year():
    assert sources.piid_relation("GS-31F-2774F", "GS-99F-2774F") == "unrelated"


def test_matching_piids_are_the_same_contract():
    assert sources.piid_relation(PIID, PIID.lower()) == "same"


def test_a_blank_piid_cannot_be_related():
    assert sources.piid_relation("", PIID) == "unrelated"


# --- the opts text a refusal hands back -----------------------------------


def test_formatted_opts_parse_back_to_the_same_dict():
    """The point of the short form is that the user can paste it into the review
    screen's Opts box, so it has to survive the round trip."""
    opts = {"staffing": 1.0, "pop_in_progress": True, "option_years": 1}
    assert sources.parse_opts(sources.format_opts(opts)) == opts


def test_booleans_render_as_fixtura_spells_them():
    assert sources.format_opts({"pop_in_progress": False}) == "pop_in_progress=false"


def test_a_list_valued_opt_falls_back_to_json():
    text = sources.format_opts({"lcat_lines": [{"lcat": "BA", "ftes": 2}]})
    assert text.startswith("{") and sources.parse_opts(text)["lcat_lines"]


def test_no_opts_says_so_rather_than_printing_an_empty_dict():
    assert sources.format_opts({}) == "(none)"


# --- what the refusal says ------------------------------------------------


def test_a_renumbering_blames_the_opts_and_not_the_seed(client, monkeypatch):
    saved = _save(client, seed=60254)
    _fixtura(monkeypatch, _rows(RENUMBERED, n=4))

    r = client.post(f"/api/contracts/{saved['id']}/timesheets/sync")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "OPTS" in detail and "pop_in_progress" in detail
    assert "?opts=" in detail
    # The seed is right and recorded. Telling the user to change it is the bug.
    assert "no longer draws it" not in detail
    assert "?seed=<n> to re-pin" not in detail
    assert "Data seed" not in detail or "Opts" in detail


def test_a_renumbering_says_no_seed_can_fix_it(client, monkeypatch):
    saved = _save(client, seed=60254)
    _fixtura(monkeypatch, _rows(RENUMBERED))

    detail = client.post(f"/api/contracts/{saved['id']}/timesheets/sync").json()[
        "detail"
    ]
    assert "fiscal-year" in detail
    assert "cannot reconcile" in detail


def test_the_suggested_opts_are_the_pairing_with_the_knob_flipped(client, monkeypatch):
    """A renumbering has a knowable repair, so the message hands back a pasteable
    pairing rather than the one that just failed plus 'edit this'. The derived opts on
    an in-progress award set pop_in_progress; the suggestion has to clear it."""
    saved = _save(client, seed=60254)
    _fixtura(monkeypatch, _rows(RENUMBERED))
    monkeypatch.setattr(
        sources,
        "derive_scenario_opts",
        lambda c: {"staffing": 1.0, "pop_in_progress": True},
    )

    detail = client.post(f"/api/contracts/{saved['id']}/timesheets/sync").json()[
        "detail"
    ]
    assert "?opts=pop_in_progress=false, staffing=1.0" in detail


def test_the_suggestion_adds_the_knob_when_the_batch_lacked_it(client, monkeypatch):
    """Renumbering runs both ways: a historical draw of an award that is actually
    in-progress needs the knob switched ON."""
    saved = _save(client, seed=60254)
    _fixtura(monkeypatch, _rows(RENUMBERED))
    monkeypatch.setattr(sources, "derive_scenario_opts", lambda c: {"staffing": 1.0})

    detail = client.post(f"/api/contracts/{saved['id']}/timesheets/sync").json()[
        "detail"
    ]
    assert "?opts=pop_in_progress=true, staffing=1.0" in detail


def test_a_recorded_seed_is_not_reported_as_stale(client, monkeypatch):
    """A genuinely foreign batch on a contract that DID record a seed: still a seed
    problem, but the message must not claim the recorded seed went bad."""
    saved = _save(client, seed=7)
    _fixtura(monkeypatch, _rows(STRANGER))

    detail = client.post(f"/api/contracts/{saved['id']}/timesheets/sync").json()[
        "detail"
    ]
    assert "recorded for this award at ingest" in detail
    assert "no longer draws it" not in detail


def test_a_contract_with_no_seed_is_still_sent_to_the_seed_field(client, monkeypatch):
    """The one case the old message got right, kept: nothing was recorded, the seed
    was hashed out of the PIID, and typing the real one is the fix."""
    saved = _save(client)
    _fixtura(monkeypatch, _rows(STRANGER))

    detail = client.post(f"/api/contracts/{saved['id']}/timesheets/sync").json()[
        "detail"
    ]
    assert "No Fixtura seed is recorded" in detail
    assert "Data seed" in detail


def test_a_refusal_reports_the_pairing_it_tried(client, monkeypatch):
    """#137's second half: the pin is only reachable if the user can see what was
    derived on their behalf, which was invisible from outside the route."""
    saved = _save(client, seed=60254)
    _fixtura(monkeypatch, _rows(RENUMBERED))

    detail = client.post(f"/api/contracts/{saved['id']}/timesheets/sync").json()[
        "detail"
    ]
    assert "seed 60254 (recorded at ingest)" in detail
    assert "derived from the award" in detail
    assert "staffing=1.0" in detail


def test_stated_halves_are_reported_as_stated(client, monkeypatch):
    saved = _save(client, seed=7)
    _fixtura(monkeypatch, _rows(STRANGER))

    detail = client.post(
        f"/api/contracts/{saved['id']}/timesheets/sync"
        "?seed=99&opts=pop_in_progress=false"
    ).json()["detail"]
    assert "seed 99 (stated on this sync)" in detail
    assert "pop_in_progress=false (stated on this sync)" in detail


def test_allow_mismatch_is_named_but_not_offered_as_a_fix(client, monkeypatch):
    saved = _save(client)
    _fixtura(monkeypatch, _rows(STRANGER))

    detail = client.post(f"/api/contracts/{saved['id']}/timesheets/sync").json()[
        "detail"
    ]
    assert "allow_mismatch" in detail
    assert "not a fix" in detail
    assert "disagrees with" in detail


def test_a_clean_sync_reports_where_each_half_came_from(client, monkeypatch):
    saved = _save(client, seed=7)
    _fixtura(monkeypatch, _rows(PIID))

    body = client.post(f"/api/contracts/{saved['id']}/timesheets/sync").json()
    assert body["seed_source"] == "recorded at ingest"
    assert body["opts_source"] == "derived from the award"


def test_a_pinned_pairing_says_it_was_pinned(client, monkeypatch):
    """Second sync: the first one pinned the opts, so the second replays them by
    record — and a later refusal has to distinguish that from a fresh guess."""
    saved = _save(client, seed=7)
    _fixtura(monkeypatch, _rows(PIID))
    client.post(f"/api/contracts/{saved['id']}/timesheets/sync")

    body = client.post(f"/api/contracts/{saved['id']}/timesheets/sync").json()
    assert body["opts_source"] == "pinned by an earlier clean sync"


def test_a_scenario_names_itself_as_the_source(client, monkeypatch):
    saved = _save(client)
    _fixtura(monkeypatch, _rows(PIID))

    body = client.post(
        f"/api/contracts/{saved['id']}/timesheets/sync?scenario=red"
    ).json()
    assert body["seed_source"] == "from scenario 'red'"
    assert body["opts_source"] == "from scenario 'red'"
