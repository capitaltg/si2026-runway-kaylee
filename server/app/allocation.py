"""Allocation matrix (#21) — the team what-if simulator's data layer.

Builds an employee x labor-CLIN grid of hrs/wk for one contract from its synced
timesheets, with each person's LCAT and the $/hr that LCAT resolves to on that
CLIN's rate table. The frontend edits cells and recomputes forward burn / runway
live; this endpoint just supplies the honest starting point and the budget/clock
each CLIN is measured against.

Deliberately thin. All the hard money math — active period, funded slice, spent to
date, the week clock — already lives in burn.py and is read straight off
burn.compute() rather than re-derived here. The one thing burn doesn't expose is
the per-employee breakdown, so that's all this module computes, on the same window
and rate resolver burn uses, so the matrix reconciles with the Flight Deck.
"""

from typing import List, Optional

from . import burn
from . import capacity
from . import lcat as lcat_match
from . import rates


def _recent_weeks(clin_rows: List[dict]) -> List[str]:
    """The most recent distinct charged weeks for a CLIN — the same trailing
    window burn.py averages its forward weekly pace over, so a cell's hrs/wk lines
    up with the CLIN's forward burn."""
    weeks = sorted({r.get("week_ending") for r in clin_rows if r.get("week_ending")})
    return weeks[-burn._PACE_WEEKS :]


def _emp_name(rows: List[dict], emp_id: str) -> str:
    for r in rows:
        if r.get("employee_id") == emp_id and (r.get("employee") or "").strip():
            return r["employee"].strip()
    return emp_id


def _staffing_moves(employees: List[dict], clins: List[dict]) -> List[dict]:
    """Turn excess current staffing into the smallest practical set of moves.

    The contract's remaining estimated hours are the demand plan. Within a CLIN
    and LCAT, whole people come off first; only the remaining fractional excess is
    spread across the people still needed. An unmatched LCAT wins the roll-off tie
    because it removes both unsupported spend and the mapping flag.
    """
    moves = []
    for clin in clins:
        groups = {}
        for employee in employees:
            cell = employee["cells"].get(clin["id"])
            if not cell or cell.get("hours", 0) <= 0:
                continue
            lcat = cell.get("planned_lcat") or cell.get("lcat") or ""
            groups.setdefault(lcat, []).append((employee, cell))
        for lcat, members in groups.items():
            target = float((clin.get("planned_lcat_hours") or {}).get(lcat, 0.0))
            excess = sum(cell["hours"] for _, cell in members) - target
            if excess <= 0:
                continue
            kept = list(members)
            while kept:
                rollable = [
                    (employee, cell)
                    for employee, cell in kept
                    if cell["hours"] <= excess
                ]
                if not rollable:
                    break
                employee, cell = min(
                    rollable,
                    key=lambda item: (
                        abs(excess - item[1]["hours"]),
                        not item[1].get("unmatched", False),
                        -item[1]["hours"],
                        item[0]["name"],
                    ),
                )
                hours = cell["hours"]
                moves.append(
                    {
                        "employee_id": employee["id"],
                        "clin": clin["id"],
                        "kind": "roll_off",
                        "from_hours": hours,
                        "to_hours": 0,
                        "clears_lcat_flag": bool(cell.get("unmatched")),
                        "weekly_savings": round(hours * (cell.get("rate") or 0), 2),
                    }
                )
                excess -= hours
                kept.remove((employee, cell))
            if excess <= 0 or not kept:
                continue
            kept_hours = sum(cell["hours"] for _, cell in kept)
            for employee, cell in kept:
                reduction = round(excess * cell["hours"] / kept_hours, 1)
                if reduction <= 0:
                    continue
                target_hours = round(cell["hours"] - reduction, 1)
                moves.append(
                    {
                        "employee_id": employee["id"],
                        "clin": clin["id"],
                        "kind": "trim",
                        "from_hours": cell["hours"],
                        "to_hours": target_hours,
                        "clears_lcat_flag": False,
                        "weekly_savings": round(reduction * (cell.get("rate") or 0), 2),
                    }
                )
    return moves


def _person_heat(employees: List[dict], moves: List[dict]) -> List[dict]:
    """Rank people by avoidable overrun dollars, never their gross billing rate."""
    by_person = {}
    names = {employee["id"]: employee for employee in employees}
    for move in moves:
        by_person.setdefault(move["employee_id"], []).append(move)
    people = []
    for employee_id, person_moves in by_person.items():
        employee = names[employee_id]
        savings = round(sum(move["weekly_savings"] for move in person_moves), 2)
        people.append(
            {
                "id": employee_id,
                "name": employee["name"],
                "lcat": employee.get("lcat"),
                "avoidable_weekly_overrun": savings,
                "moves": person_moves,
            }
        )
    return sorted(
        people,
        key=lambda person: (-person["avoidable_weekly_overrun"], person["name"]),
    )


def compute_allocation(
    contract: dict,
    rows: List[dict],
    expenses: Optional[List[dict]] = None,
    cost_model: Optional[rates.CostModel] = None,
    expected_hours_by_person: Optional[dict] = None,
    burn_data: Optional[dict] = None,
) -> dict:
    """Employee x labor-CLIN allocation for the active period, plus the per-CLIN
    budget/spend/clock the simulator recomputes runway against.

    `expected_hours_by_person` is `{employee_id: raw stored hours}` for #84's
    per-person override — data the *caller* read, passed in rather than looked up, so
    this module still cannot reach the people directory (a test pins that: allocation
    must never own a roster). Everyone in the grid is here because they charged time;
    a person with an expected week and no hours does not appear.
    """
    b = (
        burn_data
        if burn_data is not None
        else burn.compute(contract, rows, expenses, cost_model)
    )
    # Level 1 when the caller supplied nothing: cost falls back to the billing rate
    # and every cell says so (#77).
    cost_model = cost_model or rates.CostModel()
    bc = b["contract"]
    period = burn._active_period(contract, rows)
    window, _ = burn._effective_window(period, rows)
    labor = [c for c in burn._period_clins(contract, period) if c.get("is_labor")]

    # Rate-line resolution context (#64), built on the same period-scoped CLIN set
    # burn uses — so a cell's flag and the Flight Deck's banner are the same verdict
    # from the same index, including the user's confirmed LCAT mappings.
    rate_index = lcat_match.build_index(burn._period_clins(contract, period))
    aliases = lcat_match.parse_aliases(contract.get("lcat_aliases"))

    # Per-CLIN money/clock read straight off the burn payload — the simulator's
    # "actuals" baseline and the budget each edited column is measured against.
    burn_by_id = {c["id"]: c for c in b["clins"] if c.get("is_labor")}
    clin_cards = []
    resolvers = {}  # clin id -> resolve(lcat) -> lcat.Resolution
    for c in labor:
        num = burn._clin_num(c)
        resolve, blended, source = burn._rate_resolver(c, rate_index, aliases)
        resolvers[num] = resolve
        card = burn_by_id.get(num, {})
        clin_cards.append(
            {
                "id": num,
                "code": f"CLIN {num}",
                "name": c.get("title"),
                "budget": card.get("budget", 0.0),
                "spent": card.get("spent", 0.0),
                "remaining": card.get("remaining", 0.0),
                "incrementally_funded": card.get("incrementally_funded", False),
                # Enough funding context for the simulator to reach the same
                # verdict the engine does: whether a projected shortfall is a real
                # ceiling breach, or routine incremental funding that should read
                # amber. Without these the matrix scored every shortfall as red.
                "ceiling": card.get("ceiling", 0.0),
                "mod_in_progress": card.get("mod_in_progress", False),
                "funding_keeps_pace": card.get("funding_keeps_pace", True),
                # Actuals baseline the simulator diffs against.
                "base_weekly": card.get("weekly", 0.0),
                "base_status": card.get("status"),
                "base_exhaust_week": card.get("exhaust_week"),
                "base_runway_days": card.get("runway_days"),
                "rate_source": source,
                "blended_rate": round(blended, 2) if blended else None,
                # The picker needs this CLIN's actual priced categories, not the
                # contract-wide mapping targets. Keep the award's qualification
                # floors beside each rate so staffing can be planned with the same
                # information the later compliance check will read.
                "rate_lines": [
                    {
                        "lcat": (line.get("lcat") or "").strip(),
                        "rate": round(float(line["loaded_rate"]), 2),
                        "min_education": line.get("min_education"),
                        "min_experience_yrs": line.get("min_experience_yrs"),
                        "clearance": line.get("clearance"),
                    }
                    for line in (c.get("labor_rates") or [])
                    if (line.get("lcat") or "").strip()
                    and line.get("loaded_rate") is not None
                ],
                "unmatched_lcats": card.get("unmatched_lcats", []),
                # Why this CLIN's LCATs didn't match, from burn (#64). The card
                # renders one banner off `rate_table_missing` — a missing rate
                # schedule is a document problem, not 40 people's problem — and
                # per-LCAT mapping offers off `lcat_issues`.
                "rate_table_missing": card.get("rate_table_missing", False),
                "lcat_issues": card.get("lcat_issues", []),
                "aliased_lcats": card.get("aliased_lcats", []),
            }
        )

    # Walk each CLIN's charges (scoped to the active PoP window, same as burn) and
    # build per-employee avg hrs/wk + the LCAT/rate that hrs bills at.
    employees = {}  # emp_id -> {id, name, cells: {clin_id: {hours, lcat, rate}}}
    clin_cards_by_id = {card["id"]: card for card in clin_cards}
    for c in labor:
        num = burn._clin_num(c)
        resolve = resolvers[num]
        clin_rows = burn._rows_for_clin(c, rows, window)
        recent = set(_recent_weeks(clin_rows))
        n_weeks = len(recent) or 1

        # Per employee on this CLIN: total hrs in the recent window + their LCAT
        # (the one they logged the most hours under, so the rate is representative).
        hrs_by_emp = {}
        lcat_hrs = {}  # emp_id -> {lcat: hours}
        for r in clin_rows:
            if r.get("week_ending") not in recent:
                continue
            emp = r.get("employee_id")
            if not emp:
                continue
            # Billable only (#85) — the matrix's starting hrs/wk has to reconcile
            # with what burn.py priced, and a week with PTO in it billed fewer
            # hours than it paid.
            h = burn.billable_hours(r)
            hrs_by_emp[emp] = hrs_by_emp.get(emp, 0.0) + h
            lc = (r.get("labor_category") or "").strip()
            lcat_hrs.setdefault(emp, {})[lc] = lcat_hrs.get(emp, {}).get(lc, 0.0) + h

        for emp, total in hrs_by_emp.items():
            lcat = (
                max(lcat_hrs[emp], key=lcat_hrs[emp].get) if lcat_hrs.get(emp) else ""
            )
            res = resolve(lcat)
            row = employees.setdefault(
                emp, {"id": emp, "name": _emp_name(rows, emp), "cells": {}}
            )
            row["cells"][num] = {
                "hours": round(total / n_weeks, 1),
                "lcat": lcat or None,
                "rate": round(res.rate, 2) if res.rate else None,
                # LCAT charged with no matching rate line — the honest compliance
                # flag (burn surfaces the same set as unmatched_lcats).
                "unmatched": bool(lcat) and not res.matched,
                # Which of the three failures this is (#64), so the cell can offer
                # the fix instead of a dead-end tooltip: a missing rate schedule is
                # reported once on the CLIN card and deliberately NOT painted red per
                # cell, "priced elsewhere" names the CLIN that prices it, and a real
                # gap carries the closest candidate for the user to confirm.
                "cause": res.cause,
                "priced_on": (
                    res.line.clin
                    if res.cause == lcat_match.PRICED_ELSEWHERE and res.line
                    else None
                ),
                "suggestion": (
                    res.line.payload()
                    if res.cause == lcat_match.NO_RATE_LINE and res.line
                    else None
                ),
                # How the rate was resolved — `alias` means a user-confirmed mapping
                # is pricing these hours, which the matrix shows rather than passing
                # off as a printed rate line.
                "via": res.via,
                "rate_line": res.line.payload() if res.matched and res.line else None,
                "planned_lcat": res.line.lcat if res.matched and res.line else lcat,
                # Cost next to price, per person per CLIN (#77) — the acceptance
                # criterion that cost and price be separately readable for any
                # (person, CLIN, week). `cost_known` false means this is the billing
                # rate standing in because no direct rate was provided for them, so
                # the two columns are equal on purpose and margin is not derivable.
                **(
                    lambda cr: {
                        "cost_rate": round(cr.rate, 2) if cr.rate is not None else None,
                        "cost_source": cr.source,
                        "cost_known": cr.known,
                    }
                )(cost_model.cost_for(lcat, res.rate, emp)),
            }

        # The award's per-LCAT estimated hours are the staffing demand. Subtract
        # billed hours across the active period, then spread what remains over the
        # remaining PoP weeks. A category without an estimate is intentionally not
        # planned here: guessing a target is worse than omitting a suggestion.
        actual_by_lcat = {}
        for r in clin_rows:
            res = resolve((r.get("labor_category") or "").strip())
            if not res.matched or not res.line:
                continue
            key = res.line.lcat
            actual_by_lcat[key] = actual_by_lcat.get(key, 0.0) + burn.billable_hours(r)
        weeks_remaining = max(1, bc.get("weeks_remaining") or 0)
        planned = {}
        for line in c.get("labor_rates") or []:
            name = (line.get("lcat") or "").strip()
            estimated = line.get("est_hours")
            if name and estimated is not None:
                planned[name] = round(
                    max(0.0, float(estimated) - actual_by_lcat.get(name, 0.0))
                    / weeks_remaining,
                    1,
                )
        clin_cards_by_id[num]["planned_lcat_hours"] = planned

    # Contract- and LCAT-level expected hours, read once off the contract's blob.
    caps = capacity.contract_capacity(contract)
    overrides = expected_hours_by_person or {}

    # A person's headline LCAT/rate for the row = the CLIN they bill the most hrs on.
    emp_list = []
    for row in employees.values():
        primary = max(
            row["cells"].values(), key=lambda cell: cell["hours"], default=None
        )
        row["lcat"] = primary["lcat"] if primary else None
        row["rate"] = primary["rate"] if primary else None
        # #84: the matrix no longer divides by 40. Resolved here so the grid, the
        # portfolio endpoint and the People view all read one number resolved one way
        # — the whole reason this lives on the server.
        row["expected"] = capacity.resolve(
            person_hours=overrides.get(row["id"]),
            lcat=row["lcat"],
            capacity=caps,
        )
        row["hours"] = round(sum(c["hours"] for c in row["cells"].values()), 1)
        row["utilization"] = capacity.utilization(
            row["hours"], row["expected"]["hours"]
        )
        emp_list.append(row)
    emp_list.sort(
        key=lambda r: (-sum(c["hours"] for c in r["cells"].values()), r["name"])
    )

    return {
        "contract": {
            "id": bc["id"],
            "piid": bc["piid"],
            "name": bc["name"],
            "period": bc["period"],
            "current_week": bc["current_week"],
            "total_weeks": bc["total_weeks"],
            "weeks_remaining": bc["weeks_remaining"],
            "past_pop": bc["past_pop"],
            # #84's contract-level settings, so the matrix can show the target it is
            # measuring against and seed a planned add from it — without a second
            # fetch, and without restating the default in JSX.
            "utilization_target": caps["target"],
            "expected_hours": capacity.resolve(capacity=caps),
            # #85's holiday calendar and per-person absences, plus the calendar date
            # week 1 begins on. The matrix walks weeks client-side, so it needs the
            # same date→week mapping the engine used, or the two views place the same
            # absence in different weeks and disagree about the runway. Read off the
            # burn payload rather than re-derived, for the reason everything else in
            # this dict is.
            "pop_start": bc["pop_start"],
            "pop_end": bc["pop_end"],
            "absence": bc["absence"],
        },
        "clins": clin_cards,
        "employees": emp_list,
        "hot_people": _person_heat(emp_list, _staffing_moves(emp_list, clin_cards)),
    }
