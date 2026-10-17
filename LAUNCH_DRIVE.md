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

## v0.53.0 — linked reconciliation, crash recovery and bundled roadmap

The current continuation contract is [ROADMAP.md](ROADMAP.md), generated from `nexus/release_roadmap.py`. PORTAL provides a read-only Roadmap to 1.0 view and `GET /api/roadmap`. `AGENTS.md` tells future development sessions where to resume. These are bundled source references; no runtime roadmap editing is exposed. Earlier sections above are historical milestone records, not current status overrides.

An explicit internal `link_portfolio()` operation can now link a new execution account to a new or unused matching paper ledger. Existing traded accounts are rejected rather than auto-merged. Linked priced fills write the execution command and paper-ledger details in one SQLite attached-database transaction with FULL synchronization and rollback journals. The governor reconstructs partial buys/sells, verifies fill price/quantity/fee against cash deltas, and includes execution details in transaction seals. Unpriced fills cannot enter linked accounts.

Linked entry fills require their individual risk tier to be engaged and within its intent cap. Existing portfolio position and aggregate exposure limits remain active. Disengaged tiers still allow exits and duplicate replay. Quote-driven manual writes are blocked after explicit linking to avoid competing accounting paths. Recovery compares the reconstructed execution account with the sealed paper ledger before reporting `portfolio_reconciled: true`; this remains local simulation evidence only.

Completed component checks include partial fills and partial cancellation, independent cash/position detail checks, duplicate replay after tier disengagement, two-database rollback on failed commit, and a subprocess forcibly terminated after portfolio writes but before command insertion/commit. Restart rolls back both databases; retry records one fill. Emergency halt can still persist despite a portfolio discrepancy; reset remains vetoed until integrity is restored.

Remaining M4 acceptance: wider spread/slippage/expiry scenarios, repeated interruption stress, independent migration/reconciliation review, and integration into the unattended loop. Full replay remains a capacity bottleneck; M5 checkpoints/retention and durable p50/p95/p99 measurement remain next. No public priced-fill/link mutation endpoint or external broker connection was added.

## v0.54.0 — bounded synthetic quote execution and expiry

The internal quote-fill model uses explicit synthetic bid/ask, available quantity and observation/evaluation timestamps. Buys use ask plus adverse slippage; sells use bid minus adverse slippage, with explicit per-fill fees. Inputs must be finite, positive where applicable, no older than two seconds, uncrossed and within a 150 bps spread bound. Fee/slippage assumptions are bounded at 100 bps each. These are test assumptions, not measured depth, queue priority, market latency or actual fills.

Execution terms are immutable once set (maximum one-hour lifetime) and require an unfilled order. Timed orders cannot bypass the quote path through raw reference fills or direct priced fills. Evaluations cannot precede order creation or regress behind earlier modeled evaluations. Equivalent numeric inputs replay idempotently; conflicting quote/cost assumptions are rejected.

Expiry is a durable terminal transition at the deadline. It preserves any existing partial quantity, fees and cash accounting. Single-order and bounded batch expiry share the journal transaction; repeated expiry is idempotent, restart preserves it and reset does not reopen expired orders. Linked quote fills use the same atomic ledger bridge, risk-tier entry checks and exposure governor. Disengaged tiers still permit modeled exits and historical duplicate replay.

The roadmap UTF-8 read test was corrected after Windows CI detected locale-dependent decoding. No broker/network integration or public mutation endpoint was added. Next critical path: validated checkpoints/retention and full durable-path latency measurements, then the integrated unattended paper loop. M4 remains partial pending extended stress and independent acceptance.

## v0.55.0 — validated warm checkpoints and durable latency diagnostic

The journal now saves a JSON checkpoint every 64 state-changing commands in the same transaction as the command and any linked ledger writes. It retains the two latest checkpoint snapshots and all command history. Checkpoint write failure rolls back the command/fill as well. No pickle, automatic history deletion or journal-cap bypass is used.

A fresh process validates the saved state through full prefix replay. A bounded four-entry process cache subsequently restores that verified state only when the exact snapshot and retained command-prefix digest still match. Tail commands are replayed normally; changed prefix payloads, invalid sequence bounds or inconsistent snapshots fail closed. Checkpoints speed warm reconstruction; cold recovery still performs full prefix replay and every warm check still reads/hashes retained prefix payloads. Local checksums cannot prevent coordinated database rewrites.

Tests cover cold/warm equivalence to full replay, tail processing, bounded snapshot retention, modified snapshot/history rejection, rollback of linked fills when checkpoint insertion fails, and forced process death during checkpoint insertion. The existing journal limit remains 10,000 commands, with ordinary intake reserving 1,001 control slots. Safe long-run archival still needs design and acceptance evidence.

PORTAL offers an explicit disposable-account durability benchmark (`POST /api/execution/durable-benchmark`, 1–30 cycles, default 16). It compares full replay with checkpoints across submit, timed quote fill and partial cancellation, and reports local commit-return p50/p95/p99, command throughput and cold/warm recovery. Treatment order is fixed and this is a diagnostic, not a statistically controlled speedup claim. It excludes the linked portfolio, live tier validation, network, broker acknowledgments and actual fills. It neither changes application accounts nor validates the desired market-moves-per-second target.

Next: linked-ledger/tier-inclusive measurement, bounded sustained-load handling and journal archival, followed by unattended buyer/hold/seller integration. The read-only roadmap marks M5 partial, not complete.

## v0.56.0 — linked measurement and capacity admission diagnostics

The disposable benchmark now compares four treatments: gateway-only/full replay, gateway-only/checkpoint, linked-ledger/full replay, and linked-ledger/checkpoint. Linked treatments execute the real per-tier entry checks, integrity governor and atomic fill-to-ledger reconciliation against separate disposable accounts. Buy/exit cycles end in partial cancellation with no remaining holdings. The measurements still exclude feed acquisition, strategy evaluation, event-queue delays, network, broker acknowledgments and actual fills. Fixed treatment order and short runs are diagnostics, not statistically controlled performance claims.

Disposable tier/settings databases are scoped through a ContextVar and reset in a finally block. The benchmark never changes the process environment, real account configuration or user tier permissions. Cross-thread isolation and exception-reset tests cover this scope. Settings and permission connections now close explicitly, including before Windows temporary-directory cleanup.

Execution recovery includes a read-only capacity snapshot, also viewable in PORTAL. It reports command/intention counts and limits, ordinary writes remaining, reserved control capacity and admission reasons. New entries stop at their bounded journal or historical-ID limits. Tests demonstrate that duplicates still replay without consuming commands, while remaining cancellation, expiry and halt operations can use control slots. Cancelled order IDs remain reserved for idempotency. No automatic archival, deletion or cap bypass was added.

Next: sustained-load and concurrent admission testing, safe audit-preserving journal archival, and integration of the monitored unattended paper loop. M5 remains partial; temporary throughput figures do not establish real market execution speed.

## v0.57.0 — explicit archival, bounded epochs and grouped operation drills

Completed orders can now be logically archived through an explicit maintenance operation while the execution gateway and PORTAL are halted and paused. A reconciled, flat account is required; holdings are never liquidated by maintenance. Active IDs move into retained state with their full lifecycle events, priced executions, quote assumptions and immutable terms. Each batch records its IDs, journal sequence boundary and SHA-256 evidence digest. Old submissions and fill deliveries still replay idempotently; conflicting reuse remains rejected. Maintenance never resumes PORTAL.

All commands remain in the same SQLite journal with uninterrupted global sequence numbers. Only the active admission epoch resets, transactionally with archival. The limits are now explicit and separate: 10,000 commands per active epoch (1,001 reserved from normal intake for control operations), 100,000 retained commands, 1,000 active IDs, 10,000 archived IDs and 100 archive batches. Reaching retained-history limits still stops intake; this is bounded continuity, not indefinite operation or physical compaction. There is no automatic deletion or archival. The previous checkpoint schema remains readable by normalizing absent archival fields before comparing against full prefix replay.

PORTAL includes explicit archival maintenance and a disposable scripted operation drill. `POST /api/execution/archive-terminal` requires confirmation and halted/paused state; `GET /api/execution/intent/{id}` retrieves active or archived order evidence read-only. `POST /api/execution/operation-drill` accepts 1–24 cycles (default 12) and groups synthetic BUY/HOLD/SELL, a disengaged-entry veto, allowed exit, partial cancellation, halt, archive and gateway-instance reconstruction. Its settings and accounts are disposable; it is not live-data strategy autonomy, market-performance evidence or a network-outage drill.

Tests cover archived ID/fill replay and conflicts, flat/halt requirements, failed archival commit, corrupt sequence boundary, legacy checkpoint compatibility, 24 linked operation epochs and 48 preserved order IDs, plus process death during the archive checkpoint write. That forced death preserves the old halted epoch, commands and accounting on restart. The read-only roadmap carries these completed component checks forward.

Next: physical history offload/scale measurements, bounded scheduling and live-data paper-loop integration, repeated network-failure drills, then sustained forward evidence and independent release acceptance. M5 stays partial and M7 stays open until those broader gates have evidence.

## v0.58.0 — opt-in BTC public-quote paper forward runner

A separate experimental runner can now connect public quote observations to modeled BUY/HOLD/SELL execution through the linked ledger. The read-only observer retains its original no-order behavior. The new runner defaults stopped; explicit session confirmation, active/unhalted PORTAL and compatible linked accounting are required. New entry sessions require an engaged selected tier; existing BTC holdings may resume exit-only monitoring while that tier is disengaged. Fresh accounting may be initialized from the paper budget, but pre-existing traded/unverifiable accounts are not auto-merged. Sessions reject unsupported holdings or outstanding orders requiring review.

Scope is deliberately narrow: BTC-USD polling every five seconds, entry evaluation every 15 seconds, six distinct observations over at least 25 seconds, and a fixed experimental 30 bps momentum entry threshold. Position exits use bid-to-cost-basis moves of ±50 bps or an initial-capital loss threshold. Entries are capped at the stricter of $50, 1% of initial paper capital and existing tier/governor vetoes. Liquidity is assumed at the modeled quantity; adverse slippage and fees still apply. This is not a validated strategy, independently attested feed or real execution model. The broader 30-candidate AI/ranking and adaptive research objectives remain separate unfinished work.

Quotes require matching BTC identity, timezone-aware exchange timestamps, valid public provenance, two-second receive freshness, ten-second exchange freshness, bounded spreads and the feed guard. Duplicate/regressing exchange observations cannot trigger new entries. Three consecutive invalid/unavailable quotes pause the session; storage/integrity/capacity failure also pauses. Initial entry admission reserves an additional order ID and journal writes for an exit. Disengaged tiers continue to allow exits. Stops preserve holdings; they never silently liquidate or restart.

The order sequence is fenced so stop acknowledges after any in-flight local execution finishes. PORTAL pause, emergency halt, halt reset and application shutdown stop the runner. Restart reconstructs accounting but does not restore running permission. Cycle decisions and vetoes are retained in a separate bounded 1,000-row diagnostic log; execution history is not trimmed.

Tests inject timestamped quotes to exercise buy, hold, exit, tier veto, duplicate ticks, stale/provider failures, identity/timezone rejection, stop during fetch, reserved exit capacity, failed start diagnostics, API opt-in, pause/halt and shutdown. No public-feed session was started as part of this implementation. M7 is partial: sustained public-data forward runs, outages, independent strategy evidence and broader scheduling remain required.
