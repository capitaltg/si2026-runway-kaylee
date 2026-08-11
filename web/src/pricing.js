// Contract-type spelling → canonical pricing-policy code, on the client.
//
// A deliberate mirror of `classify` in `server/app/pricing.py` (#76), the same way
// `sources.py` mirrors `pricing._key` without reaching into it. The ingest review
// screen has to decide which fields to offer *before* anything is saved, so there is
// no contract to ask the server about yet — and the thing it must not do is disagree
// with the server about what "Fixed Price Incentive" is. It used to: a prefix regex
// matching `cp|cr|cost|fpi` read the abbreviation and missed every full spelling, so
// an FPI line the classifier recognises kept its target-cost, target-profit, ceiling
// and share fields hidden during manual review (#163).
//
// `pricing.test.js` asserts this table against the Python one, so the two cannot
// quietly drift apart.

// Compact match key: lowercase, trailing parenthetical dropped, every non-alphanumeric
// character removed. Turns "T&M", "Time & Materials" and "CPFF (Completion Form)" into
// keys the synonym table holds literally.
function key(text) {
  let s = String(text ?? "").trim().toLowerCase();
  // Drop a trailing qualifier — "(Completion)", "(Term)", "(LOE)" — but only when
  // something precedes it, so a string that is *only* a parenthetical still gets a
  // chance to match.
  if (s.endsWith(")") && s.includes("(")) {
    const head = s.slice(0, s.lastIndexOf("(")).trim();
    if (head) s = head;
  }
  return s.replace(/[^a-z0-9]/g, "");
}

// Every realistic spelling listed literally rather than matched by prefix or
// containment: a substring match would happily read "FFP" out of "FFP/T&M" and invent a
// type for a mixed award, which is the one thing this must never do.
const SYNONYMS = {
  FFP: [
    "ffp",
    "firm fixed price",
    "firmfixedprice",
    "fixed price",
    "fp",
    "ffp loe",
    "ffploe",
    "firm fixed price level of effort",
  ],
  TM: [
    "tm",
    "t and m",
    "tandm",
    "time and materials",
    "timeandmaterials",
    "time materials",
    "timematerials",
    "tmlh",
    "lh",
    "labor hour",
    "laborhour",
    "labor hours",
    "labour hour",
  ],
  CPFF: [
    "cpff",
    "cost plus fixed fee",
    "costplusfixedfee",
  ],
  CPIF: ["cpif", "cost plus incentive fee", "costplusincentivefee"],
  CPAF: ["cpaf", "cost plus award fee", "costplusawardfee"],
  FPI: [
    "fpi",
    "fpif",
    "fixed price incentive",
    "fixedpriceincentive",
    "fixed price incentive firm target",
    "fixed price incentive firm",
  ],
};

const BY_KEY = new Map(
  Object.entries(SYNONYMS).flatMap(([code, spellings]) =>
    spellings.map((s) => [key(s), code]),
  ),
);

// Contract *vehicles*, not pricing types. An IDIQ or a BPA says how work is ordered,
// not how it is priced — the priced thing is the order underneath, which carries its
// own type.
const VEHICLES = [
  "idiq",
  "indefinite delivery indefinite quantity",
  "indefinite delivery/indefinite quantity",
  "id/iq",
  "bpa",
  "blanket purchase agreement",
  "boa",
  "basic ordering agreement",
  "gwac",
  "gsa schedule",
  "mas",
  "multiple award schedule",
  "requirements",
];

const VEHICLE_KEYS = new Set(VEHICLES.map(key));

// The codes that price with a cost and a fee — the two cost-plus families plus the
// incentive types. FPI is a fixed-price policy (FAR 16.403) that nonetheless prices
// from a target cost and a share ratio, which is exactly why it belongs here and why
// leaving it out was the bug.
const COST_OR_INCENTIVE = new Set(["CPFF", "CPIF", "CPAF", "FPI"]);

// `{ code, unknown }` — exactly one is set. `unknown` is "absent" for empty text,
// "vehicle" for an ordering vehicle, "unsupported" for text we cannot safely map.
export function classifyContractType(text) {
  if (!String(text ?? "").trim()) return { code: null, unknown: "absent" };
  const k = key(text);
  // Text carrying no alphanumerics at all ("???", "--") is still text we read and
  // failed to map; calling that "absent" would report a missing extraction where there
  // was a failed one.
  if (!k) return { code: null, unknown: "unsupported" };
  if (BY_KEY.has(k)) return { code: BY_KEY.get(k), unknown: null };
  if (VEHICLE_KEYS.has(k)) return { code: null, unknown: "vehicle" };
  return { code: null, unknown: "unsupported" };
}

// Should the review screen offer the cost/fee fields on a CLIN of this type?
export function offersCostFeeFields(text) {
  const { code } = classifyContractType(text);
  if (code) return COST_OR_INCENTIVE.has(code);
  // A vehicle, absent text, or unsupported text prices nothing safely.
  return false;
}
