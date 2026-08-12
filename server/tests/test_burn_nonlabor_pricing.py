"""#155 — a non-labor CLIN is not automatically a cost-reimbursable pass-through.

Travel / ODC / materials lines usually are: their logged dollars are reimbursed at
cost, they consume funding and they earn no fee. But a non-labor line the award
*itself* priced fixed is a deliverable — its ceiling is a price, the dollars against
it are ours, and it has no limitation of funds to warn about. Modelling that as
pass-through hid its only real risk (cost eating the price) behind a funding read it
cannot have, and reported its spend as revenue earned.

The guard is `pricing_policy.source`: the CLIN's own type text, never the header's.
A travel line under an FFP header prints no type of its own and stays pass-through.
"""

from app import burn


_PERIOD = {
    "kind": "base",
    "start_date": "2026-01-05",
    "end_date": "2026-12-28",
    "ceiling": 232_000,
}


def _contract(clin_type=None, header_type=None, ceiling=232_000, obligated=None):
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-155",
            "total_ceiling": ceiling,
            "total_obligated": obligated,
            "contract_type": header_type,
        },
        "clins": [
            {
                "clin": "0004",
                "title": "Hardware delivery",
                "is_labor": False,
                "ceiling": ceiling,
                "type": clin_type,
            }
        ],
        "periods": [_PERIOD],
    }


def _expenses(amount):
    return [{"clin": "0004", "amount": amount}]


def _nl(payload):
    return next(c for c in payload["clins"] if c["id"] == "0004")


# ---- _nl_margin_status: the realized margin bands --------------------------


def test_margin_status_nothing_logged_is_tracked():
    assert burn._nl_margin_status(0, 232_000) == "tracked"


def test_margin_status_bands_on_the_price():
    assert burn._nl_margin_status(100_000, 232_000) == "ok"
    # 90% of the price is the same watch fraction fixed-price labor uses, applied to
    # cost already incurred rather than cost projected.
    assert burn._nl_margin_status(210_000, 232_000) == "watch"
    assert burn._nl_margin_status(232_000, 232_000) == "over"
    assert burn._nl_margin_status(300_000, 232_000) == "over"


def test_margin_status_unpriced_line_never_bands():
    # No price to measure against, so no band — not a silent 0% margin.
    assert burn._nl_margin_status(50_000, 0) == "ok"


# ---- the pass-through case, unchanged --------------------------------------


def test_travel_under_a_fixed_price_header_stays_pass_through():
    # The header says FFP; the CLIN says nothing. Travel on an FFP award is normally
    # reimbursed at cost, so inheriting the header here would invent a deliverable.
    p = burn.compute(_contract(header_type="Firm Fixed Price"), [], _expenses(200_000))
    clin = _nl(p)

    assert clin["pricing_policy"]["source"] == "header"
    assert clin["margin_managed"] is False
    assert clin["margin_position"] is None
    assert clin["revenue_known"] is True
    # Funding vocabulary, not margin vocabulary.
    assert clin["status"] == "watch"
    assert clin["status_label"] == "Watch"
    # Reimbursed at cost: the spend is both cost and revenue in the rollup.
    assert p["totals"]["revenue"] == 200_000.0


def test_cost_type_travel_clin_stays_pass_through():
    p = burn.compute(_contract(clin_type="Cost Plus Fixed Fee"), [], _expenses(100_000))
    clin = _nl(p)

    assert clin["margin_managed"] is False
    assert clin["revenue_known"] is True
    assert clin["fee_known"] is True
    # Fully funded cost-type line → Limitation of Cost, the clause a pass-through has
    # and a deliverable does not.
    assert clin["funding_clause"] == "52.232-20"
    assert p["totals"]["revenue"] == 100_000.0


# ---- the fixed-price deliverable ------------------------------------------


def test_clin_priced_fixed_is_a_deliverable_not_a_pass_through():
    p = burn.compute(_contract(clin_type="Firm Fixed Price"), [], _expenses(100_000))
    clin = _nl(p)

    assert clin["pricing_policy"]["source"] == "clin"
    assert clin["margin_managed"] is True
    # Cost against a price, so no funding story: no clause to cite and nothing to
    # exceed. `measured_against` stays cost — a logged dollar is still a cost dollar.
    assert clin["measured_against"] == "cost"
    assert clin["funding_clause"] is None
    assert clin["funds_exceeded"] is False
    assert clin["status"] == "ok"
    assert clin["status_label"] == "On pace"
    # The price is not revenue until it delivers, and nothing is earned before that.
    assert clin["revenue_known"] is False
    assert clin["fee_known"] is False


def test_deliverable_over_its_price_reads_margin_not_funding():
    p = burn.compute(_contract(clin_type="Firm Fixed Price"), [], _expenses(250_000))
    clin = _nl(p)

    assert clin["status"] == "over"
    assert clin["status_label"] == "Margin exceeded"
    # It is out of the funding tripwire list — that banner says when money runs out and
    # which limit does it, and this line has neither.
    assert p["tripwires"] == []
    # It is in the margin list instead, with the position rendered beside labor rows.
    assert [a["code"] for a in p["margin_alerts"]] == ["CLIN 0004"]
    alert = p["margin_alerts"][0]
    assert alert["policy"] == "FFP"
    assert alert["price"] == 232_000.0
    assert alert["cost"] == 250_000.0
    # Realized, not projected: there is no expense pace, so at-completion cost is the
    # cost logged so far.
    assert alert["projected_cost"] == 250_000.0
    assert alert["projected_margin"] == -18_000.0
    assert alert["known"] is True


def test_deliverable_at_risk_of_its_price_is_amber():
    p = burn.compute(_contract(clin_type="Firm Fixed Price"), [], _expenses(215_000))
    clin = _nl(p)

    assert clin["status"] == "watch"
    assert clin["status_label"] == "Margin at risk"
    assert clin["margin_position"]["eroding"] is True
    assert [a["status"] for a in p["margin_alerts"]] == ["watch"]


def test_deliverable_price_rolls_up_as_price_and_says_so():
    # $100k of cost against a $232k firm price. The rollup carries the price so
    # fee == revenue - cost still reconciles, and flags that it is not revenue earned.
    labor_free = burn.compute(
        _contract(clin_type="Firm Fixed Price"), [], _expenses(100_000)
    )
    t = labor_free["totals"]
    assert t["revenue"] == 232_000.0
    assert t["cost"] == 100_000.0
    assert t["fee"] == 132_000.0
    # No labor on this contract, so the contract-level flags keep their conservative
    # default — which is the right answer here for an unrelated reason.
    assert t["revenue_known"] is False
    assert t["fee_known"] is False


# ---- the mixed award: one deliverable vetoes the contract's revenue claim ---


def _mixed(clin_type):
    """A T&M labor line — which recognises revenue every week — beside one non-labor
    line whose type is the variable. This is where the rollup veto is visible: the
    labor line alone would report `revenue_known: True`."""
    c = _contract(clin_type=clin_type, ceiling=232_000)
    c["clins"].insert(
        0,
        {
            "clin": "0001",
            "title": "Professional Services",
            "is_labor": True,
            "ceiling": 200_000,
            "est_hours": 2_000,
            "type": "T&M",
        },
    )
    c["contract"]["total_ceiling"] = 432_000
    return c


def _timesheets():
    return [
        {
            "charge_code": "0001",
            "labor_category": "Software Engineer",
            "total_hours": 40,
            "week_ending": f"2026-01-{2 + 7 * i:02d}",
            "employee_id": "e1",
        }
        for i in range(4)
    ]


def test_pass_through_line_leaves_the_contract_revenue_claim_intact():
    p = burn.compute(_mixed("Cost Plus Fixed Fee"), _timesheets(), _expenses(50_000))
    t = p["totals"]
    assert t["revenue_known"] is True
    # Reimbursed at cost: the logged dollars are both cost and revenue.
    assert t["revenue"] - t["cost"] == t["fee"]


def test_one_fixed_price_deliverable_makes_the_contract_total_part_price():
    p = burn.compute(_mixed("Firm Fixed Price"), _timesheets(), _expenses(50_000))
    t = p["totals"]
    # The T&M line's revenue is real and the deliverable's is a price, so the *total*
    # is neither — the same veto a fixed-price labor line applies (#154).
    assert t["revenue_known"] is False
    assert t["fee_known"] is False
    # Reconciliation survives the veto: it is the flag that moves, never the sums.
    assert round(t["revenue"] - t["cost"], 2) == t["fee"]


def test_portfolio_card_says_margin_not_ceiling_for_a_red_deliverable():
    # The surface a user sees before opening anything. Its label is derived from the
    # red rows' own `margin_managed`, so a non-labor deliverable had to carry the flag
    # for the card to stop naming a funding limit this contract does not have.
    p = burn.portfolio(
        [(_contract(clin_type="Firm Fixed Price"), [], _expenses(250_000))]
    )
    card = p["contracts"][0]
    assert card["status"] == "over"
    assert card["status_label"] == "Margin exceeded"
