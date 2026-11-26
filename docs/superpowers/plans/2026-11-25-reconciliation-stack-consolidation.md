# Reconciliation Stack Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve both development lines while closing the detached reconciliation bundle's privacy, semantic, and file-binding review findings.

**Architecture:** Merge the exact remote heads without rewriting history, then tighten the existing closed receipt validator and no-follow file-set boundary. Regenerate versioned evidence only after behavior and integration tests are green.

**Tech Stack:** Python 3.10+, SQLite, pytest, Git/GitHub Actions, existing standard-library hashing/path guards.

**Spec:** `docs/superpowers/specs/2026-11-25-reconciliation-stack-consolidation-design.md`

## Global Constraints

- Version remains below 1.0 and advances only to `0.340.412`.
- No new dependency, provider call, credential access, live order, funds movement, or cloud resource.
- Preserve OFF defaults, risk/account limits, reducing exits, fail-closed behavior, independent emergency stop, seven tabs, and black/bright-purple help.
- No development/main ref update until exact-child hosted checks and independent review are observed.

## Review Focus

- An allowlisted field carrying a nested/private string must fail before export.
- A correctly resealed but semantically contradictory event summary must fail.
- A supplied canonical `null` must fail instead of behaving like omission.
- A selected reconciliation leaf symlink must fail before path normalization.
- Parsed bundle bytes must equal the initially sampled SHA-256 fingerprint.
- Omitted bundles keep legacy missing-fill evidence blocked and visible.
- Bundle credit and execution-set hashing use the same contributing identities.

---

### Task 1: Preserve both exact histories

**Files:**
- Merge: `origin/review/v0.340.411-trial-reconciliation-bundle`
- Verify: `tests/test_portal_risk_tier_controls.py`
- Verify: `tests/test_forward_trial_reconciliation.py`

**Interfaces:**
- Consumes: development head `645675bd...` and PR #133 head `2492f6c...`.
- Produces: one merge commit with both exact parents and no discarded files.

- [ ] Merge PR #133 into the development-based consolidation branch without commit.
- [ ] Preserve the newer reconciliation/version documents plus development UI/test additions.
- [ ] Run the two direct integration suites and record their result.
- [ ] Commit the exact two-parent merge.

### Task 2: Close receipt privacy and semantic gaps with TDD

**Files:**
- Modify: `nexus/forward_trial_reconciliation.py`
- Modify: `tests/test_forward_trial_reconciliation.py`

**Interfaces:**
- Consumes: existing `build(reviews)`, `loads(raw)`, and `validate(bundle, sessions)`.
- Produces: strict validation rejecting foreign values, incoherent counts/cut fields, false no-fill reconciliation, non-object roots, and forged seals.

- [ ] Add failing table-driven tests for private nested values, wrong types, count arithmetic, no-fill flags, cut coherence, resealed contradictions, and `null`.
- [ ] Run the new tests and verify each fails for the intended missing check.
- [ ] Implement minimal closed primitive/type/coherence validation and publication-time seal recomputation.
- [ ] Run direct bundle and broader reconciliation tests to green.
- [ ] Commit behavior and tests.

### Task 3: Close reconciliation-file selection races with TDD

**Files:**
- Modify: `nexus/forward_trial_review.py`
- Modify: `tests/test_forward_trial_review.py`

**Interfaces:**
- Consumes: `capture_file_identities`, `check_path_set`, `capture_file_fingerprints`.
- Produces: original-path no-follow identity capture, exact parsed-byte digest
  binding, legacy unbound diagnostics, and identity-exact execution credit.

- [ ] Add failing tests for supplied `null`, a bundle leaf symlink, a read seam
  returning bytes unlike the sampled fingerprint, a legacy row without a fill
  digest, and a mixed scheduler/outcome BUY/SELL session.
- [ ] Run tests and verify the expected unsafe acceptance/failure mode.
- [ ] Reorder identity/relationship capture, distinguish supplied from decoded
  state, compare parsed bytes with fingerprint index 7, and share one exact
  contributing-event predicate between hashing and credit.
- [ ] Run direct trial-review and SQLite guard suites to green.
- [ ] Commit behavior and tests.

### Task 4: Synchronize the v0.340.412 release increment

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`, `ROADMAP.md`, `AGENTS.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Create: `validation/release_trials_0_340_412.json`
- Modify: release-profile tests as required by stricter receipt semantics.

**Interfaces:**
- Produces: one current-source, authority-false saved report for all 16 profiles.

- [ ] Update synchronized prerelease metadata and durable limitations.
- [ ] Regenerate deterministic roadmap and saved offline release profiles.
- [ ] Revalidate saved evidence against the exact source fingerprint.
- [ ] Commit version, documentation, tests, and evidence together.

### Task 5: Verify and publish without overwriting development

**Files:**
- Verify: entire repository and generated package artifacts.

**Interfaces:**
- Produces: a reviewable PR targeting `development/windows-portal-foundation`.

- [ ] Run focused, release, browser-help/CI-inventory, emergency, and complete deterministic regression suites.
- [ ] Build and inspect the wheel; run compilation, dependency integrity, diff, and secret scans.
- [ ] Obtain independent exact-tree review and fix all high/medium findings.
- [ ] Recheck development and parent heads immediately before publishing with expected-head leases.
- [ ] Publish a draft PR and record hosted security, saved-evidence, browser, Windows, and installer states without inferring acceptance.
