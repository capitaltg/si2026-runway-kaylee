import React, { useEffect, useState } from "react";
import { getPortfolio, getAllocationConflicts } from "../api.js";
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
  const [conflicts, setConflicts] = useState(null);

  useEffect(() => {
    getPortfolio()
      .then(setData)
      .catch((e) => setError(e.message));
    getAllocationConflicts()
      .then(setConflicts)
      .catch(() => setConflicts(null));
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
                border: `1px solid ${c.status === "over" || c.status === "unpriced" ? "var(--bad)" : "var(--border)"}`,
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
                <span style={c.data_quality ? { color: "var(--bad)", fontWeight: 700 } : undefined}>
                  {c.data_quality
                    ? `${c.data_quality} CLIN${c.data_quality === 1 ? "" : "s"} can't be priced`
                    : `${c.on_pace} of ${c.lines} lines on pace`}
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

      {/* resource conflicts — people booked >100% across contracts */}
      {conflicts && conflicts.count > 0 && (
        <div style={{ ...panelStyle, marginTop: 22, borderColor: "var(--warn)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 4 }}>
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="var(--warn)" strokeWidth="2">
              <path d="M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" strokeLinejoin="round" />
              <path d="M12 9v4M12 17h.01" strokeLinecap="round" />
            </svg>
            <h3 style={{ margin: 0, fontFamily: grotesk, fontSize: 16, fontWeight: 600, color: "var(--text)" }}>
              Resource conflicts
            </h3>
            <span style={{ fontSize: 12, color: "var(--dim)" }}>
              {conflicts.count} {conflicts.count === 1 ? "person" : "people"} booked over a full week across contracts
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 9, marginTop: 12 }}>
            {conflicts.conflicts.map((p) => (
              <div
                key={p.employee_id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "9px 12px",
                  borderRadius: 11,
                  background: "var(--panel2)",
                  flexWrap: "wrap",
                }}
              >
                <span style={{ fontWeight: 600, color: "var(--text)", minWidth: 150 }}>{p.name}</span>
                <span
                  style={{
                    fontFamily: "'IBM Plex Mono',monospace",
                    fontWeight: 700,
                    fontSize: 13,
                    color: p.utilization > 1.5 ? "var(--bad)" : "var(--warn)",
                  }}
                >
                  {Math.round(p.utilization * 100)}% · {p.total_hours} hrs/wk
                </span>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {p.assignments.map((a, i) => (
                    <span
                      key={i}
                      onClick={() => onOpen(a.contract_id)}
                      title="Open contract"
                      style={{
                        fontSize: 11,
                        color: "var(--dim)",
                        border: "1px solid var(--border)",
                        borderRadius: 7,
                        padding: "2px 8px",
                        cursor: "pointer",
                      }}
                    >
                      {a.contract} · {a.hours}h
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
