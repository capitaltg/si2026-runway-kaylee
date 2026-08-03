"""#23 — the hard-stop forecast: the calendar date charging gets blocked.

The original ticket asked Runway to *block* a timesheet that would push spend past
the funded amount. It can't and shouldn't: hours arrive as a read-only batch pull
(`replace_timesheets` deletes and re-inserts the whole cache), there is no per-entry
write path to intercept, and the real hard stop is a Costpoint / Unanet control in
the accounting system that owns the charge codes. Runway is the early-warning layer
upstream of it, so what it owes the PM is the *date* — "what day does Costpoint
start rejecting charges on this CLIN?" — not enforcement.

Two properties this pins down:
  * the date is `anchor + round(weeks_left * 7)` days, i.e. the same arithmetic
    `runway_days` uses, so a card can never show a day count and a date that
    disagree;
  * it's measured against the *binding* budget, so an incrementally funded CLIN
    gets its funded-dollars date, which is always the earlier of the two.
"""

from datetime import date, timedelta

from app import burn

_PERIOD = {"name": "Base", "pop_start": "2026-01-01", "pop_end": "2026-12-31"}

# $224k over 2,240 est hours → a clean $100/hr blended rate, so 40 hrs/week is
# exactly $4,000/week of burn and every projection below is hand-checkable.
_CEILING = 224_000
_EST_HOURS = 2_240
_RATE = 100
_HOURS_PER_WEEK = 40
_WEEKLY = _HOURS_PER_WEEK * _RATE  # $4,000
_WEEKS = 10
_FIRST_WEEK = date(2026, 1, 2)
# 10 weeks of 40 hours: $40,000 spent, and the clock is anchored to the last one.
_ANCHOR = _FIRST_WEEK + timedelta(weeks=_WEEKS - 1)
_SPENT = _WEEKS * _WEEKLY


def _contract(labor_obligated=None, travel_obligated=None):
    labor = {
        "clin": "0001",
        "period": "Base",
        "title": "Professional Services (Labor)",
        "is_labor": True,
        "ceiling": _CEILING,
        "est_hours": _EST_HOURS,
    }
    travel = {
        "clin": "0002",
        "period": "Base",
        "title": "Travel & ODC",
        "is_labor": False,
        "ceiling": 50_000,
    }
    if labor_obligated is not None:
        labor["obligated"] = labor_obligated
    if travel_obligated is not None:
        travel["obligated"] = travel_obligated
    return {
        "id": 1,
        "contract": {"piid": "TEST-23", "total_ceiling": _CEILING + 50_000},
        "clins": [labor, travel],
        "periods": [_PERIOD],
    }


def _rows(weeks=_WEEKS):
    return [
        {
            "charge_code": "0001",
            "labor_category": "Software Engineer",
            "total_hours": _HOURS_PER_WEEK,
            "week_ending": (_FIRST_WEEK + timedelta(weeks=i)).isoformat(),
            "employee_id": "e1",
        }
        for i in range(weeks)
    ]


def _clin(payload, num="0001"):
    return next(c for c in payload["clins"] if c["id"] == num)


# ---- the date and the day count are the same arithmetic ---------------------


def test_stop_date_is_runway_days_from_the_anchor():
    # The one property worth locking down: `stop_date` is derived from the same
    # `weeks_left * 7` that `runway_days` is, measured from the same "now" the week
    # clock uses (the latest synced timesheet week, NOT today — a demo DB is
    # routinely months behind). Deriving the date any other way — from
    # `exhaust_week` against `pop_start`, or from today — lets a card print a day
    # count and a date that contradict each other.
    c = _clin(burn.compute(_contract(), _rows()))

    assert c["stop_date"] == (_ANCHOR + timedelta(days=c["runway_days"])).isoformat()
    assert c["stop_date_passed"] is False


def test_ceiling_limited_clin_names_the_ceiling():
    # Nothing obligated → budget is the full ceiling, so the ceiling is what runs
    # out and there is no funding story to tell.
    c = _clin(burn.compute(_contract(), _rows()))

    assert c["stop_reason"] == "ceiling"
    assert c["incrementally_funded"] is False
    # $224k ceiling - $40k spent = $184k at $4k/week = 46 weeks.
    assert c["runway_days"] == 46 * 7


# ---- the binding budget, which is always the earlier date -------------------


def test_incremental_funding_dates_the_stop_off_the_funded_slice():
    # $120k obligated against a $224k ceiling. The funded slice is what the
    # accounting system's hard stop is actually set against, so that's the date —
    # and because a funded slice can never exceed its ceiling, it is always the
    # earlier of the two. That's why "report the earlier date" needs no precedence
    # rule: `budget` already is it.
    funded = _clin(burn.compute(_contract(labor_obligated=120_000), _rows()))
    ceiling_only = _clin(burn.compute(_contract(), _rows()))

    assert funded["stop_reason"] == "funding"
    assert funded["incrementally_funded"] is True
    # ($120k - $40k) / $4k = 20 weeks, vs 46 on the ceiling.
    assert funded["runway_days"] == 20 * 7
    assert funded["stop_date"] < ceiling_only["stop_date"]


def test_the_funded_date_is_not_the_ceiling_date():
    # Guards the specific regression the funded-slice work exists to prevent: a
    # CLIN reporting a comfortable ceiling-based date while its obligated money
    # runs out months earlier.
    funded = _clin(burn.compute(_contract(labor_obligated=120_000), _rows()))

    ceiling_weeks_left = (_CEILING - _SPENT) / _WEEKLY
    ceiling_date = _ANCHOR + timedelta(days=round(ceiling_weeks_left * 7))
    assert funded["stop_date"] != ceiling_date.isoformat()


# ---- already past the funding: the date stays true, the runway floors ------


def test_exhausted_funding_keeps_the_true_past_date():
    # $30k obligated but $40k already spent. `runway_days` floors at 0 (there is no
    # time left to report), while `stop_date` deliberately does NOT clamp — the
    # honest answer to "when does charging stop" is that it already should have, and
    # the date it ran out is the useful fact. `stop_date_passed` is what tells the
    # UI to say "today" rather than naming a deadline that has been and gone.
    c = _clin(burn.compute(_contract(labor_obligated=30_000), _rows()))

    assert c["stop_date_passed"] is True
    assert c["stop_date"] < _ANCHOR.isoformat()
    assert c["runway_days"] == 0
    assert c["stop_reason"] == "funding"
    # The realized read from #73 is unchanged by any of this.
    assert c["status"] == "over"
    assert c["funds_exceeded"] is True
    assert c["status_label"] == "Funds exceeded"


def test_exhausted_funding_date_is_when_the_money_actually_ran_out():
    # $10k past a $30k slice at $4k/week → the wall was ~2.5 weeks (18 days) ago.
    c = _clin(burn.compute(_contract(labor_obligated=30_000), _rows()))

    expected = _ANCHOR + timedelta(days=round((30_000 - _SPENT) / _WEEKLY * 7))
    assert c["stop_date"] == expected.isoformat()


# ---- no pace to project from -----------------------------------------------


def test_paused_clin_has_no_stop_date():
    # No charges → no forward pace. Without the status guard this would read
    # `_PAUSED_WEEKS_LEFT` (999 weeks) and confidently put the wall 19 years out,
    # which is worse than saying nothing. Mirrors how `weeks_left` /
    # `exhaust_week` / `runway_days` already null out here.
    c = _clin(burn.compute(_contract(), []))

    assert c["status"] == "paused"
    assert c["stop_date"] is None
    assert c["stop_reason"] is None
    assert c["stop_date_passed"] is False


def test_nonlabor_clin_carries_the_keys_as_null():
    # Out of scope for a real date until #20 / #7 give non-labor CLINs actuals, but
    # the keys must exist: the tripwire and funding lists mix labor and non-labor
    # rows and read these off both.
    c = _clin(burn.compute(_contract(), _rows()), "0002")

    assert c["is_labor"] is False
    assert c["stop_date"] is None
    assert c["stop_reason"] is None
    assert c["stop_date_passed"] is False


# ---- the alert lists carry the date ----------------------------------------


def test_tripwire_carries_the_stop_date():
    # The Flight Deck's red banner should be able to say *when*, not only how many
    # weeks early. `limited_by` there is already the same value `stop_reason`
    # carries, so the reason isn't duplicated onto the row.
    p = burn.compute(_contract(labor_obligated=30_000), _rows())
    tw = next(t for t in p["tripwires"] if t["code"] == "CLIN 0001")

    assert tw["stop_date"] is not None
    assert tw["stop_date_passed"] is True
    assert tw["limited_by"] == "funding"


def test_hero_stop_date_matches_its_own_clin():
    # The hero tile shows a day count; the date beside it has to be the same CLIN's,
    # or the two headline numbers on the Flight Deck disagree.
    p = burn.compute(_contract(labor_obligated=120_000), _rows())
    c = _clin(p)

    assert p["hero"]["clin"] == c["code"]
    assert p["hero"]["days"] == c["runway_days"]
    assert p["hero"]["stop_date"] == c["stop_date"]
    assert p["hero"]["stop_date_passed"] == c["stop_date_passed"]
