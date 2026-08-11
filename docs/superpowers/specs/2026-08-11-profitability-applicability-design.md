# Profitability Applicability Design

## Goal

Make Profitability describe each CLIN using the pricing policy already present in
the burn payload instead of presenting every contract through static Ceiling, Fee,
and Margin concepts.

## Design

Add a pure frontend presentation helper that translates `family`,
`ceiling_meaning`, `revenue_basis`, and `known` into:

- the policy-specific name for the price or limit;
- the applicable earnings concept (profit, gross profit, fixed fee, award fee,
  incentive fee, or incentive profit);
- the applicable return concept (margin, profit rate, or fee rate); and
- whether earnings and return are applicable at all.

The contract summary uses a specific label when all CLINs agree and a neutral
`Earnings` / `Return` label for mixed awards. The CLIN table uses neutral headers
and prints each row's precise concept beneath the value. A cost-reimbursement
policy whose revenue basis contains no fee or profit mechanic, and pass-through
nonlabor rows, render `N/A` with an explanation rather than a zero or ambiguous
dash.

If any CLIN carries `pricing_policy.known: false`, the existing
`contract.pricing_unknown` count becomes a visible warning. Its row labels state
that the policy is unknown so legacy fallback arithmetic is not mistaken for a
typed award.

## Testing

Pure Node tests cover FFP, T&M, CPFF, CPAF, CPIF, FPI, a no-fee
cost-reimbursement policy, mixed policies, and unknown-policy counts. Existing
withholding tests continue to govern whether a numeric figure is safe to print.

## Scope

Only `web/src/profitability.js`, `web/src/profitability.test.js`, and
`web/src/views/Profitability.jsx` change. No burn, rate-model, database, workbook,
or ingest behavior changes, keeping the work disjoint from #158.
