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

test("scope notices warn when per-CLIN funding exists but no total scopes it (#61)", () => {
  assert.deepEqual(
    scopeNotices({
      clin_scope: "period",
      pop_scoped: true,
      funding_total_unknown: true,
    }),
    [
      {
        key: "funding_total",
        text: "Funded-dollar limits could not be set for this period: some CLINs state their own obligation but the documents print no contract obligated total to scope them against. Runway is reading against ceilings.",
      },
    ],
  );
});

test("scope notices stay quiet when the funding total is known", () => {
  assert.deepEqual(
    scopeNotices({
      clin_scope: "period",
      pop_scoped: true,
      funding_total_unknown: false,
    }),
    [],
  );
});

test("scope notices name the missing SF-30 for option performance", () => {
  assert.deepEqual(
    scopeNotices({
      clin_scope: "period",
      pop_scoped: true,
      missing_option_mods: [{ period: "Option 1", clins: ["1001"] }],
    }),
    [
      {
        key: "missing_option_mod:Option 1",
        text: "Option 1 performance detected on timesheets, but the Option 1 SF-30 funding modification has not been ingested.",
      },
    ],
  );
});

test("scope notices emit one stable warning per missing option modification", () => {
  assert.deepEqual(
    scopeNotices({
      missing_option_mods: [{ period: "Option 1" }, { period: "Option 2" }],
    }).map(({ key }) => key),
    ["missing_option_mod:Option 1", "missing_option_mod:Option 2"],
  );
});

test("scope notices name the CLIN whose pricing type the header had to rescue", () => {
  assert.deepEqual(
    scopeNotices({
      clin_scope: "period",
      pop_scoped: true,
      pricing_rejected: [
        {
          clin: "CLIN 0002",
          rejected: "see attachment 2",
          policy_label: "Time and Materials",
          source: "header",
        },
      ],
    }),
    [
      {
        key: "pricing_rejected:CLIN 0002",
        text: 'CLIN 0002 prints a pricing type Runway cannot read ("see attachment 2"), so its figures use the award header\'s Time and Materials policy instead.',
      },
    ],
  );
});

test("scope notices stay quiet on a contract with no rejected CLIN types", () => {
  // Both the healthy case and a malformed entry: a report with no text to show is
  // not a notice, and printing an empty quotation would be worse than silence.
  assert.deepEqual(
    scopeNotices({ clin_scope: "period", pop_scoped: true, pricing_rejected: [] }),
    [],
  );
  assert.deepEqual(
    scopeNotices({
      clin_scope: "period",
      pop_scoped: true,
      pricing_rejected: [{ clin: "CLIN 0001", rejected: "" }],
    }),
    [],
  );
});
