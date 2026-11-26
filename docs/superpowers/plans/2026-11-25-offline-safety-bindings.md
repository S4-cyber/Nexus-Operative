# Offline Safety Bindings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close two independent fail-closed gaps: process-local substitution of the emergency-latch reader during persisted intent reconciliation, and polymorphic supplied financial evidence that can forge a passing disconnected funding projection.

**Architecture:** Bind the exact emergency reader at module import and recheck its public slot at every existing latch sample. At the disconnected projection boundary, require exact built-in containers, keys, scope strings, timestamps, and supported numeric scalars before normalization. Keep both changes offline, deterministic, and authority-free.

**Tech Stack:** Python 3.10+, pytest, existing Nexus durable reconciliation and release-evidence helpers.

**Spec:** `AGENTS.md`, `ROADMAP.md`, `LAUNCH_DRIVE.md`, and `VERSIONING.md` release and safety constraints at v0.340.400.

## Global Constraints

- Base every edit on hosted PR #121 head `2ca379ad15bdb3aa2c91801c5ccb235493311ee9` and publish only with an expected-head lease.
- Do not touch `nexus/paper_credentials.py` or the active saved-credential sprint.
- Keep every broker, funding, live, cloud, spend, and release authority flag false; no provider or broker request is permitted.
- Preserve OFF-by-default engagement, bounded limits, account checks, reducing exits, fail-closed behavior, and independent emergency-stop access.
- Add no UI control or editable parameter; preserve seven beginner tabs and black/bright-purple bottom-right hover help.
- Keep the roadmap estimate at exact weighted 38%, displayed about 40%, 30–45% low-confidence range, and 0/9 independently accepted stages.

## Review Focus

- A substituted emergency reader must fail before the fake executes, including substitution after reconciliation has already begun.
- Exact built-in strings, integers, floats, and `Decimal` values remain accepted; booleans and subclasses must be rejected before conversion hooks, mapping hooks, or equality hooks execute.
- Rejected supplied evidence must not yield a partial passing projection or cash-path report.
- The emergency writer remains lock-independent and all request/order journals remain unchanged by rejected reconciliation.

### Task 1: Bind the emergency-latch reader implementation

**Files:**
- Modify: `nexus/forward_broker_intent_review.py`
- Test: `tests/test_forward_broker_intent_review.py`

- [ ] Write failing entry-time and mid-reconciliation substitution regressions.
- [ ] Run the focused tests and record forged-clear acceptance or fake execution.
- [ ] Bind the exact reader and reject a changed public slot before direct invocation.
- [ ] Run the complete forward broker-intent review test file.

### Task 2: Require exact supplied funding evidence types

**Files:**
- Modify: `nexus/broker_fee_projection.py`
- Test: `tests/test_broker_fee_projection.py`
- Test: `tests/test_broker_cash_path.py`

- [ ] Write failing regressions for polymorphic rows, balance mappings, scope strings, and financial scalars.
- [ ] Run the focused tests and record the forged passing projection/cash path.
- [ ] Add narrow projection-local exact-type validation before normalization.
- [ ] Run the projection and cash-path suites.

### Task 3: Synchronize v0.340.401 and verify the increment

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`, `nexus/release_roadmap.py`
- Modify: `AGENTS.md`, `ROADMAP.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Create: `validation/release_trials_0_340_401.json`

- [ ] Synchronize pre-1.0 version metadata and bounded release notes without changing roadmap acceptance.
- [ ] Regenerate deterministic `ROADMAP.md` and all sixteen offline profiles.
- [ ] Verify focused suites, complete regression, saved evidence, compilation, dependency integrity, wheel metadata, UI controller smokes, and public-artifact/secret scans.
- [ ] Obtain independent whole-branch review, fix Critical/Important findings with RED/GREEN regressions, then repeat affected verification.
- [ ] Recheck hosted base/head leases, publish a review branch and draft stacked PR, and inspect exact-head CI without claiming unobserved Windows acceptance.
