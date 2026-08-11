# CPAF Pool-Share Currency Design

## Problem

The CPAF fee-period API and fee engine define `pool_share` as a dollar amount. The Profitability view passes that value to the percentage formatter, so a `$45,000` period share renders as `4,500,000% of pool`. The existing frontend fixture uses fractional shares, which contradicts the server contract and masks the presentation bug.

## Design

Add a pure `awardPoolShareLabel(poolShare)` presentation helper to `web/src/profitability.js`. It returns an empty string when no share was supplied and otherwise uses the existing currency formatter to produce text such as `$45,000 of pool`. `AwardPeriods` in `web/src/views/Profitability.jsx` will render this helper instead of calling `pct` directly.

Correct the CPAF frontend fixture to use dollar-denominated pool shares matching the server tests. Add a regression assertion for the user-visible label so changing the view back to percentage formatting breaks the test.

## Scope

- No API or fee-engine changes.
- No changes to score or incentive-share percentage formatting.
- No unrelated Profitability cleanup.

## Verification

Run the targeted Profitability test file, the complete web test suite, and the production web build.
