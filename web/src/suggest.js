import { moneyM } from "./format.js";

// Turn a Flight Deck alert into a concrete recommendation. Pure and
// deterministic — every number comes straight from the burn payload — so this
// is the "AI off" copy AND the instant fallback shown while AI phrasing streams
// in (or if AI is off/unreachable). `action.kind` tells FlightDeck which buttons
// to render and where they route; `result` is the green "what this does" line.
//
// kind: "over" (tripwire) | "underburn" | "funding"
export function suggestFor(kind, item, contract) {
  const wk = (w) => `week ${Math.round(w)}`;

  if (kind === "over") {
    const ceiling = item.limited_by === "funding" ? item.funded : item.budget;
    const label = item.limited_by === "funding" ? "funded amount" : "ceiling";
    return {
      body:
        `Trim the off-pace lines back to plan. Rebalancing every line to finish right at ` +
        `the period-of-performance end pulls ${item.code} back under its ${moneyM(ceiling)} ` +
        `${label} — today it exhausts in ${wk(item.exhaust_week)}, ${item.weeks_early} weeks early.`,
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
    return {
      body:
        `Draft the incremental-funding request. ${item.code} spends through its funded ` +
        `${moneyM(item.funded)} in ${wk(item.exhaust_week)} — ` +
        (item.mod_in_progress
          ? `a mod is already outstanding, so confirm the obligation lands before then.`
          : `line up the next mod before then to keep it funded.`),
      result: null,
      action: { kind: "funding" },
    };
  }

  return { body: "", result: null, action: null };
}
