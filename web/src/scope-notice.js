export function scopeNotices(contract = {}) {
  const notices = [];
  for (const missing of contract.missing_option_mods ?? []) {
    const period = missing.period;
    if (!period) continue;
    notices.push({
      key: `missing_option_mod:${period}`,
      text: `${period} performance detected on timesheets, but the ${period} SF-30 funding modification has not been ingested.`,
    });
  }
  if (contract.clin_scope === "all") {
    notices.push({
      key: "clin_scope",
      text: "CLIN totals include all contract periods because the award did not label the active period.",
    });
  }
  if (contract.funding_total_unknown) {
    notices.push({
      key: "funding_total",
      text: "Funded-dollar limits could not be set for this period: some CLINs state their own obligation but the documents print no contract obligated total to scope them against. Runway is reading against ceilings.",
    });
  }
  // A CLIN printed its own pricing type, Runway could not read it, and the award
  // header rescued the resolution (#184). The figures are a real typed read and stay
  // as they are — what the reader is owed is the fact that the more specific field
  // was rejected, so a mixed award is not silently calculated on its broad header
  // type. Named per CLIN because that is the granularity of the fix.
  for (const rejected of contract.pricing_rejected ?? []) {
    if (!rejected?.clin || !rejected.rejected) continue;
    notices.push({
      key: `pricing_rejected:${rejected.clin}`,
      text: `${rejected.clin} prints a pricing type Runway cannot read ("${rejected.rejected}"), so its figures use the award header's ${rejected.policy_label} policy instead.`,
    });
  }
  if (contract.pop_scoped === false) {
    notices.push({
      key: "pop_scope",
      text: "Charges could not be limited to the active period of performance because no synced week overlaps it.",
    });
  }
  return notices;
}
