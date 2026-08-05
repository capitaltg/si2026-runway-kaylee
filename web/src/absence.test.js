// #85 — the simulator's week walk. The arithmetic is duplicated between here and
// server/app/absence.py (the matrix rescores on every keystroke and cannot round-trip
// per edit), so these tests exist mostly to pin the places the two could drift:
// where week 1 starts, whether a holiday and one person's PTO are unioned or summed,
// and whether the walk reproduces the closed form when nothing is absent.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildAbsenceModel,
  walkRunway,
  weekOf,
  absenceWorkdays,
  shiftDate,
} from "./absence.js";

const POP_START = "2026-01-01";
// Period week 12 is the seven days from 2026-03-19; week 13 runs to 2026-04-01.
// Both are full Mon–Fri weeks, which is what makes the numbers below whole.
const WEEK_12_START = "2026-03-19";
const WEEK_13_END = "2026-04-01";

const model = (opts) =>
  buildAbsenceModel({
    popStart: POP_START,
    fromWeek: 10,
    totalWeeks: 52,
    holidays: [],
    absences: [],
    ...opts,
  });

const pto = (person_id, start, end) => ({ person_id, start, end });

test("week 1 starts on pop_start, matching burn._clock's numbering", () => {
  // If these two ever disagree the matrix and the Flight Deck put the same absence
  // in different weeks and quietly report different runways.
  const popStart = Date.parse("2026-01-01T00:00:00Z");
  assert.equal(weekOf(popStart, Date.parse("2026-01-01T00:00:00Z")), 1);
  assert.equal(weekOf(popStart, Date.parse("2026-01-07T00:00:00Z")), 1);
  assert.equal(weekOf(popStart, Date.parse("2026-01-08T00:00:00Z")), 2);
  assert.equal(weekOf(popStart, Date.parse("2026-03-19T00:00:00Z")), 12);
});

test("nothing entered leaves the model inert", () => {
  const m = model({});
  assert.equal(m.active, false);
  assert.equal(m.factorFor("e1", 12), 1);
  // The caller branches on this to keep its original closed-form arithmetic.
});

test("absence entirely in the past leaves the model inert", () => {
  // Weeks 1–3. Leave already charged was backed out of actuals by PR #95; applying
  // it here too would subtract the same hours twice.
  const m = model({ absences: [pto("e1", "2026-01-05", "2026-01-16")] });
  assert.equal(m.active, false);
});

test("one person's absence reduces only their weeks", () => {
  const m = model({ absences: [pto("e1", WEEK_12_START, WEEK_13_END)] });
  assert.equal(m.active, true);
  assert.equal(m.factorFor("e1", 11), 1); // untouched week
  assert.equal(m.factorFor("e1", 12), 0); // out all five workdays
  assert.equal(m.factorFor("e1", 13), 0);
  assert.equal(m.factorFor("e1", 14), 1);
  assert.equal(m.factorFor("e2", 12), 1); // and nobody else moves
  assert.deepEqual(m.peopleAffected, ["e1"]);
});

test("a holiday applies to everyone without a per-person entry", () => {
  // Memorial Day 2026 is Monday 25 May — one workday of five, in period week 21.
  const m = model({ holidays: [{ date: "2026-05-25" }] });
  assert.equal(m.factorFor("e1", 21), 0.8);
  assert.equal(m.factorFor("anyone-at-all", 21), 0.8);
  assert.deepEqual(m.holidayWeeks, [21]);
  assert.deepEqual(m.peopleAffected, []);
});

test("a holiday inside someone's PTO is not counted twice", () => {
  // Period week 21 runs Thu 21 May → Wed 27 May, so it holds five workdays and this
  // PTO range covers three of them (Mon 25, Tue 26, Wed 27). Memorial Day is Mon 25,
  // already one of those three.
  //
  // Union: 3 days off of 5 → 0.4. Summing the holiday on top would give 4 of 5 →
  // 0.2, and a longer overlap would drive the factor below zero, i.e. claim the
  // contract earns money back over the holiday.
  const m = model({
    holidays: [{ date: "2026-05-25" }],
    absences: [pto("e1", "2026-05-25", "2026-05-29")],
  });
  assert.equal(m.factorFor("e1", 21), 0.4);
  assert.equal(m.factorFor("e2", 21), 0.8); // everyone else loses just the holiday
});

test("a full week of PTO over a holiday still floors at zero, never below", () => {
  const m = model({
    holidays: [{ date: "2026-05-25" }],
    absences: [pto("e1", "2026-05-18", "2026-05-29")], // covers all of week 21
  });
  assert.equal(m.factorFor("e1", 21), 0);
});

test("shiftDate turns a single date into the range end, across month and year edges", () => {
  // A "starts on the 1st" entry stores absence through the last day of the previous
  // month; getting this off by a day silently shifts a hire into the wrong week.
  assert.equal(shiftDate("2026-08-10", -1), "2026-08-09");
  assert.equal(shiftDate("2026-03-01", -1), "2026-02-28");
  assert.equal(shiftDate("2026-01-01", -1), "2025-12-31");
  assert.equal(shiftDate("2026-12-31", 1), "2027-01-01");
  assert.equal(shiftDate("", -1), "");
});

test("a future start date contributes zero burn before it and full burn after", () => {
  // A "starts on" entry is stored as absence from the period start to the day
  // before they arrive — the same dated range PTO uses, so there is one code path.
  const m = model({ absences: [pto("newhire", POP_START, WEEK_13_END)] });
  assert.equal(m.factorFor("newhire", 12), 0);
  assert.equal(m.factorFor("newhire", 13), 0);
  assert.equal(m.factorFor("newhire", 14), 1);
});

test("weekends inside a range are not counted as absence", () => {
  // Nobody charges a Saturday, so counting one would reduce a pace that never
  // included it. 19 Mar → 1 Apr spans 14 calendar days and 10 workdays.
  assert.equal(absenceWorkdays({ start: WEEK_12_START, end: WEEK_13_END }), 10);
  assert.equal(absenceWorkdays({ start: "2026-03-21", end: "2026-03-22" }), 0);
  assert.equal(absenceWorkdays({ start: "2026-04-01", end: "2026-03-19" }), 0);
});

// ------------------------------------------------------------------ the walk

const TEAM = [
  ["e1", 2000],
  ["e2", 2000],
]; // $4,000/week between them

test("the walk reproduces the closed form when nothing is absent", () => {
  // The inert model is meant to be skipped by the caller, but if the branch is ever
  // removed the walk must still land on remaining / weekly rather than near it.
  const w = walkRunway({
    perPerson: TEAM,
    remaining: 60_000,
    currentWeek: 10,
    model: model({}),
  });
  assert.equal(w.exhaustWeek, 25); // 10 + 60,000/4,000
  assert.equal(w.weeksLeft, 15);
});

test("half the team out for two weeks buys exactly one week of runway", () => {
  const w = walkRunway({
    perPerson: TEAM,
    remaining: 60_000,
    currentWeek: 10,
    model: model({ absences: [pto("e1", WEEK_12_START, WEEK_13_END)] }),
  });
  assert.equal(w.exhaustWeek, 26); // $4,000 of burn never happens
});

test("a CLIN nobody is booked on has no runway to walk", () => {
  const w = walkRunway({
    perPerson: [],
    remaining: 60_000,
    currentWeek: 10,
    model: model({}),
  });
  assert.equal(w.exhaustWeek, null);
  assert.equal(w.weeksLeft, null);
});

test("a period with no calendar falls back to no absence", () => {
  // A contract whose period carries no PoP dates has nothing to hang a date off,
  // so the simulator must take its flat path rather than guess an anchor.
  const m = buildAbsenceModel({
    popStart: null,
    fromWeek: 10,
    totalWeeks: 52,
    absences: [pto("e1", WEEK_12_START, WEEK_13_END)],
  });
  assert.equal(m.active, false);
});
