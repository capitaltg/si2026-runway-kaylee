import json
import os
import sqlite3
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "runway.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
            contract_no TEXT,
            synced_at TEXT DEFAULT (datetime('now'))
        )"""
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
    conn.commit()
    conn.close()


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


def replace_timesheets(contract_id: int, rows: list) -> int:
    """Swap in a fresh synced batch for a contract (delete-then-insert), so a
    re-sync never double-counts. Returns the number of rows stored."""
    conn = get_conn()
    conn.execute("DELETE FROM timesheets WHERE contract_id = ?", (contract_id,))
    conn.executemany(
        """INSERT INTO timesheets
           (contract_id, employee, employee_id, week_ending, charge_code,
            labor_category, total_hours, contract_no)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                contract_id,
                r.get("employee"),
                r.get("employee_id"),
                r.get("week_ending"),
                str(r.get("charge_code")) if r.get("charge_code") is not None else None,
                r.get("labor_category"),
                float(r.get("total_hours") or 0),
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
                  total_hours, contract_no, synced_at
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
