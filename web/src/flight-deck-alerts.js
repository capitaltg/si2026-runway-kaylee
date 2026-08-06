const ALERT_GROUPS = [
  { source: "dataQuality", kind: "data-quality", risk: (item) => item.charged_rows ?? 0 },
  {
    source: "tripwires",
    kind: "tripwire",
    tier: (item) => (isRealizedTripwire(item) ? 1 : 0),
    risk: (item) =>
      isRealizedTripwire(item) ? tripwireOverage(item) : -(item.runway_days ?? Infinity),
  },
  { source: "funding", kind: "funding", risk: (item) => -(item.runway_days ?? Infinity) },
  { source: "underburn", kind: "underburn", risk: (item) => item.projected_unspent ?? 0 },
  { source: "marginAlerts", kind: "margin", risk: (item) => -(item.projected_margin ?? Infinity) },
  { source: "notices", kind: "scope", grouped: true, risk: () => 0 },
  { source: "rateGaps", kind: "rate-gap", risk: (item) => item.lcats?.length ?? 0 },
  {
    source: "lcatGaps",
    kind: "lcat-gap",
    grouped: true,
    risk: (items) => items.reduce((total, item) => total + (item.issues?.length ?? 0), 0),
  },
];

function tripwireConstraint(item) {
  return item.limited_by === "funding"
    ? (item.funded ?? item.budget)
    : (item.ceiling ?? item.budget);
}

function tripwireBurn(item) {
  return item.limited_by === "funding"
    ? (item.pct_budget ?? item.pct ?? 0)
    : (item.pct ?? 0);
}

function isRealizedTripwire(item) {
  const constraint = tripwireConstraint(item);
  return (
    tripwireBurn(item) >= 1 ||
    item.stop_date_passed === true ||
    (constraint === 0 && (item.spent ?? item.pct ?? 0) > 0)
  );
}

function tripwireOverage(item) {
  const constraint = tripwireConstraint(item);
  if (item.spent != null && constraint != null) return item.spent - constraint;
  if (constraint != null) return (tripwireBurn(item) - 1) * constraint;
  return tripwireBurn(item) - 1;
}

export function nextAlertIndex(index, length, direction) {
  return length < 2 ? 0 : (index + direction + length) % length;
}

export function clampAlertIndex(index, length) {
  return Math.min(index, Math.max(length - 1, 0));
}

export function orderedFlightDeckAlerts(groups) {
  return ALERT_GROUPS.flatMap(({ source, kind, grouped, tier, risk }, priority) => {
    const items = groups[source] ?? [];
    const descriptors = grouped && items.length > 0 ? [items] : grouped ? [] : items;

    return descriptors.map((item, index) => ({
      kind,
      key: `${kind}:${item.key ?? item.code ?? index}`,
      item,
      priority,
      tier: tier?.(item) ?? 0,
      risk: risk(item),
      index,
    }));
  })
    .sort(
      (a, b) =>
        a.priority - b.priority || b.tier - a.tier || b.risk - a.risk || a.index - b.index,
    )
    .map(({ kind, key, item }) => ({ kind, key, item }));
}
