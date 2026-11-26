# Windows Optional Birth-Time Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop rejecting stable Windows distributions solely because optional
Python 3.12 birth-time metadata is absent from a path or descriptor sample.

**Architecture:** Preserve all mandatory cross-source identity fields and all
same-source stability/digest protections. Compare Windows birth time only when
both samples expose exact integer values; reject malformed exposed values.

**Tech Stack:** Python 3.10+, pathlib/os stat APIs, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-26-windows-optional-birthtime-identity-design.md`

## Global Constraints

- No live action, credential access, network routing, funding or paid resource.
- Preserve regular-file, single-link, directory, retained-handle and double-
  digest protections.
- Do not mark Windows or installer acceptance without an observed hosted pass.
- No UI control or editable parameter changes.
- Keep exact weighted progress at 38% and accepted stages at 0/9.

## Review Focus

- Optional field absence on either Windows sample.
- Malformed or unequal present birth-time values.
- Mandatory identity mismatches and same-source mutation detection.
- Error classification for a changed distribution after a successful manifest.
- No regression to POSIX change-time comparison.

---

### Task 1: Reproduce and repair optional Windows birth-time handling

**Files:**
- Modify: `tests/test_artifact_manifest.py`
- Modify: `nexus/artifact_manifest.py`
- Create: `docs/superpowers/specs/2026-11-26-windows-optional-birthtime-identity-design.md`
- Create: `docs/superpowers/plans/2026-11-26-windows-optional-birthtime-identity.md`

**Interfaces:**
- Consumes: path and open-descriptor stat samples from the same retained artifact.
- Produces: a strict identity verdict that tolerates only legitimate optional
  birth-time absence.

- [ ] **Step 1: Write the failing component regression**

Model a Windows path sample with `st_birthtime_ns` and an open-descriptor
sample without it. Require complete manifest verification to succeed while
their `st_ctime_ns` values differ.

- [ ] **Step 2: Verify RED**

Run the new regression and require the current verifier to reject the stable
distribution.

- [ ] **Step 3: Implement the minimum compatibility rule**

Require the mandatory identity fields. Reject malformed exposed birth-time
values. Compare birth times only when both exact integer values are available;
otherwise accept the cross-source identity without comparing `st_ctime_ns`.

- [ ] **Step 4: Verify GREEN and adjacent security cases**

Run the new regression plus all artifact-manifest and Windows owner-profile
review tests. Require changed identity, replacement, rewrite, directory-swap
and digest cases to remain green.

- [ ] **Step 5: Commit the behavioral change**

Commit the test, production repair, design and plan before release evidence.

### Task 2: Synchronize and verify v0.340.421

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`, `ROADMAP.md`
- Modify: `AGENTS.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Create: `validation/release_trials_0_340_421.json`

**Interfaces:**
- Consumes: verified Task 1 behavior.
- Produces: synchronized prerelease metadata, roadmap and exact-source offline
  evidence.

- [ ] **Step 1: Synchronize version and durable compatibility boundaries**
- [ ] **Step 2: Generate and verify all bounded offline release profiles**
- [ ] **Step 3: Run both complete deterministic shards, packaging, compile,
  browser syntax and security/audit checks**
- [ ] **Step 4: Review and commit the exact tested tree**
- [ ] **Step 5: Publish only if PR #136 still has the recovered expected head**

Expected result: all local verification is green; hosted Windows acceptance
remains pending until an exact v0.340.421 Windows workflow completes.
