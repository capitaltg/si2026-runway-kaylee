# FFP Base Obligation and Missing Option Modification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan.

**Goal:** Make initial FFP awards report missing Base-period obligations as fully obligated while keeping every option unexercised and unfunded until an explicit option-exercise modification is ingested, and warn when timesheets indicate performance in such an option.

**Architecture:** Add a deterministic award-only normalization step immediately after schema extraction. It will correct initial-award option state and fill only null Base-period FFP CLIN obligations, preserving explicit values and leaving modification extraction untouched. Add a read-only diagnostic in the Flight Deck computation that detects positive option activity without matching exercise history and exposes it through the existing scope-notice flow.

**Tech Stack:** Python 3, Pydantic, pytest, JavaScript, Vitest.

---

### Task 1: Normalize initial FFP awards

**Files:**
- Modify: `server/tests/test_extract.py`
- Modify: `server/app/extract.py`

**Step 1: Write failing normalization tests**

Add a W45983-shaped FFP extraction with Base and Option 1 periods and CLINs. Assert that normalization:

- defaults null Base obligations to their ceilings, including the Base COST/travel line;
- preserves explicit Base obligations, including explicit zero;
- leaves Option 1 obligations null;
- forces initial-award option periods to `exercised = false` while retaining Base as exercised;
- recomputes `total_obligated` to `3_037_736.80` and `incrementally_funded` to false when every Base CLIN is resolved;
- does not apply the FFP default to a non-FFP award;
- does not manufacture a header total if any Base ceiling remains missing.

Run: `python3 -m pytest -q server/tests/test_extract.py`

Expected: FAIL because award normalization does not exist and the old option prompt/rules remain.

**Step 2: Implement the deterministic award normalization**

In `server/app/extract.py`:

- revise the award prompt so the Base is exercised and options are false absent explicit exercise evidence;
- add `normalize_initial_award(parsed)` and call it from `_parse` after `normalize_obligations`;
- identify Base by the first period in schedule order;
- use the pricing classifier to require an overall FFP contract;
- fill only Base CLINs whose `obligated is None` and whose ceiling is known;
- preserve all explicit obligations and all option obligations;
- recompute the header total and incremental flag only when all Base CLIN obligations are resolved.

**Step 3: Run targeted tests**

Run: `python3 -m pytest -q server/tests/test_extract.py`

Expected: PASS.

**Step 4: Commit**

Commit message: `fix: normalize FFP base award obligations`

### Task 2: Detect option performance without an exercise modification

**Files:**
- Create: `server/tests/test_missing_option_mod.py`
- Modify: `server/app/burn.py`

**Step 1: Write failing diagnostic tests**

Test the real `burn.compute` payload with an unexercised Option 1 and assert:

- a positive timesheet charge to an Option 1 CLIN produces one `missing_option_mods` item;
- a positive row dated in Option 1 produces the signal even if its charge code cannot be mapped;
- Base-only/no-positive activity produces no signal;
- explicit option-exercise history suppresses the signal;
- the signal does not exercise the period or add funding.

Run: `python3 -m pytest -q server/tests/test_missing_option_mod.py`

Expected: FAIL because the payload does not expose the diagnostic.

**Step 2: Implement the read-only diagnostic**

Add `_missing_option_mods(contract, rows)` in `server/app/burn.py`. For each unexercised non-Base period, detect positive billable activity by matching the option's CLIN codes or its date window. Suppress the warning when contract history contains an explicit `option_exercise` for that period, accepting both structured history and the existing human-readable action shape. Return period and affected CLIN metadata without mutating the contract.

Expose the result as `contract.missing_option_mods` in `compute`.

**Step 3: Run targeted tests**

Run: `python3 -m pytest -q server/tests/test_missing_option_mod.py`

Expected: PASS.

**Step 4: Commit**

Commit message: `feat: flag performance missing option exercise mod`

### Task 3: Surface the warning in Flight Deck

**Files:**
- Modify: `web/src/scope-notice.js`
- Modify: `web/src/scope-notice.test.js`
- Modify: `web/src/flight-deck-alerts.test.js`

**Step 1: Write failing UI tests**

Assert that each diagnostic generates this exact scope notice:

`Option 1 performance detected on timesheets, but the Option 1 SF-30 funding modification has not been ingested.`

Also assert it enters the existing Flight Deck scope-alert ordering.

Run: `npm test -- --run src/scope-notice.test.js src/flight-deck-alerts.test.js`

Expected: FAIL because no notice is generated.

**Step 2: Add the scope notice**

Map `missing_option_mods` to stable, period-keyed notices in `scopeNotices`. Keep the existing Flight Deck renderer and ordering unchanged so this warning follows the established high-visibility scope path.

**Step 3: Run targeted tests**

Run: `npm test -- --run src/scope-notice.test.js src/flight-deck-alerts.test.js`

Expected: PASS.

**Step 4: Commit**

Commit message: `feat: warn when option modification is missing`

### Task 4: Verify the exact contract arithmetic and full application

**Files:**
- Modify if needed: `server/tests/test_mod_clin_funding.py`

**Step 1: Add or confirm the provenance regression**

Use the existing modification merge path with a W45983-shaped Option 1 exercise. Assert the Base-only award starts at `3_037_736.80`; a legitimate SF-30 action of `2_922_481.60` exercises Option 1 and yields cumulative obligations of `5_960_218.40`.

This is a code-path verification only: do not fabricate or ingest an SF-30 document that was not supplied.

**Step 2: Run all verification**

Run:

- `FIXTURA_PATH=/Users/kayleeplecas/code/si2026-test-generator-kaylee python3 -m pytest -q`
- `npm test`
- `git diff --check`

Expected: all tests pass and the diff is clean.

**Step 3: Commit any final regression test**

Commit message: `test: verify W45983 option funding provenance`
