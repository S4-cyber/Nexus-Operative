# Broker Request-Term Agreement Implementation Plan

**Goal:** Bind every matching retained activity snapshot to the original
request quantity and limit price.

**Base:** exact draft PR #127 head
`072bf3f5c012b298ce6f824defb2ded3a75f77c4`.

## Task 1: Reproduce the gap

- Add direct regressions for earlier conflicting quantity and price snapshots.
- Add a legacy missing-term regression.
- Add accepted-history propagation coverage.
- Confirm the old implementation reports agreement.

## Task 2: Implement the bounded check

- Retain all already-bounded snapshots by broker order during the existing
  receipt traversal.
- Require every matched snapshot's two request terms to exist and equal the
  canonical journal payload.
- Keep latest status/fill selection and all unchanged-journal checks intact.
- Advance only the direct policy to `nexus.request-activity-review.v2`.

## Task 3: Release synchronization and verification

- Synchronize prerelease metadata to `0.340.406`.
- Update durable continuity/release documentation and regenerate `ROADMAP.md`.
- Generate and independently revalidate all 16 offline release profiles.
- Run focused integration, deterministic full regression, compilation,
  dependency, Portal/Node, packaging, secret, and public-history checks.
- Obtain independent review, publish only with an exact-head lease, and inspect
  exact-head hosted CI without promoting any release gate.

## Task 4: Close the observed Windows replay failure

- Reproduce a transient final cold-replay read fault with a deterministic test.
- Permit at most three read-only replay observations per confirmation phase
  while the exact journal identity remains unchanged; never retry the halt
  mutation or a definitive negative.
- Re-run emergency-cut integration, exact-tree regression, evidence generation,
  packaging, independent review, and hosted Windows CI.
