# Installed Executable Distribution Binding Plan

**Goal:** Prove that the standalone, first-installed and reinstalled Windows
executables are identical and bind that identity into distribution evidence.

## Constraints

- Keep paper engagement and optional AI OFF by default.
- Preserve independent emergency stop, risk limits, account checks and exits.
- Add no UI control or editable parameter.
- Keep roadmap acceptance at 0/9 and version below 1.0.

## Tasks

1. Add red schema/equality/manifest tests to installer-result, distribution and
   owner-profile reviewers plus the Windows workflow source test.
2. Extend the Windows smoke result with three exact executable hashes and two
   equality booleans; pass the standalone path explicitly from CI.
3. Carry and validate the hashes through the two review layers and owner review.
4. Synchronize v0.340.431 package/application/installer/docs/roadmap.
5. Regenerate and independently verify all saved release profiles.
6. Run focused, full, package, workflow and public-artifact verification before
   publishing to the existing draft PR with an exact-head lease.
