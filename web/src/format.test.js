import { test } from "node:test";
import assert from "node:assert/strict";
import { shortDate, stopPhrase, asOfLabel } from "./format.js";

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

// ---- asOfLabel: the vantage point a runway figure is measured from ----------
//
// Every forward figure the engine reports is anchored to the newest synced timesheet
// week, not to today, because pace can only be measured from reported hours. That
// makes them as-of readings rather than live countdowns, and live contract 5 showed
// "99 days of runway" measured from a week four months gone — which is why the count
// looked frozen. The label is what stops the number reading as a live one.

test("asOfLabel names the week the figures are measured from", () => {
  assert.equal(asOfLabel({ as_of: "2026-04-10", data_age_days: 6 }), "as of 10 Apr 26");
});

test("asOfLabel stays quiet about a normal timesheet lag", () => {
  // Weekly timekeeping means a healthy contract is always a few days behind. Adding
  // "· 6 days ago" to every card trains people to ignore the one that says 119.
  assert.equal(asOfLabel({ as_of: "2026-04-10", data_age_days: 13 }), "as of 10 Apr 26");
});

test("asOfLabel says how far behind a stale sync is", () => {
  // Past any normal lag the staleness is the more useful fact — this is the contract
  // 5 case, where the difference is "you have 99 days" vs "you had 99 days, in April".
  assert.equal(
    asOfLabel({ as_of: "2026-04-10", data_age_days: 119 }),
    "as of 10 Apr 26 · 119 days ago",
  );
  // The boundary itself counts as stale.
  assert.equal(
    asOfLabel({ as_of: "2026-04-10", data_age_days: 14 }),
    "as of 10 Apr 26 · 14 days ago",
  );
});

test("asOfLabel renders nothing without an anchor date", () => {
  // A payload older than this bundle has no `as_of`. The callers hide the line on
  // null rather than printing "as of —", which would read as a failed lookup.
  assert.equal(asOfLabel({ rows: 0 }), null);
  assert.equal(asOfLabel(null), null);
  assert.equal(asOfLabel(undefined), null);
});

test("asOfLabel still labels a sync whose age is unknown", () => {
  // `data_age_days` absent but `as_of` present: name the date, claim nothing about
  // how old it is.
  assert.equal(asOfLabel({ as_of: "2026-04-10" }), "as of 10 Apr 26");
});

// ── #81 part 4: the fee-erosion pill ────────────────────────────────────────────

test("fee_eroding is amber, and says so with the fee that is left", async () => {
  const { pill, statusColor } = await import("./format.js");

  // Amber, beside the funding states — not a red. Every red on a cost-type CLIN names
  // a funding limit, and this one names the fee: the funded dollars are not at risk,
  // the company's profit is. Green was the bug: before #81 the backend had no state
  // for this, so a CPFF CLIN eating its fee read "On pace".
  assert.equal(statusColor("fee_eroding"), "var(--warn)");
  assert.equal(pill("fee_eroding").label, "Fee eroding");
  assert.equal(pill("fee_eroding").color, "var(--warn)");

  // Sharpened when there is no fee left to erode. Mirrors burn.py's `_pill`, and reads
  // the card's `fee_exhausted` rather than string-matching the label.
  assert.equal(pill("fee_eroding", true, false, false, true).label, "Fee exhausted");
  assert.equal(pill("fee_eroding", true, false, false, true).color, "var(--warn)");

  // The over/funds labels are unaffected by the new argument.
  assert.equal(pill("over", true, false, false, true).label, "Over ceiling");
  assert.equal(pill("over", false, false, false, true).label, "Funds short");
});

// ── #81 part 5: the T&M ceiling price is its own limit ──────────────────────────

test("a ceiling-price limit is named as a price, not as a ceiling", async () => {
  const { pill, stopPhrase } = await import("./format.js");

  // T&M's ceiling is FAR 16.601(c)(1)'s negotiated not-to-exceed, and the remedy for
  // breaching it is a ceiling increase under 52.232-7. A cost-type ceiling is estimated
  // cost plus fee, raised by a mod. The two tripwires looked identical before #81.
  assert.equal(pill("over", true, false, false, false, true).label, "Over ceiling price");
  assert.equal(pill("over", true, false, false, false, false).label, "Over ceiling");

  // `stop_reason` mirrors `limited_by`, so the hard-stop phrase names the same limit
  // the banner does. Unknown reasons still fall through to the ceiling wording.
  assert.equal(
    stopPhrase("2026-09-14", "ceiling_price", false),
    "Charging stops ~14 Sep 26 at the ceiling price",
  );
  assert.equal(
    stopPhrase("2026-09-14", "ceiling", false),
    "Charging stops ~14 Sep 26 at ceiling",
  );
  assert.equal(
    stopPhrase("2026-09-14", "funding", false),
    "Charging stops ~14 Sep 26 without a mod",
  );

  // A funds-exhaustion label is unaffected by the ceiling-price flag: the funded slice
  // running dry is the same event whatever kind of ceiling sits above it.
  assert.equal(pill("over", false, false, false, false, true).label, "Funds short");
  assert.equal(pill("over", true, true, false, false, true).label, "Funds exceeded");
});
