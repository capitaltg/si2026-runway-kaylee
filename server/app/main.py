import os
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import burn, db, extract, sources
from .schemas import Extraction

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
                result = extract.extract_from_pdf(data)
            else:
                result = extract.extract_from_text(data.decode("utf-8", "ignore"))
        elif SAMPLE.lower().endswith(".pdf"):
            with open(SAMPLE, "rb") as f:
                result = extract.extract_from_pdf(f.read())
        else:
            with open(SAMPLE, "r", encoding="utf-8") as f:
                result = extract.extract_from_text(f.read())
    except Exception as e:
        # Return a real error (with CORS headers) instead of an unhandled 500,
        # which Starlette leaves CORS-less so the browser reports "Load failed".
        raise HTTPException(status_code=502, detail=f"Extraction failed: {e}")
    return result.model_dump()


@app.post("/api/contracts/confirm")
def confirm(extraction: Extraction):
    cid = db.save_contract(extraction.contract.piid, extraction.model_dump())
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
            result = extract.extract_from_pdf(data)
        else:
            result = extract.extract_from_text(data.decode("utf-8", "ignore"))
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
    for one contract against its synced timesheets."""
    contract = db.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return burn.compute(contract, db.get_timesheets(contract_id))


@app.get("/api/portfolio")
def portfolio():
    """Cross-contract KPI aggregate + one summary card per contract."""
    pairs = [(c, db.get_timesheets(c["id"])) for c in db.list_contracts()]
    return burn.portfolio(pairs)
