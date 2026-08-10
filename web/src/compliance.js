// #66 — phrasing for the compliance check. The verdicts are the server's
// (`server/app/compliance.py`); nothing here decides whether somebody qualifies.
//
// It is all in one pure module for the reason the check itself is: the sentences are
// the risky part. "3 of 11 checked" and "3 of 40" are the same data and different
// claims, and the wrong one is a compliance report that overstates its own coverage.

export const CLEARANCE_GAP = "clearance_gap";
export const UNDER_QUALIFIED = "under_qualified";
export const UNKNOWN = "unknown";
export const OVER_QUALIFIED = "over_qualified";
export const COMPLIANT = "compliant";
export const NO_FLOOR = "no_floor";
export const UNPRICED = "unpriced";

// The badge. `unknown` gets its own visible treatment rather than being left blank —
// an un-annotated contract is the day-one state of every synced person, and a blank
// cell reads as "fine" to everyone who has ever looked at a table.
//
// Tones map onto the app's existing status vocabulary rather than inventing colours:
// red is a stop-work fact, amber is a finding, slate is an open question, green is
// checked and clear.
const BADGES = {
  [CLEARANCE_GAP]: {
    label: "Clearance",
    tone: "red",
    title: "Holds a lower clearance than this labor category requires",
  },
  [UNDER_QUALIFIED]: {
    label: "Under-qual",
    tone: "amber",
    title: "Does not meet a minimum this labor category requires",
  },
  [UNKNOWN]: {
    label: "Unchecked",
    tone: "slate",
    title: "No qualifications on file to check against this category",
  },
  [OVER_QUALIFIED]: {
    label: "Over-qual",
    tone: "blue",
    title: "Meets a better-paid category's minimums — not a violation",
  },
  [COMPLIANT]: {
    label: "Qualified",
    tone: "green",
    title: "Meets every minimum this labor category prints",
  },
  // No badge at all: the award prints no minimums for this line, so there is no
  // question to answer and a badge would imply somebody answered it.
  [NO_FLOOR]: null,
  // Also no badge — but for a different reason, and one the row already reports. These
  // hours don't resolve to a priced category at all, which is the ⚠ the cell carries
  // from #64. A second badge saying the same thing in compliance language would read
  // as two problems where there is one.
  [UNPRICED]: null,
};

export function badge(status) {
  return BADGES[status] || null;
}

// Only these two are findings. Used to decide whether a surface says anything at all,
// so that "nobody has entered any quals" never renders as a clean bill of health.
export function isFinding(status) {
  return status === CLEARANCE_GAP || status === UNDER_QUALIFIED;
}

const FIELD_WORDS = {
  education: "education",
  years_experience: "years of experience",
  clearance: "clearance",
};

// "3 yrs experience, Senior Cyber SME requires 10." Both numbers, always — a failure
// that names only the requirement is unarguable-with, and the first thing anybody
// does with a compliance flag is argue with it.
export function failureText(failure, lcat) {
  const word = FIELD_WORDS[failure.field] || failure.field;
  const held = failure.held == null || failure.held === "" ? "nothing on file" : failure.held;
  const where = lcat ? `${lcat} requires` : "requires";
  if (failure.field === "years_experience") {
    return `${held} yrs ${word}, ${where} ${failure.required}`;
  }
  return `${word} ${held}, ${where} ${failure.required}`;
}

// Why a field couldn't be checked, and — the part that matters — whose job it is.
// Two of these three are the award document's problem, and telling the user to go
// type more quals would send them to fix something that isn't broken.
export function uncheckedText(entry) {
  const word = FIELD_WORDS[entry.field] || entry.field;
  switch (entry.reason) {
    case "no_value":
      return `no ${word} on file — add it to check this`;
    case "floor_not_comparable":
      return `the award's ${word} minimum isn't a standard level, so it can't be checked automatically`;
    case "value_not_comparable":
      return `the ${word} on file isn't a standard value — re-enter it to check this`;
    default:
      return `${word} not checked`;
  }
}

// The rollup sentence. The two denominators stay apart in the copy exactly as they do
// in the payload: findings are reported over the *checked* population and the
// unchecked count is stated next to them, never folded in or averaged away.
export function rollupText(roll) {
  if (!roll || !roll.people) return "Nobody is charging this line.";
  const parts = [];
  if (roll.under_qualified) {
    parts.push(
      `${roll.under_qualified} of ${roll.checked} checked ${roll.checked === 1 ? "person" : "people"} under-qualified`,
    );
  }
  if (roll.clearance_gap) {
    parts.push(`${roll.clearance_gap} clearance ${roll.clearance_gap === 1 ? "gap" : "gaps"}`);
  }
  if (roll.over_qualified) {
    parts.push(`${roll.over_qualified} over-qualified`);
  }
  if (!parts.length && roll.checked) {
    parts.push(`${roll.checked} checked, all clear`);
  }
  if (roll.not_checked) {
    parts.push(`${roll.not_checked} not yet checked`);
  }
  if (roll.no_floor) {
    parts.push(`${roll.no_floor} on lines with no printed minimums`);
  }
  // Named as what it is — a pricing gap (#64), not a compliance result. Rolling this
  // into the line above would report unpriced hours as "the award printed no minimums",
  // which is false: there is no category resolved to have minimums on.
  if (roll.unpriced) {
    parts.push(`${roll.unpriced} on hours with no priced category`);
  }
  return parts.join(" · ");
}

// Sort order for a findings list: worst first, and unchecked above clear so the work
// left to do outranks the work already done.
const RANK = [
  CLEARANCE_GAP,
  UNDER_QUALIFIED,
  UNKNOWN,
  OVER_QUALIFIED,
  COMPLIANT,
  NO_FLOOR,
  UNPRICED,
];

export function severity(status) {
  const i = RANK.indexOf(status);
  return i === -1 ? RANK.length : i;
}

export function bySeverity(a, b) {
  return severity(a?.compliance_status) - severity(b?.compliance_status);
}
