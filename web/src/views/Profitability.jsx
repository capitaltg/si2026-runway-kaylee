import React, { useEffect, useMemo, useState } from "react";
import { getBurn, listContracts } from "../api.js";
import { money, pct, panelStyle, pill, statusColor } from "../format.js";
import {
  awardPeriods,
  clinFigures,
  feeBasisLabel,
  feeClins,
  feeFigures,
  feeGap,
  marginAvailable,
  measuredIn,
  orderedClins,
  projection,
  projectionReason,
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
  figure.withheld ? (
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
              {p.pool_share != null ? ` · ${pct(p.pool_share)} of pool` : ""}
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
  const [burn, setBurn] = useState(null);
  const [error, setError] = useState(null);

  // Same fallback the Flight Deck and Expenses use: navigating straight here with no
  // contract selected lands on the newest ingested one rather than on nothing.
  useEffect(() => {
    if (contractId || !setActiveId) return;
    listContracts()
      .then((cs) => cs.length && setActiveId(cs[0].id))
      .catch((e) => setError(e.message));
  }, [contractId, setActiveId]);

  useEffect(() => {
    if (!contractId) return;
    let live = true;
    getBurn(contractId)
      .then((b) => live && setBurn(b))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [contractId]);

  const margin = marginAvailable(burn);
  const tiles = useMemo(() => summary(burn), [burn]);
  const clins = useMemo(() => orderedClins(burn), [burn]);
  const fee = useMemo(() => feeClins(burn), [burn]);

  if (error) {
    return <div style={{ padding: 40, color: "var(--bad)", fontSize: 14 }}>{error}</div>;
  }
  if (!burn) {
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
          <div style={tileSub}>What the work earns under each CLIN's policy</div>
        </div>
        <div style={panelStyle}>
          <div style={tileLabel}>Total cost</div>
          <div style={tileNum}>
            <Figure figure={tiles.cost} format={money} />
          </div>
          <div style={tileSub}>
            {margin
              ? "Hours burdened through the indirect pools"
              : "Add direct rates to separate cost from billings"}
          </div>
        </div>
        <div style={panelStyle}>
          <div style={tileLabel}>Fee earned</div>
          <div style={tileNum}>
            <Figure figure={tiles.fee} format={money} />
          </div>
          <div style={tileSub}>Revenue less cost, at both levels</div>
        </div>
        <div style={panelStyle}>
          <div style={tileLabel}>Margin</div>
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
          <div style={tileSub}>Fee as a share of revenue</div>
        </div>
      </div>

      {!margin && (
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
          At cost-model level 1 Runway knows one number per labor hour — the burdened
          billing rate off the rate schedule — so cost and billings are equal by
          construction, and any margin read off them would be 0% by arithmetic rather
          than by fact. Enter direct rates and indirect pools under{" "}
          <em>Indirect Rates</em> to reach level 2 and this view fills in. Revenue and
          the funding read below are correct at every level.
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
                <th style={th}>Ceiling</th>
                <th style={th}>Revenue</th>
                <th style={th}>Cost</th>
                <th style={th}>Fee earned</th>
                <th style={th}>Margin</th>
                <th style={thLeft}>At completion</th>
                <th style={thLeft}>Status</th>
              </tr>
            </thead>
            <tbody>
              {clins.map((c) => {
                const f = clinFigures(c, margin);
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
                    <td style={td}>{money(c.ceiling)}</td>
                    <td style={td}>
                      <Figure figure={f.revenue} format={money} />
                    </td>
                    <td style={td}>
                      <Figure figure={f.cost} format={money} />
                    </td>
                    <td style={td}>
                      <Figure figure={f.fee} format={money} />
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
            const losing = fp.projected?.absorbed > 0 || fp.projected?.exhausted;
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
                      {award.available != null && (
                        <FeeStat
                          label="Still available"
                          figure={{ value: award.available, withheld: null }}
                          tone="var(--warn)"
                        />
                      )}
                    </div>
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
