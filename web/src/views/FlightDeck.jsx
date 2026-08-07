import React, { useEffect, useRef, useState } from "react";
import { getBurn, getSources, syncTimesheets, listContracts, askRunway } from "../api.js";
import { money, moneyM, pct, pill, hueFor, statusColor, panelStyle, shortDate, stopPhrase } from "../format.js";
import BurnChart from "../components/BurnChart.jsx";
import ImportRateSchedule from "../components/ImportRateSchedule.jsx";
import AlertCarouselCard from "../components/AlertCarouselCard.jsx";
import ContractSource from "../components/ContractSource.jsx";
import { TrashButton, DeleteConfirm } from "../components/DeleteContract.jsx";
import { suggestFor } from "../suggest.js";
import { scopeNotices } from "../scope-notice.js";
import { clampAlertIndex, nextAlertIndex, orderedFlightDeckAlerts } from "../flight-deck-alerts.js";

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
function Suggestion({ kind, item, contract, aiEnabled, contractId, onAction, onOpenDrafts }) {
  const heuristic = suggestFor(kind, item, contract);
  const urgent = heuristic.action && heuristic.action.urgent;
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
        {urgent && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: ".04em",
              color: "#fff",
              background: "var(--warn)",
              padding: "2px 8px",
              borderRadius: 20,
              whiteSpace: "nowrap",
            }}
          >
            ⚠ 30-DAY FUNDING DEADLINE
          </span>
        )}
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
              <>
                <button onClick={() => onAction("simulator")} style={btnSecondary}>
                  Open simulator
                </button>
                <button onClick={() => onAction("apply-fix")} style={btnPrimary}>
                  Apply fix
                </button>
              </>
            ) : (
              <>
                <button onClick={() => onAction("funding")} style={btnSecondary}>
                  Open funding history
                </button>
                <button
                  onClick={() => onOpenDrafts?.(contractId, "funding")}
                  style={btnPrimary}
                >
                  Draft funding request →
                </button>
              </>
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
  onApplyFix,
  onOpenFunding,
  onOpenDrafts,
  onRename,
  onDeleted,
  aiEnabled,
}) {
  // Staged for the delete confirm — the Flight Deck is where a bad extraction
  // actually gets noticed, so the fix lives here too rather than only back in
  // the portfolio.
  const [pendingDelete, setPendingDelete] = useState(false);
  const [burn, setBurn] = useState(null);
  const [sources, setSources] = useState([]);
  const [selected, setSelected] = useState(null);
  const [alertIndex, setAlertIndex] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState(null);
  // Inline contract rename (nickname) right on the title.
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const cancelRename = useRef(false);
  // Contracts we've already auto-synced once, so we don't re-sync on every load.
  const autoSyncedRef = useRef(new Set());
  // Bumped when an upload adds a source document, so the source panel picks up the
  // schedule that was just imported instead of waiting for a page reload (#30).
  const [sourceRefresh, setSourceRefresh] = useState(0);

  function onRatesImported() {
    load(contractId);
    setSourceRefresh((n) => n + 1);
  }

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
      setAlertIndex(0);
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

  const {
    contract,
    totals,
    hero,
    tripwires,
    underburn = [],
    funding = [],
    // Fixed-price margin erosion (#79) — the fixed-price counterpart to `tripwires`.
    // Its own list because these rows have no dates, no runway and no funding limit to
    // describe, so nothing that renders a tripwire can render one of these.
    margin_alerts = [],
    data_quality = [],
    // Rate-line coverage (#64), split by what fixes it: `rate_gaps` needs a
    // document, `lcat_gaps` needs a decision in the allocation matrix.
    rate_gaps = [],
    lcat_gaps = [],
    all_clear,
    sync,
  } = burn;
  const labor = burn.clins.filter((c) => c.is_labor);
  const selectedClin = labor.find((c) => c.id === selected) || labor[0];
  // An all-fixed-price contract has no runway anywhere, so the engine sends no hero
  // (#79) — and a "—" under "Days of runway" would read as missing data rather than as
  // a figure that doesn't apply. The tile becomes a margin hero instead: the tightest
  // projected margin across the fixed-price lines, which is the equivalent question.
  const marginClins = labor.filter((c) => c.margin_managed && c.margin_position);
  const marginOnly = !hero && marginClins.length > 0;
  const worstMargin = marginOnly
    ? marginClins.reduce((a, b) =>
        a.margin_position.projected_margin <= b.margin_position.projected_margin ? a : b,
      )
    : null;
  const heroColor = statusColor(hero?.status);
  const heroColor2 =
    hero?.status === "over" || hero?.status === "unpriced"
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
      : hero?.status === "unpriced"
        ? "has charges the engine can't price — burn is unknown, not clear"
        : hero?.status === "funding"
          ? "needs its next funding mod before the PoP ends"
          : hero?.status === "watch"
            ? "lands tight against the finish line"
            : "clears the finish line";
  const notices = scopeNotices(contract);
  const alerts = orderedFlightDeckAlerts({
    dataQuality: data_quality,
    tripwires,
    funding,
    underburn,
    marginAlerts: margin_alerts,
    notices,
    rateGaps: rate_gaps,
    lcatGaps: lcat_gaps,
  });
  const activeIndex = clampAlertIndex(alertIndex, alerts.length);
  const activeAlert = alerts[activeIndex];

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

  // Route a suggestion's action buttons: "simulator" opens the Allocation Matrix
  // untouched, "apply-fix" opens it with the rebalanced plan pre-applied, and
  // "funding" opens the Funding History.
  function onSuggestAction(kind) {
    if (kind === "simulator") onOpenAllocation?.();
    else if (kind === "apply-fix") onApplyFix?.();
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
        {/* The paperwork behind every number below it (#30). */}
        <ContractSource contractId={contractId} refreshKey={sourceRefresh} />
      </div>

      {activeAlert && (
        <AlertCarouselCard
          index={activeIndex}
          total={alerts.length}
          onPrevious={() => setAlertIndex(nextAlertIndex(activeIndex, alerts.length, -1))}
          onNext={() => setAlertIndex(nextAlertIndex(activeIndex, alerts.length, 1))}
        >
      {activeAlert.kind === "scope" && (
        <div
          style={{
            border: "1px solid var(--warn)",
            background: "var(--warnBg)",
            borderRadius: 12,
            padding: "12px 16px",
            marginBottom: 16,
            color: "var(--text)",
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          <b style={{ color: "var(--warn)" }}>Data scope needs review.</b>{" "}
          {activeAlert.item.map((notice, index) => (
            <React.Fragment key={notice.key}>
              {index > 0 && " "}
              {notice.text}
            </React.Fragment>
          ))}
        </div>
      )}

      {/* tripwires (real numbers only) */}
      {activeAlert.kind === "tripwire" && (() => {
        const tw = activeAlert.item;
        return (
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
                {/* Of whichever limit the sentence below is about (#39) —
                    otherwise the badge reads "40% burned" over a line that has
                    already blown through its obligated dollars. */}
                {tw.limited_by === "funding"
                  ? `${pct(tw.pct_budget)} of funded`
                  : `${pct(tw.pct)} of ceiling`}{" "}
                burned
              </span>
            </div>
            <div style={{ fontSize: 13.5, color: "var(--text)", marginTop: 6, lineHeight: 1.5 }}>
              {/* Non-labor CLINs land in this red list too (#41) but have no timesheet
                  pace, so `exhaust_week` / `runway_days` / `stop_date` are all null on
                  them. They need the realized wording: the forward branch below was
                  rendering "in week 0 — 52 weeks before the PoP ends. Only  days of
                  runway remain" for contract 6's CLIN 0004, because Math.round(null)
                  is 0 and React prints null as nothing. */}
              {tw.exhaust_week == null ? (
                <>
                  {tw.code} has already spent{" "}
                  {tw.limited_by === "funding" ? (
                    <>{pct(tw.pct_budget)} of its obligated funding</>
                  ) : (
                    <>{pct(tw.pct)} of its ceiling</>
                  )}
                  , past its{" "}
                  {tw.limited_by === "funding" ? (
                    <>
                      obligated <b>{moneyM(tw.funded)}</b>
                    </>
                  ) : (
                    <>
                      <b>{moneyM(tw.budget)}</b> ceiling
                    </>
                  )}
                  . That's a realized breach, not a forecast — the cost is at risk
                  today
                  {tw.limited_by === "funding" ? " until more funding is obligated" : ""}.
                </>
              ) : (
                <>
                  At the current burn rate, {tw.code}{" "}
                  {tw.limited_by === "funding" ? (
                    <>
                      exhausts its <b>funded</b> {moneyM(tw.funded)} in week{" "}
                      {Math.round(tw.exhaust_week)}
                    </>
                  ) : (
                    <>
                      blows its {moneyM(tw.budget)} ceiling in week{" "}
                      {Math.round(tw.exhaust_week)}
                    </>
                  )}{" "}
                  — {tw.weeks_early} weeks before the PoP ends.{" "}
                  {/* Hard-stop forecast (#23): the week index as a date a PM can act
                      against. Past dates get the present tense — naming a deadline
                      that has been and gone reads as though there were time left.

                      No date → the pre-#23 sentence, never a placeholder. A banner
                      whose whole point is naming a deadline must not render
                      "charging stops around —": that's worse than not mentioning it,
                      because it reads as a failed lookup of a date that exists. This
                      is reachable whenever the served payload is older than the
                      bundle — an API process without --reload keeps serving the old
                      shape while Vite has already hot-reloaded this file. */}
                  {!tw.stop_date ? (
                    <>
                      Only {tw.runway_days} days of runway remain
                      {tw.limited_by === "funding"
                        ? " unless more funding is obligated"
                        : ""}
                      .
                    </>
                  ) : tw.stop_date_passed ? (
                    <>
                      That money is already spent through — it ran out around{" "}
                      {shortDate(tw.stop_date)}, so charging should stop <b>today</b>
                      {tw.limited_by === "funding"
                        ? " until more funding is obligated"
                        : ""}
                      .
                    </>
                  ) : (
                    <>
                      Only {tw.runway_days} days of runway remain, so charging stops
                      around <b>{shortDate(tw.stop_date)}</b>
                      {tw.limited_by === "funding"
                        ? " unless more funding is obligated"
                        : ""}
                      .
                    </>
                  )}
                </>
              )}
            </div>
            <Suggestion
              kind="over"
              item={tw}
              contract={contract}
              aiEnabled={aiEnabled}
              contractId={contractId}
              onAction={onSuggestAction}
              onOpenDrafts={onOpenDrafts}
            />
          </div>
        </div>
        );
      })()}

      {/* under-burn warnings — amber/info, distinct from the red over-ceiling tripwire */}
      {activeAlert.kind === "underburn" && (() => {
        const ub = activeAlert.item;
        return (
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
                {/* The sentence below is about the budget, so this is too (#39). */}
                {pct(ub.pct_budget)} of{" "}
                {ub.limited_by === "funding" ? "funded" : "ceiling"} burned
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
              onOpenDrafts={onOpenDrafts}
            />
          </div>
        </div>
        );
      })()}

      {/* fixed-price margin erosion (#79) — cost projected to eat the fee. Reads in
          margin language throughout: no dates, no runway, no "funding runs out",
          because a firm price is owed however the hours land. */}
      {activeAlert.kind === "margin" && (() => {
        const ma = activeAlert.item;
        const red = ma.status === "over";
        const tone = red ? "var(--bad)" : "var(--warn)";
        return (
          <div
            key={ma.code}
            style={{
              border: `1px solid ${tone}`,
              background: red ? "var(--badBg)" : "var(--warnBg)",
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
                background: tone,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "#fff",
                fontWeight: 700,
              }}
            >
              %
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
                <span style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 15, color: tone }}>
                  {red ? "Margin exceeded" : "Margin at risk"} — {ma.code} {ma.name}
                </span>
                <span
                  style={{
                    fontSize: 11,
                    fontFamily: "'IBM Plex Mono',monospace",
                    background: tone,
                    color: "#fff",
                    padding: "2px 8px",
                    borderRadius: 20,
                  }}
                >
                  {ma.policy}
                </span>
              </div>
              <div style={{ fontSize: 13.5, color: "var(--text)", marginTop: 6, lineHeight: 1.5 }}>
                At the current pace, cost on {ma.code} reaches{" "}
                <b>{moneyM(ma.projected_cost)}</b> by the end of the PoP against a{" "}
                {moneyM(ma.price)} firm price —{" "}
                {red ? (
                  <>
                    <b>{moneyM(-ma.projected_margin)} past it</b>. The government owes the
                    price either way, so this is margin the company absorbs, not funding
                    that runs out.
                  </>
                ) : (
                  <>
                    leaving <b>{moneyM(ma.projected_margin)}</b> of margin. Nothing stops
                    charging here; what's at risk is the fee.
                  </>
                )}
                {!ma.known && (
                  <>
                    {" "}
                    Cost is standing in at the billing rate, so treat this as a shape, not
                    a number — import direct rates to make it real.
                  </>
                )}
              </div>
            </div>
          </div>
        );
      })()}

      {/* funding-pace watch — amber, routine incremental funding awaiting its
          next obligation; deliberately not the red over-ceiling tripwire (#22) */}
      {activeAlert.kind === "funding" && (() => {
        const fw = activeAlert.item;
        return (
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
              week {Math.round(fw.exhaust_week)}
              {/* Hard-stop forecast (#23) — the week index as the date a PM can act
                  against. Null on the non-labor rows that share this amber list. */}
              {fw.stop_date ? <> (around <b>{shortDate(fw.stop_date)}</b>)</> : null} —{" "}
              {fw.weeks_early} weeks before the PoP ends. This is
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
              onOpenDrafts={onOpenDrafts}
            />
          </div>
        </div>
        );
      })()}

      {/* data-quality gaps (#40) — CLINs with charged rows the engine could not
          price. The most dangerous state: without this the contract reads "All
          clear" because $0 priced looks like $0 spent. Rendered above all_clear,
          which is now gated off when any of these exist. */}
      {activeAlert.kind === "data-quality" && (() => {
        const dq = activeAlert.item;
        return (
        <div
          key={dq.code}
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
            ?
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
              <span style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 15, color: "var(--bad)" }}>
                Can't price {dq.code} {dq.name}
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
                {dq.charged_rows} rows · $0 priced
              </span>
            </div>
            <div style={{ fontSize: 13.5, color: "var(--text)", marginTop: 6, lineHeight: 1.5 }}>
              {dq.code} has {dq.charged_rows} charged timesheet{dq.charged_rows === 1 ? "" : "s"} but no
              labor rate to value them — so its burn reads $0 and would otherwise show as clear. This is a
              data gap, not a clean line.
              {dq.unmatched_lcats.length > 0 && (
                <>
                  {" "}Unpriced labor categor{dq.unmatched_lcats.length === 1 ? "y" : "ies"}:{" "}
                  <b>{dq.unmatched_lcats.join(", ")}</b>.
                </>
              )}
              {" "}Import a labor-rate schedule for this contract (supplemental rates) so the engine can
              price these hours.
            </div>
            {/* The instruction used to end there, with nothing to click (#64). */}
            <div style={{ marginTop: 10 }}>
              <ImportRateSchedule
                contractId={contractId}
                tone="var(--bad)"
                onImported={onRatesImported}
              />
            </div>
          </div>
        </div>
        );
      })()}

      {/* Rate coverage (#64) — cause A, stated once per CLIN. These CLINs *are*
          priced (blended = ceiling / est_hours, real contract arithmetic), so this
          is amber and does not gate all_clear: it says why nothing here is
          per-LCAT, and offers the document that fixes it. The old behaviour was a
          red ⚠ on every person charging the CLIN, for one missing PDF page. */}
      {activeAlert.kind === "rate-gap" && (() => {
        const g = activeAlert.item;
        return (
        <div
          key={g.code}
          style={{
            border: "1px solid var(--warn)",
            background: "var(--warnBg)",
            borderRadius: 16,
            padding: "14px 18px",
            marginBottom: 16,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
            <span style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 14.5, color: "var(--warn)" }}>
              {g.code} has no rate table
            </span>
            <span style={{ fontSize: 11.5, color: "var(--dim)" }}>{g.name}</span>
          </div>
          <div style={{ fontSize: 13, color: "var(--text)", marginTop: 6, lineHeight: 1.5 }}>
            Every labor category on this CLIN prices at the blended{" "}
            <b>{g.blended_rate ? money(g.blended_rate) : "—"}/hr</b> (its ceiling ÷ estimated hours), because
            the award we ingested carries a CLIN summary but no rate schedule.{" "}
            {g.lcats.length > 0 && (
              <>
                {g.lcats.length} categor{g.lcats.length === 1 ? "y" : "ies"} affected:{" "}
                <b>{g.lcats.slice(0, 4).join(", ")}</b>
                {g.lcats.length > 4 ? ` +${g.lcats.length - 4} more` : ""}.{" "}
              </>
            )}
            The burn figures are real, but nothing on this CLIN is per-person until the schedule lands.
          </div>
          <div style={{ marginTop: 10 }}>
            <ImportRateSchedule contractId={contractId} onImported={onRatesImported} />
          </div>
        </div>
        );
      })()}

      {/* Causes B and C (#64) — LCATs on CLINs that DO have a rate table, which no
          document fixes: someone has to decide which rate line they belong to.
          One line here, with the count and the way in, rather than making the user
          open the matrix to discover there's anything to do. */}
      {activeAlert.kind === "lcat-gap" && (() => {
        const gaps = activeAlert.item;
        return (
        <div
          style={{
            ...panelStyle,
            padding: "12px 16px",
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            gap: 12,
            flexWrap: "wrap",
          }}
        >
          <span style={{ fontSize: 13, color: "var(--text)" }}>
            <b>
              {gaps.reduce((n, g) => n + g.issues.length, 0)} labor categor
              {gaps.reduce((n, g) => n + g.issues.length, 0) === 1 ? "y" : "ies"}
            </b>{" "}
            charged on {gaps.map((g) => g.code).join(", ")} don&apos;t match a rate line, so their hours
            bill at the blended rate. Each one needs pointing at the line it belongs to.
          </span>
          <button
            type="button"
            onClick={() => onOpenAllocation?.()}
            style={{
              height: 30,
              padding: "0 13px",
              borderRadius: 8,
              border: "1px solid var(--border)",
              background: "var(--panel2)",
              color: "var(--text)",
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Map labor categories →
          </button>
        </div>
        );
      })()}
        </AlertCarouselCard>
      )}

      {!activeAlert && all_clear && (
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
            {marginOnly ? "Projected margin" : "Days of runway"}
          </div>
          <div style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 52, lineHeight: 1, marginTop: 8 }}>
            {marginOnly
              ? worstMargin.margin_position.known
                ? pct(worstMargin.margin_position.projected_margin_pct)
                : "—"
              : hero && hero.days != null
                ? hero.days
                : "—"}
          </div>
          <div style={{ fontSize: 12.5, opacity: 0.92, marginTop: 8, lineHeight: 1.4 }}>
            {marginOnly
              ? worstMargin.margin_position.known
                ? `Tightest on ${worstMargin.code} · fixed price, so cost against the price is the constraint — not funding`
                : `Fixed price throughout — margin needs direct rates before it can be read`
              : hero
                ? `Limited by ${hero.clin} · ${heroSub}`
                : "No burn logged yet — sync timesheets"}
          </div>
        </div>
        <div style={panelStyle}>
          {/* Named denominators (#39). The headline is spend against the *binding*
              budget, because this tile sits beside a runway measured the same way —
              a 40%-of-ceiling headline next to "89 days" is the reconciliation
              failure the ticket is about. The ceiling read stays underneath, and on
              a fully funded contract the two are the same number. */}
          <div style={tileLabel}>Contract burned</div>
          <div style={tileNum}>
            {pct(totals.incrementally_funded ? totals.pct_budget : totals.pct)}
          </div>
          <div style={{ fontSize: 12, color: "var(--dim)", marginTop: 6 }}>
            {totals.incrementally_funded ? (
              <>
                {moneyM(totals.spent)} of {moneyM(totals.budget)} funded
              </>
            ) : (
              <>
                {moneyM(totals.spent)} of {moneyM(totals.ceiling)} ceiling
              </>
            )}
          </div>
          {totals.incrementally_funded && (
            <div style={{ fontSize: 12, color: "var(--faint)", marginTop: 2 }}>
              {pct(totals.pct)} of the {moneyM(totals.ceiling)} ceiling
            </div>
          )}
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
            {/* The legend lives under the chart now — it carries each line's
                actual numbers, so duplicating the bare labels here read as two
                competing keys for one chart. */}
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
          // Fixed-price lines are margin-managed (#79): no runway, no hard-stop date,
          // and a cost-vs-price position in their place. A different card, not the same
          // card with odd numbers in it — the figures below are a different question.
          const mm = !!c.margin_managed;
          const pos = c.margin_position;
          const p = pill(c.status, c.ceiling_breached !== false, !!c.funds_exceeded, mm);
          // Colour off the *binding* budget, not the ceiling (#39). A CLIN at 40% of
          // its ceiling but 89% of its funded slice is nearly out of money, and the
          // pill already says so — a cool bar next to a red pill reads as a bug.
          // Identical to the ceiling read on a fully funded CLIN.
          const heat = c.pct_budget ?? c.pct;
          // Where the obligated money runs out, as a fraction of the ceiling track.
          // Null unless the line is actually incrementally funded — and always on
          // fixed price, where funding is not the constraint at all (#79).
          const fundedMarker =
            !mm && c.incrementally_funded && c.funded != null && c.ceiling
              ? Math.max(0, Math.min(100, (c.funded / c.ceiling) * 100))
              : null;
          const barColor = mm
            ? statusColor(c.status)
            : heat > 0.85
              ? "var(--bad)"
              : heat > 0.7
                ? "var(--warn)"
                : hue;
          // Non-labor CLINs have no timesheet burn — their card routes into the
          // expense log to add/see actuals; labor cards select the burn chart.
          // On a margin card the right-hand figure is the margin left in the price,
          // which is the fixed-price answer to "how much room is there".
          const runwayLabel = !c.is_labor
            ? "log actuals →"
            : mm
              ? pos && pos.known
                ? `${pct(pos.projected_margin_pct)} margin`
                : "margin n/a"
              : c.status === "paused"
                ? "no burn"
                : `${c.runway_days}d runway`;
          const runwayColor = !c.is_labor
            ? "var(--accent)"
            : mm && !(pos && pos.known)
              ? "var(--faint)"
              : statusColor(c.status);
          // No stop date on a margin card — that's the whole point. Replaced by where
          // cost lands against the price at the current pace.
          const stopLabel = mm
            ? pos
              ? `${moneyM(pos.projected_cost)} projected cost vs ${moneyM(pos.price)} price`
              : null
            : stopPhrase(c.stop_date, c.stop_reason, c.stop_date_passed);
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
              {/* The track is the ceiling, the fill is spend, and on an
                  incrementally funded line a marker sits where the obligated money
                  runs out (#39). Without it a 40% fill next to "89 days runway"
                  looks wrong, because the runway is measured against the funded
                  slice the bar never drew. */}
              <div
                style={{
                  position: "relative",
                  height: 8,
                  borderRadius: 5,
                  background: "var(--border)",
                  marginTop: 11,
                }}
                title={
                  fundedMarker !== null
                    ? `Funded ${moneyM(c.funded)} of ${moneyM(c.ceiling)} ceiling`
                    : undefined
                }
              >
                <div
                  style={{
                    height: "100%",
                    width: `${Math.min(100, Math.round(c.pct * 100))}%`,
                    background: barColor,
                    borderRadius: 5,
                  }}
                />
                {fundedMarker !== null && (
                  <span
                    style={{
                      position: "absolute",
                      top: -2,
                      bottom: -2,
                      left: `${fundedMarker}%`,
                      width: 2,
                      marginLeft: -1,
                      borderRadius: 1,
                      background: "var(--text)",
                      opacity: 0.75,
                    }}
                  />
                )}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 11.5, color: "var(--dim)" }}>
                {/* The label has to name the quantity, because `spent` means a
                    different thing per pricing policy (#79) — cost on a cost-type or
                    fixed-price line, billings on T&M. Printing "$X spent" over all
                    three is how the two get conflated. */}
                {/* Name the denominator (#39). "40%" alone is unreadable next to a
                    runway measured against funded dollars; on an incrementally
                    funded line both reads are printed, and the funded one is bolded
                    because it's the one the pill and the runway agree with. */}
                <span>
                  {mm ? "cost " : ""}
                  {moneyM(c.spent)} / {moneyM(c.ceiling)}
                  {mm ? " price" : ""} ·{" "}
                  {fundedMarker !== null ? (
                    <>
                      <span>{pct(c.pct)} of ceiling</span> ·{" "}
                      <b style={{ color: "var(--text)" }}>{pct(c.pct_budget)} of funded</b>
                    </>
                  ) : (
                    <b style={{ color: "var(--text)" }}>
                      {pct(c.pct)}
                      {mm ? "" : " of ceiling"}
                    </b>
                  )}
                </span>
                <span style={{ color: runwayColor, fontWeight: 600 }}>{runwayLabel}</span>
              </div>
              {/* Hard-stop forecast (#23): the runway figure above as a calendar
                  date, which is the form the question actually gets asked in
                  ("what day do we have to stop charging?"). Null on non-labor and
                  paused CLINs, which have no pace to project from — and on
                  fixed-price lines, where charging never stops (#79) and this slot
                  carries the cost-vs-price projection instead. */}
              {stopLabel && (
                <div style={{ marginTop: 5, fontSize: 11, color: c.stop_date_passed && !mm ? "var(--bad)" : "var(--faint)" }}>
                  {stopLabel}
                </div>
              )}
              {/* Why this card looks different from its neighbours. One line, only on
                  fixed-price lines, because a reader who knows the pre-#79 app will
                  otherwise read the missing runway as a bug. */}
              {mm && (
                <div style={{ marginTop: 5, fontSize: 10.5, color: "var(--faint)" }}>
                  {c.pricing_policy?.label || "Fixed price"} — the price is owed on
                  delivery, so there is no funding runway to report
                  {pos && !pos.known ? "; margin needs direct rates" : ""}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Bottom of the view, deliberately far from the tripwires: remove this
          contract entirely. Same quiet trash as the portfolio cards. */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginTop: 34,
          paddingTop: 14,
          borderTop: "1px solid var(--border)",
        }}
      >
        <TrashButton
          label={`Delete contract ${contract.piid || contract.name}`}
          onClick={() => setPendingDelete(true)}
        />
        <span style={{ fontSize: 11.5, color: "var(--faint)" }}>
          Delete this contract and everything attached to it
        </span>
      </div>

      {pendingDelete && (
        <DeleteConfirm
          contracts={[{ id: contractId, piid: contract.piid, name: contract.name }]}
          onCancel={() => setPendingDelete(false)}
          onDone={(ids) => {
            setPendingDelete(false);
            onDeleted?.(ids);
          }}
        />
      )}
    </div>
  );
}
