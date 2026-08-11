"""#76 — contract type becomes a real per-CLIN pricing policy.

`contract_type` was extracted, confidence-scored, displayed, and then used for one
thing: a label. `CLIN.type` was never read at all. So the engine priced FFP, T&M and
CPFF identically. This is the data model that ends that, and these tests pin down
the three things it must get right:

  * **the table is the interface** — six types plus `unknown`, each answering the
    same five questions, asserted against an explicit expected table so changing any
    policy takes two deliberate edits rather than one silent one;
  * **normalise, never guess** — every spelling of a type resolves identically, and
    text that isn't a type resolves to `unknown` with a reason instead of being
    rounded to the nearest plausible policy;
  * **no number moves** — the regression bar that mattered most here, since
    `_compute_clin` didn't ask the policy anything yet. #79 is the ticket that changed
    that, so this bar now holds only for the types measured in billings against
    funding (T&M and `unknown`); see
    `test_typing_an_award_moves_no_number_on_a_billings_measured_type` for why that's
    the right remainder, and `test_cost_revenue_fee.py` for the types that do move.
"""

from app import burn, pricing

_PERIOD = {"name": "Base", "pop_start": "2026-01-01", "pop_end": "2026-12-31"}

_LABOR_CEILING = 400_000
_TRAVEL_CEILING = 100_000


# The five questions, per type, written out independently of `pricing.py` on purpose:
# this table *is* the contract the rest of epic #88 codes against, so it's asserted
# from the outside rather than derived from the thing under test.
#
#      code    ceiling meaning                  overrun bearer               revenue basis                 clauses                    tripwire
_TABLE = {
    "FFP": (
        "firm_price",
        "contractor",
        "price_milestones",
        (),
        "none",
    ),
    "TM": (
        "ceiling_price",
        "contractor_above_ceiling",
        "hours_times_rate",
        ("52.232-7",),
        "at_ceiling",
    ),
    "CPFF": (
        "cost_plus_fixed_fee",
        "contractor_fee_first",
        "cost_plus_fixed_fee",
        ("52.232-20", "52.232-22"),
        "meaningful",
    ),
    "CPIF": (
        "cost_plus_target_fee",
        "shared",
        "cost_plus_earned_fee",
        ("52.232-20", "52.232-22"),
        "meaningful",
    ),
    "CPAF": (
        "cost_plus_base_and_award_pool",
        "contractor_above_estimate",
        "cost_plus_earned_fee",
        ("52.232-20", "52.232-22"),
        "meaningful",
    ),
    "FPI": (
        "ceiling_price",
        "shared_to_ceiling",
        "cost_plus_earned_profit",
        (),
        "none",
    ),
}


def test_all_six_types_answer_all_five_questions():
    assert set(pricing.POLICIES) == set(_TABLE)
    for code, expected in _TABLE.items():
        p = pricing.POLICIES[code]
        assert (
            p.ceiling_meaning,
            p.cost_overrun_bearer,
            p.revenue_basis,
            p.funding_clauses,
            p.funding_tripwire,
        ) == expected, code
        # A typed policy is by definition a known one.
        assert p.known is True
        assert p.unknown_reason is None


def test_fixed_price_types_have_no_funding_mechanic():
    # The row the whole ticket exists for: on FFP the government owes the price
    # whether we spend more or less (FAR 16.202), so funding cannot be the
    # constraint and a funding tripwire is not a softer warning — it's a wrong one.
    for code in ("FFP", "FPI"):
        p = pricing.POLICIES[code]
        assert p.is_fixed_price is True
        assert p.funding_tripwire == "none"
        assert p.funding_clauses == ()
        assert p.funding_clause_for(incrementally_funded=True) is None
        assert p.funding_clause_for(incrementally_funded=False) is None


def test_cost_reimbursement_picks_its_clause_from_the_funding_state():
    # -22 Limitation of Funds when incrementally funded, -20 Limitation of Cost when
    # fully funded. This is what lets #25's letter stop hardcoding -22: the wrong
    # clause number in a notice to a contracting officer is worse than no notice.
    for code in ("CPFF", "CPIF", "CPAF"):
        p = pricing.POLICIES[code]
        assert p.is_cost_reimbursement is True
        assert p.funding_clause_for(incrementally_funded=True) == "52.232-22"
        assert p.funding_clause_for(incrementally_funded=False) == "52.232-20"
    # A type carrying a single clause returns it either way — T&M payments are
    # governed by 52.232-7 regardless of how the contract is funded.
    assert pricing.TM.funding_clause_for(True) == "52.232-7"
    assert pricing.TM.funding_clause_for(False) == "52.232-7"


# --- normalisation ----------------------------------------------------------------


def test_every_spelling_of_cpff_resolves_identically():
    # The acceptance criterion, verbatim: case, punctuation, a spelled-out name, a
    # trailing parenthetical qualifier and the bare "CR" an award prints when it
    # names a cost contract without naming its fee arrangement.
    for text in (
        "CPFF",
        "cpff",
        "Cost Plus Fixed Fee",
        "cost-plus-fixed-fee",
        "COST-PLUS-FIXED-FEE",
        "CPFF (Completion)",
        "cpff (completion form)",
        "  Cost Plus Fixed Fee  ",
        "CR",
        "Cost Reimbursable",
    ):
        assert pricing.normalize_type(text) == "CPFF", text


def test_the_normaliser_folds_ampersands_and_qualifiers():
    for text in (
        "T&M",
        "t&m",
        "Time & Materials",
        "Time and Materials",
        "T&M/LH",
        "LH",
    ):
        assert pricing.normalize_type(text) == "TM", text
    for text in ("FFP", "Firm Fixed Price", "firm-fixed-price", "FFP (LOE)"):
        assert pricing.normalize_type(text) == "FFP", text
    for text in ("CPIF", "Cost Plus Incentive Fee"):
        assert pricing.normalize_type(text) == "CPIF", text
    for text in ("CPAF", "Cost Plus Award Fee"):
        assert pricing.normalize_type(text) == "CPAF", text
    for text in ("FPI", "FPIF", "Fixed Price Incentive"):
        assert pricing.normalize_type(text) == "FPI", text


def test_unreadable_text_is_never_rounded_to_the_nearest_type():
    # A mixed-award string is the trap a substring match would fall into: "FFP/T&M"
    # contains both "FFP" and "T&M", and is neither. Guessing one would silently
    # apply the wrong pricing rules to every CLIN on the award.
    for text in ("FFP/T&M", "see section B", "Firm", "cost", "12345", "TBD"):
        assert pricing.normalize_type(text) is None, text
        assert pricing.classify(text)[1] == "unrecognized", text


def test_vehicles_are_not_pricing_types():
    # An IDIQ or a BPA says how work is ordered, not how it's priced — the priced
    # thing is the order underneath. Distinguished from garbage text because it's a
    # different data-quality story: the order-level type never got extracted.
    for text in (
        "IDIQ",
        "idiq",
        "Indefinite Delivery Indefinite Quantity",
        "BPA",
        "GWAC",
    ):
        assert pricing.normalize_type(text) is None, text
        assert pricing.classify(text)[1] == "vehicle", text


def test_absent_text_is_its_own_reason():
    for text in (None, "", "   "):
        assert pricing.classify(text) == (None, "absent"), text
    # Punctuation-only text is a *failed* read, not a missing one — different
    # problem, different fix, so it must not report as absent.
    for text in ("???", "--", "()", "n/a"):
        assert pricing.classify(text) == (None, "unrecognized"), text


# --- resolution -------------------------------------------------------------------


def test_clin_type_beats_the_header():
    header = {"contract_type": "CPFF"}
    # A CLIN with no type inherits the header...
    inherited = pricing.policy_for({"clin": "0001"}, header)
    assert inherited.code == "CPFF"
    assert inherited.source == "header"
    # ...and a CLIN that names its own type wins, which is the entire reason
    # `CLIN.type` exists on an award that carries mixed line items.
    own = pricing.policy_for({"clin": "0002", "type": "FFP"}, header)
    assert own.code == "FFP"
    assert own.source == "clin"
    assert own.raw == "FFP"


def test_an_unreadable_clin_type_falls_back_but_is_still_flagged():
    # The header rescues the resolution — it's a weaker read, not a wrong one — but
    # the rejected CLIN text is still a data-quality problem and must survive the
    # fallback working.
    p = pricing.policy_for(
        {"clin": "0001", "type": "see attachment 2"}, {"contract_type": "T&M"}
    )
    assert p.code == "TM"
    assert p.known is True
    assert p.source == "header"
    assert p.rejected_type == "see attachment 2"


def test_unknown_is_a_first_class_value_not_a_default():
    p = pricing.policy_for({"clin": "0001"}, {})
    assert p.code == "unknown"
    assert p.known is False
    assert p.unknown_reason == "absent"
    assert p.source is None
    # It carries today's engine behaviour deliberately, so #79 can't move an
    # unlabelled award's numbers — but it is not T&M, and nothing may read it as a
    # statement about the award. That mistake is already ticketed once, as #42.
    assert p.revenue_basis == pricing.TM.revenue_basis
    assert p.code != pricing.TM.code
    assert p.family == "unknown"


def test_unknown_reports_which_kind_of_unknown_it_is():
    vehicle = pricing.policy_for({"clin": "0001"}, {"contract_type": "IDIQ"})
    assert (vehicle.code, vehicle.unknown_reason, vehicle.raw) == (
        "unknown",
        "vehicle",
        "IDIQ",
    )
    garbage = pricing.policy_for({"clin": "0001", "type": "???"}, {})
    assert (garbage.code, garbage.unknown_reason, garbage.raw) == (
        "unknown",
        "unrecognized",
        "???",
    )
    # The more specific field's reason wins: a CLIN printing a vehicle name on an
    # award whose header is also unreadable is a vehicle problem.
    both = pricing.policy_for({"type": "BPA"}, {"contract_type": "n/a"})
    assert both.unknown_reason == "vehicle"


def test_the_payload_is_json_serialisable_and_complete():
    p = pricing.policy_for({"type": "CPFF"}, {})
    payload = p.payload()
    assert payload["funding_clauses"] == ["52.232-20", "52.232-22"]  # list, not tuple
    assert set(payload) == {
        "code",
        "label",
        "family",
        "known",
        "source",
        "raw",
        "unknown_reason",
        "rejected_type",
        "ceiling_meaning",
        "cost_overrun_bearer",
        "revenue_basis",
        "funding_clauses",
        "funding_tripwire",
    }


# --- the burn payload -------------------------------------------------------------


def _contract(header_type=None, clin_types=(None, None, None)):
    labor, surge, travel = clin_types
    return {
        "id": 1,
        "contract": {
            "piid": "TEST-76",
            "total_ceiling": _LABOR_CEILING + _TRAVEL_CEILING,
            "total_obligated": 250_000,
            **({"contract_type": header_type} if header_type else {}),
        },
        "clins": [
            {
                "clin": "0001",
                "period": "Base",
                "title": "Professional Services (Labor)",
                "is_labor": True,
                "ceiling": _LABOR_CEILING,
                "est_hours": 4_000,
                **({"type": labor} if labor else {}),
            },
            {
                "clin": "0002",
                "period": "Base",
                "title": "Surge Support",
                "is_labor": True,
                "ceiling": _LABOR_CEILING,
                "est_hours": 4_000,
                **({"type": surge} if surge else {}),
            },
            {
                "clin": "0003",
                "period": "Base",
                "title": "Travel & ODC",
                "is_labor": False,
                "ceiling": _TRAVEL_CEILING,
                **({"type": travel} if travel else {}),
            },
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


def _clin(payload, num):
    return next(c for c in payload["clins"] if c["id"] == num)


def test_a_mixed_award_resolves_three_different_policies():
    # One award, an FFP deliverable, a T&M surge line and a cost travel line. The
    # normal shape of a real award, and the case a contract-level type cannot model.
    p = burn.compute(
        _contract(header_type="IDIQ", clin_types=("FFP", "T&M", "CPFF")), _rows()
    )
    assert _clin(p, "0001")["pricing_policy"]["code"] == "FFP"
    assert _clin(p, "0002")["pricing_policy"]["code"] == "TM"
    # Non-labor CLINs carry the same read — this one is on the payload's nl cards.
    assert _clin(p, "0003")["pricing_policy"]["code"] == "CPFF"
    assert all(c["pricing_policy"]["source"] == "clin" for c in p["clins"])
    # Every CLIN typed, so the header being a vehicle name costs nothing.
    assert p["contract"]["pricing_unknown"] == 0
    # And the vehicle label is still there for the UI that reads it.
    assert p["contract"]["vehicle"] == "IDIQ"


def test_untyped_clins_inherit_the_header():
    p = burn.compute(_contract(header_type="Cost Plus Fixed Fee"), _rows())
    assert [c["pricing_policy"]["code"] for c in p["clins"]] == ["CPFF"] * 3
    assert all(c["pricing_policy"]["source"] == "header" for c in p["clins"])
    assert p["contract"]["pricing_unknown"] == 0


def test_pricing_unknown_counts_the_clins_that_could_not_be_typed():
    # Nothing typed anywhere: every CLIN unknown, and the contract says so.
    p = burn.compute(_contract(), _rows())
    assert all(c["pricing_policy"]["known"] is False for c in p["clins"])
    assert p["contract"]["pricing_unknown"] == 3
    # Partially typed: only the untyped lines count, and labor and non-labor are
    # counted the same way.
    p = burn.compute(_contract(clin_types=("FFP", None, "CPFF")), _rows())
    assert p["contract"]["pricing_unknown"] == 1
    assert _clin(p, "0002")["pricing_policy"]["unknown_reason"] == "absent"


def _type_blind(payload):
    """The burn payload with everything that legitimately depends on contract type
    removed: the #76 fields themselves, plus `vehicle`, which has always echoed the
    header text verbatim. What's left must not vary with the type at all.

    #81 added four more, all of them *vocabulary* rather than arithmetic, and all four
    are the ticket's actual deliverable on this type — so stripping them here is what
    keeps the bar meaningful instead of deleting it. What the bar guards, and still
    guards to the cent, is that no **number** moves on a billings-measured type: every
    dollar, fraction, week, day and date below is compared unstripped.

      * `funding_clause`  — which clause governs is precisely a fact about the type.
      * `ceiling_is_price`— T&M's ceiling is a negotiated not-to-exceed (52.232-7);
                            a cost-type ceiling is estimated cost plus fee.
      * `limited_by` /    — the *name* of the limit and the copy switched off it.
        `stop_reason`       T&M resolves to `ceiling_price`, untyped to `ceiling`,
                            and both point at the identical dollar figure and date.
      * `status_label`    — the pill's wording. `status` itself is NOT stripped: the
                            band a CLIN lands in must still be type-blind here.

    If a future ticket wants to add to this list, the question to answer first is
    whether the key is a word or a quantity. Words belong here; quantities do not."""
    drop = {
        "pricing_policy",
        "funding_clause",
        "ceiling_is_price",
        "limited_by",
        "stop_reason",
        "status_label",
    }
    scrub = lambda d: {k: v for k, v in d.items() if k not in drop}
    return {
        **payload,
        "clins": [scrub(c) for c in payload["clins"]],
        "hero": scrub(payload["hero"]) if payload.get("hero") else payload.get("hero"),
        "tripwires": [scrub(t) for t in payload["tripwires"]],
        "funding": [scrub(f) for f in payload["funding"]],
        "contract": {
            k: v
            for k, v in payload["contract"].items()
            if k not in ("pricing_unknown", "vehicle")
        },
    }


def test_typing_an_award_moves_no_number_on_a_billings_measured_type():
    # Was #76's blanket "typing an award moves NO number anywhere" bar, deliberately
    # narrowed by #79 — the ticket that makes the engine ask the policy questions, and
    # whose entire point is that a fixed-price or cost-type award now reads
    # differently. What survives, and is still worth guarding, is the half that must
    # never move: a type measured in billings against funding has to produce
    # byte-identical numbers to the untyped legacy read.
    #
    # T&M is that type — the one the pre-#79 engine already got right — and `unknown`
    # is the same read by definition. The types that DO move are covered in
    # test_cost_revenue_fee.py, which asserts what each changes and why.
    rows = _rows()
    baseline = _type_blind(burn.compute(_contract(), rows))
    for header_type, clin_types in (
        ("T&M", (None, None, None)),
        (None, ("T&M", "Time and Materials", "T&M")),
        ("nonsense text", ("also nonsense", None, "BPA")),
    ):
        typed = _type_blind(burn.compute(_contract(header_type, clin_types), rows))
        assert typed == baseline, (header_type, clin_types)


def test_a_clin_only_caller_still_gets_its_own_type():
    # `_compute_clin` defaults the policy rather than requiring it, so a caller
    # holding a CLIN and no header (there are several in the test suite) still gets
    # the CLIN's own read instead of a blank.
    card = burn._compute_clin(
        {"clin": "0001", "type": "FFP", "is_labor": True}, [], 1, 10
    )
    assert card["pricing_policy"]["code"] == "FFP"
    assert card["pricing_policy"]["source"] == "clin"
