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

// --- Planned-add identity (#67 item 5) --------------------------------------
//
// A planned add used to be `added-${addSeq++}`, counted from zero per mounted view.
// Two plans saved in different sessions therefore both hold `added-0`, and the
// compare panel — which matches rows by id — reads those two different people as one
// person who changed hours. The prefix is load-bearing elsewhere (it is how the grid
// knows a row is planned rather than synced), so it stays; only the suffix changes,
// to something that cannot collide across sessions.

const ADD_PREFIX = "added-";

/** A fresh id for a planned add. Unique across sessions, plans and contracts. */
export function newAddedId() {
  const c = globalThis.crypto;
  if (c?.randomUUID) return ADD_PREFIX + c.randomUUID();
  // Non-secure contexts have no randomUUID. Two 32-bit-ish chunks is still far past
  // the collision odds of a per-session counter, which is the bug being fixed.
  const chunk = () => Math.random().toString(36).slice(2, 10);
  return ADD_PREFIX + chunk() + chunk();
}

/** Is this row a planned add rather than a synced charger? */
export function isAddedId(id) {
  return String(id).startsWith(ADD_PREFIX);
}

// --- What a plan was scored against (#67 item 5) ----------------------------
//
// A plan stores hours, not the assumptions those hours were priced under. Reload it
// after a mod re-funds a CLIN, after a rate schedule is imported, or after the
// holiday calendar is edited, and every number it shows means something different
// than it did when it was saved — silently, with the same plan name on it.
//
// Two things are deliberately NOT treated as staleness:
//
//   - `spent` / `remaining` / the current week. Those move on every sync by design.
//     Flagging them would mark every plan stale within a week and the badge would
//     mean nothing.
//   - The contract's holiday calendar and committed absences are still SCORED live,
//     not from the snapshot — see absence.contract_absence, which settled that a
//     holiday is a fact about the calendar rather than about one what-if. This only
//     adds the disclosure that PR promised: the plan says when the calendar moved
//     under it, instead of quietly projecting something new.
//
// So the snapshot holds the contract's *terms* — funding, ceiling, rates, calendar,
// period — and nothing that is merely an accumulating actual.

const round2 = (n) => (n == null ? null : Math.round(Number(n) * 100) / 100);

/** The scoring assumptions in force right now, to be stored beside a saved plan. */
export function scoringSnapshot(data) {
  if (!data) return null;
  const clins = {};
  for (const c of data.clins || []) {
    clins[c.id] = {
      budget: round2(c.budget),
      ceiling: round2(c.ceiling),
      incrementally_funded: Boolean(c.incrementally_funded),
      blended_rate: round2(c.blended_rate),
    };
  }
  const rates = {};
  for (const e of data.employees || []) {
    const row = {};
    for (const [cid, cell] of Object.entries(e.cells || {}))
      if (cell?.rate != null) row[cid] = round2(cell.rate);
    if (Object.keys(row).length) rates[e.id] = row;
  }
  const absence = data.contract?.absence || {};
  return {
    period: data.contract?.period ?? null,
    pop: [data.contract?.pop_start || null, data.contract?.pop_end || null],
    clins,
    rates,
    holidays: unordered(absence.holidays),
    absences: unordered(absence.absences),
  };
}

// Everyone the plan actually staffs — a rate change on someone with no hours in this
// plan changes none of its numbers, and claiming otherwise is a false alarm.
function staffedIn(state) {
  const draft = canonicalDraft(state?.draft);
  return new Set([
    ...Object.keys(draft),
    ...(state?.added || []).map((a) => String(a.id)),
  ]);
}

/**
 * What has moved under a saved plan since it was saved — as phrases for the badge.
 *
 * Empty means nothing relevant changed. A plan saved before this shipped has no
 * snapshot, and that is reported as unknown (empty) rather than stale: the honest
 * answer is that we cannot tell, and a badge that cries stale on every old plan
 * would be the same class of lie #62 fixed.
 */
export function snapshotChanges(saved, live, state) {
  if (!saved || !live) return [];
  const out = [];

  if (saved.period !== live.period || stable(saved.pop) !== stable(live.pop))
    out.push("the period of performance changed");

  const clinIds = [...new Set([...Object.keys(saved.clins || {}), ...Object.keys(live.clins || {})])];
  const moved = [];
  for (const id of clinIds) {
    const before = saved.clins?.[id];
    const after = live.clins?.[id];
    if (!before || !after) {
      moved.push(`CLIN ${id} ${before ? "is gone" : "is new"}`);
    } else if (stable(before) !== stable(after)) {
      moved.push(`CLIN ${id}`);
    }
  }
  if (moved.length) out.push(`funding or rates changed on ${listPhrase(moved)}`);

  const staffed = staffedIn(state);
  let changedRates = 0;
  for (const empId of new Set([...Object.keys(saved.rates || {}), ...Object.keys(live.rates || {})])) {
    if (staffed.size && !staffed.has(String(empId))) continue;
    if (stable(saved.rates?.[empId] || {}) !== stable(live.rates?.[empId] || {})) changedRates += 1;
  }
  if (changedRates)
    out.push(`${changedRates} billing rate${changedRates === 1 ? "" : "s"} changed`);

  if (stable(saved.holidays) !== stable(live.holidays))
    out.push("the holiday calendar changed");
  if (stable(saved.absences) !== stable(live.absences))
    out.push("the contract's committed absences changed");

  return out;
}

// "CLIN 1", "CLIN 1 and CLIN 2", "CLIN 1, CLIN 2 and 3 more" — a badge that lists
// eleven CLINs is not read by anybody.
function listPhrase(items) {
  if (items.length <= 2) return items.join(" and ");
  return `${items.slice(0, 2).join(", ")} and ${items.length - 2} more`;
}
