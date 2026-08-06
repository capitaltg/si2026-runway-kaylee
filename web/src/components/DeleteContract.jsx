import React, { useState } from "react";
import { deleteContract } from "../api.js";

const grotesk = "'Space Grotesk',sans-serif";

// A quiet trash affordance. It sits at the bottom of whatever it's placed in
// (portfolio card, flight deck) and stays low-contrast until hover/focus, so the
// destructive action is reachable without ever competing with the numbers.
// A real <button> — keyboard-reachable, labelled by PIID, never hover-only.
export function TrashButton({ label, onClick, size = 15 }) {
  const [hot, setHot] = useState(false);
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onMouseEnter={() => setHot(true)}
      onMouseLeave={() => setHot(false)}
      onFocus={() => setHot(true)}
      onBlur={() => setHot(false)}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: size + 11,
        height: size + 11,
        padding: 0,
        borderRadius: 8,
        border: "none",
        background: hot ? "var(--badBg)" : "transparent",
        color: hot ? "var(--bad)" : "var(--faint)",
        opacity: hot ? 1 : 0.55,
        cursor: "pointer",
        transition: "opacity .12s, background .12s, color .12s",
      }}
    >
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" />
        <path d="M10 11v5M14 11v5" />
      </svg>
    </button>
  );
}

// Confirmation for a hard delete. Names every contract by PIID so you can't nuke
// the wrong one, and says plainly that it can't be undone. Deletes run one
// request per contract; a partial failure is reported as such rather than
// silently swallowed.
export function DeleteConfirm({ contracts, onCancel, onDone }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const many = contracts.length > 1;

  async function run() {
    setBusy(true);
    setError(null);
    const ok = [];
    const failed = [];
    for (const c of contracts) {
      try {
        await deleteContract(c.id);
        ok.push(c);
      } catch {
        failed.push(c);
      }
    }
    if (failed.length) {
      setBusy(false);
      setError(
        `Deleted ${ok.length} of ${contracts.length}. Couldn't delete ${failed
          .map((c) => c.piid || c.name || c.id)
          .join(", ")}.`
      );
      // The successful ones are gone for good — let the caller refresh around them.
      onDone(ok.map((c) => c.id), { partial: true });
      return;
    }
    onDone(ok.map((c) => c.id), { partial: false });
  }

  return (
    <div
      onClick={onCancel}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(18,24,38,.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 60,
        padding: 20,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(460px,100%)",
          background: "var(--panel)",
          border: "1px solid var(--border)",
          borderRadius: 16,
          padding: "20px 22px",
          boxShadow: "0 24px 60px rgba(18,24,38,.28)",
        }}
      >
        <h3 style={{ margin: 0, fontFamily: grotesk, fontSize: 17, fontWeight: 600, color: "var(--text)" }}>
          Delete {many ? `${contracts.length} contracts` : "this contract"}?
        </h3>
        <div style={{ fontSize: 13, color: "var(--dim)", marginTop: 8, lineHeight: 1.5 }}>
          {many ? "These awards" : "This award"} and all of {many ? "their" : "its"} synced
          hours, logged expenses, saved allocation plans and contract-specific rates will be
          removed. <b style={{ color: "var(--text)" }}>This can't be undone.</b>
        </div>
        <div
          style={{
            marginTop: 14,
            maxHeight: 160,
            overflow: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          {contracts.map((c) => (
            <div
              key={c.id}
              style={{
                display: "flex",
                gap: 10,
                alignItems: "baseline",
                padding: "8px 11px",
                borderRadius: 10,
                background: "var(--panel2)",
              }}
            >
              <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 12, fontWeight: 700, color: "var(--text)" }}>
                {c.piid || `#${c.id}`}
              </span>
              <span style={{ fontSize: 12, color: "var(--dim)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {c.name || ""}
              </span>
            </div>
          ))}
        </div>
        {error && (
          <div style={{ marginTop: 12, fontSize: 12.5, color: "var(--bad)", fontWeight: 600 }}>{error}</div>
        )}
        <div style={{ display: "flex", gap: 9, justifyContent: "flex-end", marginTop: 18 }}>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            style={{
              height: 34,
              padding: "0 14px",
              borderRadius: 9,
              border: "1px solid var(--border)",
              background: "var(--panel2)",
              color: "var(--text)",
              fontSize: 12.5,
              fontWeight: 600,
              cursor: busy ? "default" : "pointer",
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={run}
            disabled={busy}
            style={{
              height: 34,
              padding: "0 16px",
              borderRadius: 9,
              border: "none",
              background: "var(--bad)",
              color: "#fff",
              fontSize: 12.5,
              fontWeight: 600,
              cursor: busy ? "default" : "pointer",
              opacity: busy ? 0.7 : 1,
            }}
          >
            {busy ? "Deleting…" : many ? `Delete ${contracts.length}` : "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}
