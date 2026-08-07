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
import {
  planFingerprint,
  isUnsaved,
  newAddedId,
  isAddedId,
  scoringSnapshot,
  snapshotChanges,
} from "./plans.js";

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

// ------------------------------------------------- planned-add ids (#67 item 5)

test("two planned adds never share an id", () => {
  const ids = new Set(Array.from({ length: 500 }, () => newAddedId()));
  assert.equal(ids.size, 500);
});

test("a minted id still reads as a planned add, and a synced id doesn't", () => {
  assert.equal(isAddedId(newAddedId()), true);
  // The grid tests ids that arrive as numbers off the payload as well as strings.
  assert.equal(isAddedId(4071), false);
  assert.equal(isAddedId("E-119"), false);
  // Plans saved before this shipped hold counter ids, and must keep loading.
  assert.equal(isAddedId("added-1"), true);
});

// ------------------------------------------------ scoring snapshot (#67 item 5)

const DATA = {
  contract: {
    period: "Base",
    pop_start: "2026-01-01",
    pop_end: "2026-12-31",
    absence: {
      holidays: [{ date: "2026-07-03", name: "Independence Day" }],
      absences: [{ person_id: "7", start: "2026-06-01", end: "2026-06-05" }],
    },
  },
  clins: [
    { id: "0001", budget: 900000, ceiling: 1200000, incrementally_funded: true, blended_rate: 145.5,
      spent: 300000, remaining: 600000 },
    { id: "0002", budget: 400000, ceiling: 400000, incrementally_funded: false, blended_rate: 132 },
  ],
  employees: [
    { id: 7, cells: { "0001": { rate: 168.25, hours: 20 } } },
    { id: 9, cells: { "0001": { rate: 151, hours: 40 } } },
  ],
};

// Deep copy with one edit applied, so each case changes exactly one thing.
function variant(edit) {
  const copy = JSON.parse(JSON.stringify(DATA));
  edit(copy);
  return copy;
}

const STATE = { draft: { 7: { "0001": 20 } }, added: [] };

test("nothing moved reads as no changes", () => {
  const snap = scoringSnapshot(DATA);
  assert.deepEqual(snapshotChanges(snap, scoringSnapshot(variant(() => {})), STATE), []);
});

test("burning down the funded slice is not staleness", () => {
  // `spent`/`remaining` move on every sync. If these counted, every plan would be
  // stale within a week and the badge would mean nothing.
  const synced = variant((d) => {
    d.clins[0].spent = 480000;
    d.clins[0].remaining = 420000;
  });
  assert.deepEqual(snapshotChanges(scoringSnapshot(DATA), scoringSnapshot(synced), STATE), []);
});

test("a mod that re-funds a CLIN marks the plan stale", () => {
  const modded = variant((d) => (d.clins[0].budget = 1050000));
  const changes = snapshotChanges(scoringSnapshot(DATA), scoringSnapshot(modded), STATE);
  assert.equal(changes.length, 1);
  assert.match(changes[0], /CLIN 0001/);
});

test("a rate change on someone the plan staffs is reported", () => {
  const repriced = variant((d) => (d.employees[0].cells["0001"].rate = 175));
  const changes = snapshotChanges(scoringSnapshot(DATA), scoringSnapshot(repriced), STATE);
  assert.deepEqual(changes, ["1 billing rate changed"]);
});

test("a rate change on someone the plan doesn't staff is not reported", () => {
  const repriced = variant((d) => (d.employees[1].cells["0001"].rate = 190));
  assert.deepEqual(
    snapshotChanges(scoringSnapshot(DATA), scoringSnapshot(repriced), STATE),
    []
  );
});

test("a planned add's own id counts as staffed", () => {
  const state = { draft: {}, added: [{ id: "added-x" }] };
  assert.deepEqual(snapshotChanges(scoringSnapshot(DATA), scoringSnapshot(DATA), state), []);
});

test("editing the holiday calendar is disclosed, since plans are scored on it live", () => {
  const rescheduled = variant((d) =>
    d.contract.absence.holidays.push({ date: "2026-11-26", name: "Thanksgiving" })
  );
  assert.deepEqual(
    snapshotChanges(scoringSnapshot(DATA), scoringSnapshot(rescheduled), STATE),
    ["the holiday calendar changed"]
  );
});

test("a new option period moves everything under the plan", () => {
  const oy1 = variant((d) => {
    d.contract.period = "OY1";
    d.contract.pop_start = "2027-01-01";
    d.contract.pop_end = "2027-12-31";
  });
  const changes = snapshotChanges(scoringSnapshot(DATA), scoringSnapshot(oy1), STATE);
  assert.match(changes[0], /period of performance/);
});

test("a CLIN appearing or disappearing is named", () => {
  const dropped = variant((d) => d.clins.pop());
  const changes = snapshotChanges(scoringSnapshot(DATA), scoringSnapshot(dropped), STATE);
  assert.match(changes[0], /CLIN 0002 is gone/);
});

test("a plan saved before snapshots existed reads as unknown, not stale", () => {
  assert.deepEqual(snapshotChanges(null, scoringSnapshot(DATA), STATE), []);
  assert.deepEqual(snapshotChanges(undefined, scoringSnapshot(DATA), STATE), []);
});

test("more than two changed CLINs collapse rather than listing every one", () => {
  const wide = { ...DATA, clins: [1, 2, 3, 4].map((n) => ({ id: `000${n}`, budget: 100 })) };
  const bumped = {
    ...wide,
    clins: wide.clins.map((c) => ({ ...c, budget: 200 })),
  };
  const changes = snapshotChanges(scoringSnapshot(wide), scoringSnapshot(bumped), STATE);
  assert.match(changes[0], /and 2 more/);
});
