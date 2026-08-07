import { moneyM, shortDate } from "./format.js";

/** The #83 diagnosis for the CLIN this alert is about, if there is one. */
function heatFor(heat, item) {
  if (!heat || !item) return null;
  // Alert items carry `code` ("CLIN 0002"); the heat payload keys on the bare number.
  const id = item.id || String(item.code || "").replace(/^CLIN\s*/i, "");
  const clin = (heat.clins || []).find((c) => c.id === id);
  if (!clin || !clin.people?.length) return null;
  const names = (heat.people || [])
    .filter((p) => clin.people.includes(p.id))
    // Ordered the way the strip orders them — by hours over expectation, never by
    // rate. A suggestion that names the expensive people while the strip above names
    // the overworked ones is the same inconsistency in a different font.
    .sort((a, b) => b.over_hours_per_week - a.over_hours_per_week)
    .map((p) => p.name);
  return { ...clin, names };
}

/** Up to three names, then a count — a suggestion is a sentence, not a roster. */
function namesOf(hot) {
  const names = hot.names || [];
  if (names.length <= 3) {
    return names.length === 1
      ? names[0]
      : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  }
  return `${names.slice(0, 3).join(", ")} and ${names.length - 3} others`;
}

// Turn a Flight Deck alert into a concrete recommendation. Pure and
// deterministic — every number comes straight from the burn payload — so this
// is the "AI off" copy AND the instant fallback shown while AI phrasing streams
// in (or if AI is off/unreachable). `action.kind` tells FlightDeck which buttons
// to render and where they route; `result` is the green "what this does" line.
//
// kind: "over" (tripwire) | "underburn" | "funding"
//
// `heat` is the #83 payload, optional. When it carries a diagnosis for this CLIN the
// tripwire advice defers to it, because the two were saying different things about
// the same CLIN: this file's default advice is "rebalance every line", and on a CLIN
// whose overrun is *entirely* people working above their expected hours that is the
// wrong remedy — it scales down people who are already at plan and leaves the
// overtime in place. Two recommendation surfaces on one dashboard giving opposite
// instructions is worse than either alone, so there is one source of truth for the
// remedy and it is the diagnosis. #63 replaces this whole branch with named
// person-level moves built on the same payload.
export function suggestFor(kind, item, contract, heat) {
  const wk = (w) => `week ${Math.round(w)}`;
  const hot = heatFor(heat, item);
  // The hard-stop date (#23) alongside the week index. A week number is the
  // engine's unit but not the PM's — the action here is scheduling a mod or a
  // staffing change, and both are done against a calendar. Always guarded: a
  // payload older than this bundle has no `stop_date` (an API process without
  // --reload serves the old shape while Vite has hot-reloaded this file), and the
  // copy has to degrade to the week index rather than print a placeholder.
  const at = item.stop_date ? ` (around ${shortDate(item.stop_date)})` : "";

  if (kind === "over") {
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
    // The overrun is entirely hours above plan (#83): say so and name the people,
    // rather than telling the PM to scale down a team that is mostly at its expected
    // hours already.
    if (hot && hot.diagnosis === "stop_overtime") {
      const bought =
        hot.weeks_bought > 0 ? ` — worth about ${hot.weeks_bought} weeks of runway` : "";
      return {
        body:
          `${item.code} exhausts in ${wk(item.exhaust_week)}${at}, ${item.weeks_early} weeks ` +
          `early, and the gap is hours above plan: ${namesOf(hot)} ${hot.people.length === 1 ? "is" : "are"} ` +
          `over their expected week. Bring them back to their expected hours and this line ` +
          `finishes inside its ${moneyM(ceiling)} ${label}${bought}.`,
        result: `Removes ${moneyM(hot.excess_weekly_dollars)}/wk of hours above plan.`,
        action: { kind: "balance" },
      };
    }
    return {
      body:
        `Trim the off-pace lines back to plan. Rebalancing every line to finish right at ` +
        `the period-of-performance end pulls ${item.code} back under its ${moneyM(ceiling)} ` +
        `${label} — today it exhausts in ${wk(item.exhaust_week)}${at}, ` +
        `${item.weeks_early} weeks early.` +
        // Same diagnosis, opposite conclusion: the rebalance IS the right remedy
        // here, and saying why keeps it from reading as a contradiction of the
        // "who's running hot" strip further down the page.
        (hot && hot.diagnosis === "reduce_staffing"
          ? ` Overtime alone doesn't explain it — this line runs out early even with everyone at their expected hours.`
          : ""),
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
