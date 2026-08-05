"""#85 — dated absence bends the forward projection, and bends nothing else.

The matrix projected one flat hrs/wk to the end of the period, so a runway date the
PM already knew was wrong — two people are out in August — could not be corrected
except by fudging hrs/wk to a blended average. This suite pins the forward model.

What is pinned here, in rough order of how expensive it would be to get wrong:

  1. **The projection is additive.** With no absence entered, `burn.compute` returns
     a payload byte-identical to the pre-ticket one and `projection` is None. With
     absence entered, every existing figure — `weekly`, `weeks_left`, `exhaust_week`,
     `runway_days`, `status`, the tripwires — still holds its flat-pace value, and
     only the new key differs. Flight Deck cards, tripwires, suggests, Portfolio and
     Ask Runway all read that payload, so this is the property that keeps them safe.
  2. Absence reduces a CLIN's pace **in proportion to the absent person's share** of
     it. Half a two-person CLIN out for two weeks buys exactly one week of runway.
  3. Absence in the **past** does nothing. Leave already charged is Part 1's
     territory (`burn.billable_hours`, PR #95), and re-applying it here would
     double-count it.
  4. A person with **no charges** on the CLIN cannot move it. There is no honest
     amount to subtract for someone whose burn was never observed.
  5. Holidays and one person's PTO are **unioned**, not summed, so a fortnight
     spanning July 4th removes ten workdays and not eleven.
  6. The projection is withheld in exactly the states the straight line is already
     withheld in — paused, over budget, past PoP — because those are the geometries
     `BurnChart` special-cases and a series must never appear where one can't draw.

DB-free, like the rest of this suite. The arithmetic is deliberately hand-checkable:
$100/hr, two people at 20 hrs/wk, $4,000 a week.
"""

from datetime import date, timedelta

from app import absence, burn

_PERIOD = {"name": "Base", "pop_start": "2026-01-01", "pop_end": "2026-12-31"}

# $100k over 1,000 est hours → a clean $100/hr blended rate. Two people at 20 hrs/wk
# is $4,000/week, so ten weeks is $40,000 spent and $60,000 (15 weeks) of runway.
_CEILING = 100_000
_EST_HOURS = 1_000
_RATE = 100
_PER_PERSON_HOURS = 20
_WEEKLY = 2 * _PER_PERSON_HOURS * _RATE  # $4,000
_WEEKS = 10
_FIRST_WEEK = date(2026, 1, 2)
_SPENT = _WEEKS * _WEEKLY  # $40,000
_FLAT_EXHAUST = 25.0  # week 10 + ($60,000 / $4,000)

# Weeks are numbered from `pop_start`, so period week 12 is the seven days from
# 2026-03-19. Both of these are full Mon–Fri weeks; the range below covers 12 and 13
# end to end, which is what makes the expected gain exactly one week.
_WEEK_12_START = date(2026, 3, 19)
_WEEK_13_END = date(2026, 4, 1)


def _contract(holidays=None, absences=None):
    contract = {
        "id": 1,
        "contract": {"piid": "TEST-85", "total_ceiling": _CEILING},
        "clins": [
            {
                "clin": "0001",
                "period": "Base",
                "title": "Professional Services (Labor)",
                "is_labor": True,
                "ceiling": _CEILING,
                "est_hours": _EST_HOURS,
            }
        ],
        "periods": [_PERIOD],
    }
    if holidays is not None:
        contract["holidays"] = holidays
    if absences is not None:
        contract["absences"] = absences
    return contract


def _rows(people=("e1", "e2"), weeks=_WEEKS):
    return [
        {
            "charge_code": "0001",
            "labor_category": "Software Engineer",
            "total_hours": _PER_PERSON_HOURS,
            "week_ending": (_FIRST_WEEK + timedelta(weeks=i)).isoformat(),
            "employee_id": who,
        }
        for i in range(weeks)
        for who in people
    ]


def _clin(payload):
    return next(c for c in payload["clins"] if c["id"] == "0001")


def _pto(person, start, end):
    return [{"person_id": person, "start": start.isoformat(), "end": end.isoformat()}]


# --------------------------------------------------- the additive-safety property


def test_no_absence_means_no_projection_and_an_unchanged_payload():
    """The fallback that protects the burn chart: nothing entered, nothing changes."""
    payload = burn.compute(_contract(), _rows())
    clin = _clin(payload)

    assert clin["projection"] is None
    # And the flat-pace figures are exactly what they always were.
    assert clin["weekly"] == _WEEKLY
    assert clin["spent"] == _SPENT
    assert clin["exhaust_week"] == _FLAT_EXHAUST
    assert clin["runway_days"] == 105  # 15 weeks


def test_entering_absence_changes_the_projection_and_nothing_else():
    """Every other key on the CLIN card is identical with and without absence.

    This is the guarantee the additive design exists to make. If this test fails,
    some consumer of the burn payload that has nothing to do with #85 has moved.
    """
    before = _clin(burn.compute(_contract(), _rows()))
    after = _clin(
        burn.compute(
            _contract(absences=_pto("e1", _WEEK_12_START, _WEEK_13_END)), _rows()
        )
    )

    assert before["projection"] is None
    assert after["projection"] is not None

    differing = {k for k in before if before[k] != after.get(k)}
    assert differing == {"projection"}, differing


def test_the_contracts_own_flat_figures_survive_absence():
    """Named explicitly, because these are the fields other views read."""
    payload = burn.compute(
        _contract(absences=_pto("e1", _WEEK_12_START, _WEEK_13_END)), _rows()
    )
    clin = _clin(payload)

    assert clin["weekly"] == _WEEKLY
    assert clin["exhaust_week"] == _FLAT_EXHAUST
    assert clin["weeks_left"] == 15.0
    assert clin["runway_days"] == 105
    # The bent read lives entirely inside the new key, next to the flat one it is
    # to be compared against — a reader never has to remember which is which.
    assert clin["projection"]["flat_exhaust_week"] == _FLAT_EXHAUST


# ------------------------------------------------------------- the bend itself


def test_half_a_clin_out_for_two_weeks_buys_exactly_one_week_of_runway():
    payload = burn.compute(
        _contract(absences=_pto("e1", _WEEK_12_START, _WEEK_13_END)), _rows()
    )
    proj = _clin(payload)["projection"]

    # One of two equal chargers, gone for two full weeks: $4,000 of burn does not
    # happen, which at $4,000/week is one week of runway.
    assert proj["weeks_gained"] == 1.0
    assert proj["exhaust_week"] == _FLAT_EXHAUST + 1
    assert proj["weeks_affected"] == 2
    assert proj["people"] == ["e1"]


def test_the_series_starts_at_today_and_ends_on_the_budget():
    payload = burn.compute(
        _contract(absences=_pto("e1", _WEEK_12_START, _WEEK_13_END)), _rows()
    )
    clin = _clin(payload)
    points = clin["projection"]["points"]

    # Anchored to where the actual line already is, or the chart would draw a step.
    assert points[0] == {"week": 10, "spent": _SPENT}
    # Terminates on the binding budget at the bent exhaust week, which is what makes
    # the "funds run out" marker land on the same point the line ends at.
    assert points[-1] == {"week": 26.0, "spent": float(_CEILING)}
    # Monotonic and one point per week in between: the polyline can be drawn as-is.
    assert [p["week"] for p in points[:5]] == [10, 11, 12, 13, 14]
    assert all(
        b["spent"] >= a["spent"] for a, b in zip(points, points[1:])
    ), "a projection must never run backwards"


def test_the_flat_weeks_of_the_series_still_advance_at_the_full_pace():
    payload = burn.compute(
        _contract(absences=_pto("e1", _WEEK_12_START, _WEEK_13_END)), _rows()
    )
    by_week = {p["week"]: p["spent"] for p in _clin(payload)["projection"]["points"]}

    assert by_week[11] == _SPENT + _WEEKLY  # normal week
    assert by_week[12] == _SPENT + _WEEKLY + _WEEKLY / 2  # half the team out
    assert by_week[13] == _SPENT + _WEEKLY + _WEEKLY  # still out
    assert by_week[14] == _SPENT + _WEEKLY * 2 + _WEEKLY  # back to full pace


# ----------------------------------------------------------- what must not count


def test_absence_in_the_past_does_nothing():
    """Leave already charged was backed out of actuals by PR #95. Applying it again
    here would subtract the same hours twice."""
    payload = burn.compute(
        _contract(absences=_pto("e1", date(2026, 1, 5), date(2026, 1, 16))), _rows()
    )
    assert _clin(payload)["projection"] is None


def test_someone_who_never_charged_this_clin_cannot_move_it():
    """No share of the observed pace means no honest amount to subtract."""
    payload = burn.compute(
        _contract(absences=_pto("stranger", _WEEK_12_START, _WEEK_13_END)), _rows()
    )
    assert _clin(payload)["projection"] is None


def test_a_paused_clin_gets_no_projection():
    """`weekly` is 0, so there is no line to bend — and BurnChart draws none."""
    payload = burn.compute(
        _contract(absences=_pto("e1", _WEEK_12_START, _WEEK_13_END)), _rows(weeks=0)
    )
    clin = _clin(payload)
    assert clin["status"] == "paused"
    assert clin["projection"] is None


def test_an_over_budget_clin_gets_no_projection():
    """Its exhaust week is behind us; a forward line would run backwards across the
    plot, which is the case BurnChart special-cases with its own geometry."""
    payload = burn.compute(
        _contract(absences=_pto("e1", _WEEK_12_START, _WEEK_13_END)),
        _rows(weeks=30),  # 30 weeks x $4,000 = $120,000 against a $100,000 ceiling
    )
    clin = _clin(payload)
    assert clin["spent"] > clin["budget"]
    assert clin["projection"] is None


def test_a_period_with_no_pop_dates_falls_back_to_the_flat_line():
    """No calendar means no way to place a dated absence on a week index."""
    contract = _contract(absences=_pto("e1", _WEEK_12_START, _WEEK_13_END))
    contract["periods"] = [{"name": "Base", "exercised": True}]
    assert _clin(burn.compute(contract, _rows()))["projection"] is None


# ------------------------------------------------------------------- holidays


def test_a_contract_holiday_applies_to_everyone_without_per_person_entry():
    # Memorial Day 2026 is Monday 25 May — period week 21, comfortably inside the
    # 25 weeks of runway this CLIN has.
    payload = burn.compute(_contract(holidays=[{"date": "2026-05-25"}]), _rows())
    proj = _clin(payload)["projection"]

    # One workday of five, for the whole team: a fifth of that week's burn.
    assert proj["weeks_affected"] == 1
    assert proj["holidays"] == ["2026-05-25"]
    assert proj["weeks_gained"] == 0.2
    assert proj["people"] == []  # company-wide, not attributed to anyone


def test_absence_after_the_money_runs_out_draws_no_bend():
    """Independence Day lands in week 27; this CLIN's funds are gone in week 25.
    A holiday the contract never reaches must not produce a second geometry."""
    payload = burn.compute(_contract(holidays=[{"date": "2026-07-03"}]), _rows())
    assert _clin(payload)["projection"] is None


def test_a_holiday_inside_someones_pto_is_not_counted_twice():
    """Union, not sum. Counting both would push that person's week below zero and
    claim the contract earns money back over the holiday."""
    both = absence.week_factors(
        date(2026, 1, 1),
        first_week=27,
        last_week=27,
        holidays=[{"date": "2026-07-03"}],
        absences=[{"person_id": "e1", "start": "2026-06-29", "end": "2026-07-10"}],
        shares={"e1": 1.0},
    )
    # e1 is out the whole week anyway; the holiday inside it removes nothing further.
    assert both[0]["factor"] == 0.0


def test_a_holiday_still_lands_when_nobody_has_a_share():
    """A CLIN with no per-person attribution can still know about the calendar."""
    factors = absence.week_factors(
        date(2026, 1, 1),
        first_week=27,
        last_week=27,
        holidays=[{"date": "2026-07-03"}],
        shares={},
    )
    assert factors[0]["factor"] == 0.8


# ------------------------------------------------------- the calendar generator


def test_the_federal_calendar_observes_the_weekend_rule():
    days = {h["name"]: h["date"] for h in absence.federal_holidays(2026)}

    assert len(days) == 11
    # 4 July 2026 is a Saturday → observed the Friday before (5 U.S.C. 6103(b)).
    assert days["Independence Day"] == "2026-07-03"
    # Floating holidays are computed, not shifted.
    assert days["Birthday of Martin Luther King, Jr."] == "2026-01-19"
    assert days["Memorial Day"] == "2026-05-25"  # last Monday in May
    assert days["Thanksgiving Day"] == "2026-11-26"  # 4th Thursday
    assert days["Christmas Day"] == "2026-12-25"  # a Friday, no shift


def test_seeding_the_calendar_twice_does_not_double_the_days_off():
    twice = absence.normalize_holidays(
        absence.federal_holidays(2026) + absence.federal_holidays(2026)
    )
    assert len(twice) == 11


# -------------------------------------------------------------- input handling


def test_a_malformed_absence_is_dropped_rather_than_taking_the_page_down():
    """This runs inside burn.compute on every page load. The write path is where a
    user hears about a bad entry; the read path must never raise."""
    entries = absence.normalize_absences(
        [
            {"person_id": "e1", "start": "2026-03-19", "end": "2026-04-01"},
            {"person_id": "e1", "start": "not-a-date", "end": "2026-04-01"},
            {"person_id": "", "start": "2026-03-19", "end": "2026-04-01"},
            {"person_id": "e2", "start": "2026-04-01", "end": "2026-03-19"},  # inverted
            "nonsense",
        ]
    )
    assert [e["person_id"] for e in entries] == ["e1"]


def test_validate_absence_names_the_problem():
    assert absence.validate_absence({"start": "2026-01-01", "end": "2026-01-02"})
    assert (
        absence.validate_absence(
            {"person_id": "e1", "start": "2026-01-05", "end": "2026-01-01"}
        )
        == "The end date is before the start date."
    )
    assert (
        absence.validate_absence(
            {"person_id": "e1", "start": "2026-01-01", "end": "2026-01-10"}
        )
        is None
    )


def test_contract_absence_reads_clean_on_a_contract_that_predates_the_ticket():
    assert absence.contract_absence({}) == {"holidays": [], "absences": []}
    assert absence.contract_absence(None) == {"holidays": [], "absences": []}
