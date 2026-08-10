"""Deterministic confidence layer (the "sanity floor" over the model's guess).

The model returns a subjective self-assessment (``field_confidence`` on the
header, ``confidence`` on each CLIN). That number is soft and the model tends
to omit the header entirely, so this module owns the header confidence and
sanity-checks the CLINs:

- baseline: each header field starts from a signal-based baseline — fields we
  can actually validate (contract-number format, ISO date, numeric money) rate
  higher than free-text fields we can only confirm are present. That gives an
  honest, non-uniform spread even when the model says nothing;
- model-as-doubt: if the model DID return a value for a field, it can only pull
  that field DOWN (min with the baseline), never inflate it;
- cap: a value that fails its format check, or a cross-field check (obligated >
  ceiling, CLIN ceilings summing past the total), is capped low regardless.
"""

import re
from typing import Optional

from .schemas import Extraction

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PIID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{4,}$")

# Signal-based baselines: how strongly we can independently verify each field.
_BASELINE = {
    "piid": 0.97,  # regex-validated identifier
    "effective_date": 0.96,  # ISO-8601 validated
    "total_ceiling": 0.95,  # numeric + cross-checked
    "total_obligated": 0.94,  # numeric + cross-checked
    # Printed dollar figures in a labelled Section B block, so they score with the
    # money fields above rather than with free text (#78).
    "total_estimated_cost": 0.94,
    "total_fee": 0.94,
    "contract_type": 0.93,  # short controlled vocab
    "agency": 0.92,  # free text, presence only
    "contractor": 0.91,  # free text, presence only
    "contracting_officer": 0.90,  # free text, presence only
}

CLIN_BASELINE = 0.93
FAIL_CAP = 0.70  # value present but malformed -> amber
CROSS_FAIL_CAP = 0.55  # cross-field sanity violation -> deep amber


def _money(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _text(v) -> bool:
    return bool(v and str(v).strip())


_HEADER_CHECKS = {
    "piid": lambda h: bool(_PIID_RE.match(h.piid or "")),
    "agency": lambda h: _text(h.agency),
    "contractor": lambda h: _text(h.contractor),
    "contract_type": lambda h: _text(h.contract_type),
    "total_ceiling": lambda h: _money(h.total_ceiling),
    "total_obligated": lambda h: _money(h.total_obligated),
    "total_estimated_cost": lambda h: _money(h.total_estimated_cost),
    "total_fee": lambda h: _money(h.total_fee),
    "effective_date": lambda h: bool(_DATE_RE.search(str(h.effective_date or ""))),
    "contracting_officer": lambda h: _text(h.contracting_officer),
}


def clin_fee(cl) -> float:
    """The fee a cost-type CLIN prints, however it prints it.

    Award fee is base + pool because the CLIN total covers both; that they are two
    fields elsewhere is about which one is *earned*, not about what the line foots
    to. A CPIF line foots to its target fee, not to its min/max brackets — those
    are what the fee moves to, and neither is in the printed total.
    """
    return sum(
        v
        for v in (cl.fixed_fee, cl.base_fee, cl.award_fee_pool, cl.target_fee)
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    )


def fee_mismatch(cl) -> Optional[str]:
    """Why this CLIN's cost + fee doesn't foot to its ceiling, or None if it does.

    The identity `ceiling == estimated_cost + fee` (FAR 16.306) is the one
    independent check available on an extracted cost-type line: the document states
    all three, so a disagreement means a figure was misread — most often a leading
    digit, or fee copied into the ceiling. So this reports rather than reconciles.
    Runway must never pick which of the two it believes and silently rewrite the
    third; the whole point of a review screen is that a human does that.

    Returns a sentence for the review screen, not a bool, because "these don't
    add up" is useless without the three numbers that didn't.

    Silent when the line isn't cost-type (nothing to check), or when the award
    prints cost with no fee at all — a cost-no-fee CLIN (FAR 16.302) is real, and
    flagging it would mean nagging every one of them forever.
    """
    if cl.estimated_cost is None or not _money(cl.ceiling):
        return None
    fee = clin_fee(cl)
    if not fee:
        return None
    expected = cl.estimated_cost + fee
    # 0.5%: enough for a sheet that rounds its own lines to the dollar, tight
    # enough to still catch a transposed or dropped digit.
    if abs(expected - cl.ceiling) <= abs(cl.ceiling) * 0.005:
        return None
    return (
        f"Cost + fee doesn't foot to this CLIN's ceiling: "
        f"${cl.estimated_cost:,.2f} cost + ${fee:,.2f} fee = ${expected:,.2f}, "
        f"but the ceiling reads ${cl.ceiling:,.2f} "
        f"(off by ${expected - cl.ceiling:+,.2f}). One of the three was misread — "
        f"check the award's Section B before saving."
    )


def apply(ext: Extraction) -> Extraction:
    h = ext.contract
    model_fc = dict(h.field_confidence or {})
    fc = {}

    for field, check in _HEADER_CHECKS.items():
        if getattr(h, field, None) is None:
            continue  # nothing extracted -> no badge for this field
        score = _BASELINE[field]
        model_val = model_fc.get(field)
        if model_val is not None:
            score = min(score, model_val)  # model may only express doubt
        if not check(h):
            score = min(score, FAIL_CAP)
        fc[field] = round(score, 2)

    # Cross-field: obligated funding cannot exceed the total ceiling.
    if _money(h.total_ceiling) and _money(h.total_obligated):
        if h.total_obligated > h.total_ceiling * 1.001:
            for f in ("total_obligated", "total_ceiling"):
                if f in fc:
                    fc[f] = min(fc[f], CROSS_FAIL_CAP)

    clin_total = 0.0
    for cl in ext.clins:
        score = cl.confidence if cl.confidence is not None else CLIN_BASELINE
        if not _money(cl.ceiling):
            score = min(score, FAIL_CAP)
        note = fee_mismatch(cl)
        if note:
            score = min(score, CROSS_FAIL_CAP)
        cl.confidence_note = note
        cl.confidence = round(score, 2)
        if _money(cl.ceiling):
            clin_total += cl.ceiling

    # Cross-check: summed CLIN ceilings shouldn't blow well past the contract
    # ceiling (5% slack for rounding / option lines).
    #
    # When they do, the header total is as suspect as the lines, so badge it too
    # rather than only the CLINs. A misread leading digit on total_ceiling looks
    # exactly like this — observed on a real extraction: the header came back
    # $3,701,569.60 for an award whose own CLIN schedule summed to $8,701,569.60.
    # The schedule is the more trustworthy side (many independently-read figures
    # that agree), so the single total is what gets flagged for review.
    if _money(h.total_ceiling) and clin_total > h.total_ceiling * 1.05:
        for cl in ext.clins:
            cl.confidence = min(cl.confidence, CROSS_FAIL_CAP)
        if "total_ceiling" in fc:
            fc["total_ceiling"] = min(fc["total_ceiling"], CROSS_FAIL_CAP)

    # Assigned last so the cross-checks above can still lower a field's score.
    h.field_confidence = fc
    return ext
