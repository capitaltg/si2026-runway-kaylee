import os
from typing import Optional

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import db, extract
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


@app.post("/api/contracts/ingest")
async def ingest(file: Optional[UploadFile] = File(default=None)):
    """Extract structured award data from an uploaded PDF, or the bundled sample."""
    if file is not None:
        data = await file.read()
        if (file.filename or "").lower().endswith(".pdf"):
            result = extract.extract_from_pdf(data)
        else:
            result = extract.extract_from_text(data.decode("utf-8", "ignore"))
    else:
        with open(SAMPLE, "r", encoding="utf-8") as f:
            result = extract.extract_from_text(f.read())
    return result.model_dump()


@app.post("/api/contracts/confirm")
def confirm(extraction: Extraction):
    cid = db.save_contract(extraction.contract.piid, extraction.model_dump())
    return {"id": cid, "piid": extraction.contract.piid}


@app.get("/api/contracts")
def contracts():
    return db.list_contracts()
