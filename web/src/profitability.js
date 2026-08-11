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

// ---- fee at risk (#82 section 5) ------------------------------------------------
//
// The one section with no equivalent anywhere else in the app: what the award promised
// in fee, what the work has earned of it, and what the current overrun is costing.
//
// Everything comes off `clin.fee_position`, which already carries its own `periods` —
// the PUT /fee-periods endpoint exists to *record* determinations, and reading them
// from a second place would let the section disagree with the engine that priced them.

const FEE_BASIS = {
  fixed_fee: "Fixed fee",
  base_plus_award: "Base fee + award fee",
  incentive_fee: "Incentive fee",
  incentive_profit: "Incentive profit",
};

export const feeBasisLabel = (fp) => FEE_BASIS[fp?.basis] || "Fee";

// Award-stated facts survive level 1; anything computed from cost does not.
//
// `target`, `clause`, `award_pool`, the share ratio and the PTA are printed on the
// award — they are true before a single hour is charged. `earned`, `at_completion`,
// `at_risk`, `absorbed`, `withhold` and `collectable` are all functions of `cost_frac`,
// so at level 1 they are functions of the billing rate and say nothing about fee. This
// split is why `_fee_payload` carries `terms_known` and `cost_known` beside `known`
// instead of only their conjunction.
const FEE_NEEDS_COST =
  "Earned fee is a function of cost, and at cost-model level 1 cost is the billing rate — supply direct rates to read this.";

export function feeFigures(fp) {
  const costOk = fp.cost_known !== false;
  const gated = (value) => (costOk ? fact(value ?? 0) : withheld(FEE_NEEDS_COST));
  return {
    // Stated by the award, so never gated on cost. Null only when the award itself
    // didn't print a target — CPAF before any determination, most often.
    target: fp.target == null ? withheld("The award printed no fee target.") : fact(fp.target),
    earned: gated(fp.earned),
    atCompletion: gated(fp.at_completion),
    // The number worth alarming on (`target_delta`): fee gained or lost against what
    // the award promised.
    delta:
      fp.target_delta == null
        ? withheld("No fee target to measure against.")
        : gated(fp.target_delta),
    atRisk: gated(fp.at_risk),
    absorbed: gated(fp.absorbed),
    // The 52.216-8 withhold: earned but not yet payable. Kept beside `collectable`
    // because the difference between the two is the whole point of the clause.
    withhold: gated(fp.withhold),
    collectable: gated(fp.collectable),
  };
}

// Why a fee card can't be trusted, or null when it can. Split rather than collapsed:
// missing award terms are fixed by importing a document, missing cost by entering
// rates, and telling a user to do the wrong one wastes the trip.
export function feeGap(fp) {
  if (!fp.terms_known) {
    const missing = (fp.missing || []).join(", ");
    return {
      fix: "terms",
      message: missing
        ? `This award's fee terms are incomplete — missing ${missing}. Import the fee structure to price it.`
        : "This award printed no fee structure for the engine to earn against.",
    };
  }
  if (fp.cost_known === false) {
    return {
      fix: "cost",
      message:
        "The fee terms are known, but earned fee needs a real cost buildup — every figure below that depends on cost is withheld.",
    };
  }
  return null;
}

// CPAF only. An undetermined period is money the government has not awarded, so it is
// `provisional` in exactly the sense BurnChart's diagonal hatch already means — the
// view hatches it rather than inventing a second visual language for "not yet real".
export function awardPeriods(fp) {
  if (fp.basis !== "base_plus_award") return null;
  return {
    pool: fp.award_pool,
    earned: fp.award_earned,
    available: fp.award_available,
    baseEarned: fp.base_earned,
    determined: fp.periods_determined,
    total: fp.periods_total,
    periods: (fp.periods || []).map((p) => ({
      ...p,
      // A pending period is provisional; a determined one is a fact, even when the
      // determination was zero. `validate_fee_period` exists to keep those apart, and
      // rendering them the same here would undo it.
      provisional: p.status !== "determined",
    })),
  };
}

// CPIF / FPI only. `share_contractor` is the contractor's share of an underrun or
// overrun; `share_raw` is how the award printed it, kept so the number can be checked
// against the document rather than trusted.
export function shareRatio(fp) {
  if (fp.basis !== "incentive_fee" && fp.basis !== "incentive_profit") return null;
  return {
    contractor: fp.share_contractor,
    raw: fp.share_raw,
    // Point of total assumption: above this cost the contractor absorbs every
    // additional dollar. Null without a price ceiling to compute it from.
    pta: fp.pta,
  };
}

// The CLINs with a fee mechanic at all. Fixed-price lines carry a margin position
// instead, T&M keeps its fee inside the billing rate, and an unlabelled award has
// neither — so an empty list is the normal state on most contracts, not a failure.
export const feeClins = (burn) =>
  orderedClins(burn).filter((c) => c.fee_position);

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
