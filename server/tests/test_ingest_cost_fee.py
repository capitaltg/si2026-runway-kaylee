"""Cost and fee on the award face (#78): the footing check, and the rates.

The identity under test is `ceiling == estimated_cost + fee` on a cost-type CLIN
(FAR 16.306). It is the only independent check an extracted cost line offers —
the document prints all three figures, so a disagreement is a misread, not a
judgement call. Two properties matter and both are asserted here:

  1. a mismatch is *reported*, with the numbers, and drops the row's confidence;
  2. a mismatch is never *reconciled* — no field is rewritten to make the sum work.

The second is the one that would rot silently. A helpful reconciliation looks
like a correct extraction from every screen in the app, which is precisely how a
tool loses an accountant's trust: the review screen exists so a human picks which
of the three figures was misread.
"""

from app import confidence
from app.schemas import CLIN, ContractHeader, Extraction, Period


def _ext(**clin_kwargs):
    base = dict(clin=" 0001".strip(), period="Base", title="Labor", is_labor=True)
    return Extraction(
        contract=ContractHeader(piid="TEST-COSTFEE", total_ceiling=10_000_000.0),
        periods=[
            Period(
                name="Base",
                pop_start="2026-01-01",
                pop_end="2026-12-31",
                exercised=True,
            )
        ],
        clins=[CLIN(**base, **clin_kwargs)],
    )


def test_cpff_that_foots_is_silent():
    cl = confidence.apply(
        _ext(ceiling=1_080_000.0, estimated_cost=1_000_000.0, fixed_fee=80_000.0)
    ).clins[0]
    assert cl.confidence_note is None
    assert cl.confidence >= confidence.CLIN_BASELINE


def test_cpff_mismatch_is_flagged_and_not_reconciled():
    ext = confidence.apply(
        # Fee copied into the ceiling as well as its own field — the classic misread.
        _ext(ceiling=1_000_000.0, estimated_cost=1_000_000.0, fixed_fee=80_000.0)
    )
    cl = ext.clins[0]
    assert cl.confidence_note is not None
    assert "1,080,000.00" in cl.confidence_note  # states what it expected
    assert cl.confidence <= confidence.CROSS_FAIL_CAP
    # Nothing rewritten to make the arithmetic work.
    assert (cl.ceiling, cl.estimated_cost, cl.fixed_fee) == (
        1_000_000.0,
        1_000_000.0,
        80_000.0,
    )


def test_award_fee_foots_on_base_plus_pool():
    """A CPAF CLIN's total covers both fee elements. That they are two fields is
    about which one is earned, not about what the line foots to."""
    cl = confidence.apply(
        _ext(
            ceiling=1_100_000.0,
            estimated_cost=1_000_000.0,
            base_fee=30_000.0,
            award_fee_pool=70_000.0,
        )
    ).clins[0]
    assert cl.confidence_note is None


def test_incentive_brackets_are_not_part_of_the_sum():
    """A CPIF line foots to its target fee. Min and max fee are where the fee can
    move to, and adding them in would flag every correctly-read CPIF CLIN."""
    cl = confidence.apply(
        _ext(
            ceiling=1_090_000.0,
            estimated_cost=1_000_000.0,
            target_fee=90_000.0,
            min_fee=40_000.0,
            max_fee=140_000.0,
            share_ratio="80/20",
        )
    ).clins[0]
    assert cl.confidence_note is None


def test_cost_without_fee_is_not_flagged():
    """A cost-no-fee CLIN (FAR 16.302) is real. Flagging it would nag forever."""
    cl = confidence.apply(_ext(ceiling=1_000_000.0, estimated_cost=1_000_000.0)).clins[
        0
    ]
    assert cl.confidence_note is None


def test_fixed_price_clin_is_untouched():
    """No cost line, nothing to check — and no note implying a missing figure."""
    cl = confidence.apply(_ext(ceiling=500_000.0, type="FFP")).clins[0]
    assert cl.confidence_note is None
    assert cl.confidence >= confidence.CLIN_BASELINE


def test_rounding_slack_is_tolerated():
    """A sheet that rounds its own lines to the dollar must not read as a misread."""
    cl = confidence.apply(
        _ext(ceiling=1_080_000.0, estimated_cost=1_000_000.0, fixed_fee=80_003.0)
    ).clins[0]
    assert cl.confidence_note is None


def test_header_fee_totals_are_scored():
    fc = confidence.apply(
        Extraction(
            contract=ContractHeader(
                piid="TEST-COSTFEE",
                total_ceiling=10_800_000.0,
                total_estimated_cost=10_000_000.0,
                total_fee=800_000.0,
            ),
            periods=[],
            clins=[],
        )
    ).contract.field_confidence
    # Printed dollar figures in a labelled block, so they score with the money
    # fields rather than with free text.
    assert fc["total_estimated_cost"] >= 0.9
    assert fc["total_fee"] >= 0.9
