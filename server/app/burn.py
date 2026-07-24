"""Burn / runway calculation engine (read-only).

Ports the reference calc layer embedded in the design (docs/design/Runway.dc.html,
`data-dc-script`) onto our real inputs: an ingested contract (with an extracted
Labor Rate Table) plus synced Fixtura timesheet rows. Drives the Flight Deck and
Portfolio views with no recomputation on the frontend — the view only builds the
SVG chart geometry from the stats returned here.

Rate resolution (the one value neither feed carries directly):
  1. the labor CLIN's extracted `labor_rates` table  →  LCAT → loaded $/hr
  2. blended fallback = ceiling / est_hours  (real contract arithmetic)
Every CLIN reports which source it used and any timesheet LCATs that didn't match
a rate line, so nothing is silently invented.
"""

from datetime import date
from typing import List, Optional

# Status thresholds, ported verbatim from the design's computeClinFor.
_PAUSED_WEEKS_LEFT = 999
_PACE_WEEKS = 4  # trailing distinct weeks used to estimate forward weekly burn


def _d(s: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except (ValueError, TypeError):
        return None


def _weeks_between(a: Optional[date], b: Optional[date]) -> Optional[int]:
    if not a or not b:
        return None
    return round((b - a).days / 7)


def _base_period(contract: dict) -> dict:
    """The period the burn clock runs against: the first exercised period, else
    the first period."""
    periods = contract.get("periods") or []
    for p in periods:
        if p.get("exercised"):
            return p
    return periods[0] if periods else {}


def _active_clins(contract: dict) -> List[dict]:
    """CLINs that belong to the current active period — the one the burn clock
    runs against (`_base_period`: the first exercised period).

    This is the guard against counting money that isn't in play yet. An award
    lists every option year's CLINs up front, but only the current period has
    obligated dollars and timesheet charges against it. Pricing all of them
    would inflate the ceiling and wreck the burn %, runway, and tripwire math.
    Un-exercised option CLINs are excluded here; exercised option CLINs carry
    no timesheets of their own (labor charges land on the base CLINs) so they
    would read as paused anyway. Keeping this consistent with `_clock`, which
    already anchors the week clock to the same base period.

    Degrades gracefully: if no CLIN carries a period label there is nothing to
    filter on, so every CLIN is kept.
    """
    active_name = (_base_period(contract).get("name") or "").strip().lower()
    clins = contract.get("clins") or []
    labeled = [c for c in clins if (c.get("period") or "").strip()]
    if not active_name or not labeled:
        return clins
    return [c for c in clins if (c.get("period") or "").strip().lower() == active_name]


def _clin_num(clin: dict) -> str:
    return str(clin.get("clin") or "").strip()


def _rows_for_clin(clin: dict, rows: List[dict]) -> List[dict]:
    """Timesheet rows charged to this CLIN. Exact charge_code match first, then
    subCLIN prefix (e.g. '0001AA' rolls up to '0001')."""
    num = _clin_num(clin)
    if not num:
        return []
    exact = [r for r in rows if (r.get("charge_code") or "").strip() == num]
    if exact:
        return exact
    return [r for r in rows if (r.get("charge_code") or "").strip().startswith(num)]


def _rate_resolver(clin: dict):
    """Return (rate_for_lcat, blended, source_label). rate_for_lcat(lcat) resolves
    an LCAT string to a $/hr, falling back to the blended rate when the rate table
    has no matching line."""
    table = clin.get("labor_rates") or []
    by_lcat = {}
    for lr in table:
        name = (lr.get("lcat") or "").strip()
        rate = lr.get("loaded_rate")
        if name and rate:
            by_lcat[name.lower()] = float(rate)

    ceiling = clin.get("ceiling") or 0
    est_hours = clin.get("est_hours") or 0
    blended = (ceiling / est_hours) if est_hours else None

    def rate_for(lcat: Optional[str]):
        key = (lcat or "").strip().lower()
        if key and key in by_lcat:
            return by_lcat[key], True
        return blended, False

    source = "rate_table" if by_lcat else ("blended" if blended else "none")
    return rate_for, blended, source


def _pill(status: str) -> str:
    return {
        "over": "Over ceiling",
        "watch": "Watch",
        "ok": "On pace",
        "paused": "Paused",
    }.get(status, "—")


def _compute_clin(clin: dict, rows: List[dict], current_week: int, total_weeks: int):
    """Per-CLIN spend, forward burn, runway and status — the heart of the engine."""
    rate_for, blended, source = _rate_resolver(clin)
    clin_rows = _rows_for_clin(clin, rows)

    spent = 0.0
    unmatched = set()
    weekly_totals = {}  # week_ending -> $ that week
    for r in clin_rows:
        hours = float(r.get("total_hours") or 0)
        rate, matched = rate_for(r.get("labor_category"))
        if rate is None:
            unmatched.add(r.get("labor_category") or "?")
            continue
        if not matched and r.get("labor_category"):
            unmatched.add(r.get("labor_category"))
        amt = hours * rate
        spent += amt
        wk = r.get("week_ending") or ""
        weekly_totals[wk] = weekly_totals.get(wk, 0.0) + amt

    # Forward weekly pace = mean weekly spend over the most recent PACE_WEEKS weeks
    # that actually have charges. Steadier than a single noisy week.
    recent_weeks = sorted(weekly_totals)[-_PACE_WEEKS:]
    weekly = (
        sum(weekly_totals[w] for w in recent_weeks) / len(recent_weeks)
        if recent_weeks
        else 0.0
    )

    ceiling = float(clin.get("ceiling") or 0)
    remaining = ceiling - spent
    pct = (spent / ceiling) if ceiling else 0.0

    if weekly <= 0:
        weeks_left = _PAUSED_WEEKS_LEFT
        status = "paused"
        runway_days = None
    else:
        weeks_left = remaining / weekly
        runway_days = round(weeks_left * 7)
    exhaust_week = current_week + weeks_left

    if weekly <= 0:
        status = "paused"
    elif exhaust_week < total_weeks - 1:
        status = "over"
    elif exhaust_week < total_weeks + 2:
        status = "watch"
    else:
        status = "ok"

    # Cumulative actuals by week index (0-based over the weeks that have charges),
    # for the Flight Deck chart. Frontend maps these onto the SVG.
    cum = 0.0
    series = []
    for i, w in enumerate(sorted(weekly_totals)):
        cum += weekly_totals[w]
        series.append({"week_ending": w, "cum_spent": round(cum, 2)})

    return {
        "id": _clin_num(clin),
        "code": f"CLIN {_clin_num(clin)}",
        "name": clin.get("title"),
        "is_labor": bool(clin.get("is_labor")),
        "ceiling": ceiling,
        "spent": round(spent, 2),
        "pct": round(pct, 4),
        "weekly": round(weekly, 2),
        "remaining": round(remaining, 2),
        "weeks_left": None if status == "paused" else round(weeks_left, 2),
        "exhaust_week": None if status == "paused" else round(exhaust_week, 2),
        "runway_days": runway_days,
        "status": status,
        "status_label": _pill(status),
        "rate_source": source,
        "blended_rate": round(blended, 2) if blended else None,
        "unmatched_lcats": sorted(unmatched),
        "actuals": series,
    }


def _clock(contract: dict, rows: List[dict]):
    """Derive (current_week, total_weeks, pop_start, pop_end). Anchored to the
    timesheet data so the demo is coherent even before the Fixtura seed alignment
    (task #1) makes the award dates line up with the timesheet dates."""
    period = _base_period(contract)
    pop_start = _d(period.get("pop_start"))
    pop_end = _d(period.get("pop_end"))
    total_weeks = _weeks_between(pop_start, pop_end) or 52

    weeks = sorted({r.get("week_ending") for r in rows if r.get("week_ending")})
    latest = _d(weeks[-1]) if weeks else None

    # Anchor "today" to the calendar only when the timesheets actually fall
    # inside this period's PoP window. Fixtura's seed dates the work in the
    # current option year while still charging the base CLIN numbers, so the
    # base PoP (which can be years earlier) won't contain those dates. Pinning
    # the clock to a long-past pop_end then reads as "week 52/52 — contract
    # over," which is useless. When the dates don't sit in the window, treat the
    # count of logged weeks as how far into execution we are instead.
    if pop_start and pop_end and latest and pop_start <= latest <= pop_end:
        current_week = (_weeks_between(pop_start, latest) or 0) + 1
    else:
        current_week = len(weeks)
    current_week = max(1, min(current_week, total_weeks))

    return {
        "current_week": current_week,
        "total_weeks": total_weeks,
        "pop_start": period.get("pop_start"),
        "pop_end": period.get("pop_end"),
        "latest_week": weeks[-1] if weeks else None,
    }


def compute(contract: dict, rows: List[dict]) -> dict:
    """Full Flight Deck payload for one contract + its synced timesheets."""
    header = contract.get("contract") or {}
    clk = _clock(contract, rows)
    cw, tw = clk["current_week"], clk["total_weeks"]

    # Only the active period's CLINs — never the whole award's option years.
    # See _active_clins for why (over-counting ceiling breaks every downstream
    # stat). Consistent with _clock, which runs the week clock off the same
    # base period.
    clins = _active_clins(contract)
    labor = [c for c in clins if c.get("is_labor")]
    nonlabor = [c for c in clins if not c.get("is_labor")]

    computed = [_compute_clin(c, rows, cw, tw) for c in labor]
    # Non-labor CLINs: no expense feature yet (#7), so spent is 0 / tracked-only.
    nl_cards = []
    for c in nonlabor:
        ceiling = float(c.get("ceiling") or 0)
        nl_cards.append(
            {
                "id": _clin_num(c),
                "code": f"CLIN {_clin_num(c)}",
                "name": c.get("title"),
                "is_labor": False,
                "ceiling": ceiling,
                "spent": 0.0,
                "pct": 0.0,
                "status": "tracked",
                "status_label": "Tracked",
                "rate_source": "n/a",
            }
        )

    active = [c for c in computed if c["status"] != "paused"] or computed
    worst = min(active, key=lambda c: c["exhaust_week"] or 1e9) if active else None

    labor_ceiling = sum(c["ceiling"] for c in computed)
    total_ceiling = labor_ceiling + sum(c["ceiling"] for c in nl_cards)
    total_spent = sum(c["spent"] for c in computed)
    total_weekly = sum(c["weekly"] for c in computed)

    tripwires = [
        {
            "code": c["code"],
            "name": c["name"],
            "pct": c["pct"],
            "exhaust_week": c["exhaust_week"],
            "runway_days": c["runway_days"],
            "weeks_early": round(tw - (c["exhaust_week"] or tw)),
        }
        for c in computed
        if c["status"] == "over"
    ]

    return {
        "contract": {
            "id": contract.get("id"),
            "piid": header.get("piid") or contract.get("piid"),
            "name": header.get("contractor") or header.get("piid"),
            "agency": header.get("agency"),
            "vehicle": header.get("contract_type"),
            "pop_start": clk["pop_start"],
            "pop_end": clk["pop_end"],
            "current_week": cw,
            "total_weeks": tw,
            "weeks_remaining": max(0, tw - cw),
        },
        "totals": {
            "ceiling": round(total_ceiling, 2),
            "spent": round(total_spent, 2),
            "pct": round(total_spent / total_ceiling, 4) if total_ceiling else 0.0,
            "weekly": round(total_weekly, 2),
            "labor_count": len(computed),
        },
        "hero": (
            {
                "days": worst["runway_days"],
                "clin": worst["code"],
                "status": worst["status"],
            }
            if worst
            else None
        ),
        "clins": computed + nl_cards,
        "tripwires": tripwires,
        "all_clear": len(tripwires) == 0,
        "sync": {
            "rows": len(rows),
            "people": len({r.get("employee_id") for r in rows if r.get("employee_id")}),
            "weeks": len({r.get("week_ending") for r in rows if r.get("week_ending")}),
            "latest_week": clk["latest_week"],
        },
    }


def portfolio(contracts_with_rows: List[tuple]) -> dict:
    """Cross-contract KPI aggregate + one summary card per contract.
    `contracts_with_rows` is a list of (contract_dict, timesheet_rows)."""
    cards = []
    for contract, rows in contracts_with_rows:
        b = compute(contract, rows)
        c, t = b["contract"], b["totals"]
        labor = [x for x in b["clins"] if x.get("is_labor")]
        if any(x["status"] == "over" for x in labor):
            overall = "over"
        elif any(x["status"] == "watch" for x in labor):
            overall = "watch"
        else:
            overall = "ok"
        on_pace = sum(1 for x in labor if x["status"] == "ok")
        cards.append(
            {
                "id": c["id"],
                "piid": c["piid"],
                "name": c["name"],
                "agency": c["agency"],
                "ceiling": t["ceiling"],
                "spent": t["spent"],
                "pct": t["pct"],
                "weekly": t["weekly"],
                "runway_days": b["hero"]["days"] if b["hero"] else None,
                "status": overall,
                "status_label": _pill(overall),
                "on_pace": on_pace,
                "lines": len(labor),
            }
        )

    at_risk = sum(1 for c in cards if c["status"] != "ok")
    return {
        "count": len(cards),
        "value": round(sum(c["ceiling"] for c in cards), 2),
        "weekly": round(sum(c["weekly"] for c in cards), 2),
        "at_risk": at_risk,
        "contracts": cards,
    }
