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

from .schemas import Extraction

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PIID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{4,}$")

# Signal-based baselines: how strongly we can independently verify each field.
_BASELINE = {
    "piid": 0.97,  # regex-validated identifier
    "effective_date": 0.96,  # ISO-8601 validated
    "total_ceiling": 0.95,  # numeric + cross-checked
    "total_obligated": 0.94,  # numeric + cross-checked
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
    "effective_date": lambda h: bool(_DATE_RE.search(str(h.effective_date or ""))),
    "contracting_officer": lambda h: _text(h.contracting_officer),
}


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

    h.field_confidence = fc

    clin_total = 0.0
    for cl in ext.clins:
        score = cl.confidence if cl.confidence is not None else CLIN_BASELINE
        if not _money(cl.ceiling):
            score = min(score, FAIL_CAP)
        cl.confidence = round(score, 2)
        if _money(cl.ceiling):
            clin_total += cl.ceiling

    # Cross-check: summed CLIN ceilings shouldn't blow well past the contract
    # ceiling (5% slack for rounding / option lines).
    if _money(h.total_ceiling) and clin_total > h.total_ceiling * 1.05:
        for cl in ext.clins:
            cl.confidence = min(cl.confidence, CROSS_FAIL_CAP)

    return ext
