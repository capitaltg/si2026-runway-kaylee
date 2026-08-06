"""A sync that isn't a demo must generate against the award it's attached to.

Runway sent `DEMO_SCENARIO_OPTS` on every timesheet sync and every source probe.
Those opts exist to make the burn demo read red — they crew the roster 20% ABOVE
the contract's planned FTEs and pin a T&M single-base-year incrementally-funded
contract — so every user's award was staffed hot against a contract shape that
wasn't theirs, and the burn couldn't tie out. These tests pin the two halves of the
fix: what a derived sync asks for, and the line between the knobs that are safe to
derive and the ones that rewrite the contract underneath.
"""

import datetime

import pytest

from app import sources

TODAY = datetime.date.today()


def _contract(**header):
    """A stored contract blob: one period containing today, T&M, incrementally
    funded — the shape both committed bundles have."""
    base = {
        "contract_type": "T&M",
        "incrementally_funded": True,
    }
    base.update(header)
    return {
        "piid": "7026HEXDVC0001043",
        "contract": base,
        "periods": [
            {
                "name": "Base",
                "pop_start": str(TODAY - datetime.timedelta(days=120)),
                "pop_end": str(TODAY + datetime.timedelta(days=120)),
                "exercised": True,
            }
        ],
        "clins": [],
    }


# --- what a derived sync asks for --------------------------------------------


def test_derived_opts_carry_no_demo_skew():
    """The whole point. None of the three roster knobs the demos tune may appear
    with a demo's value: `staffing` is on-plan, and target_hours / shared_pool are
    left to Fixtura's defaults rather than pinned to a bundle's tuning."""
    opts = sources.derive_scenario_opts(_contract())
    assert opts["staffing"] == 1.0
    assert "target_hours" not in opts
    assert "shared_pool" not in opts


def test_derived_opts_never_pin_the_contract_rewriting_knobs():
    """`active_period` and `lcat_lines` re-enter Fixtura's contract draw: pinning
    active_period on the burn bundle turns three CLINs into two, 28 weeks into 20
    and a $4.7M ceiling into $2.8M. The extraction carries enough to derive both,
    which is exactly why this test exists — deriving them would break the
    coherence the derivation is for."""
    opts = sources.derive_scenario_opts(_contract())
    assert "active_period" not in opts
    assert "lcat_lines" not in opts


def test_staffing_crews_to_planned_ftes_not_one_person_per_line():
    """1.0 means "as priced": a line priced at 7,520 hours is four people for a
    year. Leaving `staffing` unset would field one person per labor line, log a
    quarter of the planned hours and make every contract read wildly under budget —
    a different wrong answer, not a neutral one."""
    assert sources.derive_scenario_opts(_contract())["staffing"] == 1.0


# --- reading the award --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("T&M", "T&M"),
        ("Time-and-Materials", "T&M"),
        ("TIME & MATERIALS", "T&M"),
        ("CPFF (Completion Form)", "CPFF"),
        ("Firm-Fixed-Price", "FFP"),
    ],
)
def test_contract_type_normalises_to_fixturas_vocabulary(raw, expected):
    assert (
        sources.derive_scenario_opts(_contract(contract_type=raw))["contract_type"]
        == expected
    )


@pytest.mark.parametrize("raw", [None, "", "   ", "???", "Some Other Vehicle"])
def test_an_unreadable_contract_type_leaves_the_knob_off(raw):
    """Unset means Fixtura's own default governs. Guessing a type here would pick
    the contract shape for an award we admittedly couldn't read."""
    assert "contract_type" not in sources.derive_scenario_opts(
        _contract(contract_type=raw)
    )


@pytest.mark.parametrize("raw", ["IDIQ", "idiq", "I.D.I.Q."])
def test_an_ordering_vehicle_still_generates_as_one(raw):
    """`pricing.classify` returns no code for a vehicle, on purpose — a vehicle is
    not a pricing arrangement. Generation is a different question: Fixtura models
    IDIQ, and an ingested IDIQ award that dropped the knob would have a pricing type
    drawn at random for it (weighted to fixed-price)."""
    assert (
        sources.derive_scenario_opts(_contract(contract_type=raw))["contract_type"]
        == "IDIQ"
    )


def test_option_years_counts_periods_beyond_the_base():
    c = _contract()
    c["periods"] += [{"name": "Option 1"}, {"name": "Option 2"}]
    assert sources.derive_scenario_opts(c)["option_years"] == 2


def test_funding_posture_comes_from_the_award():
    assert sources.derive_scenario_opts(_contract())["funding"] == "incremental"
    full = sources.derive_scenario_opts(_contract(incrementally_funded=False))
    assert full["funding"] == "full"
    unknown = sources.derive_scenario_opts(_contract(incrementally_funded=None))
    assert "funding" not in unknown


def test_pop_in_progress_only_when_today_is_inside_a_period():
    assert sources.derive_scenario_opts(_contract())["pop_in_progress"] is True
    closed = _contract()
    closed["periods"] = [
        {"pop_start": "2019-01-01", "pop_end": "2019-12-31", "name": "Base"}
    ]
    assert "pop_in_progress" not in sources.derive_scenario_opts(closed)


@pytest.mark.parametrize("bad", ["TBD", "", None, "31/12/2026"])
def test_unparseable_period_dates_do_not_raise(bad):
    """Dates come off a PDF, so anything can be in them."""
    c = _contract()
    c["periods"] = [{"pop_start": bad, "pop_end": bad, "name": "Base"}]
    assert "pop_in_progress" not in sources.derive_scenario_opts(c)


def test_an_empty_contract_still_yields_usable_opts():
    """A blob with nothing readable must degrade to on-plan staffing and no other
    claims, not to a demo scenario and not to an exception."""
    assert sources.derive_scenario_opts({}) == {"staffing": 1.0}


# --- named demo scenarios -----------------------------------------------------


def test_named_scenarios_pair_a_seed_with_its_opts():
    red = sources.scenario("red")
    assert red["seed"] == sources.DEFAULT_SYNC_SEED
    assert red["opts"] == sources.DEMO_SCENARIO_OPTS
    amber = sources.scenario("amber")
    assert amber["seed"] == sources.FUNDING_PACE_SEED
    assert amber["opts"] == sources.FUNDING_PACE_OPTS


def test_an_unknown_scenario_raises_rather_than_falling_back():
    """The endpoint turns this into a 400. A typo'd ?scenario= that silently
    returned derived data would leave someone debugging why the demo went green."""
    with pytest.raises(KeyError):
        sources.scenario("crimson")


def test_scenario_hands_out_a_copy():
    """A caller mutating its opts (or FastAPI holding the dict) must not edit the
    module constant every other sync and the bundle test read."""
    sources.scenario("red")["opts"]["staffing"] = 99
    assert sources.DEMO_SCENARIO_OPTS["staffing"] == 1.2


# --- the seed fallback --------------------------------------------------------


def test_seed_is_derived_from_the_piid_not_borrowed_from_the_demo():
    piid = "70RCSA26C0000123"
    seed = sources.seed_for_piid(piid)
    assert seed != sources.DEFAULT_SYNC_SEED
    assert seed == sources.seed_for_piid(piid)  # stable across calls and restarts
    assert seed != sources.seed_for_piid("N60058-24-C-3695")  # per-award


def test_a_missing_piid_falls_back_to_the_module_seed():
    assert sources.seed_for_piid("") == sources.DEFAULT_SYNC_SEED
    assert sources.seed_for_piid(None) == sources.DEFAULT_SYNC_SEED


@pytest.mark.parametrize("piid,name", sorted(sources.BUNDLE_PIIDS.items()))
def test_a_bundle_award_gets_its_own_bundles_seed(piid, name):
    """The sample the ingest button loads is the red bundle's SF-26 and arrives with
    no seed — it worked before only because seed 42 was everyone's default. Hashing
    its PIID instead would generate a contract that isn't the one on the PDF."""
    assert sources.seed_for_piid(piid) == sources.SCENARIOS[name]["seed"]


def test_a_bundle_piid_still_syncs_on_plan_unless_a_scenario_is_asked_for():
    """BUNDLE_PIIDS resolves the seed, never the opts. Auto-selecting the hot roster
    for a known PIID would be the same skew this change removes, just narrower."""
    opts = sources.derive_scenario_opts(_contract())  # the red bundle's PIID
    assert opts["staffing"] == 1.0
    assert opts != sources.DEMO_SCENARIO_OPTS
