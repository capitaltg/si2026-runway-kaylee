# Supplemental Document Mismatch Guard Design

## Goal

Prevent an SF30 modification, labor-rate schedule, or indirect-rate agreement
from changing a contract when the uploaded document names a different PIID,
unless the caller explicitly passes `?allow_mismatch=true`.

## Design

Each of the three supplemental-upload routes accepts an `allow_mismatch` boolean
query parameter that defaults to false. After extraction and content validation,
the route compares the document PIID with the selected contract PIID. A mismatch
without the override returns HTTP 409 before any contract, rate, direct-rate, or
source-document write. A missing document PIID remains acceptable because some
supplemental documents are company-wide or omit the number.

With `?allow_mismatch=true`, the existing write path runs unchanged and the 200
response retains `piid_mismatch: true`. This mirrors the established timesheet
sync safety contract: reject by default, permit only an explicit override.

## Error Handling

The 409 detail names both the uploaded and selected PIIDs and explains that
`?allow_mismatch=true` is the explicit override. Extraction failures and uploads
with no usable rates continue to return their existing 502 and 422 responses.

## Testing

Backend integration tests cover all three routes. For each default-rejected
mismatch, a snapshot of every application table before and after the request must
be identical. Companion tests pass `?allow_mismatch=true` and prove the intended
mutation still occurs. Existing supplemental-upload tests remain green.

## Scope

Only backend route behavior and backend tests change. No frontend confirmation UI
or unrelated ingest, burn, pricing, or profitability behavior is included.
