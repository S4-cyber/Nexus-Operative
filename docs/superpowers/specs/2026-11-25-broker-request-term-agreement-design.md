# Broker Request-Term Agreement Design

## Goal

Prevent a newest retained broker order snapshot from hiding an earlier request-
quantity or limit-price conflict when reviewing one original paper request.

## Design

The existing exact-head, read-only request/activity review keeps its latest
snapshot selection for status and fill comparison. In parallel, it collects the
complete already-bounded list of retained snapshots for each broker order. Every
snapshot matched to an observed original request must contain canonical
`requested_quantity` and `limit_price` fields, and both values must equal the
request journal payload.

Legacy retained snapshots without either term remain replay-readable. They do
not establish exact agreement and produce fixed `*_UNBOUND` blockers. A
conflicting value produces a fixed `*_DISAGREEMENT` blocker. The direct review
policy advances from v1 to v2; higher-level compositions bind the changed
component digest and retain their existing policies.

## Safety and scope

- No network, provider, broker, account, funding, order, retry, ledger, or
  permission path is added.
- All journals stay read-only and retain exact-head and unchanged-file checks.
- Engagement and optional AI remain OFF by default; limits, account checks,
  exits, fail-closed behavior, and the independent emergency stop are unchanged.
- No UI control or parameter changes; the seven tabs and black/bright-purple
  bottom-right hover help remain unchanged.
- This is local reconciliation hardening, not broker-paper, source, fee, hold,
  funds, Windows, sustained-trial, cloud, performance, or release acceptance.

## Acceptance cases

1. A correct newest snapshot cannot hide an earlier quantity conflict.
2. A correct newest snapshot cannot hide an earlier limit-price conflict.
3. Legacy snapshots missing either term replay but fail exact agreement.
4. Canonically equivalent retained terms continue to agree.
5. The accepted-history composition propagates the unbound result without
   granting authority or mutating any journal.

## Exact-head Windows CI hardening

The parent exact-head Windows shard exposed one transient cold-replay
observation that left a committed emergency halt conservatively unconfirmed.
Cold halt confirmation may therefore retry only transient `OSError` or SQLite
operational read faults, with a strict three-attempt ceiling per confirmation
phase. Each attempt is
read-only and requires the same regular single-link journal identity before and
after replay. Identity drift and a definitive negative stop immediately. The
emergency halt mutation remains single-shot and reset remains unavailable.
