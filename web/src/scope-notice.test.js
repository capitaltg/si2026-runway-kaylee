import { test } from "node:test";
import assert from "node:assert/strict";
import { scopeNotices } from "./scope-notice.js";

test("scope notices stay hidden when CLINs and charges are scoped to the active PoP", () => {
  assert.deepEqual(scopeNotices({ clin_scope: "period", pop_scoped: true }), []);
});

test("scope notices warn when the dashboard had to include every contract CLIN", () => {
  assert.deepEqual(scopeNotices({ clin_scope: "all", pop_scoped: true }), [
    {
      key: "clin_scope",
      text: "CLIN totals include all contract periods because the award did not label the active period.",
    },
  ]);
});

test("scope notices warn when no synced charge falls inside the active PoP", () => {
  assert.deepEqual(scopeNotices({ clin_scope: "period", pop_scoped: false }), [
    {
      key: "pop_scope",
      text: "Charges could not be limited to the active period of performance because no synced week overlaps it.",
    },
  ]);
});

test("scope notices retain both warnings when both scope fallbacks apply", () => {
  assert.deepEqual(scopeNotices({ clin_scope: "all", pop_scoped: false }), [
    {
      key: "clin_scope",
      text: "CLIN totals include all contract periods because the award did not label the active period.",
    },
    {
      key: "pop_scope",
      text: "Charges could not be limited to the active period of performance because no synced week overlaps it.",
    },
  ]);
});
