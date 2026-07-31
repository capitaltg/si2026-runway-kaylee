# Runway Drafts — Design

**Date:** 2026-07-31
**Status:** Approved (design phase)
**Repo:** `capitaltg/si2026-runway-kaylee`

## Goal

From a contract's live burn data, generate three GovCon documents that a PM can edit, copy, and export to PDF:

1. **Incremental-funding request memo**
2. **SF-1034-style invoice**
3. **Monthly CDRL status / check-in**

All dollar figures, dates, and identifiers are filled deterministically from the burn/contract data — the LLM never authors numbers. The feature has a full non-AI path: with the AI toggle off it still produces a usable draft using heuristic prose.

## Non-goals (v1 / YAGNI)

- No draft persistence — drafts are generated fresh each time, not saved.
- No `.docx` export (Copy + print-to-PDF covers the handoff).
- No submission, routing, or e-signature workflow.

## Approach

Client builds the deterministic scaffold; a thin server endpoint streams only the prose. Chosen over an all-server assembly because it mirrors patterns already in the repo (`suggest.js` heuristics, client-side blob/`window.print` export), keeps numbers authoritative on the client where the burn payload already lives, and makes the AI-off path essentially free.

## Architecture / data flow

- **`web/src/drafts.js`** (new, sibling to `suggest.js`): `buildDraft(type, burn, contract, opts)` returns a structured document `{ title, meta[], sections[] }`. It fills every number/date/ID from the burn payload the view already loads (the same `burn.compute` GET the Flight Deck uses). Prose sections are populated either by streamed LLM text (AI on) or by heuristic boilerplate baked into `drafts.js` (AI off). `buildDraft` is a pure function.
- **`POST /api/draft`** (new server endpoint): body `{ contract_id, doc_type }`. Streams **narrative prose only** as a plain-text stream, exactly like the existing `/api/ask` (`StreamingResponse`, `text/plain`). Reuses `build_grounding(contract_id)` for context plus a doc-type-specific system prompt. It must not emit dollar figures — those stay client-side and authoritative.
- **`web/src/api.js`**: add a `draftProse({contractId, docType}, onChunk)` client helper mirroring `askRunway` (fetch + `getReader()` + `TextDecoder`).

## The three documents

Each document is deterministic fields (from data) + prose (LLM or heuristic).

### Incremental-funding request memo
- **Fields:** To (contracting officer / COR), contractor, PIID, current obligated vs total ceiling, the at-risk funded CLIN and its `exhaust_week`, requested increment amount, period of performance.
- **Prose:** funding justification.
- **Source fields:** `burn.compute().contract` (`piid, name/legal_name, agency, pop_start, pop_end, obligated, contract_ceiling`), the funding tripwire entry (`code, funded, budget, exhaust_week, weeks_early`), header (`contracting_officer, cor`), and `obligation_history` (last `cumulative_obligated`).

### SF-1034-style invoice
- **Fields:** PIID, billing period, per-CLIN cost incurred this period (labor hours × loaded rate + logged expenses), cumulative billed, remaining funded, certification line.
- **Prose:** minimal.
- **Marked `DRAFT`.** Any figure that cannot be computed from available data renders as a `[verify]` placeholder — never a guessed number.
- **Source fields:** per-CLIN `spent`/`weekly`/`actuals` and `blended_rate` from `_compute_clin`, expenses, `funded`/`remaining`.

### Monthly CDRL status / check-in
- **Fields:** reporting period, overall status, per-CLIN burn summary table, tripwire / funding flags.
- **Prose:** period accomplishments + next-period plan.
- **Source fields:** `burn.compute()` `totals`, `hero`, `clins`, `tripwires`, `funding`, `underburn`.

## Drafts view (client)

- New `"drafts"` branch in the `App.jsx` view ternary (currently keys on `ingest|portfolio|flightdeck|expenses|funding|allocate`), plus a Sidebar nav entry.
- **Controls:** contract dropdown (defaults to `activeId`), a 3-way doc-type selector, Generate button.
- **Output:** an editable document panel styled as a page, so the user can tweak both numbers and prose before sending. Uses existing inline-style + CSS-variable conventions (`var(--panel)`, `var(--border)`, `btnPrimary`/`btnSecondary`); no new UI library (the app has none).

## Entry points (both)

1. **Dedicated Drafts view** (above) — generate any of the three doc types for any contract, anytime.
2. **Flight Deck suggests strip** — the funding suggestion (already seeded in `suggest.js`, `action.kind === "funding"`) gets a **"Draft funding request →"** button. It calls an `onOpenDrafts(contractId, 'funding')` callback threaded from `App.jsx` via a one-shot `pendingDraft` handoff (same pattern as `pendingBalance` / `expenseClin`), landing in the Drafts view pre-selected and auto-generated.

## AI toggle integration

- Reads the same `localStorage["runway.ai"]` flag shipped with the AI toggle (default off).
- **On:** prose streams in from `/api/draft`.
- **Off:** heuristic prose from `drafts.js` + a subtle "✨ Turn on AI for tailored wording" hint.

## Error handling

- **Bedrock flakiness:** if the `/api/draft` stream errors or yields no content (a known current condition), the view **silently falls back to heuristic prose**. The deterministic scaffold is never blocked, and a small "AI unavailable — using standard wording" note is shown.
- **Missing data:** a required figure that is not derivable renders as a clearly marked `[verify]` placeholder rather than a wrong or zero value. Especially important on the invoice.
- **DRAFT labeling:** every document is labeled a draft to be verified before submission; the invoice figures are computed estimates from burn data, not an accounting system of record.

## Export

- **Copy:** `navigator.clipboard.writeText(...)` with the plain-text rendering of the doc.
- **Export to PDF:** `window.print()` with a print stylesheet that hides app chrome and renders the document panel as a page; the user saves as PDF via the browser. No PDF library.

## Testing

- **`drafts.js` `buildDraft`** (pure): unit-test each doc type against a sample burn payload — assert deterministic fields are correct, missing data yields `[verify]`, and heuristic prose is present when AI is off.
- **`POST /api/draft`**: assert the response is prose-only and contains no invented dollar figures for a known fixture.

## Reuse notes

- Grounding + streaming pattern: `server/app/ask.py` (`build_grounding`, `stream_answer`) and `POST /api/ask` in `main.py`.
- Heuristic-copy pattern and the funding seed: `web/src/suggest.js` (`suggestFor`, the `"funding"` branch).
- Client streaming consumption: `askRunway` in `web/src/api.js`.
- Cross-view one-shot handoff: `pendingBalance` / `expenseClin` in `App.jsx`.
- Export/print precedent: `exportCsv` in `App.jsx` (client-side blob).
