import React from "react";

// Nav is split into two scopes so it's obvious what each view operates on:
// Portfolio spans every contract; everything under "Current contract" is scoped
// to the one contract in focus. (Full visual treatment per the design — boxed
// contract card, top bar — is issue #3; this is just the grouping.)
const PORTFOLIO = { key: "portfolio", label: "Portfolio", icon: "🗂️" };
const CONTRACT_NAV = [
  { key: "flightdeck", label: "Flight Deck", icon: "📊" },
  { key: "allocate", label: "Allocate", icon: "👥" },
  { key: "expenses", label: "Expenses", icon: "🧾" },
  { key: "ingest", label: "Ingest Contract", icon: "📁" },
];

const sectionLabel = {
  padding: "0 14px",
  fontSize: 10.5,
  letterSpacing: ".13em",
  textTransform: "uppercase",
  color: "var(--faint)",
  fontWeight: 700,
  margin: "0 0 7px",
};

export default function Sidebar({ view, setView, theme, toggleTheme }) {
  const NavItem = (n) => {
    const active = view === n.key;
    return (
      <div
        key={n.key}
        onClick={() => setView(n.key)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 11,
          padding: "10px 12px",
          borderRadius: 11,
          cursor: "pointer",
          fontSize: 13.5,
          fontWeight: 600,
          color: active ? "#fff" : "var(--dim)",
          background: active ? "var(--accent)" : "transparent",
        }}
      >
        <span style={{ fontSize: 16 }}>{n.icon}</span>
        {n.label}
      </div>
    );
  };

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

      <nav style={{ display: "flex", flexDirection: "column", padding: "0 12px" }}>
        {/* Spans all contracts */}
        <div style={sectionLabel}>All contracts</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {NavItem(PORTFOLIO)}
        </div>

        <div style={{ margin: "16px 4px 14px", height: 1, background: "var(--border)" }} />

        {/* Scoped to the one contract in focus */}
        <div style={sectionLabel}>Current contract</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {CONTRACT_NAV.map(NavItem)}
        </div>
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
