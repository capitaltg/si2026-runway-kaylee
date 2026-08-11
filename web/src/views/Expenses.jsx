import React, { useEffect, useMemo, useState } from "react";
import {
  getBurn,
  listContracts,
  listExpenses,
  addExpense,
  deleteExpense,
} from "../api.js";
import { money, pct, panelStyle, hueFor, pill, clauseRisk } from "../format.js";

const grotesk = "'Space Grotesk',sans-serif";
const mono = "'IBM Plex Mono',monospace";
const CATEGORIES = ["Travel", "ODC", "Materials", "Subcontractor", "Other"];

// A muted hue per category, so the table reads at a glance.
const CAT_HUE = {
  Travel: "#4361ee",
  ODC: "#06b6d4",
  Materials: "#7c5cff",
  Subcontractor: "#ef8f2a",
  Other: "#9aa6bd",
};

const inputStyle = {
  height: 38,
  padding: "0 11px",
  borderRadius: 10,
  border: "1px solid var(--border)",
  background: "var(--panel2)",
  color: "var(--text)",
  fontSize: 13,
};

const labelStyle = { fontSize: 11, color: "var(--dim)", marginBottom: 5 };

const tileLabel = {
  fontSize: 11,
  letterSpacing: ".08em",
  textTransform: "uppercase",
  color: "var(--faint)",
  fontWeight: 700,
};
const tileNum = {
  fontFamily: grotesk,
  fontWeight: 700,
  fontSize: 28,
  color: "var(--text)",
  marginTop: 4,
};

// spent-vs-ceiling status, mirroring the backend's _nl_status bands.
function nlStatus(spent, ceiling) {
  if (spent <= 0) return "tracked";
  const r = ceiling ? spent / ceiling : 0;
  if (r >= 1) return "over";
  if (r >= 0.8) return "watch";
  return "ok";
}

const emptyDraft = () => ({
  date: new Date().toISOString().slice(0, 10),
  desc: "",
  cat: "Travel",
  amount: "",
});

export default function Expenses({ contractId, initialClin, setActiveId }) {
  const [burn, setBurn] = useState(null);
  const [clin, setClin] = useState(null);
  const [expenses, setExpenses] = useState([]);
  const [draft, setDraft] = useState(emptyDraft());
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  // The expense the user clicked × on — drives the "are you sure?" modal.
  const [confirming, setConfirming] = useState(null);

  // No contract picked yet — fall back to the newest ingested one, like the
  // Flight Deck does, so navigating straight here still lands on something.
  useEffect(() => {
    if (contractId || !setActiveId) return;
    listContracts()
      .then((cs) => cs.length && setActiveId(cs[0].id))
      .catch((e) => setError(e.message));
  }, [contractId, setActiveId]);

  useEffect(() => {
    if (!contractId) return;
    getBurn(contractId)
      .then((b) => setBurn(b))
      .catch((e) => setError(e.message));
  }, [contractId]);

  const nonLabor = useMemo(
    () => (burn?.clins || []).filter((c) => !c.is_labor),
    [burn]
  );

  // Pick the CLIN: the one the Flight Deck card sent us, else the first.
  useEffect(() => {
    if (!nonLabor.length) return;
    setClin((cur) => {
      if (cur && nonLabor.some((c) => c.id === cur)) return cur;
      if (initialClin && nonLabor.some((c) => c.id === initialClin))
        return initialClin;
      return nonLabor[0].id;
    });
  }, [nonLabor, initialClin]);

  function reload() {
    if (!contractId || !clin) return;
    listExpenses(contractId, clin)
      .then(setExpenses)
      .catch((e) => setError(e.message));
  }

  useEffect(() => {
    setExpenses([]);
    reload();
  }, [contractId, clin]);

  const card = nonLabor.find((c) => c.id === clin);
  const ceiling = card?.ceiling || 0;
  const logged = expenses.reduce((s, e) => s + (+e.amount || 0), 0);
  // The binding limit is the funded (obligated) slice when the CLIN is
  // incrementally funded — that's what you should stay under (#41) — otherwise
  // the full ceiling. `remaining` is measured against it, so it goes negative
  // once you're over the funded limit even with ceiling room to spare.
  const incrFunded = !!card?.incrementally_funded;
  const funded = card?.funded ?? null;
  const bindingLimit = incrFunded ? funded : ceiling;
  const remaining = bindingLimit - logged;
  const usedPct = ceiling ? logged / ceiling : 0;
  const fundedPct = incrFunded && ceiling ? funded / ceiling : null;
  // Prefer the engine's funding-aware status (#41) — it bands on the funded
  // slice, not just the ceiling — and fall back to the local ceiling read only
  // until the burn payload loads.
  const status = card?.status || nlStatus(logged, ceiling);
  const p = pill(status);
  const alarm = status === "over" || status === "funding";
  const remainingColor =
    status === "over"
      ? "var(--bad)"
      : status === "funding" || status === "watch"
        ? "var(--warn)"
        : "var(--text)";
  const barColor =
    status === "over"
      ? "var(--bad)"
      : status === "funding" || status === "watch"
        ? "var(--warn)"
        : "var(--good)";

  function onDraft(field, value) {
    setDraft((d) => ({ ...d, [field]: value }));
  }

  async function onAdd() {
    if (!contractId || !clin) return;
    const amount = parseFloat(draft.amount);
    if (!amount || amount <= 0) {
      setError("Enter an amount greater than 0.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await addExpense(contractId, {
        clin,
        date: draft.date || null,
        description: draft.desc || null,
        category: draft.cat || "Other",
        amount,
      });
      setDraft(emptyDraft());
      reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    const ex = confirming;
    setConfirming(null);
    if (!ex) return;
    try {
      await deleteExpense(contractId, ex.id);
      reload();
    } catch (e) {
      setError(e.message);
    }
  }

  if (!burn && !error) {
    return <div style={{ padding: 40, color: "var(--dim)" }}>Loading expenses…</div>;
  }

  const contractName =
    burn?.contract?.piid || burn?.contract?.name || "this contract";

  return (
    <div style={{ padding: "26px 26px 60px", maxWidth: 1080 }}>
      <div style={{ marginBottom: 16 }}>
        <h2
          style={{
            margin: 0,
            fontFamily: grotesk,
            fontSize: 20,
            fontWeight: 600,
            color: "var(--text)",
          }}
        >
          Non-labor expense log
        </h2>
        <div style={{ fontSize: 13, color: "var(--dim)", marginTop: 4 }}>
          Track travel, ODCs &amp; materials against the{" "}
          {card ? card.code : "non-labor"} ceiling for <b>{contractName}</b>.
          Everything logged here counts toward this contract&apos;s burn — the
          CLIN&apos;s spend against its ceiling and the dashboard&apos;s total
          burned-to-date.
        </div>
      </div>

      {error && (
        <div style={{ ...panelStyle, marginBottom: 16, color: "var(--bad)", fontSize: 13 }}>
          {error}
        </div>
      )}

      {!nonLabor.length ? (
        <div style={{ ...panelStyle, color: "var(--dim)", fontSize: 13 }}>
          This contract has no non-labor CLINs to log expenses against.
        </div>
      ) : (
        <>
          {/* CLIN picker — only when there's more than one non-labor CLIN */}
          {nonLabor.length > 1 && (
            <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
              {nonLabor.map((c) => {
                const on = c.id === clin;
                return (
                  <button
                    key={c.id}
                    onClick={() => setClin(c.id)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 7,
                      height: 34,
                      padding: "0 13px",
                      borderRadius: 10,
                      border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
                      background: on ? "var(--accent)" : "var(--panel)",
                      color: on ? "#fff" : "var(--dim)",
                      fontSize: 12.5,
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    <span style={{ fontFamily: mono }}>{c.code}</span>
                    <span
                      style={{
                        maxWidth: 160,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {c.name}
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          {/* funding warning — this CLIN is past its obligated (funded) slice,
              even if the ceiling still has room (#41). Mirrors the Flight Deck
              funding banner so the drill-down doesn't read "all fine". */}
          {alarm && card && (
            <div
              style={{
                ...panelStyle,
                marginBottom: 16,
                borderLeft: `3px solid ${status === "over" ? "var(--bad)" : "var(--warn)"}`,
              }}
            >
              <div style={{ display: "flex", gap: 11, alignItems: "flex-start" }}>
                <span style={{ fontSize: 17, lineHeight: 1.3 }}>⚠️</span>
                <div>
                  <div
                    style={{
                      fontFamily: grotesk,
                      fontWeight: 600,
                      fontSize: 14,
                      color: status === "over" ? "var(--bad)" : "var(--warn)",
                    }}
                  >
                    {/* `funds_exceeded` names the limit actually passed; limited_by
                        only names which one binds. They differ on an incrementally
                        funded CLIN that has spent through its ceiling too — that read
                        "Over the funded allocation" while the ceiling was also gone. */}
                    {status === "over"
                      ? `Over ${card.funds_exceeded ? "the funded allocation" : "ceiling"}${
                          card.overspent ? ` by ${money(card.overspent)}` : ""
                        }`
                      : "Over its funded allocation — awaiting the next funding action"}
                  </div>
                  <div style={{ fontSize: 12.5, color: "var(--dim)", marginTop: 5, lineHeight: 1.5 }}>
                    {money(card.spent)} logged against{" "}
                    {money(card.funded != null ? card.funded : card.budget)} obligated
                    {card.ceiling ? ` of a ${money(card.ceiling)} ceiling` : ""}.{" "}
                    {/* The clause comes off the card (#81), not from here: -22 is only
                        the incrementally funded cost-reimbursement case, and this line
                        cited it on every contract type. Null on fixed price, which has
                        no limitation-of-funds mechanic to be at risk of. */}
                    {status === "over"
                      ? clauseRisk(card.funding_clause)
                        ? `Charging past obligated funding is ${clauseRisk(card.funding_clause)}.`
                        : "Charging past the price is an overrun the contractor bears itself."
                      : card.mod_in_progress
                        ? "A funding modification is already outstanding."
                        : "Funding is keeping pace with the clock, so it needs its next funding mod — not a course correction."}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ceiling tracker */}
          <div style={{ ...panelStyle, marginBottom: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span
                style={{ width: 10, height: 10, borderRadius: 3, background: hueFor(2) }}
              />
              <span style={{ fontFamily: mono, fontSize: 11.5, fontWeight: 600, color: "var(--dim)" }}>
                {card?.code}
              </span>
              <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
                {card?.name}
              </span>
              <span style={p.style}>{p.label}</span>
            </div>
            <div style={{ display: "flex", alignItems: "flex-end", gap: 28, marginTop: 16, flexWrap: "wrap" }}>
              <div>
                <div style={tileLabel}>Logged to date</div>
                <div style={tileNum}>{money(logged)}</div>
              </div>
              {incrFunded && (
                <div>
                  <div style={{ ...tileLabel, color: "var(--warn)" }}>Funded — stay under</div>
                  <div style={{ ...tileNum, color: "var(--warn)" }}>{money(funded)}</div>
                </div>
              )}
              <div>
                <div style={tileLabel}>Ceiling</div>
                <div style={tileNum}>{money(ceiling)}</div>
              </div>
              <div>
                <div style={tileLabel}>
                  {incrFunded ? "Left before funded" : "Remaining"}
                </div>
                <div style={{ ...tileNum, color: remainingColor }}>{money(remaining)}</div>
              </div>
            </div>
            <div style={{ position: "relative", height: 10, borderRadius: 6, background: "var(--border)", marginTop: 16 }}>
              <div
                style={{
                  height: "100%",
                  width: `${Math.min(100, Math.round(usedPct * 100))}%`,
                  background: barColor,
                  borderRadius: 6,
                }}
              />
              {/* funded-limit marker: the line you should stay under (#41). */}
              {fundedPct != null && (
                <div
                  title={`Funded limit ${money(funded)}`}
                  style={{
                    position: "absolute",
                    left: `${Math.min(100, fundedPct * 100)}%`,
                    top: -3,
                    bottom: -3,
                    width: 2,
                    background: "var(--warn)",
                  }}
                />
              )}
            </div>
            <div style={{ fontSize: 12, color: "var(--dim)", marginTop: 8 }}>
              {incrFunded ? (
                <>
                  <b style={{ color: "var(--text)" }}>{pct(funded ? logged / funded : 0)}</b> of
                  funded used ·{" "}
                  <span style={{ color: "var(--warn)" }}>▏</span> funded limit {money(funded)} ·{" "}
                  {pct(usedPct)} of ceiling
                </>
              ) : (
                <>{pct(usedPct)} of ceiling used</>
              )}{" "}
              · {expenses.length} {expenses.length === 1 ? "entry" : "entries"}
            </div>
          </div>

          {/* add form */}
          <div style={{ ...panelStyle, marginBottom: 16 }}>
            <div style={{ ...tileLabel, marginBottom: 12 }}>Log an expense</div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
              <div>
                <div style={labelStyle}>Date</div>
                <input
                  type="date"
                  value={draft.date}
                  onChange={(e) => onDraft("date", e.target.value)}
                  style={inputStyle}
                />
              </div>
              <div style={{ flex: 1, minWidth: 220 }}>
                <div style={labelStyle}>Description</div>
                <input
                  type="text"
                  placeholder="e.g. On-site travel — Arlington, VA"
                  value={draft.desc}
                  onChange={(e) => onDraft("desc", e.target.value)}
                  style={{ ...inputStyle, width: "100%" }}
                />
              </div>
              <div>
                <div style={labelStyle}>Category</div>
                <select
                  value={draft.cat}
                  onChange={(e) => onDraft("cat", e.target.value)}
                  style={{ ...inputStyle, cursor: "pointer" }}
                >
                  {CATEGORIES.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </div>
              <div>
                <div style={labelStyle}>Amount ($)</div>
                <input
                  type="number"
                  placeholder="0"
                  value={draft.amount}
                  onChange={(e) => onDraft("amount", e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && onAdd()}
                  style={{ ...inputStyle, width: 120, textAlign: "right", fontFamily: mono }}
                />
              </div>
              <button
                onClick={onAdd}
                disabled={busy}
                style={{
                  height: 38,
                  padding: "0 18px",
                  borderRadius: 10,
                  border: "none",
                  background: "var(--accent)",
                  color: "#fff",
                  fontWeight: 600,
                  fontSize: 13,
                  cursor: busy ? "default" : "pointer",
                  opacity: busy ? 0.6 : 1,
                  boxShadow: "0 4px 12px rgba(67,97,238,.28)",
                }}
              >
                Add entry
              </button>
            </div>
          </div>

          {/* table */}
          <div style={{ ...panelStyle, padding: 0, overflow: "hidden" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr
                  style={{
                    background: "var(--panel2)",
                    color: "var(--faint)",
                    fontSize: 11,
                    textTransform: "uppercase",
                    letterSpacing: ".05em",
                  }}
                >
                  <th style={{ textAlign: "left", padding: "11px 18px", fontWeight: 700 }}>Date</th>
                  <th style={{ textAlign: "left", padding: "11px 8px", fontWeight: 700 }}>Description</th>
                  <th style={{ textAlign: "left", padding: "11px 8px", fontWeight: 700 }}>Category</th>
                  <th style={{ textAlign: "right", padding: "11px 8px", fontWeight: 700 }}>Amount</th>
                  <th style={{ width: 44 }} />
                </tr>
              </thead>
              <tbody>
                {expenses.map((ex) => (
                  <tr key={ex.id} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "11px 18px", fontFamily: mono, color: "var(--dim)" }}>
                      {ex.date || "—"}
                    </td>
                    <td style={{ padding: "11px 8px", color: "var(--text)" }}>
                      {ex.description || "—"}
                    </td>
                    <td style={{ padding: "11px 8px" }}>
                      <span
                        style={{
                          fontSize: 10.5,
                          fontWeight: 700,
                          padding: "2px 9px",
                          borderRadius: 20,
                          color: CAT_HUE[ex.category] || CAT_HUE.Other,
                          background: "var(--panel2)",
                        }}
                      >
                        {ex.category || "Other"}
                      </span>
                    </td>
                    <td style={{ padding: "11px 8px", textAlign: "right", fontFamily: mono, fontWeight: 600, color: "var(--text)" }}>
                      {money(ex.amount)}
                    </td>
                    <td style={{ textAlign: "center" }}>
                      <button
                        onClick={() => setConfirming(ex)}
                        title="Remove"
                        style={{
                          width: 26,
                          height: 26,
                          borderRadius: 7,
                          border: "1px solid var(--border)",
                          background: "var(--panel2)",
                          color: "var(--dim)",
                          cursor: "pointer",
                          fontSize: 14,
                          lineHeight: 1,
                        }}
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
                {expenses.length === 0 && (
                  <tr>
                    <td colSpan={5} style={{ padding: 28, textAlign: "center", color: "var(--faint)", fontSize: 13 }}>
                      No expenses logged yet — add your first entry above.
                    </td>
                  </tr>
                )}
              </tbody>
              <tfoot>
                <tr style={{ borderTop: "2px solid var(--border)", background: "var(--panel2)" }}>
                  <td colSpan={3} style={{ padding: "12px 18px", fontWeight: 700, color: "var(--text)" }}>
                    Total logged
                  </td>
                  <td style={{ padding: "12px 8px", textAlign: "right", fontFamily: mono, fontWeight: 700, color: "var(--text)" }}>
                    {money(logged)}
                  </td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        </>
      )}

      {confirming && (
        <div
          onClick={() => setConfirming(null)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15,20,35,.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 100,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              ...panelStyle,
              maxWidth: 380,
              width: "90%",
              boxShadow: "0 20px 50px rgba(0,0,0,.28)",
            }}
          >
            <div style={{ fontFamily: grotesk, fontWeight: 600, fontSize: 16, color: "var(--text)" }}>
              Delete this expense?
            </div>
            <div style={{ fontSize: 13, color: "var(--dim)", marginTop: 8, lineHeight: 1.5 }}>
              <b style={{ color: "var(--text)" }}>{money(confirming.amount)}</b>
              {confirming.description ? ` — ${confirming.description}` : ""}
              {confirming.date ? ` (${confirming.date})` : ""}. This can't be undone.
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 20 }}>
              <button
                onClick={() => setConfirming(null)}
                style={{
                  height: 36,
                  padding: "0 16px",
                  borderRadius: 10,
                  border: "1px solid var(--border)",
                  background: "var(--panel2)",
                  color: "var(--text)",
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <button
                onClick={confirmDelete}
                style={{
                  height: 36,
                  padding: "0 16px",
                  borderRadius: 10,
                  border: "none",
                  background: "var(--bad)",
                  color: "#fff",
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
