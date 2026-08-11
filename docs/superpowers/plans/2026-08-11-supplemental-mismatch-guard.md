# Supplemental Document Mismatch Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject mismatched supplemental uploads without any database mutation unless `?allow_mismatch=true` is explicitly supplied.

**Architecture:** Gate each supplemental route immediately after extraction and validation, before its first write. Preserve the existing mutation and response path when the explicit override is true.

**Tech Stack:** FastAPI, SQLite, pytest/TestClient

## Global Constraints

- Backend only; no frontend changes.
- Missing document PIIDs remain acceptable.
- HTTP 409 is the default mismatch response.
- Rejected requests leave every application table byte-for-value unchanged at the row level.

---

### Task 1: Guard all supplemental uploads

**Files:**
- Create: `server/tests/test_supplemental_mismatch_guard.py`
- Modify: `server/app/main.py`

**Interfaces:**
- Consumes: the three existing POST routes and their extracted `doc_piid` values.
- Produces: optional `allow_mismatch: bool = False` query parameters and 409 default-deny behavior.

- [ ] **Step 1: Write failing integration tests**

Create real TestClient tests for `/mods`, `/rates`, and `/rate-agreement`. Stub only extraction, snapshot every non-SQLite table as ordered tuples, submit a document naming another PIID, and assert:

```python
before = _database_snapshot()
response = _upload(client, contract_id)
assert response.status_code == 409
assert _database_snapshot() == before
```

Add companion requests using `?allow_mismatch=true` and assert status 200, `piid_mismatch is True`, and the route-specific mutation occurred.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m pytest -q server/tests/test_supplemental_mismatch_guard.py`

Expected: default requests return 200 and change the database, proving the regression tests catch the bug.

- [ ] **Step 3: Add the minimal early guards**

For each route, accept `allow_mismatch: bool = False`, compute the mismatch after usable content is validated, and run this branch before its first write:

```python
if piid_mismatch and not allow_mismatch:
    raise HTTPException(
        status_code=409,
        detail=(
            f"Supplemental upload refused: document names {doc_piid}, not "
            f"{existing_piid}. Retry with ?allow_mismatch=true only after "
            "reviewing the mismatch."
        ),
    )
```

- [ ] **Step 4: Verify GREEN and regressions**

Run:

```bash
python3 -m pytest -q server/tests/test_supplemental_mismatch_guard.py
python3 -m pytest -q server/tests
```

Expected: all tests pass; existing same-PIID and missing-PIID behavior is unchanged.

- [ ] **Step 5: Commit**

```bash
git add server/app/main.py server/tests/test_supplemental_mismatch_guard.py
git commit -m "fix(ingest): reject mismatched supplemental uploads (#157)"
```
