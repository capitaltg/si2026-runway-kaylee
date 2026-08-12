// Reconciling the review screen with what the server actually saved (#160).
//
// Confirm rescores the extraction against the values the user edited, so the
// scores and notes it returns can differ from the ones on screen — that is the
// whole point of rescoring. Merging them back is what makes a warning the user
// just fixed disappear, and a warning their edit introduced appear at all.

const LOW = 0.88; // same threshold the review screen badges an extracted field at

/** Apply a confirm response's recomputed scores onto the reviewed extraction. */
export function mergeConfidence(extraction, saved) {
  if (!extraction || !saved) return extraction;
  const byClin = new Map(
    (saved.clin_confidence || []).map((c) => [c.clin, c]),
  );
  return {
    ...extraction,
    confidence_source: saved.confidence_source || null,
    contract: {
      ...extraction.contract,
      field_confidence:
        saved.field_confidence ?? extraction.contract?.field_confidence ?? {},
    },
    clins: (extraction.clins || []).map((cl) => {
      const rescored = byClin.get(cl.clin);
      if (!rescored) return cl;
      return {
        ...cl,
        confidence: rescored.confidence,
        // Explicit null, not `||` — a note that cleared has to erase the old
        // sentence rather than fall through to it.
        confidence_note: rescored.confidence_note ?? null,
      };
    }),
  };
}

/**
 * Warnings on the SAVED record that the user had not already seen before saving.
 *
 * Only the new ones: re-announcing a caution the review screen was already
 * showing turns the confirmation into noise, and the user has by then read it
 * and chosen to save anyway. That choice stands — this never blocks, and the
 * contract is written either way.
 */
export function newWarnings(before, after) {
  const out = [];
  const fcBefore = before?.contract?.field_confidence || {};
  const fcAfter = after?.contract?.field_confidence || {};
  for (const [field, score] of Object.entries(fcAfter)) {
    const prior = fcBefore[field];
    if (score < LOW && !(prior != null && prior < LOW)) {
      out.push({ field, score });
    }
  }
  const notesBefore = new Map(
    (before?.clins || []).map((cl) => [cl.clin, cl.confidence_note || null]),
  );
  for (const cl of after?.clins || []) {
    if (cl.confidence_note && cl.confidence_note !== notesBefore.get(cl.clin)) {
      out.push({ clin: cl.clin, note: cl.confidence_note });
    }
  }
  return out;
}
