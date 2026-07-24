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

function SourceBox({ s, selected, onClick }) {
  const [txt, color, bg] = SOURCE_BADGE[s.status] || SOURCE_BADGE.disconnected;
  return (
    <div
      role="button"
      tabIndex={0}
      aria-expanded={selected}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      style={{
        display: "flex", alignItems: "center", gap: 9, padding: "9px 11px",
        border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
        borderRadius: 11, background: "var(--panel)", cursor: "pointer",
        boxShadow: selected ? "0 0 0 2px color-mix(in srgb, var(--accent) 22%, transparent)" : "none",
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

// Click-to-expand detail. Same panel for every source; the CONTENT is honest
// per state: a live source shows the real rows it's syncing, a placeholder
// shows what it *would* sync + a disabled connect action (no fake data).
function SourceDetail({ s }) {
  const rows = s.preview || [];
  const live = s.status === "live" && rows.length > 0;
  const th = {
    textAlign: "left", padding: "6px 10px", fontSize: 10,
    textTransform: "uppercase", letterSpacing: ".05em", color: "var(--faint)", fontWeight: 700,
  };
  const td = { padding: "7px 10px", fontSize: 12, color: "var(--text)" };
  return (
    <div
      style={{
        marginTop: 10, border: "1px solid var(--border)", borderRadius: 12,
        background: "var(--panel2)", padding: 14, animation: "rwrise .3s ease",
      }}
    >
      {live ? (
        <>
          <div style={{ fontSize: 12, color: "var(--dim)", marginBottom: 8 }}>
            Live sample pulled from <strong style={{ color: "var(--text)" }}>{s.name}</strong> —{" "}
            {s.kind.replace("Timesheets · ", "")}. Showing {rows.length} of the synced rows.
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={th}>Employee</th>
                  <th style={th}>Week ending</th>
                  <th style={th}>Charge</th>
                  <th style={th}>Labor category</th>
                  <th style={{ ...th, textAlign: "right" }}>Hours</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={td}>{r.employee}</td>
                    <td style={{ ...td, fontFamily: "'IBM Plex Mono',monospace" }}>{r.week_ending}</td>
                    <td style={{ ...td, fontFamily: "'IBM Plex Mono',monospace" }}>{r.charge_code}</td>
                    <td style={{ ...td, color: "var(--dim)" }}>{r.labor_category}</td>
                    <td style={{ ...td, textAlign: "right", fontFamily: "'IBM Plex Mono',monospace" }}>
                      {r.total_hours}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
              {s.name} isn’t connected
            </div>
            <div style={{ fontSize: 12, color: "var(--dim)" }}>
              Connecting would sync {s.kind.toLowerCase()} over API. This integration isn’t
              available in the current build.
            </div>
          </div>
          <button
            disabled
            title="Integration not available in this build"
            style={{
              height: 34, padding: "0 16px", borderRadius: 9, border: "1px solid var(--border)",
              background: "var(--panel)", color: "var(--faint)", fontWeight: 600, fontSize: 12.5,
              cursor: "not-allowed",
            }}
          >
            Connect
          </button>
        </div>
      )}
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
  const [openCode, setOpenCode] = useState(null);
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
  const open = d.sources.find((s) => s.code === openCode) || null;

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
          <SourceBox
            key={s.code}
            s={s}
            selected={s.code === openCode}
            onClick={() => setOpenCode(s.code === openCode ? null : s.code)}
          />
        ))}
      </div>
      {open && <SourceDetail s={open} />}
    </div>
  );
}

// --- editable-form helpers -------------------------------------------------

const inputStyle = {
  width: "100%", boxSizing: "border-box", height: 32, padding: "0 9px",
  borderRadius: 8, border: "1px solid var(--border)", background: "var(--panel2)",
  color: "var(--text)", fontSize: 13, fontFamily: "'IBM Plex Mono',monospace",
};

// Parse a typed money/number string to a number (or null when blank).
function num(s) {
  if (s === "" || s == null) return null;
  const n = parseFloat(String(s).replace(/[^0-9.\-]/g, ""));
  return isNaN(n) ? null : n;
}

function TextField({ value, onChange, placeholder, mono = true }) {
  return (
    <input
      style={{ ...inputStyle, fontFamily: mono ? "'IBM Plex Mono',monospace" : "inherit" }}
      value={value ?? ""}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function NumberField({ value, onChange, placeholder }) {
  return (
    <input
      style={inputStyle}
      inputMode="decimal"
      value={value ?? ""}
      placeholder={placeholder}
      onChange={(e) => onChange(num(e.target.value))}
    />
  );
}

const emptyContract = () => ({
  contract: {
    piid: "", contractor: "", agency: "", contract_type: "",
    total_ceiling: null, total_obligated: null, incrementally_funded: null,
    effective_date: "", contracting_officer: "", cor: null, field_confidence: {},
  },
  periods: [],
  clins: [],
});
const emptyClin = (period) => ({
  clin: "", period: period || "Base", title: "", type: "", is_labor: false,
  ceiling: null, est_hours: null, labor_rates: null, confidence: null,
});
const emptyRate = () => ({
  lcat: "", loaded_rate: null, est_hours: null,
  min_education: null, min_experience_yrs: null, clearance: null,
});

const ghostBtn = {
  height: 30, padding: "0 12px", borderRadius: 8, border: "1px solid var(--border)",
  background: "var(--panel2)", color: "var(--text)", fontWeight: 600, fontSize: 12,
  cursor: "pointer",
};

export default function Ingest() {
  const [stage, setStage] = useState("upload"); // upload | extracting | review
  const [result, setResult] = useState(null);
  const [editing, setEditing] = useState(false);
  const [fileName, setFileName] = useState(null);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);

  async function run(file) {
    setError(null);
    setEditing(false);
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

  // Manual entry (no AI): start from a blank contract, straight into edit mode.
  function startManual() {
    setError(null);
    setFileName(null);
    setResult(emptyContract());
    setEditing(true);
    setStage("review");
  }

  function reset() {
    setResult(null);
    setFileName(null);
    setEditing(false);
    setStage("upload");
  }

  async function onConfirm() {
    if (!result?.contract?.piid?.trim()) {
      setError("A contract number (PIID) is required before saving.");
      return;
    }
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
        Connect the tools that hold your labor data, then drop an award PDF — or enter it by hand.
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

      <StepHeader n={2} text="Add the signed award" />

      {stage === "upload" && (
        <div
          style={{
            border: "2px dashed var(--border)", borderRadius: 18, padding: "40px 24px",
            textAlign: "center", background: "var(--panel2)",
          }}
        >
          <div style={{ fontFamily: "'Space Grotesk',sans-serif", fontWeight: 600, fontSize: 16 }}>
            Drop an SF-26 / SF-1449 award PDF
          </div>
          <div style={{ fontSize: 12.5, color: "var(--dim)", marginTop: 6 }}>
            One award per file. AI reads it; you review and fix anything before saving.
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
          <div style={{ marginTop: 18, fontSize: 12.5, color: "var(--dim)" }}>
            Don’t want to use AI?{" "}
            <button
              onClick={startManual}
              style={{
                background: "none", border: "none", padding: 0, cursor: "pointer",
                color: "var(--accent)", fontWeight: 700, fontSize: 12.5,
              }}
            >
              Enter the contract manually →
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
            {fileName || "sample contract"} · this can take ~30s for a full award · parsing CLINs & rates
          </div>
        </div>
      )}

      {stage === "review" && result && (
        <Review
          value={result}
          onChange={setResult}
          editing={editing}
          setEditing={setEditing}
          onReset={reset}
          onConfirm={onConfirm}
        />
      )}
    </div>
  );
}

// The review/edit surface. The SAME form powers three cases: reviewing an AI
// extraction, editing it, and manual entry (which is just an empty draft opened
// in edit mode). Everything saves through POST /api/contracts/confirm.
function Review({ value, onChange, editing, setEditing, onReset, onConfirm }) {
  const [openRates, setOpenRates] = useState({}); // clin index -> expanded?
  const c = value.contract || {};
  const fc = c.field_confidence || {};
  const clins = value.clins || [];
  const periods = value.periods || [];
  const isAI = !!c.field_confidence && Object.keys(fc).length > 0;

  // --- draft mutations (all immutable, funnelled through onChange) ---
  const setC = (k, v) => onChange({ ...value, contract: { ...c, [k]: v } });
  const setClin = (i, k, v) =>
    onChange({ ...value, clins: clins.map((cl, j) => (j === i ? { ...cl, [k]: v } : cl)) });
  const addClin = (period) => onChange({ ...value, clins: [...clins, emptyClin(period)] });
  const removeClin = (i) => onChange({ ...value, clins: clins.filter((_, j) => j !== i) });
  const setRate = (ci, ri, k, v) =>
    setClin(ci, "labor_rates", (clins[ci].labor_rates || []).map((r, j) => (j === ri ? { ...r, [k]: v } : r)));
  const addRate = (ci) => setClin(ci, "labor_rates", [...(clins[ci].labor_rates || []), emptyRate()]);
  const removeRate = (ci, ri) =>
    setClin(ci, "labor_rates", (clins[ci].labor_rates || []).filter((_, j) => j !== ri));
  const setPeriod = (name, k, v) =>
    onChange({ ...value, periods: periods.map((p) => (p.name === name ? { ...p, [k]: v } : p)) });
  const addPeriod = () => {
    const name = periods.length === 0 ? "Base" : `Option ${periods.length}`;
    onChange({
      ...value,
      periods: [...periods, { name, pop_start: "", pop_end: "", exercised: periods.length === 0, ceiling: null }],
    });
  };

  // Group CLINs under their period. Periods[] fixes the order; any CLIN whose
  // period has no period-row (e.g. mid-manual-entry) still gets a group.
  const groupNames = periods.map((p) => p.name);
  clins.forEach((cl) => {
    const k = cl.period || "Base";
    if (!groupNames.includes(k)) groupNames.push(k);
  });
  const periodMeta = Object.fromEntries(periods.map((p) => [p.name, p]));

  const headerFields = [
    ["Contract No.", "piid"],
    ["Contractor", "contractor"],
    ["Agency", "agency"],
    ["Contract type", "contract_type"],
    ["Total ceiling", "total_ceiling"],
    ["Obligated to date", "total_obligated"],
    ["Effective date", "effective_date"],
    ["Contracting Officer", "contracting_officer"],
  ];
  const moneyKeys = new Set(["total_ceiling", "total_obligated"]);
  const lowCl = clins
    .filter((cl) => cl.confidence != null && cl.confidence < 0.88)
    .sort((a, b) => a.confidence - b.confidence)[0];

  return (
    <div style={{ animation: "rwrise .4s ease" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 14 }}>
        <span style={{ color: "var(--good)", fontSize: 18 }}>{isAI ? "✓" : "✎"}</span>
        <span style={{ fontWeight: 600, fontSize: 14.5 }}>
          {isAI
            ? editing
              ? "Editing extraction — fix anything the AI got wrong"
              : "Extraction complete — review before building the plan"
            : "Enter the contract by hand"}
        </span>
        {!editing && (
          <button onClick={() => setEditing(true)} style={{ ...ghostBtn, marginLeft: "auto" }}>
            ✎ Edit
          </button>
        )}
      </div>

      {/* Contract header */}
      <div style={{ ...panelStyle, marginBottom: 14 }}>
        <div style={label}>Contract header</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px 24px" }}>
          {headerFields.map(([k, key]) => (
            <div
              key={key}
              style={{
                display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12,
                paddingBottom: 10, borderBottom: "1px solid var(--border)",
              }}
            >
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 11, color: "var(--faint)", marginBottom: editing ? 4 : 0 }}>{k}</div>
                {editing ? (
                  moneyKeys.has(key) ? (
                    <NumberField value={c[key]} onChange={(v) => setC(key, v)} placeholder="0" />
                  ) : (
                    <TextField value={c[key]} onChange={(v) => setC(key, v)} />
                  )
                ) : (
                  <div style={{ fontSize: 13, fontWeight: 600, fontFamily: "'IBM Plex Mono',monospace" }}>
                    {moneyKeys.has(key) ? money(c[key]) : c[key] || "—"}
                  </div>
                )}
              </div>
              {!editing && <ConfBadge v={fc[key]} />}
            </div>
          ))}
        </div>
      </div>

      {/* CLIN schedule, grouped by period */}
      <div style={{ ...label, display: "flex", alignItems: "center" }}>
        CLIN schedule by period
        {editing && (
          <button onClick={addPeriod} style={{ ...ghostBtn, marginLeft: "auto" }}>
            + Add period
          </button>
        )}
      </div>

      {groupNames.length === 0 && (
        <div style={{ ...panelStyle, marginBottom: 16, textAlign: "center", color: "var(--dim)", fontSize: 13 }}>
          No periods yet. {editing ? "Add a period, then add its CLINs." : ""}
        </div>
      )}

      {groupNames.map((name) => {
        const p = periodMeta[name];
        const items = clins
          .map((cl, idx) => ({ cl, idx }))
          .filter(({ cl }) => (cl.period || "Base") === name);
        const sum = items.reduce((s, { cl }) => s + (Number(cl.ceiling) || 0), 0);
        const exercised = p ? p.exercised : true;
        return (
          <div key={name} style={{ ...panelStyle, padding: 0, overflow: "hidden", marginBottom: 12 }}>
            <div
              style={{
                display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
                padding: "12px 16px", background: "var(--panel2)", borderBottom: "1px solid var(--border)",
              }}
            >
              <span style={{ fontFamily: "'Space Grotesk',sans-serif", fontWeight: 600, fontSize: 14 }}>
                {name}
              </span>
              <span
                style={{
                  fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 20,
                  color: exercised ? "var(--good)" : "var(--faint)",
                  background: exercised ? "var(--goodBg)" : "color-mix(in srgb, var(--faint) 12%, transparent)",
                }}
              >
                {exercised ? "Exercised" : "Not exercised"}
              </span>
              {editing && p ? (
                <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--dim)" }}>
                  <input
                    type="checkbox"
                    checked={!!p.exercised}
                    onChange={(e) => setPeriod(name, "exercised", e.target.checked)}
                  />
                  exercised
                </span>
              ) : (
                p && (p.pop_start || p.pop_end) && (
                  <span style={{ fontSize: 11.5, color: "var(--dim)", fontFamily: "'IBM Plex Mono',monospace" }}>
                    {p.pop_start || "?"} → {p.pop_end || "?"}
                  </span>
                )
              )}
              {editing && p && (
                <span style={{ display: "flex", gap: 6, alignItems: "center", marginLeft: 4 }}>
                  <input
                    style={{ ...inputStyle, width: 120, height: 28 }}
                    placeholder="start"
                    value={p.pop_start || ""}
                    onChange={(e) => setPeriod(name, "pop_start", e.target.value)}
                  />
                  <input
                    style={{ ...inputStyle, width: 120, height: 28 }}
                    placeholder="end"
                    value={p.pop_end || ""}
                    onChange={(e) => setPeriod(name, "pop_end", e.target.value)}
                  />
                </span>
              )}
              <span
                style={{
                  marginLeft: "auto", fontSize: 12.5, fontWeight: 600,
                  fontFamily: "'IBM Plex Mono',monospace", color: "var(--dim)",
                }}
              >
                {money(sum)}
              </span>
            </div>

            <div style={{ padding: "6px 0" }}>
              {items.length === 0 && (
                <div style={{ padding: "10px 16px", fontSize: 12.5, color: "var(--faint)" }}>
                  No CLINs in this period.
                </div>
              )}
              {items.map(({ cl, idx }) => (
                <ClinRow
                  key={idx}
                  cl={cl}
                  idx={idx}
                  editing={editing}
                  open={!!openRates[idx]}
                  toggle={() => setOpenRates((o) => ({ ...o, [idx]: !o[idx] }))}
                  setClin={setClin}
                  removeClin={removeClin}
                  setRate={setRate}
                  addRate={addRate}
                  removeRate={removeRate}
                />
              ))}
              {editing && (
                <div style={{ padding: "8px 16px" }}>
                  <button onClick={() => addClin(name)} style={ghostBtn}>
                    + Add CLIN to {name}
                  </button>
                </div>
              )}
            </div>
          </div>
        );
      })}

      {lowCl && !editing && (
        <div
          style={{
            padding: "11px 16px", background: "var(--warnBg)", borderRadius: 12,
            marginBottom: 16, display: "flex", gap: 9, alignItems: "center", fontSize: 12, color: "var(--text)",
          }}
        >
          <span style={{ color: "var(--warn)" }}>⚠</span>
          CLIN {lowCl.clin} ({lowCl.title}) extracted at {Math.round(lowCl.confidence * 100)}% — edit and
          verify this line before building the plan.
        </div>
      )}

      <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 16 }}>
        <button
          onClick={onReset}
          style={{
            height: 38, padding: "0 16px", borderRadius: 10, border: "1px solid var(--border)",
            background: "var(--panel2)", color: "var(--text)", fontWeight: 600, fontSize: 13, cursor: "pointer",
          }}
        >
          Start over
        </button>
        {editing && isAI && (
          <button onClick={() => setEditing(false)} style={{ ...ghostBtn, height: 38 }}>
            Save edits
          </button>
        )}
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

// One CLIN line, with an expandable fully-burdened labor-rate table (the rates
// the burn engine will price hours against). Read-only or editable.
function ClinRow({ cl, idx, editing, open, toggle, setClin, removeClin, setRate, addRate, removeRate }) {
  const rates = cl.labor_rates || [];
  const cell = { padding: "9px 8px", fontSize: 13, verticalAlign: "middle" };
  const mono = { fontFamily: "'IBM Plex Mono',monospace" };
  return (
    <div style={{ borderTop: "1px solid var(--border)" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: editing ? "120px 1fr 80px 130px 34px" : "150px 1fr 70px 130px",
          gap: 10, alignItems: "center", padding: "9px 16px",
        }}
      >
        <div style={{ ...mono, fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}>
          {editing ? (
            <TextField value={cl.clin} onChange={(v) => setClin(idx, "clin", v)} placeholder="0001" />
          ) : (
            <>
              {cl.clin || "—"}
              {cl.is_labor && (
                <span
                  style={{
                    fontSize: 9, fontWeight: 700, padding: "1px 5px", borderRadius: 20,
                    background: "var(--goodBg)", color: "var(--good)", fontFamily: "'Manrope',sans-serif",
                  }}
                >
                  LABOR
                </span>
              )}
            </>
          )}
        </div>
        <div>
          {editing ? (
            <TextField value={cl.title} onChange={(v) => setClin(idx, "title", v)} mono={false} placeholder="Description" />
          ) : (
            <span style={{ fontSize: 13 }}>{cl.title || "—"}</span>
          )}
        </div>
        <div style={{ color: "var(--dim)" }}>
          {editing ? (
            <TextField value={cl.type} onChange={(v) => setClin(idx, "type", v)} placeholder="T&M" />
          ) : (
            <span style={mono}>{cl.type || "—"}</span>
          )}
        </div>
        <div style={{ textAlign: editing ? "left" : "right", ...mono }}>
          {editing ? (
            <NumberField value={cl.ceiling} onChange={(v) => setClin(idx, "ceiling", v)} placeholder="0" />
          ) : (
            money(cl.ceiling)
          )}
        </div>
        {editing && (
          <button
            onClick={() => removeClin(idx)}
            title="Remove CLIN"
            style={{ ...ghostBtn, width: 34, padding: 0, color: "var(--bad)" }}
          >
            ✕
          </button>
        )}
      </div>

      {/* labor toggle + rate expander */}
      <div style={{ padding: "0 16px 8px", display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
        {editing && (
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, color: "var(--dim)" }}>
            <input
              type="checkbox"
              checked={!!cl.is_labor}
              onChange={(e) => setClin(idx, "is_labor", e.target.checked)}
            />
            labor CLIN
          </label>
        )}
        {(cl.is_labor || rates.length > 0) && (
          <button
            onClick={toggle}
            style={{
              background: "none", border: "none", padding: 0, cursor: "pointer",
              color: "var(--accent)", fontWeight: 600, fontSize: 12,
            }}
          >
            {open ? "▾" : "▸"} {rates.length} labor rate{rates.length === 1 ? "" : "s"}
            {" "}(LCAT &amp; billing)
          </button>
        )}
      </div>

      {open && (
        <div style={{ padding: "0 16px 14px" }}>
          <div style={{ border: "1px solid var(--border)", borderRadius: 10, overflow: "hidden" }}>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ color: "var(--faint)", fontSize: 10, textTransform: "uppercase", background: "var(--panel2)" }}>
                    <th style={{ ...cell, textAlign: "left" }}>Labor category (LCAT)</th>
                    <th style={{ ...cell, textAlign: "right" }}>Loaded rate/hr</th>
                    <th style={{ ...cell, textAlign: "right" }}>Est. hrs</th>
                    <th style={{ ...cell, textAlign: "left" }}>Min. education</th>
                    <th style={{ ...cell, textAlign: "center" }}>Min. yrs</th>
                    <th style={{ ...cell, textAlign: "left" }}>Clearance</th>
                    {editing && <th style={cell} />}
                  </tr>
                </thead>
                <tbody>
                  {rates.length === 0 && (
                    <tr>
                      <td colSpan={editing ? 7 : 6} style={{ ...cell, color: "var(--faint)" }}>
                        No labor rates {editing ? "— add the LCAT rate lines below." : "on this CLIN."}
                      </td>
                    </tr>
                  )}
                  {rates.map((r, ri) => (
                    <tr key={ri} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={cell}>
                        {editing ? (
                          <TextField value={r.lcat} onChange={(v) => setRate(idx, ri, "lcat", v)} mono={false} />
                        ) : (
                          r.lcat
                        )}
                      </td>
                      <td style={{ ...cell, textAlign: "right", ...mono }}>
                        {editing ? (
                          <NumberField value={r.loaded_rate} onChange={(v) => setRate(idx, ri, "loaded_rate", v)} />
                        ) : r.loaded_rate != null ? (
                          "$" + Number(r.loaded_rate).toLocaleString("en-US", { minimumFractionDigits: 2 })
                        ) : (
                          "—"
                        )}
                      </td>
                      <td style={{ ...cell, textAlign: "right", ...mono }}>
                        {editing ? (
                          <NumberField value={r.est_hours} onChange={(v) => setRate(idx, ri, "est_hours", v)} />
                        ) : r.est_hours != null ? (
                          Number(r.est_hours).toLocaleString()
                        ) : (
                          "—"
                        )}
                      </td>
                      <td style={cell}>
                        {editing ? (
                          <TextField value={r.min_education} onChange={(v) => setRate(idx, ri, "min_education", v)} mono={false} />
                        ) : (
                          r.min_education || "—"
                        )}
                      </td>
                      <td style={{ ...cell, textAlign: "center" }}>
                        {editing ? (
                          <NumberField value={r.min_experience_yrs} onChange={(v) => setRate(idx, ri, "min_experience_yrs", v)} />
                        ) : (
                          r.min_experience_yrs ?? "—"
                        )}
                      </td>
                      <td style={cell}>
                        {editing ? (
                          <TextField value={r.clearance} onChange={(v) => setRate(idx, ri, "clearance", v)} mono={false} />
                        ) : (
                          r.clearance || "—"
                        )}
                      </td>
                      {editing && (
                        <td style={{ ...cell, textAlign: "center" }}>
                          <button
                            onClick={() => removeRate(idx, ri)}
                            title="Remove rate"
                            style={{ ...ghostBtn, width: 28, height: 26, padding: 0, color: "var(--bad)" }}
                          >
                            ✕
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {editing && (
              <div style={{ padding: "8px 10px", borderTop: "1px solid var(--border)" }}>
                <button onClick={() => addRate(idx)} style={ghostBtn}>
                  + Add LCAT rate
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
