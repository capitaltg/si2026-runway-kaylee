import React, { useEffect, useState } from "react";
import Sidebar from "./components/Sidebar.jsx";
import TopBar from "./components/TopBar.jsx";
import Ingest from "./views/Ingest.jsx";
import Portfolio from "./views/Portfolio.jsx";
import FlightDeck from "./views/FlightDeck.jsx";
import Expenses from "./views/Expenses.jsx";
import { applyTheme } from "./theme.js";
import { getBurn } from "./api.js";

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
  const [activeId, setActiveId] = useState(null);
  // The non-labor CLIN a Flight Deck card asked the Expenses view to open on.
  const [expenseClin, setExpenseClin] = useState(null);
  // Burn summary for the active contract, used only to dress the global chrome
  // (sidebar health card + top-bar period bar + Export). Views still fetch
  // their own data; this refreshes whenever the active contract changes.
  const [chrome, setChrome] = useState(null);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

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

  function openExpenses(clin) {
    setExpenseClin(clin);
    setView("expenses");
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
          onExport={() => exportCsv(chrome)}
          onAskRunway={() => setView("chat")}
        />
        <div style={{ flex: 1, overflow: "auto" }}>
          {view === "ingest" ? (
            <Ingest onSaved={openContract} />
          ) : view === "portfolio" ? (
            <Portfolio onOpen={openContract} />
          ) : view === "flightdeck" ? (
            <FlightDeck contractId={activeId} setActiveId={setActiveId} onOpenExpenses={openExpenses} />
          ) : view === "expenses" ? (
            <Expenses contractId={activeId} initialClin={expenseClin} setActiveId={setActiveId} />
          ) : view === "allocate" ? (
            <Placeholder name="Allocation Matrix" note="Staff → CLINs allocation is coming soon (issue #21)." />
          ) : view === "chat" ? (
            <Placeholder name="Ask Runway" note="Live AI answers on your burn are coming soon (issue #15)." />
          ) : (
            <Placeholder name={view} note="Coming soon." />
          )}
        </div>
      </main>
    </div>
  );
}
