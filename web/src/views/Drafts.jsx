import React, { useEffect, useRef, useState } from "react";
import { getBurn, listContracts, draftProse } from "../api.js";
import { buildDraft, renderDraftText, stripMd, DOC_TYPES } from "../drafts.js";
import { panelStyle } from "../format.js";

const grotesk = "'Space Grotesk',sans-serif";

// Per-doc-type flavor: a kicker label above the title and the header band's lead
// colour, so a funding memo, an invoice, and a status report read as distinct
// documents at a glance.
const DOC_LOOK = {
  funding: { kicker: "MEMORANDUM", lead: "var(--accent)" },
  invoice: { kicker: "PUBLIC VOUCHER", lead: "var(--good)" },
  cdrl: { kicker: "STATUS REPORT", lead: "var(--accent2)" },
};

// Friendly contract label — never the PIID, which is unreadable when cycling
// through contracts in the picker.
const contractLabel = (c) =>
  c.nickname || c.name || c.legal_name || `Contract #${c.id}`;

const controlBtn = {
  height: 36,
  padding: "0 18px",
  borderRadius: 9,
  border: "none",
  background: "var(--accent)",
  color: "#fff",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
  boxShadow: "0 4px 12px rgba(67,97,238,.28)",
};
const ghostBtn = {
  height: 36,
  padding: "0 14px",
  borderRadius: 9,
  border: "1px solid var(--border)",
  background: "var(--panel)",
  color: "var(--text)",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};
const divider = { width: 1, height: 30, background: "var(--border)", margin: "0 2px" };

// Render a Doc (from buildDraft) as a formatted, letterhead-style page. The prose
// text may have been swapped by the AI stream before this runs; afterward the
// whole page is contentEditable so the PM can tweak numbers and prose alike.
function docToHtml(doc) {
  const esc = (s) =>
    String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const look = DOC_LOOK[doc.docType] || { kicker: "DOCUMENT", lead: "var(--accent)" };
  const lead = look.lead;

  // Accent gradient header band. print-color-adjust keeps it on the page even
  // when the browser's "background graphics" print option is off.
  const header =
    `<div style="background:linear-gradient(135deg,${lead},var(--accent2));color:#fff;` +
    `padding:22px 30px;-webkit-print-color-adjust:exact;print-color-adjust:exact">` +
    `<div style="font-size:11px;letter-spacing:.18em;font-weight:700;opacity:.85;margin-bottom:6px">${esc(look.kicker)}</div>` +
    `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:18px">` +
    `<h1 style="font-family:${grotesk};font-size:23px;font-weight:700;margin:0;line-height:1.2">${esc(doc.title)}</h1>` +
    `<span style="border:1px solid rgba(255,255,255,.65);border-radius:20px;padding:4px 12px;font-size:10.5px;` +
    `font-weight:700;letter-spacing:.06em;white-space:nowrap;flex:0 0 auto">${esc(doc.draftLabel)}</span>` +
    `</div></div>`;

  // Meta as a clean two-column definition block.
  const meta =
    `<div style="background:var(--panel2);border:1px solid var(--border);border-radius:12px;` +
    `padding:15px 18px;margin-bottom:22px;display:grid;grid-template-columns:auto 1fr;gap:7px 20px;font-size:12.5px">` +
    doc.meta
      .map(
        (m) =>
          `<div style="color:var(--dim);font-weight:600">${esc(m.label)}</div>` +
          `<div style="color:var(--text)">${esc(m.value)}</div>`
      )
      .join("") +
    `</div>`;

  const body = doc.sections
    .map((s) => {
      const heading = s.heading
        ? `<h2 style="font-family:${grotesk};font-size:13px;text-transform:uppercase;letter-spacing:.07em;` +
          `color:${lead};border-left:3px solid ${lead};padding-left:10px;margin:24px 0 11px">${esc(s.heading)}</h2>`
        : "";
      if (s.kind === "table") {
        return (
          heading +
          `<table style="border-collapse:collapse;width:100%;font-size:12.5px;margin-bottom:8px">` +
          `<thead><tr>${s.columns
            .map(
              (c) =>
                `<th style="text-align:left;background:var(--panel2);border-bottom:2px solid ${lead};` +
                `padding:8px 10px;color:var(--dim);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.04em">${esc(c)}</th>`
            )
            .join("")}</tr></thead><tbody>${s.rows
            .map(
              (r) =>
                `<tr>${r
                  .map(
                    (cell) =>
                      `<td style="border-bottom:1px solid var(--border);padding:8px 10px;color:var(--text)">${esc(cell)}</td>`
                  )
                  .join("")}</tr>`
            )
            .join("")}</tbody></table>`
        );
      }
      const proseAttr = s.kind === "prose" ? ' data-prose="1"' : "";
      return (
        heading +
        `<p${proseAttr} style="font-size:13px;line-height:1.7;color:var(--text);white-space:pre-wrap;margin:0 0 12px">${esc(stripMd(s.text))}</p>`
      );
    })
    .join("");

  // Signature block — letters and reports get signed; the invoice already carries
  // its own certification section.
  const signature =
    doc.docType === "invoice"
      ? ""
      : `<div style="margin-top:34px;font-size:12.5px;color:var(--text)">Respectfully,` +
        `<div style="margin-top:40px;border-top:1px solid var(--text);width:250px;padding-top:5px;color:var(--dim);font-size:12px">` +
        `Authorized Representative · [verify]</div></div>`;

  const html =
    header + `<div style="padding:24px 30px 32px">` + meta + body + signature + `</div>`;
  // Every remaining [verify] is a figure the contract data genuinely does not
  // carry (a voucher number, a CDRL item). Make them look like the blanks they
  // are rather than hiding in the prose, and give them a click target — the
  // page handler selects the whole chip so typing replaces it outright.
  return html.replace(
    /\[verify\]/g,
    `<span class="verify-chip" title="Click to fill this in">[verify]</span>`
  );
}

export default function Drafts({ contractId, setActiveId, aiEnabled, pendingDocType, onConsumedPending }) {
  const [contracts, setContracts] = useState([]);
  const [docType, setDocType] = useState(pendingDocType || "funding");
  const [status, setStatus] = useState("idle"); // idle | building | streaming | ready
  const [aiNote, setAiNote] = useState(null);
  const pageRef = useRef(null);
  const docRef = useRef(null); // the current Doc object (numbers + prose)

  // Populate the contract picker; default the active contract if App hasn't set one.
  useEffect(() => {
    listContracts()
      .then((cs) => {
        setContracts(cs);
        if (contractId == null && cs.length) setActiveId(cs[0].id);
      })
      .catch(() => setContracts([]));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Render the current Doc into the (read-only during build) page.
  function paint(doc) {
    docRef.current = doc;
    if (pageRef.current) pageRef.current.innerHTML = docToHtml(doc);
  }

  async function generate(nextType = docType) {
    if (contractId == null) return;
    setStatus("building");
    setAiNote(null);
    let burn;
    try {
      burn = await getBurn(contractId);
    } catch (e) {
      setStatus("idle");
      setAiNote(`Couldn't load burn data: ${e.message}`);
      return;
    }
    // Deterministic scaffold + heuristic prose first — this is the AI-off result.
    // The report date is today's real date (the app is used live); the projected
    // exhaustion date comes from the timesheet-derived burn. With live-syncing
    // timesheets these line up — in the demo the fixed timesheets can lag today.
    const today = new Date().toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
    const doc = buildDraft(nextType, burn, { today });
    paint(doc);
    // Editable immediately — the whole document (numbers, tables, prose) can be
    // tweaked right away, even while AI phrasing streams into the prose section.
    if (pageRef.current) pageRef.current.contentEditable = "true";

    const proseSection = doc.sections.find((s) => s.kind === "prose");
    if (!aiEnabled || !proseSection) {
      setStatus("ready");
      return;
    }
    // AI on: stream a phrased version of the prose section over the top, writing
    // ONLY into that paragraph node so edits elsewhere in the doc aren't clobbered.
    setStatus("streaming");
    const proseEl = pageRef.current && pageRef.current.querySelector("[data-prose]");
    let streamed = "";
    try {
      await draftProse({ contractId, docType: nextType }, (chunk) => {
        streamed += chunk;
        if (proseEl) proseEl.textContent = stripMd(streamed);
      });
      if (!streamed.trim()) setAiNote("AI unavailable — using standard wording.");
    } catch {
      // The heuristic prose is already in place from the initial paint; keep it.
      setAiNote("AI unavailable — using standard wording.");
    }
    setStatus("ready");
  }

  // Consume a funding deep-link once: select the doc type and auto-generate.
  useEffect(() => {
    if (pendingDocType && contractId != null) {
      setDocType(pendingDocType);
      generate(pendingDocType);
      onConsumedPending?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingDocType, contractId]);

  // Clicking a [verify] blank selects the whole chip, so the first keystroke
  // replaces it instead of typing alongside the brackets.
  function onPageClick(e) {
    const chip = e.target.closest?.(".verify-chip");
    if (!chip || !pageRef.current) return;
    const range = document.createRange();
    range.selectNodeContents(chip);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  // A blank stops being a blank the moment it holds a real value. Drop the
  // highlight as soon as the text differs from the placeholder, so a finished
  // document has no orange left on it and the remaining chips still mean
  // "this one is unanswered".
  function onPageInput() {
    if (!pageRef.current) return;
    for (const chip of pageRef.current.querySelectorAll(".verify-chip")) {
      const text = chip.textContent.trim();
      if (text && text !== "[verify]") {
        chip.classList.remove("verify-chip");
        chip.removeAttribute("title");
      }
    }
  }

  // Append a fresh paragraph at the end of the body and drop the cursor in it —
  // the draft covers the standard ground, but a real memo usually needs one more
  // sentence about this particular contract.
  function addParagraph() {
    const page = pageRef.current;
    if (!page) return;
    const body = page.lastElementChild || page;
    const p = document.createElement("p");
    p.setAttribute(
      "style",
      "font-size:13px;line-height:1.7;color:var(--text);white-space:pre-wrap;margin:0 0 12px"
    );
    p.textContent = "";
    body.appendChild(p);
    p.focus?.();
    const range = document.createRange();
    range.selectNodeContents(p);
    range.collapse(true);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    p.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  function onCopy() {
    const text = pageRef.current
      ? pageRef.current.innerText
      : renderDraftText(docRef.current || { title: "", draftLabel: "", meta: [], sections: [] });
    navigator.clipboard?.writeText(text);
  }

  const busy = status === "building" || status === "streaming";
  const ready = status === "ready";

  return (
    <div style={{ padding: "24px 26px 60px" }}>
      {/* No heading here — the top bar owns the view title (#201). The description
          stays: whether AI is on changes what the button will produce. */}
      <div className="no-print" style={{ marginBottom: 18, fontSize: 13.5, color: "var(--dim)" }}>
        Generate GovCon paperwork from live burn data. Numbers come straight from the
        contract; {aiEnabled ? "AI tailors the wording." : "turn on AI for tailored wording."}
      </div>

      {/* control bar — three distinct zones: contract · what to generate · operations */}
      <div
        className="no-print"
        style={{
          display: "flex",
          gap: 10,
          flexWrap: "nowrap",
          alignItems: "center",
          marginBottom: 16,
          padding: 10,
          background: "var(--panel)",
          border: "1px solid var(--border)",
          borderRadius: 12,
          overflowX: "auto",
        }}
      >
        {/* zone 1: contract picker (by name, never PIID) */}
        <select
          value={contractId ?? ""}
          onChange={(e) => setActiveId(Number(e.target.value))}
          style={{ ...ghostBtn, cursor: "pointer", maxWidth: 220 }}
        >
          {contracts.map((c) => (
            <option key={c.id} value={c.id}>{contractLabel(c)}</option>
          ))}
        </select>

        <div style={divider} />

        {/* zone 2: what to generate — a segmented control */}
        <div style={{ display: "flex", background: "var(--panel2)", border: "1px solid var(--border)", borderRadius: 10, padding: 3, gap: 3 }}>
          {DOC_TYPES.map((d) => {
            const on = docType === d.key;
            return (
              <button
                key={d.key}
                title={d.blurb}
                onClick={() => setDocType(d.key)}
                style={{
                  height: 30,
                  padding: "0 13px",
                  borderRadius: 8,
                  border: "none",
                  cursor: "pointer",
                  fontSize: 12.5,
                  fontWeight: 600,
                  background: on ? "var(--accent)" : "transparent",
                  color: on ? "#fff" : "var(--dim)",
                  boxShadow: on ? "0 2px 8px rgba(67,97,238,.28)" : "none",
                }}
              >
                {d.label}
              </button>
            );
          })}
        </div>

        {/* zone 3: operations — always present, greyed until a draft is ready.
            Generate has a fixed width so its label changing (Generate →
            Generating… → ✨ tailoring…) never reflows the toolbar. */}
        <div style={{ display: "flex", gap: 8, marginLeft: "auto", flexShrink: 0 }}>
          <button
            onClick={() => generate()}
            disabled={contractId == null || busy}
            style={{ ...controlBtn, minWidth: 130, whiteSpace: "nowrap", opacity: contractId == null || busy ? 0.55 : 1 }}
          >
            {status === "streaming" ? "✨ tailoring…" : status === "building" ? "Generating…" : "Generate"}
          </button>
          <button
            onClick={addParagraph}
            disabled={!ready}
            title="Add a paragraph at the end of the draft"
            style={{ ...ghostBtn, opacity: ready ? 1 : 0.45, cursor: ready ? "pointer" : "default" }}
          >
            + Paragraph
          </button>
          <button onClick={onCopy} disabled={!ready} style={{ ...ghostBtn, opacity: ready ? 1 : 0.45, cursor: ready ? "pointer" : "default" }}>
            Copy
          </button>
          <button onClick={() => window.print()} disabled={!ready} style={{ ...ghostBtn, opacity: ready ? 1 : 0.45, cursor: ready ? "pointer" : "default" }}>
            Export to PDF
          </button>
        </div>
      </div>

      {aiNote && (
        <div className="no-print" style={{ fontSize: 12, color: "var(--dim)", marginBottom: 10 }}>{aiNote}</div>
      )}

      {/* the editable, printable document page */}
      {status === "idle" ? (
        <div style={{ ...panelStyle, color: "var(--dim)", fontSize: 13 }}>
          Pick a contract and document type, then Generate.
        </div>
      ) : (
        <>
          {ready && (
            <div
              className="no-print"
              style={{ fontSize: 12, color: "var(--dim)", marginBottom: 10 }}
            >
              Editable — click any figure or paragraph to change it. Anything still
              marked <span className="verify-chip">[verify]</span> is a value this
              contract's data doesn't carry.
            </div>
          )}
          <div
            ref={pageRef}
            className="draft-page"
            onClick={onPageClick}
            onInput={onPageInput}
            style={{ ...panelStyle, minHeight: 300, padding: 0, overflow: "hidden", outline: "none" }}
            suppressContentEditableWarning
          />
        </>
      )}
    </div>
  );
}
