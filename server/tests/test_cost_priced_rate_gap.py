"""#144 — a cost-type award's own cost buildup prices its labor.

A CPFF award prints an unburdened direct rate per labor category and its indirect
factors separately, because on a cost-type line the government reimburses allowable
cost — there is no hourly price to print. `lcat.resolver` bills from `loaded_rate`,
so every one of those lines was skipped and every hour fell to `ceiling / est_hours`
— while `cost` beside it resolved the very same categories correctly through
`CostModel`. One award, two ladders, two answers, and the user told that four
categories they could see priced on the page were "affected".

`burden_fn` closes it: with the contract's indirect pools in hand, a direct-rate
line prices at that category's direct rate carried through them. Every input is on
the document, so this derives a rate rather than assuming one.

Two things it is deliberately not. It is not fee — `spent` on a cost-type CLIN is
cost (#79) and #80 reports earned fee beside it, so a fee share in the hourly rate
would count it twice and pre-empt #134. And it is not a fixed-price rate: on FFP or
T&M the printed loaded rate IS the price, and those types never reach `burden_fn`.

`blended_priced_spend` is the other half — whether the blended fallback priced any
of what a card reports — which is what the Flight Deck's rate banner now speaks off
instead of the billing table's state alone.
"""

from app import allocation, burn, lcat, rates

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
        # `pricing.policy_for` reads a CLIN's own type off `type`, header off
        # `contract_type` — a mixed award is normal and the CLIN wins.
        "type": CPFF,
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


def test_a_cpff_award_prices_its_own_labor():
    card = burn.compute(_contract(), _rows(), None, _cost_model())["clins"][0]
    # There is no gap left: the direct-rate lines are billable lines now.
    assert card["rate_table_missing"] is False
    assert card["rate_table_state"] == "present"
    assert card["rate_source"] == "rate_table"
    assert card["unmatched_lcats"] == []
    assert card["blended_priced_spend"] is False


def test_the_rate_is_the_buildup_the_award_prints():
    model = _cost_model()
    resolve, blended, _ = lcat.resolver(_clin(), burden=burn.burden_fn(model))
    res = resolve("Business Analyst")
    assert res.matched is True
    # $61.86 x 1.272 x 1.449 x 1.08 — fringe, then overhead, then G&A.
    assert res.rate == rates.burden(61.86, model.rate_set).total_cost
    assert round(res.rate, 2) == 123.14
    # And it says so: a derived rate is never reported as one the award printed.
    assert res.via == lcat.VIA_BURDENED
    assert res.line.basis == lcat.BASIS_BURDENED
    assert res.line.direct == 61.86
    # The blended rate it replaced is still there, still real, just not used.
    assert blended == 500000 / 2500


def test_fee_is_not_in_the_rate():
    # The award prints a 6.8% fixed fee. It belongs to #80's engine, reported beside
    # cost — folding it in here would count it twice and pre-empt #134.
    model = _cost_model()
    resolve, _, _ = lcat.resolver(_clin(), burden=burn.burden_fn(model))
    assert (
        resolve("Business Analyst").rate
        == rates.burden(61.86, model.rate_set).total_cost
    )


def test_a_fixed_price_clin_never_burdens():
    # On FFP the printed rate is the price. Substituting our cost for a price the
    # award states would report what we spend as what we may invoice.
    ffp = _clin()
    ffp["type"] = "Firm-Fixed-Price (FAR 16.202)"
    card = burn.compute(
        _contract(ffp, contract_type="Firm-Fixed-Price (FAR 16.202)"),
        _rows(),
        None,
        _cost_model(),
    )["clins"][0]
    assert card["rate_table_state"] == "unburdened"
    assert card["rate_source"] == "blended"


def test_no_indirect_pools_means_no_burdening():
    # A direct rate alone is not a billable rate, and guessing a burden would invent
    # the number this exists to avoid inventing.
    bare = rates.model_from_rows([], [{"lcat": "Business Analyst", "rate": 61.86}])
    assert burn.burden_fn(bare) is None
    card = burn.compute(_contract(), _rows(), None, bare)["clins"][0]
    assert card["rate_table_state"] == "unburdened"
    # Nothing bills per category, so `billings` is blended…
    assert card["billings"] == round(6 * 40 * (500000 / 2500), 2)
    # …but this CLIN is measured on cost, and cost still resolves per category off
    # the unburdened direct rate (a pre-existing Level-1 read, not #144's doing). So
    # the blended rate priced nothing that is reported, and the flag says exactly
    # that rather than repeating the billing table's answer.
    assert card["blended_priced_spend"] is False


def test_without_a_cost_model_the_spend_is_blended_after_all():
    # Level 1: cost falls back to the billing rate, which IS the blended rate here.
    # The banner is correct in that state and must keep firing.
    card = burn.compute(_contract(), _rows())["clins"][0]
    assert card["cost_known"] is False
    assert card["blended_priced_spend"] is True


def test_a_partly_priced_clin_still_counts_as_blended():
    # A category the award never priced still falls to blended, and one category's
    # worth of fallback is enough — the flag is read off hours, so it cannot be
    # talked out of a real gap by the other three categories resolving.
    rows = _rows() + _rows(lcat_name="Systems Engineer", weeks=2)
    card = burn.compute(_contract(), rows, None, _cost_model())["clins"][0]
    assert card["unmatched_lcats"] == ["Systems Engineer"]
    assert card["blended_priced_spend"] is True


def test_a_printed_rate_always_wins():
    # A stated price is never overridden by a derivation, whatever the type.
    loaded = [{"lcat": "Business Analyst", "loaded_rate": 140.0, "direct_rate": 61.86}]
    card = burn.compute(
        _contract(_clin(rates_lines=loaded)), _rows(), None, _cost_model()
    )["clins"][0]
    assert card["billings"] == round(6 * 40 * 140.0, 2)
    assert card["blended_priced_spend"] is False


# ------------------------------------------------------------- the flight deck


def test_the_banner_is_gone_once_the_award_prices_itself():
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


def test_the_matrix_agrees_with_the_flight_deck():
    # The two used to disagree by construction: the deck measured cost per category
    # and the matrix billed everyone at blended.
    alloc = allocation.compute_allocation(
        _contract(), _rows(), cost_model=_cost_model()
    )["clins"][0]
    assert alloc["rate_table_missing"] is False
    assert alloc["blended_priced_spend"] is False
    # And the picker has something to offer at last.
    assert [ln["lcat"] for ln in alloc["rate_lines"]] == [
        "Business Analyst",
        "Program Manager (PMP)",
    ]


# --------------------------------------------------------------- the arithmetic


def test_the_measured_spend_does_not_move():
    # The cost side already resolved these categories correctly, so `spent` on a
    # cost-type CLIN is what it always was. What changes is `billings`, which now
    # agrees with it instead of reporting the blended rate.
    model = _cost_model()
    card = burn.compute(_contract(), _rows(), None, model)["clins"][0]
    burdened = rates.burden(61.86, model.rate_set).total_cost
    assert card["spent"] == round(6 * 40 * burdened, 2)
    assert card["billings"] == card["spent"]


def test_a_contract_with_no_cost_model_is_untouched():
    # Every pre-#144 contract: no pools, no burdening, bit-for-bit the old answers.
    card = burn.compute(_contract(), _rows())["clins"][0]
    assert card["rate_table_state"] == "unburdened"
    assert card["spent"] == 6 * 40 * (500000 / 2500)
