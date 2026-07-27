import React from "react";
import { moneyM } from "../format.js";

// Burn-vs-pace chart for one CLIN. Geometry ported verbatim from the design's
// buildChart (docs/design/Runway.dc.html): a straight actual line origin →
// (current week, spent), a dashed target-pace line to the ceiling at PoP end,
// and a projected line from today to the point funds run out. Colors are CSS
// vars so the chart tracks the light/dark theme.
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
  const funded = clin.incrementally_funded ? budget : null;
  // The pace line targets the money you actually have to stay under: the funded
  // amount when incrementally funded, otherwise the ceiling. Staying below it
  // means the burn lasts the whole period without running dry.
  const target = funded != null ? funded : ceiling;

  const ymax = (ceiling || 1) * 1.12;
  const mx = (w) => padL + plotW * (w / tot);
  const my = (v) => padT + plotH * (1 - v / ymax);
  const baseY = my(0);

  const projEndWeek = paused ? cw : Math.min(exhaustWeek ?? tot, tot);
  const projEndVal = spent + weekly * (projEndWeek - cw);
  const exhaustShow = !paused && exhaustWeek != null && exhaustWeek <= tot;
  const exX = mx(Math.min(exhaustWeek ?? tot, tot));
  const projColor =
    clin.status === "over"
      ? "var(--bad)"
      : clin.status === "watch"
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
  const yTicks = [0, 0.5, 1].map((k) => ({
    y: my(ceiling * k).toFixed(1),
    ty: (my(ceiling * k) + 4).toFixed(1),
    label: moneyM(ceiling * k),
  }));

  const grid = "var(--grid)";

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      style={{ width: "100%", height: "auto", marginTop: 10, display: "block" }}
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
          <line x1="0" y1="0" x2="0" y2="9" stroke="var(--warn)" strokeWidth="1" opacity="0.3" />
        </pattern>
      </defs>

      {yTicks.map((tk, i) => (
        <g key={`y${i}`}>
          <line x1="70" y1={tk.y} x2="1150" y2={tk.y} stroke={grid} strokeWidth="1" />
          <text x="60" y={tk.ty} textAnchor="end" fontSize="12" fill="var(--faint)">
            {tk.label}
          </text>
        </g>
      ))}
      {xTicks.map((xt, i) => (
        <text key={`x${i}`} x={xt.x} y="405" textAnchor="middle" fontSize="12" fill="var(--faint)">
          {xt.label}
        </text>
      ))}

      {/* unfunded band — the gap between the funded dollars and the ceiling:
          money the contract is authorized for but not obligated yet, spendable
          only once a funding mod lands. A light hatch (not a heavy block) marks
          it as provisional; the amount rides a muted right-edge caption that
          lines up with the Ceiling / Funded labels. */}
      {funded != null && (
        <>
          <rect
            x="70"
            y={my(ceiling).toFixed(1)}
            width="1080"
            height={(my(funded) - my(ceiling)).toFixed(1)}
            fill="url(#rwUnfunded)"
          />
          <text
            x="1146"
            y={((my(ceiling) + my(funded)) / 2 + 4).toFixed(1)}
            textAnchor="end"
            fontSize="11"
            fill="var(--dim)"
          >
            unfunded · {moneyM(ceiling - funded)}
          </text>
        </>
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
      <text
        x="1146"
        y={(my(ceiling) - 8).toFixed(1)}
        textAnchor="end"
        fontSize="12"
        fill="var(--bad)"
        fontWeight="600"
      >
        Ceiling {moneyM(ceiling)}
      </text>

      {/* funded line — the obligated dollars actually backing this CLIN. Below
          the ceiling when incrementally funded; it's the line the burn runs out
          at (FAR 52.232-22). */}
      {funded != null && (
        <>
          <line
            x1="70"
            y1={my(funded).toFixed(1)}
            x2="1150"
            y2={my(funded).toFixed(1)}
            stroke="var(--warn)"
            strokeWidth="1.5"
            strokeDasharray="4 5"
          />
          <text
            x="1146"
            y={(my(funded) - 8).toFixed(1)}
            textAnchor="end"
            fontSize="12"
            fill="var(--warn)"
            fontWeight="600"
          >
            Funded {moneyM(funded)}
          </text>
        </>
      )}

      {/* fills */}
      <path d={`M${x0},${baseY.toFixed(1)} L${xc},${yc} L${xc},${baseY.toFixed(1)} Z`} fill="url(#rwActualFill)" />
      {!paused && (
        <path
          d={`M${xc},${yc} L${pe},${pv} L${pe},${baseY.toFixed(1)} L${xc},${baseY.toFixed(1)} Z`}
          fill="url(#rwProjFill)"
        />
      )}

      {/* today marker */}
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
      <text x={xc} y="44" textAnchor="middle" fontSize="11.5" fill="var(--dim)">
        today · wk {cw}
      </text>

      {/* pace / actual / projected. The pace line runs to the funded target
          (the money in hand), not the ceiling — that's the line to stay under. */}
      <path
        d={`M${x0},${baseY.toFixed(1)} L${mx(tot).toFixed(1)},${my(target).toFixed(1)}`}
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
      {!paused && (
        <path
          d={`M${xc},${yc} L${pe},${pv}`}
          fill="none"
          stroke={projColor}
          strokeWidth="3.5"
          strokeLinecap="round"
          strokeDasharray="2 7"
          style={{ animation: "rwdash 1s linear infinite" }}
        />
      )}
      <circle cx={xc} cy={yc} r="5.5" fill="var(--accent)" stroke="var(--panel)" strokeWidth="2.5" />

      {exhaustShow && (
        <>
          <circle
            cx={exX.toFixed(1)}
            cy={my(budget).toFixed(1)}
            r="6.5"
            fill="var(--bad)"
            stroke="var(--panel)"
            strokeWidth="2.5"
          />
          <text
            x={(exX > W - 260 ? exX - 10 : exX + 10).toFixed(1)}
            y={(my(budget) + 20).toFixed(1)}
            textAnchor={exX > W - 260 ? "end" : "start"}
            fontSize="12.5"
            fill="var(--bad)"
            fontWeight="700"
          >
            Funds exhaust · wk {Math.round(exhaustWeek)}
          </text>
        </>
      )}
    </svg>
  );
}
