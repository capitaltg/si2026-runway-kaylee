"""Indirect-cost buildup: what an hour *costs* us, next to what we bill (#77).

A labor hour used to be one number. `_rate_resolver` returns the fully-burdened
billing rate off the CLIN's rate schedule and the engine multiplied hours by it —
and that single number was simultaneously used as what we bill the government
(correct), what the work cost us (it is not; it includes fee), and the basis for
every tripwire and runway figure in the app. With cost and price as the same
variable, Runway cannot compute margin, cannot compute earned fee, and cannot tell
you which indirect pool is over-running.

This module separates them, in the form the numbers actually arrive in:

    Direct labor              $ 62.00 /hr   per LCAT, or per person
    + Fringe        32%         19.84       applied to direct labor
    = Labor + fringe            81.84
    + Overhead      45%         36.83       applied to labor + fringe
    = Burdened cost            118.67
    + G&A           12%         14.24       applied to total cost input base
    = Total cost               132.91
    + Fee            8%         10.63       from the pricing policy (#76), not here
    = Billed price            $ 143.54 /hr  reconciles to the rate schedule

**Everything here is optional, by design.** This is the load-bearing product
decision on the ticket, so it lives in code rather than only in a doc: a
contractor should get real value from an award PDF alone, and should never be
forced to hand us payroll to find out whether their contract is overrunning. So
the cost side degrades in three declared tiers (`LEVEL_*` / `SOURCE_*`), and
whenever cost is not independently known the payload says so instead of quietly
presenting billing dollars as cost:

  Level 1 — zero setup. Contract documents only. Burn, PoP clock, CLIN exhaustion
            and every tripwire work exactly as they do today. Cost falls back to
            the negotiated billing rate, flagged `negotiated_fallback`, and margin
            is *withheld*, not estimated.
  Level 2 — three company percentages (fringe / OH / G&A) plus LCAT direct rates.
            Real margin, with no employee named and no payroll file uploaded.
  Level 3 — per-person direct rates (#69's people directory). True
            cost-to-complete. Opt-in; the ladder below already reads it, so #69
            only has to supply the roster.

Two rules, the same posture `pricing.py` and `lcat.py` take:

**Never invent a cost.** If the inputs for a tier are absent the resolver drops to
the next one and *names* the tier it used. There is no default salary, no assumed
wrap rate, no "typical" fringe percentage anywhere in this file.

**Reconcile, never pick.** A negotiated loaded rate that doesn't equal
`direct x (1+fringe) x (1+OH) x (1+G&A) x (1+fee)` is real and common — rates were
negotiated at a prior year's indirects, or the price was discounted to win. So
`variance` reports both numbers and the gap and lets the user decide which one
bills. Silently trusting either one is how a tool loses an accountant's confidence
permanently.

Fee is deliberately absent. Fee rate and structure are a property of the contract
*type* and live on the pricing policy (#76), which is what lets one rate set price
work on an FFP and a CPFF contract. The buildup here stops at total cost; #80 earns
the fee and #79 is where the engine starts choosing between cost and revenue.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from . import lcat as lcat_match

# The three indirect pools, in application order. Order is the whole point: each
# pool applies to a base that includes the pools before it, so computing them in
# any other order silently changes the answer.
FRINGE = "fringe"
OVERHEAD = "overhead"
GNA = "gna"
POOLS = (FRINGE, OVERHEAD, GNA)

POOL_LABELS = {FRINGE: "Fringe", OVERHEAD: "Overhead", GNA: "G&A"}

# Which base each pool applies to, as a govcon accounting system would state it.
# Stored per pool on the rate set (a real DCAA-approved structure can differ — G&A
# on a value-added base excluding subcontracts, for instance) but these are the
# conventional defaults, and the only ones the arithmetic below implements.
BASE_DIRECT = "direct_labor"
BASE_LABOR_FRINGE = "labor_plus_fringe"
BASE_TOTAL_COST_INPUT = "total_cost_input"
DEFAULT_BASES = {
    FRINGE: BASE_DIRECT,
    OVERHEAD: BASE_LABOR_FRINGE,
    GNA: BASE_TOTAL_COST_INPUT,
}

# Where a cost rate came from. Reported on every CLIN so a margin figure can never
# be mistaken for one grounded in payroll, and so a user who declined to share
# salaries can see exactly what the app is doing instead.
SOURCE_EMPLOYEE = "employee_direct"  # Level 3: this person's own direct rate
SOURCE_LCAT = "lcat_direct"  # Level 2: their labor category's direct rate
SOURCE_NEGOTIATED = "negotiated_fallback"  # Level 1: billing rate stands in
SOURCE_NONE = "none"  # nothing to price the hour with at all

# Capability level, derived from what the user has actually provided. Reported on
# the contract so the UI can hide the margin surfaces at Level 1 rather than
# render them full of billing dollars relabelled as cost.
LEVEL_BILLING_ONLY = 1
LEVEL_CATEGORY_COST = 2
LEVEL_PERSON_COST = 3

# Rate status. A provisional rate is the one you bill at during the year; the
# actual is what the incurred-cost submission settles to, and the difference is a
# real receivable or payable. Carried from day one even though #87 is the ticket
# that trues them up — retrofitting a status onto stored history later means
# recomputing every number derived from it.
PROVISIONAL = "provisional"
ACTUAL = "actual"


@dataclass(frozen=True)
class Pool:
    """One indirect pool: a rate, the base it applies to, and whether the rate is
    provisional or settled."""

    name: str
    rate: float  # 0.32 for 32%
    base: str = BASE_DIRECT
    status: str = PROVISIONAL

    def payload(self) -> dict:
        return {
            "name": self.name,
            "label": POOL_LABELS.get(self.name, self.name),
            "rate": round(self.rate, 6),
            "base": self.base,
            "status": self.status,
        }


@dataclass(frozen=True)
class Buildup:
    """One hour, layered. Every intermediate subtotal is kept because the layers are
    what a PM argues with — "our overhead is eating this contract" is a claim about
    one line of this, and a single total cost figure can't answer it."""

    direct: float
    fringe: float
    overhead: float
    gna: float

    @property
    def labor_plus_fringe(self) -> float:
        return self.direct + self.fringe

    @property
    def burdened(self) -> float:
        return self.labor_plus_fringe + self.overhead

    @property
    def total_cost(self) -> float:
        return self.burdened + self.gna

    def payload(self) -> dict:
        return {
            "direct": round(self.direct, 4),
            "fringe": round(self.fringe, 4),
            "overhead": round(self.overhead, 4),
            "gna": round(self.gna, 4),
            "labor_plus_fringe": round(self.labor_plus_fringe, 4),
            "burdened": round(self.burdened, 4),
            "total_cost": round(self.total_cost, 4),
        }


def normalize_fiscal_year(label) -> Optional[str]:
    """A stored fiscal-year label as a comparable four-digit year string.

    The same year reaches us written several ways — ingest files rates under the
    bare `"2026"` it derives from the award's effective date, a user typing into the
    rates panel writes `"FY26"`, and a rate agreement PDF may say `"FY 2026"`. They
    are one year and have to sort and match as one, or a contract ends up holding
    two rate sets that are really the same set entered twice.
    """
    text = str(label or "").strip()
    if not text:
        return None
    m = re.search(r"(\d{4}|\d{2})\s*$", text)
    if not m:
        return None
    digits = m.group(1)
    # Two digits are a century-abbreviated FY ("FY26"), never a year 26 AD.
    return digits if len(digits) == 4 else f"20{digits}"


def fiscal_year_of(iso_date) -> Optional[str]:
    """The federal fiscal year an ISO date falls in — FY runs Oct 1 to Sep 30, so
    October onward belongs to the next calendar year's FY. None when the date is
    absent or unparseable."""
    try:
        d = date.fromisoformat(str(iso_date or "")[:10])
    except ValueError:
        return None
    return str(d.year + 1 if d.month >= 10 else d.year)


@dataclass(frozen=True)
class RateSet:
    """The indirect rates in force for one fiscal year, contract- or company-scoped.

    Fiscal-year-keyed from day one. #87 is the ticket that uses the second year, but
    a rate set without a year cannot be trued up, and adding the key later means
    every stored number has to be recomputed against a year nobody recorded.
    """

    fiscal_year: Optional[str] = None
    pools: Tuple[Pool, ...] = ()
    scope: str = "contract"  # "contract" | "company"

    def rate_of(self, name: str) -> Optional[Pool]:
        for p in self.pools:
            if p.name == name:
                return p
        return None

    @property
    def complete(self) -> bool:
        """All three pools present. A partial set still computes — a company with no
        separate overhead pool is unusual but not wrong — but `complete` is what the
        UI reads to say whether the buildup is the full picture."""
        return all(self.rate_of(p) is not None for p in POOLS)

    @property
    def usable(self) -> bool:
        """Enough to burden a direct rate at all. One pool is enough to be better
        than nothing; zero pools means the user is at Level 1 whatever else they
        have provided."""
        return any(self.rate_of(p) is not None for p in POOLS)

    @property
    def status(self) -> str:
        """The set's status is the weakest of its pools: one provisional rate makes
        the whole derived cost provisional, because it feeds the same total."""
        return (
            PROVISIONAL
            if any(p.status != ACTUAL for p in self.pools) or not self.pools
            else ACTUAL
        )

    def payload(self) -> dict:
        return {
            "fiscal_year": self.fiscal_year,
            "scope": self.scope,
            "status": self.status,
            "complete": self.complete,
            "pools": [p.payload() for p in self.pools],
        }


def burden(direct: float, rate_set: RateSet) -> Buildup:
    """Layer the indirect pools onto a direct rate, in application order.

    A missing pool contributes zero rather than blocking the buildup: a rate set
    with fringe and G&A but no overhead pool is a real (if unusual) structure, and
    refusing to compute would hide the two rates the user did give us.
    """
    fringe_pool = rate_set.rate_of(FRINGE)
    oh_pool = rate_set.rate_of(OVERHEAD)
    gna_pool = rate_set.rate_of(GNA)

    fringe = direct * (fringe_pool.rate if fringe_pool else 0.0)
    labor_fringe = direct + fringe

    # Each pool applies to the base its own row declares. Anything unrecognised
    # falls back to that pool's conventional base rather than to zero — a typo in a
    # base name must not silently delete an overhead pool from the cost.
    oh_base = {
        BASE_DIRECT: direct,
        BASE_LABOR_FRINGE: labor_fringe,
    }.get(oh_pool.base if oh_pool else "", labor_fringe)
    overhead = oh_base * (oh_pool.rate if oh_pool else 0.0)

    gna_base = {
        BASE_DIRECT: direct,
        BASE_LABOR_FRINGE: labor_fringe,
        BASE_TOTAL_COST_INPUT: labor_fringe + overhead,
    }.get(gna_pool.base if gna_pool else "", labor_fringe + overhead)
    gna = gna_base * (gna_pool.rate if gna_pool else 0.0)

    return Buildup(direct=direct, fringe=fringe, overhead=overhead, gna=gna)


@dataclass(frozen=True)
class CostResolution:
    """What one hour cost, and how confident we're entitled to be about it.

    `known` is the gate every margin surface in the app reads. False means the cost
    figure is the billing rate standing in for a cost we were never given — usable
    for burn, useless for margin, and never to be presented as margin.
    """

    rate: Optional[float]
    source: str
    known: bool
    buildup: Optional[Buildup] = None
    # The direct rate this was built from, when there was one. Kept separate from
    # `rate` (the burdened total) so "our direct labor is X and our burden is Y" is
    # answerable without re-deriving it.
    direct: Optional[float] = None

    def payload(self) -> dict:
        return {
            "rate": round(self.rate, 4) if self.rate is not None else None,
            "source": self.source,
            "known": self.known,
            "direct": round(self.direct, 4) if self.direct is not None else None,
            "buildup": self.buildup.payload() if self.buildup else None,
        }


@dataclass
class CostModel:
    """The cost side of the engine for one contract: the rate set in force, the
    direct rates on offer, and the ladder that turns (person, LCAT, billing rate)
    into a cost.

    Built once per `burn.compute` and passed down, for the same reason the pricing
    policy and the LCAT index are: resolving a cost needs contract-scoped facts that
    `_compute_clin` cannot see from a single CLIN.
    """

    rate_set: RateSet = field(default_factory=RateSet)
    # Normalised LCAT key -> direct $/hr. Keyed through `lcat.normalize` so a direct
    # rate entered as "Sr. Cyber SME" answers for a timesheet's "Senior Cyber SME",
    # exactly as the billing side resolves (#64).
    lcat_direct: Dict[str, float] = field(default_factory=dict)
    # employee_id -> direct $/hr (Level 3). Empty until #96 gives these rows an entry
    # point in the rates view; the ladder already reads them, so that ticket adds no
    # engine work. Note it is #96 and deliberately NOT the people directory (#69):
    # a direct rate is a fiscal-year-scoped pricing input that happens to be keyed
    # per person, not a property of the person, and Runway does not keep
    # compensation on a person's record.
    employee_direct: Dict[str, float] = field(default_factory=dict)

    @property
    def level(self) -> int:
        """Which of the three tiers this contract is actually operating at."""
        if not self.rate_set.usable:
            # Direct rates without any indirect pool cannot produce a cost that
            # differs meaningfully from a discounted billing rate, so it isn't a
            # margin tier — saying otherwise would oversell what the user gave us.
            return LEVEL_BILLING_ONLY
        if self.employee_direct:
            return LEVEL_PERSON_COST
        if self.lcat_direct:
            return LEVEL_CATEGORY_COST
        return LEVEL_BILLING_ONLY

    @property
    def margin_available(self) -> bool:
        """Whether any margin figure on this contract would mean anything. The single
        flag the UI gates the profitability surfaces on (#82), and the reason a
        Level-1 user sees a complete, honest dashboard with one report withheld
        rather than a broken one full of zeros."""
        return self.level > LEVEL_BILLING_ONLY

    def cost_for(
        self,
        lcat: Optional[str],
        billing_rate: Optional[float],
        employee_id: Optional[str] = None,
    ) -> CostResolution:
        """The cost of one hour, down the fallback ladder.

        Order — most specific wins, and each rung is *declared*, never blended:
          1. this person's own direct rate (Level 3)
          2. their labor category's direct rate (Level 2)
          3. the negotiated billing rate, standing in (Level 1) — flagged
             `negotiated_fallback` with `known=False`, because billing dollars are
             not cost and this is the honest way to say we were not told
          4. nothing at all — the CLIN could not be priced either way (#40)
        """
        direct = None
        source = SOURCE_NONE
        if employee_id and employee_id in self.employee_direct:
            direct, source = self.employee_direct[employee_id], SOURCE_EMPLOYEE
        else:
            key = lcat_match.normalize(lcat)
            if key and key in self.lcat_direct:
                direct, source = self.lcat_direct[key], SOURCE_LCAT

        if direct is not None:
            built = burden(direct, self.rate_set)
            return CostResolution(
                rate=built.total_cost,
                source=source,
                known=True,
                buildup=built,
                direct=direct,
            )
        if billing_rate is not None:
            # The declared fallback. Cost == price here by construction, which is
            # exactly why `known=False`: margin off this number would always be
            # zero, and a zero margin presented as a fact is worse than no margin.
            return CostResolution(
                rate=float(billing_rate), source=SOURCE_NEGOTIATED, known=False
            )
        return CostResolution(rate=None, source=SOURCE_NONE, known=False)

    def payload(self) -> dict:
        return {
            "level": self.level,
            "margin_available": self.margin_available,
            "rate_set": self.rate_set.payload(),
            "lcat_direct_count": len(self.lcat_direct),
            "employee_direct_count": len(self.employee_direct),
        }


def variance(
    derived_cost: Optional[float],
    negotiated_loaded: Optional[float],
    fee_rate: float = 0.0,
) -> Optional[dict]:
    """Reconcile a derived rate against the negotiated one the schedule prints.

    The correctness test for this whole module: the rate schedule prints a loaded
    rate, the buildup derives one, and **where they disagree Runway must say so
    rather than pick.** Returns None when there is nothing to compare or the two
    agree to the cent.

    `fee_rate` comes from the pricing policy (#76) when the contract type carries
    one, so the comparison is like-for-like: a negotiated *billing* rate includes
    fee and a derived *cost* does not.
    """
    if derived_cost is None or not negotiated_loaded:
        return None
    derived_price = derived_cost * (1.0 + (fee_rate or 0.0))
    delta = negotiated_loaded - derived_price
    if abs(delta) < 0.005:
        return None
    return {
        "derived_cost": round(derived_cost, 4),
        "fee_rate": round(fee_rate or 0.0, 6),
        "derived_price": round(derived_price, 4),
        "negotiated_rate": round(float(negotiated_loaded), 4),
        "delta": round(delta, 4),
        # Positive: the award pays more than our buildup implies (margin above the
        # negotiated fee). Negative: we are billing below our own cost-plus-fee,
        # which is the case an accountant needs to see the day it appears.
        "direction": "above_buildup" if delta > 0 else "below_buildup",
        "pct": round(delta / negotiated_loaded, 6),
    }


def model_from_rows(
    pool_rows: List[dict],
    direct_rows: List[dict],
    fiscal_year: Optional[str] = None,
    scope: str = "contract",
) -> CostModel:
    """Build a `CostModel` from stored rows (`db.get_rate_model`).

    Tolerant by design: a malformed pool or direct-rate row is skipped rather than
    raised, because a bad row in a hand-maintained table must not take down a burn
    calculation for a whole contract.
    """
    pools = []
    for r in pool_rows or []:
        name = str(r.get("pool") or r.get("name") or "").strip().lower()
        if name not in POOLS or r.get("rate") is None:
            continue
        try:
            rate = float(r["rate"])
        except (TypeError, ValueError):
            continue
        pools.append(
            Pool(
                name=name,
                rate=rate,
                base=str(r.get("base") or DEFAULT_BASES[name]),
                status=ACTUAL if r.get("status") == ACTUAL else PROVISIONAL,
            )
        )

    lcat_direct: Dict[str, float] = {}
    employee_direct: Dict[str, float] = {}
    for r in direct_rows or []:
        try:
            rate = float(r.get("rate"))
        except (TypeError, ValueError):
            continue
        emp = (r.get("employee_id") or "").strip()
        if emp:
            employee_direct[emp] = rate
            continue
        key = lcat_match.normalize(r.get("lcat"))
        if key:
            lcat_direct[key] = rate

    year = fiscal_year or next(
        (r.get("fiscal_year") for r in (pool_rows or []) if r.get("fiscal_year")), None
    )
    return CostModel(
        rate_set=RateSet(fiscal_year=year, pools=tuple(pools), scope=scope),
        lcat_direct=lcat_direct,
        employee_direct=employee_direct,
    )


@dataclass
class RateSchedule:
    """Every fiscal year of rates a contract holds, so an hour is priced by the set
    that covered the week it was worked (#158).

    The bug this exists to close: the rows were stored with a fiscal year and then
    folded into one map keyed by pool alone, so a contract holding FY25 and FY26
    rates priced *all* of its hours with whichever row the merge happened to see
    last. A contract crossing October 1 — which is most of them, since the federal
    year turns over mid-period-of-performance — repriced its whole history at one
    year's overhead.

    Delegates every other attribute to `base`, so callers that legitimately have no
    charge date (the rates panel, the LCAT buildup, the payload) keep reading a
    plain `CostModel` and only the row-level pricing loop opts into `for_week`.
    """

    base: CostModel
    by_year: Dict[str, CostModel] = field(default_factory=dict)

    def for_year(self, fiscal_year) -> CostModel:
        """The model covering a fiscal year, with declared fallbacks.

        Order, most defensible first:
          1. that exact year
          2. the closest *earlier* year we hold — rates carry forward until they are
             superseded, which is what a contractor actually bills on while the new
             year's provisional rates are still being negotiated (FAR 42.704)
          3. the closest later year, so hours predating the first set we were given
             are still costed rather than dropping to Level 1
          4. `base` — undated rows, or none at all
        """
        year = normalize_fiscal_year(fiscal_year)
        if not year or not self.by_year:
            return self.base
        if year in self.by_year:
            return self.by_year[year]
        earlier = [y for y in self.by_year if y < year]
        if earlier:
            return self.by_year[max(earlier)]
        later = [y for y in self.by_year if y > year]
        if later:
            return self.by_year[min(later)]
        return self.base

    def for_week(self, week_ending) -> CostModel:
        """The model covering a timesheet week. The week's ending date decides the
        year: a week straddling September 30 is a rounding question worth one line
        of arithmetic at most, and splitting it would imply a precision the weekly
        timesheet grain does not have."""
        return self.for_year(fiscal_year_of(week_ending))

    @property
    def fiscal_years(self) -> List[str]:
        return sorted(self.by_year)

    def __getattr__(self, name):
        # Only reached for attributes RateSchedule does not define itself, so the
        # dataclass fields above never come through here. Dunders are refused
        # outright: forwarding `__deepcopy__` or `__getstate__` to `base` before
        # `base` is set recurses forever.
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.base, name)


def schedule_from_rows(
    pool_rows: List[dict],
    direct_rows: List[dict],
    scope: str = "contract",
) -> RateSchedule:
    """Build a `RateSchedule` from the full multi-year rows (`db.get_rate_rows`).

    Undated rows are folded into every year rather than kept as a fourth bucket: a
    rate entered without a year is a statement about the contract, not about 2026,
    and the alternative — a year's set silently losing the fringe rate the user
    entered before years existed — is the migration surprise #77 promised to avoid.
    """
    pool_rows = list(pool_rows or [])
    direct_rows = list(direct_rows or [])
    undated_pools = [
        r for r in pool_rows if not normalize_fiscal_year(r.get("fiscal_year"))
    ]
    undated_direct = [
        r for r in direct_rows if not normalize_fiscal_year(r.get("fiscal_year"))
    ]
    years = sorted(
        {
            y
            for r in pool_rows + direct_rows
            if (y := normalize_fiscal_year(r.get("fiscal_year")))
        }
    )
    by_year = {
        y: model_from_rows(
            undated_pools
            + [
                r for r in pool_rows if normalize_fiscal_year(r.get("fiscal_year")) == y
            ],
            undated_direct
            + [
                r
                for r in direct_rows
                if normalize_fiscal_year(r.get("fiscal_year")) == y
            ],
            fiscal_year=y,
            scope=scope,
        )
        for y in years
    }
    # The base is the newest year on file, which is the one a dateless caller means
    # — and is deterministic, where "whatever the merge saw last" was not.
    base = (
        by_year[years[-1]]
        if years
        else model_from_rows(undated_pools, undated_direct, scope=scope)
    )
    return RateSchedule(base=base, by_year=by_year)
