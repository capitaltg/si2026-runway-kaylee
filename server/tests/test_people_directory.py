"""#69 — an app-wide people directory, derived from timesheets, quals optional.

Runway had no idea who anyone was: a timesheet row carries an employee, an id, a
labor category and hours, and nothing about the person. That is the missing subject
for LCAT *qualification* checking (#66) and the missing candidate pool for the
people picker (#65).

These tests pin the four things the ticket actually turns on:

  1. The directory is populated on day one with no setup, because identity and
     charging history are derived rather than authored.
  2. Quals are optional. `unknown` is a normal state, a partially-filled person is
     supported, and years of experience is an assertion carrying its source.
  3. The invariant that stops this becoming a second roster: a person in the
     directory with no timesheet hours on a contract does not appear on that
     contract's allocation matrix.
  4. A person's billed LCAT is per contract, so the directory never collapses two
     contracts' categories into one headline the compliance check would then check
     against the wrong thing.

DB-free, like the rest of this suite: `people.py` is pure functions over rows the
caller read, and `db.py` is the thin glue.
"""

from app import allocation, people


def _fact(
    contract_id=1,
    employee_id="e1",
    employee="Aisha Khan",
    charge_code="0001",
    labor_category="Senior Engineer",
    weeks=6,
    first_week="2026-01-02",
    last_week="2026-02-06",
):
    """One (contract, person, CLIN, LCAT) charging fact, as db.people_charging_facts
    returns it."""
    return {
        "contract_id": contract_id,
        "employee_id": employee_id,
        "employee": employee,
        "charge_code": charge_code,
        "labor_category": labor_category,
        "weeks": weeks,
        "first_week": first_week,
        "last_week": last_week,
    }


def _contracts(*names):
    """Contracts in their stored shape — the display name lives under the nested
    `contract` header, not at the top level."""
    return [
        {"id": i + 1, "piid": f"PIID-{i + 1}", "contract": {"contractor": n}}
        for i, n in enumerate(names)
    ]


def _attr(employee_id, field, value, source_note=None, by="Kaylee"):
    return {
        "employee_id": employee_id,
        "field": field,
        "value": value,
        "source_note": source_note,
        "authored_by": by,
        "authored_at": "2026-08-05 12:00:00",
    }


def _by_id(payload):
    return {p["employee_id"]: p for p in payload["people"]}


# ------------------------------------------------------- derived on day one


def test_everyone_who_has_charged_is_already_in_the_directory():
    # The whole premise: no upload, no setup step. Identity comes off the feed.
    d = people.build_directory(
        facts=[_fact(employee_id="e1"), _fact(employee_id="e2", employee="Dev Rao")],
        contracts=_contracts("FALCON"),
        manual_people=[],
        attr_rows=[],
    )
    assert d["count"] == 2
    people_by_id = _by_id(d)
    assert people_by_id["e1"]["name"] == "Aisha Khan"
    assert people_by_id["e1"]["origin"] == "derived"
    # ...and with nothing typed in, they are `unknown` rather than missing or blank.
    assert people_by_id["e1"]["quals_status"] == people.UNKNOWN
    assert people_by_id["e1"]["quals"] == {}


def test_the_directory_reports_no_hours_and_no_money():
    # Utilisation costs a burn pass per contract. Keeping it out of this payload is
    # what lets the People view stay a cheap read, and is why the view fetches it
    # separately, on demand.
    d = people.build_directory(
        facts=[_fact()], contracts=_contracts("FALCON"), manual_people=[], attr_rows=[]
    )
    person = d["people"][0]
    assert not any("hour" in k or "rate" in k or "cost" in k for k in person)
    for row in person["contracts"]:
        assert not any("hour" in k or "rate" in k for k in row)


def test_no_compensation_anywhere_in_the_record():
    # Runway visualises money; it does not manage payroll. A person's salary is not
    # a field in their record, and QUAL_FIELDS is the allowlist that keeps it out.
    assert not any(
        f in people.QUAL_FIELDS for f in ("direct_rate", "salary", "rate", "pay")
    )


# ------------------------------------------------------- quals are optional


def test_quals_may_be_partially_filled():
    d = people.build_directory(
        facts=[_fact()],
        contracts=_contracts("FALCON"),
        manual_people=[],
        attr_rows=[_attr("e1", "clearance", "TS/SCI")],
    )
    person = d["people"][0]
    assert person["quals_status"] == people.PARTIAL
    assert person["quals"]["clearance"]["value"] == "TS/SCI"


def test_all_fields_present_reads_complete():
    d = people.build_directory(
        facts=[_fact()],
        contracts=_contracts("FALCON"),
        manual_people=[],
        attr_rows=[_attr("e1", f, "x") for f in people.QUAL_FIELDS],
    )
    assert d["people"][0]["quals_status"] == people.COMPLETE


def test_years_of_experience_carries_its_source():
    # "BS + 10 years *relevant* experience" is what a proposal argues, so years is an
    # assertion with a source, not a fact. `12 — per proposal resume, 2026-03` is
    # defensible in an audit; a bare `12` is a number someone will dispute.
    d = people.build_directory(
        facts=[_fact()],
        contracts=_contracts("FALCON"),
        manual_people=[],
        attr_rows=[
            _attr("e1", "years_experience", "12", source_note="per proposal resume")
        ],
    )
    years = d["people"][0]["quals"]["years_experience"]
    assert years["value"] == "12"
    assert years["source_note"] == "per proposal resume"
    assert years["authored_by"] == "Kaylee"
    assert years["authored_at"]


def test_an_unrecognised_field_never_reaches_the_payload():
    # The attrs table must not become arbitrary key-value storage on the first
    # caller that invents a field.
    d = people.build_directory(
        facts=[_fact()],
        contracts=_contracts("FALCON"),
        manual_people=[],
        attr_rows=[_attr("e1", "favourite_colour", "green")],
    )
    assert d["people"][0]["quals"] == {}
    assert d["people"][0]["quals_status"] == people.UNKNOWN


def test_coverage_counts_let_the_badge_say_checked_versus_unknown():
    # The design renders `flags.length ? 'N LCAT flag' : 'Compliant'`, which says
    # "Compliant" about a contract nobody has checked — the worst failure mode
    # available to a compliance feature. #69 supplies the counts that make
    # checked-and-clear distinguishable from not-checked; #66 owns the verdict.
    d = people.build_directory(
        facts=[
            _fact(employee_id="e1"),
            _fact(employee_id="e2", employee="Dev Rao"),
            _fact(employee_id="e3", employee="Sam Ortiz"),
        ],
        contracts=_contracts("FALCON"),
        manual_people=[],
        attr_rows=[_attr("e1", f, "x") for f in people.QUAL_FIELDS]
        + [_attr("e2", "clearance", "Secret")],
    )
    assert d["coverage"] == {"people": 3, "complete": 1, "partial": 1, "unknown": 1}


# ------------------------------------------- the invariant: no second roster


def test_a_hand_added_person_never_appears_on_the_allocation_matrix():
    # The concretely testable form of "the directory never answers who is charging
    # this contract". A hand-added person is in the directory, fully annotated, and
    # invisible to the grid — which is built from timesheet rows alone.
    added = [{"employee_id": "RW-0001", "name": "Planned Hire", "id_provisional": True}]
    d = people.build_directory(
        facts=[_fact(employee_id="e1")],
        contracts=_contracts("FALCON"),
        manual_people=added,
        attr_rows=[_attr("RW-0001", f, "x") for f in people.QUAL_FIELDS],
    )
    directory = _by_id(d)
    assert directory["RW-0001"]["origin"] == "manual"
    assert directory["RW-0001"]["quals_status"] == people.COMPLETE
    assert directory["RW-0001"]["contracts"] == []

    contract = {
        "id": 1,
        "contract": {"piid": "TEST-1", "total_ceiling": 1000000},
        "clins": [
            {
                "clin": "0001",
                "title": "Labor",
                "is_labor": True,
                "ceiling": 500000,
                "est_hours": 2500,
                "labor_rates": [{"lcat": "Senior Engineer", "loaded_rate": 150}],
            }
        ],
        "periods": [],
    }
    rows = [
        {
            "charge_code": "0001",
            "labor_category": "Senior Engineer",
            "total_hours": 40,
            "week_ending": f"2026-01-{2 + 7 * w:02d}",
            "employee": "Aisha Khan",
            "employee_id": "e1",
        }
        for w in range(6)
    ]
    alloc = allocation.compute_allocation(contract, rows)
    assert [e["id"] for e in alloc["employees"]] == ["e1"]


def test_allocation_clin_exposes_rate_line_qualifications():
    contract = {
        "id": 1,
        "contract": {"piid": "TEST-1", "total_ceiling": 1_000_000},
        "clins": [
            {
                "clin": "0001",
                "title": "Labor",
                "is_labor": True,
                "ceiling": 500_000,
                "est_hours": 2_500,
                "labor_rates": [
                    {
                        "lcat": "Senior Engineer",
                        "loaded_rate": 225,
                        "min_education": "Bachelor's",
                        "min_experience_yrs": 8,
                        "clearance": "Secret",
                    }
                ],
            }
        ],
        "periods": [],
    }

    alloc = allocation.compute_allocation(contract, [])
    assert alloc["clins"][0]["rate_lines"] == [
        {
            "lcat": "Senior Engineer",
            "rate": 225.0,
            "min_education": "Bachelor's",
            "min_experience_yrs": 8,
            "clearance": "Secret",
        }
    ]


def test_allocation_does_not_read_the_directory():
    # The structural half of the same rule. If allocation ever imports people, the
    # grid gains a second source of truth and can drift from the feed.
    assert not hasattr(allocation, "people")


def test_a_manual_person_yields_to_the_feed_once_they_charge():
    # Typing the real payroll id is the point: when a feed finally carries this
    # person, they link up instead of forking into a second profile.
    d = people.build_directory(
        facts=[_fact(employee_id="e9", employee="Dev Rao")],
        contracts=_contracts("FALCON"),
        manual_people=[
            {"employee_id": "e9", "name": "Dev R.", "id_provisional": False}
        ],
        attr_rows=[],
    )
    assert d["count"] == 1
    person = d["people"][0]
    assert person["origin"] == "derived"
    # Name comes off the feed, which is the authority on identity.
    assert person["name"] == "Dev Rao"


# ------------------------------------------- billed LCAT is per contract


def test_two_contracts_keep_their_own_lcats():
    # The case #66's per-(person, contract, CLIN) check leans on: the same person can
    # legitimately bill different categories on different contracts, so collapsing
    # them into one headline LCAT would check the wrong thing.
    d = people.build_directory(
        facts=[
            _fact(contract_id=1, labor_category="Senior Engineer", weeks=8),
            _fact(contract_id=2, labor_category="Program Manager", weeks=3),
        ],
        contracts=_contracts("FALCON", "OSPREY"),
        manual_people=[],
        attr_rows=[],
    )
    person = d["people"][0]
    assert person["contract_count"] == 2
    rows = {r["contract"]: r for r in person["contracts"]}
    assert rows["FALCON"]["lcats"] == ["Senior Engineer"]
    assert rows["OSPREY"]["lcats"] == ["Program Manager"]
    # The union is still available, but as a summary rather than the subject.
    assert person["lcats"] == ["Program Manager", "Senior Engineer"]
    # A standing assignment sorts above a short one.
    assert [r["contract"] for r in person["contracts"]] == ["FALCON", "OSPREY"]


def test_clins_are_kept_per_contract():
    d = people.build_directory(
        facts=[
            _fact(contract_id=1, charge_code="0001"),
            _fact(contract_id=1, charge_code="0002"),
        ],
        contracts=_contracts("FALCON"),
        manual_people=[],
        attr_rows=[],
    )
    assert d["people"][0]["contracts"][0]["clins"] == ["0001", "0002"]


# ------------------------------------------- identity edges


def test_blank_employee_ids_are_excluded_but_stay_visible():
    # Silently collapsing them into one person is the failure mode; so is silently
    # dropping them. They become a data-quality figure instead.
    d = people.build_directory(
        facts=[_fact(employee_id="e1"), _fact(employee_id=None, employee="Ghost")],
        contracts=_contracts("FALCON"),
        manual_people=[],
        attr_rows=[],
        unidentified={"rows": 12, "contracts": 2},
    )
    assert d["count"] == 1
    assert d["unidentified"] == {"rows": 12, "contracts": 2}


def test_a_provisional_person_is_offered_for_merge_never_merged():
    # A Runway-minted id has no relationship to a real payroll id, so the only thing
    # left to match on is the name — and a name match is not an identity match. Two
    # people called Chris Nguyen are ordinary; fusing them is not undoable.
    facts = [_fact(employee_id="EMP-771", employee="Priya Raman")]
    manual = [
        {"employee_id": "RW-0001", "name": "priya  raman", "id_provisional": True}
    ]
    d = people.build_directory(
        facts=facts,
        contracts=_contracts("FALCON"),
        manual_people=manual,
        attr_rows=[],
    )
    assert d["merge_suggestions"] == [
        {"from": "RW-0001", "name": "priya  raman", "into": "EMP-771"}
    ]
    # Still two separate records until a human confirms.
    assert d["count"] == 2


def test_a_typed_id_is_not_offered_for_merge():
    # Only minted placeholders are ambiguous. A user-entered id is a claim about
    # identity that we take at face value.
    d = people.build_directory(
        facts=[_fact(employee_id="EMP-771", employee="Priya Raman")],
        contracts=_contracts("FALCON"),
        manual_people=[
            {"employee_id": "EMP-772", "name": "Priya Raman", "id_provisional": False}
        ],
        attr_rows=[],
    )
    assert d["merge_suggestions"] == []


# ------------------------------------------- utilisation / conflicts


def _alloc(cid, name, employees):
    return {
        "contract": {"id": cid, "name": name},
        "employees": [
            {"id": eid, "name": nm, "cells": {"0001": {"hours": hrs}}}
            for eid, nm, hrs in employees
        ],
    }


def test_utilization_reports_everyone_not_just_the_overbooked():
    u = people.utilization(
        [
            _alloc(1, "FALCON", [("e1", "Aisha Khan", 30), ("e2", "Dev Rao", 40)]),
            _alloc(2, "OSPREY", [("e1", "Aisha Khan", 20)]),
        ]
    )
    rows = {p["employee_id"]: p for p in u["people"]}
    assert rows["e1"]["total_hours"] == 50
    assert rows["e1"]["utilization"] == 1.25
    assert [a["contract"] for a in rows["e1"]["assignments"]] == ["FALCON", "OSPREY"]
    # Single-contract people are in utilisation; they are simply not conflicts.
    assert rows["e2"]["total_hours"] == 40


def test_conflicts_is_a_filter_over_utilization():
    # The endpoint used to compute all of this and throw away the non-conflicts.
    # Promoting it must not change the panel's answer.
    u = people.utilization(
        [
            _alloc(1, "FALCON", [("e1", "Aisha Khan", 30), ("e2", "Dev Rao", 45)]),
            _alloc(2, "OSPREY", [("e1", "Aisha Khan", 20)]),
        ]
    )
    got = people.conflicts(u["people"])
    # e1 is over a full week across two contracts. e2 is over 40 on one contract,
    # which is overtime, not a resource conflict.
    assert [p["employee_id"] for p in got] == ["e1"]


def test_a_person_with_no_hours_is_not_a_utilisation_row():
    u = people.utilization([_alloc(1, "FALCON", [("e1", "Aisha Khan", 0)])])
    assert u["people"] == []


def test_a_contract_reads_by_the_name_the_rest_of_the_app_uses():
    # A chosen callsign wins over the legal contractor, matching burn.compute — a
    # person's contract list must not be the one place in the app that shows a PIID
    # where every other view shows "FALCON".
    d = people.build_directory(
        facts=[_fact(contract_id=1)],
        contracts=[
            {
                "id": 1,
                "piid": "PIID-1",
                "nickname": "FALCON",
                "contract": {"contractor": "Acme Defense LLC"},
            }
        ],
        manual_people=[],
        attr_rows=[],
    )
    assert d["people"][0]["contracts"][0]["contract"] == "FALCON"
