# Nexus Operative — read-only 1.0 roadmap

Revision: 0.85.0. Branch: `development/windows-portal-foundation`.

Generated from `nexus/release_roadmap.py`. View in PORTAL or `GET /api/roadmap`. Changes require source review; the application has no roadmap write endpoint.

| Milestone | Status | Evidence | Remaining acceptance |
| --- | --- | --- | --- |
| M1: Windows packaging and recovery | PARTIAL | Windows test, EXE startup and installer workflows; durable order recovery; installed-process/reinstall/uninstall smoke and distribution checksum tooling; verified offline backup/new-directory restore tooling and fresh-process linked-ledger/quote-evidence recovery drill and installed-API restored-data smoke tooling with pinned prior-version fixture support and default Windows data-layout checks; documented beginner help for all PORTAL controls and isolated headless-browser acceptance tooling; seven named workspaces and an automatic read-only beginner startup checklist with saved-state observations and optional/future setup distinctions. | Exact-SHA v0.84.0 installer/reinstall/uninstall and pinned v0.64 data recovery steps passed; record clean-machine native-window, ordinary-user profile/ACL and prior-installer upgrade acceptance. |
| M2: Market data and provenance | PARTIAL | Public observation, timestamp/gap checks, historical admission and source diagnostics; redirect-disabled public transport with exact response-URL checks; canonical compressed hub bars with cross-collector deduplication, conflict quarantine and hash-verified sealed datasets; bounded public candle collection selected by default on explicit Portal start, closed-boundary request coalescing, pre-network storage checks and receipt-time clock/stop fencing. | Independent provenance review; missing/revised/conflicting data and outage evidence. |
| M3: Decision speed and risk authority | PARTIAL | Bounded local research decision queue and retained-ID limits with invalid-clock rejection; disposable bounded linked-ledger burst timing, overflow/expiry and halt flushing; individual Terrestrial/Solar/Universal entry permissions. | Measure full durable path under bursts; verify expiry, overload and permissions throughout autonomy. |
| M4: Order lifecycle and portfolio reconciliation | PARTIAL | Partial/full priced fills, fees, idempotency, cancellation, durable recovery; atomic opt-in paper-ledger linking, partial-position reconstruction, adverse quote-cost assumptions, post-lock runtime exchange/receipt freshness and expiry/stop checks, durable expiry, cleanup-failure pauses and forward process-death tests before/after fill commit. | Extend interruption/quote-path stress; independently review execution assumptions, existing-account migration and reconciliation. |
| M5: Durable latency and capacity | PARTIAL | Replay-validated checkpoints, two-snapshot retention, checkpoint interruption tests and isolated gateway and linked-ledger/tier-inclusive p50/p95/p99 benchmark; capacity admission diagnostics; opt-in atomic quote-order fixture treatment with retained commands and crash rollback tests; explicit logical archival with retained IDs and commands. | Physical offload/long-run scalability, sustained queue pressure and independent archival acceptance; no broker throughput validated. |
| M6: Scientific strategy evidence | PARTIAL | Chronological research, holdout, regime and cost-stress modules; persisted multi-horizon ridge momentum baselines with training-only scaling, purged chronological holdout, naive/momentum comparisons, generation invalidation and compact research signals; three built-in trained feature specialists with fixed three-block baseline checks, disagreement reporting and independent deterministic validation/provenance reviewers; versioned saved research packs and unchanged-data reuse; three expanding-window refits with purged training targets, delayed nonoverlapping modeled fills, fixed cost scenarios and fold-consistency checks; shared causal feature computation and serialized preparation; engine-started bounded learning with captured public/saved-only scope, bounded polls/fit attempts, generation skips, local OS ownership and stop-fenced publication. | Independent model calibration and walk-forward/forward evidence, licensed shared-source provenance, delisting/survivorship review and performance audit; trained baselines are not validated market edge. |
| M7: Unattended paper operation | PARTIAL | Explicitly started BTC public-quote paper runner; separate research-engine and explicit Engage Paper controls, independent emergency throttle cut preserving research, reset/start race fencing, tier-gated entries, exits, halt/pause, feed-failure stop, durable session counters/heartbeats and fingerprinted fixed-policy admission; retained quote evidence, quote/fill price/quantity/fee/policy crosschecks, portable review summaries and exclusive local process ownership, clock-discontinuity pauses and exchange-regression warmup fencing and retained-storage preflight with operator runbook; bounded repeatable forward-runner fixture drill with fresh-process evidence comparison; operator recovery snapshot and pending-remainder inspect/cancel controls; Singularity account-equity and Fractal per-holding observed history with retained ranges and journal adjustment inspection and transactionally retained closed-day observation history with bounded active/total admission and range responses plus observation database byte/free-disk budgets and rollback-safe allocation failure handling. | Run sustained real-feed forward validation, independent policy evidence review and multi-market scheduling; injected outage tests are not market performance evidence. |
| M8: Broker paper integration and operations | OPEN | Provider-neutral design plus disconnected Alpaca paper IOC-limit previews and bounded identity/status/cumulative-fill diagnostics and explicit execution/asset-fee balance projections with repeatable fixtures; external adapters remain disconnected. | Alpaca paper first after explicit paper-connection authorization; durable broker IDs, pacing, account/asset eligibility and asset-denominated fee/position reconciliation. IBKR paper then Coinbase Advanced expansion. |
| M9: Release acceptance and authorization | OPEN | 0.x numbering and release gates enforced. | Independent security/risk review, operator runbook, complete acceptance evidence and explicit owner approval for 1.0. Live-money activation requires separate authorization. |

## Standing target settings (verify actual runtime separately)

- broad_refresh_seconds: 5
- ranking_seconds: 15
- ai_candidate_count: 30
- adaptive_acceleration: True
- autonomous_paper_first: True

## Next sequence

1. Validate linked-ledger recovery, risk vetoes and reconciliation under repeated interruption.
2. Extend linked quote-model stress and independently review cost, liquidity and expiry assumptions.
3. Extend sustained-load admission and recovery; validate physical offload and long-run costs while preserving IDs and audit evidence.
4. Validate the opt-in BTC paper runner across sustained public feeds, outages and restart; integrate broader research-backed admission.
5. Validate sustained local candidate refresh and independent walk-forward/cost evidence and reviewed optional local-weight runtime upgrades; never promote learned output automatically.
6. Finish provider-paper integration, forward evidence, packaging and independent release acceptance.
7. Expand markets incrementally through verified data, legal eligibility, cost and paper-evidence gates.
8. Evaluate free-tier computer-off hosting and secure headless operation without assuming anonymous hosting, free unlimited compute or automatic paid upgrades.

## Explicit expansion and cloud goals

- G1: Global opportunity expansion — PLANNED. Broaden assets, markets and providers through the existing 12-avenue expansion initiative; validate source rights, eligibility, liquidity, costs and reproducible paper evidence before enabling each route.
- G2: Computer-off cloud operation — PLANNED_NOT_DEPLOYED. Run the headless paper engine independently of the desktop, with authenticated remote observation, durable reconciliation, one execution owner, encrypted transport/storage/backups, credential isolation and tested shutdown/recovery.
  Cost preference: Free-tier only; avoid a separate dedicated account where a supported existing account can be reused. No verified anonymous always-on option selected; never silently enable billing. Acceptance: Prove durable storage, quota/outage behavior, remote halt, desktop/cloud fencing, key recovery and multi-day computer-off paper operation. See CLOUD_OPERATION.md.
- G3: Collective Matrix data and prediction hub — LOCAL_FOUNDATION_CLOUD_NOT_DEPLOYED. One authenticated central public-data unit with canonical venue/product/interval/time identities, deduplicated ingestion, conflict quarantine, compact read-only snapshots and versioned model evidence. Local bounded hub and trained momentum baselines are implemented; global instance synchronization, source rights/attestation, cloud encryption, stronger model acceptance and governed paper execution remain open. See MATRIX_HUB.md.

Aim for roughly 30+ market moves/second in the long term; internal throughput is not broker acknowledgments or fills.

## Resume after a conversation cutoff

Read AGENTS.md, ROADMAP.md, LAUNCH_DRIVE.md and VERSIONING.md; verify remote branch head, CI and current source before editing.

Run `python -m pytest -q`, Python compilation and PORTAL JavaScript validation. Record exact commits and CI outcomes. Keep 0.x until release gates pass and owner approval is explicit. Never describe an unverified gate as complete.
