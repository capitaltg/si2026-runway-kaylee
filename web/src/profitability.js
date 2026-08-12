// What the Profitability view (#82) is allowed to say, separated from how it draws
// it. Every function here answers the same question in a different place: is this
// figure a fact, or is it arithmetic that only looks like one?
//
// The engine already withholds deliberately — `cost_known`, `fee_known` and
// `margin_pct: null`. A surface that renders those as 0 turns a refusal into a claim,
// and the claim it invents ("0% margin") is exactly the number an accountant would act
// on. So withholding travels as a *reason*, not as a null the caller has to remember
// to check.

import { money } from "./format.js";

// Which tier the contract's rate ladder is configured at, and *not* the gate (#152).
// False means nobody supplied direct rates at all, so every hour costs its burdened
// billing rate and margin is 0 by arithmetic rather than by fact. True only means the
// ladder exists: `margin_available` goes true on the first indirect pool plus the first
// direct rate, so a contract with six LCATs and one direct rate reads true while five
// of its categories are still billing-rate stand-ins. What a figure may claim is
// therefore gated on the engine's own cost truth — `totals.cost_known` for the
// contract, `clin.cost_known` per CLIN — and this flag only chooses which sentence
// explains the refusal.
export const marginAvailable = (burn) =>
  Boolean(burn?.contract?.cost_model?.margin_available);

// Whether every labor CLIN priced its hours from a direct rate. False on a contract
// with no labor CLINs at all, which the engine reports the same way — nothing to know
// reads as not known, and inventing the difference here would be a fourth number.
const totalCostKnown = (burn) => burn?.totals?.cost_known === true;

// Per CLIN, the same question. `=== true` rather than `!== false`: a payload that
// omits the flag has not told us cost is known, and a truth gate that defaults open
// is not a gate.
const clinCostKnown = (clin) => clin?.cost_known === true;

// Whether the revenue figure is revenue at all (#154). False on a fixed-price line,
// where the engine puts the contract price in that slot so the three quantities keep
// reconciling — the price is earned on delivery, and nothing tells Runway a delivery
// happened. The contract flag goes false if a single labor CLIN is fixed-price: a
// total that is part recognised revenue and part price is not a revenue figure. Both
// default closed, like cost.
const totalRevenueKnown = (burn) => burn?.totals?.revenue_known === true;
const clinRevenueKnown = (clin) => clin?.revenue_known === true;

// A figure the view can print, or the reason it can't. `value` is null exactly when
// `withheld` is set, so a caller cannot accidentally format a withheld number.
const fact = (value) => ({ value, withheld: null });
const withheld = (why) => ({ value: null, withheld: why });
const notApplicable = (why) => ({
  value: null,
  withheld: why,
  notApplicable: true,
});

const CEILING_LABEL = {
  firm_price: "Firm price",
  ceiling_price: "Ceiling price",
  estimated_cost: "Estimated cost",
  cost_plus_fixed_fee: "Cost + fixed fee",
  cost_plus_target_fee: "Target cost + fee",
  cost_plus_base_and_award_pool: "Cost + fee pool",
};

const NO_EARNINGS_CONCEPT =
  "This pricing policy reimburses cost without a fee or profit mechanic.";

// The concepts a pricing-policy payload makes applicable on one CLIN. This consumes
// semantic fields the backend already owns rather than reclassifying raw type text in
// the browser. A future no-fee cost policy therefore works without adding another
// type-code branch here: a cost-reimbursement revenue basis with no fee/profit term
// simply has no earnings or return concept.
export function pricingApplicability(clin) {
  const policy = clin?.pricing_policy || {};
  const known = policy.known === true;
  const ceilingLabel = known
    ? CEILING_LABEL[policy.ceiling_meaning] || "Price / limit"
    : "Limit · policy unknown";
  // Non-labor spend cannot earn fee or profit regardless of whether its printed
  // pricing type was recognized. Keep the unknown count and limit caveat, while
  // stating the applicability fact that is already certain.
  if (!clin?.is_labor) {
    return {
      known,
      ceilingLabel,
      earningsLabel: "Not applicable",
      returnLabel: "Not applicable",
      earningsApplicable: false,
      returnApplicable: false,
    };
  }
  if (!known) {
    return {
      known: false,
      ceilingLabel: "Limit · policy unknown",
      earningsLabel: "Earnings · policy unknown",
      returnLabel: "Return · policy unknown",
      earningsApplicable: null,
      returnApplicable: null,
    };
  }

  if (policy.family === "fixed_price") {
    const incentive = policy.revenue_basis === "cost_plus_earned_profit";
    return {
      known: true,
      ceilingLabel,
      earningsLabel: incentive ? "Incentive profit" : "Profit",
      returnLabel: incentive ? "Profit margin" : "Margin",
      earningsApplicable: true,
      returnApplicable: true,
    };
  }
  if (policy.family === "time_and_materials") {
    return {
      known: true,
      ceilingLabel,
      earningsLabel: "Gross profit",
      returnLabel: "Margin",
      earningsApplicable: true,
      returnApplicable: true,
    };
  }
  if (policy.family === "cost_reimbursement") {
    const feeApplies = /fee|profit/.test(policy.revenue_basis || "");
    let earningsLabel = "Fee earned";
    if (policy.ceiling_meaning === "cost_plus_fixed_fee") {
      earningsLabel = "Fixed fee earned";
    } else if (policy.ceiling_meaning === "cost_plus_target_fee") {
      earningsLabel = "Incentive fee earned";
    } else if (
      policy.ceiling_meaning === "cost_plus_base_and_award_pool"
    ) {
      earningsLabel = "Award fee earned";
    }
    return {
      known: true,
      ceilingLabel,
      earningsLabel: feeApplies ? earningsLabel : "Not applicable",
      returnLabel: feeApplies ? "Fee margin" : "Not applicable",
      earningsApplicable: feeApplies,
      returnApplicable: feeApplies,
    };
  }

  return {
    known: true,
    ceilingLabel,
    earningsLabel: "Earnings",
    returnLabel: "Return",
    earningsApplicable: true,
    returnApplicable: true,
  };
}

const aggregateApplicability = (rows, key) => {
  if (rows.some((row) => row[key] === true)) return true;
  if (rows.length && rows.every((row) => row[key] === false)) return false;
  return null;
};

// Contract-level labels stay precise on a homogeneous award and deliberately become
// neutral on a mixed or partly-unknown one. The backend count wins when present: it
// includes every active CLIN and is the authoritative statement of extraction gaps.
export function profitabilityLabels(burn) {
  const rows = (burn?.clins || []).map(pricingApplicability);
  const statedUnknown = burn?.contract?.pricing_unknown;
  const unknownCount = Number.isInteger(statedUnknown)
    ? statedUnknown
    : rows.filter((row) => !row.known).length;
  const earningsApplicable = aggregateApplicability(rows, "earningsApplicable");
  const returnApplicable = aggregateApplicability(rows, "returnApplicable");
  const oneLabel = (key, fallback, applicableKey, applicable) => {
    if (applicable === false) return "Not applicable";
    const candidates = applicableKey
      ? rows.filter((row) => row[applicableKey] === true)
      : rows;
    const labels = [...new Set(candidates.map((row) => row[key]))];
    return unknownCount === 0 && labels.length === 1 ? labels[0] : fallback;
  };
  return {
    ceiling: oneLabel("ceilingLabel", "Price / limit"),
    earnings: oneLabel(
      "earningsLabel",
      "Earnings",
      "earningsApplicable",
      earningsApplicable,
    ),
    return: oneLabel(
      "returnLabel",
      "Return",
      "returnApplicable",
      returnApplicable,
    ),
    earningsApplicable,
    returnApplicable,
    unknownCount,
  };
}

const LEVEL_1_COST =
  "Cost equals billings at cost-model level 1 — no direct rates have been supplied.";
const LEVEL_1_FEE =
  "Fee is structural at level 1: it reconciles with cost and revenue but says nothing about profit.";
const NO_FEE_TERMS = "This award printed no fee figures for the engine to earn against.";
const NO_FEE_TERMS_CLIN =
  "This CLIN's award printed no fee figures for the engine to earn against.";
const COST_IS_STANDIN = "Cost is a billing-rate stand-in on this CLIN.";
const FEE_IS_STANDIN_CLIN =
  "This CLIN's hours are priced at the billing rate, so its fee reconciles with cost and revenue but says nothing about profit.";
// Partial coverage (#152). A contract can have pools and some direct rates and still
// leave categories priced at the billing rate — the ladder is configured, the cost is
// not known, and the fix is more rates rather than a first rate.
const PARTIAL_COST =
  "Part of this contract's hours are priced at the billing rate, so total cost is not known — every labor category needs a direct rate before a contract margin means anything.";
const PARTIAL_FEE =
  "Total fee is revenue less cost, and part of this contract's cost is a billing-rate stand-in.";
const NO_REVENUE_CLIN = "No revenue recognised on this CLIN yet.";
// Fixed-price recognition (#154). The refusal a user cannot fix by entering anything:
// it takes a milestone or delivery record Runway does not yet accept, so the sentences
// say what the figure would have meant rather than pointing at a form.
const PRICE_NOT_REVENUE =
  "Fixed-price work earns its price on delivery, and Runway has no milestone or delivery input — so a contract total counting that price as revenue would report unstarted work as earned. Each fixed-price CLIN's price and the cost against it are in its at-completion position below.";
const PRICE_NOT_REVENUE_CLIN =
  "This CLIN earns its price on delivery (FAR 16.202) and Runway is told about no deliveries, so none of it is recognised yet. The price is in the ceiling column and the cost against it in the at-completion position.";
const PRICE_NOT_REVENUE_FEE =
  "Fee is revenue less cost, and this contract's fixed-price work has no recognised revenue to take it from — a price not yet earned less the cost so far is unspent budget, not profit.";
const PRICE_NOT_REVENUE_FEE_CLIN =
  "Fee is revenue less cost, and this CLIN has no recognised revenue to take it from — its price less the cost so far is unspent budget, not profit, until it delivers.";
const PASS_THROUGH =
  "A non-labor CLIN is a cost-reimbursable pass-through: its logged travel, ODC and materials dollars consume funding and earn no fee.";

// The four contract-level tiles. Revenue survives the cost ladder — it comes from the
// CLIN policies, not from rates, and is knowable at every level — but it does not
// survive a missing recognition basis (#154): on fixed-price work the engine reports
// the price in that slot, and printing a price under a revenue heading is the whole
// bug. Cost keeps its own gate, so a level-1 T&M contract reads exactly as it did.
export function summary(burn) {
  const t = burn?.totals || {};
  const unsupported = (burn?.clins || [])
    .map((c) => c?.pricing_policy)
    .find((p) => p?.status === "unsupported");
  const unsupportedNotice =
    unsupported?.notice || "Contract policy is currently unsupported.";
  const margin = marginAvailable(burn);
  // The gate is the engine's cost truth, not the rate ladder's tier (#152): a total
  // cost that is part buildup and part billing stand-in is not a contract cost, and a
  // margin taken off it is arithmetic wearing a fact's clothes. `margin` only decides
  // which of the two refusals a reader is looking at — no rates at all, or not enough.
  const costKnown = totalCostKnown(burn);
  const revenueKnown = totalRevenueKnown(burn);
  const costWhy = margin ? PARTIAL_COST : LEVEL_1_COST;
  const figures = {
    revenue: revenueKnown ? fact(t.revenue ?? 0) : withheld(PRICE_NOT_REVENUE),
    cost: costKnown ? fact(t.cost ?? 0) : withheld(costWhy),
    // Revenue is asked first, ahead of cost, because the two refusals take different
    // fixes and only one of them is a fix at all: supplying direct rates will not
    // unlock a fee that has no recognised revenue underneath it, so sending a user to
    // the rates form on a fixed-price contract wastes the trip twice over.
    fee: unsupported
      ? withheld(unsupportedNotice)
      : !revenueKnown
      ? withheld(PRICE_NOT_REVENUE_FEE)
      : !costKnown
        ? withheld(margin ? PARTIAL_FEE : LEVEL_1_FEE)
        : t.fee_known
          ? fact(t.fee ?? 0)
          : withheld(NO_FEE_TERMS),
    // Derived here rather than read off the payload because the engine reports
    // margin per CLIN, not per contract. Same definition as `margin_pct` — fee over
    // revenue — so the two reconcile; guarded on revenue so a contract with no
    // recognised revenue yet reports nothing instead of dividing by zero.
    margin: unsupported
      ? withheld(unsupportedNotice)
      : !revenueKnown
      ? withheld(PRICE_NOT_REVENUE)
      : !costKnown
        ? withheld(costWhy)
        : !t.revenue
          ? withheld("No revenue recognised yet.")
          : fact(((t.revenue || 0) - (t.cost || 0)) / t.revenue),
  };
  const applicability = profitabilityLabels(burn);
  if (applicability.earningsApplicable === false) {
    figures.fee = notApplicable(NO_EARNINGS_CONCEPT);
  }
  if (applicability.returnApplicable === false) {
    figures.margin = notApplicable(NO_EARNINGS_CONCEPT);
  }
  return figures;
}

// One CLIN's money columns, under the same rules. `fee_known` is per CLIN because a
// mixed award can price one line fully and leave another's fee terms unstated, and
// collapsing that to a contract-level flag would withhold a fee that is actually known.
export function clinFigures(clin, margin) {
  const policy = clin?.pricing_policy || {};
  const unsupported = policy.status === "unsupported";
  const unsupportedNotice =
    policy.notice || "Contract policy is currently unsupported.";
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
      fee: unsupported ? withheld(unsupportedNotice) : notApplicable(PASS_THROUGH),
      margin: unsupported ? withheld(unsupportedNotice) : notApplicable(PASS_THROUGH),
    };
  }
  // This CLIN's own cost truth, never the contract's (#152). A mixed award prices one
  // line from category rates and leaves another on the billing fallback, and inheriting
  // a contract-wide unlock would print the fallback line's billings under a cost
  // heading. The reason splits the same way: at level 1 nobody has supplied rates, and
  // above it this CLIN's categories in particular are still standing in.
  const costKnown = clinCostKnown(clin);
  // A fixed-price line's `revenue` is its price (#154). Asked per CLIN and not off the
  // contract flag, because the mixed award is the case that matters: the T&M line next
  // to it recognises revenue every week and must keep printing it.
  const revenueKnown = clinRevenueKnown(clin);
  const costWhy = margin ? COST_IS_STANDIN : LEVEL_1_COST;
  const figures = {
    revenue: revenueKnown ? fact(clin.revenue ?? 0) : withheld(PRICE_NOT_REVENUE_CLIN),
    cost: costKnown ? fact(clin.cost ?? 0) : withheld(costWhy),
    fee: !revenueKnown
      ? withheld(PRICE_NOT_REVENUE_FEE_CLIN)
      : !costKnown
        ? withheld(margin ? FEE_IS_STANDIN_CLIN : LEVEL_1_FEE)
        : clin.fee_known
          ? fact(clin.fee_earned ?? 0)
          : withheld(NO_FEE_TERMS_CLIN),
    // With cost known, a null `margin_pct` is one of the two remaining refusals, and
    // they take different fixes: unstated fee terms are fixed by importing a document,
    // no revenue yet by waiting. Naming the cost stand-in here would send a user who
    // already supplied rates back to enter more.
    margin: !revenueKnown
      ? withheld(PRICE_NOT_REVENUE_CLIN)
      : !costKnown
        ? withheld(costWhy)
        : clin.margin_pct != null
          ? fact(clin.margin_pct)
          : withheld(clin.fee_known ? NO_REVENUE_CLIN : NO_FEE_TERMS_CLIN),
  };
  const applicability = pricingApplicability(clin);
  if (applicability.earningsApplicable === false) {
    figures.fee = notApplicable(NO_EARNINGS_CONCEPT);
  }
  if (applicability.returnApplicable === false) {
    figures.margin = notApplicable(NO_EARNINGS_CONCEPT);
  }
  if (unsupported) {
    figures.fee = withheld(unsupportedNotice);
    figures.margin = withheld(unsupportedNotice);
  }
  return figures;
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

export const awardPoolShareLabel = (poolShare) =>
  poolShare == null ? "" : `${money(poolShare)} of pool`;

// Award-stated facts survive level 1; anything computed from cost does not.
//
// `target`, `clause`, `award_pool`, the share ratio and the PTA are printed on the
// award — they are true before a single hour is charged. `earned`, `at_completion`,
// `at_risk`, `absorbed`, `withhold` and `collectable` are all functions of `cost_frac`,
// so at level 1 they are functions of the billing rate and say nothing about fee. This
// split is why `_fee_payload` carries `terms_known` and `cost_known` beside `known`
// instead of only their conjunction.
const FEE_NEEDS_COST =
  "Earned fee is a function of cost, and where cost is the billing rate standing in there is no fee to read — supply direct rates for this CLIN's categories.";
const FEE_NEEDS_TERMS =
  "This award's fee terms are incomplete, so everything earned against them is withheld rather than computed to zero.";

// Both halves of the answer, and both default closed (#153). The projected position is
// the same fee terms applied to projected cost, so it carries the same two flags — a
// payload that omits them has not said the figure is trustworthy, and the old
// `!== false` read turned that silence into a fact. Unknown *terms* matter as much as
// unknown cost: `earned_fee` against a fee structure the award never printed computes
// a clean $0, which is a claim that the work has earned no fee.
export function feeFigures(fp) {
  const why = !(fp.terms_known === true)
    ? FEE_NEEDS_TERMS
    : fp.cost_known === true
      ? null
      : FEE_NEEDS_COST;
  const gated = (value) => (why ? withheld(why) : fact(value ?? 0));
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

// The two entries in `missing` that are reasons rather than field names (#159). The
// award *did* state its cost and fee totals, at the header — so "import the fee
// structure" is the one instruction that cannot help, and the fix is a decision:
// which line the total belongs to, or how a CPAF fee divides into base and pool.
const FEE_GAP_REASONS = {
  clin_allocation: {
    fix: "allocation",
    message:
      "This award states one total fee for the whole contract and more than one line could earn it. Split the total across the CLINs to price them — Runway will not guess the allocation.",
  },
  fee_split: {
    fix: "fee_split",
    message:
      "This award states one total fee, but a cost-plus-award-fee line earns a base fee plus an award pool. Enter the two separately — the total alone cannot be divided into them.",
  },
};

// Why a fee card can't be trusted, or null when it can. Split rather than collapsed:
// missing award terms are fixed by importing a document, missing cost by entering
// rates, and telling a user to do the wrong one wastes the trip.
export function feeGap(fp) {
  if (!fp.terms_known) {
    const all = fp.missing || [];
    // A stated-but-unattributable total outranks the field list: it names a decision
    // to make, where the field list only names blanks.
    const reason = all.map((m) => FEE_GAP_REASONS[m]).find(Boolean);
    if (reason) return reason;
    const missing = all.join(", ");
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
    // With no evaluation periods recorded, `award_available` is 0 because there is
    // nothing to earn against — not because the pool is spent. Verified on a live
    // CPAF (contract 42): a $157,923 pool reporting $0 available and 0 periods. The
    // view has to say which of the two it is, or the card reads as fee already gone.
    periodsRecorded: (fp.periods_total || 0) > 0 || (fp.periods || []).length > 0,
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

// ---- the buildup, expanded (#82 section 2) --------------------------------------
//
// Where an accountant checks Runway's arithmetic against her own, so the chain shows
// the rate applied *and the base it was applied to* at every step rather than totals.
// Reconciliation is the feature. All of it comes off `contract.cost_model.rate_set`
// and the per-CLIN `rate_variance` the engine already computes — the IndirectRates
// view owns rate *entry*, and this view must not become a second place to edit them.

// The base each pool applies to, as the buildup states it. Named in prose because
// "total_cost_input" is a term of art and the whole point of the section is that a
// reader can follow the multiplication.
const POOL_BASE = {
  direct_labor: "direct labor",
  labor_plus_fringe: "direct labor + fringe",
  total_cost_input: "total cost input (labor + fringe + overhead)",
};
export const poolBaseLabel = (base) => POOL_BASE[base] || base || "—";

// The indirect chain, or null at level 1 where there is no buildup to show.
// `status` carries provisional-vs-final: a provisional rate means every figure derived
// from it gets repriced at the year-end true-up (#87), which is a caveat this section
// is the right place to state and the wrong place to hide.
export function rateChain(burn) {
  const set = burn?.contract?.cost_model?.rate_set;
  if (!set || !(set.pools || []).length) return null;
  return {
    fiscalYear: set.fiscal_year,
    scope: set.scope,
    status: set.status,
    // False when a pool the buildup needs is missing, in which case the chain is
    // partial and the cost below it is too.
    complete: set.complete !== false,
    provisional: set.status !== "final",
    steps: (set.pools || []).map((p) => ({
      name: p.name,
      label: p.label || p.name,
      rate: p.rate,
      base: p.base,
      baseLabel: poolBaseLabel(p.base),
      status: p.status,
    })),
  };
}

// Which tier priced an hour. Level 3 is a person's own direct rate, level 2 the LCAT
// category rate, level 1 the billing rate standing in for cost — a CLIN that is 90%
// category-costed and 10% fallback is a real state, and one dominant label hides it.
const COST_SOURCE = {
  employee_direct: "Per-person direct rate",
  lcat_direct: "Category (LCAT) direct rate",
  negotiated_fallback: "Billing rate, standing in for cost",
  none: "Not priced",
};
export const costSourceLabel = (source) => COST_SOURCE[source] || source;

export const pricedBy = (clin) =>
  (clin.cost_rate_mix || []).map((m) => ({
    source: m.source,
    label: costSourceLabel(m.source),
    hours: m.hours,
    // The one tier that is not a cost fact. Flagged so a mixed CLIN can show which
    // slice of its hours is a stand-in.
    standIn: m.source === "negotiated_fallback",
  }));

// Derived-vs-negotiated reconciliation per LCAT (`rate_variance`). Only LCATs whose
// cost was actually derived appear — comparing a fallback against itself would always
// report zero variance and mean nothing. Flattened across CLINs with the code kept,
// because the same LCAT can price differently on two CLINs and that difference is
// itself worth seeing.
export const rateVariance = (burn) =>
  orderedClins(burn).flatMap((c) =>
    (c.rate_variance || []).map((v) => ({ ...v, code: c.code })),
  );

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

// ---- What the view knows right now (#164) --------------------------------------
// Which contract is on screen is part of the truth this surface tells, so the load
// state is a reducer here rather than loose setState calls in the view. Two of the
// transitions are the bug: selecting a contract has to discard the previous one's
// figures *and* its failure, or the screen prints one contract's money under another
// contract's name; and a success has to clear a prior failure, or a stale error sits
// beside good data. `empty` is its own state because "no contracts exist" is a fact
// about the workspace, not a load that never finished.
export const loadIdle = { burn: null, error: null, empty: false };

export function loadReducer(state, event) {
  switch (event.type) {
    case "select":
      return loadIdle;
    case "loaded":
      return { burn: event.burn, error: null, empty: false };
    case "failed":
      return { burn: null, error: event.message, empty: false };
    case "none":
      return { burn: null, error: null, empty: true };
    default:
      return state;
  }
}

// Which of the four screens to draw, in precedence order. A failure outranks an empty
// workspace because a failed contract list is why we don't know it's empty.
export const loadPhase = (state) =>
  state?.error ? "error" : state?.empty ? "empty" : state?.burn ? "ready" : "loading";
