"""Award-extraction normalization.

The one rule with teeth: a CLIN the Accounting and Appropriation Data block
*names* but shows no money against is obligated 0.0, not null. `burn.py` reads
null as "the award is silent about this line" and falls back to spreading the
header total pro-rata; it reads 0.0 as a real, reportable funding state. A single
dropped zero therefore changes how every other CLIN in the period is funded, so
the coercion is deterministic here rather than left to the model.
"""

from app import extract
from app.schemas import CLIN, ContractHeader, Extraction, Period


def _clin(num, acrn=None, obligated=None, ceiling=100_000, is_labor=False):
    return CLIN(
        clin=num,
        period="Base",
        title=f"CLIN {num}",
        is_labor=is_labor,
        ceiling=ceiling,
        obligated=obligated,
        acrn=acrn,
    )


def _extraction(*clins):
    return Extraction(
        contract=ContractHeader(piid="TEST-EXTRACT"),
        periods=[
            Period(
                name="Base",
                pop_start="2026-01-01",
                pop_end="2026-12-31",
                exercised=True,
            )
        ],
        clins=list(clins),
    )


def test_named_acrn_with_no_dollars_becomes_zero():
    e = extract.normalize_obligations(_extraction(_clin("0002", acrn="AB")))

    assert e.clins[0].obligated == 0.0
    assert e.clins[0].obligated is not None


def test_clin_absent_from_the_accounting_block_stays_null():
    # No ACRN → the block never mentioned this line, and null is the honest
    # answer. Coercing it to 0.0 would assert an unfunded line the award does
    # not claim, and would hand the whole period to the by-name path on data
    # that isn't there.
    e = extract.normalize_obligations(_extraction(_clin("0003")))

    assert e.clins[0].obligated is None


def test_a_real_obligation_is_left_alone():
    e = extract.normalize_obligations(
        _extraction(_clin("0001", acrn="AA", obligated=800_000.0))
    )

    assert e.clins[0].obligated == 800_000.0


def test_an_explicit_zero_is_left_alone():
    # Already 0.0 from the model — must not be re-read as missing and must not
    # move. Guards against a truthiness check creeping in here.
    e = extract.normalize_obligations(
        _extraction(_clin("0002", acrn="AB", obligated=0.0))
    )

    assert e.clins[0].obligated == 0.0


def test_mixed_block_normalizes_only_the_named_zeros():
    # The case that unlocks burn.py's by-name path: labor funded, travel and ODC
    # named at $0. All three CLINs end up attributed, so the engine uses the
    # award's own figures instead of a pro-rata blend.
    e = extract.normalize_obligations(
        _extraction(
            _clin("0001", acrn="AA", obligated=800_000.0, is_labor=True),
            _clin("0002", acrn="AB"),
            _clin("0003", acrn="AC"),
        )
    )

    assert [c.obligated for c in e.clins] == [800_000.0, 0.0, 0.0]
    assert all(c.obligated is not None for c in e.clins)
