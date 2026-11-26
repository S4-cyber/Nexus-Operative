# Emergency Final Journal Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the standalone emergency command from retaining verified durable-halt evidence after its replayed journal is replaced during final result sampling.

**Architecture:** Extend the existing sampled identity protocol with one final read-only comparison against the already saved journal identity, conditioned on the original data root still being pinned. Keep all action, retry, reset, and historical displaced-root behavior unchanged.

**Tech Stack:** Python 3.10+, SQLite, pytest, existing Nexus emergency identity helpers.

**Spec:** `docs/superpowers/specs/2026-11-26-emergency-final-journal-identity-design.md`

## Global Constraints

- No broker/provider/network/credential/funding/cloud/live operations.
- No retry, second halt, reset, writer lock, UI control, or editable parameter.
- Mismatch or unavailable final observation fails closed.
- Keep versions below 1.0 and preserve every existing authority flag.

## Review Focus

- Replacement after the second replay but before return must clear durable confirmation.
- A stable journal must remain verified.
- A displaced root must preserve its existing historical durable flag while remaining unverified.
- Observation denial on a still-pinned root must fail closed without retry.
- The replacement journal must remain unmodified and cold-read as unhalted.

---

### Task 1: Final emergency journal identity fence

**Files:**
- Modify: `tests/test_emergency_cut_tool.py`
- Modify: `nexus/emergency_cut_tool.py`

**Interfaces:**
- Consumes: `_same_order(order_path, identity) -> bool | None` and the existing `root_stable`, `durable_halt`, and `order_identity` samples.
- Produces: unchanged `activate(data_root=None) -> dict` with stricter `durable_order_halt_confirmed` evidence.

- [ ] **Step 1: Write the failing real-SQLite regression**

Add `test_final_latch_sample_journal_replacement_cannot_remain_verified`.
Replace the public journal with an initialized unhalted journal after the
second `_same_intent` sample and assert `UNCONFIRMED`, durable confirmation
false, intent/root evidence true, and cold replacement state unhalted with zero
commands.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_emergency_cut_tool.py::test_final_latch_sample_journal_replacement_cannot_remain_verified`

Expected: FAIL because the current report is `VERIFIED` with durable
confirmation true.

- [ ] **Step 3: Implement the minimal read-only fence**

In `activate`, after final latch/root sampling, require
`_same_order(order_path, order_identity) is True` to retain `durable_halt`
when `root_stable` is true. Do not call the halt or replay again.

- [ ] **Step 4: Verify GREEN and focused compatibility**

Run the new regression, `tests/test_emergency_cut_tool.py`,
`tests/test_emergency_lane.py`, `tests/test_emergency_storage.py`, and
`tests/test_risk_gate.py`.

- [ ] **Step 5: Commit the behavior and test**

Commit only the design, plan, regression, and production fence before release
metadata/evidence synchronization.

### Task 2: Release synchronization and exact-source verification

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`, `ROADMAP.md`
- Modify: `AGENTS.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Create: `validation/release_trials_0_340_422.json`

**Interfaces:**
- Consumes: Task 1's unchanged public emergency report schema.
- Produces: synchronized prerelease `0.340.422` and exact-source offline evidence.

- [ ] **Step 1: Synchronize version and durable safety wording**

Document the sampled local scope, preserved UI/safety boundaries, unchanged
38% weighted estimate, and 0/9 accepted stages without claiming native or
broker acceptance.

- [ ] **Step 2: Run focused release/version/roadmap checks**

Verify deterministic `ROADMAP.md` generation and synchronized application,
package, and installer versions.

- [ ] **Step 3: Regenerate and verify all 16 release profiles**

Bind the saved evidence to the final exact source fingerprint and record its
SHA-256 without private market/account data.

- [ ] **Step 4: Run complete sharded regression, packaging, dependency and public-artifact checks**

Require both complete shards green, wheel clean-install safety flags false,
clean dependency audit, Portal/controller checks, and zero public-artifact
findings.

- [ ] **Step 5: Publish with an expected-head lease**

Update the existing consolidated draft PR only if its hosted head remains the
expected v0.340.421 commit; leave fresh hosted CI unclaimed until observed.
