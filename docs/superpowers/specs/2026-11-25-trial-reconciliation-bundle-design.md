# Trial reconciliation bundle design

## Intent

Permit the retained forward-trial reviewer to distinguish modeled BUY/SELL
events that have been proven locally consistent with the durable order/fill
journal from modeled executions that remain unbound.  This closes a local M4
reconciliation gap without presenting local hashes as broker, account, market,
sustained-trial, funding, live-trading, cloud, or release acceptance.

## Goals

- Export a privacy-safe, canonical bundle from stopped-session read-only
  evidence reports.
- Bind each contributing modeled session to the exact retained session summary,
  quote head, event head, event reconciliation seal, and two stable durable
  order snapshots.
- Bind the complete ordered modeled-execution set without exporting order ids,
  products, prices, quantities, fees, or modeled economics.
- Review the history database and detached bundle under one bounded file-set
  identity/fingerprint guard.
- Count bound and unbound modeled executions explicitly.  Only the unbound
  count is an operational reconciliation incident.
- Fail closed on malformed, aliased, oversized, duplicated, missing, stale,
  mutated, or authority-escalating bundle content.

## Non-goals and anti-goals

- No broker or credential access, provider I/O, order mutation, repair,
  automatic retry, live trading, funding, or release permission.
- No source, price, clock, market-realism, sustained-trial, performance, or
  profitability attestation.
- No UI control or editable parameter.  The seven beginner tabs and existing
  black/bright-purple bottom-right hover help remain unchanged.
- A bundle must not sanitize an incomplete event trace or any unrelated
  operational incident.

## Constraints

- Version remains below 1.0 and all existing authority flags remain false.
- The optional bundle path is explicit; omitting it preserves the v0.340.410
  fail-closed behavior.
- Only exact built-in JSON values are accepted.  The file is canonical JSON,
  regular, single-link, non-aliased with the history database, and at most
  8 MiB with at most the existing session limit.
- Only stopped sessions with a clean read-only review may be exported.
- Every modeled session retained in the history must have exactly one matching
  record; extra records are rejected rather than ignored.

## Approaches considered

1. **Open the order journal from the trial reviewer.** This could produce the
   freshest composition, but it expands the reviewer's storage roles and makes
   one command coordinate two SQLite snapshots and an emergency latch.  It is
   rejected because the existing per-session reviewer already owns that job.
2. **Trust receipt hashes supplied as CLI strings.** This is small but cannot
   prove complete per-session coverage, stable order snapshots, or exact event
   heads.  It is rejected as too weak.
3. **Detached canonical receipt bundle (selected).** Export the already sealed
   privacy-safe subset of `PaperForwardRunner.evidence_report()` and validate it
   against the exact history snapshot under a joint file guard.  This reuses the
   established reconciliation boundary, keeps private rows out of the public
   artifact, and gives missing/stale/duplicate bindings explicit fail-closed
   behavior.

## Design

`nexus.forward_trial_reconciliation` defines profile
`nexus.forward-trial-reconciliation-bundle.v1`.  `build(reviews)` accepts exact
stopped-session review mappings and returns canonicalizable public records
containing the session id, complete sealed review receipt, bounded durable event
summary, stable order snapshot summary, empty reasons, and false authority
flags.  It copies only allowlisted fields and rejects inconsistent inputs.

The per-session reconciliation summary gains
`modeled_execution_set_sha256`, a domain-separated hash of every retained
modeled event's event sequence, cycle, side, order id, quote digest, and modeled
fill digest.  The existing reconciliation seal commits to that hash.  The
detached bundle exports only the hash and count; trial review recomputes both
from the complete retained event chain, including events outside the requested
window.

`validate(bundle, sessions)` accepts an exact mapping of retained session facts
created inside the trial database snapshot.  It recomputes each receipt digest
and session-summary digest, checks quote/event counts and heads, checks the event
reconciliation seal and all positive local consistency booleans, requires
stable equal order snapshots, and returns the modeled-event count bound per
session.  It never upgrades external authority.

`nexus.forward_trial_review.review(..., reconciliation_bundle=None)` adds the
optional explicit path.  When present, the history database and bundle are
captured and finalized as one relationship-distinct file set.  The bundle is
read once within its byte limit and must be exact canonical JSON.  The reviewer
publishes `bundle_bound_modeled_execution_events_in_window` and
`unreconciled_modeled_execution_events_in_window`; only the latter contributes
the fixed `MODELED_EXECUTION_LOCAL_BUNDLE_NOT_BOUND` reason.  Other incident
rules and all acceptance flags are unchanged.

## Verification

- RED/GREEN unit tests for bundle export and strict validation.
- RED/GREEN reconciliation tests proving the execution-set commitment changes
  with any bound event identity or digest and is sealed by the reconciliation.
- Integration tests showing omission remains blocked, a complete exact bundle
  binds the modeled event, and forged/stale/duplicate/missing/extra/mutated
  bundles fail closed.
- Existing forward-trial and forward-evidence suites, full regression shards,
  saved release-evidence verification, packaging checks, and secret scan before
  publication.
