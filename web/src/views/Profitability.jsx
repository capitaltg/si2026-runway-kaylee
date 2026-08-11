import React, { useEffect, useMemo, useReducer } from "react";
import { getBurn, listContracts } from "../api.js";
import { money, pct, panelStyle, pill, statusColor } from "../format.js";
import {
  awardPoolShareLabel,
  awardPeriods,
  clinFigures,
  feeBasisLabel,
  feeClins,
  feeFigures,
  feeGap,
  loadIdle,
  loadPhase,
  loadReducer,
  marginAvailable,
  measuredIn,
  orderedClins,
  pricedBy,
  pricingApplicability,
  profitabilityLabels,
  projection,
  projectionReason,
  rateChain,
  rateVariance,
  shareRatio,
  summary,
} from "../profitability.js";
import { clauseTitle } from "../format.js";

const grotesk = "'Space Grotesk',sans-serif";
const mono = "'IBM Plex Mono',monospace";

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
const tileSub = { fontSize: 11.5, color: "var(--dim)", marginTop: 6, lineHeight: 1.45 };

const th = {
  textAlign: "right",
  padding: "9px 12px",
  fontSize: 10.5,
  letterSpacing: ".06em",
  textTransform: "uppercase",
  color: "var(--faint)",
  fontWeight: 700,
  whiteSpace: "nowrap",
  borderBottom: "1px solid var(--border)",
};
const thLeft = { ...th, textAlign: "left" };
const td = {
  textAlign: "right",
  padding: "11px 12px",
  fontSize: 13,
  fontFamily: mono,
  color: "var(--text)",
  whiteSpace: "nowrap",
  borderBottom: "1px solid var(--border)",
};
const tdLeft = { ...td, textAlign: "left", fontFamily: "inherit" };

// A withheld figure renders as a dash that carries its reason, never as 0. The
// `title` is the whole point: the reason is a fact about the contract's data, and a
// bare dash would read as a rendering bug rather than as a refusal.
// The chart's "provisional / not yet real" mark (`#rwUnfunded` in BurnChart), as a
// background layer: 45° hatch in `var(--warn)` at .3. Kept as a sibling layer rather
// than an element opacity so the text on top stays crisp. Undetermined award fee is
// provisional in exactly the sense the chart already uses it for, so it gets the same
// mark rather than a second visual language for the same idea.
const hatchLayer = {
  position: "absolute",
  inset: 0,
  opacity: 0.3,
  backgroundImage:
    "repeating-linear-gradient(45deg, transparent 0 8px, var(--warn) 8px 9px)",
  pointerEvents: "none",
};

const Figure = ({ figure, format }) =>
  figure.notApplicable ? (
    <span style={{ color: "var(--faint)", fontFamily: mono }} title={figure.withheld}>
      N/A
    </span>
  ) : figure.withheld ? (
    <span style={{ color: "var(--faint)", fontFamily: mono }} title={figure.withheld}>
      —
    </span>
  ) : (
    format(figure.value)
  );

// One labelled figure inside a fee card.
const FeeStat = ({ label, figure, format = money, tone }) => (
  <div style={{ minWidth: 120 }}>
    <div style={{ fontSize: 10.5, color: "var(--faint)", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase" }}>
      {label}
    </div>
    <div style={{ fontFamily: mono, fontSize: 15, marginTop: 3, color: tone || "var(--text)" }}>
      <Figure figure={figure} format={format} />
    </div>
  </div>
);

// The award-fee period table (CPAF). A pending period is money the government has not
// awarded yet, hatched to say so; a determined period is a fact even when the
// determination was zero, which is a real and very different outcome.
function AwardPeriods({ award }) {
  if (!award?.periods?.length) return null;
  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ fontSize: 12, color: "var(--dim)", marginBottom: 8 }}>
        Award-fee periods
        {award.determined != null && award.total != null
          ? ` · ${award.determined} of ${award.total} determined`
          : ""}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {award.periods.map((p, i) => (
          <div
            key={`${p.name}-${i}`}
            style={{
              position: "relative",
              overflow: "hidden",
              display: "flex",
              alignItems: "baseline",
              gap: 12,
              padding: "9px 12px",
              borderRadius: 10,
              border: "1px solid var(--border)",
              background: "var(--panel2)",
              fontSize: 12.5,
            }}
          >
            {p.provisional && <div style={hatchLayer} />}
            <div style={{ fontWeight: 600, minWidth: 96 }}>{p.name}</div>
            <div style={{ color: "var(--dim)", flex: 1, minWidth: 0 }}>
              {p.start && p.end ? `${p.start} → ${p.end}` : "Window not stated"}
              {p.pool_share != null ? ` · ${awardPoolShareLabel(p.pool_share)}` : ""}
              {p.score != null ? ` · score ${p.score}` : ""}
            </div>
            <div style={{ fontFamily: mono }}>
              {p.status === "determined" ? (
                money(p.determined_amount || 0)
              ) : (
                <span style={{ color: "var(--warn)" }}>Pending</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Profitability({ contractId, setActiveId }) {
  const [load, dispatch] = useReducer(loadReducer, loadIdle);

  // Same fallback the Flight Deck and Expenses use: navigating straight here with no
  // contract selected lands on the newest ingested one rather than on nothing. An
  // empty list is the one case that never resolves into a contract, so it says so
  // instead of leaving the spinner up forever.
  useEffect(() => {
    if (contractId || !setActiveId) return;
    let live = true;
    listContracts()
      .then((cs) => {
        if (!live) return;
        if (cs.length) setActiveId(cs[0].id);
        else dispatch({ type: "none" });
      })
      .catch((e) => live && dispatch({ type: "failed", message: e.message }));
    return () => {
      live = false;
    };
  }, [contractId, setActiveId]);

  useEffect(() => {
    if (!contractId) return;
    let live = true;
    // Discard the previous contract's figures and error before the new request goes
    // out; `live` keeps a slow response from a deselected contract from landing.
    dispatch({ type: "select" });
    getBurn(contractId)
      .then((b) => live && dispatch({ type: "loaded", burn: b }))
      .catch((e) => live && dispatch({ type: "failed", message: e.message }));
    return () => {
      live = false;
    };
  }, [contractId]);

  const { burn, error } = load;
  const phase = loadPhase(load);
  const margin = marginAvailable(burn);
  const tiles = useMemo(() => summary(burn), [burn]);
  const clins = useMemo(() => orderedClins(burn), [burn]);
  const fee = useMemo(() => feeClins(burn), [burn]);
  const chain = useMemo(() => rateChain(burn), [burn]);
  const variance = useMemo(() => rateVariance(burn), [burn]);
  const pricing = useMemo(() => profitabilityLabels(burn), [burn]);

  if (phase === "error") {
    return <div style={{ padding: 40, color: "var(--bad)", fontSize: 14 }}>{error}</div>;
  }
  if (phase === "empty") {
    return (
      <div style={{ maxWidth: 820, margin: "0 auto", padding: "28px 32px" }}>
        <div
          style={{ ...panelStyle, textAlign: "center", color: "var(--dim)", fontSize: 13.5 }}
        >
          No contracts ingested yet — add one from{" "}
          <b style={{ color: "var(--text)" }}>Ingest</b> to see what its work earns.
        </div>
      </div>
    );
  }
  if (phase === "loading") {
    return (
      <div style={{ padding: 40, color: "var(--dim)", fontSize: 14 }}>
        Loading profitability…
      </div>
    );
  }

  const contract = burn.contract || {};
  const level = contract.cost_model?.level ?? 1;

  return (
    <div style={{ padding: "26px 32px 60px", maxWidth: 1180 }}>
      <div>
        <div style={{ fontFamily: grotesk, fontSize: 22, fontWeight: 700, color: "var(--text)" }}>
          Profitability
        </div>
        <div style={{ fontSize: 13, color: "var(--dim)", marginTop: 4 }}>
          {contract.name}
          {contract.piid ? ` · ${contract.piid}` : ""} · cost model level {level}
        </div>
      </div>

      {pricing.unknownCount > 0 && (
        <div
          style={{
            ...panelStyle,
            marginTop: 16,
            borderColor: "var(--warn)",
            color: "var(--dim)",
            fontSize: 12.5,
            lineHeight: 1.5,
          }}
        >
          <strong style={{ color: "var(--warn)" }}>
            {pricing.unknownCount} pricing polic{pricing.unknownCount === 1 ? "y is" : "ies are"} unknown.
          </strong>{" "}
          Runway keeps its legacy fallback wherever pricing affects the calculation.
          Their price or limit labels are marked as policy-unknown; earnings and
          return that are independently non-applicable remain N/A.
        </div>
      )}

      {/* ---- Contract summary --------------------------------------------- */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))",
          gap: 14,
          marginTop: 20,
        }}
      >
        <div style={panelStyle}>
          <div style={tileLabel}>Total revenue</div>
          <div style={tileNum}>
            <Figure figure={tiles.revenue} format={money} />
          </div>
          <div style={tileSub}>
            {tiles.revenue.withheld
              ? "Fixed-price work is recognised on delivery"
              : "What the work earns under each CLIN's policy"}
          </div>
        </div>
        <div style={panelStyle}>
          <div style={tileLabel}>Total cost</div>
          <div style={tileNum}>
            <Figure figure={tiles.cost} format={money} />
          </div>
          <div style={tileSub}>
            {!tiles.cost.withheld
              ? "Hours burdened through the indirect pools"
              : margin
                ? "Some categories still price at the billing rate"
                : "Add direct rates to separate cost from billings"}
          </div>
        </div>
        <div style={panelStyle}>
          <div style={tileLabel}>{pricing.earnings}</div>
          <div style={tileNum}>
            <Figure figure={tiles.fee} format={money} />
          </div>
          <div style={tileSub}>
            {pricing.earningsApplicable === false
              ? "Not applicable under this contract's pricing policy"
              : "Revenue less cost, under each CLIN's policy"}
          </div>
        </div>
        <div style={panelStyle}>
          <div style={tileLabel}>{pricing.return}</div>
          <div
            style={{
              ...tileNum,
              color:
                tiles.margin.value != null && tiles.margin.value < 0
                  ? "var(--bad)"
                  : "var(--text)",
            }}
          >
            <Figure figure={tiles.margin} format={pct} />
          </div>
          <div style={tileSub}>
            {pricing.returnApplicable === false
              ? "Not applicable under this contract's pricing policy"
              : pricing.return === "Fee margin"
                ? "Earned fee as a share of revenue"
                : "Earnings as a share of revenue"}
          </div>
        </div>
      </div>

      {/* Why the tiles above are dashes. Two different states land here and they take
          different fixes: no rate ladder at all, or a ladder that doesn't reach every
          category (#152). The second one is the easier to mistake for a bug, because
          the contract *is* at level 2 and most of the view is populated. */}
      {tiles.cost.withheld && (
        <div
          style={{
            ...panelStyle,
            marginTop: 14,
            fontSize: 13,
            color: "var(--dim)",
            lineHeight: 1.55,
          }}
        >
          <strong style={{ color: "var(--text)" }}>
            Margin is withheld on this contract, not missing.
          </strong>{" "}
          {margin ? (
            <>
              This contract has an indirect buildup and some direct rates, but not every
              labor category is priced from one — the CLINs below name which. Where a
              category falls back, Runway prices the hour at its burdened billing rate,
              so a contract total mixing the two is part cost and part billings and a
              margin off it is arithmetic rather than a fact. The CLINs that <em>are</em>{" "}
              fully priced keep their own cost and margin; add the missing direct rates
              under <em>Indirect Rates</em> and the contract totals fill in.
            </>
          ) : (
            <>
              At cost-model level 1 Runway knows one number per labor hour — the burdened
              billing rate off the rate schedule — so cost and billings are equal by
              construction, and any margin read off them would be 0% by arithmetic rather
              than by fact. Enter direct rates and indirect pools under{" "}
              <em>Indirect Rates</em> to reach level 2 and this view fills in.
              {tiles.revenue.withheld
                ? " The funding read below is correct at every level."
                : " Revenue and the funding read below are correct at every level."}
            </>
          )}
        </div>
      )}

      {/* The refusal a user cannot fix by entering anything (#154), so it gets its own
          box rather than a line inside the rates explanation — the fix is a feature
          Runway does not have yet, and pointing at the rates form would be a wrong
          instruction rather than an incomplete one. */}
      {tiles.revenue.withheld && (
        <div
          style={{
            ...panelStyle,
            marginTop: 14,
            fontSize: 13,
            color: "var(--dim)",
            lineHeight: 1.55,
          }}
        >
          <strong style={{ color: "var(--text)" }}>
            Fixed-price revenue is withheld until there is a delivery to recognise it
            against.
          </strong>{" "}
          A firm price is earned on delivery (FAR 16.202) — hours charged do not earn
          it — and Runway records no milestones, deliverables or acceptances. So it
          recognises none of the price rather than all of it: a contract price sitting
          in a revenue tile reports an award as fully earned on the day it lands, and
          the margin under it shrinks as the work actually gets done. The price, the
          cost against it and where that cost lands at PoP end are in each fixed-price
          CLIN's at-completion position below, and the funding read is unaffected.
        </div>
      )}

      {/* ---- By CLIN ------------------------------------------------------ */}
      <div style={{ ...panelStyle, marginTop: 22, padding: 0, overflow: "hidden" }}>
        <div style={{ padding: "16px 18px 12px" }}>
          <div style={{ fontFamily: grotesk, fontSize: 15, fontWeight: 700, color: "var(--text)" }}>
            By CLIN
          </div>
          <div style={{ fontSize: 12, color: "var(--dim)", marginTop: 3 }}>
            Each line under its own pricing policy — on a mixed award the margin and the
            funding risk live on different CLINs.
          </div>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 900 }}>
            <thead>
              <tr>
                <th style={thLeft}>CLIN</th>
                <th style={thLeft}>Policy</th>
                <th style={th}>Price / limit</th>
                <th style={th}>Revenue</th>
                <th style={th}>Cost</th>
                <th style={th}>Earnings</th>
                <th style={th}>Return</th>
                <th style={thLeft}>At completion</th>
                <th style={thLeft}>Status</th>
              </tr>
            </thead>
            <tbody>
              {clins.map((c) => {
                const f = clinFigures(c, margin);
                const applicability = pricingApplicability(c);
                const proj = projection(c);
                const p = pill(
                  c.status,
                  c.ceiling_breached,
                  c.funds_exceeded,
                  c.margin_managed,
                  c.fee_exhausted,
                  c.ceiling_is_price,
                );
                return (
                  <tr key={c.code}>
                    <td style={tdLeft}>
                      <div style={{ fontWeight: 600 }}>{c.code}</div>
                      <div style={{ fontSize: 11.5, color: "var(--dim)" }}>{c.name}</div>
                    </td>
                    <td style={{ ...tdLeft, fontSize: 12.5 }}>
                      <div>{c.pricing_policy?.label || "Not stated"}</div>
                      <div style={{ fontSize: 11, color: "var(--faint)" }}>
                        measured in {measuredIn(c)}
                      </div>
                    </td>
                    <td style={td}>
                      <div>{money(c.ceiling)}</div>
                      <div style={{ fontSize: 10.5, color: "var(--faint)", fontFamily: "inherit" }}>
                        {applicability.ceilingLabel}
                      </div>
                    </td>
                    <td style={td}>
                      <Figure figure={f.revenue} format={money} />
                    </td>
                    <td style={td}>
                      <Figure figure={f.cost} format={money} />
                    </td>
                    <td style={td}>
                      <Figure figure={f.fee} format={money} />
                      <div style={{ fontSize: 10.5, color: "var(--faint)", fontFamily: "inherit" }}>
                        {applicability.earningsLabel}
                      </div>
                    </td>
                    <td
                      style={{
                        ...td,
                        color:
                          f.margin.value != null && f.margin.value < 0
                            ? "var(--bad)"
                            : "var(--text)",
                      }}
                    >
                      <Figure figure={f.margin} format={pct} />
                      <div style={{ fontSize: 10.5, color: "var(--faint)", fontFamily: "inherit" }}>
                        {applicability.returnLabel}
                      </div>
                    </td>
                    <td style={{ ...tdLeft, fontSize: 12.5 }}>
                      {proj ? (
                        <>
                          <div
                            style={{
                              fontFamily: mono,
                              color: proj.eroding ? "var(--warn)" : "var(--text)",
                            }}
                          >
                            {money(proj.value)}
                          </div>
                          <div style={{ fontSize: 11, color: "var(--faint)" }}>
                            {proj.kind === "margin"
                              ? `Cost at PoP end · margin ${
                                  proj.marginPct == null ? "—" : pct(proj.marginPct)
                                }`
                              : proj.absorbed
                                ? `Fee at completion · ${money(proj.absorbed)} absorbed`
                                : "Fee at completion"}
                          </div>
                        </>
                      ) : (
                        <span
                          style={{ color: "var(--faint)", fontFamily: mono }}
                          title={projectionReason(c)}
                        >
                          —
                        </span>
                      )}
                    </td>
                    <td style={tdLeft}>
                      <span style={{ ...p.style, marginLeft: 0, display: "inline-block" }}>
                        {p.label}
                      </span>
                      {c.runway_days != null && (
                        <div
                          style={{ fontSize: 11, marginTop: 4, color: statusColor(c.status) }}
                        >
                          {c.runway_days} days of runway
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* ---- The buildup, expanded ---------------------------------------- */}
      {chain && (
        <div style={{ ...panelStyle, marginTop: 22 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            <div style={{ fontFamily: grotesk, fontSize: 15, fontWeight: 700, color: "var(--text)" }}>
              The buildup
            </div>
            <div style={{ fontSize: 12, color: "var(--dim)" }}>
              FY {chain.fiscalYear} · {chain.scope === "contract" ? "contract rates" : "company default rates"} ·{" "}
              <span style={{ color: chain.provisional ? "var(--warn)" : "var(--good)" }}>
                {chain.provisional ? "provisional" : "final"}
              </span>
            </div>
          </div>
          <div style={{ fontSize: 12, color: "var(--dim)", marginTop: 3, maxWidth: 760 }}>
            Each rate with the base it applies to, so the arithmetic can be checked
            against your own books rather than trusted.
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 14 }}>
            <div style={{ fontSize: 12.5, color: "var(--text)" }}>
              <strong>Direct labor</strong>
              <span style={{ color: "var(--dim)" }}> — hours × the direct rate for each category</span>
            </div>
            {chain.steps.map((s) => (
              <div
                key={s.name}
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: 10,
                  flexWrap: "wrap",
                  fontSize: 12.5,
                  paddingLeft: 14,
                  borderLeft: "2px solid var(--border)",
                }}
              >
                <span style={{ fontWeight: 600, minWidth: 76 }}>{s.label}</span>
                <span style={{ fontFamily: mono, color: "var(--text)" }}>{pct(s.rate)}</span>
                <span style={{ color: "var(--dim)" }}>on {s.baseLabel}</span>
                {s.status && s.status !== "final" && (
                  <span style={{ fontSize: 11, color: "var(--warn)" }}>{s.status}</span>
                )}
              </div>
            ))}
            <div style={{ fontSize: 12.5, color: "var(--text)" }}>
              <strong>= Cost</strong>
              <span style={{ color: "var(--dim)" }}>
                {" "}
                — then fee on top, per each CLIN's pricing policy, gives revenue
              </span>
            </div>
          </div>

          {chain.provisional && (
            <div style={{ marginTop: 14, fontSize: 12, color: "var(--dim)", lineHeight: 1.55, maxWidth: 700 }}>
              These are provisional billing rates. Actual indirect rates are not known
              until the books close, and the difference reprices every hour already
              charged — so every cost, fee and margin figure on this page moves at the
              year-end true-up.
            </div>
          )}

          {!chain.complete && (
            <div style={{ marginTop: 10, fontSize: 12, color: "var(--warn)" }}>
              The buildup is missing a pool, so the cost below it is partial.
            </div>
          )}

          {/* How the hours were actually priced — which tier answered, and for how
              many hours. A CLIN that is mostly category-costed with a fallback tail
              is a real state that one dominant label would hide. */}
          {clins.some((c) => (c.cost_rate_mix || []).length > 0) && (
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
              <div style={{ fontSize: 12, color: "var(--dim)", marginBottom: 8 }}>
                How the hours were priced
              </div>
              {clins
                .filter((c) => (c.cost_rate_mix || []).length > 0)
                .map((c) => (
                  <div key={c.code} style={{ fontSize: 12.5, marginBottom: 5 }}>
                    <span style={{ fontWeight: 600 }}>{c.code}</span>
                    <span style={{ color: "var(--dim)" }}>
                      {" — "}
                      {pricedBy(c)
                        .map((m) => `${Math.round(m.hours).toLocaleString()} hrs at ${m.label.toLowerCase()}`)
                        .join(" · ")}
                    </span>
                    {c.blended_rate != null && (
                      <span style={{ color: "var(--faint)", fontFamily: mono }}>
                        {" · blended "}
                        {money(c.blended_rate)}/hr
                      </span>
                    )}
                  </div>
                ))}
            </div>
          )}

          {/* Reconciliation: what the buildup derives per LCAT against what the award
              negotiated. The engine computes this; the section exists to show it. */}
          {variance.length > 0 && (
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
              <div style={{ fontSize: 12, color: "var(--dim)", marginBottom: 8 }}>
                Derived vs. negotiated rate
              </div>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 620 }}>
                  <thead>
                    <tr>
                      <th style={thLeft}>LCAT</th>
                      <th style={thLeft}>CLIN</th>
                      <th style={th}>Built up</th>
                      <th style={th}>Negotiated</th>
                      <th style={th}>Delta</th>
                    </tr>
                  </thead>
                  <tbody>
                    {variance.map((v, i) => (
                      <tr key={`${v.code}-${v.lcat}-${i}`}>
                        <td style={{ ...tdLeft, fontSize: 12.5 }}>{v.lcat}</td>
                        <td style={{ ...tdLeft, fontSize: 12.5, color: "var(--dim)" }}>{v.code}</td>
                        <td style={td}>${v.derived_price?.toFixed(2)}</td>
                        <td style={td}>${v.negotiated_rate?.toFixed(2)}</td>
                        <td
                          style={{
                            ...td,
                            color: v.direction === "above_buildup" ? "var(--good)" : "var(--warn)",
                          }}
                          title={
                            v.direction === "above_buildup"
                              ? "The award pays more than the buildup costs — margin on this category."
                              : "The buildup costs more than the award pays on this category."
                          }
                        >
                          {v.direction === "above_buildup" ? "+" : "−"}${Math.abs(v.delta).toFixed(2)}
                          {v.pct != null ? ` (${pct(Math.abs(v.pct))})` : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ---- Fee at risk --------------------------------------------------- */}
      {fee.length > 0 && (
        <div style={{ marginTop: 26 }}>
          <div style={{ fontFamily: grotesk, fontSize: 15, fontWeight: 700, color: "var(--text)" }}>
            Fee at risk
          </div>
          <div style={{ fontSize: 12, color: "var(--dim)", marginTop: 3, maxWidth: 760 }}>
            What the award promised in fee, what the work has earned of it, and what the
            current overrun is costing. On a cost-type CLIN the overrun eats fee before
            it reaches the government's money, so this is the loss that otherwise stays
            invisible until year end.
          </div>

          {fee.map((c) => {
            const fp = c.fee_position;
            const gap = feeGap(fp);
            const now = feeFigures(fp);
            const proj = fp.projected ? feeFigures(fp.projected) : null;
            const award = awardPeriods(fp);
            const share = shareRatio(fp);
            // A warning colour is a claim as much as a number is. The projected
            // absorption is computed off cost, so tone it only where the projection's
            // own truth flags say those figures are real (#153) — an amber "at risk"
            // over an em dash reads as a loss nobody can check.
            const projTrusted = proj != null && proj.atCompletion.withheld == null;
            const losing =
              projTrusted && (fp.projected.absorbed > 0 || fp.projected.exhausted);
            return (
              <div key={c.code} style={{ ...panelStyle, marginTop: 14 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                  <div style={{ fontWeight: 700, fontSize: 13.5 }}>{c.code}</div>
                  <div style={{ fontSize: 12.5, color: "var(--dim)" }}>
                    {feeBasisLabel(fp)}
                    {fp.clause
                      ? ` · FAR ${fp.clause}${clauseTitle(fp.clause) ? ` (${clauseTitle(fp.clause)})` : ""}`
                      : ""}
                  </div>
                  {fp.exhausted && (
                    <span style={{ fontSize: 11, fontWeight: 700, color: "var(--bad)" }}>
                      Fee exhausted
                    </span>
                  )}
                </div>

                {gap && (
                  <div
                    style={{
                      marginTop: 10,
                      fontSize: 12.5,
                      color: "var(--dim)",
                      lineHeight: 1.5,
                      background: "var(--panel2)",
                      border: "1px solid var(--border)",
                      borderRadius: 10,
                      padding: "9px 12px",
                    }}
                  >
                    {gap.message}
                  </div>
                )}

                <div style={{ display: "flex", flexWrap: "wrap", gap: 22, marginTop: 14 }}>
                  <FeeStat label="Fee target" figure={now.target} />
                  <FeeStat label="Earned to date" figure={now.earned} />
                  <FeeStat
                    label="At completion"
                    figure={proj ? proj.atCompletion : now.atCompletion}
                    tone={losing ? "var(--warn)" : undefined}
                  />
                  <FeeStat
                    label="vs. target"
                    figure={proj ? proj.delta : now.delta}
                    tone={
                      (proj ? proj.delta.value : now.delta.value) < 0 ? "var(--bad)" : undefined
                    }
                  />
                  <FeeStat
                    label="At risk"
                    figure={proj ? proj.atRisk : now.atRisk}
                    tone={losing ? "var(--warn)" : undefined}
                  />
                  <FeeStat
                    label="Absorbed by overrun"
                    figure={proj ? proj.absorbed : now.absorbed}
                    tone={losing ? "var(--bad)" : undefined}
                  />
                </div>

                {/* The 52.216-8 pair. Earned is not the same as payable, and the gap
                    between them is the clause working as intended rather than a
                    problem — kept out of the row above so it doesn't read as a loss. */}
                {(now.withhold.value != null || now.collectable.value != null) && (
                  <div
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      gap: 22,
                      marginTop: 16,
                      paddingTop: 14,
                      borderTop: "1px solid var(--border)",
                    }}
                  >
                    <FeeStat label="Withheld" figure={now.withhold} />
                    <FeeStat label="Collectable now" figure={now.collectable} />
                    {fp.provisional && (
                      <div style={{ fontSize: 11.5, color: "var(--dim)", alignSelf: "center", maxWidth: 320 }}>
                        Billed provisionally at the target rate and adjusted at
                        completion.
                      </div>
                    )}
                  </div>
                )}

                {share && (
                  <div style={{ marginTop: 16, fontSize: 12.5, color: "var(--dim)", lineHeight: 1.55 }}>
                    Share ratio{" "}
                    <strong style={{ color: "var(--text)", fontFamily: mono }}>
                      {share.raw || (share.contractor != null ? pct(share.contractor) : "—")}
                    </strong>
                    {share.contractor != null
                      ? ` — the contractor absorbs ${pct(share.contractor)} of every overrun dollar.`
                      : ""}
                    {share.pta != null && (
                      <>
                        {" "}
                        Point of total assumption{" "}
                        <strong style={{ color: "var(--text)", fontFamily: mono }}>
                          {money(share.pta)}
                        </strong>
                        — above that cost the contractor absorbs every additional dollar.
                      </>
                    )}
                  </div>
                )}

                {award && (
                  <>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 22, marginTop: 16 }}>
                      {award.pool != null && (
                        <FeeStat label="Award pool" figure={{ value: award.pool, withheld: null }} />
                      )}
                      {award.baseEarned != null && (
                        <FeeStat label="Base fee earned" figure={{ value: award.baseEarned, withheld: null }} />
                      )}
                      {award.earned != null && (
                        <FeeStat label="Award fee earned" figure={{ value: award.earned, withheld: null }} />
                      )}
                      {award.available != null && award.periodsRecorded && (
                        <FeeStat
                          label="Still available"
                          figure={{ value: award.available, withheld: null }}
                          tone="var(--warn)"
                        />
                      )}
                    </div>
                    {!award.periodsRecorded && (
                      <div style={{ marginTop: 12, fontSize: 12.5, color: "var(--dim)", lineHeight: 1.55, maxWidth: 620 }}>
                        No award-fee evaluation periods are recorded, so none of this
                        pool can be earned yet. That is why nothing shows as available —
                        the pool is unallocated, not spent. Record the periods to see
                        the fee position move.
                      </div>
                    )}
                    <AwardPeriods award={award} />
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
