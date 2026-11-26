# Trial Reconciliation Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind retained modeled executions to exact privacy-safe durable order/fill reconciliation receipts without granting external authority.

**Architecture:** A focused pure module exports and validates canonical per-session reconciliation records.  The existing trial reviewer optionally includes the bundle in its bounded file-set guard and uses validated per-session counts only to remove the local modeled-execution incident.

**Tech Stack:** Python standard library, SQLite read-only snapshots, existing Nexus file guards and canonical JSON helpers, pytest.

**Spec:** `docs/superpowers/specs/2026-11-25-trial-reconciliation-bundle-design.md`

## Global Constraints

- Version stays below 1.0; broker, account, market-realism, sustained-trial, funding, live, cloud, money-movement, and release authority remain false.
- Engagement and optional AI stay OFF by default; limits, account checks, fail-closed exits, and independent emergency stop are unchanged.
- No dependency, network, credential, provider, account, order, or funding access is introduced.
- No UI control or editable parameter is added.
- The optional bundle is capped at 8 MiB and the existing `SESSION_LIMIT`.

## Review Focus

- Canonical but forged receipt digest must fail before any modeled event is counted as bound.
- A valid receipt for a stale event head or session summary must fail against the exact history snapshot.
- Missing, duplicate, or extra session records must fail closed rather than partially bind a trial.
- Bundle/history path aliasing or bundle mutation during review must fail the joint file guard.
- A valid local bundle must leave all external authority false and must not erase unrelated incidents.

---

### Task 1: Canonical bundle boundary

**Files:**
- Modify: `nexus/forward_reconciliation_review.py`
- Create: `nexus/forward_trial_reconciliation.py`
- Modify: `tests/test_forward_evidence.py`
- Create: `tests/test_forward_trial_reconciliation.py`

**Interfaces:**
- Consumes: existing `forward_reconciliation_review.RECEIPT_PROFILE` and canonical JSON conventions.
- Produces: `build(reviews: list[dict]) -> dict`, `loads(raw: bytes) -> dict`, and `validate(bundle: dict, sessions: dict[str, dict]) -> dict[str, int]`.

- [ ] Write failing tests for the sealed complete execution-set commitment, privacy-safe export, canonical parsing, receipt/session/head/snapshot validation, duplicates, omissions, extras, and false authority.
- [ ] Run the new test module and observe failures caused by the missing module/interface.
- [ ] Implement the minimal execution-set seal plus strict exporter, parser, and validator.
- [ ] Run the new module tests and the existing per-session reconciliation tests.
- [ ] Commit the tested boundary.

### Task 2: Joint retained-trial composition

**Files:**
- Modify: `nexus/forward_trial_review.py`
- Modify: `tests/test_forward_trial_review.py`

**Interfaces:**
- Consumes: Task 1 `loads` and `validate`.
- Produces: optional `reconciliation_bundle` review/CLI argument and exact bound/unbound in-window modeled-execution counts.

- [ ] Write failing integration tests for absent, valid, forged, stale, incomplete, extra, aliased, and mutated bundles.
- [ ] Run the selected tests and observe the expected missing-argument/count failures.
- [ ] Add joint history/bundle identity and fingerprint guarding, exact session facts, count classification, result keys, and CLI argument.
- [ ] Run the integration tests and full affected forward suites.
- [ ] Commit the tested composition.

### Task 3: Release synchronization and verification

**Files:**
- Modify: `pyproject.toml`, `nexus/__init__.py`, `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`, `ROADMAP.md`, `AGENTS.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Modify: release evidence expectations and generated evidence files as required by repository tooling.

**Interfaces:**
- Consumes: Tasks 1-2 public fields and unchanged authority boundaries.
- Produces: synchronized prerelease metadata, durable roadmap narrative, regenerated saved evidence, and reviewable branch commit.

- [x] Add/update release-profile assertions for the exact new counts and false authority flags; observe RED before profile synchronization.
- [x] Synchronize version `0.340.411`, roadmap source/generated documents, package/app/installer metadata, and evidence expectations.
- [x] Run affected suites, deterministic full regression shards, release profiles, saved-evidence verification, packaging checks, and available secret/security scans.
- [ ] Obtain an independent whole-branch review and fix any important findings with RED/GREEN tests.
- [ ] Commit the verified release increment and publish only if the remote expected base remains unchanged.
