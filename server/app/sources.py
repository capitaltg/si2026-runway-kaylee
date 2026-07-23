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
_TIMEOUT = 3.0  # seconds; Fixtura being down must not hang ingest Step 1

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
            {"preset": "govcon_timesheet", "rows": _PROBE_ROWS, "seed": 42}
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
        )
    except Exception:
        box.update(status="offline", kind="Timesheets · start Fixtura to sync")
    return box


def list_sources() -> dict:
    """The six Step-1 source boxes: Fixtura (live-probed) + five placeholders.
    `connected` is honest — the count of boxes actually syncing right now."""
    sources = [_probe_fixtura()]
    sources += [dict(p, status="disconnected") for p in _PLACEHOLDERS]
    connected = sum(1 for s in sources if s["status"] in ("live", "synced"))
    return {"connected": connected, "sources": sources}
