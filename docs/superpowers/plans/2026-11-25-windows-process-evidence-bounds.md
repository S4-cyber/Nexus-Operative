# Windows Process Evidence Bounds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the Windows installer smoke from hanging indefinitely on inherited output handles and stop publishing an unproved whole-process-tree containment claim.

**Architecture:** Keep the existing `System.Diagnostics.Process` launcher and ten installer phases, but give the combined stdout/stderr drain one fixed post-exit deadline. Treat a drain timeout as a failed phase, close owned readers, and publish the narrower true fact that output drains were bounded while explicitly retaining process-tree containment as false. Atomic launch into a Windows Job Object remains a separate native gate because immediate post-start assignment has an escape race.

**Tech Stack:** PowerShell 7, .NET `System.Diagnostics.Process`/`Task`, Python 3.11, pytest, GitHub Actions Windows runners.

**Spec:** `AGENTS.md`, `ROADMAP.md`, `LAUNCH_DRIVE.md`, and `VERSIONING.md` safety and release constraints at v0.340.403.

## Global Constraints

- Keep every broker, funding, live, cloud, spend, and release authority flag false and the version below 1.0.
- Preserve OFF-by-default engagement, limits, account checks, reducing exits, fail-closed behavior, and independent emergency-stop access.
- Preserve the ten phase labels, their 1–300 second bounds, exact-source/digest/session evidence, and always-retained diagnostics.
- Do not claim atomic descendant containment, clean-machine/native-window acceptance, or a Windows installer pass without observed exact-head hosted evidence.
- Add no button or editable parameter; preserve seven beginner tabs and black/bright-purple bottom-right hover help.
- Keep the roadmap estimate at exact weighted 38%, displayed about 40%, 30–45% low confidence, and 0/9 accepted stages.

## Review Focus

- A root process that exits while a descendant retains stdout/stderr must fail within the fixed drain grace rather than hang or record completion.
- A normal process whose two drain tasks finish within the grace must preserve output, exit-code, and phase-order behavior.
- A drain timeout must not produce installer-result evidence or claim descendant containment.
- The closed installer-result, distribution-binding, and owner-profile schemas must agree on the renamed true/false evidence fields.
- Existing phase timeouts and exact-source/digest/session bindings must remain unchanged.

---

### Task 1: Bound output drainage and correct the evidence schema

**Files:**
- Modify: `tools/windows_installer_smoke.ps1`
- Create: `tools/windows_bounded_process.ps1`
- Modify: `tools/windows_installer_result_review.py`
- Modify: `tools/windows_distribution_binding_review.py`
- Modify: `tools/windows_owner_profile_review.py`
- Test: `tests/test_windows_installer_workflow.py`
- Test: `tests/test_windows_installer_result_review.py`
- Test: `tests/test_windows_distribution_binding_review.py`
- Test: `tests/test_windows_owner_profile_review.py`

**Interfaces:**
- Consumes: redirected stdout/stderr tasks, existing per-phase root-process deadline, and closed installer evidence schemas.
- Produces: a reusable bounded-process helper, separately named 300-second maximum root-wait and 10-second drain budgets, `external_process_output_drains_bounded=true`, and `process_tree_containment_verified=false` across raw and reviewed evidence.

- [x] **Step 1: Add failing source-contract and closed-schema tests for the bounded drain and truthful evidence fields**
- [x] **Step 2: Run the focused Windows installer/reviewer tests and confirm failure against the old unbounded wait and whole-tree claim**
- [x] **Step 3: Implement the fixed combined drain wait, fail-closed reader cleanup, and consistent schema/limitations changes**
- [x] **Step 4: Run the complete Windows installer evidence test scope and verify pass**

### Task 2: Synchronize v0.340.404 and verify the release increment

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`, `nexus/release_roadmap.py`
- Modify: `AGENTS.md`, `ROADMAP.md`, `LAUNCH_DRIVE.md`, `VERSIONING.md`
- Create: `validation/release_trials_0_340_404.json`
- Test: release, version, roadmap, workflow, packaging, and public-repository safety suites.

**Interfaces:**
- Consumes: Task 1's bounded-drain and closed-schema contract.
- Produces: one pre-1.0 stacked review increment with synchronized metadata, regenerated 16-profile evidence, and no acceptance/authority promotion.

- [x] **Step 1: Increment all app/package/installer/roadmap metadata to 0.340.404 and add bounded release notes**
- [x] **Step 2: Regenerate deterministic `ROADMAP.md` and all 16 offline profiles**
- [ ] **Step 3: Verify saved evidence, focused suites, complete regression, compilation, dependency integrity, wheel metadata, and applicable workflow contracts**
- [ ] **Step 4: Audit the candidate and reachable history for public artifacts and high-confidence credential patterns**
- [ ] **Step 5: Commit, recheck exact base/head leases, publish a separate draft stacked PR, and inspect exact-head hosted CI**
