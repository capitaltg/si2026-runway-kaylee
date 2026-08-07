"""Who's running hot (#83) — capacity, the two-forecast diagnosis, hours ceilings."""

from app import allocation, heat


# Sized so one person at ~45 hrs/wk puts the CLIN in `over` — the gate every
# person-level finding sits behind. A comfortably funded CLIN reads `under` and is
# used deliberately in the healthy-contract test.
def _contract(est_hours=None, lcat_est=None, utilization_target=None, ceiling=95_000.0):
    clin = {
        "clin": "0001",
        "title": "Engineering",
        "is_labor": True,
        "ceiling": ceiling,
        "obligated": ceiling,
        "labor_rates": [
            {
                "lcat": "Systems Engineer",
                "loaded_rate": 100.0,
                **({"est_hours": lcat_est} if lcat_est else {}),
            }
        ],
    }
    if est_hours:
        clin["est_hours"] = est_hours
    c = {
        "id": 1,
        "contract": {"piid": "TEST-0001", "total_ceiling": ceiling},
        "clins": [clin],
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
    if utilization_target is not None:
        c["utilization_target"] = utilization_target
    return c


def _row(
    emp, week, hours, reg=None, ot=None, leave=0.0, holiday=0.0, lcat="Systems Engineer"
):
    r = {
        "employee": emp,
        "employee_id": emp.replace(" ", "").lower(),
        "week_ending": week,
        "charge_code": "0001",
        "labor_category": lcat,
        "total_hours": hours,
        "leave_hours": leave,
        "holiday_hours": holiday,
    }
    if reg is not None:
        r["reg_hours"] = reg
        r["ot_hours"] = ot or 0.0
    return r


WEEKS = ["2026-02-06", "2026-02-13", "2026-02-20", "2026-02-27"]


def _run(contract, rows):
    alloc = allocation.compute_allocation(contract, rows)
    return heat.compute_heat(contract, rows, alloc), alloc


def _rows_for(names_hours, **kw):
    """`{name: hours_per_week}` charged flat across the four-week window."""
    return [
        _row(name, wk, hours, **kw)
        for name, hours in names_hours.items()
        for wk in WEEKS
    ]


# --- the definition of "over" ------------------------------------------------


def test_a_flat_forty_hour_week_is_not_hot():
    """The fallback expectation is 40, so 40 flat is exactly on plan."""
    result, _ = _run(_contract(), _rows_for({"Dana Reed": 40.0}))
    assert result["people"] == []


def test_cumulative_window_lets_a_long_week_cancel_a_short_one():
    """50 then 30 is a normal fortnight, not overtime. A per-week > 40 test would
    have flagged the 50."""
    rows = [
        _row("Dana Reed", WEEKS[0], 50.0),
        _row("Dana Reed", WEEKS[1], 30.0),
        _row("Dana Reed", WEEKS[2], 50.0),
        _row("Dana Reed", WEEKS[3], 30.0),
    ]
    result, _ = _run(_contract(), rows)
    assert result["people"] == []


def test_sustained_overtime_is_hot_and_reports_hours_first():
    """180 hours against 160 available — the accountant's framing, reported weekly."""
    result, _ = _run(_contract(), _rows_for({"Alex Cole": 45.0}))
    (person,) = result["people"]
    assert person["name"] == "Alex Cole"
    assert person["worked_hours"] == 180.0
    assert person["available_hours"] == 160.0
    assert person["over_hours"] == 20.0
    assert person["over_hours_per_week"] == 5.0
    # 5 hrs/wk over, all of it on 0001 at $100/hr.
    assert person["weekly_dollars"] == 500.0


def test_leave_and_holidays_come_out_of_available_hours():
    """A week with a holiday in it offers fewer available hours, so someone who
    billed a full week that month worked over. Naive arithmetic reads them as
    exactly on plan, which is the wrong direction.

    Uses the reg/OT split so the billable figure is authoritative — under the older
    single-figure semantics `total_hours` includes leave and `billable_hours` backs
    it out, which would confound what this test is pinning.
    """
    rows = _rows_for({"Kelly Soto": 40.0}, reg=40.0, ot=0.0)
    rows[0]["holiday_hours"] = 8.0
    rows[1]["leave_hours"] = 8.0
    result, _ = _run(_contract(), rows)
    (person,) = result["people"]
    assert person["available_hours"] == 144.0  # 160 − 8 holiday − 8 leave
    assert person["worked_hours"] == 160.0
    assert person["over_hours"] == 16.0


def test_over_is_measured_against_expected_not_forty():
    """A 32-hour expectation makes 38 hrs/wk over. Nothing here may assume 40."""
    contract = _contract(utilization_target=0.8)  # 0.8 × 40 = 32
    result, _ = _run(contract, _rows_for({"Deborah Williams": 38.0}))
    (person,) = result["people"]
    assert person["expected_hours_per_week"] == 32.0
    assert person["expected_level"] == "contract"
    assert person["expected_assumed"] is False
    assert person["over_hours_per_week"] == 6.0


def test_an_unconfigured_expectation_is_labelled_an_assumption():
    result, _ = _run(_contract(), _rows_for({"Alex Cole": 45.0}))
    (person,) = result["people"]
    assert person["expected_assumed"] is True
    assert person["expected_level"] == "fallback"


def test_rounding_noise_does_not_name_anybody():
    """Half an hour a week over four weeks is a long lunch, not a finding."""
    result, _ = _run(_contract(), _rows_for({"Dana Reed": 40.5}))
    assert result["people"] == []


# --- overtime as corroboration, not the signal --------------------------------


def test_hours_over_expected_are_reported_without_an_overtime_column():
    """Older syncs left reg/ot NULL. The finding still stands; it just isn't
    called overtime."""
    result, _ = _run(_contract(), _rows_for({"Alex Cole": 45.0}))
    (person,) = result["people"]
    assert person["ot_known"] is False
    assert person["ot_hours"] is None
    assert person["over_hours"] == 20.0


def test_a_payroll_split_names_the_overtime():
    result, _ = _run(_contract(), _rows_for({"Alex Cole": 48.0}, reg=40.0, ot=8.0))
    (person,) = result["people"]
    assert person["ot_known"] is True
    assert person["ot_hours"] == 32.0
    assert person["over_hours"] == 32.0


# --- the gate: this ranks CLINs' money, not people ---------------------------


def test_nobody_surfaces_when_the_clin_is_healthy():
    """`capacity.py` forbids scoring people against their expected hours. The gate
    is the CLIN being off-pace — so heavy hours on a well-funded CLIN say nothing."""
    result, alloc = _run(_contract(ceiling=5_000_000.0), _rows_for({"Alex Cole": 50.0}))
    assert alloc["clins"][0]["base_status"] not in heat.HOT_CLIN_STATES
    assert result["people"] == []
    assert result["clins"] == []


def test_people_are_ranked_by_weekly_dollars():
    rows = _rows_for({"Alex Cole": 44.0}) + [
        _row("Priya Raman", wk, 50.0, lcat="Systems Engineer") for wk in WEEKS
    ]
    result, _ = _run(_contract(), rows)
    assert [p["name"] for p in result["people"]] == ["Priya Raman", "Alex Cole"]
    assert result["people"][0]["weekly_dollars"] > result["people"][1]["weekly_dollars"]


def test_unpriced_hours_are_carried_without_a_made_up_rate():
    """An LCAT with no rate line is a rate-table problem the CLIN card already
    reports (#64). It must never be priced at an invented number — but the person is
    still named, because dropping them would hide hours that are really being worked."""
    rows = _rows_for({"Alex Cole": 45.0}) + [
        _row("Wei Chen", wk, 46.0, lcat="Cyber Engineer III") for wk in WEEKS
    ]
    result, _ = _run(_contract(), rows)
    wei = next(p for p in result["people"] if p["name"] == "Wei Chen")
    (impact,) = wei["clins"]
    assert impact["unpriced"] is True
    assert impact["rate"] is None
    assert impact["weekly_dollars"] is None
    assert wei["weekly_dollars"] == 0.0
    assert wei["over_hours_per_week"] == 6.0


# --- the two forecasts are a diagnosis --------------------------------------


def _overtime_only():
    """One person at 60 hrs/wk on a CLIN whose budget survives a 40-hour week but
    not a 60-hour one. $6,000/wk now, $4,000/wk at expected hours."""
    return _run(_contract(ceiling=220_000.0), _rows_for({"Alex Cole": 60.0}))


def test_overtime_alone_diagnoses_stop_the_overtime():
    """Off-pace now, lands inside budget once the excess hours come off."""
    result, _ = _overtime_only()
    (clin,) = result["clins"]
    assert clin["status"] == "over"
    assert clin["diagnosis"] == heat.STOP_OVERTIME
    assert clin["weekly"] == 6000.0
    assert clin["weekly_at_expected"] == 4000.0
    assert clin["exhaust_week_at_expected"] >= result["total_weeks"]


def test_overstaffing_still_diagnoses_cutting_people():
    """Six people barely over their expected hours: removing the excess leaves the
    CLIN exhausting early anyway, so the remedy is headcount, not overtime."""
    rows = _rows_for({f"Person {i}": 44.0 for i in range(6)})
    result, _ = _run(_contract(ceiling=400_000.0), rows)
    (clin,) = result["clins"]
    assert clin["diagnosis"] == heat.REDUCE_STAFFING
    assert clin["weekly_at_expected"] < clin["weekly"]
    assert clin["exhaust_week_at_expected"] < result["total_weeks"]


def test_the_diagnosis_names_the_people_behind_it():
    result, _ = _overtime_only()
    (clin,) = result["clins"]
    assert clin["people"] == [p["id"] for p in result["people"]]
    assert clin["diagnosis_label"]


def test_weeks_bought_is_the_cost_of_the_overtime():
    """The headline: how many weeks of runway the overtime is consuming."""
    result, _ = _overtime_only()
    (clin,) = result["clins"]
    assert clin["weeks_bought"] == round(
        clin["exhaust_week_at_expected"] - clin["exhaust_week"], 1
    )
    assert clin["weeks_bought"] > 0


# --- the hours ceiling (est_hours, captured at ingest and never read) --------


def test_no_estimated_hours_means_no_ceiling_claim():
    result, _ = _run(_contract(), _rows_for({"Alex Cole": 45.0}))
    assert result["hours_ceilings"] == []


def test_a_rate_line_estimate_becomes_a_category_hours_ceiling():
    contract = _contract(lcat_est=1000)
    result, _ = _run(contract, _rows_for({"Alex Cole": 45.0}))
    (ceiling,) = result["hours_ceilings"]
    assert ceiling["lcat"] == "Systems Engineer"
    assert ceiling["source"] == "rate_line"
    assert ceiling["contracted_hours"] == 1000.0
    assert ceiling["charged_hours"] == 180.0
    assert ceiling["pace_per_week"] == 45.0
    # 820 hours left at 45/wk = 18.22 more weeks from the current week. Fractional,
    # like burn's own exhaust week.
    assert ceiling["exhaust_week"] == round(result["current_week"] + 820 / 45, 2)
    assert ceiling["early"] is True


def test_a_clin_total_is_only_used_when_no_rate_line_carries_hours():
    contract = _contract(est_hours=4000)
    result, _ = _run(contract, _rows_for({"Alex Cole": 45.0}))
    (ceiling,) = result["hours_ceilings"]
    assert ceiling["source"] == "clin_total"
    assert ceiling["lcat"] is None
    assert ceiling["contracted_hours"] == 4000.0


def test_a_rate_line_estimate_wins_over_the_clin_total():
    contract = _contract(est_hours=4000, lcat_est=1000)
    result, _ = _run(contract, _rows_for({"Alex Cole": 45.0}))
    assert [c["source"] for c in result["hours_ceilings"]] == ["rate_line"]


def test_a_ceiling_that_outlasts_the_period_is_not_flagged_early():
    contract = _contract(lcat_est=100_000)
    result, _ = _run(contract, _rows_for({"Alex Cole": 45.0}))
    (ceiling,) = result["hours_ceilings"]
    assert ceiling["early"] is False


def test_hours_charged_past_the_contracted_estimate_report_the_overrun():
    contract = _contract(lcat_est=100)
    result, _ = _run(contract, _rows_for({"Alex Cole": 45.0}))
    (ceiling,) = result["hours_ceilings"]
    assert ceiling["hours_remaining"] == -80.0
    assert ceiling["overrun_hours"] == 80.0
    # It is news, and it is the most severe version of this finding.
    assert ceiling["early"] is True
    # But there is no exhaust week: it already happened. Projecting `left / pace`
    # with a negative `left` dates the event in the *past*, which reads as a
    # forecast — seen for real on contract 4, week 8.6 reported at week 26.
    assert ceiling["exhaust_week"] is None


# --- window reporting -------------------------------------------------------


def test_the_window_is_reported_so_a_surface_can_name_it():
    result, _ = _run(_contract(), _rows_for({"Alex Cole": 45.0}))
    assert result["window"] == {"weeks": 4, "from": WEEKS[0], "to": WEEKS[-1]}


def test_a_contract_with_no_charges_says_nothing():
    result, _ = _run(_contract(), [])
    assert result["people"] == []
    assert result["hours_ceilings"] == []
