import { test } from "node:test";
import assert from "node:assert/strict";
import { shortDate, stopPhrase } from "./format.js";

test("shortDate renders an ISO date without shifting it", () => {
  // The reason this is regex-parsed and not `new Date(s)`: that parses a bare ISO
  // date as UTC midnight and renders it in local time, so anywhere west of
  // Greenwich every date in the app lands one day early. Asserted explicitly
  // because the bug is invisible in a UTC test runner.
  assert.equal(shortDate("2026-03-14"), "14 Mar 26");
  assert.equal(shortDate("2026-01-01"), "01 Jan 26");
  assert.equal(shortDate("2026-12-31"), "31 Dec 26");
});

test("shortDate tolerates a full timestamp and junk", () => {
  assert.equal(shortDate("2026-03-14T00:00:00Z"), "14 Mar 26");
  assert.equal(shortDate(null), "—");
  assert.equal(shortDate("not a date"), "not a date");
});

test("stopPhrase names the funding mod when funding is the limit", () => {
  const s = stopPhrase("2026-03-14", "funding", false);
  assert.match(s, /14 Mar 26/);
  assert.match(s, /without a mod/);
});

test("stopPhrase names the ceiling when the ceiling is the limit", () => {
  const s = stopPhrase("2026-03-14", "ceiling", false);
  assert.match(s, /at ceiling/);
  assert.doesNotMatch(s, /mod/);
});

test("a past stop date reads as today, not as a deadline", () => {
  // The engine keeps the true past date so the money's actual run-out can be
  // cited, but the headline has to be that charging should stop now — a bare
  // "Charging stops ~16 Feb 26" on a date behind us reads as time remaining.
  const s = stopPhrase("2026-02-16", "funding", true);
  assert.match(s, /stops today/);
  assert.match(s, /16 Feb 26/);
});

test("no stop date renders nothing at all", () => {
  // Paused, unpriced and non-labor CLINs. The caller hides the row on null rather
  // than printing an em dash where a date would go.
  assert.equal(stopPhrase(null, null, false), null);
});
