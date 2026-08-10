// #67 item 2 — drift: are we running the plan we committed to?
//
// Once a contract has an active baseline, the interesting question on the matrix
// stops being "what if" and becomes "what happened". Drift is one comparison —
// baseline hours per person per CLIN against the hours those people are actually
// charging — priced at the same rates the plan was scored with, so the answer comes
// out in dollars per week and not just in hours.
//
// Two rules this module exists to hold:
//
//   1. A planned add that hasn't started is not drift. Baseline rows minted by the
//      add-person form carry ids that can never appear on a timesheet, so scoring
//      them as "committed to 24 hrs/wk, charging nothing" would report a staffing
//      failure for every plan that includes a hire. They are reported separately, as
//      what they are: planned, not yet charging.
//   2. Silence beats noise at the margin. An hour here or there against a weekly
//      average is not a decision anybody makes, so drift under a threshold is "on
//      plan" — a panel that flags everyone flags nothing.

import { isAddedId } from "./plans.js";

// Weekly hours are averages over a period, so they arrive fractional. Half an hour a
// week is $60-ish on a senior rate — below the noise floor of the timesheet data
// feeding it, and not a number anyone staffs against.
const HRS_EPSILON = 0.5;

const sum = (obj) => Object.values(obj || {}).reduce((s, n) => s + (Number(n) || 0), 0);

// --- Reading an allocation payload -------------------------------------------
//
// Both of these were private to the matrix until the Flight Deck needed to state the
// same drift in a card. Two copies of "what does this person cost on this CLIN"
// would eventually disagree, and the version people would trust is whichever one
// they happened to be looking at.

/** The synced actuals as an editable {emp: {clin: hrs}} grid. */
export function actualsDraft(d) {
  const draft = {};
  for (const e of d?.employees || []) {
    draft[e.id] = {};
    for (const c of d?.clins || []) draft[e.id][c.id] = e.cells?.[c.id]?.hours || 0;
  }
  return draft;
}

/**
 * $/hr for (person, CLIN): their resolved rate, else the CLIN's blended fallback.
 *
 * `added` are a plan's planned hires, who are in no employee list and carry their own
 * rates. The blended fallback is what keeps an unpriced LCAT (#64) costing something
 * rather than silently costing nothing.
 */
export function rateResolver({ clins = [], employees = [], added = [] } = {}) {
  const m = {};
  for (const c of clins) m[c.id] = { _blended: c.blended_rate || 0 };
  for (const e of employees)
    for (const [cid, cell] of Object.entries(e.cells || {}))
      (m[cid] ||= {})[e.id] = cell.rate ?? null;
  for (const a of added || [])
    for (const [cid, rt] of Object.entries(a.rates || {})) (m[cid] ||= {})[a.id] = rt;
  return (empId, clinId) => {
    const c = m[clinId] || {};
    return c[empId] ?? c._blended ?? 0;
  };
}

/** Per-CLIN hours for one person in one grid, zeros dropped. */
function rowFor(draft, empId) {
  const out = {};
  for (const [clinId, hrs] of Object.entries(draft?.[empId] || {})) {
    const n = Number(hrs) || 0;
    if (n) out[clinId] = n;
  }
  return out;
}

/**
 * How far the actuals have drifted from the committed staffing.
 *
 * `baseline` and `actuals` are both sim states ({draft, added, removed}); `rate` is
 * the same (empId, clinId) => $/hr resolver the matrix scores plans with, so drift
 * dollars and plan dollars can never disagree. `nameOf` resolves a person id to a
 * display name — planned adds are not in the employee list, so the caller owns it.
 */
export function planDrift({ baseline, actuals, rate, nameOf } = {}) {
  const basedraft = baseline?.draft || {};
  const actual = actuals?.draft || {};
  const rolledOff = new Set((baseline?.removed || []).map(String));
  const addedNames = new Map(
    (baseline?.added || []).map((a) => [String(a.id), a.name || "Planned add"]),
  );

  const ids = new Set([
    ...Object.keys(basedraft),
    ...Object.keys(actual),
    ...addedNames.keys(),
    ...rolledOff,
  ]);

  const people = [];
  const planned = [];
  const byClin = {};
  const bump = (clinId, key, amount) => {
    byClin[clinId] = byClin[clinId] || { id: clinId, baseline: 0, actual: 0 };
    byClin[clinId][key] += amount;
  };

  for (const id of ids) {
    const b = rowFor(basedraft, id);
    const a = rowFor(actual, id);
    const clinIds = [...new Set([...Object.keys(b), ...Object.keys(a)])];

    let deltaHrs = 0;
    let deltaCost = 0;
    const clins = [];
    for (const clinId of clinIds) {
      const bh = b[clinId] || 0;
      const ah = a[clinId] || 0;
      const r = Number(rate?.(id, clinId)) || 0;
      bump(clinId, "baseline", bh * r);
      bump(clinId, "actual", ah * r);
      if (Math.abs(ah - bh) >= HRS_EPSILON)
        clins.push({ id: clinId, baseline: bh, actual: ah, delta: ah - bh });
      deltaHrs += ah - bh;
      deltaCost += (ah - bh) * r;
    }

    const baselineHrs = sum(b);
    const actualHrs = sum(a);
    const row = {
      id,
      name: nameOf?.(id) || addedNames.get(String(id)) || String(id),
      baselineHrs,
      actualHrs,
      deltaHrs,
      deltaCost,
      clins: clins.sort((x, y) => Math.abs(y.delta) - Math.abs(x.delta)),
    };

    // A planned hire who hasn't started is a plan not yet executed, not a staffing
    // breach — reported, but never as drift and never in the dollar total.
    if (isAddedId(id) && !actualHrs) {
      if (baselineHrs) planned.push({ ...row, kind: "planned" });
      continue;
    }

    if (Math.abs(deltaHrs) < HRS_EPSILON) continue;
    if (!baselineHrs)
      row.kind = rolledOff.has(String(id)) ? "rolled_off_charging" : "unplanned";
    else if (!actualHrs) row.kind = "not_charging";
    else row.kind = deltaHrs > 0 ? "over" : "under";
    people.push(row);
  }

  // Biggest dollar effect first — that is the order the conversation happens in.
  people.sort((x, y) => Math.abs(y.deltaCost) - Math.abs(x.deltaCost));

  const clins = Object.values(byClin)
    .map((c) => ({ ...c, delta: c.actual - c.baseline }))
    .filter((c) => c.baseline || c.actual)
    .sort((x, y) => String(x.id).localeCompare(String(y.id)));

  return {
    people,
    planned,
    clins,
    // The headline: what running the actual roster costs per week against what the
    // committed one would. Planned-not-started people are excluded from both sides —
    // they are in neither the actual burn nor, yet, in reality.
    deltaCost: people.reduce((s, p) => s + p.deltaCost, 0),
    baselineCost: clins.reduce((s, c) => s + c.baseline, 0),
    actualCost: clins.reduce((s, c) => s + c.actual, 0),
  };
}

/** Is there anything here worth showing a badge for? */
export function hasDrift(drift) {
  return Boolean(drift && (drift.people.length || drift.planned.length));
}

const KIND_WORDS = {
  over: "above plan",
  under: "below plan",
  unplanned: "not on the baseline",
  rolled_off_charging: "rolled off in the baseline, still charging",
  not_charging: "on the baseline, charging nothing",
  planned: "planned, not charging yet",
};

/** One person's drift, in the words the panel and the Flight Deck both use. */
export function driftSentence(row) {
  const what = KIND_WORDS[row.kind] || "off plan";
  if (row.kind === "unplanned" || row.kind === "rolled_off_charging")
    return `${row.name}: ${hrs(row.actualHrs)} hrs/wk, ${what}`;
  if (row.kind === "not_charging" || row.kind === "planned")
    return `${row.name}: baseline ${hrs(row.baselineHrs)} hrs/wk, ${what}`;
  return `${row.name}: baseline ${hrs(row.baselineHrs)}, actual ${hrs(row.actualHrs)} hrs/wk — ${what}`;
}

const hrs = (n) => (Math.round(n * 10) / 10).toString();

/**
 * The one-line summary — what the drift costs per week, as a phrase.
 *
 * Null when nothing has moved, so a caller can decide not to render a row at all
 * rather than render "$0/wk over plan", which reads as a measurement failure.
 */
export function driftSummary(drift) {
  if (!drift || !drift.people.length) return null;
  const d = drift.deltaCost;
  const pct = drift.baselineCost ? Math.abs(d) / drift.baselineCost : null;
  const share = pct != null ? ` (${Math.round(pct * 100)}%)` : "";
  const dir = d >= 0 ? "above" : "below";
  return {
    delta: d,
    direction: dir,
    people: drift.people.length,
    text: `Running ${dir} the baseline by ${money(Math.abs(d))}/wk${share}`,
  };
}

// --- The Flight Deck card (#67 item 3) ---------------------------------------
//
// Drift belongs next to the burn tripwires, but not all of it: the matrix panel is
// somewhere you go to read every row, and the Flight Deck is somewhere you get
// interrupted. A card for a 3% overage would train people to page past the card that
// says a CLIN is about to overrun.

// A tenth of the committed staffing cost. Below that, weekly-average noise and
// ordinary week-to-week variation are indistinguishable from a decision.
const MATERIAL_SHARE = 0.1;

/**
 * The Flight Deck's version of drift, or null if this doesn't deserve a card.
 *
 * Two ways in, deliberately: a material dollar gap, or *any* roster break. Someone
 * charging a contract they were never planned onto is worth interrupting for at any
 * size — it is a different kind of problem from working more hours than planned, and
 * the dollars are not what makes it worth knowing.
 */
export function driftAlert(drift, { runwayLost = null, clinCode = null } = {}) {
  if (!drift || !drift.people.length) return null;
  const roster = drift.people.filter(
    (p) => p.kind === "unplanned" || p.kind === "rolled_off_charging" || p.kind === "not_charging",
  );
  const share = drift.baselineCost ? Math.abs(drift.deltaCost) / drift.baselineCost : 0;
  if (share < MATERIAL_SHARE && !roster.length) return null;

  const summary = driftSummary(drift);
  return {
    key: "baseline-drift",
    deltaCost: drift.deltaCost,
    share,
    // Highest-dollar movers first, already sorted by planDrift. Three is what fits
    // in a card before it stops being a headline.
    movers: drift.people.slice(0, 3),
    roster,
    people: drift.people.length,
    runwayLost,
    clinCode,
    headline: summary.text,
  };
}

// Local, so this module stays importable by tests without the format helpers'
// DOM-adjacent baggage. Whole dollars — a drift figure carrying cents implies a
// precision weekly-average hours do not have.
function money(n) {
  const v = Math.round(Math.abs(n));
  if (v >= 1000) return `$${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}K`;
  return `$${v}`;
}
