# Browser Navigation History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Runway view navigation URL-backed, deep-linkable, and compatible with browser Back/Forward.

**Architecture:** Add a pure route helper for supported paths and a small History API adapter that owns push/replace/popstate semantics. App hydrates its existing `view` and `activeId` state from the adapter and passes URL-aware callbacks to existing components.

**Tech Stack:** React 18, Vite, browser History API, Node built-in test runner.

## Global Constraints

- Do not add `react-router-dom`.
- Preserve existing view names and component APIs where possible.
- Invalid routes replace to `/portfolio` and show a dismissible explanation.
- Do not put transient UI state in the URL.

---

### Task 1: Add pure route parsing and formatting

**Files:**
- Create: `web/src/navigation.js`
- Test: `web/src/navigation.test.js`

**Interfaces:**
- Produces `parseLocation(locationLike)`, `pathFor(view, activeId)`, and `routeForPath(pathname)` for App and tests.

- [ ] **Step 1: Write the failing tests**

```js
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
    assert.deepEqual(routeForPath(pathFor(route[0], route[1])), {
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
```

- [ ] **Step 2: Run the route test and verify it fails**

Run: `cd web && node --test src/navigation.test.js`
Expected: FAIL because `./navigation.js` does not exist.

- [ ] **Step 3: Implement the minimal route helper**

Implement an explicit view map, integer contract-ID parsing, global paths, contract paths, and an invalid result that carries `{ view: "portfolio", activeId: null, invalid: true }`.

- [ ] **Step 4: Run the route test and verify it passes**

Run: `cd web && node --test src/navigation.test.js`
Expected: PASS.

### Task 2: Add History API adapter tests and implementation

**Files:**
- Modify: `web/src/navigation.js`
- Test: `web/src/navigation.test.js`

**Interfaces:**
- Produces `createHistoryAdapter({ history, location, onChange })` with `navigate(route, mode)`, `start()`, and `stop()`.

- [ ] **Step 1: Write the failing tests**

```js
test("navigate pushes a path and popstate hydrates without pushing", () => {
  const calls = [];
  const listeners = new Map();
  const history = {
    pushState: (_state, _title, path) => calls.push(["push", path]),
    replaceState: (_state, _title, path) => calls.push(["replace", path]),
  };
  const location = { pathname: "/portfolio" };
  const adapter = createHistoryAdapter({ history, location, onChange: (route) => calls.push(["change", route]) });
  adapter.start((handler) => listeners.set("popstate", handler), (handler) => listeners.delete("popstate"));
  adapter.navigate("flightdeck", 42);
  assert.deepEqual(calls[0], ["push", "/contract/42/flight-deck"]);
  location.pathname = "/portfolio";
  listeners.get("popstate")();
  assert.equal(calls.filter(([kind]) => kind === "push").length, 1);
  assert.deepEqual(calls.at(-1)[0], "change");
});
```

- [ ] **Step 2: Run the adapter test and verify it fails**

Run: `cd web && node --test src/navigation.test.js`
Expected: FAIL because `createHistoryAdapter` is not exported.

- [ ] **Step 3: Implement adapter methods**

Use `history.pushState` for normal navigation, `history.replaceState` for invalid-route normalization, and the supplied listener registration functions in tests while using `window.addEventListener`/`removeEventListener` in the browser.

- [ ] **Step 4: Run route and adapter tests**

Run: `cd web && node --test src/navigation.test.js`
Expected: PASS.

### Task 3: Integrate URL/history state into App and navigation callbacks

**Files:**
- Modify: `web/src/App.jsx`
- Modify: `web/src/components/Sidebar.jsx` only if callback naming requires it

**Interfaces:**
- App owns `navigate`, `replaceRoute`, and URL hydration; child components continue receiving callbacks with their existing purposes.

- [ ] **Step 1: Add an integration regression test fixture**

Extend the adapter tests to assert invalid-route replacement emits the portfolio route and a notice signal, and that active-contract selection can replace an unscoped route without creating a push entry.

- [ ] **Step 2: Run the integration test and verify it fails**

Run: `cd web && node --test src/navigation.test.js`
Expected: FAIL against the unimplemented adapter integration behavior.

- [ ] **Step 3: Hydrate App state from the current location**

Initialize `{ view, activeId }` from `parseLocation(window.location)`, subscribe to `popstate` in an effect, and normalize invalid routes with `replaceState`. Keep the invalid-route message in local transient state with a close action.

- [ ] **Step 4: Route all user navigation through App**

Replace raw `setView` calls in App callbacks with a navigation function. Pass a URL-aware active-ID setter to views so contract selection updates the route via replace when it is a passive data load and push only when the user explicitly opens a contract. Preserve pending balance/person/doc state while changing views.

- [ ] **Step 5: Render the dismissible invalid-route notice**

Render a fixed, accessible alert near the app shell with text explaining the fallback and a button to dismiss it.

- [ ] **Step 6: Run the web build and focused tests**

Run: `cd web && node --test src/navigation.test.js && npm run build`
Expected: PASS and a successful Vite production build.

### Task 4: Full verification and branch handoff

**Files:**
- Modify: none beyond implementation files above

- [ ] **Step 1: Run the complete web test suite**

Run: `cd web && npm test`
Expected: all tests pass with exit code 0.

- [ ] **Step 2: Run the production build again**

Run: `cd web && npm run build`
Expected: exit code 0.

- [ ] **Step 3: Inspect the diff and branch state**

Run: `git diff --check && git status --short --branch && git diff main...HEAD --stat`
Expected: only the navigation implementation, tests, and approved docs are included; pre-existing untracked files remain unstaged.

- [ ] **Step 4: Commit implementation**

```bash
git add web/src/navigation.js web/src/navigation.test.js web/src/App.jsx web/src/components/Sidebar.jsx docs/superpowers/plans/2026-08-07-nav-history.md
git commit -m "feat(nav): sync app views with browser history"
```
