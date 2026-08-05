"""#84 — expected hours as data, so utilisation stops meaning "hours ÷ 40".

Runway divided hours by 40 in seven places and called it utilisation. The number
that produced was wrong in a specific, expensive direction: a person billing a full
1,880-hour year read as ~85% utilised, so the matrix flagged nobody as available, and
a planned person seeded at 40 hrs/wk projected forward burn as though they never took
a holiday. On a tool whose entire value is the accuracy of a runway date, the second
one is the real defect.

What is pinned here:

  1. The precedence chain resolves person → LCAT → contract → fallback, and every
     resolution names the level that answered. The UI has to show which level
     supplied the number, so an unlabelled float is not a sufficient return value.
  2. 100% means fully utilised. A 32-hour person at 32 hours is 1.0, not 0.8.
  3. The fallback is *labelled* a fallback. Behaviour for an unconfigured contract is
     unchanged from before the ticket — the difference is that the number now admits
     it is an assumption.
  4. FTE and utilisation are different measures and both survive. 40 is the correct
     denominator of an FTE (2,080 hrs/yr by definition) and the wrong denominator of
     a utilisation, which is exactly the conflation the ticket exists to end.
  5. Not-comparable is never zero. An unparseable or unset expectation yields None,
     the same way #98 made an unrecognised clearance not-comparable rather than
     low-ranked — reporting missing information as "idle" is the same failure.
  6. LCAT defaults fold through `lcat.normalize`, so a level is never crossed: a Mid
     default must not answer for a Senior.

DB-free, like the rest of this suite.
"""

from app import allocation, capacity, people


# ------------------------------------------------------------------- precedence


def test_the_chain_resolves_person_over_lcat_over_contract_over_fallback():
    contract = {
        "utilization_target": 0.8,
        "lcat_expected_hours": {"Software Engineer (Mid)": 36},
    }

    # Nothing set anywhere: the old 40, but now labelled as the assumption it is.
    bare = capacity.resolve(lcat="Software Engineer (Mid)", contract={})
    assert bare["hours"] == 40.0
    assert bare["level"] == capacity.FALLBACK
    assert bare["assumed"] is True

    # Contract target only — 80% of a full week.
    only_target = capacity.resolve(
        lcat="Business Analyst", contract={"utilization_target": 0.8}
    )
    assert only_target["hours"] == 32.0
    assert only_target["level"] == capacity.CONTRACT
    assert only_target["assumed"] is False

    # An LCAT default beats the contract target for that category.
    by_lcat = capacity.resolve(lcat="Software Engineer (Mid)", contract=contract)
    assert by_lcat["hours"] == 36.0
    assert by_lcat["level"] == capacity.LCAT

    # …and a category with no default of its own still gets the contract target.
    assert capacity.resolve(lcat="Business Analyst", contract=contract)["level"] == (
        capacity.CONTRACT
    )

    # The person's own week wins outright — part-time, or split with another job.
    own = capacity.resolve(
        person_hours="32", lcat="Software Engineer (Mid)", contract=contract
    )
    assert own["hours"] == 32.0
    assert own["level"] == capacity.PERSON


def test_every_resolution_says_which_level_supplied_the_number():
    # The acceptance criterion "the UI shows which level supplied the number" is
    # unbuildable if this function returns a bare float, which is why it doesn't.
    for res in (
        capacity.resolve(person_hours=20),
        capacity.resolve(lcat="PM", contract={"lcat_expected_hours": {"PM": 30}}),
        capacity.resolve(contract={"utilization_target": 0.9}),
        capacity.resolve(contract={}),
    ):
        assert res["level"] in (
            capacity.PERSON,
            capacity.LCAT,
            capacity.CONTRACT,
            capacity.FALLBACK,
        )
        assert res["label"]


def test_a_utilisation_target_reads_as_a_fraction_or_a_percentage():
    # Both spellings turn up in typed input, and "0.9 hours a week" is not a thing
    # anyone means.
    assert capacity.resolve(contract={"utilization_target": 0.85})["hours"] == 34.0
    assert capacity.resolve(contract={"utilization_target": 85})["hours"] == 34.0
    # Nonsense falls through to the fallback rather than producing a nonsense week.
    for junk in ("", None, "abc", -1, 0, 400):
        assert capacity.resolve(contract={"utilization_target": junk})["level"] == (
            capacity.FALLBACK
        )


def test_the_default_contract_target_is_eighty_percent():
    # Erring low keeps the forward projection from overstating burn, which is the
    # failure direction that costs money.
    assert capacity.DEFAULT_UTILIZATION_TARGET == 0.80
    assert capacity.target_hours(capacity.DEFAULT_UTILIZATION_TARGET) == 32.0


# ------------------------------------------------------- what utilisation means


def test_a_thirty_two_hour_person_at_thirty_two_hours_is_fully_utilised():
    # The ticket's headline case. Under the old maths this person read 0.8 and the
    # matrix called them available.
    expected = capacity.resolve(person_hours=32)["hours"]
    assert capacity.utilization(32, expected) == 1.0
    # And the old denominator would have said otherwise.
    assert capacity.utilization(32, 40) == 0.8


def test_an_unset_expectation_is_no_number_rather_than_zero_percent():
    # Same rule #98 set for an unrecognised clearance: missing information must not
    # be reportable as a finding. 0% utilised reads as "idle", which is a claim.
    assert capacity.utilization(30, None) is None
    assert capacity.utilization(30, "not hours") is None
    assert capacity.utilization(30, 0) is None
    assert capacity.utilization("not hours", 32) is None


def test_over_expected_hours_is_reported_not_clamped():
    # Someone booked past their expected week is the signal #83 wants. Capping it at
    # 100% would hide exactly the case worth surfacing.
    assert capacity.utilization(48, 32) == 1.5


# ------------------------------------------------------------- FTE is not utilisation


def test_fte_keeps_the_forty_hour_week_because_that_is_its_definition():
    # One FTE is a 2,080-hour year. This is the one place a bare 40 is correct, and
    # it is a *separate* constant from the utilisation fallback so that changing a
    # contract's target can never silently redefine a headcount.
    assert capacity.FTE_HOURS_PER_WEEK == 40.0
    assert capacity.fte(400, 10) == 1.0
    assert capacity.fte(200, 10) == 0.5
    assert capacity.fte(100, 0) is None


def test_fte_and_utilisation_disagree_on_purpose_for_the_same_person():
    # A 32-hr/wk person working every one of their hours for 10 weeks is fully
    # utilised *and* 0.8 of an FTE. Both are true; `hours / 40` could only ever say
    # one of them, and said it about the wrong question.
    assert capacity.utilization(320, 32 * 10) == 1.0
    assert capacity.fte(320, 10) == 0.8


# --------------------------------------------------------------- LCAT defaults


def test_an_lcat_default_never_crosses_a_level():
    # Charles Taylor bills Mid on one contract and Senior on another. A Mid default
    # answering for his Senior hours would be the same class of bug as billing senior
    # hours at a mid rate, which lcat.normalize already exists to prevent.
    contract = {"lcat_expected_hours": {"Software Engineer (Mid)": 36}}
    assert (
        capacity.resolve(lcat="Software Engineer (Mid)", contract=contract)["level"]
        == capacity.LCAT
    )
    assert (
        capacity.resolve(lcat="Senior Software Engineer", contract=contract)["level"]
        == capacity.FALLBACK
    )


def test_an_lcat_default_survives_a_spelling_difference():
    # Folded with the same normaliser the rate resolution uses, so a default typed
    # against the award's spelling still answers for the timesheet's — abbreviations
    # and a dropped certification both land on the same key.
    contract = {"lcat_expected_hours": {"Senior Software Engineer": 34}}
    assert capacity.resolve(lcat="Sr. Software Engineer", contract=contract)[
        "hours"
    ] == (34.0)

    pm = {"lcat_expected_hours": {"Program Manager (PMP)": 30}}
    assert capacity.resolve(lcat="Program Manager", contract=pm)["hours"] == 30.0

    # What it does *not* fold is a qualifier spelled without parentheses — that is
    # `lcat.suggest`'s job at a higher layer, where a wrong guess costs a declined
    # offer rather than a wrong week. Pinned so this reads as a known edge, not a
    # surprise: the default simply doesn't apply and the contract target answers.
    assert capacity.resolve(lcat="program manager, pmp", contract=pm)["level"] == (
        capacity.FALLBACK
    )


def test_unusable_lcat_defaults_are_dropped_not_crashed_on():
    caps = capacity.contract_capacity(
        {"lcat_expected_hours": {"PM": "abc", "BA": 0, "SE": 36}}
    )
    assert list(caps["lcat_hours"].values()) == [36.0]


def test_a_contract_from_before_this_ticket_reads_clean():
    # Every contract in the DB predates these keys.
    for blob in (None, {}, {"contract": {"piid": "X"}}):
        caps = capacity.contract_capacity(blob)
        assert caps == {"target": None, "lcat_hours": {}}
        assert capacity.resolve(contract=blob)["level"] == capacity.FALLBACK


# ------------------------------------------------------------------- validation


def test_expected_hours_must_be_a_plausible_week():
    assert capacity.validate_expected_hours("32") is None
    assert capacity.validate_expected_hours("37.5") is None
    # A month's hours is the slip this guard is for.
    assert "week" in capacity.validate_expected_hours("160")
    assert "number" in capacity.validate_expected_hours("part time")
    # Zero is not "unset" — clearing the field is how you go back to the default,
    # and the message has to say so or the user retypes 0 forever.
    assert "clear the field" in capacity.validate_expected_hours("0")


def test_unparseable_stored_hours_are_unset_never_zero():
    # Treating a bad stored value as 0 would make everyone infinitely utilised.
    for junk in (None, "", "abc", "0", "-4"):
        assert capacity.hours_value(junk) is None
    assert capacity.hours_value("32") == 32.0


# --------------------------------------------------- the cross-contract question


def test_a_persons_own_week_wins_across_the_whole_portfolio():
    res = capacity.portfolio_expected(
        person_hours="32",
        per_contract=[{"hours": 40.0, "level": capacity.FALLBACK}],
    )
    assert res["hours"] == 32.0
    assert res["level"] == capacity.PERSON


def test_two_contracts_never_add_up_to_a_sixty_four_hour_person():
    # Summing two contracts' 32-hour defaults claims a 64-hour week; averaging claims
    # someone splitting two full-time expectations is only expected to work one. The
    # widest week any of their contracts assumes is the least-wrong answer, and it is
    # reported with the level it came from so it never reads as settled.
    res = capacity.portfolio_expected(
        per_contract=[
            {"hours": 32.0, "level": capacity.CONTRACT},
            {"hours": 36.0, "level": capacity.LCAT},
        ]
    )
    assert res["hours"] == 36.0
    assert res["level"] == capacity.LCAT


def test_a_person_on_no_contracts_still_resolves():
    assert capacity.portfolio_expected()["level"] == capacity.FALLBACK


# ------------------------------------------------- wired into the real surfaces


def _contract(target=None, lcat_hours=None):
    blob = {
        "id": 1,
        "contract": {"piid": "TEST-1", "total_ceiling": 1_000_000},
        "clins": [
            {
                "clin": "0001",
                "title": "Labor",
                "is_labor": True,
                "ceiling": 500_000,
                "est_hours": 2500,
                "labor_rates": [{"lcat": "Senior Engineer", "loaded_rate": 150}],
            }
        ],
        "periods": [],
    }
    if target is not None:
        blob["utilization_target"] = target
    if lcat_hours is not None:
        blob["lcat_expected_hours"] = lcat_hours
    return blob


def _rows(hours=32, employee_id="e1", employee="Aisha Khan"):
    return [
        {
            "charge_code": "0001",
            "labor_category": "Senior Engineer",
            "total_hours": hours,
            "week_ending": f"2026-01-{2 + 7 * w:02d}",
            "employee": employee,
            "employee_id": employee_id,
        }
        for w in range(6)
    ]


def test_the_matrix_gets_its_expectation_from_the_server_not_from_a_forty():
    # The whole reason the resolver is server-side: the grid, the portfolio endpoint
    # and the People view have to read one number resolved one way.
    alloc = allocation.compute_allocation(_contract(target=0.8), _rows(hours=32))
    e = alloc["employees"][0]
    assert e["expected"]["hours"] == 32.0
    assert e["expected"]["level"] == capacity.CONTRACT
    # 32 billed against a 32-hour expectation. Under the old maths: 80%.
    assert e["utilization"] == 1.0
    # And the contract's own settings ride along, so the matrix can show the target it
    # is measuring against without a second fetch.
    assert alloc["contract"]["utilization_target"] == 0.8
    assert alloc["contract"]["expected_hours"]["hours"] == 32.0


def test_a_persons_override_beats_the_contract_in_the_matrix():
    alloc = allocation.compute_allocation(
        _contract(target=0.8),
        _rows(hours=24),
        expected_hours_by_person={"e1": "24"},
    )
    e = alloc["employees"][0]
    assert (e["expected"]["level"], e["expected"]["hours"]) == (capacity.PERSON, 24.0)
    assert e["utilization"] == 1.0


def test_an_unconfigured_contract_behaves_exactly_as_before_but_says_so():
    # No behaviour change for anyone who has set nothing — that is deliberate. The
    # difference is that the number now admits it is an assumption.
    alloc = allocation.compute_allocation(_contract(), _rows(hours=40))
    e = alloc["employees"][0]
    assert e["expected"]["hours"] == 40.0
    assert e["expected"]["assumed"] is True
    assert e["utilization"] == 1.0


def test_allocation_still_cannot_read_the_directory():
    # #69's invariant, re-pinned because #84 gave allocation a reason to want people
    # data. It takes the overrides as an argument instead.
    assert not hasattr(allocation, "people")


def test_expected_hours_never_counts_as_a_qualification():
    # It shares the attrs table and the save endpoint, and it is not a credential.
    # Folding it into coverage would drop a fully-recorded person to `partial` over a
    # part-time week, and #66 would find a field it has no floor for.
    assert "expected_hours" not in people.QUAL_FIELDS
    assert "expected_hours" in people.ALLOWED_FIELDS

    attrs = [
        {
            "employee_id": "e1",
            "field": f,
            "value": v,
            "source_note": None,
            "authored_by": "Kaylee",
            "authored_at": "2026-08-05 12:00:00",
        }
        for f, v in (
            ("education", "Bachelor's"),
            ("years_experience", "12"),
            ("clearance", "TS/SCI"),
            ("expected_hours", "32"),
        )
    ]
    d = people.build_directory(
        facts=[
            {
                "contract_id": 1,
                "employee_id": "e1",
                "employee": "Aisha Khan",
                "charge_code": "0001",
                "labor_category": "Senior Engineer",
                "weeks": 6,
                "first_week": "2026-01-02",
                "last_week": "2026-02-06",
            }
        ],
        contracts=[{"id": 1, "piid": "P-1", "contract": {"contractor": "Acme"}}],
        manual_people=[],
        attr_rows=attrs,
    )
    person = d["people"][0]
    assert person["quals_status"] == people.COMPLETE
    assert d["coverage"]["complete"] == 1
    # Split into its own bucket, so nothing downstream reads a week as a credential.
    assert "expected_hours" not in person["quals"]
    assert person["capacity"]["expected_hours"]["value"] == "32"
    assert person["expected"]["level"] == capacity.PERSON


def test_the_directory_resolves_a_week_without_a_burn_pass():
    # Contract-level settings are just blob keys, so the cheap listing can still say
    # where someone's expectation comes from.
    d = people.build_directory(
        facts=[
            {
                "contract_id": 1,
                "employee_id": "e1",
                "employee": "Aisha Khan",
                "charge_code": "0001",
                "labor_category": "Senior Engineer",
                "weeks": 6,
                "first_week": "2026-01-02",
                "last_week": "2026-02-06",
            }
        ],
        contracts=[
            {
                "id": 1,
                "piid": "P-1",
                "contract": {"contractor": "Acme"},
                "lcat_expected_hours": {"Senior Engineer": 36},
            }
        ],
        manual_people=[],
        attr_rows=[],
    )
    row = d["people"][0]["contracts"][0]
    assert (row["expected"]["hours"], row["expected"]["level"]) == (36.0, capacity.LCAT)


def test_overbooking_is_still_a_physical_week_not_an_expectation():
    # Kaylee's call, and the reason it is a separate constant: a 32-hr person booked to
    # 38 across two contracts is over their expectation and not double-booked. Making
    # this fire on expected hours would flood the Portfolio panel with every part-time
    # person in the company. "Over their expected hours" belongs to #83.
    rows = [
        {
            "employee_id": "e1",
            "name": "Aisha Khan",
            "total_hours": 38.0,
            "assignments": [{"contract_id": 1}, {"contract_id": 2}],
            "expected": {"hours": 32.0, "level": capacity.PERSON},
        }
    ]
    assert people.conflicts(rows) == []
    rows[0]["total_hours"] = 44.0
    assert len(people.conflicts(rows)) == 1


def test_portfolio_utilisation_reads_each_persons_own_expectation():
    # Two contracts, one person, an override of 24 — the cross-contract row divides by
    # their week, not by 40 and not by 24 + 24.
    allocs = [
        allocation.compute_allocation(
            {**_contract(), "id": cid},
            _rows(hours=12),
            expected_hours_by_person={"e1": "24"},
        )
        for cid in (1, 2)
    ]
    row = people.utilization(allocs)["people"][0]
    assert row["total_hours"] == 24.0
    assert row["expected"]["hours"] == 24.0
    assert row["expected"]["level"] == capacity.PERSON
    assert row["utilization"] == 1.0
    # Each assignment also carries what that contract expected, so the panel can say
    # why one person's 12 hours is half a week and another's is all of it.
    assert all(a["expected"]["hours"] == 24.0 for a in row["assignments"])
