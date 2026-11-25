# Post-latch Durable Halt Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a same-inode explicit journal reset after the first cold replay from producing a false `VERIFIED` standalone emergency cut.

**Architecture:** Keep the existing latch-first and durable-halt sequence, then independently replay the exact journal again after final latch reassertion. Any missing, malformed, unhalted, replaced, or unreadable final journal state downgrades the result to `UNCONFIRMED` while the independent latch remains asserted.

**Tech Stack:** Python 3.10+, SQLite-backed `DurableOrderGateway`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-25-post-latch-halt-replay-design.md`

## Global Constraints

- No network, broker, credential, funding, live-order, reset, or restart authority.
- Preserve the public emergency-result schema and OFF-by-default engagement.
- Do not hold a journal lock across result publication or auto-retry uncertain work.
- Keep versions below 1.0 and all acceptance/authority flags false.

## Review Focus

- Same-inode reset after the first cold replay must retain the latch and report `UNCONFIRMED`.
- An unchanged halted journal must still report `VERIFIED`.
- A final replay exception or malformed report must fail closed without escaping the command boundary.
- A final latch persistence/observation fault must not reuse the first replay.
- A different halted journal between replays must remain `UNCONFIRMED`.
- Existing data-root replacement diagnostics must retain their current semantics.
- No final check may invoke reset, provider I/O, or a second halt mutation.

---

### Task 1: Pin the same-inode reset regression

**Files:**
- Modify: `tests/test_emergency_cut_tool.py`

**Interfaces:**
- Consumes: `activate(data_root)` and the sanctioned `DurableOrderGateway.reset()` plus `emergency_intent.clear()` sequence.
- Produces: a real-storage regression proving final durable state, latch state, and conservative public output.

- [ ] **Step 1: Write the failing test**

Add `test_same_inode_reset_after_first_replay_cannot_report_verified` that wraps only the second latch persist, performs the real reset/clear/persist sequence, and asserts: unchanged journal inode, retained latch, cold `halted=False`, public `UNCONFIRMED`, `durable_order_halt_confirmed=False`, and `halted=True` from the latch.

Add regressions proving a fault after the second latch is durably present cannot skip the final replay, and a different already-halted journal between replays cannot satisfy identity continuity.

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. ../nexus-next-367/.venv/bin/python -m pytest -q tests/test_emergency_cut_tool.py::test_same_inode_reset_after_first_replay_cannot_report_verified`

Expected: FAIL because the current result is `VERIFIED` with `durable_order_halt_confirmed=True` even though cold replay is unhalted.

### Task 2: Revalidate durable halt after latch reassertion

**Files:**
- Modify: `nexus/emergency_cut_tool.py`
- Test: `tests/test_emergency_cut_tool.py`

**Interfaces:**
- Consumes: `emergency_halt_replayed(path, warm_result) -> bool`.
- Produces: final `durable_halt` derived from a second fresh replay after final latch persist.

- [ ] **Step 1: Implement the minimal final replay**

Retain the exact warm result, keep the public `durable_halt` false until after final latch identity is captured, and assign it from a second `emergency_halt_replayed` call only when the root remains pinned and the same regular single-link journal object survived both replay samples. Do not call `emergency_halt`, reset, or retry again.

- [ ] **Step 2: Run focused tests**

Run: `PYTHONPATH=. ../nexus-next-367/.venv/bin/python -m pytest -q tests/test_emergency_cut_tool.py`

Expected: all tests pass.

### Task 3: Synchronize prerelease evidence and documentation

**Files:**
- Modify: `pyproject.toml`
- Modify: `nexus/app.py`
- Modify: `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`
- Modify: `ROADMAP.md`
- Modify: `AGENTS.md`
- Modify: `LAUNCH_DRIVE.md`
- Modify: `VERSIONING.md`
- Create: `validation/release_trials_0_340_405.json`

**Interfaces:**
- Consumes: the verified emergency-cut behavior and existing 16-profile bounded release runner.
- Produces: one synchronized `0.340.405` prerelease increment with current-source saved evidence.

- [ ] **Step 1: Update version and truthful roadmap text**

Describe sampled post-latch durable replay, the retained non-atomic limitation, unchanged UI/risk/authority boundaries, and unchanged 38% weighted estimate / 0 of 9 accepted stages.

- [ ] **Step 2: Regenerate and verify saved evidence**

Run `python -m nexus.release_trials --expected-version 0.340.405` into a new report, then `python -m nexus.release_evidence ... --expected-version 0.340.405`.

- [ ] **Step 3: Run complete verification**

Run the focused suite, both deterministic pytest shards, compile, dependency, roadmap, Portal JavaScript, CI inventory, wheel, public-history audit, diff, worktree, and secret-pattern checks.

- [ ] **Step 4: Commit one reviewed increment**

Commit message: `fix: revalidate final emergency halt state`
