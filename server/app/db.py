import json
import os
import sqlite3
from typing import Optional

from . import lcat

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "runway.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _add_missing_columns(conn, table: str, columns: dict) -> None:
    """Add columns an existing table doesn't have yet.

    `CREATE TABLE IF NOT EXISTS` is a no-op once a table exists, so a schema
    addition never reaches a database that predates it. This is the additive half
    of a migration and deliberately nothing more: new columns arrive NULL, and no
    existing column is renamed, retyped or dropped. Anything beyond that needs a
    real migration with a data step, not this helper.
    """
    have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db():
    conn = get_conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            piid TEXT,
            data TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    # Cache of synced timesheet rows, keyed to a contract. One row per
    # employee-week-CLIN; the burn engine buckets these by charge_code (== CLIN).
    #
    # The hours arrive as a split, not a single figure (#85). `total_hours` is the
    # BILLABLE quantity — regular plus overtime — and it is the only column that
    # may be priced against a CLIN. Leave and holidays are indirect costs recovered
    # through the fringe pool (FAR 31.205-6) and are already inside every loaded
    # rate, so charging them again to a CLIN double-counts. They are stored anyway:
    # #85's dated-absence planning and #84's utilisation baseline both need to know
    # what a person's paid week actually looked like. Read hours through
    # `burn.billable_hours` rather than off `total_hours` directly — a row synced
    # before the split existed means something different by the same name.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS timesheets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER,
            employee TEXT,
            employee_id TEXT,
            week_ending TEXT,
            charge_code TEXT,
            labor_category TEXT,
            total_hours REAL,
            reg_hours REAL,
            ot_hours REAL,
            holiday_hours REAL,
            leave_hours REAL,
            paid_hours REAL,
            contract_no TEXT,
            synced_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    _add_missing_columns(
        conn,
        "timesheets",
        {
            "reg_hours": "REAL",
            "ot_hours": "REAL",
            "holiday_hours": "REAL",
            "leave_hours": "REAL",
            "paid_hours": "REAL",
        },
    )
    # Manually-logged non-labor actuals (travel / ODC / materials / subs), keyed
    # to a contract and one of its non-labor CLINs. Cost-reimbursable spend that
    # never shows up on a timesheet; the burn engine folds each CLIN's entries in
    # as that CLIN's spend.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER,
            clin TEXT,
            date TEXT,
            description TEXT,
            category TEXT,
            amount REAL,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    # Saved allocation-matrix what-if plans, keyed to a contract. `data` is the
    # JSON sim state (per-person hrs grid + planned adds + rolled-off people) so a
    # plan reloads exactly as it was modeled.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER,
            name TEXT,
            data TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    # Indirect rate pools — fringe / overhead / G&A (#77). `contract_id IS NULL` is
    # a company-wide default; a row with a contract_id overrides it for that award.
    # Fiscal-year-keyed from day one (see rates.RateSet) and status-tagged, because
    # #87 trues provisional rates up to actuals and retrofitting either key later
    # means recomputing every number derived from it.
    #
    # Its own table, NOT the contract blob and NOT anything the timesheet sync
    # touches: rate sets are hand-maintained and must survive a re-sync. See
    # `replace_timesheets` for the delete-then-insert trap this is avoiding.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rate_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER,
            fiscal_year TEXT,
            pool TEXT CHECK (pool IN ('fringe','overhead','gna')),
            rate REAL,
            base TEXT,
            status TEXT CHECK (status IN ('provisional','actual')) DEFAULT 'provisional',
            updated_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    # Direct (unburdened) labor rates. An LCAT row is the Level-2 case — category
    # averages, no employee named, no payroll file. An employee_id row is Level 3 and
    # exists only if the user opts in (#69 supplies the roster). Exactly one of the
    # two is set per row.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS direct_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER,
            fiscal_year TEXT,
            lcat TEXT,
            employee_id TEXT,
            rate REAL,
            status TEXT CHECK (status IN ('provisional','actual')) DEFAULT 'provisional',
            updated_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    # People (#69) — the *authored* half of the directory, and only that.
    #
    # This table holds manually-added people and nothing else. Everyone who
    # has ever charged is derived live from `timesheets` on every read (see
    # `people_charging_facts`) rather than upserted into a row here. That is a
    # deliberate structural choice, not a shortcut: an existing install is fully
    # populated the moment this endpoint exists with no backfill step, and the
    # ticket's central invariant stops being something a test has to defend. A
    # directory with nowhere to store "who charges what" cannot drift from the
    # timesheets that own that question.
    #
    # `id_provisional` marks a Runway-minted id (RW-0001) given to a manually-added person
    # the user added without one. It is not a payroll id and will never match a
    # timesheet feed, so those are the rows `people.merge_candidates` offers to fold
    # into a real person once one shows up.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS people (
            employee_id TEXT PRIMARY KEY,
            name TEXT,
            id_provisional INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    # Qualification assertions — one row per (person, field), NOT columns on a
    # person row (#69).
    #
    # Narrow because every qual carries its own provenance, and because years of
    # experience is not a fact: GovCon quals read "BS + 10 years *relevant*
    # experience", and "relevant" is what a proposal argues. `12 yrs — per proposal
    # resume, 2026-03` is defensible in an audit; a bare `12` is a number someone
    # will dispute. A wide table needs four columns to say that per qual, plus a
    # schema change per new field; here #84's utilisation target is one more allowed
    # `field` and no migration. Absence of a row is `unknown`, which keeps it
    # distinguishable from a typed-in blank.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS person_attrs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT,
            field TEXT,
            value TEXT,
            source_note TEXT,
            authored_by TEXT,
            authored_at TEXT DEFAULT (datetime('now')),
            UNIQUE (employee_id, field)
        )"""
    )
    conn.commit()
    conn.close()


def _scope_clause(contract_id: Optional[int]) -> tuple:
    """SQL fragment + params for "this contract" vs "the company default"."""
    if contract_id is None:
        return "contract_id IS NULL", ()
    return "contract_id = ?", (contract_id,)


def get_rate_model(contract_id: Optional[int] = None) -> dict:
    """The indirect pools + direct rates in force, with the company default filling
    in per-pool gaps (#77).

    Merged per pool, not all-or-nothing: a company that sets fringe and G&A centrally
    and negotiates overhead per contract is normal, and an all-or-nothing merge would
    make them re-enter the two they had already given us.
    """
    conn = get_conn()
    pool_rows = conn.execute(
        """SELECT contract_id, fiscal_year, pool, rate, base, status
           FROM rate_sets WHERE contract_id IS NULL OR contract_id = ?
           ORDER BY contract_id IS NULL""",
        (contract_id,),
    ).fetchall()
    direct = conn.execute(
        """SELECT contract_id, fiscal_year, lcat, employee_id, rate, status
           FROM direct_rates WHERE contract_id IS NULL OR contract_id = ?
           ORDER BY contract_id IS NULL""",
        (contract_id,),
    ).fetchall()
    conn.close()

    # Contract-specific rows sort first, so the first row seen for a key wins.
    pools = {}
    for r in pool_rows:
        pools.setdefault(r["pool"], dict(r))
    direct_rates = {}
    for r in direct:
        direct_rates.setdefault((r["employee_id"], r["lcat"]), dict(r))
    return {
        "pools": list(pools.values()),
        "direct_rates": list(direct_rates.values()),
        # Which scope supplied the pools, so the UI can say "inherited from your
        # company rates" rather than implying they were set on this contract.
        "scope": (
            "contract"
            if any(p["contract_id"] is not None for p in pools.values())
            else "company"
        ),
    }


def save_rate_pools(
    contract_id: Optional[int],
    fiscal_year: Optional[str],
    pools: list,
    status: str = "provisional",
) -> dict:
    """Replace the indirect pools for one (scope, fiscal year).

    Delete-then-insert *scoped to that year*, so entering FY26's rates never disturbs
    FY25's — which is exactly what #87 will true up against. An empty list clears
    them, because withdrawing rates has to be as easy as providing them or "optional"
    isn't true.
    """
    where, params = _scope_clause(contract_id)
    conn = get_conn()
    conn.execute(
        f"DELETE FROM rate_sets WHERE {where} AND (fiscal_year IS ? OR fiscal_year = ?)",
        (*params, fiscal_year, fiscal_year),
    )
    conn.executemany(
        """INSERT INTO rate_sets (contract_id, fiscal_year, pool, rate, base, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (
                contract_id,
                fiscal_year,
                p.get("pool"),
                float(p.get("rate") or 0),
                p.get("base"),
                status,
            )
            for p in pools or []
            if p.get("pool") and p.get("rate") is not None
        ],
    )
    conn.commit()
    conn.close()
    return get_rate_model(contract_id)


def save_direct_rates(
    contract_id: Optional[int],
    fiscal_year: Optional[str],
    rows: list,
    status: str = "provisional",
) -> dict:
    """Replace the direct-rate table for one (scope, fiscal year). An empty list
    withdraws them and drops the contract back to billing-only (Level 1)."""
    where, params = _scope_clause(contract_id)
    conn = get_conn()
    conn.execute(
        f"DELETE FROM direct_rates WHERE {where} AND (fiscal_year IS ? OR fiscal_year = ?)",
        (*params, fiscal_year, fiscal_year),
    )
    conn.executemany(
        """INSERT INTO direct_rates
           (contract_id, fiscal_year, lcat, employee_id, rate, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (
                contract_id,
                fiscal_year,
                (r.get("lcat") or None) if not r.get("employee_id") else None,
                r.get("employee_id") or None,
                float(r.get("rate") or 0),
                status,
            )
            for r in rows or []
            if r.get("rate") is not None and (r.get("lcat") or r.get("employee_id"))
        ],
    )
    conn.commit()
    conn.close()
    return get_rate_model(contract_id)


def save_contract(piid: str, data: dict) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO contracts (piid, data) VALUES (?, ?)", (piid, json.dumps(data))
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def update_contract(cid: int, data: dict) -> None:
    """Replace a contract's stored data blob (its piid column is left as-is).
    Used by the supplemental rate-schedule import to merge in labor rates."""
    conn = get_conn()
    conn.execute("UPDATE contracts SET data = ? WHERE id = ?", (json.dumps(data), cid))
    conn.commit()
    conn.close()


def rename_contract(cid: int, nickname: Optional[str]) -> Optional[dict]:
    """Set (or clear) a user-chosen nickname for a contract — a callsign like
    'FALCON' that reads better than the legal name or PIID. Stored on the data
    blob so it surfaces everywhere the blob is splatted (list/get, and burn via
    `nickname`). Passing an empty/None name clears it back to the legal name.
    Returns the refreshed contract, or None if it doesn't exist."""
    existing = get_contract(cid)
    if existing is None:
        return None
    blob = {k: v for k, v in existing.items() if k not in ("id", "piid", "created_at")}
    clean = (nickname or "").strip()
    if clean:
        blob["nickname"] = clean
    else:
        blob.pop("nickname", None)
    update_contract(cid, blob)
    return get_contract(cid)


def set_lcat_alias(
    cid: int, source: str, target_lcat: str, target_clin: Optional[str] = None
) -> Optional[dict]:
    """Point a timesheet LCAT at a rate line the award does price (#64).

    This is the resolution path for the two unmatched causes a document can't fix:
    a near-miss string the normaliser doesn't fold, and an LCAT priced on a
    *different* CLIN than the one being charged (`target_clin`). Applying one
    re-resolves burn on the next read — that's the point, and it is why the API
    returns the refreshed contract rather than just an ack: `spent`, `remaining` and
    the runway all move, and hiding that behind a cleared badge would be the
    dead-end ⚠ all over again.

    Stored on the contract's data blob, upserted by the *normalised* source LCAT so
    one mapping catches every spelling of it. Deliberately not a table: a mapping is
    a fact about this award's paperwork, it is hand-maintained, and living on the
    blob means `replace_timesheets`' wholesale delete-then-insert can never reach it
    (see its docstring — that trap is real and cost us a ticket).
    """
    existing = get_contract(cid)
    if existing is None:
        return None
    key = lcat.normalize(source)
    target = (target_lcat or "").strip()
    if not key or not target:
        return get_contract(cid)
    blob = {k: v for k, v in existing.items() if k not in ("id", "piid", "created_at")}
    aliases = [
        a
        for a in (blob.get("lcat_aliases") or [])
        if isinstance(a, dict) and lcat.normalize(a.get("from")) != key
    ]
    aliases.append(
        {
            "from": (source or "").strip(),
            "lcat": target,
            "clin": str(target_clin).strip() if target_clin else None,
        }
    )
    blob["lcat_aliases"] = aliases
    update_contract(cid, blob)
    return get_contract(cid)


def delete_lcat_alias(cid: int, source: str) -> Optional[dict]:
    """Drop one LCAT mapping, putting that LCAT back to whatever it resolved to
    before (usually the blended rate) and restoring its flag. Returns the refreshed
    contract, or None if the contract doesn't exist."""
    existing = get_contract(cid)
    if existing is None:
        return None
    key = lcat.normalize(source)
    blob = {k: v for k, v in existing.items() if k not in ("id", "piid", "created_at")}
    blob["lcat_aliases"] = [
        a
        for a in (blob.get("lcat_aliases") or [])
        if isinstance(a, dict) and lcat.normalize(a.get("from")) != key
    ]
    update_contract(cid, blob)
    return get_contract(cid)


def list_contracts() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, piid, data, created_at FROM contracts ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "piid": r["piid"],
            "created_at": r["created_at"],
            **json.loads(r["data"]),
        }
        for r in rows
    ]


def get_contract(cid: int) -> Optional[dict]:
    conn = get_conn()
    r = conn.execute(
        "SELECT id, piid, data, created_at FROM contracts WHERE id = ?", (cid,)
    ).fetchone()
    conn.close()
    if r is None:
        return None
    return {
        "id": r["id"],
        "piid": r["piid"],
        "created_at": r["created_at"],
        **json.loads(r["data"]),
    }


def _opt_float(v) -> Optional[float]:
    """float(v), but None survives as None and so does anything unparseable."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def replace_timesheets(contract_id: int, rows: list) -> int:
    """Swap in a fresh synced batch for a contract (delete-then-insert), so a
    re-sync never double-counts. Returns the number of rows stored."""
    conn = get_conn()
    conn.execute("DELETE FROM timesheets WHERE contract_id = ?", (contract_id,))
    conn.executemany(
        """INSERT INTO timesheets
           (contract_id, employee, employee_id, week_ending, charge_code,
            labor_category, total_hours, reg_hours, ot_hours, holiday_hours,
            leave_hours, paid_hours, contract_no)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                contract_id,
                r.get("employee"),
                r.get("employee_id"),
                r.get("week_ending"),
                str(r.get("charge_code")) if r.get("charge_code") is not None else None,
                r.get("labor_category"),
                float(r.get("total_hours") or 0),
                # The split columns stay None when the source doesn't send them —
                # `or 0` would forge a zero, and a missing reg_hours is exactly the
                # signal `burn.billable_hours` uses to spot a pre-split row.
                _opt_float(r.get("reg_hours")),
                _opt_float(r.get("ot_hours")),
                _opt_float(r.get("holiday_hours")),
                _opt_float(r.get("leave_hours")),
                _opt_float(r.get("paid_hours")),
                r.get("contract_no"),
            )
            for r in rows
        ],
    )
    conn.commit()
    conn.close()
    return len(rows)


def get_timesheets(contract_id: int) -> list:
    conn = get_conn()
    rows = conn.execute(
        """SELECT employee, employee_id, week_ending, charge_code, labor_category,
                  total_hours, reg_hours, ot_hours, holiday_hours, leave_hours,
                  paid_hours, contract_no, synced_at
           FROM timesheets WHERE contract_id = ? ORDER BY week_ending""",
        (contract_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_expense(
    contract_id: int,
    clin: str,
    date: str,
    description: str,
    category: str,
    amount: float,
) -> dict:
    """Log one non-labor actual against a contract's CLIN. Returns the new row."""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO expenses (contract_id, clin, date, description, category, amount)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (contract_id, clin, date, description, category, float(amount or 0)),
    )
    conn.commit()
    eid = cur.lastrowid
    conn.close()
    return {
        "id": eid,
        "contract_id": contract_id,
        "clin": clin,
        "date": date,
        "description": description,
        "category": category,
        "amount": float(amount or 0),
    }


def list_expenses(contract_id: int, clin: Optional[str] = None) -> list:
    """All logged expenses for a contract, newest first. Optionally scoped to one
    CLIN."""
    conn = get_conn()
    if clin is not None:
        rows = conn.execute(
            """SELECT id, contract_id, clin, date, description, category, amount
               FROM expenses WHERE contract_id = ? AND clin = ?
               ORDER BY date DESC, id DESC""",
            (contract_id, clin),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, contract_id, clin, date, description, category, amount
               FROM expenses WHERE contract_id = ? ORDER BY date DESC, id DESC""",
            (contract_id,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_plan(contract_id: int, name: str, data: dict) -> dict:
    """Persist one named allocation what-if plan for a contract. Returns the row."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO plans (contract_id, name, data) VALUES (?, ?, ?)",
        (contract_id, name, json.dumps(data)),
    )
    conn.commit()
    pid = cur.lastrowid
    row = conn.execute(
        "SELECT id, name, created_at FROM plans WHERE id = ?", (pid,)
    ).fetchone()
    conn.close()
    return dict(row)


def list_plans(contract_id: int) -> list:
    """A contract's saved plans, newest first, with their full sim state."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, name, data, created_at FROM plans
           WHERE contract_id = ? ORDER BY id DESC""",
        (contract_id,),
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "created_at": r["created_at"],
            "data": json.loads(r["data"]),
        }
        for r in rows
    ]


def delete_plan(contract_id: int, plan_id: int) -> bool:
    """Delete one saved plan (scoped to its contract). True if a row went."""
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM plans WHERE id = ? AND contract_id = ?", (plan_id, contract_id)
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def delete_expense(contract_id: int, expense_id: int) -> bool:
    """Remove one expense (scoped to its contract). Returns True if a row went."""
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM expenses WHERE id = ? AND contract_id = ?",
        (expense_id, contract_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


# --- People directory (#69) -------------------------------------------------
#
# Two halves, kept apart on purpose. `people_charging_facts` is a plain group-by
# over the timesheet cache — no burn, no rate resolution — because listing who
# exists must stay a cheap read. Utilisation is the expensive question (it needs a
# burn pass per contract) and is served separately, on demand, by
# /api/people/utilization.


def people_charging_facts() -> list:
    """One row per (contract, person, CLIN, LCAT) they have ever charged.

    This grain is chosen for #66: the compliance check runs per (person, contract,
    CLIN) against one global set of credentials, because the same person can
    legitimately bill different categories on different contracts.

    Deliberately reports no hours or money. `weeks` is a row count, so the caller
    can tell a one-week blip from a standing assignment without pricing anything —
    which keeps this query out of the billable-vs-paid hours question entirely
    (that belongs to burn, and to the utilisation endpoint that reads it).

    Blank employee ids are excluded rather than collapsed together; see
    `unidentified_timesheet_rows` for how they stay visible.
    """
    conn = get_conn()
    rows = conn.execute(
        """SELECT contract_id,
                  employee_id,
                  MAX(employee) AS employee,
                  charge_code,
                  labor_category,
                  COUNT(DISTINCT week_ending) AS weeks,
                  MIN(week_ending) AS first_week,
                  MAX(week_ending) AS last_week
           FROM timesheets
           WHERE employee_id IS NOT NULL AND TRIM(employee_id) <> ''
           GROUP BY contract_id, employee_id, charge_code, labor_category"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def unidentified_timesheet_rows() -> dict:
    """Timesheet rows with no usable employee id — a data-quality figure, surfaced
    rather than silently dropped.

    "Malformed" is deliberately read as blank/whitespace only. Runway does not know
    what a given customer's employee-id format looks like, so anything stricter
    would invent a rule and quietly discard real people.
    """
    conn = get_conn()
    r = conn.execute(
        """SELECT COUNT(*) AS rows,
                  COUNT(DISTINCT contract_id) AS contracts
           FROM timesheets
           WHERE employee_id IS NULL OR TRIM(employee_id) = ''"""
    ).fetchone()
    conn.close()
    return {"rows": r["rows"] or 0, "contracts": r["contracts"] or 0}


def person_charged_ids() -> set:
    """Every employee_id that appears in any timesheet. The authority for whether a
    person is derived or manually added, and the guard on deleting one."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT DISTINCT employee_id FROM timesheets
           WHERE employee_id IS NOT NULL AND TRIM(employee_id) <> ''"""
    ).fetchall()
    conn.close()
    return {r["employee_id"] for r in rows}


def list_manual_people() -> list:
    """The hand-added people. Everyone else in the directory is derived."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT employee_id, name, id_provisional, created_at
           FROM people ORDER BY created_at DESC, employee_id"""
    ).fetchall()
    conn.close()
    return [
        {
            "employee_id": r["employee_id"],
            "name": r["name"],
            "id_provisional": bool(r["id_provisional"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def _next_provisional_id(conn) -> str:
    """Mint the next RW-#### id for a manually-added person with no id.

    Visibly provisional by design: the user was offered the real payroll id and
    didn't have it, so this id cannot silently pass for one. When the person later
    turns up in a feed under their real id, `people.merge_candidates` spots the
    pair by name and offers a merge — it never joins them automatically, because a
    name match is not an identity match and merging is the destructive direction.
    """
    rows = conn.execute(
        "SELECT employee_id FROM people WHERE employee_id LIKE 'RW-%'"
    ).fetchall()
    used = []
    for r in rows:
        try:
            used.append(int(str(r["employee_id"]).split("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return f"RW-{(max(used) + 1) if used else 1:04d}"


def add_manual_person(employee_id: Optional[str], name: str) -> Optional[dict]:
    """Add a person by hand. Returns the new row, or None if that id is already taken.

    A typed-in employee id is preferred and is the whole point: give Runway the real
    payroll id and the person links up to their own timesheets automatically the
    first time a feed carries them, instead of forking into a second profile. The
    minted fallback exists so not knowing the id can't block adding someone.
    """
    clean_name = (name or "").strip()
    typed = (employee_id or "").strip()
    conn = get_conn()
    if typed:
        taken = conn.execute(
            "SELECT 1 FROM people WHERE employee_id = ?", (typed,)
        ).fetchone()
        if taken:
            conn.close()
            return None
        eid, provisional = typed, 0
    else:
        eid, provisional = _next_provisional_id(conn), 1
    conn.execute(
        "INSERT INTO people (employee_id, name, id_provisional) VALUES (?, ?, ?)",
        (eid, clean_name, provisional),
    )
    conn.commit()
    conn.close()
    return {
        "employee_id": eid,
        "name": clean_name,
        "id_provisional": bool(provisional),
    }


def delete_manual_person(employee_id: str) -> bool:
    """Remove a manually-added person and their quals. True if a row went.

    Only ever reaches a `people` row, so a person with timesheet hours cannot be
    deleted out of the directory — their presence is a fact about the feed, not
    something the directory is allowed an opinion on. The caller checks that first
    and returns a 409; this is the second line of the same rule.
    """
    conn = get_conn()
    cur = conn.execute("DELETE FROM people WHERE employee_id = ?", (employee_id,))
    conn.execute("DELETE FROM person_attrs WHERE employee_id = ?", (employee_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def list_person_attrs(employee_id: Optional[str] = None) -> list:
    """Qualification assertions, for everyone or for one person."""
    conn = get_conn()
    if employee_id is None:
        rows = conn.execute(
            """SELECT employee_id, field, value, source_note, authored_by, authored_at
               FROM person_attrs"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT employee_id, field, value, source_note, authored_by, authored_at
               FROM person_attrs WHERE employee_id = ?""",
            (employee_id,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_person_attrs(
    employee_id: str, attrs: dict, authored_by: Optional[str] = None
) -> list:
    """Upsert one person's quals. Partial by design — only the fields present in
    `attrs` are touched, so saving a clearance never disturbs a years-of-experience
    assertion someone sourced separately.

    A field whose value comes in blank is deleted rather than stored empty, because
    `unknown` has to be reachable again after a mistake or "optional" isn't true.
    """
    conn = get_conn()
    for field, entry in (attrs or {}).items():
        entry = entry or {}
        value = (str(entry.get("value") or "")).strip()
        if not value:
            conn.execute(
                "DELETE FROM person_attrs WHERE employee_id = ? AND field = ?",
                (employee_id, field),
            )
            continue
        conn.execute(
            """INSERT INTO person_attrs
               (employee_id, field, value, source_note, authored_by, authored_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT (employee_id, field) DO UPDATE SET
                 value = excluded.value,
                 source_note = excluded.source_note,
                 authored_by = excluded.authored_by,
                 authored_at = excluded.authored_at""",
            (
                employee_id,
                field,
                value,
                (str(entry.get("source_note") or "")).strip() or None,
                (authored_by or "").strip() or None,
            ),
        )
    conn.commit()
    conn.close()
    return list_person_attrs(employee_id)


def merge_person(from_id: str, into_id: str) -> bool:
    """Fold a provisional manually-added person into a real employee id.

    Carries their quals across (keeping any the target already has — an assertion
    someone sourced against the real person wins over one typed against a
    placeholder) and drops the provisional row. Only ever moves authored data;
    timesheet history is untouched because the provisional id never had any.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM people WHERE employee_id = ? AND id_provisional = 1",
        (from_id,),
    ).fetchone()
    if row is None:
        conn.close()
        return False
    conn.execute(
        """UPDATE OR IGNORE person_attrs SET employee_id = ?
           WHERE employee_id = ?""",
        (into_id, from_id),
    )
    conn.execute("DELETE FROM person_attrs WHERE employee_id = ?", (from_id,))
    conn.execute("DELETE FROM people WHERE employee_id = ?", (from_id,))
    conn.commit()
    conn.close()
    return True
