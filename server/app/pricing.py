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
lines), and deliberately free of dollar math: nothing here computes a number.
`_compute_clin` starts *asking* these questions in #79.

Two rules this module exists to enforce:

**Normalise, never guess.** Extraction reads free text off a PDF, so "CPFF",
"Cost Plus Fixed Fee", "CPFF (Completion)", "COST-PLUS-FIXED-FEE" and "CR" are one
policy and have to resolve identically. Text that isn't in the synonym table
resolves to `UNKNOWN` — it never gets rounded to the nearest plausible type.

**`unknown` is a first-class value, not a default.** It carries *today's* engine
behaviour (see `UNKNOWN` below) so an unlabelled award's numbers cannot move, and
it reports `known=False` plus a reason so a guessed read can never be mistaken for
a typed one. That's the same posture as `burn.py`'s `clin_scope: "all"` fallback,
and the mistake already ticketed as #42.
"""

from dataclasses import dataclass, replace
from typing import Optional, Tuple

# FAR references for the six pricing types, kept next to the policies they justify
# so a future reader can check the table against the regulation rather than against
# our memory of it.
#   FFP   FAR 16.202      firm-fixed-price
#   T&M   FAR 16.601      time-and-materials / labor-hour (16.601(c): ceiling price)
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
    # "absent" | "unrecognized" | "vehicle" — why this resolved to unknown.
    unknown_reason: Optional[str] = None
    # Type text that was present and unmappable on a *more specific* field than the
    # one that won. Set when a CLIN prints something we can't read and the header
    # rescues the resolution: the policy is a real typed read, but the rejected text
    # is still a data-quality problem and must not vanish because the fallback
    # happened to work.
    rejected_type: Optional[str] = None

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
)

POLICIES = {p.code: p for p in (FFP, TM, CPFF, CPIF, CPAF, FPI)}


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
    "CPFF": (
        "cpff",
        "cost plus fixed fee",
        "costplusfixedfee",
        # "CR" and bare "cost reimbursable" are what awards print when they mean a
        # cost contract without naming its fee arrangement; CPFF is by far the most
        # common of those and is what `schemas.py`'s own example text ("'CR'")
        # refers to. Same policy, per #76.
        "cr",
        "cost reimbursable",
        "costreimbursable",
        "cost reimbursement",
        "costreimbursement",
        "cost plus",
        "costplus",
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
    (IDIQ, BPA, …), and "unrecognized" for text we read but can't map."""
    if not (text or "").strip():
        return None, "absent"
    k = _key(text)
    # Text that carries no alphanumerics at all ("???", "--", "()") is still text we
    # read and could not map. Calling that "absent" would report a missing extraction
    # where there was a failed one — a different problem with a different fix.
    if not k:
        return None, "unrecognized"
    if k in _BY_KEY:
        return _BY_KEY[k], None
    if k in _VEHICLE_KEYS:
        return None, "vehicle"
    return None, "unrecognized"


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

    return replace(UNKNOWN, unknown_reason=reason, raw=raw, source=None)
