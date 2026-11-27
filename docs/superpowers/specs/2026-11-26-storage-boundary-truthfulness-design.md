# Storage and Boundary Truthfulness Design

## Intent

Close three offline acceptance gaps that can acknowledge a result against the
wrong durable portfolio leaf, omit an execution incident that supplies clipped
trial coverage, or describe a standalone emergency cut as ordered ahead of
execution after its independently observed latch was replaced.

## Goals

- Bind ordinary linked-portfolio writes to one regular, single-link public
  pathname and keep checking that pathname identity through the transaction.
- Preserve journal-only cancel, expiry, and emergency-halt writes when the
  linked portfolio is absent, aliased, locked, or inconsistent.
- Count modeled executions and event incidents on every quote endpoint whose
  interval contributes coverage inside the requested retained-trial window,
  even when that endpoint lies immediately outside the window.
- Clear standalone emergency ordering evidence if the initially observed latch
  no longer names the same file before or after the durable halt mutation.
- Keep every broker, market, funds, live, cloud, performance, release, and
  trading authority false.

## Non-goals

- No broker or credential access, provider I/O, real trading, funds movement,
  paid provisioning, deployment, retained private market evidence, or repair.
- No automatic retry of a halt mutation and no lock shared with the independent
  emergency latch.
- No new Portal control or editable parameter.
- No merge or transplant of draft PR #116 agent-handoff code.

## Approach

Use the existing read-only linked-portfolio identity contract as the model for
ordinary attachment: reject symlinks, non-regular files, and multi-link leaves,
resolve once, verify the attached SQLite filename, and retain the observed
public-path identity in `AttachedPortfolio` so existing semantic checks also
recheck it. For trial review, derive a support-endpoint set from only the
post-revalidation intervals that overlap the requested window, then include
their events and sessions in incident and reconciliation accounting. For the
standalone cut, sample the exact original latch identity again after the halt
mutation and before final latch reassertion; identity loss makes ordering
unconfirmed but never retries or undoes the halt.

## Constraints

- Version remains below 1.0 and is synchronized across application, package,
  installer, read-only roadmap, guidance, and saved evidence only after tests.
- Engagement and optional AI remain OFF by default. Limits, account checks,
  reducing exits, fail-closed behavior, and emergency-stop independence remain.
- The seven beginner-friendly tabs and black/bright-purple bottom-right hover
  help remain unchanged.
- Roadmap acceptance remains 0/9 unless independent accepted evidence changes;
  local tests do not change the 38% weighted implementation baseline.
