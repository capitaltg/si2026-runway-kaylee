import { test } from "node:test";
import assert from "node:assert/strict";
import { mergeConfidence, newWarnings } from "./ingest-confidence.js";

const ext = (overrides = {}) => ({
  contract: { piid: "T-1", field_confidence: { total_ceiling: 0.95 } },
  clins: [{ clin: "0001", confidence: 0.93, confidence_note: null }],
  ...overrides,
});

const flagged = () =>
  ext({
    contract: { piid: "T-1", field_confidence: { total_ceiling: 0.55 } },
    clins: [{ clin: "0001", confidence: 0.55, confidence_note: "doesn't foot" }],
  });

const RESCORED_BAD = {
  confidence_source: "confirmed",
  field_confidence: { total_ceiling: 0.55 },
  clin_confidence: [
    { clin: "0001", confidence: 0.55, confidence_note: "doesn't foot" },
  ],
};

const RESCORED_CLEAN = {
  confidence_source: "confirmed",
  field_confidence: { total_ceiling: 0.95 },
  clin_confidence: [{ clin: "0001", confidence: 0.93, confidence_note: null }],
};

test("merge takes the server's rescored values over the ones on screen", () => {
  const merged = mergeConfidence(ext(), RESCORED_BAD);
  assert.equal(merged.contract.field_confidence.total_ceiling, 0.55);
  assert.equal(merged.clins[0].confidence_note, "doesn't foot");
  assert.equal(merged.confidence_source, "confirmed");
});

test("merge clears a note the correction resolved", () => {
  const merged = mergeConfidence(flagged(), RESCORED_CLEAN);
  assert.equal(merged.clins[0].confidence_note, null);
});

test("merge leaves a CLIN the response didn't mention untouched", () => {
  const merged = mergeConfidence(ext(), { field_confidence: {}, clin_confidence: [] });
  assert.equal(merged.clins[0].confidence, 0.93);
});

test("a warning the edits introduced is reported", () => {
  const fresh = newWarnings(ext(), mergeConfidence(ext(), RESCORED_BAD));
  assert.deepEqual(
    fresh.map((w) => w.field ?? w.clin),
    ["total_ceiling", "0001"],
  );
});

test("nothing is said when the correction fixed everything", () => {
  const before = flagged();
  assert.deepEqual(newWarnings(before, mergeConfidence(before, RESCORED_CLEAN)), []);
});

test("a caution the user already read and saved past is not re-announced", () => {
  const before = flagged();
  assert.deepEqual(newWarnings(before, mergeConfidence(before, RESCORED_BAD)), []);
});
