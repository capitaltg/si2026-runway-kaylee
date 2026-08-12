import test from "node:test";
import assert from "node:assert";
import { readFileSync } from "node:fs";
import {
  classifyContractType,
  effectiveContractType,
  offersCostFeeFields,
} from "./pricing.js";

test("abbreviations classify", () => {
  for (const [text, code] of [
    ["FFP", "FFP"],
    ["T&M", "TM"],
    ["CPFF", "CPFF"],
    ["CPIF", "CPIF"],
    ["CPAF", "CPAF"],
    ["FPI", "FPI"],
    ["FPIF", "FPI"],
  ]) {
    assert.equal(classifyContractType(text).code, code, text);
  }
});

test("full names classify — the spellings the prefix regex missed", () => {
  for (const [text, code] of [
    ["Firm Fixed Price", "FFP"],
    ["Time and Materials", "TM"],
    ["Cost Plus Fixed Fee", "CPFF"],
    ["Cost Plus Award Fee", "CPAF"],
    ["Cost Plus Incentive Fee", "CPIF"],
    ["Fixed Price Incentive", "FPI"],
    ["Fixed Price Incentive Firm Target", "FPI"],
  ]) {
    assert.equal(classifyContractType(text).code, code, text);
  }
});

test("case, punctuation and a trailing qualifier do not change the answer", () => {
  assert.equal(classifyContractType("cost-plus-fixed-fee").code, "CPFF");
  assert.equal(classifyContractType("CPFF (Completion Form)").code, "CPFF");
  assert.equal(classifyContractType("  Fixed-Price Incentive  ").code, "FPI");
});

test("unknowns are told apart", () => {
  assert.deepEqual(classifyContractType(""), { code: null, unknown: "absent" });
  assert.deepEqual(classifyContractType(null), { code: null, unknown: "absent" });
  assert.deepEqual(classifyContractType("???"), { code: null, unknown: "unsupported" });
  assert.deepEqual(classifyContractType("IDIQ"), { code: null, unknown: "vehicle" });
  assert.deepEqual(classifyContractType("FFP/T&M"), { code: null, unknown: "unsupported" });
});

test("ambiguous cost reimbursement is refused instead of guessed as CPFF", () => {
  for (const text of ["CR", "Cost Reimbursement", "Cost-Reimbursement, No-Fee", "COST"]) {
    assert.deepEqual(classifyContractType(text), { code: null, unknown: "unsupported" });
  }
});

test("full-name FPI reveals the cost/fee fields, which is the bug", () => {
  // #163: `/^(cp|cr|cost|fpi)/i` matched the abbreviation and missed the spelling, so
  // target cost, target profit, price ceiling and share ratio stayed hidden on a line
  // the server classifies as FPI.
  assert.equal(offersCostFeeFields("Fixed Price Incentive"), true);
  assert.equal(offersCostFeeFields("Fixed Price Incentive Firm Target"), true);
  assert.equal(offersCostFeeFields("FPI"), true);
});

test("every cost and incentive spelling offers the fields", () => {
  for (const text of [
    "CPFF",
    "Cost Plus Fixed Fee",
    "CPAF",
    "Cost Plus Award Fee",
    "CPIF",
    "Cost Plus Incentive Fee",
    "FPIF",
  ]) {
    assert.equal(offersCostFeeFields(text), true, text);
  }
});

test("fixed-price and T&M lines are not offered ten empty money inputs", () => {
  for (const text of ["FFP", "Firm Fixed Price", "Fixed Price", "T&M", "Labor Hour", ""]) {
    assert.equal(offersCostFeeFields(text), false, text);
  }
});

test("a vehicle prices nothing", () => {
  assert.equal(offersCostFeeFields("IDIQ"), false);
  assert.equal(offersCostFeeFields("BPA"), false);
});

test("unsupported text does not offer cost or fee fields", () => {
  assert.equal(offersCostFeeFields("CPFF/T&M"), false);
  assert.equal(offersCostFeeFields("Cost-plus per Section H"), false);
  assert.equal(offersCostFeeFields("Section B pricing"), false);
});

// The reason this file can mirror a Python table at all: it checks the copy.
test("the synonym table matches server/app/pricing.py", () => {
  const src = readFileSync(new URL("../../server/app/pricing.py", import.meta.url), "utf8");
  const block = /_SYNONYMS = \{(.*?)\n\}/s.exec(src);
  assert.ok(block, "could not find _SYNONYMS in pricing.py");
  const entries = [...block[1].matchAll(/^ {4}"([A-Z]+)": \(([\s\S]*?)^ {4}\),/gm)];
  assert.ok(entries.length >= 6, "parsed too few policy codes");
  for (const [, code, body] of entries) {
    const spellings = [...body.matchAll(/"([^"]*)"/g)].map((m) => m[1]);
    for (const spelling of spellings) {
      assert.equal(
        classifyContractType(spelling).code,
        code,
        `pricing.py maps "${spelling}" to ${code}; the client does not`,
      );
    }
  }
  const vehicles = /_VEHICLES = \((.*?)\n\)/s.exec(src);
  assert.ok(vehicles, "could not find _VEHICLES in pricing.py");
  for (const m of vehicles[1].matchAll(/"([^"]*)"/g)) {
    assert.equal(classifyContractType(m[1]).unknown, "vehicle", `vehicle "${m[1]}"`);
  }
});

// #183: the editor used to read only `CLIN.type`, so a CPFF award whose CLIN left the
// type blank hid every cost and fee input — on a line the server then resolved to the
// header's fee-bearing policy the moment it was saved.
test("a blank CLIN type inherits the header policy, and offers its fields", () => {
  for (const header of [
    "CPFF",
    "Cost Plus Fixed Fee",
    "CPIF",
    "Cost Plus Incentive Fee",
    "CPAF",
    "Cost Plus Award Fee",
    "FPI",
    "Fixed Price Incentive Firm Target",
  ]) {
    assert.equal(effectiveContractType("", header).source, "header", header);
    assert.equal(offersCostFeeFields("", header), true, header);
    assert.equal(offersCostFeeFields(null, header), true, header);
  }
});

test("an explicit CLIN type beats the header, both directions", () => {
  // The mixed award: an FFP deliverable line on a cost-type award prices one price.
  assert.equal(offersCostFeeFields("FFP", "CPFF"), false);
  assert.equal(offersCostFeeFields("T&M", "CPAF"), false);
  // And the reverse — a cost CLIN on a fixed-price award still prices cost and fee.
  assert.equal(offersCostFeeFields("CPFF", "FFP"), true);
  assert.equal(effectiveContractType("CPFF", "FFP").code, "CPFF");
  assert.equal(effectiveContractType("CPFF", "FFP").source, "clin");
});

test("an inherited fixed-price or T&M policy offers nothing it does not have", () => {
  for (const header of ["FFP", "Firm Fixed Price", "T&M", "Time and Materials", "", null]) {
    assert.equal(offersCostFeeFields("", header), false, String(header));
  }
});

test("a vehicle header inherits nothing, and neither does an unreadable one", () => {
  assert.equal(offersCostFeeFields("", "IDIQ"), false);
  assert.deepEqual(effectiveContractType("", "IDIQ"), {
    code: null, source: null, unknown: "vehicle", rejected: "IDIQ",
  });
  assert.equal(offersCostFeeFields("", "CPFF/T&M"), false);
});

// The fallback `policy_for` performs and the reason it carries the rejected text out:
// the header read succeeds, so the fields appear, but the CLIN's own text was refused.
test("an unreadable CLIN type falls through to the header and keeps what was rejected", () => {
  const p = effectiveContractType("see attachment 2", "CPFF");
  assert.deepEqual(p, {
    code: "CPFF", source: "header", unknown: null, rejected: "see attachment 2",
  });
  assert.equal(offersCostFeeFields("see attachment 2", "CPFF"), true);
});

test("with neither field readable, the CLIN's reason wins over the header's", () => {
  // A CLIN naming a vehicle is a vehicle problem; the blank header adds nothing.
  assert.equal(effectiveContractType("BPA", "").unknown, "vehicle");
  assert.equal(effectiveContractType("???", "IDIQ").unknown, "unsupported");
  assert.equal(effectiveContractType("", "").unknown, "absent");
  assert.equal(effectiveContractType(null, null).rejected, null);
});

// Parity is the point: the client resolves the effective type the way `policy_for`
// documents it, so this checks the precedence the docstring states is still stated.
test("policy_for still resolves CLIN before header", () => {
  const src = readFileSync(new URL("../../server/app/pricing.py", import.meta.url), "utf8");
  const body = /def policy_for\((.*?)\n(?=def |# =)/s.exec(src);
  assert.ok(body, "could not find policy_for in pricing.py");
  assert.match(
    body[1],
    /candidates = \(\("clin", clin\.get\("type"\)\), \("header", header\.get\("contract_type"\)\)\)/,
    "policy_for's resolution order changed; web/src/pricing.js mirrors it",
  );
});
