// Pure sentence-building for "who's running hot" (#83). The server decides who is
// hot, by how much, and what the remedy is (see server/app/heat.py); this file only
// turns that into the prose the Flight Deck reads out, so the wording is testable
// without mounting a view.
//
// Every sentence here leads with HOURS and closes with DOLLARS. That order is the
// point of the feature: the hours are the finding a PM can act on, and the dollars
// are why it earns a slot on a money dashboard.

import { money } from "./format.js";

// How many rows the collapsed strip shows before "show all". The Flight Deck is
// already tall — the strip has to be readable at a glance or it becomes another
// section people scroll past.
export const COLLAPSED_ROWS = 3;

const plural = (n, word) => `${n} ${word}${Math.abs(n) === 1 ? "" : "s"}`;

// Hours read better without a trailing ".0" but must not lose a genuine half hour.
const hrs = (n) => {
  const v = Number(n || 0);
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
};

/** The window the capacity check was measured over, as prose. */
export function windowPhrase(heat) {
  const weeks = heat?.window?.weeks || 0;
  if (!weeks) return "no charged weeks";
  return `the last ${plural(weeks, "charged week")}`;
}

/**
 * Why `available` is not just the expected week times the weeks in the window.
 *
 * Because the row did not add up without it. A person expected at 40 hrs/wk over a
 * four-week window read "against 144 available" — and 40 × 4 is 160, so the sentence
 * asked the reader to trust a number its own next line contradicted. The missing 16
 * hours were leave, which `heat.py` nets out on purpose (a month with a week off offers
 * 120 hours, so billing 126 is genuinely over) and which the server has been sending
 * all along as `leave_hours` / `holiday_hours`.
 *
 * Returns null when nothing was deducted: expected × weeks is then the whole story and
 * spelling out arithmetic the reader can already do is noise.
 */
export function availabilityNote(person, heat) {
  const weeks = heat?.window?.weeks || 0;
  const gross = Number(person?.expected_hours_per_week || 0) * weeks;
  if (!gross) return null;
  const deductions = [];
  if (person.leave_hours) deductions.push(`${hrs(person.leave_hours)} hrs leave`);
  if (person.holiday_hours)
    deductions.push(`${hrs(person.holiday_hours)} hrs holiday`);
  if (!deductions.length) return null;
  return `${hrs(gross)} expected, less ${deductions.join(" and ")}`;
}

/**
 * One person's finding, hours first.
 *
 * "184 hrs against 152 available (160 expected, less 8 hrs leave) over the last 4
 *  charged weeks — 32 hrs over (8 hrs/wk), costing CLIN 0002 $3,200/wk"
 */
export function personSentence(person, heat) {
  if (!person) return "";
  // Booked hours, not this contract's (#116). The right-hand number is a whole-person
  // expectation, so the left-hand one has to be the whole person's too — otherwise a
  // row that reads "100 hrs against 152 available — 32 hrs over" does not add up.
  const worked = person.worked_hours_booked ?? person.worked_hours;
  const note = availabilityNote(person, heat);
  const parts = [
    `${hrs(worked)} hrs against ${hrs(person.available_hours)} available${note ? ` (${note})` : ""} over ${windowPhrase(heat)}`,
    `${hrs(person.over_hours)} hrs over (${hrs(person.over_hours_per_week)} hrs/wk)`,
  ];
  const clins = (person.clins || []).map((c) => c.clin);
  const priced = (person.clins || []).some((c) => !c.unpriced);
  if (priced && person.weekly_dollars) {
    parts.push(
      `costing ${clins.length === 1 ? `CLIN ${clins[0]}` : `CLINs ${clins.join(", ")}`} ${money(person.weekly_dollars)}/wk`,
    );
  } else if (clins.length) {
    // Unpriced hours are real hours. Naming the CLIN without a dollar figure is
    // the honest read — inventing a rate here is what #64 exists to prevent.
    parts.push(
      `on ${clins.length === 1 ? `CLIN ${clins[0]}` : `CLINs ${clins.join(", ")}`}, not yet priced`,
    );
  }
  return parts.join(" — ");
}

/** Why the baseline is what it is — and whether it is an assumption. */
export function expectationNote(person) {
  if (!person) return "";
  const base = `${hrs(person.expected_hours_per_week)} hrs/wk expected`;
  if (person.expected_assumed) {
    return `${base} — assumed, nothing is set for them`;
  }
  return `${base} — ${person.expected_label || "set"}`;
}

/**
 * Where the rest of their week is, when some of it is on another contract (#116).
 *
 * Without this the row is unanswerable: the hours it counts are not all on the
 * contract whose dashboard is showing them, and a PM looking at their own grid would
 * not find them. Names the contracts so the conversation has somewhere to go.
 */
export function elsewhereNote(person) {
  const elsewhere = person?.elsewhere || [];
  if (!elsewhere.length || !person?.worked_hours_elsewhere) return null;
  const names = elsewhere.map((e) => e.contract).filter(Boolean);
  return `${hrs(person.worked_hours_elsewhere)} hrs of that on ${names.join(", ") || "another contract"}`;
}

/** The payroll-confirmed overtime line, when the feed sent the split. */
export function overtimeNote(person) {
  if (!person?.ot_known) return null;
  if (!person.ot_hours) return "none of it booked as overtime";
  return `${hrs(person.ot_hours)} hrs of it booked as overtime`;
}

/**
 * The diagnosis for one off-pace CLIN — the two-forecast sentence.
 *
 * This is the part that isn't a restatement of the dashboard: the forward pace the
 * tripwire is built on already contains the overtime, so removing it gives a second
 * date, and the gap between the two is what the overtime costs in weeks of runway.
 */
export function diagnosisSentence(clin) {
  if (!clin) return "";
  const at = clin.exhaust_week_at_expected;
  // Hours-only (#193): this CLIN's dollars are on pace, so there is no dollar exhaust
  // week to quote. Either sentence below would invent a money forecast the payload does
  // not make — the finding here is the award's contracted hours, not the budget.
  if (clin.hot_because && !clin.hot_because.includes("dollars")) {
    return `CLIN ${clin.id} is on pace on dollars, but its contracted hours run out before the period does — the hours below are what's consuming them.`;
  }
  if (clin.diagnosis === "stop_overtime") {
    const bought =
      clin.weeks_bought > 0 ? ` — ${hrs(clin.weeks_bought)} weeks of runway` : "";
    return `Stop the overtime: at expected hours CLIN ${clin.id} finishes inside its budget instead of running out in week ${Math.round(clin.exhaust_week)}${bought}.`;
  }
  return `Cut staffing: CLIN ${clin.id} still runs out in week ${Math.round(at)} of ${clin.total_weeks || "?"} even with everyone at their expected hours — the overtime is not the whole problem.`;
}

/** The hours-ceiling line, where the award printed estimated hours. */
export function ceilingSentence(ceiling) {
  if (!ceiling) return "";
  const who = ceiling.lcat ? ceiling.lcat : `CLIN ${ceiling.clin}`;
  const scope = ceiling.lcat ? ` on CLIN ${ceiling.clin}` : "";
  if (ceiling.overrun_hours) {
    return `${who}${scope} has charged ${hrs(ceiling.charged_hours)} of the ${hrs(ceiling.contracted_hours)} hours the award estimates — ${hrs(ceiling.overrun_hours)} hrs past it already.`;
  }
  const when =
    ceiling.exhaust_week != null
      ? ` — at ${hrs(ceiling.pace_per_week)} hrs/wk the estimate is used up in week ${Math.round(ceiling.exhaust_week)}`
      : "";
  return `${who}${scope}: ${hrs(ceiling.charged_hours)} of ${hrs(ceiling.contracted_hours)} contracted hours charged${when}.`;
}

// The two orderings, both explicit. `hours` is the default because the finding is
// the hours; `cost` exists because the money is why it's on this dashboard — but a
// PM has to choose it and see it named, rather than getting a pay ranking by
// accident. Sorting by dollars silently is how "who's working too much" turns into
// "who is expensive", and those are not the same list.
export const BY_HOURS = "hours";
export const BY_COST = "cost";

export function sortPeople(people, order) {
  const rows = [...(people || [])];
  if (order === BY_COST) {
    return rows.sort(
      (a, b) =>
        b.weekly_dollars - a.weekly_dollars ||
        b.over_hours_per_week - a.over_hours_per_week ||
        a.name.localeCompare(b.name),
    );
  }
  return rows.sort(
    (a, b) =>
      b.over_hours_per_week - a.over_hours_per_week ||
      b.weekly_dollars - a.weekly_dollars ||
      a.name.localeCompare(b.name),
  );
}

/**
 * Everything the strip needs, or a reason there is nothing to show.
 *
 * `empty` is deliberately a sentence rather than a bare false: a section that says
 * "nobody is over their expected hours" is information, and one that silently
 * vanishes reads as broken.
 */
export function heatSummary(heat) {
  const people = heat?.people || [];
  const clins = (heat?.clins || []).map((c) => ({
    ...c,
    total_weeks: heat?.total_weeks,
  }));
  const ceilings = (heat?.hours_ceilings || []).filter((c) => c.early);
  if (!people.length) {
    return {
      people: [],
      clins: [],
      ceilings,
      empty: (heat?.window?.weeks ?? 0)
        ? "Nobody is working above their expected hours on a CLIN that's off pace or running out of contracted hours."
        : "No timesheet weeks synced yet, so hours against capacity can't be read.",
    };
  }
  return { people, clins, ceilings, empty: null };
}
