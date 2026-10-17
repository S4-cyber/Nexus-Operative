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

The provider-neutral `nexus/order_gateway.py` now models unique client order IDs, no-network simulated acknowledgement, cancellation and emergency cancellation; it does **not** simulate matched fills, manage real capital or hold actual assets. This is an early protocol boundary, not launch readiness.

## v0.49: 30 local decisions/second capacity target
The bounded no-network `nexus/decision_lane.py` benchmark tests pure in-process synthetic research decisions. It reports local throughput, p50/p95/p99 timing, staleness and queue overflow, and **no broker orders or fills**. The user desires roughly 30 or more completed market moves per second in the long run; do not conflate this with internal processing. Provider rate limits, costs, available liquidity, matching engine processing, order acknowledgments and market impact require separate end-to-end validation. Build streaming feeds, durable lifecycle and venue-aware pacing before claiming exchange throughput. Alpaca Trading API generally 200 requests/min/account by default; IBKR TWS commonly up to 50 client messages/second; IBKR Web API default 10 requests/sec; verify applicable conditions/headers with actual provider docs and entitlements.
