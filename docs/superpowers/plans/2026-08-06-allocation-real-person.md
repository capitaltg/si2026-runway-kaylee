# Allocation Matrix Real-Person Add Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let allocation plans add real directory people or typed new hires against an explicit CLIN LCAT rate.

**Architecture:** Keep form derivation and validation in a pure client module so Node's existing test runner can verify the pricing invariant. Extend the allocation CLIN card with its rate-line details, then have AllocationMatrix fetch directory information only while the panel is open and persist the resulting scenario fields in its existing `added` state.

**Tech Stack:** React 18, Vite, Node test runner, FastAPI/Python.

## Global Constraints

- Use the active CLIN's priced LCATs as the default selection source.
- “Other — not on the rate schedule…” requires an explicitly typed numeric rate.
- Planned people must never receive a CLIN blended-rate fallback.
- Directory selection is a plan-local copy and must not modify directory records.
- Qualification fields are optional and plan-local.

---

### Task 1: Rate-line payload and pure scenario helpers

**Files:**
- Create: `web/src/allocation-person.js`
- Create: `web/src/allocation-person.test.js`
- Modify: `server/app/allocation.py`
- Modify: `server/tests/test_people_directory.py`

**Interfaces:**
- Produces `rateOptions(clin)`, `prefillPerson(person, utilization)`, and `validateAddedPerson(form)` for the React view.
- Produces `clin.rate_lines`, an array of `{lcat, rate, min_education, min_experience_yrs, clearance}`.

- [ ] **Step 1: Write failing tests**

```js
test("Other LCAT requires an explicit rate instead of a blended fallback", () => {
  assert.match(validateAddedPerson({ lcatChoice: "other", lcat: "Principal", rate: "" }), /rate/i);
});
```

```python
def test_allocation_clin_exposes_rate_line_qualifications():
    allocation = compute_allocation(contract_with_qualified_rate_line(), [])
    assert allocation["clins"][0]["rate_lines"][0]["min_education"] == "Bachelor's"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npm test -- allocation-person.test.js` and `pytest server/tests/test_people_directory.py -q`.

Expected: failure because helpers and CLIN `rate_lines` do not exist.

- [ ] **Step 3: Write minimal implementation**

Expose active raw rate-line details from `allocation.compute_allocation` and implement pure helpers without a blended-rate input.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && npm test -- allocation-person.test.js` and `pytest server/tests/test_people_directory.py -q`.

Expected: PASS.

### Task 2: Allocation Matrix form and rate-safe plan addition

**Files:**
- Modify: `web/src/views/AllocationMatrix.jsx`
- Modify: `web/src/api.js`

**Interfaces:**
- Consumes `getPeople`, `getPeopleUtilization`, `rateOptions`, `prefillPerson`, and `validateAddedPerson`.
- Produces added plan records with explicit `{ employeeId, lcat, rates, quals, utilization }` data.

- [ ] **Step 1: Write failing test**

```js
test("a selected rate line becomes the planned person's explicit rate", () => {
  const option = rateOptions({ rate_lines: [{ lcat: "Senior Engineer", rate: 225 }] })[0];
  assert.equal(option.rate, 225);
});
```

- [ ] **Step 2: Run test to verify it passes only after Task 1**

Run: `cd web && npm test -- allocation-person.test.js`.

Expected: PASS; the view uses this tested helper rather than reading `blended_rate`.

- [ ] **Step 3: Implement the panel**

Load directory data when opening the form. Render person search, editable identity fields, CLIN, rate-line dropdown with Other, rate, hours, optional qualifications, missing-rate-table import, and inline validation. Build `added` records only after validation and use `{ [clin]: Number(rate) }`.

- [ ] **Step 4: Verify the application**

Run: `cd web && npm test && npm run build` and `pytest server/tests/test_people_directory.py -q`.

Expected: all commands exit 0.
