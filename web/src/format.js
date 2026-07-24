// Formatting + status→token helpers shared by the burn dashboard views.
// Ported from the Runway design system (docs/design/Runway.dc.html): money/
// moneyM number formats, the status pill palette, and the per-CLIN hue set.
// Colors are emitted as CSS var references so they stay theme-reactive.

export const money = (n) => "$" + Math.round(n || 0).toLocaleString("en-US");
export const moneyM = (n) => "$" + ((n || 0) / 1e6).toFixed(2) + "M";
export const pct = (frac) => Math.round((frac || 0) * 100) + "%";

// Per-CLIN accent hues, assigned by index (design's avPal).
const HUES = ["#4361ee", "#06b6d4", "#7c5cff", "#ef8f2a", "#10b981", "#f05252"];
export const hueFor = (i) => HUES[i % HUES.length];

// status → { label, color var, background var }. Mirrors design's pill().
const PILL = {
  over: { label: "Over ceiling", color: "--bad", bg: "--badBg" },
  watch: { label: "Watch", color: "--warn", bg: "--warnBg" },
  ok: { label: "On pace", color: "--good", bg: "--goodBg" },
  paused: { label: "Paused", color: "--faint", bg: "--panel2" },
  tracked: { label: "Tracked", color: "--dim", bg: "--panel2" },
};

export function pill(status) {
  const p = PILL[status] || { label: "—", color: "--dim", bg: "--panel2" };
  return {
    label: p.label,
    color: `var(${p.color})`,
    style: {
      fontSize: 10.5,
      fontWeight: 700,
      padding: "2px 9px",
      borderRadius: 20,
      color: `var(${p.color})`,
      background: `var(${p.bg})`,
      marginLeft: "auto",
      whiteSpace: "nowrap",
    },
  };
}

// status → the accent color a runway/exhaustion figure should take.
export const statusColor = (status) =>
  status === "over" ? "var(--bad)" : status === "watch" ? "var(--warn)" : "var(--good)";

export const panelStyle = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 16,
  padding: 18,
  boxShadow: "0 1px 2px rgba(26,34,51,.04),0 10px 26px rgba(26,34,51,.05)",
};
