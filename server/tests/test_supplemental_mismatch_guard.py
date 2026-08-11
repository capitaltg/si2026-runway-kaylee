"""A supplemental document must not mutate the selected contract by surprise.

The extractor is the only mocked boundary.  These tests exercise the real routes and
SQLite store, then compare every application table so a rejected upload cannot hide
a side effect in contract JSON, rates, direct rates, or retained source documents.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, extract, main  # noqa: E402
from app.schemas import (  # noqa: E402
    CLIN,
    ContractHeader,
    Extraction,
    IndirectPool,
    LaborRate,
    Modification,
    RateAgreement,
)

PIID = "FA8750-26-C-0078"
OTHER_PIID = "N00019-26-C-9999"
PDF = b"%PDF-1.4 supplemental document"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def contract(client):
    response = client.post(
        "/api/contracts/confirm",
        json={
            "contract": {
                "piid": PIID,
                "effective_date": "2026-03-02",
                "total_ceiling": 1_000_000.0,
                "total_obligated": 500_000.0,
            },
            "periods": [],
            "clins": [
                {
                    "clin": "0001",
                    "title": "Engineering",
                    "type": "CPFF",
                    "is_labor": True,
                    "ceiling": 1_000_000.0,
                }
            ],
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def _database_snapshot():
    conn = db.get_conn()
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: [
                tuple(row)
                for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            ]
            for table in tables
        }
    finally:
        conn.close()


def _assert_rejected_without_mutation(response, before):
    assert response.status_code == 409
    assert OTHER_PIID in response.json()["detail"]
    assert PIID in response.json()["detail"]
    assert "allow_mismatch=true" in response.json()["detail"]
    assert _database_snapshot() == before


def _stub_mod(monkeypatch):
    monkeypatch.setattr(
        extract,
        "extract_mod_from_text",
        lambda _text: Modification(
            piid=OTHER_PIID,
            mod_number="P00001",
            effective_date="2026-08-01",
            amount_obligated=100_000.0,
            action_type="incremental_funding",
        ),
    )


def _upload_mod(client, contract, override=False):
    query = "?allow_mismatch=true" if override else ""
    return client.post(
        f"/api/contracts/{contract}/mods{query}",
        files={"file": ("mod.txt", b"fake SF30", "text/plain")},
    )


def _stub_schedule(monkeypatch):
    monkeypatch.setattr(
        extract,
        "extract_from_pdf",
        lambda _data: Extraction(
            contract=ContractHeader(piid=OTHER_PIID, effective_date="2026-03-02"),
            periods=[],
            clins=[
                CLIN(
                    clin="0001",
                    title="Engineering",
                    is_labor=True,
                    labor_rates=[
                        LaborRate(
                            lcat="Systems Engineer",
                            loaded_rate=185.0,
                            direct_rate=82.0,
                        )
                    ],
                )
            ],
        ),
    )


def _upload_schedule(client, contract, override=False):
    query = "?allow_mismatch=true" if override else ""
    return client.post(
        f"/api/contracts/{contract}/rates{query}",
        files={"file": ("rates.pdf", PDF, "application/pdf")},
    )


def _stub_agreement(monkeypatch):
    monkeypatch.setattr(
        extract,
        "extract_rate_agreement_from_pdf",
        lambda _data: RateAgreement(
            piid=OTHER_PIID,
            fiscal_year="2026",
            status="provisional",
            pools=[
                IndirectPool(pool="fringe", rate=0.268, base="direct_labor")
            ],
        ),
    )


def _upload_agreement(client, contract, override=False):
    query = "?allow_mismatch=true" if override else ""
    return client.post(
        f"/api/contracts/{contract}/rate-agreement{query}",
        files={"file": ("fpra.pdf", PDF, "application/pdf")},
    )


def test_mismatched_mod_is_rejected_without_any_database_change(
    client, contract, monkeypatch
):
    _stub_mod(monkeypatch)
    before = _database_snapshot()

    _assert_rejected_without_mutation(_upload_mod(client, contract), before)


def test_mismatched_schedule_is_rejected_without_any_database_change(
    client, contract, monkeypatch
):
    _stub_schedule(monkeypatch)
    before = _database_snapshot()

    _assert_rejected_without_mutation(_upload_schedule(client, contract), before)


def test_mismatched_rate_agreement_is_rejected_without_any_database_change(
    client, contract, monkeypatch
):
    _stub_agreement(monkeypatch)
    before = _database_snapshot()

    _assert_rejected_without_mutation(_upload_agreement(client, contract), before)


def test_allow_mismatch_explicitly_applies_mod(client, contract, monkeypatch):
    _stub_mod(monkeypatch)

    response = _upload_mod(client, contract, override=True)

    assert response.status_code == 200
    assert response.json()["piid_mismatch"] is True
    assert db.get_contract(contract)["obligation_history"][-1]["mod"] == "P00001"


def test_allow_mismatch_explicitly_imports_schedule(client, contract, monkeypatch):
    _stub_schedule(monkeypatch)

    response = _upload_schedule(client, contract, override=True)

    assert response.status_code == 200
    assert response.json()["piid_mismatch"] is True
    assert response.json()["clins_updated"] == 1
    assert response.json()["direct_rates_stored"] == 1


def test_allow_mismatch_explicitly_imports_rate_agreement(
    client, contract, monkeypatch
):
    _stub_agreement(monkeypatch)

    response = _upload_agreement(client, contract, override=True)

    assert response.status_code == 200
    assert response.json()["piid_mismatch"] is True
    assert response.json()["pools_stored"] == 1
