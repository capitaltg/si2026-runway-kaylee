import React, { useEffect, useRef } from "react";

const grotesk = "'Space Grotesk',sans-serif";

// The app's own confirmation dialog, for destructive actions that need one.
//
// Extracted from the contract-delete confirm (#29) so the second destructive action
// in the app doesn't have to fall back to `window.confirm`. A native confirm names
// nothing you can style, can't show which thing you're about to lose, and reads as
// the browser interrupting rather than the app asking — on a delete sitting 26px
// from the button that *loads* the same row, "which one is this?" is the whole
// question the dialog exists to answer.
//
// Presentational only: the caller owns the request, its busy state and its error, so
// this can front any delete without knowing what a plan or a contract is.
//
// Escape cancels and focus lands on Cancel rather than Delete — the safe option is
// the one that should be one keystroke away.
export function ConfirmDialog({
  title,
  children,
  confirmLabel = "Delete",
  busyLabel = "Deleting…",
  busy = false,
  error = null,
  onCancel,
  onConfirm,
}) {
  const cancelRef = useRef(null);

  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  return (
    <div
      onClick={() => !busy && onCancel()}
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
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(440px,100%)",
          background: "var(--panel)",
          border: "1px solid var(--border)",
          borderRadius: 16,
          padding: "20px 22px",
          boxShadow: "0 24px 60px rgba(18,24,38,.28)",
        }}
      >
        <h3 style={{ margin: 0, fontFamily: grotesk, fontSize: 17, fontWeight: 600, color: "var(--text)" }}>
          {title}
        </h3>
        <div style={{ fontSize: 13, color: "var(--dim)", marginTop: 8, lineHeight: 1.5 }}>
          {children}
        </div>
        {error && (
          <div style={{ marginTop: 12, fontSize: 12.5, color: "var(--bad)", fontWeight: 600 }}>
            {error}
          </div>
        )}
        <div style={{ display: "flex", gap: 9, justifyContent: "flex-end", marginTop: 18 }}>
          <button
            ref={cancelRef}
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
            onClick={onConfirm}
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
            {busy ? busyLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
