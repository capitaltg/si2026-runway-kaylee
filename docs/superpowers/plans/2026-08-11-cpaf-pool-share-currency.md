# CPAF Pool-Share Currency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render CPAF award-period pool shares as dollar amounts in Profitability.

**Architecture:** Keep the server payload unchanged because it already uses dollars. Add one pure presentation helper beside the existing Profitability view-model helpers, test its user-visible output, and wire the JSX period row to it.

**Tech Stack:** React 18, JavaScript ES modules, Node's built-in test runner, Vite.

## Global Constraints

- Preserve the existing `pool_share` API contract: dollars or null.
- Reuse the existing `money` formatter.
- Do not change score or incentive-share percentage formatting.
- Do not add dependencies or unrelated refactors.

---

### Task 1: Format CPAF period shares as currency

**Files:**
- Modify: `web/src/profitability.js`
- Modify: `web/src/profitability.test.js`
- Modify: `web/src/views/Profitability.jsx`

**Interfaces:**
- Consumes: `money(number)` from `web/src/format.js` and `pool_share: number | null` from `awardPeriods` payloads.
- Produces: `awardPoolShareLabel(poolShare): string`, returning `""` for nullish input or `"$45,000 of pool"` for `45000`.

- [ ] **Step 1: Write the failing test**

Import `awardPoolShareLabel`, change the CPAF fixture shares from fractions to dollar amounts, and assert:

```js
assert.equal(awardPoolShareLabel(45000), "$45,000 of pool");
assert.equal(awardPoolShareLabel(null), "");
```

- [ ] **Step 2: Run the targeted test to verify RED**

Run: `node --test src/profitability.test.js` from `web/`.

Expected: FAIL because `awardPoolShareLabel` is not yet exported.

- [ ] **Step 3: Implement the minimal helper and wire the view**

Import `money` into `web/src/profitability.js`, export the helper, import it in `Profitability.jsx`, and replace the direct percentage formatting with the helper's text.

- [ ] **Step 4: Run the targeted test to verify GREEN**

Run: `node --test src/profitability.test.js` from `web/`.

Expected: PASS.

- [ ] **Step 5: Verify the full web surface**

Run: `npm test` and `npm run build` from `web/`.

Expected: all tests pass and Vite builds successfully.

- [ ] **Step 6: Commit the tested fix**

Stage only the design, plan, test, helper, and view files. Commit with `fix(profitability): format CPAF pool shares as currency`.
