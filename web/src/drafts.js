import { money, moneyM, pct } from "./format.js";

// Runway Drafts (v1). Turn a contract's live burn payload into GovCon paperwork.
// Every number/date/ID here comes straight from `burn` — the model never authors
// figures. Prose sections carry deterministic "heuristic" copy so the whole
// document is usable with AI off; when AI is on the Drafts view replaces the
// single prose section's text with a streamed, phrased version.

export const DOC_TYPES = [
  { key: "funding", label: "Funding request", blurb: "Incremental-funding memo to the CO" },
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

const pop = (c) => `${vf(c.pop_start)} to ${vf(c.pop_end)}`;
const contractorOf = (c) => vf(c.legal_name || c.name);

// Pick the CLIN a funding memo is about: the deep-linked one, else the first
// funding-status line, else the worst labor line.
function fundingFocus(burn, opts) {
  const funding = burn.funding || [];
  if (opts.focusClin) {
    const hit = funding.find((f) => f.code === opts.focusClin);
    if (hit) return hit;
  }
  return funding[0] || null;
}

function buildFunding(burn, opts) {
  const c = burn.contract || {};
  const item = fundingFocus(burn, opts) || {};
  const code = vf(item.code);
  // Requested increment = the still-unfunded slice of the line's ceiling. A
  // concrete number from data, not a projection.
  const increment =
    typeof item.budget === "number" && typeof item.funded === "number"
      ? item.budget - item.funded
      : null;

  const heuristic =
    `This letter requests incremental funding for ${code} under contract ` +
    `${vf(c.piid)}. At the current burn rate the line spends through its funded ` +
    `${moneyV(item.funded)} in week ${item.exhaust_week != null ? Math.round(item.exhaust_week) : "[verify]"}, ` +
    `roughly ${item.weeks_early != null ? item.weeks_early : "[verify]"} weeks before the period of ` +
    `performance ends. To keep the effort funded through completion we request an ` +
    `additional ${moneyV(increment)} be obligated to this line.`;

  return {
    docType: "funding",
    title: "Incremental Funding Request",
    draftLabel: DRAFT_LABEL,
    meta: [
      { label: "To", value: "[verify] (Contracting Officer)" },
      { label: "From", value: contractorOf(c) },
      { label: "Contract (PIID)", value: vf(c.piid) },
      { label: "Agency", value: vf(c.agency) },
      { label: "Period of performance", value: pop(c) },
      { label: "Currently obligated", value: moneyV(c.obligated) },
      { label: "Total ceiling", value: moneyV(c.contract_ceiling) },
    ],
    sections: [
      { id: "justification", heading: "Justification", kind: "prose", text: heuristic },
      {
        id: "summary",
        heading: "Funding summary",
        kind: "table",
        columns: ["Line", "Funded", "Ceiling", "Runs out", "Requested increment"],
        rows: [[
          code,
          moneyV(item.funded),
          moneyV(item.budget),
          item.exhaust_week != null ? `week ${Math.round(item.exhaust_week)}` : "[verify]",
          moneyV(increment),
        ]],
      },
    ],
  };
}

function buildInvoice(burn, _opts) {
  const c = burn.contract || {};
  const clins = burn.clins || [];
  const rows = clins.map((x) => [
    vf(x.code),
    vf(x.name),
    moneyV(x.spent),                 // cost incurred to date (labor + expenses)
    moneyV(x.ceiling),
    moneyV(x.remaining),
  ]);
  const totalSpent = clins.every((x) => typeof x.spent === "number")
    ? clins.reduce((s, x) => s + x.spent, 0)
    : null;

  return {
    docType: "invoice",
    title: "Public Voucher for Purchases and Services (SF-1034, draft)",
    draftLabel: DRAFT_LABEL,
    meta: [
      { label: "Voucher for", value: vf(c.agency) },
      { label: "Contractor", value: contractorOf(c) },
      { label: "Contract (PIID)", value: vf(c.piid) },
      { label: "Period of performance", value: pop(c) },
      { label: "Billing period", value: `through ${vf(burn.sync && burn.sync.latest_week)}` },
      { label: "Total amount claimed", value: moneyV(totalSpent) },
    ],
    sections: [
      {
        id: "lines",
        heading: "Cost incurred by CLIN",
        kind: "table",
        columns: ["CLIN", "Description", "Amount claimed", "Ceiling", "Remaining"],
        rows,
      },
      {
        id: "certification",
        heading: "Certification",
        kind: "text",
        text:
          "I certify that the above amounts are correct and represent costs " +
          "incurred in performance of the contract, that payment has not been " +
          "received, and that the amounts claimed conform to the contract terms. " +
          "This is a draft generated from burn data and must be reconciled to the " +
          "accounting system of record before submission.",
      },
    ],
  };
}

function buildCdrl(burn, _opts) {
  const c = burn.contract || {};
  const t = burn.totals || {};
  const clins = burn.clins || [];
  const flags = [
    ...(burn.tripwires || []).map((x) => `${x.code} is over pace (tripwire)`),
    ...(burn.funding || []).map((x) => `${x.code} needs its next funding mod`),
    ...(burn.underburn || []).map((x) => `${x.code} is under-burning`),
  ];

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
    title: "Monthly Status Report",
    draftLabel: DRAFT_LABEL,
    meta: [
      { label: "Contract (PIID)", value: vf(c.piid) },
      { label: "Contractor", value: contractorOf(c) },
      { label: "Reporting period", value: `through week ${vf(c.current_week)} of ${vf(c.total_weeks)}` },
      { label: "Overall status", value: vf(burn.hero && burn.hero.status) },
    ],
    sections: [
      {
        id: "burn",
        heading: "Burn summary by CLIN",
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
        id: "flags",
        heading: "Flags",
        kind: "text",
        text: flags.length ? flags.join("\n") : "No flags this period.",
      },
      {
        id: "narrative",
        heading: "Accomplishments & next-period plan",
        kind: "prose",
        text: heuristic,
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
      lines.push(s.text);
    }
  }
  return lines.join("\n");
}
