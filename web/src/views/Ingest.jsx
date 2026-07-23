import React, { useEffect, useRef, useState } from "react";
import { ingest, confirm, getSources } from "../api.js";

const money = (v) =>
  v == null ? "—" : "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });

// Confidence badge — thresholds/colors ported from the design's conf() helper
// (Runway.dc.html): >=95% good, >=88% accent, else warn. Theme-aware via CSS vars.
function confStyle(pct) {
  const color = pct >= 95 ? "var(--good)" : pct >= 88 ? "var(--accent)" : "var(--warn)";
  const bg =
    pct >= 95
      ? "var(--goodBg)"
      : pct >= 88
        ? "color-mix(in srgb, var(--accent) 14%, transparent)"
        : "var(--warnBg)";
  return {
    fontSize: 11, fontWeight: 700, fontFamily: "'IBM Plex Mono',monospace",
    padding: "2px 8px", borderRadius: 6, color, background: bg, whiteSpace: "nowrap",
  };
}

function ConfBadge({ v }) {
  if (v == null) return <span style={{ color: "var(--faint)", fontSize: 12 }}>—</span>;
  const pct = Math.round(v * 100);
  return <span style={confStyle(pct)}>{pct}%</span>;
}

const panelStyle = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 16,
  padding: 18,
};
const label = {
  fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase",
  color: "var(--faint)", fontWeight: 700, marginBottom: 12,
};

function StepHeader({ n, text, color = "var(--accent)", right }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 12 }}>
      <span
        style={{
          width: 22, height: 22, borderRadius: "50%", background: color,
          color: "#fff", display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 12, fontWeight: 700,
        }}
      >
        {n}
      </span>
      <span style={{ fontFamily: "'Space Grotesk',sans-serif", fontWeight: 600, fontSize: 15 }}>
        {text}
      </span>
      {right}
    </div>
  );
}

// Source pill styling — ported from the design's badgeMap (Runway.dc.html).
// live/synced -> green, syncing/offline -> amber, disconnected -> faint/muted.
const SOURCE_BADGE = {
  live: ["Live", "var(--good)", "var(--goodBg)"],
  synced: ["Synced", "var(--good)", "var(--goodBg)"],
  syncing: ["Syncing", "var(--warn)", "var(--warnBg)"],
  offline: ["Offline", "var(--warn)", "var(--warnBg)"],
  disconnected: ["Not connected", "var(--faint)", "color-mix(in srgb, var(--faint) 12%, transparent)"],
};

function SourceBox({ s }) {
  const [txt, color, bg] = SOURCE_BADGE[s.status] || SOURCE_BADGE.disconnected;
  return (
    <div
      style={{
        display: "flex", alignItems: "center", gap: 9, padding: "9px 11px",
        border: "1px solid var(--border)", borderRadius: 11, background: "var(--panel)",
      }}
    >
      <div
        style={{
          width: 30, height: 30, borderRadius: 8, background: s.hue, color: "#fff",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontFamily: "'IBM Plex Mono',monospace", fontWeight: 600, fontSize: 11,
          flexShrink: 0,
        }}
      >
        {s.code}
      </div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          style={{
            fontSize: 12.5, fontWeight: 600, whiteSpace: "nowrap",
            overflow: "hidden", textOverflow: "ellipsis",
          }}
        >
          {s.name}
        </div>
        <div style={{ fontSize: 10.5, color: "var(--dim)" }}>{s.kind}</div>
      </div>
      <span
        style={{
          fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 20,
          color, background: bg, whiteSpace: "nowrap",
        }}
      >
        {txt}
      </span>
    </div>
  );
}

// Fallback if the Runway backend can't be reached at all — still shows the
// design's six boxes, all "Not connected", rather than an empty panel.
const FALLBACK_SOURCES = {
  connected: 0,
  sources: [
    { code: "FX", name: "Fixtura", kind: "Timesheets · offline", hue: "#4361ee", status: "offline" },
    { code: "DK", name: "Deltek Costpoint", kind: "Billing · LCAT rates", hue: "#4b2e83", status: "disconnected" },
    { code: "UN", name: "Unanet", kind: "Timesheets · hours", hue: "#0a66c2", status: "disconnected" },
    { code: "QB", name: "QuickBooks Time", kind: "Timesheets", hue: "#2ca01c", status: "disconnected" },
    { code: "AD", name: "ADP", kind: "Payroll · roster", hue: "#d0202f", status: "disconnected" },
    { code: "HV", name: "Harvest", kind: "Timesheets", hue: "#f6552b", status: "disconnected" },
  ],
};

function ConnectSources() {
  const [data, setData] = useState(null);
  useEffect(() => {
    let live = true;
    getSources()
      .then((d) => live && setData(d))
      .catch(() => live && setData(FALLBACK_SOURCES));
    return () => {
      live = false;
    };
  }, []);
  const d = data || FALLBACK_SOURCES;
  const loading = data === null;

  return (
    <div style={{ ...panelStyle, marginBottom: 16 }}>
      <StepHeader
        n={1}
        color="var(--good)"
        text="Connect your timesheet & payroll tools"
        right={
          <span
            style={{
              marginLeft: "auto", fontSize: 11.5, fontWeight: 600,
              color: d.connected ? "var(--good)" : "var(--faint)",
            }}
          >
            {loading ? "checking…" : `${d.connected} connected · live`}
          </span>
        }
      />
      <div style={{ fontSize: 12.5, color: "var(--dim)", marginBottom: 14 }}>
        Runway pulls live hours, LCAT rates, and rosters over API — no manual entry. It also
        mines your historical data to seed pacing insights.
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))",
          gap: 10,
        }}
      >
        {d.sources.map((s) => (
          <SourceBox key={s.code} s={s} />
        ))}
      </div>
    </div>
  );
}

export default function Ingest() {
  const [stage, setStage] = useState("upload"); // upload | extracting | review
  const [result, setResult] = useState(null);
  const [fileName, setFileName] = useState(null);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);

  async function run(file) {
    setError(null);
    setStage("extracting");
    try {
      const data = await ingest(file);
      setResult(data);
      setStage("review");
    } catch (e) {
      setError(e.message);
      setStage("upload");
    }
  }

  function onPick(e) {
    const f = e.target.files?.[0];
    if (f) {
      setFileName(f.name);
      run(f);
    }
  }

  function reset() {
    setResult(null);
    setFileName(null);
    setStage("upload");
  }

  async function onConfirm() {
    try {
      await confirm(result);
      alert(`Contract ${result.contract.piid} saved. Burn plan next.`);
      reset();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div style={{ maxWidth: 860, margin: "0 auto", padding: "28px 32px" }}>
      <h1 style={{ fontFamily: "'Space Grotesk',sans-serif", fontSize: 24, margin: "0 0 4px" }}>
        Add a contract
      </h1>
      <p style={{ color: "var(--dim)", fontSize: 13.5, margin: "0 0 24px" }}>
        Two steps: connect the tools that already hold your labor data, then drop the award PDF.
      </p>

      {error && (
        <div
          style={{
            border: "1px solid var(--bad)", background: "var(--badBg)", borderRadius: 12,
            padding: "10px 14px", marginBottom: 16, fontSize: 13, color: "var(--text)",
          }}
        >
          ⚠️ {error}
        </div>
      )}

      <ConnectSources />

      <StepHeader n={2} text="Drop the signed award PDF" />

      {stage === "upload" && (
        <div
          style={{
            border: "2px dashed var(--border)", borderRadius: 18, padding: "44px 24px",
            textAlign: "center", background: "var(--panel2)",
          }}
        >
          <div style={{ fontFamily: "'Space Grotesk',sans-serif", fontWeight: 600, fontSize: 16 }}>
            Drop an SF-26 / SF-1449 award PDF
          </div>
          <div style={{ fontSize: 12.5, color: "var(--dim)", marginTop: 6 }}>
            or use the bundled sample to see the extraction
          </div>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.md,.txt"
            onChange={onPick}
            style={{ display: "none" }}
          />
          <div style={{ marginTop: 20, display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
            <button
              onClick={() => fileRef.current?.click()}
              style={{
                height: 38, padding: "0 18px", borderRadius: 10, border: "1px solid var(--border)",
                background: "var(--panel)", color: "var(--text)", fontWeight: 600, fontSize: 13,
                cursor: "pointer",
              }}
            >
              Choose a file
            </button>
            <button
              onClick={() => run(null)}
              style={{
                height: 38, padding: "0 18px", borderRadius: 10, border: "none",
                background: "var(--accent)", color: "#fff", fontWeight: 600, fontSize: 13,
                cursor: "pointer", boxShadow: "0 4px 12px rgba(67,97,238,.28)",
              }}
            >
              Ingest sample with AI
            </button>
          </div>
        </div>
      )}

      {stage === "extracting" && (
        <div style={{ ...panelStyle, padding: "40px 30px", textAlign: "center" }}>
          <div
            style={{
              width: 44, height: 44, margin: "0 auto",
              border: "3px solid var(--border)", borderTopColor: "var(--accent)",
              borderRadius: "50%", animation: "rwspin .8s linear infinite",
            }}
          />
          <div
            style={{
              fontFamily: "'Space Grotesk',sans-serif", fontWeight: 600, fontSize: 15, marginTop: 18,
            }}
          >
            Reading the award document…
          </div>
          <div style={{ maxWidth: 420, margin: "16px auto 0" }}>
            <div style={{ height: 7, borderRadius: 5, background: "var(--panel2)", overflow: "hidden" }}>
              <div
                style={{
                  height: "100%",
                  background: "linear-gradient(90deg,var(--accent),var(--accent2))",
                  animation: "rwbar 2.1s ease forwards",
                }}
              />
            </div>
          </div>
          <div
            style={{
              fontSize: 12, color: "var(--dim)", marginTop: 14,
              fontFamily: "'IBM Plex Mono',monospace",
            }}
          >
            {fileName || "sample contract"} · locating CLIN schedule · parsing obligated funding
          </div>
        </div>
      )}

      {stage === "review" && result && <Review result={result} onReset={reset} onConfirm={onConfirm} />}
    </div>
  );
}

function Review({ result, onReset, onConfirm }) {
  const c = result.contract;
  const fc = c.field_confidence || {};
  const meta = [
    ["Contract No.", c.piid, "piid"],
    ["Contractor", c.contractor, "contractor"],
    ["Agency", c.agency, "agency"],
    ["Contract type", c.contract_type, "contract_type"],
    ["Total ceiling", money(c.total_ceiling), "total_ceiling"],
    ["Obligated to date", money(c.total_obligated), "total_obligated"],
    ["Effective date", c.effective_date, "effective_date"],
    ["Contracting Officer", c.contracting_officer, "contracting_officer"],
  ];
  // Lowest-confidence CLIN below the "watch" threshold — drives the amber notice.
  const lowCl = result.clins
    .filter((cl) => cl.confidence != null && cl.confidence < 0.88)
    .sort((a, b) => a.confidence - b.confidence)[0];

  return (
    <div style={{ animation: "rwrise .4s ease" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 14 }}>
        <span style={{ color: "var(--good)", fontSize: 18 }}>✓</span>
        <span style={{ fontWeight: 600, fontSize: 14.5 }}>
          Extraction complete — review before building the plan
        </span>
      </div>

      <div style={{ ...panelStyle, marginBottom: 14 }}>
        <div style={label}>Contract header</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px 24px" }}>
          {meta.map(([k, v, key]) => (
            <div
              key={k}
              style={{
                display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12,
                paddingBottom: 10, borderBottom: "1px solid var(--border)",
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 11, color: "var(--faint)" }}>{k}</div>
                <div
                  style={{
                    fontSize: 13, fontWeight: 600,
                    fontFamily: "'IBM Plex Mono',monospace",
                  }}
                >
                  {v || "—"}
                </div>
              </div>
              <ConfBadge v={fc[key]} />
            </div>
          ))}
        </div>
      </div>

      <div style={{ ...panelStyle, padding: 0, overflow: "hidden", marginBottom: 16 }}>
        <div style={{ ...label, padding: "14px 18px 6px", marginBottom: 0 }}>
          Extracted CLIN schedule
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ color: "var(--faint)", fontSize: 11, textTransform: "uppercase" }}>
                <th style={{ textAlign: "left", padding: "8px 18px", fontWeight: 700 }}>CLIN</th>
                <th style={{ textAlign: "left", padding: "8px 8px", fontWeight: 700 }}>Description</th>
                <th style={{ textAlign: "left", padding: "8px 8px", fontWeight: 700 }}>Type</th>
                <th style={{ textAlign: "right", padding: "8px 8px", fontWeight: 700 }}>Ceiling</th>
                <th style={{ textAlign: "right", padding: "8px 18px", fontWeight: 700 }}>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {result.clins.map((cl) => (
                <tr key={cl.clin} style={{ borderTop: "1px solid var(--border)" }}>
                  <td
                    style={{
                      padding: "11px 18px", fontFamily: "'IBM Plex Mono',monospace",
                      fontWeight: 600,
                    }}
                  >
                    {cl.clin}
                    {cl.is_labor && (
                      <span
                        style={{
                          marginLeft: 7, fontSize: 9.5, fontWeight: 700, padding: "1px 6px",
                          borderRadius: 20, background: "var(--goodBg)", color: "var(--good)",
                          fontFamily: "'Manrope',sans-serif",
                        }}
                      >
                        LABOR
                      </span>
                    )}
                  </td>
                  <td style={{ padding: "11px 8px" }}>{cl.title}</td>
                  <td style={{ padding: "11px 8px", color: "var(--dim)" }}>{cl.type || "—"}</td>
                  <td
                    style={{
                      padding: "11px 8px", textAlign: "right",
                      fontFamily: "'IBM Plex Mono',monospace",
                    }}
                  >
                    {money(cl.ceiling)}
                  </td>
                  <td style={{ padding: "11px 18px", textAlign: "right" }}>
                    <ConfBadge v={cl.confidence} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {lowCl && (
          <div
            style={{
              padding: "11px 18px", background: "var(--warnBg)",
              borderTop: "1px solid var(--border)", display: "flex", gap: 9,
              alignItems: "center", fontSize: 12, color: "var(--text)",
            }}
          >
            <span style={{ color: "var(--warn)" }}>⚠</span>
            CLIN {lowCl.clin} ({lowCl.title}) extracted at{" "}
            {Math.round(lowCl.confidence * 100)}% — verify this line before building the plan.
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
        <button
          onClick={onReset}
          style={{
            height: 38, padding: "0 16px", borderRadius: 10, border: "1px solid var(--border)",
            background: "var(--panel2)", color: "var(--text)", fontWeight: 600, fontSize: 13,
            cursor: "pointer",
          }}
        >
          Start over
        </button>
        <button
          onClick={onConfirm}
          style={{
            height: 38, padding: "0 20px", borderRadius: 10, border: "none",
            background: "var(--accent)", color: "#fff", fontWeight: 600, fontSize: 13,
            cursor: "pointer", boxShadow: "0 4px 12px rgba(67,97,238,.3)",
          }}
        >
          Confirm &amp; build plan →
        </button>
      </div>
    </div>
  );
}
