#!/usr/bin/env python3
"""Which contracts are holding another contract's timesheets?

The sync gate stops new foreign batches from landing, but it cannot know about the
ones already stored — and those are the rows currently producing the unmatched-LCAT
noise. This reports them, and will clear them on request.

    python3 server/scripts/audit_timesheet_provenance.py            # report
    python3 server/scripts/audit_timesheet_provenance.py --purge    # and delete them

Purge deletes only the rows whose `contract_no` names a *different* contract. Rows
that match, and rows with no contract number at all (a hand-built CSV need not carry
one), are left alone. A purged contract is left with no synced labor until it is
re-synced against the right seed, which is the honest state: better an empty burn
chart than one priced off somebody else's roster.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, sources  # noqa: E402


def audit() -> list:
    rows = []
    for c in db.list_contracts():
        ts = db.get_timesheets(c["id"])
        check = sources.provenance(ts, c.get("piid"))
        rows.append({"id": c["id"], "piid": c.get("piid"), **check})
    return rows


def purge(contract_id: int, piid: str) -> int:
    """Delete this contract's foreign rows. Compared in SQL the same way
    `sources.normalize_piid` compares in Python — trimmed and upper-cased, dashes
    left intact — so the two can never disagree about what counts as foreign."""
    want = sources.normalize_piid(piid)
    conn = db.get_conn()
    cur = conn.execute(
        """DELETE FROM timesheets
            WHERE contract_id = ?
              AND contract_no IS NOT NULL
              AND TRIM(contract_no) != ''
              AND UPPER(TRIM(contract_no)) != ?""",
        (contract_id, want),
    )
    conn.commit()
    conn.close()
    return cur.rowcount


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--purge",
        action="store_true",
        help="delete the foreign rows instead of only reporting them",
    )
    args = ap.parse_args()

    report = audit()
    if not report:
        print("No contracts.")
        return 0

    bad = [r for r in report if r["foreign_rows"]]
    for r in report:
        if not r["checked"]:
            note = "no PIID — cannot be checked"
        elif r["total"] == 0:
            note = "no timesheets"
        elif r["foreign_rows"]:
            note = "FOREIGN: " + ", ".join(
                f"{p} ({n} rows)" for p, n in r["foreign"].items()
            )
        else:
            note = f"ok — {r['matched']} rows"
        if r["unattributed"]:
            note += f"; {r['unattributed']} unattributed"
        print(f"  {r['id']:>4}  {str(r['piid'] or '—'):<24} {note}")

    if not bad:
        print("\nEvery batch belongs to the contract it is stored against.")
        return 0

    total = sum(r["foreign_rows"] for r in bad)
    print(f"\n{len(bad)} contract(s) holding {total} foreign rows.")
    if not args.purge:
        print("Re-run with --purge to delete them, then re-sync with the right seed.")
        return 1

    for r in bad:
        print(f"  purged {purge(r['id'], r['piid'])} rows from contract {r['id']}")
    print("\nRe-sync each one with ?seed=<n> — the gate will refuse the wrong seed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
