// Dated absence in the what-if simulator (#85) — the client half of server/app/absence.py.
//
// The matrix projected one flat hrs/wk to the end of the period, so a runway date
// the PM already knew was wrong ("two people are out in August") could only be
// corrected by fudging hrs/wk to a blended average — wrong in a different way, and
// unexplainable a month later. This module turns dated absence into a per-person,
// per-week multiplier the simulator walks the remaining weeks with.
//
// Two things it deliberately does NOT do:
//
//   * It never touches a week that has already been charged. Leave in the past was
//     backed out of actuals by PR #95 (`burn.billable_hours`); re-applying it here
//     would subtract the same hours twice. Every walk starts at the week after
//     `current_week`.
//   * It never converts absence into hours. A person out for a week loses *their*
//     expected week, which #84 established is not necessarily 40 (`capacity.py`).
//     Working in fractions of workdays and applying them to whatever that person is
//     already booked for keeps the 32-hour part-timer correct without a second
//     hours model on the client.
//
// Why the arithmetic is duplicated rather than fetched: the matrix rescores on every
// keystroke and cannot round-trip to the server per edit. The two must agree, so the
// week numbering here is the same as `burn._clock`'s — week 1 is the seven days from
// `pop_start` — and `allocation.py` sends `pop_start` down so neither side guesses it.

// Days absence can be taken on. Days, not hours, on purpose — see the module note.
export const WORKDAYS_PER_WEEK = 5;

// Where the week walk gives up looking for an exhaust point. A CLIN burning a
// rounding error a week would otherwise loop forever; mirrors burn._MAX_PROJECTION_WEEKS.
export const MAX_PROJECTION_WEEKS = 520;

const DAY_MS = 86400000;

// A date out of an ISO string, at UTC midnight. UTC on purpose: these are calendar
// dates with no time in them, and parsing "2026-08-10" as local midnight puts a
// user west of Greenwich on the 9th.
export function parseDate(value) {
  if (!value) return null;
  const t = Date.parse(String(value).slice(0, 10) + "T00:00:00Z");
  return Number.isFinite(t) ? t : null;
}

export function formatDate(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

// Mon–Fri day stamps in an inclusive range. Weekends are skipped because nobody
// charges them, so counting a Saturday of PTO would reduce a pace that never
// included it.
function workdaysBetween(startMs, endMs) {
  const out = [];
  for (let t = startMs; t <= endMs; t += DAY_MS) {
    const dow = new Date(t).getUTCDay();
    if (dow !== 0 && dow !== 6) out.push(t);
  }
  return out;
}

// The inclusive calendar range of a period week, 1-indexed off pop_start — the same
// numbering burn._clock uses, so an absence lands in the same week on both sides.
export function weekWindow(popStartMs, week) {
  if (popStartMs == null || week < 1) return null;
  const first = popStartMs + 7 * (week - 1) * DAY_MS;
  return [first, first + 6 * DAY_MS];
}

// Which period week a date falls in (1-indexed), or null with no calendar.
export function weekOf(popStartMs, dateMs) {
  if (popStartMs == null || dateMs == null) return null;
  return Math.floor((dateMs - popStartMs) / (7 * DAY_MS)) + 1;
}

/**
 * Build the absence model the simulator scores against.
 *
 * `holidays` are the contract's company-wide dates; `absences` are per-person dated
 * ranges — the contract's committed ones and the plan's what-ifs, concatenated by
 * the caller, because the simulator scores them identically. Only their *storage*
 * differs (see the note in server/app/absence.py on why holidays live on the
 * contract), not their arithmetic.
 *
 * Returns `{ active, factorFor(personId, week), weeksAffected, peopleAffected,
 * holidayWeeks }`. `active` is false when nothing in the remaining weeks is
 * reduced, and the caller must then take its original flat-pace path rather than
 * walking weeks — an identical result reached two ways is still two code paths, and
 * only one of them has been drawing correct numbers for six months.
 */
export function buildAbsenceModel({
  popStart,
  fromWeek,
  totalWeeks,
  holidays = [],
  absences = [],
}) {
  const popStartMs = parseDate(popStart);
  const first = Math.max(1, (fromWeek || 0) + 1);
  const last = Math.max(first - 1, totalWeeks || 0);

  const inert = {
    active: false,
    factorFor: () => 1,
    weeksAffected: 0,
    peopleAffected: [],
    holidayWeeks: [],
  };
  if (popStartMs == null || last < first) return inert;

  const holidayDays = new Set();
  for (const h of holidays) {
    const t = parseDate(h?.date ?? h);
    if (t != null) holidayDays.add(t);
  }

  const rangesByPerson = new Map();
  for (const a of absences) {
    const s = parseDate(a?.start);
    const e = parseDate(a?.end);
    const who = a?.person_id;
    if (!who || s == null || e == null || e < s) continue;
    if (!rangesByPerson.has(who)) rangesByPerson.set(who, []);
    rangesByPerson.get(who).push([s, e]);
  }

  if (!holidayDays.size && !rangesByPerson.size) return inert;

  // Precomputed per week, because the matrix rescores this on every keystroke and
  // the walk below reads it once per person per CLIN per week.
  const perWeek = new Map(); // week -> { span, holidays: n, byPerson: Map<id, off> }
  const affected = new Set();
  const people = new Set();
  const holidayWeeks = [];

  for (let w = first; w <= last; w++) {
    const win = weekWindow(popStartMs, w);
    const days = workdaysBetween(win[0], win[1]);
    if (!days.length) continue;
    const hol = days.filter((d) => holidayDays.has(d));
    const byPerson = new Map();
    for (const [who, ranges] of rangesByPerson) {
      // Union with the holidays, never a sum: a fortnight of PTO spanning July 4th
      // removes ten workdays, not eleven, and summing would drive the factor below
      // zero — i.e. claim the contract earns money back over the holiday.
      const off = new Set(hol);
      for (const d of days) {
        if (ranges.some(([s, e]) => d >= s && d <= e)) off.add(d);
      }
      if (off.size > hol.length) {
        byPerson.set(who, off.size);
        people.add(who);
      }
    }
    if (hol.length || byPerson.size) {
      affected.add(w);
      if (hol.length) holidayWeeks.push(w);
    }
    perWeek.set(w, { span: days.length, holidays: hol.length, byPerson });
  }

  if (!affected.size) return inert;

  return {
    active: true,
    // The share of a normal week this person still works. 1 is untouched.
    factorFor(personId, week) {
      const wk = perWeek.get(week);
      if (!wk) return 1;
      const off = wk.byPerson.get(personId) ?? wk.holidays;
      return off ? Math.max(0, 1 - off / wk.span) : 1;
    },
    weeksAffected: affected.size,
    peopleAffected: [...people],
    holidayWeeks,
  };
}

/**
 * Walk the remaining weeks and find when `remaining` dollars run out.
 *
 * `perPerson` is `[[personId, weeklyDollars], ...]` for one CLIN — each person's
 * contribution at full pace. Returns `{ exhaustWeek, weeksLeft }`, or nulls when the
 * money never runs out inside the walk.
 *
 * With an inert model this returns exactly `remaining / weekly`, but the caller
 * should still skip it in that case: see `buildAbsenceModel`.
 */
export function walkRunway({ perPerson, remaining, currentWeek, model }) {
  const full = perPerson.reduce((s, [, amt]) => s + amt, 0);
  if (full <= 0 || remaining <= 0) return { exhaustWeek: null, weeksLeft: null };

  let cum = 0;
  let week = currentWeek;
  const limit = currentWeek + MAX_PROJECTION_WEEKS;
  while (week < limit) {
    week += 1;
    let step = 0;
    for (const [who, amt] of perPerson) step += amt * model.factorFor(who, week);
    if (step > 0 && cum + step >= remaining) {
      // Land inside the week it happens, so a bend that buys three days shows
      // three days rather than rounding to the week boundary.
      const exhaustWeek = week - 1 + (remaining - cum) / step;
      return { exhaustWeek, weeksLeft: exhaustWeek - currentWeek };
    }
    cum += step;
  }
  return { exhaustWeek: null, weeksLeft: null };
}

// An ISO date shifted by whole days. A "starts on the 10th" entry is stored as
// absence through the 9th, and a "last day is the 20th" one as absence from the
// 21st — so all three kinds reduce to the same dated range and the projection keeps
// a single code path.
export function shiftDate(iso, days) {
  const t = parseDate(iso);
  return t == null ? "" : formatDate(t + days * DAY_MS);
}

// A person's absences, as the row chip reads them. Sorted so the soonest is first.
export function absencesFor(absences, personId) {
  return (absences || [])
    .filter((a) => a.person_id === personId)
    .sort((a, b) => String(a.start).localeCompare(String(b.start)));
}

// Workdays an absence covers — what the entry form echoes back, because "10 days"
// is the unit a user checks the arithmetic in and "2026-08-10 → 2026-08-21" is not.
export function absenceWorkdays(entry) {
  const s = parseDate(entry?.start);
  const e = parseDate(entry?.end);
  if (s == null || e == null || e < s) return 0;
  return workdaysBetween(s, e).length;
}
