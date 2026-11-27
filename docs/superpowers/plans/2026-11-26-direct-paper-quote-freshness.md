# Direct Paper Quote Freshness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refuse a direct paper BUY when its captured public quote expires while waiting for durable writer reservations.

**Architecture:** Preserve the early quote check, then re-run the existing pure `check_quote` validation after the portfolio and tier-settings writers are held and after the independent emergency sample. Use the second check's ask for all accounting.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-11-26-direct-paper-quote-freshness-design.md`

## Global Constraints

- No external request, retry, credential access, funds movement or live action.
- Preserve OFF defaults, limits, account checks, fail-closed rollback, reducing exits and independent emergency-stop access.
- No UI controls or editable parameters.
- Keep exact weighted progress at 38% and accepted stages at 0/9.

## Review Focus

- Quote crosses the ten-second age boundary while both writers are held: reject with no mutation.
- Emergency cut and quote expiry coincide: emergency refusal remains higher priority.
- A still-fresh captured quote commits using its revalidated ask.
- Tier OFF ordering remains serialized by the existing settings writer guard.
- Revalidation performs no second market fetch or provider action.

---

### Task 1: Protected direct-quote freshness check

**Files:**
- Modify: `nexus/paper_portfolio.py`
- Modify: `tests/test_tier_permissions.py`

**Interfaces:**
- Consumes: `check_quote(product: str, quote: dict) -> tuple[Decimal, Decimal]`.
- Produces: `buy(...)` uses a post-writer revalidated ask or rolls back.

- [ ] **Step 1: Write the failing regression**

Add `test_direct_buy_quote_expiring_during_writer_wait_rolls_back` using a
synthetic `paper_portfolio.time.time` clock that advances beyond ten seconds
inside the protected `verify_ledger` call. Assert API 422 `Quote too old`, no
position, and the initialization-only ledger count.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest -q tests/test_tier_permissions.py::test_direct_buy_quote_expiring_during_writer_wait_rolls_back`

Expected: FAIL because the current route returns 200 and commits the BUY.

- [ ] **Step 3: Implement the minimum revalidation**

Keep the initial `check_quote`. After both writer reservations and the emergency
sample, call `check_quote` again and assign its ask to the accounting path.

- [ ] **Step 4: Verify GREEN and adjacent safety**

Run the new regression plus direct BUY tier ordering, emergency-cut ordering,
reducing-exit, paper-integrity and transaction-seal tests.

- [ ] **Step 5: Commit the behavioral change**

Commit the test and minimal production fix before metadata generation.

### Task 2: Release synchronization and evidence

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`, `ROADMAP.md`
- Modify: `AGENTS.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Create: `validation/release_trials_0_340_419.json`

**Interfaces:**
- Consumes: verified Task 1 behavior.
- Produces: synchronized v0.340.419 source, installer metadata, roadmap and saved evidence.

- [ ] **Step 1: Synchronize v0.340.419 metadata and durable boundaries**

Record the protected quote revalidation without changing progress or acceptance.

- [ ] **Step 2: Generate all bounded offline profiles and saved evidence**

Run the project release-trial generator and exact-source evidence verifier.

- [ ] **Step 3: Run complete deterministic shards, security/audit suites, roadmap/version contracts, compile and wheel inspection**

Require zero failures; report skips and warnings exactly.

- [ ] **Step 4: Audit the staged public tree and introduced lines for private artifacts or credential signatures**

Do not claim unavailable scanners.

- [ ] **Step 5: Review, commit, publish with an expected-head lease, and inspect exact-head hosted CI**

Update PR #136 only if its head still equals the recovered expected parent.
