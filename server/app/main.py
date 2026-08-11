import asyncio
import io
import os
import re
from datetime import date
from typing import Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from . import (
    absence,
    allocation,
    ask,
    burn,
    capacity,
    db,
    documents,
    draft,
    extract,
    heat,
    lcat,
    people,
    periods as period_ids,
    pricing,
    rates,
    sources,
    suggest,
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


def _keep_source(
    contract_id: Optional[int],
    kind: str,
    filename: Optional[str],
    blob: bytes,
    declared_type: Optional[str] = None,
) -> tuple:
    """Store one upload as a contract's source document (#30).

    Returns `(document_id, note)` — exactly one of which is set. The note is the
    reason a file was not kept, and callers pass it back to the client rather than
    swallowing it: a dashboard whose source silently failed to store looks exactly
    like one whose source is on file, which is the opposite of the point.

    Called only *after* a successful extraction, so a document is never left behind
    by an upload that produced no contract.
    """
    note = documents.rejection(filename, blob)
    if note:
        return None, note
    row = db.save_document(
        contract_id,
        kind,
        documents.safe_filename(filename),
        documents.content_type(filename, declared_type),
        blob,
    )
    return row["id"], None


@app.post("/api/contracts/ingest")
async def ingest(file: Optional[UploadFile] = File(default=None)):
    """Extract structured award data from an uploaded PDF, or the bundled sample.

    The uploaded bytes are kept (#30) so the numbers on the Flight Deck stay
    checkable against the award they came from. They are stored unattached and the
    id is handed back for `confirm` to claim, because the contract that will own
    them does not exist until the user has reviewed the extraction — see the
    `contract_documents` schema note.
    """
    # Sweep uploads whose review screen was closed without confirming. Done here
    # rather than on a timer because this app has no scheduler, and a new ingest is
    # both the cheapest and the most likely moment for stale ones to have piled up.
    db.purge_unclaimed_documents()

    source_name = os.path.basename(SAMPLE)
    declared_type = None
    try:
        if file is not None:
            data = await file.read()
            source_name, declared_type = file.filename, file.content_type
            if (file.filename or "").lower().endswith(".pdf"):
                result = await asyncio.to_thread(extract.extract_from_pdf, data)
            else:
                result = await asyncio.to_thread(
                    extract.extract_from_text, data.decode("utf-8", "ignore")
                )
        elif SAMPLE.lower().endswith(".pdf"):
            with open(SAMPLE, "rb") as f:
                data = f.read()
            result = await asyncio.to_thread(extract.extract_from_pdf, data)
        else:
            with open(SAMPLE, "rb") as f:
                data = f.read()
            result = await asyncio.to_thread(
                extract.extract_from_text, data.decode("utf-8", "ignore")
            )
    except Exception as e:
        # Return a real error (with CORS headers) instead of an unhandled 500,
        # which Starlette leaves CORS-less so the browser reports "Load failed".
        raise HTTPException(status_code=502, detail=f"Extraction failed: {e}")

    # The bundled sample is stored too, not special-cased away: the demo path is how
    # most people first see the Flight Deck, and a source panel that is empty there
    # teaches everyone that the feature doesn't work.
    document_id, note = _keep_source(
        None, documents.AWARD, source_name, data, declared_type
    )
    return {
        **result.model_dump(),
        "source_document_id": document_id,
        "source_document_note": note,
    }


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


def _fiscal_year(iso_date: Optional[str]) -> Optional[str]:
    """The federal fiscal year an ISO date falls in — FY runs Oct 1 to Sep 30, so
    October onward belongs to the next calendar year's FY. None when the date is
    absent or unparseable: a rate set with no year is still storable (fiscal_year is
    nullable) and is better than one filed under a guessed year, which #87 would
    later true up against the wrong incurred-cost submission."""
    try:
        d = date.fromisoformat((iso_date or "")[:10])
    except ValueError:
        return None
    return str(d.year + 1 if d.month >= 10 else d.year)


def _store_face_rates(contract_id: int, header: dict) -> Optional[str]:
    """Persist the indirect rates read off the award face as this contract's rate
    set (#78 slice 3a).

    The award states the rates but not their application base or status, so the
    conventional bases (`rates.DEFAULT_BASES`) and `provisional` fill in — a rate
    the government agreed to bill at is provisional until an incurred-cost
    submission settles it, and calling it final here would let #87 skip a true-up
    that is genuinely owed. An FPRA upload overwrites both, being the document that
    actually states them.

    Returns (stored?, fiscal year). The two are separate answers, not one: an award
    can print its rates and still leave the fiscal year unknown (an unparseable
    effective date), and reporting that as "nothing stored" would hide a rate set
    the app is now pricing with.
    """
    pools = [
        {"pool": pool, "rate": header.get(key), "base": rates.DEFAULT_BASES[pool]}
        for pool, key in (
            (rates.FRINGE, "indirect_fringe"),
            (rates.OVERHEAD, "indirect_overhead"),
            (rates.GNA, "indirect_gna"),
        )
        if header.get(key) is not None
    ]
    if not pools:
        return False, None
    fy = _fiscal_year(header.get("effective_date"))
    db.save_rate_pools(contract_id, fy, pools, rates.PROVISIONAL)
    return True, fy


@app.post("/api/contracts/confirm")
def confirm(
    extraction: Extraction,
    seed: Optional[int] = None,
    opts: Optional[str] = None,
    document_id: Optional[int] = None,
):
    """Save a reviewed extraction as a contract. An optional Fixtura `seed`
    records which data batch this award was generated against, so its timesheet
    syncs stay coherent (see sync_timesheets' seed precedence).

    Recording it matters more than "optional" suggests, which is why the saved value
    comes back in the response for the review screen to confirm: with no seed, a sync
    falls back to a hash of the PIID, and that draws a different contract's award and
    roster. Those rows are now refused at the sync rather than stored — so an award
    ingested without its seed is one whose first sync will stop and ask for it.

    `opts` is the other half of that pairing (#136), and a seed alone is not enough to
    reproduce an award: Fixtura builds the PIID's fiscal-year digits from the award's
    effective date, and `pop_in_progress` moves that date back a year per preceding
    option period — so one seed generates `-24-` as a historical contract and `-25-`
    as an in-progress one. Left blank, the first sync DERIVES opts from the award,
    which is a guess, and a guess that lands on the other spelling produces a contract
    permanently unable to sync: every row it draws reads as a stranger's labor. Stated
    here, the pairing is recorded rather than re-derived. Accepts `key=value` pairs or
    a JSON object; an unknown knob is a 400 rather than a knob silently ignored.

    `document_id` is the upload ingest stashed for this extraction (#30); confirming
    is what attaches it to the contract. Optional, and a stale or already-claimed id
    is reported rather than raising — manual entry has no document at all, and a
    contract must save either way. The source panel is evidence, not a gate.
    """
    data = extraction.model_dump()
    _seed_award_obligation(data)
    if seed is not None:
        data["sync_seed"] = seed
    try:
        stated_opts = sources.parse_opts(opts)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if stated_opts:
        data["sync_opts"] = stated_opts
    cid = db.save_contract(extraction.contract.piid, data)
    stored = db.claim_document(document_id, cid) if document_id is not None else False
    rates_stored, rate_fy = _store_face_rates(cid, data.get("contract") or {})
    # The other half of the same reading (#138). An award that prints its own cost
    # buildup states a direct rate per labor category as well as the indirect
    # factors, and both were extracted — storing only the percentages left the
    # contract pinned at billing-only and forced the user to re-upload the very PDF
    # they had just ingested through the supplemental rate-schedule button.
    direct_stored = _store_schedule_direct_rates(
        cid, data.get("contract") or {}, data.get("clins") or []
    )
    return {
        "id": cid,
        "piid": extraction.contract.piid,
        "source_document_stored": stored,
        # Echoed so the review screen can say the batch was recorded — a silently
        # dropped seed and no seed at all look identical until the first sync fails.
        "sync_seed": data.get("sync_seed"),
        # Same reason, for the other half of the pairing: the review screen has to be
        # able to show that the opts it sent are the opts the first sync will replay.
        "sync_opts": data.get("sync_opts"),
        # Named so the UI can say the cost model was populated from the award rather
        # than leave the user wondering why the rates view is suddenly non-empty.
        "indirect_rates_stored": rates_stored,
        "indirect_rates_fiscal_year": rate_fy,
        # Zero on a fixed-price award, which prices the work without disclosing what
        # it costs us — that is Level 1 by nature, not a failed read.
        "direct_rates_stored": direct_stored,
    }


def _store_schedule_direct_rates(contract_id: int, header: dict, clins: list) -> int:
    """Persist any unburdened direct rates a cost-buildup exhibit printed (#78).

    This is what actually moves a contract off Level 1: `rates.CostModel` has read
    `direct_rates` since #77, but nothing ever wrote to that table except a human
    typing into the rates view. A cost-type exhibit prints the direct rate per labor
    category, so ingesting one should be enough to make margin real.

    Merged, not replaced. `save_direct_rates` is a delete-then-insert over the whole
    (scope, fiscal year), so writing only what this sheet carried would silently drop
    the per-person rates behind Level 3 — a schedule upload must never cost someone
    their payroll-grade cost model. Rows for an LCAT this sheet *does* price are
    overwritten, which is the one thing the document is more authoritative about.

    Returns how many LCAT direct rates the sheet supplied.
    """
    incoming = {}
    for cl in clins or []:
        for r in cl.get("labor_rates") or []:
            name, rate = (r.get("lcat") or "").strip(), r.get("direct_rate")
            if name and rate is not None:
                incoming[name] = float(rate)
    if not incoming:
        return 0

    fy = _fiscal_year(header.get("effective_date"))
    # Compared on the normalised key so a sheet naming a category "Sr. Software
    # Engineer" replaces the "Senior Software Engineer" row it means, instead of
    # sitting beside it as a second answer for the same category (#64).
    replaced = {lcat.normalize(k) for k in incoming}
    keep = [
        r
        for r in db.get_scoped_direct_rates(contract_id)
        if r.get("employee_id") or lcat.normalize(r.get("lcat")) not in replaced
    ]
    db.save_direct_rates(
        contract_id,
        fy,
        keep + [{"lcat": k, "rate": v} for k, v in incoming.items()],
        rates.PROVISIONAL,
    )
    return len(incoming)


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

    # Keep the schedule itself (#30). The rates it just merged are the figures an
    # accountant is least willing to take on faith, and this upload was the only
    # copy Runway ever saw. Stored after the merge, so a schedule that parsed to
    # nothing (the 422 above) leaves no document implying it did.
    document_id, note = _keep_source(
        contract_id,
        documents.RATE_SCHEDULE,
        file.filename,
        data,
        file.content_type,
    )
    # A loaded-rate-only sheet writes nothing here and prices exactly as it did
    # before: no direct rate means no cost of our own to compare the price against,
    # which is Level 1 and the normal case, not a degraded one.
    direct_stored = _store_schedule_direct_rates(
        contract_id, existing.get("contract") or {}, existing.get("clins") or []
    )

    return {
        "id": contract_id,
        "clins_updated": merged,
        "rate_tables_found": len(incoming),
        "direct_rates_stored": direct_stored,
        "piid_mismatch": piid_mismatch,
        "source_document_id": document_id,
        "source_document_note": note,
    }


def _agreement_pools(pools) -> list:
    """The storable rows from an extracted rate agreement, dropping anything that
    isn't one of the three pools we can apply.

    Tolerant on the base and strict on the pool: an unrecognised base falls back to
    that pool's conventional one (`rates.burden` does the same, so a typo cannot
    silently delete a pool from the cost), but a row naming a pool we have no
    arithmetic for is skipped rather than guessed at — a fourth pool applied to the
    wrong base is worse than a fourth pool we admit we did not read.
    """
    out = []
    for p in pools or []:
        name = (p.pool or "").strip().lower()
        if name not in rates.POOLS or p.rate is None:
            continue
        base = (p.base or "").strip()
        out.append(
            {
                "pool": name,
                "rate": float(p.rate),
                "base": (
                    base
                    if base in rates.DEFAULT_BASES.values()
                    else rates.DEFAULT_BASES[name]
                ),
            }
        )
    return out


@app.post("/api/contracts/{contract_id}/rate-agreement")
async def add_rate_agreement(contract_id: int, file: UploadFile = File(...)):
    """Supplemental import: attach an indirect rate agreement to a contract (#78).

    The award face states the three percentages; this document states what they
    *are* — each pool's application base, the fiscal year, and whether the rates are
    provisional billing rates (FAR 42.704) or a final determination (FAR 42.705). So
    it overwrites what the face read, being the authority on its own subject.

    One documented limitation: `rate_sets` keys on (scope, fiscal year) with status as
    a column, so it cannot hold a provisional AND a final set for the same year at
    once. When a letter prints both, the FINAL set is stored — it is what the year
    settled to, and pricing against a superseded provisional rate would be wrong on
    purpose — and the response reports that a determination was found. Keeping both
    sets is #87's job, which is the ticket that trues one up against the other and
    needs the schema change anyway.
    """
    existing = db.get_contract(contract_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Contract not found.")

    try:
        data = await file.read()
        if (file.filename or "").lower().endswith(".pdf"):
            parsed = await asyncio.to_thread(
                extract.extract_rate_agreement_from_pdf, data
            )
        else:
            parsed = await asyncio.to_thread(
                extract.extract_rate_agreement_from_text, data.decode("utf-8", "ignore")
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Extraction failed: {e}")

    parsed_status = (parsed.status or "").strip().lower()
    single_final = parsed_status in {"final", "actual"} and not parsed.final_pools
    provisional = [] if single_final else _agreement_pools(parsed.pools)
    final = _agreement_pools(parsed.final_pools)
    if single_final:
        final = _agreement_pools(parsed.pools)
    if not provisional and not final:
        raise HTTPException(
            status_code=422,
            detail="No fringe, overhead or G&A rates found in the uploaded document.",
        )

    # A letter with no fiscal year still stores, under a null year, for the same
    # reason the award face does: the rates are the hard-won figures and refusing to
    # keep them because a year was unreadable throws away the whole upload.
    fy = (parsed.fiscal_year or "").strip() or None
    stored, status = (
        (final, rates.ACTUAL) if final else (provisional, rates.PROVISIONAL)
    )
    db.save_rate_pools(contract_id, fy, stored, status)

    # A company-wide letter names no contract, so a missing PIID is not a mismatch.
    doc_piid = (parsed.piid or "").strip()
    piid_mismatch = bool(doc_piid) and doc_piid != (existing.get("piid") or "").strip()

    document_id, note = _keep_source(
        contract_id,
        documents.RATE_AGREEMENT,
        file.filename,
        data,
        file.content_type,
    )
    return {
        "id": contract_id,
        "fiscal_year": fy,
        "status": status,
        "pools_stored": len(stored),
        # Said out loud: the upload carried a provisional set too, and this response
        # is the only place that fact survives until #87 can store both.
        "final_determination_found": status == rates.ACTUAL,
        "provisional_pools_found": len(provisional),
        "cognisant_agency": parsed.cognisant_agency,
        "determination_date": parsed.determination_date,
        "piid_mismatch": piid_mismatch,
        "source_document_id": document_id,
        "source_document_note": note,
    }


@app.get("/api/contracts/{contract_id}/documents")
def contract_documents(contract_id: int):
    """The source documents behind this contract's numbers (#30) — metadata only.

    An empty list is a normal answer, not an error: every contract ingested before
    this feature existed, and every one typed in by hand, has no stored source. The
    panel says so plainly rather than implying something is missing.
    """
    if db.get_contract(contract_id) is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return {"id": contract_id, "documents": db.list_documents(contract_id)}


@app.get("/api/contracts/{contract_id}/documents/{document_id}")
def contract_document(contract_id: int, document_id: int):
    """Serve one stored source document back for viewing or download.

    `inline` so a PDF opens in the browser's viewer next to the dashboard — checking
    an extracted number against the award is a side-by-side act, and forcing a
    download to do it is friction on the one workflow this feature exists for.
    """
    row = db.get_document(contract_id, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    name = documents.safe_filename(row["filename"])
    return Response(
        content=row["blob"],
        media_type=row["content_type"] or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{name}"',
            # The hash an auditor can recompute against their own copy, without
            # having to download it through this app to find it.
            "X-Document-SHA256": row["sha256"] or "",
        },
    )


# One definition of "same period" / "same CLIN", shared with burn.py — see
# app/periods.py for why they no longer live here.
_clin_key = period_ids.clin_key


def _acrn_list(value) -> List[str]:
    """The accounting citations already on a CLIN, as a list.

    One CLIN can be funded from several appropriations, so `acrn` holds a
    comma-separated citation list rather than a single symbol (#61). Reading it back
    through here keeps the stored form — a plain string, which every existing
    contract, payload and view already handles — while letting the merge below be a
    union instead of an overwrite.
    """
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


_period_key = period_ids.key


# "CLIN 0001 (ACRN AA) $950,000.00" — the per-CLIN split an SF-30 states in its
# Block 14 narrative. The ACRN is optional; agencies word the surrounding
# sentence differently, but the CLIN-then-dollars pairing is near-universal.
_FUNDING_LINE_RE = re.compile(
    r"CLIN\s+([0-9A-Z]{4})\s*(?:\(\s*ACRN\s+([0-9A-Z]{2})\s*\))?\s*"
    r"[:\-]?\s*\$\s*([\d,]+(?:\.\d{2})?)",
    re.IGNORECASE,
)


# Block 14 numbers its clauses "(a) … (b) …". Anchor on the clause that
# introduces a per-CLIN list and read to the next clause marker, so a mod that
# states BOTH its funding split and revised ceilings doesn't blend the two lists.
_FUNDING_CLAUSE_RE = re.compile(r"obligated\s+by\s+CLIN[^:]*:", re.IGNORECASE)
_CEILING_CLAUSE_RE = re.compile(
    r"ceilings?\s+(?:are\s+)?(?:revised|increased|established)\s+by\s+CLIN[^:]*:",
    re.IGNORECASE,
)
_CLAUSE_END_RE = re.compile(r"\([a-z]\)")


def _clause_after(text: Optional[str], marker: re.Pattern) -> Optional[str]:
    """The text a numbered Block 14 clause introduces, up to the next clause."""
    if not text:
        return None
    hit = marker.search(text)
    if not hit:
        return None
    rest = text[hit.end() :]
    end = _CLAUSE_END_RE.search(rest)
    return rest[: end.start()] if end else rest


def _parse_funding_lines(text: Optional[str]) -> List[dict]:
    """Recover a mod's per-CLIN funding split from its narrative text.

    The extractor is asked for `funding_lines` directly and usually supplies
    them, so this is a fallback rather than the primary read — but it is the one
    that keeps a mod's dollars from landing nowhere when the model returns null,
    which it does often enough to matter (that field is the schema's only nested
    object list, and constrained decoding is least reliable exactly there).

    Anchored on the funding clause when the narrative numbers its clauses; when
    it doesn't, the whole text is scanned and the caller's reconciliation check
    has to vouch for the result."""
    text = _clause_after(text, _FUNDING_CLAUSE_RE) or text
    if not text:
        return []
    lines = []
    for clin, acrn, amount in _FUNDING_LINE_RE.findall(text):
        entry = {"clin": clin, "amount": float(amount.replace(",", ""))}
        if acrn:
            entry["acrn"] = acrn.upper()
        lines.append(entry)
    return lines


def _parse_ceiling_lines(text: Optional[str]) -> List[dict]:
    """Per-CLIN not-to-exceed ceilings a mod restates.

    Only ever read from an explicit ceilings clause: unlike funding, a ceiling is
    a restatement rather than an increment, so mistaking one for the other would
    either erase a line's ceiling or inflate it."""
    clause = _clause_after(text, _CEILING_CLAUSE_RE)
    return [
        {"clin": clin, "ceiling": float(amount.replace(",", ""))}
        for clin, _acrn, amount in _FUNDING_LINE_RE.findall(clause or "")
    ]


def _funding_lines_for(mod: dict, document_text: Optional[str] = None) -> List[dict]:
    """The per-CLIN split for one mod when the extractor didn't supply one.

    The document's own text is read before the model's `description`. That order
    is not cosmetic: on one SF-30 the model transcribed a $5.6M revised ceiling
    as $5.8M, and nothing downstream could have caught it. What the PDF says is a
    fact; what the model says it says is a reading.

    A parsed split is only trusted when its lines add up to the dollars the mod
    says it obligated. A document repeats CLIN figures — accounting block,
    schedule, narrative — and a scrape that swept up two mentions of the same
    line would silently double that CLIN's funding. Failing the check leaves the
    funding unattributed, which is the honest outcome: visibly missing beats
    quietly wrong."""
    stated = mod.get("amount_obligated")
    for text in (document_text, mod.get("description")):
        lines = _parse_funding_lines(text)
        if not lines:
            continue
        if stated is None:
            return lines
        if abs(sum(line["amount"] for line in lines) - float(stated)) < 0.01:
            return lines
    return []


def _apply_ceiling_lines(existing: dict) -> List[str]:
    """Adopt the newest ceiling each CLIN has been restated at. Returns the CLINs
    whose ceiling moved.

    A mod that raises the not-to-exceed value changes what the burn engine is
    measuring against — ignore it and a contract stays red against a ceiling its
    contracting officer already lifted. Latest statement wins (ceilings are
    restated, not accumulated), and a mod that says nothing changes nothing."""
    latest: Dict[str, float] = {}
    for h in existing.get("obligation_history") or []:
        for line in h.get("ceiling_lines") or []:
            key = _clin_key(line.get("clin"))
            if key and line.get("ceiling") is not None:
                latest[key] = float(line["ceiling"])
    if not latest:
        return []

    revised = []
    for clin in existing.get("clins") or []:
        key = _clin_key(clin.get("clin"))
        if key in latest and clin.get("ceiling") != latest[key]:
            clin["ceiling"] = latest[key]
            revised.append(clin.get("clin"))
    # A period carries its own ceiling; keep the one holding this CLIN in step so
    # the period bar and the CLIN row can't disagree.
    for period in existing.get("periods") or []:
        members = [
            c
            for c in existing.get("clins") or []
            if (c.get("period") or "") == (period.get("name") or "")
        ]
        if members and all(c.get("ceiling") is not None for c in members):
            period["ceiling"] = round(sum(float(c["ceiling"]) for c in members), 2)
    return revised


def _apply_clin_funding(existing: dict) -> int:
    """Rebuild every CLIN's `obligated` from the award baseline plus the per-CLIN
    funding lines of every mod ingested so far. Returns the CLIN count touched.

    Why a full recompute rather than adding this mod's lines to what is stored:
    `obligated` is a single cumulative number, so incrementing it in place is not
    idempotent — re-ingesting P00002 (a normal thing to do, the endpoint is
    replace-by-mod-number) would fund those CLINs twice. Recomputing from the
    award figure plus the whole history is idempotent by construction, and it is
    also the only way a mod *correction* can lower a CLIN back down.

    `funded_at_award` holds what the award's own signature obligated. It is
    snapshotted from `obligated` the first time mod money is folded in, before
    anything is written, and is then the fixed floor of every later recompute.
    That distinction is the one Fixtura draws upstream: an award form can only
    report what it obligated (funded_at_award); current funding is the award plus
    every mod since.
    """
    clins = existing.get("clins") or []
    history = existing.get("obligation_history") or []
    lines = [ln for h in history for ln in (h.get("funding_lines") or [])]
    if not lines:
        return 0

    for c in clins:
        c.setdefault("funded_at_award", c.get("obligated"))

    added: Dict[str, float] = {}
    acrns: Dict[str, List[str]] = {}
    for ln in lines:
        key = _clin_key(ln.get("clin"))
        if not key or ln.get("amount") is None:
            continue
        added[key] = added.get(key, 0.0) + float(ln["amount"])
        cited = (ln.get("acrn") or "").strip()
        if cited and cited not in acrns.setdefault(key, []):
            acrns[key].append(cited)

    touched = 0
    for c in clins:
        key = _clin_key(c.get("clin"))
        if key not in added:
            continue
        # A CLIN the award never funded starts at 0, not null: it has money on it
        # now, and null means "the documents don't say" — which is no longer true.
        c["obligated"] = round(float(c.get("funded_at_award") or 0) + added[key], 2)
        # Every appropriation that funded this line, in the order first cited (#61).
        # A later fiscal year's mod citing a new ACRN neither retracts the award's
        # own citation nor replaces it: a CLIN funded from two appropriations is one
        # CLIN with two citations, and dropping either loses the provenance of money
        # that is already counted in `obligated` above.
        merged = _acrn_list(c.get("acrn"))
        for cited in acrns.get(key, []):
            if cited not in merged:
                merged.append(cited)
        if merged:
            c["acrn"] = ", ".join(merged)
        touched += 1
    return touched


def _apply_option_exercises(existing: dict) -> List[str]:
    """Flip to exercised=True every period an ingested mod put into effect.
    Returns the names of the periods newly flipped.

    This is what makes a mid-contract onboarding read correctly. An award form is
    signed once and cannot report an option exercised years later, so a contract
    now performing in Option Year 1 ingests from its award with that period
    un-exercised — and `burn._active_period` then anchors the whole burn clock to
    the closed base year, comparing cumulative obligation against a ceiling that
    stopped being spendable. The SF-30 that exercised the option is the document
    that says otherwise; ingesting it is what corrects the period status.

    The reads that answer it live in `period_ids.exercised_keys`, because burn.py
    has to ask the same question to warn about the option-exercise mod that was
    never ingested, and a warning suppressed by a different rule than the one that
    flips the flag is a warning that fires on contracts that are fine. Never flips a
    period *off* — a mod can only ever add.
    """
    periods = existing.get("periods") or []
    if not periods:
        return []
    wanted = period_ids.exercised_keys(existing)
    if not wanted:
        return []

    flipped = []
    for p in periods:
        if p.get("exercised") or _period_key(p.get("name")) not in wanted:
            continue
        p["exercised"] = True
        flipped.append(p.get("name"))
    return flipped


def _mod_document_text(pdf_bytes: bytes) -> str:
    """Every scrap of text in an SF-30, including its filled form fields.

    Block 14's narrative lives in an AcroForm field, which page text extraction
    misses entirely — and the model's `description` is not a reliable carrier
    either: it echoed the CLIN sentence on one run of a PDF and returned null on
    the next, which quietly moved $950,000 off a CLIN between two ingests of the
    same document. Reading the document ourselves makes the funding split
    deterministic instead of a coin flip. Best-effort: an unreadable PDF leaves
    the extractor's own answer as the only read, which is where we started."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = [page.extract_text() or "" for page in reader.pages]
        for value in (reader.get_fields() or {}).values():
            raw = value.get("/V")
            if raw:
                parts.append(str(raw))
        return "\n".join(parts)
    except Exception:
        return ""


# A cent, as a float-comparison slop. Money arrives as parsed decimals in floats,
# so exact equality on a running sum is not safe to rely on.
_CENT = 0.005

# An SF-30 designator carries a series letter and a sequence number: P00003 is the
# third procurement mod, A00001 the first administrative one. The two series number
# independently, so a hole in one says nothing about the other.
_MOD_SEQ_RE = re.compile(r"^\s*([A-Za-z]*)0*(\d+)\s*$")


def _mod_seq(num) -> Optional[tuple]:
    """``P00003`` -> ``("P", 3)``. None for anything unnumbered — the seeded ``Award``
    entry, or a designator we cannot read, neither of which can carry a predecessor."""
    m = _MOD_SEQ_RE.match(str(num or ""))
    return (m.group(1).upper(), int(m.group(2))) if m else None


def _missing_predecessor(held: dict, num) -> bool:
    """Is an action that would come *before* this mod absent from the trail?

    The only honest evidence that the dollars we hold undercount the contract. A hole
    at P00002 means P00003's running total legitimately exceeds the sum of what we
    have; a contiguous run leaves an excess with nothing to explain it. Checked within
    the mod's own series, and only below its own number — a later gap cannot account
    for money already counted by an earlier document."""
    seq = _mod_seq(num)
    if seq is None:
        return False
    series, n = seq
    return any(i not in held.get(series, ()) for i in range(1, n))


def _reconcile_obligated(merged: list, ceiling) -> tuple:
    """Rebuild the obligated total from a mod trail, and report the stated figures
    that could not be reconciled with it.

    Two reads answer "how much is obligated": the sum of every action we hold, and
    what a mod states as the running total at its own point in the trail. The sum is
    the reliable one — it is arithmetic over figures the extraction is most explicit
    about, and `merged` is keyed by mod number so a re-ingest cannot double-count it.

    A stated cumulative is worth consulting for exactly one thing: a mod missing from
    the trail, where the sum undercounts and the running total on a document we DO
    hold is the only thing that says so. But "states more than we can account for" is
    also the signature of a misread digit, and the two are numerically identical. So a
    stated figure overrides the arithmetic only where both independent checks can be
    made and both pass:

    - a **missing predecessor** in its own mod series, which is what an unexplained
      excess needs in order to be explicable at all; and
    - the **contract ceiling**, which it must stay inside. Obligating past the ceiling
      is an Anti-Deficiency Act problem rather than a routine funding action, so a
      figure above it is far likelier to be a bad character than a real
      over-obligation. Where no ceiling is known that check cannot be made, and an
      override we cannot validate is not one worth taking.

    The read that motivated all of this: a narrative stating "cumulative obligated
    $6,709,487.60" came back as $16,709,487.80, then $5,709,487.80, then
    $1,873,252.80 — three live attempts at one figure, three different wrong answers.
    The total is right because the arithmetic is trusted, not because that field was
    read correctly.

    Discarded, not silenced. Anything rejected comes back for `cumulative_ignored`,
    because a figure that would not reconcile is the sort of thing that should send
    somebody to the PDF. Returns ``(total, disputed)``; total is None when the trail
    carries no money at all, leaving the caller's existing figure alone.

    Walks the trail in order and *absorbs* an accepted override rather than taking a
    max over the whole history, so later actions land on top of it: a mod missing
    before P00002 raises the total from that point, and P00003's own dollars are still
    added afterwards. `runningTotals` in `web/src/funding-total.js` walks it the same
    way, which is what keeps the timeline column and this total from disagreeing."""
    held: dict = {}
    for h in merged:
        seq = _mod_seq(h.get("mod"))
        if seq:
            held.setdefault(seq[0], set()).add(seq[1])

    running, carries_money, disputed = 0.0, False, []
    for h in merged:
        if h.get("amount") is not None:
            running += float(h["amount"])
            carries_money = True
        if h.get("cumulative_obligated") is None:
            continue
        stated = float(h["cumulative_obligated"])
        # At or below the running sum through this mod there is nothing to adjudicate:
        # the arithmetic already carries the figure. This is also the ordinary shape of
        # the bug where a mod files its own increment as the cumulative — the sum
        # simply outvotes it, and it is not "ignored".
        if stated <= round(running, 2) + _CENT:
            continue
        if (
            ceiling is not None
            and stated <= float(ceiling) + _CENT
            and _missing_predecessor(held, h.get("mod"))
        ):
            running = stated
            carries_money = True
        else:
            disputed.append(stated)

    total = round(running, 2) if carries_money else None
    return total, sorted(set(disputed))


def _merge_mod(existing: dict, mod: dict) -> dict:
    """Fold one extracted SF-30 action into a contract's stored obligation
    history, refresh total_obligated, and re-derive the two things only the mod
    trail can know: per-CLIN funding and which option periods are in effect. Pure
    (no I/O) so it's unit-testable.

    Idempotent by mod number: re-ingesting the same SF-30 replaces its entry
    rather than double-counting the dollars. total_obligated is arithmetic over the
    actions the trail holds, displaced by a stated running total only on evidence of
    a mod missing from it — see `_reconcile_obligated`."""
    # The award's own obligation has to be *in* the trail before the trail can be
    # summed. A contract onboarded mid-performance carries that money in the header
    # with an empty history, and starting the arithmetic at the first mod then drops
    # the entire base period. Idempotent, and a no-op once a history exists — the
    # confirm path already seeds this at ingest, so it fires only for the contracts
    # that predate it.
    _seed_award_obligation(existing)
    history = existing.get("obligation_history") or []
    entry = {
        "mod": mod.get("mod_number"),
        "date": mod.get("effective_date"),
        "action": mod.get("action_type") or "modification",
        "amount": mod.get("amount_obligated"),
        "cumulative_obligated": mod.get("cumulative_obligated"),
        "funding_lines": mod.get("funding_lines")
        or _funding_lines_for(mod, mod.get("document_text"))
        or None,
        "ceiling": mod.get("total_ceiling"),
        "ceiling_lines": _parse_ceiling_lines(mod.get("document_text"))
        or _parse_ceiling_lines(mod.get("description"))
        or None,
        "period": mod.get("period_exercised"),
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
    # A ceiling-raising mod restates the contract total. Only ever upward, and
    # only from a mod that actually printed a per-CLIN ceiling clause: a mod that
    # merely quotes a stale total for cross-check must not shrink the contract.
    stated = [
        float(h["ceiling"])
        for h in merged
        if h.get("ceiling") is not None and h.get("ceiling_lines")
    ]
    if stated and (
        header.get("total_ceiling") is None
        or max(stated) > float(header["total_ceiling"])
    ):
        header["total_ceiling"] = max(stated)
    # The obligated total is arithmetic over the actions we hold, and a stated running
    # total displaces it only on evidence of a mod missing from the trail. See
    # `_reconcile_obligated` for why that is the rule and what it rejects.
    ceiling = header.get("total_ceiling")
    total, disputed = _reconcile_obligated(merged, ceiling)
    if total is not None:
        header["total_obligated"] = total
    if header.get("total_obligated") is not None and ceiling:
        header["incrementally_funded"] = float(header["total_obligated"]) < float(
            ceiling
        )
    # Order matters: the exercise read consults the CLIN->period labels, and the
    # funding recompute must see the full merged history, so both run last.
    ceilings_revised = _apply_ceiling_lines(existing)
    clins_funded = _apply_clin_funding(existing)
    periods_exercised = _apply_option_exercises(existing)
    return {
        "mod": entry["mod"],
        "replaced": replaced,
        "history_len": len(merged),
        "total_obligated": header.get("total_obligated"),
        "clins_funded": clins_funded,
        "periods_exercised": periods_exercised,
        "ceilings_revised": ceilings_revised,
        # Cumulative figures read off the documents that sit above the ceiling, and
        # so were not used. Empty on every well-read trail.
        "cumulative_ignored": disputed,
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
    # Keep the document's own words alongside the extraction, so the per-CLIN
    # funding split and any revised ceilings can be read from the PDF rather than
    # from the model's retelling of it (see _merge_mod).
    parsed["document_text"] = (
        _mod_document_text(data)
        if (file.filename or "").lower().endswith(".pdf")
        else data.decode("utf-8", "ignore")
    )
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


@app.delete("/api/contracts/{contract_id}")
def remove_contract(contract_id: int):
    """Hard-delete a contract and its timesheets, expenses, plans,
    contract-scoped rates and stored source documents (#30 — the award goes with
    the contract it evidences, in the same transaction, or deleting a contract
    would leave its PDF as an orphan nothing can reach or remove).
    Irreversible — the UI confirms by PIID first. Bulk
    delete is the client calling this once per contract, so a partial failure
    reports honestly instead of pretending the whole batch went."""
    if not db.delete_contract(contract_id):
        raise HTTPException(status_code=404, detail="Contract not found.")
    return {"deleted": contract_id}


def _provenance_message(
    check: dict,
    seed: int,
    opts: dict,
    seed_source: str,
    opts_source: str,
    stored: bool,
) -> str:
    """What a mismatched batch has to say for itself — as a 409 detail when the sync
    is refused, and as a `warning` when allow_mismatch waved it through.

    Long, on purpose. It has to name what was pulled, say why that is fatal rather
    than untidy, and point at the fix, because the symptom the user would otherwise
    chase (unpriced LCATs charged to unknown CLINs) sends them to the LCAT tooling
    instead of to the sync that caused it.

    Which fix it points at depends on HOW the PIIDs disagree (#137). This message is
    the entire UI for the failure, so naming the wrong dial costs the user the whole
    debugging path — and the version that always blamed the seed did exactly that on
    the one case that matters most: an opts-derived renumbering, where the seed is
    correct, recorded, and no value of `?seed=` can help. It also reports both halves
    of the pairing it actually used and where each came from, because a refusal is
    only diagnosable if the user can see what was derived on their behalf.
    """
    foreign = check["foreign"]
    top = next(iter(foreign))
    others = (
        f" (and {len(foreign) - 1} other contract{'s' if len(foreign) > 2 else ''})"
        if len(foreign) > 1
        else ""
    )
    lede = "Stored a mismatched batch" if stored else "Timesheet sync refused"
    opts_text = sources.format_opts(opts)
    tried = (
        f"The batch was generated with seed {seed} ({seed_source}) and opts "
        f"{opts_text} ({opts_source})."
    )
    if sources.piid_relation(check["piid"], top) == "renumbered":
        # Same office, type and serial, one fiscal year apart: the seed drew this
        # contract, the opts numbered it as the wrong one of its two spellings. Which
        # means the repair is knowable, not just describable — flip the one knob that
        # moves the fiscal year and hand back a pairing the user can paste, rather
        # than the pairing that just failed plus an instruction to edit it.
        flipped = dict(opts)
        flipped["pop_in_progress"] = not opts.get("pop_in_progress")
        remedy = (
            f"{top} and {check['piid']} differ only in the fiscal-year segment, which "
            "Fixtura builds from the award's effective date — and `pop_in_progress` "
            "moves that date back a year per preceding option period. So the seed is "
            "drawing the right contract and the OPTS are numbering it as the wrong "
            "one; changing ?seed= cannot reconcile this. Re-sync stating the opts this "
            f"award was generated with — ?opts={sources.format_opts(flipped)} is the "
            "same pairing with that knob flipped — or enter them in the 'Opts' field "
            "beside 'Data seed' on the ingest review screen."
        )
    elif seed_source == "recorded at ingest":
        remedy = (
            f"Seed {seed} is the one recorded for this award at ingest, and the "
            "serials differ, so the pairing that drew the batch is not this "
            "contract's. Re-sync with ?seed=<n> and/or ?opts=<knobs> stating the "
            "pairing the batch was generated with."
        )
    elif seed_source == "derived from the PIID":
        remedy = (
            "No Fixtura seed is recorded for this award, so the sync fell back to one "
            f"derived from the PIID ({seed}), which draws a different contract. Enter "
            "the batch's seed in the 'Data seed' field on the ingest review screen, or "
            "re-sync with ?seed=<n>."
        )
    else:
        remedy = (
            f"Seed {seed} ({seed_source}) draws a different contract. Re-sync with "
            "?seed=<n> and/or ?opts=<knobs> stating the pairing the batch was "
            "generated with."
        )
    tail = (
        "The burn, allocation and LCAT views are reading another contract's labor "
        "until this is re-synced."
        if stored
        # Named, but not as an equivalent: it stores rows whose contract_no disagrees
        # with the contract permanently, which is a decision to live with the
        # mismatch rather than a repair of it.
        else "?allow_mismatch=true stores the batch anyway, but it is not a fix — the "
        "rows keep the PIID they were generated with, so this contract disagrees with "
        "its own timesheets from then on."
    )
    return (
        f"{lede}: {check['foreign_rows']} of {check['total']} rows belong to "
        f"{top}{others}, not {check['piid']}. Fixtura draws the award, its CLINs and "
        "the roster from one seed and one set of opts, so this batch is a different "
        "contract's labor — stored against this one it reads as LCATs the award never "
        f"priced, charged to CLINs it does not contain. {tried} {remedy} {tail}"
    )


@app.post("/api/contracts/{contract_id}/timesheets/sync")
def sync_timesheets(
    contract_id: int,
    rows: int = sources.SYNC_ROW_CAP,
    seed: Optional[int] = None,
    opts: Optional[str] = None,
    scenario: Optional[str] = None,
    allow_mismatch: bool = False,
):
    """Pull a fresh timesheet batch from Fixtura and cache it against this
    contract. Delete-then-insert (via db.replace_timesheets) so a re-sync
    never double-counts hours.

    Scenario precedence: an explicit ?scenario ('red' / 'amber') wins; otherwise the
    scenario this contract was last synced with; otherwise the opts are DERIVED from
    the award itself and the roster is crewed to the FTEs its labor lines were priced
    at. A demo scenario is deliberately opt-in: those opts crew above or below plan on
    purpose, and sending them on every sync (as this endpoint used to) skewed every
    real contract's hours to make a demo read red.

    Seed precedence, likewise: explicit ?seed, else the seed this contract recorded
    at ingest, else the named scenario's own seed, else one derived from the PIID. So
    a contract keeps generating the *coherent* batch it was ingested against, and the
    auto-sync (which passes nothing) reproduces it rather than drifting.

    **The batch is checked before it is stored** (`sources.provenance`). A seed the
    contract never recorded still produces a perfectly well-formed batch — for
    somebody else's award — and the derived fallback is a hash of the PIID, so it is
    not even close to the right one. Six of nine contracts in the dev DB were
    carrying another contract's labor this way, which is where the standing
    unmatched-LCAT noise came from. Foreign rows are therefore refused (409), not
    warned about: unlike the SF-30 PIID check next door, which tolerates a mismatch
    because block 10A comes through OCR, `contract_no` is a generated field and a
    disagreement there is never a misread.

    Opts precedence sits above both, because an explicit `?opts=` is the only input
    that can *state* the pairing rather than reconstruct it (#136): a caller naming
    opts wins over a pin and over the derivation. It is the way to repair a contract
    whose derived opts drew the wrong award — the derivation cannot tell an in-progress
    award from a historical one with the same seed, and Fixtura numbers those two
    differently, so no amount of re-deriving gets such a contract unstuck. Accepts
    `key=value` pairs or a JSON object; an unknown knob is a 400. Naming opts and a
    scenario together is refused rather than ranked: a demo scenario IS a stated
    pairing, so the two requests contradict each other and guessing which one the
    caller meant is how a demo bundle silently stops reproducing its own bundle.

    `?allow_mismatch=true` stores anyway and says so in the response, for the case
    where the mismatch is understood and the rows are wanted regardless.
    """
    contract = db.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    try:
        stated_opts = sources.parse_opts(opts)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if stated_opts and scenario is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"?opts= and ?scenario={scenario} each name a whole (seed, opts) "
                "pairing, so only one of them can govern a sync. Send the opts to "
                "replay this award's own pairing, or the scenario to replay a demo "
                "bundle's."
            ),
        )
    name = scenario if scenario is not None else contract.get("sync_scenario")
    picked = None
    if name:
        try:
            picked = sources.scenario(name)
        except KeyError:
            known = ", ".join(sorted(sources.SCENARIOS))
            raise HTTPException(
                status_code=400,
                detail=f"Unknown scenario '{name}'. Known scenarios: {known}.",
            )
    # Seed and opts are one pairing, not two settings: Fixtura draws the contract
    # from both, so replaying a pinned seed against freshly derived opts can still
    # land on a different award. A contract that has pinned a clean batch therefore
    # replays the opts it was pinned with — unless the caller names a scenario or a
    # seed, either of which is a request for a new pairing.
    pinned_opts = contract.get("sync_opts") if seed is None else None
    # Each half carries where it came from, because a refusal that cannot say whether
    # a value was stated, recorded or guessed on the user's behalf is not diagnosable
    # (#137) — and "derived" is exactly the case whose message used to be wrong.
    if stated_opts:
        # Stated beats every reconstruction, including a demo contract's own recorded
        # scenario. On a contract carrying `sync_scenario` this is a one-shot: the
        # scenario record still governs the next auto-sync, because a demo bundle is
        # defined by that record and one repair call is not a request to redefine it.
        used_opts, opts_source = stated_opts, "stated on this sync"
    elif picked:
        used_opts, opts_source = picked["opts"], f"from scenario '{name}'"
    elif pinned_opts:
        used_opts, opts_source = pinned_opts, "pinned by an earlier clean sync"
    else:
        used_opts, opts_source = (
            sources.derive_scenario_opts(contract),
            "derived from the award",
        )
    if seed is not None:
        effective_seed, seed_source = seed, "stated on this sync"
    elif contract.get("sync_seed") is not None:
        effective_seed, seed_source = contract["sync_seed"], "recorded at ingest"
    elif picked:
        effective_seed, seed_source = picked["seed"], f"from scenario '{name}'"
    else:
        effective_seed, seed_source = (
            sources.seed_for_piid(contract.get("piid")),
            "derived from the PIID",
        )
    try:
        ts = sources.fetch_timesheets(rows=rows, seed=effective_seed, opts=used_opts)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Timesheet sync failed: {e}")

    check = sources.provenance(ts, contract.get("piid"))
    if check["foreign_rows"] and not allow_mismatch:
        # Refused before replace_timesheets, so a rejected sync leaves whatever the
        # contract already had. Wiping good rows to store nothing would be a worse
        # outcome than the mismatch this is guarding against.
        raise HTTPException(
            status_code=409,
            detail=_provenance_message(
                check,
                effective_seed,
                used_opts,
                seed_source,
                opts_source,
                stored=False,
            ),
        )

    stored = db.replace_timesheets(contract_id, ts)
    # Remember an explicitly chosen seed or scenario so future auto-syncs — which
    # pass neither — keep reproducing this same batch instead of falling back to
    # derived opts and quietly re-baselining a demo bundle to on-plan.
    remember = {}
    if seed is not None and contract.get("sync_seed") != seed:
        remember["sync_seed"] = seed
    if scenario is not None and contract.get("sync_scenario") != scenario:
        remember["sync_scenario"] = scenario
    # Pin the pairing that just produced a batch belonging to this contract, so the
    # next auto-sync reproduces it by record rather than by re-deriving and hoping.
    # Only a verified-clean batch earns a pin: pinning a mismatch waved through with
    # allow_mismatch would make the wrong pairing the contract's new baseline. Demo
    # scenarios are excluded — `sync_scenario` already records those, and their opts
    # are deliberately off-plan.
    if picked is None and check["checked"] and not check["foreign_rows"]:
        if contract.get("sync_seed") != effective_seed:
            remember["sync_seed"] = effective_seed
        if contract.get("sync_opts") != used_opts:
            remember["sync_opts"] = used_opts
    if remember:
        blob = {
            k: v for k, v in contract.items() if k not in ("id", "piid", "created_at")
        }
        blob.update(remember)
        db.update_contract(contract_id, blob)
    return {
        "id": contract_id,
        "rows": stored,
        "people": len({r.get("employee_id") for r in ts if r.get("employee_id")}),
        "weeks": len({r.get("week_ending") for r in ts if r.get("week_ending")}),
        # Which scenario the batch came from, so a caller can tell demo data from
        # data generated against the award it's attached to.
        "scenario": name or "derived",
        "seed": effective_seed,
        # The other half of the pairing, reported for the same reason the seed is: a
        # refusal is only diagnosable if the caller can see BOTH halves that produced
        # the batch, and until now the opts were invisible from outside the route.
        "opts": used_opts,
        # Where each half came from — "derived" vs "recorded" is the difference
        # between a value the user chose and one Runway guessed for them, which is
        # what makes a refusal actionable rather than mysterious (#137).
        "seed_source": seed_source,
        "opts_source": opts_source,
        # Reported on every sync, not just a refused one: "these rows are this
        # contract's" is the fact the burn numbers rest on, and it is cheap to say.
        "provenance": check,
        "warning": (
            _provenance_message(
                check,
                effective_seed,
                used_opts,
                seed_source,
                opts_source,
                stored=True,
            )
            if check["foreign_rows"]
            else None
        ),
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


def _hours_elsewhere(contract_id: int) -> dict:
    """`{employee_id: [{contract_id, contract, hours}]}` for every contract *except*
    this one (#116).

    A person's expected week belongs to the person, so headroom is only honest once
    the hours they bill elsewhere are subtracted from it. Computed by the caller and
    passed in, for the same reason the expected-hours overrides are: `allocation` must
    not reach across contracts on its own.

    `allocation.booked_hours` rather than a full `compute_allocation` per contract —
    this needs a column of hours, not a burn pass each.
    """
    out: dict = {}
    for c in db.list_contracts():
        if c["id"] == contract_id:
            continue
        # The same display name burn resolves, so a "20 hrs on FALCON" note names the
        # contract the way every other surface does.
        header = c.get("contract") or {}
        name = (
            c.get("nickname")
            or header.get("contractor")
            or c.get("piid")
            or f"Contract {c['id']}"
        )
        rows = db.get_timesheets(c["id"])
        away = heat.absence_hours(c, rows)
        for emp, hrs in allocation.booked_hours(c, rows).items():
            out.setdefault(emp, []).append(
                {
                    "contract_id": c["id"],
                    "contract": name,
                    "hours": hrs,
                    # Leave booked over there is leave out of the same week, so it
                    # travels with the hours rather than staying where it was typed.
                    **(away.get(emp) or {"leave": 0.0, "holiday": 0.0}),
                }
            )
    return out


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
        _hours_elsewhere(contract_id),
        db.quals_by_person(),
    )


@app.get("/api/contracts/{contract_id}/heat")
def contract_heat(contract_id: int):
    """Who's running hot (#83): the people whose hours above their expected week are
    driving an off-pace CLIN, what those hours cost it weekly, and whether the fix is
    to stop the overtime or to cut staffing. See heat.py.

    Composed from the allocation payload rather than recomputed, so the Flight Deck's
    person list and the allocation matrix cannot name different people or disagree
    about a rate.

    `suggestions` (#63) rides along on the same response: the ordered, named moves that
    close each off-pace CLIN's weekly gap. Same endpoint on purpose — the moves are
    built *from* this payload's ranking and diagnosis, and a second request could serve
    a plan derived from a different snapshot of the same contract.
    """
    contract = db.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    rows = db.get_timesheets(contract_id)
    alloc = allocation.compute_allocation(
        contract,
        rows,
        db.list_expenses(contract_id),
        _cost_model(contract_id),
        db.expected_hours_by_person(),
        _hours_elsewhere(contract_id),
        db.quals_by_person(),
    )
    payload = heat.compute_heat(contract, rows, alloc)
    payload["suggestions"] = suggest.solve_moves(alloc, payload)
    return payload


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


class FeePeriodsIn(BaseModel):
    """A CPAF contract's award-fee evaluation periods (#80).

    Replaced wholesale, the same convention `CapacityIn` and `AbsenceIn` use — sending
    an empty list clears the plan. Each entry carries `name`, optional `start`/`end`
    dates, an optional `pool_share` in dollars (absent shares split the pool evenly),
    a `status` of 'pending' or 'determined', and — once determined — the
    `determined_amount` and optional `score`. `clin` names the CLIN whose pool the
    period draws on and is only needed when an award carries more than one.
    """

    periods: list = []


@app.get("/api/contracts/{contract_id}/fee-periods")
def get_contract_fee_periods(contract_id: int):
    """A contract's award-fee evaluation periods (#80)."""
    contract = db.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return {
        "periods": [
            dict(p) for p in pricing.normalize_fee_periods(contract.get("fee_periods"))
        ]
    }


@app.put("/api/contracts/{contract_id}/fee-periods")
def set_contract_fee_periods(contract_id: int, body: FeePeriodsIn):
    """Record the government's award-fee determinations (#80).

    Validated before it lands, because this is the one fee input that is typed rather
    than read off a document, and an undetermined period saved as determined is the
    error that books fee nobody has awarded. Returns the stored periods rather than an
    ack: a determination moves earned fee on the Flight Deck, and the caller has to be
    able to show the number move.
    """
    for entry in body.periods or []:
        problem = pricing.validate_fee_period(entry)
        if problem:
            raise HTTPException(status_code=400, detail=problem)

    updated = db.set_contract_fee_periods(contract_id, body.periods)
    if updated is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    return {
        "periods": [
            dict(p) for p in pricing.normalize_fee_periods(updated.get("fee_periods"))
        ]
    }


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
    # Burdened the same way the burn is (#144), or the mapping picker would offer
    # nothing on exactly the cost-type CLINs whose hours it is opened to fix.
    index = lcat.build_index(
        burn._period_clins(contract, period),
        burn.burden_fn(_cost_model(contract_id), contract.get("contract") or {}),
    )
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


@app.put("/api/contracts/{contract_id}/plans/{plan_id}/baseline")
def designate_baseline(contract_id: int, plan_id: int):
    """Make this plan the contract's active baseline — the staffing we committed to.

    Idempotent, and a swap rather than a set: designating a second plan stands the
    first one down. A contract has one baseline or none, because drift gets measured
    against it and two answers to "what did we commit to?" is worse than none (#67).
    """
    if db.get_contract(contract_id) is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    try:
        return db.set_baseline_plan(contract_id, plan_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.delete("/api/contracts/{contract_id}/plans/{plan_id}/baseline")
def clear_baseline(contract_id: int, plan_id: int):
    """Stand the active baseline down, keeping the plan itself.

    Scoped to the plan the caller believes is the baseline, so a menu left open
    since before someone else re-designated can't clear the wrong one.
    """
    current = db.get_baseline_plan(contract_id)
    if current is None or current["id"] != plan_id:
        raise HTTPException(status_code=404, detail="That plan is not the baseline.")
    db.set_baseline_plan(contract_id, None)
    return {"baseline": None}


def _all_allocations() -> list:
    """One allocation payload per contract. The expensive sweep — a burn pass each —
    behind both portfolio utilisation and conflicts."""
    # One query for every per-person expected week (#84), not one per contract.
    overrides = db.expected_hours_by_person()
    # Same for the credentials the compliance check reads (#66).
    quals = db.quals_by_person()
    contracts = db.list_contracts()
    timesheets = {c["id"]: db.get_timesheets(c["id"]) for c in contracts}

    # Everyone's hours on every contract, once (#116), so each payload's "elsewhere"
    # is this map minus its own. `_hours_elsewhere` per contract would be the same
    # sweep N times over.
    booked = {
        c["id"]: allocation.booked_hours(c, timesheets[c["id"]]) for c in contracts
    }
    absence = {c["id"]: heat.absence_hours(c, timesheets[c["id"]]) for c in contracts}
    names = {}
    for c in contracts:
        header = c.get("contract") or {}
        names[c["id"]] = (
            c.get("nickname")
            or header.get("contractor")
            or c.get("piid")
            or f"Contract {c['id']}"
        )

    out = []
    for c in contracts:
        elsewhere: dict = {}
        for other_id, hours in booked.items():
            if other_id == c["id"]:
                continue
            for emp, hrs in hours.items():
                elsewhere.setdefault(emp, []).append(
                    {
                        "contract_id": other_id,
                        "contract": names[other_id],
                        "hours": hrs,
                        **(
                            absence[other_id].get(emp) or {"leave": 0.0, "holiday": 0.0}
                        ),
                    }
                )
        out.append(
            allocation.compute_allocation(
                c,
                timesheets[c["id"]],
                db.list_expenses(c["id"]),
                expected_hours_by_person=overrides,
                hours_elsewhere_by_person=elsewhere,
                quals_by_person=quals,
            )
        )
    return out


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
