import React, { useRef, useState } from "react";
import { ingest, confirm } from "../api.js";

const money = (v) =>
  v == null ? "—" : "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });

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

function StepHeader({ n, text }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 12 }}>
      <span
        style={{
          width: 22, height: 22, borderRadius: "50%", background: "var(--accent)",
          color: "#fff", display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 12, fontWeight: 700,
        }}
      >
        {n}
      </span>
      <span style={{ fontFamily: "'Space Grotesk',sans-serif", fontWeight: 600, fontSize: 15 }}>
        {text}
      </span>
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
        Ingest a contract
      </h1>
      <p style={{ color: "var(--dim)", fontSize: 13.5, margin: "0 0 24px" }}>
        Drop a signed federal award PDF — the AI reads the CLINs, funding, and period of
        performance into a structured plan.
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

      <StepHeader n={1} text="Drop the signed award PDF" />

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
  const meta = [
    ["Contract No.", c.piid],
    ["Contractor", c.contractor],
    ["Agency", c.agency],
    ["Contract type", c.contract_type],
    ["Total ceiling", money(c.total_ceiling)],
    ["Obligated to date", money(c.total_obligated)],
    ["Effective date", c.effective_date],
    ["Contracting Officer", c.contracting_officer],
  ];

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
          {meta.map(([k, v]) => (
            <div
              key={k}
              style={{
                display: "flex", justifyContent: "space-between", gap: 12,
                paddingBottom: 10, borderBottom: "1px solid var(--border)",
              }}
            >
              <div style={{ fontSize: 11, color: "var(--faint)" }}>{k}</div>
              <div
                style={{
                  fontSize: 13, fontWeight: 600, textAlign: "right",
                  fontFamily: "'IBM Plex Mono',monospace",
                }}
              >
                {v || "—"}
              </div>
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
                <th style={{ textAlign: "left", padding: "8px 8px", fontWeight: 700 }}>Period</th>
                <th style={{ textAlign: "left", padding: "8px 8px", fontWeight: 700 }}>Type</th>
                <th style={{ textAlign: "right", padding: "8px 18px", fontWeight: 700 }}>Ceiling</th>
              </tr>
            </thead>
            <tbody>
              {result.clins.map((cl) => (
                <tr key={cl.clin + (cl.period || "")} style={{ borderTop: "1px solid var(--border)" }}>
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
                  <td style={{ padding: "11px 8px", color: "var(--dim)" }}>{cl.period || "—"}</td>
                  <td style={{ padding: "11px 8px", color: "var(--dim)" }}>{cl.type || "—"}</td>
                  <td
                    style={{
                      padding: "11px 18px", textAlign: "right",
                      fontFamily: "'IBM Plex Mono',monospace",
                    }}
                  >
                    {money(cl.ceiling)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
