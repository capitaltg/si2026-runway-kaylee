import { test } from "node:test";
import assert from "node:assert/strict";
import {
  personSentence,
  expectationNote,
  overtimeNote,
  diagnosisSentence,
  ceilingSentence,
  heatSummary,
  windowPhrase,
} from "./heat.js";

const HEAT = {
  window: { weeks: 4, from: "2026-02-06", to: "2026-02-27" },
  total_weeks: 52,
};

const person = (over) => ({
  id: "e1",
  name: "Alex Cole",
  worked_hours: 184,
  available_hours: 152,
  over_hours: 32,
  over_hours_per_week: 8,
  expected_hours_per_week: 40,
  expected_label: "a 40-hour week, assumed — nothing is set",
  expected_assumed: true,
  weekly_dollars: 3200,
  ot_known: true,
  ot_hours: 32,
  clins: [
    { clin: "0002", hours_per_week: 46, rate: 100, weekly_dollars: 3200, unpriced: false },
  ],
  ...over,
});

// --- the sentence leads with hours and closes with dollars -------------------

test("the person sentence names hours worked, hours available, the excess, then the cost", () => {
  assert.equal(
    personSentence(person(), HEAT),
    "184 hrs against 152 available over the last 4 charged weeks — 32 hrs over (8 hrs/wk) — costing CLIN 0002 $3,200/wk",
  );
});

test("unpriced hours name the CLIN without inventing a dollar figure", () => {
  const p = person({
    weekly_dollars: 0,
    clins: [
      { clin: "0002", hours_per_week: 46, rate: null, weekly_dollars: null, unpriced: true },
    ],
  });
  assert.ok(personSentence(p, HEAT).includes("on CLIN 0002, not yet priced"));
  assert.ok(!personSentence(p, HEAT).includes("$"));
});

test("every CLIN the excess lands on is listed", () => {
  const p = person({
    clins: [
      { clin: "0001", weekly_dollars: 1200, unpriced: false },
      { clin: "0002", weekly_dollars: 2000, unpriced: false },
    ],
  });
  assert.ok(personSentence(p, HEAT).includes("CLINs 0001, 0002 $3,200/wk"));
});

test("a genuine half hour survives, but whole hours print clean", () => {
  assert.ok(personSentence(person({ over_hours_per_week: 2.5 }), HEAT).includes("(2.5 hrs/wk)"));
  assert.ok(personSentence(person(), HEAT).includes("(8 hrs/wk)"));
});

// --- the expectation is never presented as settled when it is a guess --------

test("an unconfigured expectation says it is an assumption", () => {
  assert.equal(
    expectationNote(person()),
    "40 hrs/wk expected — assumed, nothing is set for them",
  );
});

test("a configured expectation names the level that answered", () => {
  const p = person({
    expected_assumed: false,
    expected_hours_per_week: 32,
    expected_label: "the contract's utilisation target",
  });
  assert.equal(expectationNote(p), "32 hrs/wk expected — the contract's utilisation target");
});

// --- overtime is corroboration, not the signal ------------------------------

test("payroll-confirmed overtime is named when the split exists", () => {
  assert.equal(overtimeNote(person()), "32 hrs of it booked as overtime");
});

test("no overtime column means the note is absent, not zero", () => {
  assert.equal(overtimeNote(person({ ot_known: false, ot_hours: null })), null);
});

test("confirmed-zero overtime is a different finding from an absent split", () => {
  // The time is being worked above expectation but not recorded as overtime.
  assert.equal(overtimeNote(person({ ot_hours: 0 })), "none of it booked as overtime");
});

// --- the two forecasts read as a remedy, not two dates ----------------------

test("overtime-only diagnoses stopping it, priced in weeks of runway", () => {
  const sentence = diagnosisSentence({
    id: "0002",
    diagnosis: "stop_overtime",
    exhaust_week: 44,
    exhaust_week_at_expected: 55.2,
    weeks_bought: 11.2,
    total_weeks: 52,
  });
  assert.ok(sentence.includes("Stop the overtime"));
  assert.ok(sentence.includes("running out in week 44"));
  assert.ok(sentence.includes("11.2 weeks of runway"));
});

test("overstaffing diagnoses cutting people, and says the overtime is not the whole problem", () => {
  const sentence = diagnosisSentence({
    id: "0002",
    diagnosis: "reduce_staffing",
    exhaust_week: 41,
    exhaust_week_at_expected: 46,
    total_weeks: 52,
  });
  assert.ok(sentence.includes("Cut staffing"));
  assert.ok(sentence.includes("week 46 of 52"));
  assert.ok(sentence.includes("not the whole problem"));
});

// --- the hours ceiling only speaks where the award printed hours ------------

test("the ceiling reports charged against contracted hours and when they run out", () => {
  assert.equal(
    ceilingSentence({
      clin: "0002",
      lcat: "Systems Engineer",
      contracted_hours: 1920,
      charged_hours: 1412,
      pace_per_week: 46,
      exhaust_week: 41.04,
      early: true,
      overrun_hours: null,
    }),
    "Systems Engineer on CLIN 0002: 1412 of 1920 contracted hours charged — at 46 hrs/wk the estimate is used up in week 41.",
  );
});

test("an estimate already blown says so plainly", () => {
  const sentence = ceilingSentence({
    clin: "0002",
    lcat: "Systems Engineer",
    contracted_hours: 100,
    charged_hours: 180,
    overrun_hours: 80,
  });
  assert.ok(sentence.includes("80 hrs past it already"));
});

test("a CLIN-total estimate names the CLIN, not a category", () => {
  const sentence = ceilingSentence({
    clin: "0002",
    lcat: null,
    contracted_hours: 4000,
    charged_hours: 180,
    pace_per_week: 45,
    exhaust_week: 90,
  });
  assert.ok(sentence.startsWith("CLIN 0002:"));
});

// --- an empty result says so instead of disappearing ------------------------

test("nobody-hot and no-data are different sentences", () => {
  assert.ok(heatSummary({ ...HEAT, people: [] }).empty.includes("Nobody is working above"));
  assert.ok(
    heatSummary({ window: { weeks: 0 }, people: [] }).empty.includes("No timesheet weeks synced"),
  );
});

test("only ceilings that land before the work does are shown", () => {
  const summary = heatSummary({
    ...HEAT,
    people: [person()],
    hours_ceilings: [
      { clin: "0001", early: true, contracted_hours: 100, charged_hours: 90 },
      { clin: "0002", early: false, contracted_hours: 9000, charged_hours: 90 },
    ],
  });
  assert.deepEqual(
    summary.ceilings.map((c) => c.clin),
    ["0001"],
  );
  assert.equal(summary.empty, null);
});

test("the contract's week count rides onto each CLIN so the diagnosis can print it", () => {
  const summary = heatSummary({
    ...HEAT,
    people: [person()],
    clins: [{ id: "0002", diagnosis: "reduce_staffing", exhaust_week_at_expected: 46 }],
  });
  assert.equal(summary.clins[0].total_weeks, 52);
});

// --- the window is named so the numbers are checkable ----------------------

test("the window reads as prose", () => {
  assert.equal(windowPhrase(HEAT), "the last 4 charged weeks");
  assert.equal(windowPhrase({ window: { weeks: 1 } }), "the last 1 charged week");
  assert.equal(windowPhrase({}), "no charged weeks");
});
