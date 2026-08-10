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


def _w45983_award(contract_type="Firm-Fixed-Price"):
    return Extraction(
        contract=ContractHeader(
            piid="W45983-24-C-1675",
            contract_type=contract_type,
            total_ceiling=5_960_218.40,
            total_obligated=0.0,
            incrementally_funded=True,
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
                # This is the bad initial-award inference being normalized.
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
                obligated=None,
            ),
            CLIN(
                clin="0002",
                period="Base",
                title="Base travel",
                type="COST",
                is_labor=False,
                ceiling=171_000.00,
                obligated=None,
            ),
            CLIN(
                clin="1001",
                period="Option 1",
                title="Option 1 labor",
                type="FFP",
                is_labor=True,
                ceiling=2_751_481.60,
                obligated=None,
            ),
            CLIN(
                clin="1002",
                period="Option 1",
                title="Option 1 travel",
                type="COST",
                is_labor=False,
                ceiling=171_000.00,
                obligated=None,
            ),
        ],
    )


def test_initial_ffp_award_fully_obligates_only_the_base_period():
    e = extract.normalize_initial_award(_w45983_award())

    assert [c.obligated for c in e.clins] == [
        2_866_736.80,
        171_000.00,
        None,
        None,
    ]
    assert [p.exercised for p in e.periods] == [True, False]
    assert e.contract.total_obligated == 3_037_736.80
    assert e.contract.incrementally_funded is False


def test_initial_ffp_award_preserves_explicit_base_obligations():
    e = _w45983_award()
    e.clins[0].obligated = 2_000_000.0
    e.clins[1].obligated = 0.0

    normalized = extract.normalize_initial_award(e)

    assert [c.obligated for c in normalized.clins[:2]] == [2_000_000.0, 0.0]
    assert normalized.contract.total_obligated == 2_000_000.0
    assert normalized.contract.incrementally_funded is True


def test_initial_award_does_not_default_non_ffp_obligations():
    e = extract.normalize_initial_award(_w45983_award("Cost Plus Fixed Fee"))

    assert [c.obligated for c in e.clins] == [None, None, None, None]
    assert e.contract.total_obligated == 0.0
    assert [p.exercised for p in e.periods] == [True, False]


def test_initial_ffp_award_does_not_invent_total_with_unpriced_base_clin():
    e = _w45983_award()
    e.clins[1].ceiling = None

    normalized = extract.normalize_initial_award(e)

    assert normalized.clins[0].obligated == 2_866_736.80
    assert normalized.clins[1].obligated is None
    assert normalized.contract.total_obligated == 0.0
    assert normalized.contract.incrementally_funded is True


# --- Constrained-decoding wiring ---------------------------------------------
# Not a normalization rule, but it belongs next to one: on Bedrock a schema with
# a nested object list never compiles, so asking for constrained decoding buys a
# guaranteed grammar-compilation timeout before the plain-JSON fallback that
# always answers. It is invisible in behaviour and only shows up as an ingest
# that takes far too long, which is how it shipped once already — `funding_lines`
# gave `Modification` a nested list and left its constrained request in place.


def _record_constrained(monkeypatch):
    seen = {}

    def fake(content, system, output_format, max_tokens, constrained=True):
        seen["constrained"] = constrained
        raise RuntimeError("stop here — only the wiring is under test")

    monkeypatch.setattr(extract, "_parse_schema", fake)
    return seen


def _call(fn, monkeypatch):
    seen = _record_constrained(monkeypatch)
    try:
        fn("some document text")
    except RuntimeError:
        pass
    return seen["constrained"]


def test_bedrock_skips_constrained_decoding_on_both_schemas(monkeypatch):
    monkeypatch.setattr(extract, "PROVIDER", "bedrock")
    assert _call(extract.extract_mod_from_text, monkeypatch) is False
    assert _call(extract.extract_from_text, monkeypatch) is False


def test_the_anthropic_path_still_enforces_its_schema(monkeypatch):
    monkeypatch.setattr(extract, "PROVIDER", "anthropic")
    assert _call(extract.extract_mod_from_text, monkeypatch) is True
    assert _call(extract.extract_from_text, monkeypatch) is True
