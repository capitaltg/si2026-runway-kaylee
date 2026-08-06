# Flight Deck Alert Carousel Design

## Goal

Replace the vertically stacked Flight Deck alert banners with one click-through
banner. The selected alert retains its existing visual treatment, copy, and
actions; only the container and navigation change.

## Alert model and order

`FlightDeck` will normalize the existing alert sources into display entries and
sort them before rendering. Severity is ordered as follows:

1. Data-quality and unpriced notices
2. Over-ceiling or exhausted-funding tripwires
3. Funding-due alerts
4. Under-burn alerts
5. Margin alerts, retaining their current red/amber treatment

Entries in the same category are sorted by their existing risk signal, with the
most severe first (for example, highest overspend or shortest runway). The
pager never drops an alert: it shows the title and CLIN followed by its position
in the ordered list, such as `Funding due — CLIN 0004 · 2 of 4`.

## Interaction and presentation

One card is rendered at a time in the existing alert location. Small previous
and next controls in the banner corner move through the ordered list and wrap
from the end to the start. They have accessible labels and disabled styling only
when there is a single alert. No existing alert copy, action button, colors, or
card body content is rewritten.

The component resets its selected index safely if the contract or alert list
changes. When no alert source yields an entry, the current green all-clear state
continues to render unchanged.

## Verification

Unit tests will cover severity ordering, within-category severity ordering,
all-clear behavior, and pager index wrapping/clamping. The web test suite and
production build will be run after implementation.
