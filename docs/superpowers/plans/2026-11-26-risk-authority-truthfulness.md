# Risk and Authority Truthfulness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent stale direct-paper exits and contradictory admission-authority summaries without adding external authority.

**Architecture:** Reuse `check_quote()` on the same captured SELL observation at the final pre-mutation boundary. Add one strict authority-declaration reviewer and derive summary claims only from its reviewed rows.

**Tech Stack:** Python standard library, SQLite, FastAPI route inventory, pytest.

**Spec:** `docs/superpowers/specs/2026-11-26-risk-authority-truthfulness-design.md`

## Global Constraints

- Version stays below 1.0; every external authority flag remains false.
- No quote refetch, network access, credential access, live order or funds movement.
- Engagement and optional AI remain OFF by default.
- Reducing exits remain available during a cut when their supplied quote is valid.
- No UI control or editable parameter changes.
- Progress remains exact 38% weighted, 30-45% low confidence, 0/9 accepted.

## Review Focus

- Quote fresh before ledger audit but stale after it must not mutate accounting.
- Final quote validation must use the captured quote, never a provider callback.
- Authority rows with `automatic_start` or `automatic_resume` true must fail closed.
- Sensitive OFF/NOT_APPROVED defaults must be exact.
- Duplicate, missing, malformed or unknown authority declarations must fail closed.

---

### Task 1: Final direct SELL quote fence

**Files:**
- Modify: `tests/test_paper_portfolio.py`
- Modify: `nexus/paper_portfolio.py`

**Interfaces:**
- Consumes: `check_quote(product, quote) -> (bid, ask)` and `verify_ledger(db) -> Decimal`.
- Produces: `sell(product, quote)` using a post-audit validated bid.

- [ ] **Step 1: Write the failing synthetic-clock regression**
- [ ] **Step 2: Run it and observe that the stale exit currently commits**
- [ ] **Step 3: Revalidate the captured quote after ledger audit and before mutation**
- [ ] **Step 4: Run the focused regression and paper-portfolio suite**
- [ ] **Step 5: Commit the verified task**

### Task 2: Exact authority declaration review

**Files:**
- Modify: `tests/test_authority_inventory.py`
- Modify: `nexus/authority_inventory.py`

**Interfaces:**
- Consumes: `AUTHORITIES` and the runtime `(method, path)` set.
- Produces: `_reviewed_authorities(available) -> list[dict]` with false authority fields.

- [ ] **Step 1: Write failing declaration mutation regressions**
- [ ] **Step 2: Run them and observe contradictory declarations are accepted**
- [ ] **Step 3: Add exact schema, inventory, route, automatic-behavior and reviewed-default validation**
- [ ] **Step 4: Derive summary defaults and automatic flags from reviewed rows**
- [ ] **Step 5: Run the authority and release-profile suites**
- [ ] **Step 6: Commit the verified task**

### Task 3: Release synchronization and evidence

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`, `ROADMAP.md`, `AGENTS.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Create: `validation/release_trials_0_340_424.json`

**Interfaces:**
- Consumes: green Tasks 1-2 behavior.
- Produces: synchronized prerelease v0.340.424 and exact-source saved evidence.

- [ ] **Step 1: Synchronize version and durable roadmap narrative**
- [ ] **Step 2: Regenerate and reverify all sixteen bounded release profiles**
- [ ] **Step 3: Run affected suites, both complete deterministic shards, packaging and privacy/security checks**
- [ ] **Step 4: Obtain independent whole-branch review and resolve important findings with RED/GREEN evidence**
- [ ] **Step 5: Recheck hosted heads and publish only after v0.340.423 CI completes and the expected-head lease still holds**
