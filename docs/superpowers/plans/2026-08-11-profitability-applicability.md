# Profitability Applicability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render Profitability labels and non-applicable states from each CLIN's pricing-policy metadata.

**Architecture:** Pure functions in `profitability.js` translate policy metadata into a presentation model. `Profitability.jsx` consumes that model without duplicating contract-type rules.

**Tech Stack:** JavaScript, React, Node test runner

## Global Constraints

- Frontend only; do not edit server rate, database, burn, or pricing code.
- Preserve existing cost/revenue/fee truth gates and their withholding reasons.
- Use `N/A` only when policy metadata proves a concept is non-applicable.
- Unknown policy remains distinct from non-applicable and produces a visible count.

---

### Task 1: Policy-driven Profitability presentation

**Files:**
- Modify: `web/src/profitability.js`
- Modify: `web/src/profitability.test.js`
- Modify: `web/src/views/Profitability.jsx`

**Interfaces:**
- Consumes: `clin.pricing_policy.{known,family,ceiling_meaning,revenue_basis}` and `burn.contract.pricing_unknown`.
- Produces: `pricingApplicability(clin)` and `profitabilityLabels(burn)`.

- [ ] **Step 1: Write the failing tests**

Add literal assertions for representative policies:

```js
assert.deepEqual(pricingApplicability(ffp), {
  known: true,
  ceilingLabel: "Firm price",
  earningsLabel: "Profit",
  returnLabel: "Margin",
  earningsApplicable: true,
  returnApplicable: true,
});
assert.equal(pricingApplicability(noFeeCost).earningsApplicable, false);
assert.equal(profitabilityLabels(unknownBurn).unknownCount, 1);
```

Cover T&M, CPFF, CPAF, CPIF, FPI, a no-fee cost policy, mixed labels, and the unknown count.

- [ ] **Step 2: Verify RED**

Run: `node --test src/profitability.test.js`

Expected: import/export failure because the presentation helpers do not exist.

- [ ] **Step 3: Implement the pure presentation model**

Add mappings keyed by the payload's semantic fields, not raw displayed type text. Add a non-applicable figure shape:

```js
const notApplicable = (why) => ({
  value: null,
  withheld: why,
  notApplicable: true,
});
```

Use it for pass-through and no-fee cost policies. Aggregate homogeneous summary labels and count unknown policies without treating them as non-applicable.

- [ ] **Step 4: Render the model**

Import the helpers in `Profitability.jsx`, render `N/A` when `figure.notApplicable`, add the unknown-policy warning, use policy-driven summary labels, and add row-level price/earnings/return captions beneath neutral table headers.

- [ ] **Step 5: Verify GREEN and build**

Run:

```bash
node --test src/profitability.test.js
npm test
npm run build
```

Expected: all tests pass and Vite builds successfully.

- [ ] **Step 6: Commit**

```bash
git add web/src/profitability.js web/src/profitability.test.js web/src/views/Profitability.jsx
git commit -m "fix(profitability): honor pricing applicability (#162)"
```
