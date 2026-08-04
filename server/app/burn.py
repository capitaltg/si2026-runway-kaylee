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
a rate line, so nothing is silently invented. The matching itself lives in
`lcat.py` (#64), which also says *why* a miss missed — the CLIN has no rate table
at all, the LCAT is priced on a different CLIN, or the line is genuinely absent —
because those three need three different fixes and used to render identically.

Each CLIN also reports the pricing policy resolved from its contract type
(`pricing.py`, #76). The engine does not branch on it yet — that's #79 — so it is
carried on the payload and nothing here reads it to produce a dollar figure.
"""

from datetime import date, timedelta
from typing import List, Optional

from . import lcat as lcat_match
from . import pricing, rates

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


def _rate_resolver(clin: dict, index=None, aliases=None):
    """Return (resolve, blended, source_label). `resolve(lcat)` yields an
    `lcat.Resolution`: the $/hr the hour bills at, whether a real rate line backed
    it, and when it didn't, which of the three causes applies (#64).

    Thin wrapper over `lcat.resolver` so both callers — `_compute_clin` here and
    the allocation matrix — resolve rates through one code path and can never
    disagree about a flag. `index` (every rate line in the active period, from
    `lcat.build_index`) and `aliases` (the user's confirmed mappings) are optional:
    without them resolution still works, it just can't distinguish
    "priced on another CLIN" from "not priced anywhere".
    """
    return lcat_match.resolver(clin, index=index, aliases=aliases)


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


def _funds_exceeded(
    spent: float, budget: float, ceiling: float, incrementally_funded: bool
) -> bool:
    """Realized: spend has *already* passed the obligated funding, ceiling intact.

    Distinct from every projection in this module — this has happened, in dollars
    (`overspent` carries the amount). Only meaningful for an incrementally funded
    CLIN: when budget == ceiling, passing it is a ceiling breach and says so.
    Spend past the actual ceiling is likewise a ceiling story, so it yields here.
    """
    if not incrementally_funded or not budget or spent < budget:
        return False
    return not (ceiling and spent >= ceiling)


def _funded_shortfall_status(
    runway_days: Optional[int],
    ceiling_exhaust: Optional[float],
    total_weeks: int,
    incrementally_funded: bool,
    ceiling_breached: bool,
    mod_in_progress: bool,
    funding_keeps_pace: bool,
    funds_exceeded: bool = False,
) -> str:
    """The binding budget runs out before the finish line — how bad is that?

    Red unless this is routine incremental funding: the ceiling still holds and
    funding is either keeping pace or has a mod outstanding. In that case it only
    says anything about *funding* once the money is close to gone
    (`_FUNDING_DUE_DAYS`); until then the CLIN is judged on its ceiling projection,
    because that's the long-run truth for a CLIN that keeps getting funded.

    The softening is forward-looking only. Once spend is already past the allotted
    funding (`funds_exceeded`) there is nothing left to warn about: FAR 52.232-22's
    60-day notice under (c) is a duty owed *before* the money runs out, and past it
    (d)/(f) apply — the Government isn't obliged to reimburse and the contractor
    isn't obliged to continue. That cost is at risk today, so it stays red however
    well funding is tracking. It was previously amber "Funding due", identical to a
    CLIN with two months of runway and nothing overspent.

    Deliberately not triggered by how far *projected* spend overruns the current
    funded slice. Outrunning the current slice is what incremental funding *is*:
    a CLIN 64% obligated at 40% elapsed projects to ~1.5x its funded slice while
    landing dead on its ceiling. Treating that as trouble put a permanent amber
    "Funding due" on ideally-executing contracts. Burn genuinely outpacing the
    obligations is caught by funding_keeps_pace, which lands here as red.
    """
    if funds_exceeded:
        return "over"
    if (
        incrementally_funded
        and not ceiling_breached
        and (mod_in_progress or funding_keeps_pace)
    ):
        if runway_days is not None and runway_days <= _FUNDING_DUE_DAYS:
            return "funding"
        # Re-band on the ceiling, but never as an under-burn. Reaching this function
        # means the funded slice runs dry before PoP end, so "spend faster" is advice
        # the CLIN cannot take — it runs out of money first. The ceiling projection
        # is the right instrument for how much *scope* trouble there is, not for
        # whether to staff up, and the two disagree here by construction: the
        # under-burn card is built from the funded slice, so it rendered "projected to
        # under-spend its funded $2.7M by $0.0M ... ~-5 weeks after the PoP ends" —
        # self-contradictory numbers under a label taken from the other denominator.
        band = _forward_band(ceiling_exhaust, total_weeks)
        return "ok" if band == "under" else band
    return "over"


def _pill(
    status: str, ceiling_breached: bool = True, funds_exceeded: bool = False
) -> str:
    """Status → pill label. `over` names whichever limit is actually in jeopardy.

    A red `over` is reached three different ways, and one label can't cover them.
    When projected spend blows the real ceiling it's a ceiling problem. When the
    ceiling still holds it's the funded slice that will run short with funding
    lagging — calling that "Over ceiling" pointed at a limit the CLIN was nowhere
    near. And when the funding is already spent through, both of those are still
    forecasts while this one is a fact, so it gets the past tense.

    Precedence is realized-over-forecast: `funds_exceeded` wins even against a
    projected ceiling breach, because a CLIN can be past its obligated funding
    today *and* headed for the ceiling later, and only one of those has happened.
    A *realized* ceiling breach is the worse fact and does outrank it —
    `_funds_exceeded` returns False in that case, so the order is settled there.
    A CLIN that isn't incrementally funded has budget == ceiling, so `over` always
    implies a breach there and it keeps the ceiling wording without asking.

    Defaults to the ceiling wording for callers with no funded-slice notion.
    """
    if status == "over":
        if funds_exceeded:
            return "Funds exceeded"
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
    anchor: Optional[date] = None,
    policy: Optional[pricing.PricingPolicy] = None,
    rate_index=None,
    aliases=None,
    cost_model: Optional[rates.CostModel] = None,
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
    nothing left to project into and status is read off realized spend.

    `anchor` is the "now" the week clock is read against (`_anchor_date`) — the
    calendar date `current_week` corresponds to. It's what turns the week-indexed
    projection into the dated hard-stop forecast (#23).

    `policy` is the pricing policy for this CLIN (#76), passed down from `compute`
    because resolving it needs the contract header and this function only sees the
    CLIN. Nothing below branches on it: every figure here is computed exactly as it
    was before the policy existed, and the policy is reported on the payload only.
    #79 is what makes the arithmetic ask it questions. Defaulted rather than
    required so a caller holding only a CLIN still gets its `CLIN.type` read.

    `rate_index` and `aliases` come from `compute` for the same reason `policy`
    does — classifying a rate miss needs the *other* CLINs' rate lines and the
    contract's confirmed LCAT mappings, and this function only sees one CLIN (#64).
    Both default to empty, in which case resolution behaves exactly as it did: an
    unmatched LCAT is still reported, just without naming which of the three causes
    it is."""
    policy = policy or pricing.policy_for(clin, None)
    resolve, blended, source = _rate_resolver(clin, rate_index, aliases)
    clin_rows = _rows_for_clin(clin, rows, window)
    # The cost side (#77). Defaults to an empty model, which is Level 1: cost falls
    # back to the billing rate and is flagged as such. Nothing below reads `cost` to
    # produce a status, a runway or a tripwire — `spent` still means billings and
    # every figure on this card is unchanged. #79 is where the engine starts choosing
    # between the two.
    cost_model = cost_model or rates.CostModel()

    spent = 0.0
    cost = 0.0
    # Hours per cost-rate source, so a CLIN can report which tier actually priced it
    # rather than implying one uniform basis. `cost_known` is False the moment any
    # hour fell back to a billing rate.
    cost_hours = {}
    cost_by_lcat = {}  # lcat -> CostResolution, for the rate-variance reconciliation
    unmatched = set()
    # Unmatched LCATs, classified and weighted by the hours riding on them (#64).
    # Keyed by the LCAT as the timesheet spells it: one row per distinct string, not
    # per person, because the fix is per string.
    issues = {}  # lcat -> [Resolution, hours]
    aliased = {}  # lcat -> the confirmed mapping that priced it
    weekly_totals = {}  # week_ending -> $ that week
    for r in clin_rows:
        hours = float(r.get("total_hours") or 0)
        name = r.get("labor_category")
        res = resolve(name)
        label = (name or "").strip()
        if not res.matched and (label or res.rate is None):
            # A blank LCAT is only worth reporting when it also cost us the price:
            # with a blended rate behind it the row still values correctly, and
            # flagging it would put a "?" in front of the user with no fix attached.
            # That split is the pre-#64 behaviour, kept deliberately.
            key = label or "?"
            unmatched.add(key)
            entry = issues.setdefault(key, [res, 0.0])
            entry[1] += hours
        elif res.matched and res.via == lcat_match.VIA_ALIAS and res.line:
            aliased[label] = res.line
        if res.rate is None:
            continue
        amt = hours * res.rate
        spent += amt
        wk = r.get("week_ending") or ""
        weekly_totals[wk] = weekly_totals.get(wk, 0.0) + amt

        # What the same hour cost us, down the fallback ladder (#77). Accumulated
        # alongside billings, never mixed into them.
        cr = cost_model.cost_for(label or None, res.rate, r.get("employee_id"))
        if cr.rate is not None:
            cost += hours * cr.rate
        cost_hours[cr.source] = cost_hours.get(cr.source, 0.0) + hours
        if label and cr.known and label not in cost_by_lcat:
            cost_by_lcat[label] = (cr, res.rate)

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
    # Realized, not projected: the allotted funding is already spent through. Both
    # branches below stay red on it and the pill says so in the past tense.
    funds_exceeded = _funds_exceeded(spent, budget, ceiling, incrementally_funded)

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
                funds_exceeded,
            )
        )

    # Hard-stop forecast (#23): the calendar date charging on this CLIN gets
    # blocked — when cumulative spend reaches the *binding* budget at the current
    # pace. This is the date the accounting system's own hard stop (Costpoint /
    # Unanet, which owns the charge codes) is set against; Runway is the
    # early-warning layer upstream of it and never enforces anything.
    #
    # Derived from `anchor + round(weeks_left * 7)` days, which is `runway_days`
    # measured from the same "now" the week clock uses. Deriving it that way rather
    # than from `exhaust_week` is what keeps the date and the day count from ever
    # disagreeing on the same card — they're now the same arithmetic.
    #
    # `stop_days` is deliberately *not* floored at zero, so a CLIN whose funding is
    # already spent through keeps the true past date and can say when the money
    # actually ran out. That's the same split `runway_days` (floors at 0) and
    # `exhaust_week` (keeps the true crossing) already make. `stop_date_passed`
    # flags it so the UI can say "charging stops today" instead of naming a date
    # that has been and gone.
    #
    # Nulled only for `paused` / `unpriced`, exactly like `exhaust_week` and
    # `weeks_left` — there is no pace to project from, and `_PAUSED_WEEKS_LEFT`
    # would otherwise put the wall 19 years out.
    stop_date = None
    stop_reason = None
    stop_date_passed = False
    if status not in ("paused", "unpriced") and anchor is not None:
        stop_days = round(weeks_left * 7)
        stop_date = (anchor + timedelta(days=stop_days)).isoformat()
        # Which limit produces that date. No precedence rule is needed and none is
        # applied: the funded slice can never exceed the ceiling, so whenever a CLIN
        # is incrementally funded the funded money is what runs out first — which is
        # already exactly what `budget` is. So "the earlier of the two dates" and
        # `_pill`'s realized-over-forecast precedence agree here by construction.
        # Mirrors the `limited_by` on the tripwire lists so the copy can match.
        stop_reason = "funding" if incrementally_funded else "ceiling"
        # Zero counts as passed: the wall is today, and "stops today" is the honest
        # copy for both that and a date already behind us.
        stop_date_passed = stop_days <= 0

    # Cumulative actuals by week index (0-based over the weeks that have charges),
    # for the Flight Deck chart. Frontend maps these onto the SVG.
    # Derived-vs-negotiated reconciliation per LCAT (#77). Only LCATs whose cost we
    # actually derived can be reconciled; a fallback cost equals the billing rate, so
    # comparing them would always report zero variance and mean nothing.
    rate_variance = []
    for lc, (cr, negotiated) in sorted(cost_by_lcat.items()):
        v = rates.variance(cr.rate, negotiated)
        if v:
            rate_variance.append({"lcat": lc, **v})

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
        # The pricing policy governing this line (#76). Carried, not applied: the
        # numbers below are type-blind until #79. `known: false` means the type was
        # missing or unreadable and these figures are the legacy read — not a
        # statement about the award.
        "pricing_policy": policy.payload(),
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
        # Hard-stop forecast (#23): the date charging gets blocked, which limit
        # produces it, and whether that date is already today or behind us.
        "stop_date": stop_date,
        "stop_reason": stop_reason,
        "stop_date_passed": stop_date_passed,
        "status": status,
        "status_label": _pill(status, ceiling_breached, funds_exceeded),
        # Which limit is in jeopardy, so the frontend can label a red `over` the
        # same way this does (and its simulator can too). `ceiling_breached` is a
        # projection; `funds_exceeded` already happened, and outranks it.
        "ceiling_breached": bool(ceiling_breached),
        "funds_exceeded": bool(funds_exceeded),
        "rate_source": source,
        "blended_rate": round(blended, 2) if blended else None,
        # ---- the cost side (#77) -------------------------------------------------
        # `spent` above is billings: hours x the loaded rate the award prices. `cost`
        # is what those same hours consumed, burdened through the indirect pools. The
        # two are equal by construction when nobody has given us direct rates, which
        # is precisely what `cost_known: false` means — do NOT read margin off them
        # in that state (see rates.py for why that's a refusal and not a gap).
        "cost": round(cost, 2),
        "cost_known": bool(cost_hours) and rates.SOURCE_NEGOTIATED not in cost_hours,
        # Which tier priced the most hours on this CLIN: `employee_direct` (L3),
        # `lcat_direct` (L2), `negotiated_fallback` (L1) or `none`.
        "cost_rate_source": (
            max(cost_hours, key=cost_hours.get) if cost_hours else rates.SOURCE_NONE
        ),
        # Every tier that priced any hour here, with the hours behind it — a CLIN
        # that is 90% category-costed and 10% fallback is a real and common state,
        # and one dominant label would hide it.
        "cost_rate_mix": [
            {"source": s, "hours": round(h, 1)}
            for s, h in sorted(cost_hours.items(), key=lambda kv: -kv[1])
        ],
        # Reconciliation, per LCAT: the rate our buildup derives vs the one the award
        # schedule prints. Both numbers, the gap, and no verdict — a negotiated rate
        # that disagrees with the buildup is routine (prior-year indirects, a
        # discount to win), and picking one silently is how this loses an
        # accountant's trust. Empty until direct rates exist to derive from.
        #
        # Fee is not yet subtracted: #76 carries no fee *rate* (that's #80), so the
        # gap on a fee-bearing type still includes the fee. `fee_rate: 0` on each row
        # says so rather than letting the delta read as pure variance.
        "rate_variance": rate_variance,
        # Cause A as a CLIN-level fact (#64): this line item has no usable rate
        # table, so *every* LCAT charged to it prices at the blended rate. One
        # missing continuation sheet, one statement — the UI reads this instead of
        # painting a red cell per person for the same document.
        "rate_table_missing": source != "rate_table",
        # Timesheet rows charged to this CLIN. For an `unpriced` CLIN this is the
        # count the engine found but could not value — the "N rows, $0 priced" story.
        "charged_rows": len(clin_rows),
        # Kept as-is (a sorted list of LCAT strings) because the Flight Deck's
        # data-quality banner and the allocation cards read it. `lcat_issues` is the
        # same set with the diagnosis attached.
        "unmatched_lcats": sorted(unmatched),
        "lcat_issues": [
            lcat_match.issue_payload(name, res, hours)
            for name, (res, hours) in sorted(
                issues.items(), key=lambda kv: (-kv[1][1], kv[0])
            )
        ],
        # LCATs priced through a user-confirmed mapping rather than a printed rate
        # line. Reported so the numbers on this card are never traceable to a match
        # nobody agreed to — applying a mapping moves `spent`, and this is the
        # receipt for that.
        "aliased_lcats": [
            {"from": name, **line.payload()} for name, line in sorted(aliased.items())
        ],
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
) -> str:
    """Status for a non-labor CLIN from its logged actuals. No timesheet pace to
    project, so it's a realized spent-vs-*budget* read, where `budget` is the
    funded slice when the CLIN is incrementally funded, else the full ceiling
    (#41). This is the same binding-dollar denominator the labor path uses — a
    travel/ODC CLIN past its obligated funding is a real Limitation of Funds
    problem even while it sits under the ceiling.

    Nothing logged reads `tracked`. A realized breach of the actual ceiling is
    always red `over`. Passing the funded slice while under the ceiling is red too,
    and does *not* take the labor softening (#22): there is no forward projection
    here, so reaching the slice means the money is already spent — the at-risk-cost
    side of FAR 52.232-22, not a heads-up that a tranche is due. It used to read
    amber `funding` on a pace/mod check, which said "next tranche isn't posted yet"
    about dollars that were already out the door. `_pill` labels it "Funds exceeded"
    so it isn't confused with the ceiling. Below the binding budget, the same 80%
    `watch` band the labor cards use, on the binding denominator."""
    if spent <= 0:
        return "tracked"
    if ceiling and spent >= ceiling:
        return "over"
    if incrementally_funded and budget and spent >= budget:
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
    contract: dict,
    rows: List[dict],
    expenses: Optional[List[dict]] = None,
    cost_model: Optional[rates.CostModel] = None,
) -> dict:
    """Full Flight Deck payload for one contract + its synced timesheets and any
    logged non-labor actuals (expenses).

    `cost_model` is the indirect-cost buildup in force (#77), supplied by the caller
    because it comes from its own hand-maintained tables rather than the award. Omit
    it and the engine runs at Level 1: every billing figure is exactly what it was,
    cost falls back to the billing rate, and the payload flags that rather than
    presenting billings as cost. Nothing here branches on it to produce a status."""
    header = contract.get("contract") or {}
    # The *current* exercised period, not the first one — see _active_period.
    period = _active_period(contract, rows)
    clk = _clock(period, rows)
    cw, tw = clk["current_week"], clk["total_weeks"]
    window, window_applied = _effective_window(period, rows)
    past_pop = clk["past_pop"]
    # The calendar date `cw` corresponds to — the same "now" `_active_period` and
    # `_clock` are read against. Passed down so each CLIN's hard-stop forecast (#23)
    # is dated off the identical clock the week math uses.
    anchor = _anchor_date(rows)

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
    # 2. How much of that lands on each CLIN. An award that prints an Accounting
    #    and Appropriation Data / ACRN block funds each CLIN by name (#21), so
    #    those figures are used as-is — no split, no netting. Awards that print
    #    only a header total carry no per-CLIN attribution, so the period's funded
    #    dollars are spread across its CLINs pro-rata by ceiling instead. Either
    #    way this is what lets the engine warn when *funded* dollars run out early
    #    rather than the full ceiling — the incremental-funding case (FAR
    #    52.232-22, Limitation of Funds). Real obligation makes that warning
    #    accurate per line (labor funded near-full while travel/ODC starves)
    #    instead of a uniform blend.
    #
    # When nothing is obligated, or the obligation already covers this period's
    # whole ceiling, funded is None and every CLIN falls back to ceiling runway.
    active_ceiling = sum(float(c.get("ceiling") or 0) for c in clins)
    obligated = header.get("total_obligated")
    prior_consumed = _prior_consumed(contract, period)
    # Only when *every* active CLIN carries its own obligation is their sum
    # comparable to the period ceiling — that sum is then the period's funded
    # total, already period-scoped, so it needs none of the header netting below.
    # A partial set (mixed or legacy extractions) falls back to the header path;
    # `funded_for` still prefers whatever real per-CLIN figures it does have.
    attributed = [c for c in clins if c.get("obligated") is not None]
    if clins and len(attributed) == len(clins):
        real_funded = sum(float(c["obligated"]) for c in attributed)
        period_funded = real_funded if real_funded < active_ceiling else None
        funded_frac = None  # every CLIN has an exact figure; nothing to pro-rate
    else:
        period_funded = None
        if obligated is not None and active_ceiling:
            available = max(0.0, float(obligated) - prior_consumed)
            if available < active_ceiling:
                period_funded = available
        funded_frac = (
            (period_funded / active_ceiling) if period_funded is not None else None
        )

    def funded_for(c):
        """Funded dollars for one CLIN: the award's own obligation to it when
        present, else its pro-rata slice of the period's funded total."""
        if c.get("obligated") is not None:
            return float(c["obligated"])
        return (
            funded_frac * float(c.get("ceiling") or 0)
            if funded_frac is not None
            else None
        )

    # Outstanding funding mod (a set flag, or a future SF-30 ingest, #18) softens
    # the funding tripwire to "request outstanding" rather than an alarm (#22).
    mod_in_progress = bool(header.get("mod_in_progress"))

    # Pricing policy per CLIN (#76). Resolved here because this is the only scope
    # holding both the CLIN and the header, and per CLIN rather than per contract
    # because a mixed award — an FFP deliverable CLIN, a T&M surge CLIN, a cost
    # travel CLIN — is normal, and is the case `CLIN.type` exists for.
    def policy_of(c: dict) -> pricing.PricingPolicy:
        return pricing.policy_for(c, header)

    # Rate-line resolution context (#64), built once for the whole period and
    # shared by every CLIN. The index is what lets a CLIN say "this LCAT is priced
    # on CLIN 0002" instead of only "no rate line here"; the aliases are the
    # mappings a user confirmed, and the reason applying one changes `spent`.
    #
    # Scoped to the period's CLINs on purpose: a rate line on an un-exercised
    # option year prices nothing today, so offering it as the fix would point the
    # user at a CLIN with no money on it.
    rate_index = lcat_match.build_index(clins)
    aliases = lcat_match.parse_aliases(contract.get("lcat_aliases"))

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
            anchor=anchor,
            policy=policy_of(c),
            rate_index=rate_index,
            aliases=aliases,
            cost_model=cost_model,
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
                anchor=anchor,
                policy=policy_of(c),
                rate_index=rate_index,
                aliases=aliases,
                cost_model=cost_model,
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
    # comes from the same `funded_for` allocation labor uses — real per-CLIN
    # obligation when the award printed it (#21), else pro-rata by ceiling. Non-labor
    # is where that distinction bites hardest: a real ACRN block typically starves
    # travel/ODC to fund labor, which pro-rata hides. The funding-pace read is
    # contract-level: the
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
        # Named apart from the period-level `funded_frac` on purpose: `funded_for`
        # reads that one at call time, so reusing the name here would feed one
        # CLIN's ratio into the next CLIN's pro-rata slice.
        clin_funded_frac = (
            (funded / ceiling) if (funded is not None and ceiling) else 1.0
        )
        if pace_override is not None:
            funding_keeps_pace = pace_override
        else:
            funding_keeps_pace = clin_funded_frac >= elapsed_frac - _FUND_LAG_SLACK
        status = _nl_status(spent, budget, ceiling, incrementally_funded)
        nl_funds_exceeded = _funds_exceeded(
            spent, budget, ceiling, incrementally_funded
        )
        remaining = budget - spent
        nl_cards.append(
            {
                "id": num,
                "code": f"CLIN {num}",
                "name": c.get("title"),
                "is_labor": False,
                # Same policy read the labor cards carry (#76). A travel/ODC CLIN on
                # a cost contract is the one line item most likely to print its own
                # type, so it resolves per CLIN here too rather than inheriting the
                # header by assumption.
                "pricing_policy": policy_of(c).payload(),
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
                    else _pill(status, spent >= ceiling, nl_funds_exceeded)
                ),
                "ceiling_breached": spent >= ceiling,
                "funds_exceeded": nl_funds_exceeded,
                "rate_source": "n/a",
                # No timesheet series → no forward pace. Realized read only; the
                # None runway fields let the tripwire lists (below) treat these
                # rows uniformly with labor without inventing a runway.
                "exhaust_week": None,
                "runway_days": None,
                "weeks_left": None,
                # No pace → no dated hard stop either (#23). Present-but-null for
                # the same reason the runway fields are: the tripwire lists below
                # mix labor and non-labor rows and read these keys off both.
                # Non-labor gets a real date once #20 / #7 give it actuals.
                "stop_date": None,
                "stop_reason": None,
                "stop_date_passed": False,
                "funded_frac": round(clin_funded_frac, 4),
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
    # Total cost of the labor charged (#77). Non-labor CLINs are already actuals — a
    # logged travel dollar is a cost dollar — so they roll in unburdened. Reported
    # next to `spent`, never instead of it.
    total_cost = sum(c["cost"] for c in computed) + sum(c["spent"] for c in nl_cards)
    cost_model_out = cost_model or rates.CostModel()

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
            # The dated hard stop behind this tripwire (#23), so the banner can say
            # *when* rather than only how many weeks early. `limited_by` above is
            # already the same value `stop_reason` carries, so it isn't repeated.
            "stop_date": c["stop_date"],
            "stop_date_passed": c["stop_date_passed"],
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
            # When the funded money actually runs out (#23). An amber funding row is
            # by definition funding-limited, so `stop_reason` isn't repeated here.
            "stop_date": c["stop_date"],
            "stop_date_passed": c["stop_date_passed"],
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

    # Cause A, once per CLIN (#64). A labor CLIN with charges and no rate table
    # prices every hour at the blended rate: the figures are real contract
    # arithmetic and the CLIN is not "broken", but nothing on it is per-LCAT and the
    # user has no way to know why unless we say so. One row here replaces the flag
    # storm the ticket was filed over — N red cells for one missing PDF page.
    #
    # `unpriced` CLINs are excluded: they have no blended rate either, so they're
    # already the louder `data_quality` story above and don't need two banners.
    # Deliberately does NOT gate `all_clear`: a blended-priced CLIN is measured and
    # honest, and turning every award ingested without its continuation sheet into a
    # not-clear contract would be a new alarm, not a fix for an old one.
    rate_gaps = [
        {
            "id": c["id"],
            "code": c["code"],
            "name": c["name"],
            "charged_rows": c["charged_rows"],
            "blended_rate": c["blended_rate"],
            # The LCATs riding on the blended rate, so the prompt can be specific
            # about what is unpriced without listing every person.
            "lcats": c["unmatched_lcats"],
        }
        for c in computed
        if c["rate_table_missing"] and c["charged_rows"] and c["status"] != "unpriced"
    ]

    # Causes B and C, contract-wide (#64): unmatched LCATs on CLINs that *do* have a
    # rate table, which is the case a mapping can fix. Rolled up here so the Flight
    # Deck can show one "N labor categories need mapping" affordance rather than
    # making the user open the allocation matrix to discover them.
    lcat_gaps = [
        {
            "id": c["id"],
            "code": c["code"],
            "name": c["name"],
            "issues": c["lcat_issues"],
        }
        for c in computed
        if not c["rate_table_missing"] and c["lcat_issues"]
    ]

    # How many of the period's CLINs could not be typed (#76). Counted on the
    # contract because it's a property of the award's extraction, not of any one
    # line: a non-zero count means some figures below are the legacy type-blind read
    # and the UI should say so rather than presenting them as type-aware. Reported
    # for the same reason `clin_scope` is — an unlabelled read must never look like
    # a typed one (#42).
    pricing_unknown = sum(
        1 for c in computed + nl_cards if not c["pricing_policy"]["known"]
    )

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
            # The raw header type text, unchanged — it's a display label and the UI
            # reads it. The *meaning* of that text now lives on each CLIN's
            # `pricing_policy` (#76), which is what code should branch on.
            "vehicle": header.get("contract_type"),
            "pricing_unknown": pricing_unknown,
            "pop_start": clk["pop_start"],
            "pop_end": clk["pop_end"],
            "current_week": cw,
            "total_weeks": tw,
            "weeks_remaining": max(0, tw - cw),
            # The active period, and the funding arithmetic that scopes a
            # contract-to-date obligation down to it. Reported rather than
            # implied so the numbers on screen can be reconciled to the award:
            #   period_funded = sum of the period's per-CLIN obligations, when the
            #                   award attributed every one of them (#21), else
            #                   min(period_ceiling, obligated - prior_consumed)
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
            # Which cost tier this contract is operating at (#77): 1 = contract
            # documents only (billing burn, margin withheld), 2 = LCAT category
            # direct rates + indirect pools (margin, nobody named), 3 = per-person
            # direct rates (#69). Every rung above 1 is opt-in, and the app is fully
            # functional at 1 — `margin_available` is the single flag the
            # profitability surfaces gate on.
            "cost_model": cost_model_out.payload(),
        },
        "totals": {
            "ceiling": round(total_ceiling, 2),
            "spent": round(total_spent, 2),
            "pct": round(total_spent / total_ceiling, 4) if total_ceiling else 0.0,
            "weekly": round(total_weekly, 2),
            "labor_count": len(computed),
            # Cost of the same work, when it's independently known (#77).
            # `cost_known` false means this equals `spent` because no direct rates
            # were provided — read `contract.cost_model.margin_available` before
            # putting a margin anywhere near a user.
            "cost": round(total_cost, 2),
            "cost_known": all(c["cost_known"] for c in computed) if computed else False,
        },
        "hero": (
            {
                "days": worst["runway_days"],
                "clin": worst["code"],
                "status": worst["status"],
                "limited_by": (
                    "funding" if worst["incrementally_funded"] else "ceiling"
                ),
                # The hero tile's day count as a date (#23) — same arithmetic, so
                # the two can't disagree. Passed means the wall is today or behind.
                "stop_date": worst["stop_date"],
                "stop_date_passed": worst["stop_date_passed"],
            }
            if worst
            else None
        ),
        "clins": computed + nl_cards,
        "tripwires": tripwires,
        "underburn": underburn,
        "funding": funding,
        "data_quality": data_quality,
        # Rate-line coverage (#64), split by what fixes it: `rate_gaps` needs a
        # document (import the rate schedule), `lcat_gaps` needs a decision (map
        # this LCAT to that rate line). Neither gates `all_clear` — see rate_gaps.
        "rate_gaps": rate_gaps,
        "lcat_gaps": lcat_gaps,
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
        # The red CLINs behind an `over` rollup, so the card can name the same limit
        # its own Flight Deck does. Left to _pill's default this always said "Over
        # ceiling", including for a contract whose only red line was funds-short.
        red = [x for x in b["clins"] if x["status"] == "over"]
        if red:
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
                # Both flags handed over as-is so _pill applies its own precedence.
                # Deciding it here instead would let the card contradict the very
                # Flight Deck it links to — contract 5's CLIN 2001 is both projected
                # past its ceiling and already past its funding.
                "status_label": _pill(
                    overall,
                    any(x["ceiling_breached"] for x in red),
                    any(x.get("funds_exceeded") for x in red),
                ),
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
