"""How many hours a person is expected to work, and the utilisation that divides
by it (#84).

Runway used to divide hours by 40 and call the result utilisation. Two things were
wrong with that. Nobody bills 40 hours a week — a 2,080-hour year minus holidays,
PTO and unbillable time is where the standard 1,860–1,880 direct-hour target comes
from — so a *fully utilised* person read as ~85% and nobody was ever flagged as
available. And the matrix projects forward burn from hrs/wk, so seeding a planned
person at 40 assumed they bill every hour of every week forever, which overstates
forward burn and understates runway on a tool whose whole value is that number.

So expected hours is data, with a precedence, and this module is the one place that
resolves it. Server-side on purpose: `/api/people/utilization` renders the same word
as the allocation matrix, and a resolver living in JSX would have left the two
surfaces disagreeing about what "utilisation" means.

Pure — no imports from the rest of the app except the LCAT normaliser, which is how
every other lookup in the app folds a category string.

**Not a productivity metric.** Utilisation here is a capacity input for the
simulator: it says how many hours a person plausibly has, so the forward projection
is honest and "who is available" is answerable. Nothing in this module ranks people
or scores anyone against a target, and nothing built on it should either — the same
constraint #82 carries for per-person margin.
"""

from typing import Optional

from . import lcat as lcat_mod

# The denominator of an *FTE*, which is a different measure from utilisation and is
# the reason a bare 40 legitimately survives this ticket. One FTE is a 2,080-hour
# year — 40 hours × 52 weeks — by definition, so an FTE count is hours ÷ 40/wk and
# always was. Utilisation is hours ÷ *expected* hours. Dividing by 40 used to serve
# as both, which is the conflation #84 exists to separate; keep these two constants
# distinct even though they happen to share a value today.
FTE_HOURS_PER_WEEK = 40.0

# Where the precedence chain ends. Deliberately the old hardcoded number, so a
# contract nobody has configured behaves exactly as it did before this ticket —
# but it now arrives labelled `fallback` so every surface can say the number is an
# assumption rather than a setting.
FALLBACK_HOURS_PER_WEEK = 40.0

# A full week as a matter of physics, which is what the cross-contract overbooking
# check in `people.conflicts` compares against. A third constant sharing the value
# 40 — because it is a third distinct idea. Someone with a 32-hour expected week
# booked to 38 across two contracts is over their expectation but is not booked more
# hours than a week holds, and only the second is a scheduling impossibility. If the
# fallback below ever changes, this must not move with it.
PHYSICAL_WEEK_HOURS = 40.0

# A contract's default target when it has none of its own. 80% of a full week is the
# low end of the normal 80–90% GovCon range; erring low keeps the forward projection
# from overstating burn, which is the failure direction that costs money.
DEFAULT_UTILIZATION_TARGET = 0.80

# The levels of the chain, most specific first. Every resolution names the one that
# supplied its number — the UI has to be able to show which level answered, and a
# fallback can only be labelled as a fallback if it is tagged where it is chosen.
PERSON, LCAT, CONTRACT, FALLBACK = "person", "lcat", "contract", "fallback"

# A typo guard on expected hours, not a policy: past this it is a slip (a rate, a
# monthly total) rather than a week. Unrelated to AllocationMatrix's HOURS_CAP = 50,
# which is the suggester's ceiling.
MAX_EXPECTED_HOURS = 80.0

_LEVEL_LABELS = {
    PERSON: "this person's own expected week",
    LCAT: "the labor category's default",
    CONTRACT: "the contract's utilisation target",
    FALLBACK: "a 40-hour week, assumed — nothing is set",
}


def hours_value(value) -> Optional[float]:
    """Expected hours as a number, or None if it isn't one.

    Stored as TEXT in `person_attrs` like every other attr, so reading it has to
    parse. Unparseable is not-set, never zero: treating a bad value as 0 would make
    everyone infinitely utilised.
    """
    try:
        hours = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return hours if hours > 0 else None


def validate_expected_hours(value: str) -> Optional[str]:
    """Reject an expected-hours value that isn't a plausible week; None means fine.

    Blank never reaches here — an empty value is a delete, and clearing expected
    hours back to the contract default has to stay available.
    """
    raw = (value or "").strip()
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return (
            f"{raw!r} is not a number. Record expected hours per week as a number "
            'and put the reason in the source note — "32 · part-time, per offer '
            'letter".'
        )
    if hours <= 0:
        return (
            "Expected hours must be more than 0. To go back to the contract's "
            "default, clear the field instead."
        )
    if hours > MAX_EXPECTED_HOURS:
        return (
            f"Expected hours must be between 0 and {MAX_EXPECTED_HOURS:g} per week. "
            f"{raw} looks like a month's hours rather than a week's."
        )
    return None


def target_hours(target: Optional[float]) -> Optional[float]:
    """A utilisation target (0.8, or 80) as hours of a full week.

    Accepts either spelling because both turn up in the wild and in typed input: a
    fraction, or a percentage. Anything above 1 is read as a percentage — a target of
    "0.9 hours a week" is not a thing anyone means.
    """
    try:
        pct = float(target)
    except (TypeError, ValueError):
        return None
    if pct <= 0:
        return None
    if pct > 1:
        pct = pct / 100.0
    if pct > 1:
        return None
    return FALLBACK_HOURS_PER_WEEK * pct


def contract_capacity(contract: Optional[dict]) -> dict:
    """The capacity settings off a contract's data blob, normalised.

    Contract- and LCAT-level defaults live on the contract because that is what they
    describe; the per-person override lives on the global people directory because a
    part-time week is a property of the person, not of one of their contracts — the
    same argument #69 makes for quals.

    Keys are absent on every contract that predates this ticket, which is why every
    read goes through here and gets `{target: None, lcat_hours: {}}` rather than a
    KeyError.
    """
    blob = contract or {}
    raw = blob.get("lcat_expected_hours") or {}
    lcat_hours = {}
    if isinstance(raw, dict):
        for name, hours in raw.items():
            parsed = hours_value(hours)
            if parsed is None:
                continue
            # Keyed by the same fold the rate resolution uses, so a default typed
            # against "Program Manager (PMP)" still answers for a timesheet's
            # "Program Manager, PMP". Levels survive the fold (see lcat._LEVEL_TOKENS),
            # so a Mid default never answers for a Senior.
            lcat_hours[lcat_mod.normalize(name)] = parsed
    return {
        "target": blob.get("utilization_target"),
        "lcat_hours": lcat_hours,
    }


def resolve(
    person_hours=None,
    lcat: Optional[str] = None,
    contract: Optional[dict] = None,
    capacity: Optional[dict] = None,
) -> dict:
    """Expected hours for one person on one contract, and which level supplied it.

    person → this contract's LCAT default → this contract's target → 40, assumed.

    `capacity` is `contract_capacity(contract)` when the caller already computed it,
    which the allocation sweep does once per contract rather than once per employee.

    Returns `{hours, level, label}`. The label is prose for a tooltip; `level` is what
    code should branch on.
    """
    caps = capacity if capacity is not None else contract_capacity(contract)

    own = hours_value(person_hours)
    if own is not None:
        return _resolution(own, PERSON)

    if lcat:
        by_lcat = caps["lcat_hours"].get(lcat_mod.normalize(lcat))
        if by_lcat is not None:
            return _resolution(by_lcat, LCAT)

    from_target = target_hours(caps.get("target"))
    if from_target is not None:
        return _resolution(from_target, CONTRACT)

    return _resolution(FALLBACK_HOURS_PER_WEEK, FALLBACK)


def _resolution(hours: float, level: str) -> dict:
    return {
        "hours": round(float(hours), 2),
        "level": level,
        "label": _LEVEL_LABELS[level],
        # So a surface can mark the number as an assumption without knowing the names
        # of the levels.
        "assumed": level == FALLBACK,
    }


def utilization(actual_hours, expected_hours) -> Optional[float]:
    """Actual hours over expected hours. 1.0 means fully utilised, not 80%.

    None when there is nothing to divide by, which callers must render as "no
    number" rather than as 0% — an unset expectation is missing information, and
    reporting missing information as idle is the same failure mode #98 spelled out
    for an unrecorded clearance.
    """
    expected = hours_value(expected_hours)
    if expected is None:
        return None
    try:
        actual = float(actual_hours)
    except (TypeError, ValueError):
        return None
    return round(actual / expected, 4)


def fte(hours, weeks) -> Optional[float]:
    """Full-time equivalents: hours over a 40-hour week, for however many weeks.

    Kept separate from `utilization` on purpose. This is a headcount against the
    2,080-hour definition of a full-time year; utilisation is performance against a
    billable expectation. `hours / 40` served as both before this ticket, which is
    why the matrix could not answer either question honestly.
    """
    try:
        span = float(weeks)
    except (TypeError, ValueError):
        return None
    if span <= 0:
        return None
    try:
        worked = float(hours)
    except (TypeError, ValueError):
        return None
    return round(worked / (FTE_HOURS_PER_WEEK * span), 2)


def portfolio_expected(person_hours=None, per_contract: Optional[list] = None) -> dict:
    """One person's expected week across every contract they charge.

    A person's capacity belongs to the person, so their own override wins outright.
    With no override there is no honest single answer, and the two obvious
    aggregates are both wrong: summing two contracts' 32-hour defaults claims a
    64-hour person, and averaging them claims someone splitting two full-time
    expectations is only expected to work one. So this takes the **largest** of the
    per-contract resolutions — the widest week any contract they touch assumes — and
    reports the level it came from so the number is never presented as settled.

    The per-contract resolutions are what the allocation matrix shows; this is only
    for the cross-contract portfolio row. The physical-week overbooking check in
    `people.conflicts` deliberately does *not* use this — see that function.
    """
    own = hours_value(person_hours)
    if own is not None:
        return _resolution(own, PERSON)
    candidates = [r for r in (per_contract or []) if r and r.get("hours")]
    if not candidates:
        return _resolution(FALLBACK_HOURS_PER_WEEK, FALLBACK)
    best = max(candidates, key=lambda r: r["hours"])
    return _resolution(best["hours"], best.get("level") or FALLBACK)
