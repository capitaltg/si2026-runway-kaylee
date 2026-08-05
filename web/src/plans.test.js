// #62 — the matrix told users their work couldn't be saved. Part of that was copy,
// but the load-bearing part was that it only knew "differs from actuals", so a
// just-loaded plan looked unsaved and a just-saved one kept looking unsaved.
//
// What's pinned here: the fingerprint ignores differences that aren't edits (key
// order, member order, a cell zeroed back out), and `unsaved` means "differs from the
// loaded plan" — not "differs from actuals" — while still falling back to that when
// no plan is loaded.

import { test } from "node:test";
import assert from "node:assert/strict";
import { planFingerprint, isUnsaved } from "./plans.js";

const PLAN = {
  draft: { 7: { 1: 20, 2: 12 }, 9: { 1: 40 } },
  added: [{ id: "added-1", name: "New BA", clin: 1, hrs: 32 }],
  removed: [4],
  absences: [{ personId: 7, kind: "pto", start: "2026-05-04", end: "2026-05-08" }],
};

// ------------------------------------------------------------------ fingerprint

test("a round trip through JSON fingerprints the same", () => {
  assert.equal(planFingerprint(JSON.parse(JSON.stringify(PLAN))), planFingerprint(PLAN));
});

test("key and member order are not edits", () => {
  const shuffled = {
    absences: PLAN.absences,
    added: PLAN.added,
    removed: PLAN.removed,
    draft: { 9: { 1: 40 }, 7: { 2: 12, 1: 20 } },
  };
  assert.equal(planFingerprint(shuffled), planFingerprint(PLAN));

  const twoAbsences = (order) => ({
    absences: order.map((d) => ({ personId: 7, kind: "pto", start: d, end: d })),
  });
  assert.equal(
    planFingerprint(twoAbsences(["2026-05-04", "2026-07-02"])),
    planFingerprint(twoAbsences(["2026-07-02", "2026-05-04"]))
  );
});

test("typing a number into a cell and deleting it again is not an edit", () => {
  const zeroed = { ...PLAN, draft: { ...PLAN.draft, 9: { 1: 40, 3: 0 }, 11: { 1: 0 } } };
  assert.equal(planFingerprint(zeroed), planFingerprint(PLAN));
});

test("but a real hours change is", () => {
  const bumped = { ...PLAN, draft: { ...PLAN.draft, 9: { 1: 41 } } };
  assert.notEqual(planFingerprint(bumped), planFingerprint(PLAN));
});

test("a missing key and an empty one agree, so a pre-#85 plan doesn't read as edited", () => {
  // Plans saved before dated absence shipped have no `absences` key at all.
  const { absences, ...pre85 } = PLAN;
  assert.equal(planFingerprint({ ...pre85, absences: [] }), planFingerprint(pre85));
  assert.equal(planFingerprint({}), planFingerprint({ draft: {}, added: [], removed: [] }));
});

test("roster edits register", () => {
  assert.notEqual(planFingerprint({ ...PLAN, added: [] }), planFingerprint(PLAN));
  assert.notEqual(planFingerprint({ ...PLAN, removed: [] }), planFingerprint(PLAN));
  assert.notEqual(planFingerprint({ ...PLAN, absences: [] }), planFingerprint(PLAN));
});

// ---------------------------------------------------------------------- unsaved

test("a loaded plan is compared against itself, not against the actuals", () => {
  const fp = planFingerprint(PLAN);
  // Straight off the server: dirty (it differs from actuals) but nothing to save.
  assert.equal(
    isUnsaved({ fingerprint: fp, savedFingerprint: fp, loadedPlanId: 3, dirty: true }),
    false
  );
  // One cell nudged since.
  assert.equal(
    isUnsaved({
      fingerprint: planFingerprint({ ...PLAN, draft: { ...PLAN.draft, 9: { 1: 41 } } }),
      savedFingerprint: fp,
      loadedPlanId: 3,
      dirty: true,
    }),
    true
  );
});

test("with no plan loaded, unsaved falls back to dirty", () => {
  const fp = planFingerprint(PLAN);
  assert.equal(isUnsaved({ fingerprint: fp, loadedPlanId: null, dirty: true }), true);
  assert.equal(
    isUnsaved({ fingerprint: planFingerprint({}), loadedPlanId: null, dirty: false }),
    false
  );
});

test("a loaded plan with no recorded fingerprint errs toward offering the save", () => {
  assert.equal(
    isUnsaved({ fingerprint: planFingerprint(PLAN), loadedPlanId: 3, dirty: true }),
    true
  );
});
