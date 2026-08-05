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


def compute_allocation(
    contract: dict,
    rows: List[dict],
    expenses: Optional[List[dict]] = None,
    cost_model: Optional[rates.CostModel] = None,
) -> dict:
    """Employee x labor-CLIN allocation for the active period, plus the per-CLIN
    budget/spend/clock the simulator recomputes runway against."""
    b = burn.compute(contract, rows, expenses, cost_model)
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

    # A person's headline LCAT/rate for the row = the CLIN they bill the most hrs on.
    emp_list = []
    for row in employees.values():
        primary = max(
            row["cells"].values(), key=lambda cell: cell["hours"], default=None
        )
        row["lcat"] = primary["lcat"] if primary else None
        row["rate"] = primary["rate"] if primary else None
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
        },
        "clins": clin_cards,
        "employees": emp_list,
    }
