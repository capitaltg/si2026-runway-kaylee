"""Expected hours is a whole-person figure, so its consumers must be too (#116).

Every person in the local `runway.db` charges exactly one contract, which is why this
blind spot looked fine on the dashboard: the bug needs someone on two contracts to
show at all, so it needs a fixture rather than an eyeball.

Driven through the real `booked_hours` → `allocation` → `heat` → `suggest` chain, and
the "elsewhere" map is built here the same way `main._hours_elsewhere` builds it — a
hand-written map would pass while the wiring was wrong.
"""

import pytest

from app import allocation, heat, suggest

WEEKS = ["2026-02-06", "2026-02-13", "2026-02-20", "2026-02-27"]
SE = "Systems Engineer"
DANA = "danareed"


def _contract(cid, piid, ceiling, rate=100.0):
    return {
        "id": cid,
        "contract": {"piid": piid, "contractor": piid, "total_ceiling": ceiling},
        "clins": [
            {
                "clin": "0001",
                "title": "Engineering",
                "is_labor": True,
                "ceiling": ceiling,
                "obligated": ceiling,
                "labor_rates": [{"lcat": SE, "loaded_rate": rate}],
            }
        ],
        "periods": [
            {
                "name": "Base",
                "exercised": True,
                "pop_start": "2026-01-02",
                "pop_end": "2026-12-31",
                "ceiling": ceiling,
            }
        ],
    }


def _rows(people, leave=0.0):
    """`{name: hrs_per_wk}` charged flat across the window."""
    out = []
    for name, hours in people.items():
        for wk in WEEKS:
            out.append(
                {
                    "employee": name,
                    "employee_id": name.replace(" ", "").lower(),
                    "week_ending": wk,
                    "charge_code": "0001",
                    "labor_category": SE,
                    "total_hours": hours,
                    "reg_hours": min(hours, 40.0),
                    "ot_hours": max(0.0, hours - 40.0),
                    "leave_hours": leave,
                    "holiday_hours": 0.0,
                }
            )
    return out


def _elsewhere(others):
    """`main._hours_elsewhere`'s payload, built the way the endpoint builds it:
    `{employee_id: [{contract_id, contract, hours, leave, holiday}]}` over every
    *other* contract."""
    out = {}
    for contract, rows in others:
        away = heat.absence_hours(contract, rows)
        for emp, hrs in allocation.booked_hours(contract, rows).items():
            out.setdefault(emp, []).append(
                {
                    "contract_id": contract["id"],
                    "contract": contract["contract"]["piid"],
                    "hours": hrs,
                    **(away.get(emp) or {"leave": 0.0, "holiday": 0.0}),
                }
            )
    return out


def _split(here, there, ceiling=900_000.0, leave_here=0.0, leave_there=0.0):
    """One person on two contracts: `here` hrs/wk on the contract under test, `there`
    on another. Returns (alloc, heat payload, rows) for the contract under test."""
    a = _contract(1, "ALPHA", ceiling)
    a_rows = _rows({"Dana Reed": here}, leave=leave_here)
    b = _contract(2, "BRAVO", ceiling)
    b_rows = _rows({"Dana Reed": there}, leave=leave_there)
    alloc = allocation.compute_allocation(
        a, a_rows, hours_elsewhere_by_person=_elsewhere([(b, b_rows)])
    )
    return alloc, heat.compute_heat(a, a_rows, alloc), a_rows


def _row_for(alloc, emp=DANA):
    return next(r for r in alloc["employees"] if r["id"] == emp)


# --- the cross-contract sum itself -------------------------------------------


def test_booked_hours_reconciles_with_the_matrix_it_is_compared_against():
    """The whole point of extracting it from `compute_allocation` is that summing it
    across contracts sums the numbers the grid shows. A second walk of the timesheets
    that rounded differently would put headroom permanently a fraction off."""
    contract, rows = _contract(1, "ALPHA", 900_000.0), _rows({"Dana Reed": 22.0})
    alloc = allocation.compute_allocation(contract, rows)
    assert allocation.booked_hours(contract, rows)[DANA] == _row_for(alloc)["hours"]


def test_a_persons_hours_on_another_contract_ride_on_their_row():
    alloc, _, _ = _split(here=20.0, there=20.0)
    row = _row_for(alloc)
    assert row["hours"] == 20.0, "this contract's cells are unchanged"
    assert row["hours_elsewhere"] == 20.0
    assert row["hours_booked"] == 40.0
    assert [e["contract"] for e in row["elsewhere"]] == ["BRAVO"]
    assert alloc["contract"]["cross_contract"] is True


def test_headroom_is_what_is_left_after_every_contract():
    """20 + 20 against a 40-hour expectation is fully booked, on both contracts."""
    alloc, _, _ = _split(here=20.0, there=20.0)
    row = _row_for(alloc)
    assert row["expected"]["hours"] == 40.0
    assert row["headroom"] == 0.0


def test_headroom_never_goes_negative():
    """Someone already past their week has no slack to offer, not negative slack."""
    alloc, _, _ = _split(here=30.0, there=30.0)
    assert _row_for(alloc)["headroom"] == 0.0


def test_a_payload_built_without_the_sweep_says_so():
    """`cross_contract` false is the honest reading of a headroom nobody checked —
    the old behaviour is kept, but it is never passed off as a verified figure."""
    contract, rows = _contract(1, "ALPHA", 900_000.0), _rows({"Dana Reed": 20.0})
    alloc = allocation.compute_allocation(contract, rows)
    row = _row_for(alloc)
    assert alloc["contract"]["cross_contract"] is False
    assert row["hours_elsewhere"] == 0.0
    assert row["headroom"] == 20.0


# --- the dangerous direction: the solver offering slack that does not exist ----


def _raise_moves(alloc, cid="0001"):
    """`_raise_plan` against a target twice the current burn, so the gap is wide
    enough that anything with headroom will be offered."""
    card = next(c for c in alloc["clins"] if c["id"] == cid)
    rows_on_clin = [
        (r, r["cells"][cid]) for r in alloc["employees"] if cid in r["cells"]
    ]
    weekly = card["base_weekly"] or 1.0
    moves, _freed, _gap, _unpriced = suggest._raise_plan(
        card, rows_on_clin, cid, weekly * 2, weekly
    )
    return moves


def test_an_underburning_line_is_not_offered_hours_the_person_does_not_have():
    """The same 20 hours were being offered to two different underburning lines,
    because each contract's payload computed headroom from its own rows only. Booking
    both suggestions is a 60-hour week from a function whose docstring promises it
    never books anyone past their expectation."""
    alloc, _, _ = _split(here=20.0, there=20.0)
    assert _raise_moves(alloc) == []


def test_real_slack_is_still_fair_game():
    """The fix must not answer "no headroom, ever" — 10 + 10 against a 40-hour week is
    20 genuinely free hours, and an underburning line should still be offered them."""
    alloc, _, _ = _split(here=10.0, there=10.0)
    (move,) = _raise_moves(alloc)
    assert move["kind"] == "raise"
    assert move["to_hours"] == 30.0, "10 here + the 20 they actually have left"


# --- the inverted blind spot: nobody surfacing as hot -------------------------


def test_someone_full_across_two_contracts_surfaces_as_running_hot():
    """25 here and 20 there is 45 against a 40-hour week. Scoped to one contract they
    clear the threshold on neither and are never named.

    The ceilings below put the CLIN off pace on purpose: #83's gate is the *line*
    being in trouble, not the person's hours, and that gate is unchanged here.
    """
    _, hot, _ = _split(here=25.0, there=20.0, ceiling=110_000.0)
    (person,) = hot["people"]
    assert person["id"] == DANA
    assert person["worked_hours_elsewhere"] == pytest.approx(80.0)  # 20/wk x 4 weeks
    assert person["worked_hours_booked"] == pytest.approx(180.0)
    assert person["over_hours_per_week"] == pytest.approx(5.0)
    assert [e["contract"] for e in person["elsewhere"]] == ["BRAVO"]


def test_this_contract_carries_only_its_share_of_the_excess():
    """The excess apportions across everywhere the hours are. Charging the whole 5
    hrs/wk to this line would price another contract's overtime onto this budget."""
    _, hot, _ = _split(here=25.0, there=20.0, ceiling=110_000.0)
    (person,) = hot["people"]
    (impact,) = person["clins"]
    # 5 hrs/wk over x this contract's 25/45 share x $100.
    assert impact["weekly_dollars"] == pytest.approx(5 * (25 / 45) * 100.0, rel=1e-3)


def test_leave_taken_on_another_contract_still_comes_off_their_week():
    """Leave belongs to the person's week, not to the contract that recorded it.

    Availability is an expectation minus absence, and once the hours above it are
    counted across every contract the absence has to be too. Deducting leave only
    where the PTO code happens to live let two contracts reach two verdicts about one
    person in one window — live, one said 10 hrs/wk over and the other 30.
    """
    _alloc, hot_here, _rows_here = _split(
        here=25.0, there=20.0, ceiling=110_000.0, leave_there=8.0
    )
    (from_here,) = hot_here["people"]
    # The mirror image: same two contracts, same window, roles swapped. BRAVO gets a
    # tighter ceiling because it burns less — #83's gate is the CLIN being off pace,
    # and a comfortable line lists nobody no matter how over its people are.
    b = _contract(2, "BRAVO", 80_000.0)
    b_rows = _rows({"Dana Reed": 20.0}, leave=8.0)
    a = _contract(1, "ALPHA", 110_000.0)
    a_rows = _rows({"Dana Reed": 25.0})
    alloc = allocation.compute_allocation(
        b, b_rows, hours_elsewhere_by_person=_elsewhere([(a, a_rows)])
    )
    (from_there,) = heat.compute_heat(b, b_rows, alloc)["people"]

    assert from_here["leave_hours"] == from_there["leave_hours"] == 32.0
    assert from_here["available_hours"] == from_there["available_hours"]
    assert from_here["over_hours_per_week"] == from_there["over_hours_per_week"]


def test_nobody_inside_their_week_is_flagged_by_the_cross_contract_sum():
    """A part-timer at 15 + 15 is not running hot, and the sum must not invent one —
    even on a line that is genuinely over, which is the case that would flood the
    panel with everyone who happens to bill two contracts."""
    alloc, hot, _ = _split(here=15.0, there=15.0, ceiling=55_000.0)
    assert [c["base_status"] for c in alloc["clins"]] == ["over"]
    assert hot["people"] == []


# --- the trim that was being silently suppressed ------------------------------


def test_a_trim_is_offered_against_the_persons_real_share_of_their_week():
    """`share = cell_hours / total_hours` inflated toward 1.0 when scoped to one
    contract, so `at_expected` landed at the full 40 against a 30-hour booking and no
    trim was ever offered. 30 here of a 60-hour booked week is half of it, so the
    at-expected level for this line is half a 40-hour week."""
    alloc, hot, rows = _split(here=30.0, there=30.0, ceiling=130_000.0)
    plans = suggest.solve_moves(alloc, hot)
    plan = next(p for p in plans if p["clin"] == "0001")
    assert plan["diagnosis"] == heat.STOP_OVERTIME
    trims = [m for m in plan["moves"] if m["kind"] == "trim"]
    assert trims, "a person over their week across two contracts must be trimmable"
    assert trims[0]["from_hours"] == 30.0
    assert trims[0]["to_hours"] == 20.0
