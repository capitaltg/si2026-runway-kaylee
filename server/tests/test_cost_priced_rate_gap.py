"""#144 — a cost-measured CLIN priced from its own direct rates has no rate gap
in the number it reports.

#139 taught the UI to tell `absent` (a missing continuation sheet) from `unburdened`
(a cost-type award's direct rates, indirects stated apart). Both still said the same
thing about the money: *every category on this CLIN prices at the blended rate*.

That was true before #79 and is false after it. On a cost-reimbursement CLIN
`spent` is `cost`, and cost is resolved down `CostModel.cost_for` — the award's
per-LCAT direct rate, burdened through fringe/overhead/G&A — not down `resolve`.
A CPFF award whose cost buildup #138 already stored therefore prices every hour per
category while the banner told the user it was all blended.

These tests pin the split: the gap is still reported on the card (the allocation
picker genuinely has no rate line to offer), but the Flight Deck banner — the one
that speaks about the figures — fires only when the blended rate actually priced
them. Nothing here burdens a direct rate into a billing rate; that is still #134.
"""

from app import allocation, burn, rates

CPFF = "Cost-Plus-Fixed-Fee (FAR 16.306)"

DIRECT_ONLY = [
    {"lcat": "Business Analyst", "direct_rate": 61.86, "loaded_rate": None},
    {"lcat": "Program Manager (PMP)", "direct_rate": 65.96, "loaded_rate": None},
]


def _clin(rates_lines=DIRECT_ONLY, ceiling=500000, est_hours=2500):
    return {
        "clin": "0001",
        "title": "Professional Services (Labor) (CPFF)",
        "is_labor": True,
        "contract_type": CPFF,
        "ceiling": ceiling,
        "est_hours": est_hours,
        "labor_rates": list(rates_lines),
    }


def _contract(clin=None, contract_type=CPFF):
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-144",
            "contract_type": contract_type,
            "total_ceiling": 1000000,
            "total_obligated": None,
        },
        "clins": [clin or _clin()],
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


def _cost_model():
    """Level 2, built the way `main._cost_model` builds it — from the rows #138
    stores off a CPFF award's own cost buildup. Figures are the ones a real
    Fixtura CPFF award printed."""
    return rates.model_from_rows(
        [
            {"pool": pool, "rate": rate, "base": rates.DEFAULT_BASES[pool]}
            for pool, rate in (
                (rates.FRINGE, 0.272),
                (rates.OVERHEAD, 0.449),
                (rates.GNA, 0.08),
            )
        ],
        [
            {"lcat": "Business Analyst", "employee_id": None, "rate": 61.86},
            {"lcat": "Program Manager (PMP)", "employee_id": None, "rate": 65.96},
        ],
        fiscal_year="2026",
    )


# --------------------------------------------------------------- the new fact


def test_a_cost_priced_clin_does_not_ride_the_blended_rate():
    card = burn.compute(_contract(), _rows(), None, _cost_model())["clins"][0]
    # The gap on the billing side is untouched — nothing to map an LCAT onto.
    assert card["rate_table_missing"] is True
    assert card["rate_table_state"] == "unburdened"
    # …but the measured quantity never saw the blended rate.
    assert card["measured_against"] == "cost"
    assert card["cost_known"] is True
    assert card["blended_priced_spend"] is False


def test_without_a_cost_model_the_spend_is_blended_after_all():
    # Level 1: cost falls back to the billing rate, which IS the blended rate here.
    # The banner is correct in that state and must keep firing.
    card = burn.compute(_contract(), _rows())["clins"][0]
    assert card["cost_known"] is False
    assert card["blended_priced_spend"] is True


def test_a_partly_costed_clin_still_counts_as_blended():
    # One LCAT with no direct rate falls to the negotiated stand-in, so some of the
    # spend really is blended. A CLIN that is 90% category-costed is not clean.
    rows = _rows() + _rows(lcat_name="Systems Engineer", weeks=2)
    card = burn.compute(_contract(), rows, None, _cost_model())["clins"][0]
    assert card["cost_known"] is False
    assert card["blended_priced_spend"] is True


def test_a_present_rate_table_is_never_blended_priced():
    loaded = [{"lcat": "Business Analyst", "loaded_rate": 140.0}]
    card = burn.compute(_contract(_clin(rates_lines=loaded)), _rows())["clins"][0]
    assert card["rate_table_missing"] is False
    assert card["blended_priced_spend"] is False


# ------------------------------------------------------------- the flight deck


def test_the_banner_is_withheld_from_a_cost_priced_clin():
    gaps = burn.compute(_contract(), _rows(), None, _cost_model())["rate_gaps"]
    assert gaps == []


def test_a_missing_schedule_keeps_its_entry_even_when_the_cost_is_known():
    # The split is by remedy. A CLIN whose continuation sheet never landed still has
    # an import to offer — that import is what makes the allocation matrix mappable —
    # so the entry stays and carries the flag instead of being suppressed. Only the
    # sentence about the money changes.
    bare = _clin(rates_lines=[])
    gaps = burn.compute(_contract(bare), _rows(), None, _cost_model())["rate_gaps"]
    assert [g["rate_table_state"] for g in gaps] == ["absent"]
    assert gaps[0]["blended_priced_spend"] is False


def test_the_banner_still_fires_at_level_one():
    gaps = burn.compute(_contract(), _rows())["rate_gaps"]
    assert [g["rate_table_state"] for g in gaps] == ["unburdened"]


# -------------------------------------------------------------- the allocation


def test_the_allocation_card_carries_the_fact():
    card = allocation.compute_allocation(
        _contract(), _rows(), cost_model=_cost_model()
    )["clins"][0]
    assert card["rate_table_missing"] is True
    assert card["blended_priced_spend"] is False
    # Still nothing to pick — the picker's story is unchanged.
    assert card["rate_lines"] == []


# --------------------------------------------------------------- the arithmetic


def test_the_money_is_unchanged_by_this_ticket():
    # #144 changes what is *said* about the spend, never the spend. Both figures are
    # what main already computed; burdening a direct rate into a billing rate is
    # still #134's decision and still not taken.
    model = _cost_model()
    card = burn.compute(_contract(), _rows(), None, model)["clins"][0]
    hours = 6 * 40
    burdened = rates.burden(61.86, model.rate_set).total_cost
    assert card["spent"] == round(hours * burdened, 2)
    assert card["billings"] == round(hours * (500000 / 2500), 2)
