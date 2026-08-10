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

import datetime
import hashlib
import json
import os
import urllib.request

from . import pricing

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
# This set is DEMO-ONLY and is now reached only by an explicit `scenario=red`
# request. It used to be sent on every sync and every probe, which meant a user's
# own award was crewed 20% above plan against a T&M single-base-year contract that
# wasn't theirs — see `derive_scenario_opts` for what a real sync uses instead.
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
    # find. Each contract still sets its own CLIN and rate.
    #
    # The labor category no longer comes off the seat. A shared person is bound to
    # a qualification lineage upstream (Fixtura #70) and only fills categories
    # inside it, because #66 checks one global credential set per person against
    # each contract's billed category — a person spanning Administrative Support
    # and Senior Software Engineer is unrepresentable, and flagging them would be
    # flagging an artifact of data generation. Variation *inside* a lineage stays:
    # Systems Engineer here, Senior Software Engineer there is a career.
    "shared_pool": True,
}

# The second committed bundle — the amber one, from `regenerate.py`'s
# "funding-pace" entry at seed 19. Crewed a little UNDER plan at a 35-hour target,
# which is what puts its funded slice inside FAR 52.232-22(c)'s 60-day window
# without tripping the ceiling alarm.
#
# It needs its own entry because a scenario is (seed, opts) together: before this,
# the seed-19 award synced against the red set above and came back 7 people at 40
# hours instead of the 5 at 35 it was measured with — the amber bundle quietly
# demonstrating a hotter contract than the one its README describes.
FUNDING_PACE_OPTS = {
    "pop_in_progress": True,
    "option_years": 0,
    "contract_type": "T&M",
    "funding": "incremental",
    "staffing": 0.75,
    "target_hours": 35,
    "shared_pool": True,
}

# Rows to pull on a full sync. Deliberately above the roster x weeks grid: Fixtura
# caps a timesheet request at the full grid (one row per person per week), so
# asking for more than the grid holds returns the whole grid rather than wrapping
# and double-booking anyone. Any caller can override it.
SYNC_ROW_CAP = 460

# Fixtura seed for the burn/red bundle. Only a demo scenario defaults to a fixed
# seed now; an ordinary contract that recorded no seed of its own gets one derived
# from its PIID (see `seed_for_piid`) rather than borrowing the demo's roster.
DEFAULT_SYNC_SEED = 42
FUNDING_PACE_SEED = 19

# The demo scenarios by name, each one a (seed, opts) pair because neither half
# reproduces a bundle without the other. `scenario=red` / `scenario=amber` on a
# sync is the ONLY way these are reached — nothing here shapes a normal ingest.
SCENARIOS = {
    "red": {"seed": DEFAULT_SYNC_SEED, "opts": DEMO_SCENARIO_OPTS},
    "amber": {"seed": FUNDING_PACE_SEED, "opts": FUNDING_PACE_OPTS},
}

# The award each committed bundle carries, by PIID. Used for ONE thing: resolving
# the seed of a bundle award that recorded none at ingest — the sample the "Ingest
# sample with AI" button loads is the red bundle's SF-26, and it lands with no seed
# because nobody types one in. It used to work by accident, on the seed-42 default
# every contract shared; now that an unseeded contract gets a PIID-derived seed
# instead, the bundles need to be named or they would generate against a contract
# that isn't the one on the PDF.
#
# Deliberately NOT used to pick the opts. A bundle still syncs on-plan unless
# someone asks for `?scenario=red`, because auto-selecting the hot roster is the
# behaviour this change exists to remove — just narrowed to two PIIDs instead of
# applied to everyone.
BUNDLE_PIIDS = {
    "7026HEXDVC0001043": "red",
    "7025GMLQJC0000818": "amber",
}


def scenario(name: str) -> dict:
    """The `{"seed", "opts"}` pair for a named demo scenario.

    Raises KeyError for a name we don't know, so a typo'd `?scenario=` surfaces as
    a 400 instead of silently falling back to on-plan data and leaving someone
    wondering why the demo stopped reading red."""
    found = SCENARIOS[name]
    return {"seed": found["seed"], "opts": dict(found["opts"])}


# Runway's pricing code -> Fixtura's `contract_type` knob. The two vocabularies
# agree on every type except T&M, which Fixtura spells with the ampersand.
_FIXTURA_TYPE = {"TM": "T&M"}

# Ordering vehicles, which `pricing.classify` deliberately refuses to return a code
# for: a vehicle is not a pricing arrangement, and pricing a CLIN as one would be a
# guess. Generating against one is a different question — Fixtura models IDIQ as a
# contract type (an ordering vehicle whose task orders price T&M or CPFF), so an
# ingested IDIQ award should generate as one rather than letting Fixtura draw a type
# at random. Kept here, not in pricing.py, so the pricing policy's refusal stands.
_FIXTURA_VEHICLE = {"idiq": "IDIQ"}


def _type_key(text: str) -> str:
    """Lowercase, alphanumerics only — enough to match a vehicle name across "IDIQ",
    "idiq" and "I.D.I.Q.". Mirrors pricing._key without reaching into it."""
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def _as_date(value):
    """An ISO date string as a date, or None for anything unparseable. Extraction
    fills these from a PDF, so "TBD", "" and None all have to survive."""
    try:
        return datetime.date.fromisoformat((value or "").strip())
    except (AttributeError, TypeError, ValueError):
        return None


def _in_progress(periods: list) -> bool:
    """True when today falls inside one of the award's stated periods — i.e. the
    contract is mid-performance, which is what Fixtura's `pop_in_progress` means."""
    today = datetime.date.today()
    for period in periods or []:
        start, end = _as_date(period.get("pop_start")), _as_date(period.get("pop_end"))
        if start and end and start <= today <= end:
            return True
    return False


def derive_scenario_opts(contract: dict) -> dict:
    """Generation opts read off the ingested award itself — what a sync that is NOT
    a demo asks Fixtura for.

    The point is to reconstruct the contract Fixtura would have drawn for this
    award and then crew it to plan, rather than to the demo's deliberately hot
    roster. Only knobs the award actually states get set; an unreadable field leaves
    its knob off so Fixtura's own default governs instead of a guess.

    Two knobs are deliberately NOT derived even though the extraction carries the
    data. `active_period` and `lcat_lines` both re-enter Fixtura's contract draw and
    rewrite the result — pinning `active_period` on the burn bundle turns its three
    CLINs into two, its 28-week window into 20 and its ceiling from $4.7M into
    $2.8M. Deriving them from an extraction would break the very coherence this
    function exists to protect. `staffing`, `target_hours` and `shared_pool` are the
    roster-only knobs: they change who is on the timesheet and nothing about the
    contract underneath, which is why staffing is safe to set here and why the demo
    scenarios can differ from a derived sync without moving the award.
    """
    header = (contract or {}).get("contract") or {}
    periods = (contract or {}).get("periods") or []
    # 1.0 = crew each labor line to the FTE count it was PRICED at, which is
    # on-plan execution. NOT the same as leaving `staffing` unset: that fields one
    # person per labor line, so a line priced at 7,520 hours (four people for a
    # year) logs a quarter of its planned hours and every contract in Runway reads
    # wildly under budget.
    opts = {"staffing": 1.0}
    raw_type = header.get("contract_type")
    code, reason = pricing.classify(raw_type)
    if code:
        opts["contract_type"] = _FIXTURA_TYPE.get(code, code)
    elif reason == "vehicle" and _type_key(raw_type) in _FIXTURA_VEHICLE:
        opts["contract_type"] = _FIXTURA_VEHICLE[_type_key(raw_type)]
    if periods:
        # Fixtura counts option years on top of the base year.
        opts["option_years"] = max(0, len(periods) - 1)
    if _in_progress(periods):
        opts["pop_in_progress"] = True
    funded = header.get("incrementally_funded")
    if funded is not None:
        opts["funding"] = "incremental" if funded else "full"
    return opts


def seed_for_piid(piid: str) -> int:
    """A stable Fixtura seed derived from the award's PIID.

    The fallback for a contract that recorded no seed at ingest. Every such
    contract used to fall back to the demo's seed 42, which handed it the demo
    award's people and rates; keying off the PIID gives each award its own roster
    while staying reproducible across restarts (hashlib, not the salted builtin
    hash()).

    A committed bundle's own award is the exception: it gets that bundle's seed, so
    the sample award still generates against the contract printed on its PDF even
    though nobody typed a seed in (see BUNDLE_PIIDS)."""
    if not piid:
        return DEFAULT_SYNC_SEED
    known = BUNDLE_PIIDS.get(piid)
    if known:
        return SCENARIOS[known]["seed"]
    return int.from_bytes(hashlib.sha256(piid.encode()).digest()[:4], "big")


def normalize_piid(value) -> str:
    """A PIID reduced to what two spellings of the same contract share.

    Case and surrounding whitespace, and nothing else. Dashes are structural in a
    DoD PIID — `N66048-24-C-7647` names an issuing office, a fiscal year, a type
    and a serial — so folding them out would let two genuinely different contracts
    compare equal, which is the exact failure this comparison exists to catch.
    """
    return str(value or "").strip().upper()


def provenance(rows: list, piid: str) -> dict:
    """Which contract a timesheet batch actually belongs to.

    Fixtura draws its whole scenario from `seed + opts`: the award first, then the
    CLINs, then a roster crewed off that award's own labor lines. So a batch pulled
    against the wrong seed is not *noisy* — it is a different contract entirely, and
    storing it against this one produces exactly the symptom it looks like instead:
    LCATs the award never priced, charged to CLINs it does not contain. Every LCAT
    resolution flag #64 raises is downstream of that.

    `contract_no` is the field that says so and it costs one comparison, so the
    check is worth making before the rows land rather than after somebody spends an
    afternoon on the mismatch report.

    Rows carrying no `contract_no` are `unattributed`, not foreign: a hand-built CSV
    need not have the column, and refusing it would gate a real upload on a field
    only Fixtura fills in. A contract with no PIID of its own can't be checked at
    all (`checked: False`) — manual entry doesn't require one, and inventing a
    verdict from a blank would refuse every sync those contracts make.
    """
    want = normalize_piid(piid)
    if not want:
        return {
            "checked": False,
            "piid": None,
            "total": len(rows),
            "matched": 0,
            "unattributed": len(rows),
            "foreign": {},
            "foreign_rows": 0,
        }

    foreign, matched, unattributed = {}, 0, 0
    for r in rows:
        got = normalize_piid(r.get("contract_no"))
        if not got:
            unattributed += 1
        elif got == want:
            matched += 1
        else:
            foreign[got] = foreign.get(got, 0) + 1
    return {
        "checked": True,
        "piid": want,
        "total": len(rows),
        "matched": matched,
        "unattributed": unattributed,
        # Biggest offender first: with several foreign PIIDs in one batch, the one
        # holding most of the rows is the one worth naming in an error message.
        "foreign": dict(sorted(foreign.items(), key=lambda kv: (-kv[1], kv[0]))),
        "foreign_rows": sum(foreign.values()),
    }


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


def _probe_fixtura(opts: dict = None) -> dict:
    """Live-pull a timesheet sample from Fixtura. Returns the Fixtura source box
    with a real status: 'live' + row count when it answers, 'offline' otherwise.

    Step 1 runs before any contract exists, so with no `opts` the probe asks for
    Fixtura's own defaults. It used to send the demo scenario, which advertised a
    row/people count from a contract the user hadn't ingested."""
    box = {"code": "FX", "name": "Fixtura", "hue": "#4361ee"}
    try:
        body = {
            "preset": "govcon_timesheet",
            "rows": _PROBE_ROWS,
            "seed": DEFAULT_SYNC_SEED,
        }
        if opts:
            body["preset_opts"] = opts
        payload = json.dumps(body).encode()
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


def fetch_timesheets(
    rows: int = SYNC_ROW_CAP, seed: int = DEFAULT_SYNC_SEED, opts: dict = None
) -> list:
    """Live-pull a full timesheet batch from Fixtura for the sync endpoint.

    `opts` is the scenario to generate against — derived from the contract for an
    ordinary sync, or a named demo set. Omitted from the request entirely when
    empty, so Fixtura's defaults govern rather than a scenario the caller didn't ask
    for.

    Unlike `_probe_fixtura` (which swallows errors to keep the Step-1 sources
    page fast), this raises on failure so the caller can surface a real HTTP
    error — a sync that silently stored nothing would be worse than a 502.
    """
    body = {"preset": "govcon_timesheet", "rows": rows, "seed": seed}
    if opts:
        body["preset_opts"] = opts
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{FIXTURA_URL}/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        return json.loads(resp.read()).get("rows", [])


def list_sources(opts: dict = None) -> dict:
    """The six Step-1 source boxes: Fixtura (live-probed) + five placeholders.
    `connected` is honest — the count of boxes actually syncing right now.

    `opts` passes through to the probe for a caller that wants the preview to show
    a specific scenario; the sources page itself sends none."""
    sources = [_probe_fixtura(opts)]
    sources += [dict(p, status="disconnected") for p in _PLACEHOLDERS]
    connected = sum(1 for s in sources if s["status"] in ("live", "synced"))
    return {"connected": connected, "sources": sources}
