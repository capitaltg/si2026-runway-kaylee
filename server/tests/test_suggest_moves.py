"""Named, person-level moves (#63) — the rules that outrank the ticket's own text.

Driven through the real `allocation` → `heat` → `suggest` chain rather than hand-built
payloads, because the whole point of putting the solver on the server was that it reads
the same numbers those two already agree on. A fixture that fakes the heat payload would
pass while the wiring was wrong.

The local `runway.db` cannot exercise the `stop_overtime` branch at all — every contract
in it diagnoses `reduce_staffing` with under a week of runway bought — so the fixtures
here are the only coverage that branch has. See the PR notes.
"""

from app import allocation, heat, suggest

WEEKS = ["2026-02-06", "2026-02-13", "2026-02-20", "2026-02-27"]

SE = "Systems Engineer"
CYBER = "Cyber Engineer III"


def _clin(num, title, ceiling, rates, est_hours=None):
    """One labor CLIN. `rates` is `{lcat: loaded_rate}` or `{lcat: (rate, est_hours)}`."""
    lines = []
    for lcat, spec in rates.items():
        rate, est = spec if isinstance(spec, tuple) else (spec, None)
        line = {"lcat": lcat, "loaded_rate": rate}
        if est:
            line["est_hours"] = est
        lines.append(line)
    c = {
        "clin": num,
        "title": title,
        "is_labor": True,
        "ceiling": ceiling,
        "obligated": ceiling,
        "labor_rates": lines,
    }
    if est_hours:
        c["est_hours"] = est_hours
    return c


def _contract(clins, utilization_target=None):
    total = sum(c["ceiling"] for c in clins)
    c = {
        "id": 1,
        "contract": {"piid": "TEST-0063", "total_ceiling": total},
        "clins": clins,
        "periods": [
            {
                "name": "Base",
                "exercised": True,
                "pop_start": "2026-01-02",
                "pop_end": "2026-12-31",
                "ceiling": total,
            }
        ],
    }
    if utilization_target is not None:
        c["utilization_target"] = utilization_target
    return c


def _row(emp, week, hours, charge="0001", lcat=SE):
    return {
        "employee": emp,
        "employee_id": emp.replace(" ", "").lower(),
        "week_ending": week,
        "charge_code": charge,
        "labor_category": lcat,
        "total_hours": hours,
        "reg_hours": min(hours, 40.0),
        "ot_hours": max(0.0, hours - 40.0),
        "leave_hours": 0.0,
        "holiday_hours": 0.0,
    }


def _rows(people, charge="0001"):
    """`{name: hrs_per_wk}` or `{name: (hrs_per_wk, lcat)}`, charged flat all window."""
    out = []
    for name, spec in people.items():
        hours, lcat = spec if isinstance(spec, tuple) else (spec, SE)
        out += [_row(name, wk, hours, charge=charge, lcat=lcat) for wk in WEEKS]
    return out


def _solve(contract, rows, expected_by_person=None):
    alloc = allocation.compute_allocation(
        contract, rows, expected_hours_by_person=expected_by_person
    )
    h = heat.compute_heat(contract, rows, alloc)
    return suggest.solve_moves(alloc, h), h, alloc


def _plan(plans, cid="0001"):
    return next(p for p in plans if p["clin"] == cid)


# --- the two diagnoses get different remedies --------------------------------


def _overtime_only(**kw):
    """One person on a 60-hour week against a comfortably sized CLIN: off pace now,
    fine once the excess comes off. #83 calls this `stop_overtime`."""
    contract = _contract([_clin("0001", "Engineering", 220_000.0, {SE: 100.0})])
    return _solve(contract, _rows({"Alex Cole": 60.0}), **kw)


def _overstaffed(**kw):
    """Six people barely over: the line runs out early even at expected hours."""
    contract = _contract([_clin("0001", "Engineering", 400_000.0, {SE: 100.0})])
    return _solve(contract, _rows({f"Person {i}": 44.0 for i in range(6)}), **kw)


def test_an_overtime_diagnosis_only_ever_trims_to_expected_hours():
    """`stop_overtime` means the line finishes inside budget once people are back to
    their expected week. Rolling someone off a line that is fine at plan is the remedy
    for the *other* diagnosis, and prescribing it here cuts a team that is at plan."""
    plans, h, _ = _overtime_only()
    plan = _plan(plans)
    assert plan["diagnosis"] == heat.STOP_OVERTIME
    assert plan["moves"], "a diagnosed CLIN must produce moves"
    assert {m["kind"] for m in plan["moves"]} == {"trim"}
    assert all(m["floor"] == "expected" for m in plan["moves"])


def test_an_overtime_trim_never_lands_below_the_persons_expected_week():
    """The ticket's 32/24/20/16 stops are the flat-40 assumption #84 removed. The floor
    is whatever `capacity.resolve()` says this person's week is — and rounding to whole
    hours has to land at or above it, never a step under."""
    plans, h, _ = _overtime_only()
    (move,) = _plan(plans)["moves"]
    (person,) = h["people"]
    assert move["from_hours"] == 60.0
    assert move["expected_hours_per_week"] == 40.0
    assert move["to_hours"] >= person["expected_hours_per_week"]
    assert move["to_hours"] == 40.0


def test_a_part_timers_floor_is_their_own_week_not_forty():
    """A 32-hour expectation is trimmed to 32, not to 40 and not to a 24-hour "stop"."""
    contract = _contract([_clin("0001", "Engineering", 220_000.0, {SE: 100.0})])
    plans, h, _ = _solve(
        contract,
        _rows({"Deborah Williams": 52.0}),
        expected_by_person={"deborahwilliams": 32.0},
    )
    (move,) = _plan(plans)["moves"]
    assert move["expected_hours_per_week"] == 32.0
    assert move["to_hours"] == 32.0


def test_an_overstaffing_diagnosis_puts_real_roll_offs_on_the_table():
    """`reduce_staffing` means hours have to leave the line, not just come back to
    plan — trimming everyone to their expected week provably does not close it."""
    plans, _, _ = _overstaffed()
    plan = _plan(plans)
    assert plan["diagnosis"] == heat.REDUCE_STAFFING
    assert "roll_off" in {m["kind"] for m in plan["moves"]}
    assert plan["escalated"] is True


def test_the_gentle_ladder_is_tried_before_anyone_is_benched():
    """A trim that closes the gap is preferred to a roll-off that also would. Benching
    someone is the escalation rung, not the opening move."""
    # Two people a long way over on a line that only needs a little taken off.
    contract = _contract([_clin("0001", "Engineering", 480_000.0, {SE: 100.0})])
    plans, _, _ = _solve(contract, _rows({"Dana Reed": 58.0, "Marcus Hall": 56.0}))
    plan = _plan(plans)
    assert plan["closed"] is True
    assert "roll_off" not in {m["kind"] for m in plan["moves"]}
    assert plan["escalated"] is False


# --- the rate must never rank anybody ----------------------------------------


def test_a_higher_rate_never_selects_somebody_ahead_of_more_excess_hours():
    """The ticket says score by "$ closed per person disrupted". Taken literally the
    cheapest way to close a dollar gap is always to cut the most expensive person, which
    is the pay ranking #83 removed in 64dfa26. Ordering is by hours moved."""
    # Aisha is 14 hrs/wk over at $100/hr — a $1,400/wk trim. Wei is 6 hrs/wk over at
    # $250/hr — a $1,500/wk trim. Sized so the gap ($1,200/wk) is closed by either one
    # alone, which forces the solver to actually choose between them: a dollar-scored
    # solver takes Wei because his trim is worth more, an hours-scored one takes Aisha
    # because she is the one working the extra hours.
    contract = _contract(
        [_clin("0001", "Engineering", 742_700.0, {SE: 100.0, CYBER: 250.0})]
    )
    plans, _, _ = _solve(
        contract, _rows({"Aisha Khan": 54.0, "Wei Chen": (46.0, CYBER)})
    )
    plan = _plan(plans)
    assert plan["closed"] is True
    assert [m["person"] for m in plan["moves"]] == ["Aisha Khan"]


def test_the_move_list_follows_the_strips_order():
    """#83's acceptance criteria: the ranking is built once so "who's running hot" and
    "Runway suggests" cannot name different people in a different order on the same
    contract. web/src/suggest.test.js pins the client half of this."""
    # Sized so both people are needed, so there is an order to compare.
    contract = _contract(
        [_clin("0001", "Engineering", 686_800.0, {SE: 100.0, CYBER: 250.0})]
    )
    rows = _rows({"Aisha Khan": 54.0, "Wei Chen": (46.0, CYBER)})
    plans, h, _ = _solve(contract, rows)
    strip = [p["name"] for p in h["people"]]
    moved = [m["person"] for m in _plan(plans)["moves"]]
    assert len(moved) == 2, "both people must be moved or there is no order to compare"
    assert moved == [n for n in strip if n in moved]


def test_equal_hours_are_not_broken_by_pay():
    """Two people equally over their week are equally over. The tie-break is the LCAT
    flag and then the name — never the rate."""
    contract = _contract(
        [_clin("0001", "Engineering", 300_000.0, {SE: 100.0, CYBER: 250.0})]
    )
    plans, _, _ = _solve(
        contract, _rows({"Zoe Adams": 50.0, "Wei Chen": (50.0, CYBER)})
    )
    names = [m["person"] for m in _plan(plans)["moves"]]
    assert names == ["Wei Chen", "Zoe Adams"]  # alphabetical, not $250 before $100


# --- grouping, and the result line -------------------------------------------


def test_identical_trims_group_into_one_decision():
    """ "Trim Dana, Marcus & Sofia to 24 hrs/wk" is one decision a PM makes once. Three
    bullets saying the same thing read as three."""
    # Sized so all three trims are needed and none is sufficient alone: $15,000/wk now
    # against a $12,500/wk target, and each 10 hrs/wk trim is worth $1,000.
    contract = _contract([_clin("0001", "Engineering", 597_500.0, {SE: 100.0})])
    plans, _, _ = _solve(
        contract, _rows({"Dana Reed": 50.0, "Marcus Hall": 50.0, "Sofia Ruiz": 50.0})
    )
    plan = _plan(plans)
    assert plan["diagnosis"] == heat.STOP_OVERTIME
    trims = [g for g in plan["groups"] if g["kind"] == "trim"]
    assert len(trims) == 1
    assert sorted(trims[0]["people"]) == ["Dana Reed", "Marcus Hall", "Sofia Ruiz"]
    assert trims[0]["to_hours"] == 40.0
    assert trims[0]["hours_moved"] == 30.0  # 3 × 10 hrs/wk


def test_the_result_line_reports_the_burn_before_and_after():
    """The design's `fixResult`: `Forward burn $X/wk → $Y/wk · lands week Z of N`."""
    plans, _, _ = _overtime_only()
    plan = _plan(plans)
    assert plan["weekly"] == 6000.0
    assert plan["freed_weekly"] == 2000.0  # 20 hrs/wk × $100
    assert plan["new_weekly"] == 4000.0
    assert plan["new_exhaust_week"] > plan["exhaust_week"]
    assert plan["total_weeks"]


def test_a_closed_gap_lands_the_line_inside_the_period():
    plans, _, _ = _overtime_only()
    plan = _plan(plans)
    assert plan["closed"] is True
    assert plan["shortfall_weekly"] == 0.0
    assert plan["new_exhaust_week"] >= plan["total_weeks"]


# --- what the solver refuses to do -------------------------------------------


def test_an_unclosable_gap_says_so_instead_of_suggesting_nothing():
    """One person at 8 hrs/wk on a line with no money left: there is no move set that
    closes it, and the ticket says say so plainly rather than stay silent."""
    contract = _contract([_clin("0001", "Engineering", 1_200.0, {SE: 100.0})])
    plans, _, _ = _solve(contract, _rows({"Solo Dev": 8.0}))
    plan = _plan(plans)
    assert plan["closed"] is False
    assert plan["shortfall_weekly"] > 0


def test_unpriced_hours_are_never_priced_to_score_a_move():
    """An LCAT with no matching rate line (#64) is real hours at no known price. They
    are still reported, but no rate may be invented for them and they cannot count
    toward closing a dollar gap."""
    contract = _contract([_clin("0001", "Engineering", 60_000.0, {SE: 100.0})])
    rows = _rows({"Alex Cole": 52.0}) + _rows({"Nadia Fox": (52.0, "Ghost Category")})
    plans, h, _ = _solve(contract, rows)
    plan = _plan(plans)
    assert all(m["person"] != "Nadia Fox" for m in plan["moves"])
    assert [m["person"] for m in plan["unpriced"]] == ["Nadia Fox"]
    assert any("no printed rate" in n for n in plan["notes"])
    assert any("Nadia Fox" in n for n in plan["notes"])


def test_nothing_is_suggested_for_a_healthy_clin():
    """The gate is the CLIN being off pace, not the person being over — `capacity.py`
    forbids anything built on it from scoring people against their expected hours as a
    productivity metric, and #83 stays clean by never surfacing anyone on a line that
    is fine. The solver inherits that gate."""
    contract = _contract([_clin("0001", "Engineering", 2_000_000.0, {SE: 100.0})])
    plans, h, _ = _solve(contract, _rows({"Alex Cole": 55.0}))
    assert h["people"] == []
    assert [
        p for p in plans if p["clin"] == "0001" and p["direction"] == "reduce"
    ] == []


def test_a_move_never_books_anybody_past_the_hours_cap():
    plans, _, _ = _underburning()
    for plan in plans:
        for m in plan["moves"]:
            assert m["to_hours"] <= suggest.HOURS_CAP


# --- the hours ceiling is reported, not enforced ------------------------------


def test_a_blown_hours_ceiling_is_reported_without_vetoing_the_move_set():
    """`est_hours` semantics vary across awards — contract 4's estimates 2,080 hours for
    a category that has charged 5,882, because the figure is scoped to one FTE-year and
    not to the team. A number that unreliable may not veto a move set, so the dollar gap
    still closes and the ceiling is stated for the PM to judge."""
    contract = _contract(
        [_clin("0001", "Engineering", 220_000.0, {SE: (100.0, 100.0)})]
    )
    plans, _, _ = _solve(contract, _rows({"Alex Cole": 60.0}))
    plan = _plan(plans)
    assert plan["closed"] is True  # not vetoed
    assert any("over the" in n and "award estimates" in n for n in plan["notes"])


# --- the underburn mirror ----------------------------------------------------


def _underburning():
    """A line with far more money than its one part-time body will ever bill."""
    contract = _contract([_clin("0001", "Engineering", 900_000.0, {SE: 100.0})])
    return _solve(
        contract, _rows({"Quiet Dev": 20.0}), expected_by_person={"quietdev": 40.0}
    )


def test_an_underburning_line_raises_hours_instead_of_trimming_them():
    plans, _, alloc = _underburning()
    plan = _plan(plans)
    assert plan["direction"] == "raise"
    assert {m["kind"] for m in plan["moves"]} == {"raise"}
    assert plan["new_weekly"] > plan["weekly"]


def test_a_raise_stops_at_the_persons_expected_week_not_at_the_cap():
    """Filling an underburn by booking people past their expectation would close this
    finding by manufacturing the one #83 reports, about the same people, on the same
    dashboard. Real slack is fair game; past that the line needs another body."""
    plans, _, _ = _underburning()
    (move,) = _plan(plans)["moves"]
    assert move["from_hours"] == 20.0
    assert move["to_hours"] == 40.0  # their expected week, well under HOURS_CAP
    assert move["floor"] == "expected"


def test_an_underburn_nobody_has_slack_for_reports_a_shortfall():
    """Everyone already at their expected week means there are no hours to add, and the
    honest answer is that the gap is unclosed — not a 60-hour week."""
    contract = _contract([_clin("0001", "Engineering", 900_000.0, {SE: 100.0})])
    plans, _, _ = _solve(contract, _rows({"Full Dev": 40.0}))
    plan = _plan(plans)
    assert plan["direction"] == "raise"
    assert plan["moves"] == []
    assert plan["closed"] is False


# --- shifting to a line that can actually pay for it -------------------------


def test_a_shift_is_preferred_to_a_roll_off_when_another_line_prices_the_lcat():
    """Moving billable hours to a line that needs spending beats benching someone to fix
    a budget — it closes both findings at once and nobody loses work."""
    contract = _contract(
        [
            _clin("0001", "Engineering", 400_000.0, {SE: 100.0}),
            _clin("0002", "Option Labor", 900_000.0, {SE: 100.0}),
        ]
    )
    # 0002 needs a light charge of its own: a CLIN nobody bills reads `paused`, not
    # `under`, and paused lines are deliberately not shift destinations.
    rows = _rows({f"Person {i}": 44.0 for i in range(6)}) + _rows(
        {"Idle Dev": 4.0}, charge="0002"
    )
    plans, _, _ = _solve(contract, rows)
    plan = _plan(plans, "0001")
    assert plan["diagnosis"] == heat.REDUCE_STAFFING
    kinds = {m["kind"] for m in plan["moves"]}
    assert "shift" in kinds
    assert all(m["to_clin"] == "0002" for m in plan["moves"] if m["kind"] == "shift")


def test_nobody_is_shifted_to_a_line_that_cannot_price_their_category():
    """Closing a dollar gap by moving someone to a CLIN with no rate line for their
    category just trades a budget finding for a compliance one."""
    contract = _contract(
        [
            _clin("0001", "Engineering", 400_000.0, {SE: 100.0}),
            _clin("0002", "Option Labor", 900_000.0, {CYBER: 250.0}),
        ]
    )
    # 0002 needs a light charge of its own: a CLIN nobody bills reads `paused`, not
    # `under`, and paused lines are deliberately not shift destinations.
    rows = _rows({f"Person {i}": 44.0 for i in range(6)}) + _rows(
        {"Idle Dev": 4.0}, charge="0002"
    )
    plans, _, _ = _solve(contract, rows)
    plan = _plan(plans, "0001")
    assert all(m["kind"] != "shift" for m in plan["moves"])


# --- a finished contract is a closeout problem, not a staffing one ------------


def test_a_contract_past_its_period_gets_no_move_list():
    """Every number here is a forward one, and past PoP there are no weeks left to
    plan into. `applyBalance`'s `max(1, ...)` divisor clamps the weekly target to the
    whole remaining budget in one week, which manufactures a gap out of arithmetic.

    Found on the local contract 13 — week 129 of 52 — where it produced a $31.5K/wk
    gap and prescribed restaffing a contract whose work was over, off the back of one
    person 1.5 hrs/wk above their expected week.
    """
    # The period closed at the end of 2023; the charges are the 2026 window every other
    # test here uses. `current_week` is derived from the last charged week rather than
    # from today, so this lands at week 129 of 52 exactly as contract 13 does.
    contract = _contract([_clin("0001", "Engineering", 95_000.0, {SE: 100.0})])
    contract["periods"][0]["pop_start"] = "2023-01-02"
    contract["periods"][0]["pop_end"] = "2023-12-31"
    plans, _, alloc = _solve(contract, _rows({"Julie King": 41.5}))
    assert alloc["contract"]["past_pop"] is True
    assert alloc["contract"]["current_week"] > alloc["contract"]["total_weeks"]
    assert plans == []


def test_leave_in_the_trailing_window_never_trims_somebody_below_plan():
    """A forward move's floor is the forward expectation — not #83's leave-adjusted
    trailing excess.

    #83 measures "over" against hours *available* in the window: expected hours minus
    recorded leave and holidays. That is the right question for a report about what
    already happened and the wrong one for a staffing plan. On the local contract 4 it
    put Glenn Medina 1 hr/wk over — a month with leave in it only offered him 24 hours —
    while his forward rate is 25 hrs/wk against a 40-hour expectation. Apportioning that
    trailing excess against his forward billing rate proposed trimming him to 24 hrs/wk
    permanently, i.e. cutting someone already well under plan because he took leave.
    """
    # Sized so the colleague's trim alone closes the gap, keeping this on the trim path.
    contract = _contract([_clin("0001", "Engineering", 311_500.0, {SE: 100.0})])
    # 25 hrs/wk billed, and 64 hours of leave across the window — so `available` is 24
    # hrs/wk and #83 reports him over, but there is nothing to trim going forward.
    rows = _rows({"Glenn Medina": 25.0}) + _rows({"Busy Colleague": 55.0})
    for r in rows:
        if r["employee"] == "Glenn Medina":
            r["leave_hours"] = 16.0  # 96 hrs available in the window, 100 billed
    plans, h, _ = _solve(contract, rows)

    assert "Glenn Medina" in [
        p["name"] for p in h["people"]
    ], "the strip still reports him"
    moved = {m["person"]: m for m in _plan(plans)["moves"]}
    assert (
        "Glenn Medina" not in moved
    ), "but nobody under their expected week is trimmed"
    # The colleague genuinely over their forward expectation is still trimmed, to 40.
    assert moved["Busy Colleague"]["to_hours"] == 40.0


def test_a_trim_apportions_a_multi_clin_week_by_where_the_hours_are():
    """Someone at 50 hrs/wk with 30 on the off-pace line and a 40-hour expectation is
    trimmed to 24 there — their share of the cut — not to 40, and not by the whole 10
    hours charged to whichever line happens to be off pace."""
    contract = _contract(
        [
            # Sized so the trim alone closes the gap — a bigger gap would escalate to a
            # roll-off and stop testing the apportionment.
            _clin("0001", "Engineering", 115_500.0, {SE: 100.0}),
            _clin("0002", "Option Labor", 3_000_000.0, {SE: 100.0}),
        ]
    )
    rows = _rows({"Split Dev": 30.0}) + _rows({"Split Dev": 20.0}, charge="0002")
    plans, _, alloc = _solve(contract, rows)
    (row,) = [e for e in alloc["employees"] if e["name"] == "Split Dev"]
    assert row["hours"] == 50.0 and row["expected"]["hours"] == 40.0
    move = {m["person"]: m for m in _plan(plans)["moves"]}["Split Dev"]
    assert move["from_hours"] == 30.0
    assert move["to_hours"] == 24.0  # 40 × (30/50)


# ---- funding-limited lines get the mod, never a staffing cut ----------------
#
# Driven through a minimal allocation payload rather than the real chain above, which
# is the exception this file's preamble allows for: the guard is pure over the CLIN
# card's own funding fields (`incrementally_funded`, `ceiling`, `budget`,
# `base_weekly`) and never consults the heat payload, so there is no wiring for a
# fixture to get wrong. Building the same state through `allocation` would mean
# constructing a contract that has genuinely overspent its tranche just to reach a
# branch that reads four numbers.


def _funding_limited_alloc(ceiling, budget, weekly=10_000.0, status="over"):
    """One incrementally funded CLIN, red, with ceiling still under its funded slice."""
    return {
        "contract": {"total_weeks": 52, "current_week": 12, "past_pop": False},
        "clins": [
            {
                "id": "0001",
                "code": "CLIN 0001",
                "incrementally_funded": True,
                "ceiling": ceiling,
                "budget": budget,
                "remaining": 0.0,
                "base_weekly": weekly,
                "base_status": status,
                "base_exhaust_week": 26.0,
                # The ceiling holds — this is a tranche gap, not an overrun. The
                # breached case has its own test below.
                "ceiling_breached": False,
            }
        ],
        "employees": [],
    }


def test_a_funding_gap_proposes_no_staffing_moves():
    # The live failure on 7024HEXDVC0001043: the binding budget is the obligated slice,
    # so sizing a staffing gap against it recommended clearing a team the contract holds
    # $1.5M of ceiling to pay for. No moves, and the reason is stated.
    plans = suggest.solve_moves(_funding_limited_alloc(3_076_112, 1_572_366), {})

    assert len(plans) == 1
    plan = plans[0]
    assert plan["funding_limited"] is True
    assert plan["moves"] == []
    assert plan["groups"] == []
    # Not silent. Returning nothing would route the client to its CLIN-level fallback
    # paragraph — "trim the off-pace lines back to plan" — which is the exact advice
    # this branch exists to suppress, so the plan has to exist to carry the reason.
    assert "short an obligation, not overstaffed" in plan["notes"][0]
    assert "$1.50M of ceiling" in plan["notes"][0]
    assert plan["ceiling_headroom"] == 1_503_746.0


def test_a_projected_ceiling_breach_is_never_a_funding_gap():
    # The guard's original bug, and the sharpest case: unobligated headroom AND a
    # ceiling the line is projected to blow. A mod does not raise a ceiling, so this
    # needs the staffing plan — withholding it on headroom alone left live contract 12
    # ($277K unobligated under a $4.17M ceiling breached by week 35) with "get a mod"
    # as the answer to an overrun no obligation can fix.
    alloc = _funding_limited_alloc(4_172_771, 3_895_169, weekly=117_513.0)
    alloc["clins"][0]["ceiling_breached"] = True
    assert not suggest._funding_limited(alloc["clins"][0])


def test_a_missing_breach_flag_keeps_the_staffing_answer():
    # An allocation payload predating the flag must not have its move list silently
    # withheld — defaulting to "breached" fails safe toward saying something.
    card = _funding_limited_alloc(3_076_112, 1_572_366)["clins"][0]
    del card["ceiling_breached"]
    assert not suggest._funding_limited(card)


def test_headroom_must_beat_a_week_of_burn():
    # A line that has eaten its ceiling to within days of the end is a ceiling story
    # whichever number happens to bind.
    plans = suggest.solve_moves(
        _funding_limited_alloc(1_005_000, 1_000_000, weekly=10_000.0), {}
    )
    assert plans == [] or not plans[0].get("funding_limited")


def test_a_fully_obligated_line_is_never_funding_limited():
    # budget == ceiling: there is no unobligated slice to be short of, so an overrun
    # here is a spending problem and the solver must engage normally.
    alloc = _funding_limited_alloc(1_000_000, 1_000_000)
    assert not suggest._funding_limited(alloc["clins"][0])
