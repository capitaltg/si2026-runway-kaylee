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

test("an overrun suggestion names roll-offs before grouped trims", () => {
  const s = suggestFor(
    "over",
    { code: "CLIN 0002", budget: 1_000_000, exhaust_week: 40, weeks_early: 3 },
    {},
    [
      { name: "Aisha Khan", lcat: "Cyber Engineer III", kind: "roll_off", from_hours: 24, to_hours: 0, clears_lcat_flag: false },
      { name: "Wei Chen", lcat: "Engineer I", kind: "roll_off", from_hours: 16, to_hours: 0, clears_lcat_flag: true },
      { name: "Dana Yu", kind: "trim", from_hours: 32, to_hours: 24 },
      { name: "Marcus Lee", kind: "trim", from_hours: 32, to_hours: 24 },
    ],
  );
  assert.match(s.body, /Roll Aisha Khan \(Cyber Engineer III\) off CLIN 0002/);
  assert.match(s.body, /Roll Wei Chen \(Engineer I\) off CLIN 0002/);
  assert.doesNotMatch(s.body, /LCAT flag/);
  assert.match(s.body, /Trim Dana Yu & Marcus Lee to 24 hrs\/wk/);
});
