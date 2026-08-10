import { test } from "node:test";
import assert from "node:assert/strict";
import {
  planDrift,
  driftSummary,
  driftSentence,
  hasDrift,
  driftAlert,
  actualsDraft,
  rateResolver,
} from "./drift.js";

// $100/hr flat unless a test says otherwise, so hours and dollars stay legible.
const rate = () => 100;
const NAMES = { 7: "Wei Chen", 8: "Priya Raman", 9: "Dana Fox" };
const nameOf = (id) => NAMES[id];

const drift = (baseline, actuals, opts = {}) =>
  planDrift({ baseline, actuals, rate, nameOf, ...opts });

test("a roster charging its baseline hours has no drift", () => {
  const d = drift(
    { draft: { 7: { "0001": 24 }, 8: { "0001": 16 } } },
    { draft: { 7: { "0001": 24 }, 8: { "0001": 16 } } },
  );
  assert.equal(d.people.length, 0);
  assert.equal(d.deltaCost, 0);
  assert.equal(hasDrift(d), false);
  assert.equal(driftSummary(d), null);
});

test("someone charging more than the baseline is priced per week", () => {
  const d = drift({ draft: { 7: { "0001": 24 } } }, { draft: { 7: { "0001": 38 } } });
  const [row] = d.people;
  assert.equal(row.kind, "over");
  assert.equal(row.deltaHrs, 14);
  assert.equal(row.deltaCost, 1400);
  assert.match(driftSentence(row), /Wei Chen: baseline 24, actual 38 hrs\/wk — above plan/);
});

test("rounding-level differences are not drift", () => {
  const d = drift({ draft: { 7: { "0001": 24 } } }, { draft: { 7: { "0001": 24.3 } } });
  assert.equal(d.people.length, 0);
});

test("someone charging who is not on the baseline at all", () => {
  const d = drift({ draft: { 7: { "0001": 24 } } }, { draft: { 7: { "0001": 24 }, 9: { "0001": 10 } } });
  assert.deepEqual(
    d.people.map((p) => [p.name, p.kind]),
    [["Dana Fox", "unplanned"]],
  );
  assert.match(driftSentence(d.people[0]), /Dana Fox: 10 hrs\/wk, not on the baseline/);
});

test("someone the baseline rolled off but who is still charging says so", () => {
  const d = drift(
    { draft: { 7: { "0001": 24 } }, removed: ["9"] },
    { draft: { 7: { "0001": 24 }, 9: { "0001": 8 } } },
  );
  assert.equal(d.people[0].kind, "rolled_off_charging");
  assert.match(driftSentence(d.people[0]), /rolled off in the baseline, still charging/);
});

test("someone on the baseline charging nothing", () => {
  const d = drift({ draft: { 7: { "0001": 24 }, 8: { "0001": 16 } } }, { draft: { 7: { "0001": 24 } } });
  assert.deepEqual(
    d.people.map((p) => [p.name, p.kind, p.deltaCost]),
    [["Priya Raman", "not_charging", -1600]],
  );
});

test("a planned hire who has not started is reported, but is not drift", () => {
  // The rule that keeps every plan containing a hire from reading as a staffing
  // failure: an `added-` id can never appear on a timesheet.
  const d = drift(
    {
      draft: { 7: { "0001": 24 }, "added-abc": { "0001": 40 } },
      added: [{ id: "added-abc", name: "New BA" }],
    },
    { draft: { 7: { "0001": 24 } } },
  );
  assert.equal(d.people.length, 0);
  assert.deepEqual(
    d.planned.map((p) => [p.name, p.kind]),
    [["New BA", "planned"]],
  );
  assert.equal(d.deltaCost, 0);
  assert.equal(hasDrift(d), true);
  assert.match(driftSentence(d.planned[0]), /New BA: baseline 40 hrs\/wk, planned, not charging yet/);
});

test("a planned hire who has started is scored like anybody else", () => {
  const d = drift(
    {
      draft: { "added-abc": { "0001": 40 } },
      added: [{ id: "added-abc", name: "New BA" }],
    },
    { draft: { "added-abc": { "0001": 50 } } },
  );
  assert.equal(d.planned.length, 0);
  assert.deepEqual(
    d.people.map((p) => [p.name, p.kind, p.deltaHrs]),
    [["New BA", "over", 10]],
  );
});

test("drift is per CLIN, and offsetting moves are not cancelled away", () => {
  // 8 hours shifted from 0002 to 0001 nets to zero hours and zero dollars at this
  // rate, but it is still a staffing change, and the per-CLIN rows have to show it.
  const d = drift(
    { draft: { 7: { "0001": 20, "0002": 20 } } },
    { draft: { 7: { "0001": 28, "0002": 12 } } },
  );
  assert.equal(d.people.length, 0, "net-zero hours is not person-level drift");
  assert.deepEqual(
    d.clins.map((c) => [c.id, c.delta]),
    [
      ["0001", 800],
      ["0002", -800],
    ],
  );
});

test("CLIN rollups price each line at its own rate", () => {
  const byClin = (_id, clinId) => (clinId === "0001" ? 200 : 50);
  const d = planDrift({
    baseline: { draft: { 7: { "0001": 10, "0002": 10 } } },
    actuals: { draft: { 7: { "0001": 20, "0002": 10 } } },
    rate: byClin,
    nameOf,
  });
  assert.deepEqual(
    d.clins.map((c) => [c.id, c.baseline, c.actual]),
    [
      ["0001", 2000, 4000],
      ["0002", 500, 500],
    ],
  );
  assert.equal(d.deltaCost, 2000);
});

test("the summary names the direction, the money and the share", () => {
  const d = drift({ draft: { 7: { "0001": 20 } } }, { draft: { 7: { "0001": 30 } } });
  const s = driftSummary(d);
  assert.equal(s.direction, "above");
  assert.equal(s.people, 1);
  assert.match(s.text, /Running above the baseline by \$1\.0K\/wk \(50%\)/);
});

test("running under the baseline reads as under, not as a negative overrun", () => {
  const d = drift({ draft: { 7: { "0001": 30 } } }, { draft: { 7: { "0001": 20 } } });
  const s = driftSummary(d);
  assert.equal(s.direction, "below");
  assert.match(s.text, /Running below the baseline by \$1\.0K\/wk/);
});

test("people sort by dollar effect, biggest first", () => {
  const d = drift(
    { draft: { 7: { "0001": 20 }, 8: { "0001": 20 }, 9: { "0001": 20 } } },
    { draft: { 7: { "0001": 22 }, 8: { "0001": 40 }, 9: { "0001": 5 } } },
  );
  assert.deepEqual(d.people.map((p) => p.name), ["Priya Raman", "Dana Fox", "Wei Chen"]);
});

test("an unpriced person still counts as hours drift, at zero dollars", () => {
  // #64 leaves LCATs that resolve to no rate. Dropping those people would hide a
  // real staffing change behind a pricing gap.
  const d = planDrift({
    baseline: { draft: { 7: { "0001": 20 } } },
    actuals: { draft: { 7: { "0001": 35 } } },
    rate: () => 0,
    nameOf,
  });
  assert.equal(d.people[0].deltaHrs, 15);
  assert.equal(d.people[0].deltaCost, 0);
});

test("no baseline at all is empty, not an error", () => {
  const d = planDrift({ actuals: { draft: { 7: { "0001": 20 } } }, rate, nameOf });
  assert.equal(d.people.length, 1);
  assert.equal(d.people[0].kind, "unplanned");
  assert.equal(planDrift().people.length, 0);
});

// --- reading an allocation payload ------------------------------------------

const PAYLOAD = {
  clins: [
    { id: "0001", blended_rate: 120 },
    { id: "0002", blended_rate: 90 },
  ],
  employees: [
    { id: 7, name: "Wei Chen", cells: { "0001": { hours: 24, rate: 210 } } },
    { id: 8, name: "Priya Raman", cells: { "0001": { hours: 12 }, "0002": { hours: 8, rate: 95 } } },
  ],
};

test("the actuals grid covers every CLIN, charged or not", () => {
  assert.deepEqual(actualsDraft(PAYLOAD), {
    7: { "0001": 24, "0002": 0 },
    8: { "0001": 12, "0002": 8 },
  });
  assert.deepEqual(actualsDraft(), {});
});

test("a person with no resolved rate falls back to the CLIN's blended rate", () => {
  // #64: an unpriced LCAT has to cost something, or drift would report a staffing
  // change as free.
  const r = rateResolver(PAYLOAD);
  assert.equal(r(7, "0001"), 210);
  assert.equal(r(8, "0001"), 120, "no rate on the cell — blended");
  assert.equal(r(8, "0002"), 95);
  assert.equal(r(999, "0002"), 90, "somebody new — blended");
  assert.equal(r(7, "9999"), 0, "no such CLIN");
});

test("a plan's planned hires price at the rates the plan gave them", () => {
  const r = rateResolver({ ...PAYLOAD, added: [{ id: "added-abc", rates: { "0001": 175 } }] });
  assert.equal(r("added-abc", "0001"), 175);
  assert.equal(r("added-abc", "0002"), 90, "no rate for that CLIN — blended");
});

// --- the Flight Deck card ---------------------------------------------------

test("small dollar drift does not earn a Flight Deck card", () => {
  // 2 hrs on a 100-hr baseline is 2% — real, in the panel, and not worth
  // interrupting somebody who is reading a tripwire.
  const d = drift({ draft: { 7: { "0001": 100 } } }, { draft: { 7: { "0001": 102 } } });
  assert.equal(d.people.length, 1, "still drift");
  assert.equal(driftAlert(d), null, "just not card-worthy");
});

test("a material dollar gap earns a card", () => {
  const d = drift({ draft: { 7: { "0001": 20 } } }, { draft: { 7: { "0001": 30 } } });
  const a = driftAlert(d, { runwayLost: 28, clinCode: "0001" });
  assert.equal(a.share, 0.5);
  assert.equal(a.runwayLost, 28);
  assert.match(a.headline, /Running above the baseline by \$1\.0K\/wk/);
  assert.equal(a.movers.length, 1);
});

test("anybody charging who is not on the baseline earns a card at any size", () => {
  // The dollars are not what makes this worth knowing.
  const d = drift({ draft: { 7: { "0001": 100 } } }, { draft: { 7: { "0001": 100 }, 9: { "0001": 1 } } });
  const a = driftAlert(d);
  assert.ok(a, "roster break is its own trigger");
  assert.deepEqual(a.roster.map((p) => p.kind), ["unplanned"]);
});

test("a card names at most three movers", () => {
  const many = { draft: {} };
  const base = { draft: {} };
  for (const id of [7, 8, 9, 10, 11]) {
    base.draft[id] = { "0001": 20 };
    many.draft[id] = { "0001": 40 };
  }
  const a = driftAlert(drift(base, many));
  assert.equal(a.movers.length, 3);
  assert.equal(a.people, 5, "but it still says how many there are");
});

test("no drift is no card", () => {
  assert.equal(driftAlert(drift({ draft: { 7: { "0001": 20 } } }, { draft: { 7: { "0001": 20 } } })), null);
  assert.equal(driftAlert(null), null);
});
