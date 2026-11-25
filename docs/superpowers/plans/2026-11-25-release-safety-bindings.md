# Release Safety Bindings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close four independent pre-release fail-closed gaps in emergency-cut reconciliation, account/funding evidence normalization, sustained-trial evidence validation, and Windows distribution version binding.

**Architecture:** Keep each safeguard at its existing trust boundary: sample the independent cut after the final request-source check, reject polymorphic account scalars before normalization, bind the current forward fixture's exchange-admission counters in the release profile, and carry one package-derived expected version through release generation and verification. No broker, credential, UI, funds, live, or cloud path is enabled.

**Tech Stack:** Python 3.10+, pytest, GitHub Actions PowerShell, existing Nexus SQLite/evidence helpers.

**Spec:** `AGENTS.md`, `ROADMAP.md`, `LAUNCH_DRIVE.md`, and `VERSIONING.md` release and safety constraints at v0.340.398.

## Global Constraints

- Keep the version below 1.0 and every broker, funding, live, cloud, spend, and release authority flag false.
- Preserve OFF-by-default engagement, limits, account checks, reducing exits, fail-closed behavior, and independent emergency-stop access.
- Do not touch `nexus/paper_credentials.py` or the active saved-credential continuity sprint.
- Add no button or editable parameter; preserve seven beginner tabs and black/bright-purple bottom-right hover help.
- Emit only fixed, sanitized failure codes; never expose private account, credential, or retained market data.
- Keep the roadmap baseline at exact weighted 38%, displayed about 40%, 30–45% low-confidence range, and 0/9 accepted stages.

## Review Focus

- A cut appearing after the last pre-request sample must invalidate reconciliation before return; Task 1 adds the exact final-window regression.
- Mapping, string, numeric, and decimal subclasses must not influence account/funding normalization; Task 2 covers mapping hooks, enum equality, cash inflation, and hidden transfers.
- Admission counts must use exact built-in integers and match the fixed forward fixture; Task 3 mutates each field independently, including boolean impostors.
- A stale roadmap revision must fail both source tests and distribution commands even when generator and verifier agree with each other; Task 4 covers unit and workflow command bindings.
- Expected-version failures must occur before expensive profiles and remain redacted; Task 4 asserts early failure and fixed CLI output.

---

### Task 1: Close the final emergency-cut reconciliation window

**Files:**
- Modify: `nexus/forward_broker_intent_review.py`
- Test: `tests/test_forward_broker_intent_review.py`

**Interfaces:**
- Consumes: existing `_require_request_source(...)` and `_require_emergency_cut_clear(...)` guards.
- Produces: `reconcile_persisted(...)` that cannot return after a cut is published during its final request-source verification.

- [ ] **Step 1: Write the failing final-window cut regression**
- [ ] **Step 2: Run the single test and observe `reconcile_persisted()` incorrectly return**
- [ ] **Step 3: Recheck the independent cut immediately after the final request-source check without adding an order lock**
- [ ] **Step 4: Run the reconciliation file and verify pass**

### Task 2: Require built-in account/funding evidence scalars

**Files:**
- Modify: `nexus/alpaca_paper_inspection.py`
- Test: `tests/test_alpaca_paper_inspection.py`
- Test: `tests/test_broker_balance_review.py`
- Test: `tests/test_alpaca_paper_submission.py`

**Interfaces:**
- Consumes: `_account(raw)` normalization and downstream inspection/balance/submission callers.
- Produces: exact built-in mapping, key, enum, and numeric scalar validation before `contract.number(...)` or equality checks.

- [ ] **Step 1: Add failing subclass regressions for mapping hooks, enum forgery, cash inflation, hidden transfers, and zero-POST submission**
- [ ] **Step 2: Run focused tests and confirm the current normalizer accepts the forged inputs**
- [ ] **Step 3: Add narrow exact-type validation in `_account` without broad contract changes**
- [ ] **Step 4: Run all account, balance, and submission regressions**

### Task 3: Bind exchange-admission counters in mandatory release evidence

**Files:**
- Modify: `nexus/release_trials.py`
- Test: `tests/test_release_trials.py`

**Interfaces:**
- Consumes: the existing `forward` profile with 200 retained/in-window admissions and zero admission incidents.
- Produces: `_validate('forward', result)` rejection with `FORWARD_COVERAGE_EVIDENCE` for missing, polymorphic, or altered admission counters.

- [ ] **Step 1: Add independent mutations for all three admission counters**
- [ ] **Step 2: Run the focused validator test and observe the mutations pass**
- [ ] **Step 3: Add exact built-in integer/value assertions to the existing forward coverage evidence gate**
- [ ] **Step 4: Run the forward validation scope**

### Task 4: Bind package version through release generation and verification

**Files:**
- Modify: `nexus/release_trials.py`
- Modify: `nexus/release_evidence.py`
- Modify: `.github/workflows/research-safety-windows.yml`
- Modify: `.github/workflows/windows-release.yml`
- Test: `tests/test_release_trials.py`
- Test: `tests/test_release_evidence.py`
- Test: `tests/test_release_versioning.py`
- Test: `tests/test_research_safety_workflow.py`
- Test: `tests/test_windows_installer_workflow.py`

**Interfaces:**
- Consumes: package version from `pyproject.toml` and `nexus.release_roadmap.REVISION`.
- Produces: optional CLI `--expected-version VERSION` on generator and verifier; mismatch fails before profiles or artifact creation.

- [ ] **Step 1: Add failing revision synchronization and expected-version unit/workflow tests**
- [ ] **Step 2: Run focused tests and record the missing checks**
- [ ] **Step 3: Implement strict expected-version parsing/checks and pass `$version` in both Windows workflows**
- [ ] **Step 4: Run version, evidence, release-trial, and workflow tests**

### Task 5: Synchronize v0.340.400 and verify the release increment

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`, `nexus/release_roadmap.py`
- Modify: `AGENTS.md`, `ROADMAP.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Create: `validation/release_trials_0_340_400.json`
- Test: release, version, roadmap, workflow, packaging, and public-repository safety suites.

**Interfaces:**
- Consumes: Tasks 1–4 and existing deterministic roadmap/evidence generators.
- Produces: one pre-1.0 reviewable branch with synchronized metadata and exact-source saved evidence.

- [ ] **Step 1: Increment all app/package/installer/roadmap metadata to 0.340.400 and add bounded release notes**
- [ ] **Step 2: Regenerate deterministic `ROADMAP.md` and all 16 offline profiles**
- [ ] **Step 3: Verify saved evidence, focused suites, complete regression, compilation, dependency integrity, wheel metadata, and UI controller smokes**
- [ ] **Step 4: Audit current tree/history and introduced changes for public artifacts and high-confidence credential patterns**
- [ ] **Step 5: Commit, recheck the expected remote base/head lease, push the review branch, open a draft PR, and inspect exact-head CI**
