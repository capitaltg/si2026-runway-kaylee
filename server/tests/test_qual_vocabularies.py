"""#98 — quals need comparable vocabularies before #66 can check anything.

#69 shipped all three qualification fields as free text. Nothing read them, so
nothing noticed. #66 is the first consumer, and a compliance check compares a
person's credentials to a labor category's floor — which it cannot do across two
vocabularies that were never made to line up.

The failure mode these tests exist to prevent is the bad one: a check that reports
"does not meet" because `TS-SCI` and `TS/SCI` are different strings. That does not
read as a data problem, it reads as a finding, and a compliance demo cannot afford
a fabricated finding.

So what is pinned here:

  1. The ladders are *ordered*, because the check is "meets or exceeds", not
     equality. A TS/SCI holder clears a Secret floor.
  2. An unrecognised value is *not comparable* — never a low rank. Missing
     information must not be reportable as a failure.
  3. Fixtura's floor vocabulary (`_LCATS`) is a subset of these ladders. This is
     the actual mismatch the ticket is about: the two sides have to line up.
  4. Education is two things. Only the level is comparable; the field of study is
     context, and it never counts toward coverage.
  5. `unknown` survives the change to dropdowns — an unset state that is not a
     value, and for clearance emphatically not "None".

DB-free, like the rest of this suite.
"""

from app import people


def _entry(value, source_note=None):
    return {"value": value, "source_note": source_note}


# ------------------------------------------------------------- ordered ladders


def test_clearance_is_ordered_so_a_higher_holder_clears_a_lower_floor():
    # The whole reason these are tuples and not sets: #66 asks "meets or exceeds".
    assert people.clearance_rank("TS/SCI") > people.clearance_rank("Secret")
    assert people.clearance_rank("Secret") > people.clearance_rank("None")
    assert people.clearance_rank("Top Secret") > people.clearance_rank("Public Trust")


def test_education_is_ordered_the_same_way():
    assert people.education_rank("Master's") > people.education_rank("Bachelor's")
    assert people.education_rank("Doctorate") > people.education_rank("HS Diploma")
    assert people.education_rank("Associate's") > people.education_rank("HS Diploma")


def test_no_clearance_is_a_value_on_the_ladder_not_an_absence():
    # "Holds no clearance" is a recorded fact that fails a Secret floor. "We have
    # not recorded one" is the absence of a row. Conflating them turns an unasked
    # question into a finding.
    assert people.clearance_rank("None") == 0
    assert people.clearance_rank(None) is None
    assert people.clearance_rank("") is None


# --------------------------------------------- unrecognised means not comparable


def test_the_five_spellings_of_one_clearance_do_not_silently_rank():
    # Every one of these is reachable through a free-text box, and every one of them
    # would have compared unequal to a `TS/SCI` floor. None of them may come back a
    # *rank* — that would be a wrong answer confidently given.
    for spelling in ["TS-SCI", "Top Secret/SCI", "ts sci", "TS//SCI", "ts/sci"]:
        assert people.clearance_rank(spelling) is None, spelling


def test_a_degree_string_is_not_an_education_level():
    # The old placeholder text. It cannot be compared to "Bachelor's" at all, which
    # is the point of splitting the level off the field of study.
    assert people.education_rank("BS Computer Science") is None


def test_years_that_are_not_numbers_are_not_comparable():
    assert people.years_value("12") == 12
    assert people.years_value("12.5") == 12.5
    for junk in ["12 yrs", "~12", "12+", "twelve", None, ""]:
        assert people.years_value(junk) is None, junk


# ------------------------------------------------- lining up with Fixtura's floors


def test_fixtura_floor_vocabularies_are_a_subset_of_these_ladders():
    # The concrete mismatch from the ticket. Fixtura's `_LCATS` states each
    # category's floor in a fixed vocabulary; if a floor value isn't on our ladder,
    # #66 compares a credential to something it cannot rank and every check against
    # that category is unanswerable. Hard-coded rather than imported — Fixtura is a
    # separate repo, and this test's job is to fail loudly if the two drift.
    for floor in ["None", "Secret", "TS/SCI"]:
        assert people.clearance_rank(floor) is not None, floor
    for floor in ["HS Diploma", "Bachelor's", "Master's"]:
        assert people.education_rank(floor) is not None, floor


# ------------------------------------------------------------ server-side refusal


def test_a_new_unrecognised_clearance_is_refused():
    # Server-side, not only in the dropdown: the API is the contract and #66 trusts
    # it. A dropdown constrains one client.
    problem = people.validate_quals({"clearance": _entry("TS-SCI")}, {})
    assert problem and "TS/SCI" in problem


def test_a_degree_typed_into_the_level_field_is_refused_and_says_where_it_goes():
    problem = people.validate_quals({"education": _entry("BS Computer Science")}, {})
    assert problem and "field of study" in problem


def test_years_must_be_a_number_and_the_argument_goes_in_the_source_note():
    problem = people.validate_quals({"years_experience": _entry("12+ yrs")}, {})
    assert problem and "source note" in problem
    assert people.validate_quals({"years_experience": _entry("12")}, {}) is None


def test_years_outside_a_working_career_are_refused():
    assert people.validate_quals({"years_experience": _entry("-3")}, {}) is not None
    assert people.validate_quals({"years_experience": _entry("400")}, {}) is not None
    assert people.validate_quals({"years_experience": _entry("0")}, {}) is None


def test_recognised_values_pass():
    ok = {
        "clearance": _entry("TS/SCI", "per JPAS, 2026-02"),
        "education": _entry("Master's"),
        "education_field": _entry("Computer Science"),
        "years_experience": _entry("14", "per proposal resume, 2026-03"),
    }
    assert people.validate_quals(ok, {}) is None


def test_an_unknown_field_is_still_refused():
    # #69's rule, unchanged: the attrs table does not become key-value storage.
    problem = people.validate_quals({"salary": _entry("nope")}, {})
    assert problem and "salary" in problem


# ---------------------------------------------------- unknown survives, so does old data


def test_clearing_a_field_back_to_unknown_is_never_a_violation():
    # A blank is a delete. If validation refused it, "optional" would stop being
    # true the moment somebody made a typo.
    assert (
        people.validate_quals({"clearance": _entry("")}, {"clearance": "Secret"})
        is None
    )
    assert people.validate_quals({"education": _entry(None)}, {}) is None


def test_a_value_recorded_before_the_vocabularies_can_still_be_re_saved():
    # Editing only the source note re-sends the value. If that were refused, an old
    # free-text clearance would have uneditable provenance — and the ticket is
    # explicit that existing values are left as-is rather than guessed at.
    stored = {"clearance": "TS-SCI"}
    incoming = {"clearance": _entry("TS-SCI", "per JPAS, 2026-04")}
    assert people.validate_quals(incoming, stored) is None


def test_but_a_different_unrecognised_value_is_still_refused():
    # Grandfathering is per exact value, so the set of off-ladder values can only
    # ever shrink.
    stored = {"clearance": "TS-SCI"}
    assert people.validate_quals({"clearance": _entry("TS//SCI")}, stored) is not None


# ------------------------------------------------- education splits into two fields


def test_field_of_study_is_stored_but_never_counts_toward_coverage():
    # It is context a human reads, not a credential. If it counted, a person with a
    # field of study and nothing else would read as partially qualified, and #66's
    # "checked versus unchecked" counts would be measuring the wrong thing.
    assert people.quals_status({"education_field": {"value": "Computer Science"}}) == (
        people.UNKNOWN
    )
    full = {
        "education": {"value": "Master's"},
        "years_experience": {"value": "14"},
        "clearance": {"value": "Secret"},
    }
    assert people.quals_status(full) == people.COMPLETE
    # Adding the optional field of study does not un-complete anybody.
    assert people.quals_status({**full, "education_field": {"value": "CS"}}) == (
        people.COMPLETE
    )


def test_field_of_study_reaches_the_directory_payload():
    payload = people.build_directory(
        facts=[
            {
                "contract_id": 1,
                "employee_id": "e1",
                "employee": "Aisha Khan",
                "charge_code": "0001",
                "labor_category": "Senior Software Engineer",
                "weeks": 6,
                "first_week": "2026-01-02",
                "last_week": "2026-02-06",
            }
        ],
        contracts=[{"id": 1, "piid": "PIID-1", "contract": {"contractor": "Acme"}}],
        manual_people=[],
        attr_rows=[
            {
                "employee_id": "e1",
                "field": "education",
                "value": "Master's",
                "source_note": None,
                "authored_by": "Kaylee",
                "authored_at": "2026-08-05 12:00:00",
            },
            {
                "employee_id": "e1",
                "field": "education_field",
                "value": "Computer Science",
                "source_note": None,
                "authored_by": "Kaylee",
                "authored_at": "2026-08-05 12:00:00",
            },
        ],
    )
    person = payload["people"][0]
    assert person["quals"]["education"]["value"] == "Master's"
    assert person["quals"]["education_field"]["value"] == "Computer Science"
    # Level recorded, years and clearance not — still partial. The field of study
    # neither completes it nor is missing from it.
    assert person["quals_status"] == people.PARTIAL


def test_the_directory_serves_the_vocabularies_it_expects_back():
    # One ladder, served to the editor, rather than a second copy in JSX that drifts
    # from this one — which is this ticket's own bug, one layer further out.
    payload = people.build_directory(
        facts=[], contracts=[], manual_people=[], attr_rows=[]
    )
    assert payload["qual_vocab"]["clearance"] == list(people.CLEARANCE_LEVELS)
    assert payload["qual_vocab"]["education"] == list(people.EDUCATION_LEVELS)
