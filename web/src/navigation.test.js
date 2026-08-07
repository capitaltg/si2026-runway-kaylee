import assert from "node:assert/strict";
import test from "node:test";
import { createHistoryAdapter, parseLocation, pathFor, routeForPath } from "./navigation.js";

test("supported global and contract routes round-trip", () => {
  for (const route of [
    ["portfolio", null, "/portfolio"],
    ["people", null, "/people"],
    ["ingest", null, "/ingest"],
    ["flightdeck", null, "/flight-deck"],
    ["flightdeck", 42, "/contract/42/flight-deck"],
    ["allocate", 42, "/contract/42/allocate"],
    ["expenses", 42, "/contract/42/expenses"],
    ["funding", 42, "/contract/42/funding"],
    ["rates", 42, "/contract/42/rates"],
    ["drafts", 42, "/contract/42/drafts"],
  ]) {
    assert.equal(pathFor(route[0], route[1]), route[2]);
    assert.deepEqual(routeForPath(route[2]), {
      view: route[0],
      activeId: route[1],
      invalid: false,
    });
  }
});

test("malformed and unknown paths are invalid", () => {
  assert.equal(routeForPath("/not-a-view").invalid, true);
  assert.equal(routeForPath("/contract/nope/flight-deck").invalid, true);
  assert.equal(routeForPath("/contract/42/nope").invalid, true);
});

test("empty pathname normalizes to portfolio", () => {
  assert.deepEqual(parseLocation({ pathname: "" }), {
    view: "portfolio",
    activeId: null,
    invalid: false,
  });
});

test("navigate pushes a path and popstate hydrates without pushing", () => {
  const calls = [];
  const listeners = new Map();
  const history = {
    pushState: (_state, _title, path) => calls.push(["push", path]),
    replaceState: (_state, _title, path) => calls.push(["replace", path]),
  };
  const location = { pathname: "/portfolio" };
  const adapter = createHistoryAdapter({
    history,
    location,
    onChange: (route) => calls.push(["change", route]),
    addEventListener: (name, handler) => listeners.set(name, handler),
    removeEventListener: (name) => listeners.delete(name),
  });
  adapter.start();
  adapter.navigate("flightdeck", 42);
  assert.deepEqual(calls[0], ["push", "/contract/42/flight-deck"]);
  location.pathname = "/portfolio";
  listeners.get("popstate")();
  assert.equal(calls.filter(([kind]) => kind === "push").length, 1);
  assert.deepEqual(calls.at(-1)[0], "change");
  adapter.stop();
  assert.equal(listeners.has("popstate"), false);
});
