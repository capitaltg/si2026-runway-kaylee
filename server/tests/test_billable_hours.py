"""#85 — leave is not a direct charge to a CLIN.

Fixtura used to define `total_hours` as reg + ot + leave, and Runway priced that
figure against the CLIN's loaded rate. Since a loaded rate already carries fringe,
and leave is recovered through the fringe pool (FAR 31.205-6), that billed PTO
twice and overstated every contract's burn by the leave share.

Fixtura's counterpart fix (capitaltg/si2026-test-generator#60) redefined
`total_hours` as billable-only and added the split alongside it, deliberately
keeping the *name* so this side's existing read became correct rather than
silently reading 0. These tests pin both halves: the new contract prices reg + ot,
and a row cached under the old contract still gets its leave backed out.
"""

from app import burn


def _row(**kw):
    base = {
        "charge_code": "0001",
        "labor_category": "Software Engineer",
        "week_ending": "2026-01-02",
        "employee_id": "e1",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- the helper


def test_split_present_prices_reg_plus_ot():
    # The authoritative case: the source sends the split, so reg + ot IS billable.
    h = burn.billable_hours(
        _row(total_hours=42, reg_hours=40, ot_hours=2, holiday_hours=8, leave_hours=16)
    )
    assert h == 42


def test_split_present_ignores_leave_and_holidays_entirely():
    # A week that was all leave bills nothing, however many hours were *paid*.
    h = burn.billable_hours(
        _row(total_hours=0, reg_hours=0, ot_hours=0, holiday_hours=8, leave_hours=32)
    )
    assert h == 0


def test_missing_ot_is_not_missing_billable_hours():
    # reg present is the version signal; a null ot must read as zero, not as absent.
    assert burn.billable_hours(_row(total_hours=40, reg_hours=40, ot_hours=None)) == 40


def test_legacy_row_backs_leave_out_of_total():
    # No reg_hours → a row cached before the split existed, where total was all-in.
    assert burn.billable_hours(_row(total_hours=48, leave_hours=8)) == 40


def test_legacy_row_never_credits_the_clin():
    # Malformed: more leave than total. Contributes nothing rather than a negative.
    assert burn.billable_hours(_row(total_hours=8, leave_hours=40)) == 0


def test_no_split_at_all_takes_total_at_its_word():
    # A source that reports no leave isn't guessed at.
    assert burn.billable_hours(_row(total_hours=40)) == 40


def test_absent_hours_are_zero_not_an_error():
    assert burn.billable_hours(_row()) == 0
    assert burn.billable_hours(_row(total_hours=None)) == 0


# ------------------------------------------------------- through the engine

_CLIN = {
    "clin": "0001",
    "title": "Professional Services (Labor)",
    "is_labor": True,
    "ceiling": 500000,
    "est_hours": 4000,
    "labor_rates": [{"lcat": "Software Engineer", "loaded_rate": 100.0}],
}


def _contract():
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-85",
            "total_ceiling": 500000,
            "total_obligated": None,
        },
        "clins": [dict(_CLIN)],
        "periods": [],
    }


def _weeks(n=6, **kw):
    return [_row(week_ending=f"2026-01-{2 + 7 * i:02d}", **kw) for i in range(n)]


def test_leave_does_not_reach_the_dollars():
    # Six weeks of 40 billable + 8 leave, at $100/hr. Only the 40s may be priced.
    spent = burn.compute(
        _contract(), _weeks(total_hours=40, reg_hours=32, ot_hours=8, leave_hours=8)
    )["clins"][0]["spent"]
    assert spent == 6 * 40 * 100.0


def test_a_leave_week_costs_the_contract_nothing():
    rows = _weeks(5, total_hours=40, reg_hours=40, ot_hours=0, leave_hours=0)
    rows.append(
        _row(
            week_ending="2026-02-06",
            total_hours=0,
            reg_hours=0,
            ot_hours=0,
            leave_hours=40,
        )
    )
    spent = burn.compute(_contract(), rows)["clins"][0]["spent"]
    assert spent == 5 * 40 * 100.0


def test_legacy_rows_are_priced_below_their_all_in_total():
    # The regression this ticket is about: the same feed, priced the old way, would
    # have charged 48 hrs/wk. It must now charge 40.
    spent = burn.compute(_contract(), _weeks(total_hours=48, leave_hours=8))["clins"][
        0
    ]["spent"]
    assert spent == 6 * 40 * 100.0
