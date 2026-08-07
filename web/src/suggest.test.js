import { test } from "node:test";
import assert from "node:assert/strict";
import { suggestFor, moveSentence, fixResult } from "./suggest.js";

const fundingItem = (runway_days) => ({
  code: "CLIN 0002",
  name: "Option Labor",
  funded: 1_800_000,
  exhaust_week: 42,
  runway_days,
  mod_in_progress: false,
});

test("funding suggestion escalates within 30 days of exhaustion", () => {
  const s = suggestFor("funding", fundingItem(20), {});
  assert.equal(s.action.kind, "funding");
  assert.equal(s.action.urgent, true);
  assert.match(s.body, /Funding deadline/);
  assert.match(s.body, /20 days/);
  assert.ok(s.result); // shows a "what this does" line when urgent
});

test("funding suggestion stays routine when more than 30 days out", () => {
  const s = suggestFor("funding", fundingItem(90), {});
  assert.equal(s.action.urgent, false);
  assert.doesNotMatch(s.body, /Funding deadline/);
  assert.equal(s.result, null);
});

// ---- #23: the advice is scheduled against a calendar, not a week index ------

test("suggestions carry the hard-stop date when the payload has one", () => {
  const dated = { ...fundingItem(20), stop_date: "2026-06-27" };
  assert.match(suggestFor("funding", dated, {}).body, /27 Jun 26/);
  assert.match(suggestFor("funding", { ...dated, runway_days: 90 }, {}).body, /27 Jun 26/);
});

test("suggestions fall back to the week index when there is no date", () => {
  // A payload older than this bundle carries no `stop_date` — an API process
  // without --reload serves the old shape while Vite has hot-reloaded the client.
  // The copy has to lose the date, never print a placeholder for it.
  const s = suggestFor("funding", fundingItem(20), {});
  assert.match(s.body, /week 42/);
  assert.doesNotMatch(s.body, /—$/);
  assert.doesNotMatch(s.body, /around/);
});

test("a tripwire past its funding advises on the realized loss, not a forecast", () => {
  // Rebalancing forward can't recover money already spent, and the exhaustion
  // week is behind the current week, so quoting it as "today it exhausts in
  // week 14" reads as a future event.
  const s = suggestFor(
    "over",
    {
      code: "CLIN 2001",
      name: "Option Labor",
      limited_by: "funding",
      funded: 1_572_366,
      budget: 3_076_112,
      exhaust_week: 14,
      weeks_early: 38,
      stop_date: "2026-04-21",
      stop_date_passed: true,
    },
    {},
  );
  assert.match(s.body, /already past/);
  assert.match(s.body, /21 Apr 26/);
  assert.match(s.body, /at risk/);
  assert.doesNotMatch(s.body, /week 14/);
});

// --- the tripwire and the "who's running hot" strip must not contradict (#83) --

const overItem = {
  id: "0002",
  code: "CLIN 0002",
  name: "Option Labor",
  budget: 1_400_000,
  limited_by: "ceiling",
  exhaust_week: 44,
  weeks_early: 6,
  stop_date_passed: false,
};

const heatPayload = (diagnosis) => ({
  clins: [
    {
      id: "0002",
      diagnosis,
      exhaust_week: 44,
      exhaust_week_at_expected: 55.2,
      weeks_bought: 11.2,
      excess_weekly_dollars: 240_000,
      people: ["p1", "p2"],
    },
  ],
  people: [
    { id: "p1", name: "Wei Chen", over_hours_per_week: 4, weekly_dollars: 2000 },
    { id: "p2", name: "Aisha Khan", over_hours_per_week: 9, weekly_dollars: 900 },
  ],
});

test("without a heat payload the tripwire advice is unchanged", () => {
  const before = suggestFor("over", overItem, {});
  assert.ok(before.body.includes("Trim the off-pace lines back to plan"));
  assert.ok(!before.body.includes("hours above plan"));
});

test("an overtime-only diagnosis replaces 'rebalance everyone' with the real remedy", () => {
  // Scaling down a team that is already at its expected hours is the wrong fix, and
  // it is what this branch used to advise on exactly this CLIN.
  const s = suggestFor("over", overItem, {}, heatPayload("stop_overtime"));
  assert.ok(s.body.includes("the gap is hours above plan"));
  assert.ok(!s.body.includes("Trim the off-pace lines back to plan"));
  assert.ok(s.body.includes("11.2 weeks of runway"));
});

test("the suggestion names people in the strip's order — hours over, never rate", () => {
  const s = suggestFor("over", overItem, {}, heatPayload("stop_overtime"));
  // Aisha is 9 hrs/wk over on a cheaper rate; Wei is 4 hrs/wk over but costs more.
  assert.ok(s.body.indexOf("Aisha Khan") < s.body.indexOf("Wei Chen"));
});

test("an overstaffing diagnosis keeps the rebalance and says why it is right", () => {
  const s = suggestFor("over", overItem, {}, heatPayload("reduce_staffing"));
  assert.ok(s.body.includes("Trim the off-pace lines back to plan"));
  assert.ok(s.body.includes("even with everyone at their expected hours"));
});

test("a heat payload for a different CLIN is ignored", () => {
  const other = { ...heatPayload("stop_overtime") };
  other.clins[0].id = "0001";
  const s = suggestFor("over", overItem, {}, other);
  assert.ok(s.body.includes("Trim the off-pace lines back to plan"));
});

test("a diagnosis with nobody attached does not change the advice", () => {
  const empty = heatPayload("stop_overtime");
  empty.clins[0].people = [];
  const s = suggestFor("over", overItem, {}, empty);
  assert.ok(s.body.includes("Trim the off-pace lines back to plan"));
});

// --- #63: named, person-level moves ------------------------------------------
//
// The solver itself is tested in server/tests/test_suggest_moves.py — including the
// rules that outrank the ticket's text (no rate ranking, expected-hours floors,
// diagnosis-driven remedies). What is pinned here is that this file renders the
// server's plan faithfully and never re-decides any of it.

/** A solved plan in the shape `server/app/suggest.py` returns. */
const planFor = (over = {}) => ({
  clin: "0002",
  direction: "reduce",
  diagnosis: "stop_overtime",
  weekly: 24_000,
  target_weekly: 18_000,
  gap_weekly: 6_000,
  freed_weekly: 6_000,
  new_weekly: 18_000,
  exhaust_week: 44,
  new_exhaust_week: 51,
  total_weeks: 52,
  closed: true,
  shortfall_weekly: 0,
  escalated: false,
  moves: [
    {
      kind: "trim",
      clin: "0002",
      person_id: "p2",
      person: "Aisha Khan",
      lcat: "Cyber Engineer III",
      from_hours: 49,
      to_hours: 40,
      hours_moved: 9,
      weekly_dollars: 4_500,
      dollars_unknown: false,
      clears_lcat_flag: false,
      floor: "expected",
    },
    {
      kind: "trim",
      clin: "0002",
      person_id: "p1",
      person: "Wei Chen",
      lcat: "Engineer I",
      from_hours: 44,
      to_hours: 40,
      hours_moved: 4,
      weekly_dollars: 1_500,
      dollars_unknown: false,
      clears_lcat_flag: false,
      floor: "expected",
    },
  ],
  groups: [
    {
      kind: "trim",
      to_hours: 40,
      to_clin: null,
      people: ["Aisha Khan", "Wei Chen"],
      person_ids: ["p2", "p1"],
      hours_moved: 13,
      weekly_dollars: 6_000,
      dollars_unknown: false,
      clears_lcat_flag: false,
      floor: "expected",
    },
  ],
  unpriced: [],
  notes: [],
  ...over,
});

const withPlan = (diagnosis, over = {}) => ({
  ...heatPayload(diagnosis),
  suggestions: [planFor({ diagnosis, ...over })],
});

test("a solved plan is a bulleted move list, not a paragraph", () => {
  const s = suggestFor("over", overItem, {}, withPlan("stop_overtime"));
  assert.equal(s.steps.length, 1);
  assert.match(s.steps[0], /Trim Aisha Khan & Wei Chen to 40 hrs\/wk/);
  // The lead-in keeps the CLIN's clock — the bullets deliberately don't repeat it —
  // and stops telling the PM to "rebalance every line".
  assert.match(s.body, /exhausts in week 44/);
  assert.doesNotMatch(s.body, /Trim the off-pace lines back to plan/);
});

test("the bullets name people in the strip's order — hours over, never rate", () => {
  // Aisha is 9 hrs/wk over on a cheaper rate; Wei is 4 hrs/wk over but costs more.
  // This is the client half of the guarantee the server test pins.
  const s = suggestFor("over", overItem, {}, withPlan("stop_overtime"));
  const list = s.steps.join(" ");
  assert.ok(list.indexOf("Aisha Khan") < list.indexOf("Wei Chen"));
});

test("identical trims read as one decision", () => {
  const s = suggestFor("over", overItem, {}, withPlan("stop_overtime"));
  assert.equal(s.steps.length, 1); // two people, one bullet
  assert.match(s.steps[0], /frees \$6K\/wk/);
});

test("the result line is the design's fixResult", () => {
  const s = suggestFor("over", overItem, {}, withPlan("stop_overtime"));
  assert.equal(s.result, "Forward burn $24K/wk → $18K/wk · lands week 51 of 52");
});

test("an overstaffing plan leads with the gap rather than with overtime", () => {
  const s = suggestFor("over", overItem, {}, withPlan("reduce_staffing"));
  assert.match(s.body, /even with everyone at their expected hours/);
  assert.match(s.body, /closes? the \$6K\/wk gap/);
});

test("a partial fix is never dressed up as a fix", () => {
  const heat = withPlan("reduce_staffing", {
    closed: false,
    freed_weekly: 4_000,
    new_weekly: 20_000,
    shortfall_weekly: 2_000,
  });
  const s = suggestFor("over", overItem, {}, heat);
  assert.match(s.result, /still \$2K\/wk short/);
  assert.doesNotMatch(s.result, /lands week/);
});

test("a plan with no moves falls back to the CLIN-level paragraph", () => {
  // The ticket's own example: one person at 8 hrs/wk, nothing reasonable to move. The
  // solver withdraws the list and says why, and this surface must not render an empty
  // <ul> where advice used to be.
  const heat = withPlan("reduce_staffing", {
    moves: [],
    groups: [],
    closed: false,
    freed_weekly: 0,
    notes: ["No staffing change closes this — the line would have to stop billing."],
  });
  const s = suggestFor("over", overItem, {}, heat);
  assert.deepEqual(s.steps ?? [], []);
  assert.match(s.body, /Trim the off-pace lines back to plan/);
});

test("caveats the move list cannot express are carried through", () => {
  const heat = withPlan("stop_overtime", {
    notes: ["Cyber Engineer III hours on this CLIN are already 3,802 over the 2,080 the award estimates — closing the dollar gap does not fix that."],
  });
  const s = suggestFor("over", overItem, {}, heat);
  assert.equal(s.notes.length, 1);
  assert.match(s.notes[0], /award estimates/);
});

test("Apply fix carries the exact moves, not a scale factor", () => {
  const s = suggestFor("over", overItem, {}, withPlan("stop_overtime"));
  assert.equal(s.action.kind, "balance");
  assert.equal(s.action.moves.length, 2);
  assert.deepEqual(
    s.action.moves.map((m) => [m.person_id, m.clin, m.to_hours]),
    [
      ["p2", "0002", 40],
      ["p1", "0002", 40],
    ],
  );
});

test("a CLIN already past its ceiling still leads with the realized loss", () => {
  // Money already spent can't be recovered by moving anyone, so the move list must not
  // pre-empt the #23 branch.
  const past = { ...overItem, stop_date: "2026-04-21", stop_date_passed: true };
  const s = suggestFor("over", past, {}, withPlan("reduce_staffing"));
  assert.match(s.body, /already past/);
  assert.deepEqual(s.steps ?? [], []);
});

// --- the individual prose builders -------------------------------------------

test("a roll-off is phrased as the design phrases it", () => {
  const text = moveSentence({
    kind: "roll_off",
    people: ["Aisha Khan"],
    lcat: "Cyber Engineer III",
    to_hours: 0,
    weekly_dollars: 11_500,
  });
  assert.equal(text, "Roll Aisha Khan (Cyber Engineer III) to the bench — frees $11.5K/wk");
});

test("a shift names the destination CLIN and the flag it clears", () => {
  const text = moveSentence({
    kind: "shift",
    people: ["Wei Chen"],
    lcat: "Engineer I",
    to_clin: "0003",
    to_hours: 0,
    weekly_dollars: 4_000,
    clears_lcat_flag: true,
  });
  assert.match(text, /Move Wei Chen \(Engineer I\) to CLIN 0003/);
  assert.match(text, /also clears the LCAT flag/);
});

test("a group of people drops the LCAT parenthetical", () => {
  // "Dana, Marcus & Sofia (Systems Engineer)" implies they share one category, which
  // grouping does not guarantee.
  const text = moveSentence({
    kind: "trim",
    people: ["Dana Reed", "Marcus Hall", "Sofia Ruiz"],
    lcat: "Systems Engineer",
    to_hours: 24,
    weekly_dollars: 3_000,
  });
  assert.equal(text, "Trim Dana Reed, Marcus Hall & Sofia Ruiz to 24 hrs/wk — frees $3K/wk");
});

test("hours with no printed rate say the dollar effect is unknown", () => {
  // #64: real hours at no price. Inventing a rate to make the bullet look complete is
  // the one thing this must not do.
  const text = moveSentence({
    kind: "trim",
    people: ["Nadia Fox"],
    lcat: "Ghost Category",
    to_hours: 40,
    weekly_dollars: 0,
    dollars_unknown: true,
  });
  assert.match(text, /dollar effect unknown/);
  assert.doesNotMatch(text, /\$0/);
});

test("a fixResult with no forecast still says it lands on plan", () => {
  const line = fixResult({
    weekly: 12_000,
    new_weekly: 9_000,
    closed: true,
    new_exhaust_week: null,
    total_weeks: 52,
  });
  assert.equal(line, "Forward burn $12K/wk → $9K/wk · lands on plan");
});

test("a landing week never reads past the end of the period", () => {
  // The projection can land beyond PoP end once the excess comes off, and "lands week
  // 57 of 52" is not a sentence.
  const line = fixResult({
    weekly: 24_000,
    new_weekly: 14_000,
    closed: true,
    new_exhaust_week: 57.2,
    total_weeks: 52,
  });
  assert.match(line, /lands week 52 of 52/);
});

// --- the underburn mirror ----------------------------------------------------

test("an underburning line renders raises instead of trims", () => {
  const heat = {
    clins: [],
    people: [],
    suggestions: [
      {
        clin: "0004",
        direction: "raise",
        diagnosis: null,
        weekly: 4_000,
        gap_weekly: 3_000,
        freed_weekly: 3_000,
        new_weekly: 7_000,
        new_exhaust_week: 52,
        total_weeks: 52,
        closed: true,
        shortfall_weekly: 0,
        moves: [{ kind: "raise", person_id: "p9", person: "Quiet Dev", clin: "0004", to_hours: 40 }],
        groups: [
          {
            kind: "raise",
            people: ["Quiet Dev"],
            lcat: "Analyst II",
            to_hours: 40,
            weekly_dollars: 3_000,
          },
        ],
        notes: [],
      },
    ],
  };
  const item = {
    id: "0004",
    code: "CLIN 0004",
    projected_unspent: 900_000,
    budget: 2_000_000,
  };
  const s = suggestFor("underburn", item, {}, heat);
  assert.match(s.steps[0], /Raise Quiet Dev \(Analyst II\) to 40 hrs\/wk — adds \$3K\/wk/);
  assert.match(s.body, /hours to spare/);
});

test("an underburn with nobody to raise falls back to add-staff advice", () => {
  const item = {
    id: "0004",
    code: "CLIN 0004",
    projected_unspent: 900_000,
    budget: 2_000_000,
  };
  const s = suggestFor("underburn", item, {}, { clins: [], people: [], suggestions: [] });
  assert.match(s.body, /Add staff or raise hours/);
  assert.deepEqual(s.steps ?? [], []);
});

// ---- a funding gap asks for a mod, with or without the solved plan ----------
//
// Live contract 23 (7026HEXDVC0001043): $2.5M charged against an $800K obligation with
// $3.5M of ceiling still underneath. The remedy is the mod. The realized-loss branch
// used to preempt this and answer it with "trim the off-pace lines back to plan", a
// green "Lands every line right at PoP end" and an Open-simulator button.

const FUNDING_GAP_ITEM = {
  code: "CLIN 0001",
  limited_by: "funding",
  ceiling_breached: false,
  ceiling: 4_314_562,
  funded: 800_000,
  budget: 800_000,
  overspent: 1_703_050,
  spent: 2_503_050,
  exhaust_week: 8.22,
  runway_days: 0,
  stop_date: "2026-03-15",
  stop_date_passed: true,
};

test("a funding gap whose money already ran out asks for the mod, not a trim", () => {
  const s = suggestFor("over", FUNDING_GAP_ITEM, {}, null);
  assert.equal(s.action.kind, "funding");
  assert.equal(s.action.urgent, true);
  assert.match(s.body, /obligation gap, not overstaffing/);
  assert.match(s.body, /incremental-funding mod moving now/);
  // The failure this pins: staffing vocabulary and the rebalance action.
  assert.doesNotMatch(s.body, /[Tt]rim the off-pace lines/);
  assert.notEqual(s.result, "Lands every line right at PoP end.");
});

test("the funding remedy does not depend on the heat payload arriving", () => {
  // `heat` is fetched after burn and can fail on its own. While it is pending there is
  // no solved plan, and gating the remedy on the plan meant the first paint recommended
  // cutting staff — permanently if that request failed. Same answer either way.
  const withoutHeat = suggestFor("over", FUNDING_GAP_ITEM, {}, null);
  const withHeat = suggestFor("over", FUNDING_GAP_ITEM, {}, {
    clins: [],
    people: [],
    suggestions: [
      {
        clin: "0001",
        funding_limited: true,
        moves: [],
        groups: [],
        notes: [],
        funded: 800_000,
        ceiling: 4_314_562,
        ceiling_headroom: 3_514_562,
        overspent: 1_703_050,
      },
    ],
  });
  assert.equal(withoutHeat.action.kind, withHeat.action.kind);
  assert.equal(withoutHeat.body, withHeat.body);
});

test("real dollars, never a $0.00M placeholder", () => {
  // The tripwire item ships `funded`/`budget` but originally carried no `ceiling`, so
  // prose reaching for `item.ceiling` rendered "$0.00M" at the reader.
  const s = suggestFor("over", FUNDING_GAP_ITEM, {}, null);
  assert.doesNotMatch(s.body, /\$0\.00M/);
  assert.match(s.body, /\$4\.31M ceiling/);
  assert.match(s.body, /\$3\.51M/);
});

test("a genuine ceiling breach still gets the staffing answer", () => {
  // Unobligated headroom must not divert a line that is projected past its ceiling —
  // no obligation raises a ceiling. This is the contract 12 shape.
  const s = suggestFor(
    "over",
    { ...FUNDING_GAP_ITEM, ceiling_breached: true, overspent: 0, stop_date_passed: false },
    {},
    null,
  );
  assert.notEqual(s.action.kind, "funding");
});
