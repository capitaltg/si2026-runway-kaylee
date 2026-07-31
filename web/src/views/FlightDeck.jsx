import React, { useEffect, useRef, useState } from "react";
import { getBurn, getSources, syncTimesheets, listContracts, askRunway } from "../api.js";
import { money, moneyM, pct, pill, hueFor, statusColor, panelStyle } from "../format.js";
import BurnChart from "../components/BurnChart.jsx";
import { suggestFor } from "../suggest.js";

const grotesk = "'Space Grotesk',sans-serif";
const tileLabel = {
  fontSize: 11,
  letterSpacing: ".1em",
  textTransform: "uppercase",
  fontWeight: 700,
  color: "var(--faint)",
};
const tileNum = { fontFamily: grotesk, fontWeight: 700, fontSize: 30, color: "var(--text)", marginTop: 8 };

// Suggestion action buttons, matched to the design (Runway.dc.html): a primary
// accent button and a secondary "Open simulator".
const btnPrimary = {
  height: 34,
  padding: "0 16px",
  borderRadius: 9,
  border: "none",
  background: "var(--accent)",
  color: "#fff",
  fontSize: 12.5,
  fontWeight: 600,
  cursor: "pointer",
  boxShadow: "0 4px 12px rgba(67,97,238,.28)",
};
const btnSecondary = {
  height: 34,
  padding: "0 14px",
  borderRadius: 9,
  border: "1px solid var(--border)",
  background: "var(--panel2)",
  color: "var(--text)",
  fontSize: 12.5,
  fontWeight: 600,
  cursor: "pointer",
};

// "Runway suggests" strip that hangs under an alert card. Renders the
// deterministic heuristic copy immediately; when AI is on it streams a phrased
// version over the top of it, and silently keeps the heuristic text if the
// stream fails (Bedrock down, etc.). The action button routes via onAction.
function Suggestion({ kind, item, contract, aiEnabled, contractId, onAction }) {
  const heuristic = suggestFor(kind, item, contract);
  const [body, setBody] = useState(heuristic.body);
  const [aiActive, setAiActive] = useState(false);

  useEffect(() => {
    // AI off: show the deterministic copy, nothing to fetch.
    if (!aiEnabled || !contractId) {
      setBody(heuristic.body);
      setAiActive(false);
      return;
    }
    let cancelled = false;
    let streamed = "";
    setBody(heuristic.body); // instant fallback while the model warms up
    setAiActive(true);
    const q =
      `Advise the PM on CLIN ${item.code} (${item.name}). In 1–2 short, directive ` +
      `sentences, recommend the concrete next action. Grounding: ${heuristic.body} ` +
      `Phrase it as advice — don't repeat the numbers as a list.`;
    askRunway({ question: q, history: [], contractId }, (chunk) => {
      if (cancelled) return;
      streamed += chunk;
      setBody(streamed);
    })
      .catch(() => {
        if (!cancelled && !streamed) setBody(heuristic.body);
      })
      .finally(() => {
        if (!cancelled) setAiActive(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aiEnabled, kind, item.code, contractId]);

  return (
    <div
      style={{
        marginTop: 14,
        background: "var(--panel)",
        border: "1px solid var(--border)",
        borderRadius: 14,
        padding: "14px 16px",
        boxShadow: "0 1px 2px rgba(26,34,51,.04),0 6px 18px rgba(26,34,51,.05)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <span style={{ display: "flex", color: "var(--accent)" }}>
          <svg
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.9"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M9 18h6M10 22h4M12 2a7 7 0 00-4 12.7c.6.5 1 1.3 1 2.1h6c0-.8.4-1.6 1-2.1A7 7 0 0012 2z" />
          </svg>
        </span>
        <span
          style={{
            fontFamily: grotesk,
            fontWeight: 700,
            fontSize: 11.5,
            letterSpacing: ".06em",
            textTransform: "uppercase",
            color: "var(--accent)",
          }}
        >
          Runway suggests
        </span>
        {aiEnabled && (
          <span style={{ fontSize: 10.5, color: "var(--faint)", marginLeft: "auto" }}>
            {aiActive ? "✨ thinking…" : "✨ AI"}
          </span>
        )}
      </div>
      <div style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.55 }}>{body}</div>
      {heuristic.action && (
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12, flexWrap: "wrap" }}>
          {heuristic.result && (
            <span style={{ fontSize: 12.5, color: "var(--good)", fontWeight: 600 }}>
              {heuristic.result}
            </span>
          )}
          <div style={{ display: "flex", gap: 8, marginLeft: "auto" }}>
            {heuristic.action.kind === "balance" ? (
              <button onClick={() => onAction("simulator")} style={btnSecondary}>
                Open simulator
              </button>
            ) : (
              <button onClick={() => onAction("funding")} style={btnPrimary}>
                Open funding history
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function FlightDeck({
  contractId,
  setActiveId,
  onOpenExpenses,
  onOpenAllocation,
  onOpenFunding,
  onRename,
  aiEnabled,
}) {
  const [burn, setBurn] = useState(null);
  const [sources, setSources] = useState([]);
  const [selected, setSelected] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState(null);
  // Inline contract rename (nickname) right on the title.
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const cancelRename = useRef(false);
  // Contracts we've already auto-synced once, so we don't re-sync on every load.
  const autoSyncedRef = useRef(new Set());

  // Resolve a contract to show: the one App picked, else the newest ingested.
  useEffect(() => {
    if (contractId) return;
    listContracts()
      .then((cs) => cs.length && setActiveId(cs[0].id))
      .catch((e) => setError(e.message));
  }, [contractId, setActiveId]);

  async function load(id) {
    try {
      const b = await getBurn(id);
      setBurn(b);
      const labor = b.clins.filter((c) => c.is_labor);
      setSelected((s) => (labor.some((c) => c.id === s) ? s : labor[0]?.id ?? null));
      // First visit with no hours synced yet: pull them once automatically so
      // the Flight Deck shows real burn instead of an empty "sync timesheets".
      if ((b?.sync?.rows ?? 0) === 0 && !autoSyncedRef.current.has(id)) {
        autoSyncedRef.current.add(id);
        onSync();
      }
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    if (contractId) load(contractId);
  }, [contractId]);

  useEffect(() => {
    getSources()
      .then((d) => setSources(d.sources || []))
      .catch(() => setSources([]));
  }, []);

  async function onSync() {
    if (!contractId) return;
    setSyncing(true);
    setError(null);
    try {
      await syncTimesheets(contractId);
      await load(contractId);
    } catch (e) {
      setError(e.message);
    } finally {
      setSyncing(false);
    }
  }

  if (error) {
    return (
      <div style={{ padding: "40px", color: "var(--bad)" }}>
        Couldn't load burn data: {error}
      </div>
    );
  }
  if (!burn) {
    return <div style={{ padding: "40px", color: "var(--dim)" }}>Loading flight deck…</div>;
  }

  const { contract, totals, hero, tripwires, underburn = [], funding = [], all_clear, sync } = burn;
  const labor = burn.clins.filter((c) => c.is_labor);
  const selectedClin = labor.find((c) => c.id === selected) || labor[0];
  const heroColor = statusColor(hero?.status);
  const heroColor2 =
    hero?.status === "over"
      ? "#c23636"
      : hero?.status === "watch" || hero?.status === "funding"
        ? "#c26e12"
        : "#0b8f65";
  // Live-data strip shows only sources actually feeding this project.
  const liveSources = sources.filter((s) => s.status === "live" || s.status === "synced");
  const stripSources = liveSources.length ? liveSources : sources;
  const heroSub =
    hero?.status === "over"
      ? hero?.limited_by === "funding"
        ? "runs out of funded dollars before the PoP ends"
        : "blows the ceiling before the PoP ends"
      : hero?.status === "funding"
        ? "needs its next funding mod before the PoP ends"
        : hero?.status === "watch"
          ? "lands tight against the finish line"
          : "clears the finish line";

  function startRename() {
    cancelRename.current = false;
    setNameDraft(contract.nickname || "");
    setRenaming(true);
  }
  async function commitRename() {
    setRenaming(false);
    if (cancelRename.current) {
      cancelRename.current = false;
      return;
    }
    const next = nameDraft.trim();
    if (next !== (contract.nickname || "") && onRename) {
      await onRename(next);
      load(contractId);
    }
  }

  // Route a suggestion's action button: "simulator" opens the Allocation Matrix,
  // "funding" opens the Funding History.
  function onSuggestAction(kind) {
    if (kind === "simulator") onOpenAllocation?.();
    else if (kind === "funding") onOpenFunding?.();
  }

  return (
    <div style={{ padding: "24px 26px 60px", maxWidth: 1280 }}>
      {/* header — title doubles as a rename field (nickname); official name below */}
      <div style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {renaming ? (
            <input
              autoFocus
              value={nameDraft}
              placeholder="Name it (e.g. FALCON)"
              onChange={(e) => setNameDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
                else if (e.key === "Escape") {
                  cancelRename.current = true;
                  e.currentTarget.blur();
                }
              }}
              onBlur={commitRename}
              style={{
                fontFamily: grotesk,
                fontSize: 22,
                fontWeight: 600,
                color: "var(--text)",
                background: "var(--inputBg)",
                border: "1px solid var(--accent)",
                borderRadius: 8,
                padding: "2px 10px",
                minWidth: 260,
              }}
            />
          ) : (
            <>
              <h2 style={{ margin: 0, fontFamily: grotesk, fontSize: 22, fontWeight: 600, color: "var(--text)" }}>
                {contract.name || contract.piid}
              </h2>
              <button
                onClick={startRename}
                title="Rename this contract (give it a callsign)"
                style={{
                  border: "none",
                  background: "transparent",
                  color: "var(--accent)",
                  cursor: "pointer",
                  fontSize: 15,
                  lineHeight: 1,
                  padding: 2,
                }}
              >
                ✎
              </button>
            </>
          )}
        </div>
        <div style={{ fontSize: 13.5, color: "var(--dim)", marginTop: 5 }}>
          {contract.nickname && contract.legal_name ? `${contract.legal_name} · ` : ""}
          {contract.agency || "—"} · {contract.piid}
        </div>
      </div>

      {/* tripwires (real numbers only) */}
      {tripwires.map((tw) => (
        <div
          key={tw.code}
          style={{
            border: "1px solid var(--bad)",
            background: "var(--badBg)",
            borderRadius: 16,
            padding: "16px 18px",
            marginBottom: 16,
            display: "flex",
            gap: 14,
          }}
        >
          <div
            style={{
              width: 38,
              height: 38,
              flex: "0 0 38px",
              borderRadius: 11,
              background: "var(--bad)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#fff",
              fontWeight: 700,
            }}
          >
            !
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
              <span style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 15, color: "var(--bad)" }}>
                Tripwire — {tw.code} {tw.name}
              </span>
              <span
                style={{
                  fontSize: 11,
                  fontFamily: "'IBM Plex Mono',monospace",
                  background: "var(--bad)",
                  color: "#fff",
                  padding: "2px 8px",
                  borderRadius: 20,
                }}
              >
                {pct(tw.pct)} burned
              </span>
            </div>
            <div style={{ fontSize: 13.5, color: "var(--text)", marginTop: 6, lineHeight: 1.5 }}>
              At the current burn rate, {tw.code}{" "}
              {tw.limited_by === "funding" ? (
                <>
                  exhausts its <b>funded</b> {moneyM(tw.funded)} in week{" "}
                  {Math.round(tw.exhaust_week)}
                </>
              ) : (
                <>blows its {moneyM(tw.budget)} ceiling in week {Math.round(tw.exhaust_week)}</>
              )}{" "}
              — {tw.weeks_early} weeks before the PoP ends. Only {tw.runway_days} days of runway
              remain{tw.limited_by === "funding" ? " unless more funding is obligated" : ""}.
            </div>
            <Suggestion
              kind="over"
              item={tw}
              contract={contract}
              aiEnabled={aiEnabled}
              contractId={contractId}
              onAction={onSuggestAction}
            />
          </div>
        </div>
      ))}

      {/* under-burn warnings — amber/info, distinct from the red over-ceiling tripwire */}
      {underburn.map((ub) => (
        <div
          key={ub.code}
          style={{
            border: "1px solid var(--warn)",
            background: "var(--warnBg)",
            borderRadius: 16,
            padding: "16px 18px",
            marginBottom: 16,
            display: "flex",
            gap: 14,
          }}
        >
          <div
            style={{
              width: 38,
              height: 38,
              flex: "0 0 38px",
              borderRadius: 11,
              background: "var(--warn)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#fff",
              fontWeight: 700,
            }}
          >
            ↓
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
              <span style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 15, color: "var(--warn)" }}>
                Under-burning — {ub.code} {ub.name}
              </span>
              <span
                style={{
                  fontSize: 11,
                  fontFamily: "'IBM Plex Mono',monospace",
                  background: "var(--warn)",
                  color: "#fff",
                  padding: "2px 8px",
                  borderRadius: 20,
                }}
              >
                {pct(ub.pct)} burned
              </span>
            </div>
            <div style={{ fontSize: 13.5, color: "var(--text)", marginTop: 6, lineHeight: 1.5 }}>
              At the current burn rate, {ub.code} is projected to under-spend its{" "}
              {ub.limited_by === "funding" ? "funded" : "budgeted"} {moneyM(ub.budget)} by{" "}
              <b>{moneyM(ub.projected_unspent)}</b> — not consuming it until ~{ub.weeks_slack} weeks
              after the PoP ends. Under-staffing or slipping delivery can leave money unspent and
              jeopardize option-year exercise.
            </div>
            <Suggestion
              kind="underburn"
              item={ub}
              contract={contract}
              aiEnabled={aiEnabled}
              contractId={contractId}
              onAction={onSuggestAction}
            />
          </div>
        </div>
      ))}

      {/* funding-pace watch — amber, routine incremental funding awaiting its
          next obligation; deliberately not the red over-ceiling tripwire (#22) */}
      {funding.map((fw) => (
        <div
          key={fw.code}
          style={{
            border: "1px solid var(--warn)",
            background: "var(--warnBg)",
            borderRadius: 16,
            padding: "16px 18px",
            marginBottom: 16,
            display: "flex",
            gap: 14,
          }}
        >
          <div
            style={{
              width: 38,
              height: 38,
              flex: "0 0 38px",
              borderRadius: 11,
              background: "var(--warn)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#fff",
              fontWeight: 700,
            }}
          >
            $
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
              <span style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 15, color: "var(--warn)" }}>
                {fw.mod_in_progress ? "Funding request outstanding" : "Funding due"} — {fw.code}{" "}
                {fw.name}
              </span>
              <span
                style={{
                  fontSize: 11,
                  fontFamily: "'IBM Plex Mono',monospace",
                  background: "var(--warn)",
                  color: "#fff",
                  padding: "2px 8px",
                  borderRadius: 20,
                }}
              >
                {pct(fw.funded_frac)} funded
              </span>
            </div>
            <div style={{ fontSize: 13.5, color: "var(--text)", marginTop: 6, lineHeight: 1.5 }}>
              At the current burn rate, {fw.code} spends through its funded {moneyM(fw.funded)} in
              week {Math.round(fw.exhaust_week)} — {fw.weeks_early} weeks before the PoP ends. This is
              routine incremental funding ({pct(fw.funded_frac)} obligated at {pct(fw.elapsed_frac)}{" "}
              of the PoP elapsed), so it needs its next funding mod, not a course correction.
              {fw.mod_in_progress ? " A funding modification is already outstanding." : ""}
            </div>
            <Suggestion
              kind="funding"
              item={fw}
              contract={contract}
              aiEnabled={aiEnabled}
              contractId={contractId}
              onAction={onSuggestAction}
            />
          </div>
        </div>
      ))}

      {all_clear && (
        <div
          style={{
            border: "1px solid var(--good)",
            background: "var(--goodBg)",
            borderRadius: 16,
            padding: "14px 18px",
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <div>
            <div style={{ fontWeight: 700, fontSize: 14, color: "var(--good)", fontFamily: grotesk }}>
              All CLINs clear the ceiling
            </div>
            <div style={{ fontSize: 12.5, color: "var(--text)", marginTop: 2 }}>
              Every funding line is projected to land under budget through the period of performance.
            </div>
          </div>
        </div>
      )}

      {/* live data strip */}
      <div
        style={{
          ...panelStyle,
          padding: "12px 16px",
          marginBottom: 16,
          display: "flex",
          alignItems: "center",
          gap: 14,
          flexWrap: "wrap",
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text)" }}>Live data</span>
        <div style={{ width: 1, height: 26, background: "var(--border)" }} />
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", flex: 1 }}>
          {stripSources.map((ig) => {
            const on = ig.status === "live" || ig.status === "synced";
            return (
              <div
                key={ig.code}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 10px",
                  borderRadius: 10,
                  background: "var(--panel2)",
                }}
              >
                <div
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: 7,
                    background: ig.hue,
                    color: "#fff",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontFamily: "'IBM Plex Mono',monospace",
                    fontWeight: 600,
                    fontSize: 10,
                  }}
                >
                  {ig.code}
                </div>
                <div style={{ lineHeight: 1.2 }}>
                  <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text)" }}>{ig.name}</div>
                  <div style={{ fontSize: 10, color: "var(--dim)" }}>{ig.kind}</div>
                </div>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: on ? "var(--good)" : "var(--faint)",
                  }}
                />
              </div>
            );
          })}
        </div>
        <button
          onClick={onSync}
          disabled={syncing}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            height: 32,
            padding: "0 12px",
            borderRadius: 9,
            border: "1px solid var(--border)",
            background: "var(--panel)",
            color: "var(--dim)",
            fontSize: 11.5,
            fontWeight: 600,
            cursor: syncing ? "default" : "pointer",
          }}
        >
          {syncing ? "Syncing…" : "Sync now"}
        </button>
      </div>

      {/* stat row */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.15fr 1fr 1fr 1fr",
          gap: 14,
          marginBottom: 16,
        }}
      >
        <div
          style={{
            borderRadius: 16,
            padding: 18,
            background: `linear-gradient(155deg, ${heroColor}, ${heroColor2})`,
            color: "#fff",
            boxShadow: "0 12px 28px rgba(0,0,0,.14)",
            position: "relative",
            overflow: "hidden",
          }}
        >
          <div style={{ position: "absolute", right: -18, top: -18, opacity: 0.16 }}>
            <svg width="130" height="130" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1">
              <circle cx="12" cy="12" r="10" />
              <path d="M12 2v20M2 12h20M12 12l6-4" />
            </svg>
          </div>
          <div style={{ fontSize: 11, letterSpacing: ".12em", textTransform: "uppercase", fontWeight: 700, opacity: 0.9 }}>
            Days of runway
          </div>
          <div style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 52, lineHeight: 1, marginTop: 8 }}>
            {hero ? hero.days : "—"}
          </div>
          <div style={{ fontSize: 12.5, opacity: 0.92, marginTop: 8, lineHeight: 1.4 }}>
            {hero ? `Limited by ${hero.clin} · ${heroSub}` : "No burn logged yet — sync timesheets"}
          </div>
        </div>
        <div style={panelStyle}>
          <div style={tileLabel}>Contract burned</div>
          <div style={tileNum}>{pct(totals.pct)}</div>
          <div style={{ fontSize: 12, color: "var(--dim)", marginTop: 6 }}>
            {moneyM(totals.spent)} of {moneyM(totals.ceiling)}
          </div>
        </div>
        <div style={panelStyle}>
          <div style={tileLabel}>Time elapsed</div>
          <div style={tileNum}>
            wk {contract.current_week}
            <span style={{ fontSize: 16, color: "var(--dim)" }}>/{contract.total_weeks}</span>
          </div>
          <div style={{ fontSize: 12, color: "var(--dim)", marginTop: 6 }}>
            {contract.weeks_remaining} weeks remaining
          </div>
        </div>
        <div style={panelStyle}>
          <div style={tileLabel}>Weekly burn</div>
          <div style={tileNum}>{money(totals.weekly)}</div>
          <div style={{ fontSize: 12, color: "var(--dim)", marginTop: 6 }}>
            across {totals.labor_count} labor lines
          </div>
        </div>
      </div>

      {/* burn chart */}
      {selectedClin && (
        <div style={{ ...panelStyle, marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
            <div>
              <div style={{ fontFamily: grotesk, fontWeight: 600, fontSize: 17, color: "var(--text)" }}>
                Burn vs. pace — {selectedClin.code}
              </div>
              <div style={{ fontSize: 12.5, color: "var(--dim)", marginTop: 2 }}>
                {selectedClin.name} · updates live as timesheets sync
              </div>
            </div>
            <div style={{ display: "flex", gap: 16, fontSize: 11.5, color: "var(--dim)" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                <span style={{ width: 16, height: 0, borderTop: "2px dashed var(--faint)" }} />
                {selectedClin.incrementally_funded ? "Pace to stay funded" : "Target pace"}
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                <span style={{ width: 16, height: 3, borderRadius: 2, background: "var(--accent)" }} />
                Actual
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                <span
                  style={{
                    width: 16,
                    height: 3,
                    borderRadius: 2,
                    background: statusColor(selectedClin.status),
                  }}
                />
                Projected
              </span>
            </div>
          </div>
          <BurnChart clin={selectedClin} contract={contract} />
          <div
            style={{
              display: "flex",
              gap: 8,
              flexWrap: "wrap",
              marginTop: 6,
              borderTop: "1px solid var(--border)",
              paddingTop: 14,
            }}
          >
            {labor.map((c, i) => {
              const on = c.id === selectedClin.id;
              return (
                <button
                  key={c.id}
                  onClick={() => setSelected(c.id)}
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    padding: "7px 13px",
                    borderRadius: 9,
                    cursor: "pointer",
                    border: `1px solid ${on ? hueFor(i) : "var(--border)"}`,
                    background: on ? "var(--panel2)" : "transparent",
                    color: on ? hueFor(i) : "var(--dim)",
                  }}
                >
                  {c.code}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* CLIN health cards */}
      <div style={{ fontFamily: grotesk, fontWeight: 600, fontSize: 15, color: "var(--text)", margin: "4px 0 12px" }}>
        CLIN health
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(280px,1fr))", gap: 14 }}>
        {burn.clins.map((c, i) => {
          const hue = hueFor(i);
          const p = pill(c.status);
          const barColor = c.pct > 0.85 ? "var(--bad)" : c.pct > 0.7 ? "var(--warn)" : hue;
          // Non-labor CLINs have no timesheet burn — their card routes into the
          // expense log to add/see actuals; labor cards select the burn chart.
          const runwayLabel = !c.is_labor
            ? "log actuals →"
            : c.status === "paused"
              ? "no burn"
              : `${c.runway_days}d runway`;
          const runwayColor = !c.is_labor
            ? "var(--accent)"
            : statusColor(c.status);
          const sel = c.is_labor && selectedClin && c.id === selectedClin.id;
          const onCardClick = c.is_labor
            ? () => setSelected(c.id)
            : () => onOpenExpenses?.(c.id);
          return (
            <div
              key={c.id + c.code}
              onClick={onCardClick}
              style={{
                border: `1px solid ${sel ? hue : "var(--border)"}`,
                borderRadius: 14,
                padding: "13px 14px",
                background: "var(--panel)",
                cursor: "pointer",
                boxShadow: sel ? `0 0 0 3px ${hue}22` : "none",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ width: 9, height: 9, borderRadius: 3, background: hue }} />
                <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 11.5, fontWeight: 600, color: "var(--dim)" }}>
                  {c.code}
                </span>
                <span
                  style={{
                    fontSize: 12.5,
                    fontWeight: 600,
                    color: "var(--text)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {c.name}
                </span>
                <span style={p.style}>{p.label}</span>
              </div>
              <div
                style={{
                  height: 8,
                  borderRadius: 5,
                  background: "var(--border)",
                  marginTop: 11,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    height: "100%",
                    width: `${Math.min(100, Math.round(c.pct * 100))}%`,
                    background: barColor,
                    borderRadius: 5,
                  }}
                />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 11.5, color: "var(--dim)" }}>
                <span>
                  {moneyM(c.spent)} / {moneyM(c.ceiling)} ·{" "}
                  <b style={{ color: "var(--text)" }}>{pct(c.pct)}</b>
                </span>
                <span style={{ color: runwayColor, fontWeight: 600 }}>{runwayLabel}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
