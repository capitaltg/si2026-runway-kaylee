# Browser Navigation Design

## Goal

Make Runway views and selected contracts deep-linkable and make browser Back/Forward navigate between app screens.

## Architecture

Use a dependency-free History API adapter owned by `App.jsx`. A pure route helper converts between URL paths and the existing `{ view, activeId }` state. User-driven navigation calls `history.pushState`; initial load and `popstate` hydrate React state without pushing a second entry.

The route table is:

| Path | View | Contract ID |
| --- | --- | --- |
| `/portfolio` | `portfolio` | none |
| `/people` | `people` | none |
| `/ingest` | `ingest` | none |
| `/contract/:id/flight-deck` | `flightdeck` | required |
| `/contract/:id/allocate` | `allocate` | required |
| `/contract/:id/expenses` | `expenses` | required |
| `/contract/:id/funding` | `funding` | required |
| `/contract/:id/rates` | `rates` | required |
| `/contract/:id/drafts` | `drafts` | required |

When a contract-scoped navigation is requested without an active ID, the existing view may still resolve its first contract; the URL remains the unscoped view path until an ID is selected. Once a child view selects an ID, App replaces the route with the contract-scoped path so refresh and sharing are safe.

## Invalid routes

Unknown paths and malformed contract routes fall back with `history.replaceState` to `/portfolio`. App renders a dismissible notice explaining that the requested page could not be opened and that the user was returned to Portfolio. Invalid URLs never create an extra Back entry.

## Navigation data flow

1. `parseLocation(location)` produces a route state and an invalid flag.
2. App initializes from `window.location`.
3. `navigate(view, activeId)` updates React state and pushes the matching path.
4. `popstate` calls the parser and updates React state without pushing.
5. Existing Sidebar, Portfolio, Ingest, Flight Deck, and contract-scoped callbacks use App navigation callbacks rather than raw setters.
6. Deleting the active contract clears the selection and replaces the current URL with `/portfolio`.

## Testing

Add Node tests for the pure route helper: every supported route round-trips, IDs are validated, and malformed/unknown paths are marked invalid. Add App-facing history tests through the adapter boundary to verify push navigation, popstate hydration, replacement for invalid routes, and no duplicate history entries during Back/Forward.

## Scope constraints

- Do not add `react-router-dom`.
- Preserve existing view names and component APIs where possible.
- Preserve current default behavior for an empty URL by normalizing it to `/portfolio`.
- Do not include transient UI state such as Ask Runway, selected expense CLIN, pending draft type, or theme in the URL.
