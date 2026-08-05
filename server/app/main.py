import asyncio
import os
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import (
    absence,
    allocation,
    ask,
    burn,
    capacity,
    db,
    draft,
    extract,
    lcat,
    people,
    rates,
    sources,
)
from .schemas import Extraction, ExpenseIn

# The bundled award the "Ingest sample with AI" button reads when no file is
# uploaded. Points at the Fixtura burn-demo SF-26 so the one-click sample flows
# straight into a contract whose funded dollars trip the wire once timesheets
# sync — no file download required to demo the whole path.
SAMPLE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sample-data",
    "fixtura-runway-burn-demo.award.sf26.pdf",
)

app = FastAPI(title="Runway API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    db.init_db()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/sources")
def sources_list():
    """Step-1 connect-sources boxes. Fixtura is live-probed; the rest are
    honest 'Not connected' placeholders."""
    return sources.list_sources()


@app.post("/api/contracts/ingest")
async def ingest(file: Optional[UploadFile] = File(default=None)):
    """Extract structured award data from an uploaded PDF, or the bundled sample."""
    try:
        if file is not None:
            data = await file.read()
            if (file.filename or "").lower().endswith(".pdf"):
                result = await asyncio.to_thread(extract.extract_from_pdf, data)
            else:
                result = await asyncio.to_thread(
                    extract.extract_from_text, data.decode("utf-8", "ignore")
                )
        elif SAMPLE.lower().endswith(".pdf"):
            with open(SAMPLE, "rb") as f:
                result = await asyncio.to_thread(extract.extract_from_pdf, f.read())
        else:
            with open(SAMPLE, "r", encoding="utf-8") as f:
                result = await asyncio.to_thread(extract.extract_from_text, f.read())
    except Exception as e:
        # Return a real error (with CORS headers) instead of an unhandled 500,
        # which Starlette leaves CORS-less so the browser reports "Load failed".
        raise HTTPException(status_code=502, detail=f"Extraction failed: {e}")
    return result.model_dump()


def _seed_award_obligation(data: dict) -> None:
    """Seed the base-award funding as the first obligation_history entry, so the
    funding timeline starts where the money started rather than at the first mod.

    The award (SF-26 / SF-1449) carries only the initial obligation; SF-30 mods
    fold their later actions on top via _merge_mod. Without this seed the history
    began empty and the first timeline point was P00001 — the award baseline (and
    the true starting cumulative the mods build on) was lost. No-op when a history
    is already present or the award states no obligated amount."""
    if data.get("obligation_history"):
        return
    header = data.get("contract") or {}
    obligated = header.get("total_obligated")
    if obligated is None:
        return
    data["obligation_history"] = [
        {
            "mod": "Award",
            "date": header.get("effective_date"),
            "action": "Initial award / base-period funding",
            "amount": obligated,
            "cumulative_obligated": obligated,
            "description": None,
        }
    ]


@app.post("/api/contracts/confirm")
def confirm(extraction: Extraction, seed: Optional[int] = None):
    """Save a reviewed extraction as a contract. An optional Fixtura `seed`
    records which data batch this award was generated against, so its timesheet
    syncs stay coherent (see sync_timesheets' seed precedence)."""
    data = extraction.model_dump()
    _seed_award_obligation(data)
    if seed is not None:
        data["sync_seed"] = seed
    cid = db.save_contract(extraction.contract.piid, data)
    return {"id": cid, "piid": extraction.contract.piid}


@app.post("/api/contracts/{contract_id}/rates")
async def add_rate_schedule(contract_id: int, file: UploadFile = File(...)):
    """Supplemental import: attach a labor-rate schedule to an already-ingested
    contract. Some award forms print the CLIN summary on the face but carry the
    fully-burdened rates on a separate schedule (e.g. a 'Continuation of SF-1449,
    Schedule of Line Items and Pricing' sheet). When only the face was ingested,
    upload that schedule here and its rate tables are merged into the matching
    CLINs by CLIN number — so burn can compute exact per-LCAT spend."""
    existing = db.get_contract(contract_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Contract not found.")

    try:
        data = await file.read()
        if (file.filename or "").lower().endswith(".pdf"):
            result = await asyncio.to_thread(extract.extract_from_pdf, data)
        else:
            result = await asyncio.to_thread(
                extract.extract_from_text, data.decode("utf-8", "ignore")
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Extraction failed: {e}")

    parsed = result.model_dump()
    incoming = {
        (c.get("clin") or "").strip(): c["labor_rates"]
        for c in parsed.get("clins", [])
        if c.get("labor_rates")
    }
    if not incoming:
        raise HTTPException(
            status_code=422,
            detail="No labor rate table found in the uploaded schedule.",
        )

    # A schedule usually repeats the contract number; flag (don't block) a
    # mismatch, since some continuation sheets omit or abbreviate it.
    doc_piid = ((parsed.get("contract") or {}).get("piid") or "").strip()
    piid_mismatch = bool(doc_piid) and doc_piid != (existing.get("piid") or "").strip()

    merged = 0
    for clin in existing.get("clins", []):
        num = (clin.get("clin") or "").strip()
        if num in incoming:
            clin["labor_rates"] = incoming[num]
            merged += 1

    # Store back just the extraction blob (id / piid / created_at are columns).
    blob = {k: v for k, v in existing.items() if k not in ("id", "piid", "created_at")}
    db.update_contract(contract_id, blob)
    return {
        "id": contract_id,
        "clins_updated": merged,
        "rate_tables_found": len(incoming),
        "piid_mismatch": piid_mismatch,
    }


def _merge_mod(existing: dict, mod: dict) -> dict:
    """Fold one extracted SF-30 action into a contract's stored obligation
    history and refresh total_obligated. Pure (no I/O) so it's unit-testable.

    Idempotent by mod number: re-ingesting the same SF-30 replaces its entry
    rather than double-counting the dollars. total_obligated tracks the highest
    cumulative figure the mods state (funding is monotonic), falling back to the
    sum of per-action amounts when a doc omitted its running cumulative."""
    history = existing.get("obligation_history") or []
    entry = {
        "mod": mod.get("mod_number"),
        "date": mod.get("effective_date"),
        "action": mod.get("action_type") or "modification",
        "amount": mod.get("amount_obligated"),
        "cumulative_obligated": mod.get("cumulative_obligated"),
        "description": mod.get("description"),
    }
    by_num = {h.get("mod"): h for h in history}
    replaced = entry["mod"] in by_num
    by_num[entry["mod"]] = entry
    merged = sorted(
        by_num.values(), key=lambda h: (h.get("date") or "", h.get("mod") or "")
    )
    existing["obligation_history"] = merged

    header = existing.setdefault("contract", {})
    cums = [
        float(h["cumulative_obligated"])
        for h in merged
        if h.get("cumulative_obligated") is not None
    ]
    amts = [float(h["amount"]) for h in merged if h.get("amount") is not None]
    if cums:
        header["total_obligated"] = max(cums)
    elif amts:
        header["total_obligated"] = round(sum(amts), 2)
    ceiling = header.get("total_ceiling")
    if header.get("total_obligated") is not None and ceiling:
        header["incrementally_funded"] = float(header["total_obligated"]) < float(
            ceiling
        )
    return {
        "mod": entry["mod"],
        "replaced": replaced,
        "history_len": len(merged),
        "total_obligated": header.get("total_obligated"),
    }


@app.post("/api/contracts/{contract_id}/mods")
async def add_modification(contract_id: int, file: UploadFile = File(...)):
    """Ingest one SF-30 modification against an already-ingested contract. The
    dated funding action is folded into the contract's obligation history and
    total_obligated is refreshed, so the burn engine can read funding *pace*
    (obligations landing vs. dollars burned) — not just a single obligated
    figure. Ingest a contract's SF-30 stack one doc at a time to rebuild the
    full history; the SF-26 award already carries the initial obligation."""
    existing = db.get_contract(contract_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Contract not found.")

    try:
        data = await file.read()
        if (file.filename or "").lower().endswith(".pdf"):
            mod = await asyncio.to_thread(extract.extract_mod_from_pdf, data)
        else:
            mod = await asyncio.to_thread(
                extract.extract_mod_from_text, data.decode("utf-8", "ignore")
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Extraction failed: {e}")

    parsed = mod.model_dump()
    # A mod restates the contract number (block 10A); flag (don't block) a
    # mismatch, since OCR/extraction of that block can be imperfect.
    doc_piid = (parsed.get("piid") or "").strip()
    piid_mismatch = bool(doc_piid) and doc_piid != (existing.get("piid") or "").strip()

    summary = _merge_mod(existing, parsed)
    blob = {k: v for k, v in existing.items() if k not in ("id", "piid", "created_at")}
    db.update_contract(contract_id, blob)
    return {"id": contract_id, "piid_mismatch": piid_mismatch, **summary}


@app.get("/api/contracts/{contract_id}/funding")
def contract_funding(contract_id: int):
    """The contract's dated funding history — the SF-26 award plus every
    ingested SF-30 mod — with ceiling vs. obligated. Powers the Funding History
    view's timeline and progress bar; mods land here via POST .../mods."""
    contract = db.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    header = contract.get("contract") or {}
    return {
        "id": contract_id,
        "piid": contract.get("piid"),
        "name": header.get("contractor") or contract.get("piid"),
        "total_ceiling": header.get("total_ceiling"),
        "total_obligated": header.get("total_obligated"),
        "incrementally_funded": header.get("incrementally_funded"),
        "obligation_history": contract.get("obligation_history") or [],
    }


@app.get("/api/contracts")
def contracts():
    return db.list_contracts()


@app.post("/api/contracts/{contract_id}/timesheets/sync")
def sync_timesheets(
    contract_id: int, rows: int = sources.DEMO_SYNC_ROWS, seed: Optional[int] = None
):
    """Pull a fresh timesheet batch from Fixtura and cache it against this
    contract. Delete-then-insert (via db.replace_timesheets) so a re-sync
    never double-counts hours.

    Seed precedence: an explicit ?seed wins; otherwise the seed this contract was
    last synced with (persisted on its blob), otherwise the module default. So a
    contract keeps generating the *coherent* batch it was ingested against —
    different demo bundles carry different seeds, and the auto-sync (which passes
    no seed) reuses each contract's own seed instead of a single hardwired one."""
    contract = db.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    effective_seed = (
        seed
        if seed is not None
        else (
            contract.get("sync_seed")
            if contract.get("sync_seed") is not None
            else sources.DEFAULT_SYNC_SEED
        )
    )
    try:
        ts = sources.fetch_timesheets(rows=rows, seed=effective_seed)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Timesheet sync failed: {e}")
    stored = db.replace_timesheets(contract_id, ts)
    # Remember an explicitly chosen seed so future auto-syncs stay coherent.
    if seed is not None and contract.get("sync_seed") != seed:
        blob = {
            k: v for k, v in contract.items() if k not in ("id", "piid", "created_at")
        }
        blob["sync_seed"] = seed
        db.update_contract(contract_id, blob)
    return {
        "id": contract_id,
        "rows": stored,
        "people": len({r.get("employee_id") for r in ts if r.get("employee_id")}),
        "weeks": len({r.get("week_ending") for r in ts if r.get("week_ending")}),
    }


def _cost_model(contract_id: int) -> rates.CostModel:
    """The indirect-cost buildup in force for a contract (#77).

    Returns an empty model when the user has provided nothing, which is Level 1 and
    a fully supported state: billing burn, PoP clock and every tripwire work off the
    award alone, and the payload marks cost as `negotiated_fallback` rather than
    presenting billing dollars as cost. Nobody is ever required to upload salaries to
    use this app.
    """
    stored = db.get_rate_model(contract_id)
    return rates.model_from_rows(
        stored["pools"], stored["direct_rates"], scope=stored["scope"]
    )


@app.get("/api/contracts/{contract_id}/burn")
def contract_burn(contract_id: int):
    """Full Flight Deck payload: the active period's burn, runway and tripwires
    for one contract against its synced timesheets and logged non-labor actuals."""
    contract = db.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return burn.compute(
        contract,
        db.get_timesheets(contract_id),
        db.list_expenses(contract_id),
        _cost_model(contract_id),
    )


class RenameIn(BaseModel):
    """A user-chosen contract nickname. Empty/omitted clears it back to the legal
    name."""

    name: Optional[str] = None


@app.put("/api/contracts/{contract_id}/name")
def rename_contract(contract_id: int, body: RenameIn):
    """Set or clear a contract's nickname (a callsign like 'FALCON'). The nickname
    becomes the display name everywhere the burn payload feeds (sidebar, Flight
    Deck, Portfolio, allocation, Ask Runway)."""
    updated = db.rename_contract(contract_id, body.name)
    if updated is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return {
        "id": contract_id,
        "nickname": updated.get("nickname"),
        "piid": updated.get("piid"),
    }


@app.get("/api/contracts/{contract_id}/allocation")
def contract_allocation(contract_id: int):
    """Allocation matrix (#21): the employee x labor-CLIN hrs/wk grid for the
    active period, with each CLIN's budget/spend/clock, for the what-if simulator.
    The frontend edits cells and recomputes runway live; see allocation.py."""
    contract = db.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return allocation.compute_allocation(
        contract,
        db.get_timesheets(contract_id),
        db.list_expenses(contract_id),
        _cost_model(contract_id),
        db.expected_hours_by_person(),
    )


class CapacityIn(BaseModel):
    """A contract's expected-hours defaults (#84).

    `utilization_target` accepts a fraction (0.8) or a percentage (80); an empty string
    clears it back to the app default. `lcat_expected_hours` is the whole map and is
    replaced wholesale, so one category's default can be removed.
    """

    utilization_target: Optional[object] = None
    lcat_expected_hours: Optional[dict] = None


@app.put("/api/contracts/{contract_id}/capacity")
def set_contract_capacity(contract_id: int, body: CapacityIn):
    """Set a contract's utilisation target and per-LCAT expected hours (#84).

    Changing the target moves the forward projection, which is the point and is why
    this returns the refreshed contract rather than an ack — the same reasoning as the
    LCAT-alias endpoint. The matrix refetches its allocation after a save and every
    hrs/wk expectation, utilisation figure and projected runway moves with it.
    """
    target = body.utilization_target
    if target not in (None, ""):
        if capacity.target_hours(target) is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{target!r} is not a usable utilisation target. Give a fraction "
                    "like 0.8 or a percentage like 80."
                ),
            )
    for name, hours in (body.lcat_expected_hours or {}).items():
        if hours in (None, ""):
            continue
        problem = capacity.validate_expected_hours(str(hours))
        if problem:
            raise HTTPException(status_code=400, detail=f"{name}: {problem}")

    updated = db.set_contract_capacity(
        contract_id,
        utilization_target=target,
        lcat_expected_hours=body.lcat_expected_hours,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return updated


class AbsenceIn(BaseModel):
    """A contract's holiday calendar and per-person dated absences (#85).

    Each list is replaced wholesale; omitting one leaves it alone and sending an
    empty one clears it, the same convention `CapacityIn` uses. `seed_federal_year`
    is a convenience the editor calls instead of typing eleven dates — it *appends*
    to whatever holidays the request carries, so the seeded calendar is ordinary
    editable data from the moment it lands rather than a hidden built-in.
    """

    holidays: Optional[list] = None
    absences: Optional[list] = None
    seed_federal_year: Optional[int] = None


@app.get("/api/contracts/{contract_id}/absence")
def get_contract_absence(contract_id: int):
    """A contract's holiday calendar and per-person absences (#85)."""
    contract = db.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return absence.contract_absence(contract)


@app.put("/api/contracts/{contract_id}/absence")
def set_contract_absence(contract_id: int, body: AbsenceIn):
    """Set a contract's holidays and absences (#85).

    Returns the refreshed absence settings rather than an ack, for the reason the
    capacity endpoint does: entering an absence bends the forward projection, and
    the caller has to be able to show the number move.
    """
    for entry in body.absences or []:
        problem = absence.validate_absence(entry)
        if problem:
            raise HTTPException(status_code=400, detail=problem)

    holidays = body.holidays
    if body.seed_federal_year is not None:
        year = int(body.seed_federal_year)
        if not (1900 <= year <= 2200):
            raise HTTPException(
                status_code=400, detail=f"{year} is not a plausible calendar year."
            )
        holidays = list(holidays or []) + absence.federal_holidays(year)

    updated = db.set_contract_absence(
        contract_id, holidays=holidays, absences=body.absences
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return absence.contract_absence(updated)


class LcatAliasIn(BaseModel):
    """Point one timesheet LCAT at a rate line the award prices (#64).

    `source` is the LCAT as the timesheet spells it, `lcat` the rate line to bill
    it at, and `clin` the CLIN that rate line lives on — which may be a *different*
    CLIN than the one being charged. That cross-CLIN case is the whole reason this
    exists: an LCAT priced on 0002 and charged on 0003 is unmatchable by any amount
    of string cleverness, and only a human knows whether it's a data bug or the
    contract's actual intent.
    """

    source: str
    lcat: str
    clin: Optional[str] = None


def _lcat_gap_snapshot(contract_id: int) -> dict:
    """The rate-coverage part of the burn payload, per labor CLIN. Used to show what
    applying a mapping actually did — see `set_lcat_alias`."""
    contract = db.get_contract(contract_id)
    if contract is None:
        return {}
    b = burn.compute(
        contract, db.get_timesheets(contract_id), db.list_expenses(contract_id)
    )
    return {
        c["id"]: {
            "spent": c["spent"],
            "remaining": c["remaining"],
            "runway_days": c["runway_days"],
            "status": c["status"],
            "unmatched_lcats": c["unmatched_lcats"],
        }
        for c in b["clins"]
        if c.get("is_labor")
    }


@app.get("/api/contracts/{contract_id}/lcat-rates")
def lcat_rate_lines(contract_id: int):
    """Every rate line in play for a contract, plus its saved LCAT mappings (#64).

    What the mapping affordance offers as targets. Scoped to the active period's
    CLINs by `burn`, because a rate line on an un-exercised option year prices
    nothing today and picking it would map an LCAT onto a CLIN with no money on it.
    """
    contract = db.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    rows = db.get_timesheets(contract_id)
    period = burn._active_period(contract, rows)
    index = lcat.build_index(burn._period_clins(contract, period))
    lines = sorted(
        (line.payload() for entries in index.values() for line in entries),
        key=lambda p: (p["clin"], p["lcat"]),
    )
    return {
        "id": contract_id,
        "rate_lines": lines,
        "aliases": [
            a for a in (contract.get("lcat_aliases") or []) if isinstance(a, dict)
        ],
    }


@app.post("/api/contracts/{contract_id}/lcat-aliases")
def set_lcat_alias(contract_id: int, body: LcatAliasIn):
    """Save one LCAT → rate-line mapping and re-resolve burn against it.

    Returns the affected CLINs' spend/runway *before and after*, because a mapping
    that silently cleared a badge would be the dead-end ⚠ this ticket is about: the
    point of a mapping is that hours previously billed at the blended rate now bill
    at a real rate line, so the money moves, and the user has to be able to see by
    how much.
    """
    before = _lcat_gap_snapshot(contract_id)
    if not before and db.get_contract(contract_id) is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    updated = db.set_lcat_alias(contract_id, body.source, body.lcat, body.clin)
    if updated is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    after = _lcat_gap_snapshot(contract_id)
    return {
        "id": contract_id,
        "aliases": updated.get("lcat_aliases") or [],
        "before": before,
        "after": after,
    }


@app.delete("/api/contracts/{contract_id}/lcat-aliases")
def remove_lcat_alias(contract_id: int, source: str):
    """Drop one LCAT mapping. The LCAT goes back to whatever it resolved to before
    (usually the blended rate) and its flag comes back — undoing a mapping has to
    move the numbers the same way applying one does."""
    before = _lcat_gap_snapshot(contract_id)
    updated = db.delete_lcat_alias(contract_id, source)
    if updated is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return {
        "id": contract_id,
        "aliases": updated.get("lcat_aliases") or [],
        "before": before,
        "after": _lcat_gap_snapshot(contract_id),
    }


class PoolIn(BaseModel):
    """One indirect pool: fringe, overhead or G&A, as a decimal fraction."""

    pool: str
    rate: float
    base: Optional[str] = None


class DirectRateIn(BaseModel):
    """One direct (unburdened) labor rate. `lcat` is the privacy-preserving Level-2
    case — a category average, nobody named. `employee_id` is Level 3 and only ever
    arrives because a user chose to send it."""

    lcat: Optional[str] = None
    employee_id: Optional[str] = None
    rate: float


class RateModelIn(BaseModel):
    """A whole rate model for one fiscal year. Sending empty lists withdraws it and
    drops the contract back to Level 1 — deleting has to be as easy as providing."""

    fiscal_year: Optional[str] = None
    status: str = rates.PROVISIONAL
    pools: list[PoolIn] = []
    direct_rates: list[DirectRateIn] = []


def _rate_model_payload(contract_id: Optional[int]) -> dict:
    """Stored rows plus the derived model, so the panel can render the buildup
    without re-implementing the arithmetic in JavaScript."""
    stored = db.get_rate_model(contract_id)
    model = rates.model_from_rows(
        stored["pools"], stored["direct_rates"], scope=stored["scope"]
    )
    return {
        "contract_id": contract_id,
        "pools": stored["pools"],
        "direct_rates": stored["direct_rates"],
        "scope": stored["scope"],
        "model": model.payload(),
        # The derived buildup for each direct rate we hold — direct → fringe → OH →
        # G&A → total cost, layer by layer, so the panel can print the same ladder an
        # accountant would hand-work and the user can check it.
        "derived": [
            {
                "lcat": r.get("lcat"),
                "employee_id": r.get("employee_id"),
                "direct": r.get("rate"),
                **rates.burden(float(r.get("rate") or 0), model.rate_set).payload(),
            }
            for r in stored["direct_rates"]
        ],
    }


@app.get("/api/rate-model")
def company_rate_model():
    """The company-wide default indirect rates — set once, inherited by every
    contract that doesn't override a pool."""
    return _rate_model_payload(None)


@app.put("/api/rate-model")
def set_company_rate_model(body: RateModelIn):
    db.save_rate_pools(
        None, body.fiscal_year, [p.model_dump() for p in body.pools], body.status
    )
    db.save_direct_rates(
        None, body.fiscal_year, [d.model_dump() for d in body.direct_rates], body.status
    )
    return _rate_model_payload(None)


@app.get("/api/contracts/{contract_id}/rate-model")
def contract_rate_model(contract_id: int):
    """This contract's indirect rates and direct rates, with company defaults filling
    any pool it doesn't set itself."""
    if db.get_contract(contract_id) is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return _rate_model_payload(contract_id)


@app.put("/api/contracts/{contract_id}/rate-model")
def set_contract_rate_model(contract_id: int, body: RateModelIn):
    """Set (or clear) this contract's rates. Clearing falls back to the company
    default, and clearing both returns the contract to billing-only."""
    if db.get_contract(contract_id) is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    db.save_rate_pools(
        contract_id, body.fiscal_year, [p.model_dump() for p in body.pools], body.status
    )
    db.save_direct_rates(
        contract_id,
        body.fiscal_year,
        [d.model_dump() for d in body.direct_rates],
        body.status,
    )
    return _rate_model_payload(contract_id)


class PlanIn(BaseModel):
    """A saved allocation what-if plan: a name plus the opaque sim state the
    frontend needs to reload it (per-person hrs grid, planned adds, removals)."""

    name: str
    data: dict = {}


@app.get("/api/contracts/{contract_id}/plans")
def get_plans(contract_id: int):
    """A contract's saved allocation plans, newest first, with their full state."""
    if db.get_contract(contract_id) is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return db.list_plans(contract_id)


@app.post("/api/contracts/{contract_id}/plans")
def create_plan(contract_id: int, body: PlanIn):
    """Save a named allocation what-if plan for later reload."""
    if db.get_contract(contract_id) is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    name = (body.name or "").strip() or "Untitled plan"
    return db.save_plan(contract_id, name, body.data)


@app.put("/api/contracts/{contract_id}/plans/{plan_id}")
def replace_plan(contract_id: int, plan_id: int, body: PlanIn):
    """Save over an existing plan — editing a loaded plan updates it, not forks it."""
    if db.get_contract(contract_id) is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    name = (body.name or "").strip() or "Untitled plan"
    row = db.update_plan(contract_id, plan_id, name, body.data)
    if row is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    return row


@app.delete("/api/contracts/{contract_id}/plans/{plan_id}")
def remove_plan(contract_id: int, plan_id: int):
    """Delete one saved allocation plan."""
    if not db.delete_plan(contract_id, plan_id):
        raise HTTPException(status_code=404, detail="Plan not found.")
    return {"deleted": plan_id}


def _all_allocations() -> list:
    """One allocation payload per contract. The expensive sweep — a burn pass each —
    behind both portfolio utilisation and conflicts."""
    # One query for every per-person expected week (#84), not one per contract.
    overrides = db.expected_hours_by_person()
    return [
        allocation.compute_allocation(
            c,
            db.get_timesheets(c["id"]),
            db.list_expenses(c["id"]),
            expected_hours_by_person=overrides,
        )
        for c in db.list_contracts()
    ]


@app.get("/api/allocation/conflicts")
def allocation_conflicts():
    """Portfolio resource conflicts: people booked past a full 40-hr week once
    their hours are summed across every contract. Matches on employee_id, so it
    only surfaces real overlaps (e.g. a shared roster) — never double-counts one
    person on one contract.

    Now a filter over `people.utilization` (#69) rather than its own walk — it was
    computing everyone's cross-contract hours and discarding all the non-conflicts.
    Payload is unchanged.
    """
    rows = people.utilization(_all_allocations())["people"]
    conflicts = people.conflicts(rows)
    return {"count": len(conflicts), "conflicts": conflicts}


# --- People directory (#69) -------------------------------------------------


class PersonIn(BaseModel):
    """A person added by hand.

    `employee_id` is optional but wanted: give Runway the real payroll id and this
    person links up to their own timesheets automatically the first time a feed
    carries them, instead of forking into a second profile. Left blank, Runway mints
    a visibly provisional RW-#### id so not knowing it can't block the add.
    """

    name: str
    employee_id: Optional[str] = None


class QualsIn(BaseModel):
    """One person's qualification assertions, as `{field: {value, source_note}}`.

    Partial: only the fields present are touched. A blank value clears that field
    back to `unknown`, which has to stay reachable or "optional" isn't true.
    """

    quals: dict
    authored_by: Optional[str] = None


class MergeIn(BaseModel):
    """Fold a provisional hand-added person into a real employee id."""

    into: str


@app.get("/api/people")
def people_directory():
    """The app-wide people directory.

    Populated on day one with no setup: identity and charging history are derived
    live from the timesheet cache, so everyone who has ever charged is already here.
    Quals are the only authored part and are always optional — `unknown` is a normal,
    supported state, not a prompt to go find a file.

    Carries no hours and no money. Utilisation needs a burn pass per contract, so it
    lives at /api/people/utilization and the view asks for it only when wanted.
    """
    return people.build_directory(
        facts=db.people_charging_facts(),
        contracts=db.list_contracts(),
        manual_people=db.list_manual_people(),
        attr_rows=db.list_person_attrs(),
        unidentified=db.unidentified_timesheet_rows(),
    )


@app.get("/api/people/utilization")
def people_utilization():
    """Everyone's hours summed across every contract they charge. On demand — this
    is the costly half of the directory (a burn pass per contract)."""
    return people.utilization(_all_allocations())


@app.post("/api/people")
def add_person(body: PersonIn):
    """Add a person by hand — someone with no charges yet, e.g. a planned hire.

    They are labelled `manual` and, per this feature's invariant, appear only as a
    pickable candidate: a person with no timesheet hours on a contract never shows
    up on that contract's allocation matrix.
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="A name is required.")
    typed = (body.employee_id or "").strip()
    if typed and typed in db.person_charged_ids():
        raise HTTPException(
            status_code=409,
            detail=f"{typed} already charges time — they're already in the directory.",
        )
    row = db.add_manual_person(typed, name)
    if row is None:
        raise HTTPException(status_code=409, detail=f"{typed} is already in use.")
    return row


@app.put("/api/people/{employee_id}/quals")
def save_person_quals(employee_id: str, body: QualsIn):
    """Type in (or clear) one person's qualifications.

    This is the floor of the feature and is never gated behind anything — no import,
    no setup. Unknown fields are rejected rather than stored, so the attrs table
    can't become arbitrary key-value storage.

    Values are checked too, not only field names (#98). A dropdown constrains one
    client; this is the contract #66 will trust when it compares a person's
    credentials against a category's floor.
    """
    incoming = body.quals or {}
    # What is already on file, so a value predating the vocabularies isn't a trap —
    # see people.validate_quals for why an unchanged one is let through.
    stored = {a["field"]: a["value"] for a in db.list_person_attrs(employee_id)}
    problem = people.validate_quals(incoming, stored)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    attrs = db.save_person_attrs(employee_id, incoming, body.authored_by)

    def _entries(fields) -> dict:
        return {
            a["field"]: {
                "value": a["value"],
                "source_note": a["source_note"],
                "authored_by": a["authored_by"],
                "authored_at": a["authored_at"],
            }
            for a in attrs
            if a["field"] in fields
        }

    # Expected hours shares this table and this endpoint but is not a qualification —
    # split back out so nothing downstream reads a part-time week as a credential (#84).
    quals = _entries(people.QUAL_FIELDS + people.CONTEXT_FIELDS)
    return {
        "employee_id": employee_id,
        "quals": quals,
        "quals_status": people.quals_status(quals),
        "capacity": _entries(people.CAPACITY_FIELDS),
    }


@app.post("/api/people/{employee_id}/merge")
def merge_provisional_person(employee_id: str, body: MergeIn):
    """Fold a provisional hand-added person into the real employee id a feed now carries.

    Only ever offered as a suggestion the user confirms — the match behind it is on
    name, and a name match is not an identity match.
    """
    into = (body.into or "").strip()
    if not into:
        raise HTTPException(status_code=400, detail="A target employee id is required.")
    if not db.merge_person(employee_id, into):
        raise HTTPException(
            status_code=404,
            detail="No provisional person with that id to merge.",
        )
    return {"merged": employee_id, "into": into}


@app.delete("/api/people/{employee_id}")
def remove_person(employee_id: str):
    """Remove a manually-added person and their quals.

    Refuses anyone with timesheet hours: their presence in the directory is a fact
    about the feed, and the directory has no authority to overrule it.
    """
    if employee_id in db.person_charged_ids():
        raise HTTPException(
            status_code=409,
            detail="This person charges time — the timesheet feed owns their record.",
        )
    if not db.delete_manual_person(employee_id):
        raise HTTPException(status_code=404, detail="Person not found.")
    return {"deleted": employee_id}


@app.get("/api/portfolio")
def portfolio():
    """Cross-contract KPI aggregate + one summary card per contract."""
    pairs = [
        (c, db.get_timesheets(c["id"]), db.list_expenses(c["id"]))
        for c in db.list_contracts()
    ]
    return burn.portfolio(pairs)


@app.get("/api/contracts/{contract_id}/expenses")
def get_expenses(contract_id: int, clin: Optional[str] = None):
    """Logged non-labor actuals for a contract, optionally scoped to one CLIN."""
    if db.get_contract(contract_id) is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return db.list_expenses(contract_id, clin)


@app.post("/api/contracts/{contract_id}/expenses")
def create_expense(contract_id: int, body: ExpenseIn):
    """Log one non-labor actual (travel / ODC / materials / sub). It rolls into
    the CLIN's burn on the next burn read."""
    if db.get_contract(contract_id) is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return db.add_expense(
        contract_id,
        body.clin.strip(),
        body.date,
        body.description,
        body.category,
        body.amount,
    )


@app.delete("/api/contracts/{contract_id}/expenses/{expense_id}")
def remove_expense(contract_id: int, expense_id: int):
    """Delete one logged expense."""
    if not db.delete_expense(contract_id, expense_id):
        raise HTTPException(status_code=404, detail="Expense not found.")
    return {"deleted": expense_id}


class AskIn(BaseModel):
    """One Ask Runway turn. `history` is the prior conversation (user/assistant
    turns) for follow-up drill-downs; `contract_id` is the contract the user
    currently has open, so 'this contract' resolves to it."""

    question: str
    history: list[dict] = []
    contract_id: Optional[int] = None


@app.post("/api/ask")
def ask_runway(body: AskIn):
    """Ask Runway (#15): a natural-language answer grounded in the burn engine's
    numbers, streamed back as plain text so the chat feels live. The model never
    recomputes — it reasons over the portfolio + per-contract burn payloads that
    ask.build_grounding assembles (see ask.py)."""
    q = (body.question or "").strip()
    if not q:
        raise HTTPException(status_code=422, detail="Ask a question.")

    def gen():
        try:
            yield from ask.stream_answer(q, body.history, body.contract_id)
        except Exception as e:
            # The stream has already started (200 + headers sent), so surface the
            # failure inline in the answer text rather than as an HTTP error the
            # frontend can no longer catch.
            yield f"\n\n[Ask Runway hit an error: {e}]"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


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
