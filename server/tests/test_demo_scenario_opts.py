"""The live sync and the committed demo bundle must be generated the same way.

`sources.DEMO_SCENARIO_OPTS` is what "Sync now" sends Fixtura;
`sample-data/regenerate.py` is what writes the committed burn-demo files. If they
disagree, the synced hours charge to CLINs and weeks that don't line up with the
ingested award and the burn silently stops tying out — a failure with no error
message, which is why it gets a test rather than a comment.
"""

import importlib.util
from pathlib import Path

from app import sources

_SCRIPT = Path(__file__).resolve().parents[2] / "sample-data" / "regenerate.py"


def _bundles():
    spec = importlib.util.spec_from_file_location("_regen", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BUNDLES


def test_sync_opts_match_the_burn_bundle():
    burn = _bundles()["burn"]
    assert burn["opts"] == sources.DEMO_SCENARIO_OPTS
    assert burn["seed"] == sources.DEFAULT_SYNC_SEED


def test_sync_opts_match_the_funding_pace_bundle():
    """The amber bundle needs its own pin for the same reason the red one does.
    Both bundles synced against DEMO_SCENARIO_OPTS until the scenario map existed,
    so the seed-19 award came back crewed at 1.2 / 40h instead of the 0.75 / 35h it
    was measured at — an amber demo quietly running a hotter contract than its
    README describes."""
    pace = _bundles()["funding-pace"]
    assert pace["opts"] == sources.FUNDING_PACE_OPTS
    assert pace["seed"] == sources.FUNDING_PACE_SEED


def test_every_bundle_is_reachable_as_a_named_scenario():
    """A committed bundle nobody can ask for is a bundle that stops being tested.
    Each one has to be (seed, opts) behind a `?scenario=` name."""
    pairs = {(s["seed"], tuple(sorted(s["opts"].items()))) for s in _bundles().values()}
    named = {
        (s["seed"], tuple(sorted(s["opts"].items())))
        for s in sources.SCENARIOS.values()
    }
    assert pairs == named
