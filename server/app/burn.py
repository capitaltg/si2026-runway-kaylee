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
# Under-burn tripwire: at the current pace the CLIN won't consume its budget
# until this fraction of the PoP *past* the finish line — a large unspent
# balance / slipping delivery signal, symmetric to the over-ceiling tripwire.
_UNDER_SLACK_FRAC = 0.15
# Funding-pace tripwire (#22). An incrementally-funded CLIN whose funded slice
# runs out before the PoP ends is only a real alarm when funding is falling
# *behind* the burn — incremental funding in tranches is routine. Funding is
# treated as keeping pace when the obligated fraction stays within this slack of
# the elapsed-clock fraction; below that, obligations are genuinely lagging spend
# and the tripwire stays red. Honest read from data we already have (obligated,
# ceiling, PoP clock) — no obligation time-series required.
_FUND_LAG_SLACK = 0.15
# How close the funded money has to be to running out before a CLIN says anything
# about funding at all. Outrunning the current funded slice is the *definition* of
# incremental funding, so a shortfall projected months away is not news — before
# this gate every partially-obligated CLIN carried an amber "Funding due" for its
# whole life, including ones landing dead on their ceiling. Inside this horizon
# it's a mod that has to be moving; outside it the CLIN is judged on its ceiling
# projection instead.
#
# 60 days is FAR 52.232-22(c)'s own lookahead: under Limitation of Funds the
# contractor must notify the CO in writing when the costs it expects to incur *in
# the next 60 days*, added to costs already incurred, will exceed 75% of the funds
# allotted. So this is the window in which a PM is already obliged to be doing
# something about funding — flagging earlier is noise, flagging later is late.
# #24 adds the 75%-of-funded half of the same clause as its own state.
_FUNDING_DUE_DAYS = 60
# Minimum weeks elapsed in the active period before an obligation *rate* can be
# read off the mod history. Below this, a single early tranche divided by one or
# two weeks produces an enormous weekly figure that says nothing about funding
# behaviour — the caller falls back to the funded-vs-elapsed proxy instead.
_PACE_MIN_WEEKS = 4


def _d(s: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except (ValueError, TypeError):
        return None


def _weeks_between(a: Optional[date], b: Optional[date]) -> Optional[int]:
    if not a or not b:
        return None
    return round((b - a).days / 7)


def _period_window(p: dict):
    return _d(p.get("pop_start")), _d(p.get("pop_end"))


def _exercised(contract: dict) -> List[dict]:
    """Exercised periods in PoP order. An un-exercised option is not in play: no
    obligated dollars, no charges, and its ceiling is not yet spendable."""
    periods = [p for p in (contract.get("periods") or []) if p.get("exercised")]
    return sorted(periods, key=lambda p: (p.get("pop_start") or ""))


def _anchor_date(rows: List[dict]) -> date:
    """The "now" the period clock is read against: the latest synced timesheet
    week if there is one, else today. Anchoring to the data keeps a demo coherent
    when seeded award dates don't line up with seeded timesheet dates."""
    weeks = sorted({r.get("week_ending") for r in rows if r.get("week_ending")})
    return (_d(weeks[-1]) if weeks else None) or date.today()


def _active_period(contract: dict, rows: List[dict]) -> dict:
    """The period the burn clock runs against: the *current* exercised period —
    the one the anchor date actually falls inside.

    This used to return the *first* exercised period, which is the root of the
    funding-window-vs-PoP mismatch. A contract's obligated total is cumulative
    contract-to-date and spans every period exercised so far; a period of
    performance is one period. Anchoring to the first exercised period pinned
    every stat to a window that may have closed years ago, and made the
    cumulative obligation look bigger than the single-period ceiling it was being
    compared against — which silently switched the whole incremental-funding read
    off (see `compute`).

    Selection, in order:
      * the exercised period whose window contains the anchor date
      * past every exercised window  → the last one (overrun / close-out)
      * before every window          → the first one (not started yet)
      * in a gap between periods     → the last one that has already started
      * no dated periods / none exercised → the first period, else {}
    """
    periods = _exercised(contract) or (contract.get("periods") or [])
    if not periods:
        return {}
    dated = [p for p in periods if _d(p.get("pop_start"))]
    if not dated:
        return periods[0]

    anchor = _anchor_date(rows)
    for p in dated:
        start, end = _period_window(p)
        if start <= anchor and (end is None or anchor <= end):
            return p
    started = [p for p in dated if _d(p.get("pop_start")) <= anchor]
    return started[-1] if started else dated[0]


def _period_clins(contract: dict, period: dict) -> List[dict]:
    """The CLINs belonging to one period.

    This is the guard against counting money that isn't in play yet. An award
    lists every option year's CLINs up front, but only the current period has
    obligated dollars and timesheet charges against it. Pricing all of them
    inflates the ceiling and wrecks the burn %, runway and tripwire math.

    Matches on the CLIN's `period` label. Degrades gracefully: if no CLIN carries
    a label there is nothing to filter on, so every CLIN is kept — and `compute`
    reports that fallback as `clin_scope: "all"` rather than letting an
    over-counted ceiling pass as a scoped one.
    """
    name = (period.get("name") or "").strip().lower()
    clins = contract.get("clins") or []
    labeled = [c for c in clins if (c.get("period") or "").strip()]
    if not name or not labeled:
        return clins
    return [c for c in clins if (c.get("period") or "").strip().lower() == name]


def _prior_consumed(contract: dict, period: dict) -> float:
    """Obligated dollars already consumed by the exercised periods that ran
    before this one.

    Obligation is cumulative contract-to-date; a period of performance is not.
    Once a prior period has been performed and closed out its funding is spent
    and is no longer available to the current period — the rest of the ceiling is
    what later periods draw against. Netting this out is what makes
    `total_obligated` comparable to a *period* ceiling at all.

    A closed prior period is treated as having consumed its own ceiling: that is
    what it was funded and performed to. Prior-period actuals aren't available to
    net out precisely (only the current period's timesheets are synced), so the
    period ceiling is the honest document-backed approximation — and it is
    reported in the payload as `prior_consumed` rather than folded in silently.
    """
    start = _d(period.get("pop_start"))
    if not start:
        return 0.0
    total = 0.0
    for p in _exercised(contract):
        if p is period:
            continue
        p_start = _d(p.get("pop_start"))
        if p_start and p_start < start:
            total += float(p.get("ceiling") or 0)
    return total


def _clin_num(clin: dict) -> str:
    return str(clin.get("clin") or "").strip()


def _slot(num: str) -> str:
    """A CLIN's "slot": its trailing three digits, ignoring subCLIN letters.

    Federal awards number option-year CLINs to mirror the base year — base 0001
    becomes 1001 in Option 1 and 2001 in Option 2 — so the slot is a line item's
    stable identity across periods. Timesheet feeds commonly keep charging the
    original base charge code for the life of the contract, so matching on the
    slot is what lets the *current* period's CLIN pick up its own charges.
    """
    digits = "".join(ch for ch in str(num or "") if ch.isdigit())
    return digits[-3:]


def _effective_window(period: dict, rows: List[dict]):
    """The date window to scope charges to — or an open window when the timesheet
    feed doesn't overlap the period at all.

    Scoping to the PoP is what keeps a multi-period contract honest, but applied
    blindly it also zeroes out the deliberate non-overlap fallback `_clock`
    relies on: a seeded award whose dates don't line up with the seeded timesheet
    dates (Fixtura alignment, task #1) would report $0 burn and "all clear"
    instead of a coherent demo. So the window is only enforced when at least one
    synced week actually falls inside it; otherwise the feed is treated as
    belonging to this period wholesale, matching how `_clock` falls back to
    "weeks of timesheets logged".
    """
    start, end = _period_window(period)
    if not start and not end:
        return (None, None), False
    for r in rows:
        wk = _d(r.get("week_ending"))
        if wk and not ((start and wk < start) or (end and wk > end)):
            return (start, end), True
    return (None, None), False


def _rows_for_clin(clin: dict, rows: List[dict], window=(None, None)) -> List[dict]:
    """Timesheet rows charged to this CLIN *within the active period's window*.

    Two scoping rules, both needed to keep a multi-period contract honest:
      * date — only weeks inside the active PoP count toward this period's burn.
        Without it, a prior period's charges inflate the current period's spend
        and its forward pace.
      * CLIN — exact charge_code, then subCLIN prefix ('0001AA' rolls up to
        '0001'), then the period slot (a '0001' charge rolls into Option 2's
        2001). Slots are unique inside a single period, so that last match can't
        collide across the CLINs being priced here.
    """
    num = _clin_num(clin)
    if not num:
        return []
    start, end = window
    scoped = []
    for r in rows:
        wk = _d(r.get("week_ending"))
        if wk and ((start and wk < start) or (end and wk > end)):
            continue
        scoped.append(r)

    def code(r):
        return (r.get("charge_code") or "").strip()

    exact = [r for r in scoped if code(r) == num]
    if exact:
        return exact
    prefix = [r for r in scoped if code(r).startswith(num)]
    if prefix:
        return prefix
    slot = _slot(num)
    return [r for r in scoped if slot and _slot(code(r)) == slot]


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


def _forward_band(exhaust: Optional[float], total_weeks: int) -> str:
    """Bands a projected exhaustion week against the finish line.

    Shared so the funded slice and the ceiling are judged on identical rules —
    when a funded-slice shortfall isn't actionable yet, the status is re-derived
    from the ceiling's projection through this same function.
    """
    if exhaust is None:
        return "ok"
    if exhaust < total_weeks - 1:
        return "over"
    if exhaust < total_weeks + 2:
        return "watch"
    if exhaust > total_weeks * (1 + _UNDER_SLACK_FRAC):
        return "under"
    return "ok"


def _funded_shortfall_status(
    runway_days: Optional[int],
    ceiling_exhaust: Optional[float],
    total_weeks: int,
    incrementally_funded: bool,
    ceiling_breached: bool,
    mod_in_progress: bool,
    funding_keeps_pace: bool,
) -> str:
    """The binding budget runs out before the finish line — how bad is that?

    Red unless this is routine incremental funding: the ceiling still holds and
    funding is either keeping pace or has a mod outstanding. In that case it only
    says anything about *funding* once the money is close to gone
    (`_FUNDING_DUE_DAYS`); until then the CLIN is judged on its ceiling projection,
    because that's the long-run truth for a CLIN that keeps getting funded.

    Deliberately not triggered by how far projected spend overruns the current
    funded slice. Outrunning the current slice is what incremental funding *is*:
    a CLIN 64% obligated at 40% elapsed projects to ~1.5x its funded slice while
    landing dead on its ceiling. Treating that as trouble put a permanent amber
    "Funding due" on ideally-executing contracts. Burn genuinely outpacing the
    obligations is caught by funding_keeps_pace, which lands here as red.
    """
    if (
        incrementally_funded
        and not ceiling_breached
        and (mod_in_progress or funding_keeps_pace)
    ):
        if runway_days is not None and runway_days <= _FUNDING_DUE_DAYS:
            return "funding"
        return _forward_band(ceiling_exhaust, total_weeks)
    return "over"


def _pill(status: str, ceiling_breached: bool = True) -> str:
    """Status → pill label. `over` names whichever limit is actually in jeopardy.

    A red `over` is reached two different ways, and one label can't cover both.
    When projected spend blows the real ceiling it's a ceiling problem. When the
    ceiling still holds it's the funded slice that ran short with funding lagging
    — calling that "Over ceiling" pointed at a limit the CLIN was nowhere near.
    A CLIN that isn't incrementally funded has budget == ceiling, so `over` always
    implies a breach there and it keeps the ceiling wording without asking.

    Defaults to the ceiling wording for callers with no funded-slice notion.
    """
    if status == "over":
        return "Over ceiling" if ceiling_breached else "Funds short"
    return {
        "watch": "Watch",
        "ok": "On pace",
        "under": "Under pace",
        "funding": "Funding due",
        "paused": "Paused",
        "unpriced": "Unpriced",
    }.get(status, "—")


def _compute_clin(
    clin: dict,
    rows: List[dict],
    current_week: int,
    total_weeks: int,
    funded: Optional[float] = None,
    mod_in_progress: bool = False,
    funding_keeps_pace_override: Optional[bool] = None,
    window=(None, None),
    past_pop: bool = False,
):
    """Per-CLIN spend, forward burn, runway and status — the heart of the engine.

    `funded` is the obligated/funded dollars backing this CLIN right now — the
    active period's share of the obligation, already net of what prior periods
    consumed (see `compute`). When it is set and below the CLIN ceiling the
    contract is incrementally funded, so the binding constraint is the funded
    money, not the full ceiling (FAR 52.232-22, Limitation of Funds): runway,
    status and the exhaust week are measured against `funded`. When it's None
    (or >= ceiling) everything is measured against the ceiling.

    `window` scopes the charges to the active PoP; `past_pop` says the anchor date
    is already beyond the finish line, in which case a forward projection has
    nothing left to project into and status is read off realized spend."""
    rate_for, blended, source = _rate_resolver(clin)
    clin_rows = _rows_for_clin(clin, rows, window)

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

    # Unpriced: rows were charged to this CLIN but none could be priced (no rate
    # table and no est_hours → blended None → every row skipped above). This is a
    # data-quality gap, NOT "no charges": the engine found spend it could not value,
    # so reading it as `paused` and letting it pass `all_clear` shows the most
    # reassuring state for a contract that could not be measured at all (#40). The
    # unmatched LCATs name what to fix, via the supplemental rate import.
    unpriced = bool(clin_rows) and spent == 0.0 and source == "none"

    # Forward weekly pace = mean weekly spend over the most recent PACE_WEEKS weeks
    # that actually have charges. Steadier than a single noisy week.
    recent_weeks = sorted(weekly_totals)[-_PACE_WEEKS:]
    weekly = (
        sum(weekly_totals[w] for w in recent_weeks) / len(recent_weeks)
        if recent_weeks
        else 0.0
    )

    ceiling = float(clin.get("ceiling") or 0)
    # The dollars this CLIN can actually spend before it stalls: the funded
    # amount when incrementally funded, otherwise the full ceiling. Runway is
    # measured against this; the ceiling is still reported for the % display.
    # Zero is a real funded amount, not "no data": an option can be exercised
    # before any money is obligated against it, and that is the tightest funding
    # state there is. The old `0 < funded` guard treated it as no-funding-info and
    # fell back to a full-ceiling runway, hiding exactly the case that matters.
    incrementally_funded = funded is not None and funded < ceiling
    budget = funded if incrementally_funded else ceiling
    remaining = budget - spent
    pct = (spent / ceiling) if ceiling else 0.0

    if weekly <= 0:
        weeks_left = _PAUSED_WEEKS_LEFT
        status = "unpriced" if unpriced else "paused"
        runway_days = None
    else:
        weeks_left = remaining / weekly
        # Runway floors at zero. Once spend is past the binding budget there is no
        # time left to report, and the overrun belongs in dollars (`remaining` and
        # `overspent`), not in negative days — this read "-98 days" on the hero
        # tile. `exhaust_week` keeps the true (already-past) crossing point.
        runway_days = max(0, round(weeks_left * 7))
    exhaust_week = current_week + weeks_left

    # Funding-pace context (#22). When the binding budget is the funded slice
    # (not the full ceiling), the slice running out early is routine incremental
    # funding — only a red alarm when funding is genuinely lagging burn.
    #   funded_frac vs elapsed_frac  → is obligation keeping pace with the clock?
    #   ceiling_breached             → does projected spend blow the *actual*
    #                                  ceiling (a real breach, not just a mod gap)?
    # Clamped for the proxy and for display: the clock itself is uncapped now (so
    # overrun is visible), but "% of the PoP elapsed" past 100% would make the
    # proxy demand more than full funding to read as keeping pace.
    elapsed_frac = min(1.0, current_week / total_weeks) if total_weeks else 0.0
    funded_frac = (funded / ceiling) if (funded is not None and ceiling) else 1.0
    # Funding keeps pace: prefer the real signal derived from ingested SF-30
    # obligation history (dollars landing vs. burned, computed contract-wide in
    # compute()); fall back to the funded-fraction-vs-elapsed-clock proxy when no
    # mod history has been ingested yet.
    if funding_keeps_pace_override is not None:
        funding_keeps_pace = funding_keeps_pace_override
        pace_source = "obligation_history"
    else:
        funding_keeps_pace = funded_frac >= elapsed_frac - _FUND_LAG_SLACK
        pace_source = "proxy"
    ceiling_exhaust = current_week + (ceiling - spent) / weekly if weekly > 0 else None
    ceiling_breached = ceiling_exhaust is not None and ceiling_exhaust < total_weeks - 1

    if weekly <= 0:
        status = "unpriced" if unpriced else "paused"
    elif past_pop:
        # Past the finish line: there is no remaining PoP to project into, so a
        # forward exhaust week is meaningless. Read realized spend against the
        # binding budget instead, on the same bands the non-labor cards use — a
        # period that ended with a large unspent balance is an under-burn, one
        # that spent through its budget is a breach.
        pct_budget = (spent / budget) if budget else 0.0
        if pct_budget >= 1.0:
            status = "over"
        elif pct_budget >= 0.8:
            status = "watch"
        else:
            status = "under"
    else:
        band = _forward_band(exhaust_week, total_weeks)
        status = (
            band
            if band != "over"
            else _funded_shortfall_status(
                runway_days,
                ceiling_exhaust,
                total_weeks,
                incrementally_funded,
                ceiling_breached,
                mod_in_progress,
                funding_keeps_pace,
            )
        )

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
        # The binding budget the runway is measured against, and whether it's the
        # funded slice (incremental funding) rather than the full ceiling. The
        # Flight Deck chart draws the "funds run out" marker at `budget`.
        "budget": round(budget, 2),
        "funded": round(funded, 2) if funded is not None else None,
        "incrementally_funded": incrementally_funded,
        # Funding-pace read (#22): obligated vs elapsed-clock fraction, whether
        # funding is keeping pace, and whether a mod is flagged outstanding.
        "funded_frac": round(funded_frac, 4),
        "elapsed_frac": round(elapsed_frac, 4),
        "funding_keeps_pace": funding_keeps_pace,
        "funding_pace_source": pace_source,
        "mod_in_progress": bool(mod_in_progress),
        "spent": round(spent, 2),
        "pct": round(pct, 4),
        "weekly": round(weekly, 2),
        "remaining": round(remaining, 2),
        # Dollars already spent past the binding budget, when there are any. The
        # honest expression of a negative balance, since runway now floors at 0.
        "overspent": round(-remaining, 2) if remaining < 0 else 0.0,
        "weeks_left": (
            None if status in ("paused", "unpriced") else round(weeks_left, 2)
        ),
        "exhaust_week": (
            None if status in ("paused", "unpriced") else round(exhaust_week, 2)
        ),
        "runway_days": runway_days,
        "status": status,
        "status_label": _pill(status, ceiling_breached),
        # Which limit is in jeopardy, so the frontend can label a red `over` the
        # same way this does (and its simulator can too).
        "ceiling_breached": bool(ceiling_breached),
        "rate_source": source,
        "blended_rate": round(blended, 2) if blended else None,
        # Timesheet rows charged to this CLIN. For an `unpriced` CLIN this is the
        # count the engine found but could not value — the "N rows, $0 priced" story.
        "charged_rows": len(clin_rows),
        "unmatched_lcats": sorted(unmatched),
        "actuals": series,
    }


def _clock(period: dict, rows: List[dict]):
    """Derive the week clock for the *active* period: (current_week, total_weeks,
    pop_start, pop_end) plus whether the anchor is already past the finish line.
    Anchored to the timesheet data so the demo is coherent even before the Fixtura
    seed alignment (task #1) makes the award dates line up with the timesheet
    dates.

    `current_week` is no longer clamped to `total_weeks`. The clamp hid overrun
    entirely — a contract still charging after PoP end read as week 52 of 52 —
    zeroed `weeks_remaining` (which degenerated the under-burn projection) and
    pinned the elapsed fraction at 1.0, quietly breaking the funding-pace proxy.
    Overrun is now reported as `past_pop` / `weeks_overrun` instead.
    """
    pop_start = _d(period.get("pop_start"))
    pop_end = _d(period.get("pop_end"))
    total_weeks = _weeks_between(pop_start, pop_end) or 52

    weeks = sorted({r.get("week_ending") for r in rows if r.get("week_ending")})
    latest = _d(weeks[-1]) if weeks else None

    if pop_start and latest and latest >= pop_start:
        current_week = (_weeks_between(pop_start, latest) or 0) + 1
    else:
        # Dates don't overlap the PoP yet — treat "weeks of timesheets logged" as
        # how far into execution we are.
        current_week = len(weeks)
    current_week = max(1, current_week)

    return {
        "current_week": current_week,
        "total_weeks": total_weeks,
        "pop_start": period.get("pop_start"),
        "pop_end": period.get("pop_end"),
        "latest_week": weeks[-1] if weeks else None,
        "past_pop": current_week > total_weeks,
        "weeks_overrun": max(0, current_week - total_weeks),
    }


def _nl_status(
    spent: float,
    budget: float,
    ceiling: float,
    incrementally_funded: bool,
    past_pop: bool,
    funding_keeps_pace: bool,
    mod_in_progress: bool,
) -> str:
    """Status for a non-labor CLIN from its logged actuals. No timesheet pace to
    project, so it's a realized spent-vs-*budget* read, where `budget` is the
    funded slice when the CLIN is incrementally funded, else the full ceiling
    (#41). This is the same binding-dollar denominator the labor path uses — a
    travel/ODC CLIN past its obligated funding is a real Limitation of Funds
    problem even while it sits under the ceiling.

    Nothing logged reads `tracked`. A realized breach of the actual ceiling is
    always red `over`. Between the funded slice and the ceiling, mirror the labor
    softening (#22): still-live with funding keeping pace (or a mod flagged) reads
    amber `funding` — the next obligation tranche just isn't posted yet — while a
    finished period, or funding genuinely lagging with no mod, stays red `over`.
    Below the binding budget, the same 80% `watch` band the labor cards use, on
    the binding denominator."""
    if spent <= 0:
        return "tracked"
    if ceiling and spent >= ceiling:
        return "over"
    if incrementally_funded and budget and spent >= budget:
        if not past_pop and (mod_in_progress or funding_keeps_pace):
            return "funding"
        return "over"
    pct = (spent / budget) if budget else 0.0
    if pct >= 0.8:
        return "watch"
    return "ok"


def _funding_pace_from_history(
    contract: dict, period: dict, current_week: int, burn_weekly: float
):
    """Funding pace from ingested SF-30 obligation history (#18): are obligated
    dollars landing at least as fast as they're being burned? If so, a funded
    slice draining before PoP end is just the next tranche not yet posted —
    routine, not a shortfall.

    Scoped to the active period, and to per-action *increments* rather than the
    running cumulative. The previous version divided the latest cumulative
    obligated by the elapsed week count, which averaged the week-zero award lump
    over the clock: for one unchanged history it returned "keeping pace" early in
    a period and "lagging" later, with no new money required. That verdict tracked
    the calendar rather than funding behaviour, and it compared a contract-to-date
    figure against a period clock and a trailing-4-week burn rate — three
    different windows.

    Now: dollars obligated *inside this period's window*, over the weeks elapsed
    *in this period*, against the same forward burn rate the runway is built on.

    Returns (keeps_pace, obligation_weekly), or (None, None) when there isn't
    enough in-period history to judge — no dated action inside the window, or
    fewer than `_PACE_MIN_WEEKS` elapsed so a single early tranche would set the
    rate. The caller then falls back to the funded-vs-elapsed proxy."""
    start, end = _period_window(period)
    pop_weeks = _weeks_between(start, end)
    elapsed = min(current_week, pop_weeks) if pop_weeks else current_week
    if elapsed < _PACE_MIN_WEEKS:
        return None, None

    in_period = []
    for h in contract.get("obligation_history") or []:
        d = _d(h.get("date"))
        if d is None or (start and d < start) or (end and d > end):
            continue
        in_period.append(h)
    if not in_period:
        return None, None

    # Prefer each action's stated increment; fall back to the cumulative delta
    # across the window when the docs only stated running totals.
    amounts = [float(h["amount"]) for h in in_period if h.get("amount") is not None]
    if amounts:
        obligated_in_period = sum(amounts)
    else:
        cums = sorted(
            float(h["cumulative_obligated"])
            for h in in_period
            if h.get("cumulative_obligated") is not None
        )
        if len(cums) < 2:
            return None, None
        obligated_in_period = cums[-1] - cums[0]

    obligation_weekly = obligated_in_period / elapsed
    return (obligation_weekly >= burn_weekly), round(obligation_weekly, 2)


def compute(
    contract: dict, rows: List[dict], expenses: Optional[List[dict]] = None
) -> dict:
    """Full Flight Deck payload for one contract + its synced timesheets and any
    logged non-labor actuals (expenses)."""
    header = contract.get("contract") or {}
    # The *current* exercised period, not the first one — see _active_period.
    period = _active_period(contract, rows)
    clk = _clock(period, rows)
    cw, tw = clk["current_week"], clk["total_weeks"]
    window, window_applied = _effective_window(period, rows)
    past_pop = clk["past_pop"]

    # Only the active period's CLINs — never the whole award's option years.
    # See _period_clins for why (over-counting ceiling breaks every downstream
    # stat). Consistent with _clock, which runs the week clock off the same period.
    clins = _period_clins(contract, period)
    labor = [c for c in clins if c.get("is_labor")]
    nonlabor = [c for c in clins if not c.get("is_labor")]
    clin_scope = (
        "period"
        if any((c.get("period") or "").strip() for c in contract.get("clins") or [])
        and (period.get("name") or "").strip()
        else "all"
    )

    # Funded-dollar allocation, in two steps.
    #
    # 1. How much obligated money is available to *this* period. Obligation is
    #    cumulative contract-to-date and spans every period exercised so far, so
    #    the raw `total_obligated` is not comparable to one period's ceiling:
    #    netting out what prior periods already consumed is what makes it so.
    #    Without this, a contract past its first period reads obligated > period
    #    ceiling and the entire incremental-funding path switches off — the
    #    funding tripwire becomes unreachable on exactly the contracts that need
    #    it. Capped at the period ceiling, and floored at zero (an option can be
    #    exercised before its funding lands, which is a real, reportable state).
    # 2. An award carries no per-CLIN funding split, so spread the period's funded
    #    dollars across its CLINs pro-rata by ceiling. That's what lets the engine
    #    warn when *funded* dollars run out early rather than the full ceiling —
    #    the incremental-funding case (FAR 52.232-22, Limitation of Funds).
    #
    # When nothing is obligated, or the obligation already covers this period's
    # whole ceiling, funded is None and every CLIN falls back to ceiling runway.
    active_ceiling = sum(float(c.get("ceiling") or 0) for c in clins)
    obligated = header.get("total_obligated")
    prior_consumed = _prior_consumed(contract, period)
    period_funded = None
    if obligated is not None and active_ceiling:
        available = max(0.0, float(obligated) - prior_consumed)
        if available < active_ceiling:
            period_funded = available
    funded_frac = (
        (period_funded / active_ceiling) if period_funded is not None else None
    )
    funded_for = lambda c: (
        funded_frac * float(c.get("ceiling") or 0) if funded_frac is not None else None
    )
    # Outstanding funding mod (a set flag, or a future SF-30 ingest, #18) softens
    # the funding tripwire to "request outstanding" rather than an alarm (#22).
    mod_in_progress = bool(header.get("mod_in_progress"))

    # First pass with the proxy, just to total the forward burn rate. If SF-30
    # mods have been ingested, re-derive funding pace from that real obligation
    # history (dollars landing vs. burned) and recompute the labor CLINs with it;
    # otherwise the proxy result stands (no extra work for award-only contracts).
    prelim = [
        _compute_clin(
            c,
            rows,
            cw,
            tw,
            funded=funded_for(c),
            mod_in_progress=mod_in_progress,
            window=window,
            past_pop=past_pop,
        )
        for c in labor
    ]
    burn_weekly = sum(c["weekly"] for c in prelim)
    pace_override, obligation_weekly = _funding_pace_from_history(
        contract, period, cw, burn_weekly
    )
    if pace_override is None:
        computed = prelim
    else:
        computed = [
            _compute_clin(
                c,
                rows,
                cw,
                tw,
                funded=funded_for(c),
                mod_in_progress=mod_in_progress,
                funding_keeps_pace_override=pace_override,
                window=window,
                past_pop=past_pop,
            )
            for c in labor
        ]
    # Non-labor CLINs are cost-reimbursable — their spend is the sum of manually
    # logged actuals (travel / ODC / materials / subs), not timesheet hours.
    exp_by_clin = {}
    exp_count = {}
    for e in expenses or []:
        k = str(e.get("clin") or "").strip()
        exp_by_clin[k] = exp_by_clin.get(k, 0.0) + float(e.get("amount") or 0)
        exp_count[k] = exp_count.get(k, 0) + 1
    # Non-labor CLINs carry the same funded/budget fields the labor cards do, so
    # they're measured against the binding budget (funded slice when incrementally
    # funded, else the ceiling) rather than the raw ceiling (#41). The funded slice
    # comes from the same pro-rata `funded_for` allocation labor uses; per-CLIN
    # real-obligation splits are #21. The funding-pace read is contract-level: the
    # SF-30 obligation-history override when present, else the funded-vs-elapsed
    # proxy — the same signal `_compute_clin` applies to labor.
    elapsed_frac = min(1.0, cw / tw) if tw else 0.0
    nl_cards = []
    for c in nonlabor:
        ceiling = float(c.get("ceiling") or 0)
        num = _clin_num(c)
        spent = exp_by_clin.get(num, 0.0)
        funded = funded_for(c)
        incrementally_funded = funded is not None and funded < ceiling
        budget = funded if incrementally_funded else ceiling
        funded_frac = (funded / ceiling) if (funded is not None and ceiling) else 1.0
        if pace_override is not None:
            funding_keeps_pace = pace_override
        else:
            funding_keeps_pace = funded_frac >= elapsed_frac - _FUND_LAG_SLACK
        status = _nl_status(
            spent,
            budget,
            ceiling,
            incrementally_funded,
            past_pop,
            funding_keeps_pace,
            mod_in_progress,
        )
        remaining = budget - spent
        nl_cards.append(
            {
                "id": num,
                "code": f"CLIN {num}",
                "name": c.get("title"),
                "is_labor": False,
                "ceiling": ceiling,
                # Binding budget the status is measured against, and whether it's
                # the funded slice rather than the full ceiling (#41).
                "funded": round(funded, 2) if funded is not None else None,
                "budget": round(budget, 2),
                "incrementally_funded": incrementally_funded,
                "spent": round(spent, 2),
                # `pct` stays ceiling-based for the display %; the two-denominator
                # reconciliation on the card is #39. Status uses the budget.
                "pct": round((spent / ceiling) if ceiling else 0.0, 4),
                "remaining": round(remaining, 2),
                "overspent": round(spent - budget, 2) if remaining < 0 else 0.0,
                "entries": exp_count.get(num, 0),
                "status": status,
                # No forward pace on a non-labor line, so the breach is realized:
                # spend is past the ceiling, not just past the funded slice.
                "status_label": (
                    "Tracked"
                    if status == "tracked"
                    else _pill(status, spent >= ceiling)
                ),
                "ceiling_breached": spent >= ceiling,
                "rate_source": "n/a",
                # No timesheet series → no forward pace. Realized read only; the
                # None runway fields let the tripwire lists (below) treat these
                # rows uniformly with labor without inventing a runway.
                "exhaust_week": None,
                "runway_days": None,
                "weeks_left": None,
                "funded_frac": round(funded_frac, 4),
                "elapsed_frac": round(elapsed_frac, 4),
                "funding_keeps_pace": funding_keeps_pace,
                "mod_in_progress": bool(mod_in_progress),
                "limited_by": "funding" if incrementally_funded else "ceiling",
            }
        )

    # An `unpriced` CLIN has no runway (its spend could not be valued), so it can't
    # be the worst-runway hero any more than a `paused` one can — exclude both.
    active = [
        c for c in computed if c["status"] not in ("paused", "unpriced")
    ] or computed
    worst = min(active, key=lambda c: c["exhaust_week"] or 1e9) if active else None

    labor_ceiling = sum(c["ceiling"] for c in computed)
    total_ceiling = labor_ceiling + sum(c["ceiling"] for c in nl_cards)
    # Both feeds roll into burn: labor hours × rate, plus logged non-labor actuals.
    total_spent = sum(c["spent"] for c in computed) + sum(c["spent"] for c in nl_cards)
    total_weekly = sum(c["weekly"] for c in computed)

    tripwires = [
        {
            "code": c["code"],
            "name": c["name"],
            "pct": c["pct"],
            "exhaust_week": c["exhaust_week"],
            "runway_days": c["runway_days"],
            "weeks_early": round(tw - (c["exhaust_week"] or tw)),
            # What the CLIN runs out of first: its funded dollars (incremental
            # funding) or the full ceiling. Drives whether the UI says "funding
            # runs out" vs "blows the ceiling".
            "limited_by": "funding" if c["incrementally_funded"] else "ceiling",
            "funded": c["funded"],
            "budget": c["budget"],
        }
        # Non-labor CLINs over their binding budget are Limitation of Funds
        # problems too (#41), so they roll into the red list alongside labor.
        # Their `exhaust_week` is None (realized read, no forward pace).
        for c in computed + nl_cards
        if c["status"] == "over"
    ]

    # Under-burn: too slow to land the budget by PoP end. Projected end-of-PoP
    # spend uses the same forward weekly pace the runway is built on, so the
    # unspent figure is real, not invented.
    weeks_remaining = max(0, tw - cw)
    underburn = [
        {
            "code": c["code"],
            "name": c["name"],
            "pct": c["pct"],
            "exhaust_week": c["exhaust_week"],
            "weeks_slack": round((c["exhaust_week"] or tw) - tw),
            "budget": c["budget"],
            "spent": c["spent"],
            "projected_unspent": round(
                max(0.0, c["budget"] - (c["spent"] + c["weekly"] * weeks_remaining)), 2
            ),
            "limited_by": "funding" if c["incrementally_funded"] else "ceiling",
        }
        for c in computed
        if c["status"] == "under"
    ]

    # Funding-pace watch (#22): the funded slice runs out before PoP end, but the
    # ceiling holds and funding is keeping pace with the clock (or a mod is
    # flagged). Amber, not red — routine incremental funding awaiting its next
    # obligation, deliberately kept distinct from a real over-ceiling breach so
    # the red tripwire keeps its signal.
    funding = [
        {
            "code": c["code"],
            "name": c["name"],
            "pct": c["pct"],
            "exhaust_week": c["exhaust_week"],
            "weeks_early": round(tw - (c["exhaust_week"] or tw)),
            "runway_days": c["runway_days"],
            "funded": c["funded"],
            "budget": c["budget"],
            "funded_frac": c["funded_frac"],
            "elapsed_frac": c["elapsed_frac"],
            "mod_in_progress": c["mod_in_progress"],
        }
        # Non-labor CLINs share the amber funding softening (#41), so a travel/ODC
        # CLIN awaiting its next obligation lands in the same "request outstanding"
        # list as labor rather than reading All clear.
        for c in computed + nl_cards
        if c["status"] == "funding"
    ]

    # Data-quality gaps (#40): CLINs with charged rows the engine could not price
    # (no rate table, no est_hours). These must not read as "All clear" — the
    # distinction is "we found no spend" vs "we could not price the spend we found."
    # Each names the unmatched LCATs so the fix (supplemental rate import,
    # POST /api/contracts/{id}/rates) is one click away.
    data_quality = [
        {
            "code": c["code"],
            "name": c["name"],
            "charged_rows": c["charged_rows"],
            "unmatched_lcats": c["unmatched_lcats"],
        }
        for c in computed
        if c["status"] == "unpriced"
    ]

    return {
        "contract": {
            "id": contract.get("id"),
            "piid": header.get("piid") or contract.get("piid"),
            # A user-chosen callsign (e.g. "FALCON") wins the display name; the
            # legal contractor is the fallback. `nickname` is echoed raw so the UI
            # can tell a custom name from the legal one.
            "name": contract.get("nickname")
            or header.get("contractor")
            or header.get("piid"),
            "nickname": contract.get("nickname"),
            "legal_name": header.get("contractor"),
            "agency": header.get("agency"),
            # Addressees for generated correspondence (funding letters, etc.).
            "contracting_officer": header.get("contracting_officer"),
            "cor": header.get("cor"),
            "vehicle": header.get("contract_type"),
            "pop_start": clk["pop_start"],
            "pop_end": clk["pop_end"],
            "current_week": cw,
            "total_weeks": tw,
            "weeks_remaining": max(0, tw - cw),
            # The active period, and the funding arithmetic that scopes a
            # contract-to-date obligation down to it. Reported rather than
            # implied so the numbers on screen can be reconciled to the award:
            #   period_funded = min(period_ceiling, obligated - prior_consumed)
            "period": period.get("name"),
            "period_ceiling": round(active_ceiling, 2),
            "contract_ceiling": header.get("total_ceiling"),
            "obligated": obligated,
            "prior_consumed": round(prior_consumed, 2),
            "period_funded": (
                round(period_funded, 2) if period_funded is not None else None
            ),
            "incrementally_funded": period_funded is not None,
            # True when no CLIN carried a period label, so the CLIN set could not
            # be scoped and every period's ceiling is in these totals.
            "clin_scope": clin_scope,
            # False when the synced weeks don't overlap this PoP at all, so
            # charges could not be date-scoped to the period (see
            # _effective_window) — the burn figures are the whole feed.
            "pop_scoped": window_applied,
            "past_pop": past_pop,
            "weeks_overrun": clk["weeks_overrun"],
            # How funding pace was judged: from ingested SF-30 obligation history
            # (dollars landing vs. burned) or the funded-vs-elapsed proxy.
            "funding_pace_source": (
                "obligation_history" if pace_override is not None else "proxy"
            ),
            "obligation_weekly": obligation_weekly,
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
                "limited_by": (
                    "funding" if worst["incrementally_funded"] else "ceiling"
                ),
            }
            if worst
            else None
        ),
        "clins": computed + nl_cards,
        "tripwires": tripwires,
        "underburn": underburn,
        "funding": funding,
        "data_quality": data_quality,
        # A contract the engine could not fully price is not "all clear" — an
        # unpriced CLIN gates it just like a tripwire (#40).
        "all_clear": (
            len(tripwires) == 0
            and len(underburn) == 0
            and len(funding) == 0
            and len(data_quality) == 0
        ),
        "sync": {
            "rows": len(rows),
            "people": len({r.get("employee_id") for r in rows if r.get("employee_id")}),
            "weeks": len({r.get("week_ending") for r in rows if r.get("week_ending")}),
            "latest_week": clk["latest_week"],
        },
    }


def portfolio(contracts_with_rows: List[tuple]) -> dict:
    """Cross-contract KPI aggregate + one summary card per contract.
    `contracts_with_rows` is a list of
    (contract_dict, timesheet_rows, expense_rows)."""
    cards = []
    for contract, rows, expenses in contracts_with_rows:
        b = compute(contract, rows, expenses)
        c, t = b["contract"], b["totals"]
        labor = [x for x in b["clins"] if x.get("is_labor")]
        # Overall health watches every CLIN — a non-labor CLIN over its ceiling is
        # just as much a breach as a labor one.
        if any(x["status"] == "over" for x in b["clins"]):
            overall = "over"
        elif any(x["status"] == "unpriced" for x in b["clins"]):
            # A CLIN the engine could not price means the portfolio read can't be
            # trusted for this contract — surface it rather than showing green (#40).
            overall = "unpriced"
        elif any(x["status"] in ("watch", "funding") for x in b["clins"]):
            # Funding-due (#22) rolls up amber alongside watch — not a breach, but
            # not all-clear either; the contract needs its next funding action.
            overall = "watch"
        elif any(x["status"] == "under" for x in b["clins"]):
            overall = "under"
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
                # Count of CLINs the engine could not price (#40) — lets the card
                # badge a data-quality gap instead of implying a clean read.
                "data_quality": len(b["data_quality"]),
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
