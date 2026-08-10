"""Does the person filling a seat actually qualify for the rate it bills at (#66)?

Runway already flags an LCAT with no *price* (#64). This module asks the other
question — the one that gets money clawed back rather than mis-forecast: billing a
body at Senior Cyber SME rates who does not meet that category's minimum education,
years or clearance is a mischarging finding.

Both halves of the comparison already existed before this module and neither is
authored here:

  the floor    what the award prints beside a rate line   `lcat.Floors` (#66 ingest)
  the credential  what a human typed about a person       `people` attrs (#98)

Two rules run through everything below.

**Missing information is never a finding.** A floor we cannot read, a credential
nobody entered, a value that predates the vocabularies — each makes a field
*unchecked*, never "does not meet". Reporting an unchecked person as clear is the
worst failure mode a compliance feature has; reporting them as in breach is the
second worst, and it is the one that gets the feature switched off.

**One satisfied field never carries the others.** Someone with 12 years entered and
no education on file is checked on years and unchecked on education. There is no
partial credit and no rounding up to `compliant`.

This module is pure and takes no database handle: `allocation.py` may not reach the
people directory (see the invariant in `people.py`), so the caller reads the quals
and passes them in, exactly as it does for #84's expected hours.
"""

from typing import List, Optional

from . import people

# Per-person verdicts, worst-first. `no_floor` sits at the bottom deliberately: it is
# not a better result than `compliant`, it is the absence of a question, and a
# contract whose award prints no minimums must not read as a contract that passed.
CLEARANCE_GAP = "clearance_gap"
UNDER_QUALIFIED = "under_qualified"
UNKNOWN = "unknown"
OVER_QUALIFIED = "over_qualified"
COMPLIANT = "compliant"
NO_FLOOR = "no_floor"
# Kept apart from `no_floor` because the two have different owners and different fixes.
# `no_floor` is a rate schedule that priced a category without printing its minimums —
# a gap in the award document. `unpriced` is #64's problem arriving here: the hours
# don't resolve to a priced line at all, so there is no category to look minimums up
# on. Rolled together they produce a sentence ("N on lines with no printed minimums")
# that is false for half of what it counts.
UNPRICED = "unpriced"

# Ranked for "which of this person's cells do we put on their row". Same order the
# rollup counts in and the UI sorts by.
SEVERITY = (
    CLEARANCE_GAP,
    UNDER_QUALIFIED,
    UNKNOWN,
    OVER_QUALIFIED,
    COMPLIANT,
    NO_FLOOR,
    UNPRICED,
)

# A field is checked, short, or neither — and when it is neither, *why* decides who
# fixes it. `no_value` is the user's to fill in; `no_floor` and `floor_not_comparable`
# are the award document's problem and no amount of typing about a person clears them.
MET, SHORT, UNCHECKED = "met", "short", "unchecked"
NO_VALUE = "no_value"
FLOOR_NOT_COMPARABLE = "floor_not_comparable"
VALUE_NOT_COMPARABLE = "value_not_comparable"

# The qual field a floor is compared against. Deliberately explicit rather than
# derived from a naming convention — the award calls it `clearance` and the person
# calls it `clearance`, but `min_experience_yrs` and `years_experience` do not line
# up by accident and should not line up by string munging.
FIELD_FLOORS = (
    ("education", "min_education"),
    ("years_experience", "min_experience_yrs"),
    ("clearance", "clearance"),
)

# Human labels, served rather than restated in JSX for the same reason the
# vocabularies are (#98).
FIELD_LABELS = {
    "education": "Education",
    "years_experience": "Years of experience",
    "clearance": "Clearance",
}


def _qual_value(quals: Optional[dict], field: str) -> Optional[str]:
    """The stored value for one qual field, from the directory's attr shape.

    Accepts both the annotated shape the API serves (`{"value": ..., "source_note":
    ...}`) and a bare string, so a caller with a flat dict does not have to wrap it.
    """
    entry = (quals or {}).get(field)
    if isinstance(entry, dict):
        entry = entry.get("value")
    value = str(entry).strip() if entry is not None else ""
    return value or None


def _check_ranked(held: Optional[str], required: Optional[str], rank) -> dict:
    """One closed-vocabulary field: education or clearance.

    Rank is index-in-ladder, so the test is "at least as high", not equality — a
    Master's clears a Bachelor's floor and TS/SCI clears a Secret one.
    """
    if required is None:
        return {"state": UNCHECKED, "reason": NO_FLOOR}
    req_rank = rank(required)
    if req_rank is None:
        # The award printed something off the ladder ("BS/BA or equivalent"). Not the
        # person's problem and not assertable either way.
        return {"state": UNCHECKED, "reason": FLOOR_NOT_COMPARABLE}
    if held is None:
        return {"state": UNCHECKED, "reason": NO_VALUE}
    held_rank = rank(held)
    if held_rank is None:
        # Grandfathered free text from before #98 closed the vocabularies.
        return {"state": UNCHECKED, "reason": VALUE_NOT_COMPARABLE}
    return {"state": MET if held_rank >= req_rank else SHORT}


def _check_years(held: Optional[str], required) -> dict:
    if required is None:
        return {"state": UNCHECKED, "reason": NO_FLOOR}
    req = people.years_value(required)
    if req is None:
        return {"state": UNCHECKED, "reason": FLOOR_NOT_COMPARABLE}
    if held is None:
        return {"state": UNCHECKED, "reason": NO_VALUE}
    got = people.years_value(held)
    if got is None:
        return {"state": UNCHECKED, "reason": VALUE_NOT_COMPARABLE}
    return {"state": MET if got >= req else SHORT}


def check_fields(quals: Optional[dict], floors) -> List[dict]:
    """Per-field verdicts for one person against one rate line's floors.

    Always three entries, always in the same order, whatever is or isn't known — the
    UI's "what's required, what's known" panel needs the unchecked rows as much as the
    failing ones, because they are the ones with something to type into.
    """
    required = {
        "min_education": getattr(floors, "min_education", None),
        "min_experience_yrs": getattr(floors, "min_experience_yrs", None),
        "clearance": getattr(floors, "clearance", None),
    }
    out = []
    for field, floor_key in FIELD_FLOORS:
        held = _qual_value(quals, field)
        req = required.get(floor_key)
        if field == "years_experience":
            verdict = _check_years(held, req)
        elif field == "education":
            verdict = _check_ranked(held, req, people.education_rank)
        else:
            verdict = _check_ranked(held, req, people.clearance_rank)
        out.append(
            {
                "field": field,
                "label": FIELD_LABELS[field],
                "required": req,
                "held": held,
                **verdict,
            }
        )
    return out


def _meets_fully(quals: Optional[dict], floors) -> bool:
    """True only if every floor this line prints is checked and met.

    Used for the over-qualified sweep, where the bar has to be higher than the
    headline check's: suggesting someone is worth a more expensive category on
    partial evidence is exactly the kind of confident-and-wrong the module avoids.
    """
    fields = check_fields(quals, floors)
    if not any(f["required"] is not None for f in fields):
        return False
    return all(
        f["state"] == MET
        for f in fields
        if f["required"] is not None and f.get("reason") != FLOOR_NOT_COMPARABLE
    ) and not any(f["state"] == SHORT for f in fields)


def over_qualified_for(
    quals: Optional[dict], line, candidates: Optional[List] = None
) -> Optional[dict]:
    """The best-paid line whose floors this person fully clears, if it out-pays theirs.

    Not a violation — usually money left on the table, and free to compute from the
    same join. Ordered by rate rather than by floor height because the reason to
    surface it is the dollars, and because two lines can print the same minimums at
    different prices.
    """
    if not candidates or line is None:
        return None
    current = getattr(line, "rate", None)
    if current is None:
        return None
    better = [
        c
        for c in candidates
        if getattr(c, "rate", None) is not None
        and c.rate > current
        and getattr(c, "key", None) != getattr(line, "key", None)
        and _meets_fully(quals, getattr(c, "floors", None))
    ]
    if not better:
        return None
    best = max(better, key=lambda c: c.rate)
    return {"lcat": best.lcat, "clin": best.clin, "rate": round(best.rate, 2)}


def check(
    quals: Optional[dict],
    line,
    candidates: Optional[List] = None,
) -> dict:
    """One person against the rate line their hours actually bill at.

    `line` is a resolved `lcat.RateLine` — the resolution, not a name lookup. #64 is
    a hard dependency of this check for that reason: a verdict against the wrong rate
    line is worse than no verdict, and re-matching by LCAT string here would throw
    away the user's confirmed aliases and re-earn the ambiguity #64 refuses.

    `candidates` are the other priced lines on the CLIN, for the over-qualified sweep.
    """
    if line is None:
        # No priced line backs these hours, so there is no floor to check against.
        # #64 already reports that as an unmatched LCAT; it is not a quals finding.
        return {
            "status": UNPRICED,
            "fields": check_fields(quals, None),
            "failures": [],
            "unchecked": [],
            "quals_status": people.quals_status(_flat(quals)),
            "line": None,
            "over_qualified_for": None,
        }

    floors = getattr(line, "floors", None)
    fields = check_fields(quals, floors)
    failures = [f for f in fields if f["state"] == SHORT]
    # Only floors that exist can be *awaiting* a credential. A field with no floor is
    # not an outstanding task for anybody, and counting it as one would put every
    # contract permanently in "not yet checked".
    unchecked = [
        f for f in fields if f["state"] == UNCHECKED and f.get("reason") != NO_FLOOR
    ]
    has_floor = any(f["required"] is not None for f in fields)

    if any(f["field"] == "clearance" for f in failures):
        status = CLEARANCE_GAP
    elif failures:
        status = UNDER_QUALIFIED
    elif not has_floor:
        status = NO_FLOOR
    elif unchecked:
        status = UNKNOWN
    else:
        status = COMPLIANT

    better = (
        over_qualified_for(quals, line, candidates) if status == COMPLIANT else None
    )
    if better:
        status = OVER_QUALIFIED

    return {
        "status": status,
        "fields": fields,
        "failures": [
            {
                "field": f["field"],
                "label": f["label"],
                "required": f["required"],
                "held": f["held"],
            }
            for f in failures
        ],
        "unchecked": [
            {"field": f["field"], "label": f["label"], "reason": f["reason"]}
            for f in unchecked
        ],
        "quals_status": people.quals_status(_flat(quals)),
        "line": {
            "lcat": line.lcat,
            "clin": line.clin,
            "rate": round(line.rate, 2) if line.rate is not None else None,
            "floors": floors.payload() if floors is not None else {},
        },
        "over_qualified_for": better,
    }


def _flat(quals: Optional[dict]) -> dict:
    """The directory's annotated attr shape flattened to `{field: value}`."""
    return {f: _qual_value(quals, f) for f in people.QUAL_FIELDS}


def quals_coverage(quals: Optional[dict]) -> str:
    """`complete` / `partial` / `unknown` for one person, floors not involved.

    Re-exported through this module so a caller that must not import the directory
    (`allocation.py`) can still say how much is on file, without a second definition
    of what "complete" means.
    """
    return people.quals_status(_flat(quals))


def worst(statuses) -> Optional[str]:
    """The status that represents a set of them — a person across their CLINs, say.

    Worst-wins rather than most-common: a clearance gap on one CLIN is the headline
    even if the same person is clean on three others.
    """
    present = [s for s in statuses if s in SEVERITY]
    if not present:
        return None
    return min(present, key=SEVERITY.index)


def rollup(verdicts) -> dict:
    """Counts for a CLIN or a contract, with the two denominators kept apart.

    The one number this must never produce is a percentage over the checked subset
    presented as covering the population. "3 of 11 checked people under-qualified"
    and "29 not yet checked" are different facts and the copy says both, so `checked`
    and `people` are reported separately and no ratio is computed here at all.
    """
    counts = {s: 0 for s in SEVERITY}
    for v in verdicts or []:
        status = v if isinstance(v, str) else (v or {}).get("status")
        if status in counts:
            counts[status] += 1
    total = sum(counts.values())
    # Checked means a real verdict came back: we knew the floor and we knew enough
    # about the person to compare. `unknown` and `no_floor` are the unchecked halves.
    checked = (
        counts[COMPLIANT]
        + counts[OVER_QUALIFIED]
        + counts[UNDER_QUALIFIED]
        + counts[CLEARANCE_GAP]
    )
    return {
        "people": total,
        "checked": checked,
        "not_checked": counts[UNKNOWN],
        "no_floor": counts[NO_FLOOR],
        "unpriced": counts[UNPRICED],
        "compliant": counts[COMPLIANT],
        "over_qualified": counts[OVER_QUALIFIED],
        "under_qualified": counts[UNDER_QUALIFIED],
        "clearance_gap": counts[CLEARANCE_GAP],
        # The one flag the Flight Deck and the suggests solver read, so neither has to
        # re-derive "is there anything to act on here" from six counters.
        "has_findings": bool(counts[UNDER_QUALIFIED] or counts[CLEARANCE_GAP]),
    }
