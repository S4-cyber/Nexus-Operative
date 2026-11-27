# Trial readiness reconciliation composition design

## Intent

Let the read-only paper Trial Readiness composition consume an explicitly
supplied, privacy-safe local forward-trial reconciliation bundle.  Today the
lower-level retained-trial reviewer can validate that bundle, but the composite
readiness report cannot pass it through, so every modeled BUY/SELL remains
reported as locally unreconciled even when the exact bundle exists.

## Goals

- Add one explicit optional `reconciliation_bundle` input to
  `trial_readiness.build()`.
- Include the bundle in the same canonical path, file-identity, fingerprint,
  storage-root and owner-lease review boundary as the retained history, order
  journal and linked portfolio.
- Pass the exact guarded bundle path to `forward_trial_review.review()`.
- Preserve the current fail-closed result when the input is omitted.
- Prove a valid exact bundle can bind every modeled execution while all source,
  broker, account, funds, sustained-trial, live and release authority stays
  false.
- Fail closed if the bundle is aliased, replaced or mutated during the larger
  readiness composition.

## Non-goals and anti-goals

- No Portal button, editable field, query parameter or automatic bundle search.
- No broker, network, credential, provider, order, funding or cloud access.
- No repair, retry, automatic continuation or inferred acceptance.
- No change to the 38% weighted estimate, 0/9 accepted stages, release approval
  or trading permission.

## Approaches

1. Automatically discover a bundle beside the history. Rejected because a
   read-only dashboard call must not silently select private evidence.
2. Add a Portal path input. Rejected because it expands the UI/privacy model and
   would require new beginner controls and hover help.
3. Add an explicit function-level input and joint file-set guard. Selected
   because callers must opt in, omission remains conservative, and the existing
   lower-level validator remains the sole semantic authority.

## Design

`trial_readiness.build(..., reconciliation_bundle=None)` canonicalizes the
optional path together with every other reviewed path.  The bundle must remain
in the same storage root, distinct from the history, order, portfolio and owner
paths. A custom order gateway without a path is rejected when a bundle is
supplied because it cannot provide that complete fence. Existing files are
fingerprinted before review and rechecked after the
order-capacity composition; the owner marker remains identity-only under its
retained OS lease.  When history exists, the guarded bundle path is passed to
`forward_trial_review.review()`.  When history is absent, supplying a bundle is
rejected without semantic validation or readiness credit.

The HTTP route remains unchanged and therefore continues to omit the bundle.
This increment provides a safe composition interface for an explicit offline
operator/reviewer workflow without creating a public path control.

## Verification

- RED/GREEN integration using real retained SQLite history and a canonical
  reconciliation bundle.
- Valid bundle: modeled count equals bound count, unreconciled count is zero,
  exact bundle SHA is present, unrelated incidents remain, all authority flags
  remain false and every input byte remains unchanged.
- Bundle replacement/mutation during readiness: fixed
  `TRIAL_READINESS_FILE_SET_CHANGED` failure.
- Omitted bundle and existing API behavior remain unchanged.
