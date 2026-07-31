# Runway Drafts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate three editable GovCon documents (incremental-funding memo, SF-1034-style invoice, monthly CDRL status/check-in) from a contract's live burn data, with a deterministic non-AI path and an AI-on path that only rewrites prose.

**Architecture:** A pure client module (`web/src/drafts.js`) fills every number/date/ID from the burn payload the view already loads and inlines heuristic prose (the AI-off path). A thin server endpoint (`POST /api/draft`) reuses `ask.build_grounding()` to stream *prose only* — never dollar figures. A new Drafts view renders the document into an editable, printable page; the Flight Deck's funding suggestion deep-links into it.

**Tech Stack:** React 18 + Vite (no UI library — inline styles + CSS vars), FastAPI + a provider-configured Anthropic/Bedrock `client`, `node --test` for web unit tests, `pytest` for server unit tests.

## Global Constraints

- **No new frontend dependencies.** React + inline-style objects + CSS variables only (`var(--panel)`, `var(--border)`, `var(--text)`, `var(--accent)`, `var(--dim)`, `var(--faint)`, `var(--good)`, `var(--warn)`, `var(--bad)`). The app has no component library.
- **The model never authors numbers.** All dollar figures, dates, PIID, CLIN codes are filled client-side from the burn payload. `/api/draft` returns narrative prose only.
- **DRAFT labeling + `[verify]`.** Every document carries a "DRAFT — verify before submission" label. Any field not derivable from available data renders the literal string `[verify]`, never a guessed or zero value.
- **Streaming pattern:** plain-text stream, `StreamingResponse(gen(), media_type="text/plain; charset=utf-8")`, consumed on the client via `r.body.getReader()` + `TextDecoder` (mirror `askRunway` in `web/src/api.js`).
- **API base URL:** `http://localhost:8001` (already `BASE` in `web/src/api.js`).
- **AI toggle:** read `localStorage["runway.ai"] === "on"` (already surfaced as the `aiEnabled` prop from `App.jsx`).
- **Python formatting:** run `cd server && python3 -m black .` before any Python commit.
- **Branch:** all work on `feat/rw-drafts` (already checked out, off `main`). Do not stage `sample-data/fixtura-runway-funding-pace-demo.award.sf26.pdf`, `HANDOFF-runway-suggests.md`, or `server/runway.db.bak-2026-07-29`.

### The document object (produced by `buildDraft`, consumed by the Drafts view)

```js
// buildDraft(docType, burn, opts = {}) -> Doc
// docType: "funding" | "invoice" | "cdrl"
// burn: the payload from getBurn(contractId) — has .contract, .clins, .totals,
//       .hero, .tripwires, .underburn, .funding, .sync
// opts: { focusClin?: string }  // CLIN code to feature (funding deep-link)
//
// Doc shape:
// {
//   docType: string,
//   title: string,
//   draftLabel: "DRAFT — verify before submission",
//   meta: [ { label: string, value: string } ],      // header key/value rows
//   sections: [
//     { id, heading: string|null, kind: "prose", text: string }   // <= ONE per doc; AI-rewritable
//     { id, heading: string|null, kind: "text",  text: string }   // fixed narrative
//     { id, heading: string|null, kind: "table", columns: string[], rows: string[][] }
//   ],
// }
```

Rule enforced across all tasks: **at most one `kind: "prose"` section per document** (the only thing `/api/draft` rewrites). The invoice has zero prose sections.

---

### Task 1: `drafts.js` foundation + funding memo + `renderDraftText`

**Files:**
- Create: `web/src/drafts.js`
- Test: `web/src/drafts.test.js`
- Modify: `web/package.json` (add a `test` script)

**Interfaces:**
- Consumes: `money`, `moneyM`, `pct` from `web/src/format.js` (already exist: `money(n)`→`"$1,234"`, `moneyM(n)`→`"$1.20M"`, `pct(frac)`→`"42%"`).
- Produces:
  - `export const DOC_TYPES = [{ key, label, blurb }]` — the three doc types for the view's selector.
  - `export function buildDraft(docType, burn, opts = {})` → `Doc` (shape above).
  - `export function renderDraftText(doc)` → plain-text string (for the initial Copy/print content and fallback).
  - `export function vf(value)` — returns `value` when it is a finite number/non-empty string, else the literal `"[verify]"`. (Used by all three doc builders.)

- [ ] **Step 1: Add the web test script**

In `web/package.json`, add to `"scripts"` (keep existing keys):

```json
"test": "node --test"
```

- [ ] **Step 2: Write the failing test for `vf`, funding memo fields, and `renderDraftText`**

Create `web/src/drafts.test.js`:

```js
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
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd web && node --test src/drafts.test.js`
Expected: FAIL — `Cannot find module './drafts.js'` (or export-not-found).

- [ ] **Step 4: Implement `web/src/drafts.js` foundation + funding memo**

```js
import { money, moneyM, pct } from "./format.js";

// Runway Drafts (v1). Turn a contract's live burn payload into GovCon paperwork.
// Every number/date/ID here comes straight from `burn` — the model never authors
// figures. Prose sections carry deterministic "heuristic" copy so the whole
// document is usable with AI off; when AI is on the Drafts view replaces the
// single prose section's text with a streamed, phrased version.

export const DOC_TYPES = [
  { key: "funding", label: "Funding request", blurb: "Incremental-funding memo to the CO" },
  { key: "invoice", label: "Invoice (SF-1034)", blurb: "Public voucher for costs incurred" },
  { key: "cdrl", label: "Status check-in", blurb: "Monthly CDRL progress report" },
];

const DRAFT_LABEL = "DRAFT — verify before submission";

// Return the value when it's real, else the literal [verify] placeholder so a
// missing figure is visibly flagged instead of shown as $0 or a guess.
export function vf(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : "[verify]";
  if (typeof value === "string") return value.trim() ? value : "[verify]";
  return "[verify]";
}

// moneyM but honours [verify] for absent inputs.
function moneyV(n) {
  return typeof n === "number" && Number.isFinite(n) ? moneyM(n) : "[verify]";
}

const pop = (c) => `${vf(c.pop_start)} to ${vf(c.pop_end)}`;
const contractorOf = (c) => vf(c.legal_name || c.name);

// Pick the CLIN a funding memo is about: the deep-linked one, else the first
// funding-status line, else the worst labor line.
function fundingFocus(burn, opts) {
  const funding = burn.funding || [];
  if (opts.focusClin) {
    const hit = funding.find((f) => f.code === opts.focusClin);
    if (hit) return hit;
  }
  return funding[0] || null;
}

function buildFunding(burn, opts) {
  const c = burn.contract || {};
  const item = fundingFocus(burn, opts) || {};
  const code = vf(item.code);
  // Requested increment = the still-unfunded slice of the line's ceiling. A
  // concrete number from data, not a projection.
  const increment =
    typeof item.budget === "number" && typeof item.funded === "number"
      ? item.budget - item.funded
      : null;

  const heuristic =
    `This letter requests incremental funding for ${code} under contract ` +
    `${vf(c.piid)}. At the current burn rate the line spends through its funded ` +
    `${moneyV(item.funded)} in week ${item.exhaust_week != null ? Math.round(item.exhaust_week) : "[verify]"}, ` +
    `roughly ${item.weeks_early != null ? item.weeks_early : "[verify]"} weeks before the period of ` +
    `performance ends. To keep the effort funded through completion we request an ` +
    `additional ${moneyV(increment)} be obligated to this line.`;

  return {
    docType: "funding",
    title: "Incremental Funding Request",
    draftLabel: DRAFT_LABEL,
    meta: [
      { label: "To", value: "[verify] (Contracting Officer)" },
      { label: "From", value: contractorOf(c) },
      { label: "Contract (PIID)", value: vf(c.piid) },
      { label: "Agency", value: vf(c.agency) },
      { label: "Period of performance", value: pop(c) },
      { label: "Currently obligated", value: moneyV(c.obligated) },
      { label: "Total ceiling", value: moneyV(c.contract_ceiling) },
    ],
    sections: [
      { id: "justification", heading: "Justification", kind: "prose", text: heuristic },
      {
        id: "summary",
        heading: "Funding summary",
        kind: "table",
        columns: ["Line", "Funded", "Ceiling", "Runs out", "Requested increment"],
        rows: [[
          code,
          moneyV(item.funded),
          moneyV(item.budget),
          item.exhaust_week != null ? `week ${Math.round(item.exhaust_week)}` : "[verify]",
          moneyV(increment),
        ]],
      },
    ],
  };
}

const BUILDERS = { funding: buildFunding };

export function buildDraft(docType, burn, opts = {}) {
  const build = BUILDERS[docType];
  if (!build) throw new Error(`Unknown draft type: ${docType}`);
  return build(burn || {}, opts || {});
}

// Flatten a Doc to plain text for the initial Copy content and print fallback.
export function renderDraftText(doc) {
  const lines = [doc.title.toUpperCase(), doc.draftLabel, ""];
  for (const m of doc.meta) lines.push(`${m.label}: ${m.value}`);
  for (const s of doc.sections) {
    lines.push("");
    if (s.heading) lines.push(s.heading.toUpperCase());
    if (s.kind === "table") {
      lines.push(s.columns.join("  |  "));
      for (const r of s.rows) lines.push(r.join("  |  "));
    } else {
      lines.push(s.text);
    }
  }
  return lines.join("\n");
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd web && node --test src/drafts.test.js`
Expected: PASS — all tests in the file pass.

- [ ] **Step 6: Commit**

```bash
cd ~/code/si2026-runway
git add web/src/drafts.js web/src/drafts.test.js web/package.json
git commit -m "feat(drafts): drafts.js foundation + funding memo builder"
```

---

### Task 2: SF-1034-style invoice builder

**Files:**
- Modify: `web/src/drafts.js`
- Modify: `web/src/drafts.test.js`

**Interfaces:**
- Consumes: `buildDraft`, `renderDraftText`, `vf` (Task 1); `moneyM` via `drafts.js`.
- Produces: `buildDraft("invoice", burn, opts)` returns a Doc with **zero prose sections** — a labor-cost table (per-CLIN spend), a totals row, and a fixed certification text section; missing figures render `[verify]`.

- [ ] **Step 1: Write the failing invoice test**

Append to `web/src/drafts.test.js` (reuse the `sampleBurn` helper already defined at top of file):

```js
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && node --test src/drafts.test.js`
Expected: FAIL — `Unknown draft type: invoice`.

- [ ] **Step 3: Implement the invoice builder in `web/src/drafts.js`**

Add this function above the `BUILDERS` map:

```js
function buildInvoice(burn, _opts) {
  const c = burn.contract || {};
  const clins = burn.clins || [];
  const rows = clins.map((x) => [
    vf(x.code),
    vf(x.name),
    moneyV(x.spent),                 // cost incurred to date (labor + expenses)
    moneyV(x.ceiling),
    moneyV(x.remaining),
  ]);
  const totalSpent = clins.every((x) => typeof x.spent === "number")
    ? clins.reduce((s, x) => s + x.spent, 0)
    : null;

  return {
    docType: "invoice",
    title: "Public Voucher for Purchases and Services (SF-1034, draft)",
    draftLabel: DRAFT_LABEL,
    meta: [
      { label: "Voucher for", value: vf(c.agency) },
      { label: "Contractor", value: contractorOf(c) },
      { label: "Contract (PIID)", value: vf(c.piid) },
      { label: "Period of performance", value: pop(c) },
      { label: "Billing period", value: `through ${vf(burn.sync && burn.sync.latest_week)}` },
      { label: "Total amount claimed", value: moneyV(totalSpent) },
    ],
    sections: [
      {
        id: "lines",
        heading: "Cost incurred by CLIN",
        kind: "table",
        columns: ["CLIN", "Description", "Amount claimed", "Ceiling", "Remaining"],
        rows,
      },
      {
        id: "certification",
        heading: "Certification",
        kind: "text",
        text:
          "I certify that the above amounts are correct and represent costs " +
          "incurred in performance of the contract, that payment has not been " +
          "received, and that the amounts claimed conform to the contract terms. " +
          "This is a draft generated from burn data and must be reconciled to the " +
          "accounting system of record before submission.",
      },
    ],
  };
}
```

Then extend the `BUILDERS` map:

```js
const BUILDERS = { funding: buildFunding, invoice: buildInvoice };
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd web && node --test src/drafts.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/code/si2026-runway
git add web/src/drafts.js web/src/drafts.test.js
git commit -m "feat(drafts): SF-1034-style invoice builder"
```

---

### Task 3: Monthly CDRL status / check-in builder

**Files:**
- Modify: `web/src/drafts.js`
- Modify: `web/src/drafts.test.js`

**Interfaces:**
- Consumes: `buildDraft`, `renderDraftText` (Task 1).
- Produces: `buildDraft("cdrl", burn, opts)` returns a Doc with a per-CLIN burn summary table, a flags/at-risk text section, and **exactly one** prose section ("Accomplishments & next-period plan") seeded with heuristic copy.

- [ ] **Step 1: Write the failing CDRL test**

Append to `web/src/drafts.test.js`:

```js
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && node --test src/drafts.test.js`
Expected: FAIL — `Unknown draft type: cdrl`.

- [ ] **Step 3: Implement the CDRL builder in `web/src/drafts.js`**

Add above the `BUILDERS` map:

```js
function buildCdrl(burn, _opts) {
  const c = burn.contract || {};
  const t = burn.totals || {};
  const clins = burn.clins || [];
  const flags = [
    ...(burn.tripwires || []).map((x) => `${x.code} is over pace (tripwire)`),
    ...(burn.funding || []).map((x) => `${x.code} needs its next funding mod`),
    ...(burn.underburn || []).map((x) => `${x.code} is under-burning`),
  ];

  const heuristic =
    `During this reporting period the team continued performance across ` +
    `${clins.length} CLIN(s), with ${moneyV(t.spent)} of ${moneyV(t.ceiling)} ` +
    `expended (${typeof t.pct === "number" ? pct(t.pct) : "[verify]"} of ceiling). ` +
    (flags.length
      ? `Attention items for next period: ${flags.join("; ")}. `
      : `All lines are tracking to plan. `) +
    `Next period the team will maintain current staffing and monitor burn against pace.`;

  return {
    docType: "cdrl",
    title: "Monthly Status Report",
    draftLabel: DRAFT_LABEL,
    meta: [
      { label: "Contract (PIID)", value: vf(c.piid) },
      { label: "Contractor", value: contractorOf(c) },
      { label: "Reporting period", value: `through week ${vf(c.current_week)} of ${vf(c.total_weeks)}` },
      { label: "Overall status", value: vf(burn.hero && burn.hero.status) },
    ],
    sections: [
      {
        id: "burn",
        heading: "Burn summary by CLIN",
        kind: "table",
        columns: ["CLIN", "Description", "Spent", "Ceiling", "% burned", "Status"],
        rows: clins.map((x) => [
          vf(x.code),
          vf(x.name),
          moneyV(x.spent),
          moneyV(x.ceiling),
          typeof x.pct === "number" ? pct(x.pct) : "[verify]",
          vf(x.status_label || x.status),
        ]),
      },
      {
        id: "flags",
        heading: "Flags",
        kind: "text",
        text: flags.length ? flags.join("\n") : "No flags this period.",
      },
      {
        id: "narrative",
        heading: "Accomplishments & next-period plan",
        kind: "prose",
        text: heuristic,
      },
    ],
  };
}
```

Then extend the `BUILDERS` map:

```js
const BUILDERS = { funding: buildFunding, invoice: buildInvoice, cdrl: buildCdrl };
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd web && node --test src/drafts.test.js`
Expected: PASS (all Task 1–3 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/code/si2026-runway
git add web/src/drafts.js web/src/drafts.test.js
git commit -m "feat(drafts): CDRL monthly status check-in builder"
```

---

### Task 4: Server `/api/draft` prose-only streaming endpoint

**Files:**
- Create: `server/app/draft.py`
- Create: `server/tests/test_draft.py`
- Modify: `server/app/main.py` (add `DraftIn` + endpoint near the `/api/ask` block, ~line 500–529)
- Modify: `server/requirements.txt` (add `pytest`)

**Interfaces:**
- Consumes: `ask.build_grounding`, `ask.ASK_MODEL`, `ask.client` (from `server/app/ask.py`).
- Produces:
  - `draft.DRAFT_DOC_TYPES: set[str]` = `{"funding", "invoice", "cdrl"}`
  - `draft.draft_system_prompt(doc_type: str) -> str`
  - `draft.stream_draft(contract_id: int | None, doc_type: str)` — generator yielding prose text chunks.
  - `POST /api/draft` accepting `{ contract_id: int|None, doc_type: str }`, returning a plain-text `StreamingResponse`.

- [ ] **Step 1: Add pytest to requirements**

In `server/requirements.txt`, add a line:

```
pytest
```

Install: `cd server && python3 -m pip install pytest`

- [ ] **Step 2: Write the failing server test**

Create `server/tests/test_draft.py`:

```python
import pytest

from app.draft import DRAFT_DOC_TYPES, draft_system_prompt


def test_doc_types():
    assert DRAFT_DOC_TYPES == {"funding", "invoice", "cdrl"}


@pytest.mark.parametrize("doc_type", ["funding", "invoice", "cdrl"])
def test_prompt_is_prose_only(doc_type):
    p = draft_system_prompt(doc_type).lower()
    # It must tell the model to write prose only and NOT to emit figures/amounts,
    # since every number is filled deterministically on the client.
    assert "prose" in p or "narrative" in p
    assert "do not" in p or "never" in p
    assert "figure" in p or "dollar" in p or "amount" in p or "number" in p


def test_prompt_mentions_the_document_kind():
    assert "funding" in draft_system_prompt("funding").lower()
    assert "invoice" in draft_system_prompt("invoice").lower() or "voucher" in draft_system_prompt("invoice").lower()
    assert "status" in draft_system_prompt("cdrl").lower() or "cdrl" in draft_system_prompt("cdrl").lower()


def test_unknown_doc_type_raises():
    with pytest.raises(KeyError):
        draft_system_prompt("nope")
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_draft.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.draft'`.

- [ ] **Step 4: Implement `server/app/draft.py`**

```python
"""Runway Drafts (v1) — stream the *prose* for a generated GovCon document.

The Drafts view builds each document's numbers/dates/IDs deterministically on the
client from the burn payload; this endpoint only writes the narrative sentences.
It reuses ask.build_grounding (the same portfolio + per-contract burn context Ask
Runway uses) and the provider-configured client/model, and is told never to emit
dollar figures so a number can never come from the model.
"""

import json

from . import ask

DRAFT_DOC_TYPES = {"funding", "invoice", "cdrl"}

# What narrative each document needs. Numbers are filled on the client, so the
# model is told to write words only.
_DOC_GUIDANCE = {
    "funding": (
        "an incremental-funding request memo to the contracting officer: write the "
        "justification narrative — why continued funding is needed and the impact of "
        "a lapse."
    ),
    "invoice": (
        "an SF-1034 public voucher (invoice): write only a one-sentence cover remark. "
        "Keep it minimal; the figures and certification are supplied separately."
    ),
    "cdrl": (
        "a monthly CDRL status report: write the accomplishments-this-period and "
        "plan-for-next-period narrative."
    ),
}


def draft_system_prompt(doc_type: str) -> str:
    """Prose-only instructions for one document type. Raises KeyError if unknown."""
    guidance = _DOC_GUIDANCE[doc_type]
    return (
        "You are Runway's GovCon documentation assistant. The user is drafting "
        f"{guidance}\n\n"
        "Rules:\n"
        "- Write PROSE only — flowing sentences a program manager could send.\n"
        "- Do NOT state any dollar figures, amounts, percentages, dates, CLIN "
        "numbers, or the contract number. Those numbers are filled in separately "
        "and must never come from you. Refer to them generically ('the funded "
        "amount', 'this period') instead of inventing values.\n"
        "- No markdown, headings, or bullet asterisks — just short paragraphs.\n"
        "- Ground the tone and substance in the <data> block (the contract's burn "
        "and funding picture); never contradict it.\n"
        "- Keep it concise and professional."
    )


def stream_draft(contract_id, doc_type: str):
    """Yield the document's narrative prose in chunks. Numbers stay on the client."""
    grounding = ask.build_grounding(contract_id)
    system = (
        draft_system_prompt(doc_type)
        + "\n\n<data>\n"
        + json.dumps(grounding, default=str)
        + "\n</data>"
    )
    with ask.client.messages.stream(
        model=ask.ASK_MODEL,
        max_tokens=700,
        system=system,
        messages=[{"role": "user", "content": "Write the narrative now."}],
    ) as stream:
        for text in stream.text_stream:
            yield text
```

- [ ] **Step 5: Add the endpoint to `server/app/main.py`**

Ensure `draft` is imported alongside `ask` at the top of `main.py` (find the existing `from .app...`/`from . import ...` imports; add `draft` to the module import, e.g. change `from . import ... ask ...` to include `draft`). Then add directly below the `/api/ask` block (after line 529):

```python
class DraftIn(BaseModel):
    """One Runway Drafts request. `doc_type` is one of draft.DRAFT_DOC_TYPES;
    `contract_id` is the contract the document is about."""

    contract_id: Optional[int] = None
    doc_type: str


@app.post("/api/draft")
def draft_document(body: DraftIn):
    """Runway Drafts: stream the narrative PROSE for a generated GovCon document.
    Numbers are filled client-side from the burn payload; this only writes words,
    grounded in the same burn context as Ask Runway (see draft.py)."""
    if body.doc_type not in draft.DRAFT_DOC_TYPES:
        raise HTTPException(status_code=422, detail="Unknown document type.")

    def gen():
        try:
            yield from draft.stream_draft(body.contract_id, body.doc_type)
        except Exception as e:
            # Stream already opened (200 sent) — surface inline; the client falls
            # back to its deterministic heuristic prose on empty/errored streams.
            yield f"\n\n[Draft generation hit an error: {e}]"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")
```

- [ ] **Step 6: Run to verify the test passes**

Run: `cd server && python3 -m pytest tests/test_draft.py -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Verify the app still imports (endpoint wired without syntax errors)**

Run: `cd server && python3 -c "from app.main import app; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 8: Format and commit**

```bash
cd ~/code/si2026-runway/server && python3 -m black .
cd ~/code/si2026-runway
git add server/app/draft.py server/tests/test_draft.py server/app/main.py server/requirements.txt
git commit -m "feat(drafts): POST /api/draft prose-only streaming endpoint"
```

---

### Task 5: `draftProse` client helper + print stylesheet

**Files:**
- Modify: `web/src/api.js` (add `draftProse`)
- Modify: `web/index.html` (add `@media print` rules to the existing `<style>` block)

**Interfaces:**
- Consumes: nothing new (mirrors `askRunway`).
- Produces:
  - `export async function draftProse({ contractId, docType }, onChunk)` → resolves to the full streamed prose string; fires `onChunk(chunk)` per chunk.
  - CSS: `.draft-page` (the printable region) and `.no-print` (hidden when printing).

- [ ] **Step 1: Add `draftProse` to `web/src/api.js`**

Append at the end of the file:

```js
// Runway Drafts. Streams the narrative PROSE for a generated document (numbers
// are filled client-side); onChunk fires per chunk so the panel can render it
// live. Mirrors askRunway's plain-text stream. Returns the full prose on close.
export async function draftProse({ contractId = null, docType }, onChunk) {
  const r = await fetch(`${BASE}/api/draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ contract_id: contractId, doc_type: docType }),
  });
  if (!r.ok || !r.body) throw new Error(`Draft failed (${r.status})`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let full = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    if (chunk) {
      full += chunk;
      onChunk?.(chunk);
    }
  }
  return full;
}
```

- [ ] **Step 2: Add print rules to `web/index.html`**

Inside the existing `<style>…</style>` in `index.html`, append before the closing `</style>`:

```css
      @media print {
        body * { visibility: hidden; }
        .draft-page, .draft-page * { visibility: visible; }
        .draft-page {
          position: absolute; left: 0; top: 0; width: 100%;
          box-shadow: none !important; border: none !important; padding: 0 !important;
        }
        .no-print { display: none !important; }
      }
```

- [ ] **Step 3: Verify the web build still compiles**

Run: `cd web && npm run build`
Expected: build succeeds (Vite emits `dist/` with no errors).

- [ ] **Step 4: Commit**

```bash
cd ~/code/si2026-runway
git add web/src/api.js web/index.html
git commit -m "feat(drafts): draftProse stream helper + print stylesheet"
```

---

### Task 6: Drafts view

**Files:**
- Create: `web/src/views/Drafts.jsx`

**Interfaces:**
- Consumes: `getBurn`, `listContracts`, `draftProse` from `../api.js`; `buildDraft`, `renderDraftText`, `DOC_TYPES` from `../drafts.js`; `panelStyle` from `../format.js`.
- Produces (default export): `Drafts({ contractId, setActiveId, aiEnabled, pendingDocType, onConsumedPending })`.
  - `pendingDocType` (string | null): a doc type to auto-select and auto-generate on mount (the funding deep-link). `onConsumedPending()` clears it after use.

- [ ] **Step 1: Implement `web/src/views/Drafts.jsx`**

```jsx
import React, { useEffect, useRef, useState } from "react";
import { getBurn, listContracts, draftProse } from "../api.js";
import { buildDraft, renderDraftText, DOC_TYPES } from "../drafts.js";
import { panelStyle } from "../format.js";

const grotesk = "'Space Grotesk',sans-serif";

const controlBtn = {
  height: 36,
  padding: "0 16px",
  borderRadius: 9,
  border: "none",
  background: "var(--accent)",
  color: "#fff",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};
const ghostBtn = {
  height: 36,
  padding: "0 14px",
  borderRadius: 9,
  border: "1px solid var(--border)",
  background: "var(--panel2)",
  color: "var(--text)",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};

// Render a Doc (from buildDraft) to HTML for the editable page. Prose text can be
// swapped by the AI stream before this runs; after generation the whole page is
// contentEditable so the PM can tweak both numbers and prose.
function docToHtml(doc) {
  const esc = (s) =>
    String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const parts = [
    `<h1 style="font-family:${grotesk};font-size:22px;margin:0 0 4px">${esc(doc.title)}</h1>`,
    `<div style="color:var(--bad);font-weight:700;font-size:12px;letter-spacing:.08em;margin-bottom:16px">${esc(doc.draftLabel)}</div>`,
    `<table style="border-collapse:collapse;margin-bottom:18px">${doc.meta
      .map(
        (m) =>
          `<tr><td style="padding:2px 16px 2px 0;color:var(--dim);font-size:12.5px;vertical-align:top">${esc(m.label)}</td>` +
          `<td style="padding:2px 0;font-size:12.5px;color:var(--text)">${esc(m.value)}</td></tr>`
      )
      .join("")}</table>`,
  ];
  for (const s of doc.sections) {
    if (s.heading)
      parts.push(
        `<h2 style="font-family:${grotesk};font-size:15px;margin:18px 0 8px;color:var(--text)">${esc(s.heading)}</h2>`
      );
    if (s.kind === "table") {
      parts.push(
        `<table style="border-collapse:collapse;width:100%;font-size:12.5px;margin-bottom:8px">` +
          `<thead><tr>${s.columns
            .map(
              (c) =>
                `<th style="text-align:left;border-bottom:1px solid var(--border);padding:6px 8px;color:var(--dim);font-weight:600">${esc(c)}</th>`
            )
            .join("")}</tr></thead><tbody>${s.rows
            .map(
              (r) =>
                `<tr>${r
                  .map(
                    (cell) =>
                      `<td style="border-bottom:1px solid var(--border);padding:6px 8px;color:var(--text)">${esc(cell)}</td>`
                  )
                  .join("")}</tr>`
            )
            .join("")}</tbody></table>`
      );
    } else {
      parts.push(
        `<p style="font-size:13px;line-height:1.6;color:var(--text);white-space:pre-wrap;margin:0 0 10px">${esc(s.text)}</p>`
      );
    }
  }
  return parts.join("");
}

export default function Drafts({ contractId, setActiveId, aiEnabled, pendingDocType, onConsumedPending }) {
  const [contracts, setContracts] = useState([]);
  const [docType, setDocType] = useState(pendingDocType || "funding");
  const [status, setStatus] = useState("idle"); // idle | building | streaming | ready
  const [aiNote, setAiNote] = useState(null);
  const pageRef = useRef(null);
  const docRef = useRef(null); // the current Doc object (numbers + prose)

  // Populate the contract picker; default the active contract if App hasn't set one.
  useEffect(() => {
    listContracts()
      .then((cs) => {
        setContracts(cs);
        if (contractId == null && cs.length) setActiveId(cs[0].id);
      })
      .catch(() => setContracts([]));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Render the current Doc into the (read-only during build) page.
  function paint(doc) {
    docRef.current = doc;
    if (pageRef.current) pageRef.current.innerHTML = docToHtml(doc);
  }

  async function generate(nextType = docType) {
    if (contractId == null) return;
    setStatus("building");
    setAiNote(null);
    let burn;
    try {
      burn = await getBurn(contractId);
    } catch (e) {
      setStatus("idle");
      setAiNote(`Couldn't load burn data: ${e.message}`);
      return;
    }
    // Deterministic scaffold + heuristic prose first — this is the AI-off result.
    const doc = buildDraft(nextType, burn, {});
    paint(doc);
    if (pageRef.current) pageRef.current.contentEditable = "false";

    const proseSection = doc.sections.find((s) => s.kind === "prose");
    if (!aiEnabled || !proseSection) {
      finishEditable();
      return;
    }
    // AI on: stream a phrased version of the single prose section over the top.
    setStatus("streaming");
    let streamed = "";
    try {
      await draftProse({ contractId, docType: nextType }, (chunk) => {
        streamed += chunk;
        proseSection.text = streamed;
        paint(doc);
      });
      if (!streamed.trim()) setAiNote("AI unavailable — using standard wording.");
    } catch {
      proseSection.text = buildDraft(nextType, burn, {}).sections.find((s) => s.kind === "prose").text;
      paint(doc);
      setAiNote("AI unavailable — using standard wording.");
    }
    finishEditable();
  }

  function finishEditable() {
    setStatus("ready");
    if (pageRef.current) pageRef.current.contentEditable = "true";
  }

  // Consume a funding deep-link once: select the doc type and auto-generate.
  useEffect(() => {
    if (pendingDocType && contractId != null) {
      setDocType(pendingDocType);
      generate(pendingDocType);
      onConsumedPending?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingDocType, contractId]);

  function onCopy() {
    const text = pageRef.current ? pageRef.current.innerText : renderDraftText(docRef.current || { title: "", draftLabel: "", meta: [], sections: [] });
    navigator.clipboard?.writeText(text);
  }

  return (
    <div style={{ padding: "24px 26px 60px", maxWidth: 900 }}>
      <div className="no-print" style={{ marginBottom: 18 }}>
        <h2 style={{ margin: 0, fontFamily: grotesk, fontSize: 22, fontWeight: 600, color: "var(--text)" }}>
          Drafts
        </h2>
        <div style={{ fontSize: 13.5, color: "var(--dim)", marginTop: 5 }}>
          Generate GovCon paperwork from live burn data. Numbers come straight from the
          contract; {aiEnabled ? "AI tailors the wording." : "turn on AI for tailored wording."}
        </div>
      </div>

      {/* controls */}
      <div className="no-print" style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginBottom: 16 }}>
        <select
          value={contractId ?? ""}
          onChange={(e) => setActiveId(Number(e.target.value))}
          style={{ ...ghostBtn, cursor: "pointer" }}
        >
          {contracts.map((c) => (
            <option key={c.id} value={c.id}>{c.name || c.piid}</option>
          ))}
        </select>
        <div style={{ display: "flex", gap: 6 }}>
          {DOC_TYPES.map((d) => (
            <button
              key={d.key}
              title={d.blurb}
              onClick={() => setDocType(d.key)}
              style={{
                ...ghostBtn,
                borderColor: docType === d.key ? "var(--accent)" : "var(--border)",
                color: docType === d.key ? "var(--accent)" : "var(--text)",
              }}
            >
              {d.label}
            </button>
          ))}
        </div>
        <button onClick={() => generate()} disabled={contractId == null || status === "streaming"} style={controlBtn}>
          {status === "streaming" ? "✨ tailoring…" : "Generate"}
        </button>
        {docRef.current && status === "ready" && (
          <>
            <button onClick={onCopy} style={ghostBtn}>Copy</button>
            <button onClick={() => window.print()} style={ghostBtn}>Export to PDF</button>
          </>
        )}
      </div>

      {aiNote && (
        <div className="no-print" style={{ fontSize: 12, color: "var(--dim)", marginBottom: 10 }}>{aiNote}</div>
      )}

      {/* the editable, printable document page */}
      {status === "idle" ? (
        <div style={{ ...panelStyle, color: "var(--dim)", fontSize: 13 }}>
          Pick a contract and document type, then Generate.
        </div>
      ) : (
        <div
          ref={pageRef}
          className="draft-page"
          style={{ ...panelStyle, minHeight: 300, padding: 28, outline: "none" }}
          suppressContentEditableWarning
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify the web build compiles**

Run: `cd web && npm run build`
Expected: build succeeds (this catches JSX/import errors; the view isn't mounted until Task 7).

- [ ] **Step 3: Commit**

```bash
cd ~/code/si2026-runway
git add web/src/views/Drafts.jsx
git commit -m "feat(drafts): Drafts view (editable page, Copy, Export to PDF, AI streaming)"
```

---

### Task 7: Wire Drafts into App + Sidebar + Flight Deck deep-link

**Files:**
- Modify: `web/src/App.jsx`
- Modify: `web/src/components/Sidebar.jsx`
- Modify: `web/src/views/FlightDeck.jsx`

**Interfaces:**
- Consumes: `Drafts` default export (Task 6); the existing `pendingBalance`/`expenseClin` one-shot handoff pattern in `App.jsx`.
- Produces: a reachable `"drafts"` view; `App` passes `pendingDocType`/`onConsumedPending` to `Drafts` and `onOpenDrafts(contractId, docType)` to `FlightDeck`; the Flight Deck funding suggestion renders a "Draft funding request" button.

- [ ] **Step 1: Add the Drafts nav item + icon to `Sidebar.jsx`**

In `web/src/components/Sidebar.jsx`, add a `drafts` icon to the `ICONS` object (alongside the others):

```jsx
  drafts: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z" />
      <path d="M14 3v5h5" strokeLinejoin="round" />
      <path d="M8 13h8M8 17h5" strokeLinecap="round" />
    </svg>
  ),
```

Then add a nav entry to `CONTRACT_NAV` (place it after `funding`, before `ingest`):

```jsx
  { key: "drafts", label: "Drafts", sub: "Memos · invoices · check-ins" },
```

- [ ] **Step 2: Wire the view + handoff state into `App.jsx`**

In `web/src/App.jsx`:

(a) Add the import next to the other view imports:

```jsx
import Drafts from "./views/Drafts.jsx";
```

(b) Add handoff state next to `pendingBalance` (after line 71):

```jsx
  // A Flight Deck funding suggestion asked to draft a funding request; the Drafts
  // view reads this once on arrival to auto-select the doc type and generate.
  const [pendingDocType, setPendingDocType] = useState(null);
```

(c) Add an opener next to `openAllocationBalanced` (after line 126):

```jsx
  // Deep-link from a suggestion into the Drafts view, pre-loaded for a contract.
  function openDrafts(id, docType) {
    if (id != null) setActiveId(id);
    setPendingDocType(docType || null);
    setView("drafts");
  }
```

(d) Pass `onOpenDrafts` to `FlightDeck` (add to its props in the `view === "flightdeck"` block):

```jsx
              onOpenDrafts={openDrafts}
```

(e) Add the `"drafts"` branch to the view ternary (insert before the final `Placeholder` fallback, after the `allocate` branch):

```jsx
          ) : view === "drafts" ? (
            <Drafts
              contractId={activeId}
              setActiveId={setActiveId}
              aiEnabled={aiEnabled}
              pendingDocType={pendingDocType}
              onConsumedPending={() => setPendingDocType(null)}
            />
```

- [ ] **Step 3: Add the deep-link button to the Flight Deck funding suggestion**

In `web/src/views/FlightDeck.jsx`:

(a) Add `onOpenDrafts` to the `FlightDeck` component's destructured props (in the signature around line 158–167):

```jsx
  onOpenDrafts,
```

(b) Thread it to each `Suggestion` — add the prop to all three `<Suggestion .../>` usages (over, underburn, funding):

```jsx
              onOpenDrafts={onOpenDrafts}
              contract2Id={contractId}
```

Wait — the `Suggestion` component already receives `contractId`. Only add `onOpenDrafts={onOpenDrafts}` to the three `<Suggestion>` tags.

(c) Update the `Suggestion` component signature (line 47) to accept it:

```jsx
function Suggestion({ kind, item, contract, aiEnabled, contractId, onAction, onOpenDrafts }) {
```

(d) In the funding branch of `Suggestion`'s button row (the `else` branch around lines 146–150), replace the single "Open funding history" button with a draft button plus the existing one:

```jsx
            ) : (
              <>
                <button onClick={() => onAction("funding")} style={btnSecondary}>
                  Open funding history
                </button>
                <button
                  onClick={() => onOpenDrafts?.(contractId, "funding")}
                  style={btnPrimary}
                >
                  Draft funding request →
                </button>
              </>
            )}
```

- [ ] **Step 4: Verify the build compiles**

Run: `cd web && npm run build`
Expected: build succeeds.

- [ ] **Step 5: Manual end-to-end verification**

Start the stack (two terminals):

```bash
cd server && python3 -m uvicorn app.main:app --reload --port 8001
cd web && npm run dev
```

Confirm, in the browser:
1. Open a contract → Sidebar shows **Drafts** under the current contract; clicking it opens the Drafts view.
2. With AI **off**: pick each of the three doc types, Generate → a filled document appears with real numbers and heuristic prose; **Copy** and **Export to PDF** appear; Export opens a print view showing only the document (no sidebar/controls).
3. Toggle AI **on** (✨ pill): Generate a funding memo → "✨ tailoring…" shows, prose is replaced by streamed text; if Bedrock yields nothing, the note "AI unavailable — using standard wording" shows and heuristic prose remains.
4. On the Flight Deck, a contract with a funding-status CLIN shows **"Draft funding request →"** in the suggests strip → clicking it lands in Drafts with the funding memo already generated for that contract.
5. Edit a number directly in the page, then Copy → the copied text reflects the edit.

- [ ] **Step 6: Run the full web test suite (regression)**

Run: `cd web && node --test`
Expected: PASS (all `drafts.test.js` tests still green).

- [ ] **Step 7: Commit**

```bash
cd ~/code/si2026-runway
git add web/src/App.jsx web/src/components/Sidebar.jsx web/src/views/FlightDeck.jsx
git commit -m "feat(drafts): mount Drafts view, sidebar nav, and Flight Deck funding deep-link"
```

---

## Self-Review

**Spec coverage:**
- Three doc types (funding memo, SF-1034 invoice, CDRL check-in) → Tasks 1, 2, 3. ✓
- Deterministic numbers, LLM writes prose only → `buildDraft` fills numbers (Tasks 1–3); `/api/draft` + `draft_system_prompt` forbid figures (Task 4). ✓
- Non-AI path with heuristic prose → prose sections seeded in `buildDraft`; view uses them when `aiEnabled` is false (Tasks 1–3, 6). ✓
- `POST /api/draft` streaming, reuses `build_grounding` → Task 4. ✓
- `draftProse` client helper mirroring `askRunway` → Task 5. ✓
- Drafts view + Sidebar entry + `"drafts"` App branch → Tasks 6, 7. ✓
- Both entry points (view + Flight Deck deep-link via `pendingDocType` handoff) → Task 7. ✓
- AI toggle integration + silent heuristic fallback + "turn on AI" hint → Task 6. ✓
- `[verify]` for missing data (esp. invoice) → `vf`/`moneyV` (Tasks 1, 2); tested in Task 2. ✓
- DRAFT labeling → `DRAFT_LABEL` on every doc (Tasks 1–3). ✓
- Copy + Export-to-PDF via `window.print()` + print CSS, no PDF lib → Tasks 5, 6. ✓
- Tests for `buildDraft` and the endpoint → Tasks 1–3 (web), Task 4 (server). ✓
- YAGNI: no persistence, no `.docx`, no submission workflow → none added. ✓

**Placeholder scan:** No "TBD"/"TODO"/"handle edge cases"/"similar to Task N" — every code step carries full code. The literal string `[verify]` is an intended runtime value, not a plan placeholder.

**Type consistency:** `buildDraft(docType, burn, opts)`, the `Doc` shape (`docType`/`title`/`draftLabel`/`meta[{label,value}]`/`sections[{id,heading,kind,...}]`), `renderDraftText(doc)`, `vf`, `draftProse({contractId,docType}, onChunk)`, `draft.DRAFT_DOC_TYPES`, `draft.draft_system_prompt`, `draft.stream_draft(contract_id, doc_type)`, and the `Drafts` props (`contractId`, `setActiveId`, `aiEnabled`, `pendingDocType`, `onConsumedPending`) / `openDrafts(id, docType)` / `onOpenDrafts` are used identically across tasks. ✓

**Note on the server endpoint test:** the generative "prose contains no invented figures" assertion requires a live model, so it's covered two ways — a unit test that the *system prompt* forbids figures (Task 4, deterministic) plus manual verification with AI on (Task 7, step 5). The HTTP 422 path for an unknown `doc_type` is guarded in code; a live 200 stream is exercised manually rather than mocking Bedrock, to avoid adding an HTTP-mock dependency to a repo that currently has no server tests.
