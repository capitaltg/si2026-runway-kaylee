# Notes for the test-data generator — how real federal contracts are actually shaped

Findings from real SF-26 awards + 2026 GSA rate data, distilled into rules the
generator can encode so its contracts pass a sharp reviewer's smell test.

## 1. The document ≠ the data. An award is a cover form + a Schedule.
A real award is **Standard Form 26** (or SF-1449 for commercial). The cover
page is just numbered blocks (1–31); the money and CLINs live in **Section B**
of the attached Schedule, not on the cover. Generating a believable contract
means generating BOTH: the cover block metadata *and* the Section B schedule.

Cover-page blocks that actually matter to a burn tool:
- **Block 2** — Contract No. (PIID). Real formats: DHS `70RCSA26C0000123`,
  GSA `GS-35F-0546K`, DoD `W912DY-26-C-0042`. 13–17 chars, agency-coded prefix,
  2-digit FY, a type letter (C=contract, F=task order, D=IDIQ), sequence.
- **Block 3** — Effective date. **Block 15G** — total contract amount (ceiling).
- **Block 14 / Section G** — accounting & appropriation string.

## 2. Ceiling ≠ obligated. This is the single most important fact.
Most contracts are **incrementally funded**: the ceiling is the whole thing,
but only a slice is *obligated* (funded) right now (FAR 52.232-22, Limitation
of Funds). The gap between them IS the product — that's what a burn tool warns
about. Generator rule: always emit `total_ceiling`, `total_obligated`, and an
**obligation history** (award + mods P00001, P00002…), with obligated < ceiling.

## 3. CLIN numbering is structured, not random.
- 4-digit, zero-padded: base year `0001–0999`, option year 1 `1001–1999`,
  option year 2 `2001–2999`, etc. The leading digit encodes the period.
- **SubCLINs** (informational/funding splits) add two more digits: `0001AA`,
  `0001AB`.
- Group CLINs by **contract type**: labor is usually **T&M** or **CPFF**;
  travel and materials/ODC are **cost-reimbursable, no fee**; deliverable-based
  work is **FFP**. Don't mix a single CLIN across types.

## 4. Periods of performance are 12-month base + option years.
Standard shape: 1 base year + up to 4 option years (the "5-year IDIQ"). Option
years are in the document but **not exercised** until a mod. PoP dates are
contiguous (option starts the day after base ends). Generator should mark each
period `exercised: true/false`.

## 5. Labor rates are FULLY BURDENED and follow a wrap-rate formula.
GSA/T&M rates are *loaded* — they already include fringe, overhead, G&A, and
profit on top of base salary:

```
loaded_rate = base_hourly x (1+fringe) x (1+overhead) x (1+G&A) x (1+profit)
```

Realistic 2026 multipliers ("wrap rate") land at **2.0–2.45x** base salary
(DC-metro on-site trends high, remote low). So the generator should pick a base
salary, a wrap rate, and derive the loaded rate — not invent loaded rates
directly. Sanity-check bands (2026, fully-burdened $/hr):

| LCAT | Loaded $/hr |
|---|---|
| Admin / Junior support | 45–75 |
| Business Analyst | 100–150 |
| Systems Engineer | 90–135 |
| Software Engineer (mid) | 110–165 |
| Program Manager (PMP) | 130–190 |
| Senior Software Engineer | 155–220 |
| Senior SME / Cyber SME | 180–290 |

Clearance premium is real: **Secret ≈ +$8–12/hr, TS/SCI ≈ +$20–30/hr.**

## 6. LCATs carry minimum qualifications — that's the compliance feature.
Each labor category has **min education + min years experience + clearance**.
This is what enables the "LCAT compliance guardrail" feature: cross-check an
employee's resume against the CLIN's LCAT floor. Generator should emit those
floors on every labor line, and (for negative tests) sometimes generate
employees who *fail* them.

## 7. Hours tie to FTEs. Keep them arithmetically clean.
1 full-time FTE ≈ **2,080 hrs/yr** (1,920 productive after leave, if you want
realism). Estimated hours per LCAT should be a clean multiple of 2,080 (or
1,040 for half-time). `sum(rate x hours)` per LCAT **must equal** the labor
CLIN ceiling — a reviewer will add it up.

## 8. There are always CDRLs (deliverables).
Monthly status + monthly financial/burn report + quarterly progress is the
boilerplate set. These feed the "one-click compliance report" feature.

## Minimum viable schema for a generated contract
`piid, effective_date, agency, contractor(UEI/CAGE), contract_type,`
`total_ceiling, total_obligated, obligation_history[],`
`periods[]{name, pop_start, pop_end, exercised, ceiling},`
`clins[]{clin, period, title, type, is_labor, ceiling, est_hours},`
`labor_rates[]{lcat, rate, est_hours, min_education, min_experience_yrs, clearance},`
`cdrls[]`

## Cross-field invariants the generator must NOT violate (these are the tells)
1. `sum(clin.ceiling for period) == period.ceiling`
2. `sum(period.ceiling) == total_ceiling`
3. `total_obligated <= total_ceiling` (and usually strictly less)
4. `sum(rate x hours) per labor CLIN == that CLIN's ceiling`
5. option-period CLINs start with the period digit (1xxx, 2xxx…)
6. PoP periods are contiguous and non-overlapping
7. loaded rate ≈ base_salary/2080 x wrap(2.0–2.45)

## Sources
- [SF-26 Award/Contract (GSA, current rev)](https://www.gsa.gov/system/files/SF26-22.pdf)
- [Real filled SF-26 (OASIS, SwRI)](https://www.swri.org/sites/default/files/Pool4-SF26-Contract.pdf)
- [Reviewing an SF-26 — what the blocks mean](https://www.reliascent.com/blog/government-contract-reviews-and-the-sf-26-protecting-your-business)
- [2026 GSA labor rates & wrap-rate mechanics](https://fed-spend.com/blog/government-contract-labor-rates-gsa-schedule-2026)
- [Labor categories / LCAT requirements](https://govcongiants.com/guides/labor-categories)
