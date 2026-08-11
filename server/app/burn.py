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

from . import absence as absence_mod
from . import lcat as lcat_match
from . import periods as period_ids
from . import pricing, rates

# Status thresholds, ported verbatim from the design's computeClinFor.
_PAUSED_WEEKS_LEFT = 999
_PACE_WEEKS = 4  # trailing distinct weeks used to estimate forward weekly burn
# Under-burn tripwire: at the current pace the CLIN won't consume its budget
# until this fraction of the PoP *past* the finish line — a large unspent
# balance / slipping delivery signal, symmetric to the over-ceiling tripwire.
_UNDER_SLACK_FRAC = 0.15
# The over-ceiling counterpart to `_UNDER_SLACK_FRAC`, and the reason it exists at
# all: `_forward_band`'s red edge is a flat one week, so a projection that lands
# 1.6 weeks shy of a 52-week finish line was called a ceiling breach. That is a 2%
# tolerance on a forecast built by taking a *four-week* trailing average
# (`_PACE_WEEKS`) and extrapolating it across the whole remaining PoP — months of
# leverage on a one-month sample. One 4-day holiday week, one person on leave, one
# late timesheet moves the landing week further than the old margin allowed, so the
# flag fired on contracts sitting dead on plan: CLIN 2001 of 7024HEXDVC0001043 read
# "Over ceiling" at 22.5% of its ceiling spent against 23.1% of its PoP elapsed.
#
# The honest question is not "does the landing week miss?" but "is the pace
# materially hotter than the budget affords?", so red is gated on the pace overrun
# instead. Affordable weekly = remaining / weeks_to_go, and
#
#     weekly / affordable - 1  ==  weeks_to_go / weeks_left - 1
#
# so the test is made in weeks and needs no dollars. 5% is wider than the sampling
# noise and far narrower than a real overrun: on CLIN 2001 the pace is 4% hot with 40
# weeks to go, while a line genuinely outrunning its funded slice there is 182% hot.
#
# This is the RED edge only. An overrun inside it is amber `watch`, not silence — the
# pace genuinely is above what the budget affords, so it earns a colour; what it does
# not earn is an alarm and a staffing plan, because a four-week sample cannot resolve
# a few percent that confidently. Only `ok` means the pace is at or under affordable.
_PACE_TOLERANCE = 0.05
# Margin-erosion watch on fixed-price work (#79). Projected cost this close to the
# firm price means the fee is nearly gone — amber, because there is still a PoP left
# to correct in and no funding cliff to hit. Set alongside the 0.8 realized-spend
# watch band rather than at it: on fixed price the number is a *projection* to PoP
# end, so a lower gate would go amber on almost every healthy contract.
_MARGIN_WATCH_FRAC = 0.9
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
# The 75%-of-allotted half of the same clause has no state of its own: #24 proposed
# one and was closed not-building with #25's letter, its only consumer. If it is ever
# revived it must read its base off `funding_clause` below rather than assuming -22 —
# 75% of *allotted* is the threshold under -22 only. Under -20 the base is estimated
# cost, on T&M it is the ceiling price, and on fixed price there is no notification.
_FUNDING_DUE_DAYS = 60
# Minimum weeks elapsed in the active period before an obligation *rate* can be
# read off the mod history. Below this, a single early tranche divided by one or
# two weeks produces an enormous weekly figure that says nothing about funding
# behaviour — the caller falls back to the funded-vs-elapsed proxy instead.
_PACE_MIN_WEEKS = 4


def billable_hours(row: dict) -> float:
    """The hours on a timesheet row that may be charged to a CLIN (#85).

    Leave and holidays are not direct charges. They are indirect costs recovered
    through the fringe pool (FAR 31.205-6), and every loaded rate on a rate table
    already carries that fringe. Pricing paid-but-not-worked hours against a CLIN
    therefore bills the same cost twice and overstates burn by the leave share.

    Three cases, and the ordering matters:

    1. `reg_hours` present — the source sends the split, so regular + overtime IS
       the billable figure and is used directly. This is the only authoritative case.
    2. `reg_hours` absent but `leave_hours` present — a row synced before the split
       existed, where `total_hours` was defined as reg + ot + leave. Back the leave
       out. Absence of `reg_hours` is the version signal; there is no other one.
    3. Neither present — nothing to subtract, so `total_hours` stands. A source that
       reports no leave at all is taken at its word rather than guessed at.

    Case 2 is not hypothetical: a `runway.db` that synced before this landed keeps
    its cached rows, and `_add_missing_columns` leaves their new columns NULL. Those
    rows stay slightly overstated (as they are today) until the next sync, instead of
    silently reading 0 and zeroing the burn.
    """
    reg = row.get("reg_hours")
    if reg is not None:
        return float(reg) + float(row.get("ot_hours") or 0)
    total = float(row.get("total_hours") or 0)
    leave = row.get("leave_hours")
    if leave is not None:
        # Never below zero: a malformed row claiming more leave than total should
        # contribute nothing, not a credit against the CLIN.
        return max(0.0, total - float(leave))
    return total


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


def _missing_option_mods(contract: dict, rows: List[dict]) -> List[dict]:
    """Unexercised options that nevertheless carry positive timesheet activity.

    This is diagnostic only. A charge is evidence that performance is happening,
    not authority to exercise an option or create funding. Only an ingested option
    exercise in obligation history can suppress the signal.
    """
    schedule = contract.get("periods") or []
    clins = contract.get("clins") or []
    # The same read the mod path flips `exercised` from, so an ingested exercise
    # suppresses this warning by the rule that set the flag rather than a second,
    # looser one. It matters when the flag is stale — re-ingesting the award rebuilds
    # the periods with every option un-exercised, and the history is then the only
    # surviving evidence that one is in force.
    exercised = period_ids.exercised_keys(contract)

    # The Base by name, not by position: an extraction is not required to return the
    # schedule in order, and mistaking an option for the Base would suppress the
    # warning on the one period it exists for. Position is the fallback only when no
    # period names itself the base.
    named_base = any(period_ids.key(p.get("name")) == "base" for p in schedule)
    options = (
        [p for p in schedule if period_ids.key(p.get("name")) != "base"]
        if named_base
        else schedule[1:]
    )

    positive = [row for row in rows if billable_hours(row) > 0]
    missing = []
    for period in options:
        period_name = period_ids.key(period.get("name"))
        if period.get("exercised") or period_name in exercised:
            continue
        option_codes = {
            period_ids.clin_key(clin.get("clin"))
            for clin in clins
            if period_ids.key(clin.get("period")) == period_name and clin.get("clin")
        }
        start, end = _period_window(period)
        matched_codes = set()
        detected = False
        for row in positive:
            by_code = period_ids.clin_key(row.get("charge_code")) in option_codes
            week = _d(row.get("week_ending"))
            by_date = bool(
                week and start and start <= week and (end is None or week <= end)
            )
            if by_code or by_date:
                detected = True
                if by_code:
                    matched_codes.add(str(row.get("charge_code")).strip())
        if detected:
            missing.append(
                {
                    "period": period.get("name"),
                    "clins": sorted(matched_codes),
                }
            )
    return missing


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


def burden_fn(cost_model, header=None):
    """`lcat.line_rate`'s burdening function for a contract, or None (#144).

    A cost-reimbursement award prints an unburdened direct rate per category and its
    indirect factors separately (FAR 15.408 Table 15-2), because on a cost-type line
    the government reimburses allowable cost — there is no hourly price to print. So
    every one of its rate lines was skipped as unpriced and every hour fell to
    `ceiling / est_hours`, while `cost` beside it resolved the very same categories
    correctly through `CostModel`. One award, two ladders, two answers.

    This closes that: the rate is the category's direct rate carried through the
    contract's own pools, which is arithmetic the award fully determines.

    Two gates, both narrow on purpose:
      * `rate_set.usable` — no indirect pools, no buildup. A direct rate alone is
        not a billable rate and guessing a burden would invent the number this
        function exists to avoid inventing.
      * cost-reimbursement only — on FFP or T&M the printed loaded rate IS the
        price, and substituting our cost for a price the award states would report
        what we spend as what we may invoice. Those types simply never get here.

    Fee is deliberately NOT in it. `spent` on a cost-type CLIN is cost (#79) and the
    earned fee is reported beside it by #80's engine, so folding a fee share into
    the hourly rate would count it twice and pre-empt #134's open question about
    whether fee draws down funded availability. This rate is cost, and says so.
    """
    if not cost_model or not cost_model.rate_set.usable:
        return None

    def fn(clin, direct):
        if not pricing.policy_for(clin, header).is_cost_reimbursement:
            return None
        return rates.burden(direct, cost_model.rate_set).total_cost

    return fn


def _rate_resolver(clin: dict, index=None, aliases=None, burden=None):
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
    return lcat_match.resolver(clin, index=index, aliases=aliases, burden=burden)


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


def _ceiling_band(
    ceiling_exhaust: Optional[float], current_week: int, total_weeks: int
) -> str:
    """How the *actual* ceiling's projection reads: `over` | `watch` | `ok`.

    Not the same question as `_forward_band`, and it must not be answered with that
    function's flat one-week edge — see `_PACE_TOLERANCE` for why the edge reported a
    breach on a CLIN sitting on plan. This asks whether the forward pace is materially
    hotter than the ceiling can afford across the weeks that are left, which is the
    question a PM can act on.

    Deliberately conservative about red, because `over` here is not just a label: it
    is the gate that switches off the incremental-funding softening in
    `_funded_shortfall_status`. A false positive doesn't merely mislabel a card, it
    promotes routine tranche funding into a red "Over ceiling" and hands the Flight
    Deck a staffing-cut recommendation for a contract that has the money to pay its
    team.

    Any overrun inside the tolerance is amber `watch`. A small overrun is not silence:
    the pace really is above what the budget affords, so it earns a colour and a place
    on the card. What it does not earn is a red alarm and a staffing plan, because a
    four-week sample cannot resolve a few percent that confidently. `ok` therefore
    means what it says — the pace is at or under what the remaining budget affords.

    There is deliberately no `under` band: unspent ceiling is scope the contract never
    had to use, and "you are not going to breach your ceiling" is not a finding.

    Two edges resolved before the pace test, neither expressible as a ratio: no
    headroom left is a breach that has already happened, and past the finish line with
    headroom intact there is no remaining PoP to project into and so nothing to breach.
    """
    if ceiling_exhaust is None:
        return "ok"
    weeks_left = ceiling_exhaust - current_week
    if weeks_left <= 0:
        return "over"
    weeks_to_go = total_weeks - current_week
    if weeks_to_go <= 0:
        return "ok"
    # weekly / affordable - 1, said in weeks. See _PACE_TOLERANCE for the identity.
    overrun = weeks_to_go / weeks_left - 1
    if overrun > _PACE_TOLERANCE:
        return "over"
    if overrun > 0:
        return "watch"
    return "ok"


def _funds_exceeded(
    spent: float, budget: float, ceiling: float, incrementally_funded: bool
) -> bool:
    """Realized: spend has *already* passed the obligated funding, ceiling intact.

    Distinct from every projection in this module — this has happened, in dollars
    (`overspent` carries the amount). Only meaningful for an incrementally funded
    CLIN: when budget == ceiling, passing it is a ceiling breach and says so.
    Spend past the actual ceiling is likewise a ceiling story, so it yields here.

    A zero budget is a real budget, and the strictest one: a CLIN named in the
    accounting block at $0 (or left with nothing after the by-name obligations
    are netted out) cannot absorb a single dollar, so any spend on it is already
    past its funding. The old `not budget` guard read that budget as no-data and
    returned False, suppressing the breach on exactly the line most likely to
    have one — the same trap the `0 < funded` guard fell into.
    """
    if not incrementally_funded or spent <= 0 or spent < budget:
        return False
    return not (ceiling and spent >= ceiling)


def _funded_shortfall_status(
    runway_days: Optional[int],
    ceiling_band: str,
    incrementally_funded: bool,
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

    `ceiling_band` is `_ceiling_band`'s tolerance-aware read, and it replaced a
    `ceiling_breached` boolean plus a re-band through `_forward_band` computed right
    here. That re-band was where the funding read broke: it judged the ceiling on the
    same flat one-week edge, so a CLIN whose *ceiling* was fine to within a rounding
    error still came back red and the softening above it never got to matter. Taking
    the band ready-made also means the breach flag on the payload, the pill and this
    status can no longer disagree about the same ceiling — they are one derivation.

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
    ceiling_breached = ceiling_band == "over"
    if (
        incrementally_funded
        and not ceiling_breached
        and (mod_in_progress or funding_keeps_pace)
    ):
        if runway_days is not None and runway_days <= _FUNDING_DUE_DAYS:
            return "funding"
        # Outside the FAR window the CLIN is judged on its ceiling, so the ceiling's
        # own band *is* the answer. It can only be `ok` or `watch` here (`over` is
        # excluded by the guard above) and `_ceiling_band` has no `under` state, which
        # is what makes the old "spend faster" bug structurally impossible rather than
        # clamped: reaching this function means the funded slice runs dry before PoP
        # end, and a CLIN that runs out of money cannot take advice to spend faster.
        # It used to be able to — the under-burn card is built from the funded slice
        # while the label came from the ceiling, so the seed-19 demo rendered
        # "projected to under-spend its funded $2.7M by $0.0M ... ~-5 weeks after the
        # PoP ends" and advised staffing up on a CLIN 74 days from dry.
        return ceiling_band
    # Not softenable. Either the ceiling is genuinely going (`over`), or obligations
    # are lagging the burn with no mod moving — #22's red, which stays red however
    # comfortable the ceiling looks, because the money to pay for that ceiling is not
    # arriving. The one case left is a CLIN that is *not* incrementally funded: its
    # budget IS the ceiling, so this function was reached by that terminal budget
    # projecting dry. Inside the tolerance that is a watch and not an alarm, but it is
    # never silent the way the incremental case is — there is no next tranche to
    # replenish it, so 5% hot on the only money the contract will ever get is worth
    # saying out loud.
    if not incrementally_funded and not ceiling_breached:
        return "watch"
    return "over"


def _limited_by(incrementally_funded: bool, ceiling_is_price: bool) -> str:
    """Which limit runs out first, in the vocabulary every banner switches on.

    `funding` whenever the CLIN is incrementally funded — the funded slice can never
    exceed the ceiling, so obligated money is always what runs dry first, and that is
    already exactly what `budget` is. Otherwise the terminal limit is the ceiling, and
    `ceiling_price` distinguishes the T&M kind (#81 part 5): a negotiated not-to-exceed
    under FAR 52.232-7, whose remedy is a ceiling increase, from a cost-type ceiling of
    estimated cost plus fee, whose remedy is a mod raising the estimate.

    Third value added rather than a parallel flag because every consumer already reads
    this one, and all of them are written as `=== "funding" ? … : ceiling-copy` — so
    `ceiling_price` falls through to the ceiling wording it is a specialisation of, and
    only the surfaces that want to say something sharper have to change."""
    if incrementally_funded:
        return "funding"
    return "ceiling_price" if ceiling_is_price else "ceiling"


def _pill(
    status: str,
    ceiling_breached: bool = True,
    funds_exceeded: bool = False,
    margin_managed: bool = False,
    fee_exhausted: bool = False,
    ceiling_is_price: bool = False,
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

    `margin_managed` switches to fixed-price wording (#79). All three labels above
    name a funding limit, and a fixed-price CLIN has none — its red means cost is
    projected past the price and the fee is gone, which is a profitability statement,
    not a Limitation of Funds one. Same statuses, different vocabulary, so the pill
    can never tell an FFP reader their funding ran out.
    """
    if margin_managed:
        return {
            "over": "Margin exceeded",
            "watch": "Margin at risk",
            "ok": "On pace",
            "paused": "Paused",
            "unpriced": "Unpriced",
        }.get(status, "—")
    if status == "over":
        if funds_exceeded:
            return "Funds exceeded"
        if not ceiling_breached:
            return "Funds short"
        # A T&M ceiling is a negotiated not-to-exceed, not estimated cost plus fee
        # (#81 part 5). Naming it as a price is what tells a reader the remedy is a
        # ceiling increase under 52.232-7 rather than a mod raising an estimate.
        return "Over ceiling price" if ceiling_is_price else "Over ceiling"
    if status == "fee_eroding":
        # Two labels for one state, because "Fee eroding" on a CLIN whose fee is
        # entirely gone understates it by exactly the amount that matters. Both stay
        # amber: this is a profitability statement about money the company loses, not a
        # funding limit, and the reds on a cost-type CLIN all name a funding limit.
        # Whether a *fully* absorbed fee deserves the red that fixed-price work gets
        # for the same fact ("Margin exceeded") is a live question — see #144.
        return "Fee exhausted" if fee_exhausted else "Fee eroding"
    return {
        "watch": "Watch",
        "ok": "On pace",
        "under": "Under pace",
        "funding": "Funding due",
        "paused": "Paused",
        "unpriced": "Unpriced",
    }.get(status, "—")


# A runaway guard on the week walk below, not a policy. A CLIN burning a rounding
# error a week would otherwise walk forever looking for an exhaust point; ten years
# past PoP end is far beyond any answer worth reporting.
_MAX_PROJECTION_WEEKS = 520


def _absence_projection(
    spent: float,
    remaining: float,
    weekly: float,
    budget: float,
    current_week: int,
    total_weeks: int,
    factors: List[dict],
    exhaust_week: float,
) -> Optional[dict]:
    """The forward projection as a per-week series, bent around known absence (#85).

    **Strictly additive.** `weekly`, `weeks_left`, `exhaust_week`, `runway_days`, the
    status, the tripwires and the hero tile all keep the flat-pace figures they have
    always had, and this is a *new* key alongside them. Every existing consumer of
    the burn payload — Flight Deck cards, tripwires, suggests, Portfolio, Ask Runway
    — therefore reads exactly what it read before. Returns `None` whenever there is
    no absence in the remaining weeks, so a contract nobody has entered absence for
    produces no series at all and `BurnChart` keeps its straight-line geometry byte
    for byte. That fallback is the safety property: the bend is opt-in per contract.

    The reduction is proportional. `weekly` is a trailing *dollar* pace, and each
    week's factor is the share of the team's workdays that survive absence, so
    `weekly x factor` stays in the same dollars without this needing a rate or an
    hours figure. Weeks past PoP end are walked at factor 1.0 — the holiday calendar
    and the entered absences only describe the period we can see — so the exhaust
    point stays honest for a CLIN that outlives its own PoP.
    """
    if weekly <= 0 or remaining <= 0 or not absence_mod.has_effect(factors):
        return None

    by_week = {f["week"]: f.get("factor", 1.0) for f in factors}
    points = [{"week": current_week, "spent": round(spent, 2)}]
    cum = 0.0
    bent_exhaust = None
    week = current_week
    limit = total_weeks + _MAX_PROJECTION_WEEKS
    while week < limit:
        week += 1
        step = weekly * by_week.get(week, 1.0)
        if step > 0 and cum + step >= remaining:
            # Land the exhaust point inside the week it happens rather than at the
            # week boundary, so a bend that buys three days shows three days.
            bent_exhaust = (week - 1) + (remaining - cum) / step
            if bent_exhaust <= total_weeks:
                points.append(
                    {"week": round(bent_exhaust, 2), "spent": round(budget, 2)}
                )
            break
        cum += step
        if week <= total_weeks:
            points.append({"week": week, "spent": round(spent + cum, 2)})

    # What gets *reported* as bending the line: weeks still ahead of us, and no
    # further out than the money reaches. Absence behind us is history and Part 1
    # owns it; absence after the funds run out never happens on this CLIN's dime, and
    # naming it would print "1 week affected" beside a gain of zero.
    horizon = bent_exhaust if bent_exhaust is not None else total_weeks
    ahead = [
        f
        for f in factors
        if current_week < f["week"] <= horizon and f.get("factor", 1.0) < 1
    ]
    if not ahead:
        # Absence exists, but all of it falls after the funds run out, so the bent
        # line is the straight line. Withhold the series rather than ship one that
        # draws identically — an unexplained second geometry is a regression risk
        # for no gain.
        return None
    return {
        "points": points,
        # The bent line's exhaust week, next to the flat-pace one the rest of the
        # payload reports, so a reader can see the difference rather than infer it.
        "exhaust_week": round(bent_exhaust, 2) if bent_exhaust is not None else None,
        "flat_exhaust_week": round(exhaust_week, 2),
        # Positive means absence buys runway, which is the sentence this feature
        # exists to let someone say out loud: "the August dip buys you two weeks".
        "weeks_gained": (
            round(bent_exhaust - exhaust_week, 2) if bent_exhaust is not None else None
        ),
        "weeks_affected": len(ahead),
        "holidays": sorted({d for f in ahead for d in f.get("holidays") or []}),
        "people": sorted({p for f in ahead for p in f.get("people") or []}),
    }


def _fee_payload(position, projected, in_revenue: bool, cost_known: bool):
    """The fee position as the payload carries it (#80), or None where the type has no
    fee mechanic.

    `known` is deliberately the *conjunction* of two different facts — the award printed
    the fee figures, and cost is a real buildup rather than a billing stand-in — because
    a reader asking "can I trust this fee number?" needs one answer, and both halves have
    to be true for the answer to be yes. `terms_known` and `cost_known` are carried
    beside it so the UI can say which half is missing, which is the same split
    `margin_position.known` makes for the identical reason.
    """
    if position is None:
        return None
    out = position.payload()
    out["known"] = bool(position.known and cost_known)
    out["terms_known"] = position.known
    out["cost_known"] = cost_known
    # Whether `revenue` on this CLIN includes the earned fee. False on a fixed-price
    # line (its revenue is the price) and at Level 1 (the billing rate already
    # contains the fee), so nobody can double-count by adding it themselves.
    out["in_revenue"] = in_revenue
    out["projected"] = projected.payload() if projected is not None else None
    return out


def _fee_periods_by_clin(contract: dict, clins: List[dict]) -> dict:
    """Award-fee evaluation periods (#80) routed to the CLIN whose pool they draw on.

    The determinations are the government's and are entered rather than extracted, so
    they live on the contract blob — the same storage holidays and absences use, for the
    same reason: no migration, and they splat out of `get_contract`.

    A period may name its CLIN. Most don't, because most CPAF awards carry one pool on
    one CLIN — so an unassigned period is routed there. When *several* CLINs carry a
    pool, an unassigned period is dropped rather than applied to each: counting one
    determination against two pools would report fee that was never awarded, and the
    fix (name the CLIN) belongs to whoever entered it.
    """
    periods = pricing.normalize_fee_periods(contract.get("fee_periods"))
    if not periods:
        return {}
    bearers = [
        str(c.get("clin"))
        for c in clins
        if pricing.fee_terms(c).award_fee_pool is not None
    ]
    fallback = bearers[0] if len(bearers) == 1 else None
    routed: dict = {}
    for record in periods:
        target = record["clin"] or fallback
        if target is None:
            continue
        routed.setdefault(str(target), []).append(record)
    return routed


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
    burden=None,
    pop_start: Optional[date] = None,
    absence: Optional[dict] = None,
    fee_periods: Optional[List[dict]] = None,
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
    CLIN. Defaulted rather than required so a caller holding only a CLIN still gets
    its `CLIN.type` read.

    **What `spent` means (#79).** It is not "billings" and it is not "cost" — it is
    *the quantity this CLIN is measured against*, and the policy picks which:
    cost on cost-reimbursement, billings on T&M and on `unknown`, and cost-against-
    the-price on fixed price. `measured_against` on the payload always names it.
    Two readers will otherwise assume differently, and both readings are defensible,
    which is exactly why it is written down here.

    The name was kept because everything downstream of it — the trailing pace,
    `remaining`, `weeks_left`, `exhaust_week`, `status`, the tripwire lists, the hero
    tile, the portfolio rollup, `allocation.py` — is correct as written *once `spent`
    holds the right quantity*. That is what makes this a reformat of one number
    rather than a rewrite of the engine. `billings`, `cost`, `revenue` and
    `fee_earned` are all reported separately, so nothing is hidden behind the choice.

    On fixed-price work the policy also withholds four figures that were always
    wrong there: the funding tripwire, `runway_days`, `weeks_left`/`exhaust_week`
    and the dated hard stop. Hours do not consume funding when the government owes a
    firm price, so a "charging stops on this date" forecast is not imprecise, it is
    false. A cost-vs-price `margin_position` is reported instead.

    `rate_index` and `aliases` come from `compute` for the same reason `policy`
    does — classifying a rate miss needs the *other* CLINs' rate lines and the
    contract's confirmed LCAT mappings, and this function only sees one CLIN (#64).
    Both default to empty, in which case resolution behaves exactly as it did: an
    unmatched LCAT is still reported, just without naming which of the three causes
    it is.

    `pop_start` and `absence` drive the bent forward projection (#85) and nothing
    else. Both default to nothing, and with nothing every figure on the payload is
    identical to what it was before that ticket — the series is an extra key, never
    a replacement (see `_absence_projection`)."""
    policy = policy or pricing.policy_for(clin, None)
    # The cost side (#77). Defaults to an empty model, which is Level 1: cost falls
    # back to the billing rate and is flagged as such — so at Level 1 `cost` and
    # `billings` are equal by construction and the policy branch below cannot move
    # any number, whichever quantity it selects.
    cost_model = cost_model or rates.CostModel()
    # `compute` builds this once and hands it down, for the same reason it hands
    # down `rate_index`: it is contract-scoped, and a burden derived per CLIN could
    # disagree with the one the index was built with (#144).
    burden = burden if burden is not None else burden_fn(cost_model)
    resolve, blended, source = _rate_resolver(clin, rate_index, aliases, burden)
    clin_rows = _rows_for_clin(clin, rows, window)

    # Two accumulators, never mixed (#77), now both load-bearing (#79): `billings` is
    # hours x the loaded rate the award prices, `cost` is what those same hours
    # consumed once burdened. Which of them becomes `spent` is the policy's call.
    billings = 0.0
    cost = 0.0
    # Hours priced by the blended fallback on each side, so #144 can say whether the
    # measured quantity ever touched it.
    blended_billed_hours = 0.0
    blended_cost_hours = 0.0
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
    weekly_totals = {}  # week_ending -> billings that week
    # The same weeks under the same keys, in cost dollars. A cost-measured CLIN needs
    # a cost-measured pace, or `remaining / weekly` divides one quantity by another.
    weekly_cost = {}
    # Who those weekly dollars belong to, for #85's absence weighting: the same weeks
    # again, split by person. Accumulated in both quantities so the shares are read
    # off whichever one the policy ends up measuring — sharing a billings-weighted
    # split across a cost-measured CLIN would weight absence by the wrong dollars.
    by_person = {}  # week_ending -> {employee_id -> billings}
    cost_by_person = {}  # week_ending -> {employee_id -> cost}
    for r in clin_rows:
        hours = billable_hours(r)
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
        billings += amt
        wk = r.get("week_ending") or ""
        weekly_totals[wk] = weekly_totals.get(wk, 0.0) + amt
        emp = (r.get("employee_id") or "").strip()
        if emp:
            by_person.setdefault(wk, {})
            by_person[wk][emp] = by_person[wk].get(emp, 0.0) + amt

        # What the same hour cost us, down the fallback ladder (#77). Accumulated
        # alongside billings, never mixed into them.
        cr = cost_model.cost_for(label or None, res.rate, r.get("employee_id"))
        # Hours the blended fallback actually priced, counted on each side (#144).
        # Counted rather than inferred from `cost_known` or `source`: those are
        # CLIN-level flags answering a near-enough question, and the one asked here
        # — did the number this card reports come from `ceiling / est_hours`? — has
        # to be answered in hours or not at all.
        if res.via == lcat_match.VIA_BLENDED:
            blended_billed_hours += hours
            # On the cost side the blended rate only gets in as the Level-1 stand-in
            # for a category we hold no direct rate for.
            if cr.source == rates.SOURCE_NEGOTIATED:
                blended_cost_hours += hours
        if cr.rate is not None:
            cost += hours * cr.rate
            weekly_cost[wk] = weekly_cost.get(wk, 0.0) + hours * cr.rate
            if emp:
                cost_by_person.setdefault(wk, {})
                cost_by_person[wk][emp] = (
                    cost_by_person[wk].get(emp, 0.0) + hours * cr.rate
                )
        cost_hours[cr.source] = cost_hours.get(cr.source, 0.0) + hours
        if label and cr.known and label not in cost_by_lcat:
            cost_by_lcat[label] = (cr, res.rate)

    # Unpriced: rows were charged to this CLIN but none could be priced (no rate
    # table and no est_hours → blended None → every row skipped above). This is a
    # data-quality gap, NOT "no charges": the engine found spend it could not value,
    # so reading it as `paused` and letting it pass `all_clear` shows the most
    # reassuring state for a contract that could not be measured at all (#40). The
    # unmatched LCATs name what to fix, via the supplemental rate import.
    # Deliberately read off `billings`, not `spent`: "the engine found charges it
    # could not value" is a statement about the rate table, and it must mean the same
    # thing on every pricing policy.
    unpriced = bool(clin_rows) and billings == 0.0 and source == "none"

    # ---- the policy branch (#79) --------------------------------------------------
    # `spent` keeps its name and every downstream reader — pace, remaining, weeks_left,
    # exhaust_week, status, the tripwires, the hero, the portfolio rollup — but it now
    # means *the quantity this CLIN is measured against*, which the pricing policy
    # (#76) chooses:
    #
    #   cost_reimbursement → cost.     The government reimburses allowable cost, so
    #                                  cost is what consumes the funding. Billing
    #                                  dollars are cost + fee and overstate the draw.
    #   time_and_materials → billings. Hours x the loaded rate against the ceiling
    #                                  price (FAR 16.601(c)) — the one type the
    #                                  pre-#79 engine already measured correctly.
    #   fixed_price        → cost.     Hours do not consume funding here at all; the
    #                                  government owes the price. What is at risk is
    #                                  margin, so the quantity that matters is what
    #                                  the work cost us, measured against the price.
    #                                  `measured_against` says "price" to name the
    #                                  denominator, because that is the part that
    #                                  differs from a cost-type read.
    #   unknown            → billings. Exactly the pre-#79 behaviour, and the payload
    #                                  says `pricing_policy.known: false` so no reader
    #                                  mistakes the legacy read for a typed one.
    #
    # At Level 1 (no direct rates) cost == billings, so on today's data this selection
    # is a no-op on every type and no existing figure moves.
    if policy.is_cost_reimbursement:
        measured_against = "cost"
    elif policy.is_fixed_price:
        measured_against = "price"
    else:
        measured_against = "billings"
    on_cost = measured_against in ("cost", "price")
    spent = cost if on_cost else billings
    measured_weekly = weekly_cost if on_cost else weekly_totals

    # Fixed-price work is margin-managed, not funding-managed: the policy itself says
    # so (`funding_tripwire == "none"`), and that single declaration is the seam. On
    # these CLINs a funding tripwire, a `runway_days` and a dated hard stop are not
    # imprecise, they are false — charging will not be blocked, because the price is
    # owed regardless of hours. All three are withheld below and a cost-vs-price
    # margin position is reported in their place.
    margin_managed = policy.funding_tripwire == "none"

    # The third row of the same declaration, and until #81 part 5 it was consumed
    # nowhere: `funding_tripwire == "at_ceiling"` means the reportable limit on this
    # type is the **ceiling price** (FAR 16.601(c)(1) — a not-to-exceed the contractor
    # exceeds at its own risk), governed by 52.232-7 rather than by a limitation-of-
    # funds clause. T&M is the type that says it.
    #
    # Read narrowly, and deliberately so: it does *not* mean the funded slice stops
    # mattering. An incrementally funded T&M CLIN cannot bill dollars nobody obligated,
    # so the obligation is still the limit that runs out first and still the one the
    # runway is measured against. What changes is that a *ceiling* breach here is a
    # different event from a cost-type ceiling breach — a cost-type ceiling is estimated
    # cost plus fee and the remedy is a mod raising it, while this one is a negotiated
    # not-to-exceed and the remedy is a ceiling increase. Same statuses, different
    # limit, so it gets its own `limited_by` value and its own copy (#79 did the same
    # thing for fixed-price wording rather than inventing statuses).
    ceiling_is_price = policy.funding_tripwire == "at_ceiling"

    # Forward weekly pace = mean weekly spend over the most recent PACE_WEEKS weeks
    # that actually have charges. Steadier than a single noisy week. Measured in the
    # same quantity as `spent`, so `remaining / weekly` stays dimensionally honest.
    recent_weeks = sorted(measured_weekly)[-_PACE_WEEKS:]
    weekly = (
        sum(measured_weekly[w] for w in recent_weeks) / len(recent_weeks)
        if recent_weeks
        else 0.0
    )

    # Each person's share of that pace (#85). The same trailing window, in the same
    # quantity, so an absence removes the dollars that person is actually observed to
    # put on this CLIN. Someone who has not charged here has no share and therefore
    # cannot reduce the pace — see absence.week_factors.
    measured_by_person = cost_by_person if on_cost else by_person
    pace_shares = {}
    for wk in recent_weeks:
        for emp, amt in (measured_by_person.get(wk) or {}).items():
            pace_shares[emp] = pace_shares.get(emp, 0.0) + amt

    ceiling = float(clin.get("ceiling") or 0)
    # The dollars this CLIN can actually spend before it stalls: the funded
    # amount when incrementally funded, otherwise the full ceiling. Runway is
    # measured against this; the ceiling is still reported for the % display.
    # Zero is a real funded amount, not "no data": an option can be exercised
    # before any money is obligated against it, and that is the tightest funding
    # state there is. The old `0 < funded` guard treated it as no-funding-info and
    # fell back to a full-ceiling runway, hiding exactly the case that matters.
    incrementally_funded = funded is not None and funded < ceiling
    # Resolved here rather than at the payload because this is the first point where
    # both halves of the answer exist — the type (from `policy`) and whether the money
    # arrives in tranches. See the payload key for why it goes no further than that.
    funding_clause = policy.funding_clause_for(incrementally_funded)
    budget = funded if incrementally_funded else ceiling
    remaining = budget - spent
    pct = (spent / ceiling) if ceiling else 0.0

    # ---- cost, revenue and fee (#79) ----------------------------------------------
    # Three numbers that must always reconcile: `fee = revenue - cost`, reported and
    # never derived independently, so no reader can arrive at a fourth answer.
    #
    # `cost_known` is the #77 flag, hoisted here because the fee read depends on it.
    cost_known = bool(cost_hours) and rates.SOURCE_NEGOTIATED not in cost_hours

    # Whether the blended fallback priced any of the number this card reports (#144).
    #
    # `rate_table_missing` is a fact about the BILLING table alone, and until #79 that
    # was the whole story — everything was measured in billings. Since #79 a
    # cost-reimbursement CLIN is measured on `cost`, resolved down a different ladder
    # entirely (`CostModel.cost_for`), so the two can disagree: a CLIN can have no
    # billable rate line and still price every measured hour per category.
    #
    # Read off the hours each side actually charged at `blended`, so it stays true on
    # a CLIN that is part-costed rather than reporting the whole card as blended for
    # one category's worth of fallback.
    blended_priced_spend = bool(blended_cost_hours if on_cost else blended_billed_hours)

    # The fee position (#80): what this CLIN's fee terms have earned at the cost it has
    # actually incurred, under its own type's rule. Pure arithmetic in `pricing`, called
    # again below on projected cost for the forecast.
    fee_terms = pricing.fee_terms(clin)
    fee_position = pricing.earned_fee(
        policy, fee_terms, cost, periods=tuple(fee_periods or ())
    )
    # Folded into revenue only where cost is a real buildup. At Level 1 there are no
    # direct rates, so `cost` *is* the loaded billing rate and already contains the fee
    # (#77) — adding an earned fee on top would count the same dollars twice and report
    # a margin off two copies of them. The terms are still reported; only the fold is
    # withheld, which is the same layered-privacy contract `margin_pct` keeps.
    # Cost-reimbursement only: FPI carries a fee position too, but a fixed-price line's
    # revenue is its price and never cost-plus-fee, so its projected profit is reported
    # beside the margin position rather than folded into revenue.
    fee_in_revenue = bool(
        policy.is_cost_reimbursement
        and fee_position
        and fee_position.known
        and cost_known
    )
    if policy.is_cost_reimbursement:
        # Cost incurred plus the fee earned on it (FAR 16.306 et seq). Where the award
        # printed no fee figures, or cost is a billing stand-in, the fee is zero and
        # says so via `fee_known: false` rather than being estimated off the spread —
        # revenue == cost is then the correct partial answer: what we may invoice
        # today, before fee.
        # Quantised to cents *before* it lands in revenue, so `cost + fee == revenue`
        # holds exactly on the payload instead of to within a rounding cent — #79's
        # reconciliation promise has to survive the fee becoming a real number.
        revenue = cost + (round(fee_position.earned, 2) if fee_in_revenue else 0.0)
        fee_known = fee_in_revenue
    elif policy.is_fixed_price:
        # The price is owed on delivery, not on hours (FAR 16.202) — so revenue is the
        # firm price and the fee is whatever the price did not have to spend. This is
        # the margin-at-completion position the FFP card reports instead of a runway.
        revenue = ceiling
        fee_known = cost_known
    else:
        # T&M and unknown: we may bill hours x the loaded rate, and the spread over
        # cost is the fee inside that rate.
        revenue = billings
        fee_known = cost_known
    fee = revenue - cost
    # Withheld, not estimated, when cost is a billing-rate stand-in: at Level 1 cost
    # equals billings, so `fee` is a structural zero and a margin % off it would be a
    # fabricated 0%. `fee` itself stays a number so the rollups still reconcile; the
    # percentage — the figure a user would actually read as profitability — is None.
    margin_pct = (fee / revenue) if (fee_known and revenue) else None

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
    if margin_managed:
        # No runway on fixed-price work. Nothing runs out, so there is no number of
        # days to report and reporting one invites the reader to plan around a wall
        # that does not exist. `weeks_left` and `exhaust_week` are nulled on the
        # payload for the same reason; both stay live as locals because the margin
        # projection below is the same forward pace, aimed at the price.
        runway_days = None

    # The bent projection (#85). Withheld in exactly the states where the straight
    # line is already withheld, so this can never draw a projection into a place the
    # existing geometry refuses to: paused/unpriced (no pace), margin-managed (no
    # funding wall to run into), past PoP (nothing left to project into), and already
    # over budget (the exhaust week is behind us and a forward line would run
    # backwards across the plot — the case BurnChart special-cases).
    projection = None
    # `weekly > 0` is the same test that produces `paused` / `unpriced` below, said
    # here because the status has not been resolved yet at this point.
    projects = weekly > 0 and not margin_managed and not past_pop and remaining > 0
    if projects and pop_start and absence:
        projection = _absence_projection(
            spent=spent,
            remaining=remaining,
            weekly=weekly,
            budget=budget,
            current_week=current_week,
            total_weeks=total_weeks,
            factors=absence_mod.week_factors(
                pop_start,
                # From *next* week. The current week is already charged, and its
                # actuals are leave-free courtesy of Part 1 — reducing it here would
                # subtract the same absence twice.
                current_week + 1,
                total_weeks,
                holidays=absence.get("holidays"),
                absences=absence.get("absences"),
                shares=pace_shares,
            ),
            exhaust_week=exhaust_week,
        )

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
    # Tolerance-aware (see `_ceiling_band` / `_PACE_TOLERANCE`). This used to be
    # `ceiling_exhaust < total_weeks - 1` — a flat one-week margin that called a
    # projection landing 1.6 weeks shy of a 52-week finish line a breach, and so read
    # "Over ceiling" on a CLIN 22.5% through its ceiling at 23.1% elapsed.
    ceiling_band = _ceiling_band(ceiling_exhaust, current_week, total_weeks)
    ceiling_breached = ceiling_band == "over"
    # Realized, not projected: the allotted funding is already spent through. Both
    # branches below stay red on it and the pill says so in the past tense.
    # Never raised on fixed-price work: there is no allotment to exceed, and "Funds
    # exceeded" on a CLIN whose price is owed in full is the single most misleading
    # thing the pre-#79 engine said.
    funds_exceeded = (
        False
        if margin_managed
        else _funds_exceeded(spent, budget, ceiling, incrementally_funded)
    )

    # Cost projected to PoP end, at the current pace — the margin read's forward look.
    # Past the finish line there is nothing left to project into, so realized cost is
    # the answer. Computed for every CLIN so the payload can carry the position, and
    # consulted for the status only on margin-managed ones.
    weeks_to_go = max(0, total_weeks - current_week)
    projected_cost = spent if past_pop else spent + weekly * weeks_to_go

    # The same fee rule, evaluated at the cost this CLIN is heading for (#80). Projected
    # fee at completion is the figure worth alarming on — "projected fee $312K against a
    # $400K target, the overrun has cost $88K of fee" — and it rides the identical
    # forward pace as the runway forecast, so the two can never disagree about the burn.
    fee_projected = (
        pricing.earned_fee(
            policy, fee_terms, projected_cost, periods=tuple(fee_periods or ())
        )
        if fee_position is not None
        else None
    )

    # Is the projected cost overrun eating this CLIN's fee (#81 part 4)? The rule that
    # makes cost-plus different from everything else Runway models: the obligated
    # dollars cover cost *and* fee, so spending past estimated cost does not breach the
    # funded limit while fee remains — it consumes the fee.
    #
    # Read off the same `absorbed` on the same projected position that #80's
    # `fee_alerts` list is built from, deliberately, so the CLIN's own pill and the
    # alert can never disagree about one CLIN. `absorbed` is only ever fee that *cost*
    # has taken — a fixed fee eaten by the overrun (52.216-8's `contractor_fee_first`)
    # or an incentive fee walked down by the share ratio. It is never CPAF's
    # undetermined award pool, which sits below target from day one on every healthy
    # CPAF contract and is a fee-at-risk report, not an overrun.
    #
    # Gated on the payload's own `known` conjunction rather than the position's: at
    # Level 1 cost is a billing stand-in that already contains the fee, so "cost has
    # passed estimated cost" there is an artefact of the rate ladder and not a fact
    # about profit.
    fee_eroding = bool(
        fee_projected is not None
        and fee_projected.known
        and cost_known
        and fee_projected.absorbed > 0
    )
    fee_exhausted = fee_eroding and bool(fee_projected.exhausted)

    if weekly <= 0:
        status = "unpriced" if unpriced else "paused"
    elif margin_managed:
        # Fixed price: cost against the price, not spend against funding. The risk is
        # that cost eats the fee, so the bands are margin bands. There is deliberately
        # no `under` — spending less than the price on fixed-price work is margin
        # earned, not a delivery signal to chase, and flagging it as under-burn is how
        # the pre-#79 engine told teams to spend down money they got to keep. Delivery
        # slippage is a real fixed-price risk, but it is not visible in cost pace and
        # is not this ticket's to invent.
        margin_frac = (projected_cost / ceiling) if ceiling else 0.0
        if margin_frac > 1.0:
            status = "over"
        elif margin_frac >= _MARGIN_WATCH_FRAC:
            status = "watch"
        else:
            status = "ok"
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
                ceiling_band,
                incrementally_funded,
                mod_in_progress,
                funding_keeps_pace,
                funds_exceeded,
            )
        )

    # The state between "on pace" and "over the funded limit" (#81 part 4). Cost has
    # passed estimated cost and the fee is absorbing it, which is where the money
    # actually goes on cost-plus work and was invisible on the card until now — a CPFF
    # CLIN could read "On pace" in green with a third of its fixed fee already spoken
    # for by the overrun.
    #
    # A refinement of `ok`/`watch` only. It is emphatically *not* a funding read and
    # must never be confused with one: the same distinction #22 drew between routine
    # incremental funding and a real breach, applied one level down. So it cannot
    # promote itself over a red, cannot fire when the funding is already spent through
    # or the ceiling is going, and moves no dollar figure — #134 owns whether fee nets
    # out of the funded slice, and until it lands every funding number here is
    # unchanged.
    #
    # `under` is excluded too, though it is only reachable past PoP end: there the
    # budget closed out with money unspent, so "the overrun is eating your fee" is not
    # the finding — and a CLIN can only reach `under` with a *realized* underspend.
    if fee_eroding and status in ("ok", "watch"):
        status = "fee_eroding"

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
    # Read `stop_date_passed` precisely: it means *the binding budget is spent
    # through as of the latest sync*, which is a spend fact, not a calendar one. It
    # is not `stop_date <= today` and must not be swapped for it. Because the whole
    # clock is anchored to the newest timesheet week (`_anchor_date`), a contract
    # that has not synced in months can carry a `stop_date` genuinely behind us with
    # this flag still False — and that is the honest answer, because there are no
    # timesheets for those weeks: we know the projection is old, not that the wall
    # was hit. Testing against today instead would assert a breach nobody has
    # measured, and would paint "charging stopped" on a CLIN whose own pill reads
    # "On pace". Naming the vantage point is what resolves the tension — see
    # `sync.as_of` / `data_age_days` on the payload and `asOfLabel` in the UI.
    #
    # Nulled only for `paused` / `unpriced`, exactly like `exhaust_week` and
    # `weeks_left` — there is no pace to project from, and `_PAUSED_WEEKS_LEFT`
    # would otherwise put the wall 19 years out.
    stop_date = None
    stop_reason = None
    stop_date_passed = False
    #
    # Withheld entirely on margin-managed (fixed-price) CLINs. This is the most
    # consequential of the four removals: a hard-stop date asserts that the
    # accounting system will block charging on a given day, and on fixed-price work
    # it will not — the price is owed however the hours land. Saying "charging stops
    # 14 Nov" there is not a rounding error, it is a false statement about what the
    # user's own Costpoint will do.
    if (
        status not in ("paused", "unpriced")
        and anchor is not None
        and not margin_managed
    ):
        stop_days = round(weeks_left * 7)
        stop_date = (anchor + timedelta(days=stop_days)).isoformat()
        # Which limit produces that date. No precedence rule is needed and none is
        # applied: the funded slice can never exceed the ceiling, so whenever a CLIN
        # is incrementally funded the funded money is what runs out first — which is
        # already exactly what `budget` is. So "the earlier of the two dates" and
        # `_pill`'s realized-over-forecast precedence agree here by construction.
        # Mirrors the `limited_by` on the tripwire lists so the copy can match.
        stop_reason = _limited_by(incrementally_funded, ceiling_is_price)
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

    # Drawn in the measured quantity (#79), not always in billings: the chart's
    # "funds run out" marker sits at `budget`, so a cost-measured CLIN whose curve was
    # still billings would show the crossing at the wrong week.
    cum = 0.0
    series = []
    for i, w in enumerate(sorted(measured_weekly)):
        cum += measured_weekly[w]
        series.append({"week_ending": w, "cum_spent": round(cum, 2)})

    return {
        "id": _clin_num(clin),
        "code": f"CLIN {_clin_num(clin)}",
        "name": clin.get("title"),
        "is_labor": bool(clin.get("is_labor")),
        # The pricing policy governing this line (#76), now applied (#79): it chooses
        # which quantity `spent` holds, whether a funding tripwire and a runway mean
        # anything here, and how revenue and fee are recognised. `known: false` means
        # the type was missing or unreadable, in which case this card is the legacy
        # billings-vs-funding read and says so — not a statement about the award.
        "pricing_policy": policy.payload(),
        # Which quantity `spent` holds on this card, and therefore what every figure
        # derived from it means: "cost" (cost-reimbursement — allowable cost consumes
        # the funding), "billings" (T&M and unknown — hours x loaded rate against the
        # ceiling price), or "price" (fixed price — cost measured against the firm
        # price, a margin read with no funding constraint). Any UI printing a "$ spent"
        # label must read this to label it correctly.
        "measured_against": measured_against,
        # Fixed-price work is margin-managed: no funding tripwire, no runway, no dated
        # hard stop. The flag the Flight Deck switches card shape on.
        "margin_managed": margin_managed,
        "ceiling": ceiling,
        # The binding budget the runway is measured against, and whether it's the
        # funded slice (incremental funding) rather than the full ceiling. The
        # Flight Deck chart draws the "funds run out" marker at `budget`, and since
        # #39 the CLIN card's bar draws the same marker at `funded_frac` along its
        # ceiling track — the two surfaces now agree.
        "budget": round(budget, 2),
        "funded": round(funded, 2) if funded is not None else None,
        "incrementally_funded": incrementally_funded,
        # The single FAR clause that actually governs this CLIN's funding limit (#81).
        # Which of -20 / -22 applies is not a property of the type: Limitation of Cost
        # governs a fully funded cost contract, Limitation of Funds an incrementally
        # funded one, so the policy needs `incrementally_funded` to pick. T&M carries
        # 52.232-7 either way — its limit is the ceiling price, not an allotment — and
        # fixed-price carries None, because it has no limitation-of-funds mechanic at
        # all. `pricing_policy.funding_clauses` above is the *candidate* list; this is
        # the resolved one, and it is the only one anything user-facing may cite.
        # Deliberately not passed into `_funds_exceeded` / `_funded_shortfall_status`:
        # those decide in dollars, this is a pure lookup, and handing them a clause
        # invites a threshold to be derived from it there instead of from the policy.
        "funding_clause": funding_clause,
        # Whether this CLIN's terminal limit is a negotiated not-to-exceed rather than a
        # cost-plus-fee ceiling (#81 part 5) — the T&M case, where the remedy for a
        # breach is a ceiling increase and the clause governing it is 52.232-7. Carried
        # so the tripwire rows can resolve `limited_by` without re-reading the policy.
        "ceiling_is_price": ceiling_is_price,
        # Funding-pace read (#22): obligated vs elapsed-clock fraction, whether
        # funding is keeping pace, and whether a mod is flagged outstanding.
        "funded_frac": round(funded_frac, 4),
        "elapsed_frac": round(elapsed_frac, 4),
        "funding_keeps_pace": funding_keeps_pace,
        "funding_pace_source": pace_source,
        "mod_in_progress": bool(mod_in_progress),
        "spent": round(spent, 2),
        "pct": round(pct, 4),
        # The same spend against the *binding* budget (#39). `pct` is
        # spent/ceiling while `remaining`, `weeks_left` and the status are all
        # spent-vs-budget, so a card printing only `pct` next to a runway shows
        # two numbers with different denominators and no way to reconcile them.
        # Equal to `pct` whenever the CLIN is fully funded, so a reader who
        # doesn't care about the distinction never sees one.
        "pct_budget": round((spent / budget) if budget else 0.0, 4),
        "weekly": round(weekly, 2),
        "remaining": round(remaining, 2),
        # Dollars already spent past the binding budget, when there are any. The
        # honest expression of a negative balance, since runway now floors at 0.
        "overspent": round(-remaining, 2) if remaining < 0 else 0.0,
        # Nulled on margin-managed CLINs alongside `runway_days`: all three answer
        # "when does the money run out", and on fixed price nothing does.
        "weeks_left": (
            None
            if status in ("paused", "unpriced") or margin_managed
            else round(weeks_left, 2)
        ),
        "exhaust_week": (
            None
            if status in ("paused", "unpriced") or margin_managed
            else round(exhaust_week, 2)
        ),
        "runway_days": runway_days,
        # The forward projection as a per-week series, bent around known absence
        # (#85). **Null unless this contract has absence in its remaining weeks** —
        # every figure above is the flat-pace one it has always been, and a reader
        # that ignores this key sees precisely the pre-#85 payload. `BurnChart` draws
        # the polyline when it's here and its original straight line when it isn't.
        "projection": projection,
        # Hard-stop forecast (#23): the date charging gets blocked, which limit
        # produces it, and whether that date is already today or behind us.
        "stop_date": stop_date,
        "stop_reason": stop_reason,
        "stop_date_passed": stop_date_passed,
        "status": status,
        "status_label": _pill(
            status,
            ceiling_breached,
            funds_exceeded,
            margin_managed,
            fee_exhausted,
            ceiling_is_price,
        ),
        # Whether the fee is fully absorbed rather than merely eroding (#81). Carried
        # because `status` alone can't tell the two apart and the pill's own wording is
        # not something a consumer should have to string-match.
        "fee_exhausted": fee_exhausted,
        # Which limit is in jeopardy, so the frontend can label a red `over` the
        # same way this does (and its simulator can too). `ceiling_breached` is a
        # projection; `funds_exceeded` already happened, and outranks it.
        "ceiling_breached": bool(ceiling_breached),
        "funds_exceeded": bool(funds_exceeded),
        "rate_source": source,
        "blended_rate": round(blended, 2) if blended else None,
        # ---- cost, revenue and fee (#77, #79) ------------------------------------
        # `cost` is what the hours consumed, burdened through the indirect pools;
        # `billings` is what the award lets us invoice for them. They are equal by
        # construction when nobody has given us direct rates, which is precisely what
        # `cost_known: false` means — do NOT read margin off them in that state (see
        # rates.py for why that's a refusal and not a gap).
        #
        # `revenue` is what this CLIN earns under its policy and `fee_earned` is
        # `revenue - cost`, always. Reported rather than independently derived, so the
        # three reconcile by definition and no reader can produce a fourth number.
        "cost": round(cost, 2),
        "cost_known": cost_known,
        "billings": round(billings, 2),
        "revenue": round(revenue, 2),
        "fee_earned": round(fee, 2),
        # False on a cost-type CLIN whose award printed no fee figures for #80's engine
        # to earn against, and false anywhere cost is a billing-rate stand-in. When
        # false, `fee_earned` is a structural number that still reconciles but says
        # nothing about profit — read `fee_position` for which half is missing.
        "fee_known": fee_known,
        # None rather than 0.0 when the fee isn't known: a withheld margin is the
        # layered-privacy contract, and a fabricated 0% is the failure it prevents.
        "margin_pct": round(margin_pct, 4) if margin_pct is not None else None,
        # The fixed-price margin position that stands in for the runway (#79): what the
        # price is, what the work has cost, where cost lands at PoP end at the current
        # pace, and whether that projection eats the fee. None on every other policy —
        # cost-type and T&M work keeps its funding read, and emitting a margin position
        # there would invite the two to be compared as if they were the same shape.
        "margin_position": (
            {
                "price": round(ceiling, 2),
                "cost": round(cost, 2),
                "projected_cost": round(projected_cost, 2),
                "projected_margin": round(ceiling - projected_cost, 2),
                "projected_margin_pct": (
                    round((ceiling - projected_cost) / ceiling, 4) if ceiling else None
                ),
                "eroding": bool(
                    ceiling and projected_cost >= _MARGIN_WATCH_FRAC * ceiling
                ),
                # The same withholding as `margin_pct`: at Level 1 cost is a billing
                # stand-in, so these numbers reconcile but are not a profit read.
                "known": cost_known,
            }
            if margin_managed
            else None
        ),
        # The earned-fee position (#80), on the types that have one — CPFF, CPAF, CPIF
        # and FPI. None on FFP (profit is price - cost, reported as `margin_position`),
        # on T&M (the fee is inside the billing rate) and on an unlabelled award.
        "fee_position": _fee_payload(
            fee_position, fee_projected, fee_in_revenue, cost_known
        ),
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
        # Fee is still not subtracted, and #80 does not change that: the earned-fee
        # engine prices fee at the CLIN, off the award's fee figures, which is a
        # different quantity from the fee margin baked into one LCAT's negotiated
        # billing rate. So the gap on a fee-bearing type still includes that fee, and
        # `fee_rate: 0` on each row says so rather than letting the delta read as pure
        # variance.
        "rate_variance": rate_variance,
        # Cause A as a CLIN-level fact (#64): this line item has no usable rate
        # table, so *every* LCAT charged to it prices at the blended rate. One
        # missing continuation sheet, one statement — the UI reads this instead of
        # painting a red cell per person for the same document.
        "rate_table_missing": source != "rate_table",
        # Which kind of rate gap it is (#139): `absent` (no rate lines — a missing
        # continuation sheet, and importing one is the fix) vs `unburdened` (a
        # cost-type award's direct rates per LCAT, with the indirect factors
        # separate — the schedule is in, the burdening is what's missing, and no
        # document fixes it). Both leave `rate_table_missing` true; only the first
        # may be answered with "import the rate schedule".
        "rate_table_state": lcat_match.rate_table_state(clin, burden),
        # Whether that gap priced the quantity this card measures. False on a
        # cost-measured CLIN whose every hour resolved to a declared direct rate —
        # the burn is per-category and the blended rate touched only `billings`.
        # The UI phrases the rate-coverage banner off this, so a CPFF award that
        # printed its own cost buildup stops being told its burn is blended (#144).
        "blended_priced_spend": blended_priced_spend,
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
    # No `budget` truthiness guard: spend is already > 0 here, so a $0-funded
    # CLIN with charges on it has passed its funding by definition.
    if incrementally_funded and spent >= budget:
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
    missing_option_mods = _missing_option_mods(contract, rows)
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

    # Absence settings for the bent projection (#85), read once for the whole sweep.
    # `pop_start` is the calendar date week 1 begins on — the same one `_clock` numbers
    # weeks from — because absence is entered as dates while the engine thinks in week
    # indices, and this is the only place the two are reconciled. A period carrying no
    # PoP dates has no calendar to hang absence off, so `pop_start` stays None and
    # every CLIN falls back to the flat projection it had before this ticket.
    pop_start = _d(clk.get("pop_start"))
    absence_settings = absence_mod.contract_absence(contract)

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
    #    A mixed award — some CLINs named in the ACRN block, some not — is the
    #    case that needs care, and the one this used to get wrong: it pro-rated
    #    the *whole* header obligation across *every* CLIN, so a CLIN that
    #    already carried an exact figure kept it AND had a slice of the same
    #    dollars smeared onto its neighbours. On the burn-demo award (header
    #    $800K obligated, all $800K printed against CLIN 0001, travel and ODC
    #    printed at $0) that invented ~$34.5K of travel funding and ~$27.5K of
    #    ODC funding and summed per-CLIN funded to $862K against $800K
    #    obligated. Dollars already attributed by name are therefore netted out
    #    of the header total first, and only the remainder is pro-rated — across
    #    the unattributed ceilings alone, since the attributed lines have
    #    already been paid for. When nothing is left over the unnamed CLINs get
    #    $0, which is the award's own answer, not a smear.
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
        funding_attribution = "full"
        funding_total_unknown = False
    else:
        period_funded = None
        funding_attribution = "partial" if attributed else "none"
        # The gap #61 named: some CLINs carry an obligation of their own, but the
        # header prints no contract total, so there is no document-backed way to
        # scope a *period* funded figure. `funded_for` still honours the real
        # per-CLIN figures it has, but `period_funded` stays None, which reads
        # downstream as "not incrementally funded" — indistinguishable from a
        # fully-funded contract, which is the one thing this is not known to be.
        # Reported so the caveat can be shown rather than inferred from silence.
        funding_total_unknown = bool(attributed) and obligated is None
        if obligated is not None and active_ceiling:
            available = max(0.0, float(obligated) - prior_consumed)
            if available < active_ceiling:
                period_funded = available
        # Net the by-name obligations out of the period's funded total, and
        # pro-rate what's left over the ceilings that have no figure of their
        # own. Both halves matter: netting stops the same dollars being counted
        # twice, and the narrowed denominator stops the remainder being diluted
        # by ceilings that were already funded explicitly. Floored at zero —
        # attributed dollars can exceed the header total on a stale extraction,
        # and that means "nothing left to spread", not negative funding.
        attributed_funded = sum(float(c["obligated"]) for c in attributed)
        unattributed_ceiling = sum(
            float(c.get("ceiling") or 0) for c in clins if c.get("obligated") is None
        )
        if period_funded is not None and unattributed_ceiling:
            remainder = max(0.0, period_funded - attributed_funded)
            funded_frac = remainder / unattributed_ceiling
        else:
            funded_frac = None

    def funded_for(c):
        """Funded dollars for one CLIN: the award's own obligation to it when
        present, else its pro-rata slice of whatever the period's funded total
        has left after the by-name obligations are netted out."""
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
    # Burdening, resolved once for the whole contract (#144) so the index, every
    # CLIN's resolver and `rate_table_state` all price a direct-rate line the same
    # way. None on a contract with no indirect pools, which is the pre-#144 world.
    burden = burden_fn(cost_model, header)
    rate_index = lcat_match.build_index(clins, burden)
    aliases = lcat_match.parse_aliases(contract.get("lcat_aliases"))
    # Award-fee determinations, per CLIN (#80). Resolved once here rather than per CLIN
    # because routing an unassigned period needs to see every CLIN on the award.
    fee_periods = _fee_periods_by_clin(contract, clins)

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
            burden=burden,
            aliases=aliases,
            cost_model=cost_model,
            pop_start=pop_start,
            absence=absence_settings,
            fee_periods=fee_periods.get(str(c.get("clin"))),
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
                burden=burden,
                aliases=aliases,
                cost_model=cost_model,
                pop_start=pop_start,
                absence=absence_settings,
                fee_periods=fee_periods.get(str(c.get("clin"))),
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
        nl_policy = policy_of(c)
        nl_ceiling_is_price = nl_policy.funding_tripwire == "at_ceiling"
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
                "pricing_policy": nl_policy.payload(),
                # A logged travel or ODC dollar is a cost dollar — there is no rate
                # ladder between the two here, so the measured quantity is cost
                # whatever the CLIN's type says, and there is no margin read to make
                # of it. Both keys are present-and-flat so the lists below can filter
                # labor and non-labor rows on the same field (#79).
                "measured_against": "cost",
                "margin_managed": False,
                "ceiling": ceiling,
                # Binding budget the status is measured against, and whether it's
                # the funded slice rather than the full ceiling (#41).
                "funded": round(funded, 2) if funded is not None else None,
                "budget": round(budget, 2),
                "incrementally_funded": incrementally_funded,
                # The governing clause (#81), resolved the same way labor resolves it.
                # It can be None here while the card still shows a funding status: a
                # travel/ODC CLIN is measured in cost dollars whatever its type says
                # (see `measured_against` above), so an FFP-typed one keeps its funding
                # read but has no limitation-of-funds clause to cite. That is #79's
                # deliberate call about non-labor spend, and the honest answer for a
                # consumer asking "what would I cite?" is nothing, not -22.
                "funding_clause": nl_policy.funding_clause_for(incrementally_funded),
                "spent": round(spent, 2),
                # `pct` stays ceiling-based; `pct_budget` is the same spend against
                # the binding budget the status is actually read off, and
                # `funded_frac` places the funded marker on the ceiling track (#39).
                # All three are present on labor cards too, so the card renders one
                # way for both.
                "pct": round((spent / ceiling) if ceiling else 0.0, 4),
                "pct_budget": round((spent / budget) if budget else 0.0, 4),
                "funded_frac": round(
                    (funded / ceiling) if (funded is not None and ceiling) else 1.0, 4
                ),
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
                # No pace to bend either (#85). Present-and-null so a reader can
                # test one key across labor and non-labor rows alike.
                "projection": None,
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
                # A travel/ODC CLIN on a T&M line carries the same not-to-exceed its
                # labor does, so it resolves the same way (#81 part 5).
                "ceiling_is_price": nl_ceiling_is_price,
                "limited_by": _limited_by(incrementally_funded, nl_ceiling_is_price),
            }
        )

    # An `unpriced` CLIN has no runway (its spend could not be valued), so it can't
    # be the worst-runway hero any more than a `paused` one can — exclude both. A
    # margin-managed (fixed-price) CLIN is excluded for a stronger reason (#79): it has
    # no runway at all, so it cannot be the worst one, and the `or computed` fallback
    # would otherwise hand the hero tile a card whose `runway_days` is None. On an
    # all-fixed-price contract `worst` is None and the hero is absent — correct, and
    # the Flight Deck renders the margin hero in its place.
    active = [
        c
        for c in computed
        if c["status"] not in ("paused", "unpriced") and not c["margin_managed"]
    ] or [c for c in computed if not c["margin_managed"]]
    worst = min(active, key=lambda c: c["exhaust_week"] or 1e9) if active else None

    labor_ceiling = sum(c["ceiling"] for c in computed)
    total_ceiling = labor_ceiling + sum(c["ceiling"] for c in nl_cards)
    # The binding budget rolled up the same way (#39): the funded slice where a CLIN
    # is incrementally funded, its ceiling where it isn't. Summed per CLIN rather
    # than taken from `period_funded` so it reconciles line-by-line with the cards.
    total_budget = sum(c["budget"] for c in computed + nl_cards)
    # Both feeds roll into burn: labor hours × rate, plus logged non-labor actuals.
    total_spent = sum(c["spent"] for c in computed) + sum(c["spent"] for c in nl_cards)
    total_weekly = sum(c["weekly"] for c in computed)
    # Total cost of the labor charged (#77). Non-labor CLINs are already actuals — a
    # logged travel dollar is a cost dollar — so they roll in unburdened. Reported
    # next to `spent`, never instead of it.
    total_cost = sum(c["cost"] for c in computed) + sum(c["spent"] for c in nl_cards)
    # Revenue and fee rolled up the same way (#79). Non-labor actuals are cost dollars
    # that pass straight through to a reimbursement, so they contribute equally to both
    # and carry no fee — which keeps `total_fee == total_revenue - total_cost` true at
    # the contract level exactly as it is per CLIN.
    total_revenue = sum(c["revenue"] for c in computed) + sum(
        c["spent"] for c in nl_cards
    )
    total_fee = total_revenue - total_cost
    cost_model_out = cost_model or rates.CostModel()

    tripwires = [
        {
            "code": c["code"],
            "name": c["name"],
            "pct": c["pct"],
            # Spend against whichever limit this row is about (#39). A funding-
            # limited tripwire that prints a ceiling percentage contradicts its own
            # headline — "40% burned, past its obligated $1.4M".
            "pct_budget": c["pct_budget"],
            "exhaust_week": c["exhaust_week"],
            "runway_days": c["runway_days"],
            "weeks_early": round(tw - (c["exhaust_week"] or tw)),
            # What the CLIN runs out of first: its funded dollars (incremental
            # funding) or the full ceiling. Drives whether the UI says "funding
            # runs out" vs "blows the ceiling".
            "limited_by": _limited_by(c["incrementally_funded"], c["ceiling_is_price"]),
            # And the clause that governs the limit `limited_by` names (#81), so a
            # banner or a letter built from this row cites what actually applies
            # instead of assuming -22. None where the type has no funding clause.
            "funding_clause": c["funding_clause"],
            # The dated hard stop behind this tripwire (#23), so the banner can say
            # *when* rather than only how many weeks early. `limited_by` above is
            # already the same value `stop_reason` carries, so it isn't repeated.
            "stop_date": c["stop_date"],
            "stop_date_passed": c["stop_date_passed"],
            "funded": c["funded"],
            "budget": c["budget"],
            # Enough to tell an obligation gap from an overrun without a second
            # request. `limited_by` says which limit binds, but not whether the
            # *ceiling* is also going — and those two want opposite remedies (a mod
            # vs. fewer hours). The suggestion layer used to get this from the heat
            # payload's solved plan, which is fetched after burn and can fail on its
            # own; on that path a funding gap was answered with a staffing cut. The
            # remedy must not depend on a second fetch, so the facts ride here.
            "ceiling": c["ceiling"],
            "ceiling_breached": c["ceiling_breached"],
            # Realized dollars past the obligation — the "already at risk" figure.
            "overspent": c["overspent"],
        }
        # Non-labor CLINs over their binding budget are Limitation of Funds
        # problems too (#41), so they roll into the red list alongside labor.
        # Their `exhaust_week` is None (realized read, no forward pace).
        for c in computed + nl_cards
        # Every row in this list is a funding story — the banner says when the money
        # runs out and which limit does it. A red fixed-price CLIN is red for an
        # unrelated reason (cost is eating the fee), so it goes to `margin_alerts`
        # instead of being described with funding vocabulary it has no version of.
        if c["status"] == "over" and not c["margin_managed"]
    ]

    # Margin erosion on fixed-price work (#79) — the list that stands in for the
    # funding tripwire on these CLINs. Same severity ladder (`over` = the fee is gone,
    # `watch` = projected to eat it), no dates and no runway, because neither exists
    # here. Kept as its own list rather than a flag on `tripwires` so no existing
    # reader picks these up and prints "funding runs out" over them.
    margin_alerts = [
        {
            "code": c["code"],
            "name": c["name"],
            "status": c["status"],
            "pct": c["pct"],
            "policy": c["pricing_policy"]["code"],
            **c["margin_position"],
        }
        for c in computed
        if c["margin_managed"] and c["status"] in ("over", "watch")
    ]

    # Fee erosion on cost-reimbursement work (#80) — the cost-type counterpart to
    # `margin_alerts`. Driven off `absorbed` on the *projected* position, never off
    # `target_delta`: on CPAF an undetermined award pool makes at-completion fee sit
    # below target from day one, and that is the normal state of a CPAF contract, not an
    # alert. `absorbed` is only ever fee that *cost* has taken — the overrun eating a
    # fixed fee (52.216-8's `contractor_fee_first`) or the share ratio walking an
    # incentive fee down — which is the thing worth waking someone for.
    fee_alerts = [
        {
            "code": c["code"],
            "name": c["name"],
            "policy": c["pricing_policy"]["code"],
            "basis": c["fee_position"]["basis"],
            # "over" = the fee is gone; "watch" = the projection is eating it.
            "status": (
                "over" if c["fee_position"]["projected"]["exhausted"] else "watch"
            ),
            "target": c["fee_position"]["target"],
            "earned": c["fee_position"]["earned"],
            "projected": c["fee_position"]["projected"]["at_completion"],
            # The headline: "projected fee $312K against a $400K target — the overrun
            # has cost $88K of fee."
            "fee_lost": c["fee_position"]["projected"]["absorbed"],
            "overrun": c["fee_position"]["projected"]["overrun"],
        }
        for c in computed
        if c["fee_position"]
        and c["fee_position"]["known"]
        and c["fee_position"]["projected"]
        and c["fee_position"]["projected"]["absorbed"] > 0
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
            # Same reason as the tripwire list (#39): this banner's sentence is
            # about the budget, so the badge beside it has to be too.
            "pct_budget": c["pct_budget"],
            "exhaust_week": c["exhaust_week"],
            "weeks_slack": round((c["exhaust_week"] or tw) - tw),
            "budget": c["budget"],
            "spent": c["spent"],
            "projected_unspent": round(
                max(0.0, c["budget"] - (c["spent"] + c["weekly"] * weeks_remaining)), 2
            ),
            "limited_by": _limited_by(c["incrementally_funded"], c["ceiling_is_price"]),
        }
        for c in computed
        # Fixed-price CLINs can't reach `under` (their margin bands don't emit it), so
        # this guard is belt-and-braces: under-burn means "money you were given is
        # going unspent", and on fixed price unspent money is margin you keep.
        if c["status"] == "under" and not c["margin_managed"]
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
            # The clause behind this row (#81). This is the list #25's funding letter
            # is generated from, so it is the one place a wrong citation reaches a
            # contracting officer — an amber funding row is funding-limited by
            # definition, but *which* clause limits it still depends on the type.
            "funding_clause": c["funding_clause"],
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
        # A fixed-price CLIN can never be `funding` — its policy declares
        # `funding_tripwire: "none"` and `_compute_clin` never reaches the funded-
        # shortfall branch for it. Filtered anyway so the invariant is stated where the
        # list is built, not only where the status is chosen.
        if c["status"] == "funding" and not c["margin_managed"]
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
    #
    # A cost-measured CLIN that priced every hour from a declared direct rate keeps
    # its entry only while it still has something to offer (#144). This list is an
    # alert group, and the alert is "the figures you are looking at are blended" —
    # on those CLINs they are not: `spent` is `cost`, resolved per LCAT from the
    # award's own buildup, and the blended rate touched only `billings`.
    #
    # So the split is by remedy, not by severity:
    #   * `absent` — the continuation sheet really never landed, and importing one
    #     is a real fix (it is what makes the allocation matrix mappable). The entry
    #     stays and carries `blended_priced_spend` so the banner can drop the false
    #     claim about the money while keeping the import.
    #   * `unburdened` — the schedule is already in, there is no document to fetch,
    #     and the money is right. Nothing is left to say, so nothing is said. This is
    #     the state a CPFF award whose cost buildup #138 stored lands in.
    # Either way the gap survives on the CLIN card (`rate_table_missing`,
    # `rate_table_state`), where the mapping story belongs.
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
            # `absent` or `unburdened` (#139) — the banner phrases itself off this
            # and only offers an import on the half a document can answer.
            "rate_table_state": c["rate_table_state"],
            # Whether the blended rate priced what this CLIN reports (#144). False
            # means the burn is per-category and only the billing side is missing,
            # so the banner states the document gap without misdescribing the money.
            "blended_priced_spend": c["blended_priced_spend"],
        }
        for c in computed
        if c["rate_table_missing"]
        and c["charged_rows"]
        and c["status"] != "unpriced"
        and (
            c["blended_priced_spend"]
            or c["rate_table_state"] == lcat_match.TABLE_ABSENT
        )
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
            # How the funded dollars above were attributed: "full" (every active
            # CLIN carried its own obligation), "partial" (some did, the rest took
            # a pro-rata slice of the netted remainder) or "none" (header total
            # only). `funding_total_unknown` is the honest-failure case within
            # "partial": per-CLIN figures exist but no header total scopes them, so
            # no funding limit could be set for the period at all (#61).
            "funding_attribution": funding_attribution,
            "funding_total_unknown": funding_total_unknown,
            # True when no CLIN carried a period label, so the CLIN set could not
            # be scoped and every period's ceiling is in these totals.
            "clin_scope": clin_scope,
            # False when the synced weeks don't overlap this PoP at all, so
            # charges could not be date-scoped to the period (see
            # _effective_window) — the burn figures are the whole feed.
            "pop_scoped": window_applied,
            "past_pop": past_pop,
            "weeks_overrun": clk["weeks_overrun"],
            # The contract's holiday calendar and per-person absences (#85). Echoed
            # on the payload so the Flight Deck can *name* what bent a CLIN's line
            # rather than showing an unexplained kink, and so the allocation matrix
            # seeds its simulator from the same list the engine projected against.
            "absence": absence_settings,
            # How funding pace was judged: from ingested SF-30 obligation history
            # (dollars landing vs. burned) or the funded-vs-elapsed proxy.
            "funding_pace_source": (
                "obligation_history" if pace_override is not None else "proxy"
            ),
            "obligation_weekly": obligation_weekly,
            # Positive performance in an unexercised option is a missing-document
            # signal, never permission to synthesize option funding.
            "missing_option_mods": missing_option_mods,
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
            # Contract-level mirror of the per-CLIN pair (#39). The hero tile reads
            # a runway measured against funded dollars, so "Contract burned" needs
            # the funded denominator available next to the ceiling one.
            "budget": round(total_budget, 2),
            "pct_budget": (
                round(total_spent / total_budget, 4) if total_budget else 0.0
            ),
            "incrementally_funded": any(
                c["incrementally_funded"] for c in computed + nl_cards
            ),
            "weekly": round(total_weekly, 2),
            "labor_count": len(computed),
            # Cost of the same work, when it's independently known (#77).
            # `cost_known` false means this equals `spent` because no direct rates
            # were provided — read `contract.cost_model.margin_available` before
            # putting a margin anywhere near a user.
            "cost": round(total_cost, 2),
            "cost_known": all(c["cost_known"] for c in computed) if computed else False,
            # Revenue and fee across the contract (#79). `fee` is
            # `revenue - cost` here exactly as it is per CLIN, so the three
            # reconcile at both levels. `fee_known` false means the fee is a
            # structural figure — either cost is a billing stand-in, or a
            # cost-type CLIN's award printed no fee figures for #80 to earn against.
            "revenue": round(total_revenue, 2),
            "fee": round(total_fee, 2),
            "fee_known": all(c["fee_known"] for c in computed) if computed else False,
        },
        "hero": (
            {
                "days": worst["runway_days"],
                "clin": worst["code"],
                "status": worst["status"],
                "limited_by": _limited_by(
                    worst["incrementally_funded"], worst["ceiling_is_price"]
                ),
                # The clause governing the hero's limit (#81).
                "funding_clause": worst["funding_clause"],
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
        # Fixed-price margin erosion (#79). The fixed-price counterpart to `tripwires`:
        # every CLIN whose projected cost is at or past its price. Empty on contracts
        # with no fixed-price lines, which is most of them today.
        "margin_alerts": margin_alerts,
        # Cost-type fee erosion (#80). Same shape of story as `margin_alerts` — money
        # the company is losing rather than money running out — kept separate because
        # these rows carry a fee position and those carry a margin position.
        "fee_alerts": fee_alerts,
        "data_quality": data_quality,
        # Rate-line coverage (#64), split by what fixes it: `rate_gaps` needs a
        # document (import the rate schedule), `lcat_gaps` needs a decision (map
        # this LCAT to that rate line). Neither gates `all_clear` — see rate_gaps.
        "rate_gaps": rate_gaps,
        "lcat_gaps": lcat_gaps,
        # A contract the engine could not fully price is not "all clear" — an
        # unpriced CLIN gates it just like a tripwire (#40).
        # A fixed-price CLIN eating its fee gates `all_clear` too (#79): it is not a
        # funding problem, but it is money the company is losing, and it is exactly the
        # thing this contract type is at risk of. A cost-type CLIN projected to lose fee
        # to its own overrun gates it for the same reason (#80) — that is the loss a PM
        # otherwise does not see until year end.
        "all_clear": (
            len(tripwires) == 0
            and len(underburn) == 0
            and len(funding) == 0
            and len(data_quality) == 0
            and len(margin_alerts) == 0
            and len(fee_alerts) == 0
        ),
        "sync": {
            "rows": len(rows),
            "people": len({r.get("employee_id") for r in rows if r.get("employee_id")}),
            "weeks": len({r.get("week_ending") for r in rows if r.get("week_ending")}),
            "latest_week": clk["latest_week"],
            # The vantage point every forward number on this payload is measured from,
            # and how old it is. `runway_days`, `exhaust_week` and `stop_date` are all
            # anchored to `_anchor_date` — the newest synced timesheet week — not to
            # today, because pace can only be measured from hours that have actually
            # been reported. That is the right denominator, but it makes the day counts
            # *as-of* figures rather than live countdowns: they move when a sync lands,
            # not when the clock ticks. With weekly timekeeping the gap is a few days
            # and nobody needs to think about it; on a contract that hasn't synced in
            # months the same "99 days of runway" is a claim from a season ago, and a
            # reader has no way to know unless the payload says so. So it says so, and
            # every surface printing one of those numbers labels it "as of <date>".
            "as_of": anchor.isoformat(),
            "data_age_days": (date.today() - anchor).days,
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
                # The portfolio card carries the same runway the Flight Deck hero
                # does, so it inherits the same two-denominator problem and the same
                # fix (#39): the funded read alongside the ceiling read.
                "budget": t["budget"],
                "pct_budget": t["pct_budget"],
                "incrementally_funded": t["incrementally_funded"],
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
                    # Margin wording only when *every* red line is fixed-price (#79).
                    # A mixed contract has a genuine funding breach to report and that
                    # is the more urgent of the two, so the funding label wins; on an
                    # all-fixed-price card "Over ceiling" would name a limit that does
                    # not constrain anything.
                    bool(red) and all(x.get("margin_managed") for x in red),
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
