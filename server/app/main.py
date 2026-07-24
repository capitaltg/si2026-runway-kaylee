import os
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import db, extract, sources
from .schemas import Extraction

SAMPLE = os.path.join(
    os.path.dirname(__file__), "..", "..", "sample-data", "contract-70RCSA26C0000123.md"
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
