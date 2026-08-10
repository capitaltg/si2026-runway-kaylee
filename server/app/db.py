import json
import os
import sqlite3
from typing import Optional

from . import absence as absence_mod
from . import documents
from . import lcat
from . import people
from . import pricing

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
    # plan reloads exactly as it was modeled, plus `scored_against` — the contract
    # terms in force when it was saved, which is how the matrix knows a plan has
    # gone stale (#67).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER,
            name TEXT,
            data TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    # NULL until the plan is first saved over, which is the distinction the menu
    # draws: "Saved 12 Jun" vs "Updated 3 Aug". A default of `created_at` would
    # claim every plan had been edited.
    _add_missing_columns(conn, "plans", {"updated_at": "TEXT"})
    # The active baseline — "this is the staffing we said we'd run" (#67 item 1). At
    # most one per contract, enforced in SQL rather than by the caller: two baselines
    # would make drift-vs-baseline ambiguous, and that ambiguity would surface as a
    # wrong number on the Flight Deck rather than as an error anyone could see. The
    # index is partial so the many non-baseline plans don't collide with each other.
    _add_missing_columns(conn, "plans", {"is_baseline": "INTEGER DEFAULT 0"})
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_plans_one_baseline
           ON plans (contract_id) WHERE is_baseline = 1"""
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
    # The uploaded award / rate schedule a contract's numbers were extracted from
    # (#30) — the audit trail ingest used to discard.
    #
    # Its own table, not a column on `contracts`, for three reasons that all bite
    # later: `get_contract` splats the whole row into every payload and would start
    # carrying a multi-megabyte blob into the burn response; one contract can have
    # more than one document (an award plus a rate schedule today, a cost buildup
    # once #78 lands); and a blob column on the hot table makes every contract read
    # pay for bytes it never uses.
    #
    # `contract_id` is nullable on purpose. Ingest is two steps — extract, then
    # confirm — and the bytes arrive at step one, before the contract that will own
    # them exists. A row with a NULL contract_id is an upload whose extraction the
    # user has not confirmed yet; `purge_unclaimed_documents` sweeps the abandoned
    # ones, so a review screen someone closed never leaves a document attached to
    # nothing.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS contract_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER,
            kind TEXT,
            filename TEXT,
            content_type TEXT,
            size_bytes INTEGER,
            sha256 TEXT,
            blob BLOB,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    # The `kind` column shipped with an inline CHECK listing the two kinds that
    # existed then, and SQLite cannot alter a CHECK constraint — so #78's rate
    # agreement was rejected by every database created before it, with
    # `CREATE TABLE IF NOT EXISTS` silently declining to fix it. Rebuilt once, here,
    # and the allowed set now lives in `documents.KINDS` where the other document
    # rules already are: a constraint that has to be migrated to add a document kind
    # is a constraint in the wrong layer.
    if (
        "CHECK (kind IN"
        in (
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='contract_documents'"
            ).fetchone()
            or {"sql": ""}
        )["sql"]
    ):
        conn.executescript(
            """
            ALTER TABLE contract_documents RENAME TO contract_documents_old;
            CREATE TABLE contract_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id INTEGER,
                kind TEXT,
                filename TEXT,
                content_type TEXT,
                size_bytes INTEGER,
                sha256 TEXT,
                blob BLOB,
                created_at TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO contract_documents
                SELECT id, contract_id, kind, filename, content_type, size_bytes,
                       sha256, blob, created_at
                FROM contract_documents_old;
            DROP TABLE contract_documents_old;
            """
        )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_contract_documents_contract
           ON contract_documents (contract_id)"""
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


def get_scoped_direct_rates(contract_id: Optional[int]) -> list:
    """The direct-rate rows belonging to exactly this scope — no company-default
    fill-in (#78).

    `get_rate_model` merges the company defaults in, which is right for pricing and
    wrong for writing: a caller that read the merged view and saved it back would
    silently copy every company rate onto the contract, and a later change to the
    company set would stop reaching it. Any code that merges *into* a scope needs
    this instead.
    """
    conn = get_conn()
    where, params = _scope_clause(contract_id)
    rows = conn.execute(
        f"""SELECT fiscal_year, lcat, employee_id, rate, status
            FROM direct_rates WHERE {where}""",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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


def delete_contract(cid: int) -> bool:
    """Hard-delete a contract and everything hanging off it. There is no FK
    cascade in this schema, so every contract-scoped table is cleared explicitly,
    in one transaction. Returns False if the id doesn't exist.

    `rate_sets` / `direct_rates` also hold company-wide rows (`contract_id IS
    NULL`) — scoping on `contract_id = ?` leaves those defaults alone, which is
    the whole point. `people` / `person_attrs` are not contract-scoped: identity
    is derived from charging, so a person who only ever appeared here simply
    stops resolving once the timesheets go.
    """
    conn = get_conn()
    try:
        if (
            conn.execute("SELECT 1 FROM contracts WHERE id = ?", (cid,)).fetchone()
            is None
        ):
            return False
        for table in (
            "timesheets",
            "expenses",
            "plans",
            "rate_sets",
            "direct_rates",
            "contract_documents",
        ):
            conn.execute(f"DELETE FROM {table} WHERE contract_id = ?", (cid,))
        conn.execute("DELETE FROM contracts WHERE id = ?", (cid,))
        conn.commit()
        return True
    finally:
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


def set_contract_capacity(
    cid: int,
    utilization_target=None,
    lcat_expected_hours=None,
) -> Optional[dict]:
    """Set (or clear) a contract's utilisation target and per-LCAT expected hours (#84).

    Stored on the data blob, like `nickname` and the LCAT aliases, so it splats out of
    `get_contract`/`list_contracts` and needs no migration — every contract in the DB
    predates these keys and reads clean without them.

    Passing None for either leaves it alone; passing an empty value clears it back to
    the default, which has to stay reachable or "default" is a one-way door. Returns
    the refreshed contract, or None if it doesn't exist.
    """
    existing = get_contract(cid)
    if existing is None:
        return None
    blob = {k: v for k, v in existing.items() if k not in ("id", "piid", "created_at")}

    if utilization_target is not None:
        if utilization_target == "":
            blob.pop("utilization_target", None)
        else:
            blob["utilization_target"] = float(utilization_target)

    if lcat_expected_hours is not None:
        # Replace rather than merge: the editor sends the whole map, and a merge would
        # make removing one category's default impossible.
        clean = {
            str(name).strip(): float(hours)
            for name, hours in (lcat_expected_hours or {}).items()
            if str(name).strip() and hours not in (None, "")
        }
        if clean:
            blob["lcat_expected_hours"] = clean
        else:
            blob.pop("lcat_expected_hours", None)

    update_contract(cid, blob)
    return get_contract(cid)


def set_contract_absence(cid: int, holidays=None, absences=None) -> Optional[dict]:
    """Set (or clear) a contract's holiday calendar and per-person absences (#85).

    Same storage and the same conventions as `set_contract_capacity` above: on the
    data blob so no migration is needed, None leaves a list alone, and an empty list
    clears it — a contract that observes no holidays has to be expressible, and
    deleting the last absence has to actually delete it rather than leaving the
    previous list in place.

    Both lists are replaced wholesale rather than merged, for the reason
    `lcat_expected_hours` is: the editor sends the whole list, and a merge would make
    removing one entry impossible.

    **Contract-level on purpose.** A holiday is a fact about the calendar rather than
    about one what-if, and the burn engine cannot read plan data — so absence stored
    in a plan could never bend the Flight Deck's chart. See `absence.contract_absence`
    for the consequence this accepts: editing the calendar changes what every saved
    plan projects.
    """
    existing = get_contract(cid)
    if existing is None:
        return None
    blob = {k: v for k, v in existing.items() if k not in ("id", "piid", "created_at")}

    if holidays is not None:
        clean = absence_mod.normalize_holidays(holidays)
        if clean:
            blob["holidays"] = clean
        else:
            blob.pop("holidays", None)

    if absences is not None:
        clean = absence_mod.normalize_absences(absences)
        if clean:
            blob["absences"] = clean
        else:
            blob.pop("absences", None)

    update_contract(cid, blob)
    return get_contract(cid)


def set_contract_fee_periods(cid: int, periods) -> Optional[dict]:
    """Set (or clear) a contract's award-fee evaluation periods (#80).

    Same storage and conventions as `set_contract_capacity` / `set_contract_absence`
    above: on the data blob so no migration is needed, and the list is replaced
    wholesale because the editor sends all of it — a merge would make deleting a
    period impossible.

    **Entered, not extracted.** The award prints the pool; only the government's
    determination says what was earned of it, and that arrives quarterly in a letter
    rather than in the award document. So this is the one fee input a user types, and
    `pricing.normalize_fee_periods` is what keeps a half-filled row from reaching the
    engine as revenue.
    """
    existing = get_contract(cid)
    if existing is None:
        return None
    blob = {k: v for k, v in existing.items() if k not in ("id", "piid", "created_at")}

    clean = [dict(p) for p in pricing.normalize_fee_periods(periods)]
    if clean:
        blob["fee_periods"] = clean
    else:
        # Clearing has to stay reachable: an award-fee plan can be re-cut, and the
        # last period has to be deletable or the list is a one-way door.
        blob.pop("fee_periods", None)

    update_contract(cid, blob)
    return get_contract(cid)


def expected_hours_by_person() -> dict:
    """Every per-person expected-hours override, as `{employee_id: value}` (#84).

    Read here and passed *into* `allocation.compute_allocation` rather than looked up
    there, because allocation must not reach the people directory — see the invariant
    in people.py. One query for the whole sweep, not one per contract.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT employee_id, value FROM person_attrs WHERE field = 'expected_hours'"
    ).fetchall()
    conn.close()
    return {r["employee_id"]: r["value"] for r in rows}


def quals_by_person() -> dict:
    """Every stored credential, as `{employee_id: {field: {value, source_note}}}` (#66).

    The compliance check's half of `expected_hours_by_person`, and passed in for the
    same reason. Only the three comparable fields (`people.QUAL_FIELDS`) are selected:
    expected hours share this table and are a week, not a credential, and a field of
    study has no rank to compare, so neither belongs in a dict called `quals`.

    The source note comes along because the panel that shows a failing check has to
    show *why we believe the number* — "3 yrs · per resume, 2026-03" is a different
    conversation from a bare 3, and it is the first thing anyone disputes.
    """
    fields = tuple(people.QUAL_FIELDS)
    conn = get_conn()
    rows = conn.execute(
        f"""SELECT employee_id, field, value, source_note, authored_by, authored_at
            FROM person_attrs
            WHERE field IN ({','.join('?' * len(fields))})""",
        fields,
    ).fetchall()
    conn.close()
    out: dict = {}
    for r in rows:
        out.setdefault(r["employee_id"], {})[r["field"]] = {
            "value": r["value"],
            "source_note": r["source_note"],
            "authored_by": r["authored_by"],
            "authored_at": r["authored_at"],
        }
    return out


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


# One shape for a plan row wherever it leaves this module, so the menu, the save
# response and the baseline call can't disagree about whether a plan is the baseline.
_PLAN_ROW_SQL = (
    "SELECT id, name, created_at, updated_at, is_baseline FROM plans WHERE id = ?"
)


def _plan_row(row) -> dict:
    d = dict(row)
    d["is_baseline"] = bool(d.get("is_baseline"))
    return d


def save_plan(contract_id: int, name: str, data: dict) -> dict:
    """Persist one named allocation what-if plan for a contract. Returns the row."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO plans (contract_id, name, data) VALUES (?, ?, ?)",
        (contract_id, name, json.dumps(data)),
    )
    conn.commit()
    pid = cur.lastrowid
    row = conn.execute(_PLAN_ROW_SQL, (pid,)).fetchone()
    conn.close()
    return _plan_row(row)


def update_plan(contract_id: int, plan_id: int, name: str, data: dict):
    """Overwrite one existing plan in place. None if it isn't this contract's.

    Saving an edited plan has to update it, not fork it: a POST-only API meant that
    "load Q3 crew-up, nudge a cell, save" left two plans with the same name and no
    way to tell which one anybody meant (#62).
    """
    conn = get_conn()
    cur = conn.execute(
        """UPDATE plans SET name = ?, data = ?, updated_at = datetime('now')
           WHERE id = ? AND contract_id = ?""",
        (name, json.dumps(data), plan_id, contract_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        conn.close()
        return None
    row = conn.execute(_PLAN_ROW_SQL, (plan_id,)).fetchone()
    conn.close()
    return _plan_row(row)


def list_plans(contract_id: int) -> list:
    """A contract's saved plans, newest first, with their full sim state.

    The baseline sorts first: it is the one plan the contract is actually being run
    against, so it should not be one of eleven rows in save order.
    """
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, name, data, created_at, updated_at, is_baseline FROM plans
           WHERE contract_id = ? ORDER BY is_baseline DESC, id DESC""",
        (contract_id,),
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "is_baseline": bool(r["is_baseline"]),
            "data": json.loads(r["data"]),
        }
        for r in rows
    ]


def set_baseline_plan(contract_id: int, plan_id: Optional[int]):
    """Designate one saved plan as the contract's active baseline, or clear it.

    Designating is a swap, not a set: the old baseline is stood down in the same
    transaction, because the partial unique index means a second baseline is a
    write error rather than a silent second answer to "what did we commit to?".

    Returns the new baseline row, None when cleared, and raises LookupError if the
    plan isn't this contract's — a baseline pointing at another award's staffing
    would produce drift numbers that look real.
    """
    conn = get_conn()
    try:
        if plan_id is not None:
            owned = conn.execute(
                "SELECT 1 FROM plans WHERE id = ? AND contract_id = ?",
                (plan_id, contract_id),
            ).fetchone()
            if owned is None:
                raise LookupError("Plan not found on this contract.")
        conn.execute(
            "UPDATE plans SET is_baseline = 0 WHERE contract_id = ? AND is_baseline = 1",
            (contract_id,),
        )
        if plan_id is None:
            conn.commit()
            return None
        conn.execute("UPDATE plans SET is_baseline = 1 WHERE id = ?", (plan_id,))
        conn.commit()
        return _plan_row(conn.execute(_PLAN_ROW_SQL, (plan_id,)).fetchone())
    finally:
        conn.close()


def get_baseline_plan(contract_id: int):
    """The contract's active baseline with its full sim state, or None."""
    conn = get_conn()
    row = conn.execute(
        """SELECT id, name, data, created_at, updated_at FROM plans
           WHERE contract_id = ? AND is_baseline = 1""",
        (contract_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "is_baseline": True,
        "data": json.loads(row["data"]),
    }


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


def save_document(
    contract_id: Optional[int],
    kind: str,
    filename: str,
    content_type: str,
    blob: bytes,
) -> dict:
    """Keep one uploaded source document. Returns its metadata (never its bytes).

    Deduplicated by content hash within a (contract, kind): re-uploading the exact
    same award returns the row already on file rather than storing a second copy of
    the same megabytes. A *different* file of the same kind is appended, not
    overwritten — a corrected award is new evidence, and the panel reads the newest,
    so replacing would destroy the older version that older numbers were derived
    from.

    Dedup is skipped while `contract_id` is NULL, because an unclaimed row belongs to
    nobody yet and folding two users' identical uploads together would hand one of
    them the other's row to claim.
    """
    # The kind check the dropped CHECK constraint used to make, now in one place and
    # against `documents.KINDS` — so adding a document kind is one edit rather than a
    # schema migration, and a typo'd kind is still refused rather than stored as a
    # document nothing will ever look for.
    if kind not in documents.KINDS:
        raise ValueError(f"Unknown document kind {kind!r}")

    conn = get_conn()
    sha = documents.digest(blob)
    if contract_id is not None:
        existing = conn.execute(
            """SELECT id, contract_id, kind, filename, content_type, size_bytes,
                      sha256, created_at
               FROM contract_documents
               WHERE contract_id = ? AND kind = ? AND sha256 = ?""",
            (contract_id, kind, sha),
        ).fetchone()
        if existing is not None:
            conn.close()
            return {**dict(existing), "duplicate": True}
    cur = conn.execute(
        """INSERT INTO contract_documents
           (contract_id, kind, filename, content_type, size_bytes, sha256, blob)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (contract_id, kind, filename, content_type, len(blob), sha, blob),
    )
    conn.commit()
    row = conn.execute(
        """SELECT id, contract_id, kind, filename, content_type, size_bytes,
                  sha256, created_at
           FROM contract_documents WHERE id = ?""",
        (cur.lastrowid,),
    ).fetchone()
    conn.close()
    return {**dict(row), "duplicate": False}


def claim_document(document_id: int, contract_id: int) -> bool:
    """Attach an ingest-time upload to the contract its extraction was confirmed as.

    Scoped to `contract_id IS NULL`, so this can only ever claim a document that
    nobody owns — a confirm that passes some other contract's document id moves
    nothing and returns False instead of quietly re-parenting evidence.
    """
    conn = get_conn()
    cur = conn.execute(
        """UPDATE contract_documents SET contract_id = ?
           WHERE id = ? AND contract_id IS NULL""",
        (contract_id, document_id),
    )
    conn.commit()
    claimed = cur.rowcount > 0
    conn.close()
    return claimed


def purge_unclaimed_documents(older_than_hours: int = 24) -> int:
    """Drop uploads whose extraction was never confirmed. Returns rows removed.

    Uploading an award and then closing the review screen is an ordinary thing to do,
    and every one of those leaves bytes owned by no contract. Swept on a delay rather
    than immediately because the review step has no bounded length — someone can
    reasonably leave it open over lunch — and deleting the document out from under a
    tab that is still going to confirm is the worse failure.
    """
    conn = get_conn()
    cur = conn.execute(
        f"""DELETE FROM contract_documents
            WHERE contract_id IS NULL
              AND created_at < datetime('now', '-{int(older_than_hours)} hours')"""
    )
    conn.commit()
    removed = cur.rowcount
    conn.close()
    return removed


def list_documents(contract_id: int) -> list:
    """A contract's stored source documents, newest first. Metadata only — the blob
    is served by `get_document`, one at a time, so listing stays cheap."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, contract_id, kind, filename, content_type, size_bytes,
                  sha256, created_at
           FROM contract_documents WHERE contract_id = ?
           ORDER BY created_at DESC, id DESC""",
        (contract_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_document(contract_id: int, document_id: int) -> Optional[dict]:
    """One stored document with its bytes, scoped to its contract so a guessed id
    can't read another contract's award. None if it isn't there."""
    conn = get_conn()
    row = conn.execute(
        """SELECT id, contract_id, kind, filename, content_type, size_bytes,
                  sha256, created_at, blob
           FROM contract_documents WHERE id = ? AND contract_id = ?""",
        (document_id, contract_id),
    ).fetchone()
    conn.close()
    return dict(row) if row is not None else None


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
