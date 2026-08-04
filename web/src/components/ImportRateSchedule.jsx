import React, { useRef, useState } from "react";
import { importRateSchedule } from "../api.js";

const grotesk = "'Space Grotesk',sans-serif";

// The rate-schedule import (#64). `POST /api/contracts/{id}/rates` has existed since
// the burn engine shipped and nothing in `web/src` called it — so a contract ingested
// from an SF-26 face, with its fully-burdened rates on a continuation sheet we never
// read, flagged every charged LCAT as unmatched and the user had no way at all to fix
// it. The endpoint that solves the problem was unreachable from the app.
//
// One shared component because the prompt has to appear wherever the gap is visible:
// the Flight Deck banner, and the allocation matrix's CLIN cards. Reuses the same
// upload path as ingest, so a continuation sheet drops in exactly like an award.
export default function ImportRateSchedule({
  contractId,
  onImported,
  tone = "var(--warn)",
  label = "Import rate schedule",
  compact = false,
}) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);

  async function onPick(e) {
    const file = e.target.files?.[0];
    // Reset the input so picking the same file twice still fires a change event.
    e.target.value = "";
    if (!file || !contractId) return;
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const r = await importRateSchedule(contractId, file);
      // Say what landed, per CLIN. "Imported" alone doesn't tell the user whether
      // the sheet matched the CLINs they were looking at — and a schedule whose
      // rate tables matched *no* CLIN is a real outcome worth naming, not a
      // success message.
      setMsg(
        r.clins_updated
          ? `Rates merged into ${r.clins_updated} CLIN${r.clins_updated === 1 ? "" : "s"}.`
          : `Found ${r.rate_tables_found} rate table${r.rate_tables_found === 1 ? "" : "s"}, but none matched a CLIN on this contract.`
      );
      if (r.piid_mismatch) {
        setMsg(
          (m) => `${m} Note: the schedule states a different contract number.`
        );
      }
      if (r.clins_updated) await onImported?.();
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }

  const btn = {
    height: compact ? 28 : 32,
    padding: compact ? "0 10px" : "0 14px",
    borderRadius: 8,
    border: `1px solid ${tone}`,
    background: "transparent",
    color: tone,
    fontFamily: grotesk,
    fontSize: compact ? 11.5 : 12.5,
    fontWeight: 600,
    cursor: busy ? "default" : "pointer",
    opacity: busy ? 0.6 : 1,
    whiteSpace: "nowrap",
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      <button type="button" style={btn} disabled={busy} onClick={() => inputRef.current?.click()}>
        {busy ? "Reading schedule…" : label}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.txt,.md,.csv"
        onChange={onPick}
        style={{ display: "none" }}
      />
      {msg && <span style={{ fontSize: 11.5, color: "var(--dim)" }}>{msg}</span>}
      {err && <span style={{ fontSize: 11.5, color: "var(--bad)" }}>{err}</span>}
    </div>
  );
}
