"""#80 — the earned-fee engine: fixed fee, award fee, incentive share ratio.

Fee is the contractor's entire economic interest in a cost-reimbursement contract,
and until this ticket Runway had no concept of it: #79 left `fee_earned: 0.0` with
`fee_known: false` on every cost-type CLIN because there was no honest number to
put there. These tests pin the number, and more importantly they pin the four rules
that make it honest:

  * **Fee is earned, not accrued at a rate**, and each cost-plus variant earns it by
    a different rule — so there is one branch per type and a hand-worked example for
    each.
  * **A CPFF cost overrun does not increase fee.** The fee is fixed at award; the
    overrun eats it (`contractor_fee_first`) rather than growing it, and the eaten
    fee is reported as exhaustion instead of a negative number.
  * **An undetermined award-fee pool is never earned revenue.** That is the mistake
    that overstates margin for three quarters and corrects violently in the fourth.
  * **Nothing about funding moves.** Fee draws on the same obligated dollars as
    cost, but wiring it into `spent` / `runway_days` / the tripwires is deliberately
    out of scope here (follow-up): every existing figure is pinned unchanged.

The arithmetic tests run against `pricing.earned_fee` directly, because it is a pure
function of (policy, terms, cost) and deserves to be tested as one. The wiring tests
run through `burn.compute` at Level 2, where cost and billings are genuinely
different numbers and a mis-fold shows up.
"""

import os
import sys
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import burn, db, main, pricing, rates  # noqa: E402

# ---------------------------------------------------------------- the pure engine

_EST = 1_000_000.0


def _terms(**kw):
    return pricing.fee_terms({"clin": "0001", **kw})


def _position(code, cost, periods=(), **clin):
    return pricing.earned_fee(
        pricing.POLICIES[code], _terms(**clin), cost, periods=periods
    )


# ------------------------------------------------------------------- share ratios


@pytest.mark.parametrize(
    "text,expected",
    [
        ("80/20", (0.80, 0.20)),
        ("50/50", (0.50, 0.50)),
        ("70 / 30", (0.70, 0.30)),
        ("60-40", (0.60, 0.40)),
        ("75:25", (0.75, 0.25)),
        ("0.8/0.2", (0.80, 0.20)),
    ],
)
def test_share_ratio_parses_the_forms_an_award_prints(text, expected):
    # Government share first, per the extraction prompt. Both percentage and fraction
    # spellings appear on real Section B exhibits.
    assert pricing.parse_share_ratio(text) == expected


@pytest.mark.parametrize("text", ["", None, "80", "80/30", "eighty/twenty", "/", "80/"])
def test_an_unreadable_share_ratio_is_not_guessed(text):
    # A ratio whose halves don't sum to the whole is a misread, not a ratio — and
    # 50/50 is never assumed. `share_unreadable` on the terms is what makes the CPIF
    # position report `known: False` instead of inventing a split.
    assert pricing.parse_share_ratio(text) is None


# --------------------------------------------------------------- CPFF: fixed fee


def test_cpff_fee_is_earned_in_step_with_cost():
    # FAR 16.306 / 52.216-8: the fee is a fixed dollar amount billed proportionally as
    # cost is incurred. 60% through the estimated cost is 60% of the fixed fee.
    pos = _position("CPFF", 600_000.0, estimated_cost=_EST, fixed_fee=80_000.0)

    assert pos.basis == "fixed_fee"
    assert pos.known is True
    assert pos.cost_frac == 0.6
    assert pos.earned == 48_000.0
    assert pos.target == 80_000.0
    assert pos.at_completion == 80_000.0


def test_cpff_fee_is_provably_unchanged_by_a_cost_overrun():
    # THE point of the whole epic: overrun the cost and the fee stays flat while total
    # cost rises, so margin falls. Runway must be able to show that the fee did not
    # move — hence `earned` identical at 100% and 110% of the estimate.
    on_estimate = _position("CPFF", _EST, estimated_cost=_EST, fixed_fee=80_000.0)
    overrun = _position("CPFF", 1_100_000.0, estimated_cost=_EST, fixed_fee=80_000.0)

    assert on_estimate.earned == overrun.earned == 80_000.0
    # The overrun is reported, and it lands on the fee (`contractor_fee_first`): the
    # obligated dollars cover cost *and* fee, so dollars spent past the estimate are
    # dollars that would have paid fee.
    assert overrun.overrun == 100_000.0
    assert overrun.absorbed == 80_000.0
    assert overrun.at_completion == 0.0
    assert overrun.exhausted is True


def test_cpff_fee_is_never_negative():
    # Three times the estimated cost. The fee is gone, and "gone" is 0.0 with
    # `exhausted: True` — never -$1.92M, which is what `fixed_fee - overrun` would say.
    pos = _position("CPFF", 3_000_000.0, estimated_cost=_EST, fixed_fee=80_000.0)

    assert pos.at_completion == 0.0
    assert pos.absorbed == 80_000.0  # capped at the fee, not the overrun
    assert pos.exhausted is True
    assert pos.earned == 80_000.0  # still fully earned under the clause


def test_a_partial_overrun_eats_part_of_the_fee():
    pos = _position("CPFF", 1_050_000.0, estimated_cost=_EST, fixed_fee=80_000.0)

    assert pos.earned == 80_000.0
    assert pos.absorbed == 50_000.0
    assert pos.at_completion == 30_000.0
    assert pos.exhausted is False


def test_earned_fee_and_collectable_fee_differ_by_the_withhold():
    # 52.216-8(b): the CO may withhold up to 15% of the total fixed fee or $100,000,
    # whichever is less, until the contract is complete. An accountant tracking cash
    # asks for the second number, so both are reported.
    pos = _position("CPFF", 600_000.0, estimated_cost=_EST, fixed_fee=80_000.0)

    assert pos.earned == 48_000.0
    assert pos.withhold == 12_000.0  # 15% of the $80k total fee
    assert pos.collectable == 36_000.0


def test_the_withhold_is_capped_at_one_hundred_thousand_dollars():
    # "whichever is less" — on a $1M fee, 15% is $150k and the clause caps it at $100k.
    pos = _position("CPFF", _EST, estimated_cost=_EST, fixed_fee=1_000_000.0)

    assert pos.withhold == 100_000.0
    assert pos.collectable == 900_000.0


def test_the_withhold_never_exceeds_what_has_been_earned():
    # 5% through the cost: $4k earned against a $12k withhold ceiling. Withholding the
    # full $12k would report negative collectable fee.
    pos = _position("CPFF", 50_000.0, estimated_cost=_EST, fixed_fee=80_000.0)

    assert pos.earned == 4_000.0
    assert pos.withhold == 4_000.0
    assert pos.collectable == 0.0


# ----------------------------------------------------- CPAF: base + award fee pool


def _periods():
    """Four quarterly evaluation periods, two of them determined — the state the
    ticket describes: $180K pool, 2 of 4 determined, $71K earned of $90K available."""
    return [
        {
            "name": "Q1",
            "start": "2026-01-01",
            "end": "2026-03-31",
            "pool_share": 45_000.0,
            "status": "determined",
            "determined_amount": 38_000.0,
            "score": 88.0,
        },
        {
            "name": "Q2",
            "start": "2026-04-01",
            "end": "2026-06-30",
            "pool_share": 45_000.0,
            "status": "determined",
            "determined_amount": 33_000.0,
            "score": 74.0,
        },
        {
            "name": "Q3",
            "start": "2026-07-01",
            "end": "2026-09-30",
            "pool_share": 45_000.0,
            "status": "pending",
        },
        {
            "name": "Q4",
            "start": "2026-10-01",
            "end": "2026-12-31",
            "pool_share": 45_000.0,
            "status": "pending",
        },
    ]


def _cpaf(cost=600_000.0, periods=None):
    return _position(
        "CPAF",
        cost,
        periods=_periods() if periods is None else periods,
        estimated_cost=_EST,
        base_fee=30_000.0,
        award_fee_pool=180_000.0,
    )


def test_cpaf_base_fee_and_award_fee_are_separate_quantities():
    # FAR 16.401(e): a small base fee earned like a fixed fee, plus a pool earned only
    # on the government's determination. Summing them into one "fee" is what hides the
    # risk, so the payload carries them apart.
    pos = _cpaf()

    assert pos.basis == "base_plus_award"
    assert pos.base_earned == 18_000.0  # 60% of the $30k base
    assert pos.award_earned == 71_000.0
    assert pos.earned == 89_000.0
    assert pos.award_pool == 180_000.0


def test_cpaf_distinguishes_earned_available_and_at_risk():
    pos = _cpaf()

    assert pos.award_available == 90_000.0  # the two determined periods' share
    assert pos.at_risk == 90_000.0  # the two that have not been determined
    assert (pos.periods_determined, pos.periods_total) == (2, 4)


def test_an_undetermined_pool_is_never_counted_as_earned():
    # No determinations at all — the state every CPAF contract starts in. The pool is
    # entirely at risk and the only earned fee is the base.
    pos = _cpaf(periods=[])

    assert pos.award_earned == 0.0
    assert pos.at_risk == 180_000.0
    assert pos.earned == pos.base_earned == 18_000.0
    # And at completion: base fee plus what was actually determined. Never the pool.
    assert pos.at_completion == 30_000.0


def test_a_pending_period_contributes_nothing_even_with_an_amount_on_it():
    # A recommended-but-undetermined amount is a forecast, not fee. Status governs.
    periods = _periods()
    periods[2]["determined_amount"] = 45_000.0
    pos = _cpaf(periods=periods)

    assert pos.award_earned == 71_000.0
    assert pos.at_risk == 90_000.0


def test_a_determination_above_its_pool_share_is_clamped():
    # A period cannot earn more than its share of the pool; a figure that says
    # otherwise is a data-entry error and must not inflate the pool.
    periods = _periods()
    periods[0]["determined_amount"] = 90_000.0
    pos = _cpaf(periods=periods)

    assert pos.award_earned == 78_000.0  # 45k clamped + 33k
    assert pos.award_earned <= pos.award_pool


def test_periods_without_shares_split_the_pool_evenly():
    # The common case for a plan that just names four quarters: an even split is the
    # award-fee plan's own default and beats refusing to compute.
    periods = [{"name": f"Q{i + 1}", "status": "pending"} for i in range(4)]
    periods[0].update(status="determined", determined_amount=40_000.0)
    pos = _cpaf(periods=periods)

    assert pos.award_available == 45_000.0
    assert pos.award_earned == 40_000.0
    assert pos.at_risk == 135_000.0


# ------------------------------------------------------- CPIF: the incentive share


def _cpif(cost, **kw):
    figures = {
        "estimated_cost": _EST,
        "target_fee": 80_000.0,
        "min_fee": 40_000.0,
        "max_fee": 120_000.0,
        "share_ratio": "80/20",
        **kw,
    }
    return _position("CPIF", cost, **figures)


@pytest.mark.parametrize(
    "cost,expected",
    [
        (_EST, 80_000.0),  # on target: the target fee
        (900_000.0, 100_000.0),  # $100k underrun x 20% contractor share
        (1_100_000.0, 60_000.0),  # $100k overrun, same share, downward
        (700_000.0, 120_000.0),  # would be $140k — clamped at max fee
        (1_300_000.0, 40_000.0),  # would be $20k — floored at min fee
    ],
)
def test_the_incentive_formula_matches_the_hand_worked_example(cost, expected):
    # fee = clamp(target_fee + share_contractor x (target_cost - actual_cost), min, max)
    # FAR 16.304 / 52.216-10, with an 80/20 government/contractor share.
    assert _cpif(cost).at_completion == expected


def test_cpif_fee_moves_when_the_burn_assumption_moves():
    # The reason this is the most interesting number in the epic: fee is a live
    # function of cost, so a staffing change in the allocation matrix moves projected
    # profit. Same contract, two burn assumptions, two fees.
    lean = _cpif(950_000.0).at_completion
    heavy = _cpif(1_050_000.0).at_completion

    assert lean > heavy
    assert lean - heavy == 20_000.0  # 20% of the $100k cost swing


def test_cpif_earned_to_date_is_provisional_at_the_target_fee():
    # The incentive is settled on *final* cost, so nothing is earned at the incentive
    # rate mid-performance: 52.216-10 bills fee provisionally at the target rate and
    # adjusts at completion. Half the target cost incurred → half the target fee.
    pos = _cpif(500_000.0)

    assert pos.earned == 40_000.0
    assert pos.provisional is True
    # And the at-completion read is the formula, which is a different number.
    assert pos.at_completion == 120_000.0


def test_cpif_without_a_readable_share_ratio_reports_unknown():
    pos = _cpif(_EST, share_ratio="not a ratio")

    assert pos.known is False
    assert "share_ratio" in pos.missing
    assert pos.earned == 0.0


# ------------------------------------------------------------------- FPI: profit


def _fpi(cost, **kw):
    figures = {
        "estimated_cost": _EST,
        "target_profit": 100_000.0,
        "share_ratio": "70/30",
        "ceiling_price": 1_200_000.0,
        **kw,
    }
    return _position("FPI", cost, **figures)


def test_fpi_shares_the_cost_variance_on_profit():
    # FAR 16.403: the same share arithmetic, on profit rather than fee.
    assert _fpi(900_000.0).at_completion == 130_000.0
    assert _fpi(1_100_000.0).at_completion == 70_000.0
    assert _fpi(_EST).basis == "incentive_profit"


def test_fpi_profit_is_capped_by_the_price_ceiling():
    # Past the ceiling price the government owes nothing more, so cost + profit cannot
    # exceed it: at $1.15M cost the formula says $55k of profit and the ceiling allows
    # $50k.
    assert _fpi(1_150_000.0).at_completion == 50_000.0
    # And past the ceiling entirely, profit is zero rather than negative.
    assert _fpi(1_400_000.0).at_completion == 0.0


def test_fpi_reports_the_point_of_total_assumption():
    # PTA = target_cost + (ceiling_price - target_price) / government_share, where
    # target_price is target cost + target profit. Above it the contractor absorbs
    # every additional dollar.
    assert _fpi(_EST).pta == pytest.approx(1_142_857.14, abs=0.01)


# ------------------------------------------------ types with no fee mechanics, and gaps


@pytest.mark.parametrize("code", ["FFP", "TM"])
def test_types_without_fee_mechanics_have_no_fee_position(code):
    # FFP profit is price - cost and is handled as margin in #79; T&M fee is inside the
    # billing rate. Emitting an empty fee position on either would invite it to be read
    # as a fee arrangement that does not exist.
    assert _position(code, 500_000.0, estimated_cost=_EST, fixed_fee=80_000.0) is None


def test_an_unknown_policy_has_no_fee_position():
    assert pricing.earned_fee(pricing.UNKNOWN, _terms(), 500_000.0) is None


def test_missing_fee_figures_are_named_not_guessed():
    # A CPFF CLIN whose award never printed a fee. There is no honest number, so the
    # position says which figures are missing and reports zero — the same posture #76
    # takes on an unreadable contract type.
    pos = _position("CPFF", 500_000.0, estimated_cost=_EST)

    assert pos.known is False
    assert pos.missing == ("fixed_fee",)
    assert (pos.earned, pos.at_completion, pos.collectable) == (0.0, 0.0, 0.0)


def test_a_missing_estimated_cost_is_also_a_gap():
    # Without the cost basis there is nothing to earn the fee proportionally against.
    pos = _position("CPFF", 500_000.0, fixed_fee=80_000.0)

    assert pos.known is False
    assert "estimated_cost" in pos.missing
    assert pos.cost_frac is None


def test_zero_cost_earns_no_fee_but_is_still_known():
    pos = _position("CPFF", 0.0, estimated_cost=_EST, fixed_fee=80_000.0)

    assert pos.known is True
    assert pos.earned == 0.0
    assert pos.exhausted is False


# ------------------------------------------------------------- the burn.py wiring

_PERIOD = {"name": "Base", "pop_start": "2026-01-01", "pop_end": "2026-12-31"}
_CEILING = 400_000.0
_CLIN_EST = 370_000.0
_FIXED_FEE = 30_000.0


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
    """Level 2, exactly as #79's tests use it: $40 direct burdens to $85.75 against a
    $100 billing rate, so cost is a real number and not a billings stand-in."""
    return rates.CostModel(rate_set=_pools(), lcat_direct={"software engineer": 40.00})


def _contract(clin_type="CPFF", fee_figures=True, fee_periods=None, **clin_extra):
    figures = (
        {"estimated_cost": _CLIN_EST, "fixed_fee": _FIXED_FEE} if fee_figures else {}
    )
    contract = {
        "id": 1,
        "contract": {
            "piid": "TEST-80",
            "total_ceiling": _CEILING,
            "total_obligated": _CEILING,
        },
        "clins": [
            {
                "clin": "0001",
                "period": "Base",
                "title": "Professional Services",
                "is_labor": True,
                "ceiling": _CEILING,
                "est_hours": 4_000,
                "type": clin_type,
                **figures,
                **clin_extra,
            }
        ],
        "periods": [_PERIOD],
    }
    if fee_periods is not None:
        contract["fee_periods"] = fee_periods
    return contract


def _rows(weeks=8, hours=40):
    return [
        {
            "charge_code": "0001",
            "labor_category": "Software Engineer",
            "total_hours": hours,
            "week_ending": (date(2026, 1, 2) + timedelta(days=7 * i)).isoformat(),
            "employee_id": "e1",
        }
        for i in range(weeks)
    ]


def _card(model=None, **kw):
    p = burn.compute(_contract(**kw), _rows(), cost_model=model)
    return p["clins"][0], p


def test_a_cpff_card_carries_the_fee_position():
    card, _ = _card(_model())
    fee = card["fee_position"]

    assert fee["basis"] == "fixed_fee"
    assert fee["known"] is True
    # Earned in step with the cost the engine actually measured.
    assert fee["earned"] == round(_FIXED_FEE * card["cost"] / _CLIN_EST, 2)
    assert fee["target"] == _FIXED_FEE
    assert fee["withhold"] == round(min(0.15 * _FIXED_FEE, fee["earned"]), 2)
    assert fee["collectable"] == round(fee["earned"] - fee["withhold"], 2)


def test_earned_fee_lands_in_revenue_and_still_reconciles():
    # #79's invariant — cost + fee == revenue at the CLIN — has to survive the fee
    # becoming a real number.
    card, _ = _card(_model())

    assert card["fee_earned"] == card["fee_position"]["earned"]
    assert round(card["cost"] + card["fee_earned"], 2) == card["revenue"]
    assert card["fee_known"] is True
    assert card["margin_pct"] is not None


def test_the_fee_is_not_folded_into_revenue_when_cost_is_a_billing_standin():
    # Level 1: no direct rates, so `cost` IS the loaded billing rate and already
    # contains the fee. Adding an earned fee on top would count it twice and report a
    # margin off two copies of the same dollars. #77's `cost_known` is the gate.
    card, _ = _card(model=None)

    assert card["cost_known"] is False
    assert card["revenue"] == card["cost"]
    assert card["fee_earned"] == 0.0
    assert card["fee_known"] is False
    # The award's fee terms are still reported — they are a fact about the document —
    # but flagged as not a profit read, the same way `margin_position` is.
    assert card["fee_position"]["target"] == _FIXED_FEE
    assert card["fee_position"]["known"] is False


def test_projected_fee_at_completion_is_reported_beside_earned_to_date():
    # "Projected fee $312K against a $400K target" is the number worth alarming on, so
    # it rides the same forward pace as the runway forecast.
    card, _ = _card(_model())
    fee = card["fee_position"]

    assert fee["projected"]["cost"] > card["cost"]
    assert fee["projected"]["at_completion"] > fee["earned"]
    assert fee["projected"]["target_delta"] == round(
        fee["projected"]["at_completion"] - _FIXED_FEE, 2
    )


def test_a_projected_overrun_shows_the_fee_it_costs():
    # 85 hrs/wk on a CLIN sized for 40: the projection runs past the estimated cost, so
    # projected fee falls below target and the shortfall is named. Earned-to-date is
    # untouched, because the overrun has not happened yet.
    p = burn.compute(_contract(), _rows(weeks=8, hours=85), cost_model=_model())
    fee = p["clins"][0]["fee_position"]

    assert fee["projected"]["overrun"] > 0
    assert fee["projected"]["at_completion"] < _FIXED_FEE
    assert fee["projected"]["target_delta"] < 0
    assert fee["earned"] > 0


def test_the_fee_engine_does_not_move_a_single_funding_figure():
    # The scope line for this ticket. Fee genuinely draws on the same obligated
    # dollars as cost, but rewiring `spent` and the runway is a separate change with
    # its own follow-up. Same contract, with and without fee figures on the CLIN:
    # every funding-side number is identical.
    with_fee, p_fee = _card(_model())
    without, p_bare = _card(_model(), fee_figures=False)

    for key in (
        "spent",
        "remaining",
        "weekly",
        "pct",
        "pct_budget",
        "weeks_left",
        "exhaust_week",
        "runway_days",
        "status",
        "stop_date",
        "funds_exceeded",
        "ceiling_breached",
        "cost",
    ):
        assert with_fee[key] == without[key], key
    for key in ("spent", "budget", "ceiling", "pct", "cost"):
        assert p_fee["totals"][key] == p_bare["totals"][key], key


def test_contract_totals_reconcile_with_the_fee_in_them():
    _, p = _card(_model())
    t = p["totals"]

    assert round(t["revenue"] - t["cost"], 2) == t["fee"]
    assert t["fee"] == round(sum(c["fee_earned"] for c in p["clins"]), 2)
    assert t["fee"] > 0
    assert t["fee_known"] is True


def test_projected_fee_loss_raises_a_fee_alert():
    # The cost-type counterpart to #79's `margin_alerts`, and the reason the epic is
    # worth doing: the fee this overrun is going to cost, named before year end.
    p = burn.compute(_contract(), _rows(weeks=8, hours=85), cost_model=_model())
    alert = p["fee_alerts"][0]

    assert alert["code"] == "CLIN 0001"
    assert alert["policy"] == "CPFF"
    assert alert["target"] == _FIXED_FEE
    assert alert["fee_lost"] > 0
    assert alert["projected"] == round(_FIXED_FEE - alert["fee_lost"], 2)
    assert p["all_clear"] is False


def test_a_cost_type_clin_on_pace_raises_no_fee_alert():
    _, p = _card(_model())

    assert p["fee_alerts"] == []


def test_an_undetermined_award_pool_is_not_a_fee_alert():
    # A CPAF pool sitting undetermined puts at-completion fee below target on every
    # contract of this type from day one. Alarming on that would be crying wolf about
    # the ordinary state of the world — only fee that *cost* has taken is an alert.
    _, p = _card(
        _model(),
        clin_type="CPAF",
        fee_figures=False,
        fee_periods=[],
        estimated_cost=_CLIN_EST,
        base_fee=10_000.0,
        award_fee_pool=180_000.0,
    )
    card = p["clins"][0]

    assert card["fee_position"]["at_risk"] == 180_000.0
    assert card["fee_position"]["target_delta"] < 0
    assert p["fee_alerts"] == []


def test_award_fee_determinations_come_off_the_contract():
    # The government's determinations are entered, not extracted, so they live on the
    # contract blob and reach the engine the way holidays and absences do.
    card, _ = _card(
        _model(),
        clin_type="CPAF",
        fee_figures=False,
        fee_periods=_periods(),
        estimated_cost=_CLIN_EST,
        base_fee=10_000.0,
        award_fee_pool=180_000.0,
    )
    fee = card["fee_position"]

    assert fee["basis"] == "base_plus_award"
    assert fee["periods_determined"] == 2
    assert fee["award_earned"] == 71_000.0
    assert fee["at_risk"] == 90_000.0
    # The base fee still earns off cost, like a fixed fee.
    assert fee["base_earned"] == round(10_000.0 * card["cost"] / _CLIN_EST, 2)


def test_a_fixed_price_card_has_no_fee_position():
    card, _ = _card(_model(), clin_type="FFP", fee_figures=False)

    assert card["fee_position"] is None
    assert card["margin_position"] is not None  # #79's read, untouched


def test_a_tm_card_is_unchanged_to_the_cent():
    # The #79 regression bar, re-pinned here: T&M has no fee mechanics and this ticket
    # must not touch it.
    card, _ = _card(_model(), clin_type="T&M", fee_figures=False)

    assert card["fee_position"] is None
    assert card["revenue"] == card["billings"]
    assert card["spent"] == card["billings"]


# ------------------------------------------------------------------- the endpoint


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with TestClient(main.app) as c:
        yield c


def _saved(client):
    cid = db.save_contract("TEST-80", {"contract": {"piid": "TEST-80"}, "clins": []})
    return cid


def test_fee_periods_round_trip(client):
    cid = _saved(client)
    r = client.put(f"/api/contracts/{cid}/fee-periods", json={"periods": _periods()})

    assert r.status_code == 200
    saved = r.json()["periods"]
    assert len(saved) == 4
    assert saved[0]["status"] == "determined"
    assert saved[0]["determined_amount"] == 38_000.0
    assert saved[2]["determined_amount"] is None
    # And it is readable back out.
    assert client.get(f"/api/contracts/{cid}/fee-periods").json()["periods"] == saved


def test_clearing_the_periods_is_reachable(client):
    cid = _saved(client)
    client.put(f"/api/contracts/{cid}/fee-periods", json={"periods": _periods()})
    r = client.put(f"/api/contracts/{cid}/fee-periods", json={"periods": []})

    assert r.status_code == 200
    assert r.json()["periods"] == []


def test_a_bad_determination_is_rejected_with_a_reason(client):
    cid = _saved(client)
    r = client.put(
        f"/api/contracts/{cid}/fee-periods",
        json={"periods": [{"name": "Q1", "status": "determined"}]},
    )

    assert r.status_code == 400
    assert "determined_amount" in r.json()["detail"]


def test_an_unknown_status_is_rejected(client):
    cid = _saved(client)
    r = client.put(
        f"/api/contracts/{cid}/fee-periods",
        json={"periods": [{"name": "Q1", "status": "maybe"}]},
    )

    assert r.status_code == 400


def test_a_negative_amount_is_rejected(client):
    cid = _saved(client)
    r = client.put(
        f"/api/contracts/{cid}/fee-periods",
        json={
            "periods": [
                {"name": "Q1", "status": "determined", "determined_amount": -1.0}
            ]
        },
    )

    assert r.status_code == 400


def test_fee_periods_on_a_missing_contract_is_a_404(client):
    assert (
        client.put("/api/contracts/9999/fee-periods", json={"periods": []}).status_code
        == 404
    )
