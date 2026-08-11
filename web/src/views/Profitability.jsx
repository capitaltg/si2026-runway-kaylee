import React, { useEffect, useMemo, useState } from "react";
import { getBurn, listContracts } from "../api.js";
import { money, pct, panelStyle, pill, statusColor } from "../format.js";
import {
  clinFigures,
  marginAvailable,
  measuredIn,
  orderedClins,
  projection,
  projectionReason,
  summary,
} from "../profitability.js";

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
const Figure = ({ figure, format }) =>
  figure.withheld ? (
    <span style={{ color: "var(--faint)", fontFamily: mono }} title={figure.withheld}>
      —
    </span>
  ) : (
    format(figure.value)
  );

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
    </div>
  );
}
