import test from "node:test";
import assert from "node:assert";
import { runningTotals } from "./funding-total.js";

const AWARD = { mod: "Award", amount: 4_836_234.8, cumulative_obligated: 4_836_234.8 };
const OPTION = { mod: "P00001", amount: 1_873_252.8 };

test("a mod stating no cumulative still advances the running total", () => {
  // The live case: the SF-30 prints only "Obligated this action $X", so the
  // column used to render an em dash under a heading that says CUMULATIVE.
  const totals = runningTotals([AWARD, OPTION]);
  assert.equal(totals[1].total, 6_709_487.6);
  assert.equal(totals[1].stated, null);
});

test("a mod restating its own increment as the cumulative does not walk the total back", () => {
  const totals = runningTotals([
    AWARD,
    { ...OPTION, cumulative_obligated: 1_873_252.8 },
  ]);
  assert.equal(totals[1].total, 6_709_487.6);
});

test("a stated cumulative above the sum wins and carries forward", () => {
  // Evidence of a mod missing from the trail: our sum undercounts and the
  // document is the only thing that says so.
  const totals = runningTotals([
    AWARD,
    { mod: "P00002", amount: 100_000, cumulative_obligated: 9_000_000 },
    { mod: "P00003", amount: 50_000 },
  ]);
  assert.equal(totals[1].total, 9_000_000);
  assert.equal(totals[2].total, 9_050_000);
});

test("the stated figure is kept alongside the computed one", () => {
  const totals = runningTotals([AWARD]);
  assert.equal(totals[0].stated, 4_836_234.8);
  assert.equal(totals[0].summed, 4_836_234.8);
});

test("an empty history is not an error", () => {
  assert.deepEqual(runningTotals([]), []);
  assert.deepEqual(runningTotals(), []);
});

test("a stated cumulative above the ceiling is a misread, not an over-obligation", () => {
  // The live read: "cumulative obligated $6,709,487.60" came back as
  // $16,709,487.80 against a $14,535,792.80 ceiling. Trusting it let one wrong
  // leading digit overwrite a figure every other number on the page agreed with.
  const totals = runningTotals(
    [AWARD, { ...OPTION, cumulative_obligated: 16_709_487.8 }],
    14_535_792.8
  );
  assert.equal(totals[1].total, 6_709_487.6);
  assert.equal(totals[1].disputed, true);
});

test("a stated cumulative at the ceiling exactly is still trusted", () => {
  const totals = runningTotals(
    [AWARD, { ...OPTION, cumulative_obligated: 14_535_792.8 }],
    14_535_792.8
  );
  assert.equal(totals[1].total, 14_535_792.8);
  assert.equal(totals[1].disputed, false);
});

test("with no ceiling known, the stated figure is still allowed to win", () => {
  const totals = runningTotals([AWARD, { ...OPTION, cumulative_obligated: 9_000_000 }]);
  assert.equal(totals[1].total, 9_000_000);
});
