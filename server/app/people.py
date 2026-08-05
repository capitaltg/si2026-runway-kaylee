"""People directory (#69) — one app-wide answer to "who is this person and what
are they qualified for".

The first genuinely global entity in Runway. Everything else is contract-scoped,
and that is the point of the departure: a person's degree is not a fact about a
contract. The data splits by what it is actually a property of —

  identity (name, employee id)      global    derived from timesheets
  quals (education, years, ...)     global    typed in, always optional
  charging (contracts, CLINs, LCAT) per       derived from timesheets, never authored

— and only the middle row is ever entered by a human.

The invariant this module exists to protect:

    The directory answers "what are this person's credentials". It never answers
    "who is charging this contract". Timesheets own that, exclusively.

Concretely: a person in the directory with no timesheet hours on a contract does
not appear on that contract's allocation matrix. `allocation.py` does not import
this module and must not — the grid is built from timesheet rows and nothing else.

Deliberately not in here: any notion of compensation. Runway visualises money, it
does not manage payroll, so a person's salary is not a field in their record and
not editable anywhere in this feature. Cost and fee math does not need one — a
*direct labor rate* is a fiscal-year-scoped pricing input and already lives in
`direct_rates` (#77), where LCAT averages alone produce real margin with nobody
named.

One vocabulary note, because it will matter soon: `origin: "manual"` is an *origin*
— how someone entered the directory — and today it is the same set of people the
UI once called "the bench". #63 gives "bench" a different meaning: a *state*, where
a person who does have timesheet history becomes unassigned. Keep the two words
apart. This module only ever knows about origin.
"""

from typing import List, Optional

from . import capacity as capacity_mod

# The qualification fields the directory will store, as an allowlist rather than
# free-form keys — the attrs table would otherwise become arbitrary key-value
# storage on the first caller that felt like inventing a field. #84's utilisation
# target joins this tuple and needs no schema change to do it.
#
# These three are the *comparable* fields: #66 checks a person's credentials against
# a labor category's floor, so each one has to be drawn from a vocabulary that lines
# up with the floor's (#98).
QUAL_FIELDS = ("education", "years_experience", "clearance")

# Stored and allowlisted, but never compared: context a human reads. Education is
# two things wearing one label — a *level* (closed, ordered) and a *field of study*
# (open). Only the level can be checked; "Computer Science" is not more or less than
# "Mechanical Engineering". Splitting them keeps "BS Computer Science" expressible
# without making it the thing #66 compares.
CONTEXT_FIELDS = ("education_field",)

# Capacity, not a credential (#84). Expected hours per week is stored here because a
# part-time or split week is a property of the *person*, which is the same argument
# this module makes for quals — but it is deliberately not a `QUAL_FIELD`. #66 never
# compares it against a labor category's floor, and folding it into coverage would
# drop every person in the directory out of `complete` over a field that says nothing
# about whether they are qualified.
CAPACITY_FIELDS = ("expected_hours",)

ALLOWED_FIELDS = QUAL_FIELDS + CONTEXT_FIELDS + CAPACITY_FIELDS

# The closed vocabularies, in ascending order. Order is the point: the check is
# "meets or exceeds", not equality, so a TS/SCI holder must clear a Secret floor and
# a Master's must clear a Bachelor's floor. Index in these tuples *is* the rank.
#
# `None` is a real clearance value — "holds no clearance" — and is a different fact
# from "we have not recorded one", which is the absence of a row. #66 must never
# conflate them: the first is a person who fails a Secret floor, the second is a
# person nobody has checked.
CLEARANCE_LEVELS = ("None", "Public Trust", "Secret", "Top Secret", "TS/SCI")

EDUCATION_LEVELS = (
    "HS Diploma",
    "Associate's",
    "Bachelor's",
    "Master's",
    "Doctorate",
)

# Served to the UI rather than restated there. A second copy of these lists in JSX
# is the same two-vocabularies-that-never-line-up problem this ticket exists to
# remove, one layer further out.
QUAL_VOCAB = {
    "clearance": list(CLEARANCE_LEVELS),
    "education": list(EDUCATION_LEVELS),
}

# A typo guard, not a policy. Past this it is a slip — a birth year, a rate — rather
# than a career.
MAX_YEARS_EXPERIENCE = 70

# Coverage states for one person's quals. `unknown` is a first-class, supported
# state and the day-one state for everybody — nothing in the UI may imply an
# upload is required to leave it.
COMPLETE, PARTIAL, UNKNOWN = "complete", "partial", "unknown"


def _norm_name(name: Optional[str]) -> str:
    """A name flattened enough to compare two spellings of the same person."""
    keep = [c for c in (name or "").lower() if c.isalpha() or c.isspace()]
    return " ".join("".join(keep).split())


def quals_status(quals: dict) -> str:
    have = [f for f in QUAL_FIELDS if (quals or {}).get(f)]
    if not have:
        return UNKNOWN
    return COMPLETE if len(have) == len(QUAL_FIELDS) else PARTIAL


def clearance_rank(value: Optional[str]) -> Optional[int]:
    """Where a clearance sits on the ladder, or None if it isn't on it.

    None means "not comparable" and is what #66 must treat as *unchecked*, never as
    a failure — an unrecognised or unrecorded clearance is missing information, and
    reporting missing information as "does not meet" is the failure mode this whole
    ticket exists to prevent.
    """
    try:
        return CLEARANCE_LEVELS.index((value or "").strip())
    except ValueError:
        return None


def education_rank(value: Optional[str]) -> Optional[int]:
    """Where an education *level* sits on the ladder, or None if it isn't on it.

    Reads the `education` field only. `education_field` ("Computer Science") has no
    rank by design — a field of study is not more or less than another one.
    """
    try:
        return EDUCATION_LEVELS.index((value or "").strip())
    except ValueError:
        return None


def years_value(value: Optional[str]) -> Optional[float]:
    """Years of experience as a number, or None if it isn't one.

    Stored as TEXT like every other attr, so the comparison has to parse. Anything
    unparseable is not-comparable, same as an off-ladder clearance.
    """
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def validate_qual_value(field: str, value: str) -> Optional[str]:
    """Reject a value the vocabularies don't admit; None means it's fine.

    Server-side because the API is the contract and #66 will trust it — a dropdown
    constrains one client, and the check reading `TS-SCI` next to a `TS/SCI` floor
    reports "does not meet", which looks like a finding rather than like a typo.

    Blank never reaches here: an empty value is a delete, and clearing a field back
    to unknown stays available (see save_person_attrs).
    """
    value = (value or "").strip()
    if field == "clearance" and clearance_rank(value) is None:
        return (
            f"{value!r} is not a recognised clearance. "
            f"Use one of: {', '.join(CLEARANCE_LEVELS)}."
        )
    if field == "education" and education_rank(value) is None:
        return (
            f"{value!r} is not a recognised education level. "
            f"Use one of: {', '.join(EDUCATION_LEVELS)}. "
            "A field of study goes in the separate field-of-study box."
        )
    if field == "years_experience":
        years = years_value(value)
        if years is None:
            return (
                f"{value!r} is not a number. Record the years as a number and put "
                'the argument in the source note — "12 · per proposal resume, '
                '2026-03".'
            )
        if years < 0 or years > MAX_YEARS_EXPERIENCE:
            return f"Years of experience must be between 0 and {MAX_YEARS_EXPERIENCE}."
    if field == "expected_hours":
        # A week, not a credential — the rule lives with the resolver that reads it.
        return capacity_mod.validate_expected_hours(value)
    return None


def validate_quals(incoming: dict, stored: dict) -> Optional[str]:
    """Check one save's field names and values. First problem, or None.

    `stored` is what is already on file for this person, and it grandfathers itself
    in: a value typed before the vocabularies existed can be re-sent unchanged, which
    is what editing only its source note does. Without that, an old free-text
    clearance would make its own provenance uneditable. Typing a *new* unrecognised
    value is still refused, so the set of off-ladder values can only shrink.

    A blank value is a delete, never a violation — clearing a field back to unknown
    has to stay available or "optional" isn't true.
    """
    unknown = [f for f in (incoming or {}) if f not in ALLOWED_FIELDS]
    if unknown:
        return f"Unknown qualification field(s): {unknown}"
    for field, entry in (incoming or {}).items():
        value = (str((entry or {}).get("value") or "")).strip()
        if not value or value == (stored or {}).get(field):
            continue
        problem = validate_qual_value(field, value)
        if problem:
            return problem
    return None


def build_directory(
    facts: List[dict],
    contracts: List[dict],
    manual_people: List[dict],
    attr_rows: List[dict],
    unidentified: Optional[dict] = None,
) -> dict:
    """The whole directory, from derived charging facts plus authored extras.

    Pure — every argument is data the caller read. Reports no hours: utilisation is
    the expensive question (a burn pass per contract) and is served separately, on
    demand, so that listing 114 people stays a cheap query.
    """
    # Same precedence burn.compute uses for a contract's display name — a chosen
    # callsign wins, then the legal contractor, then the PIID — so a person's
    # contracts read the same here as everywhere else in the app.
    contract_names = {}
    for c in contracts or []:
        header = c.get("contract") or {}
        contract_names[c["id"]] = (
            c.get("nickname")
            or header.get("contractor")
            or header.get("piid")
            or c.get("piid")
            or f"#{c['id']}"
        )

    # Capacity settings per contract, read once. Just blob keys — no burn pass — so
    # the directory can say "expected 32 hrs/wk, the contract's target" while staying
    # the cheap query it promises to be.
    contract_caps = {}
    for c in contracts or []:
        contract_caps[c["id"]] = capacity_mod.contract_capacity(c)

    # Quals and capacity share the attrs table and are split back apart here: one is
    # a credential #66 compares, the other is a week #84 divides by, and putting
    # expected hours inside a key called `quals` would make every consumer of the
    # directory read it as a qualification.
    quals_by_person = {}
    capacity_by_person = {}
    for a in attr_rows or []:
        field = a.get("field")
        if field not in ALLOWED_FIELDS:
            continue
        bucket = capacity_by_person if field in CAPACITY_FIELDS else quals_by_person
        bucket.setdefault(a["employee_id"], {})[field] = {
            "value": a.get("value"),
            "source_note": a.get("source_note"),
            "authored_by": a.get("authored_by"),
            "authored_at": a.get("authored_at"),
        }

    # Derived people first — identity comes off the feed, which is the authority.
    people = {}
    for f in facts or []:
        eid = f.get("employee_id")
        if not eid:
            continue
        p = people.setdefault(
            eid,
            {
                "employee_id": eid,
                "name": (f.get("employee") or "").strip() or eid,
                "origin": "derived",
                "id_provisional": False,
                "contracts": {},
            },
        )
        cid = f.get("contract_id")
        row = p["contracts"].setdefault(
            cid,
            {
                "contract_id": cid,
                "contract": contract_names.get(cid, f"#{cid}"),
                "clins": set(),
                "lcats": set(),
                "weeks": 0,
                "first_week": f.get("first_week"),
                "last_week": f.get("last_week"),
            },
        )
        if f.get("charge_code"):
            row["clins"].add(str(f["charge_code"]))
        if (f.get("labor_category") or "").strip():
            row["lcats"].add(f["labor_category"].strip())
        # Summed across the (CLIN, LCAT) tuples of one contract: an upper bound on
        # weeks engaged, not a precise count, and only ever used to sort a standing
        # assignment above a one-week blip.
        row["weeks"] += f.get("weeks") or 0
        if f.get("first_week") and f["first_week"] < (row["first_week"] or "9999"):
            row["first_week"] = f["first_week"]
        if f.get("last_week") and f["last_week"] > (row["last_week"] or ""):
            row["last_week"] = f["last_week"]

    # Then the hand-added people. One whose id already appears in timesheets is not
    # added again — the feed took over, and their typed-in id doing exactly that is
    # the reason to prefer a real payroll id when adding someone.
    for m in manual_people or []:
        eid = m["employee_id"]
        if eid in people:
            continue
        people[eid] = {
            "employee_id": eid,
            "name": (m.get("name") or "").strip() or eid,
            "origin": "manual",
            "id_provisional": bool(m.get("id_provisional")),
            "contracts": {},
        }

    out = []
    for p in people.values():
        own_capacity = capacity_by_person.get(p["employee_id"], {})
        own_hours = (own_capacity.get("expected_hours") or {}).get("value")
        rows = []
        for row in p["contracts"].values():
            lcats = sorted(row["lcats"])
            rows.append(
                {
                    **row,
                    "clins": sorted(row["clins"]),
                    "lcats": lcats,
                    # An LCAT default can only be looked up when there is one category
                    # to look up. Charging two on one contract is rare and, where it
                    # happens, no single category describes the week — so it falls
                    # through to the contract's target rather than picking a winner.
                    "expected": capacity_mod.resolve(
                        person_hours=own_hours,
                        lcat=lcats[0] if len(lcats) == 1 else None,
                        capacity=contract_caps.get(
                            row["contract_id"], capacity_mod.contract_capacity(None)
                        ),
                    ),
                }
            )
        rows.sort(key=lambda r: (-r["weeks"], r["contract"]))
        quals = quals_by_person.get(p["employee_id"], {})
        out.append(
            {
                "employee_id": p["employee_id"],
                "name": p["name"],
                "origin": p["origin"],
                "id_provisional": p["id_provisional"],
                "contracts": rows,
                "contract_count": len(rows),
                # Every category this person bills anywhere. The compliance check
                # (#66) reads the per-contract lists instead: the same person can
                # legitimately bill different categories on different contracts, so
                # a single headline LCAT would be the wrong subject to check.
                "lcats": sorted({lc for r in rows for lc in r["lcats"]}),
                "quals": quals,
                "quals_status": quals_status(quals),
                # Capacity rides alongside quals, never inside them (#84).
                "capacity": own_capacity,
                # Their week across the whole portfolio, for the directory row. The
                # per-contract numbers above are what the allocation matrix shows.
                "expected": capacity_mod.portfolio_expected(
                    person_hours=own_hours,
                    per_contract=[r["expected"] for r in rows],
                ),
            }
        )
    out.sort(key=lambda p: (p["origin"] != "derived", -p["contract_count"], p["name"]))

    status_counts = {COMPLETE: 0, PARTIAL: 0, UNKNOWN: 0}
    for p in out:
        status_counts[p["quals_status"]] += 1

    return {
        "count": len(out),
        "people": out,
        # What the compliance badge needs to stop saying "Compliant" about a
        # contract nobody has checked (#66): checked and clear has to be
        # distinguishable from not checked. #69 supplies the counts; the verdict
        # itself is #66's.
        "coverage": {
            "people": len(out),
            "complete": status_counts[COMPLETE],
            "partial": status_counts[PARTIAL],
            "unknown": status_counts[UNKNOWN],
        },
        "unidentified": unidentified or {"rows": 0, "contracts": 0},
        "merge_suggestions": merge_candidates(manual_people, facts),
        # The closed vocabularies, so the editor renders the same ladder the check
        # will read rather than a second list that drifted.
        "qual_vocab": QUAL_VOCAB,
    }


def merge_candidates(manual_people: List[dict], facts: List[dict]) -> List[dict]:
    """Provisional hand-added people who look like someone the feed now carries.

    A suggestion, never an action. A Runway-minted id has no relationship to a real
    payroll id, so the only thing left to match on is the name — and a name match is
    not an identity match. Two people called Chris Nguyen are ordinary; silently
    fusing their records is how a directory quietly corrupts itself, and merging is
    the direction that cannot be undone.
    """
    derived = {}
    for f in facts or []:
        eid = f.get("employee_id")
        if eid:
            derived.setdefault(_norm_name(f.get("employee")), set()).add(eid)
    out = []
    for m in manual_people or []:
        if not m.get("id_provisional"):
            continue
        for eid in sorted(derived.get(_norm_name(m.get("name")), ())):
            out.append(
                {
                    "from": m["employee_id"],
                    "name": m.get("name"),
                    "into": eid,
                }
            )
    return out


def utilization(allocations: List[dict]) -> dict:
    """Everyone's hours summed across every contract they charge.

    Promoted out of `/api/allocation/conflicts`, which computed this and then threw
    away everything that wasn't a conflict. Conflicts is now a filter over it
    (`conflicts` below) and returns the identical payload, so the Portfolio panel is
    unchanged.

    This is the expensive half of the directory — a burn pass per contract via
    `allocation.compute_allocation` — which is why it is its own endpoint and the
    People view loads it on demand rather than on mount.

    Utilisation here is against each person's *expected* week, not against 40 (#84).
    The per-contract expectations arrive already resolved on each employee row,
    because the resolution needs the contract's blob and the allocation pass is the
    thing holding it.
    """
    people = {}
    for alloc in allocations or []:
        cid = alloc["contract"]["id"]
        cname = alloc["contract"]["name"]
        for e in alloc.get("employees", []):
            hrs = sum(cell["hours"] for cell in e.get("cells", {}).values())
            if hrs <= 0:
                continue
            p = people.setdefault(
                e["id"],
                {
                    "employee_id": e["id"],
                    "name": e["name"],
                    "total_hours": 0.0,
                    "assignments": [],
                    "_expected": [],
                },
            )
            p["total_hours"] += hrs
            expected = e.get("expected")
            if expected:
                p["_expected"].append(expected)
            p["assignments"].append(
                {
                    "contract_id": cid,
                    "contract": cname,
                    "hours": round(hrs, 1),
                    # What this contract expects of them, so the panel can say why one
                    # person's 34 hours is full and another's is not.
                    "expected": expected,
                    "utilization": capacity_mod.utilization(
                        hrs, (expected or {}).get("hours")
                    ),
                }
            )
    rows = []
    for p in people.values():
        per_contract = p.pop("_expected")
        # A person's own override wins outright; with none, the widest week any of
        # their contracts assumes. See capacity.portfolio_expected for why neither
        # summing nor averaging the contracts is defensible.
        own = None
        for res in per_contract:
            if res.get("level") == capacity_mod.PERSON:
                own = res["hours"]
                break
        expected = capacity_mod.portfolio_expected(
            person_hours=own, per_contract=per_contract
        )
        rows.append(
            {
                **p,
                "total_hours": round(p["total_hours"], 1),
                "expected": expected,
                "utilization": capacity_mod.utilization(
                    p["total_hours"], expected["hours"]
                ),
            }
        )
    rows.sort(key=lambda p: -p["total_hours"])
    return {"count": len(rows), "people": rows}


def conflicts(util_rows: List[dict]) -> List[dict]:
    """People booked past a full 40-hr week once summed across contracts. Matches on
    employee_id, so it only ever surfaces real overlaps.

    Deliberately still 40, and deliberately *not* each person's expected hours (#84).
    This is a physical-week check — the question is whether one person has been booked
    more hours than a week holds, which is a scheduling impossibility. Someone with a
    32-hour expected week booked to 38 across two contracts is over their expectation
    and not double-booked, and turning that into an overbooking alert would flood the
    Portfolio panel with every part-time person in the company. "Over their expected
    hours" is a separate signal, and #83 is where it belongs.
    """
    return [
        p
        for p in util_rows or []
        if len(p.get("assignments", [])) >= 2
        and p["total_hours"] > capacity_mod.PHYSICAL_WEEK_HOURS
    ]
