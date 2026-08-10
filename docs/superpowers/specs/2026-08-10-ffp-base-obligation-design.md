# FFP Base Obligation and Missing Option-Mod Design

## Goal

Make an initial FFP award ingest report the Base period as fully obligated while
keeping option funding auditable. An option remains unexercised and unfunded until
an explicit SF-30 exercise modification is ingested. If operational data shows work
against that option first, Runway warns about the missing modification instead of
inventing funding.

The regression document is
`/Users/kayleeplecas/Downloads/govcon_award_sf26-6.pdf`, contract
`W45983-24-C-1675`.

## Extraction Semantics

### Initial award

- The extraction prompt treats every option period on an SF-26 or SF-1449 as
  `exercised = false` unless the document contains explicit evidence that the
  option was exercised. Merely omitting the phrase "option not exercised" is not
  evidence.
- The Base period remains `exercised = true`.
- An SF-30 extraction may identify `action_type = option_exercise` and the period it
  exercises. The existing mod merge path is the only operation that flips that
  option to `exercised = true`.

### FFP Base obligation normalization

After model extraction and ordinary obligation normalization:

- Recognize FFP through the existing pricing normalizer rather than raw string
  equality.
- Apply the default only to CLINs in the Base period of an overall FFP contract.
- If a Base CLIN has `obligated = null` and a numeric `ceiling`, set
  `obligated = ceiling`.
- Preserve every explicitly extracted obligation, including an explicit zero.
- Apply the Base default to all Base-period CLINs on the FFP award, including
  travel/ODC COST lines; do not apply it to option CLINs.
- Do not change cost-reimbursement, T&M, incentive, vehicle, or unknown contracts.
- Recompute header `total_obligated` from the Base-period CLIN obligations after
  normalization. This produces `$3,037,736.80` for the regression award.
- Derive `incrementally_funded` from the Base obligation versus the Base-period
  ceiling, not from the all-options contract ceiling. A fully obligated Base with
  unexercised options is not incrementally funded.

## Missing Option-Modification Signal

The burn payload will expose one notice for each option period that satisfies all
of these conditions:

1. the option is not exercised;
2. no option-exercise entry for it exists in `obligation_history`; and
3. synced timesheets contain positive billable hours on one of that option's CLINs
   or during that option's date window.

The notice carries the period name and affected CLINs. It does not change funding,
exercise state, spend, or pricing.

The Flight Deck renders the notice through the existing scope-notice/alert flow:

> Option 1 performance detected on timesheets, but the Option 1 SF-30 funding
> modification has not been ingested.

The notice is a data-provenance warning, not a funding tripwire. It remains until
an SF-30 option-exercise modification is ingested.

## SF-30 Workflow

The matching SF-30 is intentionally not synthesized. When supplied and ingested,
the existing mod merge path must:

- set Option 1 to exercised;
- add `$2,922,481.60` across Option 1 CLINs from the document's funding lines; and
- bring cumulative obligations to `$5,960,218.40` with the modification retained
  in `obligation_history`.

No code in this change invents those option dollars without the source document.

## Tests

Server tests will prove:

- the regression-shaped FFP award fully obligates missing Base CLIN amounts;
- explicit Base obligations and explicit zeroes are preserved;
- option CLINs remain unexercised and un-obligated after award ingest;
- non-FFP awards are unchanged;
- header obligation and funding posture reconcile to the Base period;
- option-period timesheet activity emits the missing-SF-30 signal;
- the signal disappears after an option-exercise history entry exists; and
- ingesting the matching-shaped Option 1 mod produces the expected cumulative
  obligation without changing Base funding.

Frontend tests will prove the scope notice copy and that it enters the existing
Flight Deck alert ordering.

## Scope Boundaries

- No database rewrite of already-ingested contracts.
- No fabricated SF-30 or option funding.
- No change to pricing, margin, LCAT resolution, or timesheet synchronization.
- No automatic obligation of unexercised options.
