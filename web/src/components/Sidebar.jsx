import React from "react";

// Global sidebar, built to match docs/design/Runway.dc.html (issue #3).
// Two scopes: app-wide (Portfolio, People) and "Current contract" — the latter
// is a boxed card (name · number · health · Switch↗) whose bottom edge flows
// straight into the contract-scoped nav items, so they read as one unit.

const ICONS = {
  portfolio: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  ),
  flightdeck: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <circle cx="12" cy="12" r="9" /><path d="M12 12l4-2.5" strokeLinecap="round" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" /><path d="M12 3v2M21 12h-2M12 21v-2M3 12h2" />
    </svg>
  ),
  allocate: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M3 15h18M9 3v18" />
    </svg>
  ),
  expenses: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18" />
      <path d="M7 14h5M7 17h3" strokeLinecap="round" /><circle cx="16.5" cy="15.5" r="1.6" />
    </svg>
  ),
  ingest: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z" />
      <path d="M14 3v5h5" strokeLinejoin="round" /><path d="M9 13l2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  funding: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M3 21h18" strokeLinecap="round" />
      <path d="M6 21V12M11 21V7M16 21V15" strokeLinecap="round" />
      <path d="M6 12l5-5 5 8 3-6" strokeLinecap="round" strokeLinejoin="round" opacity=".55" />
    </svg>
  ),
  rates: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M18.5 5.5l-13 13" strokeLinecap="round" />
      <circle cx="7.75" cy="7.75" r="2.6" />
      <circle cx="16.25" cy="16.25" r="2.6" opacity=".55" />
    </svg>
  ),
  drafts: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z" />
      <path d="M14 3v5h5" strokeLinejoin="round" />
      <path d="M8 13h8M8 17h5" strokeLinecap="round" />
    </svg>
  ),
  people: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 20a5.5 5.5 0 0111 0" strokeLinecap="round" />
      <path d="M16 5.6a3.2 3.2 0 010 4.8M17.5 14.4A5.5 5.5 0 0120.5 19.4" strokeLinecap="round" opacity=".55" />
    </svg>
  ),
};

// The app-wide scopes. People is global for the same reason Portfolio is: a
// person's degree is not a fact about a contract, so it does not belong under
// "Current contract" — and putting it here is the nav expressing that. Ingest is
// global for a blunter reason: it is the *add a contract* screen and takes no
// contract at all, so filing it under "Current contract" claimed it would act on
// the open one. It sits last because it's an occasional action, not a landing.
const GLOBAL_NAV = [
  { key: "portfolio", label: "Portfolio", sub: "All contracts" },
  { key: "people", label: "People", sub: "Directory & quals" },
  { key: "ingest", label: "Add a Contract", sub: "PDF → data" },
];
const CONTRACT_NAV = [
  { key: "flightdeck", label: "Flight Deck", sub: "Live burn & runway" },
  { key: "allocate", label: "Allocation Matrix", sub: "Staff → CLINs" },
  { key: "expenses", label: "Expenses", sub: "Non-labor CLINs" },
  { key: "funding", label: "Funding History", sub: "Award + SF-30 mods" },
  { key: "rates", label: "Indirect Rates", sub: "Cost vs. billing (optional)" },
  // Sits directly after Indirect Rates because that is where its inputs come from:
  // a Level-1 contract sees this view withhold every margin figure and point back
  // up one row (#82).
  { key: "profitability", label: "Profitability", sub: "Cost · revenue · fee" },
  { key: "drafts", label: "Drafts", sub: "Memos · invoices · check-ins" },
];

const sectionLabel = {
  padding: "0 18px",
  fontSize: 10.5,
  letterSpacing: ".13em",
  textTransform: "uppercase",
  color: "var(--faint)",
  fontWeight: 600,
  marginBottom: 9,
};

// over → at risk, watch → watch closely, otherwise on plan. Health comes from
// the real burn `hero`; with no contract loaded the card shows a neutral prompt.
function healthOf(hero) {
  const s = hero?.status;
  if (s === "over") return { label: "At risk", color: "var(--bad)", dot: "var(--bad)" };
  // `fee_eroding` (#81) sits with `watch`: on plan against its funding, losing fee to
  // the overrun. Without it the health card reads "On plan" green on a contract whose
  // fee the overrun is eating, which is the blind spot the state exists to close.
  if (s === "watch" || s === "fee_eroding")
    return { label: "Watch closely", color: "var(--warn)", dot: "var(--warn)" };
  return { label: "On plan", color: "var(--good)", dot: "var(--good)" };
}

export default function Sidebar({ view, setView, contract, hero }) {
  const NavItem = (n) => {
    const active = view === n.key;
    return (
      <div
        key={n.key}
        onClick={() => setView(n.key)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 11,
          padding: "10px 12px",
          borderRadius: 11,
          cursor: "pointer",
          color: active ? "#fff" : "var(--dim)",
          background: active ? "var(--accent)" : "transparent",
        }}
      >
        <span style={{ display: "flex", flex: "0 0 19px" }}>{ICONS[n.key]}</span>
        <div>
          <div style={{ fontWeight: 600, fontSize: 13.5 }}>{n.label}</div>
          <div style={{ fontSize: 11, opacity: 0.62 }}>{n.sub}</div>
        </div>
      </div>
    );
  };

  const boxBase = {
    margin: "0 12px",
    border: "1px solid var(--border)",
    background: "var(--panel2)",
  };
  const health = healthOf(hero);

  return (
    <aside
      style={{
        width: 242,
        flex: "0 0 242px",
        background: "var(--panel)",
        borderRight: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        position: "sticky",
        top: 0,
        height: "100vh",
      }}
    >
      {/* Brand */}
      <div style={{ padding: "22px 18px 18px", display: "flex", alignItems: "center", gap: 11 }}>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 13,
            background: "linear-gradient(150deg,var(--accent),var(--accent2))",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow: "0 8px 18px rgba(67,97,238,.32)",
          }}
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
            <path d="M2 15.5l20-6.2-3.1-2.2-6.4 1.6-5.4-4.3-2.1.6 3.2 4.9-4 1-2.1-1.4-1.6.5L2 15.5z" fill="#fff" />
            <path d="M4 19h16" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" opacity=".85" />
          </svg>
        </div>
        <div>
          <div
            style={{
              fontFamily: "'Space Grotesk',sans-serif",
              fontWeight: 700,
              fontSize: 20,
              letterSpacing: "-.02em",
              lineHeight: 1,
              color: "var(--text)",
            }}
          >
            Runway
          </div>
          <div
            style={{
              fontSize: 10.5,
              letterSpacing: ".14em",
              textTransform: "uppercase",
              color: "var(--faint)",
              marginTop: 3,
              fontWeight: 600,
            }}
          >
            Burn early-warning
          </div>
        </div>
      </div>

      {/* Everything between the brand and the footer scrolls as one region. The
          brand and the footer stay put — a scrolling wordmark reads as broken,
          and the footer carries the "not a system of record" disclaimer, which
          shouldn't be scrollable out of existence. `minHeight: 0` is the
          load-bearing bit: without it this flex child refuses to shrink below
          its content and the overflow never engages. */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          scrollbarWidth: "thin",
          scrollbarColor: "var(--border) transparent",
        }}
      >
        {/* App-wide */}
        <div style={{ padding: "8px 12px 0", display: "flex", flexDirection: "column", gap: 4 }}>
          {GLOBAL_NAV.map(NavItem)}
        </div>

        <div style={{ margin: "16px 16px 11px", height: 1, background: "var(--border)" }} />

        {/* Current contract — boxed card + its scoped nav */}
        <div style={sectionLabel}>Current contract</div>
        <div
          onClick={() => setView("portfolio")}
          title="Switch contract"
          style={{
            ...boxBase,
            padding: "12px 13px",
            borderBottom: "none",
            borderRadius: "14px 14px 0 0",
            cursor: "pointer",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <div style={{ fontWeight: 600, fontSize: 12.5, color: "var(--text)", lineHeight: 1.3, flex: 1, minWidth: 0 }}>
              {contract?.name || "No contract selected"}
            </div>
            <span style={{ fontSize: 10, color: "var(--accent)", fontWeight: 600, whiteSpace: "nowrap" }}>
              Switch &#8599;
            </span>
          </div>
          {contract ? (
            <div style={{ marginTop: 5 }}>
              {contract.nickname && contract.legal_name && (
                <div style={{ fontSize: 11, color: "var(--dim)", lineHeight: 1.25 }}>{contract.legal_name}</div>
              )}
              {contract.piid && (
                <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: "var(--faint)", marginTop: 2 }}>
                  {contract.piid}
                </div>
              )}
            </div>
          ) : (
            <div style={{ fontSize: 10.5, color: "var(--dim)", marginTop: 5 }}>Open one from Portfolio</div>
          )}
          {contract && (
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 9 }}>
              <span style={{ width: 9, height: 9, borderRadius: "50%", background: health.dot }} />
              <span style={{ fontSize: 11.5, fontWeight: 600, color: health.color }}>{health.label}</span>
            </div>
          )}
        </div>
        <div
          style={{
            ...boxBase,
            padding: "7px 8px 9px",
            borderTop: "none",
            borderRadius: "0 0 14px 14px",
            display: "flex",
            flexDirection: "column",
            gap: 3,
          }}
        >
          {CONTRACT_NAV.map(NavItem)}
        </div>

        {/* Timesheets live — honest: shown only while a contract is loaded; no
            fabricated "synced N min ago" timestamp (we don't persist one). */}
        {contract && (
          <div
            style={{
              margin: "14px 12px 0",
              padding: "11px 13px",
              borderRadius: 12,
              background: "var(--goodBg)",
              display: "flex",
              alignItems: "center",
              gap: 9,
            }}
          >
            <span style={{ width: 9, height: 9, flex: "0 0 9px", borderRadius: "50%", background: "var(--good)" }} />
            <div style={{ fontSize: 11, color: "var(--text)", lineHeight: 1.35 }}>
              <b>Timesheets live</b>
              <br />
              <span style={{ color: "var(--dim)" }}>synced from your provider</span>
            </div>
          </div>
        )}

        {/* Breathing room so the last item never sits flush against the footer
            when the region is scrolled to the bottom. */}
        <div style={{ height: 14 }} />
      </div>

      {/* Footer — outside the scroll region, so the disclaimer is always visible.
          The scroll wrapper's flex:1 now does the pushing that marginTop:auto did. */}
      <div style={{ padding: "16px 18px", fontSize: 10.5, color: "var(--faint)", lineHeight: 1.5 }}>
        <div style={{ fontFamily: "'IBM Plex Mono',monospace", letterSpacing: ".05em" }}>CTG · GovCon fintech</div>
        <div style={{ marginTop: 3 }}>Demo data · not a system of record</div>
      </div>
    </aside>
  );
}
