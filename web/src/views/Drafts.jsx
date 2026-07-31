import React, { useEffect, useRef, useState } from "react";
import { getBurn, listContracts, draftProse } from "../api.js";
import { buildDraft, renderDraftText, DOC_TYPES } from "../drafts.js";
import { panelStyle } from "../format.js";

const grotesk = "'Space Grotesk',sans-serif";

const controlBtn = {
  height: 36,
  padding: "0 16px",
  borderRadius: 9,
  border: "none",
  background: "var(--accent)",
  color: "#fff",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};
const ghostBtn = {
  height: 36,
  padding: "0 14px",
  borderRadius: 9,
  border: "1px solid var(--border)",
  background: "var(--panel2)",
  color: "var(--text)",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};

// Render a Doc (from buildDraft) to HTML for the editable page. Prose text can be
// swapped by the AI stream before this runs; after generation the whole page is
// contentEditable so the PM can tweak both numbers and prose.
function docToHtml(doc) {
  const esc = (s) =>
    String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const parts = [
    `<h1 style="font-family:${grotesk};font-size:22px;margin:0 0 4px">${esc(doc.title)}</h1>`,
    `<div style="color:var(--bad);font-weight:700;font-size:12px;letter-spacing:.08em;margin-bottom:16px">${esc(doc.draftLabel)}</div>`,
    `<table style="border-collapse:collapse;margin-bottom:18px">${doc.meta
      .map(
        (m) =>
          `<tr><td style="padding:2px 16px 2px 0;color:var(--dim);font-size:12.5px;vertical-align:top">${esc(m.label)}</td>` +
          `<td style="padding:2px 0;font-size:12.5px;color:var(--text)">${esc(m.value)}</td></tr>`
      )
      .join("")}</table>`,
  ];
  for (const s of doc.sections) {
    if (s.heading)
      parts.push(
        `<h2 style="font-family:${grotesk};font-size:15px;margin:18px 0 8px;color:var(--text)">${esc(s.heading)}</h2>`
      );
    if (s.kind === "table") {
      parts.push(
        `<table style="border-collapse:collapse;width:100%;font-size:12.5px;margin-bottom:8px">` +
          `<thead><tr>${s.columns
            .map(
              (c) =>
                `<th style="text-align:left;border-bottom:1px solid var(--border);padding:6px 8px;color:var(--dim);font-weight:600">${esc(c)}</th>`
            )
            .join("")}</tr></thead><tbody>${s.rows
            .map(
              (r) =>
                `<tr>${r
                  .map(
                    (cell) =>
                      `<td style="border-bottom:1px solid var(--border);padding:6px 8px;color:var(--text)">${esc(cell)}</td>`
                  )
                  .join("")}</tr>`
            )
            .join("")}</tbody></table>`
      );
    } else {
      parts.push(
        `<p style="font-size:13px;line-height:1.6;color:var(--text);white-space:pre-wrap;margin:0 0 10px">${esc(s.text)}</p>`
      );
    }
  }
  return parts.join("");
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
    const doc = buildDraft(nextType, burn, {});
    paint(doc);
    if (pageRef.current) pageRef.current.contentEditable = "false";

    const proseSection = doc.sections.find((s) => s.kind === "prose");
    if (!aiEnabled || !proseSection) {
      finishEditable();
      return;
    }
    // AI on: stream a phrased version of the single prose section over the top.
    setStatus("streaming");
    let streamed = "";
    try {
      await draftProse({ contractId, docType: nextType }, (chunk) => {
        streamed += chunk;
        proseSection.text = streamed;
        paint(doc);
      });
      if (!streamed.trim()) setAiNote("AI unavailable — using standard wording.");
    } catch {
      proseSection.text = buildDraft(nextType, burn, {}).sections.find((s) => s.kind === "prose").text;
      paint(doc);
      setAiNote("AI unavailable — using standard wording.");
    }
    finishEditable();
  }

  function finishEditable() {
    setStatus("ready");
    if (pageRef.current) pageRef.current.contentEditable = "true";
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

  function onCopy() {
    const text = pageRef.current
      ? pageRef.current.innerText
      : renderDraftText(docRef.current || { title: "", draftLabel: "", meta: [], sections: [] });
    navigator.clipboard?.writeText(text);
  }

  return (
    <div style={{ padding: "24px 26px 60px", maxWidth: 900 }}>
      <div className="no-print" style={{ marginBottom: 18 }}>
        <h2 style={{ margin: 0, fontFamily: grotesk, fontSize: 22, fontWeight: 600, color: "var(--text)" }}>
          Drafts
        </h2>
        <div style={{ fontSize: 13.5, color: "var(--dim)", marginTop: 5 }}>
          Generate GovCon paperwork from live burn data. Numbers come straight from the
          contract; {aiEnabled ? "AI tailors the wording." : "turn on AI for tailored wording."}
        </div>
      </div>

      {/* controls */}
      <div className="no-print" style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginBottom: 16 }}>
        <select
          value={contractId ?? ""}
          onChange={(e) => setActiveId(Number(e.target.value))}
          style={{ ...ghostBtn, cursor: "pointer" }}
        >
          {contracts.map((c) => (
            <option key={c.id} value={c.id}>{c.name || c.piid}</option>
          ))}
        </select>
        <div style={{ display: "flex", gap: 6 }}>
          {DOC_TYPES.map((d) => (
            <button
              key={d.key}
              title={d.blurb}
              onClick={() => setDocType(d.key)}
              style={{
                ...ghostBtn,
                borderColor: docType === d.key ? "var(--accent)" : "var(--border)",
                color: docType === d.key ? "var(--accent)" : "var(--text)",
              }}
            >
              {d.label}
            </button>
          ))}
        </div>
        <button onClick={() => generate()} disabled={contractId == null || status === "streaming"} style={controlBtn}>
          {status === "streaming" ? "✨ tailoring…" : "Generate"}
        </button>
        {docRef.current && status === "ready" && (
          <>
            <button onClick={onCopy} style={ghostBtn}>Copy</button>
            <button onClick={() => window.print()} style={ghostBtn}>Export to PDF</button>
          </>
        )}
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
        <div
          ref={pageRef}
          className="draft-page"
          style={{ ...panelStyle, minHeight: 300, padding: 28, outline: "none" }}
          suppressContentEditableWarning
        />
      )}
    </div>
  );
}
