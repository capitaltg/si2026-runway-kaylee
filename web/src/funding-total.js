// The running obligated total down a mod trail.
//
// Computed rather than read off each mod. Most SF-30s state no cumulative at all —
// plenty print only "Obligated this action $X" — so the stated figure is null on a
// perfectly normal document, and rendering it raw put "$0" in a column headed
// CUMULATIVE. Where a document DOES state a running total above our sum, that is
// evidence of a mod missing from the trail, so it wins and carries forward.
//
// Deliberately the same rule the server applies to the header total (`_merge_mod`):
// a timeline that disagrees with the total printed above it is worse than either
// number on its own.
export function runningTotals(history = []) {
  const out = [];
  history.reduce((carried, h) => {
    const summed = carried + (h.amount || 0);
    const total = Math.max(summed, h.cumulative_obligated || 0);
    // Kept so a caller can say the document disagreed rather than quietly
    // overriding it — the same posture the ingest takes on a cost/fee mismatch.
    out.push({ total, stated: h.cumulative_obligated ?? null, summed });
    return total;
  }, 0);
  return out;
}
