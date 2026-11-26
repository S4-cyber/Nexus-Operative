# Windows Emergency Read Backoff Plan

**Observed parent:** exact PR #130 commit
`6cb7c1e7ffd12b235fc1231f370aee3738f3eb9b`.

1. Record both hosted shard failures and preserve their exact parameter cases.
2. Reproduce a transient observation that remains unavailable until a yield.
3. Add fixed bounded delays only between unavailable read-only samples.
4. Prove the three-attempt ceiling, immediate definitive rejection and
   single-shot halt mutation remain unchanged.
5. Run focused repetition, complete regression and saved evidence verification.
6. Publish only with exact-parent-head and source-tree leases.
