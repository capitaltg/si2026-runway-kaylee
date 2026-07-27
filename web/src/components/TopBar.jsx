import React from "react";

// Global top bar, built to match docs/design/Runway.dc.html (issue #3):
// view title/subtitle, active-contract meta (vehicle + period-of-performance
// progress), and the global actions — Export to Excel, Ask Runway, theme.

// Per-view header copy. `meta: true` views also show the contract vehicle badge
// and period-of-performance bar (only ever when a contract is actually loaded).
const HEADERS = {
  portfolio: { main: "Portfolio", sub: "All active contracts", meta: false },
  flightdeck: { main: null, sub: "Flight Deck · Live burn & runway", meta: true },
  allocate: { main: "Allocation Matrix", sub: "Staff → CLINs", meta: true },
  expenses: { main: "Expenses", sub: "Non-labor CLINs", meta: true },
  ingest: { main: "Contract Ingest", sub: "PDF → data", meta: false },
  chat: { main: "Ask Runway", sub: "Live answers on your burn", meta: true },
};

// "2025-12-01" → "01 Dec 25"; falls back to the raw string if it won't parse.
function fmtDate(s) {
  if (!s) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if (!m) return s;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${m[3]} ${months[+m[2] - 1] || m[2]} ${m[1].slice(2)}`;
}

const iconBtn = {
  display: "flex",
  alignItems: "center",
  gap: 7,
  height: 36,
  padding: "0 13px",
  borderRadius: 10,
  fontSize: 12.5,
  fontWeight: 600,
  cursor: "pointer",
};

export default function TopBar({ view, contract, theme, toggleTheme, onExport, onAskRunway }) {
  const h = HEADERS[view] || { main: view, sub: "", meta: false };
  const showMeta = h.meta && !!contract;
  const main = h.main || contract?.name || "Runway";

  const tw = contract?.total_weeks || 0;
  const cw = contract?.current_week || 0;
  const popPct = tw ? `${Math.min(100, Math.round((cw / tw) * 100))}%` : "0%";

  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: 20,
        padding: "16px 26px",
        borderBottom: "1px solid var(--border)",
        background: "var(--panel)",
        position: "sticky",
        top: 0,
        zIndex: 20,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h1
            style={{
              margin: 0,
              fontFamily: "'Space Grotesk',sans-serif",
              fontSize: 17,
              fontWeight: 600,
              letterSpacing: "-.01em",
              color: "var(--text)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {main}
          </h1>
          {showMeta && contract.vehicle && (
            <span
              style={{
                fontSize: 10.5,
                fontFamily: "'IBM Plex Mono',monospace",
                color: "var(--dim)",
                border: "1px solid var(--border)",
                padding: "2px 7px",
                borderRadius: 6,
                whiteSpace: "nowrap",
              }}
            >
              {contract.vehicle}
            </span>
          )}
        </div>
        <div style={{ fontSize: 12, color: "var(--dim)", marginTop: 3 }}>{h.sub}</div>
      </div>

      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
        {showMeta && (
          <div style={{ textAlign: "right", marginRight: 4 }}>
            <div
              style={{
                fontSize: 10.5,
                color: "var(--faint)",
                textTransform: "uppercase",
                letterSpacing: ".1em",
                fontWeight: 600,
              }}
            >
              Period of performance
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
              <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 11.5, color: "var(--dim)" }}>
                {fmtDate(contract.pop_start)}
              </span>
              <div
                style={{
                  width: 118,
                  height: 6,
                  borderRadius: 4,
                  background: "var(--panel2)",
                  border: "1px solid var(--border)",
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    height: "100%",
                    width: popPct,
                    background: "linear-gradient(90deg,var(--accent),var(--accent2))",
                  }}
                />
              </div>
              <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 11.5, color: "var(--dim)" }}>
                {fmtDate(contract.pop_end)}
              </span>
            </div>
          </div>
        )}

        <button
          onClick={onExport}
          disabled={!contract}
          title={contract ? "Download the loaded burn data as a spreadsheet (CSV)" : "Open a contract first"}
          style={{
            ...iconBtn,
            border: "1px solid var(--good)",
            background: "var(--goodBg)",
            color: "var(--good)",
            opacity: contract ? 1 : 0.45,
            cursor: contract ? "pointer" : "not-allowed",
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z" />
            <path d="M14 3v5h5" strokeLinejoin="round" />
            <path d="M9 13l2 2M11 15l-2 2M15 13l-2 2M13 15l2 2" strokeLinecap="round" />
          </svg>
          Export to Excel
        </button>

        <button
          onClick={onAskRunway}
          style={{
            ...iconBtn,
            border: "1px solid var(--border)",
            background: "var(--panel2)",
            color: "var(--text)",
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M21 11.5a8.4 8.4 0 01-11.9 7.6L3 21l1.9-6A8.4 8.4 0 1121 11.5z" strokeLinejoin="round" />
          </svg>
          Ask Runway
        </button>

        <button
          onClick={toggleTheme}
          title="Toggle theme"
          style={{
            width: 36,
            height: 36,
            borderRadius: 10,
            border: "1px solid var(--border)",
            background: "var(--panel2)",
            color: "var(--text)",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <span style={{ fontSize: 16 }}>{theme === "dark" ? "☀️" : "🌙"}</span>
        </button>
      </div>
    </header>
  );
}
