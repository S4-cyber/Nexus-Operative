# Windows phase evidence binding design

## Intent

Close the gap between the Windows installer phase log and the public release
artifact chain.  Today downstream reviews validate a phase summary's
semantics, but they do not identify the exact phase log bytes or exact phase
review artifact that produced the successful claim.  A different valid phase
summary can therefore be substituted without changing the installer-result
review.

## Goals

- Bind the exact `installer-phases.jsonl` byte count and SHA-256 into the
  closed phase-review schema.
- Bind the exact `installer-phase-review.json` byte count and SHA-256 into the
  installer-result review.
- Ship and manifest the phase-review artifact in the Windows distribution.
- Recheck the phase-review bytes and both digest links in distribution and
  ordinary-user owner-profile review.
- Preserve all authority flags as exact false and leave runtime/UI behavior
  unchanged.

## Non-goals and anti-goals

This is not native Windows, clean-machine, native-window, ordinary-user,
power-loss, signing, broker, market, funds, profitability or release
acceptance.  It must not expose credentials or retained market evidence, grant
runtime authority, introduce a dependency, or change Portal controls.

## Approaches

1. Keep semantic phase validation only.  Rejected because semantically valid
   substitutions remain indistinguishable downstream.
2. Bind only the phase-review artifact.  This closes distribution substitution
   but loses the exact raw JSONL lineage.
3. Bind both layers.  The phase review records the exact raw JSONL digest; the
   installer-result review records the exact phase-review digest; distribution
   and owner review recheck the shipped phase-review bytes and manifest entry.
   This is the selected approach because it creates one auditable, closed
   byte-identity chain without publishing the private diagnostic directory.

## Constraints

- Continue using standard-library hashing and closed JSON schemas.
- Keep the raw JSONL in the separately retained installer diagnostic artifact;
  publish only its digest in the reviewed distribution.
- Add `installer-phase-review.json` to the public artifact manifest and exact
  upload set before the manifest is constructed.
- Any missing, extra, stale, promoted, type-confused or unequal field fails
  closed.

## Acceptance

Red/green tests must prove that raw formatting changes alter the raw phase
digest, semantically valid phase-review substitutions alter or invalidate the
installer-result binding, changed/missing manifest entries fail distribution
review, owner review rechecks the actual phase-review bytes, and workflow
ordering copies/manifests/uploads the artifact before review.  Hosted Windows
execution remains a separate gate.
