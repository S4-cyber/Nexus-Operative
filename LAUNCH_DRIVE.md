# Nexus Operative — Main Launch Drive: v1.0

**Primary objective:** finish, validate and package a reliable, easy-to-use Windows Nexus Operative v1.0.0. **Secondary parallel objective:** expand the researched opportunity universe globally, as documented in [EXPANSION_INITIATIVE.md](EXPANSION_INITIATIVE.md). Breadth must not delay closure of primary readiness defects.

## Core automated-execution ambition

Nexus is intended to evaluate public information, rank independently eligible opportunities, and eventually manage orders more quickly and consistently than manual human operation. This requires bounded machine response time, not a promise of outperforming prices, undercutting other traders, guaranteed fills, or profit. Exchange rates, market data latency, network conditions, queue priority, adverse selection, fees, API rate limits, and order-book depth determine actual outcomes.

### Critical path to a usable v1.0
1. **Green Windows build and recovery** — reproducible unit/integration tests, well-formed PORTAL, installer smoke test, durable paper data, safe halt/restart.
2. **Wide but disciplined data observation** — authenticated provenance of public feeds, bounded caches, timestamp alignment, missing-data veto and multi-source checks. Scan many markets without flooding APIs.
3. **Deterministic low-latency signal-to-decision pipeline** — asynchronous bounded event queue, deduplication, monotonic timing, stale-signal expiry, fixed risk budgets and predictable overload behavior. Decisions remain research/paper-only until separate approval.
4. **Execution lifecycle simulator** — independent order-intent IDs, simulated acknowledgment / partial fill / cancellation / rejection, idempotency, conservative spread+fee+slippage, and durable ledger reconciliation. Reject any unverified or stale input.
5. **Latency and capacity validation** — p50/p95/p99 local decision delays, burst handling, simulated exchange delays, worst-case lost/duplicate/out-of-order messages and emergency stop behavior. Local timing is not exchange fill speed.
6. **Scientific market performance evidence** — fixed strategies tested with chronological holdouts, cross-regime/volume stress, survivorship/delisting checks, cost sensitivity and recorded failed candidates.
7. **Autonomous paper forward operation** — scheduled research, simulated position sizing, risk veto and monitoring with no need for constant manual interaction; repeated restarts and interrupted-network drills.
8. **Safety and operational signoff** — protected secrets, explicit provider permissions, independent code/security review, incident response, audited controls, installer and operator documentation.
9. **Explicit release authorization** — preserve 0.x until all v1.0 gates have evidence and the user approves. Any real-money activation separately requires explicit authorization, broker agreements, legal/venue eligibility and certified order routing.

### What is *not* being activated today
No broker account, live funds, exchange placement, real orders, leveraged execution or money movement. Deterministic simulations can be automated without that authorization.

### Timing targets are acceptance tests, not marketing claims
Set local reaction-time budgets for decision and risk evaluation; measure p50/p95/p99 under synthetic event bursts in CI. Do not advertise millisecond market access until true market-data-to-acknowledgment traces demonstrate it, and never claim to be faster than competing trading systems without comparison evidence.

## Standing sprint rule
Each hourly sprint should **first fix CI/build regressions and then complete the largest coherent unmet v1.0 launch gate** with test coverage. Allocate remaining capacity to global expansion as a research/discovery track. Do not increase a feature version just for plans or cosmetic metadata. Do not trade readiness for adding more listings.

## First external execution service decision (planned)
Use **Alpaca paper** as the first third-party broker API adapter: separate paper credentials and endpoint, broker order-ID reconciliation, provider-specific rate limits and asset eligibility. Add an IBKR paper adapter for expanded stocks/FX/options/futures after sessions and entitlements are verified; consider Coinbase Advanced as a separate authenticated crypto execution venue. Until explicit user permission, these adapters stay disconnected; they are not substitutes for independently tested execution safeguards.

The provider-neutral `nexus/order_gateway.py` models unique client order IDs, no-network simulated acknowledgement, partial/full fill events, rejection, cancellation and emergency cancellation. Ordered incremental synthetic fills track explicit fees; duplicate events are idempotent, conflicting replays and overfills are rejected, and cancellation preserves partial fills. The PORTAL uses a transactional SQLite command journal (`nexus/durable_orders.py`) to replay and validate offline state after restart, including IDs, fills, fees, cancellations and halt/reset. Lifecycle events are not exposed as a public API or wired into the portfolio ledger. Quantity/price-based portfolio reconciliation, price/spread/slippage modeling and broker-confirmed fills remain unfinished. It does not manage real capital or hold actual assets.

## v0.49: 30 local decisions/second capacity target
The bounded no-network `nexus/decision_lane.py` benchmark tests pure in-process synthetic research decisions. It reports local throughput, p50/p95/p99 timing, staleness and queue overflow, and **no broker orders or fills**. The user desires roughly 30 or more completed market moves per second in the long run; do not conflate this with internal processing. Provider rate limits, costs, available liquidity, matching engine processing, order acknowledgments and market impact require separate end-to-end validation. Build streaming feeds, durable lifecycle and venue-aware pacing before claiming exchange throughput. Alpaca Trading API generally 200 requests/min/account by default; IBKR TWS commonly up to 50 client messages/second; IBKR Web API default 10 requests/sec; verify applicable conditions/headers with actual provider docs and entitlements.


## Release readiness review — v0.51.0

These are evidence-based stages, not a percent-complete estimate or release date.

| Stage | Current evidence | Remaining acceptance work |
| --- | --- | --- |
| Windows foundation | Automated test, EXE startup and installer workflows exist | Clean-machine install/upgrade/uninstall and recovery drills; record artifact checksums |
| Tier authority | Terrestrial/Solar/Universal entry controls and exit behavior have regression coverage | Verify them throughout the autonomous buyer/seller loop |
| Order lifecycle | Offline partial/full fills, fees, rejection, idempotency and cancellation tests | Price/quantity fills with realistic execution costs and expiry |
| Durable recovery | SQLite transactions preserve commands; replay validates transitions; halt survives restart | Ledger linkage and reconciliation; crash/process-kill drills; retention/checkpoint design |
| Low-latency execution | Bounded local decision benchmark | Measure durable end-to-end paper path and venue pacing, not just decision throughput |
| Data and AI research | Provenance, data checks, model adapters and holdout/stress modules exist | Independent audits and recorded provider/failure/cost evidence |
| Paper autonomy | Paper portfolio and risk gates exist | Integrated unattended forward runs, monitored restarts and network-failure exercises |
| Release signoff | Release gates documented | Independent security/risk review, operator runbook and owner acceptance |

### Next coherent milestones, in order
1. Add explicit simulated execution price/quantity to lifecycle fills and atomically reconcile each fill with the paper ledger, including duplicate delivery and partial cancellations. Never infer holdings from reference notionals.
2. Replace full command replay on every write with validated checkpoints/indexed state, preserving restart correctness and audit history. Benchmark durable latency and test controlled process interruption.
3. Join streaming observations, tier permissions, risk vetoes, lifecycle accounting and monitoring into an unattended paper loop. Keep 5-second broad refresh, 15-second ranking and top-30 AI candidates with adaptive acceleration.
4. Implement the Alpaca paper adapter only within explicitly authorized paper scope; verify IDs, provider pacing, disconnected recovery and credentials. Follow with IBKR paper and Coinbase research as separate milestones.
5. Collect forward-run and research evidence, complete installer/recovery/security acceptance, then request 1.0 release authorization.

### Recovery operation and limits
`GET /api/execution/recovery` returns validated offline order state and an explicit `portfolio_reconciled: false`. The SQLite journal is stored under `NEXUS_PORTABLE_DATA` (default `~/.nexus-operative`). Writes acknowledge only after transaction commit; invalid transitions roll back. Independent writers serialize through SQLite. Startup restores the halt latch and stays paused; invalid recovery fails closed. Reset cannot revive terminal orders.

The journal is bounded to 10,000 state-changing commands, reserving 1,001 slots from ordinary intake for control operations. Repeated idempotent requests do not consume slots. Full replay is conservative and is **not yet evidence of the desired durable throughput**. Recovery validates lifecycle structure, not malicious database rewriting or actual ownership. Existing in-memory orders from older running processes cannot be recovered retroactively. Global market expansion stays secondary to these launch blockers.

## v0.52.0 — priced synthetic execution accounting

The durable gateway now supports internal `initialize_account` and `simulate_priced_fill` commands. Explicit quantity, price and fee establish reference notional, cash debit/credit and weighted acquisition cost. Partial buys accumulate quantity; partial sells reduce quantity without shorting. Lifecycle transitions and derived accounting share one committed command, so failed writes cannot acknowledge a fill and duplicate commands cannot change balances twice. Restart reconstructs both from committed commands.

This is a **separate synthetic execution account**, reported under `execution_account` by the recovery endpoint. It does not update or combine with the existing public-quote paper portfolio. That existing portfolio's reconstruction rules currently assume whole-position operations; extending it to partial fills needs its own migration and compatibility review. `portfolio_reconciled` therefore remains false. Historical fills without price/quantity stay explicitly unreconciled and never create invented holdings. No priced-fill mutation endpoint or automated trading loop is exposed.

Validation covers partial buys/sells, restart, concurrent duplicate delivery, conflicting execution details, overfills, overselling, insufficient cash, invalid numbers and failed-commit rollback. Next: unify the two accounting paths with compatible position-event reconstruction and tier/risk enforcement, then measure durable throughput and process-kill recovery. The internal priced-fill helper alone does not satisfy the complete portfolio reconciliation gate.
