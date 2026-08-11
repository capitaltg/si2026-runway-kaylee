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
