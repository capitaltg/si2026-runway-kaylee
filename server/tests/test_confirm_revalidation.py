"""Confirm rescores what the user saved, not what the model read (#160).

A confidence score is a claim about specific figures. The review screen exists so
those figures change, which means every score computed at extraction is stale the
moment a user edits the field under it. Two failures follow from carrying them
over, and both are asserted here:

  1. a correction that FIXED a problem stays flagged — the warning outlives the
     misread it described, and the user learns their edits don't take;
  2. an edit that INTRODUCED one is stored clean — the more expensive direction,
     because nothing downstream ever asks again.

The third property is idempotence. Scoring twice must not ratchet: the second
pass reads scores this module wrote on the first, and treating those as the
model's own doubt would walk every field down a little further on every save.
"""

from app import confidence
from app.schemas import CLIN, ContractHeader, Extraction, Period


def _ext(*, type_="CPFF", **clin_kwargs):
    base = dict(clin="0001", period="Base", title="Labor", is_labor=True)
    return Extraction(
        contract=ContractHeader(
            piid="TEST-REVALIDATE",
            contract_type=type_,
            total_ceiling=10_000_000.0,
        ),
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


def test_correction_clears_the_stale_note():
    # Extraction misread the ceiling, so the line was flagged for not footing.
    ext = confidence.apply(
        _ext(ceiling=1_000_000.0, estimated_cost=1_000_000.0, fixed_fee=80_000.0)
    )
    assert ext.clins[0].confidence_note is not None
    flagged = ext.clins[0].confidence

    # The user fixes the ceiling on the review screen and saves.
    ext.clins[0].ceiling = 1_080_000.0
    confidence.apply(ext, source="confirmed")

    assert ext.clins[0].confidence_note is None
    assert ext.clins[0].confidence > flagged
    assert ext.clins[0].confidence >= confidence.CLIN_BASELINE


def test_edit_that_breaks_the_footing_is_flagged_on_save():
    ext = confidence.apply(
        _ext(ceiling=1_080_000.0, estimated_cost=1_000_000.0, fixed_fee=80_000.0)
    )
    assert ext.clins[0].confidence_note is None

    # A typo in the cost field on the way past — a digit dropped, not a decision.
    ext.clins[0].estimated_cost = 100_000.0
    confidence.apply(ext, source="confirmed")

    assert ext.clins[0].confidence_note is not None
    assert ext.clins[0].confidence <= confidence.CROSS_FAIL_CAP


def test_rescoring_a_clean_extraction_is_stable():
    ext = confidence.apply(
        _ext(ceiling=1_080_000.0, estimated_cost=1_000_000.0, fixed_fee=80_000.0)
    )
    first_fc = dict(ext.contract.field_confidence)
    first_clin = ext.clins[0].confidence

    confidence.apply(ext, source="confirmed")
    confidence.apply(ext, source="confirmed")

    assert ext.contract.field_confidence == first_fc
    assert ext.clins[0].confidence == first_clin


def test_model_doubt_is_not_reapplied_as_the_servers_own():
    # The model hedged on a field it read correctly. That hedge belongs to the
    # extraction; once a human has confirmed the value, it is no longer evidence.
    ext = _ext(ceiling=1_080_000.0, estimated_cost=1_000_000.0, fixed_fee=80_000.0)
    ext.contract.agency = "GSA"
    ext.contract.field_confidence = {"agency": 0.40}
    confidence.apply(ext)
    assert ext.contract.field_confidence["agency"] == 0.40

    confidence.apply(ext, source="confirmed")
    assert ext.contract.field_confidence["agency"] == confidence._BASELINE["agency"]


def test_unsupported_contract_type_cannot_score_high():
    ext = confidence.apply(_ext(type_="Cost Plus Vibes", ceiling=1_000_000.0))
    assert ext.contract.field_confidence["contract_type"] <= confidence.FAIL_CAP


def test_supported_contract_type_keeps_its_baseline():
    ext = confidence.apply(_ext(type_="Cost-Plus-Fixed-Fee", ceiling=1_000_000.0))
    assert (
        ext.contract.field_confidence["contract_type"]
        == confidence._BASELINE["contract_type"]
    )


def test_ordering_vehicle_is_a_reading_not_a_misread():
    # An IDIQ header is what the award says. It isn't a pricing arrangement, which
    # is the pricing layer's problem to handle — not a failed extraction.
    ext = confidence.apply(_ext(type_="IDIQ", ceiling=1_000_000.0))
    assert (
        ext.contract.field_confidence["contract_type"]
        == confidence._BASELINE["contract_type"]
    )
