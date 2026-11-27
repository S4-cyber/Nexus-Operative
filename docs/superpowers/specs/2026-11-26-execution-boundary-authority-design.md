# v0.340.429 execution-boundary authority design

## Intent

Close three local fail-closed gaps at the last authority boundaries before a
paper order or emergency cut: unsafe order-journal shape must not suppress the
independent emergency latch, provider-budget owner loss must prevent POST, and
broker asset flags must not accept JSON numbers as booleans.

## Goals

- Publish and retain the sibling emergency intent latch even when the selected
  order journal is a symlink that cannot be safely mutated.
- Preserve the external symlink target byte-for-byte and report the cut as
  unconfirmed but locally halted through the independent latch.
- Re-prove the original provider-budget owner lease inside the final locked
  request snapshot after opaque credential callbacks and before the independent
  emergency-stop sample and POST opener entry.
- Require an exact built-in asset object, exact built-in text fields and exact
  boolean eligibility flags; numeric `1`, `1.0` and `1e0` are not `true`.
- Preserve one-shot, paper-only, OFF-by-default operation and all existing
  durable marker, lookup-only, bounded-risk and emergency-stop behavior.

## Non-goals and anti-goals

- No broker, account, source, hold, fee, funding, live, cloud or release
  acceptance.
- No automatic retry, owner reacquisition, pending-budget repair or inferred
  `NOT_SENT` completion after owner loss.
- No UI controls, editable parameters, dependencies, credential storage or
  provider requests.
- Do not make the emergency latch depend on SQLite validity or replace/follow an
  unsafe journal leaf.

## Constraints

- The independent emergency stop remains sampled last and outside the request
  and provider-owner locks.
- Owner loss after the new final sample remains a documented residual race; the
  change narrows observed-loss behavior and does not claim distributed locking.
- A provider-owner failure after the request marker leaves the marker
  lookup-only and the provider budget pending for explicit review because the
  original lease cannot safely acknowledge a terminal result.
- Version and generated evidence remain below 1.0; roadmap progress stays at
  the read-only 38% weighted baseline with 0/9 accepted stages.

## Approaches considered

1. **Chosen: move the emergency journal-shape refusal into the existing durable
   gateway and extend the existing reservation replay with the original owner.**
   This reuses the journal's no-follow guard and the provider budget's exact
   `owner.validate()` proof, minimizing new authority and synchronization.
2. Add separate preflight owner/journal helpers. This duplicates existing
   validators and risks divergent error handling.
3. Reacquire or replace ownership after loss. Rejected because a new owner
   cannot prove continuity with the retained BEGIN row and could authorize a
   second process or POST.

## Acceptance

- A real order-leaf symlink produces `UNCONFIRMED`, `halted=true`, canonical
  intent present, no durable halt confirmation and no external-target mutation.
- A running paper loop observes that latch even if the exact real journal is
  restored before a blocked quote returns.
- Injected and POSIX owner-loss cases enter no POST opener, retain the request
  marker, leave the provider reservation pending, and cannot re-arm.
- Initial malformed asset flags fail before either durable reservation. Final
  malformed flags retain the lookup-only marker, produce zero POSTs and close
  the still-owned budget as `NOT_SENT`.
