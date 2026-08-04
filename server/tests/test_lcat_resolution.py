"""#64 — an unmatched LCAT must say *which* of three things went wrong, and the
flag must be fixable.

Before this, every failure to match a timesheet's labor category to an award's rate
line produced the same bare flag: a red cell, a ⚠, an entry in `unmatched_lcats`.
Three causes rendered identically —

  A. the CLIN has no rate table at all (SF-26 face ingested without its
     continuation sheet) — one document fact, retold as N per-person alarms
  B. the LCAT is priced, but on another CLIN
  C. the strings differ, or the rate line is genuinely absent

— and none of the three could be resolved from the UI.

These tests pin the classification, the normalisation that is safe to apply to
money, the fuzzy suggestion that deliberately is *not*, and the two invariants the
ticket turns on: applying a mapping must move `spent`, and a missing rate table must
be reported once on the CLIN rather than once per person.
"""

from app import allocation, burn, lcat


def _rows(lcats, charge_code="0001", weeks=6, hours=40, per_person=True):
    """One row per LCAT per week. Distinct employees so the allocation matrix has
    one row each — the "flag storm" this ticket is about is per person."""
    out = []
    for i, lc in enumerate(lcats):
        for w in range(weeks):
            out.append(
                {
                    "charge_code": charge_code,
                    "labor_category": lc,
                    "total_hours": hours,
                    "week_ending": f"2026-01-{2 + 7 * w:02d}",
                    "employee": f"Person {i}",
                    "employee_id": f"e{i}" if per_person else "e0",
                }
            )
    return out


def _clin(num="0001", rates=None, ceiling=500000, est_hours=2500, title="Labor"):
    return {
        "clin": num,
        "title": title,
        "is_labor": True,
        "ceiling": ceiling,
        "est_hours": est_hours,
        "labor_rates": rates if rates is not None else [],
    }


def _contract(clins, aliases=None):
    c = {
        "id": 1,
        "contract": {
            "piid": "TEST-1",
            "total_ceiling": 1000000,
            "total_obligated": None,
        },
        "clins": clins if isinstance(clins, list) else [clins],
        "periods": [],
    }
    if aliases is not None:
        c["lcat_aliases"] = aliases
    return c


def _rate(lcat, rate):
    return {"lcat": lcat, "loaded_rate": rate}


# ---------------------------------------------------------------- normalisation


def test_normalize_folds_notation_not_meaning():
    # Case, punctuation and honorifics are spelling, so these must land together —
    # this is the exact pair the ticket was filed over.
    assert lcat.normalize("Sr. Cyber SME") == lcat.normalize("Senior Cyber SME")
    # A trailing parenthetical is a qualifier on the person, not the category.
    assert lcat.normalize("Senior Cyber SME (TS/SCI)") == lcat.normalize(
        "Senior Cyber SME"
    )
    # Roman-numeral levels and their digits are one rate line.
    assert lcat.normalize("Engineer III") == lcat.normalize("Engineer 3")
    # But two genuinely different categories must not fold.
    assert lcat.normalize("Program Manager") != lcat.normalize("Program Analyst")


def test_a_level_parenthetical_is_priced_so_it_is_kept():
    # Real schedules in this repo's own test data print "Software Engineer (Mid)".
    # Dropping the level would make a senior's hours match the mid rate line and bill
    # at the mid rate, silently — the one outcome this module must not produce.
    assert lcat.normalize("Software Engineer (Mid)") != lcat.normalize(
        "Software Engineer (Senior)"
    )
    assert lcat.normalize("Software Engineer (Mid)") != lcat.normalize(
        "Software Engineer"
    )
    # A certification is not a level, and still folds away.
    assert lcat.normalize("Program Manager (PMP)") == lcat.normalize("Program Manager")
    # A parenthesised level still reconciles with a written one.
    assert lcat.normalize("Engineer (II)") == lcat.normalize("Engineer 2")


def test_an_omitted_level_is_suggested_not_billed():
    # "Software Engineer" against a schedule that prices only "(Mid)" is a decision,
    # not a notation difference: offered, never applied.
    clin = _clin(rates=[_rate("Software Engineer (Mid)", 150.0)])
    p = burn.compute(_contract(clin), _rows(["Software Engineer"]))
    c = p["clins"][0]
    assert c["spent"] == 6 * 40 * 200.0  # blended, not the Mid rate line
    assert c["lcat_issues"][0]["suggestion"]["lcat"] == "Software Engineer (Mid)"


def test_normalize_keeps_token_order():
    # Order-insensitivity is for suggestions only: this key decides what an hour
    # bills at, and folding "Lead Analyst" into "Analyst Lead" is a guess.
    assert lcat.normalize("Lead Analyst") != lcat.normalize("Analyst Lead")
    assert lcat.similarity("Lead Analyst", "Analyst Lead") == 1.0


def test_normalize_survives_an_empty_parenthetical():
    # "(Unassigned)" must not normalise to nothing, or every such row would collide.
    assert lcat.normalize("(Unassigned)") == "unassigned"


# ------------------------------------------------------- normalised match applies


def test_near_miss_prices_off_the_real_rate_line():
    clin = _clin(rates=[_rate("Senior Cyber SME", 200.0)])
    p = burn.compute(_contract(clin), _rows(["Sr. Cyber SME"]))
    c = p["clins"][0]

    # 6 weeks x 40 hrs at the *rate line*, not the blended $200/hr coincidence —
    # est_hours is 2500 against a 500k ceiling, so blended is $200 too. Pick a
    # distinguishing case below; here just assert the match itself.
    assert c["unmatched_lcats"] == []
    assert c["lcat_issues"] == []
    assert c["rate_source"] == "rate_table"


def test_normalised_match_moves_the_money_off_blended():
    # Blended is 500000/2500 = $200/hr; the rate line is $250. A working
    # normalisation has to produce the rate-line number.
    clin = _clin(rates=[_rate("Senior Cyber SME", 250.0)])
    p = burn.compute(_contract(clin), _rows(["Sr. Cyber SME"]))
    assert p["clins"][0]["spent"] == 6 * 40 * 250.0


def test_ambiguous_normalised_key_is_refused_not_picked():
    # Two lines, one normalised key ("engineer 2"), different rates. Picking the
    # cheaper or the first would be inventing a number, so the engine refuses,
    # falls back to blended, and names what it was asked to choose between.
    clin = _clin(rates=[_rate("Engineer II", 150.0), _rate("Engineer 2", 190.0)])
    p = burn.compute(_contract(clin), _rows(["Engineer-II"]) + _rows(["Engineer 2"]))
    c = p["clins"][0]

    issues = {i["lcat"]: i for i in c["lcat_issues"]}
    assert issues["Engineer-II"]["cause"] == lcat.AMBIGUOUS
    assert sorted(x["rate"] for x in issues["Engineer-II"]["candidates"]) == [
        150.0,
        190.0,
    ]
    # "Engineer 2" still matches exactly — an exact hit is never overridden, so the
    # ambiguity is only ever reported for the spelling that needs a decision.
    assert "Engineer 2" not in issues
    assert c["spent"] == 6 * 40 * 200.0 + 6 * 40 * 190.0


def test_exact_match_wins_over_normalisation():
    # A contract with a deliberately odd-but-correct LCAT keeps billing as it did.
    clin = _clin(rates=[_rate("SR CYBER SME", 300.0), _rate("Senior Cyber SME", 250.0)])
    p = burn.compute(_contract(clin), _rows(["SR CYBER SME"]))
    assert p["clins"][0]["spent"] == 6 * 40 * 300.0


# ------------------------------------------------------------- cause A (document)


def test_missing_rate_table_is_one_clin_fact_not_a_flag_storm():
    # Five people, five LCATs, no rate table: one CLIN-level statement, and every
    # unmatched LCAT attributes to the same cause.
    people = [
        "Senior Cyber SME",
        "Cyber Analyst",
        "Program Manager",
        "SOC Lead",
        "Engineer II",
    ]
    p = burn.compute(_contract(_clin(rates=[])), _rows(people))
    c = p["clins"][0]

    assert c["rate_table_missing"] is True
    assert c["rate_source"] == "blended"
    assert {i["cause"] for i in c["lcat_issues"]} == {lcat.RATE_TABLE_MISSING}

    # One banner for the contract, naming the CLIN and what to do.
    assert len(p["rate_gaps"]) == 1
    gap = p["rate_gaps"][0]
    assert gap["code"] == "CLIN 0001"
    assert gap["blended_rate"] == 200.0
    assert sorted(gap["lcats"]) == sorted(people)

    # And it is NOT the mapping list — no mapping fixes a missing document.
    assert p["lcat_gaps"] == []


def test_missing_rate_table_does_not_gate_all_clear():
    # A blended-priced CLIN is measured and honest. Turning every award ingested
    # without its continuation sheet into a not-clear contract would be a new alarm.
    # 45 hrs/wk at the $200 blended rate lands the 500k ceiling inside the 52-week
    # clock, so the CLIN is genuinely `ok` and the only thing that could gate
    # all_clear is the rate gap.
    p = burn.compute(_contract(_clin(rates=[])), _rows(["Cyber Analyst"], hours=45))
    assert p["clins"][0]["status"] == "ok"
    assert p["rate_gaps"]
    assert p["all_clear"] is True


def test_unpriced_clin_gets_one_banner_not_two():
    # No rate table AND no est_hours → already the louder `data_quality` story.
    p = burn.compute(
        _contract(_clin(rates=[], est_hours=None)), _rows(["Cyber Analyst"])
    )
    assert p["clins"][0]["status"] == "unpriced"
    assert len(p["data_quality"]) == 1
    assert p["rate_gaps"] == []


# ---------------------------------------------------------- cause B (wrong CLIN)


def test_priced_elsewhere_names_the_clin_that_prices_it():
    charged = _clin("0003", rates=[_rate("Cyber Analyst", 120.0)])
    priced = _clin("0002", rates=[_rate("Senior Cyber SME", 250.0)])
    p = burn.compute(
        _contract([charged, priced]),
        _rows(["Senior Cyber SME"], charge_code="0003"),
    )
    c = next(x for x in p["clins"] if x["id"] == "0003")

    issue = c["lcat_issues"][0]
    assert issue["cause"] == lcat.PRICED_ELSEWHERE
    assert issue["priced_on"] == "0002"
    # Diagnosis only: the other CLIN's rate is NOT applied. Nobody agreed to move
    # this labour onto another line item's pricing.
    assert c["spent"] == 6 * 40 * c["blended_rate"]


def test_priced_elsewhere_needs_the_index():
    # Resolving one CLIN in isolation can only report an absence — the cross-CLIN
    # fact requires the period index. Documents why compute() builds it once.
    charged = _clin("0003", rates=[_rate("Cyber Analyst", 120.0)])
    resolve, _, _ = burn._rate_resolver(charged)
    assert resolve("Senior Cyber SME").cause == lcat.NO_RATE_LINE


# ------------------------------------------------------ cause C (real gap + fix)


def test_no_rate_line_offers_the_closest_candidate():
    clin = _clin(
        rates=[_rate("Cybersecurity SME", 250.0), _rate("Program Manager", 190.0)]
    )
    p = burn.compute(_contract(clin), _rows(["Cyber Security SME"]))
    issue = p["clins"][0]["lcat_issues"][0]

    assert issue["cause"] == lcat.NO_RATE_LINE
    assert issue["suggestion"]["lcat"] == "Cybersecurity SME"
    assert issue["suggestion"]["rate"] == 250.0
    assert issue["score"] >= lcat.SUGGEST_MIN
    # Suggested, never applied: the hours still bill at blended until confirmed.
    assert issue["billed_at"] == p["clins"][0]["blended_rate"]


def test_a_genuinely_unrelated_lcat_gets_no_suggestion():
    clin = _clin(rates=[_rate("Program Manager", 190.0)])
    p = burn.compute(_contract(clin), _rows(["Heavy Equipment Operator"]))
    issue = p["clins"][0]["lcat_issues"][0]
    assert issue["cause"] == lcat.NO_RATE_LINE
    assert issue["suggestion"] is None


def test_issue_carries_the_hours_riding_on_it():
    # A flag on 4 hours and a flag on a third of the contract's labour used to look
    # identical. Hours are what makes it triageable.
    clin = _clin(rates=[_rate("Program Manager", 190.0)])
    p = burn.compute(_contract(clin), _rows(["Cyber Analyst"], weeks=6, hours=40))
    assert p["clins"][0]["lcat_issues"][0]["hours"] == 240.0


def test_issues_are_ordered_by_hours():
    clin = _clin(rates=[_rate("Program Manager", 190.0)])
    rows = _rows(["Small Gap"], hours=2) + _rows(["Big Gap"], hours=40)
    p = burn.compute(_contract(clin), rows)
    assert [i["lcat"] for i in p["clins"][0]["lcat_issues"]] == ["Big Gap", "Small Gap"]


# ------------------------------------------------------------ mappings (aliases)


def test_alias_prices_the_hours_and_clears_the_flag():
    clin = _clin(rates=[_rate("Cybersecurity SME", 250.0)])
    rows = _rows(["Sr Cyber Subject Matter Expert"])

    before = burn.compute(_contract(clin), rows)["clins"][0]
    assert before["unmatched_lcats"] == ["Sr Cyber Subject Matter Expert"]
    assert before["spent"] == 6 * 40 * 200.0  # blended

    aliased = _contract(
        clin,
        aliases=[
            {
                "from": "Sr Cyber Subject Matter Expert",
                "lcat": "Cybersecurity SME",
                "clin": "0001",
            }
        ],
    )
    after = burn.compute(aliased, rows)["clins"][0]

    # The whole point: applying a mapping re-resolves burn, it doesn't hide a badge.
    assert after["unmatched_lcats"] == []
    assert after["spent"] == 6 * 40 * 250.0
    assert after["aliased_lcats"] == [
        {
            "from": "Sr Cyber Subject Matter Expert",
            "clin": "0001",
            "lcat": "Cybersecurity SME",
            "rate": 250.0,
        }
    ]


def test_alias_can_point_at_another_clins_rate_line():
    # Cause B's only real fix. Charged on 0003, priced on 0002.
    charged = _clin("0003", rates=[_rate("Cyber Analyst", 120.0)])
    priced = _clin("0002", rates=[_rate("Senior Cyber SME", 250.0)])
    rows = _rows(["Senior Cyber SME"], charge_code="0003")
    aliased = _contract(
        [charged, priced],
        aliases=[
            {"from": "Senior Cyber SME", "lcat": "Senior Cyber SME", "clin": "0002"}
        ],
    )
    c = next(x for x in burn.compute(aliased, rows)["clins"] if x["id"] == "0003")
    assert c["spent"] == 6 * 40 * 250.0
    assert c["unmatched_lcats"] == []


def test_alias_matches_every_spelling_of_its_source():
    # Keyed on the normalised source, so one mapping covers the variants.
    clin = _clin(rates=[_rate("Cybersecurity SME", 250.0)])
    aliased = _contract(
        clin, aliases=[{"from": "Sr. Cyber SME", "lcat": "Cybersecurity SME"}]
    )
    p = burn.compute(aliased, _rows(["SR CYBER SME "]))
    assert p["clins"][0]["unmatched_lcats"] == []


def test_a_stale_alias_is_dropped_not_honoured():
    # Re-importing a schedule can remove the line a saved mapping names. The mapping
    # must not price hours off a rate line nobody can point at any more.
    clin = _clin(rates=[_rate("Program Manager", 190.0)])
    aliased = _contract(clin, aliases=[{"from": "Cyber Analyst", "lcat": "Gone SME"}])
    c = burn.compute(aliased, _rows(["Cyber Analyst"]))["clins"][0]
    assert c["unmatched_lcats"] == ["Cyber Analyst"]
    assert c["spent"] == 6 * 40 * 200.0


def test_malformed_aliases_cannot_break_a_burn():
    clin = _clin(rates=[_rate("Program Manager", 190.0)])
    aliased = _contract(clin, aliases=["nonsense", {"lcat": "no source"}, {"from": ""}])
    assert burn.compute(aliased, _rows(["Program Manager"]))["clins"][0]["spent"] > 0


def test_an_exact_rate_line_outranks_an_alias():
    # A mapping written to patch an old misspelling must not shadow a line the award
    # actually prints today.
    clin = _clin(
        rates=[_rate("Cyber Analyst", 120.0), _rate("Cybersecurity SME", 250.0)]
    )
    aliased = _contract(
        clin, aliases=[{"from": "Cyber Analyst", "lcat": "Cybersecurity SME"}]
    )
    assert (
        burn.compute(aliased, _rows(["Cyber Analyst"]))["clins"][0]["spent"]
        == 6 * 40 * 120.0
    )


# ----------------------------------------------------------- allocation reconciles


def test_allocation_cells_carry_the_same_cause_burn_reports():
    charged = _clin("0003", rates=[_rate("Cyber Analyst", 120.0)])
    priced = _clin("0002", rates=[_rate("Senior Cyber SME", 250.0)])
    contract = _contract([charged, priced])
    rows = _rows(["Senior Cyber SME", "Cyber Analyst"], charge_code="0003")

    a = allocation.compute_allocation(contract, rows)
    card = next(c for c in a["clins"] if c["id"] == "0003")
    assert card["rate_table_missing"] is False
    assert [i["cause"] for i in card["lcat_issues"]] == [lcat.PRICED_ELSEWHERE]

    cells = [e["cells"]["0003"] for e in a["employees"]]
    flagged = [c for c in cells if c["unmatched"]]
    assert len(flagged) == 1
    assert flagged[0]["cause"] == lcat.PRICED_ELSEWHERE
    assert flagged[0]["priced_on"] == "0002"
    # The matched person carries the rate line that priced them, not a bare number.
    matched = next(c for c in cells if not c["unmatched"])
    assert matched["rate_line"] == {
        "clin": "0003",
        "lcat": "Cyber Analyst",
        "rate": 120.0,
    }


def test_allocation_and_burn_agree_on_spend_under_an_alias():
    # allocation reads burn directly so the matrix reconciles with the Flight Deck;
    # a mapping must not be able to break that (the #64 acceptance criterion).
    clin = _clin(rates=[_rate("Cybersecurity SME", 250.0)])
    rows = _rows(["Sr. Cyber SME"])
    contract = _contract(
        clin, aliases=[{"from": "Sr. Cyber SME", "lcat": "Cybersecurity SME"}]
    )

    a = allocation.compute_allocation(contract, rows)
    b = burn.compute(contract, rows)
    assert a["clins"][0]["spent"] == b["clins"][0]["spent"]
    assert a["employees"][0]["cells"]["0001"]["via"] == lcat.VIA_ALIAS
    assert a["employees"][0]["cells"]["0001"]["rate"] == 250.0
