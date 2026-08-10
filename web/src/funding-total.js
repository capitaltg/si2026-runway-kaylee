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
// `ceiling` gates the stated figure: obligating past the contract ceiling is an
// Anti-Deficiency Act problem, not a routine action, so a stated cumulative above it
// is far likelier to be a misread digit than a real over-obligation. Seen in the
// wild: a narrative reading "cumulative obligated $6,709,487.60" extracted as
// $16,709,487.80 against a $14,535,792.80 ceiling. Same gate the server applies.
export function runningTotals(history = [], ceiling = null) {
  const out = [];
  history.reduce((carried, h) => {
    const summed = carried + (h.amount || 0);
    const stated = h.cumulative_obligated ?? null;
    const credible = stated != null && (ceiling == null || stated <= ceiling);
    const total = Math.max(summed, credible ? stated : 0);
    // Kept so a caller can say the document disagreed rather than quietly
    // overriding it — the same posture the ingest takes on a cost/fee mismatch.
    out.push({ total, stated, summed, disputed: stated != null && !credible });
    return total;
  }, 0);
  return out;
}
