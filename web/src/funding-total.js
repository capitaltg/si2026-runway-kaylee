// The running obligated total down a mod trail.
//
// Computed rather than read off each mod. Most SF-30s state no cumulative at all —
// plenty print only "Obligated this action $X" — so the stated figure is null on a
// perfectly normal document, and rendering it raw put "$0" in a column headed
// CUMULATIVE.
//
// Deliberately the same rule the server applies to the header total
// (`_reconcile_obligated` in `server/app/main.py`): a timeline that disagrees with the
// total printed above it is worse than either number on its own.
//
// A stated running total displaces the arithmetic for exactly one reason — a mod
// missing from the trail, where our sum undercounts and the document we DO hold is the
// only thing that says so. But "states more than we can account for" is equally the
// signature of a misread digit, and the two are numerically identical, so an override
// needs both independent checks to pass:
//
//   - a hole in its own mod series below its own number, which is what makes an
//     unexplained excess explicable at all; and
//   - the contract ceiling, which it must stay inside. Obligating past the ceiling is
//     an Anti-Deficiency Act problem rather than a routine action, so a figure above it
//     is far likelier to be a bad character. Where no ceiling is known that check
//     cannot be made, and an override we cannot validate is not worth taking.
//
// Seen in the wild: a narrative reading "cumulative obligated $6,709,487.60" extracted
// as $16,709,487.80, then $5,709,487.80, then $1,873,252.80 — three attempts at one
// figure, three different wrong answers.

// A cent of slop. These are parsed decimals in floats, so exact equality on a running
// sum is not safe to lean on.
const CENT = 0.005;

// An SF-30 designator carries a series letter and a sequence number: P00003 is the
// third procurement mod, A00001 the first administrative one. The two series number
// independently, so a hole in one says nothing about the other.
const MOD_SEQ = /^\s*([A-Za-z]*)0*(\d+)\s*$/;

function modSeq(num) {
  const m = MOD_SEQ.exec(String(num ?? ""));
  return m ? [m[1].toUpperCase(), Number(m[2])] : null;
}

// Is an action that would come *before* this mod absent from the trail? Checked within
// the mod's own series and only below its own number — a later gap cannot account for
// money an earlier document already claims to have counted.
function missingPredecessor(held, num) {
  const seq = modSeq(num);
  if (!seq) return false;
  const [series, n] = seq;
  const have = held.get(series) ?? new Set();
  for (let i = 1; i < n; i += 1) if (!have.has(i)) return true;
  return false;
}

export function runningTotals(history = [], ceiling = null) {
  const held = new Map();
  for (const h of history) {
    const seq = modSeq(h.mod);
    if (!seq) continue;
    if (!held.has(seq[0])) held.set(seq[0], new Set());
    held.get(seq[0]).add(seq[1]);
  }

  const out = [];
  history.reduce((carried, h) => {
    const summed = carried + (h.amount || 0);
    const stated = h.cumulative_obligated ?? null;
    // At or below the sum there is nothing to adjudicate — including the ordinary bug
    // where a mod files its own increment as the cumulative, which the sum outvotes.
    const excess = stated != null && stated > summed + CENT;
    const credible =
      excess &&
      ceiling != null &&
      stated <= ceiling + CENT &&
      missingPredecessor(held, h.mod);
    // An accepted override is absorbed, so later actions land on top of it rather than
    // being lost behind it.
    const total = credible ? stated : summed;
    // `stated` and `disputed` are kept so a caller can say the document disagreed
    // rather than quietly overriding it — the same posture the ingest takes on a
    // cost/fee mismatch.
    out.push({ total, stated, summed, disputed: excess && !credible });
    return total;
  }, 0);
  return out;
}
