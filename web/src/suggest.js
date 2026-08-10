import { money, moneyM, shortDate } from "./format.js";

/** Alert items carry `code` ("CLIN 0002"); the heat payload keys on the bare number. */
function clinIdOf(item) {
  return item.id || String(item.code || "").replace(/^CLIN\s*/i, "");
}

/** The #83 diagnosis for the CLIN this alert is about, if there is one. */
function heatFor(heat, item) {
  if (!heat || !item) return null;
  const id = clinIdOf(item);
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

// ---- #63: the named, person-level move list -------------------------------
//
// The moves themselves are solved on the server (`server/app/suggest.py`) and arrive
// on the heat payload. Nothing here decides who moves or by how much — this file only
// turns the plan into the design's prose. That split is deliberate: #83's ranking is
// server-side so two surfaces cannot name different people, and a solver in JSX would
// let the Flight Deck's advice and the matrix's `Apply fix` drift apart again.

/** The solved move plan for this alert's CLIN, if the server produced one. */
function planFor(heat, item) {
  if (!heat || !item) return null;
  const id = clinIdOf(item);
  return (heat.suggestions || []).find((p) => p.clin === id) || null;
}

/** Weekly dollars, compact — the design writes `$24K/wk`, not `$24,000/wk`. */
function weekly(n) {
  const v = Math.abs(n || 0);
  if (v >= 1000) return `$${Math.round(v / 100) / 10}K/wk`;
  return `${money(v)}/wk`;
}

/** `A, B & C` — the design's join for a grouped bullet. Sentences elsewhere in this
 *  file use "and"; a terse list item is a different register and the ticket is
 *  explicitly a design-parity one, so the bullets match the mock's punctuation. */
function joinNames(names) {
  if (names.length <= 1) return names[0] || "";
  return `${names.slice(0, -1).join(", ")} & ${names[names.length - 1]}`;
}

const hrs = (n) => `${Number.isInteger(n) ? n : n.toFixed(1)} hrs/wk`;

/** One bullet for one grouped decision, in the design's phrasing. */
export function moveSentence(group) {
  const who = joinNames(group.people || []);
  // The LCAT parenthetical only reads well for a single person — "Dana, Marcus &
  // Sofia (Systems Engineer)" implies they share one category, which grouping does
  // not guarantee.
  const lcat =
    group.people?.length === 1 && group.lcat ? ` (${group.lcat})` : "";
  const cost = group.dollars_unknown
    ? "dollar effect unknown"
    : `frees ${weekly(group.weekly_dollars)}`;

  let text;
  if (group.kind === "roll_off") {
    text = `Roll ${who}${lcat} to the bench — ${cost}`;
  } else if (group.kind === "shift") {
    text = `Move ${who}${lcat} to CLIN ${group.to_clin} — ${cost} off this line`;
  } else if (group.kind === "raise") {
    text = `Raise ${who}${lcat} to ${hrs(group.to_hours)} — adds ${weekly(
      group.weekly_dollars,
    )}`;
  } else {
    text = `Trim ${who}${lcat} to ${hrs(group.to_hours)} — ${cost}`;
  }
  // Free to compute and worth saying: clearing these hours off the line also closes
  // an unmatched-LCAT flag. This is the design's "also clears the LCAT flag".
  if (group.clears_lcat_flag) text += " — also clears the LCAT flag";
  // #66, the same pattern for the more serious of the two: the move takes somebody off
  // a category they don't meet the minimums for. Said after the dollars because the
  // solver is closing a funding gap and this is a side effect, not the reason — but
  // said, because it is the side effect somebody would want to know about.
  if (group.clears_compliance_flag) text += " — also clears a quals finding";
  return text;
}

/** The design's `fixResult`: `Forward burn $X/wk → $Y/wk · lands week Z of N`. */
export function fixResult(plan) {
  const head = `Forward burn ${weekly(plan.weekly)} → ${weekly(plan.new_weekly)}`;
  if (!plan.closed) {
    // Never dress a partial fix as a fix. The gap that is left is the whole point of
    // saying anything at all here.
    return `${head} · still ${weekly(plan.shortfall_weekly)} short of landing on plan`;
  }
  if (plan.new_exhaust_week == null || !plan.total_weeks) return `${head} · lands on plan`;
  return `${head} · lands week ${Math.round(
    Math.min(plan.new_exhaust_week, plan.total_weeks),
  )} of ${Math.round(plan.total_weeks)}`;
}

/** The bullets, the result line and the caveats for a solved plan. */
function movePlan(plan) {
  return {
    steps: (plan.groups || []).map(moveSentence),
    result: fixResult(plan),
    notes: plan.notes || [],
    // The client applies exactly these, rather than re-deriving a uniform scale.
    action: { kind: "balance", moves: plan.moves, clin: plan.clin },
  };
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
// Returns `{ body, steps?, result, notes?, action }`. `steps` is #63's named move list
// and is present only when the server solved one; `body` is then the lead-in sentence
// rather than the whole recommendation. When there are no moves this degrades to the
// CLIN-level paragraph it has always returned, which is the fallback the ticket asks
// for when no reasonable move set closes the gap.
//
// `heat` is the #83 payload, optional. When it carries a diagnosis for this CLIN the
// tripwire advice defers to it, because the two were saying different things about
// the same CLIN: this file's default advice is "rebalance every line", and on a CLIN
// whose overrun is *entirely* people working above their expected hours that is the
// wrong remedy — it scales down people who are already at plan and leaves the
// overtime in place. Two recommendation surfaces on one dashboard giving opposite
// instructions is worse than either alone, so there is one source of truth for the
// remedy and it is the diagnosis. #63 builds the named person-level moves on top of
// that same payload; these sentences remain as the fallback for a CLIN the solver
// cannot close, and for a payload that predates `suggestions`.
export function suggestFor(kind, item, contract, heat) {
  const wk = (w) => `week ${Math.round(w)}`;
  const hot = heatFor(heat, item);
  const plan = planFor(heat, item);
  // A plan with no moves is the solver saying it could not close this with any
  // reasonable move set (its `notes` say why). The ticket asks for the CLIN-level
  // paragraph as the fallback in exactly that case, so `hasMoves` — not the presence
  // of a plan — is what switches this surface into a bullet list.
  const hasMoves = !!(plan && plan.moves && plan.moves.length);
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
    // A funding-limited line asks for a mod, and that answer outranks every branch
    // below including the realized one — checked FIRST on purpose.
    //
    // Money having already run out makes the obligation *more* urgent, not less, so
    // routing the realized case to the staffing paragraph answered the most urgent
    // funding situation in the app with "trim the off-pace lines back to plan", a green
    // "Lands every line right at PoP end" and an Open-simulator button. On live contract
    // 23 (7026HEXDVC0001043) — $2.5M charged against an $800K obligation with $3.5M of
    // ceiling still underneath — rebalancing recovers nothing already spent, and no
    // staffing change turns unobligated ceiling into money the government owes.
    //
    // Decided from the tripwire item, NOT from the solved plan. `heat` (and with it
    // `suggestions`) is fetched after burn and can fail on its own, and while it is
    // pending `plan` is null — so gating the remedy on the plan meant a funding gap
    // rendered the staffing paragraph on first paint and permanently if that request
    // failed. Both sources derive from the same `ceiling_breached`, so they agree; the
    // plan is preferred only for its already-rounded dollars.
    const funded = plan?.funded ?? item.funded ?? item.budget ?? 0;
    const headroom = plan?.ceiling_headroom ?? (item.ceiling ?? 0) - funded;
    const overspent = plan?.overspent ?? item.overspent ?? 0;
    //
    // `!hasMoves` keeps this in step with the server's extra condition — it also
    // requires headroom to beat a week of burn, which the item cannot express. If the
    // solver produced real moves it judged this a ceiling story, so the staffing answer
    // stands; if it produced none (or hasn't answered yet) the funding answer does.
    const fundingLimited =
      item.limited_by === "funding" &&
      item.ceiling_breached === false &&
      headroom > 0 &&
      !hasMoves;
    if (fundingLimited) {
      const spentThrough = overspent > 0;
      return {
        body: spentThrough
          ? `${item.code} spent through its obligated ${moneyM(funded)} around ` +
            `${shortDate(item.stop_date)} — ${moneyM(overspent)} has been charged since, ` +
            `and that cost stays at risk until a mod lands. Its ` +
            `${moneyM(item.ceiling ?? plan?.ceiling)} ceiling still has ${moneyM(headroom)} ` +
            `underneath, so this is an obligation gap, not overstaffing: get the ` +
            `incremental-funding mod moving now.`
          : `${item.code} spends through its obligated ${moneyM(funded)} in ` +
            `${wk(item.exhaust_week)}${at}, but its ${moneyM(item.ceiling ?? plan?.ceiling)} ` +
            `ceiling still has ${moneyM(headroom)} beneath that — an obligation gap, not ` +
            `overstaffing. Get the next incremental-funding mod moving; no staffing change ` +
            `is needed to deliver the work already funded.`,
        result: spentThrough
          ? "Puts the mod on the clock — the only thing that clears the risk."
          : "Keeps the team in place and puts the mod on the clock.",
        action: { kind: "funding", urgent: spentThrough },
      };
    }
    // Already spent through: rebalancing forward can't recover money that's gone,
    // so the advice leads with the realized fact and the date it happened rather
    // than an exhaustion week that's already behind the current week. Reached only
    // when the ceiling is the binding limit — a funding-limited line returned above.
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
    // #63: the solver closed this with a concrete set of named moves, so the advice is
    // the list. The lead-in stays a sentence — it carries the CLIN's clock, which the
    // bullets deliberately don't repeat — and it branches on the diagnosis for the same
    // reason the prose below does: telling a PM to cut a team that is already at plan is
    // the one wrong answer here.
    if (hasMoves) {
      const lead =
        plan.diagnosis === "stop_overtime"
          ? `${item.code} exhausts in ${wk(item.exhaust_week)}${at}, ${item.weeks_early} weeks ` +
            `early, and the gap is hours above plan — not headcount. Bring these people back ` +
            `to their expected week:`
          : `${item.code} exhausts in ${wk(item.exhaust_week)}${at}, ${item.weeks_early} weeks ` +
            `early, and it runs out early even with everyone at their expected hours. ` +
            `These moves close the ${weekly(plan.gap_weekly)} gap:`;
      return { body: lead, ...movePlan(plan) };
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
    // The mirror image, same solver with the sign flipped. Note what the server will
    // and won't offer here: it raises people who are billing under their own expected
    // week, and stops there. If that doesn't close the gap there are no moves and this
    // falls through to the paragraph, because the honest remedy is another body on the
    // line — not a longer week for the people already on it.
    if (hasMoves) {
      return {
        body:
          `${item.code} leaves ${moneyM(item.projected_unspent)} of its ` +
          `${moneyM(item.budget)} unspent at the current pace. There is ` +
          `${weekly(plan.gap_weekly)} of room and people with hours to spare:`,
        ...movePlan(plan),
      };
    }
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
