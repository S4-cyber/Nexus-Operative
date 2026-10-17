# Nexus Operative release acceptance

Current build: **0.100.0 prerelease**. Production 1.0 remains blocked. This checklist records evidence scope, not runtime permission. No claim that an environment is perfect or a strategy will be profitable follows from passing these checks.

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
| HTTP startup/shutdown | Actual loopback TCP server/lifespan and status, roadmap, To Do, root redirect markup and Portal HTML requests | Fresh source-process startup stays stopped, creates no accounting data and performs no external connection/vault access during checked reads; server shuts down | Rendered browser interaction, installed Windows desktop behavior, existing-account migration or actual provider access |

The HTTP trial denies/counts non-loopback connections and keyring operations in its disposable child. It checks the actual Portal asset separately from root meta-refresh markup. Other trial profiles use the established injected/scripted fixtures; these are not claimed to have a system-wide network sandbox. Sample counts and modeled liquidity are explicit. Zero exit alone does not certify a profile. No trial grants order, funding, model promotion, resume or release authority.

## Current evidence and open boxes

| Release requirement | Status | Evidence / next acceptance |
| --- | --- | --- |
| Source regression, controller help and syntax | Automated checks available | Full regression, compilation, nine Node controllers and dependency check are recorded in LAUNCH_DRIVE.md for each push |
| Integrated local trials | Bounded offline trials passed | Source-fingerprinted Linux report in `validation/release_trials_0_100_0.json`; exact new-source Windows trials must still run |
| Windows packaging and data recovery | Predecessor verified | v0.98 exact commit `65763dd5f9ed4b7367f064a1bf1b25023c114fa0` packaging run39075131403/job117521122766 succeeded; v0.99/new-source evidence tracked separately |
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
