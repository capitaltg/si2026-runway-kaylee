import { test } from "node:test";
import assert from "node:assert/strict";
import { suggestFor } from "./suggest.js";

const fundingItem = (runway_days) => ({
  code: "CLIN 0002",
  name: "Option Labor",
  funded: 1_800_000,
  exhaust_week: 42,
  runway_days,
  mod_in_progress: false,
});

test("funding suggestion escalates within 30 days of exhaustion", () => {
  const s = suggestFor("funding", fundingItem(20), {});
  assert.equal(s.action.kind, "funding");
  assert.equal(s.action.urgent, true);
  assert.match(s.body, /Funding deadline/);
  assert.match(s.body, /20 days/);
  assert.ok(s.result); // shows a "what this does" line when urgent
});

test("funding suggestion stays routine when more than 30 days out", () => {
  const s = suggestFor("funding", fundingItem(90), {});
  assert.equal(s.action.urgent, false);
  assert.doesNotMatch(s.body, /Funding deadline/);
  assert.equal(s.result, null);
});

// ---- #23: the advice is scheduled against a calendar, not a week index ------

test("suggestions carry the hard-stop date when the payload has one", () => {
  const dated = { ...fundingItem(20), stop_date: "2026-06-27" };
  assert.match(suggestFor("funding", dated, {}).body, /27 Jun 26/);
  assert.match(suggestFor("funding", { ...dated, runway_days: 90 }, {}).body, /27 Jun 26/);
});

test("suggestions fall back to the week index when there is no date", () => {
  // A payload older than this bundle carries no `stop_date` — an API process
  // without --reload serves the old shape while Vite has hot-reloaded the client.
  // The copy has to lose the date, never print a placeholder for it.
  const s = suggestFor("funding", fundingItem(20), {});
  assert.match(s.body, /week 42/);
  assert.doesNotMatch(s.body, /—$/);
  assert.doesNotMatch(s.body, /around/);
});

test("a tripwire past its funding advises on the realized loss, not a forecast", () => {
  // Rebalancing forward can't recover money already spent, and the exhaustion
  // week is behind the current week, so quoting it as "today it exhausts in
  // week 14" reads as a future event.
  const s = suggestFor(
    "over",
    {
      code: "CLIN 2001",
      name: "Option Labor",
      limited_by: "funding",
      funded: 1_572_366,
      budget: 3_076_112,
      exhaust_week: 14,
      weeks_early: 38,
      stop_date: "2026-04-21",
      stop_date_passed: true,
    },
    {},
  );
  assert.match(s.body, /already past/);
  assert.match(s.body, /21 Apr 26/);
  assert.match(s.body, /at risk/);
  assert.doesNotMatch(s.body, /week 14/);
});

// --- the tripwire and the "who's running hot" strip must not contradict (#83) --

const overItem = {
  id: "0002",
  code: "CLIN 0002",
  name: "Option Labor",
  budget: 1_400_000,
  limited_by: "ceiling",
  exhaust_week: 44,
  weeks_early: 6,
  stop_date_passed: false,
};

const heatPayload = (diagnosis) => ({
  clins: [
    {
      id: "0002",
      diagnosis,
      exhaust_week: 44,
      exhaust_week_at_expected: 55.2,
      weeks_bought: 11.2,
      excess_weekly_dollars: 240_000,
      people: ["p1", "p2"],
    },
  ],
  people: [
    { id: "p1", name: "Wei Chen", over_hours_per_week: 4, weekly_dollars: 2000 },
    { id: "p2", name: "Aisha Khan", over_hours_per_week: 9, weekly_dollars: 900 },
  ],
});

test("without a heat payload the tripwire advice is unchanged", () => {
  const before = suggestFor("over", overItem, {});
  assert.ok(before.body.includes("Trim the off-pace lines back to plan"));
  assert.ok(!before.body.includes("hours above plan"));
});

test("an overtime-only diagnosis replaces 'rebalance everyone' with the real remedy", () => {
  // Scaling down a team that is already at its expected hours is the wrong fix, and
  // it is what this branch used to advise on exactly this CLIN.
  const s = suggestFor("over", overItem, {}, heatPayload("stop_overtime"));
  assert.ok(s.body.includes("the gap is hours above plan"));
  assert.ok(!s.body.includes("Trim the off-pace lines back to plan"));
  assert.ok(s.body.includes("11.2 weeks of runway"));
});

test("the suggestion names people in the strip's order — hours over, never rate", () => {
  const s = suggestFor("over", overItem, {}, heatPayload("stop_overtime"));
  // Aisha is 9 hrs/wk over on a cheaper rate; Wei is 4 hrs/wk over but costs more.
  assert.ok(s.body.indexOf("Aisha Khan") < s.body.indexOf("Wei Chen"));
});

test("an overstaffing diagnosis keeps the rebalance and says why it is right", () => {
  const s = suggestFor("over", overItem, {}, heatPayload("reduce_staffing"));
  assert.ok(s.body.includes("Trim the off-pace lines back to plan"));
  assert.ok(s.body.includes("even with everyone at their expected hours"));
});

test("a heat payload for a different CLIN is ignored", () => {
  const other = { ...heatPayload("stop_overtime") };
  other.clins[0].id = "0001";
  const s = suggestFor("over", overItem, {}, other);
  assert.ok(s.body.includes("Trim the off-pace lines back to plan"));
});

test("a diagnosis with nobody attached does not change the advice", () => {
  const empty = heatPayload("stop_overtime");
  empty.clins[0].people = [];
  const s = suggestFor("over", overItem, {}, empty);
  assert.ok(s.body.includes("Trim the off-pace lines back to plan"));
});
