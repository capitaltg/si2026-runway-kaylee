import React, { useState } from "react";
import { panelStyle, money } from "../format.js";
import {
  BY_COST,
  BY_HOURS,
  COLLAPSED_ROWS,
  heatSummary,
  sortPeople,
  personSentence,
  elsewhereNote,
  expectationNote,
  overtimeNote,
  diagnosisSentence,
  ceilingSentence,
} from "../heat.js";

// "Who's running hot" (#83) — the person-level strip under the CLIN cards.
//
// Collapsed to three rows by default. The Flight Deck is already tall enough that
// #56 exists because of it, and this section reports rather than acts: every row
// links into the allocation matrix, which is where hours actually get changed.
//
// All prose comes from heat.js and all judgement from the server. This file is
// layout.

const label = {
  fontFamily: "var(--grotesk, inherit)",
  fontWeight: 600,
  fontSize: 15,
  color: "var(--text)",
};

export default function PeopleRunningHot({ heat, onOpenPerson }) {
  const [expanded, setExpanded] = useState(false);
  // Hours by default. The cost ordering is one click away and labelled, because a
  // silent sort by dollars is a pay ranking wearing an overtime report's clothes.
  const [order, setOrder] = useState(BY_HOURS);
  if (!heat) return null;
  const summary = heatSummary(heat);

  if (summary.empty) {
    return (
      <div style={{ ...panelStyle, marginTop: 18 }}>
        <div style={label}>Who's running hot</div>
        <div style={{ fontSize: 12.5, color: "var(--dim)", marginTop: 6 }}>{summary.empty}</div>
      </div>
    );
  }

  const ranked = sortPeople(summary.people, order);
  const shown = expanded ? ranked : ranked.slice(0, COLLAPSED_ROWS);
  const hidden = ranked.length - shown.length;

  return (
    <div style={{ ...panelStyle, marginTop: 18 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 }}>
        <div style={label}>Who's running hot</div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 11.5, color: "var(--faint)" }}>
            hours above each person's expected week, on a CLIN that's off pace
          </span>
          <span style={{ display: "flex", gap: 2, fontSize: 11.5 }}>
            {[
              [BY_HOURS, "most hours over"],
              [BY_COST, "highest cost"],
            ].map(([key, text]) => (
              <button
                key={key}
                type="button"
                aria-pressed={order === key}
                onClick={() => setOrder(key)}
                style={{
                  padding: "2px 7px",
                  borderRadius: 7,
                  border: "1px solid var(--border)",
                  cursor: "pointer",
                  background: order === key ? "var(--panel2)" : "transparent",
                  color: order === key ? "var(--text)" : "var(--dim)",
                  fontWeight: order === key ? 600 : 400,
                }}
              >
                {text}
              </button>
            ))}
          </span>
        </div>
      </div>

      {/* The diagnosis comes first: it decides whether the rows below mean "stop the
          overtime" or "cut people", and those remedies are opposites. */}
      {summary.clins.map((c) => (
        <div
          key={c.id}
          style={{
            marginTop: 12,
            padding: "9px 11px",
            borderRadius: 10,
            border: "1px solid var(--border)",
            background: "var(--bg)",
            fontSize: 12.5,
            color: "var(--text)",
            display: "flex",
            gap: 8,
            alignItems: "flex-start",
          }}
        >
          <span
            aria-hidden="true"
            style={{
              flex: "0 0 auto",
              marginTop: 4,
              width: 8,
              height: 8,
              borderRadius: 8,
              background: c.diagnosis === "stop_overtime" ? "var(--warn)" : "var(--bad)",
            }}
          />
          <span>{diagnosisSentence(c)}</span>
        </div>
      ))}

      <ul style={{ listStyle: "none", margin: "12px 0 0", padding: 0 }}>
        {shown.map((p) => {
          const ot = overtimeNote(p);
          const away = elsewhereNote(p);
          return (
            <li
              key={p.id}
              style={{
                display: "flex",
                gap: 12,
                alignItems: "flex-start",
                padding: "10px 0",
                borderTop: "1px solid var(--border)",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
                  {p.name}
                  {p.lcat ? (
                    <span style={{ fontWeight: 400, color: "var(--dim)" }}> · {p.lcat}</span>
                  ) : null}
                </div>
                <div style={{ fontSize: 12, color: "var(--text)", marginTop: 3 }}>
                  {personSentence(p, heat)}
                </div>
                <div style={{ fontSize: 11.5, color: "var(--faint)", marginTop: 3 }}>
                  {expectationNote(p)}
                  {away ? ` · ${away}` : ""}
                  {ot ? ` · ${ot}` : ""}
                </div>
              </div>
              <div style={{ flex: "0 0 auto", textAlign: "right" }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
                  {p.clins.some((c) => !c.unpriced) ? `${money(p.weekly_dollars)}/wk` : "—"}
                </div>
                <button
                  type="button"
                  onClick={() => onOpenPerson?.(p.name)}
                  style={{
                    marginTop: 4,
                    fontSize: 11.5,
                    color: "var(--accent, var(--text))",
                    background: "none",
                    border: "none",
                    padding: 0,
                    cursor: "pointer",
                    textDecoration: "underline",
                  }}
                >
                  Open in matrix
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      {ranked.length > COLLAPSED_ROWS && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          style={{
            marginTop: 4,
            fontSize: 11.5,
            color: "var(--dim)",
            background: "none",
            border: "none",
            padding: 0,
            cursor: "pointer",
          }}
        >
          {expanded ? "Show fewer" : `Show all ${ranked.length} (${hidden} more)`}
        </button>
      )}

      {/* The award's own estimated hours (#83) — charged against contracted, where
          the rate table printed a figure. Separate from the dollar story above: this
          is the ceiling the work is held to, not the money. */}
      {summary.ceilings.length > 0 && (
        <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
          <div style={{ fontSize: 11.5, color: "var(--faint)", marginBottom: 5 }}>
            Contracted hours — from the award's estimated hours
          </div>
          {summary.ceilings.map((c) => (
            <div
              key={`${c.clin}-${c.lcat || "total"}`}
              style={{ fontSize: 12, color: "var(--text)", marginTop: 4 }}
            >
              {ceilingSentence(c)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
