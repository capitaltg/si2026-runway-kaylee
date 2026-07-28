import asyncio
import os
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import burn, db, extract, sources
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
def confirm(extraction: Extraction):
    data = extraction.model_dump()
    _seed_award_obligation(data)
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
    contract_id: int, rows: int = sources.DEMO_SYNC_ROWS, seed: int = 42
):
    """Pull a fresh timesheet batch from Fixtura and cache it against this
    contract. Delete-then-insert (via db.replace_timesheets) so a re-sync
    never double-counts hours."""
    if db.get_contract(contract_id) is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    try:
        ts = sources.fetch_timesheets(rows=rows, seed=seed)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Timesheet sync failed: {e}")
    stored = db.replace_timesheets(contract_id, ts)
    return {
        "id": contract_id,
        "rows": stored,
        "people": len({r.get("employee_id") for r in ts if r.get("employee_id")}),
        "weeks": len({r.get("week_ending") for r in ts if r.get("week_ending")}),
    }


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
    )


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
