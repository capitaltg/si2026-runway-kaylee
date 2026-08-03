import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  getAllocation,
  listContracts,
  listPlans,
  savePlan,
  deletePlan,
} from "../api.js";
import { money, panelStyle, hueFor, statusColor, pill } from "../format.js";

const grotesk = "'Space Grotesk',sans-serif";
const mono = "'IBM Plex Mono',monospace";

// Initials for the avatar chip, from an employee name.
function initials(name) {
  const parts = (name || "").trim().split(/\s+/);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase() || "—";
}

// Forward status from a projected exhaustion week, mirroring burn.py's bands
// (minus the funding nuance — the simulator is a straight hrs→runway what-if).
function simStatus(exhaustWeek, totalWeeks) {
  if (exhaustWeek == null) return "paused";
  if (exhaustWeek < totalWeeks - 1) return "over";
  if (exhaustWeek < totalWeeks + 2) return "watch";
  if (exhaustWeek > totalWeeks * 1.15) return "under";
  return "ok";
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

export default function AllocationMatrix({
  contractId,
  setActiveId,
  autoBalance,
  onAutoBalanced,
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
  // What-if roster edits: `added` are planned new people, `removed` are ids rolled
  // off the plan. Both live only in this simulation until Reset (nothing persists).
  const [added, setAdded] = useState([]);
  const [removed, setRemoved] = useState([]);
  const [newPerson, setNewPerson] = useState(null); // the open "add person" form
  const addSeq = useRef(0);
  // Saved plans (persisted server-side): the list, the open save-name form, and
  // which plan is currently loaded.
  const [plans, setPlans] = useState([]);
  const [planName, setPlanName] = useState(null);
  const [loadedPlan, setLoadedPlan] = useState(null);
  // Side-by-side plan comparison.
  const [comparing, setComparing] = useState(false);
  const [cmpA, setCmpA] = useState("current");
  const [cmpB, setCmpB] = useState("current");
  const [plansMenuOpen, setPlansMenuOpen] = useState(false);

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
    setLoadedPlan(null);
    getAllocation(contractId)
      .then((d) => {
        setData(d);
        setDraft(buildDraft(d));
      })
      .catch((e) => setError(e.message));
  }, [contractId]);

  const refreshPlans = () => {
    if (contractId)
      listPlans(contractId)
        .then(setPlans)
        .catch(() => setPlans([]));
  };
  useEffect(refreshPlans, [contractId]);

  const clins = data?.clins || [];
  const employees = data?.employees || [];
  const { current_week: cw, total_weeks: tw } = data?.contract || {};

  // The effective roster this plan runs on: synced people minus those rolled off,
  // plus any planned adds.
  const roster = useMemo(
    () => [...employees.filter((e) => !removed.includes(e.id)), ...added],
    [employees, added, removed],
  );

  // Rate resolver for a given set of planned adds: LCAT-resolved $/hr per person
  // per CLIN, blended-rate fallback. Pure so it can score any plan, not just live.
  const makeRate = (addedX) => {
    const m = {};
    for (const c of clins) m[c.id] = { _blended: c.blended_rate || 0 };
    for (const e of employees)
      for (const [cid, cell] of Object.entries(e.cells || {}))
        (m[cid] ||= {})[e.id] = cell.rate ?? null;
    for (const a of addedX || [])
      for (const [cid, rt] of Object.entries(a.rates || {}))
        (m[cid] ||= {})[a.id] = rt;
    return (empId, clinId) => {
      const c = m[clinId] || {};
      return c[empId] ?? c._blended ?? 0;
    };
  };

  // Score a plan state ({draft, added, removed}) into per-CLIN runway + totals —
  // the whole point of the view, and reused to compare saved plans.
  const evalPlan = (state) => {
    const dr = state.draft || {};
    const rost = [
      ...employees.filter((e) => !(state.removed || []).includes(e.id)),
      ...(state.added || []),
    ];
    const rate = makeRate(state.added);
    const clin = {};
    let totalWeekly = 0;
    let totalHrs = 0;
    for (const c of clins) {
      let weekly = 0;
      for (const e of rost)
        weekly += (dr[e.id]?.[c.id] || 0) * rate(e.id, c.id);
      let exhaustWeek = null;
      let runwayDays = null;
      if (weekly > 0) {
        const weeksLeft = c.remaining / weekly;
        exhaustWeek = cw + weeksLeft;
        runwayDays = Math.max(0, Math.round(weeksLeft * 7));
      }
      clin[c.id] = {
        weekly,
        exhaustWeek,
        runwayDays,
        status: weekly > 0 ? simStatus(exhaustWeek, tw) : "paused",
      };
      totalWeekly += weekly;
    }
    for (const e of rost)
      for (const c of clins) totalHrs += dr[e.id]?.[c.id] || 0;
    return { clin, totalWeekly, totalHrs, headcount: rost.length };
  };

  const rateFor = useMemo(() => makeRate(added), [clins, employees, added]);
  const current = useMemo(
    () => evalPlan({ draft, added, removed }),
    [draft, added, removed, employees, clins, cw, tw],
  );
  const sim = current.clin;
  const totalWeekly = current.totalWeekly;
  const totalHrs = current.totalHrs;
  const dirty = useMemo(
    () =>
      (data && JSON.stringify(draft) !== JSON.stringify(buildDraft(data))) ||
      added.length > 0 ||
      removed.length > 0,
    [draft, data, added, removed],
  );

  // Per-person avatar hue keyed to roster order, so a person keeps their color
  // regardless of filtering/sorting.
  const hueOf = useMemo(() => {
    const m = {};
    roster.forEach((e, i) => (m[e.id] = hueFor(i)));
    return (id) => m[id] || hueFor(0);
  }, [roster]);

  const rowWeeklyOf = (e) =>
    clins.reduce(
      (s, c) => s + (draft?.[e.id]?.[c.id] || 0) * rateFor(e.id, c.id),
      0,
    );
  const rowHrsOf = (e) =>
    clins.reduce((s, c) => s + (draft?.[e.id]?.[c.id] || 0), 0);

  // Someone "charges" a CLIN if they logged hours there originally or have any in
  // the current plan — the set the CLIN-card filter narrows the roster to.
  const chargesClin = (e, clinId) =>
    e.cells?.[clinId] != null || (draft?.[e.id]?.[clinId] || 0) > 0;

  // The rows actually rendered: CLIN filter, then name/LCAT search, then sort.
  const visible = useMemo(() => {
    let list = clinFilter
      ? roster.filter((e) => chargesClin(e, clinFilter))
      : roster;
    const q = query.trim().toLowerCase();
    if (q)
      list = list.filter((e) =>
        [e.name, e.id, e.lcat].some((v) => (v || "").toLowerCase().includes(q)),
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
                  ? rowHrsOf(e)
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
  const defaultDir = (key) =>
    key === "name" || key === "lcat" ? "asc" : "desc";
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
        visible.reduce(
          (s, e) => s + (draft?.[e.id]?.[c.id] || 0) * rateFor(e.id, c.id),
          0,
        ),
      ),
    );
    const totalRow = [
      "Forward weekly burn →",
      "",
      "",
      "",
      ...clinTotals,
      clinTotals.reduce((s, x) => s + x, 0),
    ];
    const csv = [header, ...body, [], totalRow]
      .map((r) => r.map(esc).join(","))
      .join("\n");
    const url = URL.createObjectURL(
      new Blob([csv], { type: "text/csv;charset=utf-8;" }),
    );
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
    setNewPerson(null);
    setLoadedPlan(null);
  }

  // Persist the current sim state (grid + adds + removals) under a name.
  async function doSavePlan() {
    const nm = (planName || "").trim() || "Untitled plan";
    try {
      const saved = await savePlan(contractId, nm, { draft, added, removed });
      setPlanName(null);
      setLoadedPlan(saved.id);
      refreshPlans();
    } catch (e) {
      setError(e.message);
    }
  }

  // Reload a saved plan into the simulation.
  function loadPlan(id) {
    const plan = plans.find((p) => p.id === Number(id));
    if (!plan) return;
    const d = plan.data || {};
    setDraft(d.draft || buildDraft(data));
    setAdded(d.added || []);
    setRemoved(d.removed || []);
    // Keep new-add ids from colliding with the reloaded plan's.
    const maxSeq = (d.added || []).reduce((m, a) => {
      const n = parseInt(String(a.id).replace("added-", ""), 10);
      return Number.isFinite(n) ? Math.max(m, n + 1) : m;
    }, addSeq.current);
    addSeq.current = maxSeq;
    setLoadedPlan(plan.id);
  }

  async function deletePlanById(id) {
    try {
      await deletePlan(contractId, id);
      if (loadedPlan === id) setLoadedPlan(null);
      refreshPlans();
    } catch (e) {
      setError(e.message);
    }
  }

  // Any labor CLIN not already finishing on plan. A mid-flight tool should let
  // you course-correct BEFORE a line blows its budget, so this covers hot lines
  // (over / watch / funding-due) and slow ones (under) — not just breaches.
  const OFF_PACE = new Set(["over", "watch", "funding", "under"]);
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
  }

  // Deep-link from a Flight Deck "Apply fix" suggestion: once the grid has
  // loaded, fire applyBalance once, then tell App to clear the flag so a later
  // manual visit isn't auto-rebalanced. balancedRef guards double-fires within a
  // single mount while the flag-clear propagates.
  const balancedRef = useRef(false);
  useEffect(() => {
    if (!autoBalance) {
      balancedRef.current = false;
      return;
    }
    if (!data || !draft || balancedRef.current) return;
    balancedRef.current = true;
    applyBalance();
    onAutoBalanced?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoBalance, data, draft]);

  // The sim state behind a compare slot ("current" or a saved plan id).
  const planStateFor = (sel) =>
    sel === "current"
      ? { draft, added, removed }
      : plans.find((p) => String(p.id) === String(sel))?.data || { draft: {} };
  const cmpLabel = (sel) =>
    sel === "current"
      ? "Current plan"
      : plans.find((p) => String(p.id) === String(sel))?.name || "—";

  // Roll someone off the plan. A planned add just disappears; a synced person is
  // marked removed (excluded from burn) — Reset brings everyone back.
  function removePerson(id) {
    if (String(id).startsWith("added-"))
      setAdded((a) => a.filter((p) => p.id !== id));
    else setRemoved((r) => (r.includes(id) ? r : [...r, id]));
  }

  // Add a planned person to a CLIN at that CLIN's blended rate — models "what if
  // we crew up here?". Lives in the simulation only.
  function addPerson() {
    const clin = newPerson.clin || clins[0]?.id;
    if (!clin) return;
    const hrs = Math.max(0, Math.min(80, +newPerson.hrs || 0));
    const id = `added-${addSeq.current++}`;
    const rate = clins.find((c) => c.id === clin)?.blended_rate || 0;
    setAdded((a) => [
      ...a,
      {
        id,
        name: newPerson.name.trim() || "New hire",
        lcat: "Planned add",
        rates: { [clin]: rate },
      },
    ]);
    setDraft((d) => ({
      ...d,
      [id]: Object.fromEntries(
        clins.map((c) => [c.id, c.id === clin ? hrs : 0]),
      ),
    }));
    setNewPerson(null);
  }

  // Clone a person as a planned add — same LCAT, rates and hours. "Add another
  // like this LCAT" without retyping.
  function duplicatePerson(e) {
    const id = `added-${addSeq.current++}`;
    const rates = {};
    clins.forEach((c) => (rates[c.id] = rateFor(e.id, c.id)));
    setAdded((a) => [
      ...a,
      { id, name: `${e.name} (copy)`, lcat: e.lcat, rates },
    ]);
    setDraft((d) => ({
      ...d,
      [id]: Object.fromEntries(
        clins.map((c) => [c.id, d?.[e.id]?.[c.id] || 0]),
      ),
    }));
  }

  if (error) {
    return (
      <div style={{ padding: 40 }}>
        <div style={{ ...panelStyle, color: "var(--bad)", fontSize: 13 }}>
          {error}
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <div style={{ padding: 40, color: "var(--dim)" }}>
        Loading allocation…
      </div>
    );
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
          <h2
            style={{
              margin: 0,
              fontFamily: grotesk,
              fontSize: 20,
              fontWeight: 600,
              color: "var(--text)",
            }}
          >
            Team allocation matrix
          </h2>
          <div style={{ fontSize: 13, color: "var(--dim)", marginTop: 4 }}>
            Model staffing on <b>{name}</b> ·{" "}
            {data.contract.period ? `${data.contract.period}, ` : ""}
            week {cw} of {tw} · a live what-if — nothing here is saved.
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {dirty && (
            <span
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 11.5,
                fontWeight: 600,
                color: "var(--warn)",
              }}
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: "var(--warn)",
                }}
              />
              Simulating · live, not saved
            </span>
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
            onClick={reset}
            disabled={!dirty}
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
            Reset plan
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
          <div
            style={{
              display: "flex",
              gap: 10,
              flexWrap: "wrap",
              marginBottom: 14,
            }}
          >
            {[
              { label: "Headcount", value: roster.length },
              { label: "FTEs", value: (totalHrs / 40).toFixed(1) },
              {
                label: "Hrs / wk",
                value: Math.round(totalHrs).toLocaleString(),
              },
              { label: "Weekly burn", value: money(totalWeekly) },
            ].map((t) => (
              <div
                key={t.label}
                style={{
                  ...panelStyle,
                  padding: "9px 14px",
                  flex: "1 1 120px",
                  minWidth: 110,
                }}
              >
                <div
                  style={{
                    fontSize: 10.5,
                    letterSpacing: ".07em",
                    textTransform: "uppercase",
                    color: "var(--faint)",
                    fontWeight: 700,
                  }}
                >
                  {t.label}
                </div>
                <div
                  style={{
                    fontFamily: grotesk,
                    fontWeight: 700,
                    fontSize: 20,
                    color: "var(--text)",
                    marginTop: 2,
                  }}
                >
                  {t.value}
                </div>
              </div>
            ))}
          </div>

          {/* single control bar: search + filter status (left) · actions (right) */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              marginBottom: 12,
              flexWrap: "wrap",
            }}
          >
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search people or LCAT…"
              style={{
                height: 34,
                width: 200,
                maxWidth: "100%",
                padding: "0 12px",
                borderRadius: 10,
                border: "1px solid var(--border)",
                background: "var(--inputBg)",
                color: "var(--text)",
                fontSize: 13,
              }}
            />
            <div style={{ fontSize: 12.5, color: "var(--dim)" }}>
              Showing <b style={{ color: "var(--text)" }}>{visible.length}</b>{" "}
              of {roster.length}
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
              <button
                onClick={clearSort}
                title="Return to the default order"
                style={chipBtnDim}
              >
                ✕ Clear sort
              </button>
            )}

            <div
              style={{
                marginLeft: "auto",
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <button
                onClick={() =>
                  setNewPerson({ name: "", clin: clins[0]?.id || "", hrs: 40 })
                }
                title="Add a planned person"
                style={primaryBtn}
              >
                + Add person
              </button>

              {/* Plans menu — save / load / compare / delete, folded into one button */}
              <div style={{ position: "relative" }}>
                <button
                  onClick={() => setPlansMenuOpen((v) => !v)}
                  style={{
                    ...secondaryBtn,
                    borderColor: plansMenuOpen
                      ? "var(--accent)"
                      : "var(--border)",
                  }}
                >
                  Plans ▾
                </button>
                {plansMenuOpen && (
                  <>
                    <div
                      onClick={() => setPlansMenuOpen(false)}
                      style={{ position: "fixed", inset: 0, zIndex: 40 }}
                    />
                    <div
                      style={{
                        position: "absolute",
                        right: 0,
                        top: 40,
                        zIndex: 41,
                        width: 250,
                        ...panelStyle,
                        padding: 8,
                        boxShadow: "0 16px 40px rgba(15,20,35,.24)",
                      }}
                    >
                      {planName == null ? (
                        <button
                          onClick={() => setPlanName("")}
                          style={menuItem}
                        >
                          ＋ Save current plan
                        </button>
                      ) : (
                        <div style={{ display: "flex", gap: 6, padding: 4 }}>
                          <input
                            autoFocus
                            value={planName}
                            placeholder="Plan name…"
                            onChange={(e) => setPlanName(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                doSavePlan();
                                setPlansMenuOpen(false);
                              } else if (e.key === "Escape") setPlanName(null);
                            }}
                            style={{
                              flex: 1,
                              minWidth: 0,
                              height: 30,
                              padding: "0 9px",
                              borderRadius: 8,
                              border: "1px solid var(--accent)",
                              background: "var(--inputBg)",
                              color: "var(--text)",
                              fontSize: 12.5,
                            }}
                          />
                          <button
                            onClick={() => {
                              doSavePlan();
                              setPlansMenuOpen(false);
                            }}
                            style={{
                              height: 30,
                              padding: "0 12px",
                              borderRadius: 8,
                              border: "none",
                              background: "var(--accent)",
                              color: "#fff",
                              fontWeight: 600,
                              fontSize: 12,
                              cursor: "pointer",
                            }}
                          >
                            Save
                          </button>
                        </div>
                      )}
                      {plans.length > 0 && (
                        <>
                          <div style={menuDivider} />
                          <div style={menuLabel}>Saved plans</div>
                          {plans.map((p) => (
                            <div
                              key={p.id}
                              style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 4,
                              }}
                            >
                              <button
                                onClick={() => {
                                  loadPlan(p.id);
                                  setPlansMenuOpen(false);
                                }}
                                style={{
                                  ...menuItem,
                                  flex: 1,
                                  color:
                                    loadedPlan === p.id
                                      ? "var(--accent)"
                                      : "var(--text)",
                                }}
                              >
                                {loadedPlan === p.id ? "✓ " : ""}
                                {p.name}
                              </button>
                              <button
                                onClick={() => deletePlanById(p.id)}
                                title="Delete plan"
                                style={{
                                  width: 26,
                                  height: 26,
                                  borderRadius: 7,
                                  border: "none",
                                  background: "transparent",
                                  color: "var(--faint)",
                                  cursor: "pointer",
                                  fontSize: 14,
                                }}
                              >
                                ×
                              </button>
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
                        title={
                          plans.length ? "" : "Save a plan first to compare"
                        }
                        style={{
                          ...menuItem,
                          color: plans.length
                            ? comparing
                              ? "var(--accent)"
                              : "var(--text)"
                            : "var(--faint)",
                          cursor: plans.length ? "pointer" : "default",
                        }}
                      >
                        ⇄ {comparing ? "Close compare" : "Compare plans"}
                      </button>
                    </div>
                  </>
                )}
              </div>

              <button
                onClick={exportCsv}
                title="Download this view as a CSV"
                style={exportBtn}
              >
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                >
                  <path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z" />
                  <path d="M14 3v5h5" strokeLinejoin="round" />
                </svg>
                Export
              </button>
            </div>
          </div>

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
          {newPerson && (
            <div
              style={{
                ...panelStyle,
                padding: "12px 14px",
                marginBottom: 12,
                display: "flex",
                gap: 10,
                alignItems: "flex-end",
                flexWrap: "wrap",
              }}
            >
              <div>
                <div
                  style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}
                >
                  Name
                </div>
                <input
                  autoFocus
                  value={newPerson.name}
                  placeholder="New hire"
                  onChange={(e) =>
                    setNewPerson((p) => ({ ...p, name: e.target.value }))
                  }
                  onKeyDown={(e) => e.key === "Enter" && addPerson()}
                  style={{
                    height: 34,
                    width: 200,
                    padding: "0 11px",
                    borderRadius: 10,
                    border: "1px solid var(--border)",
                    background: "var(--inputBg)",
                    color: "var(--text)",
                    fontSize: 13,
                  }}
                />
              </div>
              <div>
                <div
                  style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}
                >
                  CLIN
                </div>
                <select
                  value={newPerson.clin}
                  onChange={(e) =>
                    setNewPerson((p) => ({ ...p, clin: e.target.value }))
                  }
                  style={{
                    height: 34,
                    padding: "0 11px",
                    borderRadius: 10,
                    border: "1px solid var(--border)",
                    background: "var(--panel2)",
                    color: "var(--text)",
                    fontSize: 13,
                    cursor: "pointer",
                  }}
                >
                  {clins.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.code} — {c.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <div
                  style={{ fontSize: 11, color: "var(--dim)", marginBottom: 5 }}
                >
                  Hrs / wk
                </div>
                <input
                  type="number"
                  min="0"
                  max="80"
                  value={newPerson.hrs}
                  onChange={(e) =>
                    setNewPerson((p) => ({ ...p, hrs: e.target.value }))
                  }
                  onKeyDown={(e) => e.key === "Enter" && addPerson()}
                  style={{
                    height: 34,
                    width: 90,
                    padding: "0 11px",
                    borderRadius: 10,
                    border: "1px solid var(--border)",
                    background: "var(--inputBg)",
                    color: "var(--text)",
                    fontSize: 13,
                    textAlign: "right",
                    fontFamily: mono,
                  }}
                />
              </div>
              <button
                onClick={addPerson}
                style={{
                  height: 34,
                  padding: "0 16px",
                  borderRadius: 10,
                  border: "none",
                  background: "var(--accent)",
                  color: "#fff",
                  fontWeight: 600,
                  fontSize: 12.5,
                  cursor: "pointer",
                }}
              >
                Add
              </button>
              <button
                onClick={() => setNewPerson(null)}
                style={{
                  height: 34,
                  padding: "0 14px",
                  borderRadius: 10,
                  border: "1px solid var(--border)",
                  background: "var(--panel2)",
                  color: "var(--text)",
                  fontWeight: 600,
                  fontSize: 12.5,
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <div
                style={{
                  fontSize: 11.5,
                  color: "var(--dim)",
                  flexBasis: "100%",
                }}
              >
                Added at{" "}
                {clins.find((c) => c.id === newPerson.clin)?.code || "CLIN"}
                &apos;s blended rate
                {clins.find((c) => c.id === newPerson.clin)?.blended_rate
                  ? ` ($${Math.round(clins.find((c) => c.id === newPerson.clin).blended_rate)}/hr)`
                  : ""}
                . This is a what-if only.
              </div>
            </div>
          )}

          {/* matrix */}
          <div style={{ ...panelStyle, padding: 0, overflow: "hidden" }}>
            <div style={{ overflowX: "auto" }}>
              <table
                style={{
                  width: "100%",
                  borderCollapse: "collapse",
                  fontSize: 13,
                  minWidth: 760,
                }}
              >
                <thead>
                  <tr style={{ background: "var(--panel2)" }}>
                    <th
                      onClick={() => toggleSort("name")}
                      title="Sort by name"
                      style={{
                        ...thSort,
                        position: "sticky",
                        left: 0,
                        background: "var(--panel2)",
                      }}
                    >
                      Employee{sortGlyph("name")}
                    </th>
                    <th
                      onClick={() => toggleSort("lcat")}
                      title="Sort by labor category"
                      style={thSort}
                    >
                      LCAT{sortGlyph("lcat")}
                    </th>
                    <th
                      onClick={() => toggleSort("rate")}
                      title="Sort by rate"
                      style={{ ...thSort, textAlign: "right" }}
                    >
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
                            background: active
                              ? `${hueFor(i)}14`
                              : "var(--panel2)",
                          }}
                        >
                          <span style={{ color: hueFor(i), fontFamily: mono }}>
                            {c.code}
                          </span>
                          {sortGlyph(c.id)}
                          <br />
                          <span
                            style={{
                              fontWeight: 500,
                              textTransform: "none",
                              fontSize: 10.5,
                            }}
                          >
                            hrs/wk
                          </span>
                        </th>
                      );
                    })}
                    <th
                      onClick={() => toggleSort("util")}
                      title="Sort by utilization (hrs vs a 40-hr week)"
                      style={{ ...thSort, textAlign: "center" }}
                    >
                      Util{sortGlyph("util")}
                    </th>
                    <th
                      onClick={() => toggleSort("weekly")}
                      title="Sort by weekly $"
                      style={{ ...thSort, textAlign: "right" }}
                    >
                      Weekly{sortGlyph("weekly")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {visible.length === 0 && (
                    <tr>
                      <td
                        colSpan={clins.length + 5}
                        style={{
                          padding: 26,
                          textAlign: "center",
                          color: "var(--faint)",
                          fontSize: 13,
                        }}
                      >
                        No one matches —{" "}
                        <span
                          onClick={showAll}
                          style={{
                            color: "var(--accent)",
                            cursor: "pointer",
                            fontWeight: 600,
                          }}
                        >
                          show all people
                        </span>
                        .
                      </td>
                    </tr>
                  )}
                  {visible.map((e) => {
                    const rowWeekly = rowWeeklyOf(e);
                    const hue = hueOf(e.id);
                    const tier = tierOf(e.lcat);
                    return (
                      <tr
                        key={e.id}
                        style={{ borderTop: "1px solid var(--border)" }}
                      >
                        <td
                          style={{
                            padding: "10px 16px",
                            position: "sticky",
                            left: 0,
                            background: "var(--panel)",
                          }}
                        >
                          <div
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 10,
                            }}
                          >
                            <span style={avatarStyle(hue)}>
                              {initials(e.name)}
                            </span>
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div
                                style={{
                                  fontWeight: 600,
                                  color: "var(--text)",
                                  display: "flex",
                                  alignItems: "center",
                                  gap: 6,
                                }}
                              >
                                {e.name}
                                {String(e.id).startsWith("added-") && (
                                  <span
                                    style={{
                                      fontSize: 9.5,
                                      fontWeight: 700,
                                      color: "var(--accent)",
                                      background: "var(--panel2)",
                                      padding: "1px 6px",
                                      borderRadius: 5,
                                    }}
                                  >
                                    PLANNED
                                  </span>
                                )}
                              </div>
                              <div
                                style={{
                                  fontSize: 11.5,
                                  color: "var(--dim)",
                                  fontFamily: mono,
                                }}
                              >
                                {String(e.id).startsWith("added-") ? "—" : e.id}
                              </div>
                            </div>
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
                            <div
                              style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 8,
                              }}
                            >
                              <span style={tierPill(tier)}>{tier.label}</span>
                              <span
                                style={{
                                  fontSize: 12,
                                  color: "var(--dim)",
                                  maxWidth: 150,
                                  overflow: "hidden",
                                  textOverflow: "ellipsis",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                {e.lcat}
                              </span>
                            </div>
                          ) : (
                            <span style={{ color: "var(--dim)" }}>—</span>
                          )}
                        </td>
                        <td
                          style={{
                            padding: "10px 12px",
                            textAlign: "right",
                            fontFamily: mono,
                            color: "var(--dim)",
                          }}
                        >
                          {e.rate ? "$" + Math.round(e.rate) : "—"}
                        </td>
                        {clins.map((c, ci) => {
                          const cell = e.cells?.[c.id];
                          const val = draft?.[e.id]?.[c.id] ?? 0;
                          const colHue = hueFor(ci);
                          const flagged = cell?.unmatched;
                          const filled = val > 0;
                          return (
                            <td
                              key={c.id}
                              style={{
                                padding: 8,
                                textAlign: "center",
                                position: "relative",
                                background:
                                  clinFilter === c.id
                                    ? `${colHue}0d`
                                    : undefined,
                              }}
                            >
                              <input
                                type="number"
                                min="0"
                                max="80"
                                step="1"
                                value={val}
                                onChange={(ev) =>
                                  setCell(e.id, c.id, ev.target.value)
                                }
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
                                  color: flagged
                                    ? "var(--bad)"
                                    : filled
                                      ? "var(--text)"
                                      : "var(--faint)",
                                  fontFamily: mono,
                                  fontSize: 13,
                                  fontWeight: filled ? 600 : 400,
                                }}
                              />
                              {flagged && (
                                <span
                                  title={`${cell.lcat}: no matching rate line — billed at the blended rate`}
                                  style={{
                                    position: "absolute",
                                    top: 4,
                                    right: 8,
                                    color: "var(--bad)",
                                    fontSize: 11,
                                    cursor: "help",
                                  }}
                                >
                                  ⚠
                                </span>
                              )}
                            </td>
                          );
                        })}
                        <td
                          style={{ padding: "10px 8px", textAlign: "center" }}
                        >
                          {(() => {
                            const util = rowHrsOf(e) / 40;
                            const uc =
                              util > 1.05
                                ? "var(--warn)"
                                : util >= 0.9
                                  ? "var(--good)"
                                  : "var(--dim)";
                            return (
                              <span
                                style={{
                                  fontFamily: mono,
                                  fontSize: 12.5,
                                  fontWeight: 600,
                                  color: uc,
                                }}
                              >
                                {Math.round(util * 100)}%
                              </span>
                            );
                          })()}
                        </td>
                        <td
                          style={{ padding: "10px 16px", textAlign: "right" }}
                        >
                          <div
                            style={{
                              fontFamily: mono,
                              fontWeight: 600,
                              color: "var(--text)",
                            }}
                          >
                            {money(rowWeekly)}
                          </div>
                          {totalWeekly > 0 && (
                            <div
                              style={{ fontSize: 10.5, color: "var(--faint)" }}
                            >
                              {Math.round((rowWeekly / totalWeekly) * 100)}% of
                              burn
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
                <tfoot>
                  <tr
                    style={{
                      borderTop: "2px solid var(--border)",
                      background: "var(--panel2)",
                    }}
                  >
                    <td
                      colSpan={3}
                      style={{
                        padding: "12px 16px",
                        fontWeight: 700,
                        color: "var(--text)",
                      }}
                    >
                      Forward weekly burn →
                    </td>
                    {clins.map((c) => (
                      <td
                        key={c.id}
                        style={{
                          padding: "12px 8px",
                          textAlign: "center",
                          fontFamily: mono,
                          fontWeight: 600,
                          fontSize: 12,
                          color: statusColor(sim[c.id]?.status),
                        }}
                      >
                        {money(sim[c.id]?.weekly || 0)}
                      </td>
                    ))}
                    <td
                      style={{
                        padding: "12px 8px",
                        textAlign: "center",
                        fontFamily: mono,
                        fontWeight: 600,
                        fontSize: 12,
                        color: "var(--dim)",
                      }}
                    >
                      {roster.length
                        ? Math.round((totalHrs / 40 / roster.length) * 100)
                        : 0}
                      %
                    </td>
                    <td
                      style={{
                        padding: "12px 16px",
                        textAlign: "right",
                        fontFamily: mono,
                        fontWeight: 700,
                        color: "var(--text)",
                      }}
                    >
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
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill,minmax(240px,1fr))",
              gap: 14,
            }}
          >
            {clins.map((c, i) => {
              const s = sim[c.id] || {};
              const p = pill(s.status);
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
                  title={
                    active
                      ? "Showing only this CLIN — click to show everyone"
                      : `Show only people on ${c.code}`
                  }
                  style={{
                    border: `${active ? 2 : 1}px solid ${rc}`,
                    borderRadius: 14,
                    padding: active ? "13px 14px" : "14px 15px",
                    background: p.style.background,
                    cursor: "pointer",
                    boxShadow: active ? `0 0 0 3px ${hueFor(i)}22` : "none",
                  }}
                >
                  <div
                    style={{ display: "flex", alignItems: "center", gap: 8 }}
                  >
                    <span
                      style={{
                        width: 9,
                        height: 9,
                        borderRadius: 3,
                        background: hueFor(i),
                      }}
                    />
                    <span
                      style={{
                        fontFamily: mono,
                        fontSize: 11.5,
                        color: "var(--dim)",
                      }}
                    >
                      {c.code}
                    </span>
                    <span style={p.style}>{p.label}</span>
                    {active && (
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          letterSpacing: ".06em",
                          color: hueFor(i),
                        }}
                      >
                        FILTERING
                      </span>
                    )}
                  </div>
                  <div
                    style={{
                      fontSize: 12.5,
                      color: "var(--text)",
                      fontWeight: 600,
                      marginTop: 8,
                    }}
                  >
                    {c.name}
                  </div>
                  <div style={row}>
                    {/* Against c.remaining (budget − spent), and budget is the
                        funded slice on an incrementally funded CLIN — so name the
                        funds, not the ceiling. */}
                    <span>Projected funds exhaustion</span>
                    <span style={{ fontWeight: 600, color: rc }}>
                      {s.exhaustWeek == null
                        ? "—"
                        : `Week ${Math.round(s.exhaustWeek)} / ${tw}`}
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
                      <span
                        style={{
                          fontWeight: 600,
                          color: delta > 0 ? "var(--good)" : "var(--bad)",
                        }}
                      >
                        {delta > 0 ? `+${delta}` : delta} wk
                      </span>
                    </div>
                  )}
                  {!!c.unmatched_lcats?.length && (
                    <div
                      style={{
                        marginTop: 10,
                        fontSize: 11,
                        color: "var(--warn)",
                      }}
                    >
                      ⚠ Unmatched LCAT: {c.unmatched_lcats.join(", ")} — billed
                      at blended
                      {c.blended_rate
                        ? ` $${Math.round(c.blended_rate)}/hr`
                        : ""}
                      .
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

// Side-by-side comparison of two plan states (Current or a saved plan), scored
// through the same evalPlan the live view uses.
function ComparePanel({
  a,
  b,
  setA,
  setB,
  plans,
  clins,
  tw,
  cmpLabel,
  evalPlan,
  planStateFor,
  onClose,
}) {
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
  const options = [
    { value: "current", label: "Current plan" },
    ...plans.map((p) => ({ value: String(p.id), label: p.name })),
  ];

  // rows: { label, av, bv, dir (1 higher-better, -1 lower-better, 0 neutral), kind }
  const rows = [
    {
      label: "Headcount",
      av: A.headcount,
      bv: B.headcount,
      dir: 0,
      kind: "num",
    },
    {
      label: "FTEs",
      av: A.totalHrs / 40,
      bv: B.totalHrs / 40,
      dir: 0,
      kind: "fte",
    },
    {
      label: "Forward weekly burn",
      av: A.totalWeekly,
      bv: B.totalWeekly,
      dir: -1,
      kind: "money",
    },
    ...clins.map((c) => ({
      label: `${c.code} runway`,
      av: A.clin[c.id]?.runwayDays,
      bv: B.clin[c.id]?.runwayDays,
      dir: 1,
      kind: "days",
    })),
  ];

  const fmt = (v, kind) =>
    v == null
      ? "—"
      : kind === "money"
        ? money(v)
        : kind === "fte"
          ? v.toFixed(1)
          : kind === "days"
            ? `${v}d`
            : v;
  const delta = (av, bv, dir, kind) => {
    if (av == null || bv == null)
      return <span style={{ color: "var(--faint)" }}>—</span>;
    const d = bv - av;
    const color =
      d === 0 || dir === 0
        ? "var(--dim)"
        : dir > 0 === d > 0
          ? "var(--good)"
          : "var(--bad)";
    const mag =
      kind === "money"
        ? money(Math.abs(d))
        : kind === "fte"
          ? Math.abs(d).toFixed(1)
          : `${Math.abs(d)}${kind === "days" ? "d" : ""}`;
    return (
      <span style={{ color, fontWeight: 600 }}>
        {d === 0 ? "—" : `${d > 0 ? "+" : "−"}${mag}`}
      </span>
    );
  };

  const cell = { padding: "9px 14px", fontFamily: mono, fontSize: 13 };
  return (
    <div
      style={{
        ...panelStyle,
        padding: 0,
        overflow: "hidden",
        marginBottom: 12,
      }}
    >
      <div
        style={{
          display: "flex",
          gap: 10,
          alignItems: "center",
          padding: "12px 14px",
          flexWrap: "wrap",
        }}
      >
        <span style={{ fontSize: 12.5, color: "var(--dim)" }}>Compare</span>
        <select value={a} onChange={(e) => setA(e.target.value)} style={sel}>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <span style={{ color: "var(--faint)" }}>vs</span>
        <select value={b} onChange={(e) => setB(e.target.value)} style={sel}>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
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
      <table
        style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}
      >
        <thead>
          <tr
            style={{
              background: "var(--panel2)",
              color: "var(--faint)",
              fontSize: 11,
              textTransform: "uppercase",
              letterSpacing: ".05em",
            }}
          >
            <th
              style={{
                textAlign: "left",
                padding: "10px 14px",
                fontWeight: 700,
              }}
            >
              Metric
            </th>
            <th
              style={{
                textAlign: "right",
                padding: "10px 14px",
                fontWeight: 700,
                maxWidth: 160,
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {cmpLabel(a)}
            </th>
            <th
              style={{
                textAlign: "right",
                padding: "10px 14px",
                fontWeight: 700,
              }}
            >
              {cmpLabel(b)}
            </th>
            <th
              style={{
                textAlign: "right",
                padding: "10px 14px",
                fontWeight: 700,
              }}
            >
              Δ
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} style={{ borderTop: "1px solid var(--border)" }}>
              <td
                style={{
                  padding: "9px 14px",
                  color: "var(--text)",
                  fontWeight: 500,
                }}
              >
                {r.label}
              </td>
              <td style={{ ...cell, textAlign: "right", color: "var(--dim)" }}>
                {fmt(r.av, r.kind)}
              </td>
              <td
                style={{
                  ...cell,
                  textAlign: "right",
                  color: "var(--text)",
                  fontWeight: 600,
                }}
              >
                {fmt(r.bv, r.kind)}
              </td>
              <td style={{ ...cell, textAlign: "right" }}>
                {delta(r.av, r.bv, r.dir, r.kind)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Snapshot the synced actuals into an editable {emp: {clin: hrs}} grid.
function buildDraft(d) {
  const draft = {};
  for (const e of d.employees || []) {
    draft[e.id] = {};
    for (const c of d.clins || [])
      draft[e.id][c.id] = e.cells?.[c.id]?.hours || 0;
  }
  return draft;
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
const thSort = {
  ...th,
  cursor: "pointer",
  userSelect: "none",
  whiteSpace: "nowrap",
};

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
const chipBtnDim = {
  ...chipBtn,
  border: "1px solid var(--border)",
  color: "var(--dim)",
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
const menuDivider = {
  height: 1,
  background: "var(--border)",
  margin: "6px 4px",
};
const row = {
  display: "flex",
  justifyContent: "space-between",
  marginTop: 8,
  fontSize: 12,
  color: "var(--dim)",
};
