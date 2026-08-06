import React, { useEffect, useState } from "react";
import { getPortfolio, getAllocationConflicts } from "../api.js";
import { money, moneyM, pct, pill, hueFor, statusColor, panelStyle } from "../format.js";
import { TrashButton, DeleteConfirm } from "../components/DeleteContract.jsx";

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

export default function Portfolio({ onOpen, onDeleted }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [conflicts, setConflicts] = useState(null);
  // Contract ids ticked for bulk delete. Empty === no selection chrome on screen.
  const [picked, setPicked] = useState([]);
  // Contracts staged in the confirm dialog (one row's trash, or the whole selection).
  const [pending, setPending] = useState(null);

  function load() {
    getPortfolio()
      .then(setData)
      .catch((e) => setError(e.message));
    getAllocationConflicts()
      .then(setConflicts)
      .catch(() => setConflicts(null));
  }

  useEffect(load, []);

  const toggle = (id) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  // Deleted contracts are gone from the server; drop them from the selection,
  // refresh, and let App clear the active contract if it was one of them.
  function afterDelete(ids) {
    setPending(null);
    setPicked((p) => p.filter((id) => !ids.includes(id)));
    load();
    if (onDeleted) onDeleted(ids);
  }

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
          // Styling from the status, wording from the server: it rolls the card's
          // label up from the CLINs behind it, so a contract whose only red line is
          // a funding problem stops claiming the ceiling was breached.
          const p = pill(c.status);
          const pillLabel = c.status_label || p.label;
          const barColor = c.pct > 0.85 ? "var(--bad)" : c.pct > 0.7 ? "var(--warn)" : hue;
          const isPicked = picked.includes(c.id);
          return (
            <div
              key={c.id}
              onClick={() => onOpen(c.id)}
              style={{
                border: `1px solid ${
                  isPicked
                    ? "var(--accent)"
                    : c.status === "over" || c.status === "unpriced"
                      ? "var(--bad)"
                      : "var(--border)"
                }`,
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
                <span style={p.style}>{pillLabel}</span>
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

              {/* Card footer — the only place delete lives. A small tick box for
                  bulk selection and a low-contrast trash, both kept below the
                  numbers so the destructive action never reads as an action you
                  were meant to take. */}
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
                <input
                  type="checkbox"
                  checked={isPicked}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => toggle(c.id)}
                  aria-label={`Select ${c.piid || c.name} for deletion`}
                  style={{ width: 14, height: 14, accentColor: "var(--accent)", cursor: "pointer" }}
                />
                <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 600, color: "var(--accent)" }}>
                  Open flight deck →
                </span>
                <TrashButton
                  label={`Delete ${c.piid || c.name}`}
                  onClick={() => setPending([c])}
                />
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
                {/* The percentage is against this person's expected week (#84), not
                    against 40 — so a part-time person booked to a full week reads well
                    over 100%, which is the honest reading. The row is here because the
                    hours exceed a physical week; that check is deliberately separate. */}
                <span
                  title={
                    p.expected
                      ? `${p.total_hours} hrs/wk against ${p.expected.hours} expected — ${p.expected.label}.`
                      : undefined
                  }
                  style={{
                    fontFamily: "'IBM Plex Mono',monospace",
                    fontWeight: 700,
                    fontSize: 13,
                    color: p.utilization > 1.5 ? "var(--bad)" : "var(--warn)",
                  }}
                >
                  {p.utilization != null ? `${Math.round(p.utilization * 100)}% · ` : ""}
                  {p.total_hours} hrs/wk
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

      {/* Bulk action bar. Appears only once something is ticked, so the portfolio
          carries no delete chrome at rest. */}
      {picked.length > 0 && (
        <div
          style={{
            position: "fixed",
            bottom: 22,
            left: "50%",
            transform: "translateX(-50%)",
            display: "flex",
            alignItems: "center",
            gap: 14,
            padding: "11px 16px",
            borderRadius: 13,
            background: "var(--panel)",
            border: "1px solid var(--border)",
            boxShadow: "0 14px 40px rgba(18,24,38,.22)",
            zIndex: 40,
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
            {picked.length} selected
          </span>
          <button
            type="button"
            onClick={() => setPicked([])}
            style={{
              height: 30,
              padding: "0 12px",
              borderRadius: 8,
              border: "1px solid var(--border)",
              background: "var(--panel2)",
              color: "var(--dim)",
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Clear selection
          </button>
          <button
            type="button"
            onClick={() => setPending(data.contracts.filter((c) => picked.includes(c.id)))}
            style={{
              height: 30,
              padding: "0 13px",
              borderRadius: 8,
              border: "none",
              background: "var(--bad)",
              color: "#fff",
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Delete {picked.length} contract{picked.length === 1 ? "" : "s"}
          </button>
        </div>
      )}

      {pending && (
        <DeleteConfirm
          contracts={pending}
          onCancel={() => setPending(null)}
          onDone={afterDelete}
        />
      )}
    </div>
  );
}
