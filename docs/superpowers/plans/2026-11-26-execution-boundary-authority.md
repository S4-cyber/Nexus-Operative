# v0.340.429 Execution-Boundary Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure unsafe journal leaves, lost provider ownership and malformed asset flags cannot suppress a stop or cross the paper POST boundary.

**Architecture:** Reuse the emergency latch's sibling-file independence, the durable gateway's existing no-follow journal validation, and the provider budget's existing active-owner validator. Tighten only the paper submission asset normalization boundary and carry the original owner proof through the existing reservation replay.

**Tech Stack:** Python 3, pytest, SQLite-backed local journals, existing injected HTTP fixtures.

**Spec:** `docs/superpowers/specs/2026-11-26-execution-boundary-authority-design.md`

## Global Constraints

- Paper-only and OFF by default; no provider, credential, live, funding or cloud actions.
- Independent emergency stop stays unlocked and sampled last.
- No UI, dependency, retry, owner reacquisition or automatic pending-state repair.
- Keep version below 1.0 and the roadmap baseline at 38% weighted, 0/9 accepted.

## Review Focus

- Real symlink privileges unavailable: retain a portable injected latch-independence proof alongside the native symlink case.
- Owner loss after marker but before opener: assert zero POST and pending review rather than a fabricated terminal result.
- Owner loss during exception unwind: do not mask the pending state with owner reacquisition.
- JSON equality coercion: cover integer and decimal encodings for both eligibility flags.
- Final malformed asset sample: assert marker retention, zero actual POSTs and no re-arm.

---

### Task 1: Emergency latch independence

**Files:**
- Modify: `tests/test_emergency_cut_tool.py`
- Modify: `nexus/emergency_cut_tool.py`

**Interfaces:**
- Consumes: `emergency_intent.persist(order_path)` and `DurableOrderGateway(order_path).emergency_halt()`.
- Produces: unchanged `activate(data_root)` result schema with latch-backed `halted=true` for unsafe order leaves.

- [ ] **Step 1: Write failing symlink and in-flight runner tests**

Add tests proving a symlinked order leaf cannot prevent the sibling latch, cannot mutate its external target, and still stops a blocked runner after the real journal is restored.

- [ ] **Step 2: Verify RED**

Run: `pytest -q tests/test_emergency_cut_tool.py -k 'symlinked_order or symlink_leaf_during_inflight'`

Expected: failures showing no intent latch and `halted=false`.

- [ ] **Step 3: Remove only the pre-latch order symlink refusal**

Let the existing durable gateway reject the unsafe journal after the independent latch is published. Keep root fencing, both latch writes and the single durable attempt unchanged.

- [ ] **Step 4: Verify GREEN**

Run the focused tests and the complete `tests/test_emergency_cut_tool.py` file.

### Task 2: Exact asset schema

**Files:**
- Modify: `tests/test_alpaca_paper_submission.py`
- Modify: `nexus/alpaca_paper_submission.py`

**Interfaces:**
- Consumes: decoded wire asset dictionaries and the existing prepared IOC payload.
- Produces: the existing normalized asset dictionary only from exact object/text/boolean/numeric shapes.

- [ ] **Step 1: Write failing initial and final malformed-flag tests**

Cover `1`, `1.0`/`Decimal('1')`, and both `tradable` and `fractionable`; assert pre-reservation silence initially and lookup-only/`NOT_SENT` closure finally.

- [ ] **Step 2: Verify RED**

Run the new parameterized tests and confirm malformed values currently reach success or the wrong later branch.

- [ ] **Step 3: Tighten `_asset(raw, payload)` minimally**

Require `type(raw) is dict`, exact built-in strings for symbol/class/status,
exact booleans for both flags, and exact admitted scalar types for the three
numeric contract fields before calling `contract.number`.

- [ ] **Step 4: Verify GREEN**

Run the focused asset and complete broker submission suites.

### Task 3: Final provider-owner proof

**Files:**
- Modify: `tests/test_alpaca_paper_submission.py`
- Modify: `nexus/alpaca_paper_submission.py`

**Interfaces:**
- Consumes: the original `_PostOwnership`, pending token, request marker and existing stable journal replays.
- Produces: `_reservation_state_check(..., ownership=owner)` that proves owner continuity before and after its durable reads.

- [ ] **Step 1: Write failing injected and POSIX replacement tests**

Trigger owner validation loss during the final credential callback; assert no POST, lookup-only marker, exact pending token, no RESULT, and no re-arm. On POSIX, additionally replace the owner pathname with a byte-identical marker.

- [ ] **Step 2: Verify RED**

Run the new tests and confirm the current opener receives one POST.

- [ ] **Step 3: Extend the reservation state check**

Pass the original owner into `_reservation_state_check` and call the budget's existing owner proof before and after the stable request/budget reads. Do not acquire a replacement owner or finish the pending row after owner loss.

- [ ] **Step 4: Verify GREEN**

Run focused ownership tests followed by the full broker submission and provider-budget suites.

### Task 4: Release synchronization and verification

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`, generated `ROADMAP.md`
- Modify: `AGENTS.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Create: `validation/release_trials_0_340_429.json`

**Interfaces:**
- Consumes: green implementation and test evidence from Tasks 1–3.
- Produces: synchronized prerelease v0.340.429 and independently verifiable saved evidence.

- [ ] **Step 1: Synchronize version and durable roadmap narrative**

Describe only local fail-closed evidence; retain every external authority flag as false and do not change the progress baseline.

- [ ] **Step 2: Regenerate and independently verify all required release profiles**

Run the repository's documented release generation/verification commands and require 16/16 profiles.

- [ ] **Step 3: Run release, packaging, security and full deterministic suites**

Require focused suites, both exact deterministic shards, compilation, Portal/controller checks, wheel install/import, dependency integrity and public-artifact scanning.

- [ ] **Step 4: Review, commit and publish with an expected-head lease**

Require a clean diff review and exact tree binding. Publish only if PR #136 still points to `83d3eb8d23b9a3634f0d1ae35be57d49344c3386`; otherwise stop without overwriting.
