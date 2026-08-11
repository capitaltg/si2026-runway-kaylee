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
from . import compliance
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


def _clin_hours(clin: dict, rows: List[dict], window):
    """Per-employee billable hours on one CLIN in its trailing window, the LCAT split
    behind them, and how many weeks the window spans.

    Shared by the matrix and by `booked_hours` below so a person's hrs/wk is one
    number computed one way — the cross-contract sum has to reconcile with the cells
    it is compared against, and two copies of this walk would drift.
    """
    clin_rows = burn._rows_for_clin(clin, rows, window)
    recent = set(_recent_weeks(clin_rows))
    n_weeks = len(recent) or 1

    hrs_by_emp = {}
    lcat_hrs = {}  # emp_id -> {lcat: hours}
    for r in clin_rows:
        if r.get("week_ending") not in recent:
            continue
        emp = r.get("employee_id")
        if not emp:
            continue
        # Billable only (#85) — the matrix's starting hrs/wk has to reconcile with
        # what burn.py priced, and a week with PTO in it billed fewer hours than it
        # paid.
        h = burn.billable_hours(r)
        hrs_by_emp[emp] = hrs_by_emp.get(emp, 0.0) + h
        lc = (r.get("labor_category") or "").strip()
        lcat_hrs.setdefault(emp, {})[lc] = lcat_hrs.get(emp, {}).get(lc, 0.0) + h
    return hrs_by_emp, lcat_hrs, n_weeks


def booked_hours(contract: dict, rows: List[dict]) -> dict:
    """`{employee_id: hrs/wk}` on one contract — the matrix's hours without the money.

    The cheap half of `compute_allocation`, extracted for #116: a person's headroom is
    their expected week minus what they are booked *everywhere*, so every consumer of
    one contract's grid needs the hours on the others. Doing that with a full
    `compute_allocation` per contract would run a burn pass — rates, funding, forecast
    — to read a column of hours, on every allocation request.

    Same window, same billable rule and the same per-CLIN rounding the cells use, so
    summing this across contracts is summing the numbers the grid shows.
    """
    period = burn._active_period(contract, rows)
    window, _ = burn._effective_window(period, rows)
    out: dict = {}
    for c in burn._period_clins(contract, period):
        if not c.get("is_labor"):
            continue
        hrs_by_emp, _lcats, n_weeks = _clin_hours(c, rows, window)
        for emp, total in hrs_by_emp.items():
            out[emp] = out.get(emp, 0.0) + round(total / n_weeks, 1)
    return {emp: round(h, 1) for emp, h in out.items() if h > 0}


def compute_allocation(
    contract: dict,
    rows: List[dict],
    expenses: Optional[List[dict]] = None,
    cost_model: Optional[rates.CostModel] = None,
    expected_hours_by_person: Optional[dict] = None,
    hours_elsewhere_by_person: Optional[dict] = None,
    quals_by_person: Optional[dict] = None,
) -> dict:
    """Employee x labor-CLIN allocation for the active period, plus the per-CLIN
    budget/spend/clock the simulator recomputes runway against.

    `expected_hours_by_person` is `{employee_id: raw stored hours}` for #84's
    per-person override — data the *caller* read, passed in rather than looked up, so
    this module still cannot reach the people directory (a test pins that: allocation
    must never own a roster). Everyone in the grid is here because they charged time;
    a person with an expected week and no hours does not appear.

    `hours_elsewhere_by_person` is `{employee_id: [{contract_id, contract, hours}]}`
    for the *other* contracts this person charges (#116) — built by the caller from
    `booked_hours`, passed in for the same reason. Without it, `headroom` on a row is
    this contract's hours against a whole-person expectation, so someone at 20 hrs/wk
    here and 20 elsewhere reads as having 20 hours of slack on both grids and the same
    hours get offered to two different underburning lines. Omitted means "nobody
    asked", which is why the payload says so on `cross_contract` rather than letting a
    zero pass for a checked zero.

    `quals_by_person` is `{employee_id: {field: {"value": ...}}}` for #66's compliance
    check — the same passed-in-not-looked-up arrangement, and for the sharper version
    of the same reason: a person's degree is not a fact about a contract, so the grid
    reading it directly would be the module reaching into the directory it is not
    allowed to know about. Omitted means nobody has typed any quals, which produces
    `unknown` verdicts and never `compliant` ones.
    """
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
                # Whether the *actual* ceiling is the limit in jeopardy, not just the
                # current tranche. This is the difference between "you need a mod" and
                # "you need to spend less", and the solver cannot tell them apart
                # without it: ceiling headroom alone doesn't settle it, because a line
                # can be projected past its ceiling *and* hold an unobligated slice at
                # the same time (live: contract 12, $277K unobligated under a ceiling
                # it is projected to blow by week 35). Defaults True so a payload
                # without the flag keeps the staffing answer rather than silently
                # deciding a breach is somebody else's paperwork.
                "ceiling_breached": card.get("ceiling_breached", True),
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
                # …and which gap it is, so the card can withhold "import the rate
                # schedule" from a CLIN whose schedule is already here (#139).
                "rate_table_state": card.get(
                    "rate_table_state", lcat_match.TABLE_ABSENT
                ),
                # …and whether that gap reaches the spend this card reports (#144).
                # False on a cost-measured CLIN priced entirely from declared direct
                # rates: the mapping is still missing and the picker still has no rate
                # to offer, but "all N categories bill at the blended rate" is not
                # what the money on this card did.
                "blended_priced_spend": card.get("blended_priced_spend", True),
                "lcat_issues": card.get("lcat_issues", []),
                "aliased_lcats": card.get("aliased_lcats", []),
            }
        )

    # Walk each CLIN's charges (scoped to the active PoP window, same as burn) and
    # build per-employee avg hrs/wk + the LCAT/rate that hrs bills at.
    employees = {}  # emp_id -> {id, name, cells: {clin_id: {hours, lcat, rate}}}
    quals = quals_by_person or {}
    for c in labor:
        num = burn._clin_num(c)
        resolve = resolvers[num]
        # This CLIN's priced lines as resolved objects, for #66's over-qualified sweep
        # — "does this person clear a better-paid category on this same CLIN". Built
        # here rather than reused from the `rate_lines` payload below because the sweep
        # compares floors, and the payload is a display shape.
        candidates = [
            lcat_match.RateLine(
                clin=num,
                lcat=(lr.get("lcat") or "").strip(),
                rate=float(lr["loaded_rate"]),
                key=lcat_match.normalize(lr.get("lcat")),
                floors=lcat_match.Floors.from_rate_line(lr),
            )
            for lr in (c.get("labor_rates") or [])
            if (lr.get("lcat") or "").strip() and lr.get("loaded_rate")
        ]
        # Per employee on this CLIN: total hrs in the recent window + their LCAT
        # (the one they logged the most hours under, so the rate is representative).
        hrs_by_emp, lcat_hrs, n_weeks = _clin_hours(c, rows, window)

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
                # #66: is the person filling this seat qualified for the category it
                # bills at? Checked against the *resolved* line only — when the LCAT
                # didn't match, `res.line` is a suggestion, and grading somebody
                # against a category nobody has agreed they bill under would be a
                # finding invented out of a mapping guess.
                "compliance": compliance.check(
                    quals.get(emp),
                    res.line if res.matched else None,
                    candidates,
                ),
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

    # Contract- and LCAT-level expected hours, read once off the contract's blob.
    caps = capacity.contract_capacity(contract)
    overrides = expected_hours_by_person or {}
    elsewhere_by_person = hours_elsewhere_by_person or {}

    # A person's headline LCAT/rate for the row = the CLIN they bill the most hrs on.
    emp_list = []
    for row in employees.values():
        primary = max(
            row["cells"].values(), key=lambda cell: cell["hours"], default=None
        )
        row["lcat"] = primary["lcat"] if primary else None
        row["rate"] = primary["rate"] if primary else None
        # #66: the row's badge is the worst verdict across the person's CLINs, not the
        # primary cell's. Somebody clean on the three CLINs they mostly sit on and
        # short a clearance on the fourth is a clearance gap — the badge follows the
        # exposure, and `compliance_cells` lets the panel name which CLIN it came from.
        row["compliance_status"] = compliance.worst(
            cell["compliance"]["status"] for cell in row["cells"].values()
        )
        row["compliance_cells"] = {
            num: cell["compliance"]["status"] for num, cell in row["cells"].items()
        }
        # How much is on file about them at all, independent of any one line's floors —
        # the difference between "we checked and they're short" and "there is nothing
        # here to check", which is what the fill-in-the-blanks path hangs off.
        row["quals_status"] = compliance.quals_coverage(quals.get(row["id"]))
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
        # #116: the expectation is a whole-person week, so the hours it is measured
        # against have to be the whole person's too. `hours` stays this contract's —
        # it is what the grid's cells sum to and the simulator edits — and the
        # cross-contract figures ride beside it.
        elsewhere = elsewhere_by_person.get(row["id"]) or []
        row["elsewhere"] = elsewhere
        row["hours_elsewhere"] = round(sum(e["hours"] for e in elsewhere), 1)
        row["hours_booked"] = round(row["hours"] + row["hours_elsewhere"], 1)
        # What this person actually has left, everywhere. Never negative: someone
        # already past their expectation has no slack to offer, not negative slack.
        row["headroom"] = round(
            max(0.0, float(row["expected"]["hours"]) - row["hours_booked"]), 1
        )
        emp_list.append(row)
    emp_list.sort(
        key=lambda r: (-sum(c["hours"] for c in r["cells"].values()), r["name"])
    )

    # #66 rollups. Per CLIN over the cells on that CLIN, then per contract over
    # *people* — deliberately not summed from the CLIN rollups, because somebody
    # charging three CLINs would count three times and "29 not yet checked" would come
    # out larger than the number of people on the contract.
    for card in clin_cards:
        card["compliance"] = compliance.rollup(
            [
                cell["compliance"]["status"]
                for row in employees.values()
                for num, cell in row["cells"].items()
                if num == card["id"]
            ]
        )
    contract_compliance = compliance.rollup([r["compliance_status"] for r in emp_list])

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
            # Whether anyone's other contracts were actually looked at (#116). False
            # means every `headroom` on this payload is this contract's view alone —
            # a surface that reads it as "hours this person has left" must say so, and
            # the solver must not spend slack it has not checked.
            "cross_contract": hours_elsewhere_by_person is not None,
            # #66's contract-level counters. `quals_checked` says whether anybody has
            # typed anything at all, so a surface can tell "nobody has started" apart
            # from "everybody came back clean" — the two look identical if you only
            # read the findings count, and reporting the first as the second is the
            # failure mode this feature is most able to cause.
            "compliance": contract_compliance,
            "quals_checked": bool(quals),
        },
        "clins": clin_cards,
        "employees": emp_list,
    }
