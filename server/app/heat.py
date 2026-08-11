"""Who is running hot, and what their hours are costing the contract (#83).

The Flight Deck is CLIN-level. It says CLIN 0002 exhausts in week 44, six weeks
early, and gives no way to see which people produced that. This module answers the
PM's actual next question — *who is running hot* — and answers it the way an
accountant asks it: **hours worked against hours available**, not hours worked
against a flat 40.

## What "hot" means here

Over-capacity, measured *cumulatively over the trailing charged weeks* and reported
as hrs/wk. Cumulative on purpose: a 50-hour week followed by a 30-hour week is not
overtime, it is a normal fortnight, and a per-week `> 40` test would flag both. Four
weeks is also close enough to a month that the "160 available, worked 180" framing
reads straight through — but the axis stays weekly, because weekly is the cadence
every other number in Runway is expressed in.

`available` is not a constant. It is `capacity.resolve()`'s expected week (#84 —
person override → the CLIN's LCAT default → the contract's target → 40, assumed)
times the weeks in the window, **minus recorded leave and holidays**. Netting those
out is what keeps the check from reading backwards: a month with one holiday offers
152 hours, so someone who billed 160 worked eight hours over, and naive arithmetic
would have called them exactly on plan.

The numerator is `burn.billable_hours` — regular + overtime, the same quantity the
engine already priced against the CLIN. So the dollars here reconcile with the
Flight Deck's by construction rather than by coincidence.

## Overtime is corroboration, not the signal

`ot_hours` is only populated when the feed sends the split, which older syncs did
not. So over-capacity is derived from total billable hours, which every row has, and
the payroll-confirmed OT figure is reported *beside* it when present. A contract
whose feed never carried the split still gets an honest answer; it just says "hours
over expected" instead of naming overtime.

## The two forecasts are a diagnosis, not two numbers

Forward pace is averaged over trailing weeks that *contain* the overtime, so Runway
has quietly been projecting overtime as the baseline forever. Removing the excess
hours' dollars from that pace gives a second exhaust week, and the pair says what to
actually do:

  * early now, lands fine at expected hours → the overtime **is** the problem: stop it
  * early both ways → too many hours on the CLIN even at plan: **cut people**

Without the second projection those two look identical on the dashboard and get the
opposite remedy.

## This does not rank people against a target

`capacity.py` states that nothing built on it may score people against their
expected hours, and that constraint is kept: the gate here is **the CLIN being
off-pace**, not the person being over. On a healthy contract nobody surfaces however
many hours they work. What is ranked is *where an off-pace CLIN's money is going*,
which is a fact about the contract. Runway never says someone is underperforming.

## The hours ceiling (the "held to the hours on contract" half)

An award that prints a labor rate table often prints estimated hours beside each
category, and `LaborRate.est_hours` has been captured at ingest since #64 and read
by nothing. That number is the contracted quantity for a category on a CLIN, so
charged-vs-contracted hours and the week the category runs out are both computable
where it exists. It is a *category* total over the period, deliberately NOT used as
anyone's personal denominator — one person's available week is `capacity.resolve()`'s
job and conflating the two would make a part-timer's expectation depend on how many
people share their LCAT.

Pure — no DB. Takes the contract blob, its timesheet rows and the allocation payload,
and reads the period window through `burn`'s helpers so the window, the rates and the
money are the same ones the Flight Deck and the matrix already agree on.
"""

from typing import List, Optional

from . import burn
from . import lcat as lcat_match

# A person's excess has to clear this many hrs/wk before they are named. Below it
# the finding is rounding on a four-week window — a single 90-minute long day — and
# a dashboard that names someone over 40 minutes a week trains people to ignore it.
MIN_OVER_HOURS_PER_WEEK = 1.0

# There is deliberately NO dollar floor to clear. An earlier draft had one, and it
# was a rate-based *inclusion* filter: someone on $20/hr working 2 hrs/wk over
# contributed $40/wk and was dropped from the list entirely, while someone on $250/hr
# working the identical 2 hrs was kept. That makes the cheaper half of a team
# invisible on a report about how much people are working, which is worse than a
# noisy list. The hours floor above is rate-neutral and does the whole job.

# CLIN states that open the gate. `watch` is included because the point of the
# second forecast is to catch a CLIN whose overtime is *about* to push it over,
# which is precisely a watch. `fee_eroding` (#81) refines `ok`/`watch` on a cost-type
# CLIN eating its fee, so it has to be listed or those lines would fall out of the gate
# the moment the state shipped — and overtime on a line already past estimated cost is
# the sharpest version of the thing this gate looks for.
HOT_CLIN_STATES = ("over", "watch", "fee_eroding")

# The two diagnoses, and the remedy each implies. #63's move solver branches on
# these rather than re-deriving them.
STOP_OVERTIME = "stop_overtime"
REDUCE_STAFFING = "reduce_staffing"

_DIAGNOSIS_LABELS = {
    STOP_OVERTIME: (
        "Hours above plan are the whole overrun — at expected hours this CLIN "
        "finishes inside its budget."
    ),
    REDUCE_STAFFING: (
        "This CLIN runs out early even with everyone at their expected hours — "
        "there is more staffing on it than the remaining budget funds."
    ),
}


def _f(value) -> float:
    """A float, treating None and junk as 0 — these are summed, never divided by."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _labor_charges(
    contract: dict, period: dict, rows: List[dict], window
) -> List[tuple]:
    """`[(clin_id, row)]` for every labor charge in the active period.

    Routed through `burn._rows_for_clin` rather than matching `charge_code` directly,
    because CLIN matching is not string equality — a `0001AA` subCLIN charge rolls up
    to `0001`, and a bare `0001` charge rolls into an option period's `2001`. Doing
    that matching by hand here would silently drop a person's hours on exactly the
    contracts where subCLINs are used.

    A row can legitimately appear under one CLIN only, so the pairs are unique per
    (row, clin) and summing over them cannot double-count a week — except where the
    feed itself repeats a day, which is the feed describing the week twice and not
    something this module can detect.
    """
    charges = []
    for c in burn._period_clins(contract, period):
        if not c.get("is_labor"):
            continue
        num = burn._clin_num(c)
        if not num:
            continue
        for r in burn._rows_for_clin(c, rows, window):
            charges.append((num, r))
    return charges


def _window_weeks(charges: List[tuple]) -> List[str]:
    """The trailing charged weeks the capacity check is measured over.

    Contract-level rather than per-CLIN, which is where this deliberately differs
    from `allocation._recent_weeks`. Capacity is a property of the person: someone
    booked 20 hrs/wk on each of two CLINs is working a full week, and two separate
    per-CLIN windows can neither see that nor subtract their leave once.
    """
    weeks = sorted({r.get("week_ending") for _, r in charges if r.get("week_ending")})
    return weeks[-burn._PACE_WEEKS :]


def _person_window_hours(charges: List[tuple], weeks) -> dict:
    """Per employee, in the trailing window: billable / leave / holiday / OT hours,
    and billable hours split by CLIN."""
    by_emp = {}
    weekset = set(weeks)
    for code, r in charges:
        if r.get("week_ending") not in weekset:
            continue
        emp = r.get("employee_id")
        if not emp:
            continue
        rec = by_emp.setdefault(
            emp,
            {
                "name": (r.get("employee") or "").strip() or emp,
                "billable": 0.0,
                "leave": 0.0,
                "holiday": 0.0,
                "ot": 0.0,
                "ot_known": False,
                "by_clin": {},
                "lcat_by_clin": {},
            },
        )
        if (r.get("employee") or "").strip():
            rec["name"] = r["employee"].strip()
        hours = burn.billable_hours(r)
        rec["billable"] += hours
        rec["by_clin"][code] = rec["by_clin"].get(code, 0.0) + hours
        lc = (r.get("labor_category") or "").strip()
        if lc:
            per = rec["lcat_by_clin"].setdefault(code, {})
            per[lc] = per.get(lc, 0.0) + hours
        rec["leave"] += _f(r.get("leave_hours"))
        rec["holiday"] += _f(r.get("holiday_hours"))
        # The split's presence is the version signal, exactly as `billable_hours`
        # reads it. A row that carries `reg_hours` is authoritative about its OT
        # even when that OT is zero.
        if r.get("reg_hours") is not None:
            rec["ot_known"] = True
            rec["ot"] += _f(r.get("ot_hours"))
    return by_emp


def _alloc_lookup(alloc: dict) -> dict:
    """`{employee_id: allocation row}` — for the resolved expected week and the
    per-CLIN rate, both of which are already resolved once on the server and must
    not be re-derived here."""
    return {row["id"]: row for row in (alloc.get("employees") or [])}


def _rate_for(alloc_row: Optional[dict], clin_id: str) -> Optional[float]:
    cell = ((alloc_row or {}).get("cells") or {}).get(clin_id) or {}
    rate = cell.get("rate")
    return float(rate) if rate else None


def _hours_ceilings(
    contract, period, rows, window, clin_cards, weeks, current_week, total_weeks
):
    """Charged vs contracted hours per (CLIN, labor category), where the award
    printed the estimate.

    Two tiers, most specific first: a rate line's own `est_hours` describes exactly
    one category on one CLIN, so it is preferred; a CLIN's `est_hours` describes the
    whole line item and is used only when no rate line carries hours, reported as
    covering the CLIN rather than a category.

    Charged hours are the whole active period, not the trailing window — a ceiling is
    consumed cumulatively. The *pace* is the trailing window, because that is what
    forecasts the week it runs out.

    A charge is attributed to a category the way the rest of the app attributes it —
    on `lcat.normalize`'s key, then a confirmed alias (#64) — never on the raw string.
    Raw-string equality was the original defect: a timesheet's "Senior Systems
    Engineer" already bills at an award's "Sr. Systems Engineer" line, so the hours
    were priced against it while the ceiling built from that same line could not see
    them. It reported no finding on a category 80 hours past its estimate, and where
    two spellings shared a CLIN it reported hours *remaining* on a category 120 hours
    over — a forecast the module exists to withhold.
    """
    out = []
    card_by_id = {c["id"]: c for c in clin_cards}
    recent = set(weeks)
    n_recent = len(recent) or 1
    aliases = lcat_match.parse_aliases(contract.get("lcat_aliases"))

    for c in burn._period_clins(contract, period):
        if not c.get("is_labor"):
            continue
        num = burn._clin_num(c)
        if num not in card_by_id:
            continue
        clin_rows = burn._rows_for_clin(c, rows, window)

        lines = [
            line
            for line in (c.get("labor_rates") or [])
            if (line.get("lcat") or "").strip() and line.get("est_hours")
        ]
        # Every category this CLIN prices, priced or not, so a charge that folds onto
        # one of its own lines is never handed to an alias — the same precedence
        # `lcat.resolver` uses, where a line the award actually prints outranks a
        # mapping written to fix an old misspelling.
        own_keys = {
            lcat_match.normalize(line.get("lcat"))
            for line in (c.get("labor_rates") or [])
            if (line.get("lcat") or "").strip()
        }

        def charged_key(row) -> str:
            key = lcat_match.normalize(row.get("labor_category"))
            if key in own_keys:
                return key
            alias = aliases.get(key)
            # A cross-CLIN alias is deliberately left alone: it moves the *rate* to
            # another line item's rate line, and whether it should also consume that
            # line item's contracted quantity is a question about the award, not a
            # matching bug. Noted rather than guessed.
            if alias and (not alias["clin"] or alias["clin"] == num):
                return lcat_match.normalize(alias["lcat"])
            return key

        keyed_rows = [(r, charged_key(r)) for r in clin_rows]

        if lines:
            # Folded by key, so two spellings of one category on the same schedule
            # produce one row rather than two that both claim the same charges. Their
            # estimates sum: the award contracted that many hours of that category,
            # however many lines it took to print them.
            merged: dict = {}
            for line in lines:
                name = (line["lcat"] or "").strip()
                key = lcat_match.normalize(name)
                if key in merged:
                    merged[key]["contracted"] += float(line["est_hours"])
                    continue
                merged[key] = {"lcat": name, "contracted": float(line["est_hours"])}
            targets = [
                (m["lcat"], key, m["contracted"], "rate_line")
                for key, m in merged.items()
            ]
        elif c.get("est_hours"):
            targets = [(None, None, float(c["est_hours"]), "clin_total")]
        else:
            continue

        for lcat, key, contracted, source in targets:
            if contracted <= 0:
                continue
            matching = [r for r, row_key in keyed_rows if key is None or row_key == key]
            if not matching:
                continue
            charged = sum(burn.billable_hours(r) for r in matching)
            pace = (
                sum(
                    burn.billable_hours(r)
                    for r in matching
                    if r.get("week_ending") in recent
                )
                / n_recent
            )
            left = contracted - charged
            # An estimate already blown has no exhaust week — it happened. Projecting
            # `left / pace` anyway puts the date in the *past* (negative weeks added to
            # the current week), which reads as a forecast and is the one thing this
            # figure must never be. The overrun is the finding instead.
            overrun = left < 0
            weeks_left = (left / pace) if (pace > 0 and not overrun) else None
            exhaust_week = (
                round(current_week + weeks_left, 2) if weeks_left is not None else None
            )
            out.append(
                {
                    "clin": num,
                    "lcat": lcat,
                    "source": source,
                    "contracted_hours": round(contracted, 1),
                    "charged_hours": round(charged, 1),
                    "hours_remaining": round(left, 1),
                    "pct_charged": round(charged / contracted, 4),
                    "pace_per_week": round(pace, 1),
                    "exhaust_week": exhaust_week,
                    # News if the estimate is already gone, or runs out before the work
                    # does. A ceiling that outlasts the period is not a finding.
                    "early": bool(
                        overrun
                        or (
                            exhaust_week is not None
                            and total_weeks
                            and exhaust_week < total_weeks
                        )
                    ),
                    "overrun_hours": round(-left, 1) if left < 0 else None,
                }
            )
    out.sort(key=lambda h: (not h["early"], h["exhaust_week"] or 10**6))
    return out


def absence_hours(contract: dict, rows: List[dict]) -> dict:
    """`{employee_id: {leave, holiday}}` as hrs/wk on one contract (#116).

    The companion to `allocation.booked_hours`, and needed for the same reason. Leave
    is a property of the person's week, not of the contract that happened to record
    it: someone on two contracts who takes a fortnight off books it wherever their
    PTO code lives, and deducting it only there let the two contracts disagree about
    how much of the same week the same person had available — one saying 10 hrs/wk
    over, the other 30, about one person in one window.
    """
    period = burn._active_period(contract, rows)
    window, _ = burn._effective_window(period, rows)
    charges = _labor_charges(contract, period, rows, window)
    weeks = _window_weeks(charges)
    n_weeks = len(weeks) or 1
    out = {}
    for emp, rec in _person_window_hours(charges, weeks).items():
        if not rec["leave"] and not rec["holiday"]:
            continue
        out[emp] = {
            "leave": round(rec["leave"] / n_weeks, 2),
            "holiday": round(rec["holiday"] / n_weeks, 2),
        }
    return out


def compute_heat(contract: dict, rows: List[dict], alloc: dict) -> dict:
    """The people running hot on one contract, ranked by what their excess hours
    cost an off-pace CLIN each week.

    `alloc` is `allocation.compute_allocation`'s payload — passed in rather than
    recomputed so the expected week, the resolved rate and every CLIN's money and
    clock are literally the same numbers the allocation matrix shows.
    """
    period = burn._active_period(contract, rows)
    window, _ = burn._effective_window(period, rows)
    ac = alloc.get("contract") or {}
    current_week = ac.get("current_week") or 0
    total_weeks = ac.get("total_weeks") or 0
    clin_cards = [c for c in (alloc.get("clins") or [])]
    card_by_id = {c["id"]: c for c in clin_cards}

    charges = _labor_charges(contract, period, rows, window)
    weeks = _window_weeks(charges)
    n_weeks = len(weeks) or 1
    per_emp = _person_window_hours(charges, weeks)
    alloc_rows = _alloc_lookup(alloc)

    hot_clins = {
        cid
        for cid, card in card_by_id.items()
        if card.get("base_status") in HOT_CLIN_STATES
    }

    people = []
    excess_dollars_by_clin = {}
    for emp, rec in per_emp.items():
        arow = alloc_rows.get(emp)
        expected = (arow or {}).get("expected") or {}
        expected_wk = expected.get("hours")
        if not expected_wk:
            # No expectation resolved means there is nothing to be over. Missing
            # information is never reported as a finding.
            continue
        # Absence recorded on their other contracts, over the same window. Subtracted
        # here for the same reason the hours below are added: both sides of this
        # comparison have to describe the whole person, or two contracts reach two
        # verdicts about one week.
        away = (arow or {}).get("elsewhere") or []
        leave = rec["leave"] + sum(_f(e.get("leave")) for e in away) * n_weeks
        holiday = rec["holiday"] + sum(_f(e.get("holiday")) for e in away) * n_weeks
        available = max(0.0, float(expected_wk) * n_weeks - leave - holiday)
        # What they are booked on every *other* contract, over the same window (#116).
        # The expectation on the left of this comparison is a whole-person week, so
        # the hours on the right have to be the whole person's: someone at 40 hrs/wk
        # across two contracts clears the threshold on neither and never surfaces as
        # running hot, which is exactly the person a PM needs named.
        elsewhere_wk = _f((arow or {}).get("hours_elsewhere"))
        elsewhere = elsewhere_wk * n_weeks
        worked = rec["billable"] + elsewhere
        over = worked - available
        over_wk = over / n_weeks
        if over_wk < MIN_OVER_HOURS_PER_WEEK:
            continue

        # The excess apportions across everywhere their hours are, so this contract's
        # CLINs carry their share of it and not another contract's overtime.
        total_hours = worked or 1.0
        clin_impacts = []
        weekly_dollars = 0.0
        for cid, hours in sorted(rec["by_clin"].items()):
            if cid not in hot_clins:
                continue
            rate = _rate_for(arow, cid)
            if not rate:
                # Unpriced hours are a rate-table problem the CLIN card already
                # reports (#64). Carried without dollars rather than priced at a
                # made-up number.
                clin_impacts.append(
                    {
                        "clin": cid,
                        "hours_per_week": round(hours / n_weeks, 1),
                        "rate": None,
                        "weekly_dollars": None,
                        "unpriced": True,
                    }
                )
                continue
            share = hours / total_hours
            dollars = over_wk * share * rate
            weekly_dollars += dollars
            excess_dollars_by_clin[cid] = excess_dollars_by_clin.get(cid, 0.0) + dollars
            clin_impacts.append(
                {
                    "clin": cid,
                    "hours_per_week": round(hours / n_weeks, 1),
                    "rate": rate,
                    "weekly_dollars": round(dollars, 2),
                    "unpriced": False,
                }
            )
        if not clin_impacts:
            continue

        people.append(
            {
                "id": emp,
                "name": rec["name"],
                "lcat": (arow or {}).get("lcat"),
                "expected_hours_per_week": round(float(expected_wk), 2),
                "expected_level": expected.get("level"),
                "expected_label": expected.get("label"),
                # So a row can mark its own baseline as an assumption rather than
                # presenting an unconfigured 40 as a setting.
                "expected_assumed": bool(expected.get("assumed")),
                "worked_hours": round(rec["billable"], 1),
                # This contract's hours, their other contracts', and the total the
                # excess is actually measured from — so a row whose overtime is
                # somebody else's line can say so instead of reading as unexplained.
                "worked_hours_elsewhere": round(elsewhere, 1),
                "worked_hours_booked": round(worked, 1),
                "elsewhere": (arow or {}).get("elsewhere") or [],
                "available_hours": round(available, 1),
                "over_hours": round(over, 1),
                "over_hours_per_week": round(over_wk, 1),
                "leave_hours": round(leave, 1),
                "holiday_hours": round(holiday, 1),
                # Payroll-confirmed overtime, when the feed sent the split. False
                # `ot_known` means the hours are still over expected — we just may
                # not call it overtime.
                "ot_hours": round(rec["ot"], 1) if rec["ot_known"] else None,
                "ot_known": rec["ot_known"],
                "weekly_dollars": round(weekly_dollars, 2),
                "clins": clin_impacts,
            }
        )

    # Ranked by HOURS over expectation, not by dollars.
    #
    # Dollars were the first ordering and they encoded a pay ranking: on a live
    # contract, someone 3.5 hrs/wk over on a $167 rate sorted above someone 4.0 hrs/wk
    # over on $126, and people with identical excess hours came out ordered by their
    # billing rate. That tells a PM their expensive people are the problem, which is
    # both the wrong message and useless advice — you cannot make someone cheaper, and
    # a rate is a property of the award's price list rather than of how much anyone is
    # working. Two people equally over their hours are equally over.
    #
    # The dollars are still computed, still shown per row, and still summed per CLIN,
    # because that is what earns this a slot on a money dashboard and what the second
    # forecast is built from. They are a consequence here, not the rank. A surface that
    # wants the cost ordering has to ask for it and say so.
    people.sort(
        key=lambda p: (-p["over_hours_per_week"], -p["weekly_dollars"], p["name"])
    )

    clins = []
    for cid in sorted(hot_clins):
        card = card_by_id[cid]
        excess = excess_dollars_by_clin.get(cid, 0.0)
        weekly = _f(card.get("base_weekly"))
        remaining = _f(card.get("remaining"))
        at_expected = max(0.0, weekly - excess)
        weeks_at_expected = (remaining / at_expected) if at_expected > 0 else None
        # Fractional, like `burn`'s own exhaust week. Rounding to a whole week here
        # would make the two projections look identical whenever the overtime is
        # worth less than a week of runway — which is exactly the case the pair is
        # supposed to tell apart.
        exhaust_at_expected = (
            round(current_week + weeks_at_expected, 2)
            if weeks_at_expected is not None
            else None
        )
        # Lands fine = the budget outlasts the period once the excess hours come
        # off. `None` weeks means no forward burn at all at expected hours, which
        # also lands fine.
        lands_fine = (
            exhaust_at_expected is None
            or not total_weeks
            or exhaust_at_expected >= total_weeks
        )
        diagnosis = STOP_OVERTIME if (excess > 0 and lands_fine) else REDUCE_STAFFING
        clins.append(
            {
                "id": cid,
                "status": card.get("base_status"),
                "weekly": round(weekly, 2),
                "weekly_at_expected": round(at_expected, 2),
                "excess_weekly_dollars": round(excess, 2),
                "exhaust_week": card.get("base_exhaust_week"),
                "exhaust_week_at_expected": exhaust_at_expected,
                # What the overtime is costing, in the unit a PM cares about: weeks
                # of runway. The headline sentence of the whole feature.
                "weeks_bought": (
                    round(exhaust_at_expected - float(card["base_exhaust_week"]), 1)
                    if exhaust_at_expected is not None and card.get("base_exhaust_week")
                    else None
                ),
                "diagnosis": diagnosis,
                "diagnosis_label": _DIAGNOSIS_LABELS[diagnosis],
                "people": [
                    p["id"] for p in people if any(i["clin"] == cid for i in p["clins"])
                ],
            }
        )

    return {
        "window": {
            "weeks": n_weeks,
            "from": weeks[0] if weeks else None,
            "to": weeks[-1] if weeks else None,
        },
        "current_week": current_week,
        "total_weeks": total_weeks,
        "people": people,
        "clins": clins,
        "hours_ceilings": _hours_ceilings(
            contract, period, rows, window, clin_cards, weeks, current_week, total_weeks
        ),
    }
