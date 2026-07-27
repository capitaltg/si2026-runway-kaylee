import React, { useEffect, useState } from "react";
import Sidebar from "./components/Sidebar.jsx";
import Ingest from "./views/Ingest.jsx";
import Portfolio from "./views/Portfolio.jsx";
import FlightDeck from "./views/FlightDeck.jsx";
import { applyTheme } from "./theme.js";

function Placeholder({ name }) {
  return (
    <div style={{ padding: "60px 40px", color: "var(--dim)" }}>
      <div style={{ fontFamily: "'Space Grotesk',sans-serif", fontSize: 22, color: "var(--text)" }}>
        {name}
      </div>
      <p style={{ fontSize: 14, marginTop: 8 }}>Coming next — ingest a contract first.</p>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState("portfolio");
  const [theme, setTheme] = useState("light");
  const [activeId, setActiveId] = useState(null);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  function openContract(id) {
    setActiveId(id);
    setView("flightdeck");
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <Sidebar
        view={view}
        setView={setView}
        theme={theme}
        toggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
      />
      <main style={{ flex: 1, overflowY: "auto" }}>
        {view === "ingest" ? (
          <Ingest onSaved={openContract} />
        ) : view === "portfolio" ? (
          <Portfolio onOpen={openContract} />
        ) : view === "flightdeck" ? (
          <FlightDeck contractId={activeId} setActiveId={setActiveId} />
        ) : (
          <Placeholder name={labelFor(view)} />
        )}
      </main>
    </div>
  );
}

function labelFor(view) {
  return {
    portfolio: "Portfolio",
    flightdeck: "Flight Deck",
    allocate: "Team Allocator",
    expenses: "Expenses",
  }[view];
}
