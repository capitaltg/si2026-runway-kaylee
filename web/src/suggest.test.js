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
