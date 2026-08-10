import { money, moneyM, pct } from "./format.js";

// Runway Drafts (v1). Turn a contract's live burn payload into GovCon paperwork.
// Every number/date/ID here comes straight from `burn` — the model never authors
// figures. Prose sections carry deterministic "heuristic" copy so the whole
// document is usable with AI off; when AI is on the Drafts view replaces the
// single prose section's text with a streamed, phrased version.
//
// Structures follow the authoritative forms: the SF-1034 public voucher (GSA),
// the FAR 52.232-22 Limitation of Funds notification, and the DI-MGMT-80368A
// monthly Contractor's Progress, Status and Management Report.

export const DOC_TYPES = [
  { key: "funding", label: "Funding request", blurb: "FAR 52.232-22 Limitation of Funds letter" },
  { key: "invoice", label: "Invoice (SF-1034)", blurb: "Public voucher for costs incurred" },
  { key: "cdrl", label: "Status check-in", blurb: "Monthly CDRL progress report" },
];

const DRAFT_LABEL = "DRAFT — verify before submission";

// Return the value when it's real, else the literal [verify] placeholder so a
// missing figure is visibly flagged instead of shown as $0 or a guess.
export function vf(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : "[verify]";
  if (typeof value === "string") return value.trim() ? value : "[verify]";
  return "[verify]";
}

// moneyM but honours [verify] for absent inputs.
function moneyV(n) {
  return typeof n === "number" && Number.isFinite(n) ? moneyM(n) : "[verify]";
}

// Strip markdown syntax a model may emit despite instructions, so headings and
// bold/bullet markers never render literally in the finished document.
export function stripMd(s) {
  return String(s)
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .trim();
}

const pop = (c) => `${vf(c.pop_start)} to ${vf(c.pop_end)}`;
const contractorOf = (c) => vf(c.legal_name || c.name);

const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];

// Turn a week number into a real calendar date: pop_start + (week-1) weeks.
// Parsed as UTC so the result doesn't drift by timezone. Returns null if the
// inputs aren't usable, so callers can fall back to [verify].
export function weekToDate(popStart, week) {
  if (!popStart || week == null || !Number.isFinite(week)) return null;
  const base = new Date(`${popStart}T00:00:00Z`);
  if (isNaN(base.getTime())) return null;
  base.setUTCDate(base.getUTCDate() + Math.round((week - 1) * 7));
  return `${MONTHS[base.getUTCMonth()]} ${base.getUTCDate()}, ${base.getUTCFullYear()}`;
}

// An ISO date as prose. A letter to a contracting officer should not read
// "through 2027-01-21" next to "on or about July 24, 2026".
export function longDate(iso) {
  if (!iso) return null;
  const d = new Date(`${String(iso).slice(0, 10)}T00:00:00Z`);
  if (isNaN(d.getTime())) return null;
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
}

// Pick the CLIN a funding letter is about.
//
// `burn.funding` only lists lines in the amber "funding due" band. A line that
// has already blown through its allotment drops OUT of that list and into the
// tripwires — so the contract most in need of this letter produced one with
// every figure blank. Fall back to the funding tripwires, then to any CLIN whose
// spend has passed its funded slice, then to the worst labor line.
function fundingFocus(burn, opts) {
  const candidates = [
    ...(burn.funding || []),
    ...(burn.tripwires || []).filter((t) => t.limited_by === "funding"),
    ...(burn.clins || []).filter((c) => c.funds_exceeded || c.status === "over"),
    ...(burn.clins || []).filter((c) => c.is_labor && c.incrementally_funded),
  ];
  if (opts.focusClin) {
    const hit = candidates.find((f) => f.code === opts.focusClin);
    if (hit) return hit;
  }
  return candidates[0] || null;
}

// ── Incremental funding — FAR 52.232-22 Limitation of Funds notification ──────
function buildFunding(burn, opts) {
  const c = burn.contract || {};
  const item = fundingFocus(burn, opts) || {};
  const code = vf(item.code);
  const today = vf(opts.today);
  // The funding payload's `budget` equals the FUNDED slice for an incrementally
  // funded line (not the ceiling), so budget - funded is $0. Pull the CLIN's real
  // ceiling from burn.clins so the request is the actual unfunded remainder.
  const clin = (burn.clins || []).find((x) => x.code === item.code) || {};
  const funded = item.funded != null ? item.funded : clin.funded;
  const ceiling = clin.ceiling != null ? clin.ceiling : item.budget;
  const increment =
    typeof ceiling === "number" && typeof funded === "number" ? ceiling - funded : null;
  const exhaustDate = weekToDate(c.pop_start, item.exhaust_week);
  const popEnd = longDate(c.pop_end) || vf(c.pop_end);
  const exhaustWk = item.exhaust_week != null ? `week ${Math.round(item.exhaust_week)}` : null;
  // Prefer a real calendar date; fall back to the week number, then [verify].
  const exhaustPhrase = exhaustDate
    ? `on or about ${exhaustDate}${exhaustWk ? ` (${exhaustWk})` : ""}`
    : exhaustWk || "[verify]";

  // The heuristic tracks the clause's required 75% / next-60-days language.
  const heuristic =
    `In accordance with FAR 52.232-22 (Limitation of Funds), this letter provides ` +
    `notice that the costs we expect to incur under the referenced contract within ` +
    `the next 60 days, added to all costs previously incurred, will exceed 75 percent ` +
    `of the total amount presently allotted to ${code}. At the current rate of ` +
    `performance the funds allotted to this line are projected to be exhausted ` +
    `${exhaustPhrase}. To avoid a lapse in performance we respectfully request that an ` +
    `additional ${moneyV(increment)} be obligated to the contract to cover continued ` +
    `performance through ${popEnd}.`;

  return {
    docType: "funding",
    title: "Limitation of Funds — Notification & Request for Additional Funding",
    draftLabel: DRAFT_LABEL,
    meta: [
      { label: "Date", value: today },
      { label: "To", value: `${vf(c.contracting_officer)} (Contracting Officer)` },
      { label: "From", value: contractorOf(c) },
      { label: "Contract No.", value: vf(c.piid) },
      { label: "Reference", value: "FAR 52.232-22, Limitation of Funds" },
      { label: "Subject", value: `Funds limitation notice — ${code}` },
    ],
    sections: [
      { id: "notice", heading: "Notification", kind: "prose", text: heuristic },
      {
        id: "summary",
        heading: "Funding status",
        kind: "table",
        columns: ["Item", "Amount"],
        rows: [
          ["Total obligated to contract", moneyV(c.obligated)],
          ["Funds allotted to line", moneyV(funded)],
          ["Line ceiling (fully funded)", moneyV(ceiling)],
          ["Projected funds-exhaustion date", exhaustDate || exhaustWk || "[verify]"],
          ["Additional funds requested", moneyV(increment)],
          ["Period covered", `through ${popEnd}`],
        ],
      },
    ],
  };
}

// ── SF-1034 Public Voucher for Purchases and Services Other Than Personal ─────
function buildInvoice(burn, opts) {
  const c = burn.contract || {};
  const clins = burn.clins || [];
  const rows = clins.map((x) => [vf(x.code), vf(x.name), moneyV(x.spent)]);
  const totalSpent = clins.every((x) => typeof x.spent === "number")
    ? clins.reduce((s, x) => s + x.spent, 0)
    : null;

  return {
    docType: "invoice",
    title: "Public Voucher for Purchases and Services Other Than Personal (SF-1034)",
    draftLabel: DRAFT_LABEL,
    meta: [
      { label: "U.S. Department / Establishment", value: vf(c.agency) },
      { label: "Date voucher prepared", value: vf(opts.today) },
      { label: "Contract number and date", value: vf(c.piid) },
      { label: "Voucher No.", value: "[verify] (sequential)" },
      { label: "Payee's name and address", value: contractorOf(c) },
      { label: "Period of performance", value: pop(c) },
      { label: "Billing period", value: `through ${vf(burn.sync && burn.sync.latest_week)}` },
    ],
    sections: [
      {
        id: "basis",
        heading: null,
        kind: "text",
        text: "COST REIMBURSABLE — PROVISIONAL PAYMENT. Provisional payment subject to later audit.",
      },
      {
        id: "lines",
        heading: "Articles or services",
        kind: "table",
        columns: ["CLIN", "Articles or services", "Amount claimed"],
        rows: [...rows, ["", "Total", moneyV(totalSpent)]],
      },
      {
        id: "costsummary",
        heading: "Analysis of claimed costs",
        kind: "table",
        columns: ["", "Amount"],
        rows: [
          ["Amount claimed this voucher", moneyV(totalSpent)],
          ["Total costs incurred to date", moneyV(totalSpent)],
        ],
      },
      {
        id: "certification",
        heading: "Certification",
        kind: "text",
        text:
          "Pursuant to authority vested in me, I certify that this voucher is correct " +
          "and proper for payment. This is a draft generated from burn data and must be " +
          "reconciled to the accounting system of record before submission.",
      },
    ],
  };
}

// ── DI-MGMT-80368A Contractor's Progress, Status and Management Report ────────
function buildCdrl(burn, opts) {
  const c = burn.contract || {};
  const t = burn.totals || {};
  const clins = burn.clins || [];
  const flags = [
    ...(burn.tripwires || []).map((x) => `${x.code} is over pace (tripwire)`),
    ...(burn.funding || []).map((x) => `${x.code} needs its next funding mod`),
    ...(burn.underburn || []).map((x) => `${x.code} is under-burning`),
  ];
  const actions = (burn.funding || []).length
    ? `Obligation of incremental funding for ${(burn.funding || []).map((x) => x.code).join(", ")}.`
    : "None at this time.";

  const heuristic =
    `During this reporting period the team continued performance across ` +
    `${clins.length} CLIN(s), with ${moneyV(t.spent)} of ${moneyV(t.ceiling)} ` +
    `expended (${typeof t.pct === "number" ? pct(t.pct) : "[verify]"} of ceiling). ` +
    (flags.length
      ? `Attention items for next period: ${flags.join("; ")}. `
      : `All lines are tracking to plan. `) +
    `Next period the team will maintain current staffing and monitor burn against pace.`;

  return {
    docType: "cdrl",
    title: "Contractor's Progress, Status and Management Report",
    draftLabel: DRAFT_LABEL,
    meta: [
      { label: "Contractor", value: contractorOf(c) },
      { label: "Contract No.", value: vf(c.piid) },
      { label: "CDRL / Data item", value: "[verify] (e.g. A001, DI-MGMT-80368A)" },
      { label: "Reporting period", value: `through week ${vf(c.current_week)} of ${vf(c.total_weeks)}` },
      { label: "Date of report", value: vf(opts.today) },
      { label: "Classification", value: "Unclassified" },
    ],
    sections: [
      { id: "exec", heading: "1. Executive summary", kind: "prose", text: heuristic },
      {
        id: "status",
        heading: "2. Contract & funding status",
        kind: "table",
        columns: ["CLIN", "Description", "Spent", "Ceiling", "% burned", "Status"],
        rows: clins.map((x) => [
          vf(x.code),
          vf(x.name),
          moneyV(x.spent),
          moneyV(x.ceiling),
          typeof x.pct === "number" ? pct(x.pct) : "[verify]",
          vf(x.status_label || x.status),
        ]),
      },
      {
        id: "schedule",
        heading: "3. Schedule & risk status",
        kind: "text",
        text: flags.length ? flags.join("\n") : "No schedule or funding risks this period.",
      },
      {
        id: "actions",
        heading: "4. Government actions requested",
        kind: "text",
        text: actions,
      },
    ],
  };
}

const BUILDERS = { funding: buildFunding, invoice: buildInvoice, cdrl: buildCdrl };

export function buildDraft(docType, burn, opts = {}) {
  const build = BUILDERS[docType];
  if (!build) throw new Error(`Unknown draft type: ${docType}`);
  return build(burn || {}, opts || {});
}

// Flatten a Doc to plain text for the initial Copy content and print fallback.
export function renderDraftText(doc) {
  const lines = [doc.title.toUpperCase(), doc.draftLabel, ""];
  for (const m of doc.meta) lines.push(`${m.label}: ${m.value}`);
  for (const s of doc.sections) {
    lines.push("");
    if (s.heading) lines.push(s.heading.toUpperCase());
    if (s.kind === "table") {
      lines.push(s.columns.join("  |  "));
      for (const r of s.rows) lines.push(r.join("  |  "));
    } else {
      lines.push(stripMd(s.text));
    }
  }
  return lines.join("\n");
}
