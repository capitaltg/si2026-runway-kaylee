"""The original bytes behind an extracted contract (#30).

Runway's dashboard is a pile of derived numbers — burn rate, runway date, per-CLIN
remaining — and every one of them traces back to a PDF that ingest used to read and
throw away. "Where did this 45% overhead rate come from?" was unanswerable the moment
the ingest screen closed, which is precisely the question the users who distrust an AI
extraction ask first.

This module holds the *rules* for what may be kept; `db` holds the bytes and `main`
wires them to the upload routes. Kept separate so the limits are testable without a
database and so there is one place to change when #78 adds a cost-buildup document.

The limits are deliberately conservative. This is a single-file SQLite app, so an
unbounded blob column is an unbounded database file — a 25 MB cap on a form that is
normally under 1 MB leaves room for a scanned award without letting one upload double
the install's footprint.
"""

import hashlib
import os
from typing import Optional

AWARD = "award"
RATE_SCHEDULE = "rate_schedule"
# The FPRA or provisional billing rate letter behind a contract's indirect rates
# (#78). Its own kind rather than a second rate_schedule: it is the document an
# accountant asks for by name when a burden percentage is questioned, and the
# source panel is worth nothing if it cannot tell them apart.
RATE_AGREEMENT = "rate_agreement"
KINDS = (AWARD, RATE_SCHEDULE, RATE_AGREEMENT)

MAX_BYTES = 25 * 1024 * 1024

PDF_TYPE = "application/pdf"
TEXT_TYPE = "text/plain"

# What may be stored is exactly what ingest can read (see `extract_from_pdf` /
# `extract_from_text`), and not a narrower list. A PDF-only rule would have looked
# tighter while quietly leaving the text path storing nothing at all — an upload that
# produced a contract but no auditable source, with no error to say so. If ingest is
# ever taught a third format, add it here in the same breath.
ALLOWED_EXTENSIONS = {".pdf": PDF_TYPE, ".txt": TEXT_TYPE, ".text": TEXT_TYPE}


def digest(blob: bytes) -> str:
    """The SHA-256 an auditor would recompute. Also how a re-upload of the same file
    is recognised as the same file rather than stored twice."""
    return hashlib.sha256(blob).hexdigest()


def content_type(filename: Optional[str], declared: Optional[str] = None) -> str:
    """The type to serve this document back as.

    The extension wins over the browser's declared type: a `Content-Type` off a form
    upload is whatever the client felt like sending, and it is the value that ends up
    on the download response — so trusting it lets an upload choose how the browser
    later interprets its own bytes.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in ALLOWED_EXTENSIONS:
        return ALLOWED_EXTENSIONS[ext]
    if declared in (PDF_TYPE, TEXT_TYPE):
        return declared
    return "application/octet-stream"


def rejection(filename: Optional[str], blob: Optional[bytes]) -> Optional[str]:
    """Why this upload must not be stored, or None if it may be.

    Returns a sentence for a human rather than a bool: the caller shows it, because a
    file that silently failed to store is worse than one that never uploaded — the
    dashboard looks identically auditable either way.
    """
    if not blob:
        return "The uploaded file was empty, so there was nothing to keep."
    if len(blob) > MAX_BYTES:
        mb = len(blob) / (1024 * 1024)
        return (
            f"The file is {mb:.1f} MB, over the {MAX_BYTES // (1024 * 1024)} MB limit "
            "for a stored source document, so only the extracted data was kept."
        )
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        kinds = ", ".join(sorted(ALLOWED_EXTENSIONS))
        return (
            f"Only {kinds} files are kept as source documents, so only the extracted "
            "data was stored."
        )
    return None


def safe_filename(filename: Optional[str], fallback: str = "source-document") -> str:
    """A filename safe to put in a `Content-Disposition` header.

    Strips any directory part (a browser is not the only client, and multipart
    filenames are attacker-controlled) and any character that would break out of the
    quoted header value. An empty result falls back rather than emitting `filename=""`.
    """
    base = os.path.basename((filename or "").strip().replace("\\", "/"))
    clean = "".join(c for c in base if c.isprintable() and c not in '"\\\r\n')
    return clean or fallback
