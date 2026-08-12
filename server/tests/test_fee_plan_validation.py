"""#185 — an award-fee plan is checked against the pool it draws on, at write time.

#80 validated each evaluation period against itself: a number is a number, a status is
a status, a determined period carries an amount. What it could not see was the pool.
So a determination larger than its period's share of that pool saved happily, and
`_award_fee_position` quietly clamped it on the way out — which put two different
numbers in front of the same reader, the period table showing the $90,000 that was
entered and the fee total counting the $45,000 that could be earned.

The rule these tests pin: **the plan is refused at write time, and the engine's clamp
stops being the correction path for anything a user typed.** The clamp stays where it
is, as a floor under stored data that predates this validation — but a plan that gets
past the endpoint reconciles, and the last test in each section is the one that proves
it: sum the determinations in the period table, and you have the earned award fee.

The mixed case — some periods stating a share, others silent — is refused rather than
filled in. Splitting the remainder across the silent periods would be Runway inferring
how a negotiated fee plan divides, which is the same thing it refuses to do when it
declines to guess tranche cadence from two obligations.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, main, pricing  # noqa: E402

_POOL = 180_000.0


def _pools(**kw):
    """CLIN id -> award-fee pool, the shape `validate_fee_plan` routes against."""
    return {k: v for k, v in kw.items()} or {"0001": _POOL}


def _period(name, share=None, amount=None, clin=None):
    entry = {"name": name, "status": "determined" if amount is not None else "pending"}
    if share is not None:
        entry["pool_share"] = share
    if amount is not None:
        entry["determined_amount"] = amount
    if clin is not None:
        entry["clin"] = clin
    return entry


def _even_quarters():
    """Four periods, no shares stated — the plan's own default, and the shape that has
    to keep working untouched."""
    return [_period("Q1", amount=40_000.0), _period("Q2"), _period("Q3"), _period("Q4")]


# ------------------------------------------------------- a determination vs its share


def test_the_ticket_repro_is_refused():
    # #185 verbatim: a $45,000 share determined at $90,000 on a $180,000 pool. This is
    # the save that used to succeed and then disagree with itself on screen.
    problem = pricing.validate_fee_plan(
        [_period("Q1", share=45_000.0, amount=90_000.0)], {"0001": _POOL}
    )

    assert problem is not None
    # The message has to be actionable without opening the contract: which period, and
    # both of the numbers that conflict.
    assert "'Q1'" in problem
    assert "$90,000" in problem
    assert "$45,000" in problem


def test_a_determination_equal_to_its_share_is_fine():
    # The government determined the full share. Nothing is wrong with that.
    assert (
        pricing.validate_fee_plan(
            [_period("Q1", share=45_000.0, amount=45_000.0)], {"0001": _POOL}
        )
        is None
    )


def test_a_zero_determination_survives():
    # A determination of zero is a real and very different outcome from "not yet
    # evaluated", and #80 went out of its way to keep them apart. Nothing here may
    # collapse the two.
    assert (
        pricing.validate_fee_plan(
            [_period("Q1", share=45_000.0, amount=0.0)], {"0001": _POOL}
        )
        is None
    )


def test_shares_totalling_more_than_the_pool_are_refused():
    # Each period is individually plausible; the plan is not. $60k x 4 divides up
    # $240,000 of a $180,000 pool.
    problem = pricing.validate_fee_plan(
        [_period(f"Q{i}", share=60_000.0) for i in range(1, 5)], {"0001": _POOL}
    )

    assert problem is not None
    assert "$240,000" in problem
    assert "$180,000" in problem


def test_shares_totalling_exactly_the_pool_are_accepted():
    assert (
        pricing.validate_fee_plan(
            [_period(f"Q{i}", share=45_000.0) for i in range(1, 5)], {"0001": _POOL}
        )
        is None
    )


def test_a_rounding_hair_over_the_pool_is_not_a_refusal():
    # Thirds of a pool don't divide into cents. A plan that sums to the pool within
    # rounding *is* the plan summing to the pool, and refusing it would be arithmetic
    # pedantry aimed at a user who typed the right numbers.
    third = _POOL / 3 + 0.001
    assert (
        pricing.validate_fee_plan(
            [_period(f"P{i}", share=third) for i in range(1, 4)], {"0001": _POOL}
        )
        is None
    )


# ------------------------------------------------------------------ the even split


def test_an_even_split_plan_still_works():
    # No shares stated anywhere: the pool splits evenly, which is the award-fee plan's
    # own default and predates this ticket.
    assert pricing.validate_fee_plan(_even_quarters(), {"0001": _POOL}) is None


def test_a_determination_over_the_even_share_is_refused():
    # The same contradiction as the ticket repro, reached without an explicit share:
    # $90,000 determined where the even split affords $45,000.
    periods = _even_quarters()
    periods[0]["determined_amount"] = 90_000.0
    problem = pricing.validate_fee_plan(periods, {"0001": _POOL})

    assert problem is not None
    assert "'Q1'" in problem
    # And it says where the $45,000 came from, because the user never typed it — the
    # fix is to state the shares, and the message has to point at that.
    assert "evenly" in problem
    assert "pool_share" in problem


def test_a_mixed_plan_is_refused_and_names_the_silent_periods():
    # THE deliberate decision in this ticket. Q1 and Q2 are priced; Q3 and Q4 are
    # silent. Splitting the leftover $80,000 across them would be Runway inventing the
    # back half of a negotiated fee plan.
    periods = [
        _period("Q1", share=50_000.0),
        _period("Q2", share=50_000.0),
        _period("Q3"),
        _period("Q4"),
    ]
    problem = pricing.validate_fee_plan(periods, {"0001": _POOL})

    assert problem is not None
    assert "'Q3'" in problem and "'Q4'" in problem


# --------------------------------------------------------------- multi-CLIN routing


_TWO_POOLS = {"0001": _POOL, "0002": 60_000.0}


def test_each_clin_pool_is_validated_separately():
    # $150,000 of shares on 0001 and $50,000 on 0002. Summed across the award that is
    # $200,000 against $240,000 of pools and passes; routed, each pool holds. The
    # aggregate check would have to be per-CLIN to see either of those correctly.
    periods = [
        _period("A1", share=100_000.0, clin="0001"),
        _period("A2", share=50_000.0, clin="0001"),
        _period("B1", share=50_000.0, clin="0002"),
    ]

    assert pricing.validate_fee_plan(periods, _TWO_POOLS) is None


def test_an_overrun_on_the_second_pool_is_caught_and_named():
    # 0001 is fine; 0002's periods divide $80,000 of a $60,000 pool. The error has to
    # name the CLIN, or it sends the reader to the wrong half of the award.
    periods = [
        _period("A1", share=100_000.0, clin="0001"),
        _period("B1", share=40_000.0, clin="0002"),
        _period("B2", share=40_000.0, clin="0002"),
    ]
    problem = pricing.validate_fee_plan(periods, _TWO_POOLS)

    assert problem is not None
    assert "0002" in problem
    assert "$60,000" in problem


def test_the_even_split_is_per_pool_not_per_award():
    # Two silent periods on 0002 split *its* $60,000, not the award's $240,000. A
    # $40,000 determination there is over the $30,000 share and must be refused.
    periods = [
        _period("A1", clin="0001"),
        _period("B1", amount=40_000.0, clin="0002"),
        _period("B2", clin="0002"),
    ]
    problem = pricing.validate_fee_plan(periods, _TWO_POOLS)

    assert problem is not None
    assert "'B1'" in problem
    assert "$30,000" in problem


def test_an_unassigned_period_on_a_two_pool_award_is_refused():
    # `burn._fee_periods_by_clin` drops it rather than counting one determination
    # against two pools — so saving it stores a determination that no fee total will
    # ever include. That is the same contradiction the ticket is about, arriving from
    # the other side.
    problem = pricing.validate_fee_plan([_period("Q1", amount=10_000.0)], _TWO_POOLS)

    assert problem is not None
    assert "'Q1'" in problem
    assert "0001" in problem and "0002" in problem


def test_an_unassigned_period_is_fine_on_a_single_pool_award():
    # Which is the ordinary case, and the reason `clin` is optional at all.
    assert (
        pricing.validate_fee_plan([_period("Q1", amount=10_000.0)], {"0001": _POOL})
        is None
    )


def test_a_period_pointed_at_a_clin_with_no_pool_is_refused():
    problem = pricing.validate_fee_plan(
        [_period("Q1", amount=10_000.0, clin="0003")], _TWO_POOLS
    )

    assert problem is not None
    assert "0003" in problem


def test_periods_entered_before_the_award_is_priced_are_not_refused():
    # No CLIN on this award carries a pool yet — the determinations can genuinely
    # arrive before the document that prices them, and there is nothing to validate
    # against. Refusing here would be Runway demanding the paperwork in its own order.
    assert (
        pricing.validate_fee_plan(
            [_period("Q1", share=45_000.0, amount=90_000.0)], {"0001": None}
        )
        is None
    )
    assert pricing.validate_fee_plan([_period("Q1", amount=90_000.0)], {}) is None


# ------------------------------------------------------- the reconciliation itself


@pytest.mark.parametrize(
    "periods",
    [
        [
            _period("Q1", share=45_000.0, amount=38_000.0),
            _period("Q2", share=45_000.0, amount=33_000.0),
            _period("Q3", share=45_000.0),
            _period("Q4", share=45_000.0),
        ],
        [_period("Q1", amount=40_000.0), _period("Q2", amount=45_000.0), _period("Q3")],
    ],
)
def test_a_validated_plan_needs_no_clamp(periods):
    # The point of the ticket, stated as arithmetic: for any plan that passes
    # validation, the fee total equals the sum of the determinations the period table
    # renders. `min(share, determined)` inside the engine changes nothing here, which
    # is exactly what "the clamp is no longer the correction path" means.
    assert pricing.validate_fee_plan(periods, {"0001": _POOL}) is None

    position = pricing.earned_fee(
        pricing.POLICIES["CPAF"],
        pricing.fee_terms(
            {"clin": "0001", "estimated_cost": 1_000_000.0, "award_fee_pool": _POOL}
        ),
        600_000.0,
        periods=tuple(periods),
    )
    entered = sum(p.get("determined_amount") or 0.0 for p in periods)

    assert position.award_earned == pytest.approx(entered)


# ------------------------------------------------------------------- the endpoint


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with TestClient(main.app) as c:
        yield c


def _cpaf(clins=None):
    return db.save_contract(
        "TEST-185",
        {
            "contract": {"piid": "TEST-185"},
            "clins": clins
            or [
                {
                    "clin": "0001",
                    "type": "CPAF",
                    "is_labor": True,
                    "estimated_cost": 1_000_000.0,
                    "base_fee": 20_000.0,
                    "award_fee_pool": _POOL,
                }
            ],
        },
    )


def test_the_endpoint_refuses_the_ticket_repro(client):
    cid = _cpaf()
    r = client.put(
        f"/api/contracts/{cid}/fee-periods",
        json={"periods": [_period("Q1", share=45_000.0, amount=90_000.0)]},
    )

    assert r.status_code == 400
    assert "'Q1'" in r.json()["detail"]
    # And nothing landed. A refused plan that stored half of itself would be worse
    # than the bug.
    assert client.get(f"/api/contracts/{cid}/fee-periods").json()["periods"] == []


def test_a_valid_plan_still_saves(client):
    cid = _cpaf()
    r = client.put(
        f"/api/contracts/{cid}/fee-periods",
        json={
            "periods": [
                _period("Q1", share=45_000.0, amount=38_000.0),
                _period("Q2", share=45_000.0),
            ]
        },
    )

    assert r.status_code == 200
    assert r.json()["periods"][0]["determined_amount"] == 38_000.0


def test_the_endpoint_routes_before_validating(client):
    # Two pools, and a plan that is only legible once each period is routed to its own.
    cid = _cpaf(
        clins=[
            {"clin": "0001", "type": "CPAF", "award_fee_pool": _POOL},
            {"clin": "0002", "type": "CPAF", "award_fee_pool": 60_000.0},
        ]
    )
    ok = client.put(
        f"/api/contracts/{cid}/fee-periods",
        json={
            "periods": [
                _period("A1", share=150_000.0, amount=100_000.0, clin="0001"),
                _period("B1", share=60_000.0, amount=50_000.0, clin="0002"),
            ]
        },
    )
    over = client.put(
        f"/api/contracts/{cid}/fee-periods",
        json={"periods": [_period("B1", share=90_000.0, clin="0002")]},
    )

    assert ok.status_code == 200
    assert over.status_code == 400
    assert "0002" in over.json()["detail"]


def test_clearing_the_periods_is_still_reachable(client):
    # An empty plan can't contradict a pool, and clearing is how a wrong one gets
    # fixed — this must not be validated into a corner.
    cid = _cpaf()
    r = client.put(f"/api/contracts/{cid}/fee-periods", json={"periods": []})

    assert r.status_code == 200
    assert r.json()["periods"] == []


def test_a_missing_contract_is_still_a_404(client):
    assert (
        client.put(
            "/api/contracts/9999/fee-periods",
            json={"periods": [_period("Q1", share=45_000.0)]},
        ).status_code
        == 404
    )


def test_a_bad_field_is_still_a_400_before_the_contract_is_read(client):
    # Per-field validation runs first and does not need the contract, so a malformed
    # period on a contract that doesn't exist is still reported as the malformed
    # period. Pinned because the two-pass order is easy to swap by accident.
    r = client.put(
        "/api/contracts/9999/fee-periods",
        json={"periods": [{"name": "Q1", "status": "determined"}]},
    )

    assert r.status_code == 400
    assert "determined_amount" in r.json()["detail"]
