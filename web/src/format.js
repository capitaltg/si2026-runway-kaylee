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
  // Label picked per-call by pill()'s ceilingBreached — see there.
  over: { label: "Over ceiling", color: "--bad", bg: "--badBg" },
  // The funded slice runs short but the ceiling holds and funding is either
  // keeping pace or has a mod outstanding (burn.py's #22 downgrade). Routine
  // incremental funding, so it's amber with the rest of the funding states — the
  // backend has emitted this since #22 but PILL had no entry, so it rendered "—".
  funding: { label: "Funding due", color: "--warn", bg: "--warnBg" },
  watch: { label: "Watch", color: "--warn", bg: "--warnBg" },
  ok: { label: "On pace", color: "--good", bg: "--goodBg" },
  under: { label: "Under pace", color: "--warn", bg: "--warnBg" },
  paused: { label: "Paused", color: "--faint", bg: "--panel2" },
  // Charged rows the engine couldn't price (#40) — a data gap, flagged red because
  // it masks the burn rather than reporting one.
  unpriced: { label: "Unpriced", color: "--bad", bg: "--badBg" },
  tracked: { label: "Tracked", color: "--dim", bg: "--panel2" },
};

// `ceilingBreached` names the limit a red `over` is about, matching burn.py's
// _pill: projected spend blowing the real ceiling is a ceiling problem, while the
// ceiling holding means the funded slice ran short with funding lagging behind.
// `fundsExceeded` outranks both — those are forecasts, this one already happened,
// so it takes the past tense.
// Defaults to the ceiling wording, which is always right for a CLIN that isn't
// incrementally funded (its budget *is* the ceiling) and is what callers with no
// funded-slice notion — expenses — should keep saying.
export function pill(status, ceilingBreached = true, fundsExceeded = false) {
  const p = PILL[status] || { label: "—", color: "--dim", bg: "--panel2" };
  const overLabel = fundsExceeded
    ? "Funds exceeded"
    : ceilingBreached
      ? p.label
      : "Funds short";
  return {
    label: status === "over" ? overLabel : p.label,
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
  status === "over" || status === "unpriced"
    ? "var(--bad)"
    : status === "watch" || status === "under" || status === "funding"
      ? "var(--warn)"
      : "var(--good)";

export const panelStyle = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 16,
  padding: 18,
  boxShadow: "0 1px 2px rgba(26,34,51,.04),0 10px 26px rgba(26,34,51,.05)",
};
