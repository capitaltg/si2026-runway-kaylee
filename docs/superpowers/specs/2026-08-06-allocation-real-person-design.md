# Allocation Matrix real-person add design

## Goal

Replace the allocation matrix's placeholder planned person and blended-rate pricing
with a typed staffing scenario that is either based on a directory person or a new
hire, and that always carries an explicit rate.

## User flow

The Add person panel starts with two choices: select a person from the directory or
enter a new hire. Selecting a directory person fills name, employee ID, their most
recent known LCAT, and current cross-contract utilization; every staffing field stays
editable. A new hire starts blank and uses the same editable form.

The CLIN's own priced LCATs are the primary LCAT dropdown. Choosing one fills its
listed loaded rate. The final option is “Other — not on the rate schedule…”, which
reveals a free-text LCAT and requires a manually entered rate. The form cannot add a
person without a rate, so a blended rate is never silently used. A CLIN without a
rate table shows only Other and an inline rate-schedule import control.

Years of experience, education, and clearance are optional planning attributes kept
in the saved plan's added-person object. They do not update the people directory.
Rate options show the award's minimum qualifications when present.

## Data and persistence

The allocation API exposes each active labor CLIN's rate lines, including loaded rate
and printed qualification floors. The client fetches the directory and utilization
on demand when opening Add person. A selected directory record is copied into the
what-if; it does not change allocation actuals or directory data. Added records save
with existing plan state and retain their selected/manual rate, LCAT, ID, utilization
snapshot, and optional qualifications.

## Error handling

Directory loading failures leave the typed-new-hire path usable and display an inline
notice. An unknown LCAT needs a non-empty numeric rate. A rate-table-less CLIN names
the missing schedule and offers import, but still permits Other with an explicit rate.

## Tests

Pure client helpers cover rate-option selection, directory prefill, and validation
that rejects missing/manual rates and never falls back to a blended rate. A server
test pins qualification-bearing rate lines on the allocation CLIN payload.
