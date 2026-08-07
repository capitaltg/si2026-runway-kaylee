import React, { useEffect, useState } from "react";
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
  const [view, setView] = useState("portfolio");
  const [theme, setTheme] = useState("light");
  // App-wide AI preference (off by default). When on, Runway may use AI to
  // phrase things like Flight Deck suggestions; when off it uses built-in
  // deterministic copy. Read by any feature that wants an AI path.
  const [aiEnabled, setAiEnabled] = useState(() => localStorage.getItem("runway.ai") === "on");
  const [activeId, setActiveId] = useState(null);
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

  function openContract(id) {
    setActiveId(id);
    setView("flightdeck");
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
      setActiveId(null);
      setChrome(null);
      setView("portfolio");
    }
  }

  function openExpenses(clin) {
    setExpenseClin(clin);
    setView("expenses");
  }

  // A trim/boost suggestion jumped us to the Allocation Matrix — flag it to
  // pre-apply the rebalanced plan on arrival. AllocationMatrix clears the flag
  // (onAutoBalanced) once it fires, so a later manual visit stays untouched.
  function openAllocationBalanced() {
    setPendingBalance(true);
    setView("allocate");
  }

  // Deep-link from the Flight Deck's "who's running hot" strip (#83) into the
  // allocation matrix with that person in view. Reuses the matrix's existing
  // name/LCAT search rather than adding a second filter concept — the dashboard
  // reports, the matrix is where hours change.
  function openAllocationForPerson(name) {
    setPendingPerson(name || null);
    setView("allocate");
  }

  // Deep-link from a suggestion into the Drafts view, pre-loaded for a contract.
  function openDrafts(id, docType) {
    if (id != null) setActiveId(id);
    setPendingDocType(docType || null);
    setView("drafts");
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <Sidebar view={view} setView={setView} contract={chrome?.contract} hero={chrome?.hero} />
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
              onOpenAllocation={() => setView("allocate")}
              onOpenPerson={openAllocationForPerson}
              onApplyFix={openAllocationBalanced}
              onOpenFunding={() => setView("funding")}
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
    </div>
  );
}
