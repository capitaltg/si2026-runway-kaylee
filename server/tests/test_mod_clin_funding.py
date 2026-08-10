"""Mid-contract onboarding: what an SF-30 trail restores that the award can't say.

An award form is signed once. A contract already performing in Option Year 1
ingests from its SF-26 with the option un-exercised and its CLINs unfunded —
correct for that document, and wrong about today. These cover the mod ingest
closing that gap: per-CLIN obligations rebuilt from the mod trail, and the option
period flipped into effect. See #60.
"""

from app import extract
from app.main import _merge_mod
from app.schemas import CLIN, ContractHeader, Extraction, Period


def _contract():
    """A two-period T&M award as its own SF-26 prints it: the base year funded,
    Option Year 1 priced but neither funded nor (per the award) exercised."""
    return {
        "contract": {"total_ceiling": 6_000_000.0, "total_obligated": 1_550_000.0},
        "periods": [
            {
                "name": "Base Year",
                "pop_start": "2025-03-01",
                "pop_end": "2026-02-28",
                "exercised": True,
                "ceiling": 3_000_000.0,
            },
            {
                "name": "Option Year 1",
                "pop_start": "2026-03-01",
                "pop_end": "2027-02-28",
                "exercised": False,
                "ceiling": 3_000_000.0,
            },
        ],
        "clins": [
            {
                "clin": "0001",
                "period": "Base Year",
                "ceiling": 2_600_000.0,
                "obligated": 1_400_000.0,
                "acrn": "AA",
            },
            {
                "clin": "0002",
                "period": "Base Year",
                "ceiling": 400_000.0,
                "obligated": 150_000.0,
                "acrn": "AA",
            },
            {
                "clin": "1001",
                "period": "Option Year 1",
                "ceiling": 2_600_000.0,
                "obligated": None,
                "acrn": None,
            },
            {
                "clin": "1002",
                "period": "Option Year 1",
                "ceiling": 400_000.0,
                "obligated": None,
                "acrn": None,
            },
        ],
        "obligation_history": [],
    }


def _exercise_mod(num="P00002", amount=1_800_000.0):
    return {
        "mod_number": num,
        "effective_date": "2026-02-20",
        "action_type": "option_exercise",
        "amount_obligated": amount,
        "cumulative_obligated": 1_550_000.0 + amount,
        "period_exercised": "Option Year 1",
        "funding_lines": [
            {"clin": "1001", "acrn": "AB", "amount": amount - 200_000.0},
            {"clin": "1002", "acrn": "AB", "amount": 200_000.0},
        ],
    }


def _by_clin(contract):
    return {c["clin"]: c for c in contract["clins"]}


def test_w45983_base_then_option_exercise_has_documented_cumulative_funding():
    award = Extraction(
        contract=ContractHeader(
            piid="W45983-24-C-1675",
            contract_type="Firm-Fixed-Price",
            total_ceiling=5_960_218.40,
            total_obligated=0.0,
        ),
        periods=[
            Period(
                name="Base",
                pop_start="2024-09-24",
                pop_end="2025-09-23",
                exercised=True,
                ceiling=3_037_736.80,
            ),
            Period(
                name="Option 1",
                pop_start="2025-09-24",
                pop_end="2026-09-23",
                exercised=True,
                ceiling=2_922_481.60,
            ),
        ],
        clins=[
            CLIN(
                clin="0001",
                period="Base",
                title="Base labor",
                type="FFP",
                is_labor=True,
                ceiling=2_866_736.80,
            ),
            CLIN(
                clin="0002",
                period="Base",
                title="Base travel",
                type="COST",
                is_labor=False,
                ceiling=171_000.00,
            ),
            CLIN(
                clin="1001",
                period="Option 1",
                title="Option 1 labor",
                type="FFP",
                is_labor=True,
                ceiling=2_751_481.60,
            ),
            CLIN(
                clin="1002",
                period="Option 1",
                title="Option 1 travel",
                type="COST",
                is_labor=False,
                ceiling=171_000.00,
            ),
        ],
    )
    contract = extract.normalize_initial_award(award).model_dump()
    contract["obligation_history"] = []

    assert contract["contract"]["total_obligated"] == 3_037_736.80
    assert contract["periods"][1]["exercised"] is False
    assert [c["obligated"] for c in contract["clins"][2:]] == [None, None]

    _merge_mod(
        contract,
        {
            "mod_number": "P00001",
            "effective_date": "2025-09-24",
            "action_type": "option_exercise",
            "amount_obligated": 2_922_481.60,
            "cumulative_obligated": 5_960_218.40,
            "period_exercised": "Option 1",
            "funding_lines": [
                {"clin": "1001", "acrn": "AB", "amount": 2_751_481.60},
                {"clin": "1002", "acrn": "AB", "amount": 171_000.00},
            ],
        },
    )

    assert contract["contract"]["total_obligated"] == 5_960_218.40
    assert contract["periods"][1]["exercised"] is True
    assert [c["obligated"] for c in contract["clins"][2:]] == [
        2_751_481.60,
        171_000.00,
    ]


def test_option_exercise_funds_its_clins_and_opens_the_period():
    c = _contract()
    summary = _merge_mod(c, _exercise_mod())

    clins = _by_clin(c)
    assert clins["1001"]["obligated"] == 1_600_000.0
    assert clins["1001"]["acrn"] == "AB"
    assert clins["1002"]["obligated"] == 200_000.0
    assert summary["clins_funded"] == 2

    # The whole point: the burn clock can now find the period it is actually in.
    assert c["periods"][1]["exercised"] is True
    assert summary["periods_exercised"] == ["Option Year 1"]


def test_base_year_clins_are_left_alone():
    """A mod funds the CLINs it names. Nothing else moves — and 0001 must not
    collect money cited against 1001 (same line item, different year)."""
    c = _contract()
    _merge_mod(c, _exercise_mod())

    clins = _by_clin(c)
    assert clins["0001"]["obligated"] == 1_400_000.0
    assert clins["0001"]["acrn"] == "AA"
    assert clins["0002"]["obligated"] == 150_000.0
    assert c["periods"][0]["exercised"] is True


def test_incremental_funding_adds_to_the_award_figure():
    """A later increment on an already-funded CLIN stacks on what the award
    obligated, rather than replacing it."""
    c = _contract()
    _merge_mod(
        c,
        {
            "mod_number": "P00001",
            "effective_date": "2025-09-01",
            "action_type": "incremental_funding",
            "amount_obligated": 900_000.0,
            "cumulative_obligated": 2_450_000.0,
            "funding_lines": [{"clin": "0001", "acrn": "AA", "amount": 900_000.0}],
        },
    )
    assert _by_clin(c)["0001"]["obligated"] == 2_300_000.0
    # An incremental-funding mod exercises nothing.
    assert c["periods"][1]["exercised"] is False


def test_reingesting_the_same_mod_does_not_double_fund():
    """The endpoint is replace-by-mod-number, so re-uploading a doc is routine.
    Per-CLIN funding is recomputed from the award baseline each time, not
    incremented in place."""
    c = _contract()
    _merge_mod(c, _exercise_mod())
    first = _by_clin(c)["1001"]["obligated"]
    summary = _merge_mod(c, _exercise_mod())

    assert summary["replaced"] is True
    assert summary["history_len"] == 1
    assert _by_clin(c)["1001"]["obligated"] == first


def test_a_corrected_mod_can_lower_a_clin_back_down():
    c = _contract()
    _merge_mod(c, _exercise_mod())
    _merge_mod(c, _exercise_mod(amount=1_000_000.0))
    assert _by_clin(c)["1001"]["obligated"] == 800_000.0


def test_a_period_is_never_flipped_back_off():
    """Mods only add. An option, once exercised, stays exercised — ingesting an
    unrelated administrative mod afterwards must not retract it."""
    c = _contract()
    _merge_mod(c, _exercise_mod())
    _merge_mod(
        c,
        {
            "mod_number": "P00003",
            "effective_date": "2026-04-01",
            "action_type": "administrative",
            "description": "Change of COR.",
        },
    )
    assert c["periods"][1]["exercised"] is True


def test_funded_clins_alone_open_the_period_when_the_prose_is_silent():
    """`period_exercised` comes out of a narrative and can be missed. Money
    landing on 1001 is an Option Year 1 obligation regardless."""
    c = _contract()
    mod = _exercise_mod()
    mod["period_exercised"] = None
    _merge_mod(c, mod)
    assert c["periods"][1]["exercised"] is True


def test_a_period_named_differently_by_the_mod_still_matches():
    c = _contract()
    mod = _exercise_mod()
    mod["period_exercised"] = "Option 1"
    mod["funding_lines"] = None
    _merge_mod(c, mod)
    assert c["periods"][1]["exercised"] is True


def test_a_total_only_mod_leaves_per_clin_funding_untouched():
    """Older docs and terser forms state one contract-level figure. That still
    updates total_obligated; it must not be smeared across CLINs, and it must not
    null out what the award said."""
    c = _contract()
    summary = _merge_mod(
        c,
        {
            "mod_number": "P00001",
            "effective_date": "2025-09-01",
            "action_type": "incremental_funding",
            "amount_obligated": 900_000.0,
            "cumulative_obligated": 2_450_000.0,
        },
    )
    assert summary["clins_funded"] == 0
    assert c["contract"]["total_obligated"] == 2_450_000.0
    clins = _by_clin(c)
    assert clins["0001"]["obligated"] == 1_400_000.0
    assert clins["1001"]["obligated"] is None
    # No baseline was snapshotted, because no CLIN figure was ever overwritten.
    assert "funded_at_award" not in clins["0001"]


def test_fully_funded_award_needs_no_mod_read():
    """The other case #60 names: a fixed-price award that obligates each CLIN's
    ceiling at signature. Per-CLIN obligated equals ceiling straight out of the
    award, so there is nothing for the mod path to add."""
    c = {
        "contract": {"total_ceiling": 900_000.0, "total_obligated": 900_000.0},
        "periods": [{"name": "Base Year", "exercised": True, "ceiling": 900_000.0}],
        "clins": [
            {
                "clin": "0001",
                "period": "Base Year",
                "ceiling": 700_000.0,
                "obligated": 700_000.0,
                "acrn": "AA",
            },
            {
                "clin": "0002",
                "period": "Base Year",
                "ceiling": 200_000.0,
                "obligated": 200_000.0,
                "acrn": "AA",
            },
        ],
        "obligation_history": [],
    }
    for clin in c["clins"]:
        assert clin["obligated"] == clin["ceiling"]
    assert c["contract"]["total_obligated"] == c["contract"]["total_ceiling"]


# A CLIN funded from two appropriations at once (#61). Real awards do this
# routinely when one line item spans two colours of money, and the mod's breakout
# then carries two rows for the same CLIN.


def test_two_acrns_on_one_clin_sum_and_keep_both_citations():
    c = _contract()
    _merge_mod(
        c,
        {
            "mod_number": "P00003",
            "effective_date": "2026-02-20",
            "action_type": "option_exercise",
            "amount_obligated": 1_800_000.0,
            "cumulative_obligated": 3_350_000.0,
            "period_exercised": "Option Year 1",
            "funding_lines": [
                {"clin": "1001", "acrn": "AB", "amount": 1_000_000.0},
                {"clin": "1001", "acrn": "AC", "amount": 600_000.0},
                {"clin": "1002", "acrn": "AC", "amount": 200_000.0},
            ],
        },
    )
    line = _by_clin(c)["1001"]
    # One CLIN, not two, and its funding is the sum of both rows — reporting only
    # one row's dollars would understate the line's real limit.
    assert line["obligated"] == 1_600_000.0
    assert line["acrn"] == "AB, AC"


def test_a_new_acrn_joins_the_awards_citation_rather_than_replacing_it():
    """The award funded 0001 under AA; a later mod adds AD money to the same line.
    Both appropriations are now behind that CLIN's `obligated`, so both are named."""
    c = _contract()
    _merge_mod(
        c,
        {
            "mod_number": "P00001",
            "effective_date": "2025-09-01",
            "action_type": "incremental_funding",
            "amount_obligated": 500_000.0,
            "cumulative_obligated": 2_050_000.0,
            "funding_lines": [{"clin": "0001", "acrn": "AD", "amount": 500_000.0}],
        },
    )
    line = _by_clin(c)["0001"]
    assert line["obligated"] == 1_900_000.0
    assert line["acrn"] == "AA, AD"


def test_citations_are_not_duplicated_on_reingest():
    c = _contract()
    mod = {
        "mod_number": "P00004",
        "effective_date": "2026-02-20",
        "action_type": "option_exercise",
        "amount_obligated": 400_000.0,
        "cumulative_obligated": 1_950_000.0,
        "period_exercised": "Option Year 1",
        "funding_lines": [
            {"clin": "1001", "acrn": "AB", "amount": 200_000.0},
            {"clin": "1001", "acrn": "AC", "amount": 200_000.0},
        ],
    }
    _merge_mod(c, mod)
    _merge_mod(c, mod)
    assert _by_clin(c)["1001"]["acrn"] == "AB, AC"
    assert _by_clin(c)["1001"]["obligated"] == 400_000.0
