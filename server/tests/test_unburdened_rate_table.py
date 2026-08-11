"""#139 — a CLIN priced with direct rates is not a CLIN missing its rate schedule.

`lcat.resolver` bills from `loaded_rate` and skips any line without one. A cost-type
award prints unburdened direct rates per LCAT with the indirect factors stated
separately, so every one of its lines is skipped and the CLIN falls to the blended
rate — the same end state as an SF-26 face ingested without its continuation sheet.

The two are not the same problem. One is a missing document; the other is a
burdening decision (#134) on a document already in the database. These tests pin the
distinction the UI phrases itself off, so it can stop telling a user to import a
schedule they already gave us.

What a direct-rate line *bills at* is deliberately not settled here — the CLIN still
prices at blended, exactly as before.
"""

from app import allocation, burn, lcat


def _clin(num="0001", rates=None, ceiling=500000, est_hours=2500):
    return {
        "clin": num,
        "title": "Labor",
        "is_labor": True,
        "ceiling": ceiling,
        "est_hours": est_hours,
        "labor_rates": rates if rates is not None else [],
    }


def _contract(clins):
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-1",
            "total_ceiling": 1000000,
            "total_obligated": None,
        },
        "clins": clins if isinstance(clins, list) else [clins],
        "periods": [],
    }


def _rows(lcat_name="Business Analyst", weeks=6, hours=40):
    return [
        {
            "charge_code": "0001",
            "labor_category": lcat_name,
            "total_hours": hours,
            "week_ending": f"2026-01-{2 + 7 * w:02d}",
            "employee": "Person 0",
            "employee_id": "e0",
        }
        for w in range(weeks)
    ]


DIRECT_ONLY = [
    {"lcat": "Business Analyst", "direct_rate": 61.86, "loaded_rate": None},
    {"lcat": "Program Manager (PMP)", "direct_rate": 65.96, "loaded_rate": None},
]


# ------------------------------------------------------------------ the state


def test_three_states_are_distinguished():
    assert lcat.rate_table_state(_clin(rates=[])) == lcat.TABLE_ABSENT
    assert lcat.rate_table_state(_clin(rates=DIRECT_ONLY)) == lcat.TABLE_UNBURDENED
    assert (
        lcat.rate_table_state(
            _clin(rates=[{"lcat": "Business Analyst", "loaded_rate": 140.0}])
        )
        == lcat.TABLE_PRESENT
    )


def test_a_mixed_table_counts_as_present():
    # One burdened line is enough to bill from, and the rest are cause B/C for the
    # per-LCAT mapping path — not a CLIN-level statement.
    mixed = DIRECT_ONLY + [{"lcat": "Admin Support", "loaded_rate": 55.0}]
    assert lcat.rate_table_state(_clin(rates=mixed)) == lcat.TABLE_PRESENT


def test_a_nameless_line_is_not_a_rate_table():
    # An extraction that produced empty rows must still read as "no schedule here",
    # or the UI withholds the import that would actually fix it.
    junk = [{"lcat": "  ", "direct_rate": 61.86}, {"lcat": None}]
    assert lcat.rate_table_state(_clin(rates=junk)) == lcat.TABLE_ABSENT


# ------------------------------------------------------------------- the cards


def test_burn_reports_the_gap_without_calling_the_document_missing():
    card = burn.compute(_contract(_clin(rates=DIRECT_ONLY)), _rows())["clins"][0]
    # Still a rate gap: nothing here prices per-LCAT.
    assert card["rate_table_missing"] is True
    # …but not the missing-document kind.
    assert card["rate_table_state"] == lcat.TABLE_UNBURDENED


def test_an_empty_table_still_reads_as_absent():
    card = burn.compute(_contract(_clin(rates=[])), _rows())["clins"][0]
    assert card["rate_table_missing"] is True
    assert card["rate_table_state"] == lcat.TABLE_ABSENT


def test_the_flight_deck_rate_gap_carries_the_state():
    gaps = burn.compute(_contract(_clin(rates=DIRECT_ONLY)), _rows())["rate_gaps"]
    assert [g["rate_table_state"] for g in gaps] == [lcat.TABLE_UNBURDENED]


def test_the_allocation_card_carries_the_state():
    card = allocation.compute_allocation(_contract(_clin(rates=DIRECT_ONLY)), _rows())[
        "clins"
    ][0]
    assert card["rate_table_state"] == lcat.TABLE_UNBURDENED
    # The direct lines are not offered as pickable rates — there is no rate to pick.
    assert card["rate_lines"] == []


# ------------------------------------------------------------------- the cause


def test_the_per_lcat_cause_splits_too():
    resolve, blended, source = lcat.resolver(_clin(rates=DIRECT_ONLY))
    res = resolve("Business Analyst")
    assert res.matched is False
    assert res.cause == lcat.RATE_TABLE_UNBURDENED
    # And the arithmetic is untouched: still the CLIN's blended rate, which is real.
    assert res.rate == blended == 500000 / 2500
    assert source == "blended"

    absent = lcat.resolver(_clin(rates=[]))[0]("Business Analyst")
    assert absent.cause == lcat.RATE_TABLE_MISSING


def test_the_spend_is_unchanged_by_this_ticket():
    # (b) is a pricing decision left to #134 — the money must not move here.
    priced = burn.compute(_contract(_clin(rates=DIRECT_ONLY)), _rows())["clins"][0]
    bare = burn.compute(_contract(_clin(rates=[])), _rows())["clins"][0]
    assert priced["spent"] == bare["spent"] == 6 * 40 * (500000 / 2500)
