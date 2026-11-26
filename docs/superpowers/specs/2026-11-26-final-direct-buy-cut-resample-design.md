# Final Direct BUY Cut Resample Design

## Intent

Restore the independent emergency latch as the last fallible guard sampled
before a direct local-paper BUY mutates accounting. v0.340.419 added protected
quote revalidation after the existing latch sample, leaving a bounded window in
which a cut asserted during revalidation could still permit new exposure.

## Constraints

- Keep every engagement and tier permission OFF unless explicitly enabled.
- Preserve reducing SELL exits, account checks, exposure/cash limits, durable
  rollback and the existing ten-second quote limit.
- Perform no broker request, credential access, retry, funds movement or live
  action.
- Add no UI control or parameter; preserve the seven tabs and black/bright-
  purple bottom-right hover help.
- Keep the version below 1.0 and retain the 38% weighted roadmap baseline and
  0/9 independently accepted stages.

## Approaches

1. Keep one latch sample before quote revalidation. Rejected: this is the
   v0.340.419 ordering gap and allows a cut during revalidation to lose.
2. Move the one latch sample after quote revalidation. Rejected: when a cut is
   already asserted and the quote is also stale, quote failure would replace
   the established higher-priority emergency refusal.
3. Sample before and after quote revalidation. Selected: the first sample
   preserves emergency error priority, while the second closes the new
   post-sample window and is the final fallible check before accounting.

## Behavior

`paper_portfolio.buy` keeps its existing post-writer emergency sample, then
revalidates the exact captured quote, then samples `cut_requested` once more.
If the latch becomes active during quote validation, the transaction raises
`EMERGENCY_CUT_INTERRUPTED_TRADE` and rolls back without a position, cash
delta, event or seal. The quote check remains pure local validation and the
second latch read takes neither SQLite writer reservation.

## Evidence boundary

The regression asserts the durable cut from inside the second quote validation
and proves a 409 refusal plus an initialization-only ledger. This is bounded
same-host race evidence, not distributed atomicity, broker/account/source
acceptance, sustained-market acceptance, profitability or release approval.
