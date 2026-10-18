# Nexus Operative release acceptance

Current build: **0.106.0 prerelease**. Production 1.0 remains blocked. This checklist records evidence scope, not runtime permission. No claim that an environment is perfect or a strategy will be profitable follows from passing these checks.

## Repeatable trials

Run from a source checkout with the project dependencies installed:

```sh
python -m nexus.release_trials --output release-trial-evidence/report.json
python -m pytest -q
python -m compileall -q nexus tests tools
```

The trial command uses separate temporary child-process data roots and never starts the installed account, reads actual credentials, connects a broker or requests actual prices. It stops on child failure, malformed/incomplete reports, accounting disagreement or unsupported authority claims. Its report fingerprints Python source, tests, tooling, workflows, package/installer metadata and Portal HTML before and after execution; changing those inputs invalidates the run. The fingerprint is a reproducibility aid, not a signature or source attestation. Generated reports and documentation are excluded to avoid self-reference. Machine/Python/SQLite versions and local timings are recorded. An interrupted command or missing report is not a pass.

| Trial | Work exercised | What passing establishes | What remains unverified |
| --- | --- | --- | --- |
| Forward runner | 20 retained sessions, 280 serial cycles, injected BUY/HOLD/SELL, duplicate ticks, tier-off exits/reentry vetoes, three-failure outage pauses and independent process recovery | Internal evidence/ledger agreement, flat halted recovery and no automatic resume for this bounded script | Sustained real feeds, unpredictable outages, actual price formation, depth, exchange fills or profitable strategy |
| Paper operations | 24 buy/hold/sell rounds, tier vetoes, halt/restart, logical archives and original-ID replay | Scripted accounting, retained identities and history remain consistent | Long-run storage, indefinite operation or broker ownership |
| Backup/restore | Disposable linked-account and quote-evidence backup, fresh-directory restore and fresh-process fingerprints | Local fixture restoration and cross-database agreement | Real owner backups, Windows profile/ACL/key recovery or complete disaster recovery |
| Capacity | Four 30-cycle gateway/linked-account treatments, checkpoint comparisons and two bounded burst treatments | Local durable accounting and measured commit-return latency under the declared workload | Exchange throughput, feed-to-order latency, sustained production load or a guaranteed optimization factor |
| Retained activity merge | Two synthetic partial-fill batches, evolving order snapshots and unlinked fee debits through real temporary retention/replay/review | Combined explicit fills agree with supplied order quantities/prices; coverage and fee-link flags stay unverified | Actual broker activity completeness, correction semantics, fee links or reconciled spending |
| Local paper-read pacing | Temporary account-bound journals, fixed-clock success/rate/auth/pending outcomes, owned interruption/full-day cooldown and fresh-instance replay | Local cooldown and review blocks remain retained | Actual provider quotas, default runtime adoption, owner-profile recovery acceptance or distributed ownership |
| Combined supplied balance review | Explicit linked-fill/fee cash path against supplied total cash/BTC, separate availability/open terms, missing-availability and cash-mismatch negative controls | Supplied arithmetic and review conditions remain distinct from permissions | Authenticated source/order/activity/fee coverage, actual provider holds, reconciled account or spendable funds |
| Collection observation retention | Four injected GETs, two full activity pages, actual normalization/atomic receipt/restart review with preserved cursor chain and page cap | Bounded page observations survive local retention without duplicating global activity payloads | Actual account/source attestation, pagination completeness, corrected records, execution-linked fees or spendable funds |
| Retained gross balance review | Exact account/head-bound raw REST retention/replay, explicit fills and gross residuals, unallocated fees and stale-head rejection | Retained arithmetic remains tied to sampled source history without fee/link/funds authority | Verified opening/observed totals, complete source/fee/posting coverage, holds, settled balances or an execution reservation |
| HTTP startup/shutdown | Actual loopback TCP server/lifespan and status, roadmap, To Do, root redirect markup and Portal HTML requests | Fresh source-process startup stays stopped, creates no accounting data and performs no external connection/vault access during checked reads; server shuts down | Rendered browser interaction, installed Windows desktop behavior, existing-account migration or actual provider access |

The HTTP trial denies/counts non-loopback connections and keyring operations in its disposable child. It checks the actual Portal asset separately from root meta-refresh markup. Other trial profiles use the established injected/scripted fixtures; these are not claimed to have a system-wide network sandbox. Sample counts and modeled liquidity are explicit. Zero exit alone does not certify a profile. No trial grants order, funding, model promotion, resume or release authority.

## Current evidence and open boxes

| Release requirement | Status | Evidence / next acceptance |
| --- | --- | --- |
| Source regression, controller help and syntax | Automated checks available | Full regression, compilation, ten Node controllers and dependency check are recorded in LAUNCH_DRIVE.md for each push |
| Integrated local trials | Bounded offline trials passed | Source-fingerprinted Linux report in `validation/release_trials_0_109_0.json`; exact new-source Windows trials must still run |
| Windows packaging and data recovery | v0.107.0 verified; current source pending | Exact commit `5803c685184e4738e0d35e65355752705df586a3`: packaging run39087145807/job117557139094 and safety/browser run39087145868/job117555983422 passed; newer source requires its own runs |
| Clean-machine desktop and ordinary-user profile | OPEN | Native window, ACLs, prior-installer upgrade, Windows credential lifecycle and representative owner environment |
| Independent market-data authenticity | OPEN | Provenance, missing/revised records, independent comparisons and source rights |
| Scientific strategy and execution realism | OPEN | Chronological independent evaluation, held-out costs/drawdown, depth/liquidity sensitivity and sustained forward observations; no tuning on acceptance data |
| Sustained autonomous paper operation | OPEN | Multi-day real-feed operation, monitored incidents, restarts, resource pressure and recovery |
| Connected broker paper | OPEN | Authorized actual-account inspection/recovery, authenticated activity coverage, verified execution/fee links, broker-wide pacing and cash/position/hold reconciliation |
| Secure provider-held funding | NOT IMPLEMENTED | Hosted provider workflow, pending/settled transfer reconciliation and duplicate/failure/reversal handling |
| Governed live autonomy | BLOCKED | Accepted policy/integration, reconciled funds, hard risk caps, independent review and separate owner authorization |
| Computer-off cloud operation | PLANNED | Eligible free-tier host, authentication, encryption/key recovery, distributed ownership, remote cut and outage/quota trials |
| Independent security/risk acceptance | OPEN | External review, operational incident evidence and signed acceptance scope |
| 1.0 release approval | BLOCKED | Complete all agreed acceptance gates and obtain explicit owner release approval; live activation remains separate |

A green local trial or Windows workflow cannot check the remaining boxes. Read the generated ROADMAP.md for the complete staged roadmap; update this evidence through source review, never a runtime completion checkbox. Windows safety CI now runs the same bounded trial suite and retains its JSON report for 30 days alongside the existing browser evidence.

The v0.101 activity review also detects conflicting equal-time order snapshots, quantity regressions, terminal status/financial revisions and average-price changes without added quantity. It reports unions/gaps of declared creation windows only; an overlapping or gap-free request schedule never proves received activity coverage. Review is read-only, bounded to 1,000 distinct orders and does not repair, prune, link fees or authorize trading.

Windows regression blocker identified in v0.100: passive AI status called credential retrieval, which attempted OS access on Windows during readiness/To Do. The v0.101.1 correction uses nonblocking process-local hints, labels restart state as NOT_CHECKED and keeps actual request-time credential checks. The real HTTP trial injects the Windows-only gate on every host and covers provider status/readiness as well as To Do. This simulated branch is not native Windows acceptance; the fixed source still requires green Windows CI.

Passive AI metadata is also tested under an injected Windows-only credential policy on Linux. A previous “configured” hint may be stale and never grants request permission; explicit request-time retrieval remains required. The Portal labels NOT_CHECKED honestly after restart and keeps reviewed public-data requests separately confirmed. The new `ai_status_smoke.cjs` checks the actual controller, bringing the controller suite to ten. A Windows regression failure is recorded as a blocker until the corrected exact-source workflow passes; local reproduction/trials do not erase it.

Optional AI credential controls (v0.101.2) now reject untrusted origin/peer before secret parsing, use bounded duplicate-free JSON without framework input echoes, return no-store fixed errors, and run explicit OS actions outside the event loop. They bind the exact checked built-in Windows backend, verify save/delete readback and treat absent removal idempotently. Invalid OS records cannot enable research. Tests inject all OS operations; these changes do not certify actual Windows profile/ACLs, provider validity, key revocation or cancellation of in-flight requests. Independent security review remains open.

Credential failure trials also cover save/delete operations that change the injected OS store and then lose their acknowledgment. A failed reply is not proof of absence or rollback; an explicit retry performs fresh checks. No automatic deletion/rollback or retry is attempted. A concurrent ASGI trial verifies status stays responsive and vault-silent while an injected slow save is pending. This is local scheduling/failure evidence, not a hard timing guarantee or provider-key revocation test.

Installer smoke contract correction (v0.101.3): fresh passive startup must preserve missing accounting files. The installed checker now verifies the expected journal path after its explicit Emergency Stop write, then checks halt recovery, rather than requiring startup to create the journal. Expected halted/restored history must exist before launch. Source-process CLI regression covers fresh startup plus halted restart and missing-history refusal; native installer/default-AppData/reinstall/backup/uninstall checks remain mandatory.

Shared local pacing (v0.102) is optional/internal and has no default runtime path. Its 256-session cap and pending/auth/review blocks cannot be cleared automatically. A separately reviewed operator recovery workflow and actual broker-wide quota/account acceptance remain open; fixture success does not close the connected-paper gate.

Owned interruption (v0.103) adds explicit local account/head/token/OS-ownership-fenced review with an unknown outcome and full-day cooldown. Legacy unowned pending reads and completed auth/review blocks remain blocked; source/profile/account quota and independent recovery acceptance are still open. The operation never resumes automatically or clears uncertain original-order evidence.

Combined supplied balance review (v0.104) never closes the account/hold/spending gate. It compares total holdings separately from availability and open IOC term caps, with no network/storage/runtime registration. All actual broker coverage and financial reconciliation acceptance remains open.

Collection observations (v0.105) retain declared transport/page/cursor/time/content metadata inside atomic activity receipts. A transport label is not a durable source attestation; short pages, successful reads and hashes never establish complete coverage or order authority. Failed/incomplete collections do not write partial observations; absence of a receipt is not proof of no read attempt.

Head-bound gross balance diagnostics (v0.106) use retained explicit fills with nanosecond grouping and sampled head rechecks. Fee residual matches remain unallocated arithmetic; gross funding excludes every fee obligation and cannot establish spending permission. Actual account/source/reconciliation and final release gates remain open.

Separated inventory observation (v0.107) adds reported USD cash/non-margin buying power and total/available BTC to the existing explicitly confirmed read. Unknown currency prevents USD projection; missing availability stays unknown and unsupported holdings suppress BTC totals. The unavailable difference is not an authenticated hold. Normalized fingerprints and sampled read timing are not source attestations or complete account snapshots. The eleventh injected trial checks these distinctions; actual account/hold/spending and release acceptance remain open.

Portable observation review (v0.108) adds explicit private JSON download plus bounded offline shape/arithmetic/fingerprint checks. A self-consistent rewritten manifest is still unverified; no signature, provider identity, freshness, activity completeness, hold, spendable-funds or routing acceptance is established. Browser/controller checks bind exported fields to the displayed read and clear stale exports; actual new-source rendered/native Windows validation remains required.

Portable observation/activity comparison (v0.109) binds two validated manifests to an explicit account/history head and treats each sequential read as an uncertainty window. Retained boundary fills, overlapping/reversed windows, mixed source labels or missing totals block its gross balance comparison. Exact nanosecond boundary checks and final full replay do not establish quiet windows, complete activity, fee links, spending or source authority. The twelfth offline profile covers this conditional diagnostic and its refusal cases; all actual account/funding/live/cloud/release gates remain open.
