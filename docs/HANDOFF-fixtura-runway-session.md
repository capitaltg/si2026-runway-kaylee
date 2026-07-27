# Handoff — Runway funding-aware burn + coherent Fixtura test data

Written 2026-07-27 for the next AI picking up Runway. Kaylee works in a VS Code
dev container; Claude Code runs on the Mac host. Both repos live in `~/code` so
they're shared into the container.

## TL;DR

This session made Runway's burn/runway **funding-aware** (it now measures runway
against the *obligated/funded* dollars, not just the ceiling — the FAR 52.232-22
"you run out of cash before the ceiling" story), generated a **coherent Fixtura
test dataset** (a real SF-26 PDF + matching timesheet/labor CSVs, all one
contract), wired the demo so it works end-to-end with a **funded tripwire firing
~week 34**, reworked the **burn chart** to show funded-vs-ceiling clearly, and
fixed the **Confirm button** so it navigates to the new contract.

## PRs opened this session

- **Runway** (this repo): branch `feat/funding-aware-burn` → PR to `main`
  (the previous burn-engine PR #16 is already merged). Contains everything below.
- **Fixtura** (`si2026-test-generator-kaylee`):
  - **PR #45** `feat/govcon-editable-templates` — the editable GovCon options +
    saveable templates feature, **plus** a fix to `_recent_week_ending` (a
    Friday-rounding underflow that could date a timesheet before the PoP start).
  - **PR #46** `feat/scenario-staffing` — the general opt-in `staffing` knob on
    `build_scenario` (crew the roster to the CLIN's planned FTEs).

> **Cross-repo dependency:** Runway's live "Sync now" asks Fixtura to generate
> timesheets with `{pop_in_progress, option_years:0, contract_type:"T&M",
> staffing:1.0}`. `pop_in_progress` etc. come from Fixtura **PR #45** and
> `staffing` from **PR #46**. Both must be merged (and Fixtura restarted) for the
> live sync to produce the tripping dataset. The committed CSVs in
> `sample-data/` were generated with those PRs' code, so testing against the
> bundled files works regardless.

## What changed in Runway

### Funding-aware burn engine (`server/app/burn.py`)
- `_compute_clin(..., funded=)` measures runway/status/exhaust against the
  **binding budget** = the funded slice when `0 < funded < ceiling`, else the
  ceiling. Adds `budget`, `funded`, `incrementally_funded` to each CLIN.
- `compute()` allocates the award's single `total_obligated` across the active
  period's CLINs **pro-rata by ceiling** (this is an approximation — see #21).
- `hero` and each `tripwire` carry `limited_by: "funding" | "ceiling"` so the UI
  can say "runs out of funded dollars" vs "blows the ceiling".

### Frontend
- `web/src/components/BurnChart.jsx` — the pace line targets the **funded** level
  (not the ceiling); a dashed **Funded** line + a **hatched "unfunded" band** with
  a muted right-edge caption sit below the red **Ceiling** cap; the "funds
  exhaust" marker sits on the funded line.
- `web/src/views/FlightDeck.jsx` — hero + tripwire copy reflect `limited_by`;
  **auto-syncs** timesheets on first visit to a contract with no hours; legend
  reads "Pace to stay funded" when incrementally funded.
- `web/src/views/Ingest.jsx` + `web/src/App.jsx` — **Confirm & build plan** now
  navigates to the new contract's Flight Deck (`onSaved` → `openContract`),
  dropping the old native `alert()`.
- `web/src/api.js` — `syncTimesheets` omits params it isn't given so the backend
  demo defaults govern.

### Sync + sample (`server/app/sources.py`, `server/app/main.py`)
- `DEMO_SCENARIO_OPTS` (adds `staffing:1.0`) + `DEMO_SYNC_ROWS = 460` — the opts
  and row count Sync sends to Fixtura, tuned so the bundled contract burns on plan
  and funded dollars exhaust ~wk 34. Overridable by any caller.
- `SAMPLE` now points at the SF-26 PDF; the no-file "Ingest sample with AI" branch
  uses `extract_from_pdf`.

### Sample data (`sample-data/fixtura-runway-burn-demo.*`)
One coherent contract, seed 42: `.award.sf26.pdf` (real filled SF-26 + Section B
rates), `.contract.json`, `.timesheets.csv`, `.labor.csv`, `.README.md`.
PIID `7026HEXDVC0001043`, ceiling $8,701,569.60, obligated $5,608,002.71 (64%
funded). Funded exhausts ~wk 34 (CLIN 0001) / ~wk 36 (CLIN 0002).

## Demo it end-to-end (no file download needed)
1. Start the apps (ports below).
2. Runway → Ingest → **"Ingest sample with AI"** (reads the bundled SF-26 PDF; no
   file to pick). *Needs Bedrock/Anthropic creds for extraction — see gotchas.*
3. Review → **Confirm & build plan →** lands on the Flight Deck.
4. It **auto-syncs** on first load; two red tripwires fire (~wk 34 / ~wk 36).

## Ports

| App | Dir | Port | Start | URL |
|---|---|---|---|---|
| Fixtura (API + UI) | `si2026-test-generator-kaylee/` | 8000 | `uvicorn server:app --reload` | http://127.0.0.1:8000/ |
| Runway API | `si2026-runway/server/` | 8001 | `uvicorn app.main:app --reload --port 8001` | http://127.0.0.1:8001/ |
| Runway web | `si2026-runway/web/` | 5173 | `npm run dev` | http://127.0.0.1:5173/ |

Runway's frontend targets `http://localhost:8001` (`web/src/api.js`); Runway
reaches Fixtura at `http://localhost:8000` (`FIXTURA_URL`, `sources.py`).

## Gotchas
- **AI extraction needs cloud creds** (Bedrock default, or Anthropic — see
  `extract.py`). Without them, "Ingest sample with AI" 502s; use the **manual
  entry** path, or the committed `.contract.json` for engine testing. The burn
  math this session was all verified headless against the CSVs.
- **`pop_in_progress` dates are today-relative.** The award's effective date and
  the timesheet weeks are anchored to *today*, so regenerating the sample on a
  different day shifts the dates (seed 42 keeps the week offset stable). If the
  committed CSVs drift far from "today", regenerate them (see the sample README).
- **Per-CLIN funded is pro-rata** by ceiling, not real ACRN-level obligation
  (approximation — #21).
- `docs/design/` is left untracked on purpose (earlier design import).

## Open tickets (all on `capitaltg/si2026-runway-kaylee`)
- **#17** burn chart: fuller funded-vs-ceiling rework (this session did the first pass).
- **#18** ingest SF-30 mods to add funding + refresh burn.
- **#19** browser Back/Forward navigation (URL + history).
- **#20** non-labor CLIN cards route to a per-CLIN manual import (overlaps **#7** Expenses view).
- **#21** allocate funded dollars per-CLIN from real obligation data (not pro-rata).
- **#22** don't cry wolf on routine incremental funding — measure funding pace vs burn pace.
- Prior: **#15** AI "Runway suggests" on tripwires, grounded in real data.
