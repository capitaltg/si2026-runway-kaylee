"""#41 — non-labor CLINs (travel / ODC / materials / subs) consume the funded
allocation and must be measured against it, not the raw ceiling. A CLIN past its
obligated funding is a real Limitation of Funds problem even while it sits under
the ceiling, so it must band on the binding budget and roll into the tripwire
lists the same way labor does.
"""

from app import burn


# ---- _nl_status: the banding logic, as a pure function ---------------------
#
# Args: (spent, budget, ceiling, incrementally_funded). `budget` is the funded
# slice when incrementally funded, else the ceiling. No pace/mod/past_pop args:
# there is no forward projection on a non-labor line, so passing the funded slice
# means the money is already spent and the #22 softening does not apply.


def test_nl_status_nothing_logged_is_tracked():
    assert burn._nl_status(0, 150_000, 232_000, True) == "tracked"


def test_nl_status_full_funding_keeps_old_ceiling_bands():
    # Not incrementally funded → budget == ceiling → old behavior byte-for-byte.
    assert burn._nl_status(100_000, 232_000, 232_000, False) == "ok"
    assert burn._nl_status(200_000, 232_000, 232_000, False) == "watch"
    assert burn._nl_status(232_000, 232_000, 232_000, False) == "over"


def test_nl_status_over_funded_slice_is_red_however_funding_is_tracking():
    # Past the $150k funded slice, under the $232k ceiling. This is realized spend,
    # not a forecast: FAR 52.232-22's 60-day notice is owed *before* the money runs
    # out, and past it the cost is at risk. Red regardless of pace or an outstanding
    # mod — it used to read amber "Funding due", the same pill as a CLIN with two
    # months of runway and nothing overspent.
    assert burn._nl_status(200_000, 150_000, 232_000, True) == "over"


def test_nl_status_over_ceiling_is_always_red():
    # A realized breach of the actual ceiling is red too — see _funds_exceeded for
    # which of the two the pill ends up naming.
    assert burn._nl_status(240_000, 150_000, 232_000, True) == "over"


def test_nl_status_watch_band_is_on_the_binding_budget():
    # 0.8 * funded slice ($120k) trips watch even though it's only ~56% of ceiling.
    assert burn._nl_status(130_000, 150_000, 232_000, True) == "watch"
    assert burn._nl_status(100_000, 150_000, 232_000, True) == "ok"


# ---- _funds_exceeded: which limit a red `over` is actually about ------------


def test_funds_exceeded_only_when_past_funding_with_ceiling_intact():
    # Past the funded slice, ceiling holding → the funding story.
    assert burn._funds_exceeded(200_000, 150_000, 232_000, True) is True
    # Past the ceiling too → the ceiling is the worse breach and wins the label.
    assert burn._funds_exceeded(240_000, 150_000, 232_000, True) is False
    # Not incrementally funded: budget *is* the ceiling, so this is never a
    # funding problem no matter how far past it spend has gone.
    assert burn._funds_exceeded(240_000, 232_000, 232_000, False) is False
    # Still inside the funded slice → nothing realized yet.
    assert burn._funds_exceeded(100_000, 150_000, 232_000, True) is False


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


def test_incrementally_funded_over_slice_is_red_and_names_the_funding():
    # Obligated $150k of a $232k ceiling; $200k logged → past the funded slice,
    # under the ceiling. Mod flagged, which used to buy the amber softening.
    p = burn.compute(_contract(150_000, mod=True), [], _expenses(200_000))
    clin = _nl(p)

    assert clin["incrementally_funded"] is True
    assert clin["funded"] == 150_000.0
    assert clin["budget"] == 150_000.0
    assert clin["limited_by"] == "funding"
    # Red, because the money is already spent — but labelled for the funded slice,
    # not the ceiling it is nowhere near.
    assert clin["status"] == "over"
    assert clin["funds_exceeded"] is True
    assert clin["ceiling_breached"] is False
    assert clin["status_label"] == "Funds exceeded"
    # remaining/overspent are on the binding budget, not the ceiling.
    assert clin["remaining"] == -50_000.0
    assert clin["overspent"] == 50_000.0
    # No forward pace for a non-labor CLIN.
    assert clin["exhaust_week"] is None
    assert clin["runway_days"] is None

    # Rolls into the red tripwire list now, not the amber funding list.
    assert [t["code"] for t in p["tripwires"]] == ["CLIN 0004"]
    assert p["funding"] == []
    assert p["all_clear"] is False


def test_over_ceiling_rolls_into_red_tripwires():
    # $250k logged is past the $232k ceiling → red, and the ceiling outranks the
    # funded slice for the label even though both have been passed.
    p = burn.compute(_contract(150_000, mod=True), [], _expenses(250_000))
    clin = _nl(p)

    assert clin["status"] == "over"
    assert clin["funds_exceeded"] is False
    assert clin["status_label"] == "Over ceiling"
    assert [t["code"] for t in p["tripwires"]] == ["CLIN 0004"]
    # A realized read has no runway to report on the tripwire.
    assert p["tripwires"][0]["exhaust_week"] is None
    assert p["funding"] == []


def test_portfolio_card_names_the_same_limit_the_flight_deck_does():
    # The card's label used to come from _pill's default, so a contract whose only
    # red CLIN was a funding overrun announced "Over ceiling" on the portfolio while
    # its own Flight Deck said otherwise. It rolls up from the CLINs now.
    contract = _contract(150_000, mod=True)
    pf = burn.portfolio([(contract, [], _expenses(200_000))])
    card = pf["contracts"][0]

    assert card["status"] == "over"
    assert card["status_label"] == "Funds exceeded"

    # A real ceiling breach still outranks it and keeps the ceiling wording.
    pf = burn.portfolio([(_contract(150_000, mod=True), [], _expenses(250_000))])
    assert pf["contracts"][0]["status_label"] == "Over ceiling"


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
