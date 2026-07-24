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
