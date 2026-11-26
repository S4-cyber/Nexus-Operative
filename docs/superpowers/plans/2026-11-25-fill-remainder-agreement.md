# Durable Fill Remainder Agreement Implementation Plan

**Goal:** Prevent contradictory retained fill completion types and remainders
from establishing durable local request/activity agreement.

**Architecture:** Add one conservative semantic review reason in the canonical
Alpaca REST activity normalizer. Keep the supplied record readable, propagate
the reason through the existing broker activity review, require the higher
request/activity composition to withhold agreement, and bind the negative case
into the activity release profile.

**Tech Stack:** Python 3.11, pytest, deterministic offline release profiles.

**Spec:** `docs/superpowers/specs/2026-11-25-fill-remainder-agreement-design.md`

## Global constraints

- Keep every broker, funding, live, cloud, spend, and release authority false.
- Preserve OFF-by-default engagement, risk limits, account checks, reducing
  exits, fail-closed behavior, and independent emergency-stop access.
- Keep the version below 1.0 and the roadmap estimate at exact weighted 38%,
  displayed about 40%, 30–45% low confidence, with 0/9 accepted stages.
- Add no UI control or editable parameter.

### Task 1: Prove and close the durable fill semantic gap

**Files:**
- Modify: `nexus/alpaca_activity_normalizer.py`
- Modify: `nexus/broker_activity_review.py`
- Test: `tests/test_alpaca_activity_normalizer.py`
- Test: `tests/test_broker_activity_review.py`
- Create: `tests/test_broker_fill_type_reconciliation.py`

- [x] Add red tests for terminal fills with a positive remainder and partial
  fills with zero remainder.
- [x] Run the focused tests and observe the old code fail to report the new
  review reason and fail to block higher-level local agreement.
- [x] Add the minimal normalizer reason and the broker activity fixture check.
- [x] Re-run the focused scope to green.

### Task 2: Bind the increment to versioned release evidence

**Files:**
- Modify: `pyproject.toml`, `nexus/app.py`, `installer/NexusOperative.iss`
- Modify: `nexus/release_roadmap.py`, `AGENTS.md`, `LAUNCH_DRIVE.md`,
  `VERSIONING.md`, generated `ROADMAP.md`
- Modify: `tests/test_release_trials.py`
- Create: `validation/release_trials_0_340_407.json`

- [x] Add a red release-profile contract for the negative fill semantic check.
- [x] Synchronize v0.340.407 metadata and bounded non-authoritative roadmap
  evidence.
- [x] Regenerate deterministic ROADMAP.md and all 16 offline profiles.
- [x] Verify focused suites, full two-shard regression, compilation,
  dependencies, Portal/Node checks, wheel metadata, evidence, and secrets.
- [ ] Obtain independent review, commit atomically, recheck exact remote heads,
  and publish a draft stacked PR only with an expected-head lease.
