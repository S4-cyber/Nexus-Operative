# Replaced-Owner Interruption Design

## Intent

Close one durable reconciliation gap created by the fail-closed paper POST
boundary.  If the active provider-budget owner marker is replaced after the
attempt reservation, the original lease correctly loses authority and the
request performs zero POSTs.  The retained pending attempt must then have one
bounded, explicitly confirmed way to become permanent unknown-outcome review
state without restoring the old owner, retrying, repairing, or re-arming the
request.

## Goals

- Keep the existing same-owner `interrupt()` path strict and unchanged.
- Add a distinct hash-chained replaced-owner interruption that requires the
  exact pending token/head and two explicit confirmations.
- Prove that the current owner identity differs from the identity retained by
  the pending attempt.
- Retain `INTERRUPTED_UNKNOWN`, a full-day cooldown, review-required state,
  unknown POST count, and no request-after head.
- Cold-replay the exact durable interruption after a lost local acknowledgement
  while holding the new owner lease and exact journal file identity.
- Promote the new interruption and the v0.340.429 exact-type/final-owner guards
  into mandatory saved release evidence.

## Non-goals and anti-goals

- No provider request, credential read, request resubmission, owner repair,
  automatic recovery, cooldown shortening, routing, funding, live, cloud, or
  release authority.
- No claim that the replacement identity is legitimate outside the local
  cooperating-process boundary.
- No Portal button or editable setting.  Existing seven tabs and
  black/bright-purple bottom-right hover help remain unchanged.
- No change to the 38% weighted roadmap baseline or 0/9 accepted stages.

## Approaches considered

1. **Relax ordinary interruption ownership.** Rejected because it would let an
   identity change silently inherit the existing interruption authority.
2. **Leave the tail pending forever.** Safe for routing, but operationally
   unreconcilable and contrary to explicit durable review.
3. **Add a distinct replaced-owner event and reviewed command.** Selected.  It
   preserves the original identity in history, binds the new identity only to
   the interruption acknowledgement, and requires explicit uncertainty
   preservation.

## Design

`PaperProviderPostBudget.interrupt_replaced_owner(...)` opens a new normal
local owner lease and appends `OWNER_REPLACED_INTERRUPTED` only when the exact
pending token/head match, the retained and current owner digests differ, both
confirmations are exact booleans, and the journal clock is monotonic.  Replay
records a completed `INTERRUPTED_UNKNOWN` attempt with `actual_posts=None` and
`request_after_head_sha256=None`; no attempt capacity is reclaimed.

A new `paper_provider_post_interruption` command preflights one existing
private journal, pins its regular-file identity and fingerprint, confirms the
pending retained owner differs from the current lease, attempts the append
once, and then accepts only two identical cold receipts bound to the old head,
new head, token, both owner digests, and terminal review state.  A lost append
acknowledgement is recovered by replay, never by a second append.

The disposable fixture will exercise an actual owner-path replacement, prove
the ordinary path refuses it, prove the reviewed path closes it exactly once,
and expose only content-free booleans/digests.  The release trial and transport
uncertainty matrix will require that fixture plus explicit proof that malformed
asset flag types and final owner loss never reach POST.

## Failure boundaries

- Same owner, stale head/token, missing confirmations, unreadable/mutated
  journal, unchanged owner identity, or a changed owner path during return all
  fail closed without a second provider attempt.
- A committed event whose acknowledgement is lost is accepted only through an
  exact receipt replay; an uncommitted or fabricated return stays uncertain.
- The original owner cannot finish after its pathname identity is lost, and
  the replacement owner cannot resume the pending reservation into provider
  I/O.
