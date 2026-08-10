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
    """The reason the stated figure is consulted at all: a trail missing a mod sums
    to less than the contract really carries, and the running total on the mod we DO
    have is the only evidence of the one we don't."""
    c = _contract()
    _merge_mod(c, _option_mod(cumulative_obligated=9_000_000.0))

    assert c["contract"]["total_obligated"] == 9_000_000.0


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
