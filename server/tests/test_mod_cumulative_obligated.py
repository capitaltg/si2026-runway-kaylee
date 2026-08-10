"""The header's obligated total, rebuilt from a mod trail that may be lying to it.

Two figures in the history can answer "how much is obligated": what a mod states as
the running total, and the sum of what every action moved. Each is a *lower bound* on
the truth and each fails differently — a trail missing a mod undercounts the sum, and
a mod that never printed a running total leaves the extraction with nothing to read.

The failure this pins down: an SF-30 that prints only "Obligated this action
$1,873,252.80" and no cumulative gives the model an inviting number to put in the
cumulative field. Taking the largest stated cumulative then silently discards the
option's money, because the award's own $4,836,234.80 is bigger. The contract reads
exactly as it did before the mod was ingested, which is the one outcome nobody can
detect by looking at it.
"""

from app.main import _merge_mod

AWARD_OBLIGATED = 4_836_234.80
OPTION_MONEY = 1_873_252.80


def _contract():
    """An FFP award mid-Option-Year-1, with the base obligation seeded as the first
    history entry the way `_seed_award_obligation` writes it at ingest."""
    return {
        "contract": {
            "total_ceiling": 8_000_000.0,
            "total_obligated": AWARD_OBLIGATED,
        },
        "periods": [
            {"name": "Base Year", "exercised": True, "ceiling": AWARD_OBLIGATED},
            {"name": "Option Year 1", "exercised": False, "ceiling": 2_000_000.0},
        ],
        "clins": [
            {"clin": "0001", "period": "Base Year", "obligated": AWARD_OBLIGATED},
            {"clin": "1001", "period": "Option Year 1", "obligated": None},
        ],
        "obligation_history": [
            {
                "mod": "Award",
                "date": "2024-12-22",
                "action": "Initial award / base-period funding",
                "amount": AWARD_OBLIGATED,
                "cumulative_obligated": AWARD_OBLIGATED,
            }
        ],
    }


def _option_mod(**over):
    mod = {
        "mod_number": "P00001",
        "effective_date": "2025-11-23",
        "action_type": "option_exercise",
        "amount_obligated": OPTION_MONEY,
        "period_exercised": "Option Year 1",
    }
    mod.update(over)
    return mod


def test_a_mod_restating_its_own_amount_as_the_cumulative_does_not_lose_the_money():
    """The live defect. The SF-30 prints no running total, so the extraction filled
    the cumulative with the action's own figure — which is smaller than the award's,
    so the largest-stated-cumulative rule kept the award figure and the option
    exercise obligated nothing at all."""
    c = _contract()
    _merge_mod(c, _option_mod(cumulative_obligated=OPTION_MONEY))

    assert c["contract"]["total_obligated"] == 6_709_487.60


def test_a_mod_that_states_no_cumulative_at_all_still_adds_its_money():
    c = _contract()
    _merge_mod(c, _option_mod())

    assert c["contract"]["total_obligated"] == 6_709_487.60


def test_a_genuine_cumulative_above_the_sum_still_wins():
    """The reason the stated figure is consulted at all: a trail missing a mod sums to
    less than the contract really carries, and the running total on the mod we DO hold
    is the only evidence of the one we don't.

    The trail has to actually omit that mod for this to be the missing-mod case. P00001
    is absent here, and the hole below P00002 in the numbering is what makes the excess
    explicable. A *contiguous* trail stating the same figure is indistinguishable from a
    misread digit, and is rejected — see below."""
    c = _contract()
    _merge_mod(c, _option_mod(mod_number="P00002", cumulative_obligated=7_500_000.0))

    assert c["contract"]["total_obligated"] == 7_500_000.0


def test_a_consistent_trail_is_unchanged():
    """The ordinary case, where both reads agree — and must not be double-counted."""
    c = _contract()
    _merge_mod(c, _option_mod(cumulative_obligated=6_709_487.60))

    assert c["contract"]["total_obligated"] == 6_709_487.60


def test_reingesting_the_same_mod_does_not_double_count():
    """The sum is over merged history, which is keyed by mod number — so ingesting
    P00001 twice is one action, not two."""
    c = _contract()
    _merge_mod(c, _option_mod())
    _merge_mod(c, _option_mod())

    assert c["contract"]["total_obligated"] == 6_709_487.60


def test_the_option_money_reaches_the_incrementally_funded_flag():
    """A contract whose obligation silently stalled also reads as more incrementally
    funded than it is, which is what the funding-pace tripwire watches."""
    c = _contract()
    _merge_mod(c, _option_mod(cumulative_obligated=OPTION_MONEY))

    assert c["contract"]["incrementally_funded"] is True
    assert c["contract"]["total_obligated"] == 6_709_487.60


def test_a_cumulative_above_the_ceiling_is_a_misread_not_an_over_obligation():
    """The read that motivated the ceiling gate: a narrative stating "cumulative
    obligated $6,709,487.60" extracted as $16,709,487.80. Obligating past the
    ceiling is an Anti-Deficiency Act problem, not a routine funding action, so the
    arithmetic every other figure on the document agrees with wins."""
    c = _contract()
    c["contract"]["total_ceiling"] = 14_535_792.80
    summary = _merge_mod(c, _option_mod(cumulative_obligated=16_709_487.80))

    assert c["contract"]["total_obligated"] == 6_709_487.60
    assert summary["cumulative_ignored"] == [16_709_487.80]


def test_a_cumulative_at_the_ceiling_exactly_is_still_trusted():
    """The ceiling bound is inclusive: obligating a contract to exactly its ceiling is
    a fully-funded contract, not an over-obligation. Needs the gapped trail for the
    same reason as above — a figure this far above the sum is only admissible as
    evidence of the mod that is missing."""
    c = _contract()
    c["contract"]["total_ceiling"] = 14_535_792.80
    summary = _merge_mod(
        c, _option_mod(mod_number="P00002", cumulative_obligated=14_535_792.80)
    )

    assert c["contract"]["total_obligated"] == 14_535_792.80
    assert summary["cumulative_ignored"] == []


def test_a_well_read_trail_reports_nothing_ignored():
    c = _contract()
    c["contract"]["total_ceiling"] = 14_535_792.80
    summary = _merge_mod(c, _option_mod(cumulative_obligated=6_709_487.60))

    assert summary["cumulative_ignored"] == []


# --- The band between the sum and the ceiling ---------------------------------
#
# The ceiling gate alone leaves a window: on this contract, anything a misread lands
# between $6,709,487.60 and $14,535,792.80 clears the ceiling check and beats the
# arithmetic. Three live attempts at one figure returned $16,709,487.80, $5,709,487.80
# and $1,873,252.80, so a fourth landing in the band is not a hypothetical.


def test_an_in_band_misread_on_a_contiguous_trail_is_discarded():
    """One bad leading digit, comfortably inside the ceiling. The trail is contiguous —
    Award then P00001, nothing absent — so there is no missing mod whose money could
    account for the excess, and the arithmetic every other figure agrees with wins."""
    c = _contract()
    c["contract"]["total_ceiling"] = 14_535_792.80
    summary = _merge_mod(c, _option_mod(cumulative_obligated=9_709_487.60))

    assert c["contract"]["total_obligated"] == 6_709_487.60
    assert summary["cumulative_ignored"] == [9_709_487.60]


def test_an_inflated_total_would_have_read_as_better_funded():
    """Why the band matters rather than being a cosmetic wrong number: obligated is the
    numerator the funding tripwires watch, so a figure that is too high makes the
    contract look better funded than it is and quietens the warning."""
    c = _contract()
    c["contract"]["total_ceiling"] = 14_535_792.80
    _merge_mod(c, _option_mod(cumulative_obligated=9_709_487.60))

    assert c["contract"]["total_obligated"] == 6_709_487.60
    assert c["contract"]["incrementally_funded"] is True


def test_no_known_ceiling_means_no_override_at_all():
    """With no ceiling the upper bound cannot be checked, and the guard that depends on
    it silently passes everything. An override that cannot be validated is not one
    worth taking, so the arithmetic holds and the figure is reported."""
    c = _contract()
    c["contract"]["total_ceiling"] = None
    summary = _merge_mod(c, _option_mod(cumulative_obligated=16_709_487.80))

    assert c["contract"]["total_obligated"] == 6_709_487.60
    assert summary["cumulative_ignored"] == [16_709_487.80]


def test_a_hole_after_the_stating_mod_does_not_excuse_its_excess():
    """P00003 is absent, but it comes *after* P00001 — a later action cannot be the
    source of money P00001 already claims to have counted."""
    c = _contract()
    c["contract"]["total_ceiling"] = 14_535_792.80
    _merge_mod(c, _option_mod(mod_number="P00002", cumulative_obligated=None))
    summary = _merge_mod(
        c,
        _option_mod(
            mod_number="P00001",
            effective_date="2025-06-01",
            cumulative_obligated=9_709_487.60,
        ),
    )

    assert summary["cumulative_ignored"] == [9_709_487.60]


def test_a_hole_in_another_mod_series_does_not_excuse_an_excess():
    """Administrative and procurement mods number independently, so a gap in the A
    series says nothing about whether a P mod is missing. A00001/A00002 present with
    P00001 contiguous leaves the excess unexplained."""
    c = _contract()
    c["contract"]["total_ceiling"] = 14_535_792.80
    for num in ("A00001", "A00002"):
        _merge_mod(
            c,
            {
                "mod_number": num,
                "effective_date": "2025-03-01",
                "action_type": "administrative",
                "amount_obligated": None,
            },
        )
    summary = _merge_mod(c, _option_mod(cumulative_obligated=9_709_487.60))

    assert c["contract"]["total_obligated"] == 6_709_487.60
    assert summary["cumulative_ignored"] == [9_709_487.60]


def test_a_contract_onboarded_mid_performance_keeps_its_award_money():
    """The arithmetic is only safe if it starts where the money did. A contract whose
    award obligation sits in the header with an empty trail must not have its base
    period dropped when the first mod is folded in — the award is materialised as the
    trail's first row so there is something to sum."""
    c = _contract()
    c["obligation_history"] = []
    _merge_mod(c, _option_mod())

    assert c["contract"]["total_obligated"] == 6_709_487.60
    assert c["obligation_history"][0]["mod"] == "Award"
    assert c["obligation_history"][0]["amount"] == AWARD_OBLIGATED
