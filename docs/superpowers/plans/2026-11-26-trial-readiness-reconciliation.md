# Trial Readiness Reconciliation Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the composite paper Trial Readiness report consume an explicitly supplied exact local reconciliation bundle under one fail-closed file-set fence.

**Architecture:** Extend the existing readiness builder only; reuse the lower-level bundle validator and the existing readiness path/identity/fingerprint guard. Keep the Portal route unchanged so private evidence is never auto-selected.

**Tech Stack:** Python standard library, SQLite read-only snapshots, existing Nexus file guards, pytest.

**Spec:** `docs/superpowers/specs/2026-11-26-trial-readiness-reconciliation-design.md`

## Global Constraints

- Version remains below 1.0 and every external authority flag remains false.
- Engagement and optional AI remain OFF by default; limits, account checks, reducing exits, fail-closed behavior and independent emergency stop remain unchanged.
- No UI control or editable parameter, provider I/O, credentials, funds, paid resource or cloud deployment is added.
- Roadmap progress remains exact 38% weighted, displayed about 40%, 30–45% low confidence, 0/9 independently accepted.

## Review Focus

- Bundle/history aliasing must fail before either file is read for credit.
- A replaced or mutated bundle during order-capacity composition must invalidate the whole result.
- Supplying a bundle without retained history must not create or credit history.
- Omission must preserve the existing modeled-execution blocker.
- A valid bundle must not remove unrelated incidents or set any external authority flag.

---

### Task 1: Joint readiness/bundle composition

**Files:**
- Modify: `nexus/trial_readiness.py`
- Modify: `tests/test_trial_readiness.py`

**Interfaces:**
- Consumes: `forward_trial_review.review(path, after=..., until=..., reconciliation_bundle=...)`.
- Produces: `build(path, *, hours=72, now=None, order_gateway=None, reconciliation_bundle=None) -> dict`.

- [ ] **Step 1: Write the failing exact-bundle integration test**

  Use real retained stopped-session history and the canonical test bundle helper. Assert a valid explicit bundle produces its exact SHA, binds all modeled executions, leaves zero unreconciled modeled executions, keeps every external authority false and leaves all input bytes unchanged.

- [ ] **Step 2: Run the test and observe the expected failure**

  Run: `pytest tests/test_trial_readiness.py -k reconciliation_bundle -q`
  Expected: `TypeError` because `build()` does not accept `reconciliation_bundle`.

- [ ] **Step 3: Add the minimal guarded pass-through**

  Canonicalize the optional bundle with the existing path set, retain owner index semantics, reject a bundle without retained history, and pass the guarded path to `trial.review()`.

- [ ] **Step 4: Run the exact-bundle test and affected suites**

  Run: `pytest tests/test_trial_readiness.py tests/test_forward_trial_review.py tests/test_forward_trial_reconciliation.py -q`
  Expected: all pass.

- [ ] **Step 5: Add and verify the mutation-race regression**

  Replace the exact bundle after the retained review but before the final file-set check and assert `TRIAL_READINESS_FILE_SET_CHANGED`.

### Task 2: Release synchronization and verification

**Files:**
- Modify: `pyproject.toml`, `nexus/__init__.py`, `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`, `ROADMAP.md`, `AGENTS.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Regenerate: saved release evidence required by repository tooling.

**Interfaces:**
- Consumes: Task 1 behavior and unchanged false-authority contract.
- Produces: synchronized prerelease `0.340.423`, exact-source evidence and a reviewable commit.

- [ ] **Step 1: Synchronize version and durable roadmap narrative**
- [ ] **Step 2: Regenerate and reverify all required offline release profiles**
- [ ] **Step 3: Run affected suites, both complete deterministic shards, packaging, dependency and secret/artifact checks**
- [ ] **Step 4: Obtain independent whole-branch review; fix every important finding with RED/GREEN evidence**
- [ ] **Step 5: Recheck remote heads and publish only under the exact expected-head lease**
