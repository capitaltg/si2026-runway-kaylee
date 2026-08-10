"""#66 — does the person filling the seat qualify for the rate it bills at.

The tests are weighted deliberately. The arithmetic of "10 >= 8" needs one case; the
cases that earn their keep are the ones where *nothing is known*, because the failure
this feature is most able to cause is reporting an unchecked person as clear.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import allocation, compliance, lcat, suggest


def floors(edu=None, yrs=None, clr=None):
    return lcat.Floors(min_education=edu, min_experience_yrs=yrs, clearance=clr)


def line(rate=200.0, lcat_name="Senior Cyber SME", clin="0001", **kw):
    return lcat.RateLine(
        clin=clin,
        lcat=lcat_name,
        rate=rate,
        key=lcat.normalize(lcat_name),
        floors=floors(**kw),
    )


def quals(**kw):
    """The directory's annotated attr shape, which is what allocation passes in."""
    return {k: {"value": str(v)} for k, v in kw.items() if v is not None}


# --- the states -----------------------------------------------------------------


def test_meeting_every_printed_floor_is_compliant():
    v = compliance.check(
        quals(education="Master's", years_experience=12, clearance="TS/SCI"),
        line(edu="Master's", yrs=10, clr="TS/SCI"),
    )
    assert v["status"] == compliance.COMPLIANT
    assert v["failures"] == []
    assert v["unchecked"] == []


def test_exceeding_a_floor_clears_it():
    # The check is "meets or exceeds", so rank order is the whole point: a Doctorate
    # clears a Bachelor's floor and TS/SCI clears Secret.
    v = compliance.check(
        quals(education="Doctorate", years_experience=30, clearance="TS/SCI"),
        line(edu="Bachelor's", yrs=8, clr="Secret"),
    )
    assert v["status"] == compliance.COMPLIANT


def test_short_on_years_is_under_qualified_and_reports_the_numbers():
    v = compliance.check(
        quals(education="Master's", years_experience=3, clearance="TS/SCI"),
        line(edu="Master's", yrs=10, clr="TS/SCI"),
    )
    assert v["status"] == compliance.UNDER_QUALIFIED
    # "Wei Chen: 3 yrs experience, Senior Cyber SME requires 10" — the copy needs both
    # numbers, so the verdict carries both rather than a boolean.
    assert v["failures"] == [
        {
            "field": "years_experience",
            "label": "Years of experience",
            "required": 10,
            "held": "3",
        }
    ]


def test_a_low_clearance_is_broken_out_from_other_shortfalls():
    # Its own state because it stops work today rather than surfacing in an audit.
    v = compliance.check(
        quals(education="Master's", years_experience=12, clearance="Secret"),
        line(edu="Master's", yrs=10, clr="TS/SCI"),
    )
    assert v["status"] == compliance.CLEARANCE_GAP


def test_a_clearance_gap_outranks_a_years_shortfall_but_reports_both():
    v = compliance.check(
        quals(education="Master's", years_experience=2, clearance="Secret"),
        line(edu="Master's", yrs=10, clr="TS/SCI"),
    )
    assert v["status"] == compliance.CLEARANCE_GAP
    assert {f["field"] for f in v["failures"]} == {"clearance", "years_experience"}


def test_holding_no_clearance_fails_a_clearance_floor():
    # "None" is a recorded fact — this person holds no clearance — and is a real
    # failure against a Secret floor. It is not the same as nobody having asked.
    v = compliance.check(
        quals(education="Master's", years_experience=12, clearance="None"),
        line(edu="Master's", yrs=10, clr="Secret"),
    )
    assert v["status"] == compliance.CLEARANCE_GAP


# --- missing information is never a finding -------------------------------------


def test_nobody_checked_is_unknown_not_compliant():
    # The day-one state of every synced person, and the single most important
    # assertion in this file: an un-annotated contract must not read as a clean one.
    v = compliance.check({}, line(edu="Master's", yrs=10, clr="TS/SCI"))
    assert v["status"] == compliance.UNKNOWN
    assert v["failures"] == []
    assert {u["field"] for u in v["unchecked"]} == {
        "education",
        "years_experience",
        "clearance",
    }
    assert all(u["reason"] == compliance.NO_VALUE for u in v["unchecked"])


def test_one_satisfied_field_never_carries_the_others():
    # Years entered and met, education and clearance blank. Not compliant: partial
    # data is normal and must stay visibly partial.
    v = compliance.check(
        quals(years_experience=12), line(edu="Master's", yrs=10, clr="TS/SCI")
    )
    assert v["status"] == compliance.UNKNOWN
    years = next(f for f in v["fields"] if f["field"] == "years_experience")
    assert years["state"] == compliance.MET
    assert {u["field"] for u in v["unchecked"]} == {"education", "clearance"}


def test_a_real_shortfall_still_reports_alongside_unknown_fields():
    # Years entered and short, education unknown: under-qualified on what we checked,
    # and still openly unchecked on the rest. The finding is not softened by the gap
    # and the gap is not hidden by the finding.
    v = compliance.check(quals(years_experience=3), line(edu="Master's", yrs=10))
    assert v["status"] == compliance.UNDER_QUALIFIED
    assert [f["field"] for f in v["failures"]] == ["years_experience"]
    assert [u["field"] for u in v["unchecked"]] == ["education"]


def test_a_line_that_prints_no_minimums_is_no_floor_not_compliant():
    # A document gap, not a person who passed. Kept out of `compliant` so a contract
    # whose award printed no minimums cannot report itself clean.
    v = compliance.check(quals(education="Master's"), line())
    assert v["status"] == compliance.NO_FLOOR
    assert v["unchecked"] == []


def test_an_unreadable_floor_is_the_documents_problem_not_the_persons():
    # "BS/BA or equivalent" is not on the education ladder. Unchecked, and flagged as
    # the floor's fault so the UI does not send the user off to type more quals.
    v = compliance.check(
        quals(education="Master's", years_experience=12),
        line(edu="BS/BA or equivalent", yrs=10),
    )
    assert v["status"] == compliance.UNKNOWN
    edu = next(f for f in v["fields"] if f["field"] == "education")
    assert edu["state"] == compliance.UNCHECKED
    assert edu["reason"] == compliance.FLOOR_NOT_COMPARABLE


def test_a_grandfathered_free_text_value_is_unchecked_not_short():
    # A clearance typed before #98 closed the vocabularies. "TS-SCI" against a "TS/SCI"
    # floor must not read as "does not meet" — that looks like a finding and is a typo.
    v = compliance.check(quals(clearance="TS-SCI"), line(clr="TS/SCI"))
    assert v["status"] == compliance.UNKNOWN
    clr = next(f for f in v["fields"] if f["field"] == "clearance")
    assert clr["reason"] == compliance.VALUE_NOT_COMPARABLE


def test_unmatched_hours_are_not_a_quals_finding():
    # No priced line backs these hours, so there is no floor to check. #64 already
    # reports that; grading somebody against a category nobody agreed they bill under
    # would invent a finding out of a mapping guess.
    v = compliance.check(quals(education="HS Diploma"), None)
    assert v["status"] == compliance.UNPRICED
    assert v["line"] is None
    # And it is its own state, not folded into the award-printed-no-minimums one: those
    # have different owners and the rollup copy names them differently.
    assert v["status"] != compliance.NO_FLOOR


# --- over-qualified --------------------------------------------------------------


def test_clearing_a_better_paid_line_reads_as_over_qualified():
    billed = line(rate=120.0, lcat_name="Cyber Analyst", edu="Bachelor's", yrs=3)
    senior = line(rate=250.0, lcat_name="Senior Cyber SME", edu="Master's", yrs=10)
    v = compliance.check(
        quals(education="Master's", years_experience=12, clearance="TS/SCI"),
        billed,
        [billed, senior],
    )
    assert v["status"] == compliance.OVER_QUALIFIED
    assert v["over_qualified_for"] == {
        "lcat": "Senior Cyber SME",
        "clin": "0001",
        "rate": 250.0,
    }


def test_over_qualified_needs_full_evidence_not_partial():
    # Suggesting somebody is worth a more expensive category is a money claim, so the
    # bar is higher than the headline check's: unknown education keeps them compliant
    # on their own line and silent about the senior one.
    billed = line(rate=120.0, lcat_name="Cyber Analyst", yrs=3)
    senior = line(rate=250.0, lcat_name="Senior Cyber SME", edu="Master's", yrs=10)
    v = compliance.check(quals(years_experience=12), billed, [billed, senior])
    assert v["status"] == compliance.COMPLIANT
    assert v["over_qualified_for"] is None


def test_a_shortfall_is_never_dressed_up_as_over_qualified():
    billed = line(rate=250.0, lcat_name="Senior Cyber SME", yrs=10)
    cheap = line(rate=80.0, lcat_name="Junior Analyst", yrs=1)
    v = compliance.check(quals(years_experience=2), billed, [billed, cheap])
    assert v["status"] == compliance.UNDER_QUALIFIED


# --- rollups ---------------------------------------------------------------------


def test_the_rollup_keeps_the_two_denominators_apart():
    # "3 of 11 checked people under-qualified · 2 clearance gaps · 29 not yet checked."
    # Both numbers, no ratio — a percentage over the checked subset presented as
    # covering the contract is the one output this must never produce.
    r = compliance.rollup(
        [compliance.COMPLIANT] * 6
        + [compliance.UNDER_QUALIFIED] * 3
        + [compliance.CLEARANCE_GAP] * 2
        + [compliance.UNKNOWN] * 29
    )
    assert r["people"] == 40
    assert r["checked"] == 11
    assert r["not_checked"] == 29
    assert r["under_qualified"] == 3
    assert r["clearance_gap"] == 2
    assert r["has_findings"] is True
    assert not any(isinstance(v, float) for v in r.values())


def test_an_unchecked_contract_has_no_findings():
    r = compliance.rollup([compliance.UNKNOWN] * 12)
    assert r["has_findings"] is False
    assert r["checked"] == 0
    assert r["not_checked"] == 12


def test_no_floor_people_are_not_counted_as_checked():
    r = compliance.rollup([compliance.NO_FLOOR] * 5)
    assert r["checked"] == 0
    assert r["no_floor"] == 5
    assert r["has_findings"] is False


def test_unpriced_hours_are_counted_apart_from_a_missing_floor():
    # A document that priced a category without printing its minimums, and hours that
    # resolve to no category at all, are two different problems with two different
    # fixes. Counting them together produces a sentence that is false for half of it.
    r = compliance.rollup([compliance.NO_FLOOR] * 2 + [compliance.UNPRICED] * 3)
    assert r["no_floor"] == 2
    assert r["unpriced"] == 3
    assert r["checked"] == 0
    assert r["people"] == 5


def test_worst_wins_across_a_persons_clins():
    # Clean on three CLINs and short a clearance on the fourth is a clearance gap. The
    # badge follows the exposure, not the majority or the biggest cell.
    assert (
        compliance.worst(
            [compliance.COMPLIANT, compliance.COMPLIANT, compliance.CLEARANCE_GAP]
        )
        == compliance.CLEARANCE_GAP
    )
    assert (
        compliance.worst([compliance.COMPLIANT, compliance.UNKNOWN])
        == compliance.UNKNOWN
    )
    # No floor anywhere is the weakest claim available and loses to everything.
    assert (
        compliance.worst([compliance.NO_FLOOR, compliance.COMPLIANT])
        == compliance.COMPLIANT
    )
    assert compliance.worst([]) is None


# --- wired into the grid ---------------------------------------------------------


def _contract():
    return {
        "id": 1,
        "piid": "TEST-0001",
        "contract": {"piid": "TEST-0001"},
        "clins": [
            {
                "clin": "0001",
                "title": "Cyber services",
                "is_labor": True,
                "ceiling": 1_000_000.0,
                "obligated": 1_000_000.0,
                "est_hours": 5000,
                "labor_rates": [
                    {
                        "lcat": "Senior Cyber SME",
                        "loaded_rate": 250.0,
                        "min_education": "Master's",
                        "min_experience_yrs": 10,
                        "clearance": "TS/SCI",
                    },
                    {
                        "lcat": "Cyber Analyst",
                        "loaded_rate": 120.0,
                        "min_education": "Bachelor's",
                        "min_experience_yrs": 3,
                        "clearance": "Secret",
                    },
                ],
            }
        ],
        "periods": [],
    }


def _timesheets():
    return [
        {
            "employee": "Wei Chen",
            "employee_id": "E-1",
            "week_ending": "2026-01-09",
            "charge_code": "0001",
            "labor_category": "Senior Cyber SME",
            "total_hours": 40,
            "contract_no": "TEST-0001",
        },
        {
            "employee": "Dana Ruiz",
            "employee_id": "E-2",
            "week_ending": "2026-01-09",
            "charge_code": "0001",
            "labor_category": "Cyber Analyst",
            "total_hours": 40,
            "contract_no": "TEST-0001",
        },
    ]


def test_the_grid_carries_a_verdict_per_cell_and_a_badge_per_row():
    alloc = allocation.compute_allocation(
        _contract(),
        _timesheets(),
        quals_by_person={
            # Billed as a Senior Cyber SME on 3 years — the audit exposure.
            "E-1": {"years_experience": {"value": "3"}},
        },
    )
    rows = {r["id"]: r for r in alloc["employees"]}
    assert rows["E-1"]["compliance_status"] == compliance.UNDER_QUALIFIED
    assert rows["E-1"]["cells"]["0001"]["compliance"]["failures"][0]["required"] == 10
    # Nobody typed anything about Dana, so she is unknown — never compliant.
    assert rows["E-2"]["compliance_status"] == compliance.UNKNOWN
    assert rows["E-2"]["quals_status"] == "unknown"


def test_the_grid_rolls_up_per_clin_and_per_contract():
    alloc = allocation.compute_allocation(
        _contract(),
        _timesheets(),
        quals_by_person={"E-1": {"years_experience": {"value": "3"}}},
    )
    clin = alloc["clins"][0]["compliance"]
    assert clin["people"] == 2
    assert clin["under_qualified"] == 1
    assert clin["not_checked"] == 1
    assert alloc["contract"]["compliance"]["people"] == 2
    assert alloc["contract"]["compliance"]["has_findings"] is True
    assert alloc["contract"]["quals_checked"] is True


def test_a_grid_nobody_has_annotated_reports_nothing_checked():
    # The whole contract on day one. `has_findings` false must be readable as "nobody
    # has looked", which is what `quals_checked` is for.
    alloc = allocation.compute_allocation(_contract(), _timesheets())
    roll = alloc["contract"]["compliance"]
    assert roll["people"] == 2
    assert roll["checked"] == 0
    assert roll["not_checked"] == 2
    assert roll["has_findings"] is False
    assert alloc["contract"]["quals_checked"] is False


def test_the_grid_never_reaches_the_directory_for_quals():
    # The structural half of the rule in people.py: allocation reads credentials only
    # from what the caller handed it. Passing nothing must yield unknown verdicts even
    # though the database may be full of annotations.
    alloc = allocation.compute_allocation(_contract(), _timesheets())
    assert all(r["compliance_status"] == compliance.UNKNOWN for r in alloc["employees"])
    # And the check module reads the directory's *vocabularies*, never its rows.
    assert not hasattr(compliance, "db")


def test_floors_ride_on_the_resolved_line_through_an_alias():
    # #64 is a dependency for a reason: when a confirmed alias prices these hours off
    # a different line, the floors that apply are that line's. A name lookup here
    # would grade the person against the category the timesheet spelled instead.
    contract = _contract()
    # A name no amount of normalising reaches — the mapping only exists because a user
    # confirmed it, which is what makes this the alias path and not the fuzzy one.
    contract["lcat_aliases"] = [
        {"from": "Packet Wrangler", "lcat": "Senior Cyber SME", "clin": "0001"}
    ]
    rows = _timesheets()
    rows[0]["labor_category"] = "Packet Wrangler"
    alloc = allocation.compute_allocation(
        contract, rows, quals_by_person={"E-1": {"years_experience": {"value": "3"}}}
    )
    cell = next(r for r in alloc["employees"] if r["id"] == "E-1")["cells"]["0001"]
    assert cell["via"] == "alias"
    assert cell["lcat"] == "Packet Wrangler"
    assert cell["compliance"]["line"]["lcat"] == "Senior Cyber SME"
    assert cell["compliance"]["line"]["floors"]["min_experience_yrs"] == 10
    assert cell["compliance"]["status"] == compliance.UNDER_QUALIFIED


# --- fed into the suggests solver (#63) ------------------------------------------


def _cell(status, hours=40.0, rate=250.0):
    return {"hours": hours, "rate": rate, "compliance": {"status": status}}


def _row(cell):
    return {
        "id": "E-1",
        "name": "Wei Chen",
        "lcat": "Senior Cyber SME",
        "expected": {"hours": 40},
        "cells": {"0001": cell},
    }


def test_a_move_off_a_category_they_dont_meet_reports_it():
    cell = _cell(compliance.UNDER_QUALIFIED)
    move = suggest._candidate("trim", _row(cell), cell, "0001", 20.0)
    assert move["clears_compliance_flag"] is True


def test_adding_hours_never_claims_to_clear_a_finding():
    # The move makes the exposure bigger. Advertising it as a fix would be worse than
    # saying nothing at all.
    cell = _cell(compliance.CLEARANCE_GAP, hours=20.0)
    move = suggest._candidate("raise", _row(cell), cell, "0001", 40.0)
    assert move["clears_compliance_flag"] is False


def test_an_unchecked_person_is_not_a_finding_to_clear():
    # Nobody typed any quals, so there is no finding — and a solver that claimed
    # otherwise would be inventing compliance value out of missing data.
    cell = _cell(compliance.UNKNOWN)
    move = suggest._candidate("trim", _row(cell), cell, "0001", 20.0)
    assert move["clears_compliance_flag"] is False


def test_compliance_only_breaks_ties_and_never_outranks_hours():
    # The solver closes a dollar gap. If a quals finding could reorder the list, it
    # would quietly become a compliance tool that also does money.
    big = {
        "hours_moved": 20.0,
        "clears_lcat_flag": False,
        "clears_compliance_flag": False,
        "person": "Ana",
    }
    small_but_compliant = {
        "hours_moved": 4.0,
        "clears_lcat_flag": False,
        "clears_compliance_flag": True,
        "person": "Bo",
    }
    assert suggest._order([small_but_compliant, big])[0] is big

    # Same hours, though, and the one that also closes a finding wins.
    tie_plain = dict(big, hours_moved=10.0, person="Ana")
    tie_flagged = dict(small_but_compliant, hours_moved=10.0, person="Zed")
    assert suggest._order([tie_plain, tie_flagged])[0] is tie_flagged
