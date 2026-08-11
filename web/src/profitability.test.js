import { test } from "node:test";
import assert from "node:assert/strict";
import {
  clinFigures,
  marginAvailable,
  measuredIn,
  orderedClins,
  projection,
  projectionReason,
  summary,
} from "./profitability.js";

const level = (n, marginOk) => ({
  contract: { cost_model: { level: n, margin_available: marginOk } },
  totals: { cost: 800000, revenue: 1000000, fee: 200000, fee_known: true, cost_known: marginOk },
  clins: [],
});

test("a level-2 contract reports cost, fee and margin as facts", () => {
  const s = summary(level(2, true));
  assert.equal(s.cost.value, 800000);
  assert.equal(s.fee.value, 200000);
  assert.equal(s.margin.value, 0.2);
  assert.equal(s.margin.withheld, null);
});

test("a level-1 contract withholds cost, fee and margin — and never reports them as zero", () => {
  const s = summary(level(1, false));
  for (const key of ["cost", "fee", "margin"]) {
    assert.equal(s[key].value, null, `${key} must not carry a value`);
    assert.match(s[key].withheld, /level 1/);
  }
  // The whole reason the view is still worth opening at level 1.
  assert.equal(s.revenue.value, 1000000);
  assert.equal(s.revenue.withheld, null);
});

test("margin is withheld rather than divided by zero when no revenue is recognised", () => {
  const burn = level(2, true);
  burn.totals.revenue = 0;
  const s = summary(burn);
  assert.equal(s.margin.value, null);
  assert.match(s.margin.withheld, /No revenue/);
});

test("a known cost model with unstated fee terms withholds the fee but keeps the cost", () => {
  const burn = level(2, true);
  burn.totals.fee_known = false;
  const s = summary(burn);
  assert.equal(s.cost.value, 800000);
  assert.equal(s.fee.value, null);
  assert.match(s.fee.withheld, /no fee figures/);
});

test("a CLIN whose own fee terms are unstated withholds only its fee, not the contract's", () => {
  const priced = { is_labor: true, revenue: 500000, cost: 400000, fee_earned: 100000, fee_known: true, margin_pct: 0.2 };
  const unpriced = { is_labor: true, revenue: 500000, cost: 450000, fee_earned: 50000, fee_known: false, margin_pct: 0.1 };
  assert.equal(clinFigures(priced, true).fee.value, 100000);
  assert.equal(clinFigures(unpriced, true).fee.value, null);
  // The priced line keeps its margin — a mixed award must not lose a known figure
  // because a sibling CLIN is missing one.
  assert.equal(clinFigures(priced, true).margin.value, 0.2);
});

test("a null margin_pct is a refusal, not an absent key", () => {
  const f = clinFigures(
    { is_labor: true, revenue: 1, cost: 1, fee_earned: 0, fee_known: true, margin_pct: null },
    true,
  );
  assert.equal(f.margin.value, null);
  assert.match(f.margin.withheld, /stand-in/);
});

// The engine sends non-labor cards with no cost/revenue/fee keys at all — verified
// against a live payload, where CLINs 0002 and 0003 carried `revenue: undefined`.
// Defaulting those to 0 printed "$0" of revenue on CLINs that had spent real money.
test("a non-labor CLIN reports its logged spend, not a fabricated $0", () => {
  const f = clinFigures({ is_labor: false, spent: 42000 }, true);
  assert.equal(f.revenue.value, 42000);
  assert.equal(f.cost.value, 42000);
});

test("a non-labor CLIN's cost survives level 1, because no rate stands in for it", () => {
  const f = clinFigures({ is_labor: false, spent: 42000 }, false);
  assert.equal(f.cost.value, 42000, "a logged travel dollar is its own cost at any level");
});

test("a non-labor CLIN earns no fee and no margin, and says why", () => {
  const f = clinFigures({ is_labor: false, spent: 42000 }, true);
  assert.equal(f.fee.value, null);
  assert.equal(f.margin.value, null);
  assert.match(f.margin.withheld, /pass-through/);
});

test("a fixed-price CLIN projects cost at PoP end against its price", () => {
  const p = projection({
    is_labor: true,
    margin_position: {
      price: 1000000,
      projected_cost: 940000,
      projected_margin_pct: 0.06,
      eroding: true,
      known: true,
    },
  });
  assert.equal(p.kind, "margin");
  assert.equal(p.value, 940000);
  assert.equal(p.eroding, true);
});

// A live level-1 FFP contract carries a full margin_position with `known: false`.
// Printing its projected_cost would label the billing-rate stand-in as cost.
test("a level-1 fixed-price CLIN withholds its projection rather than calling billings cost", () => {
  const clin = {
    is_labor: true,
    margin_position: { price: 1000000, projected_cost: 940000, projected_margin_pct: 0.06, known: false },
  };
  assert.equal(projection(clin), null);
  assert.match(projectionReason(clin), /level 1/);
});

test("each empty at-completion cell explains its own reason", () => {
  assert.match(projectionReason({ is_labor: false, spent: 1 }), /pass-through/);
  assert.match(
    projectionReason({ is_labor: true, measured_against: "billings" }),
    /when the funding runs out/,
  );
});

test("a cost-type CLIN projects fee at completion, and flags fee absorbed by an overrun", () => {
  const p = projection({
    fee_position: { projected: { at_completion: 60000, absorbed: 20000, exhausted: false } },
  });
  assert.equal(p.kind, "fee");
  assert.equal(p.value, 60000);
  assert.equal(p.absorbed, 20000);
  assert.equal(p.eroding, true);
});

test("a T&M CLIN states no at-completion projection and none is synthesized", () => {
  // Carries a weekly pace the view could have multiplied out. It must not: a
  // projection the engine did not publish would reconcile with nothing.
  assert.equal(projection({ weekly: 25000, weeks_left: 12, measured_against: "billings" }), null);
});

test("the measured quantity is named per CLIN, so two denominators are never silently mixed", () => {
  assert.equal(measuredIn({ measured_against: "cost" }), "cost");
  assert.equal(measuredIn({ measured_against: "price" }), "price");
  // Unknown-type awards keep the legacy billings read rather than rendering blank.
  assert.equal(measuredIn({}), "billings");
});

test("labor CLINs list before non-labor ones, and every CLIN is listed", () => {
  const burn = {
    clins: [
      { code: "CLIN 0002", is_labor: false },
      { code: "CLIN 0001", is_labor: true },
      { code: "CLIN 0003", is_labor: true },
    ],
  };
  assert.deepEqual(
    orderedClins(burn).map((c) => c.code),
    ["CLIN 0001", "CLIN 0003", "CLIN 0002"],
  );
});

test("the gate reads the contract's cost model, defaulting closed on a payload without one", () => {
  assert.equal(marginAvailable(level(2, true)), true);
  assert.equal(marginAvailable(level(1, false)), false);
  assert.equal(marginAvailable({ contract: {} }), false);
  assert.equal(marginAvailable(null), false);
});
