import { test } from "node:test";
import assert from "node:assert/strict";
import { DOC_TYPES, buildDraft, renderDraftText, vf } from "./drafts.js";

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
    },
    totals: { ceiling: 5_000_000, spent: 2_400_000, pct: 0.48, weekly: 80_000, labor_count: 3 },
    hero: { days: 60, clin: "CLIN 0002", status: "funding", limited_by: "funding" },
    clins: [
      { id: 1, code: "CLIN 0001", name: "Base Labor", is_labor: true, ceiling: 2_000_000, spent: 900_000, funded: 2_000_000, weekly: 30_000, remaining: 1_100_000, status: "ok", status_label: "On pace", runway_days: 200 },
      { id: 2, code: "CLIN 0002", name: "Option Labor", is_labor: true, ceiling: 3_000_000, spent: 1_500_000, funded: 1_800_000, weekly: 50_000, remaining: 300_000, status: "funding", status_label: "Funding due", runway_days: 42 },
    ],
    tripwires: [],
    underburn: [],
    funding: [
      { code: "CLIN 0002", name: "Option Labor", pct: 0.83, exhaust_week: 42, weeks_early: 10, runway_days: 42, funded: 1_800_000, budget: 3_000_000, funded_frac: 0.6, elapsed_frac: 0.58, mod_in_progress: false },
    ],
    sync: { rows: 120, latest_week: "2024-07-28" },
  };
}

test("vf falls back to [verify] on missing values", () => {
  assert.equal(vf(1234), 1234);
  assert.equal(vf("Acme"), "Acme");
  assert.equal(vf(null), "[verify]");
  assert.equal(vf(undefined), "[verify]");
  assert.equal(vf(""), "[verify]");
  assert.equal(vf(NaN), "[verify]");
});

test("DOC_TYPES lists the three documents", () => {
  assert.deepEqual(DOC_TYPES.map((d) => d.key).sort(), ["cdrl", "funding", "invoice"]);
});

test("funding memo fills deterministic fields from burn + focus CLIN", () => {
  const doc = buildDraft("funding", sampleBurn(), { focusClin: "CLIN 0002" });
  assert.equal(doc.docType, "funding");
  assert.match(doc.draftLabel, /DRAFT/);
  const metaText = doc.meta.map((m) => `${m.label}: ${m.value}`).join("\n");
  assert.match(metaText, /W519TC-24-C-0007/);            // PIID present
  assert.match(metaText, /Acme Federal LLC/);            // contractor present
  // Contracting officer is not in the burn payload -> [verify], never fabricated.
  assert.match(metaText, /\[verify\]/);
  // Exactly one prose section, seeded with heuristic text (AI-off path).
  const prose = doc.sections.filter((s) => s.kind === "prose");
  assert.equal(prose.length, 1);
  assert.ok(prose[0].text.length > 0);
  // A funding summary table names the at-risk CLIN and its funded amount.
  const text = renderDraftText(doc);
  assert.match(text, /CLIN 0002/);
  assert.match(text, /\$1\.80M/);                        // moneyM(funded)
});

test("funding memo defaults to the first funding item when no focusClin given", () => {
  const doc = buildDraft("funding", sampleBurn(), {});
  assert.match(renderDraftText(doc), /CLIN 0002/);
});

test("invoice bills per-CLIN incurred cost with no prose section", () => {
  const doc = buildDraft("invoice", sampleBurn(), {});
  assert.equal(doc.docType, "invoice");
  // Invoice is numbers + certification only — the model rewrites nothing here.
  assert.equal(doc.sections.filter((s) => s.kind === "prose").length, 0);
  const text = renderDraftText(doc);
  assert.match(text, /DRAFT/);
  assert.match(text, /W519TC-24-C-0007/);          // PIID
  assert.match(text, /CLIN 0001/);
  assert.match(text, /\$0\.90M/);                   // moneyM(clin[0].spent)
  // A certification line is present (fixed text, not model-authored).
  assert.match(text.toLowerCase(), /certif/);
});

test("invoice flags missing spend as [verify], never $0", () => {
  const burn = sampleBurn();
  delete burn.clins[0].spent;                        // simulate absent actuals
  const text = renderDraftText(buildDraft("invoice", burn, {}));
  assert.match(text, /\[verify\]/);
});

test("cdrl check-in summarises burn and carries one prose section", () => {
  const doc = buildDraft("cdrl", sampleBurn(), {});
  assert.equal(doc.docType, "cdrl");
  assert.equal(doc.sections.filter((s) => s.kind === "prose").length, 1);
  const text = renderDraftText(doc);
  assert.match(text, /DRAFT/);
  assert.match(text, /48%/);                     // pct(totals.pct) overall burned
  assert.match(text, /CLIN 0001/);
  assert.match(text, /CLIN 0002/);
  // Funding-due flag surfaced in the status section.
  assert.match(text.toLowerCase(), /funding/);
});
