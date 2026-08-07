"""Named, person-level moves that close a CLIN's weekly gap (#63).

The Flight Deck used to answer an off-pace CLIN with one CLIN-level paragraph —
"trim the off-pace lines back to plan" — and a button that scaled everybody's hours
by a uniform factor. That is a description of arithmetic, not a decision a PM can
take. `docs/design/Runway.dc.html:1132` specifies the real thing: an ordered list of
concrete moves that name people ("Roll Aisha Khan to the bench", "Trim Dana, Marcus
& Sofia to 24 hrs/wk"), and a one-line result in the form
`Forward burn $X/wk → $Y/wk · lands week Z of N`.

## Why this is on the server

#83 put the "who's running hot" ranking in `heat.py` specifically so two surfaces
could not name different people on the same contract. A solver in JSX would
reintroduce exactly that: the Flight Deck's advice and the matrix's `Apply fix`
would each derive their own move list and drift. So the *decision* is made here,
once, and the client only renders it and applies it into the draft grid. The AI
phrasing layer grounds on this same list, which is what makes AI-on and AI-off
recommend identical actions rather than merely similar ones.

## Scoring is by hours moved, never by dollars saved

The ticket's step 3 says "score by `$ closed per person disrupted`". Taken literally
that reinstates the bias #83 removed in `64dfa26`: the cheapest way to close a dollar
gap is always to cut the most expensive person, so a dollar-ranked solver is a pay
ranking wearing a staffing plan's clothes. Candidates are therefore ordered by
**hours moved**, which is rate-neutral, and dollars are used only to decide *when the
gap is closed* — a threshold, not a rank. Two people equally over their hours are
equally over, whatever they bill.

## The diagnosis picks the remedy; this module does not re-derive it

`heat.py` already decided whether a CLIN is over because of hours above plan
(`stop_overtime`) or because there is more staffing on it than the remaining budget
funds (`reduce_staffing`), by comparing two forward projections. Getting that
backwards prescribes cutting a team that is already at plan, so:

  * `stop_overtime`   → the only legal move is bringing someone back to their
                        expected week. Never a roll-off, never below expectation.
  * `reduce_staffing` → real roll-offs, trims and shifts are on the table.

## Floors are expected hours, not 40-hour-week stops

The ticket suggests trim stops of 32/24/20/16. Those are the flat-40 assumption #84
removed. Every floor here is the person's own resolved expected week
(`capacity.resolve()`, carried on the allocation row) apportioned to this CLIN, so a
32-hour part-timer is never "trimmed" to 32 as though that were a concession.

## Two kinds of missing rate, and only one of them blocks scoring

`cells[clin].unmatched` means the LCAT had no printed rate line and the hours are
priced by the blended fallback — the dollars are approximate but real, and a move
that clears those hours also clears a compliance flag, which is the design's "also
clears the LCAT flag" and is used as a tie-break. `rate is None` is different: there
is no price at all (#64). Those hours are real and are deliberately still reported,
but no rate may be invented to score them, so they never count toward closing the gap
and are surfaced as an explicit unknown instead.

Pure — no DB, no HTTP. Takes `allocation.compute_allocation`'s payload and
`heat.compute_heat`'s, so every number here is one the matrix and the Flight Deck
already agree on.
"""

import math
from typing import List, Optional

from .heat import REDUCE_STAFFING, STOP_OVERTIME

# Mirrors AllocationMatrix.jsx's HOURS_CAP. Nobody is ever booked past this, in
# either direction — a solver that "closes" an underburn by putting someone on a
# 60-hour week has just written next quarter's overtime finding.
HOURS_CAP = 50

# A move has to shift at least this many hrs/wk to be worth printing. Below it the
# bullet is rounding noise on a trailing-window average, and a list that says "trim
# Dana to 39.6 hrs/wk" trains people to stop reading the list.
MIN_MOVE_HOURS = 0.5

# CLIN states this solves for. `over`/`watch` reduce; `under` raises. Kept aligned
# with AllocationMatrix's OFF_PACE set so the strip and the toolbar agree on which
# lines are even in play.
REDUCE_STATES = ("over", "watch")
RAISE_STATES = ("under",)

ROLL_OFF = "roll_off"
TRIM = "trim"
SHIFT = "shift"
RAISE = "raise"


def _f(value) -> float:
    """A float, treating None and junk as 0."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _norm(lcat) -> str:
    return (lcat or "").strip().lower()


def _moneyM(n) -> str:
    """`$1.50M`, matching the client's `moneyM` so a note reads like the copy
    around it. The only place this module formats dollars — everything else hands
    raw numbers to the prose layer."""
    return f"${_f(n) / 1e6:.2f}M"


def _booked(row: dict) -> float:
    """A person's hrs/wk across every contract Runway can see (#116).

    Their expected week is a whole-person figure, so anything measured against it has
    to be a whole-person figure too. Falls back to this contract's hours when the
    payload was built without the cross-contract sweep — the old, contract-blind
    behaviour, kept only so a hand-built payload still solves.
    """
    booked = row.get("hours_booked")
    return _f(booked) if booked is not None else _f(row.get("hours"))


def _headroom(row: dict) -> float:
    """Hours this person actually has left before their expected week is full."""
    if row.get("headroom") is not None:
        return _f(row.get("headroom"))
    return _f((row.get("expected") or {}).get("hours")) - _booked(row)


def _priced_lcats(card: dict) -> set:
    """The categories a CLIN actually prices, for deciding whether a shift is real.
    Proposing that someone move to a line that cannot pay their category is how you
    close a dollar gap on paper and open an unmatched-LCAT flag in the same move."""
    return {_norm(line.get("lcat")) for line in (card.get("rate_lines") or [])}


def _group_moves(moves: List[dict]) -> List[dict]:
    """Collapse identical decisions into one bullet.

    Three people trimmed to the same hours on the same CLIN is *one* decision a PM
    makes once, and the design writes it that way ("Trim Dana, Marcus & Sofia to 24
    hrs/wk"). Grouping on (kind, target, destination) rather than on kind alone keeps
    two genuinely different trims from being blurred into one number.
    """
    groups = []
    index = {}
    for m in moves:
        key = (m["kind"], m.get("to_hours"), m.get("to_clin"))
        g = index.get(key)
        if g is None:
            g = {
                "kind": m["kind"],
                "to_hours": m.get("to_hours"),
                "to_clin": m.get("to_clin"),
                "people": [],
                "person_ids": [],
                "hours_moved": 0.0,
                "weekly_dollars": 0.0,
                "dollars_unknown": False,
                "clears_lcat_flag": False,
                "floor": m.get("floor"),
            }
            index[key] = g
            groups.append(g)
        g["people"].append(m["person"])
        g["person_ids"].append(m["person_id"])
        g["hours_moved"] += m["hours_moved"]
        if m.get("dollars_unknown"):
            g["dollars_unknown"] = True
        else:
            g["weekly_dollars"] += _f(m.get("weekly_dollars"))
        g["clears_lcat_flag"] = g["clears_lcat_flag"] or bool(m.get("clears_lcat_flag"))
    for g in groups:
        g["hours_moved"] = round(g["hours_moved"], 1)
        g["weekly_dollars"] = round(g["weekly_dollars"], 2)
    return groups


def _candidate(kind, row, cell, cid, to_hours, *, floor=None, to_clin=None):
    """One concrete move, or None if it isn't worth a bullet.

    `weekly_dollars` is what the move takes off (or adds to) this CLIN's forward
    burn. `dollars_unknown` is the #64 case — real hours at no printed price — and it
    is deliberately not zero-filled: zero would read as "this move is free", when the
    truth is that nobody knows what it is worth.
    """
    from_hours = round(_f(cell.get("hours")), 1)
    to_hours = round(to_hours, 1)
    moved = abs(from_hours - to_hours)
    if moved < MIN_MOVE_HOURS:
        return None
    rate = cell.get("rate")
    return {
        "kind": kind,
        "clin": cid,
        "person_id": row["id"],
        "person": row.get("name"),
        "lcat": cell.get("lcat") or row.get("lcat"),
        "from_hours": from_hours,
        "to_hours": to_hours,
        "hours_moved": round(moved, 1),
        "to_clin": to_clin,
        "weekly_dollars": round(moved * float(rate), 2) if rate else None,
        "dollars_unknown": not rate,
        # The blended-fallback case: these hours are priced, but not by a printed
        # rate line. Clearing them off the CLIN closes a compliance flag as a side
        # effect, which is free to compute and is why it breaks ties.
        "clears_lcat_flag": bool(cell.get("unmatched")),
        "floor": floor,
        "expected_hours_per_week": round(
            _f((row.get("expected") or {}).get("hours")), 1
        ),
    }


def _file_move(m: Optional[dict], bucket: List[dict], unpriced: dict) -> bool:
    """Route a candidate to its bucket, or to the unpriced set when it cannot be
    scored. Returns whether it was filed at all — `_candidate` drops moves too small
    to print. Unpriced candidates are keyed by person because "we cannot price this
    person's hours" is one fact about them, not one per candidate they generated."""
    if m is None:
        return False
    if m["dollars_unknown"]:
        unpriced.setdefault(m["person_id"], m)
    else:
        bucket.append(m)
    return True


def _order(candidates: List[dict]) -> List[dict]:
    """Rank by hours moved, then by whether the move also clears an LCAT flag, then
    by name for determinism.

    Note what is *not* in this key: the rate, and the dollars derived from it. That is
    the whole point — see the module docstring. `name` last means the same contract
    always produces the same list, which is what lets a test pin the order.
    """
    return sorted(
        candidates,
        key=lambda m: (
            -m["hours_moved"],
            not m["clears_lcat_flag"],
            m["person"] or "",
        ),
    )


def _take_until_closed(candidates: List[dict], gap: float, taken: dict) -> float:
    """Greedily accept moves until the dollar gap is covered, skipping anyone already
    moved. Returns the dollars freed.

    Unpriced moves are *not* accepted here. They cannot be scored, so letting one into
    the set would either overstate the closure (if counted at zero it closes nothing
    while consuming a person) or invent a price (if counted at all). They are reported
    separately instead.
    """
    freed = 0.0
    for m in _order(candidates):
        if freed >= gap:
            break
        if m["person_id"] in taken or m["dollars_unknown"]:
            continue
        taken[m["person_id"]] = m
        freed += _f(m["weekly_dollars"])
    return freed


def _reduce_plan(card, heat_clin, rows_on_clin, cid, target, weekly, under_cards):
    """Build the ordered move list for an over/watch CLIN."""
    gap = weekly - target
    # The remedy comes from #83's diagnosis and is never re-derived here. A CLIN with no
    # diagnosis (no one over, or a payload without the field) defaults to the staffing
    # reading, which is the one that keeps every option open.
    diagnosis = (heat_clin or {}).get("diagnosis") or REDUCE_STAFFING

    gentle: List[dict] = []
    escalation: List[dict] = []
    # Keyed by person: an unpriced person generates a trim *and* a roll-off candidate
    # like anyone else, but the note this feeds says "we cannot price this person's
    # hours", which is one fact about them and not one per rejected candidate.
    unpriced: dict = {}

    for row, cell in rows_on_clin:
        cell_hours = _f(cell.get("hours"))
        # Cross-contract (#116), because the expectation being apportioned is a
        # whole-person week. Scoped to this contract the share inflated toward 1.0 and
        # `at_expected` landed at the full expectation against a part-time booking, so
        # a person genuinely over their week — 30 here, 30 elsewhere, 40 expected —
        # was offered no trim at all.
        total_hours = _booked(row) or cell_hours or 1.0
        expected_wk = _f((row.get("expected") or {}).get("hours"))

        # The at-expected level for *this* CLIN: the person's whole week scaled back to
        # their expectation, apportioned by where their hours actually are. A person's
        # expected week is a total across every line they bill, so someone at 50 hrs/wk
        # with 30 on this CLIN and a 40-hour expectation lands at 24 here — their share
        # of the trim — rather than having the entire excess charged to whichever line
        # happens to be off pace.
        #
        # Deliberately NOT built from #83's `over_hours_per_week`, even though that is
        # the excess this whole feature is about. That figure is measured against hours
        # *available* in the trailing window — expected hours minus recorded leave and
        # holidays — which is the right question for a report about what already
        # happened and the wrong one for a forward plan. On the local contract 4 it puts
        # Glenn Medina 1 hr/wk "over" because a month with leave in it only offered him
        # 24 hours, while his forward rate is 25 hrs/wk against a 40-hour expectation:
        # apportioning that trailing excess against a forward billing rate proposed
        # trimming him to 24 hrs/wk permanently because he took leave last month. The
        # floor for a forward move has to be the forward expectation.
        #
        # `ceil` on purpose: rounding to whole hours (the unit the grid edits in) must
        # land at or *above* the expectation, never a rounding step below it.
        at_expected = None
        if expected_wk:
            share = cell_hours / total_hours if total_hours else 0.0
            at_expected = float(math.ceil(expected_wk * share))

        if diagnosis == STOP_OVERTIME:
            # The diagnosis says this line finishes inside budget once people are back
            # to their expected week. So that is the entire move set: no roll-offs, no
            # shifts, nothing below expectation. Anyone at or under their expectation
            # is not part of this remedy and is left alone.
            if at_expected is None or at_expected >= cell_hours:
                continue
            _file_move(
                _candidate(TRIM, row, cell, cid, at_expected, floor="expected"),
                gentle,
                unpriced,
            )
            continue

        # reduce_staffing: the line runs out early even at expected hours, so hours
        # have to leave it. Gentlest first — a shift keeps the person billable and
        # moves money to a line that needs spending, which is strictly better than
        # benching someone to fix a budget.
        #
        # Only `under` lines are destinations, deliberately not `paused` ones. A paused
        # CLIN has no charges at all, and the reason is usually that the work is not
        # authorised yet — an unexercised option, a line awaiting a mod. Runway cannot
        # tell that apart from "nobody got around to it", and a suggestion to bill an
        # unauthorised line is worse than no suggestion.
        shifted = False
        for dest in under_cards:
            if dest["id"] == cid:
                continue
            lcat = _norm(cell.get("lcat"))
            if lcat and lcat in _priced_lcats(dest):
                shifted = _file_move(
                    _candidate(SHIFT, row, cell, cid, 0.0, to_clin=dest["id"]),
                    gentle,
                    unpriced,
                )
                break
        if shifted:
            continue

        if at_expected is not None and at_expected < cell_hours:
            _file_move(
                _candidate(TRIM, row, cell, cid, at_expected, floor="expected"),
                gentle,
                unpriced,
            )

        # The escalation rung, only reached if the gentle set cannot close the gap.
        _file_move(_candidate(ROLL_OFF, row, cell, cid, 0.0), escalation, unpriced)

    taken: dict = {}
    freed = _take_until_closed(gentle, gap, taken)
    escalated = False
    if freed < gap and escalation:
        # Re-run the ladder with roll-offs available. A person already trimmed is
        # replaced by their roll-off rather than counted twice — the grid can only
        # hold one target number per (person, CLIN).
        for m in _order(escalation):
            if freed >= gap:
                break
            if m["dollars_unknown"]:
                continue
            prior = taken.get(m["person_id"])
            if prior:
                if prior["kind"] == ROLL_OFF or prior["kind"] == SHIFT:
                    continue
                freed -= _f(prior["weekly_dollars"])
            taken[m["person_id"]] = m
            freed += _f(m["weekly_dollars"])
            escalated = True

    moves = _order(list(taken.values()))
    return moves, freed, gap, diagnosis, unpriced, escalated


def _raise_plan(card, rows_on_clin, cid, target, weekly):
    """The mirror image for an underburning CLIN — the same solver with the sign
    flipped, per the ticket's note.

    The ceiling here is each person's *expected* week, not `HOURS_CAP`. Filling an
    underburn by booking people past their expectation would close this finding by
    manufacturing the one #83 reports, on the same dashboard, about the same people.
    Real slack — someone billing under their expected week — is fair game; past that
    the honest answer is that the line needs another body, not a longer week.
    """
    gap = target - weekly
    candidates: List[dict] = []
    unpriced: dict = {}
    for row, cell in rows_on_clin:
        # Headroom is measured against what they are booked *everywhere* (#116).
        # Against this contract alone, someone at 20 hrs/wk here and 20 on another
        # contract reads as 20 hours of slack — and the other contract's payload does
        # the identical sum, so the same 20 hours get offered to two underburning
        # lines and this function books a 60-hour week while promising it never books
        # anyone past their expectation.
        headroom = _headroom(row)
        if headroom <= 0:
            continue
        to_hours = min(_f(cell.get("hours")) + headroom, HOURS_CAP)
        _file_move(
            _candidate(RAISE, row, cell, cid, to_hours, floor="expected"),
            candidates,
            unpriced,
        )

    taken: dict = {}
    freed = _take_until_closed(candidates, gap, taken)
    moves = _order(list(taken.values()))
    return moves, freed, gap, unpriced


def _ceiling_notes(heat_payload, cid, moves) -> List[str]:
    """The hours ceiling is a second, non-dollar constraint (#83's `hours_ceilings`),
    and it is deliberately *reported* rather than enforced.

    `est_hours` semantics vary across awards — contract 4's estimates 2,080 hours for a
    category that has charged 5,882, because the figure is scoped to one FTE-year and
    not to the team. Treating a number that unreliable as a hard constraint would have
    the solver confidently demand cuts that aren't real. So a move set closes the
    dollar gap, and if a category is still outside its contracted hours the suggestion
    says so and leaves the judgement to the PM.
    """
    notes = []
    for h in heat_payload.get("hours_ceilings") or []:
        if h.get("clin") != cid or not h.get("early"):
            continue
        who = f"{h['lcat']} hours" if h.get("lcat") else "contracted hours"
        if h.get("overrun_hours"):
            notes.append(
                f"{who} on this CLIN are already {round(h['overrun_hours']):,} over the "
                f"{round(h['contracted_hours']):,} the award estimates — closing the "
                f"dollar gap does not fix that."
            )
        else:
            notes.append(
                f"{who} still run out around week {round(h['exhaust_week'])} of "
                f"{round(_f(heat_payload.get('total_weeks')))} even after these moves."
            )
    return notes


def _funding_limited(card: dict) -> bool:
    """True when this line's shortfall is an unobligated slice, not a spending problem.

    Three conditions, and the middle one is the one this guard originally got wrong.

    The ceiling must actually hold. Headroom under the funded slice does *not* settle
    that on its own: a line can carry an unobligated slice and still be projected past
    its ceiling, and then a mod is not the remedy — the ceiling is a hard limit and no
    obligation raises it. Live contract 12 is exactly that shape ($277K unobligated
    beneath a $4.17M ceiling it is projected to blow by week 35 of 52), and on headroom
    alone this function called it a funding matter and withheld the staffing plan a
    genuine breach needs. So the engine's own verdict decides it.

    Headroom must also beat a week of burn: a line that has eaten its ceiling to within
    days of the end is a ceiling story whether or not the last increment happens to be
    the binding number.
    """
    if not card.get("incrementally_funded"):
        return False
    if card.get("ceiling_breached", True):
        return False
    headroom = _f(card.get("ceiling")) - _f(card.get("budget"))
    return headroom > _f(card.get("base_weekly"))


def _funding_limited_plan(card: dict, cid: str, total_weeks: float) -> dict:
    """A plan that declines to move anyone, and says why.

    Withdrawing the move list is not the same as having nothing to say, and the
    difference matters more here than anywhere else in this module. Returning nothing
    routes the client to its CLIN-level fallback paragraph, which is "trim the off-pace
    lines back to plan" — the exact staffing advice this branch exists to prevent. So
    the plan is emitted with an empty move list and a note, the same shape the
    stop-billing case above uses, and `funding_limited` tells the prose layer to
    recommend the mod instead of a trim.
    """
    headroom = _f(card.get("ceiling")) - _f(card.get("budget"))
    return {
        "clin": cid,
        "direction": "reduce",
        "diagnosis": None,
        "weekly": round(_f(card.get("base_weekly")), 2),
        "target_weekly": None,
        "gap_weekly": 0.0,
        "freed_weekly": 0.0,
        "new_weekly": round(_f(card.get("base_weekly")), 2),
        "exhaust_week": card.get("base_exhaust_week"),
        "new_exhaust_week": card.get("base_exhaust_week"),
        "total_weeks": round(total_weeks, 2) if total_weeks else None,
        "closed": False,
        "shortfall_weekly": 0.0,
        "escalated": False,
        "moves": [],
        "groups": [],
        "unpriced": [],
        # The dollars that make this an obligation gap rather than an overrun: ceiling
        # the contract holds but has not yet been given the money to spend.
        #
        # `ceiling` and `overspent` ride along because the Flight Deck's tripwire item
        # does NOT carry them — it ships `funded`/`budget` and the percentages, so prose
        # reaching for `item.ceiling` renders "$0.00M". This plan is the one place that
        # already holds both numbers, so it hands them over rather than making the
        # client find a second source for the same contract.
        "funding_limited": True,
        "ceiling_headroom": round(headroom, 2),
        "ceiling": round(_f(card.get("ceiling")), 2),
        "funded": round(_f(card.get("budget")), 2),
        # Positive only once spend has passed the obligation — the realized case, where
        # the money is already gone rather than merely forecast to go.
        "overspent": round(max(0.0, _f(card.get("spent")) - _f(card.get("budget"))), 2),
        "notes": [
            "This line is short an obligation, not overstaffed — there is still "
            f"{_moneyM(headroom)} of ceiling beneath its funded slice, so no staffing "
            "move is proposed. The fix is the next incremental-funding mod."
        ],
    }


def solve_moves(alloc: dict, heat_payload: dict) -> List[dict]:
    """One plan per off-pace CLIN: ordered named moves, grouped, with the result line.

    Returns a list rather than a dict so the order is the server's (worst first, the
    way the CLIN cards are already ranked) and the client never has to decide which
    plan to show first.
    """
    ac = alloc.get("contract") or {}
    total_weeks = _f(ac.get("total_weeks"))
    current_week = _f(ac.get("current_week"))

    # A contract past its period of performance gets no move list at all.
    #
    # Every number this module produces is a forward one — a weekly target of
    # `remaining ÷ weeks left`, a landing week, a gap to close between now and PoP end.
    # Past PoP there are no weeks left, and `applyBalance`'s `max(1, ...)` divisor
    # clamps the target to the entire remaining budget spent in a single week, which
    # manufactures an enormous "gap" out of arithmetic rather than out of anything true.
    # On the local contract 13 (week 129 of 52) that produced a $31.5K/wk gap and a
    # `stop_overtime` diagnosis for one person 1.5 hrs/wk over — advice to restaff a
    # contract whose work is finished. The honest answer is that a closed-out contract
    # is a closeout problem, so this returns nothing and the CLIN-level paragraph stands.
    if ac.get("past_pop") or (total_weeks and current_week >= total_weeks):
        return []

    # The same divisor `applyBalance` uses, so "lands at PoP end" means the same thing
    # in the suggestion and in the button that carries it out.
    weeks_remaining = max(1.0, total_weeks - current_week)

    cards = alloc.get("clins") or []
    heat_clins = {c["id"]: c for c in (heat_payload.get("clins") or [])}
    under_cards = [c for c in cards if c.get("base_status") in RAISE_STATES]
    employees = alloc.get("employees") or []

    plans = []
    for card in cards:
        cid = card["id"]
        status = card.get("base_status")
        if status not in REDUCE_STATES and status not in RAISE_STATES:
            continue
        weekly = _f(card.get("base_weekly"))
        if weekly <= 0:
            continue  # nothing charging yet — there is nothing to move

        # Running out of *funding* is not running out of *money*, and the two want
        # opposite remedies. On an incrementally funded line the binding budget is the
        # obligated slice, not the ceiling — so a line burning through that slice with
        # ceiling still underneath it is short an obligation, not overstaffed, and the
        # fix is the next mod. Sizing a staffing gap against the funded slice instead
        # proposed rolling a fully-funded team off a contract that has the money to pay
        # them: that is how 7024HEXDVC0001043 came to recommend clearing its own staff.
        # A real ceiling breach still gets the move list.
        if status in REDUCE_STATES and _funding_limited(card):
            plans.append(_funding_limited_plan(card, cid, total_weeks))
            continue
        target = max(0.0, _f(card.get("remaining"))) / weeks_remaining

        rows_on_clin = [
            (row, (row.get("cells") or {})[cid])
            for row in employees
            if _f(((row.get("cells") or {}).get(cid) or {}).get("hours")) > 0
        ]

        if status in REDUCE_STATES:
            moves, freed, gap, diagnosis, unpriced, escalated = _reduce_plan(
                card,
                heat_clins.get(cid),
                rows_on_clin,
                cid,
                target,
                weekly,
                under_cards,
            )
            direction = "reduce"
            new_weekly = max(0.0, weekly - freed)
        else:
            moves, freed, gap, unpriced = _raise_plan(
                card, rows_on_clin, cid, target, weekly
            )
            diagnosis = None
            escalated = False
            direction = "raise"
            new_weekly = weekly + freed

        unpriced = list(unpriced.values())
        notes = list(_ceiling_notes(heat_payload, cid, moves))

        # Emptying the line is not a plan.
        #
        # A CLIN spent through its budget has `target == 0`, and the arithmetic will
        # happily "close" that gap by taking every hour off the line — the ticket's own
        # example of an unclosable case (one person at 8 hrs/wk) closes perfectly if you
        # are willing to bench them. But zeroing a line while the work continues is a
        # work-stoppage decision, not a rebalance, and it is not a move this solver is
        # entitled to make. So the move list is withdrawn rather than dressed up as a
        # fix: `closed: false` with the reason routes the client back to the CLIN-level
        # paragraph, which is exactly the fallback the ticket asks for here.
        if direction == "reduce" and moves and (target <= 0 or new_weekly <= 0):
            moves = []
            freed = 0.0
            new_weekly = weekly
            notes.append(
                "No staffing change closes this — the line would have to stop billing "
                "entirely, and that is a modification or a rescope rather than a trim."
            )

        closed = freed >= gap - 0.5  # half a dollar a week is not a shortfall
        new_exhaust = (
            round(current_week + max(0.0, _f(card.get("remaining"))) / new_weekly, 2)
            if new_weekly > 0
            else None
        )

        if unpriced:
            names = sorted({m["person"] for m in unpriced})
            notes.append(
                f"{', '.join(names)} also bill{'s' if len(names) == 1 else ''} this CLIN "
                f"at a category with no printed rate, so the dollar effect of moving "
                f"them is unknown and is not counted above."
            )

        plans.append(
            {
                "clin": cid,
                "direction": direction,
                "diagnosis": diagnosis,
                "weekly": round(weekly, 2),
                "target_weekly": round(target, 2),
                "gap_weekly": round(gap, 2),
                "freed_weekly": round(freed, 2),
                "new_weekly": round(new_weekly, 2),
                "exhaust_week": card.get("base_exhaust_week"),
                "new_exhaust_week": new_exhaust,
                "total_weeks": round(total_weeks, 2) if total_weeks else None,
                "closed": bool(closed),
                "shortfall_weekly": round(max(0.0, gap - freed), 2),
                # True when the gentle ladder (shifts and trims to expected hours)
                # wasn't enough and people are being taken off the line. Worth saying
                # out loud rather than burying in the bullets.
                "escalated": bool(escalated),
                "moves": moves,
                "groups": _group_moves(moves),
                "unpriced": unpriced,
                "notes": notes,
            }
        )
    return plans
