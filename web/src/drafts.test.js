import { test } from "node:test";
import assert from "node:assert/strict";
import { DOC_TYPES, buildDraft, renderDraftText, vf, stripMd, weekToDate } from "./drafts.js";

// A minimal burn payload shaped like getBurn(id) returns.
function sampleBurn() {
  return {
    contract: {
      piid: "W519TC-24-C-0007",
      name: "FALCON",
      legal_name: "Acme Federal LLC",
      agency: "Department of Defense",
      pop_start: "2024-01-01",
      pop_end: "2024-12-31",
      current_week: 30,
      total_weeks: 52,
      weeks_remaining: 22,
      contract_ceiling: 5_000_000,
      obligated: 3_000_000,
      contracting_officer: "Jane Smith",
    },
    totals: { ceiling: 5_000_000, spent: 2_400_000, pct: 0.48, weekly: 80_000, labor_count: 3 },
    hero: { days: 60, clin: "CLIN 0002", status: "funding", limited_by: "funding" },
    clins: [
      { id: 1, code: "CLIN 0001", name: "Base Labor", is_labor: true, ceiling: 2_000_000, spent: 900_000, funded: 2_000_000, weekly: 30_000, remaining: 1_100_000, pct: 0.45, status: "ok", status_label: "On pace", runway_days: 200 },
      { id: 2, code: "CLIN 0002", name: "Option Labor", is_labor: true, ceiling: 3_000_000, spent: 1_500_000, funded: 1_800_000, weekly: 50_000, remaining: 300_000, pct: 0.5, status: "funding", status_label: "Funding due", runway_days: 42 },
    ],
    tripwires: [],
    underburn: [],
    funding: [
      // budget == funded for an incrementally funded line, as real burn output does.
      { code: "CLIN 0002", name: "Option Labor", pct: 0.83, exhaust_week: 42, weeks_early: 10, runway_days: 42, funded: 1_800_000, budget: 1_800_000, funded_frac: 0.6, elapsed_frac: 0.58, mod_in_progress: false },
    ],
    sync: { rows: 120, latest_week: "2024-07-28" },
  };
}

test("vf falls back to [verify] on missing values", () => {
  assert.equal(vf(1234), 1234);
  assert.equal(vf("Acme"), "Acme");
  assert.equal(vf(null), "[verify]");
  assert.equal(vf(""), "[verify]");
  assert.equal(vf(NaN), "[verify]");
});

test("stripMd removes headings, bold, and bullet markers", () => {
  assert.equal(stripMd("# Heading"), "Heading");
  assert.equal(stripMd("**bold** text"), "bold text");
  assert.equal(stripMd("* bullet"), "bullet");
  assert.equal(stripMd("plain"), "plain");
});

test("DOC_TYPES lists the three documents", () => {
  assert.deepEqual(DOC_TYPES.map((d) => d.key).sort(), ["cdrl", "funding", "invoice"]);
});

test("weekToDate converts a PoP week into a real calendar date", () => {
  assert.equal(weekToDate("2024-01-01", 1), "January 1, 2024");
  assert.equal(weekToDate("2024-01-01", 2), "January 8, 2024");
  assert.equal(weekToDate(null, 5), null);
  assert.equal(weekToDate("2024-01-01", null), null);
});

test("funding letter tracks FAR 52.232-22, pulls the CO, and states an exhaustion date", () => {
  const doc = buildDraft("funding", sampleBurn(), { focusClin: "CLIN 0002", today: "2024-07-31" });
  assert.equal(doc.docType, "funding");
  const metaText = doc.meta.map((m) => `${m.label}: ${m.value}`).join("\n");
  assert.match(metaText, /W519TC-24-C-0007/);          // contract no.
  assert.match(metaText, /FAR 52\.232-22/);            // reference clause
  assert.match(metaText, /Jane Smith/);                // CO pulled, not [verify]
  const prose = doc.sections.filter((s) => s.kind === "prose");
  assert.equal(prose.length, 1);
  assert.match(prose[0].text, /75 percent/);           // required notice language
  assert.match(prose[0].text, /on or about \w+ \d+, \d{4}/); // real exhaustion date
  const text = renderDraftText(doc);
  assert.match(text, /CLIN 0002/);
  assert.match(text, /\$1\.80M/);                       // funds allotted to line
  assert.match(text, /Projected funds-exhaustion date/);
  // Requested increment = ceiling ($3.00M) - funded ($1.80M) = $1.20M, not $0.
  assert.match(text, /\$1\.20M/);
  assert.doesNotMatch(prose[0].text, /\$0\.00M/);
});

// ── The clause the letter is written under (#81) ───────────────────────────────
//
// `sampleBurn` above deliberately carries no `funding_clause` and no
// `pricing_policy`: it is the pre-#81 payload, and the test above pins that it still
// produces the -22 letter it always did. These start from a typed payload instead.
function typedBurn(clause, clin = {}, policy = {}, fundingRow = {}) {
  const burn = sampleBurn();
  burn.clins[1] = {
    ...burn.clins[1],
    funding_clause: clause,
    incrementally_funded: true,
    pricing_policy: { code: "CPFF", label: "Cost Plus Fixed Fee", known: true, ...policy },
    ...clin,
  };
  burn.funding[0] = { ...burn.funding[0], funding_clause: clause, ...fundingRow };
  return burn;
}

const fundingDoc = (burn) =>
  buildDraft("funding", burn, { focusClin: "CLIN 0002", today: "2024-07-31" });
const metaOf = (doc) => doc.meta.map((m) => `${m.label}: ${m.value}`).join("\n");
const proseOf = (doc) => doc.sections.find((s) => s.kind === "prose").text;

test("a fully funded cost line is notified under Limitation of Cost, not Funds", () => {
  const doc = fundingDoc(
    typedBurn("52.232-20", {
      incrementally_funded: false,
      funded: 3_000_000,
      fee_position: { projected: { overrun: 250_000 } },
    })
  );
  assert.match(metaOf(doc), /FAR 52\.232-20, Limitation of Cost/);
  assert.doesNotMatch(metaOf(doc), /52\.232-22/);
  const prose = proseOf(doc);
  // -20's threshold is 75% of the *estimated cost*, not of an allotment.
  assert.match(prose, /75 percent of the estimated cost/);
  assert.doesNotMatch(prose, /allotted/);
  // The remedy is an increase in the estimate, not an obligation.
  assert.match(prose, /estimated cost of the contract be increased by \$0\.25M/);
  assert.doesNotMatch(prose, /be obligated/);
  const text = renderDraftText(doc);
  assert.match(text, /Increase in estimated cost requested {2}\| {2}\$0\.25M/);
  // The $0 request that citing -22 on a fully funded line used to produce.
  assert.doesNotMatch(text, /\$0\.00M/);
});

test("a T&M line is notified against its ceiling price and asks for no dollar figure", () => {
  const doc = fundingDoc(
    typedBurn(
      "52.232-7",
      { incrementally_funded: false, funded: 3_000_000 },
      { code: "TM", label: "Time and Materials" },
      { funded: 3_000_000 }
    )
  );
  assert.match(metaOf(doc), /FAR 52\.232-7, Payments under Time-and-Materials/);
  const prose = proseOf(doc);
  // 52.232-7 runs at 85% of the ceiling price — a different threshold against a
  // different quantity than either cost-type clause.
  assert.match(prose, /85 percent of the ceiling price/);
  assert.match(prose, /increase in the ceiling price/);
  assert.doesNotMatch(prose, /be obligated/);
  // No dollar remedy is stated, so no request row and no invented figure.
  const text = renderDraftText(doc);
  assert.doesNotMatch(text, /Additional funds requested|Increase in estimated cost/);
  assert.match(text, /Ceiling price {2}\| {2}\$3\.00M/);
});

test("an incrementally funded T&M line says the allotment binds before the ceiling", () => {
  const doc = fundingDoc(typedBurn("52.232-7", {}, { code: "TM", label: "Time and Materials" }));
  const caveat = doc.sections.find((s) => s.id === "caveats");
  // T&M carries only 52.232-7, so the letter is about the ceiling — but the funded
  // slice is the smaller limit and stops the charging first. Saying so is the whole
  // point; a ceiling-increase request alone would leave the real gap unasked for.
  assert.match(caveat.text, /allotted to CLIN 0002 \(\$1\.80M\) are below that ceiling/);
  assert.match(caveat.text, /obligation of the remaining \$1\.20M/);
  // A caveat the AI pass can't overwrite: it only rewrites the prose section.
  assert.notEqual(caveat.kind, "prose");
});

test("fixed price gets no letter at all, and says why", () => {
  const doc = fundingDoc(
    typedBurn(null, {}, { code: "FFP", label: "Firm Fixed Price", known: true })
  );
  assert.match(doc.title, /Not Applicable/);
  assert.match(metaOf(doc), /Reference: None — no limitation-of-funds clause applies/);
  // No prose section, so the AI pass has nothing to stream a funding justification
  // into — the one document a fixed-price contract must not produce.
  assert.equal(doc.sections.filter((s) => s.kind === "prose").length, 0);
  const text = renderDraftText(doc);
  assert.match(text, /Firm Fixed Price work carries no Limitation of Funds/);
  assert.doesNotMatch(text, /52\.232/);
});

test("an untyped award's clause is labelled as the assumption it is", () => {
  const doc = fundingDoc(
    typedBurn("52.232-22", {}, { code: "unknown", label: "Unknown contract type", known: false })
  );
  // The citation itself is unchanged — #81 kept the legacy -22 assumption rather than
  // citing nothing — but it can no longer be read as something the award stated.
  assert.match(metaOf(doc), /FAR 52\.232-22, Limitation of Funds \(assumed/);
  const caveat = doc.sections.find((s) => s.id === "caveats");
  assert.match(caveat.text, /does not state one/);
  assert.match(caveat.text, /Confirm the clause against the executed contract/);
});

test("a typed cost line carries no assumption caveat", () => {
  const doc = fundingDoc(typedBurn("52.232-22"));
  assert.doesNotMatch(metaOf(doc), /assumed/);
  assert.equal(doc.sections.find((s) => s.id === "caveats"), undefined);
});

test("invoice follows SF-1034 structure with certification, no prose", () => {
  const doc = buildDraft("invoice", sampleBurn(), { today: "2024-07-31" });
  assert.equal(doc.docType, "invoice");
  assert.equal(doc.sections.filter((s) => s.kind === "prose").length, 0);
  const text = renderDraftText(doc);
  assert.match(text, /SF-1034|Public Voucher/);
  assert.match(text, /W519TC-24-C-0007/);
  assert.match(text, /CLIN 0001/);
  assert.match(text, /\$0\.90M/);                       // moneyM(clin[0].spent)
  assert.match(text, /COST REIMBURSABLE/);
  assert.match(text, /correct and proper for payment/); // SF-1034 certification
});

test("invoice flags missing spend as [verify], never $0", () => {
  const burn = sampleBurn();
  delete burn.clins[0].spent;
  assert.match(renderDraftText(buildDraft("invoice", burn, {})), /\[verify\]/);
});

test("cdrl follows the DI-MGMT-80368A section order with one prose section", () => {
  const doc = buildDraft("cdrl", sampleBurn(), { today: "2024-07-31" });
  assert.equal(doc.docType, "cdrl");
  assert.equal(doc.sections.filter((s) => s.kind === "prose").length, 1);
  const headings = doc.sections.map((s) => s.heading);
  assert.match(headings[0], /Executive summary/);
  assert.match(headings[1], /Contract & funding status/);
  const text = renderDraftText(doc);
  assert.match(text, /48%/);                            // pct(totals.pct)
  assert.match(text, /CLIN 0001/);
  assert.match(text, /government actions requested/i);
});
