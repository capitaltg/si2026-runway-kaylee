import { test } from "node:test";
import assert from "node:assert/strict";
import {
  fileSize,
  kindLabel,
  shortHash,
  sourceDocuments,
} from "./contract-source.js";

const award = {
  id: 1,
  kind: "award",
  filename: "award.sf26.pdf",
  size_bytes: 240_000,
  sha256: "a".repeat(64),
  created_at: "2026-08-01 10:00:00",
};
const schedule = {
  id: 2,
  kind: "rate_schedule",
  filename: "rates.pdf",
  size_bytes: 90_000,
  sha256: "b".repeat(64),
  created_at: "2026-08-02 09:00:00",
};

test("a contract with no stored source reports the empty state", () => {
  const state = sourceDocuments([]);
  assert.equal(state.empty, true);
  assert.equal(state.award, null);
  assert.equal(state.rateSchedule, null);
  assert.deepEqual(state.items, []);
});

test("an award on its own is not the empty state and offers no rate schedule", () => {
  const state = sourceDocuments([award]);
  assert.equal(state.empty, false);
  assert.equal(state.award, award);
  assert.equal(state.rateSchedule, null);
  assert.deepEqual(state.items, [award]);
});

test("an award plus a rate schedule offers both, award first", () => {
  const state = sourceDocuments([schedule, award]);
  assert.equal(state.empty, false);
  assert.equal(state.award, award);
  assert.equal(state.rateSchedule, schedule);
  assert.deepEqual(
    state.items.map((d) => d.kind),
    ["award", "rate_schedule"],
  );
});

test("a re-uploaded award supersedes the older one rather than listing both", () => {
  const corrected = { ...award, id: 3, created_at: "2026-08-05 12:00:00" };
  const state = sourceDocuments([award, corrected]);
  assert.equal(state.award.id, 3);
  assert.equal(state.items.length, 1);
});

test("a rate schedule with no award still shows, because it evidences the rates", () => {
  const state = sourceDocuments([schedule]);
  assert.equal(state.empty, false);
  assert.equal(state.award, null);
  assert.deepEqual(state.items, [schedule]);
});

test("sizes read the way a person would say them", () => {
  assert.equal(fileSize(512), "512 B");
  assert.equal(fileSize(90_000), "88 KB");
  assert.equal(fileSize(2_500_000), "2.4 MB");
  assert.equal(fileSize(0), "");
  assert.equal(fileSize(null), "");
});

test("the hash is shortened to something comparable by eye", () => {
  assert.equal(shortHash("a".repeat(64)), "aaaaaaaaaaaa");
  assert.equal(shortHash(null), "");
});

test("each document kind names itself, and an unknown kind still names something", () => {
  assert.equal(kindLabel("award"), "Award document");
  assert.equal(kindLabel("rate_schedule"), "Rate schedule");
  assert.equal(kindLabel("cost_buildup"), "Source document");
});
