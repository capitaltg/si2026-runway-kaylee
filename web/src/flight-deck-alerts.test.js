import assert from "node:assert/strict";
import test from "node:test";

import * as alertHelpers from "./flight-deck-alerts.js";

const { nextAlertIndex, orderedFlightDeckAlerts } = alertHelpers;

test("orders alerts by severity and then risk", () => {
  const alerts = orderedFlightDeckAlerts({
    dataQuality: [{ code: "CLIN 0003", charged_rows: 1 }],
    tripwires: [{ code: "CLIN 0001", pct: 1.1 }, { code: "CLIN 0002", pct: 1.3 }],
    funding: [{ code: "CLIN 0004", runway_days: 9 }],
    underburn: [{ code: "CLIN 0005", projected_unspent: 10 }],
  });

  assert.deepEqual(alerts.map(({ kind, item }) => [kind, item.code]), [
    ["data-quality", "CLIN 0003"],
    ["tripwire", "CLIN 0002"],
    ["tripwire", "CLIN 0001"],
    ["funding", "CLIN 0004"],
    ["underburn", "CLIN 0005"],
  ]);
});

test("uses funded-budget burn to rank funding-limited tripwires", () => {
  const alerts = orderedFlightDeckAlerts({
    tripwires: [
      { code: "CLIN 0001", limited_by: "funding", pct: 2, pct_budget: 1.1 },
      { code: "CLIN 0002", limited_by: "funding", pct: 1.2, pct_budget: 1.3 },
      { code: "CLIN 0003", pct: 1.4 },
    ],
  });

  assert.deepEqual(alerts.map(({ item }) => item.code), ["CLIN 0003", "CLIN 0002", "CLIN 0001"]);
});

test("ranks realized tripwire breaches by dollars over the active constraint", () => {
  const alerts = orderedFlightDeckAlerts({
    tripwires: [
      {
        code: "CLIN 0001",
        limited_by: "funding",
        pct: 0.2,
        pct_budget: 2,
        funded: 100_000,
        budget: 100_000,
        runway_days: 0,
      },
      {
        code: "CLIN 0002",
        limited_by: "ceiling",
        pct: 1.05,
        ceiling: 10_000_000,
        budget: 10_000_000,
        runway_days: 0,
      },
    ],
  });

  assert.deepEqual(alerts.map(({ item }) => item.code), ["CLIN 0002", "CLIN 0001"]);
});

test("ranks forecast tripwires by shortest runway instead of percent burned", () => {
  const alerts = orderedFlightDeckAlerts({
    tripwires: [
      {
        code: "CLIN 0001",
        limited_by: "ceiling",
        pct: 0.98,
        ceiling: 1_000_000,
        budget: 1_000_000,
        runway_days: 60,
      },
      {
        code: "CLIN 0002",
        limited_by: "funding",
        pct: 0.3,
        pct_budget: 0.75,
        funded: 200_000,
        budget: 200_000,
        runway_days: 7,
      },
    ],
  });

  assert.deepEqual(alerts.map(({ item }) => item.code), ["CLIN 0002", "CLIN 0001"]);
});

test("includes every remaining alert bucket in severity and risk order", () => {
  const notices = [
    { key: "clin_scope", text: "CLIN scope" },
    { key: "charge_scope", text: "Charge scope" },
  ];
  const alerts = orderedFlightDeckAlerts({
    marginAlerts: [
      { code: "CLIN 0005", projected_margin: 20 },
      { code: "CLIN 0006", projected_margin: -10 },
    ],
    notices,
    rateGaps: [
      { code: "CLIN 0007", lcats: ["Engineer"] },
      { code: "CLIN 0008", lcats: ["Engineer", "Analyst"] },
    ],
    lcatGaps: [
      { code: "CLIN 0009", issues: [{}] },
      { code: "CLIN 0010", issues: [{}, {}] },
    ],
  });

  assert.deepEqual(alerts.map(({ kind, item }) => [kind, item.code]), [
    ["margin", "CLIN 0006"],
    ["margin", "CLIN 0005"],
    ["scope", undefined],
    ["rate-gap", "CLIN 0008"],
    ["rate-gap", "CLIN 0007"],
    ["lcat-gap", undefined],
  ]);
  assert.equal(alerts[2].item, notices);
  assert.deepEqual(alerts[5].item.map(({ code }) => code), ["CLIN 0009", "CLIN 0010"]);
});

test("emits an alert for every non-empty bucket and none for empty groups", () => {
  const alerts = orderedFlightDeckAlerts({
    dataQuality: [{ code: "CLIN 0001" }],
    tripwires: [{ code: "CLIN 0002" }],
    funding: [{ code: "CLIN 0003" }],
    underburn: [{ code: "CLIN 0004" }],
    marginAlerts: [{ code: "CLIN 0005" }],
    notices: [{ key: "clin_scope", text: "scope" }],
    rateGaps: [{ code: "CLIN 0006" }],
    lcatGaps: [{ code: "CLIN 0007", issues: [{}] }],
  });

  assert.deepEqual(alerts.map(({ kind }) => kind), [
    "data-quality",
    "tripwire",
    "funding",
    "underburn",
    "margin",
    "scope",
    "rate-gap",
    "lcat-gap",
  ]);
  assert.deepEqual(orderedFlightDeckAlerts({}), []);
});

test("wraps pager navigation", () => {
  assert.equal(nextAlertIndex(0, 2, -1), 1);
  assert.equal(nextAlertIndex(1, 2, 1), 0);
});

test("clamps the selected alert when the list shrinks or clears", () => {
  assert.equal(typeof alertHelpers.clampAlertIndex, "function");
  assert.equal(alertHelpers.clampAlertIndex(3, 2), 1);
  assert.equal(alertHelpers.clampAlertIndex(2, 0), 0);
});

test("baseline drift reads under the money-runs-out alerts and over underburn", () => {
  // A staffing gap often causes the tripwire above it, but it is the tripwire that
  // carries the date — so drift must not be able to push one down the carousel.
  const ordered = orderedFlightDeckAlerts({
    underburn: [{ code: "0002", projected_unspent: 90000 }],
    baselineDrift: [{ key: "baseline-drift", deltaCost: 5200 }],
    funding: [{ code: "0001", runway_days: 20 }],
  });
  assert.deepEqual(ordered.map((a) => a.kind), ["funding", "drift", "underburn"]);
});

test("drift ranks on the size of the gap, not its direction", () => {
  // Running well under the staffing you committed to is as much a departure as
  // running over it, and can be the more serious one — the work isn't happening.
  const ordered = orderedFlightDeckAlerts({
    baselineDrift: [
      { key: "small", deltaCost: 900 },
      { key: "big-under", deltaCost: -8000 },
    ],
  });
  assert.deepEqual(ordered.map((a) => a.item.key), ["big-under", "small"]);
});
