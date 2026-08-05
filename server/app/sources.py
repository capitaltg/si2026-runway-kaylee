"""Step-1 "connect your tools" data source for the ingest flow.

The design (Runway.dc.html, CONTRACT INGEST) shows six timesheet/payroll boxes
that Runway pulls labor data from over API. In this build exactly one of them is
real: **Fixtura**, our synthetic-data service, which actually live-syncs
timesheet rows over HTTP. The other five are honest "Not connected" placeholders
for the commercial systems a real deployment would wire up (Deltek, Unanet, etc.)
— we don't fake a connection we don't have.

`list_sources()` probes Fixtura live (short timeout, never blocks the request)
and reports a real row count when it answers; every other box is static.
"""

import json
import os
import urllib.request

FIXTURA_URL = os.getenv("FIXTURA_URL", "http://localhost:8000")
_PROBE_ROWS = 8  # small live pull — enough to prove the feed is real
_PREVIEW_ROWS = 5  # rows surfaced in the click-to-expand preview
_TIMEOUT = 3.0  # seconds; Fixtura being down must not hang ingest Step 1

# Fixtura builds the timesheet scenario (people, CLINs, week dates) from its
# seed + these generation opts. They MUST match the opts the demo award was
# generated with, or the synced hours charge to CLINs/weeks that don't line up
# with the ingested contract and the burn never ties out. This is the single
# source of truth for the burn-demo scenario: seed 42, an in-progress single
# base year, T&M — the same set that produced sample-data/fixtura-runway-burn-
# demo.* (award PIID 7026HEXDVC0001043).
#
# Kept in step with `sample-data/regenerate.py`'s "burn" bundle, which is what
# actually writes those files. tests/test_demo_scenario_opts.py fails when the two
# drift apart — a drift here is invisible until the burn quietly stops tying out.
DEMO_SCENARIO_OPTS = {
    "pop_in_progress": True,
    "option_years": 0,
    "contract_type": "T&M",
    # Pin the funding posture instead of depending on the seed's own draw. This
    # bundle exists to show a ceiling/funded gap, and an un-pinned posture stopped
    # producing one.
    "funding": "incremental",
    # Crew the roster ABOVE the contract's planned FTEs so the synced hours burn it
    # hot — this is the demo that is meant to read red. A one-person-per-line
    # roster logs a fraction of the hours and the contract reads far under budget.
    "staffing": 1.2,
    # The regular weekly billable target. Stated explicitly because the burn scales
    # with it and the red band is narrow enough that the default is worth pinning.
    "target_hours": 40,
    # Draw rosters from a shared cross-contract people pool so some employees show
    # up on more than one contract — which is what the portfolio resource-conflict
    # detector (people booked >100% across contracts) needs to have anything to
    # find. Identity is shared; each contract still sets its own LCAT/CLIN/rate.
    "shared_pool": True,
}
# Rows to pull on a full sync. Deliberately above the roster x weeks grid: Fixtura
# caps a timesheet request at the full grid (one row per person per week), so
# asking for more than the grid holds returns the whole grid rather than wrapping
# and double-booking anyone. Any caller can override it.
DEMO_SYNC_ROWS = 460

# Fallback Fixtura seed for a contract that hasn't recorded its own. The demo's
# burn-demo bundle was generated at this seed; other bundles (e.g. funding-pace)
# carry their own, persisted per-contract and passed back on sync.
DEFAULT_SYNC_SEED = 42

# The five commercial systems we show as placeholders. Real GovCon timesheet /
# payroll / billing tools, matching the design's vendor set — marked "Not
# connected" because we have no live integration with them here.
_PLACEHOLDERS = [
    {
        "code": "DK",
        "name": "Deltek Costpoint",
        "kind": "Billing · LCAT rates",
        "hue": "#4b2e83",
    },
    {"code": "UN", "name": "Unanet", "kind": "Timesheets · hours", "hue": "#0a66c2"},
    {"code": "QB", "name": "QuickBooks Time", "kind": "Timesheets", "hue": "#2ca01c"},
    {"code": "AD", "name": "ADP", "kind": "Payroll · roster", "hue": "#d0202f"},
    {"code": "HV", "name": "Harvest", "kind": "Timesheets", "hue": "#f6552b"},
]


def _probe_fixtura() -> dict:
    """Live-pull a timesheet sample from Fixtura. Returns the Fixtura source box
    with a real status: 'live' + row count when it answers, 'offline' otherwise."""
    box = {"code": "FX", "name": "Fixtura", "hue": "#4361ee"}
    try:
        payload = json.dumps(
            {
                "preset": "govcon_timesheet",
                "rows": _PROBE_ROWS,
                "seed": 42,
                "preset_opts": DEMO_SCENARIO_OPTS,
            }
        ).encode()
        req = urllib.request.Request(
            f"{FIXTURA_URL}/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            rows = json.loads(resp.read()).get("rows", [])
        people = len({r.get("employee_id") for r in rows if r.get("employee_id")})
        box.update(
            status="live",
            kind=f"Timesheets · {len(rows)} rows, {people} people",
            # A few real rows so the UI can show what's actually syncing — not a
            # mock. Curated to the columns worth glancing at in a preview.
            preview=[
                {
                    "employee": r.get("employee"),
                    "week_ending": r.get("week_ending"),
                    "charge_code": r.get("charge_code"),
                    "labor_category": r.get("labor_category"),
                    "total_hours": r.get("total_hours"),
                    # Carried so the preview can show leave sitting *outside* the
                    # billable figure rather than inside it (#85).
                    "leave_hours": r.get("leave_hours"),
                }
                for r in rows[:_PREVIEW_ROWS]
            ],
        )
    except Exception:
        box.update(status="offline", kind="Timesheets · start Fixtura to sync")
    return box


def fetch_timesheets(rows: int = DEMO_SYNC_ROWS, seed: int = DEFAULT_SYNC_SEED) -> list:
    """Live-pull a full timesheet batch from Fixtura for the sync endpoint.

    Unlike `_probe_fixtura` (which swallows errors to keep the Step-1 sources
    page fast), this raises on failure so the caller can surface a real HTTP
    error — a sync that silently stored nothing would be worse than a 502.
    """
    payload = json.dumps(
        {
            "preset": "govcon_timesheet",
            "rows": rows,
            "seed": seed,
            "preset_opts": DEMO_SCENARIO_OPTS,
        }
    ).encode()
    req = urllib.request.Request(
        f"{FIXTURA_URL}/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        return json.loads(resp.read()).get("rows", [])


def list_sources() -> dict:
    """The six Step-1 source boxes: Fixtura (live-probed) + five placeholders.
    `connected` is honest — the count of boxes actually syncing right now."""
    sources = [_probe_fixtura()]
    sources += [dict(p, status="disconnected") for p in _PLACEHOLDERS]
    connected = sum(1 for s in sources if s["status"] in ("live", "synced"))
    return {"connected": connected, "sources": sources}
