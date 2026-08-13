import React, { useEffect, useMemo, useState } from "react";
import {
  getRateModel,
  saveRateModel,
  getLcatRates,
  listContracts,
  importRateAgreement,
} from "../api.js";
import { money, panelStyle } from "../format.js";
import ImportRateSchedule from "../components/ImportRateSchedule.jsx";

const grotesk = "'Space Grotesk',sans-serif";
const mono = "'IBM Plex Mono',monospace";

// Mirrors rates.burden() in the backend, for live preview while the user types.
// The server is authoritative — every figure that gets stored or shown on the Flight
// Deck is computed there. This exists so the buildup updates as you drag a
// percentage, not to be a second implementation of the truth.
function buildup(direct, { fringe, overhead, gna }) {
  const d = Number(direct) || 0;
  const f = d * (Number(fringe) || 0);
  const lf = d + f;
  const oh = lf * (Number(overhead) || 0);
  const burdened = lf + oh;
  const ga = burdened * (Number(gna) || 0);
  return { direct: d, fringe: f, labor_plus_fringe: lf, overhead: oh, burdened, gna: ga, total: burdened + ga };
}

const LEVELS = [
  {
    n: 1,
    title: "Contract documents only",
    body: "Burn, period-of-performance clock, CLIN exhaustion and every tripwire — from the award PDF alone. Cost falls back to the negotiated billing rate, so margin is withheld rather than estimated.",
  },
  {
    n: 2,
    title: "Three company percentages + category averages",
    body: "Enter fringe, overhead and G&A once, plus a direct rate per labor category. You get real margin and a rate variance — without a single employee name or payroll file.",
  },
  {
    n: 3,
    title: "Per-person direct rates",
    body: "True cost-to-complete, per person. Not available yet (#96): the engine already prefers a person's own rate wherever one exists, so all it needs is a way to enter one here, against a name picked from your people directory. Level 2 below is a complete, supported state in the meantime.",
  },
];

// The indirect-rate panel (#77). Deliberately built around the fact that all of this
// is optional: the level card at the top tells the user what they get for what they
// share, and Level 1 is presented as a supported, complete state rather than a
// nag. Nobody has to hand this app payroll to find out their contract is overrunning.
export default function IndirectRates({ contractId, setActiveId }) {
  const [stored, setStored] = useState(null);
  const [rateLines, setRateLines] = useState([]);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(null);
  const [uploading, setUploading] = useState(false);

  // Editable draft. Percentages are held as whole numbers because that is how an
  // accountant says them ("32", not "0.32"); converted on save.
  const [fy, setFy] = useState("");
  const [status, setStatus] = useState("provisional");
  const [pools, setPools] = useState({ fringe: "", overhead: "", gna: "" });
  const [direct, setDirect] = useState({}); // lcat -> direct $/hr as typed

  useEffect(() => {
    if (contractId || !setActiveId) return;
    listContracts()
      .then((cs) => cs.length && setActiveId(cs[0].id))
      .catch((e) => setError(e.message));
  }, [contractId, setActiveId]);

  function load() {
    if (!contractId) return;
    Promise.all([getRateModel(contractId), getLcatRates(contractId)])
      .then(([m, r]) => {
        setStored(m);
        setRateLines(r.rate_lines || []);
        const byPool = {};
        for (const p of m.pools || []) byPool[p.pool] = String(Math.round(p.rate * 10000) / 100);
        setPools({ fringe: byPool.fringe ?? "", overhead: byPool.overhead ?? "", gna: byPool.gna ?? "" });
        setFy(m.pools?.[0]?.fiscal_year || "");
        setStatus(m.pools?.[0]?.status || "provisional");
        const d = {};
        for (const r2 of m.direct_rates || []) if (r2.lcat) d[r2.lcat] = String(r2.rate);
        setDirect(d);
      })
      .catch((e) => setError(e.message));
  }
  useEffect(load, [contractId]);

  const frac = useMemo(
    () => ({
      fringe: (Number(pools.fringe) || 0) / 100,
      overhead: (Number(pools.overhead) || 0) / 100,
      gna: (Number(pools.gna) || 0) / 100,
    }),
    [pools]
  );

  const anyPool = frac.fringe > 0 || frac.overhead > 0 || frac.gna > 0;
  const anyDirect = Object.values(direct).some((v) => Number(v) > 0);
  // Same rule the backend applies (rates.CostModel.level): direct rates without an
  // indirect pool are not a margin tier, because the result can't differ
  // meaningfully from a discounted billing rate.
  const level = !anyPool ? 1 : anyDirect ? 2 : 1;

  // Import the letter rather than typing what it says. Reloads instead of merging
  // into local state on purpose: the server decides which set is in force (a letter
  // carrying a final determination supersedes the provisional rates it was billed
  // at), and a client that guessed would show one thing while pricing another.
  async function onImportAgreement(file, input) {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const res = await importRateAgreement(contractId, file);
      load();
      const fyText = res.fiscal_year ? `FY${res.fiscal_year}` : "no fiscal year stated";
      setSaved(
        `Read ${res.pools_stored} ${res.status} pool${res.pools_stored === 1 ? "" : "s"} · ${fyText}` +
          (res.final_determination_found ? " · final determination applied" : "") +
          (res.piid_mismatch ? " · ⚠ letter names a different contract" : "")
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(false);
      // Let the same file be re-picked after a failure.
      if (input) input.value = "";
    }
  }

  async function onSave() {
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const body = {
        fiscal_year: fy || null,
        status,
        pools: ["fringe", "overhead", "gna"]
          .filter((k) => Number(pools[k]) > 0)
          .map((k) => ({ pool: k, rate: Number(pools[k]) / 100 })),
        direct_rates: Object.entries(direct)
          .filter(([, v]) => Number(v) > 0)
          .map(([lcat, v]) => ({ lcat, rate: Number(v) })),
      };
      const m = await saveRateModel(contractId, body);
      setStored(m);
      setSaved(
        m.model.margin_available
          ? `Saved. Margin is now available on this contract (Level ${m.model.level}).`
          : "Saved. This contract stays on billing-only — add the three percentages to unlock margin."
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function onClear() {
    setSaving(true);
    try {
      await saveRateModel(contractId, { fiscal_year: fy || null, status, pools: [], direct_rates: [] });
      setPools({ fringe: "", overhead: "", gna: "" });
      setDirect({});
      setSaved("Removed. This contract is back to billing-only, and no cost data is stored.");
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  const input = {
    height: 34,
    padding: "0 10px",
    borderRadius: 9,
    border: "1px solid var(--border)",
    background: "var(--inputBg)",
    color: "var(--text)",
    fontFamily: mono,
    fontSize: 13,
    width: 90,
  };
  const label = {
    fontSize: 11,
    letterSpacing: ".08em",
    textTransform: "uppercase",
    fontWeight: 700,
    color: "var(--faint)",
  };
  const cell = { padding: "9px 12px", fontSize: 12.5, fontFamily: mono };

  if (error) return <div style={{ padding: 40, color: "var(--bad)" }}>{error}</div>;
  if (!stored) return <div style={{ padding: 40, color: "var(--dim)" }}>Loading rates…</div>;

  const activeLevel = stored.model?.level || 1;

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: "28px 32px" }}>
      {/* No heading here — the top bar owns the view title (#201). This line stays
          because "optional" is the thing a user needs to read before the salary asks. */}
      <p style={{ color: "var(--dim)", fontSize: 13.5, margin: "0 0 22px", lineHeight: 1.55 }}>
        What an hour <b>costs</b> you, next to what you <b>bill</b> for it. Everything on this page is
        optional — the app is fully functional without it.
      </p>

      {/* What you get for what you share. The point of leading with this is that a
          user should never wonder why the app is asking for salaries. */}
      <div style={{ display: "grid", gap: 10, marginBottom: 22 }}>
        {LEVELS.map((l) => {
          const on = activeLevel >= l.n;
          return (
            <div
              key={l.n}
              style={{
                ...panelStyle,
                padding: "12px 15px",
                borderColor: on ? "var(--good)" : "var(--border)",
                opacity: on ? 1 : 0.72,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
                <span
                  style={{
                    fontSize: 10.5,
                    fontWeight: 700,
                    padding: "2px 8px",
                    borderRadius: 20,
                    background: on ? "var(--goodBg)" : "var(--panel2)",
                    color: on ? "var(--good)" : "var(--faint)",
                  }}
                >
                  LEVEL {l.n}
                </span>
                <span style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 13.5, color: "var(--text)" }}>
                  {l.title}
                </span>
                {on && <span style={{ fontSize: 11, color: "var(--good)", fontWeight: 600 }}>active</span>}
                {l.n === 3 && !on && (
                  <span style={{ fontSize: 11, color: "var(--faint)" }}>opt-in · arrives with #69</span>
                )}
              </div>
              <div style={{ fontSize: 12.5, color: "var(--dim)", marginTop: 5, lineHeight: 1.5 }}>{l.body}</div>
            </div>
          );
        })}
      </div>

      {/* the three percentages */}
      <div style={{ ...panelStyle, padding: 18, marginBottom: 18 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
          <span style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 15 }}>Indirect pools</span>
          {/* Upload the document instead of typing the rates (#78). Sits on this panel
              because it answers this panel's question, and because the letter is the
              only thing that states each pool's application base — the award face
              prints three percentages and no bases at all. */}
          <label
            style={{
              marginLeft: "auto", fontSize: 12, fontWeight: 600, cursor: uploading ? "wait" : "pointer",
              color: "var(--accent)", opacity: uploading ? 0.6 : 1,
            }}
            title="Read the pools, their bases and the fiscal year from a rate agreement PDF"
          >
            {uploading ? "reading…" : "⇪ import rate agreement"}
            <input
              type="file"
              accept=".pdf,.txt"
              disabled={uploading}
              style={{ display: "none" }}
              onChange={(e) => onImportAgreement(e.target.files?.[0], e.target)}
            />
          </label>
          {stored.scope === "company" && stored.pools?.length > 0 && (
            <span style={{ fontSize: 11.5, color: "var(--dim)" }}>
              inherited from your company rates — editing here overrides them for this contract
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "flex-end" }}>
          {[
            ["fringe", "Fringe %", "on direct labor"],
            ["overhead", "Overhead %", "on labor + fringe"],
            ["gna", "G&A %", "on total cost input"],
          ].map(([k, lab, base]) => (
            <div key={k}>
              <div style={label}>{lab}</div>
              <input
                style={{ ...input, marginTop: 6 }}
                value={pools[k]}
                inputMode="decimal"
                placeholder="—"
                onChange={(e) => setPools((p) => ({ ...p, [k]: e.target.value }))}
              />
              <div style={{ fontSize: 10.5, color: "var(--faint)", marginTop: 4 }}>{base}</div>
            </div>
          ))}
          <div>
            <div style={label}>Fiscal year</div>
            <input
              style={{ ...input, marginTop: 6, width: 100 }}
              value={fy}
              placeholder="FY26"
              onChange={(e) => setFy(e.target.value)}
            />
            <div style={{ fontSize: 10.5, color: "var(--faint)", marginTop: 4 }}>rates are per year</div>
          </div>
          <div>
            <div style={label}>Status</div>
            <select
              style={{ ...input, marginTop: 6, width: 130, fontFamily: "inherit" }}
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="provisional">Provisional</option>
              <option value="actual">Actual (settled)</option>
            </select>
            <div style={{ fontSize: 10.5, color: "var(--faint)", marginTop: 4 }}>trued up in #87</div>
          </div>
        </div>
        {/* The ladder, worked live on the first direct rate we have, so the user can
            check our arithmetic against their own rate exhibit. */}
        {anyPool && (
          <div style={{ marginTop: 16, fontSize: 12, color: "var(--dim)", fontFamily: mono }}>
            {(() => {
              const first = Object.values(direct).find((v) => Number(v) > 0) || 62;
              const b = buildup(first, frac);
              return (
                <>
                  ${b.direct.toFixed(2)} direct → +${b.fringe.toFixed(2)} fringe → ${b.labor_plus_fringe.toFixed(2)}{" "}
                  → +${b.overhead.toFixed(2)} OH → ${b.burdened.toFixed(2)} → +${b.gna.toFixed(2)} G&amp;A →{" "}
                  <b style={{ color: "var(--text)" }}>${b.total.toFixed(2)} total cost/hr</b>
                  {!Object.values(direct).some((v) => Number(v) > 0) && " (example direct rate)"}
                </>
              );
            })()}
            <div style={{ fontSize: 11, marginTop: 5 }}>
              Fee is not part of this — it comes from the contract type, so the same rates price an FFP and a
              CPFF award.
            </div>
          </div>
        )}
      </div>

      {/* direct rates per LCAT, with the reconciliation the ticket asks for */}
      <div style={{ ...panelStyle, padding: 0, overflow: "hidden", marginBottom: 18 }}>
        <div style={{ padding: "16px 18px 12px" }}>
          <div style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 15 }}>Direct labor rates by category</div>
          <div style={{ fontSize: 12.5, color: "var(--dim)", marginTop: 5, lineHeight: 1.5 }}>
            Category averages — no employee names, no payroll file. Where the derived rate and the award&apos;s
            negotiated rate disagree, both are shown: that gap is normal (rates negotiated at a prior
            year&apos;s indirects, or a price discounted to win), and Runway will never pick one for you.
          </div>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--panel2)", color: "var(--faint)", fontSize: 10, textTransform: "uppercase" }}>
                <th style={{ ...cell, textAlign: "left", fontFamily: "inherit" }}>Labor category</th>
                <th style={{ ...cell, textAlign: "left", fontFamily: "inherit" }}>CLIN</th>
                <th style={{ ...cell, textAlign: "right", fontFamily: "inherit" }}>Direct $/hr</th>
                <th style={{ ...cell, textAlign: "right", fontFamily: "inherit" }}>Derived cost</th>
                <th style={{ ...cell, textAlign: "right", fontFamily: "inherit" }}>Negotiated (award)</th>
                <th style={{ ...cell, textAlign: "right", fontFamily: "inherit" }}>Variance</th>
              </tr>
            </thead>
            <tbody>
              {rateLines.length === 0 && (
                <tr>
                  <td colSpan={6} style={{ ...cell, color: "var(--faint)", fontFamily: "inherit" }}>
                    No rate lines on this contract yet — import the award&apos;s rate schedule below and the
                    categories appear here.
                  </td>
                </tr>
              )}
              {rateLines.map((l) => {
                const typed = direct[l.lcat] ?? "";
                const b = Number(typed) > 0 ? buildup(typed, frac) : null;
                // Fee isn't subtracted: #76 carries no fee rate yet (that's #80), so
                // on a fee-bearing type this gap still includes the fee. Labelled as
                // "vs buildup" rather than "margin" for exactly that reason.
                const delta = b ? l.rate - b.total : null;
                return (
                  <tr key={`${l.clin}|${l.lcat}`} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ ...cell, fontFamily: "inherit" }}>{l.lcat}</td>
                    <td style={{ ...cell, color: "var(--dim)" }}>{l.clin}</td>
                    <td style={{ ...cell, textAlign: "right" }}>
                      <input
                        style={{ ...input, width: 84, height: 30, textAlign: "right" }}
                        value={typed}
                        inputMode="decimal"
                        placeholder="—"
                        onChange={(e) => setDirect((d) => ({ ...d, [l.lcat]: e.target.value }))}
                      />
                    </td>
                    <td style={{ ...cell, textAlign: "right", color: b ? "var(--text)" : "var(--faint)" }}>
                      {b ? `$${b.total.toFixed(2)}` : "—"}
                    </td>
                    <td style={{ ...cell, textAlign: "right", color: "var(--dim)" }}>${l.rate.toFixed(2)}</td>
                    <td
                      style={{
                        ...cell,
                        textAlign: "right",
                        color: delta == null ? "var(--faint)" : delta < 0 ? "var(--bad)" : "var(--dim)",
                        fontWeight: delta != null && delta < 0 ? 700 : 400,
                      }}
                      title={
                        delta == null
                          ? ""
                          : delta < 0
                            ? "The award bills below your derived cost — check this before it becomes a loss."
                            : "The award bills above your derived cost; the gap includes fee."
                      }
                    >
                      {delta == null ? "—" : `${delta < 0 ? "−" : "+"}$${Math.abs(delta).toFixed(2)}`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 20 }}>
        <button
          type="button"
          onClick={onSave}
          disabled={saving}
          style={{
            height: 36,
            padding: "0 18px",
            borderRadius: 9,
            border: "none",
            background: "var(--accent)",
            color: "#fff",
            fontWeight: 600,
            fontSize: 13,
            cursor: saving ? "default" : "pointer",
            opacity: saving ? 0.6 : 1,
          }}
        >
          {saving ? "Saving…" : "Save rates"}
        </button>
        <button
          type="button"
          onClick={onClear}
          disabled={saving}
          style={{
            height: 36,
            padding: "0 14px",
            borderRadius: 9,
            border: "1px solid var(--border)",
            background: "var(--panel2)",
            color: "var(--text)",
            fontSize: 12.5,
            fontWeight: 600,
            cursor: "pointer",
          }}
          title="Deletes the stored cost data and returns this contract to billing-only"
        >
          Remove all cost data
        </button>
        <span style={{ fontSize: 12, color: "var(--dim)" }}>
          Level {level} while editing · saved as Level {activeLevel}
        </span>
        {saved && <span style={{ fontSize: 12, color: "var(--good)" }}>{saved}</span>}
      </div>

      {/* The rate schedule is what populates the negotiated column, so this is its
          permanent home — #64 wired it onto the alert banners, which is where you
          discover the gap, not where you'd go looking to fix it. */}
      <div style={{ ...panelStyle, padding: 16 }}>
        <div style={{ fontSize: 12.5, color: "var(--text)", marginBottom: 10 }}>
          Negotiated rates come off the award&apos;s rate schedule — often a continuation sheet rather than the
          form face. Import it here if the categories above look incomplete.
        </div>
        <ImportRateSchedule contractId={contractId} onImported={load} />
      </div>
    </div>
  );
}
