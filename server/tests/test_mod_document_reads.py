"""What an SF-30 says, read from the SF-30.

The extractor is asked for a mod's per-CLIN funding split directly, and
test_mod_clin_funding.py covers what happens once it supplies one. This file
covers the reads that don't go through the model: the funding split recovered
from the document's own text when the extraction comes back with `funding_lines`
null, and the per-CLIN not-to-exceed ceilings a mod restates, which the schema
never carried at all.

Both exist because the model's retelling of a document is not the document. The
same SF-30 echoed its CLIN sentence on one extraction run and returned null on
the next — quietly moving $950,000 off a CLIN between two ingests of one file —
and on another it transcribed a $5.6M revised ceiling as $5.8M.
"""

from app.main import (
    _funding_lines_for,
    _merge_mod,
    _parse_ceiling_lines,
    _parse_funding_lines,
    _seed_award_obligation,
)

_NARRATIVE = (
    "The purpose of this modification is to obligate incremental funding. "
    "Accordingly: (a) Total funds obligated on this contract are increased by "
    "$1,175,000.00, from $1,750,000.00 to $2,925,000.00. (b) The total contract "
    "ceiling remains $4,677,562.40. (d) Funds are obligated by CLIN as follows: "
    "CLIN 0001 (ACRN AA) $1,050,000.00, CLIN 0002 (ACRN AB) $90,000.00, "
    "CLIN 0003 (ACRN AC) $35,000.00."
)

_CEILING_MOD = (
    "The purpose of this modification is to increase the contract ceiling and "
    "obligate additional funding. Accordingly: (a) Total funds obligated on this "
    "contract are increased by $1,200,000.00, from $800,000.00 to $2,000,000.00. "
    "(b) The total contract ceiling is increased to $5,700,000.00. (c) All other "
    "terms remain unchanged. (d) Funds are obligated by CLIN as follows: "
    "CLIN 0001 (ACRN AA) $1,200,000.00. (e) Not-to-exceed ceilings are revised by "
    "CLIN as follows: CLIN 0001 $5,500,000.00."
)


def _award():
    """An ingested SF-26: $800k on the labor CLIN, nothing on the ODC line."""
    data = {
        "contract": {
            "piid": "7026HEXDVC0001043",
            "total_ceiling": 4_677_562.40,
            "total_obligated": 800_000.0,
            "effective_date": "2026-01-22",
        },
        "clins": [
            {"clin": "0001", "ceiling": 4_314_562.40, "obligated": 800_000.0},
            {"clin": "0002", "ceiling": 202_000.0, "obligated": None},
        ],
    }
    _seed_award_obligation(data)
    return data


# ── The funding split, when the model didn't supply one ───────────────────────


def test_parses_the_clin_split_out_of_the_narrative():
    lines = _parse_funding_lines(_NARRATIVE)
    assert lines == [
        {"clin": "0001", "amount": 1_050_000.0, "acrn": "AA"},
        {"clin": "0002", "amount": 90_000.0, "acrn": "AB"},
        {"clin": "0003", "amount": 35_000.0, "acrn": "AC"},
    ]
    # The header dollars in the same sentence are not CLIN-tagged, so they must
    # not be mistaken for a funding line.
    assert all(line["amount"] < 1_100_000 for line in lines)


def test_the_split_is_read_from_the_document_when_the_model_omits_it():
    """`funding_lines` is the extraction schema's only nested object list, which
    is where constrained decoding is least reliable. When it comes back null the
    dollars still have to land somewhere."""
    contract = _award()
    _merge_mod(
        contract,
        {
            "mod_number": "P00002",
            "effective_date": "2026-06-01",
            "amount_obligated": 1_175_000.0,
            "cumulative_obligated": 2_925_000.0,
            "funding_lines": None,
            "description": None,
            "document_text": "STANDARD FORM 30\n" + _NARRATIVE,
        },
    )
    by_num = {c["clin"]: c for c in contract["clins"]}
    assert by_num["0001"]["obligated"] == 1_850_000.0  # $800k award + $1.05M mod
    assert by_num["0002"]["obligated"] == 90_000.0


def test_the_document_split_wins_over_a_conflicting_model_split():
    """The PDF/AcroForm is the source of truth when both reads are present."""
    contract = _award()
    _merge_mod(
        contract,
        {
            "mod_number": "P00002",
            "effective_date": "2026-06-01",
            "amount_obligated": 1_175_000.0,
            "cumulative_obligated": 2_925_000.0,
            "funding_lines": [{"clin": "0001", "amount": 1_175_000.0}],
            "document_text": _NARRATIVE,
        },
    )
    by_num = {c["clin"]: c for c in contract["clins"]}
    assert by_num["0001"]["obligated"] == 1_850_000.0
    assert by_num["0002"]["obligated"] == 90_000.0


def test_a_model_split_is_used_only_when_it_reconciles():
    """A structured model split still helps text-only ingests, but cannot invent
    or misattribute more dollars than the mod says it obligated."""
    contract = _award()
    _merge_mod(
        contract,
        {
            "mod_number": "P00002",
            "effective_date": "2026-06-01",
            "amount_obligated": 1_175_000.0,
            "cumulative_obligated": 2_925_000.0,
            "funding_lines": [{"clin": "0001", "amount": 1_000_000.0}],
        },
    )
    by_num = {c["clin"]: c for c in contract["clins"]}
    assert by_num["0001"]["obligated"] == 800_000.0
    assert by_num["0002"]["obligated"] is None


def test_a_split_that_does_not_add_up_is_thrown_away():
    """A document restates CLIN figures in its accounting block and its schedule.
    Sweeping up a second mention would double that line's funding, so a scraped
    split is only trusted when it reconciles to the dollars the mod obligated."""
    doubled = _NARRATIVE + " Accounting: CLIN 0001 (ACRN AA) $1,050,000.00."
    lines = _funding_lines_for(
        {"amount_obligated": 1_175_000.0, "description": None}, doubled
    )
    assert lines == []


def test_a_split_stands_when_the_mod_states_no_total():
    """Nothing to reconcile against is not the same as failing to reconcile."""
    lines = _funding_lines_for({"amount_obligated": None}, _NARRATIVE)
    assert [line["clin"] for line in lines] == ["0001", "0002", "0003"]


def test_the_document_text_beats_the_models_description():
    """On the SF-30 that prompted this, the model read a $5.6M revised ceiling as
    $5.8M. What the PDF says is a fact; what the model says it says is a
    reading."""
    mod = {
        "amount_obligated": 1_200_000.0,
        "description": "(d) Funds are obligated by CLIN as follows: "
        "CLIN 0001 (ACRN AA) $1,200,000.00.",
    }
    assert _funding_lines_for(mod, _CEILING_MOD) == [
        {"clin": "0001", "amount": 1_200_000.0, "acrn": "AA"}
    ]


# ── Revised ceilings, which the extraction schema never carried ──────────────


def test_a_ceiling_raising_mod_moves_what_burn_measures_against():
    """Money alone can't fix a line projecting past its ceiling. When a mod lifts
    the not-to-exceed value, the CLIN has to be measured against the new one or
    the contract stays red against a limit the CO already raised."""
    contract = _award()
    summary = _merge_mod(
        contract,
        {
            "mod_number": "P00003",
            "effective_date": "2026-08-06",
            "amount_obligated": 1_200_000.0,
            "cumulative_obligated": 2_000_000.0,
            "total_ceiling": 5_700_000.0,
            "document_text": _CEILING_MOD,
        },
    )
    by_num = {c["clin"]: c for c in contract["clins"]}
    assert by_num["0001"]["ceiling"] == 5_500_000.0
    assert by_num["0001"]["obligated"] == 2_000_000.0  # $800k award + $1.2M mod
    assert by_num["0002"]["ceiling"] == 202_000.0  # unnamed, so unchanged
    assert contract["contract"]["total_ceiling"] == 5_700_000.0
    assert summary["ceilings_revised"] == ["0001"]


def test_the_two_clin_lists_in_one_mod_are_not_blended():
    """The same mod states dollars-by-CLIN and ceilings-by-CLIN. Reading either
    list as the other would fund a line at its ceiling, or cap it at its
    increment."""
    assert _parse_funding_lines(_CEILING_MOD) == [
        {"clin": "0001", "amount": 1_200_000.0, "acrn": "AA"}
    ]
    assert _parse_ceiling_lines(_CEILING_MOD) == [
        {"clin": "0001", "ceiling": 5_500_000.0}
    ]


def test_a_mod_that_states_no_ceiling_clause_leaves_ceilings_alone():
    """A ceiling is a restatement, not an increment: mistaking a funding line for
    one would cap a CLIN at the dollars a single mod put on it."""
    assert _parse_ceiling_lines(_NARRATIVE) == []
    contract = _award()
    _merge_mod(
        contract,
        {
            "mod_number": "P00002",
            "effective_date": "2026-06-01",
            "amount_obligated": 1_175_000.0,
            "document_text": _NARRATIVE,
        },
    )
    assert {c["clin"]: c["ceiling"] for c in contract["clins"]} == {
        "0001": 4_314_562.40,
        "0002": 202_000.0,
    }


def test_a_quoted_stale_ceiling_cannot_shrink_the_contract():
    """Mods restate the total ceiling for cross-check. A later one quoting an
    older figure must not walk the contract backwards."""
    contract = _award()
    contract["contract"]["total_ceiling"] = 5_700_000.0
    _merge_mod(
        contract,
        {
            "mod_number": "P00004",
            "effective_date": "2026-09-01",
            "amount_obligated": 100_000.0,
            "cumulative_obligated": 2_100_000.0,
            "total_ceiling": 4_677_562.40,
            "document_text": "(e) Not-to-exceed ceilings are revised by CLIN as "
            "follows: CLIN 0002 $250,000.00.",
        },
    )
    assert contract["contract"]["total_ceiling"] == 5_700_000.0
    assert {c["clin"]: c["ceiling"] for c in contract["clins"]}["0002"] == 250_000.0


def test_a_period_ceiling_keeps_step_with_its_clins():
    """A period carries its own ceiling. Leaving it stale would have the period
    bar and the CLIN row disagree about the same raise."""
    contract = _award()
    for c in contract["clins"]:
        c["period"] = "Base Year"
    contract["periods"] = [{"name": "Base Year", "ceiling": 4_516_562.40}]
    _merge_mod(
        contract,
        {
            "mod_number": "P00003",
            "effective_date": "2026-08-06",
            "amount_obligated": 1_200_000.0,
            "total_ceiling": 5_700_000.0,
            "document_text": _CEILING_MOD,
        },
    )
    assert contract["periods"][0]["ceiling"] == 5_702_000.0  # $5.5M + $202k
