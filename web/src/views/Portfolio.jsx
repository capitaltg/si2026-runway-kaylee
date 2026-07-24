import React, { useEffect, useState } from "react";
import { getPortfolio } from "../api.js";
import { money, moneyM, pct, pill, hueFor, statusColor, panelStyle } from "../format.js";

const grotesk = "'Space Grotesk',sans-serif";
const tileLabel = {
  fontSize: 11,
  letterSpacing: ".1em",
  textTransform: "uppercase",
  fontWeight: 700,
  color: "var(--faint)",
};
const tileNum = { fontFamily: grotesk, fontWeight: 700, fontSize: 30, color: "var(--text)", marginTop: 6 };

const initials = (name) =>
  (name || "")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0] || "")
    .join("")
    .toUpperCase();

export default function Portfolio({ onOpen }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getPortfolio()
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return <div style={{ padding: 40, color: "var(--bad)" }}>Couldn't load portfolio: {error}</div>;
  }
  if (!data) {
    return <div style={{ padding: 40, color: "var(--dim)" }}>Loading portfolio…</div>;
  }

  return (
    <div style={{ padding: "26px 26px 60px", maxWidth: 1280 }}>
      <div style={{ marginBottom: 18 }}>
        <h2 style={{ margin: 0, fontFamily: grotesk, fontSize: 22, fontWeight: 600, color: "var(--text)" }}>
          Portfolio
        </h2>
        <div style={{ fontSize: 13.5, color: "var(--dim)", marginTop: 5 }}>
          How your {data.count} active {data.count === 1 ? "contract is" : "contracts are"} pacing today.
        </div>
      </div>

      {/* KPI tiles */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14, marginBottom: 20 }}>
        <div style={panelStyle}>
          <div style={tileLabel}>Active contracts</div>
          <div style={tileNum}>{data.count}</div>
        </div>
        <div style={panelStyle}>
          <div style={tileLabel}>Portfolio value</div>
          <div style={tileNum}>{moneyM(data.value)}</div>
        </div>
        <div style={panelStyle}>
          <div style={tileLabel}>Weekly burn</div>
          <div style={tileNum}>{money(data.weekly)}</div>
        </div>
        <div
          style={{
            borderRadius: 16,
            padding: "16px 18px",
            background: data.at_risk ? "var(--warnBg)" : "var(--goodBg)",
            border: `1px solid ${data.at_risk ? "var(--warn)" : "var(--good)"}`,
          }}
        >
          <div style={{ ...tileLabel, color: data.at_risk ? "var(--warn)" : "var(--good)" }}>Need attention</div>
          <div style={{ ...tileNum, color: data.at_risk ? "var(--warn)" : "var(--good)" }}>{data.at_risk}</div>
        </div>
      </div>

      {/* contract cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(370px,1fr))", gap: 16 }}>
        {data.contracts.map((c, i) => {
          const hue = hueFor(i);
          const p = pill(c.status);
          const barColor = c.pct > 0.85 ? "var(--bad)" : c.pct > 0.7 ? "var(--warn)" : hue;
          return (
            <div
              key={c.id}
              onClick={() => onOpen(c.id)}
              style={{
                border: `1px solid ${c.status === "over" ? "var(--bad)" : "var(--border)"}`,
                borderRadius: 16,
                padding: "16px 17px",
                background: "var(--panel)",
                cursor: "pointer",
                boxShadow: "0 8px 22px rgba(26,34,51,.05)",
              }}
            >
              <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
                <div
                  style={{
                    width: 42,
                    height: 42,
                    flex: "0 0 42px",
                    borderRadius: 12,
                    background: hue,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "#fff",
                    fontFamily: grotesk,
                    fontWeight: 700,
                    fontSize: 15,
                  }}
                >
                  {initials(c.name || c.piid)}
                </div>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontFamily: grotesk, fontWeight: 600, fontSize: 15, color: "var(--text)", lineHeight: 1.25 }}>
                    {c.name || c.piid}
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--dim)", marginTop: 2 }}>{c.agency || c.piid}</div>
                </div>
                <span style={p.style}>{p.label}</span>
              </div>

              <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginTop: 16 }}>
                <div>
                  <div style={{ fontSize: 10.5, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--faint)", fontWeight: 700 }}>
                    Runway
                  </div>
                  <div style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 26, color: statusColor(c.status), lineHeight: 1.1 }}>
                    {c.runway_days ?? "—"}
                    <span style={{ fontSize: 13, fontWeight: 600, color: "var(--dim)" }}> days</span>
                  </div>
                </div>
                <div style={{ marginLeft: "auto", textAlign: "right" }}>
                  <div style={{ fontSize: 10.5, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--faint)", fontWeight: 700 }}>
                    Burned
                  </div>
                  <div style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 26, color: "var(--text)", lineHeight: 1.1 }}>
                    {pct(c.pct)}
                  </div>
                </div>
              </div>

              <div style={{ height: 8, borderRadius: 5, background: "var(--border)", marginTop: 12, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${Math.min(100, Math.round(c.pct * 100))}%`, background: barColor, borderRadius: 5 }} />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 11.5, color: "var(--dim)" }}>
                <span>
                  {c.on_pace} of {c.lines} lines on pace
                </span>
                <span>{money(c.weekly)}/wk</span>
              </div>

              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 7,
                  marginTop: 14,
                  paddingTop: 12,
                  borderTop: "1px solid var(--border)",
                }}
              >
                <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 600, color: "var(--accent)" }}>
                  Open flight deck →
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
