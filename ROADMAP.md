# Nexus Operative — read-only 1.0 roadmap

Revision: 0.63.0. Branch: `development/windows-portal-foundation`.

Generated from `nexus/release_roadmap.py`. View in PORTAL or `GET /api/roadmap`. Changes require source review; the application has no roadmap write endpoint.

| Milestone | Status | Evidence | Remaining acceptance |
| --- | --- | --- | --- |
| M1: Windows packaging and recovery | PARTIAL | Windows test, EXE startup and installer workflows; durable order recovery; installed-process/reinstall/uninstall smoke and distribution checksum tooling; verified offline backup/new-directory restore tooling. | Collect passing installed-process CI evidence; record clean-machine native-window and cross-version upgrade/recovery acceptance. |
| M2: Market data and provenance | PARTIAL | Public observation, timestamp/gap checks, historical admission and source diagnostics. | Independent provenance review; missing/revised/conflicting data and outage evidence. |
| M3: Decision speed and risk authority | PARTIAL | Bounded local decision queue; individual Terrestrial/Solar/Universal entry permissions. | Measure full durable path under bursts; verify expiry, overload and permissions throughout autonomy. |
| M4: Order lifecycle and portfolio reconciliation | PARTIAL | Partial/full priced fills, fees, idempotency, cancellation, durable recovery; atomic opt-in paper-ledger linking, partial-position reconstruction, adverse quote-cost assumptions and durable expiry. | Extend interruption/quote-path stress; independently review execution assumptions, existing-account migration and reconciliation. |
| M5: Durable latency and capacity | PARTIAL | Replay-validated checkpoints, two-snapshot retention, checkpoint interruption tests and isolated gateway and linked-ledger/tier-inclusive p50/p95/p99 benchmark; capacity admission diagnostics; explicit logical archival with retained IDs and commands. | Physical offload/long-run scalability, sustained queue pressure and independent archival acceptance; no broker throughput validated. |
| M6: Scientific strategy evidence | PARTIAL | Chronological research, holdout, regime and cost-stress modules. | Reproducible forward evidence, delisting/survivorship review and independent performance audit. |
| M7: Unattended paper operation | PARTIAL | Explicitly started BTC public-quote paper runner; tier-gated entries, exits, halt/pause, feed-failure stop, durable session counters/heartbeats and fingerprinted fixed-policy admission; retained quote evidence, quote/fill crosschecks and portable review summaries with operator runbook. | Run sustained real-feed forward validation, independent policy evidence review and multi-market scheduling; injected outage tests are not market performance evidence. |
| M8: Broker paper integration and operations | OPEN | Provider-neutral design; external adapters remain disconnected. | Alpaca paper first after explicit paper-connection authorization; broker IDs, pacing, eligibility and reconciliation. IBKR paper then Coinbase Advanced expansion. |
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
5. Finish provider-paper integration, forward evidence, packaging and independent release acceptance.

Global expansion is a supporting track. Aim for roughly 30+ market moves/second in the long term; internal throughput is not broker acknowledgments or fills.

## Resume after a conversation cutoff

Read AGENTS.md, ROADMAP.md, LAUNCH_DRIVE.md and VERSIONING.md; verify remote branch head, CI and current source before editing.

Run `python -m pytest -q`, Python compilation and PORTAL JavaScript validation. Record exact commits and CI outcomes. Keep 0.x until release gates pass and owner approval is explicit. Never describe an unverified gate as complete.
