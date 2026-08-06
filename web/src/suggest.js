import { moneyM, shortDate } from "./format.js";

// Turn a Flight Deck alert into a concrete recommendation. Pure and
// deterministic — every number comes straight from the burn payload — so this
// is the "AI off" copy AND the instant fallback shown while AI phrasing streams
// in (or if AI is off/unreachable). `action.kind` tells FlightDeck which buttons
// to render and where they route; `result` is the green "what this does" line.
//
// kind: "over" (tripwire) | "underburn" | "funding"
export function suggestFor(kind, item, contract, staffingMoves = []) {
  const wk = (w) => `week ${Math.round(w)}`;
  // The hard-stop date (#23) alongside the week index. A week number is the
  // engine's unit but not the PM's — the action here is scheduling a mod or a
  // staffing change, and both are done against a calendar. Always guarded: a
  // payload older than this bundle has no `stop_date` (an API process without
  // --reload serves the old shape while Vite has hot-reloaded this file), and the
  // copy has to degrade to the week index rather than print a placeholder.
  const at = item.stop_date ? ` (around ${shortDate(item.stop_date)})` : "";

  if (kind === "over") {
    if (staffingMoves.length) {
      const describe = (move) =>
        `${move.name}${move.lcat ? ` (${move.lcat})` : ""}`;
      const rollOffs = staffingMoves
        .filter((move) => move.kind === "roll_off")
        .map((move) =>
          move.clears_lcat_flag
            ? `Move ${describe(move)} off ${item.code} — also clears the LCAT flag`
            : `Roll ${describe(move)} off ${item.code}`
        );
      const trims = new Map();
      for (const move of staffingMoves.filter((move) => move.kind === "trim")) {
        const names = trims.get(move.to_hours) || [];
        names.push(move.name);
        trims.set(move.to_hours, names);
      }
      const trimSteps = [...trims.entries()].map(([hours, names]) =>
        `Trim ${names.join(" & ")} to ${hours} hrs/wk`
      );
      return {
        body: [...rollOffs, ...trimSteps].join(". ") + ".",
        result: "Brings this CLIN back to its contracted labor-hour plan.",
        action: { kind: "balance" },
      };
    }
    const ceiling = item.limited_by === "funding" ? item.funded : item.budget;
    const label = item.limited_by === "funding" ? "funded amount" : "ceiling";
    // Already spent through: rebalancing forward can't recover money that's gone,
    // so the advice leads with the realized fact and the date it happened rather
    // than an exhaustion week that's already behind the current week.
    if (item.stop_date_passed) {
      return {
        body:
          `${item.code} is already past its ${moneyM(ceiling)} ${label} — it ran out ` +
          `around ${shortDate(item.stop_date)}, so cost incurred since then is at risk. ` +
          `Trim the off-pace lines back to plan and get the obligation moving.`,
        result: "Lands every line right at PoP end.",
        action: { kind: "balance" },
      };
    }
    return {
      body:
        `Trim the off-pace lines back to plan. Rebalancing every line to finish right at ` +
        `the period-of-performance end pulls ${item.code} back under its ${moneyM(ceiling)} ` +
        `${label} — today it exhausts in ${wk(item.exhaust_week)}${at}, ` +
        `${item.weeks_early} weeks early.`,
      result: "Lands every line right at PoP end.",
      action: { kind: "balance" },
    };
  }

  if (kind === "underburn") {
    return {
      body:
        `Add staff or raise hours on ${item.code}. At the current pace it leaves ` +
        `${moneyM(item.projected_unspent)} of its ${moneyM(item.budget)} unspent — ` +
        `rebalancing to finish on plan redistributes the slack and protects the ` +
        `option-year exercise.`,
      result: "Redistributes the slack so nothing lands unspent.",
      action: { kind: "balance" },
    };
  }

  if (kind === "funding") {
    // Within 30 days of the funded slice running out, escalate from "line up the
    // next mod" to a hard deadline: generate the funding request now.
    const days = item.runway_days;
    const urgent = days != null && days <= 30;
    const body = urgent
      ? `Funding deadline — only ${days} days until ${item.code} exhausts its funded ` +
        `${moneyM(item.funded)}${at || ` (${wk(item.exhaust_week)})`}. Generate an ` +
        `incremental-funding request now so the mod can be obligated before the money ` +
        `runs out.`
      : `Draft the incremental-funding request. ${item.code} spends through its funded ` +
        `${moneyM(item.funded)} in ${wk(item.exhaust_week)}${at} — ` +
        (item.mod_in_progress
          ? `a mod is already outstanding, so confirm the obligation lands before then.`
          : `line up the next mod before then to keep it funded.`);
    return {
      body,
      result: urgent ? "Creates a ready-to-send funding request." : null,
      action: { kind: "funding", urgent },
    };
  }

  return { body: "", result: null, action: null };
}
