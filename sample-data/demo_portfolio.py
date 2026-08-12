#!/usr/bin/env python3
"""Build the demo portfolio: one contract per story beat, ingested and synced.

    python3 sample-data/demo_portfolio.py            # measure only, write nothing
    python3 sample-data/demo_portfolio.py --build    # add the set to a running server
    python3 sample-data/demo_portfolio.py --reset    # DELETE every contract, then build

Why this exists. A demo needs a portfolio that reads the same way every time, and
the dev database does not: contracts accumulate across sessions, several of them
share a PIID (which makes one person's hours read as cross-contract double-booking
on contracts that have nothing to do with each other), and `pop_in_progress` bundles
drift against real time. This rebuilds the set from seeds in about a minute, so a
database that goes bad the night before is a re-run rather than an evening.

Each entry pins its own SEED, which is what makes the PIIDs distinct — Fixtura
derives the PIID from the seed and the effective date, so two contracts built from
one seed are the same award wearing different contract types. That was the flaw in
the first sweep of this set.

AURORA also gets its SF-30 funding trail pre-loaded (P00001, P00002), so the Funding
History screen reads as a staircase rather than one bar at award. **P00003 — the
supplemental agreement that clears the red — is left on disk on purpose**: dropping it
in live is the last beat of the demo, and the Flight Deck goes green in front of the
room. Regenerate all three with `python3 sample-data/regenerate.py burn`.

The stack must be up: Fixtura on :8000, Runway on :8001 (see the README).
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
FIXTURA = Path(
    os.environ.get("FIXTURA_PATH", REPO.parent / "si2026-test-generator-kaylee")
)

sys.path.insert(0, str(FIXTURA))
sys.path.insert(0, str(REPO / "server"))
sys.path.insert(0, str(HERE))

from testgen import presets  # noqa: E402
from regenerate import to_runway  # noqa: E402

API = os.environ.get("RUNWAY_API", "http://127.0.0.1:8001")

# Every entry is a story beat first and a contract type second. The demo opens on the
# portfolio, so what matters is that the five cards do not all say the same thing:
# one red, one amber, and three healthy contracts that each exercise a different
# pricing family.
#
# `staffing` and `target_hours` are the tuning knobs, and they are tuned by measuring
# — run without --build and the script reports what each one actually lands on.
DEMO = [
    {
        "key": "red",
        "nickname": "AURORA",
        "seed": 42,
        "type": "T&M",
        "staffing": 1.2,
        "hours": 40,
        "story": "THE demo contract. Over its funded slice with a dated stop-work "
        "risk — this is the one to click into.",
        "want": "over",
        # Pre-load the two INCREMENTAL funding actions so the Funding History
        # screen shows a real staircase instead of one bar at award. Each ingest is
        # a Bedrock round trip (~17s), which is exactly why they belong in the build
        # script and not on stage.
        #
        # P00003 — the supplemental agreement that CLEARS the red — is deliberately
        # left on disk. It is the last beat of the demo: drop it live and the Flight
        # Deck goes green in front of the room. Regenerate all three with
        # `python3 sample-data/regenerate.py burn`.
        "mods": ["P00001", "P00002"],
        "mod_stem": "fixtura-runway-burn-demo",
    },
    {
        "key": "amber",
        "nickname": "MERIDIAN",
        "seed": 19,
        "type": "T&M",
        "staffing": 0.75,
        "hours": 35,
        "story": "Must NOT cry wolf: funded dollars run dry inside FAR "
        "52.232-22(c)'s 60-day window, but the ceiling holds. Reads 'Funding due'.",
        "want": "funding",
    },
    {
        "key": "cpff",
        "nickname": "HALYARD",
        "seed": 77,
        "type": "CPFF",
        "staffing": 0.9,
        "hours": 38,
        "story": "Cost-plus-fixed-fee: cost against estimated cost, with a "
        "negotiated fee being earned against it (FAR 16.306).",
        "want": None,
    },
    {
        "key": "ffp",
        "nickname": "KESTREL",
        "seed": 108,
        "type": "FFP",
        "staffing": 0.85,
        "hours": 40,
        "story": "Firm-fixed-price: funding cannot be the constraint, so the "
        "question is margin against the price, not runway.",
        "want": None,
    },
    {
        "key": "cpaf",
        "nickname": "SABLE",
        "seed": 231,
        "type": "CPAF",
        "staffing": 0.95,
        "hours": 38,
        "story": "Cost-plus-award-fee: a guaranteed base fee plus an at-risk award "
        "pool the app refuses to book as earned.",
        "want": None,
    },
]


def call(method, path, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise SystemExit(
            f"{method} {path} -> {e.code}\n{e.read()[:600].decode('utf8', 'replace')}"
        )


def upload_mod(cid, pdf: Path):
    """POST one SF-30 to /mods as multipart, the same shape the browser sends.

    Hand-rolled rather than pulled from `requests`, which this script does not
    otherwise need."""
    boundary = "----runway-demo-portfolio"
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{pdf.name}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode()
        + pdf.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(
        f"{API}/api/contracts/{cid}/mods", data=body, method="POST"
    )
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    # Generous: extraction is a Bedrock round trip, not a local parse.
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read() or b"{}")


def at_award_face(payload, contract):
    """Trim a `to_runway` payload to what the SF-26 itself obligates.

    `to_runway` states `total_obligated` as the contract's CURRENT cumulative, every
    incremental action already folded in, because that is what Fixtura's structured
    contract carries. An award form cannot say that: block 15G reports what *this
    signature* obligated, and the later money arrives on SF-30s.

    Two things went wrong from posting the cumulative as the award face. The funding
    timeline collapsed to a single bar — `Extraction` has no `obligation_history`
    field, so the trail is dropped and `_seed_award_obligation` synthesizes one
    "Award" entry at whatever `total_obligated` says. And the mods became unlayerable:
    ingest P00001 onto a face that already counts it and the contract double-counts.

    The first `obligation_history` entry is the award action, and its `funding_lines`
    say which CLINs that signature actually funded. Anything not named gets 0 — an
    award that did not obligate a line did not obligate it. Read off the Fixtura
    contract rather than the payload, because `to_runway` drops `funding_lines`."""
    history = contract.get("obligation_history") or []
    if not history:
        return payload
    award = history[0]
    by_clin = {ln["clin"]: ln["amount"] for ln in award.get("funding_lines") or []}
    payload["contract"]["total_obligated"] = award["cumulative_obligated"]
    for cl in payload["clins"]:
        cl["obligated"] = by_clin.get(cl["clin"], 0.0)
    return payload


def opts_for(spec):
    return {
        "pop_in_progress": True,
        "option_years": 0,
        "contract_type": spec["type"],
        "funding": "incremental",
        "staffing": spec["staffing"],
        "target_hours": spec["hours"],
        # Shared people pool, so a handful of employees recur across the set and the
        # portfolio's cross-contract booking check has something real to find.
        "shared_pool": True,
    }


def build_one(spec):
    opts = opts_for(spec)
    contract = presets.build_scenario(spec["seed"], dict(opts))["contract"]
    payload = to_runway(contract)
    # A contract whose mods get ingested has to start where its award started, or the
    # increments land on top of money already counted. Without mods the cumulative
    # figure IS the current truth, so leave it alone rather than understate funding.
    if spec.get("mods"):
        payload = at_award_face(payload, contract)
    optstr = urllib.parse.quote(",".join(f"{k}={v}" for k, v in opts.items()))
    saved = call(
        "POST",
        f"/api/contracts/confirm?seed={spec['seed']}&opts={optstr}",
        payload,
    )
    cid = saved["id"]
    for mod_no in spec.get("mods") or []:
        pdf = HERE / f"{spec['mod_stem']}.mod.{mod_no}.sf30.pdf"
        if not pdf.exists():
            print(f"     !! {mod_no} missing — run regenerate.py; funding trail short")
            continue
        try:
            r = upload_mod(cid, pdf)
            print(f"     {mod_no} -> obligated {r.get('total_obligated')}")
        except Exception as e:
            # One flaky Bedrock call should not cost the whole portfolio.
            print(f"     !! {mod_no} ingest failed ({e}) — funding trail short")
    call("POST", f"/api/contracts/{cid}/timesheets/sync")
    call("PUT", f"/api/contracts/{cid}/name", {"name": spec["nickname"]})
    return cid, contract["piid"]


def measure(cid):
    b = call("GET", f"/api/contracts/{cid}/burn")
    # How many dated funding actions the timeline actually holds. 1 means the award
    # alone; a mod-loaded contract should read 3. Cheap, and it is the one thing that
    # silently degrades — a failed mod ingest still leaves a working contract.
    actions = len(call("GET", f"/api/contracts/{cid}/funding")["obligation_history"])
    labor = [c for c in b["clins"] if c.get("is_labor")]
    hero = b.get("hero") or {}
    cm = b["contract"].get("cost_model") or {}
    t = b["totals"]
    return {
        "actions": actions,
        "status": [c["status"] for c in labor],
        "runway": hero.get("days"),
        "tripwires": len(b.get("tripwires") or []),
        "level": cm.get("level"),
        "margin": cm.get("margin_available"),
        "fee": t.get("fee"),
        "fee_known": t.get("fee_known"),
        "cost_known": t.get("cost_known"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="add the set to the server")
    ap.add_argument(
        "--reset",
        action="store_true",
        help="DELETE every contract on the server first, then build",
    )
    args = ap.parse_args()

    if args.reset:
        existing = call("GET", "/api/contracts")
        print(f"Deleting {len(existing)} existing contract(s):")
        for c in existing:
            print(f"  - {c['id']}  {c['piid']}")
        for c in existing:
            call("DELETE", f"/api/contracts/{c['id']}")
        print()

    build = args.build or args.reset
    if not build:
        print("(measure only — pass --build to write, --reset to wipe first)\n")

    rows = []
    for spec in DEMO:
        if build:
            cid, piid = build_one(spec)
            m = measure(cid)
        else:
            cid, piid, m = "-", "-", {}
        rows.append((spec, cid, piid, m))
        print(
            f"{spec['nickname']:9} {spec['type']:5} seed={spec['seed']:<4} "
            f"id={cid!s:4} {piid:20} {json.dumps(m, default=str)}"
        )

    print()
    ok = True
    for spec, _cid, _piid, m in rows:
        want = spec.get("want")
        if not want or not m:
            continue
        if want not in (m.get("status") or []):
            ok = False
            print(
                f"!! {spec['nickname']} wanted a '{want}' line, measured "
                f"{m.get('status')} — retune staffing/target_hours for this entry."
            )
    if build and ok:
        print("All acceptance bars met.")


if __name__ == "__main__":
    main()
