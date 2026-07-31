"""#40 — a CLIN with charged rows but no usable rate must read as a data-quality
gap (`unpriced`), not `paused`/`All clear`. The distinction that matters:
"we found no spend" vs "we could not price the spend we found."
"""

from app import burn


def _rows(charge_code="0001", lcat="Software Engineer", weeks=6, hours=40):
    # One row per week, so the CLIN clearly has charges to (fail to) price.
    return [
        {
            "charge_code": charge_code,
            "labor_category": lcat,
            "total_hours": hours,
            "week_ending": f"2026-01-{2 + 7 * i:02d}",
            "employee_id": "e1",
        }
        for i in range(weeks)
    ]


def _contract(clin):
    # No periods → single-period fallback; the clock runs off the timesheet feed.
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-1",
            "total_ceiling": 500000,
            "total_obligated": None,
        },
        "clins": [clin],
        "periods": [],
    }


# A labor CLIN with neither a rate table nor est_hours: nothing can price its rows.
_UNPRICEABLE = {
    "clin": "0001",
    "title": "Professional Services (Labor)",
    "is_labor": True,
    "ceiling": 500000,
    "est_hours": None,
    "labor_rates": [],
}


def test_unpriced_clin_is_flagged_and_not_all_clear():
    p = burn.compute(_contract(dict(_UNPRICEABLE)), _rows())

    clin = p["clins"][0]
    assert clin["status"] == "unpriced"
    assert clin["spent"] == 0.0
    assert clin["charged_rows"] == 6
    assert clin["unmatched_lcats"] == ["Software Engineer"]

    assert p["all_clear"] is False
    assert len(p["data_quality"]) == 1
    dq = p["data_quality"][0]
    assert dq["code"] == "CLIN 0001"
    assert dq["charged_rows"] == 6
    assert dq["unmatched_lcats"] == ["Software Engineer"]

    # The hero must not imply a runway it doesn't have.
    assert p["hero"]["status"] == "unpriced"
    assert p["hero"]["days"] is None


def test_no_rows_is_paused_not_unpriced():
    # Same unpriceable CLIN, but nothing charged to it → genuinely paused.
    p = burn.compute(_contract(dict(_UNPRICEABLE)), [])
    assert p["clins"][0]["status"] == "paused"
    assert p["data_quality"] == []


def test_blended_rate_prices_rows_so_not_unpriced():
    # est_hours gives a blended rate, so the rows can be valued — not a data gap.
    priced = dict(_UNPRICEABLE, est_hours=5000)
    p = burn.compute(_contract(priced), _rows())
    clin = p["clins"][0]
    assert clin["status"] != "unpriced"
    assert clin["spent"] > 0
    assert p["data_quality"] == []


def test_portfolio_badges_the_unpriced_contract():
    contract = _contract(dict(_UNPRICEABLE))
    pf = burn.portfolio([(contract, _rows(), [])])
    card = pf["contracts"][0]
    assert card["status"] == "unpriced"
    assert card["data_quality"] == 1
    assert pf["at_risk"] == 1
