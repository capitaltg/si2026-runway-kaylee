import test from "node:test";
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { classifyContractType, offersCostFeeFields } from "./pricing.js";

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
