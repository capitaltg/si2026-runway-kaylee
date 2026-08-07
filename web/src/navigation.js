const GLOBAL_PATHS = {
  portfolio: "/portfolio",
  people: "/people",
  ingest: "/ingest",
};

const CONTRACT_SEGMENTS = {
  flightdeck: "flight-deck",
  allocate: "allocate",
  expenses: "expenses",
  funding: "funding",
  rates: "rates",
  drafts: "drafts",
};

const UNSCOPED_CONTRACT_PATHS = Object.fromEntries(
  Object.entries(CONTRACT_SEGMENTS).map(([view, segment]) => [view, `/${segment}`]),
);

const VIEW_BY_SEGMENT = Object.fromEntries(
  Object.entries(CONTRACT_SEGMENTS).map(([view, segment]) => [segment, view]),
);

export function pathFor(view, activeId) {
  if (GLOBAL_PATHS[view]) return GLOBAL_PATHS[view];
  const segment = CONTRACT_SEGMENTS[view];
  if (segment && activeId != null && Number.isInteger(Number(activeId)) && Number(activeId) >= 0) {
    return `/contract/${Number(activeId)}/${segment}`;
  }
  return UNSCOPED_CONTRACT_PATHS[view] || "/portfolio";
}

export function routeForPath(pathname) {
  const path = pathname || "/portfolio";
  const globalView = Object.entries(GLOBAL_PATHS).find(([, value]) => value === path)?.[0];
  if (globalView) return { view: globalView, activeId: null, invalid: false };

  const unscopedView = Object.entries(UNSCOPED_CONTRACT_PATHS).find(([, value]) => value === path)?.[0];
  if (unscopedView) return { view: unscopedView, activeId: null, invalid: false };

  const match = path.match(/^\/contract\/([^/]+)\/([^/]+)$/);
  if (match) {
    const id = Number(match[1]);
    const view = VIEW_BY_SEGMENT[match[2]];
    if (view && Number.isInteger(id) && id >= 0) {
      return { view, activeId: id, invalid: false };
    }
  }

  return { view: "portfolio", activeId: null, invalid: true };
}

export function parseLocation(locationLike) {
  return routeForPath(locationLike?.pathname || "/portfolio");
}

export function createHistoryAdapter({
  history = globalThis.window?.history,
  location = globalThis.window?.location,
  onChange = () => {},
  addEventListener = globalThis.window?.addEventListener?.bind(globalThis.window),
  removeEventListener = globalThis.window?.removeEventListener?.bind(globalThis.window),
} = {}) {
  if (!history || !location) throw new Error("History adapter requires history and location");
  const handlePopState = () => onChange(parseLocation(location));

  return {
    start() {
      addEventListener?.("popstate", handlePopState);
    },
    stop() {
      removeEventListener?.("popstate", handlePopState);
    },
    navigate(view, activeId, { replace = false } = {}) {
      const path = pathFor(view, activeId);
      history[replace ? "replaceState" : "pushState"]({}, "", path);
      onChange(routeForPath(path));
    },
  };
}
