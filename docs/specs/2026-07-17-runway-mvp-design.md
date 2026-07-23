# Runway — MVP Design Spec

**Date:** 2026-07-17
**Repo:** `capitaltg/si2026-runway`
**Status:** Approved to build (stack + look locked; layout polish deferred)
**Timeline:** 4-week internship build

---

## 1. What we're building

**Runway** is an AI-native early-warning system for GovCon project managers. Drop in a
federal award PDF and Runway turns it into a live spend plan that forecasts *when* a
contract runs out of money and *how* to fix it. It's a lightweight overlay on top of
existing tools — **not** an accounting ERP.

One-line: *"Runway reads your contract and tells you when the money runs out while you can still fix it."*

## 2. User & goal

- **Primary user:** a GovCon project manager / CFO tracking post-award spend.
- **Their problem today:** they track a million-dollar ceiling by hand across spreadsheets,
  time trackers, and a monthly accounting report — and discover overruns too late to fix.
- **Success for the MVP (demo):** a believable, good-looking flow — ingest a contract PDF,
  allocate a team, and watch the dashboard forecast a funding shortfall with a concrete fix.
  Mockups/seeded data are fine; no production integrations required.

## 3. Scope

### In scope (MVP — ship no matter what)
1. Project scaffolding (React + FastAPI, routing, theme).
2. **Contract PDF ingest** — upload an SF-26/SF-1449; AI extracts CLINs, obligated funding,
   ceiling, and period-of-performance dates into structured records.
3. **Employee pool** — manual form: name, role, LCAT, hourly bill rate.
4. **Allocation matrix** — assign employees to CLINs with estimated weekly hours.
5. **Burn dashboard** — spend velocity, "days of runway remaining", planned-vs-projected pacing chart.
6. **Tripwire alerts** — warn when a staffing mix exhausts a CLIN before period-of-performance ends, with a suggested fix.
7. **Seeded demo data** — one realistic fake contract + team so the demo always works.

### Stretch (in priority order, only if time allows)
NL "chat with your contract" · CSV employee import · LCAT compliance check ·
multi-contract portfolio view · timesheet/actuals import · pacing personas.

### Non-goals
Real payroll/ERP integrations · multi-tenant auth/billing · production-grade security ·
non-labor CLIN forecasting (non-labor is manual expense entry only) · mobile app.

## 4. Architecture (mirrors slate-data, minus the heavy infra)

| Layer | Choice | Notes |
|---|---|---|
| Frontend | React + TypeScript + Vite + **Mantine** | Same as slate-data. Themed to Runway brand. |
| Backend | Python + **FastAPI** | Same as slate-data. REST endpoints the frontend calls. |
| Database | **SQLite** (single file) | Simpler than slate-data's ClickHouse; right size for this. |
| ORM/migrations | SQLModel or SQLAlchemy + Alembic | Whatever keeps models simple. |
| AI | **Anthropic / Claude API** | PDF field extraction, tripwire fix suggestions, (stretch) chat. |
| PDF parsing | `pypdf`/`pdfplumber` → text → Claude for structured extraction | Text first, LLM to structure it. |
| Dev run | `uvicorn` (backend) + `npm run dev` (frontend) | **No Docker required for dev.** |
| Container (later) | devcontainer config added at scaffold | So Kaylee develops in-container like slate-data. |

**Module boundaries (keep units small and focused):**
- `frontend/` — pages (Dashboard, Contracts, Allocations, Team), reusable components, a Runway theme file, an API client.
- `backend/` — `routers/` (contracts, employees, allocations, burn), `services/` (pdf_ingest, burn_engine, tripwire, ai_client), `models/`, `db.py`, `seed.py`.
- The **burn engine** is pure functions (inputs → forecast) so it's testable in isolation.

## 5. Data model (core entities)

- **Contract** — id, name, contract_number, ceiling, period_of_performance_start/end, source_pdf.
- **CLIN** — id, contract_id, number, title, type (`labor` | `non_labor`), obligated_funding.
- **Employee** — id, name, role, lcat, hourly_bill_rate.
- **Allocation** — id, clin_id, employee_id, weekly_hours, start/end (optional).
- **Expense** (non-labor) — id, clin_id, description, amount, date. *(manual entry)*
- **Derived (computed, not stored):** spend-to-date, weekly burn rate, projected exhaustion
  date per CLIN, "days of runway remaining", tripwire status + suggested fix.

## 6. Key flows

1. **Ingest:** upload PDF → extract text → Claude returns structured CLINs/funding/dates →
   user confirms/edits → saved as Contract + CLINs.
2. **Plan:** add employees → assign to CLINs with weekly hours in the allocation matrix.
3. **Forecast:** burn engine computes, per CLIN, `obligated − spend` and weeks-to-exhaustion
   at current weekly burn; rolls up to contract-level "days of runway".
4. **Alert:** if any CLIN's projected exhaustion < period-of-performance end, raise a Tripwire
   with a Claude-generated fix (e.g. "swap 1 senior → junior = +21 weeks").

## 7. Look & feel

Runway brand palette pulled from the pitch deck: **purple** (structure/brand), **signature
yellow** (one hero moment + key callouts), **coral/mint/amber** (status), **periwinkle**
(plan line), **cream** ground; light + night themes via a Mantine theme file.

Reference mockup: https://claude.ai/code/artifact/a320ec1d-a8c5-4d90-adaa-25c1b320a1f8

**Deferred:** the card/layout composition in the mockup is placeholder-generic. Real layout
character (a proper burn gauge, a distinctive allocation matrix, non-boxy cards) is a polish
pass after the functional MVP works.

## 8. Build order

Scaffold → PDF ingest → employee pool → allocation matrix → burn dashboard → tripwire →
seed data → (stretch features). Each becomes one plan phase.

## 9. Open items / decisions still to make during build

- ORM choice (SQLModel vs SQLAlchemy) — decide at scaffold.
- Exact SF-26/SF-1449 fields Claude should target — refine against a real sample PDF.
- Layout/component polish pass (see §7).
