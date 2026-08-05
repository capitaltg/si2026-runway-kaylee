"""Dated absence as an input to the forward projection (#85).

The matrix used to project one flat hrs/wk to the end of the period. Real forward
burn is not flat, and known, dated, already-approved absence is the largest
predictable deviation from it: two people out in August under-burns their CLIN by
~80 hours, and the runway date the tool reports is wrong in a direction the PM
already knew about. The alternative users reach for — fudging hrs/wk to a blended
average — is wrong in a different way and unexplainable a month later.

**Forward-looking only.** Leave in the *past* is already handled: `burn.billable_hours`
backs it out of actuals (Part 1 of this ticket, PR #95), so the pace this module
reduces is leave-free before it gets here. Nothing in this module may adjust a week
that has already been charged — a light week in the past is history, not a
prediction, and rewriting it would corrupt the baseline the matrix reads from.

**Absence is measured in workdays, not hours.** A person out for a week loses their
*expected* week, which #84 established is not necessarily 40 hours (`capacity.resolve`).
Expressing absence as a fraction of workdays and applying it to whatever that person
already contributes keeps this true for the 32-hour part-timer without this module
needing to know their expected hours at all — and reintroducing a hardcoded 40 here
is exactly the bug #84 removed.

**Weighted by who is actually charging.** Contract-level absence reduces a CLIN's
pace in proportion to the absent person's share of that CLIN's recent billable
hours. Someone who bills 30% of a CLIN taking a week off removes 30% of that week's
burn, not all of it. Someone with no charges in the trailing window contributes
nothing to the pace, so their absence cannot reduce it — noted rather than fixed,
because inventing a share for them would mean inventing a rate.

Pure — no DB, no app imports. The contract blob is read in `contract_absence`, the
week math is calendar arithmetic, and the caller supplies the shares.
"""

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

# A working week for absence purposes. Deliberately *days*, not hours, so this
# never becomes a fourth spelling of `capacity`'s three 40s — those describe how
# many hours a week holds, this describes how many days absence can be taken on.
# A holiday removes one fifth of a week from everybody regardless of whether their
# expected week is 40 hours or 32.
WORKDAYS_PER_WEEK = 5

# What an entry is *called*. Purely a label for the UI — the dated range is always
# authoritative, and a start date is modelled as absence from the period's start
# until the person arrives, so all three of the ticket's kinds run through one
# mechanism and one code path.
PTO, HOLIDAY, START, ROLL_OFF = "pto", "holiday", "start", "roll_off"
KINDS = (PTO, START, ROLL_OFF)

_KIND_LABELS = {
    PTO: "PTO / leave",
    START: "not started yet",
    ROLL_OFF: "rolled off",
}

# A typo guard, not a policy: an absence longer than this is a data-entry slip (a
# swapped year, usually) rather than a plan. Two years of PTO is not a thing.
MAX_ABSENCE_DAYS = 730


def _d(value) -> Optional[date]:
    """A date out of whatever the blob holds. None for anything unparseable —
    never today, because defaulting a bad date to now would silently shift an
    absence onto the current week."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _workdays(start: date, end: date) -> List[date]:
    """Mon–Fri dates in the inclusive range. Weekends are excluded because nobody
    charges them, so counting a Saturday of PTO as absence would reduce a pace that
    never included it."""
    if not start or not end or end < start:
        return []
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Normalising what's stored on the contract
# ---------------------------------------------------------------------------


def normalize_absences(raw) -> List[dict]:
    """Per-person dated absences off the contract blob, cleaned and sorted.

    Silently drops entries that can't be read (no person, unparseable or inverted
    dates) rather than raising: this runs inside `burn.compute` on every page load,
    and one bad row must not take the Flight Deck down. The write path
    (`validate_absence`) is where a user hears about a bad entry.
    """
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        person = str(item.get("person_id") or "").strip()
        start, end = _d(item.get("start")), _d(item.get("end"))
        if not person or not start or not end or end < start:
            continue
        kind = str(item.get("kind") or PTO).strip().lower()
        out.append(
            {
                "person_id": person,
                "person": str(item.get("person") or "").strip() or person,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "kind": kind if kind in KINDS else PTO,
                "note": str(item.get("note") or "").strip(),
            }
        )
    return sorted(out, key=lambda a: (a["start"], a["person_id"]))


def normalize_holidays(raw) -> List[dict]:
    """Contract-level holidays off the blob: one dated entry each, deduped by date.

    Company-wide by definition, which is why they live on the contract rather than
    on each person — the ticket's "entered once, applies to everyone". Deduped
    because seeding the federal calendar twice must not remove two fifths of a week.
    """
    seen = {}
    for item in raw or []:
        if isinstance(item, dict):
            day, name = _d(item.get("date")), str(item.get("name") or "").strip()
        else:
            day, name = _d(item), ""
        if not day:
            continue
        seen.setdefault(day, name or "Holiday")
    return [{"date": d.isoformat(), "name": seen[d]} for d in sorted(seen)]


def contract_absence(contract: Optional[dict]) -> dict:
    """The absence settings off a contract's data blob, normalised.

    Same storage pattern as #84's capacity settings (`db.set_contract_capacity`):
    keys on the data blob, absent on every contract that predates this ticket, so
    every read goes through here and gets empty lists rather than a KeyError.

    **Contract-level, deliberately.** Holidays are a fact about the calendar, not
    about one what-if, so snapshotting them into a saved plan would leave old plans
    scored against a stale calendar — and the burn engine cannot read plan data
    anyway, so a holiday stored in a plan could never bend the Flight Deck's chart.
    The consequence, which the PR states plainly: editing the calendar changes what
    every saved plan projects. A saved plan stores only the per-person absences
    typed into that plan.
    """
    blob = contract or {}
    return {
        "holidays": normalize_holidays(blob.get("holidays")),
        "absences": normalize_absences(blob.get("absences")),
    }


def validate_absence(entry: dict) -> Optional[str]:
    """Reject an absence that can't mean anything; None means fine.

    Prose rather than a code, because these surface directly in the matrix next to
    the row the user was typing in.
    """
    if not isinstance(entry, dict):
        return "An absence must be a person and a date range."
    if not str(entry.get("person_id") or "").strip():
        return "An absence needs a person."
    start, end = _d(entry.get("start")), _d(entry.get("end"))
    if not start:
        return f"{entry.get('start')!r} is not a date. Use YYYY-MM-DD."
    if not end:
        return f"{entry.get('end')!r} is not a date. Use YYYY-MM-DD."
    if end < start:
        return "The end date is before the start date."
    if (end - start).days > MAX_ABSENCE_DAYS:
        return (
            f"That range is over {MAX_ABSENCE_DAYS // 365} years long — check the "
            "year on the end date."
        )
    return None


# ---------------------------------------------------------------------------
# Turning dates into a per-week factor
# ---------------------------------------------------------------------------


def week_window(
    pop_start: Optional[date], week: int
) -> Tuple[Optional[date], Optional[date]]:
    """The inclusive calendar range of a period week, 1-indexed.

    Matches `burn._clock`, which numbers week 1 as the seven days from `pop_start`.
    Returns (None, None) with no PoP start — a contract whose period has no dates
    has no calendar to hang absence off, and the caller must fall back to no
    absence rather than to a guessed anchor.
    """
    if not pop_start or week < 1:
        return (None, None)
    first = pop_start + timedelta(days=7 * (week - 1))
    return (first, first + timedelta(days=6))


def _days_by_person(absences: List[dict]) -> Dict[str, List[Tuple[date, date]]]:
    ranges: Dict[str, List[Tuple[date, date]]] = {}
    for a in absences or []:
        start, end = _d(a.get("start")), _d(a.get("end"))
        if not start or not end:
            continue
        ranges.setdefault(a.get("person_id") or "", []).append((start, end))
    return ranges


def week_factors(
    pop_start: Optional[date],
    first_week: int,
    last_week: int,
    holidays: Optional[List[dict]] = None,
    absences: Optional[List[dict]] = None,
    shares: Optional[Dict[str, float]] = None,
) -> List[dict]:
    """A multiplier on the forward pace for each week in `[first_week, last_week]`.

    1.0 is a normal week. 0.8 means one fifth of the week's burn does not happen.

    `shares` is `{person_id: fraction of this CLIN's recent billable hours}` — the
    caller's, because only the engine knows which rows priced. With no shares the
    only thing that can be known is the holiday calendar, which applies to everyone
    equally, so the factor falls back to holidays alone.

    Holidays and a person's own PTO are **unioned per person** before counting. A
    fortnight of PTO spanning July 4th removes ten workdays from that person, not
    eleven; adding the two separately would push their factor below zero and claim
    the contract earns money back over Independence Day.
    """
    holiday_days = {_d(h.get("date")) for h in (holidays or [])}
    holiday_days.discard(None)
    by_person = _days_by_person(absences or [])
    weights = {p: w for p, w in (shares or {}).items() if w > 0}
    total_weight = sum(weights.values())

    out = []
    for week in range(max(1, first_week), max(0, last_week) + 1):
        win_start, win_end = week_window(pop_start, week)
        workdays = _workdays(win_start, win_end)
        if not workdays:
            out.append({"week": week, "factor": 1.0, "holidays": [], "people": []})
            continue
        span = len(workdays)
        hol = [d for d in workdays if d in holiday_days]
        hol_set = set(hol)

        if not total_weight:
            # Nothing charged this CLIN recently, so no per-person share exists and
            # only the company-wide calendar is knowable.
            factor = 1.0 - len(hol_set) / span
            people = []
        else:
            factor = 0.0
            people = []
            for person, weight in weights.items():
                off = set(hol_set)
                for start, end in by_person.get(person, []):
                    off.update(d for d in workdays if start <= d <= end)
                factor += (weight / total_weight) * (1.0 - len(off) / span)
                if len(off) > len(hol_set):
                    people.append(person)
            # Anyone absent who never charged this CLIN is invisible here on
            # purpose: with no share of the pace there is no honest amount to
            # subtract, and assuming one would invent burn that was never observed.

        out.append(
            {
                "week": week,
                "factor": round(max(0.0, min(1.0, factor)), 4),
                "holidays": [h.isoformat() for h in sorted(hol_set)],
                "people": sorted(people),
            }
        )
    return out


def has_effect(factors: List[dict]) -> bool:
    """Whether any week in the range is actually reduced.

    The gate on emitting a projection at all: a contract with no absence in its
    remaining weeks must produce *no* series, so every surface keeps the exact
    geometry and the exact numbers it had before this ticket.
    """
    return any(f.get("factor", 1.0) < 1.0 for f in factors or [])


# ---------------------------------------------------------------------------
# The federal calendar
# ---------------------------------------------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth (1-indexed) given weekday of a month; n = -1 for the last one."""
    if n < 0:
        d = (
            date(year, 12, 31)
            if month == 12
            else date(year, month + 1, 1) - timedelta(days=1)
        )
        while d.weekday() != weekday:
            d -= timedelta(days=1)
        return d
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d + timedelta(days=7 * (n - 1))


def _observed(day: date) -> date:
    """The federal observance rule (5 U.S.C. 6103(b)): a fixed-date holiday on a
    Saturday is observed the Friday before, on a Sunday the Monday after. Applied
    because it is the observed day nobody charges, which is the day that matters to
    a burn projection."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def federal_holidays(year: int) -> List[dict]:
    """The eleven federal holidays for a year, on their observed dates.

    Seeded as a *default*, never imposed: the ticket's posture is that the tool must
    be usable by typing in the two or three absences that matter, and a contractor
    whose company observes a different calendar has to be able to delete these. So
    they are written into the contract's own holiday list on request, where they can
    then be edited, rather than being a hidden built-in the projection consults.
    """
    days = [
        (_observed(date(year, 1, 1)), "New Year's Day"),
        (_nth_weekday(year, 1, 0, 3), "Birthday of Martin Luther King, Jr."),
        (_nth_weekday(year, 2, 0, 3), "Washington's Birthday"),
        (_nth_weekday(year, 5, 0, -1), "Memorial Day"),
        (_observed(date(year, 6, 19)), "Juneteenth National Independence Day"),
        (_observed(date(year, 7, 4)), "Independence Day"),
        (_nth_weekday(year, 9, 0, 1), "Labor Day"),
        (_nth_weekday(year, 10, 0, 2), "Columbus Day"),
        (_observed(date(year, 11, 11)), "Veterans Day"),
        (_nth_weekday(year, 11, 3, 4), "Thanksgiving Day"),
        (_observed(date(year, 12, 25)), "Christmas Day"),
    ]
    return [{"date": d.isoformat(), "name": n} for d, n in sorted(days)]


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, _KIND_LABELS[PTO])
