"""#41 — non-labor CLINs (travel / ODC / materials / subs) consume the funded
allocation and must be measured against it, not the raw ceiling. A CLIN past its
obligated funding is a real Limitation of Funds problem even while it sits under
the ceiling, so it must band on the binding budget and roll into the tripwire
lists the same way labor does.
"""

from app import burn


# ---- _nl_status: the banding logic, as a pure function ---------------------
#
# Args: (spent, budget, ceiling, incrementally_funded, past_pop,
#        funding_keeps_pace, mod_in_progress). `budget` is the funded slice when
# incrementally funded, else the ceiling.


def test_nl_status_nothing_logged_is_tracked():
    assert burn._nl_status(0, 150_000, 232_000, True, False, True, False) == "tracked"


def test_nl_status_full_funding_keeps_old_ceiling_bands():
    # Not incrementally funded → budget == ceiling → old behavior byte-for-byte.
    assert burn._nl_status(100_000, 232_000, 232_000, False, False, True, False) == "ok"
    assert (
        burn._nl_status(200_000, 232_000, 232_000, False, False, True, False) == "watch"
    )
    assert (
        burn._nl_status(232_000, 232_000, 232_000, False, False, True, False) == "over"
    )


def test_nl_status_over_funded_slice_softens_when_live_and_on_pace():
    # Past the $150k funded slice, under the $232k ceiling, still live, funding
    # keeping pace → amber "funding", not red.
    assert (
        burn._nl_status(200_000, 150_000, 232_000, True, False, True, False)
        == "funding"
    )


def test_nl_status_over_funded_slice_softens_when_mod_flagged():
    # Funding lagging, but a mod is flagged outstanding → still amber.
    assert (
        burn._nl_status(200_000, 150_000, 232_000, True, False, False, True)
        == "funding"
    )


def test_nl_status_over_funded_slice_is_red_when_lagging_no_mod():
    # Past the funded slice, funding genuinely lagging, no mod → red over.
    assert (
        burn._nl_status(200_000, 150_000, 232_000, True, False, False, False) == "over"
    )


def test_nl_status_past_pop_does_not_soften():
    # Period is over — no next tranche is coming, so past the funded slice is red
    # even if the pace proxy still reads "keeping pace".
    assert burn._nl_status(200_000, 150_000, 232_000, True, True, True, False) == "over"


def test_nl_status_over_ceiling_is_always_red():
    # A realized breach of the actual ceiling is red regardless of funding state.
    assert burn._nl_status(240_000, 150_000, 232_000, True, False, True, True) == "over"


def test_nl_status_watch_band_is_on_the_binding_budget():
    # 0.8 * funded slice ($120k) trips watch even though it's only ~56% of ceiling.
    assert (
        burn._nl_status(130_000, 150_000, 232_000, True, False, True, False) == "watch"
    )
    assert burn._nl_status(100_000, 150_000, 232_000, True, False, True, False) == "ok"


# ---- compute(): the fields and tripwire rollup -----------------------------

_PERIOD = {"name": "Base", "pop_start": "2026-01-01", "pop_end": "2026-12-31"}


def _contract(obligated, ceiling=232_000, mod=False):
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-41",
            "total_ceiling": ceiling,
            "total_obligated": obligated,
            "mod_in_progress": mod,
        },
        # Single non-labor CLIN, unlabeled → period fallback keeps it in scope.
        "clins": [
            {
                "clin": "0004",
                "title": "Travel & ODC",
                "is_labor": False,
                "ceiling": ceiling,
            }
        ],
        "periods": [_PERIOD],
    }


def _expenses(amount):
    return [{"clin": "0004", "amount": amount}]


def _nl(payload):
    return next(c for c in payload["clins"] if c["id"] == "0004")


def test_incrementally_funded_over_slice_reads_funding_and_rolls_up():
    # Obligated $150k of a $232k ceiling; $200k logged → past the funded slice,
    # under the ceiling. Mod flagged so the amber softening is deterministic.
    p = burn.compute(_contract(150_000, mod=True), [], _expenses(200_000))
    clin = _nl(p)

    assert clin["incrementally_funded"] is True
    assert clin["funded"] == 150_000.0
    assert clin["budget"] == 150_000.0
    assert clin["limited_by"] == "funding"
    assert clin["status"] == "funding"
    # remaining/overspent are on the binding budget, not the ceiling.
    assert clin["remaining"] == -50_000.0
    assert clin["overspent"] == 50_000.0
    # No forward pace for a non-labor CLIN.
    assert clin["exhaust_week"] is None
    assert clin["runway_days"] is None

    # Rolls into the amber funding list, not the red tripwire list.
    assert [f["code"] for f in p["funding"]] == ["CLIN 0004"]
    assert p["tripwires"] == []
    assert p["all_clear"] is False


def test_over_ceiling_rolls_into_red_tripwires():
    # $250k logged is past the $232k ceiling → red, regardless of funding.
    p = burn.compute(_contract(150_000, mod=True), [], _expenses(250_000))
    clin = _nl(p)

    assert clin["status"] == "over"
    assert [t["code"] for t in p["tripwires"]] == ["CLIN 0004"]
    # A realized read has no runway to report on the tripwire.
    assert p["tripwires"][0]["exhaust_week"] is None
    assert p["funding"] == []


def test_fully_funded_non_labor_keeps_ceiling_behavior():
    # No obligation gap (obligated covers the ceiling) → budget is the ceiling,
    # bands are the old ceiling bands. $200k of $232k → watch, not a tripwire.
    p = burn.compute(_contract(None), [], _expenses(200_000))
    clin = _nl(p)

    assert clin["incrementally_funded"] is False
    assert clin["funded"] is None
    assert clin["budget"] == 232_000.0
    assert clin["limited_by"] == "ceiling"
    assert clin["status"] == "watch"
    assert clin["remaining"] == 32_000.0
    assert p["tripwires"] == []
    assert p["funding"] == []
