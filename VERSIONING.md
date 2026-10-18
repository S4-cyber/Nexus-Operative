# Nexus Operative versioning and launch gates

**Current development build: 0.139.0 (pre-release).** Bounded optional Found Markets discovery, deterministic multi-horizon council screening and required/optional/future checklist sections; discovery does not change execution markets.

## Historical versions
Earlier development iterations were labeled 1.x, 2.x, and 3.x while the application was still simulation-only. Those labels represented prototype development milestones, **not** production releases or compatibility promises. The development snapshot previously labeled **3.8.0** is now **0.38.0**. Existing Git commits, CI logs, and artifacts retain their original historical names and are not rewritten.

## Forward version policy
- **0.101.0, 0.102.0, 0.103.0, …:** milestone-level pre-release development. Versions may progress beyond 0.101.0 if necessary (e.g., 0.102.0); there is no deadline-based automatic 1.0 promotion.
- **0.x.y:** fixes, small coherent enhancements, and refactoring of that pre-release milestone. No live-money approval implied.
- **1.0.0:** first completed production release, only after all launch acceptance gates are evidenced and explicitly approved by the project owner.
- **1.1.0 onward:** features added after the 1.0.0 launch, maintaining documented compatibility where possible.

The primary implementation sequence is [LAUNCH_DRIVE.md](LAUNCH_DRIVE.md). Global opportunity expansion in [EXPANSION_INITIATIVE.md](EXPANSION_INITIATIVE.md) and secure computer-off operation in [CLOUD_OPERATION.md](CLOUD_OPERATION.md) are explicit staged goals, with data, venue, risk and hosting acceptance before activation. Low-latency autonomous *paper* execution and its reliability tests belong to the v1.0 critical path; real-money use is separately authorized.

## Required gates before 1.0.0
1. Stable packaging, installer, startup, configuration, upgrade and recovery tests.
2. Independently audited and reliable market-data freshness/provenance, gaps, replays and failure behavior.
3. Verified model-provider integrations, safe output validation, cost controls, and no uncontrolled order authority.
4. Chronological historical research using unbiased train/holdout splits, reproducible performance measurements and realistic costs.
5. Sustained autonomous paper-trading reliability with persistent accounting, reconciliation and automatic risk vetoes.
6. Secure, permission-scoped execution infrastructure independently tested with strict order lifecycle handling, idempotency and emergency stops.
7. Extended forward validation, operational monitoring, secrets management, independent security/risk review and documented recovery drills.
8. Secure provider-held funding and governed autonomous execution workflows for the agreed production scope: credential isolation, spendable-funds and cash/position reconciliation, transfer failure/duplicate handling and a separately authorized limited rollout. No release enables deposits or live orders automatically.
9. Secure computer-off operation for the agreed scope, including encrypted durable state, remote stop, distributed ownership and quota/outage recovery.
10. Explicit owner approval for production release and any *separate* authorization for live-money order activation.

A green CI build alone is not a release acceptance gate. Research profitability cannot be guaranteed. The current branch has **no real-money order authority**.

## Enforcement
Application version, package metadata and Windows installer version/output filename must match. Automated tests guard against accidentally labeling an unfinished preview 1.0.0 or 1.1.0. The hourly autonomous development task is instructed to keep all work within the 0.x prerelease series unless these gates are independently evidenced and the user explicitly directs the promotion.

