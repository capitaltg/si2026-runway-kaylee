// #62 — what "changed" means on the allocation matrix.
//
// The matrix had one notion of change: the grid differs from the synced actuals. That
// is the right test for whether Discard has anything to throw away, and the wrong one
// for whether a save is pending — a plan freshly loaded off the server differs from
// actuals by definition, so the header announced "live, not saved" over work that was
// already saved. Two predicates, from one fingerprint:
//
//   dirty   — the modelled state differs from the actuals. Enables Discard.
//   unsaved — it differs from what the loaded plan holds. Drives the header dot, and
//             decides whether Save updates a plan or has to name a new one.
//
// The fingerprint is order- and zero-insensitive on purpose: typing 5 into a cell and
// deleting it again is not an edit, and neither is adding two absences in the other
// order. Both false positives would leave a save button lit with nothing to save,
// which is the same class of lie this ticket is about.

// Sorted-key JSON, so two equal plans can't differ by key order alone.
function stable(value) {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.keys(value)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stable(value[k])}`)
      .join(",")}}`;
  return JSON.stringify(value === undefined ? null : value);
}

// A collection whose order carries no meaning — compared as a set of members.
const unordered = (list) => (Array.isArray(list) ? list.map(stable).sort() : []);

// Hours grid: a cell holding 0 and a cell that was never typed are the same plan, and
// so is a person whose whole row is zero.
function canonicalDraft(draft) {
  const out = {};
  for (const empId of Object.keys(draft || {})) {
    const row = {};
    for (const clinId of Object.keys(draft[empId] || {})) {
      const hrs = Math.round((Number(draft[empId][clinId]) || 0) * 100) / 100;
      if (hrs) row[clinId] = hrs;
    }
    if (Object.keys(row).length) out[empId] = row;
  }
  return out;
}

/** One comparable string for a simulation state (grid + adds + removals + absence). */
export function planFingerprint(state = {}) {
  return stable({
    draft: canonicalDraft(state.draft),
    added: unordered(state.added),
    removed: unordered((state.removed || []).map(String)),
    absences: unordered(state.absences),
  });
}

/**
 * Is this state different from the plan it came from?
 *
 * With no plan loaded there is nothing to save over, so "unsaved" collapses back to
 * "modelled at all" — `dirty` is passed in rather than recomputed so the caller keeps
 * one definition of it.
 */
export function isUnsaved({ fingerprint, savedFingerprint, loadedPlanId, dirty }) {
  if (!loadedPlanId) return Boolean(dirty);
  // A plan is loaded but we never recorded its fingerprint (a reload mid-session, a
  // failed save): assume there is something to save rather than claiming there isn't.
  if (!savedFingerprint) return true;
  return fingerprint !== savedFingerprint;
}
