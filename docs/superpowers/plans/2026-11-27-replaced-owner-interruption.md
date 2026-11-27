# Replaced-Owner Interruption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separately confirmed, durable no-provider reconciliation path for a pending paper POST reservation whose original local owner marker was replaced.

**Architecture:** Extend the existing provider-budget hash chain with one distinct interruption event, then wrap it in a file-identity-bound operator command that cold-replays lost acknowledgements. Compose its disposable fixture and the v0.340.429 negative guards into the mandatory release evidence chain.

**Tech Stack:** Python 3, SQLite hash-chain journals, pytest, existing release-profile composition.

**Spec:** `docs/superpowers/specs/2026-11-27-replaced-owner-interruption-design.md`

## Global Constraints

- Paper-only and OFF by default; no provider, vault, funding, live or cloud action.
- No retry, rearm, owner repair, automatic continuation or cooldown shortening.
- Existing ordinary interruption and independent emergency-stop semantics remain strict.
- No UI control or parameter changes; preserve seven tabs and bottom-right hover help.
- Keep version below 1.0 and roadmap baseline at 38% weighted, 0/9 accepted.

## Review Focus

- A same-owner caller must not use the replaced-owner path.
- Stale head/token or missing confirmation must leave journal bytes unchanged.
- Lost append acknowledgement must cold-replay exactly once without another append.
- Owner/journal replacement or mutation during receipt replay must fail closed.
- Release evidence must reject missing, false, type-confused, or authority-promoted fields.

---

### Task 1: Replaced-owner hash-chain event

**Files:**
- Modify: `tests/test_paper_provider_post_budget.py`
- Modify: `nexus/paper_provider_post_budget.py`

**Interfaces:**
- Consumes: `PaperProviderPostBudget.ownership()`, pending token/head and retained `owner_sha256`.
- Produces: `interrupt_replaced_owner(...)` and `replaced_owner_interruption_receipt()`.

- [ ] Write failing tests for replaced-owner success, same-owner refusal, stale inputs, confirmations, history/cooldown, and cold receipt schema.
- [ ] Run focused tests and verify the expected missing-method/schema failures.
- [ ] Add the exact event parser, method and read-only receipt with no change to ordinary `interrupt()`.
- [ ] Run the complete provider POST-budget tests and verify green.

### Task 2: Bounded operator interruption command

**Files:**
- Create: `tests/test_paper_provider_post_interruption.py`
- Create: `nexus/paper_provider_post_interruption.py`

**Interfaces:**
- Consumes: Task 1 methods and existing SQLite identity/fingerprint guards.
- Produces: `interrupt(path, expected_head, expected_token, ...)` and `fixture_report()`.

- [ ] Write failing tests for exact success, ordinary-path refusal, lost acknowledgement, fabricated/uncommitted return, journal replacement/mutation, CLI privacy, and false authority.
- [ ] Run the new tests and verify import/API failures.
- [ ] Implement a one-append command with exact preflight, current-owner lease, cold receipt replay, and create-only private output behavior.
- [ ] Run the new module tests and Task 1 tests together.

### Task 3: Mandatory saved-evidence composition

**Files:**
- Modify: `nexus/alpaca_paper_submission.py`
- Modify: `nexus/paper_transport_uncertainty_matrix.py`
- Modify: `nexus/release_trials.py`
- Modify: `tests/test_alpaca_paper_submission.py`
- Modify: `tests/test_paper_transport_uncertainty_matrix.py`
- Modify: `tests/test_release_trials.py`

**Interfaces:**
- Consumes: Task 2 `fixture_report()` and existing submission fixture.
- Produces: exact required booleans for asset types, final owner reproof, zero POST, and replaced-owner interruption.

- [ ] Write failing exact-schema and false-field tests in component, matrix and release validation.
- [ ] Run focused tests and verify missing-field/schema failures.
- [ ] Add disposable probes and require them in matrix and pacing release validation.
- [ ] Run all focused broker/release evidence tests.

### Task 4: Version, durable roadmap and regenerated evidence

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`
- Modify: `AGENTS.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`, `nexus/release_roadmap.py`, `ROADMAP.md`
- Create: `validation/release_trials_0_340_430.json`

**Interfaces:**
- Consumes: verified Tasks 1–3.
- Produces: synchronized v0.340.430 package/application/installer/docs/evidence.

- [ ] Update all version surfaces to 0.340.430 and describe only observed bounded evidence.
- [ ] Regenerate ROADMAP deterministically and all 16 bounded profiles create-only.
- [ ] Independently verify saved evidence against the exact source fingerprint.
- [ ] Run full regression shards, package/install checks, secret scan, and independent review before commit/publication.
