export function scopeNotices(contract = {}) {
  const notices = [];
  if (contract.clin_scope === "all") {
    notices.push({
      key: "clin_scope",
      text: "CLIN totals include all contract periods because the award did not label the active period.",
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
