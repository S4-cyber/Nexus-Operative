# Modeled-execution reconciliation fence plan

1. Add red tests proving BUY and SELL outcomes cannot claim incident-free
   coverage, while HOLD remains unaffected and raw declared coverage remains
   visible.
2. Add a trial-readiness regression proving the embedded blocker survives a
   read-only composite review with no retained order journal and no file
   mutation.
3. Count in-window modeled executions, publish the count, classify them as an
   incident, add the fixed review reason, require quote bindings for BUY/SELL,
   and reject outcomes timestamped before their bound quotes.
4. Extend the forward drill and release-profile exact-schema/count validation,
   including mutation tests for omitted or forged blocker evidence.
5. Run focused tests, release-profile verification, deterministic regression
   shards, version/evidence synchronization, secret/history checks, and an
   independent review before publishing against an exact parent-head lease.
