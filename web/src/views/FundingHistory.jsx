import React, { useEffect, useRef, useState } from "react";
import { getFunding, addMod } from "../api.js";

// Funding History — the contract's dated obligation timeline (SF-26 award +
// every ingested SF-30 mod) with a cumulative-vs-ceiling bar, and a dropzone
// that ingests one more SF-30 against the active contract (POST .../mods).

const money = (v) =>
  v == null
    ? "—"
    : "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });

const panelStyle = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 16,
  padding: 18,
};
const label = {
  fontSize: 11,
  letterSpacing: ".08em",
  textTransform: "uppercase",
  color: "var(--faint)",
  fontWeight: 700,
  marginBottom: 12,
};
const th = {
  textAlign: "left",
  padding: "8px 12px",
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: ".05em",
  color: "var(--faint)",
  fontWeight: 700,
};
const td = { padding: "10px 12px", fontSize: 13, color: "var(--text)" };
const mono = { fontFamily: "'IBM Plex Mono',monospace" };

// The award entry reads differently from a mod: it's the base obligation, not a
// modification. Everything else is a P0000N action.
const isAward = (h) =>
  (h.mod || "").toLowerCase() === "award" ||
  /initial award/i.test(h.action || "");

function FundedBar({ obligated, ceiling }) {
  const pct =
    ceiling && obligated != null
      ? Math.min(100, Math.round((obligated / ceiling) * 100))
      : 0;
  return (
    <div style={{ marginTop: 14 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: 12,
          color: "var(--dim)",
          marginBottom: 6,
        }}
      >
        <span>
          Obligated <b style={{ color: "var(--text)" }}>{money(obligated)}</b>
        </span>
        <span>
          Ceiling <b style={{ color: "var(--text)" }}>{money(ceiling)}</b>
        </span>
      </div>
      <div
        style={{
          height: 12,
          borderRadius: 7,
          background: "var(--panel2)",
          overflow: "hidden",
          border: "1px solid var(--border)",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: "linear-gradient(90deg,var(--accent),var(--accent2))",
            transition: "width .5s ease",
          }}
        />
      </div>
      <div style={{ fontSize: 11.5, color: "var(--dim)", marginTop: 6 }}>
        {pct}% of ceiling obligated
        {pct < 100
          ? " — the remainder awaits future funding modifications."
          : " — fully funded to ceiling."}
      </div>
    </div>
  );
}

// One-file SF-30 dropzone. Uploads, then hands the summary back so the parent
// can refresh the timeline and surface what happened (added / replaced / PIID
// mismatch).
function ModUpload({ contractId, onUploaded }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const fileRef = useRef(null);

  async function run(file) {
    setError(null);
    setNote(null);
    setBusy(true);
    try {
      const r = await addMod(contractId, file);
      const what = r.replaced ? "replaced" : "added";
      setNote(
        `Mod ${r.mod || "(unnumbered)"} ${what}. Obligated now ${money(
          r.total_obligated
        )}.` + (r.piid_mismatch ? " ⚠ Contract number on the doc didn't match." : "")
      );
      onUploaded?.(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  function onPick(e) {
    const f = e.target.files?.[0];
    if (f) run(f);
    e.target.value = ""; // allow re-picking the same file
  }

  return (
    <div style={{ ...panelStyle, marginBottom: 18 }}>
      <div style={label}>Add a modification</div>
      {error && (
        <div
          style={{
            border: "1px solid var(--bad)",
            background: "var(--badBg)",
            borderRadius: 10,
            padding: "9px 13px",
            marginBottom: 12,
            fontSize: 12.5,
            color: "var(--text)",
          }}
        >
          ⚠️ {error}
        </div>
      )}
      {note && (
        <div
          style={{
            border: "1px solid var(--good)",
            background: "var(--goodBg)",
            borderRadius: 10,
            padding: "9px 13px",
            marginBottom: 12,
            fontSize: 12.5,
            color: "var(--text)",
          }}
        >
          ✓ {note}
        </div>
      )}
      <div
        style={{
          border: "2px dashed var(--border)",
          borderRadius: 14,
          padding: "26px 20px",
          textAlign: "center",
          background: "var(--panel2)",
        }}
      >
        {busy ? (
          <>
            <div
              style={{
                width: 36,
                height: 36,
                margin: "0 auto",
                border: "3px solid var(--border)",
                borderTopColor: "var(--accent)",
                borderRadius: "50%",
                animation: "rwspin .8s linear infinite",
              }}
            />
            <div style={{ fontSize: 12.5, color: "var(--dim)", marginTop: 14 }}>
              Reading the SF-30 & folding it into the funding history…
            </div>
          </>
        ) : (
          <>
            <div
              style={{
                fontFamily: "'Space Grotesk',sans-serif",
                fontWeight: 600,
                fontSize: 15,
              }}
            >
              Drop an SF-30 modification PDF
            </div>
            <div style={{ fontSize: 12.5, color: "var(--dim)", marginTop: 6 }}>
              One mod per file. Runway reads the dated funding action and folds it
              in — re-ingesting the same mod number won't double-count.
            </div>
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.md,.txt"
              onChange={onPick}
              style={{ display: "none" }}
            />
            <button
              onClick={() => fileRef.current?.click()}
              style={{
                marginTop: 18,
                height: 38,
                padding: "0 18px",
                borderRadius: 10,
                border: "none",
                background: "var(--accent)",
                color: "#fff",
                fontWeight: 600,
                fontSize: 13,
                cursor: "pointer",
                boxShadow: "0 4px 12px rgba(67,97,238,.28)",
              }}
            >
              Choose an SF-30
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export default function FundingHistory({ contractId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  function load() {
    if (contractId == null) return;
    setError(null);
    getFunding(contractId)
      .then(setData)
      .catch((e) => setError(e.message));
  }

  useEffect(() => {
    setData(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contractId]);

  if (contractId == null) {
    return (
      <div style={{ maxWidth: 820, margin: "0 auto", padding: "28px 32px" }}>
        <div
          style={{
            ...panelStyle,
            textAlign: "center",
            color: "var(--dim)",
            fontSize: 13.5,
          }}
        >
          No contract selected. Open one from the Portfolio to see its funding
          history.
        </div>
      </div>
    );
  }

  const history = data?.obligation_history || [];

  return (
    <div style={{ maxWidth: 820, margin: "0 auto", padding: "28px 32px" }}>
      <h1
        style={{
          fontFamily: "'Space Grotesk',sans-serif",
          fontSize: 24,
          margin: "0 0 4px",
        }}
      >
        Funding history
      </h1>
      <p style={{ color: "var(--dim)", fontSize: 13.5, margin: "0 0 24px" }}>
        The dated obligation trail behind this contract — the award plus each
        SF-30 modification. This is the record Runway reads funding <i>pace</i>{" "}
        from.
      </p>

      {error && (
        <div
          style={{
            border: "1px solid var(--bad)",
            background: "var(--badBg)",
            borderRadius: 12,
            padding: "10px 14px",
            marginBottom: 16,
            fontSize: 13,
            color: "var(--text)",
          }}
        >
          ⚠️ {error}
        </div>
      )}

      {/* Summary + funded bar */}
      <div style={{ ...panelStyle, marginBottom: 18 }}>
        <div style={label}>
          {data?.name || "Contract"} · {data?.piid || contractId}
        </div>
        <FundedBar
          obligated={data?.total_obligated}
          ceiling={data?.total_ceiling}
        />
      </div>

      <ModUpload contractId={contractId} onUploaded={load} />

      {/* Timeline */}
      <div style={{ ...panelStyle, padding: 0, overflow: "hidden" }}>
        <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)" }}>
          <span
            style={{
              fontFamily: "'Space Grotesk',sans-serif",
              fontWeight: 600,
              fontSize: 15,
            }}
          >
            Obligation timeline
          </span>
          <span style={{ fontSize: 12, color: "var(--dim)", marginLeft: 8 }}>
            {history.length} {history.length === 1 ? "entry" : "entries"}
          </span>
        </div>
        {history.length === 0 ? (
          <div
            style={{
              padding: "24px 16px",
              textAlign: "center",
              color: "var(--dim)",
              fontSize: 13,
            }}
          >
            No funding history yet. Upload an SF-30 above to start the trail.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ background: "var(--panel2)" }}>
                  <th style={th}>Mod</th>
                  <th style={th}>Effective</th>
                  <th style={th}>Action</th>
                  <th style={{ ...th, textAlign: "right" }}>Amount</th>
                  <th style={{ ...th, textAlign: "right" }}>Cumulative</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h, i) => (
                  <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ ...td, ...mono, fontWeight: 600 }}>
                      {isAward(h) ? (
                        <span
                          style={{
                            fontSize: 10,
                            fontWeight: 700,
                            padding: "1px 6px",
                            borderRadius: 20,
                            background: "var(--goodBg)",
                            color: "var(--good)",
                            fontFamily: "'Manrope',sans-serif",
                          }}
                        >
                          AWARD
                        </span>
                      ) : (
                        h.mod || "—"
                      )}
                    </td>
                    <td style={{ ...td, ...mono, color: "var(--dim)" }}>
                      {h.date || "—"}
                    </td>
                    <td style={td}>{h.action || "modification"}</td>
                    <td style={{ ...td, ...mono, textAlign: "right" }}>
                      {money(h.amount)}
                    </td>
                    <td
                      style={{
                        ...td,
                        ...mono,
                        textAlign: "right",
                        fontWeight: 600,
                      }}
                    >
                      {money(h.cumulative_obligated)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
