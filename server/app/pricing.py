"""Contract-type pricing policy, resolved per CLIN (#76).

`contract_type` used to be extracted, confidence-scored, shown on the ingest review
screen and then used for exactly one thing: a display label (`burn.py`'s
`"vehicle"`). `CLIN.type` was captured per line item and never read at all. So the
engine priced an FFP CLIN, a T&M CLIN and a CPFF CLIN identically — hours × loaded
rate, measured against ceiling and obligated. That is approximately right for T&M
and wrong for the others, most visibly on FFP, where funding *cannot* be the
constraint and Runway still raised funding tripwires against it.

This module is the data model that fixes it. It answers five questions per CLIN,
and those five are the whole interface the rest of epic #88 codes against:

  1. what does `ceiling` mean?                    → `ceiling_meaning`
  2. is cost overrun the government's problem?    → `cost_overrun_bearer`
  3. how is revenue recognised?                   → `revenue_basis`
  4. which FAR funding clause governs?            → `funding_clauses`
  5. is a funding tripwire even meaningful?       → `funding_tripwire`

Deliberately its own file rather than more surface on `burn.py` (already ~1,280
lines). `_compute_clin` starts *asking* these questions in #79.

#80 adds the second half: the earned-fee engine (`FeeTerms` / `earned_fee`). That
*is* dollar math — the one kind this file was always going to have to own, because
which rule earns the fee is a property of the contract type and nothing else. It
stays pure: a function of (policy, terms, cost) with no clock, no database and no
knowledge of the burn, so `burn.py` can call it twice per CLIN — once on cost to
date and once on projected cost — and get an earned position and a forecast from
the same arithmetic.

Two rules this module exists to enforce:

**Normalise, never guess.** Extraction reads free text off a PDF, so "CPFF",
"Cost Plus Fixed Fee", "CPFF (Completion)" and "COST-PLUS-FIXED-FEE" are one
policy and have to resolve identically. Ambiguous labels such as bare "CR" resolve
to an explicit unsupported state rather than a guessed fee-bearing type.

**`unknown` is a first-class value, not a default.** It carries *today's* engine
behaviour (see `UNKNOWN` below) so an unlabelled award's numbers cannot move, and
it reports `known=False` plus a reason so a guessed read can never be mistaken for
a typed one. That's the same posture as `burn.py`'s `clin_scope: "all"` fallback,
and the mistake already ticketed as #42.
"""

import re
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Dict, Optional, Sequence, Tuple

# FAR references for the seven pricing types, kept next to the policies they justify
# so a future reader can check the table against the regulation rather than against
# our memory of it.
#   FFP   FAR 16.202      firm-fixed-price
#   T&M   FAR 16.601      time-and-materials / labor-hour (16.601(c): ceiling price)
#   COST  FAR 16.302      cost — allowable cost reimbursed, and no fee is paid
#   CPFF  FAR 16.306      cost-plus-fixed-fee
#   CPIF  FAR 16.304      cost-plus-incentive-fee
#   CPAF  FAR 16.401(e)   cost-plus-award-fee
#   FPI   FAR 16.403      fixed-price incentive

# Funding clauses. Which of -20 / -22 applies to a cost-reimbursement contract is
# not a property of the type: Limitation of Cost (-20) governs a fully funded
# contract, Limitation of Funds (-22) an incrementally funded one. So the policy
# carries both and `funding_clause_for` picks, because getting this wrong is worse
# than saying nothing — #25 writes a letter that cites the clause by number to a
# contracting officer.
_LOC = "52.232-20"  # Limitation of Cost
_LOF = "52.232-22"  # Limitation of Funds
_TM_PAYMENTS = "52.232-7"  # Payments under T&M and Labor-Hour Contracts


@dataclass(frozen=True)
class PricingPolicy:
    """What a contract type means to the engine. Immutable; there is one instance
    per type, shared by every CLIN that resolves to it."""

    code: str
    label: str
    # "fixed_price" | "time_and_materials" | "cost_reimbursement" | "unknown".
    # FPI is a fixed-price family member despite the share ratio (FAR 16.4).
    family: str

    # 1. What `ceiling` means on this CLIN.
    #    "firm_price"        — the agreed price; spend is margin, not exposure
    #    "ceiling_price"     — a not-to-exceed the contractor may not bill past
    #    "cost_plus_*"       — estimated cost *plus* a fee component, two
    #                          different quantities with different rules
    ceiling_meaning: str

    # 2. Who absorbs a cost overrun.
    #    "contractor"                — from the first dollar (fixed price)
    #    "contractor_above_ceiling"  — reimbursed to the ceiling, then contractor
    #    "contractor_fee_first"      — the funded amount covers cost *and* fee, so
    #                                  an overrun consumes fee before it consumes
    #                                  the government's money
    #    "contractor_above_estimate" — reimbursed to the estimated cost, then
    #                                  contractor (absent a mod)
    #    "shared"                    — split by the negotiated share ratio
    #    "shared_to_ceiling"         — shared, then contractor past the ceiling
    cost_overrun_bearer: str

    # 3. How revenue is recognised.
    revenue_basis: str

    # 5. Whether a funding tripwire says anything on this type.
    #    "none"       — funding cannot be the constraint; don't raise one
    #    "at_ceiling" — only as the ceiling price is approached
    #    "meaningful" — yes; funded dollars are the binding limit
    funding_tripwire: str

    # 4. The FAR funding clause(s) in play. Empty for fixed-price types, which have
    #    no limitation-of-funds mechanic at all.
    funding_clauses: Tuple[str, ...] = ()

    # Provenance, filled in by `policy_for`. `known` is what downstream code must
    # check before it treats any of the five answers as a fact about the award.
    known: bool = True
    # "clin" | "header" | None — which field the type was read from.
    source: Optional[str] = None
    # The raw text it was read from, verbatim, so the UI can show what we saw.
    raw: Optional[str] = None
    # "absent" | "unsupported" | "vehicle" — why this resolved to unknown.
    unknown_reason: Optional[str] = None
    # Type text that was present and unmappable on a *more specific* field than the
    # one that won. Set when a CLIN prints something we can't read and the header
    # rescues the resolution: the policy is a real typed read, but the rejected text
    # is still a data-quality problem and must not vanish because the fallback
    # happened to work.
    rejected_type: Optional[str] = None
    status: str = "supported"
    notice: Optional[str] = None

    @property
    def is_fixed_price(self) -> bool:
        return self.family == "fixed_price"

    @property
    def is_cost_reimbursement(self) -> bool:
        return self.family == "cost_reimbursement"

    def funding_clause_for(self, incrementally_funded: bool) -> Optional[str]:
        """The single clause that governs, given whether the CLIN is incrementally
        funded. A pure lookup — no dollar math, no thresholds. Limitation of Funds
        (-22) applies when the contract is incrementally funded, Limitation of Cost
        (-20) when it's fully funded; a type carrying only one clause returns it
        regardless, and a fixed-price type returns None because it has none.

        Exists so #25's funding letter can cite its clause from the policy instead
        of hardcoding -22 the way `drafts.js` does today."""
        if not self.funding_clauses:
            return None
        if len(self.funding_clauses) == 1:
            return self.funding_clauses[0]
        return _LOF if incrementally_funded else _LOC

    def payload(self) -> dict:
        """JSON-serialisable form for the burn payload."""
        return {
            "code": self.code,
            "label": self.label,
            "family": self.family,
            "known": self.known,
            "source": self.source,
            "raw": self.raw,
            "unknown_reason": self.unknown_reason,
            "rejected_type": self.rejected_type,
            "ceiling_meaning": self.ceiling_meaning,
            "cost_overrun_bearer": self.cost_overrun_bearer,
            "revenue_basis": self.revenue_basis,
            "funding_clauses": list(self.funding_clauses),
            "funding_tripwire": self.funding_tripwire,
            "status": self.status,
            "notice": self.notice,
        }


FFP = PricingPolicy(
    code="FFP",
    label="Firm Fixed Price",
    family="fixed_price",
    # The government owes the price whether we spend more or less (FAR 16.202), so
    # "spent 80% of the ceiling" is a margin report, not a warning. This is the row
    # that makes the whole ticket worth doing: Runway raises funding tripwires,
    # a `runway_days` and a hard-stop date (#23) on contracts where funding cannot
    # be the problem and charging will not be blocked.
    ceiling_meaning="firm_price",
    cost_overrun_bearer="contractor",
    revenue_basis="price_milestones",
    funding_tripwire="none",
    funding_clauses=(),
)

TM = PricingPolicy(
    code="TM",
    label="Time and Materials",
    family="time_and_materials",
    # FAR 16.601(c): a T&M order carries a ceiling price the contractor exceeds at
    # its own risk. Hours × loaded rate against that ceiling is what the engine
    # already does, which is why T&M is the one type it gets approximately right.
    ceiling_meaning="ceiling_price",
    cost_overrun_bearer="contractor_above_ceiling",
    revenue_basis="hours_times_rate",
    funding_tripwire="at_ceiling",
    funding_clauses=(_TM_PAYMENTS,),
)

COST = PricingPolicy(
    code="COST",
    label="Cost Reimbursement (No Fee)",
    family="cost_reimbursement",
    # FAR 16.302. The plainest member of the cost-reimbursement family: allowable cost
    # is reimbursed up to the estimated cost, and no fee is paid at all. It is the type
    # a travel or ODC line normally carries on a cost or T&M award, which is how a
    # correct award ended up telling the reader Runway could not read it (#200).
    #
    # No-fee is a *policy* fact and not a missing figure, which is what keeps it out of
    # `_FEE_BEARING` and `_HEADER_FEE_FIELD` below. `earned_fee` therefore returns None
    # here for the same reason it does on FFP and T&M — there is no fee mechanic to
    # report, as distinct from a fee mechanic whose figures the award left blank. That
    # distinction is #159's whole subject and this type must not blur it.
    ceiling_meaning="estimated_cost",
    # FAR 52.232-20/-22: reimbursed to the estimated cost, then the contractor's, absent
    # a mod. Same bearer as CPAF, and for the same reason — with no fee to consume
    # first, an overrun goes straight past the government's obligation.
    cost_overrun_bearer="contractor_above_estimate",
    # Cost, and only cost. Deliberately spelled without "fee" or "profit": the
    # profitability surfaces read the *basis* rather than the type code to decide
    # whether an earnings concept applies at all, so a no-fee policy has to be
    # recognisable as one from this field alone.
    revenue_basis="cost_only",
    funding_tripwire="meaningful",
    funding_clauses=(_LOC, _LOF),
)

CPFF = PricingPolicy(
    code="CPFF",
    label="Cost Plus Fixed Fee",
    family="cost_reimbursement",
    # FAR 16.306. The ceiling is estimated cost *plus* a fixed fee — two quantities
    # with different rules. Measuring hours-at-billing-rate against the blend
    # conflates the cost we may overrun with the fee we can lose.
    ceiling_meaning="cost_plus_fixed_fee",
    cost_overrun_bearer="contractor_fee_first",
    revenue_basis="cost_plus_fixed_fee",
    funding_tripwire="meaningful",
    funding_clauses=(_LOC, _LOF),
)

CPIF = PricingPolicy(
    code="CPIF",
    label="Cost Plus Incentive Fee",
    family="cost_reimbursement",
    # FAR 16.304. Target cost + target fee, with over/underruns split by the
    # negotiated share ratio — the fee is earned, not fixed (#80).
    ceiling_meaning="cost_plus_target_fee",
    cost_overrun_bearer="shared",
    revenue_basis="cost_plus_earned_fee",
    funding_tripwire="meaningful",
    funding_clauses=(_LOC, _LOF),
)

CPAF = PricingPolicy(
    code="CPAF",
    label="Cost Plus Award Fee",
    family="cost_reimbursement",
    # FAR 16.401(e). Estimated cost + a base fee + an award pool earned against
    # periodic evaluation, so a share of the fee is always at risk (#80).
    ceiling_meaning="cost_plus_base_and_award_pool",
    cost_overrun_bearer="contractor_above_estimate",
    revenue_basis="cost_plus_earned_fee",
    funding_tripwire="meaningful",
    funding_clauses=(_LOC, _LOF),
)

FPI = PricingPolicy(
    code="FPI",
    label="Fixed Price Incentive",
    family="fixed_price",
    # FAR 16.403. Target cost / target profit / ceiling price, with cost variance
    # shared to the ceiling and profit adjusted by formula. Fixed-price family: no
    # limitation-of-funds mechanic, so no funding tripwire.
    ceiling_meaning="ceiling_price",
    cost_overrun_bearer="shared_to_ceiling",
    revenue_basis="cost_plus_earned_profit",
    funding_tripwire="none",
    funding_clauses=(),
)

# `unknown` deliberately carries *today's engine behaviour*, field for field, not
# T&M's policy and not a neutral blank: hours × loaded rate, measured against the
# binding budget, with the funded-dollar tripwire live and the Limitation of Funds
# clause `drafts.js:75` already hardcodes. That is the whole point — when #79 makes
# `_compute_clin` branch on the policy, an unlabelled award's numbers must not move.
#
# What separates it from a typed read is `known=False` and `unknown_reason`, which
# every consumer is expected to surface rather than swallow. Silently assuming T&M
# for an unlabelled award is the exact failure mode ticketed as #42 for
# `clin_scope: "all"`, and it must not be repeated here.
UNKNOWN = PricingPolicy(
    code="unknown",
    label="Unknown contract type",
    family="unknown",
    ceiling_meaning="ceiling_price",
    cost_overrun_bearer="contractor_above_ceiling",
    revenue_basis="hours_times_rate",
    funding_tripwire="meaningful",
    funding_clauses=(_LOF,),
    known=False,
    unknown_reason="absent",
    status="unknown",
)

POLICIES = {p.code: p for p in (FFP, TM, COST, CPFF, CPIF, CPAF, FPI)}


def _key(text: str) -> str:
    """Compact match key: lowercase, trailing parenthetical dropped, every
    non-alphanumeric character removed. Turns "T&M", "Time & Materials",
    "COST-PLUS-FIXED-FEE" and "CPFF (Completion Form)" into keys the synonym table
    can hold literally, so folding case and punctuation needs no per-entry rules."""
    s = (text or "").strip().lower()
    # Drop a trailing parenthetical qualifier — "(Completion)", "(Term)", "(LOE)",
    # "(CPFF)" — but only when something precedes it, so a string that is *only* a
    # parenthetical still gets a chance to match.
    if s.endswith(")") and "(" in s:
        head = s[: s.rindex("(")].strip()
        if head:
            s = head
    return "".join(ch for ch in s if ch.isalnum())


# Spelling → policy code. Every realistic spelling is listed literally rather than
# matched by prefix or containment: a substring match would happily read "FFP" out
# of "FFP/T&M" and invent a type for a mixed award, which is the one thing this
# module must never do.
_SYNONYMS = {
    "FFP": (
        "ffp",
        "firm fixed price",
        "firmfixedprice",
        "fixed price",
        "fp",
        "ffp loe",
        "ffploe",
        "firm fixed price level of effort",
    ),
    "TM": (
        "tm",
        "t and m",
        "tandm",
        "time and materials",
        "timeandmaterials",
        "time materials",
        "timematerials",
        "tmlh",
        "lh",
        "labor hour",
        "laborhour",
        "labor hours",
        "labour hour",
    ),
    # Bare cost, no fee (FAR 16.302). Every spelling here names *cost* and stops —
    # none of them names a fee — which is the whole basis for reading them as the
    # no-fee type rather than as the cost-reimbursement family in general.
    #
    # "cost plus" is deliberately absent, in any spelling. That phrase names the
    # family, not a type: fixed, incentive and award fee are all cost-plus, and
    # resolving it to a no-fee policy would answer a question the award did not.
    # It stays `unsupported` so the notice fires and a human reads the award.
    "COST": (
        "cost",
        "cost contract",
        "costcontract",
        "cost reimbursement",
        "costreimbursement",
        "cost reimbursable",
        "costreimbursable",
        "cost type",
        "costtype",
        "cost no fee",
        "costnofee",
        "no fee cost",
        "nofeecost",
        "cost reimbursement no fee",
        "costreimbursementnofee",
        "cost reimbursable no fee",
        "costreimbursablenofee",
    ),
    "CPFF": (
        "cpff",
        "cost plus fixed fee",
        "costplusfixedfee",
    ),
    "CPIF": (
        "cpif",
        "cost plus incentive fee",
        "costplusincentivefee",
    ),
    "CPAF": (
        "cpaf",
        "cost plus award fee",
        "costplusawardfee",
    ),
    "FPI": (
        "fpi",
        "fpif",
        "fixed price incentive",
        "fixedpriceincentive",
        "fixed price incentive firm target",
        "fixed price incentive firm",
    ),
}

_BY_KEY = {
    _key(spelling): code
    for code, spellings in _SYNONYMS.items()
    for spelling in spellings
}

# Contract *vehicles*, not pricing types. An IDIQ, a BPA or a GSA schedule says how
# work is ordered, not how it's priced — the priced thing is the order underneath,
# which carries its own type. Recognised explicitly so they resolve to `unknown`
# with a reason that says "this is a vehicle" rather than "we couldn't read this":
# an award whose only type text is "IDIQ" has a *different* data-quality story
# (the order-level type never got extracted) from one printing garbage. See #51 in
# Fixtura, which generates the documents this reads.
_VEHICLES = (
    "idiq",
    "indefinite delivery indefinite quantity",
    "indefinite delivery/indefinite quantity",
    "id/iq",
    "bpa",
    "blanket purchase agreement",
    "boa",
    "basic ordering agreement",
    "gwac",
    "gsa schedule",
    "mas",
    "multiple award schedule",
    "requirements",
)

_VEHICLE_KEYS = frozenset(_key(v) for v in _VEHICLES)


def normalize_type(text: Optional[str]) -> Optional[str]:
    """Free text → a policy code in `POLICIES`, or None when it isn't one.

    None covers all three not-a-type cases; use `classify` when the caller needs to
    tell them apart."""
    code, _ = classify(text)
    return code


def classify(text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """`(code, unknown_reason)`. Exactly one of the two is set.

    `unknown_reason` is "absent" for empty text, "vehicle" for an ordering vehicle
    (IDIQ, BPA, …), and "unsupported" for text we cannot safely map."""
    if not (text or "").strip():
        return None, "absent"
    k = _key(text)
    # Text that carries no alphanumerics at all ("???", "--", "()") is still text we
    # read and could not map. Calling that "absent" would report a missing extraction
    # where there was a failed one — a different problem with a different fix.
    if not k:
        return None, "unsupported"
    if k in _BY_KEY:
        return _BY_KEY[k], None
    if k in _VEHICLE_KEYS:
        return None, "vehicle"
    return None, "unsupported"


def policy_for(clin: Optional[dict], header: Optional[dict]) -> PricingPolicy:
    """The pricing policy governing one CLIN.

    Resolution order is `CLIN.type` → header `contract_type` → `unknown`. The CLIN
    wins because mixed awards are normal and are the reason `CLIN.type` exists at
    all: one award routinely carries an FFP CLIN for a deliverable, a T&M CLIN for
    surge support and a cost CLIN for travel. A CLIN with no type on an award whose
    header says CPFF is CPFF; a CLIN that says FFP on that same award is FFP.

    A CLIN whose own type text is unreadable falls through to the header rather
    than stopping at `unknown` — the header is a weaker read, not a wrong one, and
    `source` reports which one was used either way. The rejected CLIN text is
    carried out on `rejected_type` so the fallback succeeding doesn't erase the
    data-quality problem underneath it.

    What never happens is a guess: when neither field maps, the result is `UNKNOWN`
    with the reason from the more specific field that actually carried text."""
    clin = clin or {}
    header = header or {}
    candidates = (("clin", clin.get("type")), ("header", header.get("contract_type")))

    rejected = None
    for source, raw in candidates:
        code, reason = classify(raw)
        if code:
            return replace(
                POLICIES[code],
                source=source,
                raw=(raw or "").strip(),
                known=True,
                unknown_reason=None,
                rejected_type=rejected,
            )
        # Text was present but unmappable. Remember the first such value — it's the
        # most specific one — and keep looking for a field that does resolve.
        if reason != "absent" and rejected is None:
            rejected = (raw or "").strip()

    # Nothing resolved. Report the reason from the most specific field that carried
    # any text — a CLIN saying "IDIQ" is a vehicle problem, not a missing-data one —
    # and echo that text so the UI can show what was rejected instead of a shrug.
    reason = "absent"
    raw = None
    for _source, candidate in candidates:
        _code, candidate_reason = classify(candidate)
        if candidate_reason != "absent":
            reason = candidate_reason
            raw = (candidate or "").strip()
            break

    return replace(
        UNKNOWN,
        unknown_reason=reason,
        raw=raw,
        source=None,
        status="unsupported" if reason == "unsupported" else "unknown",
        notice=(
            f"Contract policy '{raw}' is currently unsupported."
            if reason == "unsupported" and raw
            else None
        ),
    )


# =============================================================================
# The earned-fee engine (#80)
# =============================================================================
#
# Fee is the contractor's entire economic interest in a cost-reimbursement
# contract, and it is *earned*, not accrued at a rate — each cost-plus variant
# earns it by a different rule. A single "fee percentage" is enough for CPFF and
# wrong for everything else, which is why this is a branch per type rather than a
# multiplier on the policy.
#
# FAR 52.216-8(b): the Contracting Officer may withhold payment of fee until the
# contract is complete, up to 15 percent of the total fixed fee or $100,000,
# *whichever is less*. So earned fee and collectable fee are different numbers and
# an accountant tracking cash will ask for the second one.
_FIXED_FEE_CLAUSE = "52.216-8"
_WITHHOLD_FRAC = 0.15
_WITHHOLD_CAP = 100_000.0

_PERIOD_STATUSES = ("pending", "determined")

# Government/contractor share ratio as awards print it: "80/20", "50-50", "75:25".
_SHARE_SPLIT = re.compile(r"^\s*([0-9.]+)\s*[/:\-]\s*([0-9.]+)\s*$")


def parse_share_ratio(text: Optional[str]) -> Optional[Tuple[float, float]]:
    """`"80/20"` → `(0.80, 0.20)`, government share first. None when it isn't one.

    Percentages and fractions are both accepted because both get printed, but the
    halves must sum to the whole: a ratio reading "80/30" is a misread of the
    document, not a share ratio, and normalising it by its own sum would invent a
    split nobody negotiated. Same posture as `classify` — normalise, never guess,
    and in particular never fall back to 50/50."""
    m = _SHARE_SPLIT.match(text or "")
    if not m:
        return None
    try:
        gov, contractor = float(m.group(1)), float(m.group(2))
    except ValueError:  # pragma: no cover — the regex already refuses non-numerics
        return None
    if gov < 0 or contractor < 0:
        return None
    total = gov + contractor
    if abs(total - 1.0) < 1e-9:
        return gov, contractor
    if abs(total - 100.0) < 1e-9:
        return gov / 100.0, contractor / 100.0
    return None


def _num(value) -> Optional[float]:
    """A float, or None for anything that isn't a usable number. Absent is a real
    state here — an award that never printed a fee figure must read as a gap, not
    as zero fee."""
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


@dataclass(frozen=True)
class FeeTerms:
    """The cost and fee figures one CLIN prints, normalised (#78 captured them).

    Flat scalars only, and every one optional: which figures a CLIN carries is how
    you tell a CPFF line from a CPIF line that was mislabelled, and a missing figure
    has to stay missing all the way to the payload rather than defaulting to zero."""

    # "Total Estimated Cost" on a cost-type CLIN; "Target Cost" on an incentive one.
    estimated_cost: Optional[float] = None
    fixed_fee: Optional[float] = None
    base_fee: Optional[float] = None
    award_fee_pool: Optional[float] = None
    target_fee: Optional[float] = None
    min_fee: Optional[float] = None
    max_fee: Optional[float] = None
    # FPI prints "Target Profit" rather than a fee — it is a fixed-price line.
    target_profit: Optional[float] = None
    ceiling_price: Optional[float] = None
    share_government: Optional[float] = None
    share_contractor: Optional[float] = None
    # The ratio verbatim, so the UI can show what the document said.
    share_raw: Optional[str] = None
    # Ratio text was present and unreadable — a data-quality problem that must not
    # look the same as no ratio at all.
    share_unreadable: bool = False
    # Provenance for the two figures the award *header* can supply (#159). A
    # contract-level total standing in for one line's terms is a weaker statement than
    # that line printing them, and no consumer may lose the difference.
    estimated_cost_from_header: bool = False
    fee_from_header: bool = False
    # Why header totals that existed were refused. See `HeaderFee.gap`.
    header_gap: Optional[str] = None


def fee_terms(
    clin: Optional[dict], header_fee: Optional["HeaderFee"] = None
) -> FeeTerms:
    """Read the fee figures off one CLIN dict, with the share ratio parsed.

    `header_fee` is what the award header's own cost and fee totals contribute to this
    CLIN, resolved by `header_fee_by_clin` (#159). It only ever *fills*: a figure the
    CLIN itself printed always wins, because the line's own terms are the terms."""
    clin = clin or {}
    raw_ratio = clin.get("share_ratio")
    parsed = parse_share_ratio(raw_ratio)
    stated = bool((raw_ratio or "").strip()) if isinstance(raw_ratio, str) else False
    terms = FeeTerms(
        estimated_cost=_num(clin.get("estimated_cost")),
        fixed_fee=_num(clin.get("fixed_fee")),
        base_fee=_num(clin.get("base_fee")),
        award_fee_pool=_num(clin.get("award_fee_pool")),
        target_fee=_num(clin.get("target_fee")),
        min_fee=_num(clin.get("min_fee")),
        max_fee=_num(clin.get("max_fee")),
        target_profit=_num(clin.get("target_profit")),
        ceiling_price=_num(clin.get("ceiling_price")),
        share_government=parsed[0] if parsed else None,
        share_contractor=parsed[1] if parsed else None,
        share_raw=(raw_ratio or "").strip() or None if stated else None,
        share_unreadable=stated and parsed is None,
    )
    return _fill_from_header(terms, header_fee)


@dataclass(frozen=True)
class HeaderFee:
    """What the award header's cost and fee totals contribute to one CLIN (#159).

    Ingest extracts `total_estimated_cost` and `total_fee` off the face of the award,
    scores them, shows them on the review screen and stores them — and until now
    nothing read them, so a document could ingest cleanly while its primary cost and
    fee terms had no effect on the fee model at all.

    They are a *contract-level* statement, though, and it is a CLIN-level rule that
    earns the fee. So the only safe reading is the unambiguous one: one fee-bearing
    CLIN on the award means the header totals are that line's terms. More than one
    means the award stated a total without stating the split, and dividing it would be
    the guess this module exists to refuse.

    `gap` is that refusal, named so the CLIN card can explain it:

      `clin_allocation` — the totals exist but several lines could hold them, so which
      line they belong to is a question only the user can answer.
      `fee_split`       — CPAF, where the negotiated fee is a base fee *plus* an award
      pool and one total cannot be split back into two without inventing the pool.

    A gap never fills anything. It explains why a position with figures available is
    still refusing to compute."""

    estimated_cost: Optional[float] = None
    # Which `FeeTerms` field the header's single fee total *is* under this CLIN's type.
    fee_field: Optional[str] = None
    fee: Optional[float] = None
    gap: Optional[str] = None


# Which `FeeTerms` figure a header "total fee" maps to, per type — the types spell the
# negotiated fee differently, and mapping it to the wrong field would leave the
# position unknown while looking like it had been filled.
#
# CPAF is absent on purpose (see `HeaderFee.gap`): a CPAF line takes the header's cost
# and leaves the fee as the gap the award actually left.
_HEADER_FEE_FIELD = {
    "CPFF": "fixed_fee",
    "CPIF": "target_fee",
    # FPI prints "Target Profit" rather than a fee. It is the same figure the face of a
    # fixed-price incentive award totals as fee, and the one `_incentive_position`
    # reads, so mapping it here is a naming difference and not a reinterpretation.
    "FPI": "target_profit",
}

_FEE_BEARING = ("CPFF", "CPIF", "CPAF", "FPI")


def fee_bearing(policy: Optional[PricingPolicy]) -> bool:
    """Whether a CLIN under this policy earns a fee `earned_fee` can compute.

    Deliberately a property of the *policy* and not of which figures the CLIN happens
    to carry: an FFP or T&M line with a stray fee figure on it is still not fee-bearing
    (its profit is price minus cost), and a CPFF line with every figure blank still is
    — that blankness is the whole case #159 is about. An unknown type is excluded
    because it must keep behaving exactly as the pre-#76 engine did."""
    return bool(policy and policy.known and policy.code in _FEE_BEARING)


def header_fee_by_clin(
    header: Optional[dict], clins: Optional[Sequence[dict]], policy_of
) -> Dict[str, HeaderFee]:
    """The header's cost/fee totals resolved against the award's CLINs, keyed by CLIN.

    Resolved once for the contract — like `burn`'s award-fee period routing, and for the
    same reason: whether the totals are unambiguous is the one question a per-CLIN call
    cannot answer about itself.

    Only labor CLINs are eligible, because they are the only lines the engine gives a
    fee position to today. That also keeps the common award shape working: a cost-type
    travel line sitting beside one CPFF labor line must not make the award look like it
    has two candidates for the header's fee. Widening this is #155's call, not this
    one's."""
    header = header or {}
    est = _num(header.get("total_estimated_cost"))
    fee = _num(header.get("total_fee"))
    if est is None and fee is None:
        return {}
    eligible = [
        c for c in (clins or []) if c.get("is_labor") and fee_bearing(policy_of(c))
    ]
    if not eligible:
        return {}
    if len(eligible) > 1:
        gap = HeaderFee(gap="clin_allocation")
        return {str(c.get("clin")): gap for c in eligible}
    clin = eligible[0]
    fee_field = _HEADER_FEE_FIELD.get(policy_of(clin).code)
    return {
        str(clin.get("clin")): HeaderFee(
            estimated_cost=est,
            fee_field=fee_field,
            fee=fee if fee_field else None,
            gap="fee_split" if fee is not None and fee_field is None else None,
        )
    }


def _fill_from_header(terms: FeeTerms, header_fee: Optional[HeaderFee]) -> FeeTerms:
    """Fill the figures the CLIN left blank from the award header's totals.

    Fills, never overrides — and stamps which figures came from the header, so nothing
    downstream can report a contract-level total as a line's printed terms."""
    if header_fee is None:
        return terms
    patch: dict = {}
    if terms.estimated_cost is None and header_fee.estimated_cost is not None:
        patch["estimated_cost"] = header_fee.estimated_cost
        patch["estimated_cost_from_header"] = True
    fee_field = header_fee.fee_field
    if fee_field and header_fee.fee is not None and getattr(terms, fee_field) is None:
        patch[fee_field] = header_fee.fee
        patch["fee_from_header"] = True
    if header_fee.gap:
        patch["header_gap"] = header_fee.gap
    return replace(terms, **patch) if patch else terms


@dataclass(frozen=True)
class FeePosition:
    """Where the fee stands at one cost level, under one type's rule.

    Two reads, from one call: `earned` is fee earned at the cost handed in, and
    `at_completion` is the fee if that cost turns out to be the final cost. Hand it
    cost-to-date and you get today's position; hand it projected cost and
    `at_completion` is the forecast. That is why nothing in here knows the date.

    `known` is the gate every consumer must check: False means the award didn't print
    the figures this rule needs, `missing` names them, and every dollar field is 0.0
    rather than a plausible guess.

    `at_risk` means "fee that exists on paper and is not assured", and what threatens
    it is type-specific by design: on CPFF it is fee the cost overrun has consumed
    (`contractor_fee_first`), on CPAF it is award pool the government has not yet
    determined. On the incentive types the downside is not a separate quantity — it
    is the share ratio moving `at_completion`, which `target_delta` reports."""

    basis: str  # fixed_fee | base_plus_award | incentive_fee | incentive_profit
    known: bool
    missing: Tuple[str, ...] = ()
    cost: float = 0.0
    # None when no estimated/target cost was stated — there is then nothing to earn
    # the fee proportionally against.
    cost_frac: Optional[float] = None
    # The full negotiated fee: the fixed fee, base + pool, the target fee, the target
    # profit. What `at_completion` is compared against.
    target: Optional[float] = None
    earned: float = 0.0
    at_completion: float = 0.0
    withhold: float = 0.0
    collectable: float = 0.0
    at_risk: float = 0.0
    overrun: float = 0.0
    # Fee lost to the overrun, capped at the fee itself so nothing goes negative.
    absorbed: float = 0.0
    exhausted: bool = False
    # True where `earned` is a provisional billing at the target rate pending final
    # settlement (the incentive types), rather than fee actually earned.
    provisional: bool = False
    clause: Optional[str] = None
    # CPAF detail. None on every other type.
    base_earned: Optional[float] = None
    award_earned: Optional[float] = None
    award_available: Optional[float] = None
    award_pool: Optional[float] = None
    periods_determined: Optional[int] = None
    periods_total: Optional[int] = None
    periods: Tuple[dict, ...] = field(default_factory=tuple)
    # Incentive detail.
    share_contractor: Optional[float] = None
    share_raw: Optional[str] = None
    # FPI's point of total assumption: the cost above which the contractor absorbs
    # every additional dollar. None without a price ceiling to compute it from.
    pta: Optional[float] = None
    # At least one figure behind this position came off the award *header* rather than
    # the CLIN (#159). The arithmetic is identical; the claim is weaker, and a surface
    # reporting fee has to be able to say so.
    header_derived: bool = False

    @property
    def target_delta(self) -> Optional[float]:
        """`at_completion - target` — the fee the position has gained or lost against
        what the award promised. The number worth alarming on."""
        if self.target is None:
            return None
        return self.at_completion - self.target

    def payload(self) -> dict:
        """JSON-serialisable form for the burn payload."""
        return {
            "basis": self.basis,
            "known": self.known,
            "missing": list(self.missing),
            "cost": round(self.cost, 2),
            "cost_frac": (
                round(self.cost_frac, 4) if self.cost_frac is not None else None
            ),
            "target": round(self.target, 2) if self.target is not None else None,
            "earned": round(self.earned, 2),
            "at_completion": round(self.at_completion, 2),
            "target_delta": (
                round(self.target_delta, 2) if self.target_delta is not None else None
            ),
            "withhold": round(self.withhold, 2),
            "collectable": round(self.collectable, 2),
            "at_risk": round(self.at_risk, 2),
            "overrun": round(self.overrun, 2),
            "absorbed": round(self.absorbed, 2),
            "exhausted": self.exhausted,
            "provisional": self.provisional,
            "clause": self.clause,
            "base_earned": (
                round(self.base_earned, 2) if self.base_earned is not None else None
            ),
            "award_earned": (
                round(self.award_earned, 2) if self.award_earned is not None else None
            ),
            "award_available": (
                round(self.award_available, 2)
                if self.award_available is not None
                else None
            ),
            "award_pool": (
                round(self.award_pool, 2) if self.award_pool is not None else None
            ),
            "periods_determined": self.periods_determined,
            "periods_total": self.periods_total,
            "periods": [dict(p) for p in self.periods],
            "share_contractor": self.share_contractor,
            "share_raw": self.share_raw,
            "pta": round(self.pta, 2) if self.pta is not None else None,
            "header_derived": self.header_derived,
        }


def validate_fee_period(entry) -> Optional[str]:
    """A human-readable problem with one award-fee evaluation period, or None.

    The determinations are the government's, so they are *entered* rather than
    extracted — which makes this the only place a bad one can be caught before it
    becomes revenue. The rule worth the strictness: a period marked determined with
    no amount would otherwise read as a determination of zero, which is a real and
    very different outcome from "the evaluation hasn't happened yet"."""
    if not isinstance(entry, dict):
        return "Each award-fee period must be an object."
    status = str(entry.get("status") or "pending").strip().lower()
    if status not in _PERIOD_STATUSES:
        return (
            f"{status!r} is not an award-fee period status. Use "
            f"{' or '.join(repr(s) for s in _PERIOD_STATUSES)}."
        )
    for key in ("pool_share", "determined_amount", "score"):
        value = entry.get(key)
        if value in (None, ""):
            continue
        if _num(value) is None:
            return f"{key} must be a number, not {value!r}."
        if _num(value) < 0:
            return f"{key} cannot be negative."
    if status == "determined" and _num(entry.get("determined_amount")) is None:
        return (
            "A determined period needs a determined_amount — leave the status "
            "'pending' until the government has made its determination."
        )
    start, end = entry.get("start"), entry.get("end")
    for key, value in (("start", start), ("end", end)):
        if value in (None, ""):
            continue
        try:
            date.fromisoformat(str(value))
        except ValueError:
            return f"{key} must be an ISO date (YYYY-MM-DD), not {value!r}."
    if start and end and str(start) > str(end):
        return "An award-fee period cannot end before it starts."
    return None


def _dollars(value: float) -> str:
    """A figure for an error message. Whole dollars where the number is whole, because
    "$45,000.00" in a sentence reads like precision that isn't being claimed."""
    return f"${value:,.2f}".replace(".00", "")


# Half a cent. Shares and determinations arrive as floats, and a plan that sums to the
# pool within rounding is the plan summing to the pool — refusing it would be arithmetic
# pedantry aimed at a user who typed the right numbers.
_FEE_PLAN_EPSILON = 0.005


def validate_fee_plan(periods, pools) -> Optional[str]:
    """A human-readable problem with an award-fee plan taken as a whole, or None.

    `validate_fee_period` checks each period against itself. This checks the plan
    against the pool it draws on, which is the only place the contradiction in #185
    can be caught: a determination larger than its period's share of the pool used to
    save happily and then be clamped by `_award_fee_position`, so the period table
    showed one number and the fee total counted another.

    `pools` maps CLIN id to that CLIN's award-fee pool. Routing repeats the rule in
    `burn._fee_periods_by_clin` deliberately — a period must validate against the same
    pool it will later earn against, so the two must not drift apart. An award with no
    pool visible at all validates nothing: the determinations can be entered before the
    document that prices them is imported, and refusing them would be Runway demanding
    the paperwork arrive in its preferred order.
    """
    records = normalize_fee_periods(periods)
    bearers = {str(k): float(v) for k, v in (pools or {}).items() if v is not None}
    if not records or not bearers:
        return None

    named = ", ".join(sorted(bearers))
    fallback = next(iter(bearers)) if len(bearers) == 1 else None
    routed: dict = {}
    for record in records:
        target = record["clin"] or fallback
        if target is None:
            return (
                f"{record['name']!r} doesn't say which CLIN's award-fee pool it draws "
                f"on, and this award carries {len(bearers)} of them ({named}). Name the "
                "CLIN on the period — a determination counted against the wrong pool is "
                "fee that was never awarded."
            )
        if str(target) not in bearers:
            return (
                f"{record['name']!r} names CLIN {target}, which carries no award-fee "
                f"pool. The pools on this award are on {named}."
            )
        routed.setdefault(str(target), []).append(record)

    for clin_id, group in routed.items():
        pool = bearers[clin_id]
        silent = [r for r in group if r["pool_share"] is None]
        explicit = [r for r in group if r["pool_share"] is not None]

        # Mixed plans are refused rather than filled in. Splitting what's left of the
        # pool across the silent periods would be Runway inferring a negotiated fee
        # structure, the same thing it refuses to do with tranche cadence.
        if explicit and silent:
            missing = ", ".join(repr(r["name"]) for r in silent)
            return (
                f"This plan states a pool_share on some of CLIN {clin_id}'s periods and "
                f"not on others ({missing}). Give every period an explicit share, or "
                "none of them and let the pool split evenly — Runway will not infer how "
                "the rest of the pool was meant to divide."
            )

        if explicit:
            total = sum(r["pool_share"] for r in explicit)
            if total > pool + _FEE_PLAN_EPSILON:
                return (
                    f"CLIN {clin_id}'s period shares total {_dollars(total)}, more than "
                    f"its {_dollars(pool)} award-fee pool."
                )
            even = None
        else:
            even = pool / len(group)

        for record in group:
            if record["status"] != "determined":
                continue
            amount = record["determined_amount"] or 0.0
            share = even if record["pool_share"] is None else record["pool_share"]
            if amount <= share + _FEE_PLAN_EPSILON:
                continue
            if even is None:
                return (
                    f"{record['name']!r} was determined at {_dollars(amount)}, more than "
                    f"the {_dollars(share)} share of the pool it is worth."
                )
            return (
                f"{record['name']!r} was determined at {_dollars(amount)}, more than the "
                f"{_dollars(share)} it can earn: with no pool_share stated, CLIN "
                f"{clin_id}'s {_dollars(pool)} pool splits evenly across "
                f"{len(group)} periods. State each period's pool_share if the award-fee "
                "plan is not an even split."
            )
    return None


def normalize_fee_periods(periods) -> Tuple[dict, ...]:
    """Award-fee periods in canonical form, ordered as given.

    Every key is present on every record — `determined_amount: None` on a pending
    period is the honest shape, and the reader shouldn't have to distinguish a
    missing key from an undetermined one."""
    out = []
    for i, entry in enumerate(periods or []):
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "pending").strip().lower()
        if status not in _PERIOD_STATUSES:
            status = "pending"
        amount = _num(entry.get("determined_amount"))
        out.append(
            {
                "name": str(entry.get("name") or f"Period {i + 1}").strip(),
                # Which CLIN's pool this period draws on. Null is the ordinary case —
                # one award pool on one CLIN — and is resolved by the caller, because
                # only it can see whether the award has more than one.
                "clin": str(entry["clin"]).strip() if entry.get("clin") else None,
                "start": str(entry["start"]) if entry.get("start") else None,
                "end": str(entry["end"]) if entry.get("end") else None,
                "pool_share": _num(entry.get("pool_share")),
                "status": status,
                # Carried only where the determination actually happened, so a
                # recommendation left on a pending period can never be read as fee.
                "determined_amount": amount if status == "determined" else None,
                "score": _num(entry.get("score")),
            }
        )
    return tuple(out)


def _unknown(basis: str, missing, cost: float, cost_frac, target) -> FeePosition:
    """A position that refuses to compute, naming what the award didn't print."""
    return FeePosition(
        basis=basis,
        known=False,
        missing=tuple(missing),
        cost=cost,
        cost_frac=cost_frac,
        target=target,
    )


def _withhold(total_fee: float, earned: float) -> Tuple[float, float]:
    """`(withheld, collectable)` under 52.216-8: 15% of the total fee or $100,000,
    whichever is less — and never more than has actually been earned, or collectable
    fee would go negative early in performance."""
    ceiling = min(_WITHHOLD_FRAC * total_fee, _WITHHOLD_CAP)
    withheld = max(0.0, min(ceiling, earned))
    return withheld, earned - withheld


def _fixed_fee_position(terms: FeeTerms, cost: float) -> FeePosition:
    """CPFF, FAR 16.306 / 52.216-8.

    The fee is a fixed dollar amount set at award. It does not move with actual cost:
    it is billed proportionally as cost is incurred, so a CLIN 60% through its
    estimated cost has earned 60% of the fee — and a CLIN 110% through it has earned
    100% of the fee and not a dollar more. That flat line under an overrun is the
    single best argument for this whole epic, so `earned` is deliberately computed
    from a fraction capped at 1.0 rather than from cost directly.

    What the overrun *does* do is eat the fee. The obligated amount covers cost plus
    fee (`cost_overrun_bearer == "contractor_fee_first"`), so every dollar spent past
    the estimated cost is a dollar that would have paid fee: `absorbed`, capped at the
    fee, floored so the answer is "the fee is gone" rather than a negative fee."""
    missing = []
    if terms.fixed_fee is None:
        missing.append("fixed_fee")
    if terms.estimated_cost is None:
        missing.append("estimated_cost")
    est = terms.estimated_cost
    cost_frac = (cost / est) if est else None
    if missing:
        return _unknown("fixed_fee", missing, cost, cost_frac, terms.fixed_fee)

    fee = max(0.0, terms.fixed_fee)
    earned = fee * min(1.0, cost_frac or 0.0)
    overrun = max(0.0, cost - est)
    absorbed = min(fee, overrun)
    withheld, collectable = _withhold(fee, earned)
    return FeePosition(
        basis="fixed_fee",
        known=True,
        cost=cost,
        cost_frac=cost_frac,
        target=fee,
        earned=earned,
        at_completion=fee - absorbed,
        withhold=withheld,
        collectable=collectable,
        at_risk=absorbed,
        overrun=overrun,
        absorbed=absorbed,
        exhausted=bool(fee) and absorbed >= fee,
        clause=_FIXED_FEE_CLAUSE,
    )


def _award_fee_position(terms: FeeTerms, cost: float, periods) -> FeePosition:
    """CPAF, FAR 16.401(e).

    Two fees, and keeping them apart is the whole point. The **base fee** is small and
    earned like a fixed fee (DFARS 215.404-74 holds it to roughly 0-3% on DoD work; a
    zero base fee is legal, which is why an absent one is not a gap). The **award fee
    pool** is divided into evaluation periods and earned *only* on the government's
    subjective determination against an award-fee plan.

    So the pool is at risk until determined, and nothing here may treat an
    undetermined period as earned revenue — that is the mistake that overstates margin
    for three quarters and corrects violently in the fourth. A period's own status
    governs: an amount sitting on a pending period is a recommendation, not fee.

    Each period earns at most its share of the pool, and the aggregate is capped at the
    pool, so a mistyped determination cannot inflate the fee past what was negotiated.
    """
    if terms.award_fee_pool is None or terms.estimated_cost is None:
        missing = [
            k
            for k, v in (
                ("award_fee_pool", terms.award_fee_pool),
                ("estimated_cost", terms.estimated_cost),
            )
            if v is None
        ]
        est = terms.estimated_cost
        target = (terms.base_fee or 0.0) + (terms.award_fee_pool or 0.0) or None
        return _unknown(
            "base_plus_award", missing, cost, (cost / est) if est else None, target
        )

    est = terms.estimated_cost
    cost_frac = (cost / est) if est else None
    base = max(0.0, terms.base_fee or 0.0)
    pool = max(0.0, terms.award_fee_pool)
    records = normalize_fee_periods(periods)

    # An award-fee plan that names its periods without pricing them is splitting the
    # pool evenly — that is the plan's own default, and it beats refusing to compute.
    even_share = (pool / len(records)) if records else 0.0
    available = 0.0
    award_earned = 0.0
    for record in records:
        share = record["pool_share"]
        share = even_share if share is None else max(0.0, share)
        if record["status"] != "determined":
            continue
        available += share
        award_earned += min(share, max(0.0, record["determined_amount"] or 0.0))
    available = min(available, pool)
    award_earned = min(award_earned, pool)

    base_earned = base * min(1.0, cost_frac or 0.0)
    earned = base_earned + award_earned
    return FeePosition(
        basis="base_plus_award",
        known=True,
        cost=cost,
        cost_frac=cost_frac,
        target=base + pool,
        earned=earned,
        # Base fee earns out in full; the pool contributes only what was determined.
        at_completion=base + award_earned,
        # 52.216-8's withhold is the *fixed*-fee mechanic. CPAF fee payment runs
        # through the award-fee plan and 52.216-7, so there is no flat withhold to
        # report here and inventing one would understate collectable cash.
        collectable=earned,
        at_risk=max(0.0, pool - available),
        overrun=max(0.0, cost - est),
        base_earned=base_earned,
        award_earned=award_earned,
        award_available=available,
        award_pool=pool,
        periods_determined=sum(1 for r in records if r["status"] == "determined"),
        periods_total=len(records),
        periods=records,
    )


def _incentive_position(terms: FeeTerms, cost: float, profit: bool) -> FeePosition:
    """CPIF (FAR 16.304 / 52.216-10) and FPI (FAR 16.403).

        fee = clamp(target_fee + share_contractor x (target_cost - actual_cost),
                    min_fee, max_fee)

    Underrun the target and fee rises by the contractor's share of the saving;
    overrun and it falls, floored at the minimum fee. This makes fee a live function
    of the burn, which is genuinely new for Runway: change a staffing assumption and
    projected fee moves.

    But it settles on *final* cost, so nothing is earned at the incentive rate
    mid-performance. 52.216-10 bills fee provisionally at the target rate and adjusts
    at completion, so `earned` is the target fee pro-rated on cost (flagged
    `provisional`) and `at_completion` is the formula. Reporting the formula as earned
    would book a fee swing that final settlement has not yet agreed to.

    FPI runs the same arithmetic on profit rather than fee, with the price ceiling as
    the real bound: past it the government owes nothing more, so cost + profit cannot
    exceed the ceiling price. `pta` names where that bites."""
    target = terms.target_profit if profit else terms.target_fee
    target_key = "target_profit" if profit else "target_fee"
    missing = []
    if target is None:
        missing.append(target_key)
    if terms.estimated_cost is None:
        missing.append("estimated_cost")
    if terms.share_contractor is None:
        missing.append("share_ratio")
    basis = "incentive_profit" if profit else "incentive_fee"
    est = terms.estimated_cost
    cost_frac = (cost / est) if est else None
    if missing:
        return _unknown(basis, missing, cost, cost_frac, target)

    target = max(0.0, target)
    share = terms.share_contractor
    at_completion = target + share * (est - cost)
    # Brackets are optional on the document; absent, the formula stands unclamped
    # except by the floor at zero, which is never a bracket but an invariant.
    if terms.min_fee is not None:
        at_completion = max(at_completion, terms.min_fee)
    if terms.max_fee is not None:
        at_completion = min(at_completion, terms.max_fee)
    pta = None
    if profit and terms.ceiling_price is not None:
        # Cost + profit may not exceed the price ceiling (FAR 16.403).
        at_completion = min(at_completion, terms.ceiling_price - cost)
        gov = terms.share_government
        if gov:
            pta = est + (terms.ceiling_price - (est + target)) / gov
    at_completion = max(0.0, at_completion)

    earned = target * min(1.0, cost_frac or 0.0)
    withheld, collectable = _withhold(target, earned) if not profit else (0.0, earned)
    return FeePosition(
        basis=basis,
        known=True,
        cost=cost,
        cost_frac=cost_frac,
        target=target,
        earned=earned,
        at_completion=at_completion,
        withhold=withheld,
        collectable=collectable,
        overrun=max(0.0, cost - est),
        absorbed=max(0.0, target - at_completion),
        exhausted=at_completion <= 0.0,
        provisional=True,
        clause=None if profit else "52.216-10",
        share_contractor=share,
        share_raw=terms.share_raw,
        pta=pta,
    )


def earned_fee(
    policy: PricingPolicy,
    terms: FeeTerms,
    cost: float,
    *,
    periods: Sequence[dict] = (),
) -> Optional[FeePosition]:
    """The fee position at one cost level, or None where the type has no fee mechanic.

    None on FFP (profit is price - cost, reported as margin in #79), on T&M (the fee is
    inside the billing rate) and on an unknown type (which must behave exactly as the
    pre-#76 engine did). Emitting an empty position on those would invite it to be read
    as a fee arrangement that does not exist.

    `cost` is whatever cost the caller wants the position measured at: cost to date for
    today's position, projected cost for the forecast. Negative cost is not a state —
    it floors at zero."""
    cost = max(0.0, _num(cost) or 0.0)
    if not policy.known:
        return None
    if policy.code == "CPFF":
        position = _fixed_fee_position(terms, cost)
    elif policy.code == "CPAF":
        position = _award_fee_position(terms, cost, periods)
    elif policy.code == "CPIF":
        position = _incentive_position(terms, cost, profit=False)
    elif policy.code == "FPI":
        position = _incentive_position(terms, cost, profit=True)
    else:
        return None
    return _carry_header_provenance(position, terms)


def _carry_header_provenance(position: FeePosition, terms: FeeTerms) -> FeePosition:
    """Move the header provenance (#159) off the terms and onto the position.

    Both directions matter. A position computed off a contract-level total says so,
    because "the award printed this line's fee" and "the award printed one fee for the
    whole contract and this is the only line that could hold it" are different claims
    and the second one is the weaker. A position still refusing to compute names the
    header gap alongside the figures it lacks, so the card can say the totals need
    allocating instead of repeating that the award printed nothing — which is the
    difference between a dead end and an action."""
    if position.known:
        if terms.estimated_cost_from_header or terms.fee_from_header:
            return replace(position, header_derived=True)
        return position
    if terms.header_gap and terms.header_gap not in position.missing:
        return replace(position, missing=position.missing + (terms.header_gap,))
    return position
