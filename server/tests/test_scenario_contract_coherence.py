"""Deriving opts from the award must not move the award.

This is the load-bearing assumption behind `sources.derive_scenario_opts`. Fixtura
builds the scenario *contract* from `seed + opts` and only then crews a roster onto
it, so an opt that touches the contract draw changes the CLINs, the PoP window and
the ceiling — and the synced hours stop charging anything the ingested award
contains. The derivation is therefore allowed to differ from a demo scenario in the
roster-only knobs (`staffing`, `target_hours`, `shared_pool`) and nothing else.

Asserted against Fixtura itself rather than reasoned about in a comment, because the
failure mode is silent: the burn just quietly stops tying out.
"""

import os
import sys
from pathlib import Path

import pytest

from app import sources

FIXTURA = Path(
    os.environ.get(
        "FIXTURA_PATH",
        Path(__file__).resolve().parents[2].parent / "si2026-test-generator-kaylee",
    )
)

if str(FIXTURA) not in sys.path:
    sys.path.insert(0, str(FIXTURA))

presets = pytest.importorskip(
    "testgen.presets", reason="needs a Fixtura checkout (set FIXTURA_PATH)"
)


def _shape(seed, opts):
    """The parts of a generated scenario the ingested award has to agree with. The
    roster is deliberately excluded — that is what staffing is meant to change."""
    sc = presets.build_scenario(seed, opts)
    contract = sc["contract"]
    active = presets._active_period(contract)
    return {
        "piid": contract["piid"],
        "clins": [c["clin"] for c in active["clins"]],
        "ceiling": contract.get("total_ceiling"),
        "weeks": sc["weeks"],
        "est_hours": sum(
            line["est_hours"]
            for c in active["clins"]
            for line in c.get("labor_rates", [])
        ),
    }


@pytest.mark.parametrize("name", sorted(sources.SCENARIOS))
def test_derived_opts_reproduce_each_bundles_contract(name):
    """Same award, same CLINs, same weeks, same priced hours — only the crew size
    differs between a demo sync and a derived one."""
    demo = sources.scenario(name)
    derived = {
        "staffing": 1.0,
        "contract_type": "T&M",
        "option_years": 0,
        "pop_in_progress": True,
        "funding": "incremental",
    }
    assert _shape(demo["seed"], demo["opts"]) == _shape(demo["seed"], derived)


@pytest.mark.parametrize("knob,value", [("active_period", 0), ("lcat_lines", 4)])
def test_the_excluded_knobs_really_do_rewrite_the_contract(knob, value):
    """The reason `derive_scenario_opts` refuses to set these two. If Fixtura ever
    makes them inert this test fails, and the derivation can safely widen."""
    demo = sources.scenario("red")
    baseline = _shape(demo["seed"], demo["opts"])
    with_knob = _shape(demo["seed"], {**demo["opts"], knob: value})
    assert with_knob != baseline


def test_the_roster_knobs_really_are_roster_only():
    """The other half of the same line: dropping the demo's roster tuning changes
    who is on the timesheet and nothing about the contract."""
    demo = sources.scenario("red")
    stripped = {
        k: v
        for k, v in demo["opts"].items()
        if k not in ("staffing", "target_hours", "shared_pool")
    }
    assert _shape(demo["seed"], stripped) == _shape(demo["seed"], demo["opts"])
