"""A partially-obligated CLIN should not talk about funding for its whole life.

Outrunning the *current* funded slice is what incremental funding is: a CLIN 64%
obligated at 40% elapsed projects to ~1.5x its funded slice while landing dead on
its ceiling. Banding on the slice alone therefore put a permanent amber "Funding
due" on ideally-executing contracts. Funding is only mentioned inside the
`_FUNDING_DUE_DAYS` horizon; outside it the CLIN is judged on its ceiling.
"""

from app import burn


# ---- _forward_band: shared banding, used for the slice and the ceiling -----


def test_forward_band_bands_against_the_finish_line():
    assert burn._forward_band(30, 52) == "over"  # dry well before PoP end
    assert burn._forward_band(52, 52) == "watch"  # lands right on the wire
    assert burn._forward_band(56, 52) == "ok"  # a little past, comfortable
    assert burn._forward_band(70, 52) == "under"  # won't consume it in time
    assert burn._forward_band(None, 52) == "ok"  # nothing to project


# ---- _funded_shortfall_status ----------------------------------------------
#
# Args: (runway_days, ceiling_exhaust, total_weeks, incrementally_funded,
#        ceiling_breached, mod_in_progress, funding_keeps_pace, funds_exceeded)


def test_funds_already_spent_through_is_red_not_amber():
    # runway_days floors at 0 once spend passes the funded slice, so this used to
    # satisfy `0 <= 60` and read amber "Funding due" — the same pill as a CLIN with
    # two months of runway left. The softening is forward-looking; once the money is
    # gone the notice under FAR 52.232-22(c) is already overdue and the cost is at
    # risk under (d)/(f), so it stays red however well funding is tracking.
    assert (
        burn._funded_shortfall_status(0, 56.0, 52, True, False, True, True, True)
        == "over"
    )
    # Same CLIN a dollar short of the slice: still the amber heads-up.
    assert (
        burn._funded_shortfall_status(0, 56.0, 52, True, False, True, True, False)
        == "funding"
    )


def test_not_incrementally_funded_is_plain_red():
    # The budget *is* the ceiling, so a shortfall is a ceiling problem. No
    # funding softening applies and the horizon is irrelevant.
    assert (
        burn._funded_shortfall_status(200, 30.0, 52, False, True, False, True) == "over"
    )


def test_ceiling_breach_stays_red_even_inside_the_horizon():
    # Funds gone in a week AND projected spend blows the real ceiling: the
    # ceiling breach dominates, so this must not soften to amber.
    assert burn._funded_shortfall_status(7, 30.0, 52, True, True, False, True) == "over"


def test_funding_lagging_with_no_mod_stays_red():
    # Obligations genuinely behind the burn and nothing flagged as moving. This
    # is the "projected to highly outrun the funding" case — it stays red.
    assert (
        burn._funded_shortfall_status(200, 56.0, 52, True, False, False, False)
        == "over"
    )


def test_amber_funding_inside_the_horizon():
    # Funded slice runs dry in 21 days, ceiling holds, funding keeping pace →
    # this is the actionable moment, so it says so.
    assert (
        burn._funded_shortfall_status(21, 56.0, 52, True, False, False, True)
        == "funding"
    )


def test_amber_funding_exactly_on_the_horizon():
    assert (
        burn._funded_shortfall_status(
            burn._FUNDING_DUE_DAYS, 56.0, 52, True, False, False, True
        )
        == "funding"
    )


def test_horizon_is_the_far_60_day_lookahead():
    # FAR 52.232-22(c) obliges written notice to the CO about costs expected in
    # the next 60 days. Inside that window the PM already owes someone an action,
    # so that's where the pill starts talking.
    assert burn._FUNDING_DUE_DAYS == 60
    assert (
        burn._funded_shortfall_status(60, 56.0, 52, True, False, False, True)
        == "funding"
    )
    assert burn._funded_shortfall_status(61, 56.0, 52, True, False, False, True) == "ok"


def test_two_months_of_funded_runway_is_a_funding_matter():
    # Real shape from contract 9: ~50-56 days of funded runway on a CLIN that is
    # otherwise projected to land essentially on its ceiling. Under a 30-day gate
    # this read purely as a ceiling story; at 60 days it's close enough to the
    # money running out that funding is the more useful thing to say.
    assert (
        burn._funded_shortfall_status(56, 55.7, 52, True, False, False, True)
        == "funding"
    )
    assert (
        burn._funded_shortfall_status(50, 51.2, 52, True, False, False, True)
        == "funding"
    )


def test_outside_the_horizon_reports_the_ceiling_instead():
    # The regression this test file exists for. 97 days of funded runway, ceiling
    # projected to land comfortably → "ok", not a permanent amber "Funding due".
    assert burn._funded_shortfall_status(97, 54.4, 52, True, False, False, True) == "ok"
    # Same shape, but the ceiling itself lands on the wire → the honest read is
    # "watch" about the *ceiling*, still not a funding message.
    assert (
        burn._funded_shortfall_status(92, 51.8, 52, True, False, False, True) == "watch"
    )
    # And an under-burn outside the horizon reads as an under-burn.
    assert (
        burn._funded_shortfall_status(200, 70.0, 52, True, False, False, True)
        == "under"
    )


def test_mod_in_progress_still_needs_the_horizon():
    # A flagged mod keeps it out of red, but doesn't make a months-away shortfall
    # worth an amber pill either.
    assert (
        burn._funded_shortfall_status(180, 56.0, 52, True, False, True, False) == "ok"
    )
    assert (
        burn._funded_shortfall_status(10, 56.0, 52, True, False, True, False)
        == "funding"
    )


def test_no_runway_figure_falls_back_to_the_ceiling():
    assert (
        burn._funded_shortfall_status(None, 56.0, 52, True, False, False, True) == "ok"
    )


# ---- _pill: the label names the limit actually in jeopardy -----------------


def test_pill_names_the_limit():
    assert burn._pill("over", ceiling_breached=True) == "Over ceiling"
    assert burn._pill("over", ceiling_breached=False) == "Funds short"
    assert burn._pill("over") == "Over ceiling"  # default for callers with no slice
    assert burn._pill("funding") == "Funding due"


def test_pill_distinguishes_spent_funding_from_a_forecast_shortfall():
    # "Funds short" is a forecast; "Funds exceeded" already happened. Both are red
    # and both are about the funded slice, so one label for the pair loses the only
    # thing a PM acts on differently.
    assert (
        burn._pill("over", ceiling_breached=False, funds_exceeded=True)
        == "Funds exceeded"
    )
    assert (
        burn._pill("over", ceiling_breached=True, funds_exceeded=False)
        == "Over ceiling"
    )
    # Both at once is real — a CLIN can be past its obligated funding today *and*
    # projected to blow the ceiling later (live: contract 5 CLIN 2001). Realized
    # beats forecast, so the funding wording wins. A *realized* ceiling breach
    # still outranks it, but _funds_exceeded returns False in that case so the
    # ordering never has to be re-decided here.
    assert (
        burn._pill("over", ceiling_breached=True, funds_exceeded=True)
        == "Funds exceeded"
    )
    # Only `over` is ambiguous; the amber states are unaffected.
    assert burn._pill("funding", ceiling_breached=False, funds_exceeded=True) == (
        "Funding due"
    )
