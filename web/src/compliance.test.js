import { test } from "node:test";
import assert from "node:assert/strict";
import {
  badge,
  bySeverity,
  failureText,
  isFinding,
  rollupText,
  severity,
  uncheckedText,
  CLEARANCE_GAP,
  COMPLIANT,
  NO_FLOOR,
  OVER_QUALIFIED,
  UNDER_QUALIFIED,
  UNKNOWN,
  UNPRICED,
} from "./compliance.js";

// --- the badge -------------------------------------------------------------------

test("unknown gets a visible treatment of its own", () => {
  // The assertion this file exists for. Every synced person starts here, and a blank
  // badge reads as "fine" to anyone who has ever looked at a table.
  assert.equal(badge(UNKNOWN).label, "Unchecked");
  assert.equal(badge(UNKNOWN).tone, "slate");
  assert.notEqual(badge(UNKNOWN).tone, badge(COMPLIANT).tone);
  assert.notEqual(badge(UNKNOWN).label, badge(COMPLIANT).label);
});

test("a clearance gap is separated from an ordinary shortfall", () => {
  assert.equal(badge(CLEARANCE_GAP).tone, "red");
  assert.equal(badge(UNDER_QUALIFIED).tone, "amber");
});

test("no badge at all when the award printed no minimums", () => {
  // A badge here would imply somebody answered a question nobody asked.
  assert.equal(badge(NO_FLOOR), null);
});

test("unpriced hours get no compliance badge either", () => {
  // The cell already carries #64's ⚠ for this. A second badge in compliance language
  // would read as two problems where there is one.
  assert.equal(badge(UNPRICED), null);
  assert.equal(isFinding(UNPRICED), false);
});

test("over-qualified is not dressed up as a violation", () => {
  assert.notEqual(badge(OVER_QUALIFIED).tone, "red");
  assert.notEqual(badge(OVER_QUALIFIED).tone, "amber");
  assert.equal(isFinding(OVER_QUALIFIED), false);
});

test("only real shortfalls count as findings", () => {
  assert.equal(isFinding(CLEARANCE_GAP), true);
  assert.equal(isFinding(UNDER_QUALIFIED), true);
  assert.equal(isFinding(UNKNOWN), false);
  assert.equal(isFinding(COMPLIANT), false);
});

// --- failure copy ----------------------------------------------------------------

test("a failure names what they have and what the category wants", () => {
  assert.equal(
    failureText({ field: "years_experience", held: "3", required: 10 }, "Senior Cyber SME"),
    "3 yrs years of experience, Senior Cyber SME requires 10",
  );
});

test("a missing value says so rather than printing a blank", () => {
  assert.equal(
    failureText({ field: "clearance", held: null, required: "TS/SCI" }),
    "clearance nothing on file, requires TS/SCI",
  );
});

// --- unchecked copy --------------------------------------------------------------

test("the user is sent to type quals only when the quals are what's missing", () => {
  assert.match(uncheckedText({ field: "education", reason: "no_value" }), /add it/);
});

test("an unreadable floor blames the document, not the person", () => {
  // Telling somebody to enter more quals here would send them to fix something that
  // isn't broken.
  const text = uncheckedText({ field: "education", reason: "floor_not_comparable" });
  assert.match(text, /award/);
  assert.doesNotMatch(text, /add it/);
});

test("a pre-vocabulary value asks for a re-entry", () => {
  assert.match(
    uncheckedText({ field: "clearance", reason: "value_not_comparable" }),
    /re-enter/,
  );
});

// --- the rollup sentence ---------------------------------------------------------

const ROLL = {
  people: 40,
  checked: 11,
  not_checked: 29,
  no_floor: 0,
  compliant: 6,
  over_qualified: 0,
  under_qualified: 3,
  clearance_gap: 2,
  has_findings: true,
};

test("findings are reported over the checked subset, with the rest stated", () => {
  assert.equal(
    rollupText(ROLL),
    "3 of 11 checked people under-qualified · 2 clearance gaps · 29 not yet checked",
  );
});

test("a checked-subset count is never presented as covering the population", () => {
  // The one output this module must not produce: no percentage, and the sentence may
  // not say "of 40" for a number derived from 11.
  const text = rollupText(ROLL);
  assert.doesNotMatch(text, /%/);
  assert.doesNotMatch(text, /3 of 40/);
  assert.match(text, /29 not yet checked/);
});

test("an unchecked contract is not called clear", () => {
  const text = rollupText({
    people: 12,
    checked: 0,
    not_checked: 12,
    no_floor: 0,
    compliant: 0,
    over_qualified: 0,
    under_qualified: 0,
    clearance_gap: 0,
    has_findings: false,
  });
  assert.equal(text, "12 not yet checked");
  assert.doesNotMatch(text, /clear/);
});

test("all clear is said only when something was actually checked", () => {
  assert.equal(
    rollupText({
      people: 5,
      checked: 5,
      not_checked: 0,
      no_floor: 0,
      compliant: 5,
      over_qualified: 0,
      under_qualified: 0,
      clearance_gap: 0,
      has_findings: false,
    }),
    "5 checked, all clear",
  );
});

test("clear and not-yet-checked stay two facts in one sentence", () => {
  assert.equal(
    rollupText({
      people: 9,
      checked: 4,
      not_checked: 5,
      no_floor: 0,
      compliant: 4,
      over_qualified: 0,
      under_qualified: 0,
      clearance_gap: 0,
      has_findings: false,
    }),
    "4 checked, all clear · 5 not yet checked",
  );
});

test("the singular is used where it should be", () => {
  assert.equal(
    rollupText({
      people: 1,
      checked: 1,
      not_checked: 0,
      no_floor: 0,
      compliant: 0,
      over_qualified: 0,
      under_qualified: 1,
      clearance_gap: 1,
      has_findings: true,
    }),
    "1 of 1 checked person under-qualified · 1 clearance gap",
  );
});

test("unpriced hours are named as a pricing gap, not a printed-minimums gap", () => {
  // Rolling these into "no printed minimums" would report them as a document that
  // omitted a floor, when the truth is no category was resolved to have one.
  const text = rollupText({
    people: 8,
    checked: 0,
    not_checked: 0,
    no_floor: 2,
    unpriced: 6,
    compliant: 0,
    over_qualified: 0,
    under_qualified: 0,
    clearance_gap: 0,
    has_findings: false,
  });
  assert.match(text, /2 on lines with no printed minimums/);
  assert.match(text, /6 on hours with no priced category/);
});

test("a line nobody charges says so", () => {
  assert.match(rollupText({ people: 0 }), /Nobody is charging/);
  assert.match(rollupText(null), /Nobody is charging/);
});

// --- severity order -------------------------------------------------------------

test("stop-work sorts first and unchecked outranks clear", () => {
  // Unchecked beats compliant because it is the work left to do.
  assert.ok(severity(CLEARANCE_GAP) < severity(UNDER_QUALIFIED));
  assert.ok(severity(UNDER_QUALIFIED) < severity(UNKNOWN));
  assert.ok(severity(UNKNOWN) < severity(COMPLIANT));
  assert.ok(severity(COMPLIANT) < severity(NO_FLOOR));
});

test("rows sort worst first", () => {
  const rows = [
    { name: "clear", compliance_status: COMPLIANT },
    { name: "gap", compliance_status: CLEARANCE_GAP },
    { name: "unchecked", compliance_status: UNKNOWN },
  ];
  assert.deepEqual(
    [...rows].sort(bySeverity).map((r) => r.name),
    ["gap", "unchecked", "clear"],
  );
});
