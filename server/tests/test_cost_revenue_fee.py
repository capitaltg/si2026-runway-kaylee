"""#79 — cost, revenue and fee per CLIN, branching on the pricing policy.

The engine had one number per CLIN — hours x one rate — and every warning on the
Flight Deck read off it. #76 gave it the contract type and #77 gave it a second
number; this is the ticket that makes it choose between them, which means it is the
first ticket in epic #88 where a figure a user already looked at can legitimately
change.

So these tests are written as a set of promises about *which* figures may move:

  * **T&M does not move, to the cent.** The one type the pre-#79 engine measured
    correctly must be untouched. Guarded here on a Level-2 cost model, where cost and
    billings are genuinely different numbers — the case where a wrong selection would
    actually show up.
  * **cost-type is measured in cost**, because that is what the government
    reimburses, and cost + fee == revenue at every level.
  * **fixed price stops lying.** No funding tripwire, no runway, no hard-stop date —
    those four figures were never right there — and a cost-vs-price margin position
    in their place.
  * **unknown behaves exactly as before** and declares itself, so the legacy read is
    never mistaken for a typed one.
  * **nothing double-counts.** The three quantities reconcile per CLIN, per contract
    and across the portfolio.
"""

from app import burn, rates

_PERIOD = {"name": "Base", "pop_start": "2026-01-01", "pop_end": "2026-12-31"}
_CEILING = 400_000
_RATE = 100.0  # est_hours 4_000 against a 400k ceiling → a clean blended $100/hr


def _pools():
    return rates.RateSet(
        fiscal_year="FY26",
        pools=tuple(
            rates.Pool(name=n, rate=r, base=rates.DEFAULT_BASES[n])
            for n, r in (
                (rates.FRINGE, 0.32),
                (rates.OVERHEAD, 0.45),
                (rates.GNA, 0.12),
            )
        ),
    )


def _model():
    """Level 2: real indirect pools and an LCAT direct rate, so `cost` is derived and
    is NOT equal to billings. Every test that cares which quantity was selected has to
    run here — at Level 1 the two are equal by construction and any selection passes.

    $40 direct burdens to $85.75 (x1.32 fringe, x1.45 OH, x1.12 G&A) against a $100
    billing rate — a profitable line, so cost sits *below* billings and the two are
    never confusable by accident. Note how little headroom there is: $47 direct would
    already burden past $100, which is the whole reason #77 refuses to guess at cost."""
    return rates.CostModel(rate_set=_pools(), lcat_direct={"software engineer": 40.00})


def _contract(clin_type=None, ceiling=_CEILING, obligated=None):
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-79",
            "total_ceiling": ceiling,
            "total_obligated": obligated if obligated is not None else ceiling,
        },
        "clins": [
            {
                "clin": "0001",
                "period": "Base",
                "title": "Professional Services",
                "is_labor": True,
                "ceiling": ceiling,
                "est_hours": 4_000,
                **({"type": clin_type} if clin_type else {}),
            }
        ],
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


def _card(clin_type, model=None, rows=None, **kw):
    p = burn.compute(
        _contract(clin_type, **kw),
        rows if rows is not None else _rows(),
        cost_model=model,
    )
    return p["clins"][0], p


# ------------------------------------------------------ what `spent` is measured in


def test_tm_is_unchanged_to_the_cent():
    # THE regression bar for this ticket, and the reason `measured_against` exists at
    # all: T&M bills hours x the loaded rate against a ceiling price, which is exactly
    # what the engine already did. Asserted at Level 2 so cost is a different number
    # and a mis-selection cannot hide.
    card, p = _card("T&M", _model())
    hours = 8 * 40
    assert card["measured_against"] == "billings"
    assert card["spent"] == round(hours * _RATE, 2)
    assert card["billings"] == card["spent"]
    # Cost is genuinely lower here — that's the point of running at Level 2.
    assert card["cost"] < card["billings"]
    # And every figure derived from `spent` is the billings-based one.
    assert card["remaining"] == round(_CEILING - hours * _RATE, 2)
    assert card["weekly"] == round(40 * _RATE, 2)
    assert card["runway_days"] is not None
    assert card["stop_date"] is not None


def test_a_cost_type_clin_is_measured_in_cost():
    # The government reimburses allowable cost, so cost is what consumes the funding.
    # Billing dollars are cost + fee and overstate the draw against the obligation.
    card, p = _card("CPFF", _model())
    assert card["measured_against"] == "cost"
    assert card["spent"] == card["cost"]
    assert card["spent"] < card["billings"]
    # Runway is still real on a cost type — funding genuinely can run out here.
    assert card["runway_days"] is not None
    assert card["stop_date"] is not None
    assert card["pricing_policy"]["funding_tripwire"] == "meaningful"


def test_an_unknown_policy_reads_exactly_as_before_and_says_so():
    # No type anywhere. The legacy billings-vs-funding read, and a payload that flags
    # itself so no reader mistakes it for a typed one.
    untyped, _ = _card(None, _model())
    tm, _ = _card("T&M", _model())
    assert untyped["pricing_policy"]["known"] is False
    assert untyped["pricing_policy"]["unknown_reason"] == "absent"
    assert untyped["measured_against"] == "billings"
    assert untyped["spent"] == tm["spent"]
    assert untyped["runway_days"] == tm["runway_days"]


def test_the_measured_quantity_also_drives_the_pace_and_the_chart():
    # `remaining / weekly` divides one quantity by another unless the trailing pace is
    # measured the same way `spent` is — and the Flight Deck chart draws its "funds run
    # out" marker at `budget`, so a cost-measured CLIN with a billings curve would show
    # the crossing at the wrong week.
    card, _ = _card("CPFF", _model())
    assert card["weekly"] < 40 * _RATE  # cost pace, not billings pace
    assert card["actuals"][-1]["cum_spent"] == card["spent"]


# ------------------------------------------------------------------ cost + fee == revenue


def test_a_cpff_clin_reports_cost_and_fee_separately_and_they_reconcile():
    card, p = _card("CPFF", _model())
    assert card["cost"] + card["fee_earned"] == card["revenue"]
    # This CLIN prints no fee figures, so #80's engine has nothing to earn against:
    # the fee is zero and declares itself rather than being estimated off the billing
    # spread. See `test_fee_engine.py` for the same card with a fixed fee on it.
    assert card["fee_earned"] == 0.0
    assert card["fee_known"] is False
    assert card["margin_pct"] is None
    assert card["revenue"] == card["cost"]


def test_a_tm_clin_reports_the_spread_over_cost_as_fee():
    card, _ = _card("T&M", _model())
    assert card["revenue"] == card["billings"]
    assert round(card["revenue"] - card["cost"], 2) == card["fee_earned"]
    assert card["fee_known"] is True
    assert card["margin_pct"] > 0


def test_margin_is_withheld_rather_than_estimated_at_level_1():
    # No direct rates: cost falls back to the billing rate, so the fee is a structural
    # zero. `fee_earned` stays a number so the rollups reconcile, but the percentage —
    # the figure a user reads as profitability — is withheld, not fabricated as 0%.
    card, p = _card("T&M")
    assert card["cost"] == card["billings"]
    assert card["cost_known"] is False
    assert card["fee_known"] is False
    assert card["margin_pct"] is None
    assert p["contract"]["cost_model"]["margin_available"] is False


def test_the_three_quantities_reconcile_at_the_contract_level():
    _, p = _card("T&M", _model())
    t = p["totals"]
    assert round(t["revenue"] - t["cost"], 2) == t["fee"]
    assert t["cost"] == sum(c["cost"] for c in p["clins"])
    assert t["revenue"] == sum(c["revenue"] for c in p["clins"])
    assert t["fee"] == round(sum(c["fee_earned"] for c in p["clins"]), 2)


# ------------------------------------------------------------------- the FFP problem


def test_ffp_reports_no_runway_no_tripwire_and_no_hard_stop():
    # The four figures that were always wrong on fixed-price work. Hours do not consume
    # funding when the government owes a firm price, so none of these has a meaning —
    # and the hard-stop date is worse than meaningless, it asserts that charging will be
    # blocked on a day when it will not be.
    card, p = _card("FFP", _model())
    assert card["margin_managed"] is True
    assert card["runway_days"] is None
    assert card["weeks_left"] is None
    assert card["exhaust_week"] is None
    assert card["stop_date"] is None
    assert card["stop_reason"] is None
    assert card["funds_exceeded"] is False
    assert p["tripwires"] == []
    assert p["funding"] == []
    assert p["underburn"] == []
    # No runway anywhere on the contract → no hero tile. The Flight Deck renders the
    # margin card in its place rather than a "—" where the day count used to be.
    assert p["hero"] is None


def test_ffp_reports_a_cost_vs_price_position_instead():
    card, _ = _card("FFP", _model())
    m = card["margin_position"]
    assert card["measured_against"] == "price"
    assert m["price"] == _CEILING
    assert m["cost"] == card["cost"]
    # Projected to PoP end at the current pace, which is what makes it an early
    # warning rather than a retrospective.
    assert m["projected_cost"] > m["cost"]
    assert m["projected_margin"] == round(m["price"] - m["projected_cost"], 2)
    assert m["known"] is True


def test_ffp_goes_red_when_cost_is_projected_past_the_price():
    # Margin erosion is the fixed-price red: cost eating the fee. It is reported on its
    # own list, in its own vocabulary — never as a funding tripwire, because there is no
    # funding constraint to describe.
    heavy = _rows(weeks=8, hours=400)  # burning ~10x, projects far past the price
    card, p = _card("FFP", _model(), rows=heavy)
    assert card["status"] == "over"
    assert card["status_label"] == "Margin exceeded"
    assert p["tripwires"] == []
    assert len(p["margin_alerts"]) == 1
    alert = p["margin_alerts"][0]
    assert alert["policy"] == "FFP"
    assert alert["projected_cost"] > alert["price"]
    # And it still gates all_clear — this is money the company is losing.
    assert p["all_clear"] is False


def test_ffp_never_reports_under_burn():
    # Unspent money on fixed-price work is margin earned, not a delivery signal to
    # chase. The pre-#79 engine put these CLINs on the under-burn list and told teams to
    # spend down money they got to keep.
    light = _rows(weeks=8, hours=1)
    card, p = _card("FFP", _model(), rows=light)
    assert card["status"] == "ok"
    assert p["underburn"] == []


def test_a_fixed_price_clin_is_never_told_its_funding_ran_out():
    # Same shape as the case that produced "Funds exceeded" on FFP before: obligated
    # well under the ceiling, burning hard. On a T&M line that is a real Limitation of
    # Funds problem; on FFP it is not a problem at all.
    heavy = _rows(weeks=8, hours=400)
    tm, tp = _card("T&M", _model(), rows=heavy, obligated=50_000)
    ffp, fp = _card("FFP", _model(), rows=heavy, obligated=50_000)
    assert tm["status"] == "over"
    assert "Funds" in tm["status_label"] or tm["status_label"] == "Over ceiling"
    assert len(tp["tripwires"]) == 1
    assert ffp["funds_exceeded"] is False
    assert ffp["status_label"] == "Margin exceeded"
    assert fp["tripwires"] == []


def test_a_non_fixed_price_clin_carries_no_margin_position():
    # Emitting one on every type would invite a funding read and a margin read to be
    # compared as though they were the same shape.
    for t in ("T&M", "CPFF", None):
        card, _ = _card(t, _model())
        assert card["margin_managed"] is False
        assert card["margin_position"] is None
        assert card["measured_against"] in ("billings", "cost")


def test_fpi_is_margin_managed_too():
    # Fixed-price incentive is a fixed-price type despite the share ratio (FAR 16.4) and
    # its policy declares `funding_tripwire: "none"` — so it gets the same treatment.
    # The switch is the policy's own declaration, not a list of type codes.
    card, _ = _card("FPI", _model())
    assert card["pricing_policy"]["family"] == "fixed_price"
    assert card["margin_managed"] is True
    assert card["runway_days"] is None
    assert card["margin_position"] is not None


# -------------------------------------------------------------------- the rollups


def test_portfolio_totals_equal_the_sum_of_their_clins():
    rows = _rows()
    pf = burn.portfolio(
        [
            (_contract("T&M"), rows, []),
            (_contract("CPFF"), rows, []),
            (_contract("FFP"), rows, []),
        ]
    )
    assert pf["count"] == 3
    # An all-fixed-price contract has no runway to report, and the card says None
    # rather than 0 — which would read as "out of money today".
    ffp_card = pf["contracts"][2]
    assert ffp_card["runway_days"] is None


def test_a_mixed_award_keeps_funding_vocabulary_on_its_funded_lines():
    # One award, an FFP deliverable and a T&M surge line, both burning hard. The
    # contract card must describe the funding breach — that's the more urgent of the
    # two and the one with a deadline attached.
    c = _contract("T&M")
    c["clins"].append(
        {
            "clin": "0002",
            "period": "Base",
            "title": "Fixed deliverable",
            "is_labor": True,
            "ceiling": 100_000,
            "est_hours": 1_000,
            "type": "FFP",
        }
    )
    rows = _rows(weeks=8, hours=400) + [
        {**r, "charge_code": "0002"} for r in _rows(weeks=8, hours=400)
    ]
    pf = burn.portfolio([(c, rows, [])])
    card = pf["contracts"][0]
    assert card["status"] == "over"
    assert card["status_label"] in ("Over ceiling", "Funds exceeded", "Funds short")
