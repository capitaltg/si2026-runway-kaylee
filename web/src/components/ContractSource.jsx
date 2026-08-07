import React, { useEffect, useState } from "react";
import { contractDocumentUrl, listContractDocuments } from "../api.js";
import { shortDate } from "../format.js";
import {
  NO_SOURCE_NOTE,
  fileSize,
  kindLabel,
  shortHash,
  sourceDocuments,
} from "../contract-source.js";

// "Contract source" (#30): the paperwork this dashboard's numbers were extracted
// from, one line per document, linked.
//
// Compact on purpose. The Flight Deck already prints every extracted value — PIID,
// agency, PoP, funded vs. ceiling, the CLIN table — and reproducing them here would
// be a second copy to keep in sync. The gap this closes is narrower than that: from
// any of those numbers there was no way back to the page it came from.
export default function ContractSource({ contractId, refreshKey = 0 }) {
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!contractId) return;
    let live = true;
    listContractDocuments(contractId)
      .then((r) => live && setState(sourceDocuments(r.documents)))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [contractId, refreshKey]);

  // Nothing at all until the fetch lands: a panel that flashes "no source document
  // stored" and then contradicts itself is worse than one that arrives a beat late,
  // because the empty state is the one an auditor would act on.
  if (error || !state) return null;

  return (
    <div
      style={{
        marginTop: 12,
        display: "flex",
        flexWrap: "wrap",
        alignItems: "baseline",
        gap: "4px 10px",
        fontSize: 12.5,
        color: "var(--dim)",
      }}
    >
      <span style={{ textTransform: "uppercase", letterSpacing: ".06em", fontSize: 11 }}>
        Contract source
      </span>
      {state.empty ? (
        <span>{NO_SOURCE_NOTE}</span>
      ) : (
        state.items.map((doc, i) => (
          <span key={doc.id} style={{ display: "inline-flex", gap: 6, alignItems: "baseline" }}>
            {i > 0 && <span aria-hidden="true">·</span>}
            <a
              href={contractDocumentUrl(contractId, doc.id)}
              target="_blank"
              rel="noreferrer"
              title={`${kindLabel(doc.kind)} · SHA-256 ${shortHash(doc.sha256)}…`}
              style={{ color: "var(--accent)", textDecoration: "none", fontWeight: 500 }}
            >
              {doc.filename}
            </a>
            <span>
              {kindLabel(doc.kind).toLowerCase()}
              {doc.created_at ? `, added ${shortDate(doc.created_at)}` : ""}
              {fileSize(doc.size_bytes) ? ` · ${fileSize(doc.size_bytes)}` : ""}
            </span>
          </span>
        ))
      )}
    </div>
  );
}
