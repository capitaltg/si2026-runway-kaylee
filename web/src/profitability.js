// What the Profitability view (#82) is allowed to say, separated from how it draws
// it. Every function here answers the same question in a different place: is this
// figure a fact, or is it arithmetic that only looks like one?
//
// The engine already withholds deliberately — `cost_known`, `fee_known`,
// `margin_pct: null`, and `contract.cost_model.margin_available` as the single gate
// named for this view in rates.py. A surface that renders those as 0 turns a refusal
// into a claim, and the claim it invents ("0% margin") is exactly the number an
// accountant would act on. So withholding travels as a *reason*, not as a null the
// caller has to remember to check.

// The gate. False means nobody supplied direct rates, so `cost` is the burdened
// billing rate and equals `billings` by construction — margin off those two is 0 by
// arithmetic, not by fact. Level 1 is a supported, complete state (the app is fully
// functional there); it just has one report withheld.
export const marginAvailable = (burn) =>
  Boolean(burn?.contract?.cost_model?.margin_available);

// A figure the view can print, or the reason it can't. `value` is null exactly when
// `withheld` is set, so a caller cannot accidentally format a withheld number.
const fact = (value) => ({ value, withheld: null });
const withheld = (why) => ({ value: null, withheld: why });

const LEVEL_1_COST =
  "Cost equals billings at cost-model level 1 — no direct rates have been supplied.";
const LEVEL_1_FEE =
  "Fee is structural at level 1: it reconciles with cost and revenue but says nothing about profit.";
const NO_FEE_TERMS = "This award printed no fee figures for the engine to earn against.";
const NO_FEE_TERMS_CLIN =
  "This CLIN's award printed no fee figures for the engine to earn against.";
const COST_IS_STANDIN = "Cost is a billing-rate stand-in on this CLIN.";
const PASS_THROUGH =
  "A non-labor CLIN is a cost-reimbursable pass-through: its logged travel, ODC and materials dollars consume funding and earn no fee.";

// The four contract-level tiles. Revenue is never withheld — it comes from the CLIN
// policies rather than from the cost ladder, so it is knowable at every level, and it
// is the reason a Level-1 user still has a reason to open this view.
export function summary(burn) {
  const t = burn?.totals || {};
  const margin = marginAvailable(burn);
  return {
    revenue: fact(t.revenue ?? 0),
    cost: margin ? fact(t.cost ?? 0) : withheld(LEVEL_1_COST),
    fee: !margin
      ? withheld(LEVEL_1_FEE)
      : t.fee_known
        ? fact(t.fee ?? 0)
        : withheld(NO_FEE_TERMS),
    // Derived here rather than read off the payload because the engine reports
    // margin per CLIN, not per contract. Same definition as `margin_pct` — fee over
    // revenue — so the two reconcile; guarded on revenue so a contract with no
    // recognised revenue yet reports nothing instead of dividing by zero.
    margin:
      !margin || !t.revenue
        ? withheld(margin ? "No revenue recognised yet." : LEVEL_1_COST)
        : fact(((t.revenue || 0) - (t.cost || 0)) / t.revenue),
  };
}

// One CLIN's money columns, under the same rules. `fee_known` is per CLIN because a
// mixed award can price one line fully and leave another's fee terms unstated, and
// collapsing that to a contract-level flag would withhold a fee that is actually known.
export function clinFigures(clin, margin) {
  // Non-labor cards carry no `cost` / `revenue` / `fee_earned` keys at all — they are
  // logged actuals, not priced hours, so the engine reports one figure (`spent`) and
  // the contract totals count it as both cost and revenue to keep
  // `fee == revenue - cost` true. Defaulting the missing keys to 0 would print $0 of
  // revenue on a CLIN that has spent real money and contradict the tiles above.
  //
  // Cost is *known* here even at level 1, which is the one place the gate does not
  // apply: there is no rate ladder between a logged travel dollar and its cost, so
  // nothing is standing in for anything.
  if (!clin.is_labor) {
    const spent = clin.spent ?? 0;
    return {
      revenue: fact(spent),
      cost: fact(spent),
      fee: withheld(PASS_THROUGH),
      margin: withheld(PASS_THROUGH),
    };
  }
  return {
    revenue: fact(clin.revenue ?? 0),
    cost: margin ? fact(clin.cost ?? 0) : withheld(LEVEL_1_COST),
    fee: !margin
      ? withheld(LEVEL_1_FEE)
      : clin.fee_known
        ? fact(clin.fee_earned ?? 0)
        : withheld(NO_FEE_TERMS_CLIN),
    margin: !margin
      ? withheld(LEVEL_1_COST)
      : clin.margin_pct == null
        ? withheld(COST_IS_STANDIN)
        : fact(clin.margin_pct),
  };
}

// A CLIN's at-completion projection, in whatever shape its policy states one — and
// nothing where it states none.
//
// Fixed-price lines carry `margin_position.projected_cost` (cost at PoP end against a
// firm price); cost-type lines carry `fee_position.projected.at_completion` (the fee
// the engine expects to have earned). T&M and unlabelled awards carry neither,
// because their read is when the funding runs out, not what the work earns. The
// temptation is to fill that cell by projecting cost from `weekly` — that would be a
// fourth number reconciling with nothing the engine published, so it stays empty.
// `margin_position.known` mirrors `cost_known`, and it is why this returns null at
// level 1 on a contract that *does* carry a margin position: the projected figure is
// real arithmetic, but it was built from the billing rate, so labelling it "cost at
// PoP end" would put the level-1 stand-in behind a cost word. A projection nobody can
// read as cost is worse than an empty cell that says why.
export function projection(clin) {
  const m = clin.margin_position;
  if (m && m.known) {
    return {
      kind: "margin",
      value: m.projected_cost,
      marginPct: m.projected_margin_pct,
      absorbed: 0,
      // The projection eats into fee — `_MARGIN_WATCH_FRAC` of the price, decided by
      // the engine, never re-derived here from a threshold this view invents.
      eroding: Boolean(m.eroding),
    };
  }
  const p = clin.fee_position?.projected;
  if (p) {
    return {
      kind: "fee",
      value: p.at_completion,
      marginPct: null,
      absorbed: p.absorbed || 0,
      eroding: Boolean(p.exhausted || p.absorbed),
    };
  }
  return null;
}

// Why a CLIN's at-completion cell is empty. Three different reasons land in the same
// blank cell, and the difference matters to the reader: a T&M line has no projection
// to state, a level-1 fixed-price line has one that can't be called cost yet, and a
// travel CLIN was never going to have one.
export function projectionReason(clin) {
  if (!clin.is_labor) return PASS_THROUGH;
  if (clin.margin_position && !clin.margin_position.known)
    return "This CLIN projects its spend against a firm price, but at cost-model level 1 that projection is billings — supply direct rates to read it as cost.";
  return "This CLIN's policy states no at-completion projection: its read is when the funding runs out, not what the work earns.";
}

// What `spent` and every figure derived from it is denominated in (`measured_against`,
// #79). Printed beside each CLIN because a cost-measured line and a billings-measured
// line put different quantities in the same column.
const MEASURED = { cost: "cost", billings: "billings", price: "price" };
export const measuredIn = (clin) =>
  MEASURED[clin.measured_against] || clin.measured_against || "billings";

// Labor first, then non-labor, matching the order the Flight Deck lists them in.
// Non-labor CLINs are cost-reimbursable logged actuals with no margin read of their
// own, so they belong in the table (they consume funding) but never carry a fee.
export const orderedClins = (burn) => [
  ...(burn?.clins || []).filter((c) => c.is_labor),
  ...(burn?.clins || []).filter((c) => !c.is_labor),
];
