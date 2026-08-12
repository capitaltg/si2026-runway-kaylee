"""#159 — the award header's own cost and fee totals reach the fee model.

Ingest extracts `total_estimated_cost` and `total_fee` off the face of the award,
confidence-scores them, shows them on the review screen for the user to correct, and
stores them. Nothing then read them: `pricing.fee_terms` looked at CLIN keys only. So a
document could ingest cleanly, display its primary cost and fee terms as confirmed, and
have no effect whatsoever on the fee position — the dead-data failure mode where the
product looks like it understood the contract and didn't.

What makes this more than a wiring fix is that the header totals are a *contract-level*
statement while a CLIN-level rule earns the fee. These tests pin the three rules that
make using them honest:

  * **Fill, never override.** A figure the CLIN printed always wins. The header is the
    fallback for a line that stated nothing, not a second opinion about one that did.
  * **One candidate or none.** One fee-bearing labor CLIN means the totals are that
    line's terms. Two means the award stated a total without stating the split, and the
    answer is a named gap for the user to close — never a division.
  * **Say where the number came from.** A position computed off a contract-level total
    carries `header_derived`, because "the award printed this line's fee" is a stronger
    claim than "this is the only line that could have held it".

The resolution tests call `pricing.header_fee_by_clin` directly, since deciding whether
a total is unambiguous is a pure function of the award. The wiring test runs through
`burn.compute` at Level 2, where a filled figure actually has to move a card.
"""

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import burn, pricing, rates  # noqa: E402

_HEADER_EST = 900_000.0
_HEADER_FEE = 60_000.0


def _labor(clin="0001", clin_type="CPFF", **extra):
    return {
        "clin": clin,
        "period": "Base",
        "title": "Professional Services",
        "is_labor": True,
        "ceiling": 1_000_000.0,
        "est_hours": 10_000,
        "type": clin_type,
        **extra,
    }


def _header(**extra):
    return {
        "piid": "TEST-159",
        "total_ceiling": 1_000_000.0,
        "total_obligated": 1_000_000.0,
        "total_estimated_cost": _HEADER_EST,
        "total_fee": _HEADER_FEE,
        **extra,
    }


def _resolve(clins, header=None):
    """The header totals resolved against these CLINs, keyed by CLIN — as `compute`
    calls it, policy included, so a mistyped fixture fails the way a real award would.
    """
    header = _header() if header is None else header
    return pricing.header_fee_by_clin(
        header, clins, lambda c: pricing.policy_for(c, header)
    )


def _terms(clins, header=None, index=0):
    """The resolved `FeeTerms` for one CLIN of a fixture award."""
    resolved = _resolve(clins, header)
    clin = clins[index]
    return pricing.fee_terms(clin, resolved.get(str(clin["clin"])))


def _position(clins, header=None, index=0, cost=450_000.0):
    clin = clins[index]
    header = _header() if header is None else header
    return pricing.earned_fee(
        pricing.policy_for(clin, header), _terms(clins, header, index), cost
    )


# ------------------------------------------------------- one unambiguous candidate


def test_header_totals_fill_a_cpff_clin_that_printed_nothing():
    """The whole ticket, in one case: a CPFF line with blank figures under an award that
    stated both totals now has terms, and the position computes."""
    clins = [_labor()]

    terms = _terms(clins)
    assert terms.estimated_cost == _HEADER_EST
    assert terms.fixed_fee == _HEADER_FEE

    pos = _position(clins)
    assert pos.known is True
    assert pos.missing == ()
    assert pos.target == _HEADER_FEE
    # Earned in step with cost against the header's estimate, same rule as ever.
    assert pos.earned == pytest.approx(_HEADER_FEE * 450_000.0 / _HEADER_EST)


def test_a_filled_position_says_the_figures_came_from_the_header():
    """`header_derived` is the honesty half. Without it the UI reports a contract-level
    total as this line's negotiated terms, which is a claim the award never made."""
    assert _position([_labor()]).header_derived is True


def test_a_clin_that_printed_its_own_terms_is_not_header_derived():
    clins = [_labor(estimated_cost=800_000.0, fixed_fee=50_000.0)]

    pos = _position(clins)
    assert pos.known is True
    assert pos.header_derived is False


def test_clin_figures_win_over_header_totals():
    """Fill, never override — and per figure, not per CLIN: a line that printed its cost
    and left the fee blank keeps its own cost and takes only the fee."""
    clins = [_labor(estimated_cost=800_000.0)]

    terms = _terms(clins)
    assert terms.estimated_cost == 800_000.0
    assert terms.estimated_cost_from_header is False
    assert terms.fixed_fee == _HEADER_FEE
    assert terms.fee_from_header is True


def test_a_header_with_no_totals_changes_nothing():
    clins = [_labor()]
    header = _header(total_estimated_cost=None, total_fee=None)

    assert _resolve(clins, header) == {}
    pos = _position(clins, header)
    assert pos.known is False
    assert set(pos.missing) == {"fixed_fee", "estimated_cost"}


# --------------------------------------------------------- the per-type fee figure


@pytest.mark.parametrize(
    "clin_type,field",
    [
        ("CPFF", "fixed_fee"),
        ("CPIF", "target_fee"),
        # FPI prints "Target Profit" rather than a fee; it is the same figure the face of
        # the award totals as fee, so the mapping is a naming difference.
        ("FPI", "target_profit"),
    ],
)
def test_the_header_fee_maps_to_the_figure_the_type_prints(clin_type, field):
    clins = [
        _labor(clin_type=clin_type, share_ratio="80/20", ceiling_price=1_000_000.0)
    ]

    terms = _terms(clins)
    assert getattr(terms, field) == _HEADER_FEE
    assert terms.estimated_cost == _HEADER_EST
    assert _position(clins).known is True


def test_cpaf_takes_the_cost_and_refuses_the_fee():
    """A CPAF fee is a base fee *plus* an award pool. One total cannot be split back into
    two without inventing the pool's size, so the cost fills and the fee stays the gap
    the award actually left — named, so the card can explain itself."""
    clins = [_labor(clin_type="CPAF")]

    terms = _terms(clins)
    assert terms.estimated_cost == _HEADER_EST
    assert terms.award_fee_pool is None
    assert terms.base_fee is None
    assert terms.header_gap == "fee_split"

    pos = _position(clins)
    assert pos.known is False
    assert "fee_split" in pos.missing
    assert pos.header_derived is False


# ------------------------------------------------------------------- eligibility


def test_two_fee_bearing_clins_produce_an_allocation_gap_not_a_split():
    """The refusal that matters. Half of $60,000 is a plausible number and a fabricated
    one; which line the award's total belongs to is the user's answer to give."""
    clins = [_labor("0001"), _labor("0002")]

    for i in (0, 1):
        terms = _terms(clins, index=i)
        assert terms.estimated_cost is None
        assert terms.fixed_fee is None
        assert terms.header_gap == "clin_allocation"

        pos = _position(clins, index=i)
        assert pos.known is False
        assert "clin_allocation" in pos.missing
        # The figures it still lacks are named alongside the reason, not replaced by it.
        assert "fixed_fee" in pos.missing


def test_a_nonlabor_cost_line_is_not_a_second_candidate():
    """The ordinary award shape: one CPFF labor CLIN and a cost-type travel line. Travel
    gets no fee position from this engine, so counting it as a candidate would strand the
    header totals on the most common contract there is."""
    clins = [
        _labor("0001"),
        {"clin": "0002", "period": "Base", "title": "Travel", "type": "CPFF"},
    ]

    resolved = _resolve(clins)
    assert set(resolved) == {"0001"}
    assert _position(clins).known is True


@pytest.mark.parametrize("clin_type", ["FFP", "TM"])
def test_a_line_with_no_fee_mechanic_never_takes_the_totals(clin_type):
    """FFP profit is price minus cost and a T&M fee is inside the billing rate. Neither
    earns a fee these totals could be, and `earned_fee` returns nothing for either."""
    clins = [_labor(clin_type=clin_type)]

    assert _resolve(clins) == {}
    assert _position(clins) is None


def test_an_unknown_type_keeps_its_pre_type_behaviour():
    """An unlabelled award must not start taking header figures — `unknown` carries the
    pre-#76 engine's behaviour on purpose, and a guessed read must not look typed."""
    clins = [_labor(clin_type=None)]

    assert _resolve(clins) == {}


def test_fee_bearing_reads_the_policy_not_the_populated_fields():
    assert pricing.fee_bearing(pricing.POLICIES["CPFF"]) is True
    assert pricing.fee_bearing(pricing.POLICIES["CPAF"]) is True
    assert pricing.fee_bearing(pricing.POLICIES["FFP"]) is False
    assert pricing.fee_bearing(pricing.UNKNOWN) is False
    assert pricing.fee_bearing(None) is False


# ----------------------------------------------------------------- burn.py wiring

_PERIOD = {"name": "Base", "pop_start": "2026-01-01", "pop_end": "2026-12-31"}


def _model():
    """Level 2, as #79/#80's tests use it, so `cost` is a real buildup and `cost_known`
    is true — without which a fee position is withheld no matter how good the terms."""
    return rates.CostModel(
        rate_set=rates.RateSet(
            fiscal_year="FY26",
            pools=tuple(
                rates.Pool(name=n, rate=r, base=rates.DEFAULT_BASES[n])
                for n, r in (
                    (rates.FRINGE, 0.32),
                    (rates.OVERHEAD, 0.45),
                    (rates.GNA, 0.12),
                )
            ),
        ),
        lcat_direct={"software engineer": 40.00},
    )


def _rows(weeks=8, hours=40, charge_code="0001"):
    return [
        {
            "charge_code": charge_code,
            "labor_category": "Software Engineer",
            "total_hours": hours,
            "week_ending": (date(2026, 1, 2) + timedelta(days=7 * i)).isoformat(),
            "employee_id": "e1",
        }
        for i in range(weeks)
    ]


def _cards(clins, rows=None):
    contract = {"id": 1, "contract": _header(), "clins": clins, "periods": [_PERIOD]}
    payload = burn.compute(contract, rows or _rows(), cost_model=_model())
    return {c["id"]: c for c in payload["clins"]}, payload


def test_the_card_reports_a_fee_position_built_from_the_header():
    """End to end: the figures the review screen confirmed now move a CLIN card, and the
    card says they came from the header."""
    card = _cards([_labor()])[0]["0001"]
    fee = card["fee_position"]

    assert fee["known"] is True
    assert fee["terms_known"] is True
    assert fee["target"] == _HEADER_FEE
    assert fee["header_derived"] is True
    # The forecast is the same terms at projected cost, so it is derived the same way.
    assert fee["projected"]["header_derived"] is True


def test_the_card_reports_the_allocation_gap_when_the_award_is_ambiguous():
    cards, payload = _cards(
        [_labor("0001"), _labor("0002")], _rows() + _rows(charge_code="0002")
    )

    for clin in ("0001", "0002"):
        fee = cards[clin]["fee_position"]
        assert fee["known"] is False
        assert "clin_allocation" in fee["missing"]
        # No half of the total leaked onto either line, in any field the card reports.
        assert fee["target"] is None
        assert fee["earned"] == 0.0
        assert fee["at_completion"] == 0.0
        assert fee["header_derived"] is False
    # Both lines are typed reads — the gap is the unstated split, not a failed type.
    assert payload["contract"]["pricing_unknown"] == 0
