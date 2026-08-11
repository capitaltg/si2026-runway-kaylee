import { test } from "node:test";
import assert from "node:assert/strict";
import {
  awardPoolShareLabel,
  pricedBy,
  rateChain,
  rateVariance,
  awardPeriods,
  clinFigures,
  feeBasisLabel,
  feeClins,
  feeFigures,
  feeGap,
  loadIdle,
  loadPhase,
  loadReducer,
  shareRatio,
  marginAvailable,
  measuredIn,
  orderedClins,
  projection,
  projectionReason,
  summary,
} from "./profitability.js";

// `costOk` defaults to the tier but is separable, because the two coming apart is the
// whole of #152: a contract can carry indirect pools and a direct rate (level 2) and
// still price part of its hours at the billing rate.
const level = (n, marginOk, costOk = marginOk) => ({
  contract: { cost_model: { level: n, margin_available: marginOk } },
  totals: { cost: 800000, revenue: 1000000, revenue_known: true, fee: 200000, fee_known: true, cost_known: costOk },
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

test("an unsupported contract policy names the refusal and withholds fee and margin", () => {
  const notice = "Contract policy 'Cost-No-Fee' is currently unsupported.";
  const burn = level(2, true);
  burn.clins = [
    {
      is_labor: true,
      pricing_policy: { status: "unsupported", notice },
      revenue_known: true,
      cost_known: true,
      revenue: 500000,
      cost: 400000,
      fee_known: false,
      margin_pct: null,
    },
  ];
  assert.equal(summary(burn).fee.withheld, notice);
  assert.equal(summary(burn).margin.withheld, notice);
  assert.equal(clinFigures(burn.clins[0], true).fee.withheld, notice);
  assert.equal(clinFigures(burn.clins[0], true).margin.withheld, notice);
});

test("a CLIN whose own fee terms are unstated withholds only its fee, not the contract's", () => {
  const priced = { is_labor: true, revenue_known: true, cost_known: true, revenue: 500000, cost: 400000, fee_earned: 100000, fee_known: true, margin_pct: 0.2 };
  const unpriced = { is_labor: true, revenue_known: true, cost_known: true, revenue: 500000, cost: 450000, fee_earned: 50000, fee_known: false, margin_pct: 0.1 };
  assert.equal(clinFigures(priced, true).fee.value, 100000);
  assert.equal(clinFigures(unpriced, true).fee.value, null);
  // The priced line keeps its margin — a mixed award must not lose a known figure
  // because a sibling CLIN is missing one.
  assert.equal(clinFigures(priced, true).margin.value, 0.2);
});

test("a null margin_pct is a refusal, not an absent key", () => {
  const f = clinFigures(
    { is_labor: true, revenue_known: true, cost_known: false, revenue: 1, cost: 1, fee_earned: 0, fee_known: true, margin_pct: null },
    true,
  );
  assert.equal(f.margin.value, null);
  assert.match(f.margin.withheld, /stand-in/);
});

// ---- partial cost coverage (#152) -----------------------------------------------
// `margin_available` goes true on the first indirect pool plus the first direct rate,
// so it says the ladder exists and not that it reaches every hour. Gating on it printed
// a factual cost and margin off a total that was part buildup and part billing rate.

test("a level-2 contract whose total cost is not known withholds cost, fee and margin", () => {
  const s = summary(level(2, true, false));
  for (const key of ["cost", "fee", "margin"]) {
    assert.equal(s[key].value, null, `${key} must not be claimed on a partial cost model`);
  }
  // Not the level-1 sentence: this user already supplied rates, and telling them to
  // supply a first one sends them somewhere they have already been.
  assert.match(s.cost.withheld, /Part of this contract's hours/);
  assert.doesNotMatch(s.cost.withheld, /level 1/);
  // Revenue still stands — it comes off the CLIN policies, not the cost ladder.
  assert.equal(s.revenue.value, 1000000);
});

test("a contract-level unlock cannot vouch for a CLIN that priced its hours at the billing rate", () => {
  const covered = { is_labor: true, revenue_known: true, cost_known: true, revenue: 500000, cost: 400000, fee_earned: 100000, fee_known: true, margin_pct: 0.2 };
  const fallback = { is_labor: true, revenue_known: true, cost_known: false, revenue: 500000, cost: 500000, fee_earned: 0, fee_known: false, margin_pct: null };
  // Same contract, same `margin` argument: the mixed award is the point.
  assert.equal(clinFigures(covered, true).cost.value, 400000);
  assert.equal(clinFigures(covered, true).margin.value, 0.2);
  assert.equal(clinFigures(fallback, true).cost.value, null);
  assert.match(clinFigures(fallback, true).cost.withheld, /stand-in/);
  assert.equal(clinFigures(fallback, true).fee.value, null);
  assert.equal(clinFigures(fallback, true).margin.value, null);
});

test("a payload that omits cost_known has not said cost is known", () => {
  const f = clinFigures({ is_labor: true, revenue_known: true, revenue: 500000, cost: 400000, fee_known: true }, true);
  assert.equal(f.cost.value, null, "a truth gate that defaults open is not a gate");
  assert.equal(summary({ contract: { cost_model: { margin_available: true } }, totals: { cost: 1, revenue: 2, revenue_known: true } }).cost.value, null);
});

test("a payload that omits revenue_known has not said revenue is recognised", () => {
  // Same rule as cost, for the same reason (#154): the flag is the claim, and silence
  // is not it. A CLIN carrying `revenue` with no flag is the pre-#154 payload shape,
  // where every fixed-price price was already sitting in that key.
  const f = clinFigures({ is_labor: true, cost_known: true, revenue: 500000, cost: 400000, fee_earned: 100000, fee_known: true, margin_pct: 0.2 }, true);
  assert.equal(f.revenue.value, null);
  assert.equal(f.fee.value, null);
  assert.equal(f.margin.value, null);
  assert.equal(summary({ contract: { cost_model: { margin_available: true } }, totals: { cost: 1, revenue: 2, cost_known: true, fee_known: true } }).revenue.value, null);
});

test("a fully covered CLIN with no fee terms is sent to the document, not back to the rates view", () => {
  const f = clinFigures(
    { is_labor: true, revenue_known: true, cost_known: true, revenue: 500000, cost: 400000, fee_earned: 100000, fee_known: false, margin_pct: null },
    true,
  );
  assert.equal(f.cost.value, 400000, "cost is known here; only the fee terms are not");
  assert.match(f.margin.withheld, /no fee figures/);
});

// ---- fixed-price recognition (#154) ---------------------------------------------
// A firm price is earned on delivery, and Runway has no delivery input. The engine puts
// the price in the `revenue` key so its three quantities keep reconciling; what this
// module may *call* it is the question here.

const ffp = {
  is_labor: true,
  revenue_known: false,
  cost_known: true,
  revenue: 1000000,
  cost: 200000,
  fee_earned: 800000,
  fee_known: false,
  margin_pct: null,
};

test("a fixed-price CLIN reports no recognised revenue, fee or margin", () => {
  const f = clinFigures(ffp, true);
  for (const key of ["revenue", "fee", "margin"]) {
    assert.equal(f[key].value, null, `${key} must not be claimed off an unearned price`);
  }
  // The figure withheld is the one that was wrong: $800k of "fee" on a CLIN that has
  // delivered nothing, which is unspent budget.
  assert.match(f.fee.withheld, /unspent budget, not profit/);
  // Cost is a real buildup and stays — the price is what isn't earned, not the hours.
  assert.equal(f.cost.value, 200000);
});

test("the fixed-price refusal names delivery, never the rates form", () => {
  // The one refusal a user cannot fix by entering something. Sending them to Indirect
  // Rates would be a wrong instruction rather than an incomplete one.
  const f = clinFigures({ ...ffp, cost_known: false }, false);
  assert.match(f.revenue.withheld, /delivery/);
  assert.doesNotMatch(f.revenue.withheld, /direct rate/);
  // Cost keeps its own level-1 reason: that one *is* fixable, and both are true here.
  assert.match(f.cost.withheld, /level 1/);
  // Fee asks revenue first — more rates will not unlock a fee with no revenue under it.
  assert.match(f.fee.withheld, /no recognised revenue/);
});

test("a T&M line beside a fixed-price one keeps its own revenue", () => {
  const tm = { is_labor: true, revenue_known: true, cost_known: true, revenue: 500000, cost: 400000, fee_earned: 100000, fee_known: true, margin_pct: 0.2 };
  assert.equal(clinFigures(tm, true).revenue.value, 500000);
  assert.equal(clinFigures(tm, true).margin.value, 0.2);
  assert.equal(clinFigures(ffp, true).revenue.value, null);
});

test("one fixed-price CLIN withholds the contract totals, which mix price with revenue", () => {
  const burn = level(2, true);
  burn.totals.revenue_known = false;
  const s = summary(burn);
  assert.equal(s.revenue.value, null);
  assert.equal(s.fee.value, null);
  assert.equal(s.margin.value, null);
  assert.match(s.revenue.withheld, /unstarted work as earned/);
  // Cost is untouched: the hours cost what they cost regardless of what earns them.
  assert.equal(s.cost.value, 800000);
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

// ---- fee at risk (#82 section 5) ------------------------------------------------
// Shapes taken from a live CPFF payload (contract 39), which is why the numbers are
// the odd ones they are: fee target 176,075 with 44,080 absorbed by the projected
// overrun.
const cpff = {
  basis: "fixed_fee",
  known: true,
  terms_known: true,
  cost_known: true,
  missing: [],
  target: 176075.26,
  earned: 116058.26,
  at_completion: 176075.26,
  target_delta: 0,
  withhold: 26411.29,
  collectable: 89646.97,
  at_risk: 0,
  absorbed: 0,
  overrun: 0,
  exhausted: false,
  provisional: false,
  clause: "52.216-8",
  periods: [],
  projected: {
    basis: "fixed_fee",
    terms_known: true,
    cost_known: true,
    target: 176075.26,
    earned: 176075.26,
    at_completion: 131995.26,
    target_delta: -44080,
    withhold: 26411.29,
    collectable: 149663.97,
    at_risk: 44080,
    absorbed: 44080,
    overrun: 44080,
    exhausted: false,
  },
};

test("a priced fixed-fee position reports every figure as a fact", () => {
  const f = feeFigures(cpff);
  assert.equal(f.target.value, 176075.26);
  assert.equal(f.earned.value, 116058.26);
  assert.equal(f.withhold.value, 26411.29);
  assert.equal(f.collectable.value, 89646.97);
  assert.equal(feeGap(cpff), null);
});

test("the projection carries the loss the current position does not yet show", () => {
  const p = feeFigures(cpff.projected);
  assert.equal(p.delta.value, -44080, "fee lost against what the award promised");
  assert.equal(p.absorbed.value, 44080);
  assert.equal(p.atRisk.value, 44080);
  // The whole point of the section: to date the position looks whole.
  assert.equal(feeFigures(cpff).absorbed.value, 0);
});

test("award-stated fee terms survive level 1, but earned fee does not", () => {
  const level1 = { ...cpff, known: false, cost_known: false };
  const f = feeFigures(level1);
  assert.equal(f.target.value, 176075.26, "the award printed this before any hour was charged");
  for (const key of ["earned", "atCompletion", "atRisk", "absorbed", "withhold", "collectable"]) {
    assert.equal(f[key].value, null, `${key} depends on cost`);
    assert.match(f[key].withheld, /billing rate standing in/);
  }
  assert.equal(feeGap(level1).fix, "cost");
});

// ---- projected fee (#153) --------------------------------------------------------
// The projection is the same fee terms applied to projected cost, so it is exactly as
// trustworthy as the current position — but it used to ship without its truth flags,
// and `cost_known !== false` read that silence as a fact.

test("a projected position with no truth flags is withheld, not trusted", () => {
  const bare = { at_completion: 131995.26, target_delta: -44080, at_risk: 44080, absorbed: 44080 };
  const f = feeFigures(bare);
  for (const key of ["atCompletion", "delta", "atRisk", "absorbed"]) {
    assert.equal(f[key].value, null, `${key} was published without a truth state`);
  }
});

test("unknown fee terms withhold the figures rather than computing them to zero", () => {
  // `earned_fee` against a structure the award never printed returns a clean $0, and a
  // $0 at-completion fee is a claim: that the work will earn nothing.
  const noTerms = { ...cpff.projected, terms_known: false, earned: 0, at_completion: 0, at_risk: 0, absorbed: 0 };
  const f = feeFigures(noTerms);
  for (const key of ["earned", "atCompletion", "atRisk", "absorbed"]) {
    assert.equal(f[key].value, null, `${key} must not be a computed zero`);
    assert.match(f[key].withheld, /terms are incomplete/);
  }
});

test("a projection whose cost is a stand-in withholds every figure the current position does", () => {
  const now = feeFigures({ ...cpff, cost_known: false });
  const proj = feeFigures({ ...cpff.projected, cost_known: false });
  // The pair must agree: the view prefers the projected figure where one exists, so a
  // projection that claims more than the position beside it is the defect.
  for (const key of ["earned", "atCompletion", "atRisk", "absorbed"]) {
    assert.equal(now[key].value, null, key);
    assert.equal(proj[key].value, null, key);
  }
});

test("missing award terms and missing cost are different problems with different fixes", () => {
  const noTerms = { ...cpff, terms_known: false, missing: ["fee target", "fee type"] };
  const gap = feeGap(noTerms);
  assert.equal(gap.fix, "terms");
  assert.match(gap.message, /fee target, fee type/);
  assert.match(gap.message, /Import/);
  // Cost is the other fix, and telling the user to import a document would waste it.
  assert.equal(feeGap({ ...cpff, cost_known: false }).fix, "cost");
});

test("a position with no fee target withholds the target and the delta, not the earned fee", () => {
  const f = feeFigures({ ...cpff, target: null, target_delta: null });
  assert.equal(f.target.value, null);
  assert.equal(f.delta.value, null);
  assert.equal(f.earned.value, 116058.26);
});

test("an award-fee pool share is displayed as dollars, not as a ratio", () => {
  assert.equal(awardPoolShareLabel(45000), "$45,000 of pool");
  assert.equal(awardPoolShareLabel(null), "");
});

test("an undetermined award-fee period is provisional; a zero determination is a fact", () => {
  const cpaf = {
    basis: "base_plus_award",
    award_pool: 200000,
    award_earned: 60000,
    award_available: 140000,
    base_earned: 90000,
    periods_determined: 2,
    periods_total: 3,
    periods: [
      { name: "Period 1", status: "determined", determined_amount: 60000, pool_share: 45000 },
      { name: "Period 2", status: "determined", determined_amount: 0, pool_share: 45000 },
      { name: "Period 3", status: "pending", determined_amount: null, pool_share: 45000 },
    ],
  };
  const a = awardPeriods(cpaf);
  assert.equal(a.pool, 200000);
  assert.deepEqual(
    a.periods.map((p) => p.provisional),
    [false, false, true],
    "a determination of zero is an outcome, not a pending evaluation",
  );
});

test("only CPAF gets award periods, and only incentive types get a share ratio", () => {
  assert.equal(awardPeriods(cpff), null);
  assert.equal(shareRatio(cpff), null);
  const cpif = { basis: "incentive_fee", share_contractor: 0.2, share_raw: "80/20", pta: 2400000 };
  assert.equal(shareRatio(cpif).raw, "80/20");
  assert.equal(shareRatio(cpif).pta, 2400000);
  assert.equal(awardPeriods(cpif), null);
});

test("fee basis reads as prose, and an unknown basis still renders a label", () => {
  assert.equal(feeBasisLabel(cpff), "Fixed fee");
  assert.equal(feeBasisLabel({ basis: "base_plus_award" }), "Base fee + award fee");
  assert.equal(feeBasisLabel({ basis: "something_new" }), "Fee");
  assert.equal(feeBasisLabel(null), "Fee");
});

test("only CLINs with a fee mechanic get a card — fixed-price and T&M lines carry none", () => {
  const burn = {
    clins: [
      { code: "CLIN 0001", is_labor: true, fee_position: cpff },
      { code: "CLIN 0002", is_labor: true, margin_position: { known: true } },
      { code: "CLIN 0003", is_labor: true 
      },
      { code: "CLIN 0004", is_labor: false, spent: 100 },
    ],
  };
  assert.deepEqual(
    feeClins(burn).map((c) => c.code),
    ["CLIN 0001"],
  );
});

// ---- the buildup (#82 section 2) ------------------------------------------------
// rate_set shape from a live level-2 contract (42).
const withChain = {
  contract: {
    cost_model: {
      level: 2,
      margin_available: true,
      rate_set: {
        fiscal_year: "2026",
        scope: "contract",
        status: "provisional",
        complete: true,
        pools: [
          { name: "fringe", label: "Fringe", rate: 0.272, base: "direct_labor", status: "provisional" },
          { name: "overhead", label: "Overhead", rate: 0.449, base: "labor_plus_fringe", status: "provisional" },
          { name: "gna", label: "G&A", rate: 0.08, base: "total_cost_input", status: "provisional" },
        ],
      },
    },
  },
  totals: {},
  clins: [],
};

test("the chain names every rate with the base it applies to, in order", () => {
  const chain = rateChain(withChain);
  assert.deepEqual(
    chain.steps.map((s) => [s.label, s.rate, s.baseLabel]),
    [
      ["Fringe", 0.272, "direct labor"],
      ["Overhead", 0.449, "direct labor + fringe"],
      ["G&A", 0.08, "total cost input (labor + fringe + overhead)"],
    ],
  );
});

test("a provisional rate set is flagged, because the true-up reprices every hour already charged", () => {
  assert.equal(rateChain(withChain).provisional, true);
  const final = JSON.parse(JSON.stringify(withChain));
  final.contract.cost_model.rate_set.status = "final";
  assert.equal(rateChain(final).provisional, false);
});

test("a level-1 contract has no chain to show", () => {
  assert.equal(rateChain({ contract: { cost_model: { level: 1, rate_set: null } } }), null);
  assert.equal(rateChain({ contract: {} }), null);
});

test("each pricing tier is named, and the billing-rate fallback is marked a stand-in", () => {
  const mix = pricedBy({
    cost_rate_mix: [
      { source: "lcat_direct", hours: 900 },
      { source: "negotiated_fallback", hours: 100 },
    ],
  });
  assert.equal(mix[0].label, "Category (LCAT) direct rate");
  assert.equal(mix[0].standIn, false);
  assert.equal(mix[1].standIn, true, "the fallback is the one tier that is not a cost fact");
});

test("rate variance is flattened across CLINs with the CLIN kept", () => {
  const rows = rateVariance({
    clins: [
      {
        code: "CLIN 0001",
        is_labor: true,
        rate_variance: [{ lcat: "Program Manager (PMP)", delta: 2.13, direction: "above_buildup", pct: 0.0162 }],
      },
      { code: "CLIN 0002", is_labor: true, rate_variance: [] },
    ],
  });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].code, "CLIN 0001");
  assert.equal(rows[0].direction, "above_buildup");
});

// Live CPAF (contract 42): a $157,923 pool reporting $0 available with 0 periods. The
// zero means "nothing recorded to earn against", not "the fee is gone".
test("a CPAF pool with no evaluation periods recorded is unallocated, not spent", () => {
  const a = awardPeriods({
    basis: "base_plus_award",
    award_pool: 157923.16,
    award_earned: 0,
    award_available: 0,
    base_earned: 4785.91,
    periods_determined: 0,
    periods_total: 0,
    periods: [],
  });
  assert.equal(a.periodsRecorded, false);
  assert.equal(a.pool, 157923.16);
});

test("a CPAF with recorded periods reports them", () => {
  const a = awardPeriods({
    basis: "base_plus_award",
    periods_total: 1,
    periods: [{ name: "Period 1", status: "pending" }],
  });
  assert.equal(a.periodsRecorded, true);
});

// ---- Load state (#164) ---------------------------------------------------------

test("switching contracts drops the previous contract's figures before the new ones land", () => {
  const shown = loadReducer(loadIdle, { type: "loaded", burn: { contract: { id: 7 } } });
  const switched = loadReducer(shown, { type: "select" });
  assert.equal(switched.burn, null);
  assert.equal(loadPhase(switched), "loading");
});

test("switching contracts drops the previous contract's failure too", () => {
  const failed = loadReducer(loadIdle, { type: "failed", message: "contract 7 not found" });
  const switched = loadReducer(failed, { type: "select" });
  assert.equal(switched.error, null);
  assert.equal(loadPhase(switched), "loading");
});

test("a success clears the previous failure instead of showing both", () => {
  const failed = loadReducer(loadIdle, { type: "failed", message: "500 from /burn" });
  const recovered = loadReducer(failed, { type: "loaded", burn: { contract: { id: 7 } } });
  assert.equal(recovered.error, null);
  assert.equal(loadPhase(recovered), "ready");
});

test("a failure after a success replaces the figures rather than sitting beside them", () => {
  const shown = loadReducer(loadIdle, { type: "loaded", burn: { contract: { id: 7 } } });
  const failed = loadReducer(shown, { type: "failed", message: "500 from /burn" });
  assert.equal(failed.burn, null);
  assert.equal(loadPhase(failed), "error");
});

test("an empty workspace is an empty state, not a load that never finishes", () => {
  const none = loadReducer(loadIdle, { type: "none" });
  assert.equal(loadPhase(none), "empty");
  assert.equal(loadPhase(loadIdle), "loading");
});

test("a failed contract list outranks the empty workspace it can no longer prove", () => {
  const none = loadReducer(loadIdle, { type: "none" });
  const failed = loadReducer(none, { type: "failed", message: "network error" });
  assert.equal(loadPhase(failed), "error");
});
