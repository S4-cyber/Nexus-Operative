# Reconciliation Stack Consolidation Design

## Intent

Consolidate the current `development/windows-portal-foundation` risk-tier UI
work with the v0.340.411 reconciliation stack without rewriting either line.
Before promotion, close the independent-review findings in the detached trial
bundle: strictly type every exported field, recompute its reconciliation seal,
require complete no-fill/cut coherence, reject a supplied non-object bundle,
and bind parsed bundle bytes to the original no-follow file fingerprint.

## Goals

- Preserve both exact parents in one reviewable merge history.
- Keep the prominent Terrestrial/Solar/Universal controls and their existing
  entry-permission gating tests.
- Prevent private or structurally foreign values from escaping through the
  detached reconciliation allowlist.
- Reject contradictory resealed receipts, incomplete no-fill reconciliation,
  supplied `null`, symlinked bundle leaves, and bytes that differ from the
  sampled fingerprint.
- Preserve a blocked diagnostic review for omitted bundles with legacy modeled
  rows that lack a fill digest; a supplied bundle must still reject them.
- Use one exact modeled-outcome predicate for execution-set hashing and credit,
  so scheduler-classified BUY/SELL rows can never receive bundle credit.
- Synchronize prerelease version `0.340.412`, generated roadmap, release notes,
  installer/package metadata, and current-source saved evidence.

## Non-goals

- No broker, source, account, fill, fee, hold, funds, live, cloud, profitability,
  Windows-owner-profile, installer, or release acceptance.
- No automatic merge to `main`, real-money activation, credential access,
  provider request, or paid provisioning.
- No new UI control or editable parameter.
- No claim that caller-recomputable hashes authenticate a local journal.

## Constraints

- Engagement and optional AI stay OFF by default.
- Existing limits, account checks, reducing exits, fail-closed behavior, and
  independent emergency stop remain unchanged.
- Seven beginner-friendly tabs and black/bright-purple bottom-right hover help
  remain intact.
- The weighted estimate remains 38% (displayed about 40%, 30–45% low
  confidence) and 0/9 accepted stages unless independent evidence changes it.

## Approaches

1. **Rewrite/rebase the reconciliation stack onto development.** Produces a
   linear history but violates the no-destructive-rewrite boundary and makes
   exact hosted evidence stale. Rejected.
2. **Cherry-pick the 142-commit stack.** Preserves neither the reviewed remote
   commit identities nor a small auditable integration boundary. Rejected.
3. **Merge the exact development and PR #133 heads, then harden the combined
   tree.** Preserves both histories, makes the divergence explicit, and supports
   an expected-head review PR. Selected.

## Design

The consolidation branch starts at exact development head
`645675bd6e6904efd4aaf6a0bd615b35ffeabda0` and merges exact PR #133 head
`2492f6c309238311d67be9a37b67df3ac99d7f46` without squashing or forcing.

`forward_trial_reconciliation` will validate a closed primitive schema. Counts
are exact non-negative integers with arithmetic relationships, state flags are
exact booleans, digests are exact lowercase SHA-256 strings, and cut fields use
the existing closed enums. A clean export requires both fill and no-fill
reconciliation, a coherent cut projection, and a seal recomputed from the
allowlisted fields before publication and again during consumption. Canonical
JSON roots other than a bundle object fail even if their bytes are valid JSON.

Legacy modeled rows without a fill digest remain reviewable only when the
bundle is omitted, and stay explicitly unbound with no authority. When a bundle
is supplied, the missing digest is a hard validation failure. Execution credit
is computed from the same event identities that contribute to the sealed
execution-set digest, never from every BUY/SELL row in a validated session.

`forward_trial_review` will capture identities from caller-selected paths before
relationship normalization, so a symlink leaf cannot disappear through
`Path.resolve()`. The SHA-256 of the exact bytes passed to the canonical parser
must equal the initially sampled bundle fingerprint, followed by the existing
identity/fingerprint finalization checks. Supplying a path and decoding a
bundle remain separate states, so a supplied `null` can never become omission.

## Acceptance

Tests must demonstrate red then green for private-field injection, wrong scalar
types, negative/incoherent counts, false no-fill reconciliation, contradictory
cut fields, forged resealed event summaries, supplied `null`, symlink leaves,
parsed bytes that do not match the sampled fingerprint, legacy missing-fill
diagnostics, and mixed scheduler/outcome BUY/SELL sessions. Existing risk-tier,
reconciliation, release-profile, saved-evidence, complete regression,
browser-help, security, Windows, and packaging checks remain mandatory.
