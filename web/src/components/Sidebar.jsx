import React from "react";

const NAV = [
  { key: "portfolio", label: "Portfolio", icon: "🗂️" },
  { key: "flightdeck", label: "Flight Deck", icon: "📊" },
  { key: "allocate", label: "Allocate", icon: "👥" },
  { key: "expenses", label: "Expenses", icon: "🧾" },
  { key: "ingest", label: "Ingest Contract", icon: "📁" },
];

export default function Sidebar({ view, setView, theme, toggleTheme }) {
  return (
    <aside
      style={{
        width: 242,
        flex: "0 0 242px",
        background: "var(--panel)",
        borderRight: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        padding: "18px 0",
      }}
    >
      <div style={{ padding: "0 20px 18px", display: "flex", alignItems: "center", gap: 10 }}>
        <div
          style={{
            width: 30, height: 30, borderRadius: 9,
            background: "linear-gradient(135deg,var(--accent),var(--accent2))",
          }}
        />
        <span
          style={{
            fontFamily: "'Space Grotesk',sans-serif",
            fontWeight: 700, fontSize: 19, color: "var(--text)",
          }}
        >
          Runway
        </span>
      </div>

      <nav style={{ display: "flex", flexDirection: "column", gap: 2, padding: "0 12px" }}>
        {NAV.map((n) => {
          const active = view === n.key;
          return (
            <div
              key={n.key}
              onClick={() => setView(n.key)}
              style={{
                display: "flex", alignItems: "center", gap: 11,
                padding: "10px 12px", borderRadius: 11, cursor: "pointer",
                fontSize: 13.5, fontWeight: 600,
                color: active ? "#fff" : "var(--dim)",
                background: active ? "var(--accent)" : "transparent",
              }}
            >
              <span style={{ fontSize: 16 }}>{n.icon}</span>
              {n.label}
            </div>
          );
        })}
      </nav>

      <button
        onClick={toggleTheme}
        style={{
          margin: "auto 20px 4px", height: 36, borderRadius: 10,
          border: "1px solid var(--border)", background: "var(--panel2)",
          color: "var(--text)", cursor: "pointer", fontSize: 12.5, fontWeight: 600,
        }}
      >
        {theme === "dark" ? "☀️ Light" : "🌙 Dark"} mode
      </button>
    </aside>
  );
}
