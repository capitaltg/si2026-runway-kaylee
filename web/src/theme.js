// Palette ported verbatim from the Runway design system (Runway.dc.html).
export const themes = {
  light: {
    bg: "#eef1f8", panel: "#ffffff", panel2: "#f3f5fb", border: "#e6e9f2",
    text: "#1a2233", dim: "#5f6b80", faint: "#9aa6bd",
    accent: "#4361ee", accent2: "#06b6d4",
    good: "#10b981", goodBg: "#e6f7f1", warn: "#ef8f2a", warnBg: "#fdf1e2",
    bad: "#f05252", badBg: "#fdecec", inputBg: "#ffffff",
  },
  dark: {
    bg: "#0b1120", panel: "#141d31", panel2: "#1c2840", border: "#2b3b59",
    text: "#eaf0fb", dim: "#a3b1c9", faint: "#6d7d97",
    accent: "#6d8bff", accent2: "#34d3e0",
    good: "#34d399", goodBg: "#0f2a20", warn: "#fbbf24", warnBg: "#2c2110",
    bad: "#fb7185", badBg: "#2c1518", inputBg: "#0d1626",
  },
};

export function applyTheme(name) {
  const t = themes[name] || themes.light;
  const root = document.documentElement;
  Object.entries(t).forEach(([k, v]) => root.style.setProperty(`--${k}`, v));
  root.style.setProperty("color-scheme", name);
}
