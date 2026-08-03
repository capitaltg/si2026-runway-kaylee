"""#21 — per-CLIN obligated funding.

An award that prints an Accounting and Appropriation Data / ACRN block funds each
CLIN by name, so the engine must use those figures instead of splitting the header
total pro-rata by ceiling. The split is the thing being replaced: it blends a real
"labor funded near-full, travel starved" obligation into a uniform fraction, which
is exactly the signal the funded-exhaust read exists to surface.

Awards that print only a header total (and every legacy extraction) must keep the
pro-rata behaviour unchanged.
"""

from app import burn

_PERIOD = {"name": "Base", "pop_start": "2026-01-01", "pop_end": "2026-12-31"}

# $400k labor + $100k travel/ODC. Header obligation of $250k is half the $500k
# period ceiling, so pro-rata would fund every CLIN at 50%: labor $200k, travel
# $50k. The ACRN block below says otherwise — labor $240k, travel $10k.
_LABOR_CEILING = 400_000
_TRAVEL_CEILING = 100_000
_OBLIGATED = 250_000


def _contract(labor_obligated=None, travel_obligated=None, obligated=_OBLIGATED):
    labor = {
        "clin": "0001",
        "period": "Base",
        "title": "Professional Services (Labor)",
        "is_labor": True,
        "ceiling": _LABOR_CEILING,
        "est_hours": 4_000,
    }
    travel = {
        "clin": "0002",
        "period": "Base",
        "title": "Travel & ODC",
        "is_labor": False,
        "ceiling": _TRAVEL_CEILING,
    }
    if labor_obligated is not None:
        labor["obligated"] = labor_obligated
        labor["acrn"] = "AA"
    if travel_obligated is not None:
        travel["obligated"] = travel_obligated
        travel["acrn"] = "AB"
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-21",
            "total_ceiling": _LABOR_CEILING + _TRAVEL_CEILING,
            "total_obligated": obligated,
        },
        "clins": [labor, travel],
        "periods": [_PERIOD],
    }


def _rows(weeks=8, hours=40):
    return [
        {
            "charge_code": "0001",
            "labor_category": "Software Engineer",
            "total_hours": hours,
            "week_ending": f"2026-01-{2 + 7 * i:02d}",
            "employee_id": "e1",
        }
        for i in range(weeks)
    ]


def _clin(payload, num):
    return next(c for c in payload["clins"] if c["id"] == num)


def test_real_per_clin_obligation_beats_the_pro_rata_split():
    p = burn.compute(
        _contract(labor_obligated=240_000, travel_obligated=10_000), _rows()
    )

    # Not the 50/50 blend the header total would have produced ($200k / $50k).
    assert _clin(p, "0001")["funded"] == 240_000.0
    assert _clin(p, "0002")["funded"] == 10_000.0
    # Travel is starved to fund labor, and reads as funding-limited.
    assert _clin(p, "0002")["limited_by"] == "funding"
    assert _clin(p, "0002")["funded_frac"] == 0.1


def test_period_funded_comes_from_the_real_obligation_sum():
    # Fully attributed → the period's funded total is the sum of its CLINs, with
    # no header netting involved.
    p = burn.compute(
        _contract(labor_obligated=240_000, travel_obligated=10_000), _rows()
    )

    assert p["contract"]["period_funded"] == 250_000.0
    assert p["contract"]["incrementally_funded"] is True


def test_fully_obligated_clins_fall_back_to_ceiling_runway():
    # The ACRN block funds both CLINs to their ceilings → nothing is incrementally
    # funded, so the period reports no funded slice and budgets are the ceilings.
    p = burn.compute(
        _contract(
            labor_obligated=_LABOR_CEILING,
            travel_obligated=_TRAVEL_CEILING,
            obligated=_LABOR_CEILING + _TRAVEL_CEILING,
        ),
        _rows(),
    )

    assert p["contract"]["period_funded"] is None
    assert p["contract"]["incrementally_funded"] is False
    assert _clin(p, "0002")["incrementally_funded"] is False
    assert _clin(p, "0002")["budget"] == float(_TRAVEL_CEILING)
    assert _clin(p, "0002")["limited_by"] == "ceiling"


def test_partial_attribution_mixes_real_and_pro_rata():
    # Only labor carries an ACRN figure. It is used as printed; travel keeps the
    # header-derived pro-rata slice (50% of $100k), and the period total still
    # comes from the header netting.
    p = burn.compute(_contract(labor_obligated=240_000), _rows())

    assert _clin(p, "0001")["funded"] == 240_000.0
    assert _clin(p, "0002")["funded"] == 50_000.0
    assert p["contract"]["period_funded"] == float(_OBLIGATED)


def test_award_only_contract_keeps_the_pro_rata_fallback():
    # Regression: no ACRN block anywhere → unchanged #41 behaviour, both CLINs on
    # the blended 50% slice.
    p = burn.compute(_contract(), _rows())

    assert _clin(p, "0001")["funded"] == 200_000.0
    assert _clin(p, "0002")["funded"] == 50_000.0
    assert p["contract"]["period_funded"] == float(_OBLIGATED)


def test_one_nonlabor_clins_ratio_does_not_leak_into_the_next():
    # Two non-labor CLINs, the first fully obligated. Its funded/ceiling ratio of
    # 1.0 must not become the pro-rata fraction applied to the second, which
    # carries no per-CLIN figure and is owed the header-derived 50% slice.
    c = _contract()
    c["clins"].append(
        {
            "clin": "0003",
            "period": "Base",
            "title": "Materials",
            "is_labor": False,
            "ceiling": _TRAVEL_CEILING,
        }
    )
    c["clins"][1]["obligated"] = _TRAVEL_CEILING
    c["clins"][1]["acrn"] = "AB"

    p = burn.compute(c, _rows())

    assert _clin(p, "0002")["funded"] == float(_TRAVEL_CEILING)
    # $250k obligated, $600k period ceiling → 41.67% pro-rata on the unattributed
    # line, not the 100% the previous CLIN was funded at.
    assert _clin(p, "0003")["funded_frac"] == 0.4167
