import React from "react";
import { moneyM } from "../format.js";

// Burn-vs-pace chart for one CLIN. Geometry ported from the design's buildChart
// (docs/design/Runway.dc.html): a straight actual line origin → (current week,
// spent), a dashed target-pace line at the ceiling slope, and a projected line
// from today to the point funds run out.
//
// The plot itself carries no prose — every reading (amounts, weeks, what each
// line means) lives in the footer legend below it. Annotations pinned to the
// geometry piled into the top-right corner and became illegible as soon as two
// of them landed near each other, and SVG text scales with the viewBox, so it
// went blurry on narrow screens. Colors are CSS vars so the chart tracks theme.
export default function BurnChart({ clin, contract }) {
  const W = 1180,
    H = 420,
    padL = 70,
    padR = 30,
    padT = 28,
    padB = 52;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const tot = contract.total_weeks || 52;
  const cw = contract.current_week || 0;
  const ceiling = clin.ceiling || 0;
  const spent = clin.spent || 0;
  const weekly = clin.weekly || 0;
  const exhaustWeek = clin.exhaust_week;
  const paused = clin.status === "paused";
  // The binding budget the runway is measured against. When the CLIN is
  // incrementally funded it's the funded slice (below the ceiling), and that's
  // the line the projection runs out at — so the "funds exhaust" marker sits on
  // it, not on the full ceiling.
  const budget = clin.budget != null ? clin.budget : ceiling;
  // A funded line at (or above) the ceiling is redundant with the ceiling line.
  const funded = clin.incrementally_funded && budget < ceiling ? budget : null;

  // The money already ran out: spend is at or past the binding budget, so the
  // "exhaust week" the backend reports is in the *past*. Everything forward-
  // looking (projection, ghost, exhaust marker) is meaningless here — projecting
  // from today to a week behind today drew the line backwards across the plot.
  // This state gets its own geometry: actual line only, with the week the burn
  // crossed the limit marked behind us.
  const overBudget = spent >= budget && budget > 0;
  const overBy = spent - budget;
  // Where the (straight, origin-anchored) actual line crossed the budget line.
  const crossWeek = overBudget && spent > 0 ? (cw * budget) / spent : null;

  // Scale to whichever is tallest. An overrun puts spent above the ceiling, and
  // clamping ymax to the ceiling pushed the actual line off the top of the plot.
  const ymax = Math.max(ceiling, spent, budget, 1) * 1.12;
  const mx = (w) => padL + plotW * (w / tot);
  const my = (v) => padT + plotH * (1 - v / ymax);
  const baseY = my(0);

  // The pace line keeps the ceiling slope (ceiling / tot — the real "burn evenly
  // across the period" rate) and stops where the funded dollars run out, rather
  // than sloping down to the funded amount at PoP end. A shallower funded slope
  // would imply the funded slice is meant to cover the whole PoP; incremental
  // funding is meant to carry you to the next funding mod. At ceiling slope the
  // line crosses y = funded exactly at funded_frac * tot, so the truncation
  // point *is* the crossing point — the funding horizon falls out of the geometry.
  const fundedFrac =
    funded != null && ceiling > 0
      ? clin.funded_frac != null
        ? clin.funded_frac
        : funded / ceiling
      : null;
  const paceEndWeek =
    fundedFrac != null ? Math.min(fundedFrac * tot, tot) : tot;
  const paceEndVal = fundedFrac != null ? funded : ceiling;

  // A projection only exists if there's runway left ahead of us. Clamped to
  // today at the low end so a stale or past exhaust_week can never invert it.
  const projects = !paused && !overBudget;
  const projEndWeek = projects
    ? Math.max(cw, Math.min(exhaustWeek ?? tot, tot))
    : cw;
  const projEndVal = spent + weekly * (projEndWeek - cw);

  // The projection bends around known absence (#85) — but only when the server
  // sent a per-week series for this CLIN, which it does only when there is absence
  // in the weeks still ahead. `bend` is null on every other contract, and when it's
  // null every line below is the exact straight-line geometry this chart has always
  // drawn. That is the whole safety story: the series is additive, and the fallback
  // is the original. It is also withheld server-side in the states this file already
  // special-cases (paused, over budget, past PoP), so a bent line can never appear
  // where the straight one refused to draw.
  const bend = (() => {
    const p = clin.projection;
    if (!projects || !p || !(p.points?.length > 1)) return null;
    const pts = p.points.map((q) => [mx(q.week), my(q.spent)]);
    return {
      d: pts
        .map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`)
        .join(" "),
      endX: pts[pts.length - 1][0].toFixed(1),
      endVal: p.points[p.points.length - 1].spent,
      exhaustWeek: p.exhaust_week,
      weeksGained: p.weeks_gained,
      weeksAffected: p.weeks_affected,
    };
  })();

  // The week the money runs out on whichever line is actually being drawn. The
  // marker has to sit on the end of the line the user is looking at, or the chart
  // says one thing and the dot says another.
  const runOutWeek = bend?.exhaustWeek ?? exhaustWeek;
  const exhaustShow =
    projects && runOutWeek != null && runOutWeek <= tot && runOutWeek >= cw;
  const exX = mx(Math.min(runOutWeek ?? tot, tot));
  // Where the burn would land if a funding mod arrived: same weekly rate carried
  // on from the exhaust point, capped at the ceiling and at PoP end.
  const ghost = (() => {
    if (!exhaustShow || weekly <= 0 || ceiling - budget <= 0) return null;
    const week = Math.min(runOutWeek + (ceiling - budget) / weekly, tot);
    if (week - runOutWeek < 0.5) return null;
    return { week, val: budget + weekly * (week - runOutWeek) };
  })();

  const projColor = overBudget
    ? "var(--bad)"
    : clin.status === "over"
      ? "var(--bad)"
      : // `fee_eroding` (#81) is amber like `watch`. The projection line is the thing
        // that eats the fee, so drawing it green while the card says the fee is going
        // makes the chart contradict its own pill.
        clin.status === "watch" || clin.status === "fee_eroding"
        ? "var(--warn)"
        : "var(--good)";

  const x0 = mx(0),
    yc = my(spent),
    xc = mx(cw),
    pe = mx(projEndWeek),
    pv = my(projEndVal);

  // Quartered week axis, scaled to the real PoP length (design shows 0/13/…/52).
  const xTicks = [0, 0.25, 0.5, 0.75, 1].map((k) => {
    const w = Math.round(tot * k);
    return { x: mx(w).toFixed(1), label: "wk " + w };
  });
  // The money axis is scaled off the ceiling, so its top tick already labels the
  // ceiling. The funded amount gets its own tick in the same gutter, in the
  // funded line's color — that's the value you're actually measured against, and
  // reading it off the side beats pinning it inside the plot. When it lands on
  // top of a regular tick the regular one loses its label (the gridline stays):
  // funded is the more useful number, and the ceiling is also in the footer.
  const fundedY = funded != null ? my(funded) : null;
  const yTicks = [0, 0.5, 1].map((k) => {
    const y = my(ceiling * k);
    return {
      y: y.toFixed(1),
      ty: (y + 4).toFixed(1),
      label: moneyM(ceiling * k),
      hideLabel: fundedY != null && Math.abs(y - fundedY) < 12,
    };
  });

  const grid = "var(--grid)";

  // Footer legend — one row per thing drawn, carrying the numbers that used to
  // be pinned to the geometry. Order follows the reading order of the chart:
  // what happened, what should happen, what will happen, what bounds it.
  const legend = [
    {
      dash: "solid",
      color: "var(--accent)",
      label: "Actual",
      value: `${moneyM(spent)} through wk ${cw}`,
    },
    {
      dash: "dashed",
      color: "var(--faint)",
      label: funded != null ? "Pace to stay funded" : "Target pace",
      value:
        fundedFrac != null
          ? `funded through ~wk ${Math.round(paceEndWeek)}`
          : `${moneyM(ceiling)} at wk ${tot}`,
    },
    paused
      ? {
          dash: "dotted",
          color: "var(--dim)",
          label: "Projected",
          value: "paused — no burn",
        }
      : overBudget
        ? {
            dash: "dotted",
            color: "var(--bad)",
            label: "Funds ran out",
            value: `~wk ${Math.round(crossWeek ?? cw)} · ${moneyM(overBy)} over ${
              funded != null ? "funded" : "ceiling"
            }`,
          }
        : exhaustShow
          ? {
              dash: "dotted",
              color: projColor,
              label: "Projected",
              value: `funds exhaust wk ${Math.round(runOutWeek)}`,
            }
          : {
              dash: "dotted",
              color: projColor,
              label: "Projected",
              value: `${moneyM(bend ? bend.endVal : projEndVal)} by wk ${tot}`,
            },
    // Say that the line bends and why (#85). A kink in the projection with nothing
    // explaining it reads as a rendering bug, and an accountant checking the
    // arithmetic by hand needs to know the projection is not hrs/wk x weeks. The
    // gain is stated in whole days because that is the unit runway is read in.
    bend && {
      dash: "dotted",
      color: projColor,
      label: "Includes absence",
      value:
        `${bend.weeksAffected} wk${bend.weeksAffected === 1 ? "" : "s"} affected` +
        (bend.weeksGained
          ? ` · ${bend.weeksGained > 0 ? "+" : ""}${Math.round(bend.weeksGained * 7)} days runway`
          : ""),
    },
    ghost && {
      dash: "dotted",
      color: "var(--dim)",
      label: "With a funding mod",
      value: `ceiling ~wk ${Math.round(ghost.week)}`,
    },
    {
      dash: "dashed",
      color: "var(--bad)",
      label: "Ceiling",
      value: moneyM(ceiling),
    },
    funded != null && {
      dash: "dashed",
      color: "var(--warn)",
      label: "Funded",
      value: `${moneyM(funded)} · ${moneyM(ceiling - funded)} unfunded`,
    },
  ].filter(Boolean);

  return (
    <>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{
          width: "100%",
          height: "auto",
          marginTop: 10,
          display: "block",
        }}
        fontFamily="IBM Plex Mono, monospace"
      >
        <defs>
          <linearGradient id="rwActualFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--accent)" stopOpacity="0.28" />
            <stop offset="1" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="rwProjFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={projColor} stopOpacity="0.22" />
            <stop offset="1" stopColor={projColor} stopOpacity="0" />
          </linearGradient>
          {/* Light diagonal hatch marks the "unfunded" zone as provisional money —
              reads as not-yet-solid without a heavy fill or inline text. */}
          <pattern
            id="rwUnfunded"
            width="9"
            height="9"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <line
              x1="0"
              y1="0"
              x2="0"
              y2="9"
              stroke="var(--warn)"
              strokeWidth="1"
              opacity="0.3"
            />
          </pattern>
        </defs>

        {yTicks.map((tk, i) => (
          <g key={`y${i}`}>
            <line
              x1="70"
              y1={tk.y}
              x2="1150"
              y2={tk.y}
              stroke={grid}
              strokeWidth="1"
            />
            {!tk.hideLabel && (
              <text
                x="60"
                y={tk.ty}
                textAnchor="end"
                fontSize="12"
                fill="var(--faint)"
              >
                {tk.label}
              </text>
            )}
          </g>
        ))}
        {xTicks.map((xt, i) => (
          <text
            key={`x${i}`}
            x={xt.x}
            y="405"
            textAnchor="middle"
            fontSize="12"
            fill="var(--faint)"
          >
            {xt.label}
          </text>
        ))}
        {fundedY != null && (
          <text
            x="60"
            y={(fundedY + 4).toFixed(1)}
            textAnchor="end"
            fontSize="12"
            fill="var(--warn)"
            fontWeight="600"
          >
            {moneyM(funded)}
          </text>
        )}

        {/* unfunded band — the gap between the funded dollars and the ceiling:
            money the contract is authorized for but not obligated yet, spendable
            only once a funding mod lands. A light hatch (not a heavy block) marks
            it as provisional. */}
        {funded != null && (
          <rect
            x="70"
            y={my(ceiling).toFixed(1)}
            width="1080"
            height={(my(funded) - my(ceiling)).toFixed(1)}
            fill="url(#rwUnfunded)"
          />
        )}

        {/* ceiling band + line */}
        <rect
          x="70"
          y="28"
          width="1080"
          height={(my(ceiling) - padT).toFixed(1)}
          fill="var(--bad)"
          opacity="0.06"
        />
        <line
          x1="70"
          y1={my(ceiling).toFixed(1)}
          x2="1150"
          y2={my(ceiling).toFixed(1)}
          stroke="var(--bad)"
          strokeWidth="1.5"
          strokeDasharray="7 6"
        />

        {/* funded line — the obligated dollars actually backing this CLIN. Below
            the ceiling when incrementally funded; it's the line the burn runs out
            at (FAR 52.232-22). */}
        {funded != null && (
          <line
            x1="70"
            y1={my(funded).toFixed(1)}
            x2="1150"
            y2={my(funded).toFixed(1)}
            stroke="var(--warn)"
            strokeWidth="1.5"
            strokeDasharray="4 5"
          />
        )}

        {/* fills */}
        <path
          d={`M${x0},${baseY.toFixed(1)} L${xc},${yc} L${xc},${baseY.toFixed(1)} Z`}
          fill="url(#rwActualFill)"
        />
        {projects && (
          <path
            d={
              bend
                ? `${bend.d} L${bend.endX},${baseY.toFixed(1)} L${xc},${baseY.toFixed(1)} Z`
                : `M${xc},${yc} L${pe},${pv} L${pe},${baseY.toFixed(1)} L${xc},${baseY.toFixed(1)} Z`
            }
            fill="url(#rwProjFill)"
          />
        )}

        {/* today marker — the one caption that stays in the plot: it names a
            position on the x axis, which a legend row can't do, and the top of
            the plot is otherwise empty now so it has nothing to collide with. */}
        <line
          x1={xc}
          y1="28"
          x2={xc}
          y2="368"
          stroke="var(--dim)"
          strokeWidth="1"
          strokeDasharray="3 3"
          opacity=".55"
        />
        <text
          x={xc}
          y="44"
          textAnchor="middle"
          fontSize="11.5"
          fill="var(--dim)"
        >
          today · wk {cw}
        </text>

        {/* pace / actual / projected. The pace line rises at the ceiling slope and
            stops at the funding horizon when incrementally funded (see above). */}
        <path
          d={`M${x0},${baseY.toFixed(1)} L${mx(paceEndWeek).toFixed(1)},${my(paceEndVal).toFixed(1)}`}
          fill="none"
          stroke="var(--faint)"
          strokeWidth="2"
          strokeDasharray="7 6"
        />
        <path
          d={`M${x0},${baseY.toFixed(1)} L${xc},${yc}`}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="3.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {projects && (
          <path
            d={bend ? bend.d : `M${xc},${yc} L${pe},${pv}`}
            fill="none"
            stroke={projColor}
            strokeWidth="3.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray="2 7"
            style={{ animation: "rwdash 1s linear infinite" }}
          />
        )}
        <circle
          cx={xc}
          cy={yc}
          r="5.5"
          fill="var(--accent)"
          stroke="var(--panel)"
          strokeWidth="2.5"
        />

        {/* Ghost continuation: the projection stopping dead on the funded line reads
            as "the contract ends here" rather than "the money ends here." Carry the
            same weekly rate on toward the ceiling, muted and un-animated so it stays
            subordinate to the live projection. */}
        {ghost && (
          <path
            d={`M${exX.toFixed(1)},${my(budget).toFixed(1)} L${mx(ghost.week).toFixed(1)},${my(ghost.val).toFixed(1)}`}
            fill="none"
            stroke="var(--dim)"
            strokeWidth="2"
            strokeLinecap="round"
            strokeDasharray="2 7"
            opacity="0.75"
          />
        )}

        {exhaustShow && (
          <circle
            cx={exX.toFixed(1)}
            cy={my(budget).toFixed(1)}
            r="6.5"
            fill="var(--bad)"
            stroke="var(--panel)"
            strokeWidth="2.5"
          />
        )}

        {/* Already past the limit: mark the week the burn crossed it, behind us. */}
        {crossWeek != null && (
          <circle
            cx={mx(crossWeek).toFixed(1)}
            cy={my(budget).toFixed(1)}
            r="6.5"
            fill="var(--bad)"
            stroke="var(--panel)"
            strokeWidth="2.5"
          />
        )}
      </svg>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "8px 18px",
          marginTop: 12,
          fontSize: 11.5,
          color: "var(--dim)",
        }}
      >
        {legend.map((it) => (
          <span
            key={it.label}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            {it.dash === "solid" ? (
              <span
                style={{
                  width: 16,
                  height: 3,
                  borderRadius: 2,
                  background: it.color,
                }}
              />
            ) : (
              <span
                style={{
                  width: 16,
                  height: 0,
                  borderTop: `2px ${it.dash} ${it.color}`,
                }}
              />
            )}
            <span style={{ color: "var(--text)" }}>{it.label}</span>
            <span>{it.value}</span>
          </span>
        ))}
      </div>
    </>
  );
}
