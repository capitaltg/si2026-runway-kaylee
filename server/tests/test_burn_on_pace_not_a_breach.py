"""A contract on pace must not be told to cut its staff (#63).

The helper tests in `test_burn_funding_horizon.py` all passed while the live app was
wrong, because the defect was in the *caller*: `_compute_clin` derived
`ceiling_breached` from `ceiling_exhaust < total_weeks - 1`, a flat one-week margin on
a projection built by extrapolating a four-week trailing average across the whole
remaining PoP. Live contract 5 (7024HEXDVC0001043) CLIN 2001 was 22.5% through its
$3.08M ceiling at 23.1% of its PoP elapsed — dead on plan — and its ceiling projected
to week 50.44 of 52. `50.44 < 51`, so the flag was raised.

That flag is the gate on the incremental-funding softening, so the chain ran:

    on-pace CLIN → ceiling_breached → red "Over ceiling" → `tripwires`
        → suggest.solve_moves sizes a staffing gap against the *funded slice*
        → "roll people off this contract"

on a contract with $1.5M of ceiling underneath its current obligation and 99 days of
funded runway. So these tests go through `compute()` end to end rather than through
the banding helpers: the arithmetic was never the part that was wrong.

Each case fixes the ceiling and the clock and moves only one variable, so a future
change that re-tightens the tolerance fails on the specific claim it breaks.
"""

from datetime import date, timedelta

from app import burn

# 52-week PoP, so `total_weeks` is the year the tolerance argument is calibrated for.
_PERIOD = {"name": "Base", "pop_start": "2026-01-01", "pop_end": "2026-12-31"}

# $1.04M over 10,400 estimated hours is a blended $100/hr, which makes every dollar
# below a round number of hours: 200 hrs/wk is exactly $20,000/wk, and $20,000 x 52 is
# exactly the ceiling. So "on pace" here is not approximately on pace, it is the
# straight line landing on the last week of the PoP.
_CEILING = 1_040_000
_EST_HOURS = 10_400
_ON_PACE_HOURS = 200  # hrs/wk == $20,000/wk == _CEILING / 52
_WEEKS_LOGGED = 12  # → current_week 12 of 52, elapsed_frac 0.2308


def _contract(obligated):
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-63",
            "total_ceiling": _CEILING,
            "total_obligated": obligated,
        },
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


def _rows(hours_per_week=_ON_PACE_HOURS, weeks=_WEEKS_LOGGED):
    """One person, `weeks` distinct weeks ending 2026-01-01 + 7k.

    Distinct week endings matter twice over: `_clock` reads `current_week` off the
    latest one, and the forward pace is the trailing `_PACE_WEEKS` average, so a
    constant weekly here makes the pace estimate and the cumulative average agree.
    That is deliberate — it isolates the tolerance from sampling noise, which is a
    separate effect with its own tests.
    """
    start = date.fromisoformat("2026-01-01")
    return [
        {
            "charge_code": "0001",
            "labor_category": "Software Engineer",
            "total_hours": hours_per_week,
            "week_ending": (start + timedelta(days=7 * k)).isoformat(),
            "employee_id": "e1",
        }
        for k in range(weeks)
    ]


def _clin(obligated, hours_per_week=_ON_PACE_HOURS, weeks=_WEEKS_LOGGED):
    return burn.compute(_contract(obligated), _rows(hours_per_week, weeks))


def _only(payload):
    return payload["clins"][0]


# ---- the clock and the pace the cases are built on --------------------------


def test_fixture_is_genuinely_on_pace():
    # Guards every assertion below: if the fixture drifts off pace the other tests
    # stop testing the tolerance and start testing something else.
    c = _only(_clin(520_000))
    assert c["elapsed_frac"] == 0.2308
    assert c["weekly"] == 20_000.0
    assert c["spent"] == 240_000.0
    # Spend fraction and clock fraction agree to the rounding — the definition of
    # on-pace, and the thing the old flag called a ceiling breach.
    assert round(c["pct"], 4) == 0.2308


# ---- the regression --------------------------------------------------------


def test_on_pace_contract_is_all_clear_and_recommends_nothing():
    # Contract 5's shape: funded to half the ceiling, ~98 days of funded runway, the
    # slice projected to run dry at week 26 of 52 — and none of that is news, because
    # outrunning the current tranche is what incremental funding *is*.
    payload = _clin(520_000)
    c = _only(payload)

    assert c["incrementally_funded"] is True
    assert c["ceiling_breached"] is False
    assert c["status"] == "ok"
    assert c["status_label"] == "On pace"

    # The funded slice really does run dry mid-PoP. The point is not that the engine
    # missed it — it is that this is the routine case and gets no alarm.
    assert c["exhaust_week"] == 26.0
    assert c["runway_days"] == 98

    # No alert of any kind, on any list. This is what the PM sees.
    assert payload["tripwires"] == []
    assert payload["funding"] == []
    assert payload["underburn"] == []
    assert payload["all_clear"] is True


def test_on_pace_contract_produces_no_staffing_moves():
    # The consequence that made this ticket urgent, pinned at the seam it crossed:
    # `tripwires` is what `suggest.solve_moves` is handed. Empty here means the Flight
    # Deck has nothing to recommend, so it cannot recommend rolling anyone off.
    assert _only(_clin(520_000))["status"] not in ("over", "watch")


# ---- the tolerance is not an amnesty ---------------------------------------


def test_a_materially_hot_pace_is_still_red():
    # 30% above the affordable pace. The ceiling projects to week 40 of 52, a 43%
    # pace overrun, and that is exactly what the red is for.
    c = _only(_clin(520_000, hours_per_week=260))

    assert c["ceiling_breached"] is True
    assert c["status"] == "over"
    assert c["status_label"] == "Over ceiling"


def test_funding_lagging_the_burn_is_still_red():
    # #22's red, and the one the softening must never rescue: the ceiling is
    # comfortable, but the money to pay for it is not arriving.
    #
    # This case cannot be built on the on-pace fixture above, and the reason is a
    # property of the model worth writing down. `funding_keeps_pace` fails when
    # funded_frac < elapsed_frac - 0.15; on-pace burn means pct == elapsed_frac; so
    # lagging funding on an on-pace CLIN implies funded < spent - 0.15 x ceiling, i.e.
    # spend is already past the allotment and `funds_exceeded` has fired. The forecast
    # "Funds short" and the realized "Funds exceeded" are therefore not two readings of
    # one situation — a *forecast* funding shortfall can only exist on a CLIN burning
    # slower than its clock. So: 30% spent at 62% elapsed, 40% obligated.
    c = _only(_clin(416_000, hours_per_week=97.5, weeks=32))

    assert c["funds_exceeded"] is False  # the forecast case, not the realized one
    assert c["funding_keeps_pace"] is False
    assert c["ceiling_breached"] is False  # the ceiling is nowhere near
    assert c["status"] == "over"
    # Names the funded slice, not the ceiling. Calling this "Over ceiling" would point
    # a PM at a limit the CLIN is $728k away from.
    assert c["status_label"] == "Funds short"


# ---- and the amber still arrives, at the FAR window ------------------------


def test_amber_funding_due_inside_the_60_day_window():
    # The state Kaylee described as the correct one: silent at 99 days, amber at 60.
    # Funded to $360k → six weeks of runway left, 42 days, inside FAR 52.232-22(c)'s
    # lookahead. It lands on `funding`, NOT on `tripwires` — the remedy is the next
    # mod, and a tripwire would hand it to the staffing solver instead.
    payload = _clin(360_000)
    c = _only(payload)

    assert c["runway_days"] == 42
    assert c["status"] == "funding"
    assert c["status_label"] == "Funding due"
    assert [t["code"] for t in payload["funding"]] == ["CLIN 0001"]
    assert payload["tripwires"] == []


def test_the_window_is_the_only_thing_that_moved():
    # The two sit either side of the gate with the same ceiling, same pace, same
    # clock — only the size of the tranche differs. Pinning them together is what
    # stops a future change from reintroducing a permanent amber.
    assert _only(_clin(520_000))["status"] == "ok"  # 98 days
    assert _only(_clin(360_000))["status"] == "funding"  # 42 days
