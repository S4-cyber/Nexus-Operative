# Durable Reconciliation Truthfulness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make linked execution reconciliation and retained forward-evidence identity fail closed when durable histories disagree.

**Architecture:** Add an exact keyed fill comparison beside the existing aggregate portfolio comparison and invoke it only where a replayed linked gateway is available. Separately, distinguish true legacy admission absence from missing-head corruption and include the already bounded forward-database fingerprint in the trial evidence digest.

**Tech Stack:** Python 3.10+, SQLite, pytest, standard-library `Decimal` and SHA-256.

**Spec:** `docs/superpowers/specs/2026-11-26-durable-reconciliation-truthfulness-design.md`

## Global Constraints

- No network, credentials, account access, provider writes, real trading, funds movement, paid provisioning, or cloud deployment.
- Preserve OFF defaults, account/risk gates, reducing exits, fail-closed behavior, and independent emergency halt/cancel/expiry access.
- Add no UI controls or editable parameters.
- Keep all versions below 1.0 and synchronize durable release artifacts only after tests pass.

## Review Focus

- Renamed fill identity with unchanged economics must fail every reconciliation claim before mutation.
- Archived and active order fills must compare identically despite checkpoint sorting.
- Missing, extra, duplicate, wrong-side, wrong-product, and numeric-detail disagreements must fail closed.
- Emergency halt/cancel/expiry must remain available during existing portfolio disagreement.
- Headless admission rows and diagnostic-changing forward database mutations must be read-only and visibly invalid.

---

### Task 1: Exact linked fill identity reconciliation

**Files:**
- Modify: `tests/test_execution_portfolio.py`
- Modify: `nexus/execution_portfolio.py`
- Modify: `nexus/durable_orders.py`

**Interfaces:**
- Produces: `execution_portfolio.compare_linked(portfolio, gateway) -> None`
- Consumes: `gateway.account`, `gateway.executions`, and `gateway.lookup(order_id)`.

- [ ] **Step 1: Write the failing real-SQLite regressions**

Add a test that changes only the final retained fill event ID in the order command. Assert cold recovery, session snapshot, exact duplicate acknowledgement, and a new fill all raise `EXECUTION_PORTFOLIO_DISAGREEMENT`; both database bytes remain unchanged. Add table-driven direct comparison cases for missing, extra, duplicate/conflicting, side/product, quantity, price, and fee disagreement, including an archived order.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_execution_portfolio.py -q`

Expected: the renamed-fill regression demonstrates the current false reconciliation.

- [ ] **Step 3: Implement exact keyed comparison**

Add `compare_linked(portfolio, gateway)` which calls existing aggregate `compare`, builds a bounded expected key map from replayed executions and immutable orders, streams the `execution_fills AS f JOIN ledger AS l` projection, and rejects any missing, extra, duplicate, or unequal row with `EXECUTION_PORTFOLIO_DISAGREEMENT`.

Invoke it for cold `compare_portfolio` recovery, session snapshots, ordinary linked mutation prechecks, and immediately after an applied linked fill. Keep `emergency_halt`, `cancel`, `expire_order`, and `expire_due` outside the ordinary reconciliation precheck.

- [ ] **Step 4: Verify GREEN and safety exceptions**

Run: `python -m pytest tests/test_execution_portfolio.py tests/test_order_recovery_read_only.py tests/test_emergency_storage.py -q`

Expected: all pass; existing disagreement still permits durable emergency halt.

### Task 2: Retained exchange-admission head integrity

**Files:**
- Modify: `tests/test_forward_evidence.py`
- Modify: `tests/test_forward_trial_review.py`
- Modify: `nexus/forward_evidence.py`

**Interfaces:**
- Preserves: `review_exchange_admissions(db, session)` return shape.
- Adds failure reason: `EXCHANGE_ADMISSION_HEAD_MISSING`.

- [ ] **Step 1: Write failing helper and integration regressions**

Prove `(no head, zero rows)` remains legacy `None`, while `(no head, retained rows)` returns failed integrity. From the real crash-tail fixture, delete only the head and assert Trial Review and cold retained-feed baseline fail with their existing fixed public errors without modifying either database.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_forward_evidence.py tests/test_forward_trial_review.py -q`

Expected: headless retained rows are currently treated as absent legacy evidence.

- [ ] **Step 3: Implement the missing-head distinction**

Count bounded admission rows before returning legacy absence. Return `None` only for zero rows and no head. Return the exact existing result schema with `integrity_ok=False` and reason `EXCHANGE_ADMISSION_HEAD_MISSING` when rows remain.

- [ ] **Step 4: Verify GREEN**

Run the Task 2 test command again; all pass.

### Task 3: Bind forward-trial snapshot to diagnostic source bytes

**Files:**
- Modify: `tests/test_forward_trial_review.py`
- Modify: `nexus/forward_trial_review.py`

**Interfaces:**
- Preserves the public review schema.
- Changes only the canonical input to `evidence_snapshot_sha256`.

- [ ] **Step 1: Write the failing mutation regression**

Review a complete real fixture, mutate one retained forward-event ordinal without changing count/head metadata, and review again. Assert diagnostic completeness changes, the evidence snapshot digest also changes, repeated review is stable, and the database remains byte-identical during each review.

- [ ] **Step 2: Verify RED**

Run the single new test and observe the digest remain unchanged.

- [ ] **Step 3: Include the bounded main database fingerprint**

Pass the already captured/rechecked main database SHA-256 into the bounded review function and include it in the canonical evidence snapshot payload. Do not add a second read or expose paths/private content.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_forward_trial_review.py tests/test_trial_readiness.py tests/test_release_trials.py -q`

Expected: all pass.

### Task 4: Version, evidence, packaging, and durable documentation

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`, generated `ROADMAP.md`
- Modify: `AGENTS.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Create: `validation/release_trials_0_340_425.json`

- [ ] **Step 1: Synchronize prerelease version `0.340.425` and narrative boundaries**

Document the exact local fixes and explicitly retain 38% weighted implementation, 0/9 accepted stages, and all external authority false.

- [ ] **Step 2: Regenerate and verify saved evidence**

Run all required bounded release profiles and the saved-evidence verifier against the exact source tree.

- [ ] **Step 3: Run focused, complete, packaging, syntax, and security verification**

Run affected tests, deterministic full shards, compileall, pip check, Portal JavaScript syntax, CI inventory, wheel build/clean import, and repository secret/history scans available locally.

- [ ] **Step 4: Independent review and exact-head publication**

Obtain a fresh whole-branch review, fix findings test-first, reverify, commit coherently, confirm the remote expected head, and update PR #136 with a non-forced expected-head lease. Observe hosted CI without cancelling or claiming pending jobs.
