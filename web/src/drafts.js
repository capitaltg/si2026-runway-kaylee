import { money, moneyM, pct, CLAUSE_NAME } from "./format.js";

// Runway Drafts (v1). Turn a contract's live burn payload into GovCon paperwork.
// Every number/date/ID here comes straight from `burn` — the model never authors
// figures. Prose sections carry deterministic "heuristic" copy so the whole
// document is usable with AI off; when AI is on the Drafts view replaces the
// single prose section's text with a streamed, phrased version.
//
// Structures follow the authoritative forms: the SF-1034 public voucher (GSA), the
// funding-limitation notification required by whichever FAR clause governs this
// contract type (see CLAUSE_LETTERS), and the DI-MGMT-80368A monthly Contractor's
// Progress, Status and Management Report.

export const DOC_TYPES = [
  { key: "funding", label: "Funding request", blurb: "Funding-limitation notification to the CO" },
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

const LOF = "52.232-22";
const LOC = "52.232-20";
const TM_PAYMENTS = "52.232-7";

// The FAR clause that governs decides the whole letter: the notice threshold it has
// to report, the limit its figures measure against, and the remedy it asks for.
// Which clause applies is not a property of the contract type alone — Limitation of
// Cost governs a fully funded cost contract and Limitation of Funds an incrementally
// funded one — so the engine resolves it per CLIN and puts the answer on the payload
// as `funding_clause` (#81). This table is the letter each clause produces; nothing
// here picks a clause, and a clause missing from the table produces no letter rather
// than the nearest one. This document cites a clause by number to a contracting
// officer, and the wrong number is worse than sending nothing.
//
// `amount` is the dollar remedy being requested, computed from payload figures only.
// It differs per clause because the remedies are different acts: an obligation of
// funds already authorised, an increase in an authorised estimate, or a renegotiated
// ceiling. Asking for the wrong one is asking the CO to do something they can't.
const CLAUSE_LETTERS = {
  // Incrementally funded cost reimbursement. 52.232-22(c) requires notice once the
  // costs expected in the next 60 days plus those already incurred pass 75 percent of
  // the amount *allotted* — the allotment, not the contract's estimated cost. The
  // remedy is an obligation, so the ask is the unfunded remainder of the ceiling.
  [LOF]: {
    name: CLAUSE_NAME[LOF],
    title: "Limitation of Funds — Notification & Request for Additional Funding",
    subject: "Funds limitation notice",
    pct: "75 percent",
    basis: (code) => `the total amount presently allotted to ${code}`,
    exhausts: "the funds allotted to this line are projected to be exhausted",
    ask: (amount, popEnd) =>
      `To avoid a lapse in performance we respectfully request that an additional ` +
      `${amount} be obligated to the contract to cover continued performance through ` +
      `${popEnd}.`,
    allottedRow: "Funds allotted to line",
    limitRow: "Line ceiling (fully funded)",
    exhaustRow: "Projected funds-exhaustion date",
    askRow: "Additional funds requested",
    amount: (clin, funded, ceiling) => gap(ceiling, funded),
  },
  // Fully funded cost reimbursement. 52.232-20(b) carries the same 75 percent /
  // 60-day trigger, but against the contract's *estimated cost*, past which the
  // Government is not obligated to reimburse. The remedy is a modification raising
  // that estimate, not a new obligation — so the ask is the projected overrun the fee
  // engine already computes (#80), not an unfunded remainder. On a fully funded line
  // that remainder is zero by construction, which is exactly what citing -22 here
  // used to produce: a letter requesting $0.
  [LOC]: {
    name: CLAUSE_NAME[LOC],
    title: "Limitation of Cost — Notification & Request for Increase in Estimated Cost",
    subject: "Cost limitation notice",
    pct: "75 percent",
    basis: (code) => `the estimated cost of ${code}`,
    exhausts: "the estimated cost of this line is projected to be reached",
    ask: (amount, popEnd) =>
      `To avoid a lapse in performance we respectfully request that the estimated cost ` +
      `of the contract be increased by ${amount} to cover continued performance through ` +
      `${popEnd}.`,
    allottedRow: null,
    limitRow: "Estimated cost of line",
    exhaustRow: "Projected date estimated cost is reached",
    askRow: "Increase in estimated cost requested",
    amount: (clin) => projectedOverrun(clin),
  },
  // T&M / labor-hour. The limit here is the negotiated ceiling price, which the
  // contractor exceeds at its own risk (FAR 16.601(c)(1)), and the clause's notice
  // runs against that ceiling rather than an allotment. Deliberately asks for no
  // dollar figure: the remedy is a negotiated ceiling increase, and there is no
  // projected-overrun number on a T&M line to put in its place — the fee is inside
  // the billing rate, so `fee_position` is null here by design.
  [TM_PAYMENTS]: {
    name: CLAUSE_NAME[TM_PAYMENTS],
    title: "Ceiling Price — Notification & Request for Ceiling Increase",
    subject: "Ceiling price notice",
    pct: "85 percent",
    basis: (code) => `the ceiling price of ${code}`,
    exhausts: "the ceiling price of this line is projected to be reached",
    ask: (amount, popEnd) =>
      `To avoid a lapse in performance we respectfully request an increase in the ` +
      `ceiling price sufficient to cover continued performance through ${popEnd}.`,
    allottedRow: "Funds allotted to line",
    limitRow: "Ceiling price",
    exhaustRow: "Projected date ceiling price is reached",
    askRow: null,
    amount: () => null,
  },
};

// a - b, or null when either side isn't a real figure, so moneyV shows [verify]
// instead of arithmetic on an absent number.
const gap = (a, b) => (typeof a === "number" && typeof b === "number" ? a - b : null);

// The projected cost overrun above estimated cost (#80), which is the increase a
// Limitation of Cost letter asks for. Null unless the engine actually projects one —
// a letter is only written when it does, and an overrun of zero is not a request.
function projectedOverrun(clin) {
  const p = clin && clin.fee_position && clin.fee_position.projected;
  return p && typeof p.overrun === "number" && p.overrun > 0 ? p.overrun : null;
}

// The clause to write under. Read off the payload, never inferred from the dollars.
// A payload predating #81 carries no `funding_clause` at all, in which case this
// falls back to Limitation of Funds — the same assumption the engine made for an
// untyped award before #81 and still makes (`UNKNOWN.funding_clauses`). `clauseAssumed`
// is what keeps that from reading as a fact.
function clauseFor(item, clin) {
  if (item && "funding_clause" in item) return item.funding_clause;
  if (clin && "funding_clause" in clin) return clin.funding_clause;
  return LOF;
}

// Whether the citation is a read or an assumption. `pricing_policy.known` is false
// when the award never stated a contract type, and #81 deliberately kept the legacy
// -22 assumption in that state rather than citing nothing. That is only safe while
// every surface printing the clause also prints that it is an assumption — this is
// that surface, and it's the one a contracting officer reads.
const clauseAssumed = (clin) => !(clin && clin.pricing_policy && clin.pricing_policy.known);

// Fixed-price work has no limitation-of-funds mechanic to notify under, so there is
// no letter to write: the Government owes the price and the overrun is the
// contractor's. Returned as a document rather than thrown so the view can say why
// instead of failing, and with no `kind: "prose"` section on purpose — the AI pass
// writes over the prose section, and a streamed funding justification is precisely
// the document a fixed-price contract must not produce.
function noClauseDoc(burn, opts, clin) {
  const c = burn.contract || {};
  const policy = (clin && clin.pricing_policy) || {};
  const label = vf(policy.label);
  return {
    docType: "funding",
    title: "Funding Notification — Not Applicable to This Contract Type",
    draftLabel: DRAFT_LABEL,
    meta: [
      { label: "Date", value: vf(opts.today) },
      { label: "Contract No.", value: vf(c.piid) },
      { label: "Contract type", value: label },
      { label: "Reference", value: "None — no limitation-of-funds clause applies" },
    ],
    sections: [
      {
        id: "notice",
        heading: "Why there is no funding request to send",
        kind: "text",
        text:
          `${label} work carries no Limitation of Funds or Limitation of Cost clause. ` +
          `The price is firm, so cost past that price is borne by the contractor rather ` +
          `than notified to the Government: an overrun here is a margin problem, not a ` +
          `funding one, and the remedy is a change in scope or price negotiated as a ` +
          `modification — not an obligation of additional funds.\n\n` +
          `If this line is in fact cost-reimbursement or time-and-materials, the ` +
          `contract type recorded against it is wrong. Correct it in the award data and ` +
          `regenerate rather than sending this notice.`,
      },
    ],
  };
}

// ── Funding notification — under the clause the payload says governs ───────────
function buildFunding(burn, opts) {
  const c = burn.contract || {};
  const item = fundingFocus(burn, opts) || {};
  // The funding payload's `budget` equals the FUNDED slice for an incrementally
  // funded line (not the ceiling), so budget - funded is $0. Pull the CLIN's real
  // ceiling from burn.clins so the request is the actual unfunded remainder.
  const clin = (burn.clins || []).find((x) => x.code === item.code) || {};
  const clause = clauseFor(item, clin);
  const spec = clause ? CLAUSE_LETTERS[clause] : null;
  if (!spec) return noClauseDoc(burn, opts, clin);

  const code = vf(item.code);
  const today = vf(opts.today);
  const funded = item.funded != null ? item.funded : clin.funded;
  const ceiling = clin.ceiling != null ? clin.ceiling : item.budget;
  const request = spec.amount(clin, funded, ceiling);
  const exhaustDate = weekToDate(c.pop_start, item.exhaust_week);
  const popEnd = longDate(c.pop_end) || vf(c.pop_end);
  const exhaustWk = item.exhaust_week != null ? `week ${Math.round(item.exhaust_week)}` : null;
  // Prefer a real calendar date; fall back to the week number, then [verify].
  const exhaustPhrase = exhaustDate
    ? `on or about ${exhaustDate}${exhaustWk ? ` (${exhaustWk})` : ""}`
    : exhaustWk || "[verify]";

  // The heuristic tracks the cited clause's own notice language — its percentage, its
  // 60-day lookahead, and the quantity that percentage is *of*.
  const heuristic =
    `In accordance with FAR ${clause} (${spec.name}), this letter provides notice that ` +
    `the costs we expect to incur under the referenced contract within the next 60 days, ` +
    `added to all costs previously incurred, will exceed ${spec.pct} of ` +
    `${spec.basis(code)}. At the current rate of performance ${spec.exhausts} ` +
    `${exhaustPhrase}. ${spec.ask(moneyV(request), popEnd)}`;

  // Both caveats have to survive the AI pass, which rewrites the prose section and
  // only the prose section — so they get a section of their own rather than being
  // sentences inside the notice, where streaming would silently drop them.
  const caveats = [];
  if (clauseAssumed(clin))
    caveats.push(
      `Contract type: the award data available for ${code} does not state one. FAR ` +
        `${clause} is cited above on the assumption that this is an incrementally funded ` +
        `cost-reimbursement line — the same assumption the burn figures make. Confirm the ` +
        `clause against the executed contract before submission.`
    );
  if (clause === TM_PAYMENTS && clin.incrementally_funded)
    caveats.push(
      `Two limits apply: FAR ${TM_PAYMENTS} governs the ceiling price, but the funds ` +
        `presently allotted to ${code} (${moneyV(funded)}) are below that ceiling and are ` +
        `the earlier constraint. An obligation of the remaining ${moneyV(gap(ceiling, funded))} ` +
        `is required to reach the ceiling whether or not the ceiling itself is raised.`
    );

  return {
    docType: "funding",
    title: spec.title,
    draftLabel: DRAFT_LABEL,
    meta: [
      { label: "Date", value: today },
      { label: "To", value: `${vf(c.contracting_officer)} (Contracting Officer)` },
      { label: "From", value: contractorOf(c) },
      { label: "Contract No.", value: vf(c.piid) },
      {
        label: "Reference",
        value:
          `FAR ${clause}, ${spec.name}` +
          (clauseAssumed(clin) ? " (assumed — contract type not stated in the award)" : ""),
      },
      { label: "Subject", value: `${spec.subject} — ${code}` },
    ],
    sections: [
      { id: "notice", heading: "Notification", kind: "prose", text: heuristic },
      ...(caveats.length
        ? [{ id: "caveats", heading: null, kind: "text", text: caveats.join("\n\n") }]
        : []),
      {
        id: "summary",
        heading: "Funding status",
        kind: "table",
        columns: ["Item", "Amount"],
        rows: [
          ["Total obligated to contract", moneyV(c.obligated)],
          ...(spec.allottedRow ? [[spec.allottedRow, moneyV(funded)]] : []),
          [spec.limitRow, moneyV(ceiling)],
          [spec.exhaustRow, exhaustDate || exhaustWk || "[verify]"],
          ...(spec.askRow ? [[spec.askRow, moneyV(request)]] : []),
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
