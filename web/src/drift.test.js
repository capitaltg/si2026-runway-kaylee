import { test } from "node:test";
import assert from "node:assert/strict";
import { planDrift, driftSummary, driftSentence, hasDrift } from "./drift.js";

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
