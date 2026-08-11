import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  getAllocation,
  listContracts,
  listPlans,
  savePlan,
  updatePlan,
  deletePlan,
  setBaselinePlan,
  clearBaselinePlan,
  getLcatRates,
  getPeople,
  getPeopleUtilization,
  savePersonQuals,
  setLcatAlias,
  deleteLcatAlias,
  setContractCapacity,
  setContractAbsence,
} from "../api.js";
import {
  buildAbsenceModel,
  walkRunway,
  absencesFor,
  absenceWorkdays,
  shiftDate,
} from "../absence.js";
import {
  planFingerprint,
  isUnsaved,
  newAddedId,
  isAddedId,
  scoringSnapshot,
  snapshotChanges,
} from "../plans.js";
import {
  planDrift,
  driftSummary,
  driftSentence,
  rateResolver,
  actualsDraft as buildDraft,
} from "../drift.js";
import {
  badge as complianceBadge,
  failureText,
  uncheckedText,
  rollupText,
} from "../compliance.js";
import { money, panelStyle, hueFor, statusColor, pill, shortDate } from "../format.js";
import ImportRateSchedule from "../components/ImportRateSchedule.jsx";
import { ConfirmDialog } from "../components/ConfirmDialog.jsx";
import { TrashButton } from "../components/DeleteContract.jsx";
import {
  prefillPerson,
  rateOptions,
  selectDirectoryPersonForm,
  switchPersonSource,
  validateAddedPerson,
} from "../allocation-person.js";

const grotesk = "'Space Grotesk',sans-serif";
const mono = "'IBM Plex Mono',monospace";

// Initials for the avatar chip, from an employee name.
function initials(name) {
  const parts = (name || "").trim().split(/\s+/);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase() || "—";
}

// The denominator of an FTE, mirroring capacity.py's FTE_HOURS_PER_WEEK. One FTE is
// a 2,080-hour year — 40 hrs × 52 weeks — by definition, so this is the one place a
// 40 belongs in this file after #84.
//
// Utilisation is a different measure and no longer uses it: it divides by each
// person's *expected* week, which the server resolves (person → LCAT → contract →
// a labelled fallback) and sends down on every employee row. That resolution is
// never repeated here — a second precedence chain in JSX is exactly how the API and
// the UI end up disagreeing about what "utilisation" means.
const FTE_HOURS_PER_WEEK = 40;

// A stored utilisation target as a fraction. Mirrors capacity.target_hours' tolerance:
// the value may be 0.8 or 80, because both spellings reach the API and it accepts them.
const pctOf = (target) => {
  const n = Number(target);
  if (!(n > 0)) return 0;
  return n > 1 ? n / 100 : n;
};

// Mirrors burn.py's _FUNDING_DUE_DAYS — how close the funded money has to be to
// running out before a CLIN mentions funding at all. 60 days is FAR 52.232-22(c)'s
// own notification lookahead; see burn.py for the reasoning.
const FUNDING_DUE_DAYS = 60;

// Does projected spend blow the real ceiling, not just the funded slice? Decides
// whether trouble is a ceiling problem or a funding one — and on a CLIN that isn't
// incrementally funded the budget *is* the ceiling, so any shortfall lands here
// and keeps the ceiling wording.
function ceilingBreachedFor(c, weekly, cw, totalWeeks) {
  if (!(weekly > 0) || !c.ceiling) return false;
  return cw + (c.ceiling - c.spent) / weekly < totalWeeks - 1;
}

// burn.py's _funds_exceeded: the allotted funding is already spent through while
// the ceiling holds. Realized, so simulating a different staffing mix can't undo
// it — `spent` is history. That's why it doesn't take the funding softening below.
function fundsExceededFor(c) {
  if (!c.incrementally_funded || !c.budget || c.spent < c.budget) return false;
  return !(c.ceiling && c.spent >= c.ceiling);
}

// Bands a projected exhaustion week against the finish line — burn.py's
// _forward_band. Shared so the funded slice and the ceiling are judged alike.
function forwardBand(exhaust, totalWeeks) {
  if (exhaust == null) return "ok";
  if (exhaust < totalWeeks - 1) return "over";
  if (exhaust < totalWeeks + 2) return "watch";
  if (exhaust > totalWeeks * 1.15) return "under";
  return "ok";
}

// Forward status from a projected exhaustion week, mirroring burn.py's bands —
// including the #22 funding downgrade and its horizon, which this used to skip
// entirely ("minus the funding nuance"), so these cards scored a CLIN red that
// the Flight Deck was showing amber for.
function simStatus(exhaustWeek, totalWeeks, c, weekly, cw) {
  if (exhaustWeek == null) return "paused";
  const band = forwardBand(exhaustWeek, totalWeeks);
  if (band !== "over") return band;
  // Money already out the door stays red — the softening below is forward-looking.
  if (fundsExceededFor(c)) return "over";
  if (
    c.incrementally_funded &&
    !ceilingBreachedFor(c, weekly, cw, totalWeeks) &&
    (c.mod_in_progress || c.funding_keeps_pace)
  ) {
    // Routine incremental funding — only says "funding due" once the money is
    // actually close to gone. Otherwise the CLIN is judged on its ceiling, same
    // as burn.py. See _FUNDING_DUE_DAYS there for why outrunning the funded
    // slice can't be the trigger on its own.
    if ((exhaustWeek - cw) * 7 <= FUNDING_DUE_DAYS) return "funding";
    const ceilingExhaust =
      weekly > 0 && c.ceiling ? cw + (c.ceiling - c.spent) / weekly : null;
    return forwardBand(ceilingExhaust, totalWeeks);
  }
  return "over";
}

// A seniority tier inferred from the LCAT name, for the colored tier chip —
// mirrors the reference's Jr/Mid/Sr tier map (design's tierMap).
function tierOf(lcat) {
  const s = (lcat || "").toLowerCase();
  if (/\b(sr|senior|principal|lead|staff|expert)\b/.test(s))
    return { label: "Sr", color: "var(--accent2)" };
  if (/\b(jr|junior|associate|entry|intern|assistant|support|clerk)\b/.test(s))
    return { label: "Jr", color: "var(--dim)" };
  return { label: "Mid", color: "var(--accent)" };
}

// Round, per-person colored avatar (design's avatarStyle): hue-tinted fill, hue
// text. `hue` is a hex like "#4361ee"; the alpha suffixes give a light wash.
const avatarStyle = (hue) => ({
  width: 30,
  height: 30,
  flexShrink: 0,
  borderRadius: "50%",
  background: `${hue}26`,
  color: hue,
  fontSize: 11,
  fontWeight: 700,
  fontFamily: grotesk,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
});

const tierPill = (tier) => ({
  display: "inline-block",
  fontSize: 10.5,
  fontWeight: 700,
  padding: "2px 8px",
  borderRadius: 6,
  color: tier.color,
  background: "var(--panel2)",
  whiteSpace: "nowrap",
});

// #66's compliance badge, deliberately built out of the tier chip above rather than a
// new visual language — one more pill on a row people already read, not a new column.
//
// The tones reuse the app's existing status palette so a clearance gap is the same red
// as an over-ceiling CLIN. `slate` is the one that earns its own entry: "unchecked" has
// to be visible and has to *not* look like a pass, because it is the day-one state of
// every person on the grid and a quiet badge there would be a clean bill of health
// nobody issued.
const COMPLIANCE_TONES = {
  red: { color: "var(--bad)", border: "var(--bad)" },
  amber: { color: "var(--warn)", border: "var(--warn)" },
  slate: { color: "var(--dim)", border: "var(--border)" },
  blue: { color: "var(--accent)", border: "var(--accent)" },
  green: { color: "var(--good)", border: "var(--border)" },
};

const compliancePill = (tone) => ({
  display: "inline-block",
  fontSize: 10,
  fontWeight: 700,
  fontFamily: grotesk,
  padding: "2px 7px",
  borderRadius: 6,
  cursor: "pointer",
  background: "transparent",
  color: (COMPLIANCE_TONES[tone] || COMPLIANCE_TONES.slate).color,
  border: `1px solid ${(COMPLIANCE_TONES[tone] || COMPLIANCE_TONES.slate).border}`,
  whiteSpace: "nowrap",
});

// Why an LCAT didn't resolve, in words (#64). The backend classifies (see
// `lcat.py`); this only phrases it. Every branch has to name a *fix*, because the
// whole complaint about the old flag was that it described a problem and stopped.
function causeText(x) {
  switch (x?.cause) {
    case "clin_unpriced":
      return "this CLIN has no rate table at all — import the rate schedule";
    case "clin_unburdened":
      return "this CLIN's rates are unburdened direct rates — there is no billable rate to map to";
    case "priced_elsewhere":
      return `priced on CLIN ${x.priced_on || "another line"}, not the one it's charged to`;
    case "ambiguous_rate_line":
      return "two rate lines match it at different rates — pick one";
    case "no_rate_line":
      return x.suggestion
        ? `no rate line matches; closest is "${x.suggestion.lcat}"`
        : "no rate line on this contract matches it";
    default:
      return "no matching rate line";
  }
}

export default function AllocationMatrix({
  contractId,
  setActiveId,
  autoBalance,
  onAutoBalanced,
  focusPerson,
  onFocusedPerson,
}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  // draft[empId][clinId] = hrs/wk. The editable overlay on the synced actuals.
  const [draft, setDraft] = useState(null);
  // Spreadsheet-style pivots on the roster: filter to one CLIN's chargers, a
  // free-text name/LCAT search, and a click-to-sort column.
  const [clinFilter, setClinFilter] = useState(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState({ key: null, dir: "desc" });
  // Arriving from the Flight Deck's "who's running hot" strip (#83): put that person
  // in view. Consumed once and cleared, so a later manual visit isn't still filtered.
  useEffect(() => {
    if (!focusPerson) return;
    setQuery(focusPerson);
    setClinFilter(null);
    onFocusedPerson?.();
  }, [focusPerson]);
  // What-if roster edits: `added` are planned new people, `removed` are ids rolled
  // off the plan. Both are simulation-only — they change no employee record — but they
  // do go into `plans.data` on save, and Discard puts the roster back.
  const [added, setAdded] = useState([]);
  const [removed, setRemoved] = useState([]);
  const [newPerson, setNewPerson] = useState(null); // the open "add person" form
  const [addPersonError, setAddPersonError] = useState(null);
  const [directory, setDirectory] = useState({ people: [], qualVocab: {}, utilization: {}, loading: false, error: null });
  // #66 — the open compliance/quals panel: `{ row, draft, saving, error }`. `draft` is
  // null until the user types, so an untouched panel saves nothing and a blank field
  // stays the difference between "cleared" and "never entered".
  const [qualsPanel, setQualsPanel] = useState(null);
  // #85 — dated absence. Two tiers, deliberately:
  //   `absences` is the what-if list: PTO, start and roll-off dates typed into *this*
  //   plan. Client-side and saved into `plans.data`, like `added` / `removed`.
  //   The contract's own committed absences and its holiday calendar live server-side
  //   (they are what bends the Flight Deck's chart, which cannot read plan data) and
  //   are edited through the panel below.
  const [absences, setAbsences] = useState([]);
  const [absenceFor, setAbsenceFor] = useState(null); // person whose editor is open
  const [holidaysOpen, setHolidaysOpen] = useState(false);
  const [holidayBusy, setHolidayBusy] = useState(false);
  // Saved plans (persisted server-side): the list, the open save-name form, and
  // which plan is currently loaded.
  const [plans, setPlans] = useState([]);
  const [planName, setPlanName] = useState(null);
  const [loadedPlan, setLoadedPlan] = useState(null);
  // Its name, held alongside the id rather than looked up in `plans` — the header names
  // the loaded plan continuously, and the list is refetched after every save, so a
  // lookup would blank the name for the length of that round trip.
  const [loadedPlanName, setLoadedPlanName] = useState(null);
  // #62 — the fingerprint of the loaded plan as it sits on the server, so "unsaved
  // changes" can mean "differs from the saved plan" instead of "differs from the
  // actuals". Without it every loaded plan read as unsaved forever.
  const [savedFp, setSavedFp] = useState(null);
  const [saveBusy, setSaveBusy] = useState(false);
  // Side-by-side plan comparison.
  const [comparing, setComparing] = useState(false);
  const [cmpA, setCmpA] = useState("current");
  const [cmpB, setCmpB] = useState("current");
  const [plansMenuOpen, setPlansMenuOpen] = useState(false);
  // The plan the delete dialog is asking about (#67). Held as the row itself, not an
  // id, so the dialog can name and date what is about to go.
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  // LCAT → rate-line mapping (#64). `rateLines` is every line in play on this
  // contract plus the mappings already saved; `mapping` is the open affordance
  // ({ lcat, clinId, cause, suggestion, priced_on }), which is what the ⚠ opens
  // instead of a dead-end tooltip. `mapResult` holds the before/after the server
  // returns, because a mapping re-resolves burn and the user has to see it move.
  const [rateLines, setRateLines] = useState({ rate_lines: [], aliases: [] });
  const [mapping, setMapping] = useState(null);
  const [mapTarget, setMapTarget] = useState("");
  const [mapBusy, setMapBusy] = useState(false);
  const [mapResult, setMapResult] = useState(null);
  // The contract's utilisation target (#84) — the open editor and its in-flight save.
  const [targetOpen, setTargetOpen] = useState(false);
  const [targetDraft, setTargetDraft] = useState("");
  const [targetBusy, setTargetBusy] = useState(false);

  // No contract picked yet — fall back to the newest, like the other views.
  useEffect(() => {
    if (contractId || !setActiveId) return;
    listContracts()
      .then((cs) => cs.length && setActiveId(cs[0].id))
      .catch((e) => setError(e.message));
  }, [contractId, setActiveId]);

  useEffect(() => {
    if (!contractId) return;
    setData(null);
    setDraft(null);
    setAdded([]);
    setRemoved([]);
    setAbsences([]);
    setLoadedPlan(null);
    setLoadedPlanName(null);
    setSavedFp(null);
    getAllocation(contractId)
      .then((d) => {
        setData(d);
        setDraft(buildDraft(d));
      })
      .catch((e) => setError(e.message));
  }, [contractId]);

  const refreshPlans = () => {
    if (contractId) listPlans(contractId).then(setPlans).catch(() => setPlans([]));
  };
  useEffect(refreshPlans, [contractId]);

  const refreshRateLines = () => {
    if (!contractId) return;
    getLcatRates(contractId)
      .then(setRateLines)
      .catch(() => setRateLines({ rate_lines: [], aliases: [] }));
  };
  useEffect(refreshRateLines, [contractId]);

  // Re-read the allocation after a rate change (a mapping applied/removed, or a
  // rate schedule imported). Deliberately leaves `draft` / `added` / `removed`
  // alone: a mapping changes what an hour *bills at*, not who is working which
  // hours, so blowing away an in-progress what-if plan would be a worse surprise
  // than the flag we just fixed.
  async function reloadRates() {
    if (!contractId) return;
    try {
      const d = await getAllocation(contractId);
      setData(d);
      if (!draft) setDraft(buildDraft(d));
      refreshRateLines();
    } catch (e) {
      setError(e.message);
    }
  }

  // Expected-hours editor (#84). Saving re-reads the allocation through the same
  // path a rate change uses, and for the same reason: the target changes what a full
  // week *means*, not who is working which hours, so an in-progress what-if survives
  // it. Every utilisation figure and the forward projection move visibly.
  async function saveTarget(raw) {
    if (!contractId) return;
    setTargetBusy(true);
    try {
      await setContractCapacity(contractId, { utilization_target: raw });
      setTargetOpen(false);
      await reloadRates();
    } catch (e) {
      setError(e.message);
    } finally {
      setTargetBusy(false);
    }
  }

  // #85 — the contract's holiday calendar. Saved server-side and refetched through
  // the same path a target change uses, for the same reason: a holiday changes what
  // the forward projection *is*, not who is working which hours, so an in-progress
  // what-if survives it and every runway figure on screen moves visibly.
  async function saveHolidays(body) {
    if (!contractId) return;
    setHolidayBusy(true);
    try {
      await setContractAbsence(contractId, body);
      await reloadRates();
    } catch (e) {
      setError(e.message);
    } finally {
      setHolidayBusy(false);
    }
  }

  // Plan-level absence: a what-if, so it lives in React state and rides into
  // `plans.data` on save. Nothing here writes to the server — a typed "what if Priya
  // takes August off" is a question, not a fact about the contract, and it must not
  // bend the Flight Deck's chart for everyone who opens it on the strength of a
  // question. Committing it is a separate, deliberate act; see `commitAbsence`.
  function addAbsence(entry) {
    setAbsences((list) => [...list, entry]);
  }
  function removeAbsence(target) {
    setAbsences((list) => list.filter((a) => a !== target));
  }

  // Promote a what-if into a fact about the contract. This is the *only* path by
  // which a person's absence reaches the burn engine — the engine cannot read plan
  // data, so an uncommitted absence bends this view's runway and nothing else. Once
  // committed it moves the Flight Deck's projection for everyone.
  //
  // It leaves the plan on the way out: leaving it in both lists would show the same
  // range twice in the chip strip. The projection would still be right (the day sets
  // are unioned, never summed) but the UI would be lying about how many entries exist.
  async function commitAbsence(entry) {
    setHolidayBusy(true);
    try {
      await setContractAbsence(contractId, {
        absences: [...contractAbsence.absences, entry],
      });
      removeAbsence(entry);
      await reloadRates();
    } catch (e) {
      setError(e.message);
    } finally {
      setHolidayBusy(false);
    }
  }

  // And back out again. A committed absence is contract data, so withdrawing it is a
  // server write too — it must not silently become a plan-local what-if again.
  async function withdrawAbsence(entry) {
    setHolidayBusy(true);
    try {
      await setContractAbsence(contractId, {
        absences: contractAbsence.absences.filter(
          (a) =>
            !(
              a.person_id === entry.person_id &&
              a.start === entry.start &&
              a.end === entry.end
            )
        ),
      });
      await reloadRates();
    } catch (e) {
      setError(e.message);
    } finally {
      setHolidayBusy(false);
    }
  }

  // Saved mappings, keyed for lookup so a mapped LCAT can offer "remove" instead
  // of "apply". Keys are the raw source strings the API stores.
  const aliasBySource = useMemo(() => {
    const m = {};
    for (const a of rateLines.aliases || []) m[(a.from || "").trim().toLowerCase()] = a;
    return m;
  }, [rateLines]);

  function openMapping(lcat, clinId, cell) {
    const existing = aliasBySource[(lcat || "").trim().toLowerCase()];
    setMapResult(null);
    setMapping({
      lcat,
      clinId,
      cause: cell?.cause || null,
      suggestion: cell?.suggestion || null,
      priced_on: cell?.priced_on || null,
      existing: existing || null,
    });
    // Pre-select the best available target: an existing mapping, the engine's
    // suggestion, or the CLIN that already prices this LCAT (cause B). Never
    // auto-*applied* — a fuzzy match has to be confirmed by a human before it can
    // move a dollar (see lcat.py).
    const pick =
      (existing && `${existing.clin || ""}|${existing.lcat}`) ||
      (cell?.suggestion && `${cell.suggestion.clin}|${cell.suggestion.lcat}`) ||
      (cell?.priced_on ? `${cell.priced_on}|${lcat}` : "");
    setMapTarget(pick || "");
  }

  async function applyMapping() {
    if (!mapping || !mapTarget) return;
    const [clin, ...rest] = mapTarget.split("|");
    setMapBusy(true);
    try {
      const r = await setLcatAlias(contractId, {
        source: mapping.lcat,
        lcat: rest.join("|"),
        clin: clin || null,
      });
      setMapResult(r);
      await reloadRates();
    } catch (e) {
      setError(e.message);
    } finally {
      setMapBusy(false);
    }
  }

  async function removeMapping() {
    if (!mapping) return;
    setMapBusy(true);
    try {
      const r = await deleteLcatAlias(contractId, mapping.lcat);
      setMapResult(r);
      setMapping((m) => (m ? { ...m, existing: null } : m));
      await reloadRates();
    } catch (e) {
      setError(e.message);
    } finally {
      setMapBusy(false);
    }
  }

  const clins = data?.clins || [];
  const employees = data?.employees || [];
  const { current_week: cw, total_weeks: tw } = data?.contract || {};
  const activeNewClin = clins.find((c) => c.id === newPerson?.clin) || clins[0] || null;
  const newPersonRateOptions = rateOptions(activeNewClin);
  const selectedRateOption = newPersonRateOptions.find((line) => line.lcat === newPerson?.lcatChoice);
  const directoryMatches = (directory.people || []).filter((person) => {
    const q = (newPerson?.search || "").trim().toLowerCase();
    return !q || [person.name, person.employee_id, ...(person.lcats || [])].some((x) =>
      (x || "").toLowerCase().includes(q)
    );
  });

  // The effective roster this plan runs on: synced people minus those rolled off,
  // plus any planned adds.
  const roster = useMemo(
    () => [...employees.filter((e) => !removed.includes(e.id)), ...added],
    [employees, added, removed]
  );

  // #84 — what this contract expects of a full-time person, resolved server-side and
  // used to seed planned adds. Present on every contract, since the chain always
  // terminates in a labelled 40-hr fallback.
  const contractExpected = data?.contract?.expected_hours || null;

  // #85's contract-level absence: the holiday calendar and any committed absences,
  // resolved server-side so the matrix and the Flight Deck's chart bend around the
  // same list. Always present in shape, empty on a contract nobody has configured.
  const contractAbsence = useMemo(
    () => data?.contract?.absence || { holidays: [], absences: [] },
    [data]
  );

  // The active period's calendar edges. A "starts on" absence runs from the period
  // start to the day before they arrive and a "rolls off" one runs from their last
  // day to the period end, so both reduce to the same dated range PTO uses and the
  // projection keeps a single code path (#85).
  const periodBounds = {
    start: data?.contract?.pop_start || "",
    end: data?.contract?.pop_end || "",
  };

  // The dated range the open entry would store, and why it can't be stored yet.
  // Derived rather than validated at click time, so the button can be disabled *and*
  // say what is missing — a disabled button with no explanation reads as broken.
  const absenceRange = (() => {
    if (!absenceFor) return null;
    const { kind, start, end } = absenceFor;
    if (kind === "start")
      // Away from the period's start until the day before they arrive.
      return { start: periodBounds.start, end: start ? shiftDate(start, -1) : "" };
    if (kind === "roll_off")
      // Away from the day after their last day until the period's end.
      return { start: start ? shiftDate(start, 1) : "", end: periodBounds.end };
    return { start, end };
  })();
  const absenceProblem = (() => {
    if (!absenceFor || !absenceRange) return null;
    const single = absenceFor.kind !== "pto";
    if (single && !absenceFor.start) return "Pick a date.";
    if (!single && !absenceFor.start) return "Pick the first day out.";
    if (!single && !absenceFor.end) return "Pick the last day out.";
    if (!absenceRange.start || !absenceRange.end)
      return "This contract's period has no dates, so absence can't be placed on a week.";
    if (absenceRange.end < absenceRange.start)
      return single
        ? `That date is outside the period (${periodBounds.start} – ${periodBounds.end}).`
        : "The last day is before the first day.";
    if (!absenceWorkdays(absenceRange))
      return "That range is all weekend — nobody charges those days.";
    return null;
  })();

  // Everything the simulator scored against: the contract's committed absences plus
  // this plan's what-ifs. The two are stored differently and scored identically.
  const allAbsences = useMemo(
    () => [...contractAbsence.absences, ...absences],
    [contractAbsence, absences]
  );

  // One person's expected week. Synced people carry their own resolution (which may
  // be their personal override); a planned add inherits the contract's.
  const expectedOf = (e) =>
    e?.expected ||
    contractExpected || {
      hours: FTE_HOURS_PER_WEEK,
      level: "fallback",
      label: "a 40-hour week, assumed — nothing is set",
      assumed: true,
    };

  // Rate resolver for a given set of planned adds: LCAT-resolved $/hr per person
  // per CLIN, blended-rate fallback. Pure so it can score any plan, not just live.
  // Shared with the Flight Deck's drift card (#67), so the two surfaces cannot put
  // different dollar figures on the same staffing gap.
  const makeRate = (addedX) => rateResolver({ clins, employees, added: addedX });

  // Score a plan state ({draft, added, removed, absences}) into per-CLIN runway +
  // totals — the whole point of the view, and reused to compare saved plans.
  const evalPlan = (state) => {
    const dr = state.draft || {};
    const rost = [
      ...employees.filter((e) => !(state.removed || []).includes(e.id)),
      ...(state.added || []),
    ];
    const rate = makeRate(state.added);
    // #85 — dated absence for this plan state. The contract's committed absences and
    // holidays are scored alongside the plan's what-ifs, because the arithmetic is
    // identical and only the storage differs (see server/app/absence.py). Built per
    // plan state rather than once, so the compare panel scores each side against its
    // own saved absences instead of both against the live ones.
    const model = buildAbsenceModel({
      popStart: data?.contract?.pop_start,
      fromWeek: cw,
      totalWeeks: tw,
      holidays: contractAbsence.holidays,
      absences: [...contractAbsence.absences, ...(state.absences || [])],
    });
    const clin = {};
    let totalWeekly = 0;
    let totalHrs = 0;
    for (const c of clins) {
      let weekly = 0;
      // Per-person contributions, kept apart from the total because absence is per
      // person: one charger being out reduces only their share of the week.
      const perPerson = [];
      for (const e of rost) {
        const amt = (dr[e.id]?.[c.id] || 0) * rate(e.id, c.id);
        weekly += amt;
        if (amt > 0) perPerson.push([e.id, amt]);
      }
      let exhaustWeek = null;
      let runwayDays = null;
      if (weekly > 0) {
        // With no absence ahead, keep the original closed-form arithmetic rather
        // than a week walk that agrees to within a rounding error. One code path has
        // been producing correct numbers since #21; a second one that *usually*
        // matches is a worse trade than the branch.
        const walked = model.active
          ? walkRunway({ perPerson, remaining: c.remaining, currentWeek: cw, model })
          : null;
        const weeksLeft = walked ? walked.weeksLeft : c.remaining / weekly;
        if (weeksLeft != null) {
          exhaustWeek = cw + weeksLeft;
          runwayDays = Math.max(0, Math.round(weeksLeft * 7));
        }
      }
      clin[c.id] = {
        weekly,
        exhaustWeek,
        runwayDays,
        status: weekly > 0 ? simStatus(exhaustWeek, tw, c, weekly, cw) : "paused",
        ceilingBreached: ceilingBreachedFor(c, weekly, cw, tw),
        fundsExceeded: fundsExceededFor(c),
      };
      totalWeekly += weekly;
    }
    for (const e of rost) for (const c of clins) totalHrs += dr[e.id]?.[c.id] || 0;
    // The team's expected hours, summed per person — so team utilisation divides by
    // what this roster is actually expected to work rather than by 40 × headcount.
    // Summing across *people* is sound; the thing that isn't is summing one person's
    // expectations across contracts (see capacity.portfolio_expected).
    const totalExpected = rost.reduce((s, e) => s + (expectedOf(e).hours || 0), 0);
    return {
      clin,
      totalWeekly,
      totalHrs,
      totalExpected,
      headcount: rost.length,
      // Carried out so every runway figure this scored can say it is not
      // `hrs/wk × weeks`. An accountant checks that by hand, and a projection that
      // quietly differs from it reads as an arithmetic bug (#85's last criterion).
      absence: model,
    };
  };

  const rateFor = useMemo(() => makeRate(added), [clins, employees, added]);
  const current = useMemo(
    () => evalPlan({ draft, added, removed, absences }),
    [draft, added, removed, absences, contractAbsence, employees, clins, cw, tw]
  );
  const sim = current.clin;
  const totalWeekly = current.totalWeekly;
  const totalHrs = current.totalHrs;
  // Two questions, not one (#62). `dirty` — is anything modelled on top of the synced
  // actuals, i.e. does Discard have something to throw away. `unsaved` — does what's
  // on screen differ from the plan it was loaded from, i.e. is a save pending. The
  // old code answered the first and displayed it as though it answered the second.
  const fingerprint = useMemo(
    () => planFingerprint({ draft, added, removed, absences }),
    [draft, added, removed, absences]
  );
  const actualsFp = useMemo(
    () => planFingerprint({ draft: data ? buildDraft(data) : {} }),
    [data]
  );
  const dirty = fingerprint !== actualsFp;
  const unsaved = isUnsaved({
    fingerprint,
    savedFingerprint: savedFp,
    loadedPlanId: loadedPlan,
    dirty,
  });

  // A third question (#67): has the *contract* moved under a saved plan? Not an edit
  // and not a sync — a mod that re-funded a CLIN, an imported rate schedule, an
  // exercised option year. Those change what a saved plan's numbers mean while its
  // name stays the same. Derived per plan rather than stored, so the badge is a
  // reading of the live contract and can never itself go stale.
  const liveSnapshot = useMemo(() => scoringSnapshot(data), [data]);
  const staleReasons = useMemo(() => {
    const m = {};
    for (const p of plans)
      m[p.id] = snapshotChanges(p.data?.scored_against, liveSnapshot, p.data);
    return m;
  }, [plans, liveSnapshot]);
  const loadedStale = (loadedPlan && staleReasons[loadedPlan]) || [];

  // The active baseline (#67 item 1) — the one saved plan this contract is being run
  // against, as opposed to the ten what-ifs sitting beside it. Read off the list
  // rather than held in its own state: the server owns which plan it is (one per
  // contract, enforced by a unique index), and a second copy here would be the thing
  // that goes stale after somebody designates a different one.
  const baseline = plans.find((p) => p.is_baseline) || null;
  // Is the grid showing reality rather than a what-if? Not simply "no plan loaded":
  // an unnamed what-if typed straight onto the actuals has no plan id either, and
  // ticking that as "what's running now" would be the same lie #62 fixed.
  const onActuals = !loadedPlan && !dirty;

  // #67 item 2 — drift. Once a baseline exists the question stops being "what if"
  // and becomes "are we running what we committed to", which is one comparison:
  // the baseline's hours against the synced actuals, priced with the same resolver
  // the matrix scores plans with so drift dollars and plan dollars agree.
  //
  // Deliberately compared against the *actuals*, not against whatever is on screen.
  // Drift is a fact about reality; scoring it against a half-typed what-if would
  // make it move while you edit and mean nothing by the time you stopped.
  const actualsState = useMemo(
    () => ({ draft: data ? buildDraft(data) : {} }),
    [data]
  );
  const drift = useMemo(() => {
    if (!baseline) return null;
    const names = new Map(employees.map((e) => [String(e.id), e.name]));
    const rate = makeRate(baseline.data?.added || []);
    return planDrift({
      baseline: baseline.data || {},
      actuals: actualsState,
      rate,
      nameOf: (id) => names.get(String(id)),
    });
  }, [baseline, actualsState, employees, clins]);
  const driftLine = useMemo(() => driftSummary(drift), [drift]);

  // What the gap has cost in runway, per CLIN — the number that turns "18% above
  // baseline" into something with a deadline attached. Scored through evalPlan, so
  // it carries the same absence and funding arithmetic as every other runway figure
  // on the page rather than a second, simpler model of the same thing.
  const driftRunway = useMemo(() => {
    if (!baseline) return {};
    const b = evalPlan(baseline.data || {});
    const a = evalPlan(actualsState);
    const out = {};
    for (const c of clins) {
      const bd = b.clin[c.id]?.runwayDays;
      const ad = a.clin[c.id]?.runwayDays;
      if (bd != null && ad != null) out[c.id] = ad - bd;
    }
    return out;
  }, [baseline, actualsState, clins, employees, contractAbsence, cw, tw]);
  const [showDrift, setShowDrift] = useState(false);
  const [baselineBusy, setBaselineBusy] = useState(false);
  const [baselineErr, setBaselineErr] = useState(null);

  // Designate or stand down, from the plans menu. Refetches rather than patching the
  // list locally, because designating is a swap — the plan that lost the baseline is
  // also changing, and guessing which one that was is how the menu ends up showing
  // two baselines.
  async function toggleBaseline(plan) {
    if (baselineBusy) return;
    setBaselineBusy(true);
    setBaselineErr(null);
    try {
      if (plan.is_baseline) await clearBaselinePlan(contractId, plan.id);
      else await setBaselinePlan(contractId, plan.id);
      refreshPlans();
    } catch (e) {
      setBaselineErr(e.message || "Could not change the baseline.");
    } finally {
      setBaselineBusy(false);
    }
  }

  // Per-person avatar hue keyed to roster order, so a person keeps their color
  // regardless of filtering/sorting.
  const hueOf = useMemo(() => {
    const m = {};
    roster.forEach((e, i) => (m[e.id] = hueFor(i)));
    return (id) => m[id] || hueFor(0);
  }, [roster]);

  const rowWeeklyOf = (e) =>
    clins.reduce((s, c) => s + (draft?.[e.id]?.[c.id] || 0) * rateFor(e.id, c.id), 0);
  const rowHrsOf = (e) => clins.reduce((s, c) => s + (draft?.[e.id]?.[c.id] || 0), 0);

  // Utilisation: hours against this person's expected week. 1 means fully utilised.
  // null when there is nothing to divide by, which renders as "—" rather than 0% —
  // an unset expectation is missing information, and showing it as idle is a claim.
  const utilOf = (e) => {
    const hours = expectedOf(e).hours;
    return hours > 0 ? rowHrsOf(e) / hours : null;
  };

  // Someone "charges" a CLIN if they logged hours there originally or have any in
  // the current plan — the set the CLIN-card filter narrows the roster to.
  const chargesClin = (e, clinId) =>
    e.cells?.[clinId] != null || (draft?.[e.id]?.[clinId] || 0) > 0;

  // The rows actually rendered: CLIN filter, then name/LCAT search, then sort.
  const visible = useMemo(() => {
    let list = clinFilter ? roster.filter((e) => chargesClin(e, clinFilter)) : roster;
    const q = query.trim().toLowerCase();
    if (q)
      list = list.filter((e) =>
        [e.name, e.id, e.lcat].some((v) => (v || "").toLowerCase().includes(q))
      );
    if (sort.key) {
      const val = (e) =>
        sort.key === "name"
          ? (e.name || "").toLowerCase()
          : sort.key === "lcat"
            ? (e.lcat || "").toLowerCase()
            : sort.key === "rate"
              ? e.rate || 0
              : sort.key === "weekly"
                ? rowWeeklyOf(e)
                : sort.key === "util"
                  ? // Sorts on the ratio now, not raw hours: a 32-hr person at 32
                    // hours outranks a 40-hr person at 34, which is the point of the
                    // column and was not true when everyone shared a denominator.
                    (utilOf(e) ?? -1)
                  : draft?.[e.id]?.[sort.key] || 0;
      list = [...list].sort((a, b) => {
        const va = val(a);
        const vb = val(b);
        const c = va < vb ? -1 : va > vb ? 1 : 0;
        return sort.dir === "asc" ? c : -c;
      });
    }
    return list;
  }, [roster, clinFilter, query, sort, draft, rateFor]);

  const filtered = clinFilter != null || query.trim() !== "";
  const clinFilterCode = clins.find((c) => c.id === clinFilter)?.code;

  // Text columns default A→Z, numeric high→low.
  const defaultDir = (key) => (key === "name" || key === "lcat" ? "asc" : "desc");
  // Cycle per header: unsorted → default dir → opposite dir → unsorted.
  function toggleSort(key) {
    setSort((s) => {
      if (s.key !== key) return { key, dir: defaultDir(key) };
      if (s.dir === defaultDir(key))
        return { key, dir: defaultDir(key) === "asc" ? "desc" : "asc" };
      return { key: null, dir: "desc" };
    });
  }
  const clearSort = () => setSort({ key: null, dir: "desc" });
  // A persistent sort glyph on every sortable header: dim "⇅" so it's clearly
  // clickable, a bold accent ▲/▼ on the column currently sorted.
  const sortGlyph = (key) => {
    const active = sort.key === key;
    return (
      <span
        style={{
          marginLeft: 4,
          fontSize: 9,
          color: active ? "var(--accent)" : "var(--faint)",
          opacity: active ? 1 : 0.6,
        }}
      >
        {active ? (sort.dir === "asc" ? "▲" : "▼") : "⇅"}
      </span>
    );
  };

  function showAll() {
    setClinFilter(null);
    setQuery("");
  }

  // Export exactly what's on screen (respects filter/search) as a CSV — the
  // hand-off a PM would drop into their own staffing spreadsheet.
  function exportCsv() {
    const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const header = [
      "Employee",
      "Employee ID",
      "LCAT",
      "Rate ($/hr)",
      ...clins.map((c) => `${c.code} hrs/wk`),
      "Weekly $",
    ];
    const body = visible.map((e) => [
      e.name,
      e.id,
      e.lcat || "",
      e.rate ?? "",
      ...clins.map((c) => draft?.[e.id]?.[c.id] || 0),
      Math.round(rowWeeklyOf(e)),
    ]);
    // Per-CLIN weekly $ summed over the VISIBLE rows, so the CSV ties out to itself.
    const clinTotals = clins.map((c) =>
      Math.round(
        visible.reduce((s, e) => s + (draft?.[e.id]?.[c.id] || 0) * rateFor(e.id, c.id), 0)
      )
    );
    const totalRow = [
      "Forward weekly burn →",
      "",
      "",
      "",
      ...clinTotals,
      clinTotals.reduce((s, x) => s + x, 0),
    ];
    const csv = [header, ...body, [], totalRow].map((r) => r.map(esc).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8;" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `allocation-${data.contract.piid || data.contract.name || "contract"}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function setCell(empId, clinId, value) {
    const v = value === "" ? 0 : Math.max(0, Math.min(80, +value || 0));
    setDraft((d) => ({ ...d, [empId]: { ...d[empId], [clinId]: v } }));
  }

  function reset() {
    if (data) setDraft(buildDraft(data));
    setAdded([]);
    setRemoved([]);
    setAbsences([]);
    setNewPerson(null);
    setLoadedPlan(null);
    setLoadedPlanName(null);
    setSavedFp(null);
  }

  // Discarding is destructive and it was the *only* prominent affordance on a dirty
  // grid (#62) — the discoverable way out of an edited plan was to throw it away. It
  // stays, behind a confirm, now that Save sits next to it.
  function discardChanges() {
    if (!dirty) return;
    const warning =
      loadedPlanName && unsaved
        ? `Discard unsaved changes to “${loadedPlanName}”? The saved plan itself is kept.`
        : "Discard this what-if and go back to the synced actuals?";
    if (!window.confirm(warning)) return;
    reset();
  }

  // Back out of a plan to what is actually running. The same `reset()` Discard uses,
  // reached from the plans menu and framed as a destination rather than as a loss:
  // "Discard changes" is the right name for throwing away an edit and the wrong one
  // for "show me the synced actuals again", which is a place you go, not damage you
  // do. It confirms only when there is genuinely something unsaved to lose —
  // switching back from a cleanly-saved plan costs nothing and shouldn't ask.
  function showActuals() {
    if (!loadedPlan && !dirty) return;
    if (unsaved) {
      const warning = loadedPlanName
        ? `Go back to the synced actuals? Unsaved changes to “${loadedPlanName}” are lost — the saved plan itself is kept.`
        : "Go back to the synced actuals? This unsaved what-if is lost.";
      if (!window.confirm(warning)) return;
    }
    reset();
  }

  // Persist the current sim state (grid + adds + removals + absence) under a name.
  // A loaded plan saves over itself; `asNew` is the deliberate fork. Before #62 every
  // save created, so editing a loaded plan and saving silently left two same-named
  // plans and no way to tell which one was meant.
  async function doSavePlan({ asNew = false } = {}) {
    const typed = (planName || "").trim();
    const updating = Boolean(loadedPlan) && !asNew;
    const nm = typed || (updating ? loadedPlanName : "") || "Untitled plan";
    // Snapshot what we send, not what's on screen when the response lands: an edit
    // made mid-flight must still read as unsaved afterwards.
    //
    // `scored_against` rides along (#67): the contract terms these hours were priced
    // under, so a reload after a mod can say the plan's numbers no longer mean what
    // they meant. It is deliberately outside `planFingerprint` — the terms moving is
    // not an edit the user made, and lighting up Save because a mod landed would put
    // us back where #62 started.
    const sent = { draft, added, removed, absences, scored_against: liveSnapshot };
    const sentFp = fingerprint;
    setSaveBusy(true);
    try {
      const saved = updating
        ? await updatePlan(contractId, loadedPlan, nm, sent)
        : await savePlan(contractId, nm, sent);
      setPlanName(null);
      setLoadedPlan(saved.id);
      setLoadedPlanName(saved.name || nm);
      setSavedFp(sentFp);
      refreshPlans();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaveBusy(false);
    }
  }

  // The header's Save button: a loaded plan saves straight over itself, an unnamed
  // what-if needs a name first, which is the one thing that still opens the menu.
  function promptSave() {
    if (loadedPlan) return doSavePlan();
    setPlanName("");
    setPlansMenuOpen(true);
  }

  // Reload a saved plan into the simulation.
  function loadPlan(id) {
    const plan = plans.find((p) => p.id === Number(id));
    if (!plan) return;
    const d = plan.data || {};
    setDraft(d.draft || buildDraft(data));
    setAdded(d.added || []);
    setRemoved(d.removed || []);
    // A plan saved before #85 has no `absences` key, and it must load as "no
    // absences" and score exactly as it scored when it was saved — never inheriting
    // whatever what-if absences are on screen right now. Same staleness trap #67
    // item 5 names: a plan scored against assumptions it never had is worse than no
    // plan. (The contract's *committed* absences and holidays do apply to every
    // plan, old and new — those are facts about the contract, not about the plan.)
    setAbsences(d.absences || []);
    // Planned-add ids used to be a per-session counter, which meant a reload had to
    // push the counter past whatever the plan held — and two plans saved in separate
    // sessions still both owned `added-0`, so comparing them read two different
    // people as one. Ids are minted unique now (see plans.newAddedId), so there is
    // nothing left to reseed.
    setLoadedPlan(plan.id);
    setLoadedPlanName(plan.name);
    // What's on screen now IS the saved plan, so nothing is pending (#62).
    setSavedFp(planFingerprint({ ...d, draft: d.draft || (data ? buildDraft(data) : {}) }));
  }

  // Deleting is the one irreversible thing in this view and it sat on a bare × next
  // to the button you click to *load* a plan — a 26px miss threw the work away with
  // no undo. It asks first now, through the app's own dialog rather than the
  // browser's: the question a mis-click raises is "which plan is this?", and only a
  // dialog that can name the plan and date it answers that.
  function requestDeletePlan(id) {
    setDeleteError(null);
    setPendingDelete(plans.find((p) => p.id === id) || null);
  }

  async function confirmDeletePlan() {
    if (!pendingDelete) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await deletePlan(contractId, pendingDelete.id);
      if (loadedPlan === pendingDelete.id) {
        // The grid keeps the deleted plan's numbers — they're still a valid what-if —
        // but it is no longer anybody's saved plan, so Save has to name a new one.
        setLoadedPlan(null);
        setLoadedPlanName(null);
        setSavedFp(null);
      }
      setPendingDelete(null);
      refreshPlans();
    } catch (e) {
      // Kept in the dialog rather than thrown up to the page banner: the failure
      // belongs next to the button that caused it, and the plan is still there.
      setDeleteError(e.message);
    } finally {
      setDeleteBusy(false);
    }
  }

  // Any labor CLIN not already finishing on plan. A mid-flight tool should let
  // you course-correct BEFORE a line blows its budget, so this covers hot lines
  // (over / watch / funding-due) and slow ones (under) — not just breaches.
  // `fee_eroding` (#81) refines what used to come back as `ok` or `watch`, so without
  // it here a CLIN eating its fee would *leave* this set the moment the state shipped —
  // and losing fee to an overrun is exactly a line worth course-correcting.
  const OFF_PACE = new Set(["over", "watch", "funding", "under", "fee_eroding"]);
  const offPaceClins = clins.filter((c) => OFF_PACE.has(sim[c.id]?.status));
  const HOURS_CAP = 50; // don't suggest booking anyone past ~50 hrs/wk

  // Rebalance every off-pace CLIN so its funds land exactly at PoP end
  // (weekly = remaining ÷ weeks left): scale < 1 trims a line burning too hot,
  // scale > 1 boosts one running too slow. Loads into the editable grid.
  function applyBalance() {
    const weeksRemaining = Math.max(1, (tw || 1) - (cw || 0));
    setDraft((d) => {
      const nd = { ...d };
      for (const c of offPaceClins) {
        const weekly = sim[c.id]?.weekly || 0;
        if (weekly <= 0) continue; // nothing charging yet — can't scale it
        const target = Math.max(0, c.remaining) / weeksRemaining;
        const scale = target / weekly;
        for (const e of roster) {
          const cur = d?.[e.id]?.[c.id] || 0;
          if (cur > 0)
            nd[e.id] = {
              ...(nd[e.id] || d[e.id] || {}),
              [c.id]: Math.max(0, Math.min(HOURS_CAP, Math.round(cur * scale))),
            };
        }
      }
      return nd;
    });
    setLoadedPlan(null);
    setLoadedPlanName(null);
    setSavedFp(null);
  }

  // Apply #63's solved move list into the grid, exactly as the suggestion listed it.
  //
  // This replaces the uniform scale for the tripwire path. `applyBalance` above stays
  // as the manual "⚡ Balance to finish on plan" toolbar action, because scaling every
  // line to land at PoP end is still a legitimate thing to ask for on purpose — it is
  // just the wrong thing to hand someone who asked "what do I do about CLIN 0002",
  // since it silently trims people who are already at their expected hours.
  //
  // The moves are applied verbatim. Nothing is re-derived here: the target hours, the
  // floors and the destination CLIN were all decided server-side, and recomputing any
  // of them client-side is how the button and the bullet list above it would start
  // disagreeing.
  function applyMoves(moves) {
    setDraft((d) => {
      const nd = { ...d };
      const cell = (id) => nd[id] || d[id] || {};
      for (const m of moves) {
        const to = Math.max(0, Math.min(HOURS_CAP, Math.round(m.to_hours || 0)));
        nd[m.person_id] = { ...cell(m.person_id), [m.clin]: to };
        // A shift moves the hours rather than deleting them, so the destination line
        // has to gain what the source line gave up — otherwise "move Wei to 0003"
        // reads as a roll-off in the grid and the underburn it was meant to fix
        // stays open.
        if (m.kind === "shift" && m.to_clin) {
          const dest = cell(m.person_id)[m.to_clin] || 0;
          nd[m.person_id] = {
            ...nd[m.person_id],
            [m.to_clin]: Math.max(0, Math.min(HOURS_CAP, Math.round(dest + m.hours_moved))),
          };
        }
      }
      return nd;
    });
    setLoadedPlan(null);
    setLoadedPlanName(null);
    setSavedFp(null);
  }

  // Deep-link from a Flight Deck "Apply fix" suggestion: once the grid has
  // loaded, apply the plan once, then tell App to clear the flag so a later
  // manual visit isn't auto-rebalanced. balancedRef guards double-fires within a
  // single mount while the flag-clear propagates.
  //
  // `autoBalance` is either #63's array of moves or `true` — the latter meaning the
  // solver had no move set for this line, so the uniform rebalance is the honest
  // fallback (and the shape older callers pass).
  const balancedRef = useRef(false);
  useEffect(() => {
    if (!autoBalance) {
      balancedRef.current = false;
      return;
    }
    if (!data || !draft || balancedRef.current) return;
    balancedRef.current = true;
    if (Array.isArray(autoBalance) && autoBalance.length) applyMoves(autoBalance);
    else applyBalance();
    onAutoBalanced?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoBalance, data, draft]);

  // The sim state behind a compare slot ("current" or a saved plan id). A saved
  // plan's `data` is passed through untouched, `absences` included — and a pre-#85
  // plan has no such key, so it compares with none rather than borrowing whatever is
  // on screen now (#67 item 5).
  const planStateFor = (sel) =>
    sel === "current"
      ? { draft, added, removed, absences }
      : plans.find((p) => String(p.id) === String(sel))?.data || { draft: {} };
  const cmpLabel = (sel) =>
    sel === "current" ? "Current plan" : plans.find((p) => String(p.id) === String(sel))?.name || "—";

  // Roll someone off the plan. A planned add just disappears; a synced person is
  // marked removed (excluded from burn) — Discard brings everyone back.
  function removePerson(id) {
    if (isAddedId(id)) setAdded((a) => a.filter((p) => p.id !== id));
    else setRemoved((r) => (r.includes(id) ? r : [...r, id]));
  }

  // Open the staffing panel and fetch its optional directory context. A failed
  // directory request never blocks a typed new-hire scenario.
  async function openAddPerson() {
    setAddPersonError(null);
    setNewPerson({
      source: "new",
      personId: "",
      search: "",
      name: "",
      employeeId: "",
      clin: clins[0]?.id || "",
      lcatChoice: "",
      lcat: "",
      rate: "",
      utilization: null,
      quals: { education: "", years_experience: "", clearance: "" },
      // Seeded from the contract's resolved expectation, not a hard-coded 40.
      hrs: Math.round(contractExpected?.hours ?? FTE_HOURS_PER_WEEK),
    });
    await loadDirectory();
  }

  // The directory fetch on its own, because two things need it now: the staffing
  // panel's people picker and #66's quals panel. It carries `qual_vocab` — the
  // clearance and education ladders (#98) — so the panel's dropdowns and the
  // server's check read one set of levels rather than two that drifted apart.
  async function loadDirectory() {
    setDirectory((d) => ({ ...d, loading: true, error: null }));
    try {
      const [directoryData, utilizationData] = await Promise.all([getPeople(), getPeopleUtilization()]);
      const utilization = Object.fromEntries(
        (utilizationData.people || []).map((person) => [person.employee_id, person])
      );
      setDirectory({
        people: directoryData.people || [],
        qualVocab: directoryData.qual_vocab || {},
        utilization,
        loading: false,
        error: null,
      });
    } catch (e) {
      setDirectory((d) => ({ ...d, loading: false, error: e.message }));
    }
  }

  // #66 — open the quals panel for one person on the grid. The check itself is the
  // server's; this is the "inline path to fill in what's missing" beside it, and it
  // is the half that matters most: the person *already billing* at a senior rate is
  // the live audit exposure, so annotating a synced person cannot be a trip to
  // another view.
  async function openQuals(row) {
    setQualsPanel({ row, saving: false, error: null, draft: null });
    if (!directory.people.length && !directory.loading) await loadDirectory();
  }

  // Save the quals typed into the panel, then re-read the grid so the badge moves.
  //
  // Only fields the user actually touched are sent: the endpoint is a partial upsert
  // that overwrites a field's source note along with its value, so replaying an
  // untouched field would quietly erase the provenance somebody else recorded for it.
  async function saveQuals(draft) {
    const row = qualsPanel?.row;
    if (!row) return;
    const quals = Object.fromEntries(
      Object.entries(draft || {}).map(([field, entry]) => [
        field,
        { value: entry.value ?? "", source_note: entry.source_note || null },
      ]),
    );
    if (!Object.keys(quals).length) {
      setQualsPanel(null);
      return;
    }
    setQualsPanel((q) => q && { ...q, saving: true, error: null });
    try {
      await savePersonQuals(row.id, quals, null);
      // The verdict is the server's, so re-read rather than patch it here — a badge
      // computed client-side would be a second opinion that can disagree with the
      // rollups on the CLIN cards. Through the same path a rate change uses, which
      // leaves an in-progress what-if alone: a credential changes what an hour is
      // *allowed* to bill at, never who is working which hours.
      await Promise.all([reloadRates(), loadDirectory()]);
      setQualsPanel(null);
    } catch (e) {
      setQualsPanel((q) => q && { ...q, saving: false, error: e.message });
    }
  }

  function selectDirectoryPerson(employeeId) {
    const person = directory.people.find((p) => p.employee_id === employeeId);
    if (!person) return;
    const prefill = prefillPerson(person, directory.utilization[employeeId]);
    const match = newPersonRateOptions.find((line) => line.lcat === prefill.lcat);
    setAddPersonError(null);
    setNewPerson((p) => ({
      ...selectDirectoryPersonForm(p, employeeId, prefill),
      lcatChoice: match?.lcat || "other",
      rate: match ? String(match.rate) : "",
    }));
  }

  function setNewPersonClin(clin) {
    const target = clins.find((c) => c.id === clin);
    const matches = rateOptions(target);
    setNewPerson((p) => {
      const match = matches.find((line) => line.lcat === p.lcat);
      return {
        ...p,
        clin,
        lcatChoice: match?.lcat || "other",
        rate: match ? String(match.rate) : "",
      };
    });
  }

  function selectPlannedLcat(choice) {
    if (choice === "other") {
      setNewPerson((p) => ({ ...p, lcatChoice: "other", rate: "" }));
      return;
    }
    const option = newPersonRateOptions.find((line) => line.lcat === choice);
    if (!option) return;
    setNewPerson((p) => ({ ...p, lcatChoice: option.lcat, lcat: option.lcat, rate: String(option.rate) }));
  }

  // Add a plan-local person with an explicit rate. Unlike synced actuals, a what-if
  // has no timesheet LCAT for the burn engine to resolve; using blended here would
  // silently underprice a senior hire and reintroduce the bug this panel replaces.
  function addPerson() {
    const clin = newPerson.clin || clins[0]?.id;
    if (!clin) return;
    const problem = validateAddedPerson(newPerson);
    if (problem) {
      setAddPersonError(problem);
      return;
    }
    const hrs = Math.max(0, Math.min(80, +newPerson.hrs || 0));
    const id = newAddedId();
    const rate = Number(newPerson.rate);
    setAdded((a) => [
      ...a,
      {
        id,
        name: newPerson.name.trim(),
        employeeId: newPerson.employeeId.trim(),
        source: newPerson.source,
        lcat: newPerson.lcat.trim(),
        rate,
        rates: { [clin]: rate },
        utilization: newPerson.utilization,
        quals: newPerson.quals,
        // So their utilisation cell measures against the same expectation their
        // seeded hours came from, rather than falling back to 40.
        expected: contractExpected,
      },
    ]);
    setDraft((d) => ({
      ...d,
      [id]: Object.fromEntries(clins.map((c) => [c.id, c.id === clin ? hrs : 0])),
    }));
    setAddPersonError(null);
    setNewPerson(null);
  }

  // Clone a person as a planned add — same LCAT, rates and hours. "Add another
  // like this LCAT" without retyping.
  function duplicatePerson(e) {
    const id = newAddedId();
    const rates = {};
    clins.forEach((c) => (rates[c.id] = rateFor(e.id, c.id)));
    // "Another like this one" carries their expected week too — a copy of a 32-hr
    // person is another 32-hr person, not a 40-hr one.
    setAdded((a) => [
      ...a,
      { id, name: `${e.name} (copy)`, lcat: e.lcat, rates, expected: expectedOf(e) },
    ]);
    setDraft((d) => ({
      ...d,
      [id]: Object.fromEntries(clins.map((c) => [c.id, d?.[e.id]?.[c.id] || 0])),
    }));
  }

  if (error) {
    return (
      <div style={{ padding: 40 }}>
        <div style={{ ...panelStyle, color: "var(--bad)", fontSize: 13 }}>{error}</div>
      </div>
    );
  }
  if (!data) {
    return <div style={{ padding: 40, color: "var(--dim)" }}>Loading allocation…</div>;
  }

  const name = data.contract.name || data.contract.piid || "this contract";

  return (
    <div style={{ padding: "26px 26px 60px", maxWidth: 1280 }}>
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          marginBottom: 16,
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontFamily: grotesk, fontSize: 20, fontWeight: 600, color: "var(--text)" }}>
            Team allocation matrix
          </h2>
          <div style={{ fontSize: 13, color: "var(--dim)", marginTop: 4 }}>
            Model staffing on <b>{name}</b> · {data.contract.period ? `${data.contract.period}, ` : ""}
            week {cw} of {tw} ·{" "}
            {/* #62 — this line used to read "a live what-if — nothing here is saved",
                which stopped being true the day plans shipped and was the first thing
                users believed. */}
            {loadedPlanName ? (
              <>
                editing <b>“{loadedPlanName}”</b>
                {unsaved ? " · unsaved changes" : " · saved"}
              </>
            ) : dirty ? (
              "an unsaved what-if on top of the synced actuals"
            ) : (
              "modelling from the synced actuals"
            )}
          </div>
          {/* #67 — the plan is still scored live, against today's funding, rates and
              calendar. That is the right arithmetic and the wrong silence: the same
              plan, under the same name, now says something it didn't say when it was
              written. Saying which terms moved is what makes the numbers on screen
              readable; saving over it re-baselines the snapshot. */}
          {/* #67 — what this contract is actually being run against, stated where the
              matrix says what it is modelling. Without it a saved plan is one of a
              list; with it, one of them is the commitment and the rest are what-ifs.
              Absent entirely when nothing is designated: an empty "Baseline: —" would
              read as a broken field rather than as a decision nobody has made. */}
          {baseline && (
            <div style={{ fontSize: 12, color: "var(--dim)", marginTop: 6 }}>
              <span style={baselineChip}>BASELINE</span>{" "}
              <b style={{ color: "var(--text)" }}>“{baseline.name}”</b> — the staffing
              this contract is committed to
              {loadedPlan === baseline.id ? " · open now" : ""}
              {" · "}
              {/* The summary is the link. With nothing adrift it still opens — "we are
                  running the plan" is an answer worth being able to check, and a
                  control that appears only when there is bad news teaches people that
                  its absence means nobody looked. */}
              <button
                onClick={() => setShowDrift((v) => !v)}
                title="Show how the actuals differ from the committed staffing"
                style={{
                  border: "none",
                  background: "transparent",
                  padding: 0,
                  font: "inherit",
                  fontWeight: driftLine ? 700 : 500,
                  color: driftLine?.delta > 0 ? "var(--warn)" : "var(--dim)",
                  cursor: "pointer",
                  textDecoration: "underline",
                }}
              >
                {driftLine ? driftLine.text : "On the baseline staffing"}
              </button>
            </div>
          )}
          {baselineErr && (
            <div style={{ fontSize: 12, color: "var(--bad)", marginTop: 6 }}>
              {baselineErr}
            </div>
          )}
          {loadedStale.length > 0 && (
            <div style={{ fontSize: 12, color: "var(--warn)", marginTop: 6, display: "flex", gap: 6 }}>
              <span aria-hidden="true">⚠</span>
              <span>
                Scored against terms that have changed since it was saved —{" "}
                {loadedStale.join("; ")}. Its numbers are current; what it was written
                against is not. Save it again to re-baseline.
              </span>
            </div>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {/* Which plan you are in, and whether it's saved. The badge this replaces
              said "live, not saved" whenever the grid differed from the actuals —
              which is true of every saved plan anyone ever loads (#62). */}
          {(loadedPlanName || dirty) && (
            <span
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 11.5,
                fontWeight: 600,
                color: unsaved ? "var(--warn)" : "var(--good)",
              }}
              title={
                unsaved
                  ? "This what-if has changes that are not on the server yet"
                  : `Saved as “${loadedPlanName}”`
              }
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: unsaved ? "var(--warn)" : "var(--good)",
                }}
              />
              {loadedPlanName || "Unsaved what-if"}
              {loadedPlanName && (
                <span style={{ color: "var(--dim)", fontWeight: 500 }}>
                  · {unsaved ? "unsaved changes" : "saved"}
                </span>
              )}
            </span>
          )}
          {unsaved && (
            <button
              onClick={promptSave}
              disabled={saveBusy}
              title={
                loadedPlanName
                  ? `Save these changes over “${loadedPlanName}”`
                  : "Name this what-if and save it to the contract"
              }
              style={{
                height: 36,
                padding: "0 14px",
                borderRadius: 10,
                border: "none",
                background: "var(--accent)",
                color: "#fff",
                fontWeight: 600,
                fontSize: 12.5,
                cursor: saveBusy ? "default" : "pointer",
                opacity: saveBusy ? 0.6 : 1,
                boxShadow: "0 4px 12px rgba(67,97,238,.28)",
              }}
            >
              {saveBusy ? "Saving…" : loadedPlanName ? "Save plan" : "Save plan…"}
            </button>
          )}
          {!dirty && offPaceClins.length > 0 && (
            <button
              onClick={applyBalance}
              title={`Rebalance ${offPaceClins.map((c) => c.code).join(", ")} so each finishes right at PoP end — trims lines burning too fast, boosts ones running slow`}
              style={{
                height: 36,
                padding: "0 14px",
                borderRadius: 10,
                border: "none",
                background: "var(--accent)",
                color: "#fff",
                fontWeight: 600,
                fontSize: 12.5,
                cursor: "pointer",
                boxShadow: "0 4px 12px rgba(67,97,238,.28)",
              }}
            >
              ⚡ Balance to finish on plan
            </button>
          )}
          <button
            onClick={discardChanges}
            disabled={!dirty}
            title="Throw this what-if away and go back to the synced actuals"
            style={{
              height: 36,
              padding: "0 14px",
              borderRadius: 10,
              border: "1px solid var(--border)",
              background: "var(--panel2)",
              color: "var(--text)",
              fontWeight: 600,
              fontSize: 12.5,
              cursor: dirty ? "pointer" : "default",
              opacity: dirty ? 1 : 0.5,
            }}
          >
            Discard changes
          </button>
        </div>
      </div>

      {!employees.length || !clins.length ? (
        <div style={{ ...panelStyle, color: "var(--dim)", fontSize: 13 }}>
          No labor charges synced for this contract's active period yet — sync
          timesheets from the Flight Deck and they'll populate here.
        </div>
      ) : (
        <>
          {/* live rollups — slim strip, updates as you edit */}
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
            {[
              { label: "Headcount", value: roster.length },
              {
                label: "FTEs",
                value: (totalHrs / FTE_HOURS_PER_WEEK).toFixed(1),
                // Says which measure this is, because the other one is two tiles away
                // and they answer different questions (#84).
                hint: `Full-time equivalents — hours against a ${FTE_HOURS_PER_WEEK}-hour week, the definition of an FTE. Not utilisation: that measures each person against their own expected week.`,
              },
              { label: "Hrs / wk", value: Math.round(totalHrs).toLocaleString() },
              { label: "Weekly burn", value: money(totalWeekly) },
            ].map((t) => (
              <div key={t.label} title={t.hint} style={{ ...panelStyle, padding: "9px 14px", flex: "1 1 120px", minWidth: 110 }}>
                <div style={{ fontSize: 10.5, letterSpacing: ".07em", textTransform: "uppercase", color: "var(--faint)", fontWeight: 700 }}>
                  {t.label}
                </div>
                <div style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 20, color: "var(--text)", marginTop: 2 }}>
                  {t.value}
                </div>
              </div>
            ))}
          </div>

          {/* #85's last acceptance bullet: when a projection includes absence, say so.
              A runway that quietly differs from hrs/wk x weeks reads as an arithmetic
              bug to anyone checking it by hand, and an accountant will check it by
              hand. Shown only when absence actually moved something. */}
          {current.absence?.active && (
            <div
              style={{
                ...panelStyle,
                padding: "9px 13px",
                marginBottom: 12,
                display: "flex",
                alignItems: "center",
                gap: 9,
                borderColor: "var(--accent)",
                fontSize: 12.5,
                color: "var(--dim)",
              }}
            >
              <span style={{ color: "var(--accent)" }}>☂</span>
              <span>
                These runway figures include dated absence — they are{" "}
                <b style={{ color: "var(--text)" }}>not</b> hrs/wk × weeks.{" "}
                <b style={{ color: "var(--text)" }}>{current.absence.weeksAffected}</b>{" "}
                of the remaining {Math.max(0, (tw || 0) - (cw || 0))} weeks are reduced
                {current.absence.peopleAffected.length > 0 && (
                  <>
                    {" "}
                    for{" "}
                    <b style={{ color: "var(--text)" }}>
                      {current.absence.peopleAffected.length}
                    </b>{" "}
                    {current.absence.peopleAffected.length === 1 ? "person" : "people"}
                  </>
                )}
                {current.absence.holidayWeeks.length > 0 && (
                  <>
                    {" "}
                    · {current.absence.holidayWeeks.length} with a holiday
                  </>
                )}
                .
              </span>
            </div>
          )}

          {/* single control bar: search + filter status (left) · actions (right) */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search people or LCAT…"
              style={{ height: 34, width: 200, maxWidth: "100%", padding: "0 12px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--inputBg)", color: "var(--text)", fontSize: 13 }}
            />
            <div style={{ fontSize: 12.5, color: "var(--dim)" }}>
              Showing <b style={{ color: "var(--text)" }}>{visible.length}</b> of {roster.length}
              {clinFilterCode ? (
                <>
                  {" "}
                  · on <b style={{ color: "var(--text)" }}>{clinFilterCode}</b>
                </>
              ) : (
                " people"
              )}
            </div>
            {filtered && (
              <button onClick={showAll} style={chipBtn}>
                ✕ Show all
              </button>
            )}
            {sort.key && (
              <button onClick={clearSort} title="Return to the default order" style={chipBtnDim}>
                ✕ Clear sort
              </button>
            )}

            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
              {/* The expectation everything on this screen is measured against, and
                  the one control that moves it. Reads as a setting rather than a
                  number so the fallback can say it is an assumption. */}
              <div style={{ position: "relative" }}>
                <button
                  onClick={() => {
                    setTargetDraft(
                      data.contract.utilization_target != null
                        ? String(Math.round(pctOf(data.contract.utilization_target) * 100))
                        : ""
                    );
                    setTargetOpen((v) => !v);
                  }}
                  title="What a full week is on this contract. Utilisation and every planned add measure against it."
                  style={{
                    ...chipBtnDim,
                    borderColor: targetOpen ? "var(--accent)" : "var(--border)",
                  }}
                >
                  {contractExpected?.assumed ? "Expected: 40 hrs/wk*" : `Expected: ${contractExpected?.hours} hrs/wk`}
                </button>
                {targetOpen && (
                  <div
                    style={{
                      position: "absolute",
                      top: "calc(100% + 6px)",
                      right: 0,
                      zIndex: 20,
                      width: 268,
                      ...panelStyle,
                      padding: 13,
                    }}
                  >
                    <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", marginBottom: 5 }}>
                      Utilisation target
                    </div>
                    <div style={{ fontSize: 11.5, color: "var(--faint)", lineHeight: 1.5, marginBottom: 9 }}>
                      The share of a 40-hour week a full-time person on this contract
                      is expected to bill. 80–90% is the norm once holidays, leave and
                      unbillable time come out. Nobody bills 40.
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                      <input
                        type="number"
                        min={1}
                        max={100}
                        value={targetDraft}
                        onChange={(e) => setTargetDraft(e.target.value)}
                        placeholder="80"
                        style={{
                          width: 74,
                          padding: "7px 9px",
                          borderRadius: 9,
                          border: "1px solid var(--border)",
                          background: "var(--inputBg)",
                          color: "var(--text)",
                          fontSize: 13,
                          fontFamily: mono,
                        }}
                      />
                      <span style={{ fontSize: 12.5, color: "var(--dim)" }}>
                        % ={" "}
                        {targetDraft && +targetDraft > 0
                          ? `${Math.round((FTE_HOURS_PER_WEEK * +targetDraft) / 100 * 10) / 10} hrs/wk`
                          : "—"}
                      </span>
                    </div>
                    <div style={{ display: "flex", gap: 7, marginTop: 11 }}>
                      <button
                        onClick={() => saveTarget(targetDraft)}
                        disabled={targetBusy || !targetDraft}
                        style={{ ...primaryBtn, padding: "6px 12px", fontSize: 12 }}
                      >
                        {targetBusy ? "Saving…" : "Apply"}
                      </button>
                      {/* Clearing back to the default has to stay reachable, or the
                          default is a one-way door. */}
                      <button
                        onClick={() => saveTarget("")}
                        disabled={targetBusy || data.contract.utilization_target == null}
                        style={{ ...chipBtnDim, padding: "6px 12px" }}
                      >
                        Clear
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* #85 — the holiday calendar. Contract-level, not plan-level: a
                  holiday is a fact about the calendar rather than about one
                  what-if, and the burn engine can only bend the Flight Deck's
                  chart around data it can read. The trade this accepts, stated in
                  the panel: editing it changes what every saved plan projects. */}
              <div style={{ position: "relative" }}>
                <button
                  onClick={() => setHolidaysOpen((v) => !v)}
                  title="Company-wide days nobody charges. Applies to everyone on this contract."
                  style={{
                    ...chipBtnDim,
                    borderColor: holidaysOpen ? "var(--accent)" : "var(--border)",
                  }}
                >
                  Holidays: {contractAbsence.holidays.length || "none"}
                </button>
                {holidaysOpen && (
                  <div
                    style={{
                      position: "absolute",
                      top: "calc(100% + 6px)",
                      right: 0,
                      zIndex: 20,
                      width: 300,
                      ...panelStyle,
                      padding: 13,
                    }}
                  >
                    <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", marginBottom: 5 }}>
                      Holiday calendar
                    </div>
                    <div style={{ fontSize: 11.5, color: "var(--faint)", lineHeight: 1.5, marginBottom: 9 }}>
                      Entered once, applies to everyone. Saved on the contract, so it
                      bends the Flight Deck's burn chart too — and changes what every
                      saved plan projects.
                    </div>
                    {contractAbsence.holidays.length > 0 && (
                      <div style={{ maxHeight: 168, overflowY: "auto", marginBottom: 9 }}>
                        {contractAbsence.holidays.map((h) => (
                          <div
                            key={h.date}
                            style={{ display: "flex", alignItems: "center", gap: 7, padding: "3px 0", fontSize: 11.5 }}
                          >
                            <span style={{ fontFamily: mono, color: "var(--dim)" }}>{h.date}</span>
                            <span style={{ color: "var(--faint)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {h.name}
                            </span>
                            <button
                              onClick={() =>
                                saveHolidays({
                                  holidays: contractAbsence.holidays.filter((x) => x.date !== h.date),
                                })
                              }
                              disabled={holidayBusy}
                              style={{ border: 0, background: "none", color: "var(--dim)", cursor: "pointer", fontSize: 13, lineHeight: 1, padding: 0 }}
                            >
                              ×
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                    <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
                      {/* A seed, never an imposition — a contractor observing a
                          different calendar has to be able to delete these, which is
                          why they land as ordinary editable entries. */}
                      <button
                        onClick={() =>
                          saveHolidays({
                            holidays: contractAbsence.holidays,
                            seed_federal_year: new Date(
                              periodBounds.start || Date.now()
                            ).getUTCFullYear(),
                          })
                        }
                        disabled={holidayBusy}
                        style={{
                          ...primaryBtn,
                          padding: "6px 12px",
                          fontSize: 12,
                          ...(holidayBusy ? disabledBtn : null),
                        }}
                      >
                        {holidayBusy ? "Saving…" : "Seed federal"}
                      </button>
                      <button
                        onClick={() => saveHolidays({ holidays: [] })}
                        disabled={holidayBusy || !contractAbsence.holidays.length}
                        style={{ ...chipBtnDim, padding: "6px 12px" }}
                      >
                        Clear
                      </button>
                    </div>
                  </div>
                )}
              </div>

              <button
                onClick={openAddPerson}
                title="Add a planned person"
                style={primaryBtn}
              >
                + Add person
              </button>

              {/* Plans menu — save / load / compare / delete, folded into one button */}
              <div style={{ position: "relative" }}>
                <button
                  onClick={() => setPlansMenuOpen((v) => !v)}
                  title={
                    plans.length
                      ? `${plans.length} saved plan${plans.length === 1 ? "" : "s"} on this contract`
                      : "Save, load and compare what-if plans"
                  }
                  style={{ ...secondaryBtn, borderColor: plansMenuOpen ? "var(--accent)" : "var(--border)" }}
                >
                  {/* The count is the hint that this is a place things live, not just
                      another secondary button (#62). */}
                  Plans{plans.length ? ` · ${plans.length}` : ""} ▾
                </button>
                {plansMenuOpen && (
                  <>
                    <div onClick={() => setPlansMenuOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 40 }} />
                    <div style={{ position: "absolute", right: 0, top: 40, zIndex: 41, width: 250, ...panelStyle, padding: 8, boxShadow: "0 16px 40px rgba(15,20,35,.24)" }}>
                      {planName == null ? (
                        <>
                          {/* A loaded plan saves over itself here too, so the menu and
                              the toolbar button can't disagree about what Save does. */}
                          {loadedPlanName && (
                            <button
                              onClick={() => {
                                doSavePlan();
                                setPlansMenuOpen(false);
                              }}
                              disabled={!unsaved || saveBusy}
                              style={{
                                ...menuItem,
                                color: unsaved ? "var(--text)" : "var(--faint)",
                                cursor: unsaved ? "pointer" : "default",
                              }}
                            >
                              ⤓ Save to “{loadedPlanName}”
                            </button>
                          )}
                          <button onClick={() => setPlanName("")} style={menuItem}>
                            ＋ {loadedPlanName ? "Save as a new plan" : "Save current plan"}
                          </button>
                        </>
                      ) : (
                        <div style={{ display: "flex", gap: 6, padding: 4 }}>
                          <input
                            autoFocus
                            value={planName}
                            placeholder="Plan name…"
                            onChange={(e) => setPlanName(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                // Reached from "Save as a new plan" — a typed name
                                // always means a new plan, never an overwrite.
                                doSavePlan({ asNew: true });
                                setPlansMenuOpen(false);
                              } else if (e.key === "Escape") setPlanName(null);
                            }}
                            style={{ flex: 1, minWidth: 0, height: 30, padding: "0 9px", borderRadius: 8, border: "1px solid var(--accent)", background: "var(--inputBg)", color: "var(--text)", fontSize: 12.5 }}
                          />
                          <button onClick={() => { doSavePlan({ asNew: true }); setPlansMenuOpen(false); }} style={{ height: 30, padding: "0 12px", borderRadius: 8, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 600, fontSize: 12, cursor: "pointer" }}>
                            Save
                          </button>
                        </div>
                      )}
                      {(plans.length > 0 || dirty || loadedPlan) && (
                        <>
                          <div style={menuDivider} />
                          {/* One group, read as a radio list: the actuals and the saved
                              plans are alternative things the grid can be showing, and
                              the menu was previously only able to express half of that
                              — you could move between plans but never back out of one.
                              Hence "Modelling from" rather than "Saved plans". */}
                          <div style={menuLabel}>Modelling from</div>
                          <button
                            onClick={() => {
                              showActuals();
                              setPlansMenuOpen(false);
                            }}
                            title="Show the synced actuals — who is charging, at the hours they are actually charging"
                            style={{
                              ...menuItem,
                              color: onActuals ? "var(--accent)" : "var(--text)",
                            }}
                          >
                            <div>
                              {onActuals ? "✓ " : ""}
                              What's running now
                            </div>
                            <div style={{ fontSize: 10.5, color: "var(--faint)", fontWeight: 400 }}>
                              The synced actuals, no what-if
                            </div>
                          </button>
                          {plans.map((p) => (
                            <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                              <button
                                onClick={() => { loadPlan(p.id); setPlansMenuOpen(false); }}
                                title={staleReasons[p.id]?.length ? staleTitle(staleReasons[p.id]) : ""}
                                style={{ ...menuItem, flex: 1, color: loadedPlan === p.id ? "var(--accent)" : "var(--text)" }}
                              >
                                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                                    {loadedPlan === p.id ? "✓ " : ""}
                                    {p.name}
                                  </span>
                                  {p.is_baseline && <span style={baselineChip}>BASELINE</span>}
                                  {staleReasons[p.id]?.length > 0 && (
                                    <span style={staleChip}>STALE</span>
                                  )}
                                </div>
                                {/* Whether a plan has been touched since it was written,
                                    which is what tells two similar names apart. A plan
                                    that was never saved over shows only when it was
                                    written — claiming an edit that never happened would
                                    be worse than saying nothing. */}
                                <div style={{ fontSize: 10.5, color: "var(--faint)", fontWeight: 400 }}>
                                  {p.updated_at
                                    ? `Updated ${shortDate(p.updated_at)}`
                                    : `Saved ${shortDate(p.created_at)}`}
                                </div>
                              </button>
                              {/* Designate / stand down, on the row rather than behind a
                                  submenu: which plan is the baseline is a property of
                                  the plan, and the only place all the plans are listed
                                  is here. Deliberately reversible and unconfirmed —
                                  unlike delete, nothing is lost by getting it wrong. */}
                              <button
                                onClick={() => toggleBaseline(p)}
                                disabled={baselineBusy}
                                aria-pressed={Boolean(p.is_baseline)}
                                title={
                                  p.is_baseline
                                    ? `“${p.name}” is the active baseline — click to stand it down`
                                    : `Make “${p.name}” the active baseline — the staffing this contract is committed to`
                                }
                                style={{
                                  ...baselineToggle,
                                  color: p.is_baseline ? "var(--accent)" : "var(--faint)",
                                  cursor: baselineBusy ? "default" : "pointer",
                                }}
                              >
                                {p.is_baseline ? "★" : "☆"}
                              </button>
                              {/* The same quiet trash the contract delete uses (#29),
                                  rather than a × that could be a close button. It
                                  names the plan, so the control isn't "delete
                                  something" to a screen reader. */}
                              <TrashButton
                                size={13}
                                label={`Delete the saved plan ${p.name}`}
                                onClick={() => requestDeletePlan(p.id)}
                              />
                            </div>
                          ))}
                        </>
                      )}
                      <div style={menuDivider} />
                      <button
                        onClick={() => {
                          if (!plans.length) return;
                          setComparing((v) => !v);
                          if (!comparing) setCmpB(String(plans[0].id));
                          setPlansMenuOpen(false);
                        }}
                        disabled={!plans.length}
                        title={plans.length ? "" : "Save a plan first to compare"}
                        style={{ ...menuItem, color: plans.length ? (comparing ? "var(--accent)" : "var(--text)") : "var(--faint)", cursor: plans.length ? "pointer" : "default" }}
                      >
                        ⇄ {comparing ? "Close compare" : "Compare plans"}
                      </button>
                    </div>
                  </>
                )}
              </div>

              <button onClick={exportCsv} title="Download this view as a CSV" style={exportBtn}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z" />
                  <path d="M14 3v5h5" strokeLinejoin="round" />
                </svg>
                Export
              </button>
            </div>
          </div>

          {/* drift vs the active baseline (on demand) */}
          {showDrift && baseline && (
            <DriftPanel
              drift={drift}
              baselineName={baseline.name}
              clins={clins}
              runwayDelta={driftRunway}
              onClose={() => setShowDrift(false)}
            />
          )}

          {/* compare panel (on demand) */}
          {comparing && (
            <ComparePanel
              a={cmpA}
              b={cmpB}
              setA={setCmpA}
              setB={setCmpB}
              plans={plans}
              clins={clins}
              tw={tw}
              cmpLabel={cmpLabel}
              evalPlan={evalPlan}
              planStateFor={planStateFor}
              onClose={() => setComparing(false)}
            />
          )}

          {/* add-person inline form */}
          {/* #85 — one person's dated absence. Inline above the grid, like the
              add-person form, so the row it belongs to stays visible while typing. */}
          {absenceFor && (
            <div style={{ ...panelStyle, padding: "12px 14px", marginBottom: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
                  Absence · {absenceFor.name}
                </span>
                <span style={{ fontSize: 11.5, color: "var(--faint)" }}>
                  Reduces this plan's projection in those weeks. Commit one to move the
                  Flight Deck's burn chart too — until then it rides with the plan.
                </span>
                <button onClick={() => setAbsenceFor(null)} style={{ ...chipBtnDim, marginLeft: "auto" }}>
                  ✕ Close
                </button>
              </div>

              {/* What is already booked, committed and what-if together. A committed
                  entry is the contract's and can't be deleted from a plan. */}
              {absencesFor(allAbsences, absenceFor.id).length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 7, marginBottom: 11 }}>
                  {absencesFor(allAbsences, absenceFor.id).map((a, i) => {
                    const planned = absences.includes(a);
                    return (
                      <span
                        key={`${a.start}-${a.end}-${i}`}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 6,
                          fontSize: 11.5,
                          fontFamily: mono,
                          padding: "4px 8px",
                          borderRadius: 8,
                          border: "1px solid var(--border)",
                          background: "var(--panel2)",
                          color: "var(--dim)",
                        }}
                      >
                        {a.start} → {a.end}
                        <b style={{ color: "var(--text)" }}>{absenceWorkdays(a)}d</b>
                        <span style={{ color: "var(--faint)" }}>
                          {a.kind === "start" ? "start" : a.kind === "roll_off" ? "roll-off" : "PTO"}
                        </span>
                        {planned ? (
                          <>
                            {/* The only route by which a person's absence reaches the
                                burn engine, and therefore the Flight Deck's chart. */}
                            <button
                              onClick={() => commitAbsence(a)}
                              disabled={holidayBusy}
                              title="Commit to the contract — this is what makes the Flight Deck's burn chart bend around it"
                              style={{
                                ...commitBtn,
                                ...(holidayBusy ? disabledBtn : null),
                              }}
                            >
                              Commit
                            </button>
                            <button
                              onClick={() => removeAbsence(a)}
                              title="Remove from this plan"
                              style={{ border: 0, background: "none", color: "var(--dim)", cursor: "pointer", fontSize: 13, lineHeight: 1, padding: 0 }}
                            >
                              ×
                            </button>
                          </>
                        ) : (
                          <>
                            <span
                              title="Committed on the contract — the Flight Deck's chart bends around this one"
                              style={{ color: "var(--accent)", fontSize: 10, fontWeight: 700 }}
                            >
                              ON CONTRACT
                            </span>
                            <button
                              onClick={() => withdrawAbsence(a)}
                              disabled={holidayBusy}
                              title="Withdraw from the contract"
                              style={{ border: 0, background: "none", color: "var(--dim)", cursor: holidayBusy ? "not-allowed" : "pointer", fontSize: 13, lineHeight: 1, padding: 0 }}
                            >
                              ×
                            </button>
                          </>
                        )}
                      </span>
                    );
                  })}
                </div>
              )}

              <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
                <div>
                  <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}>Kind</div>
                  <select
                    value={absenceFor.kind}
                    onChange={(ev) => setAbsenceFor((f) => ({ ...f, kind: ev.target.value }))}
                    style={{ height: 34, padding: "0 11px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--panel2)", color: "var(--text)", fontSize: 13, cursor: "pointer" }}
                  >
                    <option value="pto">PTO / leave</option>
                    <option value="start">Starts on…</option>
                    <option value="roll_off">Rolls off after…</option>
                  </select>
                </div>
                {/* PTO is a range; a start or roll-off date is a single date whose
                    other end is the period boundary. Asking for one field instead of
                    two disabled-and-mislabelled ones — the range is still what gets
                    stored, so the projection keeps one code path for all three. */}
                {absenceFor.kind === "pto" ? (
                  <>
                    <div>
                      <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}>
                        First day out
                      </div>
                      <input
                        type="date"
                        value={absenceFor.start}
                        onChange={(ev) => setAbsenceFor((f) => ({ ...f, start: ev.target.value }))}
                        style={dateInput}
                      />
                    </div>
                    <div>
                      <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}>
                        Last day out
                      </div>
                      <input
                        type="date"
                        value={absenceFor.end}
                        onChange={(ev) => setAbsenceFor((f) => ({ ...f, end: ev.target.value }))}
                        style={dateInput}
                      />
                    </div>
                  </>
                ) : (
                  <div>
                    <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}>
                      {absenceFor.kind === "start" ? "Starts on" : "Last day on the contract"}
                    </div>
                    <input
                      type="date"
                      value={absenceFor.start}
                      onChange={(ev) => setAbsenceFor((f) => ({ ...f, start: ev.target.value }))}
                      style={dateInput}
                    />
                  </div>
                )}
                <button
                  onClick={() => {
                    if (absenceProblem || !absenceRange) return;
                    addAbsence({
                      person_id: absenceFor.id,
                      person: absenceFor.name,
                      ...absenceRange,
                      kind: absenceFor.kind,
                    });
                    setAbsenceFor((f) => ({ ...f, start: "", end: "" }));
                  }}
                  disabled={!!absenceProblem}
                  style={{
                    ...primaryBtn,
                    height: 34,
                    ...(absenceProblem ? disabledBtn : null),
                  }}
                >
                  + Add
                </button>
                {/* Never a dead click: the button says why it can't fire yet, and once
                    it can, it echoes the workday count — the unit a user checks the
                    arithmetic in, since "2026-08-10 → 2026-08-21" is not one. */}
                <span
                  style={{
                    fontSize: 11.5,
                    color: absenceProblem ? "var(--warn)" : "var(--faint)",
                  }}
                >
                  {absenceProblem ||
                    `${absenceWorkdays(absenceRange)} workdays · ${absenceRange.start} → ${absenceRange.end}`}
                </span>
              </div>
            </div>
          )}

          {newPerson && (
            <div
              style={{
                ...panelStyle,
                padding: "12px 14px",
                marginBottom: 12,
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
                gap: 10,
                alignItems: "flex-end",
              }}
            >
              <div style={{ gridColumn: "1 / -1", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", paddingBottom: 2 }}>
                <span style={{ fontSize: 12, fontWeight: 700 }}>1. Person</span>
                <button
                  type="button"
                  onClick={() => setNewPerson((p) => switchPersonSource(p, "directory"))}
                  style={{ ...chipBtnDim, padding: "6px 10px", ...(newPerson.source === "directory" ? { borderColor: "var(--accent)", color: "var(--accent)" } : null) }}
                >
                  People directory
                </button>
                <button
                  type="button"
                  onClick={() => setNewPerson((p) => switchPersonSource(p, "new"))}
                  style={{ ...chipBtnDim, padding: "6px 10px", ...(newPerson.source === "new" ? { borderColor: "var(--accent)", color: "var(--accent)" } : null) }}
                >
                  New hire
                </button>
                {newPerson.source === "directory" && directory.loading && <span style={{ fontSize: 11.5, color: "var(--dim)" }}>Loading people…</span>}
                {directory.error && <span style={{ fontSize: 11.5, color: "var(--warn)" }}>Directory unavailable — enter a new hire instead.</span>}
              </div>
              {newPerson.source === "directory" && (
                <div style={{ gridColumn: "1 / -1", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", padding: "10px", borderRadius: 8, background: "var(--panel2)" }}>
                  <input
                    value={newPerson.search}
                    placeholder="Search name, ID, or LCAT"
                    onChange={(e) => setNewPerson((p) => ({ ...p, search: e.target.value }))}
                    style={{ height: 34, width: 240, padding: "0 11px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--inputBg)", color: "var(--text)", fontSize: 13 }}
                  />
                  <select
                    value={newPerson.personId}
                    onChange={(e) => selectDirectoryPerson(e.target.value)}
                    style={{ height: 34, minWidth: 260, padding: "0 11px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--panel2)", color: "var(--text)", fontSize: 13 }}
                  >
                    <option value="">Select a person…</option>
                    {directoryMatches.map((person) => (
                      <option key={person.employee_id} value={person.employee_id}>
                        {person.name} · {person.employee_id}{person.lcats?.length ? ` · ${person.lcats.join(", ")}` : ""}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div>
                <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}>Name</div>
                <input
                  autoFocus
                  value={newPerson.name}
                  placeholder="Name"
                  onChange={(e) => setNewPerson((p) => ({ ...p, name: e.target.value }))}
                  onKeyDown={(e) => e.key === "Enter" && addPerson()}
                  style={{ boxSizing: "border-box", height: 34, width: "100%", padding: "0 11px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--inputBg)", color: "var(--text)", fontSize: 13 }}
                />
              </div>
              <div>
                <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}>Employee ID</div>
                <input
                  value={newPerson.employeeId}
                  placeholder="Optional"
                  onChange={(e) => setNewPerson((p) => ({ ...p, employeeId: e.target.value }))}
                  style={{ boxSizing: "border-box", height: 34, width: "100%", padding: "0 11px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--inputBg)", color: "var(--text)", fontSize: 13 }}
                />
              </div>
              <div style={{ gridColumn: "1 / -1", borderTop: "1px solid var(--border)", paddingTop: 10, fontSize: 11, fontWeight: 700, color: "var(--dim)" }}>
                2. Assignment
              </div>
              <div>
                <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}>CLIN</div>
                <select
                  value={newPerson.clin}
                  onChange={(e) => setNewPersonClin(e.target.value)}
                  style={{ boxSizing: "border-box", height: 34, width: "100%", padding: "0 11px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--panel2)", color: "var(--text)", fontSize: 13, cursor: "pointer" }}
                >
                  {clins.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.code} — {c.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}>LCAT</div>
                <select
                  value={newPerson.lcatChoice}
                  onChange={(e) => selectPlannedLcat(e.target.value)}
                  style={{ boxSizing: "border-box", height: 34, width: "100%", padding: "0 11px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--panel2)", color: "var(--text)", fontSize: 13 }}
                >
                  <option value="">Select priced LCAT…</option>
                  {newPersonRateOptions.map((line) => (
                    <option key={line.lcat} value={line.lcat}>
                      {line.lcat} — {money(line.rate)}/hr
                      {/* A built rate is never allowed to pass for a printed one (#144). */}
                      {line.basis === "burdened" ? " (burdened)" : ""}
                    </option>
                  ))}
                  <option value="other">Other — not on the rate schedule…</option>
                </select>
              </div>
              {newPerson.lcatChoice === "other" && (
                <div>
                  <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}>Other LCAT</div>
                  <input
                    value={newPerson.lcat}
                    placeholder="Negotiated category"
                    onChange={(e) => setNewPerson((p) => ({ ...p, lcat: e.target.value }))}
                  style={{ boxSizing: "border-box", height: 34, width: "100%", padding: "0 11px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--inputBg)", color: "var(--text)", fontSize: 13 }}
                  />
                </div>
              )}
              <div>
                <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}>Rate / hr</div>
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={newPerson.rate}
                  placeholder={newPerson.lcatChoice === "other" ? "Required" : "Select LCAT"}
                  onChange={(e) => setNewPerson((p) => ({ ...p, rate: e.target.value }))}
                  style={{ boxSizing: "border-box", height: 34, width: "100%", padding: "0 11px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--inputBg)", color: "var(--text)", fontSize: 13, textAlign: "right", fontFamily: mono }}
                />
              </div>
              <div>
                <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}>Hrs / wk</div>
                <input
                  type="number"
                  min="0"
                  max="80"
                  value={newPerson.hrs}
                  onChange={(e) => setNewPerson((p) => ({ ...p, hrs: e.target.value }))}
                  onKeyDown={(e) => e.key === "Enter" && addPerson()}
                  style={{ boxSizing: "border-box", height: 34, width: "100%", padding: "0 11px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--inputBg)", color: "var(--text)", fontSize: 13, textAlign: "right", fontFamily: mono }}
                />
              </div>
              <div style={{ gridColumn: "1 / -1", display: "flex", gap: 10, flexWrap: "wrap", borderTop: "1px solid var(--border)", paddingTop: 10 }}>
                <span style={{ flexBasis: "100%", fontSize: 11, fontWeight: 700, color: "var(--dim)" }}>3. Optional planning profile</span>
                {[["education", "Education"], ["years_experience", "Years"], ["clearance", "Clearance"]].map(([field, label]) => (
                  <label key={field} style={{ fontSize: 11, color: "var(--dim)" }}>
                    {label} <span style={{ color: "var(--faint)" }}>(optional)</span><br />
                    <input
                      value={newPerson.quals[field] || ""}
                      onChange={(e) => setNewPerson((p) => ({ ...p, quals: { ...p.quals, [field]: e.target.value } }))}
                      style={{ marginTop: 5, height: 30, width: 140, padding: "0 9px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--inputBg)", color: "var(--text)", fontSize: 12 }}
                    />
                  </label>
                ))}
                {newPerson.utilization != null && <span style={{ alignSelf: "end", fontSize: 11.5, color: "var(--dim)", paddingBottom: 7 }}>Current utilization: {Math.round(newPerson.utilization * 100)}%</span>}
              </div>
              {selectedRateOption && (
                <div style={{ gridColumn: "1 / -1", fontSize: 11.5, color: "var(--dim)" }}>
                  Rate schedule minimums: {[
                    selectedRateOption.min_education,
                    selectedRateOption.min_experience_yrs != null ? `${selectedRateOption.min_experience_yrs} yrs experience` : null,
                    selectedRateOption.clearance,
                  ].filter(Boolean).join(" · ") || "none listed"}.
                </div>
              )}
              {!newPersonRateOptions.length && (
                <div style={{ gridColumn: "1 / -1", fontSize: 11.5, color: "var(--warn)", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  {activeNewClin?.rate_table_state === "unburdened" ? (
                    // The schedule is in; it just isn't burdened (#139). Offering an
                    // import here would send the user after a document we have.
                    "This CLIN prices categories at unburdened direct rates, so there's no billable rate to pick. Enter an Other LCAT and explicit rate."
                  ) : (
                    <>
                      This CLIN has no rate table. Enter an Other LCAT and explicit rate, or
                      <ImportRateSchedule contractId={contractId} onImported={reloadRates} compact />
                    </>
                  )}
                </div>
              )}
              <button
                onClick={addPerson}
                style={{ gridColumn: "-2", height: 34, padding: "0 16px", borderRadius: 10, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 600, fontSize: 12.5, cursor: "pointer" }}
              >
                Add
              </button>
              <button
                onClick={() => setNewPerson(null)}
                style={{ height: 34, padding: "0 14px", borderRadius: 10, border: "1px solid var(--border)", background: "var(--panel2)", color: "var(--text)", fontWeight: 600, fontSize: 12.5, cursor: "pointer" }}
              >
                Cancel
              </button>
              <div style={{ gridColumn: "1 / -1", fontSize: 11.5, color: addPersonError ? "var(--bad)" : "var(--dim)" }}>
                {addPersonError || "This is a what-if only. The plan uses the explicit rate shown above; it never falls back to the CLIN blended rate."}
              </div>
            </div>
          )}

          {/* matrix */}
          <div style={{ ...panelStyle, padding: 0, overflow: "hidden" }}>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 760 }}>
                <thead>
                  <tr style={{ background: "var(--panel2)" }}>
                    <th
                      onClick={() => toggleSort("name")}
                      title="Sort by name"
                      style={{ ...thSort, position: "sticky", left: 0, background: "var(--panel2)" }}
                    >
                      Employee{sortGlyph("name")}
                    </th>
                    <th onClick={() => toggleSort("lcat")} title="Sort by labor category" style={thSort}>
                      LCAT{sortGlyph("lcat")}
                    </th>
                    <th onClick={() => toggleSort("rate")} title="Sort by rate" style={{ ...thSort, textAlign: "right" }}>
                      Rate{sortGlyph("rate")}
                    </th>
                    {clins.map((c, i) => {
                      const active = clinFilter === c.id;
                      return (
                        <th
                          key={c.id}
                          onClick={() => toggleSort(c.id)}
                          title="Sort by hours on this CLIN"
                          style={{
                            ...thSort,
                            textAlign: "center",
                            minWidth: 96,
                            background: active ? `${hueFor(i)}14` : "var(--panel2)",
                          }}
                        >
                          <span style={{ color: hueFor(i), fontFamily: mono }}>{c.code}</span>
                          {sortGlyph(c.id)}
                          <br />
                          <span style={{ fontWeight: 500, textTransform: "none", fontSize: 10.5 }}>hrs/wk</span>
                        </th>
                      );
                    })}
                    <th
                      onClick={() => toggleSort("util")}
                      title="Sort by utilization — hours against each person's expected week, so 100% means fully utilised. Hover a figure to see where that expectation comes from."
                      style={{ ...thSort, textAlign: "center" }}
                    >
                      Util{sortGlyph("util")}
                    </th>
                    <th onClick={() => toggleSort("weekly")} title="Sort by weekly $" style={{ ...thSort, textAlign: "right" }}>
                      Weekly{sortGlyph("weekly")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {visible.length === 0 && (
                    <tr>
                      <td colSpan={clins.length + 5} style={{ padding: 26, textAlign: "center", color: "var(--faint)", fontSize: 13 }}>
                        No one matches — <span onClick={showAll} style={{ color: "var(--accent)", cursor: "pointer", fontWeight: 600 }}>show all people</span>.
                      </td>
                    </tr>
                  )}
                  {visible.map((e) => {
                    const rowWeekly = rowWeeklyOf(e);
                    // Committed and what-if absence together — the row shows what the
                    // projection was actually scored against, not one of the two.
                    const away = absencesFor(allAbsences, e.id);
                    const hue = hueOf(e.id);
                    const tier = tierOf(e.lcat);
                    return (
                      <tr key={e.id} style={{ borderTop: "1px solid var(--border)" }}>
                        <td style={{ padding: "10px 16px", position: "sticky", left: 0, background: "var(--panel)" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                            <span style={avatarStyle(hue)}>{initials(e.name)}</span>
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ fontWeight: 600, color: "var(--text)", display: "flex", alignItems: "center", gap: 6 }}>
                                {e.name}
                                {isAddedId(e.id) && (
                                  <span style={{ fontSize: 9.5, fontWeight: 700, color: "var(--accent)", background: "var(--panel2)", padding: "1px 6px", borderRadius: 5 }}>
                                    PLANNED
                                  </span>
                                )}
                              </div>
                              <div style={{ fontSize: 11.5, color: "var(--dim)", fontFamily: mono }}>
                                {isAddedId(e.id) ? "—" : e.id}
                              </div>
                            </div>
                            {/* #85 — dated absence, entered from the person's row
                                because that is where the user is already looking at
                                their hours. Accent-coloured once they have any, so
                                the roster shows at a glance who is out. */}
                            <button
                              onClick={() =>
                                setAbsenceFor({
                                  id: e.id,
                                  name: e.name,
                                  start: "",
                                  end: "",
                                  kind: "pto",
                                })
                              }
                              title={
                                away.length
                                  ? away
                                      .map(
                                        (a) =>
                                          `${a.start} → ${a.end} (${absenceWorkdays(a)} days)`
                                      )
                                      .join("\n")
                                  : "Add PTO, a start date or a roll-off date"
                              }
                              style={{
                                width: 24,
                                height: 24,
                                flexShrink: 0,
                                borderRadius: 6,
                                border: `1px solid ${
                                  away.length
                                    ? "var(--accent)"
                                    : "var(--border)"
                                }`,
                                background: "var(--panel2)",
                                color: away.length
                                  ? "var(--accent)"
                                  : "var(--dim)",
                                cursor: "pointer",
                                fontSize: 12,
                                lineHeight: 1,
                              }}
                            >
                              ☂
                            </button>
                            <button
                              onClick={() => duplicatePerson(e)}
                              title="Duplicate as a planned add"
                              style={{
                                width: 24,
                                height: 24,
                                flexShrink: 0,
                                borderRadius: 6,
                                border: "1px solid var(--border)",
                                background: "var(--panel2)",
                                color: "var(--dim)",
                                cursor: "pointer",
                                fontSize: 12,
                                lineHeight: 1,
                              }}
                            >
                              ⧉
                            </button>
                            <button
                              onClick={() => removePerson(e.id)}
                              title="Remove from this plan"
                              style={{
                                width: 24,
                                height: 24,
                                flexShrink: 0,
                                borderRadius: 6,
                                border: "1px solid var(--border)",
                                background: "var(--panel2)",
                                color: "var(--dim)",
                                cursor: "pointer",
                                fontSize: 13,
                                lineHeight: 1,
                              }}
                            >
                              ×
                            </button>
                          </div>
                        </td>
                        <td style={{ padding: "10px 12px" }}>
                          {e.lcat ? (
                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              <span style={tierPill(tier)}>{tier.label}</span>
                              <span style={{ fontSize: 12, color: "var(--dim)", maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {e.lcat}
                              </span>
                              {/* #66. Only real people get a verdict: a planned add has
                                  no employee id to hang credentials off yet, and the
                                  add-person form already collects them at the point of
                                  typing. */}
                              {!isAddedId(e.id) && complianceBadge(e.compliance_status) && (
                                <button
                                  type="button"
                                  onClick={() => openQuals(e)}
                                  title={`${complianceBadge(e.compliance_status).title} — click for detail`}
                                  style={compliancePill(complianceBadge(e.compliance_status).tone)}
                                >
                                  {complianceBadge(e.compliance_status).label}
                                </button>
                              )}
                            </div>
                          ) : (
                            <span style={{ color: "var(--dim)" }}>—</span>
                          )}
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "right", fontFamily: mono, color: "var(--dim)" }}>
                          {e.rate ? "$" + Math.round(e.rate) : "—"}
                        </td>
                        {clins.map((c, ci) => {
                          const cell = e.cells?.[c.id];
                          const val = draft?.[e.id]?.[c.id] ?? 0;
                          const colHue = hueFor(ci);
                          // A CLIN with no rate table at all is one document
                          // problem, reported once on the CLIN card (#64) — it must
                          // not paint a red cell per person. Red on 40 cells for one
                          // missing PDF page is what trained people to ignore red.
                          const noise =
                            cell?.cause === "clin_unpriced" ||
                            cell?.cause === "clin_unburdened";
                          const flagged = cell?.unmatched && !noise;
                          const mapped = cell?.via === "alias";
                          const filled = val > 0;
                          return (
                            <td
                              key={c.id}
                              style={{
                                padding: 8,
                                textAlign: "center",
                                position: "relative",
                                background: clinFilter === c.id ? `${colHue}0d` : undefined,
                              }}
                            >
                              <input
                                type="number"
                                min="0"
                                max="80"
                                step="1"
                                value={val}
                                onChange={(ev) => setCell(e.id, c.id, ev.target.value)}
                                style={{
                                  width: 62,
                                  height: 34,
                                  textAlign: "center",
                                  borderRadius: 8,
                                  border: flagged
                                    ? "1px solid var(--bad)"
                                    : filled
                                      ? `1px solid ${colHue}66`
                                      : "1px solid transparent",
                                  background: flagged
                                    ? "var(--badBg)"
                                    : filled
                                      ? `${colHue}14`
                                      : "transparent",
                                  color: flagged ? "var(--bad)" : filled ? "var(--text)" : "var(--faint)",
                                  fontFamily: mono,
                                  fontSize: 13,
                                  fontWeight: filled ? 600 : 400,
                                }}
                              />
                              {/* ⚠ is a button now, not a tooltip (#64). It opens
                                  the mapping affordance: which of the three causes
                                  this is, and the rate line it can be pointed at.
                                  The old dead-end told the user something was wrong
                                  and gave them nowhere to go. */}
                              {flagged && (
                                <button
                                  type="button"
                                  onClick={() => openMapping(cell.lcat, c.id, cell)}
                                  title={`${cell.lcat}: ${causeText(cell)} — click to map it to a rate line`}
                                  style={{
                                    position: "absolute",
                                    top: 2,
                                    right: 4,
                                    border: "none",
                                    background: "transparent",
                                    color: "var(--bad)",
                                    fontSize: 11,
                                    cursor: "pointer",
                                    padding: 2,
                                    lineHeight: 1,
                                  }}
                                >
                                  ⚠
                                </button>
                              )}
                              {/* Priced through a mapping the user confirmed, not a
                                  rate line the award prints. Shown, never implied. */}
                              {mapped && (
                                <button
                                  type="button"
                                  onClick={() => openMapping(cell.lcat, c.id, cell)}
                                  title={`${cell.lcat}: mapped to ${cell.rate_line?.lcat} on CLIN ${cell.rate_line?.clin} — click to change`}
                                  style={{
                                    position: "absolute",
                                    top: 2,
                                    right: 4,
                                    border: "none",
                                    background: "transparent",
                                    color: "var(--dim)",
                                    fontSize: 10,
                                    cursor: "pointer",
                                    padding: 2,
                                    lineHeight: 1,
                                  }}
                                >
                                  ⇄
                                </button>
                              )}
                            </td>
                          );
                        })}
                        <td style={{ padding: "10px 8px", textAlign: "center" }}>
                          {(() => {
                            const util = utilOf(e);
                            if (util == null)
                              return <span style={{ color: "var(--faint)" }}>—</span>;
                            const exp = expectedOf(e);
                            // 100% is now fully utilised, so the bands sit around 1
                            // rather than around the old 0.85-is-really-full fudge.
                            const uc =
                              util > 1.05
                                ? "var(--warn)"
                                : util >= 0.95
                                  ? "var(--good)"
                                  : "var(--dim)";
                            return (
                              <span
                                // The percentage is this contract's hours over a
                                // whole-person expectation, so a person billing
                                // elsewhere reads as having slack they do not have
                                // (#116). The cell keeps this contract's number —
                                // it is the column the grid edits — and the tooltip
                                // names the rest of their week.
                                title={
                                  `${rowHrsOf(e).toFixed(1)} hrs/wk against ${exp.hours} expected — ${exp.label}.` +
                                  (e?.hours_elsewhere
                                    ? ` Also booked ${e.hours_elsewhere} hrs/wk on ${(e.elsewhere || [])
                                        .map((x) => x.contract)
                                        .join(", ")} — ${e.headroom} hrs/wk left.`
                                    : "")
                                }
                                style={{ fontFamily: mono, fontSize: 12.5, fontWeight: 600, color: uc }}
                              >
                                {Math.round(util * 100)}%
                                {/* An assumed expectation is marked, so a number
                                    resting on the fallback never reads as configured. */}
                                {exp.assumed && (
                                  <span style={{ color: "var(--faint)", fontWeight: 400 }}>*</span>
                                )}
                              </span>
                            );
                          })()}
                        </td>
                        <td style={{ padding: "10px 16px", textAlign: "right" }}>
                          <div style={{ fontFamily: mono, fontWeight: 600, color: "var(--text)" }}>
                            {money(rowWeekly)}
                          </div>
                          {totalWeekly > 0 && (
                            <div style={{ fontSize: 10.5, color: "var(--faint)" }}>
                              {Math.round((rowWeekly / totalWeekly) * 100)}% of burn
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
                <tfoot>
                  <tr style={{ borderTop: "2px solid var(--border)", background: "var(--panel2)" }}>
                    <td colSpan={3} style={{ padding: "12px 16px", fontWeight: 700, color: "var(--text)" }}>
                      Forward weekly burn →
                    </td>
                    {clins.map((c) => (
                      <td
                        key={c.id}
                        style={{ padding: "12px 8px", textAlign: "center", fontFamily: mono, fontWeight: 600, fontSize: 12, color: statusColor(sim[c.id]?.status) }}
                      >
                        {money(sim[c.id]?.weekly || 0)}
                      </td>
                    ))}
                    <td
                      title={
                        current.totalExpected > 0
                          ? `${Math.round(totalHrs)} hrs/wk against ${Math.round(current.totalExpected)} expected across ${roster.length} people.`
                          : undefined
                      }
                      style={{ padding: "12px 8px", textAlign: "center", fontFamily: mono, fontWeight: 600, fontSize: 12, color: "var(--dim)" }}
                    >
                      {current.totalExpected > 0
                        ? `${Math.round((totalHrs / current.totalExpected) * 100)}%`
                        : "—"}
                    </td>
                    <td style={{ padding: "12px 16px", textAlign: "right", fontFamily: mono, fontWeight: 700, color: "var(--text)" }}>
                      {money(totalWeekly)}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>

          {/* per-CLIN runway cards */}
          <div
            style={{
              fontSize: 11,
              letterSpacing: ".08em",
              textTransform: "uppercase",
              color: "var(--faint)",
              fontWeight: 700,
              margin: "20px 0 10px",
            }}
          >
            Per-CLIN runway · click a card to filter the roster
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(240px,1fr))", gap: 14 }}>
            {clins.map((c, i) => {
              const s = sim[c.id] || {};
              const p = pill(s.status, s.ceilingBreached, s.fundsExceeded);
              const rc = statusColor(s.status);
              const baseWeek = c.base_exhaust_week;
              const delta =
                s.exhaustWeek != null && baseWeek != null
                  ? Math.round(s.exhaustWeek - baseWeek)
                  : 0;
              const active = clinFilter === c.id;
              return (
                <div
                  key={c.id}
                  onClick={() => setClinFilter(active ? null : c.id)}
                  title={active ? "Showing only this CLIN — click to show everyone" : `Show only people on ${c.code}`}
                  style={{
                    border: `${active ? 2 : 1}px solid ${rc}`,
                    borderRadius: 14,
                    padding: active ? "13px 14px" : "14px 15px",
                    background: p.style.background,
                    cursor: "pointer",
                    boxShadow: active ? `0 0 0 3px ${hueFor(i)}22` : "none",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ width: 9, height: 9, borderRadius: 3, background: hueFor(i) }} />
                    <span style={{ fontFamily: mono, fontSize: 11.5, color: "var(--dim)" }}>{c.code}</span>
                    <span style={p.style}>{p.label}</span>
                    {active && (
                      <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".06em", color: hueFor(i) }}>
                        FILTERING
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 12.5, color: "var(--text)", fontWeight: 600, marginTop: 8 }}>{c.name}</div>
                  <div style={row}>
                    {/* Against c.remaining (budget − spent), and budget is the
                        funded slice on an incrementally funded CLIN — so name the
                        funds, not the ceiling. */}
                    <span>Projected funds exhaustion</span>
                    <span style={{ fontWeight: 600, color: rc }}>
                      {s.exhaustWeek == null ? "—" : `Week ${Math.round(s.exhaustWeek)} / ${tw}`}
                    </span>
                  </div>
                  <div style={row}>
                    <span>Runway from today</span>
                    <span style={{ fontWeight: 600, color: rc }}>
                      {s.runwayDays == null ? "Paused" : `${s.runwayDays} days`}
                    </span>
                  </div>
                  <div style={row}>
                    <span>Weekly draw</span>
                    <span style={{ fontWeight: 600, color: "var(--text)" }}>
                      {c.remaining > 0
                        ? `${Math.round(((s.weekly || 0) / c.remaining) * 100)}% of remaining`
                        : "—"}
                    </span>
                  </div>
                  {dirty && delta !== 0 && (
                    <div style={{ ...row, color: "var(--faint)" }}>
                      <span>vs. actuals</span>
                      <span style={{ fontWeight: 600, color: delta > 0 ? "var(--good)" : "var(--bad)" }}>
                        {delta > 0 ? `+${delta}` : delta} wk
                      </span>
                    </div>
                  )}
                  {/* Rate coverage, cause-first (#64). This used to be one line —
                      "⚠ Unmatched LCAT: … — billed at blended" — for all three
                      failures at once, which is why a missing continuation sheet and
                      a single misspelled category read identically. */}
                  {c.rate_table_missing ? (
                    <div
                      style={{
                        marginTop: 10,
                        fontSize: 11,
                        // Not a warning when the spend didn't ride the blended rate
                        // (#144) — it's a note about what the picker can offer.
                        color: c.blended_priced_spend === false ? "var(--dim)" : "var(--warn)",
                      }}
                      onClick={(ev) => ev.stopPropagation()}
                    >
                      {c.blended_priced_spend === false ? (
                        <>
                          Direct rates only on this CLIN — its spend is priced per category from the
                          award&apos;s own buildup, so there is no billable rate line to map onto.
                        </>
                      ) : (
                        <>
                          {c.rate_table_state === "unburdened"
                            ? "Direct rates only on this CLIN — no burdened rate to bill from, so all "
                            : "No rate table on this CLIN — all "}
                          {c.unmatched_lcats?.length || 0} categor
                          {(c.unmatched_lcats?.length || 0) === 1 ? "y" : "ies"} bill at the blended
                          {c.blended_rate ? ` $${Math.round(c.blended_rate)}/hr` : " rate"}.
                        </>
                      )}
                      {/* No import offer when the schedule is already in (#139). */}
                      {c.rate_table_state !== "unburdened" && (
                        <div style={{ marginTop: 6 }}>
                          <ImportRateSchedule contractId={contractId} onImported={reloadRates} compact />
                        </div>
                      )}
                    </div>
                  ) : (
                    !!c.lcat_issues?.length && (
                      <div
                        style={{ marginTop: 10, fontSize: 11, color: "var(--warn)" }}
                        onClick={(ev) => ev.stopPropagation()}
                      >
                        {c.lcat_issues.slice(0, 3).map((iss) => (
                          <div key={iss.lcat} style={{ marginBottom: 4 }}>
                            <b>{iss.lcat}</b> · {Math.round(iss.hours)} hrs — {causeText(iss)}{" "}
                            <button
                              type="button"
                              onClick={() => openMapping(iss.lcat, c.id, iss)}
                              style={{
                                border: "none",
                                background: "transparent",
                                color: "var(--accent)",
                                fontSize: 11,
                                fontWeight: 600,
                                cursor: "pointer",
                                padding: 0,
                              }}
                            >
                              Map →
                            </button>
                          </div>
                        ))}
                        {c.lcat_issues.length > 3 && (
                          <div style={{ color: "var(--faint)" }}>
                            +{c.lcat_issues.length - 3} more
                          </div>
                        )}
                      </div>
                    )
                  )}
                  {!!c.aliased_lcats?.length && (
                    <div style={{ marginTop: 8, fontSize: 10.5, color: "var(--dim)" }}>
                      ⇄ {c.aliased_lcats.length} categor
                      {c.aliased_lcats.length === 1 ? "y" : "ies"} priced through a mapping you
                      confirmed.
                    </div>
                  )}
                  {/* #66's per-CLIN rollup. Rendered whenever anybody charges the line —
                      including when nothing has been checked — because "no findings" and
                      "nobody looked" are the two readings this must never let collapse
                      into each other, and only saying something when there's a finding
                      makes silence mean the wrong one. */}
                  {!!c.compliance?.people && (
                    <div
                      style={{
                        marginTop: 8,
                        fontSize: 10.5,
                        color: c.compliance.has_findings ? "var(--warn)" : "var(--dim)",
                      }}
                    >
                      {c.compliance.has_findings ? "⚑" : "◦"} Quals: {rollupText(c.compliance)}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {mapping && (
        <MappingPanel
          mapping={mapping}
          rateLines={rateLines.rate_lines || []}
          target={mapTarget}
          setTarget={setMapTarget}
          busy={mapBusy}
          result={mapResult}
          contractId={contractId}
          onApply={applyMapping}
          onRemove={removeMapping}
          onImported={reloadRates}
          onClose={() => {
            setMapping(null);
            setMapResult(null);
          }}
        />
      )}
      {qualsPanel && (
        <QualsPanel
          row={qualsPanel.row}
          clins={clins}
          person={directory.people.find((p) => p.employee_id === qualsPanel.row.id)}
          vocab={directory.qualVocab || {}}
          loading={directory.loading}
          draft={qualsPanel.draft}
          setDraft={(draft) => setQualsPanel((q) => q && { ...q, draft })}
          saving={qualsPanel.saving}
          error={qualsPanel.error}
          onSave={saveQuals}
          onClose={() => setQualsPanel(null)}
        />
      )}
      {pendingDelete && (
        <ConfirmDialog
          title={`Delete “${pendingDelete.name || "Untitled plan"}”?`}
          confirmLabel="Delete plan"
          busy={deleteBusy}
          error={deleteError}
          onCancel={() => setPendingDelete(null)}
          onConfirm={confirmDeletePlan}
        >
          {/* What actually goes, and — the part worth saying out loud — what doesn't.
              A plan is a question, not a record of work: no synced hours, no rates and
              no funding are touched by deleting one. */}
          <div>
            This saved plan — its hours grid, planned adds, roll-offs and any absences
            typed into it — will be removed.{" "}
            <b style={{ color: "var(--text)" }}>This can't be undone.</b>
          </div>
          <div style={{ marginTop: 8 }}>
            No synced hours, rates or funding change. Only the plan goes.
          </div>
          <div
            style={{
              marginTop: 12,
              padding: "8px 11px",
              borderRadius: 10,
              background: "var(--panel2)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <span style={{ fontWeight: 600, color: "var(--text)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
              {pendingDelete.name || "Untitled plan"}
            </span>
            {staleReasons[pendingDelete.id]?.length > 0 && <span style={staleChip}>STALE</span>}
            <span style={{ marginLeft: "auto", fontSize: 11.5, color: "var(--faint)", whiteSpace: "nowrap" }}>
              {pendingDelete.updated_at
                ? `Updated ${shortDate(pendingDelete.updated_at)}`
                : `Saved ${shortDate(pendingDelete.created_at)}`}
            </span>
          </div>
          {loadedPlan === pendingDelete.id && (
            // Deleting the plan you're in doesn't clear the grid, and a dialog that
            // let you assume it did would be scarier than the truth.
            <div style={{ marginTop: 10, color: "var(--dim)" }}>
              You're editing this plan. The numbers stay on screen as an unsaved
              what-if — you'd just have to name it again to save it.
            </div>
          )}
        </ConfirmDialog>
      )}
    </div>
  );
}

// #66's compliance detail, and the inline path to fix it.
//
// Three jobs, in the order somebody reading a flag needs them: what the award requires
// and what we know, which rate line and CLIN drove that requirement, and the boxes to
// type in whatever is missing without leaving the grid.
//
// The panel never asserts anything the check didn't. Fields the award prints no floor
// for are shown as "not required" rather than hidden, because "we didn't check this"
// and "nothing to check" are the two states a compliance screen most needs to keep
// apart, and hiding one makes the screen look more thorough than it is.
function QualsPanel({
  row,
  clins,
  person,
  vocab,
  loading,
  draft,
  setDraft,
  saving,
  error,
  onSave,
  onClose,
}) {
  const stored = person?.quals || {};
  // A field's current text: what the user has typed this session, else what's on file.
  const valueOf = (field) =>
    draft?.[field]?.value ?? stored[field]?.value ?? "";
  const noteOf = (field) =>
    draft?.[field]?.source_note ?? stored[field]?.source_note ?? "";
  const edit = (field, patch) =>
    setDraft({
      ...(draft || {}),
      [field]: {
        value: draft?.[field]?.value ?? stored[field]?.value ?? "",
        source_note: draft?.[field]?.source_note ?? stored[field]?.source_note ?? "",
        ...patch,
      },
    });

  const FIELDS = [
    { key: "education", label: "Education", vocab: "education" },
    { key: "years_experience", label: "Years of experience", numeric: true },
    {
      key: "clearance",
      label: "Clearance",
      vocab: "clearance",
      // Worth saying on the screen where a clearance gap is being reported: picking
      // "None" is an assertion that they hold none, which fails a Secret floor. It is
      // not the same as leaving the box alone.
      note: "“None” records that they hold no clearance — not the same as leaving this blank.",
    },
  ];

  // Every CLIN this person charges that has a verdict worth reading, worst first, so
  // the line that drove the badge is the one at the top.
  const cells = Object.entries(row.cells || {})
    .map(([clinId, cell]) => ({ clinId, cell, v: cell.compliance }))
    .filter((x) => x.v)
    .sort((a, b) => (b.cell.hours || 0) - (a.cell.hours || 0));

  const label = { fontSize: 11, color: "var(--faint)", fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase" };
  const input = {
    width: "100%",
    height: 32,
    padding: "0 10px",
    borderRadius: 9,
    border: "1px solid var(--border)",
    background: "var(--panel2)",
    color: "var(--text)",
    fontSize: 12.5,
  };
  const btn = (primary) => ({
    height: 32,
    padding: "0 14px",
    borderRadius: 9,
    border: primary ? "none" : "1px solid var(--border)",
    background: primary ? "var(--accent)" : "var(--panel2)",
    color: primary ? "#fff" : "var(--text)",
    fontSize: 12.5,
    fontWeight: 600,
    cursor: saving ? "default" : "pointer",
    opacity: saving ? 0.6 : 1,
  });

  const badgeInfo = complianceBadge(row.compliance_status);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,.35)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 60,
        padding: 20,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ ...panelStyle, padding: 20, width: 560, maxWidth: "100%", maxHeight: "90vh", overflow: "auto" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 16, color: "var(--text)" }}>
            {row.name}
          </div>
          {badgeInfo && (
            <span style={{ ...compliancePill(badgeInfo.tone), cursor: "default" }}>{badgeInfo.label}</span>
          )}
        </div>
        <div style={{ fontSize: 12.5, color: "var(--dim)", marginTop: 6, lineHeight: 1.5 }}>
          Qualifications are checked against the minimums the award prints beside the
          rate line these hours actually bill at. Runway reports; it never blocks a charge.
        </div>

        {cells.map(({ clinId, cell, v }) => {
          const clin = clins.find((c) => c.id === clinId);
          return (
            <div
              key={clinId}
              style={{
                marginTop: 14,
                padding: "10px 12px",
                borderRadius: 10,
                background: "var(--panel2)",
                border: "1px solid var(--border)",
              }}
            >
              <div style={{ fontSize: 12, color: "var(--text)", fontWeight: 700 }}>
                CLIN {clinId}
                {clin?.name ? ` — ${clin.name}` : ""}
              </div>
              {/* Which rate line drove the requirement. Named explicitly because it is
                  not always the LCAT on the timesheet: a confirmed mapping prices these
                  hours off another category, and its floors are the ones that apply. */}
              <div style={{ fontSize: 11.5, color: "var(--dim)", marginTop: 4, lineHeight: 1.5 }}>
                {v.line ? (
                  <>
                    Billing as <b style={{ color: "var(--text)" }}>{v.line.lcat}</b>
                    {v.line.rate ? ` at $${Math.round(v.line.rate)}/hr` : ""}
                    {cell.lcat && cell.lcat !== v.line.lcat ? ` (charged as “${cell.lcat}”)` : ""}
                    {v.line.clin && v.line.clin !== clinId ? ` — priced on CLIN ${v.line.clin}` : ""}
                  </>
                ) : (
                  <>
                    These hours don&apos;t resolve to a priced rate line, so there is no
                    qualification floor to check. Map the category first.
                  </>
                )}
              </div>
              {v.failures.length > 0 && (
                <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 12, color: "var(--text)", lineHeight: 1.6 }}>
                  {v.failures.map((f) => (
                    <li key={f.field}>
                      <span style={{ color: f.field === "clearance" ? "var(--bad)" : "var(--warn)" }}>
                        {failureText(f, v.line?.lcat)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {v.unchecked.length > 0 && (
                <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 11.5, color: "var(--dim)", lineHeight: 1.6 }}>
                  {v.unchecked.map((u) => (
                    <li key={u.field}>{uncheckedText(u)}</li>
                  ))}
                </ul>
              )}
              {v.status === "no_floor" && (
                <div style={{ fontSize: 11.5, color: "var(--dim)", marginTop: 8 }}>
                  The award prints no minimum education, experience or clearance for this
                  category, so there is nothing to check here. That&apos;s a gap in the rate
                  schedule, not a clean result.
                </div>
              )}
              {v.over_qualified_for && (
                <div style={{ fontSize: 11.5, color: "var(--accent)", marginTop: 8, lineHeight: 1.5 }}>
                  Meets the minimums for {v.over_qualified_for.lcat} ($
                  {Math.round(v.over_qualified_for.rate)}/hr) on CLIN {v.over_qualified_for.clin} —
                  not a violation, but likely money left on the table.
                </div>
              )}
              {/* The requirement table, including the fields with no floor. */}
              <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr auto auto", gap: "4px 12px", fontSize: 11.5 }}>
                <div style={label}>Field</div>
                <div style={{ ...label, textAlign: "right" }}>Required</div>
                <div style={{ ...label, textAlign: "right" }}>On file</div>
                {v.fields.map((f) => (
                  <React.Fragment key={f.field}>
                    <div style={{ color: "var(--dim)" }}>{f.label}</div>
                    <div style={{ textAlign: "right", color: f.required == null ? "var(--faint)" : "var(--text)" }}>
                      {f.required == null ? "not required" : String(f.required)}
                    </div>
                    <div
                      style={{
                        textAlign: "right",
                        color: f.state === "short" ? "var(--bad)" : f.held == null ? "var(--faint)" : "var(--text)",
                      }}
                    >
                      {f.held == null ? "—" : String(f.held)}
                    </div>
                  </React.Fragment>
                ))}
              </div>
            </div>
          );
        })}

        <div style={{ marginTop: 18, ...label }}>What we know about them</div>
        <div style={{ fontSize: 11.5, color: "var(--dim)", marginTop: 4, lineHeight: 1.5 }}>
          Optional, and stored against the person rather than this contract. Leave a field
          blank to keep it unrecorded; clearing one returns it to unchecked.
        </div>
        {loading && !person && (
          <div style={{ fontSize: 11.5, color: "var(--dim)", marginTop: 10 }}>Loading what&apos;s on file…</div>
        )}
        <div style={{ marginTop: 10, display: "grid", gap: 10 }}>
          {FIELDS.map((f) => {
            const options = f.vocab ? vocab[f.vocab] || [] : null;
            const current = valueOf(f.key);
            // A value typed before the vocabularies closed (#98) won't be in the list.
            // Kept as a selectable option rather than silently swapped for a blank,
            // which would look like the app had deleted somebody's record.
            const offLadder = options && current && !options.includes(current);
            return (
              <div key={f.key}>
                <div style={{ fontSize: 11.5, color: "var(--dim)", marginBottom: 4 }}>{f.label}</div>
                <div style={{ display: "flex", gap: 8 }}>
                  {options ? (
                    <select
                      value={current}
                      onChange={(e) => edit(f.key, { value: e.target.value })}
                      style={{ ...input, flex: "0 0 45%" }}
                    >
                      <option value="">Not recorded</option>
                      {offLadder && <option value={current}>{current} (not a standard level)</option>}
                      {options.map((o) => (
                        <option key={o} value={o}>
                          {o}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="number"
                      min="0"
                      value={current}
                      placeholder="—"
                      onChange={(e) => edit(f.key, { value: e.target.value })}
                      style={{ ...input, flex: "0 0 45%" }}
                    />
                  )}
                  {/* Provenance, because the first thing anybody does with a compliance
                      flag is argue with it. "per proposal resume, 2026-03" is a different
                      conversation from a bare number. */}
                  <input
                    value={noteOf(f.key)}
                    placeholder="Source — e.g. per proposal resume, 2026-03"
                    onChange={(e) => edit(f.key, { source_note: e.target.value })}
                    style={{ ...input, flex: 1 }}
                  />
                </div>
                {f.note && (
                  <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 4 }}>{f.note}</div>
                )}
              </div>
            );
          })}
        </div>

        {error && (
          <div style={{ marginTop: 12, fontSize: 12, color: "var(--bad)" }}>{error}</div>
        )}
        <div style={{ marginTop: 16, display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button type="button" onClick={onClose} disabled={saving} style={btn(false)}>
            Close
          </button>
          <button
            type="button"
            onClick={() => onSave(draft)}
            disabled={saving || !draft}
            style={{ ...btn(true), opacity: saving || !draft ? 0.6 : 1 }}
          >
            {saving ? "Saving…" : "Save qualifications"}
          </button>
        </div>
      </div>
    </div>
  );
}

// The mapping affordance behind the ⚠ (#64) — the thing that makes the flag not a
// dead end. It states which of the three causes it is, offers the rate lines this
// contract actually prices (including ones on other CLINs, which is the only fix
// for a "priced elsewhere" charge), and after applying, shows what the money did.
//
// The engine's suggestion is pre-selected but never pre-applied: a fuzzy match that
// moved spend-to-date without a human agreeing to it is exactly the failure mode
// `lcat.py` is built to avoid.
function MappingPanel({
  mapping,
  rateLines,
  target,
  setTarget,
  busy,
  result,
  contractId,
  onApply,
  onRemove,
  onImported,
  onClose,
}) {
  const rows = [];
  if (result?.before && result?.after) {
    for (const [id, after] of Object.entries(result.after)) {
      const before = result.before[id];
      if (!before) continue;
      if (before.spent === after.spent && before.runway_days === after.runway_days) continue;
      rows.push({ id, before, after });
    }
  }

  const label = { fontSize: 11, color: "var(--faint)", fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase" };
  const btn = (primary) => ({
    height: 32,
    padding: "0 14px",
    borderRadius: 9,
    border: primary ? "none" : "1px solid var(--border)",
    background: primary ? "var(--accent)" : "var(--panel2)",
    color: primary ? "#fff" : "var(--text)",
    fontSize: 12.5,
    fontWeight: 600,
    cursor: busy ? "default" : "pointer",
    opacity: busy ? 0.6 : 1,
  });

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,.35)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 60,
        padding: 20,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ ...panelStyle, padding: 20, width: 520, maxWidth: "100%", maxHeight: "90vh", overflow: "auto" }}
      >
        <div style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 16, color: "var(--text)" }}>
          Map “{mapping.lcat}”
        </div>
        <div style={{ fontSize: 12.5, color: "var(--dim)", marginTop: 6, lineHeight: 1.5 }}>
          Charged on CLIN {mapping.clinId} — {causeText(mapping)}.
        </div>

        {/* Cause A has no mapping answer: no rate line exists to point at, so the
            panel offers the document instead of a picker full of nothing. On the
            unburdened half (#139) there is equally nothing to map to, but the
            document is already here — say so and offer nothing. */}
        {mapping.cause === "clin_unburdened" && !rateLines.length ? (
          <div style={{ marginTop: 16, fontSize: 12.5, color: "var(--text)" }}>
            This contract&apos;s rate schedule prices each category at an unburdened direct rate, with the
            indirect factors stated separately, so there is no billable rate line to map to. Nothing to
            import — the schedule is already ingested.
          </div>
        ) : mapping.cause === "clin_unpriced" && !rateLines.length ? (
          <div style={{ marginTop: 16 }}>
            <div style={{ fontSize: 12.5, color: "var(--text)", marginBottom: 10 }}>
              This contract has no rate lines at all yet, so there is nothing to map to. Import the award&apos;s
              rate schedule (the continuation sheet that prints the fully-burdened rates) and these
              categories resolve on their own.
            </div>
            <ImportRateSchedule contractId={contractId} onImported={onImported} />
          </div>
        ) : (
          <>
            {mapping.suggestion && (
              <div
                style={{
                  marginTop: 14,
                  padding: "10px 12px",
                  borderRadius: 10,
                  border: "1px solid var(--border)",
                  background: "var(--panel2)",
                  fontSize: 12.5,
                  color: "var(--text)",
                }}
              >
                Closest match: <b>{mapping.suggestion.lcat}</b> · CLIN {mapping.suggestion.clin} ·{" "}
                {money(mapping.suggestion.rate)}/hr
                <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 3 }}>
                  Suggested, not applied — confirm below and the hours re-price.
                </div>
              </div>
            )}

            <div style={{ marginTop: 16 }}>
              <div style={label}>Bill these hours at</div>
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                style={{
                  marginTop: 7,
                  width: "100%",
                  height: 36,
                  padding: "0 10px",
                  borderRadius: 9,
                  border: "1px solid var(--border)",
                  background: "var(--inputBg)",
                  color: "var(--text)",
                  fontSize: 12.5,
                }}
              >
                <option value="">Select a rate line…</option>
                {rateLines.map((l) => (
                  <option key={`${l.clin}|${l.lcat}`} value={`${l.clin}|${l.lcat}`}>
                    {l.lcat} — CLIN {l.clin} — {money(l.rate)}/hr
                    {l.basis === "burdened" ? " (burdened)" : ""}
                    {l.clin !== mapping.clinId ? " (other CLIN)" : ""}
                  </option>
                ))}
              </select>
            </div>
          </>
        )}

        {/* What the mapping did to the money. The acceptance criterion this exists
            to satisfy: applying a mapping changes spent / remaining / runway, and
            the change is visible rather than a badge quietly clearing. */}
        {rows.length > 0 && (
          <div style={{ marginTop: 16, fontSize: 12.5, color: "var(--text)" }}>
            <div style={label}>What changed</div>
            {rows.map((r) => (
              <div key={r.id} style={{ marginTop: 7, fontFamily: mono, fontSize: 12 }}>
                CLIN {r.id}: {money(r.before.spent)} → <b>{money(r.after.spent)}</b> spent
                {r.before.runway_days != null && r.after.runway_days != null && (
                  <>
                    {" · "}
                    {r.before.runway_days} → <b>{r.after.runway_days}</b> days runway
                  </>
                )}
              </div>
            ))}
          </div>
        )}
        {result && rows.length === 0 && (
          <div style={{ marginTop: 14, fontSize: 12, color: "var(--dim)" }}>
            Saved. No CLIN&apos;s spend moved — the mapped rate matches what these hours already billed at.
          </div>
        )}

        <div style={{ display: "flex", gap: 10, marginTop: 20, flexWrap: "wrap" }}>
          <button type="button" style={btn(true)} disabled={busy || !target} onClick={onApply}>
            {busy ? "Applying…" : mapping.existing ? "Update mapping" : "Apply mapping"}
          </button>
          {mapping.existing && (
            <button type="button" style={btn(false)} disabled={busy} onClick={onRemove}>
              Remove mapping
            </button>
          )}
          <button type="button" style={btn(false)} onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// Side-by-side comparison of two plan states (Current or a saved plan), scored
// through the same evalPlan the live view uses.
function ComparePanel({ a, b, setA, setB, plans, clins, tw, cmpLabel, evalPlan, planStateFor, onClose }) {
  const A = evalPlan(planStateFor(a));
  const B = evalPlan(planStateFor(b));
  const sel = {
    height: 32,
    padding: "0 10px",
    borderRadius: 9,
    border: "1px solid var(--border)",
    background: "var(--panel2)",
    color: "var(--text)",
    fontSize: 12.5,
    cursor: "pointer",
  };
  const options = [{ value: "current", label: "Current plan" }, ...plans.map((p) => ({ value: String(p.id), label: p.name }))];

  // rows: { label, av, bv, dir (1 higher-better, -1 lower-better, 0 neutral), kind }
  const rows = [
    { label: "Headcount", av: A.headcount, bv: B.headcount, dir: 0, kind: "num" },
    {
      label: "FTEs",
      av: A.totalHrs / FTE_HOURS_PER_WEEK,
      bv: B.totalHrs / FTE_HOURS_PER_WEEK,
      dir: 0,
      kind: "fte",
    },
    { label: "Forward weekly burn", av: A.totalWeekly, bv: B.totalWeekly, dir: -1, kind: "money" },
    ...clins.map((c) => ({
      label: `${c.code} runway`,
      av: A.clin[c.id]?.runwayDays,
      bv: B.clin[c.id]?.runwayDays,
      dir: 1,
      kind: "days",
    })),
  ];

  const fmt = (v, kind) =>
    v == null ? "—" : kind === "money" ? money(v) : kind === "fte" ? v.toFixed(1) : kind === "days" ? `${v}d` : v;
  const delta = (av, bv, dir, kind) => {
    if (av == null || bv == null) return <span style={{ color: "var(--faint)" }}>—</span>;
    const d = bv - av;
    const color = d === 0 || dir === 0 ? "var(--dim)" : (dir > 0) === d > 0 ? "var(--good)" : "var(--bad)";
    const mag = kind === "money" ? money(Math.abs(d)) : kind === "fte" ? Math.abs(d).toFixed(1) : `${Math.abs(d)}${kind === "days" ? "d" : ""}`;
    return (
      <span style={{ color, fontWeight: 600 }}>
        {d === 0 ? "—" : `${d > 0 ? "+" : "−"}${mag}`}
      </span>
    );
  };

  const cell = { padding: "9px 14px", fontFamily: mono, fontSize: 13 };
  return (
    <div style={{ ...panelStyle, padding: 0, overflow: "hidden", marginBottom: 12 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", padding: "12px 14px", flexWrap: "wrap" }}>
        <span style={{ fontSize: 12.5, color: "var(--dim)" }}>Compare</span>
        <select value={a} onChange={(e) => setA(e.target.value)} style={sel}>
          {options.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <span style={{ color: "var(--faint)" }}>vs</span>
        <select value={b} onChange={(e) => setB(e.target.value)} style={sel}>
          {options.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <button
          onClick={onClose}
          title="Close comparison"
          style={{
            marginLeft: "auto",
            width: 28,
            height: 28,
            borderRadius: 8,
            border: "1px solid var(--border)",
            background: "var(--panel2)",
            color: "var(--dim)",
            cursor: "pointer",
            fontSize: 15,
            lineHeight: 1,
          }}
        >
          ×
        </button>
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr style={{ background: "var(--panel2)", color: "var(--faint)", fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em" }}>
            <th style={{ textAlign: "left", padding: "10px 14px", fontWeight: 700 }}>Metric</th>
            <th style={{ textAlign: "right", padding: "10px 14px", fontWeight: 700, maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis" }}>{cmpLabel(a)}</th>
            <th style={{ textAlign: "right", padding: "10px 14px", fontWeight: 700 }}>{cmpLabel(b)}</th>
            <th style={{ textAlign: "right", padding: "10px 14px", fontWeight: 700 }}>Δ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} style={{ borderTop: "1px solid var(--border)" }}>
              <td style={{ padding: "9px 14px", color: "var(--text)", fontWeight: 500 }}>{r.label}</td>
              <td style={{ ...cell, textAlign: "right", color: "var(--dim)" }}>{fmt(r.av, r.kind)}</td>
              <td style={{ ...cell, textAlign: "right", color: "var(--text)", fontWeight: 600 }}>{fmt(r.bv, r.kind)}</td>
              <td style={{ ...cell, textAlign: "right" }}>{delta(r.av, r.bv, r.dir, r.kind)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Drift vs the active baseline (#67 item 2) — the plan we committed to against the
// hours people are actually charging. Presentation only: the arithmetic and the
// wording both live in drift.js, so the Flight Deck card can say the same thing.
function DriftPanel({ drift, baselineName, clins, runwayDelta, onClose }) {
  const clinName = (id) => clins.find((c) => String(c.id) === String(id))?.code || `CLIN ${id}`;
  const cell = { padding: "9px 14px", fontFamily: mono, fontSize: 13, textAlign: "right" };
  // Over plan is the expensive direction, so it is the one that gets a warning
  // color. Under plan is not automatically good news — it can mean the work isn't
  // getting done — so it stays neutral rather than green.
  const deltaColor = (d) => (d > 0 ? "var(--warn)" : d < 0 ? "var(--dim)" : "var(--faint)");
  const signed = (d, fmt) => (d === 0 ? "—" : `${d > 0 ? "+" : "−"}${fmt(Math.abs(d))}`);

  return (
    <div style={{ ...panelStyle, padding: 0, overflow: "hidden", marginBottom: 12 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", padding: "12px 14px", flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
          Drift vs “{baselineName}”
        </span>
        <span style={{ fontSize: 11.5, color: "var(--faint)" }}>
          The committed staffing against what people are actually charging — not
          against the what-if on screen.
        </span>
        <button onClick={onClose} title="Close" style={{ marginLeft: "auto", width: 28, height: 28, borderRadius: 8, border: "1px solid var(--border)", background: "var(--panel2)", color: "var(--dim)", cursor: "pointer", fontSize: 15, lineHeight: 1 }}>
          ×
        </button>
      </div>

      {drift.people.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ background: "var(--panel2)", color: "var(--faint)", fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em" }}>
              <th style={{ textAlign: "left", padding: "10px 14px", fontWeight: 700 }}>Person</th>
              <th style={{ textAlign: "right", padding: "10px 14px", fontWeight: 700 }}>Baseline</th>
              <th style={{ textAlign: "right", padding: "10px 14px", fontWeight: 700 }}>Actual</th>
              <th style={{ textAlign: "right", padding: "10px 14px", fontWeight: 700 }}>Δ hrs/wk</th>
              <th style={{ textAlign: "right", padding: "10px 14px", fontWeight: 700 }}>Δ $/wk</th>
            </tr>
          </thead>
          <tbody>
            {drift.people.map((p) => (
              <tr key={p.id} style={{ borderTop: "1px solid var(--border)" }} title={driftSentence(p)}>
                <td style={{ padding: "9px 14px", color: "var(--text)", fontWeight: 500 }}>
                  {p.name}
                  {/* Roster drift — somebody who isn't in the plan at all, or who the
                      plan rolled off. An hours delta alone can't say which. */}
                  {(p.kind === "unplanned" || p.kind === "rolled_off_charging" || p.kind === "not_charging") && (
                    <span style={{ display: "block", fontSize: 10.5, color: "var(--faint)", fontWeight: 400 }}>
                      {p.kind === "unplanned"
                        ? "not on the baseline"
                        : p.kind === "rolled_off_charging"
                          ? "rolled off in the baseline, still charging"
                          : "on the baseline, charging nothing"}
                    </span>
                  )}
                </td>
                <td style={{ ...cell, color: "var(--dim)" }}>{p.baselineHrs ? p.baselineHrs.toFixed(1) : "—"}</td>
                <td style={{ ...cell, color: "var(--text)", fontWeight: 600 }}>{p.actualHrs ? p.actualHrs.toFixed(1) : "—"}</td>
                <td style={{ ...cell, color: deltaColor(p.deltaHrs), fontWeight: 600 }}>
                  {signed(Math.round(p.deltaHrs * 10) / 10, (v) => v.toFixed(1))}
                </td>
                <td style={{ ...cell, color: deltaColor(p.deltaCost), fontWeight: 600 }}>
                  {signed(Math.round(p.deltaCost), money)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Per CLIN, with what the gap has cost in runway — the number that gives the
          percentage a deadline. Only CLINs that actually moved. */}
      {drift.clins.some((c) => Math.abs(c.delta) >= 1) && (
        <div style={{ borderTop: "1px solid var(--border)", padding: "10px 14px", display: "flex", flexWrap: "wrap", gap: 14 }}>
          {drift.clins
            .filter((c) => Math.abs(c.delta) >= 1)
            .map((c) => (
              <div key={c.id} style={{ fontSize: 12, color: "var(--dim)" }}>
                <b style={{ color: "var(--text)" }}>{clinName(c.id)}</b>{" "}
                <span style={{ fontFamily: mono }}>
                  {money(c.baseline)} → {money(c.actual)}/wk
                </span>{" "}
                <span style={{ color: deltaColor(c.delta), fontWeight: 600 }}>
                  {signed(Math.round(c.delta), money)}
                </span>
                {runwayDelta?.[c.id] != null && runwayDelta[c.id] !== 0 && (
                  <span style={{ color: runwayDelta[c.id] < 0 ? "var(--warn)" : "var(--dim)" }}>
                    {" · "}
                    {runwayDelta[c.id] < 0
                      ? `${Math.abs(runwayDelta[c.id])} days of runway lost since the plan was set`
                      : `${runwayDelta[c.id]} days gained`}
                  </span>
                )}
              </div>
            ))}
        </div>
      )}

      {/* Planned but not charging. Kept out of the drift numbers on purpose: a hire
          who hasn't started is a plan not yet executed, not a staffing breach. */}
      {drift.planned.length > 0 && (
        <div style={{ borderTop: "1px solid var(--border)", padding: "10px 14px", fontSize: 12, color: "var(--dim)" }}>
          <b style={{ color: "var(--text)" }}>Planned, not charging yet:</b>{" "}
          {drift.planned.map((p) => `${p.name} (${p.baselineHrs.toFixed(0)} hrs/wk)`).join(", ")}
          <span style={{ color: "var(--faint)" }}>
            {" "}
            — not counted as drift until they appear on a timesheet.
          </span>
        </div>
      )}

      {drift.people.length === 0 && (
        <div style={{ borderTop: "1px solid var(--border)", padding: "12px 14px", fontSize: 12.5, color: "var(--good)" }}>
          Everyone is charging the baseline hours, within half an hour a week.
        </div>
      )}
    </div>
  );
}


const th = {
  textAlign: "left",
  padding: "12px 12px",
  fontSize: 11,
  letterSpacing: ".06em",
  textTransform: "uppercase",
  color: "var(--faint)",
  fontWeight: 700,
};
// A sortable header: same look, but clearly clickable (pointer + no text-select).
const thSort = { ...th, cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" };

// Control-bar button styles (shared so the single toolbar stays consistent).
const chipBtn = {
  height: 30,
  padding: "0 12px",
  borderRadius: 20,
  border: "1px solid var(--accent)",
  background: "transparent",
  color: "var(--accent)",
  fontWeight: 600,
  fontSize: 12,
  cursor: "pointer",
};
const chipBtnDim = { ...chipBtn, border: "1px solid var(--border)", color: "var(--dim)" };
// A disabled button has to *look* disabled. `primaryBtn` keeps its accent fill,
// pointer cursor and shadow when the `disabled` attribute is set, so a button that
// silently refuses to fire is indistinguishable from one that works — which reads
// as "the button is broken" rather than "I haven't finished filling the form".
const disabledBtn = {
  background: "var(--panel2)",
  color: "var(--faint)",
  boxShadow: "none",
  cursor: "not-allowed",
};
// Commit is the one action in the chip strip that writes to the server and moves
// what everyone else sees, so it has to read as a button rather than as another
// label sitting among labels. Same visual language as `primaryBtn` — accent fill,
// white text, a shadow to lift it off the chip — shrunk to chip scale.
const commitBtn = {
  border: "none",
  borderRadius: 6,
  background: "var(--accent)",
  color: "#fff",
  cursor: "pointer",
  fontSize: 10.5,
  fontWeight: 700,
  lineHeight: 1,
  padding: "4px 8px",
  boxShadow: "0 2px 6px rgba(67,97,238,.32)",
};
const dateInput = {
  height: 34,
  padding: "0 10px",
  borderRadius: 10,
  border: "1px solid var(--border)",
  background: "var(--inputBg)",
  color: "var(--text)",
  fontSize: 13,
  fontFamily: mono,
};
const primaryBtn = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  height: 34,
  padding: "0 13px",
  borderRadius: 10,
  border: "none",
  background: "var(--accent)",
  color: "#fff",
  fontWeight: 600,
  fontSize: 12.5,
  cursor: "pointer",
  boxShadow: "0 4px 12px rgba(67,97,238,.28)",
};
const secondaryBtn = {
  height: 34,
  padding: "0 13px",
  borderRadius: 10,
  border: "1px solid var(--border)",
  background: "var(--panel)",
  color: "var(--text)",
  fontWeight: 600,
  fontSize: 12.5,
  cursor: "pointer",
};
const exportBtn = {
  display: "flex",
  alignItems: "center",
  gap: 7,
  height: 34,
  padding: "0 13px",
  borderRadius: 10,
  border: "1px solid var(--good)",
  background: "var(--goodBg)",
  color: "var(--good)",
  fontWeight: 600,
  fontSize: 12.5,
  cursor: "pointer",
};
const menuItem = {
  display: "block",
  width: "100%",
  textAlign: "left",
  padding: "8px 10px",
  borderRadius: 8,
  border: "none",
  background: "transparent",
  color: "var(--text)",
  fontWeight: 600,
  fontSize: 12.5,
  cursor: "pointer",
};
const menuLabel = {
  fontSize: 10,
  letterSpacing: ".07em",
  textTransform: "uppercase",
  color: "var(--faint)",
  fontWeight: 700,
  padding: "4px 10px",
};
const menuDivider = { height: 1, background: "var(--border)", margin: "6px 4px" };
// A plan whose contract has moved under it (#67). Only ever set from real evidence:
// a plan saved before snapshots existed carries no chip, because "we can't tell" and
// "it's stale" are different answers.
const staleChip = {
  fontSize: 9,
  fontWeight: 700,
  letterSpacing: ".05em",
  color: "var(--warn)",
  border: "1px solid var(--warn)",
  borderRadius: 5,
  padding: "0 4px",
  flexShrink: 0,
};
// The active baseline (#67). Accent rather than warn: a baseline is a decision that
// has been made, not a problem — the chip has to read differently from STALE sitting
// next to it on the same row.
const baselineChip = {
  fontSize: 9,
  fontWeight: 700,
  letterSpacing: ".05em",
  color: "var(--accent)",
  border: "1px solid var(--accent)",
  borderRadius: 5,
  padding: "0 4px",
  flexShrink: 0,
};
const baselineToggle = {
  border: "none",
  background: "transparent",
  padding: "0 2px",
  fontSize: 14,
  lineHeight: 1,
  flexShrink: 0,
};
const staleTitle = (reasons) =>
  `Saved under terms that have since changed — ${reasons.join("; ")}.`;
const row = {
  display: "flex",
  justifyContent: "space-between",
  marginTop: 8,
  fontSize: 12,
  color: "var(--dim)",
};
