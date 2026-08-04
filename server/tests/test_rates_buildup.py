"""#77 — cost and price become two numbers, and every tier of it is optional.

A labor hour used to be one number doing three jobs: what we bill, what the work
cost, and the basis for every tripwire. This pins the buildup arithmetic, the
reconciliation against the negotiated rate, and — the product decision this ticket
turns on — that a user who declines to share salaries still gets a working app:

  Level 1  contract documents only  → billing burn, margin withheld (not estimated)
  Level 2  three percentages + LCAT averages → margin, nobody named
  Level 3  per-person direct rates (#69) → true cost-to-complete

The invariant behind all of it: **no figure the engine already reported may move.**
Cost is additive; #79 is what makes the engine choose between cost and billings.
"""

from app import allocation, burn, rates


def _pools(fringe=0.32, overhead=0.45, gna=0.12, status=rates.PROVISIONAL):
    return rates.RateSet(
        fiscal_year="FY26",
        pools=tuple(
            rates.Pool(name=n, rate=r, base=rates.DEFAULT_BASES[n], status=status)
            for n, r in (
                (rates.FRINGE, fringe),
                (rates.OVERHEAD, overhead),
                (rates.GNA, gna),
            )
            if r is not None
        ),
    )


def _rows(lcat="Cyber Analyst", weeks=6, hours=40, emp="e1"):
    return [
        {
            "charge_code": "0001",
            "labor_category": lcat,
            "total_hours": hours,
            "week_ending": f"2026-01-{2 + 7 * w:02d}",
            "employee": "Person One",
            "employee_id": emp,
        }
        for w in range(weeks)
    ]


def _contract(rate=200.0, lcat="Cyber Analyst"):
    return {
        "id": 1,
        "contract": {"piid": "T-1", "total_ceiling": 1000000, "total_obligated": None},
        "clins": [
            {
                "clin": "0001",
                "title": "Labor",
                "is_labor": True,
                "ceiling": 500000,
                "est_hours": 2500,
                "labor_rates": [{"lcat": lcat, "loaded_rate": rate}],
            }
        ],
        "periods": [],
    }


# --------------------------------------------------------------- the buildup


def test_buildup_matches_the_hand_worked_example_to_the_cent():
    # The ticket's own worked example — $62.00 direct, 32 / 45 / 12, and the layers
    # have to land on the stated subtotals, not just the total.
    b = rates.burden(62.00, _pools())
    assert round(b.fringe, 2) == 19.84
    assert round(b.labor_plus_fringe, 2) == 81.84
    assert round(b.overhead, 2) == 36.83
    assert round(b.burdened, 2) == 118.67
    assert round(b.gna, 2) == 14.24
    assert round(b.total_cost, 2) == 132.91
    # And with the policy's 8% fee on top it reconciles to the schedule's $143.54.
    assert round(b.total_cost * 1.08, 2) == 143.54


def test_each_pool_applies_to_its_own_base_not_a_wrap_rate():
    # A flat 89% wrap on direct would give 62 * 1.89 = 117.18. The layered buildup
    # gives 132.91, because OH applies to labor+fringe and G&A to total cost input.
    # This is the difference the ticket exists to model.
    assert round(rates.burden(62.00, _pools()).total_cost, 2) != round(62.00 * 1.89, 2)


def test_a_missing_pool_contributes_zero_rather_than_blocking():
    # Fringe and G&A but no overhead pool is unusual, not wrong — and refusing to
    # compute would hide the two rates the user did give us.
    b = rates.burden(100.0, _pools(overhead=None))
    assert round(b.total_cost, 2) == round(132.0 * 1.12, 2)
    assert b.overhead == 0.0


def test_a_typo_in_a_base_falls_back_to_the_conventional_base():
    # Never silently delete a pool from the cost because a base string was wrong.
    odd = rates.RateSet(pools=(rates.Pool(rates.OVERHEAD, 0.45, base="nonsense"),))
    assert rates.burden(100.0, odd).overhead == 45.0


# ---------------------------------------------------------- the fallback ladder


def test_level_1_falls_back_to_the_billing_rate_and_says_so():
    # No rates provided at all: the app still works, and it does NOT pretend to know
    # cost. This is the state most users will be in on day one.
    m = rates.CostModel()
    cr = m.cost_for("Cyber Analyst", 200.0, "e1")
    assert cr.rate == 200.0
    assert cr.source == rates.SOURCE_NEGOTIATED
    assert cr.known is False
    assert m.level == rates.LEVEL_BILLING_ONLY
    assert m.margin_available is False


def test_level_2_uses_the_lcat_average_with_nobody_named():
    m = rates.CostModel(rate_set=_pools(), lcat_direct={"cyber analyst": 62.00})
    cr = m.cost_for("Cyber Analyst", 200.0, "e1")
    assert round(cr.rate, 2) == 132.91
    assert cr.source == rates.SOURCE_LCAT
    assert cr.known is True
    assert m.level == rates.LEVEL_CATEGORY_COST
    assert m.margin_available is True


def test_level_3_prefers_the_persons_own_rate():
    m = rates.CostModel(
        rate_set=_pools(),
        lcat_direct={"cyber analyst": 62.00},
        employee_direct={"e1": 80.00},
    )
    assert m.cost_for("Cyber Analyst", 200.0, "e1").source == rates.SOURCE_EMPLOYEE
    # Anyone without their own rate still resolves through their category, so a
    # partial payroll feed doesn't blank out the rest of the team.
    assert m.cost_for("Cyber Analyst", 200.0, "e2").source == rates.SOURCE_LCAT
    assert m.level == rates.LEVEL_PERSON_COST


def test_direct_rates_without_indirect_pools_are_not_a_margin_tier():
    # Direct rates alone can't produce a cost meaningfully different from a
    # discounted billing rate, so claiming margin here would oversell the input.
    m = rates.CostModel(lcat_direct={"cyber analyst": 62.00})
    assert m.level == rates.LEVEL_BILLING_ONLY
    assert m.margin_available is False


def test_an_lcat_direct_rate_resolves_through_the_same_normaliser_as_billing():
    # A rate typed as "Sr. Cyber SME" has to answer for a timesheet's "Senior Cyber
    # SME", or the cost side would flag misses the billing side doesn't (#64).
    m = rates.CostModel(rate_set=_pools(), lcat_direct={})
    m.lcat_direct[__import__("app.lcat", fromlist=["x"]).normalize("Sr. Cyber SME")] = (
        90.0
    )
    assert m.cost_for("Senior Cyber SME", 300.0).source == rates.SOURCE_LCAT


def test_nothing_to_price_with_reports_none_not_zero():
    assert rates.CostModel().cost_for("Any", None).source == rates.SOURCE_NONE
    assert rates.CostModel().cost_for("Any", None).rate is None


# ------------------------------------------------------------- reconciliation


def test_variance_reports_both_numbers_and_never_picks():
    # Derived cost 132.91 vs a negotiated 143.54 — the 8% fee, which is #80's job to
    # name. Both figures survive to the payload; neither overrides the other.
    v = rates.variance(132.91, 143.54)
    assert v["derived_cost"] == 132.91
    assert v["negotiated_rate"] == 143.54
    assert round(v["delta"], 2) == 10.63
    assert v["direction"] == "above_buildup"


def test_billing_below_our_own_cost_is_flagged_the_other_way():
    v = rates.variance(200.0, 150.0)
    assert v["direction"] == "below_buildup"
    assert v["delta"] < 0


def test_agreement_to_the_cent_is_not_a_variance():
    assert rates.variance(132.91, 132.91) is None
    assert rates.variance(None, 143.54) is None
    assert rates.variance(132.91, None) is None


# ----------------------------------------------------- fiscal year + provisional


def test_every_rate_carries_a_fiscal_year_and_a_status():
    rs = _pools()
    assert rs.fiscal_year == "FY26"
    assert all(p.status == rates.PROVISIONAL for p in rs.pools)
    assert rs.status == rates.PROVISIONAL
    # One provisional pool makes the whole derived cost provisional — they feed the
    # same total, so the weakest rate governs (#87 trues this up).
    mixed = rates.RateSet(
        pools=(
            rates.Pool(rates.FRINGE, 0.32, status=rates.ACTUAL),
            rates.Pool(rates.GNA, 0.12, status=rates.PROVISIONAL),
        )
    )
    assert mixed.status == rates.PROVISIONAL


def test_an_empty_rate_set_is_provisional_not_settled():
    assert rates.RateSet().status == rates.PROVISIONAL
    assert rates.RateSet().complete is False


# ------------------------------------------------------------- engine integration


def test_the_engine_reports_cost_without_moving_a_single_billing_figure():
    contract, rows = _contract(), _rows()
    before = burn.compute(contract, rows)["clins"][0]

    model = rates.CostModel(rate_set=_pools(), lcat_direct={"cyber analyst": 62.00})
    after = burn.compute(contract, rows, None, model)["clins"][0]

    # Everything the Flight Deck already showed is unchanged to the cent.
    for k in ("spent", "weekly", "remaining", "runway_days", "status", "exhaust_week"):
        assert before[k] == after[k], k
    # And cost is now a separate, smaller number.
    assert after["cost"] == round(6 * 40 * rates.burden(62.00, _pools()).total_cost, 2)
    assert after["cost"] < after["spent"]
    assert after["cost_known"] is True
    assert after["cost_rate_source"] == rates.SOURCE_LCAT


def test_level_1_reports_cost_equal_to_spend_and_flags_it_unknown():
    c = burn.compute(_contract(), _rows())["clins"][0]
    assert c["cost"] == c["spent"]
    assert c["cost_known"] is False
    assert c["cost_rate_source"] == rates.SOURCE_NEGOTIATED


def test_the_contract_payload_declares_the_level_so_the_ui_can_hide_margin():
    p = burn.compute(_contract(), _rows())
    assert p["contract"]["cost_model"]["level"] == rates.LEVEL_BILLING_ONLY
    assert p["contract"]["cost_model"]["margin_available"] is False
    assert p["totals"]["cost_known"] is False

    model = rates.CostModel(rate_set=_pools(), lcat_direct={"cyber analyst": 62.00})
    p2 = burn.compute(_contract(), _rows(), None, model)
    assert p2["contract"]["cost_model"]["margin_available"] is True
    assert p2["contract"]["cost_model"]["rate_set"]["complete"] is True
    assert p2["totals"]["cost"] < p2["totals"]["spent"]


def test_a_mixed_clin_reports_every_tier_that_priced_it():
    # One person costed off their category, one falling through to the billing rate.
    contract = _contract()
    contract["clins"][0]["labor_rates"].append(
        {"lcat": "Program Manager", "loaded_rate": 180.0}
    )
    rows = _rows() + _rows(lcat="Program Manager", emp="e2")
    model = rates.CostModel(rate_set=_pools(), lcat_direct={"cyber analyst": 62.00})
    c = burn.compute(contract, rows, None, model)["clins"][0]

    mix = {m["source"]: m["hours"] for m in c["cost_rate_mix"]}
    assert mix == {rates.SOURCE_LCAT: 240.0, rates.SOURCE_NEGOTIATED: 240.0}
    # One hour on a fallback rate is enough to withhold the margin claim.
    assert c["cost_known"] is False


def test_the_engine_surfaces_the_rate_variance_per_lcat():
    model = rates.CostModel(rate_set=_pools(), lcat_direct={"cyber analyst": 62.00})
    c = burn.compute(_contract(rate=143.54), _rows(), None, model)["clins"][0]
    v = c["rate_variance"][0]
    assert v["lcat"] == "Cyber Analyst"
    assert round(v["delta"], 2) == 10.63


def test_no_variance_is_invented_from_a_fallback_cost():
    # A fallback cost *is* the billing rate, so comparing them would always report
    # zero and mean nothing. Better to report nothing.
    assert burn.compute(_contract(), _rows())["clins"][0]["rate_variance"] == []


def test_allocation_makes_cost_and_price_readable_per_person():
    # The ticket's "cost and price separately readable for any (person, CLIN, week)".
    contract, rows = _contract(), _rows()
    model = rates.CostModel(rate_set=_pools(), lcat_direct={"cyber analyst": 62.00})
    a = allocation.compute_allocation(contract, rows, None, model)
    cell = a["employees"][0]["cells"]["0001"]
    assert cell["rate"] == 200.0
    assert round(cell["cost_rate"], 2) == 132.91
    assert cell["cost_source"] == rates.SOURCE_LCAT
    assert cell["cost_known"] is True


def test_allocation_at_level_1_shows_equal_columns_and_says_why():
    a = allocation.compute_allocation(_contract(), _rows())
    cell = a["employees"][0]["cells"]["0001"]
    assert cell["cost_rate"] == cell["rate"]
    assert cell["cost_known"] is False


# ------------------------------------------------------------------- row parsing


def test_model_from_rows_skips_junk_rather_than_raising():
    # A bad row in a hand-maintained table must not take down a whole contract's burn.
    m = rates.model_from_rows(
        [
            {"pool": "fringe", "rate": 0.32, "fiscal_year": "FY26"},
            {"pool": "not_a_pool", "rate": 0.5},
            {"pool": "gna", "rate": None},
            {"pool": "overhead", "rate": "abc"},
        ],
        [
            {"lcat": "Cyber Analyst", "rate": 62.0},
            {"lcat": "Broken", "rate": None},
            {"employee_id": "e9", "rate": 80.0},
        ],
    )
    assert [p.name for p in m.rate_set.pools] == ["fringe"]
    assert m.rate_set.fiscal_year == "FY26"
    assert m.lcat_direct == {"cyber analyst": 62.0}
    assert m.employee_direct == {"e9": 80.0}
