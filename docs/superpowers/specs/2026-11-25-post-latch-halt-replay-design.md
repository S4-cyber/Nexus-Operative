# Post-latch durable halt replay design

## Intent

The standalone local-paper emergency cut must never report `VERIFIED` from a
durable halt replay that was made stale by the separately sanctioned explicit
reset workflow. A reset can reuse the same SQLite inode, so pathname identity
checks and the independently reasserted intent latch are not sufficient proof
that the order journal is still halted at final reporting.

## Goals

- Recheck the durable order halt after the final emergency-intent latch is
  reasserted.
- Bind the two cold replays to the same regular single-link journal object.
- Preserve the latch and return `UNCONFIRMED` when a same-inode reset wins the
  post-replay window.
- Keep the public durable-halt result false until the post-latch replay finishes;
  latch persistence or observation faults cannot promote the first replay.
- Keep the emergency path bounded and independent: no automatic reset, retry,
  provider I/O, Portal/event-loop dependency, or long-lived journal lock.
- Preserve the current verified result when the durable journal and latch stay
  unchanged.

## Non-goals and anti-goals

- This does not make sampled filesystem evidence atomic across processes.
- It does not authorize live orders, broker access, funds movement, restart, or
  reset.
- It does not replace the existing data-root and latch identity checks or claim
  native Windows, broker, sustained-trial, or release acceptance.

## Constraints

- Engagement and optional AI remain OFF by default.
- The independent intent latch must remain asserted after uncertainty.
- A reset or replay failure must reduce authority, never manufacture success.
- The public result schema remains unchanged.

## Approaches considered

1. **Final post-latch cold replay — selected.** Reuse the exact warm halt
   acknowledgement only as an input and independently reopen/replay the journal
   after the second latch persist. This catches same-inode reset without holding
   a cross-process lock and preserves emergency independence.
2. **Hold a journal lock through result publication — rejected.** It would
   narrow the race further but couples the emergency path to prolonged writer
   exclusion and can obstruct the separately authorized reset path.
3. **Treat the final latch as sufficient — rejected.** The latch proves intake
   remains fenced, not that durable order history remains halted.

## Verification boundary

The new replay is another bounded observation, not an atomic lease. The result
may say `VERIFIED` only when both durable replays observe the same regular
single-link journal object and the final exact latch/root samples agree. A later
explicit reset remains possible and is outside the completed sampled result.
