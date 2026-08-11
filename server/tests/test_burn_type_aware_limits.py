"""#81 part 1 — the policy picks the funding clause, and the payload carries it.

`pricing.funding_clause_for` has existed since #76 and was called by nothing: every
consumer either assumed FAR 52.232-22 or said nothing at all. `drafts.js` is the one
that matters — it hardcodes -22 into the reference line and the body of a letter
addressed to a contracting officer, and a wrong clause number on an outgoing letter
is worse than no letter.

These tests pin the resolved clause at CLIN level and on every list a banner or a
letter is built from. What they deliberately do *not* touch is a dollar: the clause
is a citation, not a threshold, so no status, runway, budget or tripwire moves here.
Parts 2-5 of #81 are where behaviour changes.
"""

from app import burn

_PERIOD = {"name": "Base", "pop_start": "2026-01-01", "pop_end": "2026-12-31"}
_CEILING = 1_000_000


def _contract(clin_type=None, header_type=None, obligated=500_000, **clin):
    """One labor CLIN, $1M ceiling, funded to half by default so it is incrementally
    funded — the -22 case. Pass `obligated=_CEILING` for the fully funded -20 case."""
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-81",
            "total_ceiling": _CEILING,
            "total_obligated": obligated,
            "contract_type": header_type,
        },
        "clins": [
            {
                "clin": "0001",
                "period": "Base",
                "title": "Professional Services (Labor)",
                "is_labor": True,
                "ceiling": _CEILING,
                "est_hours": 4_000,
                "type": clin_type,
                **clin,
            }
        ],
        "periods": [_PERIOD],
    }


def _rows(weeks=20, hours=40):
    return [
        {
            "charge_code": "0001",
            "labor_category": "Software Engineer",
            "total_hours": hours,
            "week_ending": "2026-01-02",
            "employee_id": "e1",
        }
        for _ in range(weeks)
    ]


def _clause(payload):
    return payload["clins"][0]["funding_clause"]


# ── Which clause, and why ────────────────────────────────────────────────────────


def test_incrementally_funded_cost_contract_cites_limitation_of_funds():
    # Money arriving in tranches: FAR 52.232-22 governs, which is the case the whole
    # app assumed universally.
    assert _clause(burn.compute(_contract(header_type="CPFF"), _rows())) == "52.232-22"


def test_fully_funded_cost_contract_cites_limitation_of_cost():
    # Same type, same everything except that the award obligated the full ceiling.
    # -22 does not apply to a contract with nothing left to allot; -20 does. This is
    # the citation the pre-#81 engine got wrong on every fully funded cost contract.
    p = burn.compute(_contract(header_type="CPFF", obligated=_CEILING), _rows())
    assert _clause(p) == "52.232-20"


def test_time_and_materials_cites_the_payments_clause_either_way():
    # T&M's limit is the ceiling price (FAR 16.601(c)(1)), not an allotment, so the
    # funded/fully-funded split that picks between -20 and -22 does not apply to it.
    # Both directions pinned, because "it happened to be right once" is how the -22
    # assumption survived this long.
    incremental = burn.compute(_contract(header_type="T&M"), _rows())
    full = burn.compute(_contract(header_type="T&M", obligated=_CEILING), _rows())
    assert _clause(incremental) == _clause(full) == "52.232-7"


def test_fixed_price_cites_nothing():
    # No limitation-of-funds mechanic exists on FFP, so there is no clause to cite and
    # None is the honest answer. A consumer that treats a missing clause as -22 would
    # reintroduce exactly the false alarm #79 removed.
    assert _clause(burn.compute(_contract(header_type="FFP"), _rows())) is None


def test_unreadable_type_keeps_the_legacy_assumption_and_flags_it():
    # An award whose type never resolved keeps the legacy funding read, and its clause
    # keeps the legacy citation with it: `UNKNOWN.funding_clauses` is deliberately the
    # single-entry `(-22,)`, so the fully-funded/-20 split doesn't apply and the letter
    # #25 has always sent still has a clause to cite.
    #
    # That citation is an *assumption*, not a read, and the only thing that makes it
    # safe is that `pricing_policy.known` says so on the same payload. Any surface that
    # prints the clause has to print that caveat too — which is why part 3 gates the
    # letter's clause line on this flag rather than trusting the string alone.
    p = burn.compute(_contract(header_type="Blanket Purchase Agreement"), _rows())
    assert p["clins"][0]["pricing_policy"]["known"] is False
    assert _clause(p) == "52.232-22"


def test_clin_type_beats_the_header_for_the_clause_too():
    # Mixed awards are normal and `CLIN.type` is why. A T&M surge CLIN on a CPFF award
    # is governed by -7, and resolving the clause off the header would cite -22 at it.
    p = burn.compute(_contract(clin_type="T&M", header_type="CPFF"), _rows())
    assert _clause(p) == "52.232-7"


# ── Every surface a citation can reach ──────────────────────────────────────────


def test_the_clause_rides_the_lists_a_letter_is_built_from():
    # The amber funding list is #25's letter source and the red tripwire list is the
    # banner's. A row that reaches either without its clause sends the caller back to
    # assuming one.
    p = burn.compute(_contract(header_type="CPFF"), _rows(weeks=40))
    rows = p["tripwires"] + p["funding"]
    assert rows, "expected this CLIN to raise something to carry the clause on"
    assert all(r["funding_clause"] == "52.232-22" for r in rows)


def test_the_hero_carries_the_clause_for_the_limit_it_names():
    # The hero tile already says *which* limit binds (`limited_by`); this is the
    # clause behind that limit, so the tile and any copy under it can agree.
    p = burn.compute(_contract(header_type="CPFF"), _rows())
    assert p["hero"]["limited_by"] == "funding"
    assert p["hero"]["funding_clause"] == "52.232-22"


def test_non_labor_clin_resolves_its_own_clause():
    # A travel/ODC CLIN is measured in cost dollars whatever its type says, so it
    # keeps a funding read — but the clause still comes from its own policy.
    c = {
        "id": 1,
        "contract": {
            "piid": "TEST-81-NL",
            "total_ceiling": 100_000,
            "total_obligated": 40_000,
            "contract_type": "CPFF",
        },
        "clins": [
            {
                "clin": "0002",
                "period": "Base",
                "title": "Travel",
                "is_labor": False,
                "ceiling": 100_000,
            }
        ],
        "periods": [_PERIOD],
    }
    p = burn.compute(c, [], expenses=[{"clin": "0002", "amount": 30_000}])
    assert p["clins"][0]["funding_clause"] == "52.232-22"


# ── The invariant that makes this commit safe ───────────────────────────────────


def test_adding_the_clause_moves_no_money():
    # The clause is a lookup, not a threshold. Two contracts identical but for the
    # type text must produce identical funding arithmetic, because parts 2-5 of #81
    # have not landed yet and #134 owns the denominator question. If this ever fails,
    # a citation has started driving a number.
    typed = burn.compute(_contract(header_type="CPFF"), _rows(weeks=40))["clins"][0]
    untyped = burn.compute(_contract(), _rows(weeks=40))["clins"][0]
    for key in (
        "budget",
        "spent",
        "remaining",
        "runway_days",
        "exhaust_week",
        "status",
        "stop_date",
        "funds_exceeded",
    ):
        assert typed[key] == untyped[key], key


# ── Part 4: the cost overrun eats fee before it eats funding ─────────────────────
#
# The rule that makes cost-plus different from everything else Runway models. The
# obligated dollars cover cost *and* fee, so spending past estimated cost does not
# breach the funded limit while fee remains — it consumes the fee. That state was
# invisible on the card: a CPFF CLIN could read "On pace" in green with a third of its
# fixed fee already spoken for by the overrun.
#
# `fee_eroding` is a *label*, deliberately. No denominator moves here — #134 owns
# whether earned fee nets out of funded availability — and the last test in this block
# is the one that holds that line.

from app import rates

# Level 2, so `cost` is a real buildup and not a billing stand-in. $40 direct burdens
# to $85.75/hr (x1.32 fringe, x1.45 OH, x1.12 G&A), which is the quantity the fee rule
# is evaluated against. At Level 1 cost equals billings by construction and already
# contains the fee, so "cost passed estimated cost" there is an artefact of the rate
# ladder — `test_level_1_cost_cannot_erode_a_fee` pins that it stays silent.
_COST_PER_HOUR = 85.75


def _model():
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


# 12 weeks at 40 hrs — $41,160 of cost to date, $3,430/wk forward, 40 weeks to go, so
# cost lands at $178,360 against a $150K estimate: a $28,360 overrun that eats $28,360
# of a $40K fixed fee and leaves $11,640. The ceiling is $190K (estimate + fee), which
# that projection still clears, so the funding read is `ok` and the *only* thing wrong
# with this CLIN is the fee — exactly the case the state exists for.
_EST_COST = 150_000
_FIXED_FEE = 40_000
_FEE_CEILING = _EST_COST + _FIXED_FEE


def _weeks(n=12, hours=40):
    from datetime import date, timedelta

    start = date(2026, 1, 2)
    return [
        {
            "charge_code": "0001",
            "labor_category": "Software Engineer",
            "total_hours": hours,
            "week_ending": (start + timedelta(weeks=i)).isoformat(),
            "employee_id": "e1",
        }
        for i in range(n)
    ]


def _fee_contract(clin_type="CPFF", ceiling=_FEE_CEILING, **fee):
    """A fully funded cost CLIN whose ceiling is estimated cost + fee, priced so the
    forward projection overruns the estimate and still clears the ceiling."""
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-81-FEE",
            "total_ceiling": ceiling,
            "total_obligated": ceiling,
            "contract_type": clin_type,
        },
        "clins": [
            {
                "clin": "0001",
                "period": "Base",
                "title": "Professional Services (Labor)",
                "is_labor": True,
                "ceiling": ceiling,
                "est_hours": 1_900,
                **fee,
            }
        ],
        "periods": [_PERIOD],
    }


def _fee_card(**kw):
    contract = _fee_contract(**kw)
    return burn.compute(contract, _weeks(), cost_model=_model())["clins"][0]


def test_the_overrun_reads_fee_eroding_not_a_funding_breach():
    c = _fee_card(estimated_cost=_EST_COST, fixed_fee=_FIXED_FEE)

    # The whole point: nothing about the *funding* is wrong here.
    assert c["funds_exceeded"] is False
    assert c["ceiling_breached"] is False
    # And yet this is not "On pace" — which is what it read before #81.
    assert c["status"] == "fee_eroding"
    assert c["status_label"] == "Fee eroding"


def test_fee_eroding_states_the_fee_that_is_left():
    # "$11,640 of the $40K fixed fee remains" is the actionable half of the state; a
    # colour without the number is not a finding.
    c = _fee_card(estimated_cost=_EST_COST, fixed_fee=_FIXED_FEE)
    projected = c["fee_position"]["projected"]

    # $178,354.18 of projected cost against the $150K estimate. The odd cents are the
    # burden chain ($40 x 1.32 x 1.45 x 1.12 = $85.7472/hr), carried rather than rounded
    # away so the figures here foot to the same buildup #77 reports.
    assert projected["overrun"] == 28_354.18
    assert projected["absorbed"] == 28_354.18
    assert projected["at_completion"] == 11_645.82
    assert projected["exhausted"] is False
    assert c["fee_exhausted"] is False


def test_the_card_and_the_fee_alert_never_disagree():
    # Both are driven off `absorbed` on the same projected position, on purpose. A CLIN
    # whose pill says the fee is going while the alert list is empty (or the reverse) is
    # the failure this shares one derivation to prevent.
    p = burn.compute(
        _fee_contract(estimated_cost=_EST_COST, fixed_fee=_FIXED_FEE),
        _weeks(),
        cost_model=_model(),
    )
    assert p["clins"][0]["status"] == "fee_eroding"
    assert [a["code"] for a in p["fee_alerts"]] == ["CLIN 0001"]
    assert p["fee_alerts"][0]["fee_lost"] == 28_354.18
    # #80 already gated `all_clear` on the alert, so this was never "all clear" — but a
    # reader who only looked at the pill could not tell.
    assert p["all_clear"] is False


def test_no_overrun_is_not_an_alert():
    # A CLIN inside its estimated cost has absorbed nothing, so the state must not fire
    # merely because a fee exists. Same fixture with an estimate the projection clears —
    # which leaves the forward band's own read of a CLIN landing well short of its
    # budget (`under`), untouched by the fee.
    c = _fee_card(estimated_cost=300_000, fixed_fee=_FIXED_FEE, ceiling=340_000)
    assert c["fee_position"]["projected"]["absorbed"] == 0.0
    assert c["status"] == "under"


def test_award_fee_at_risk_is_not_fee_erosion():
    # CPAF's undetermined award pool sits below target from day one on every healthy
    # CPAF contract. `_award_fee_position` never sets `absorbed` for exactly that
    # reason, and this pins that the status inherits that discipline rather than
    # painting amber on the normal state of the type (#80).
    c = _fee_card(
        clin_type="CPAF",
        estimated_cost=_EST_COST,
        base_fee=5_000,
        award_fee_pool=35_000,
    )
    assert c["fee_position"]["projected"]["at_risk"] > 0
    assert c["fee_position"]["projected"]["absorbed"] == 0.0
    assert c["status"] == "ok"


def test_level_1_cost_cannot_erode_a_fee():
    # No cost model: cost is hours x the negotiated billing rate, which already contains
    # the fee. Comparing that to estimated cost is comparing a number to a component of
    # itself, and the fee position says so with `cost_known: False`.
    c = burn.compute(
        _fee_contract(estimated_cost=_EST_COST, fixed_fee=_FIXED_FEE), _weeks()
    )["clins"][0]
    assert c["cost_known"] is False
    assert c["fee_position"]["known"] is False
    assert c["status"] != "fee_eroding"


def test_the_projected_position_carries_its_own_truth_flags():
    # #153. The projection is the same fee terms applied to projected cost, so it is
    # exactly as trustworthy as the current position — but it used to ship without
    # saying so, and a surface reading a missing flag as "known" printed at-completion
    # fee, the delta against target and the absorbed fee as facts off a billing-rate
    # stand-in. Level 1 is where that shows, because everything else about the position
    # looks ordinary.
    c = burn.compute(
        _fee_contract(estimated_cost=_EST_COST, fixed_fee=_FIXED_FEE), _weeks()
    )["clins"][0]
    projected = c["fee_position"]["projected"]
    assert projected["cost_known"] is False
    assert projected["terms_known"] is True, "the award printed the fee figures"
    assert projected["known"] is False
    # A priced contract says the opposite on the same three keys, so a consumer can
    # never read one state as the other's absence.
    priced = _fee_card(estimated_cost=_EST_COST, fixed_fee=_FIXED_FEE)["fee_position"]
    assert priced["projected"]["known"] is True
    assert priced["projected"]["cost_known"] is True


def test_a_red_is_never_downgraded_to_fee_eroding():
    # Precedence, and the reason the refinement is scoped to `ok`/`watch`. Where the
    # ceiling is exactly estimated cost + fee, exhausting the fee *is* a ceiling breach
    # by construction — cost past est + fee is cost past the ceiling — so the ceiling
    # read wins and keeps its red. "The fee is gone" must never read amber on a CLIN
    # that is also blowing its ceiling.
    c = _fee_card(estimated_cost=_EST_COST, fixed_fee=10_000, ceiling=160_000)
    assert c["fee_position"]["projected"]["exhausted"] is True
    assert c["ceiling_breached"] is True
    assert c["status"] == "over"
    assert c["status_label"] == "Over ceiling"


def test_the_exhausted_label_exists_for_the_case_that_can_reach_it():
    # `_pill` at the unit level, because a fully funded CLIN whose ceiling is estimate
    # plus fee can't reach it (see above) — it becomes reachable once the ceiling
    # carries headroom over est + fee, or funding softening holds the status at `watch`.
    # "Fee eroding" on a CLIN with no fee left understates it by the amount that
    # matters, so the label exists and stays amber either way.
    assert burn._pill("fee_eroding") == "Fee eroding"
    assert burn._pill("fee_eroding", fee_exhausted=True) == "Fee exhausted"


def test_fixed_price_keeps_its_margin_vocabulary():
    # FFP has no fee mechanic to erode — profit is price minus cost, reported as
    # `margin_position` and labelled "Margin at risk"/"Margin exceeded" since #79. A
    # cost-type state leaking onto it would undo that ticket.
    c = _fee_card(clin_type="FFP", estimated_cost=_EST_COST, fixed_fee=_FIXED_FEE)
    assert c["margin_managed"] is True
    assert c["status"] != "fee_eroding"
    assert c["fee_position"] is None


def test_fee_erosion_moves_no_funding_figure():
    # The line this commit must not cross. Two identical CLINs — same ceiling, same
    # obligation, same hours, same cost model — differing only in whether the award
    # printed fee terms. The status differs, and nothing denominated in dollars does.
    # #134 is where the funded slice may start netting fee out; until then a fee state
    # that moved a runway would be that ticket landing by accident.
    eroding = _fee_card(estimated_cost=_EST_COST, fixed_fee=_FIXED_FEE)
    no_terms = _fee_card()

    assert eroding["status"] == "fee_eroding"
    assert no_terms["status"] == "ok"
    for key in (
        "budget",
        "spent",
        "cost",
        "remaining",
        "weekly",
        "weeks_left",
        "exhaust_week",
        "runway_days",
        "stop_date",
        "stop_date_passed",
        "funds_exceeded",
        "ceiling_breached",
        "pct",
        "pct_budget",
    ):
        assert eroding[key] == no_terms[key], key


# ── Part 5: the T&M ceiling price is its own limit ───────────────────────────────
#
# `funding_tripwire == "at_ceiling"` has been on the TM policy since #76 and was read
# by nothing — only `== "none"` (fixed price) was ever consulted. It declares that the
# reportable limit on this type is the **ceiling price**: FAR 16.601(c)(1)'s
# not-to-exceed, governed by 52.232-7, whose remedy is a ceiling increase.
#
# Read narrowly on purpose. It does *not* mean the funded slice stops mattering — an
# incrementally funded T&M CLIN cannot bill dollars nobody obligated, so the obligation
# is still what runs out first and still what the runway measures against. What it means
# is that a T&M *ceiling* breach is a different event from a cost-type one, and until now
# the two were indistinguishable in the payload.


def _tm_card(obligated=_CEILING, weeks=40):
    p = burn.compute(
        _contract(header_type="T&M", obligated=obligated), _rows(weeks=weeks)
    )
    return p["clins"][0], p


def test_a_tm_ceiling_breach_names_the_ceiling_price():
    # Fully funded, so the ceiling is the terminal limit and the breach is a ceiling
    # breach. Before #81 this said "Over ceiling" — true, but indistinguishable from a
    # cost-type CLIN blowing estimated cost plus fee, which needs a different remedy
    # from a different person.
    c, _ = _tm_card()
    assert c["ceiling_is_price"] is True
    assert c["stop_reason"] == "ceiling_price"
    assert c["status_label"] == "Over ceiling price"
    assert c["funding_clause"] == "52.232-7"


def test_the_cost_type_ceiling_keeps_its_own_wording():
    # The control. A cost-type ceiling is estimated cost plus fee and stays "ceiling",
    # so the new value can only ever appear where the ceiling really is a price.
    c = burn.compute(
        _contract(header_type="CPFF", obligated=_CEILING), _rows(weeks=40)
    )["clins"][0]
    assert c["ceiling_is_price"] is False
    assert c["stop_reason"] == "ceiling"
    assert c["status_label"] == "Over ceiling"


def test_an_incrementally_funded_tm_clin_still_runs_out_of_money_first():
    # The line part 5 does not cross. `at_ceiling` is about which limit gets *named*,
    # not about pretending obligated dollars are unlimited: the funded slice can never
    # exceed the ceiling, so it is still what runs dry first and still what the runway
    # is measured against. Reading `at_ceiling` as "measure T&M against the ceiling"
    # would hand every incrementally funded T&M contract a longer runway than it has
    # money for — the exact class of falsehood #79 existed to remove.
    c, _ = _tm_card(obligated=500_000)
    assert c["ceiling_is_price"] is True
    assert c["incrementally_funded"] is True
    assert c["budget"] == 500_000.0
    assert c["stop_reason"] == "funding"


def test_the_tripwire_row_and_the_hero_carry_the_ceiling_price_limit():
    # The tripwire row is what the banner copy switches on, and the hero is what the
    # tile does. A row that says `ceiling` sends the reader after a mod raising an
    # estimate that this contract does not have.
    c, p = _tm_card()
    assert c["status"] == "over"
    assert [t["limited_by"] for t in p["tripwires"]] == ["ceiling_price"]
    assert p["tripwires"][0]["funding_clause"] == "52.232-7"
    assert p["hero"]["limited_by"] == "ceiling_price"


def test_stop_reason_matches_the_limit_that_produces_the_date():
    # `stop_reason` on the card and `limited_by` on the row are one derivation, so the
    # hard-stop phrase and the banner can never name different limits for the same date.
    c, p = _tm_card()
    assert c["stop_reason"] == p["tripwires"][0]["limited_by"] == "ceiling_price"


def test_the_new_limit_value_moves_no_number():
    # Same bar as part 1, for the same reason: `ceiling_price` is a *name*. A T&M CLIN
    # and an untyped one are the same billings-measured read to the cent — the one thing
    # the pre-#79 engine got right — and the only differences are words.
    tm, _ = _tm_card()
    untyped = burn.compute(_contract(obligated=_CEILING), _rows(weeks=40))["clins"][0]
    assert tm["stop_reason"] != untyped["stop_reason"]
    assert tm["status"] == untyped["status"]
    for key in (
        "budget",
        "spent",
        "billings",
        "remaining",
        "weekly",
        "weeks_left",
        "exhaust_week",
        "runway_days",
        "stop_date",
        "pct",
        "pct_budget",
        "funds_exceeded",
        "ceiling_breached",
    ):
        assert tm[key] == untyped[key], key


def test_fixed_price_never_reaches_the_ceiling_price_vocabulary():
    # FFP's ceiling is a firm price it is *owed*, not a limit it may not pass. Its
    # policy declares `funding_tripwire: "none"`, so `ceiling_is_price` is False and the
    # margin vocabulary from #79 stands — "Margin exceeded", not "Over ceiling price".
    c = burn.compute(_contract(header_type="FFP", obligated=_CEILING), _rows(weeks=40))[
        "clins"
    ][0]
    assert c["ceiling_is_price"] is False
    assert c["margin_managed"] is True
    assert c["status_label"] == "Margin exceeded"
