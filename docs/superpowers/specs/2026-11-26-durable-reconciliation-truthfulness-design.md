# Durable Reconciliation Truthfulness Design

## Intent

Prevent local review and cold recovery from reporting reconciled or complete evidence when retained durable identities disagree. This increment addresses two verified offline defects without adding provider, account, routing, money-movement, cloud, live, or release authority.

## Goals

- Bind every replayed priced fill to the exact linked-ledger `(order_id, event_id)`, action, product, quantity, price, and fee.
- Preserve journal-only emergency halt, cancel, and expiry operations when linked accounting is already inconsistent.
- Treat retained exchange-admission rows without their session head as corrupt, not as legacy absence.
- Bind the forward-trial evidence snapshot to the already bounded and rechecked main forward-database fingerprint so diagnostic-changing mutations change the snapshot digest.
- Keep reads non-mutating and all public authority flags false.

## Non-goals

- No broker, credential, provider, account, source, funds, or genuine-market acceptance.
- No schema migration, cross-database atomicity claim, distributed coordination, repair, retry, or automatic evidence discovery.
- No Portal button or editable parameter.
- No merge of draft agency-agent coordination code.

## Constraints

- Application, package, installer, roadmap, operator guidance, and saved evidence remain synchronized below 1.0.
- Engagement and optional AI stay OFF by default. Limits, account checks, fail-closed behavior, reducing exits, and the independently writable emergency cut remain intact.
- The seven beginner-friendly tabs and black/bright-purple bottom-right hover help remain unchanged.
- Private retained evidence, account data, and credentials must not enter the repository.

## Approaches

### 1. Keep aggregate-only reconciliation

Rejected. Matching cash and positions cannot detect renamed, missing, extra, or reassigned fill identities and allowed a changed durable command to remain `REPLAY_VERIFIED`.

### 2. Compare exact keyed durable projections

Selected. Build a bounded map from replayed gateway executions keyed by `(order_id, event_id)`, derive the expected ledger action/product from the immutable order, and consume the linked-ledger join row by row. Reject missing, extra, duplicate, or unequal fills with `EXECUTION_PORTFOLIO_DISAGREEMENT`. This uses existing replay bounds and adds no storage format.

### 3. Add cross-store seal columns and migrations

Deferred. A persistent cross-store seal could strengthen future proofs but requires a migration and broader compatibility work. Exact comparison closes the demonstrated defect with less risk.

For forward evidence, missing admission head plus retained rows is corruption. True legacy absence remains only the exact `(no head, zero rows)` case. The snapshot digest incorporates the existing bounded main-database fingerprint instead of introducing an unbounded second read.

## Failure behavior

- Cold linked recovery, session snapshots, idempotent fill acknowledgements, and new ordinary fills fail with `EXECUTION_PORTFOLIO_DISAGREEMENT` when fill identities differ.
- Emergency halt, cancel, and expiry remain journal-only safety paths and do not require a reconciled portfolio.
- Headless retained admissions return failed integrity with `EXCHANGE_ADMISSION_HEAD_MISSING`; existing callers convert that to their fixed fail-closed errors.
- Trial review remains diagnostic for a corrupt event chain, but its evidence digest changes with the bounded database fingerprint.

## Evidence boundaries

Synthetic SQLite fixtures prove local replay and identity behavior only. They do not attest provider delivery, broker activity, actual fees or balances, sustained operation, native Windows behavior, profitability, or release readiness.
