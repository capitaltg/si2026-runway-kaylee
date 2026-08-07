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


# ---- _ceiling_band: the pace tolerance on the real ceiling ------------------
#
# Args: (ceiling_exhaust, current_week, total_weeks). `_forward_band`'s flat
# one-week edge is wrong for the ceiling: the projection extrapolates a four-week
# trailing average across the whole remaining PoP, so on a year-long CLIN it carries
# months of leverage on a one-month sample. Red is gated on the pace being materially
# hotter than the ceiling affords instead of on the landing week missing at all.


def test_on_pace_clin_is_not_a_ceiling_breach():
    # THE BUG. Live contract 5 (7024HEXDVC0001043) CLIN 2001: week 12 of 52, 22.5% of
    # its $3.08M ceiling spent against 23.1% of the PoP elapsed — dead on plan. Its
    # ceiling projected to week 50.44, and `50.44 < 52 - 1` called that a breach.
    #
    # The consequences were not cosmetic. `ceiling_breached` is the gate on the
    # incremental-funding softening, so the flag turned a routine tranche shortfall
    # into a red "Over ceiling", which put the CLIN in `tripwires`, which handed
    # `suggest.solve_moves` a reduce-staffing gap sized against the *funded slice* —
    # and the Flight Deck told a PM to roll people off a contract that was on pace and
    # had $1.5M of ceiling underneath its current obligation.
    #
    # The pace is 40/38.44 - 1 = 4.1% hotter than the ceiling affords with 40 weeks to
    # go. Inside the tolerance, so it is amber — the pace really is a shade above what
    # the ceiling affords and that earns a colour — but emphatically NOT red, which is
    # the thing that produced the staffing recommendation.
    assert burn._ceiling_band(50.44, 12, 52) == "watch"
    assert burn._ceiling_band(50.44, 12, 52) != "over"


def test_material_overrun_is_still_a_breach():
    # The tolerance is not an amnesty: a CLIN burning through its ceiling at nearly
    # twice what it can afford is red, which is the case the flag exists for.
    assert burn._ceiling_band(26.0, 12, 52) == "over"
    # Just past the 10% line — 40/36.0 - 1 = 11.1% hot.
    assert burn._ceiling_band(48.0, 12, 52) == "over"


def test_the_read_stays_graded_between_silence_and_red():
    # Three bands, and `ok` means what it says: the pace is at or under what the
    # remaining budget affords. Landing exactly on the finish line is 0% hot.
    assert burn._ceiling_band(52.0, 12, 52) == "ok"
    # 40/38.5 - 1 = 3.9% — over affordable but inside the tolerance, so amber.
    assert burn._ceiling_band(50.5, 12, 52) == "watch"
    # 40/37.0 - 1 = 8.1% — past the 5% red edge.
    assert burn._ceiling_band(49.0, 12, 52) == "over"


def test_tolerance_scales_with_what_is_left_not_with_a_flat_week():
    # The same one-week miss is a rounding error across a year and a crisis across a
    # month. A flat edge could not tell those apart; a pace ratio does — the identical
    # absolute miss lands amber in one case and red in the other.
    assert burn._ceiling_band(51.0, 12, 52) == "watch"  # 1 wk shy of 52, 40 to go: 2.5%
    assert burn._ceiling_band(15.0, 12, 16) == "over"  # 1 wk shy of 16, 4 to go: 33%


def test_no_headroom_left_is_a_breach_that_already_happened():
    # Spend is at or past the ceiling, so there is no ratio to take — the projection
    # crossed behind us. Never softened.
    assert burn._ceiling_band(12.0, 12, 52) == "over"
    assert burn._ceiling_band(9.0, 12, 52) == "over"


def test_past_pop_with_headroom_intact_has_nothing_left_to_breach():
    # No remaining PoP to project into. The period is judged on realized spend
    # elsewhere (`past_pop` in _compute_clin), not on a forward line.
    assert burn._ceiling_band(60.0, 54, 52) == "ok"


def test_no_projection_is_not_a_breach():
    # No pace (paused / unpriced) means no ceiling_exhaust. Absence of evidence must
    # not read as a breach — that would flag every CLIN with nothing charging yet.
    assert burn._ceiling_band(None, 12, 52) == "ok"


def test_ceiling_band_has_no_under_state():
    # A ceiling landing far past the finish line is scope the contract never had to
    # use, not an under-burn to chase. This is what makes the old "spend faster on a
    # CLIN 74 days from dry" bug structurally impossible rather than merely clamped.
    assert burn._ceiling_band(80.0, 12, 52) == "ok"


# ---- _funded_shortfall_status ----------------------------------------------
#
# Args: (runway_days, ceiling_band, incrementally_funded, mod_in_progress,
#        funding_keeps_pace, funds_exceeded)
#
# `ceiling_band` arrives ready-made from `_ceiling_band` above. It used to be a
# `ceiling_breached` boolean plus a re-band through `_forward_band` computed in here,
# which is where the funding read broke: that re-band judged the ceiling on the same
# flat one-week edge, so a CLIN whose ceiling was fine to within a rounding error came
# back red and the softening never got to matter.


def test_funds_already_spent_through_is_red_not_amber():
    # runway_days floors at 0 once spend passes the funded slice, so this used to
    # satisfy `0 <= 60` and read amber "Funding due" — the same pill as a CLIN with
    # two months of runway left. The softening is forward-looking; once the money is
    # gone the notice under FAR 52.232-22(c) is already overdue and the cost is at
    # risk under (d)/(f), so it stays red however well funding is tracking.
    assert burn._funded_shortfall_status(0, "ok", True, True, True, True) == "over"
    # Same CLIN a dollar short of the slice: still the amber heads-up.
    assert burn._funded_shortfall_status(0, "ok", True, True, True, False) == "funding"


def test_not_incrementally_funded_is_plain_red():
    # The budget *is* the ceiling, so a shortfall is a ceiling problem. No
    # funding softening applies and the horizon is irrelevant.
    assert burn._funded_shortfall_status(200, "over", False, False, True) == "over"


def test_terminal_budget_inside_the_tolerance_is_a_watch_not_silence():
    # A CLIN that is not incrementally funded reached this function by its *only*
    # money projecting dry. Inside the tolerance that is not an alarm, but it is not
    # silent either the way the incremental case is: there is no next tranche coming,
    # so being a few percent hot on everything the contract will ever get is worth
    # saying. This is the one place the two denominators are treated differently, and
    # the difference is replenishable vs terminal, not a threshold.
    assert burn._funded_shortfall_status(200, "watch", False, False, True) == "watch"
    assert burn._funded_shortfall_status(200, "ok", False, False, True) == "watch"


def test_ceiling_breach_stays_red_even_inside_the_horizon():
    # Funds gone in a week AND projected spend blows the real ceiling: the
    # ceiling breach dominates, so this must not soften to amber.
    assert burn._funded_shortfall_status(7, "over", True, False, True) == "over"


def test_funding_lagging_with_no_mod_stays_red():
    # Obligations genuinely behind the burn and nothing flagged as moving. This is the
    # "projected to highly outrun the funding" case — it stays red, and the comfortable
    # ceiling does not rescue it: the money to pay for that ceiling is not arriving.
    assert burn._funded_shortfall_status(200, "ok", True, False, False) == "over"


def test_amber_funding_inside_the_horizon():
    # Funded slice runs dry in 21 days, ceiling holds, funding keeping pace →
    # this is the actionable moment, so it says so.
    assert burn._funded_shortfall_status(21, "ok", True, False, True) == "funding"


def test_amber_funding_exactly_on_the_horizon():
    assert (
        burn._funded_shortfall_status(burn._FUNDING_DUE_DAYS, "ok", True, False, True)
        == "funding"
    )


def test_horizon_is_the_far_60_day_lookahead():
    # FAR 52.232-22(c) obliges written notice to the CO about costs expected in
    # the next 60 days. Inside that window the PM already owes someone an action,
    # so that's where the pill starts talking.
    assert burn._FUNDING_DUE_DAYS == 60
    assert burn._funded_shortfall_status(60, "ok", True, False, True) == "funding"
    assert burn._funded_shortfall_status(61, "ok", True, False, True) == "ok"


def test_two_months_of_funded_runway_is_a_funding_matter():
    # Real shape from contract 9: ~50-56 days of funded runway on a CLIN that is
    # otherwise projected to land essentially on its ceiling. Under a 30-day gate
    # this read purely as a ceiling story; at 60 days it's close enough to the
    # money running out that funding is the more useful thing to say.
    assert burn._funded_shortfall_status(56, "ok", True, False, True) == "funding"
    assert burn._funded_shortfall_status(50, "watch", True, False, True) == "funding"


def test_outside_the_horizon_reports_the_ceiling_instead():
    # The regression this test file exists for. 97 days of funded runway and a ceiling
    # projecting comfortably → "ok", not a permanent amber "Funding due". This is also
    # the live shape of contract 5 CLIN 2001 (99 days, ceiling band "ok"), which is
    # what the whole chain has to return to for the Flight Deck to stop recommending
    # staffing cuts on a contract that is on pace.
    assert burn._funded_shortfall_status(97, "ok", True, False, True) == "ok"
    # Same shape with the ceiling itself running warm → the honest read is "watch"
    # about the *ceiling*, still not a funding message. The band passes straight
    # through, so this surface can never disagree with the pill about the same ceiling.
    assert burn._funded_shortfall_status(92, "watch", True, False, True) == "watch"


def test_mod_in_progress_still_needs_the_horizon():
    # A flagged mod keeps it out of red, but doesn't make a months-away shortfall
    # worth an amber pill either.
    assert burn._funded_shortfall_status(180, "ok", True, True, False) == "ok"
    assert burn._funded_shortfall_status(10, "ok", True, True, False) == "funding"


def test_no_runway_figure_falls_back_to_the_ceiling():
    assert burn._funded_shortfall_status(None, "ok", True, False, True) == "ok"


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
