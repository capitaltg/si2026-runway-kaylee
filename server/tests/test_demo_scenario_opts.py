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
