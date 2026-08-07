import React, { useEffect, useRef, useState } from "react";
import Sidebar from "./components/Sidebar.jsx";
import TopBar from "./components/TopBar.jsx";
import Ingest from "./views/Ingest.jsx";
import Portfolio from "./views/Portfolio.jsx";
import FlightDeck from "./views/FlightDeck.jsx";
import Expenses from "./views/Expenses.jsx";
import FundingHistory from "./views/FundingHistory.jsx";
import IndirectRates from "./views/IndirectRates.jsx";
import AskRunway from "./views/AskRunway.jsx";
import AllocationMatrix from "./views/AllocationMatrix.jsx";
import Drafts from "./views/Drafts.jsx";
import People from "./views/People.jsx";
import { applyTheme } from "./theme.js";
import { getBurn, renameContract } from "./api.js";
import { createHistoryAdapter, parseLocation, pathFor } from "./navigation.js";

function Placeholder({ name, note }) {
  return (
    <div style={{ padding: "60px 40px", color: "var(--dim)" }}>
      <div style={{ fontFamily: "'Space Grotesk',sans-serif", fontSize: 22, color: "var(--text)" }}>
        {name}
      </div>
      <p style={{ fontSize: 14, marginTop: 8 }}>{note}</p>
    </div>
  );
}

// Download the loaded contract's burn data as a spreadsheet. CSV (opens in
// Excel) — honest export of exactly what's on screen, no server round-trip.
function exportCsv(burn) {
  if (!burn) return;
  const c = burn.contract || {};
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const rows = [
    ["Runway export", c.name || ""],
    ["PIID", c.piid || ""],
    ["Agency", c.agency || ""],
    ["Period of performance", `${c.pop_start || ""} to ${c.pop_end || ""}`],
    [],
    ["CLIN", "Name", "Status", "Spent", "Budget", "Weekly burn", "Runway (days)"],
    ...(burn.clins || []).map((x) => [
      x.code,
      x.name,
      x.status_label || x.status,
      x.spent,
      x.budget,
      x.weekly,
      x.runway_days,
    ]),
  ];
  const csv = rows.map((r) => r.map(esc).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8;" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `runway-${c.piid || "contract"}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function App() {
  const initialRouteRef = useRef(null);
  if (!initialRouteRef.current) initialRouteRef.current = parseLocation(window.location);
  const [route, setRoute] = useState(() => initialRouteRef.current);
  const [routeNotice, setRouteNotice] = useState(() => initialRouteRef.current.invalid);
  const [theme, setTheme] = useState("light");
  // App-wide AI preference (off by default). When on, Runway may use AI to
  // phrase things like Flight Deck suggestions; when off it uses built-in
  // deterministic copy. Read by any feature that wants an AI path.
  const [aiEnabled, setAiEnabled] = useState(() => localStorage.getItem("runway.ai") === "on");
  const view = route.view;
  const activeId = route.activeId;
  const historyAdapterRef = useRef(null);
  if (!historyAdapterRef.current) {
    historyAdapterRef.current = createHistoryAdapter({
      onChange: (nextRoute) => {
        if (nextRoute.invalid) {
          window.history.replaceState({}, "", "/portfolio");
          setRoute({ view: "portfolio", activeId: null, invalid: false });
          setRouteNotice(true);
        } else {
          setRoute(nextRoute);
          setRouteNotice(false);
        }
      },
    });
  }
  const historyAdapter = historyAdapterRef.current;
  // Ask Runway is a slide-out drawer overlaid on any view, not a view itself.
  const [askOpen, setAskOpen] = useState(false);
  // The non-labor CLIN a Flight Deck card asked the Expenses view to open on.
  const [expenseClin, setExpenseClin] = useState(null);
  // Set when a Flight Deck suggestion routes to the Allocation Matrix so it
  // pre-applies the rebalanced plan; cleared once the Matrix consumes it.
  const [pendingBalance, setPendingBalance] = useState(false);
  const [pendingPerson, setPendingPerson] = useState(null);
  // A Flight Deck funding suggestion asked to draft a funding request; the Drafts
  // view reads this once on arrival to auto-select the doc type and generate.
  const [pendingDocType, setPendingDocType] = useState(null);
  // Burn summary for the active contract, used only to dress the global chrome
  // (sidebar health card + top-bar period bar + Export). Views still fetch
  // their own data; this refreshes whenever the active contract changes.
  const [chrome, setChrome] = useState(null);

  useEffect(() => {
    historyAdapter.start();
    if (initialRouteRef.current.invalid) {
      window.history.replaceState({}, "", "/portfolio");
      setRoute({ view: "portfolio", activeId: null, invalid: false });
    }
    return () => historyAdapter.stop();
  }, [historyAdapter]);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Persist the AI preference so it sticks across sessions and stays app-wide.
  useEffect(() => {
    localStorage.setItem("runway.ai", aiEnabled ? "on" : "off");
  }, [aiEnabled]);

  useEffect(() => {
    if (activeId == null) {
      setChrome(null);
      return;
    }
    let live = true;
    getBurn(activeId)
      .then((b) => live && setChrome(b))
      .catch(() => live && setChrome(null));
    return () => {
      live = false;
    };
  }, [activeId]);

  function navigate(nextView, nextId = activeId, options) {
    historyAdapter.navigate(nextView, nextId, options);
  }

  // Child views use this when they discover or change the current contract.
  // Replacing keeps passive data hydration from adding a history entry while
  // still making the selected contract refresh-safe and shareable.
  function setActiveId(next) {
    setRoute((current) => {
      const resolved = typeof next === "function" ? next(current.activeId) : next;
      const normalized = resolved == null ? null : Number(resolved);
      const nextPath = pathFor(current.view, normalized);
      if (window.location.pathname !== nextPath) window.history.replaceState({}, "", nextPath);
      return { ...current, activeId: normalized };
    });
  }

  function openContract(id) {
    navigate("flightdeck", id);
  }

  // Save a user-chosen nickname for the active contract, then refresh the chrome
  // so the new name lands in the sidebar card and top bar immediately.
  async function onRename(name) {
    if (activeId == null) return;
    await renameContract(activeId, name);
    getBurn(activeId)
      .then(setChrome)
      .catch(() => {});
  }

  // A contract was deleted. If it was the one loaded, the whole app is pointed at
  // a dead id — clear the selection (which drops the sidebar health card and the
  // top-bar period bar too) and fall back to the portfolio.
  function onContractsDeleted(ids) {
    if (activeId != null && ids.includes(activeId)) {
      setChrome(null);
      navigate("portfolio", null, { replace: true });
    }
  }

  function openExpenses(clin) {
    setExpenseClin(clin);
    navigate("expenses");
  }

  // A trim/boost suggestion jumped us to the Allocation Matrix — flag it to
  // pre-apply the rebalanced plan on arrival. AllocationMatrix clears the flag
  // (onAutoBalanced) once it fires, so a later manual visit stays untouched.
  function openAllocationBalanced() {
    setPendingBalance(true);
    navigate("allocate");
  }

  // Deep-link from the Flight Deck's "who's running hot" strip (#83) into the
  // allocation matrix with that person in view. Reuses the matrix's existing
  // name/LCAT search rather than adding a second filter concept — the dashboard
  // reports, the matrix is where hours change.
  function openAllocationForPerson(name) {
    setPendingPerson(name || null);
    navigate("allocate");
  }

  // Deep-link from a suggestion into the Drafts view, pre-loaded for a contract.
  function openDrafts(id, docType) {
    if (id != null) setActiveId(id);
    setPendingDocType(docType || null);
    navigate("drafts", id ?? activeId);
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <Sidebar view={view} setView={(nextView) => navigate(nextView)} contract={chrome?.contract} hero={chrome?.hero} />
      <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <TopBar
          view={view}
          contract={chrome?.contract}
          theme={theme}
          toggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          aiEnabled={aiEnabled}
          toggleAi={() => setAiEnabled((v) => !v)}
          onExport={() => exportCsv(chrome)}
          onAskRunway={() => setAskOpen(true)}
        />
        <div style={{ flex: 1, overflow: "auto" }}>
          {view === "ingest" ? (
            <Ingest onSaved={openContract} />
          ) : view === "portfolio" ? (
            <Portfolio onOpen={openContract} onDeleted={onContractsDeleted} />
          ) : view === "people" ? (
            <People onOpenContract={openContract} />
          ) : view === "flightdeck" ? (
            <FlightDeck
              contractId={activeId}
              setActiveId={setActiveId}
              onOpenExpenses={openExpenses}
              onOpenAllocation={() => navigate("allocate")}
              onOpenPerson={openAllocationForPerson}
              onApplyFix={openAllocationBalanced}
              onOpenFunding={() => navigate("funding")}
              onOpenDrafts={openDrafts}
              onRename={onRename}
              onDeleted={onContractsDeleted}
              aiEnabled={aiEnabled}
            />
          ) : view === "expenses" ? (
            <Expenses contractId={activeId} initialClin={expenseClin} setActiveId={setActiveId} />
          ) : view === "funding" ? (
            <FundingHistory contractId={activeId} />
          ) : view === "rates" ? (
            <IndirectRates contractId={activeId} setActiveId={setActiveId} />
          ) : view === "allocate" ? (
            <AllocationMatrix
              contractId={activeId}
              setActiveId={setActiveId}
              autoBalance={pendingBalance}
              onAutoBalanced={() => setPendingBalance(false)}
              focusPerson={pendingPerson}
              onFocusedPerson={() => setPendingPerson(null)}
            />
          ) : view === "drafts" ? (
            <Drafts
              contractId={activeId}
              setActiveId={setActiveId}
              aiEnabled={aiEnabled}
              pendingDocType={pendingDocType}
              onConsumedPending={() => setPendingDocType(null)}
            />
          ) : (
            <Placeholder name={view} note="Coming soon." />
          )}
        </div>
      </main>
      <AskRunway
        open={askOpen}
        onClose={() => setAskOpen(false)}
        contractId={activeId}
      />
      {routeNotice && (
        <div
          role="alert"
          style={{
            position: "fixed",
            right: 22,
            bottom: 22,
            zIndex: 20,
            maxWidth: 360,
            padding: "12px 14px",
            border: "1px solid var(--border)",
            borderRadius: 12,
            background: "var(--panel)",
            color: "var(--text)",
            boxShadow: "0 8px 24px rgba(26,34,51,.16)",
            fontSize: 13,
          }}
        >
          <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
            <span>That page could not be opened, so Runway returned you to Portfolio.</span>
            <button
              type="button"
              aria-label="Dismiss navigation notice"
              onClick={() => setRouteNotice(false)}
              style={{ border: 0, background: "transparent", color: "var(--dim)", cursor: "pointer", fontSize: 16 }}
            >
              ×
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
