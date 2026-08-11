// Formatting + status→token helpers shared by the burn dashboard views.
// Ported from the Runway design system (docs/design/Runway.dc.html): money/
// moneyM number formats, the status pill palette, and the per-CLIN hue set.
// Colors are emitted as CSS var references so they stay theme-reactive.

export const money = (n) => "$" + Math.round(n || 0).toLocaleString("en-US");
export const moneyM = (n) => "$" + ((n || 0) / 1e6).toFixed(2) + "M";
export const pct = (frac) => Math.round((frac || 0) * 100) + "%";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// "2026-03-14" → "14 Mar 26", matching the date format the top bar already uses.
// Regex-parsed rather than through `new Date()` on purpose: `new Date("2026-03-14")`
// is parsed as UTC midnight and then rendered in local time, so west of Greenwich
// every date in the app would render one day early. The year is kept because a
// hard stop (#23) can land in the next calendar year, where a bare "14 Mar" is
// ambiguous. Falls back to the raw string if it won't parse.
export function shortDate(s) {
  if (!s) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if (!m) return s;
  return `${m[3]} ${MONTHS[+m[2] - 1] || m[2]} ${m[1].slice(2)}`;
}

// The hard-stop forecast (#23) as a phrase. `passed` means the binding money is
// already gone, so the date is behind us and the honest reading is that charging
// should stop now — naming a past date as though it were a deadline reads as though
// there were still time. `reason` names which limit produces the date, matching the
// engine's `stop_reason` / `limited_by`.
export function stopPhrase(stopDate, reason, passed) {
  if (!stopDate) return null;
  if (passed) return `Charging stops today (funds out ${shortDate(stopDate)})`;
  return reason === "funding"
    ? `Charging stops ~${shortDate(stopDate)} without a mod`
    : `Charging stops ~${shortDate(stopDate)} at ceiling`;
}

// How stale a sync has to be before the "as of" label also says how old it is.
// Weekly timekeeping means a healthy contract is always a few days behind, and
// printing "· 6 days ago" on every card would train people to ignore the one that
// says 119. Two weeks is past any normal timesheet lag.
const STALE_DAYS = 14;

// The vantage point a runway figure is measured from (`sync.as_of` / `data_age_days`).
//
// Every forward number the engine produces — `runway_days`, `exhaust_week`,
// `stop_date` — is anchored to the newest synced timesheet week, not to today,
// because pace can only be measured from hours that have actually been reported.
// That is the right denominator and it is deliberately not being changed: a burn rate
// invented for the weeks nobody has filed yet would be a guess wearing an actual's
// clothes. But it makes those figures *as-of* readings rather than live countdowns —
// they move when a sync lands, not when the clock ticks — and a reader has no way to
// know that from the number alone. Contract 5 rendered "99 days of runway" measured
// from a week four months gone, which is why the count appeared frozen.
//
// So the number keeps its meaning and gains its date. Inside a normal sync lag that
// is just a quiet "as of 10 Apr"; past `STALE_DAYS` it says how far behind it is,
// because at that point the staleness is the more useful fact.
export function asOfLabel(sync) {
  if (!sync?.as_of) return null;
  const age = sync.data_age_days;
  const stale = age != null && age >= STALE_DAYS;
  return `as of ${shortDate(sync.as_of)}${stale ? ` · ${age} days ago` : ""}`;
}

// Per-CLIN accent hues, assigned by index (design's avPal).
const HUES = ["#4361ee", "#06b6d4", "#7c5cff", "#ef8f2a", "#10b981", "#f05252"];
export const hueFor = (i) => HUES[i % HUES.length];

// status → { label, color var, background var }. Mirrors design's pill().
const PILL = {
  // Label picked per-call by pill()'s ceilingBreached — see there.
  over: { label: "Over ceiling", color: "--bad", bg: "--badBg" },
  // The funded slice runs short but the ceiling holds and funding is either
  // keeping pace or has a mod outstanding (burn.py's #22 downgrade). Routine
  // incremental funding, so it's amber with the rest of the funding states — the
  // backend has emitted this since #22 but PILL had no entry, so it rendered "—".
  funding: { label: "Funding due", color: "--warn", bg: "--warnBg" },
  watch: { label: "Watch", color: "--warn", bg: "--warnBg" },
  // Cost past estimated cost, with the fee absorbing it (#81). Amber, beside the
  // funding states rather than among the reds: it is money the company loses, not a
  // funding limit, and every red on a cost-type CLIN names a funding limit. `pill()`
  // sharpens the label to "Fee exhausted" when there is no fee left to erode.
  fee_eroding: { label: "Fee eroding", color: "--warn", bg: "--warnBg" },
  ok: { label: "On pace", color: "--good", bg: "--goodBg" },
  under: { label: "Under pace", color: "--warn", bg: "--warnBg" },
  paused: { label: "Paused", color: "--faint", bg: "--panel2" },
  // Charged rows the engine couldn't price (#40) — a data gap, flagged red because
  // it masks the burn rather than reporting one.
  unpriced: { label: "Unpriced", color: "--bad", bg: "--badBg" },
  tracked: { label: "Tracked", color: "--dim", bg: "--panel2" },
};

// The same ladder in fixed-price vocabulary (#79). A fixed-price CLIN has no funding
// limit to breach, so `over` means the fee is gone and `watch` means cost is projected
// to eat it. `under` is absent because the engine never emits it here — spending less
// than a firm price is margin earned, not a signal to chase.
const MARGIN_PILL = {
  over: { label: "Margin exceeded", color: "--bad", bg: "--badBg" },
  watch: { label: "Margin at risk", color: "--warn", bg: "--warnBg" },
  ok: { label: "On pace", color: "--good", bg: "--goodBg" },
  paused: { label: "Paused", color: "--faint", bg: "--panel2" },
  unpriced: { label: "Unpriced", color: "--bad", bg: "--badBg" },
};

const pillStyle = (p) => ({
  fontSize: 10.5,
  fontWeight: 700,
  padding: "2px 9px",
  borderRadius: 20,
  color: `var(${p.color})`,
  background: `var(${p.bg})`,
  marginLeft: "auto",
  whiteSpace: "nowrap",
});

// `ceilingBreached` names the limit a red `over` is about, matching burn.py's
// _pill: projected spend blowing the real ceiling is a ceiling problem, while the
// ceiling holding means the funded slice ran short with funding lagging behind.
// `fundsExceeded` outranks both — those are forecasts, this one already happened,
// so it takes the past tense.
// Defaults to the ceiling wording, which is always right for a CLIN that isn't
// incrementally funded (its budget *is* the ceiling) and is what callers with no
// funded-slice notion — expenses — should keep saying.
// `marginManaged` switches to fixed-price wording (#79), mirroring burn.py's _pill.
// All three labels above name a funding limit and fixed-price work has none — its red
// means cost is projected past the price and the fee is gone. Same statuses, different
// vocabulary, so the pill can never tell an FFP reader their funding ran out.
// `feeExhausted` sharpens the amber `fee_eroding` label the same way (#81): the state
// is the same, but "Fee eroding" on a CLIN with none of its fee left understates it by
// exactly the amount that matters. Read it off the card's `fee_exhausted`, never by
// string-matching the label.
export function pill(
  status,
  ceilingBreached = true,
  fundsExceeded = false,
  marginManaged = false,
  feeExhausted = false,
) {
  if (marginManaged) {
    const m = MARGIN_PILL[status] || { label: "—", color: "--dim", bg: "--panel2" };
    return { label: m.label, color: `var(${m.color})`, style: pillStyle(m) };
  }
  const p = PILL[status] || { label: "—", color: "--dim", bg: "--panel2" };
  const overLabel = fundsExceeded
    ? "Funds exceeded"
    : ceilingBreached
      ? p.label
      : "Funds short";
  return {
    label:
      status === "over"
        ? overLabel
        : status === "fee_eroding" && feeExhausted
          ? "Fee exhausted"
          : p.label,
    color: `var(${p.color})`,
    style: pillStyle(p),
  };
}

// status → the accent color a runway/exhaustion figure should take.
export const statusColor = (status) =>
  status === "over" || status === "unpriced"
    ? "var(--bad)"
    : status === "watch" ||
        status === "under" ||
        status === "funding" ||
        status === "fee_eroding"
      ? "var(--warn)"
      : "var(--good)";

export const panelStyle = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 16,
  padding: 18,
  boxShadow: "0 1px 2px rgba(26,34,51,.04),0 10px 26px rgba(26,34,51,.05)",
};
