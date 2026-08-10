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

// Local, so this module stays importable by tests without the format helpers'
// DOM-adjacent baggage. Whole dollars — a drift figure carrying cents implies a
// precision weekly-average hours do not have.
function money(n) {
  const v = Math.round(Math.abs(n));
  if (v >= 1000) return `$${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}K`;
  return `$${v}`;
}
