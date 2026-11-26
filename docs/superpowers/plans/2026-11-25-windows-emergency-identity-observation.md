# Windows Emergency Identity Observation Plan

**Base:** exact v0.340.407 PR #129 tree
`ac8a5612d9b1093417919747f94a64a6a875a814`.

1. Reproduce one transient order-journal `lstat` denial with a real durable
   halt and verify the current observer returns false before replay.
2. Preserve a tri-state identity observation through the bounded replay helper.
3. Verify transient recovery, immediate replacement rejection, definitive
   negative handling, retry ceiling and single halt-command durability.
4. Synchronize version, generated roadmap, release notes and saved evidence.
5. Run focused, release, full regression, packaging, secret and history checks.
6. Obtain independent review and publish only after an exact-head lease.
