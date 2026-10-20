# Nexus Operative versioning and launch gates

**Current development build: 0.178.0 (pre-release).** The installed/source order-offload tool now has a read-only rotation preflight that binds a verified private copy back to the exact unchanged application-closed live source. It performs no compaction or deletion and reclaims no capacity. Exact-source CI remains mandatory. Rough implementation stays about 40% (30–45%, low confidence), with 0/9 fully accepted major stages.

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

## v0.178.0 exact-source-bound rotation preflight

`order-offload rotation-plan` verifies an existing copy and requires its exact order/portfolio file hashes, full replay digest, command/archive counts and stopped, flat, reconciled boundary to match the explicitly application-closed live source. It rechecks both sides after comparison and rejects stale or changed inputs. The command writes nothing, never constructs a replacement, deletes no history, reclaims no capacity and cannot resume the application.

The common source/standalone/installed operator smoke now executes 21 cross-platform command-prefix children plus the Windows-only read-only-journal emergency fault. No UI control or editable setting changed. Capacity-reclaiming publication, crash recovery, multi-epoch load, native private-volume/profile acceptance and every broker/market/funding/live/cloud/independent/owner gate remain open.

Local validation passed 3,406 Python tests and the 157-test focused durability/release set. All sixteen bounded release profiles passed and saved-evidence verification matched 347 source files / `d4e0278c9ca0e90d52f26b8c59fd15bdae91593823c692a04d98a5ba6dd5bcbd`; report SHA256 is `fe93088d748e323377ce55839026baa99360ec029070aa0c70946bcf9764c3a0`. All fourteen Node controllers, Portal syntax, compilation, dependency consistency, generated-roadmap equality and whitespace checks passed. Exact-source v0.178.0 CI remains required.

## v0.177.2 bounded shared-budget contention recovery

Exact v0.177.1 security run 39385781709 and safety/browser/evidence run 39385781778 passed. Windows run 39385781816 completed 3,379 tests but failed before packaging because concurrent shared-budget instances admitted 8 of 12 safe slots. SQLite writer lock timeouts had been conservatively grouped with expected cap exhaustion.

The budget now retries only typed SQLite BUSY/LOCKED contention for at most 30 monotonic seconds. Every failed attempt closes its connection and rolls back; a continued lock produces a fixed timeout code with no SQLite/path detail. The concurrency contract requires exactly 12 committed admissions and 18 exact exhaustion denials. The 36-request/hour limit, commit-before-transport ordering, explicit public-discovery start, UI, permissions and authority remain unchanged. Exact-source Windows execution remains required.

Local validation passed 3,401 tests, the 215-test focused set and all sixteen source-fingerprinted profiles. Saved evidence matched 347 files / `3a04e339dd8a1b92669d1a2dae2af3dc806b2cc9572edaac31738518006ce882`; report SHA256 is `2ed3f208329c276fe3a14d9faf3a5a81b965b1b94c194dd3b1e752c7e1de7d9b`. This does not replace exact-source Windows acceptance.

## v0.177.1 Windows offload handle correction

Exact v0.177.0 public-security run 39335584198 and its browser-help job passed, while the Windows release-evidence operations child failed. Its SQLite integrity connections were entered as transaction context managers, which do not close the underlying handles. Linux permitted rename/removal with the leaked handles, masking the native locking defect. v0.177.1 explicitly closes both read-only integrity connections before replay, rename and cleanup; a regression blocks publication until every tracked integrity handle is closed.

This patch changes no command semantics, user interface, editable parameter, permission, risk limit, account, provider, funding, live/cloud behavior or authority flag. Exact-source Windows regression/release evidence, standalone/installed command smoke, packaging/installer and security workflows remain mandatory.

Local validation passed 3,399 tests, the 152-test focused set and all sixteen source-fingerprinted profiles. Saved evidence matched 347 source files / `5d7aec1cd4980de8e8b330b319cf04ac8d95368bccf221db4a913b948367de21`; report SHA256 is `262de076ea23e39df01e7be2a1c85529e796f5f3fbd877861f27e35117b1f258`. This does not replace exact-source Windows acceptance.

## v0.175.1 Windows acceptance-harness repair

Exact v0.175.0 security 39311553891 and safety/browser/evidence 39311553832 passed. Windows 39311553897 reached 3,365 passes and 19 skips but failed before packaging because its blocked-fill race asserted only one of two exact safe orderings and its complete loopback/tool child exceeded a 35-second aggregate process budget. v0.175.1 accepts only `VETO`/`EMERGENCY_CUT_REQUESTED` or `HOLD`/`RUNNER_STOPPED_BEFORE_FILL`, preserving every durable no-fill/reconciliation assertion, and gives the aggregate child a bounded 120 seconds without changing its individual checks.

Local validation passed 3,386 tests, the 124-test focused set and all sixteen source-fingerprinted profiles; exact-source v0.175.1 Windows packaging, installed smoke, browser/safety and security CI remain mandatory. No runtime, UI, permission, default, account, order, funds, live, cloud or release authority changed.

## v0.176.0 sustained-trial capacity readiness

The existing read-only Paper Trial Readiness action now combines retained quote/session headroom with the replayed durable order journal's ordinary-command and intent-ID capacity. It reports whether the current reserve can admit the next runner cycle and separately computes a conservative upper bound that assumes every policy-cadence cycle consumes the runner's full admission reserve. That bound is a fail-closed capacity diagnostic, not predicted strategy activity, expected orders, performance or market realism.

Missing histories remain absent and no check starts the engine, creates a session, reserves storage, opens credentials, contacts a provider or changes an account. The view keeps all authority flags false. A 72-hour quote window fits the current 100,000-row evidence cap on a fresh profile, while its deliberately conservative order bound exceeds current command/ID limits and therefore makes the physical-offload/long-run gate visible instead of implying sustained readiness. Exact-source Windows, browser/help, release-evidence and security CI remain required.

Local validation passed 3,387 tests, the 89-test focused set and all sixteen source-fingerprinted profiles. Saved evidence matched 345 files / `53ff9780601ce573d9976460b04dc75cc939e9cb885a6e678f9b8b818d40de99`; report SHA256 is `f4bdf23a5a75abd04dbbdf622a17395f57210897859127530d81041809bbfc24`. This local result does not substitute for exact-source native Windows/browser/security CI or actual sustained-market and broker-paper acceptance.

Exact v0.176.0 commit `6f6eeaa44439d49e697597397c3678b409f1a974` subsequently passed public-security run 39327666704, all safety/browser/evidence jobs in run 39327666689 and Windows/installer run 39327666522.

## v0.177.0 verified physical-copy foundation

The new `order-offload` installed/source tool requires an explicit application-closed confirmation and refuses anything except a stopped, flat, reconciled journal with at least one logical archive batch and no active command epoch. It rejects SQLite sidecars, symlinks, hard links, special files, missing linked portfolio state, oversized inputs, an existing or in-tree destination and malformed/tampered output. It stages a new private directory, copies both SQLite databases, records content hashes and bounded replay counts, runs integrity checks plus complete archived-order replay, and verifies that the source bytes/replay remained unchanged before and after publication.

The output is deliberately copy-only. Source deletion and `capacity_reclaimed` remain false; there is no compaction, rotation policy, automatic resume, broker access, credential access, order submission or permission change. The operations release profile now includes a disposable verified-copy fixture, and the packaged tool smoke runs create and verify through the executable command dispatcher. See `ORDER_OFFLOAD.md` for operator scope. No Portal button or editable parameter was added, so the seven tabs and existing black/bright-purple beginner-help contract are unchanged.

Exact-source v0.177.0 Windows packaging/installed-tool, safety/browser/evidence and public-security workflows remain mandatory, as do private-volume/native-profile recovery acceptance, multi-epoch rotation/compaction, sustained long-run trials, actual broker-paper reconciliation and every funding/live/cloud/independent/owner gate.

Local validation passed 3,398 Python tests, the 125-test focused durability/packaging/release set and all sixteen source-fingerprinted profiles. Saved evidence matched 347 files / `2d396ef98eb40fcaac1db4889c64079c00d2e6c9ef63ce3f10c8d51b6105ee3b`; report SHA256 is `68220344fc05ddd8a8db3e4af210eea1df7344c58d4458d89a55c935d27e067d`. Fourteen Node controllers and the static/dependency/roadmap checks passed. This remains local/injected evidence with all authority flags false.

## Enforcement
Application version, package metadata and Windows installer version/output filename must match. Automated tests guard against accidentally labeling an unfinished preview 1.0.0 or 1.1.0. The hourly autonomous development task is instructed to keep all work within the 0.x prerelease series unless these gates are independently evidenced and the user explicitly directs the promotion.

