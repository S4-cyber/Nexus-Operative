# Windows Optional Birth-Time Identity Design

## Failure

The exact development-branch Windows complete regression run
`45807905265` failed its first artifact-manifest test and then cascaded through
other artifact consumers with `INVALID_ARTIFACT_FILE` and
`ARTIFACT_CHANGED_DURING_READ`. Those diagnostics do not retain field-level
stat observations, so they do not prove which metadata differed. Local
reproduction identified a matching compatibility failure: the verifier
requires `st_birthtime_ns` on both a pathname sample and its open-descriptor
sample on Python 3.12+ Windows even though Python documents that field as not
always available. Exact hosted validation remains required.

## Design

Keep the existing cross-source identity fields mandatory: device, file index,
size, modification time and link count must match exactly. Treat Windows birth
time as an optional strengthening field:

- when both samples expose exact integer birth times, require equality;
- when either sample legitimately lacks the optional field, do not compare
  cross-source change time because Windows path and descriptor samples can
  expose different change-time semantics;
- when an exposed birth-time value is malformed, fail closed.

Same-source pathname stability, same-source descriptor stability, regular-file
and single-link checks, retained handles, directory pinning and both complete
digest passes remain strict. Retain the initial pathname sample independently
from the descriptor sample, then require every later pathname observation to
match its same-source device, file index, size, modification time, change time,
link count and any available Windows birth time. Replacement still changes the
mandatory file identity; in-place mutation still changes same-source metadata
or a digest.

## Alternatives rejected

1. Keep birth time mandatory. This preserves the observed Python 3.12 false
   rejection and blocks ordinary Windows verification.
2. Fall back to cross-source `st_ctime_ns`. That compares metadata whose
   semantics can differ between Windows pathname and descriptor samples.
3. Remove cross-source metadata checks entirely. This would discard stable
   identity fields that remain available and meaningful.

## Acceptance

- A Windows path sample and descriptor sample can identify the same artifact
  when birth time is absent from one or both but all mandatory fields match.
- Matching birth times remain accepted; differing or malformed exposed birth
  times remain rejected.
- Device, file index, size, modification-time or link-count differences remain
  rejected.
- Full distribution verification succeeds under a fixture that models path
  birth time being available while descriptor birth time is unavailable.
- A pathname-only metadata change after the retained handle is opened is
  rejected by distribution verification and single-file hashing.
- Existing replacement, rewrite, directory-swap and two-pass digest tests stay
  green.

This repairs a compatibility false negative. It is not native Windows or
installer acceptance until the exact hosted Windows workflow passes, and it
grants no broker, account, sustained-trial, funding, live, cloud, release or
trading authority.
