# Storage and Boundary Truthfulness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make linked portfolio mutation, clipped retained-trial coverage, and standalone emergency ordering fail closed at their exact durable boundaries.

**Architecture:** Extend existing identity guards rather than adding a new authority layer. Retain endpoint identities through coverage accounting and latch/portfolio identities through their existing sampled operations.

**Tech Stack:** Python 3.10+, SQLite, pytest, standard-library filesystem/stat and Decimal primitives.

**Spec:** `docs/superpowers/specs/2026-11-26-storage-boundary-truthfulness-design.md`

## Global Constraints

- No network, credentials, provider writes, real trading, funds movement, paid provisioning, deployment, or private retained market evidence.
- Preserve OFF defaults, limits, account checks, reducing exits, and journal-only safety writes.
- Add no UI control or editable parameter.
- Keep all versions below 1.0 and all external authority flags false.

## Review Focus

- Symlink and hardlink portfolio leaves must fail before either durable store changes.
- Public-path replacement after attachment must fail before commit.
- Safety-only cancel, expiry, and emergency halt must never attach the portfolio.
- A boundary-support quote execution must block incident-free trial acceptance without an exact reconciliation bundle.
- Latch identity loss between the first sample and the halt must prevent `VERIFIED` without retrying the halt.

---

### Task 1: Bind ordinary linked-portfolio writes to the declared leaf

**Files:**
- Modify: `tests/test_execution_portfolio.py`
- Modify: `nexus/execution_portfolio.py`

**Interfaces:**
- Preserves: `attach(db, path) -> AttachedPortfolio`
- Produces: the same ongoing `AttachedPortfolio.require_current()` identity fence already used by read-only attachment.

- [x] Write real-SQLite symlink, hardlink, and post-attach replacement regressions and observe RED without any durable mutation.
- [x] Harden `attach()` with exact regular/single-link/resolved/attached-path checks and retained public-path identity.
- [x] Run the focused execution-portfolio and recovery suites GREEN.

### Task 2: Count coverage-support endpoint incidents

**Files:**
- Modify: `tests/test_forward_trial_review.py`
- Modify: `nexus/forward_trial_review.py`

**Interfaces:**
- Preserves: `review(...)` response schema.
- Produces: event, modeled-execution, and reconciliation counts that include endpoints of accepted clipped intervals.

- [x] Add a retained-public boundary quote with a modeled BUY just before `after`; observe false incident-free acceptance RED.
- [x] Derive accepted coverage-support endpoints after cross-session rejection and include their events/sessions in bounded incident and reconciliation accounting.
- [x] Run forward-trial and Trial Readiness suites GREEN.

### Task 3: Bind standalone emergency ordering to the original latch

**Files:**
- Modify: `tests/test_emergency_cut_tool.py`
- Modify: `nexus/emergency_cut_tool.py`

**Interfaces:**
- Preserves: `activate(data_root=None)` and fixed public result schema.
- Produces: `VERIFIED` only when the exact initially observed latch survives through the durable halt boundary.

- [x] Add a deterministic reset/clear interleaving after the first latch sample and observe false `VERIFIED` RED.
- [x] Recheck the initial latch identity immediately after the halt result; clear ordering evidence on any mismatch/unavailability without retry.
- [x] Run emergency-cut, emergency-storage, and emergency-lane suites GREEN.

### Task 4: Synchronize prerelease evidence and publish for review

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`, generated `ROADMAP.md`
- Modify: `AGENTS.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Create: `validation/release_trials_0_340_426.json`

- [x] Synchronize v0.340.426 with unchanged 38% / 0-of-9 acceptance boundaries.
- [x] Regenerate all required bounded profiles and independently verify saved evidence.
- [x] Run affected suites, complete deterministic shards, packaging, syntax, CI inventory, and available secret/artifact audits.
- [x] Obtain independent whole-branch review, correct findings test-first, and publish only with an exact expected-head non-forced lease after prior CI is terminal.
