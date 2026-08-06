"""#39 — the two denominators, reported side by side.

`pct` has always been `spent / ceiling` while `remaining`, `weeks_left`,
`exhaust_week` and the status band are all measured against `budget` — the funded
slice on an incrementally funded CLIN. A card printing only `pct` next to a runway
therefore showed two numbers a reader could not reconcile: contract 6's CLIN 0001
rendered "40% burned" beside a red "Funding due" pill and 89 days of runway.

The engine's arithmetic was never wrong; the payload just never exposed the second
read. These tests pin both denominators, at CLIN, contract and portfolio level, and
pin the invariant that makes the distinction invisible on a fully funded line:
`pct_budget == pct` whenever budget == ceiling.
"""

from app import burn

_PERIOD = {"name": "Base", "pop_start": "2026-01-01", "pop_end": "2026-12-31"}

# One labor CLIN, $1M ceiling, funded to $500k. 2,000 hours at the blended
# $1M / 4,000hr = $250/hr rate is $500k of spend — half the ceiling, all of the
# funded slice, which is the sharpest possible version of the mismatch.
_CEILING = 1_000_000
_FUNDED = 500_000


def _contract(obligated=_FUNDED):
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-39",
            "total_ceiling": _CEILING,
            "total_obligated": obligated,
        },
        "clins": [
            {
                "clin": "0001",
                "period": "Base",
                "title": "Professional Services (Labor)",
                "is_labor": True,
                "ceiling": _CEILING,
                "est_hours": 4_000,
            }
        ],
        "periods": [_PERIOD],
    }


def _rows(weeks=50, hours=40):
    return [
        {
            "charge_code": "0001",
            "labor_category": "Software Engineer",
            "total_hours": hours,
            "week_ending": "2026-01-02",
            "employee_id": "e1",
        }
        for _ in range(weeks)
    ]


def _only_clin(payload):
    return payload["clins"][0]


def test_clin_reports_both_denominators():
    c = _only_clin(burn.compute(_contract(), _rows()))

    assert c["incrementally_funded"] is True
    assert c["spent"] == 500_000.0
    # Half the ceiling, all of the funded money — the same dollars, two reads.
    assert c["pct"] == 0.5
    assert c["pct_budget"] == 1.0


def test_funded_marker_position_is_on_the_payload():
    # `funded_frac` is where the UI draws the "funds run out" tick along the
    # ceiling track. Without it the bar can only draw `pct` and the gap between
    # burned and funded is invisible — which is the bug.
    c = _only_clin(burn.compute(_contract(), _rows()))

    assert c["funded_frac"] == 0.5
    assert c["funded"] == 500_000.0
    assert c["ceiling"] == 1_000_000.0


def test_fully_funded_clin_collapses_the_two_reads():
    # budget == ceiling → the distinction does not exist and the card must not
    # invent one. A reader who never funds incrementally sees no change at all.
    c = _only_clin(burn.compute(_contract(obligated=_CEILING), _rows()))

    assert c["incrementally_funded"] is False
    assert c["pct_budget"] == c["pct"]
    assert c["funded_frac"] == 1.0


def test_nonlabor_clin_carries_the_same_three_keys():
    # Non-labor cards render through the same component, so they need the same
    # keys or the bar silently falls back to the ceiling-only read (#41).
    contract = _contract()
    contract["clins"].append(
        {
            "clin": "0002",
            "period": "Base",
            "title": "Travel & ODC",
            "is_labor": False,
            "ceiling": 100_000,
        }
    )
    contract["contract"]["total_ceiling"] = _CEILING + 100_000

    travel = next(
        c for c in burn.compute(contract, _rows())["clins"] if c["id"] == "0002"
    )

    assert "pct_budget" in travel
    assert "funded_frac" in travel
    assert 0.0 <= travel["funded_frac"] <= 1.0


def test_totals_roll_the_binding_budget_up():
    # The hero tile's runway is funded-measured, so "Contract burned" needs the
    # funded denominator beside the ceiling one. Summed per CLIN so it reconciles
    # line by line with the cards rather than via a separate header figure.
    p = burn.compute(_contract(), _rows())
    t = p["totals"]

    assert t["incrementally_funded"] is True
    assert t["budget"] == 500_000.0
    assert t["ceiling"] == 1_000_000.0
    assert t["pct"] == 0.5
    assert t["pct_budget"] == 1.0
    assert t["budget"] == sum(c["budget"] for c in p["clins"])


def test_portfolio_card_inherits_both_reads():
    # The portfolio card shows the same Runway-beside-Burned pairing, so it has
    # the same defect and takes the same fix.
    card = burn.portfolio([(_contract(), _rows(), [])])["contracts"][0]

    assert card["incrementally_funded"] is True
    assert card["pct"] == 0.5
    assert card["pct_budget"] == 1.0
    assert card["budget"] == 500_000.0
