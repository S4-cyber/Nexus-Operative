# Final Direct BUY Cut Resample Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refuse a direct paper BUY when the emergency latch is asserted during protected quote revalidation.

**Architecture:** Preserve the existing pre-revalidation latch sample for error priority. Add one final sample immediately after the pure quote check and before the first accounting mutation.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-11-26-final-direct-buy-cut-resample-design.md`

## Global Constraints

- No external request, retry, credential access, funds movement or live action.
- Preserve OFF defaults, limits, account checks, reducing exits and fail-closed rollback.
- No UI controls or editable parameters.
- Keep exact weighted progress at 38% and accepted stages at 0/9.

## Review Focus

- Cut asserted during second quote validation: refuse with no mutation.
- Cut already active with a stale quote: emergency refusal remains higher priority.
- Quote expiry without a cut: retain `Quote too old` and no mutation.
- Fresh quote with no cut: retain successful bounded paper accounting.
- Reducing SELL exits: remain outside the entry-only latch fence.

---

### Task 1: Restore the final emergency fence

**Files:**
- Modify: `nexus/paper_portfolio.py`
- Modify: `tests/test_tier_permissions.py`

**Interfaces:**
- Consumes: `check_quote(product, quote) -> tuple[Decimal, Decimal]` and optional `cut_requested() -> bool`.
- Produces: `buy(...)` refuses a cut asserted during protected quote validation.

- [ ] **Step 1: Write the failing regression**

Add `test_direct_buy_cut_during_quote_revalidation_rolls_back`. Wrap the real
`check_quote`; on its second invocation publish the durable emergency intent.
Assert HTTP 409 `EMERGENCY_CUT_INTERRUPTED_TRADE`, no position and one INIT
event.

- [ ] **Step 2: Verify RED**

Run the single regression and require the current implementation to return 200
with a committed position.

- [ ] **Step 3: Implement the minimum final resample**

After the protected `check_quote` returns its ask, call `cut_requested` again
when supplied and raise the existing emergency error before reading capital or
mutating accounting.

- [ ] **Step 4: Verify GREEN and adjacent ordering**

Run the new regression, the existing post-writer cut test, quote-expiry test,
tier ordering, reducing-exit tests, paper integrity and transaction seals.

- [ ] **Step 5: Commit the behavioral change**

Commit the test, production change, design and plan before evidence generation.

### Task 2: Synchronize and verify v0.340.420

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`, `ROADMAP.md`
- Modify: `AGENTS.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Create: `validation/release_trials_0_340_420.json`

**Interfaces:**
- Consumes: verified Task 1 behavior.
- Produces: synchronized prerelease metadata, roadmap and exact-source offline evidence.

- [ ] **Step 1: Synchronize version and durable safety boundaries**
- [ ] **Step 2: Generate and verify all bounded offline release profiles**
- [ ] **Step 3: Run both complete deterministic shards, packaging, compile, browser syntax and security/audit checks**
- [ ] **Step 4: Review and commit the exact tested tree**
- [ ] **Step 5: Publish only if PR #136 still has the recovered expected head**
