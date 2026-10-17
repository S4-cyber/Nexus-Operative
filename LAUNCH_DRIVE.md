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

## v0.59.0 — forward-session evidence and outage admission hardening

Forward cycles now atomically update a durable session summary and its diagnostic event. Summaries retain started/updated times, decision counters, last event, stored state and the exact policy parameters/fingerprint. Stop, pause/halt/reset and shutdown carry explicit reasons. The 1,000-row rolling cycle log remains diagnostic; up to 1,000 session summaries are retained without automatic deletion, after which new sessions are vetoed. The order journal and sealed portfolio remain the execution/accounting source of truth: session counters are not independently anchored proof of fills.

Read-only session evidence identifies stale heartbeats, previous/interrupted running sessions and a stopped worker without a final persisted record. These findings require review; neither reading evidence nor restarting the process grants running permission. PORTAL includes session-evidence review. `GET /api/paper/forward/sessions` and `GET /api/paper/forward/policies` expose the same diagnostics and fixed-policy catalogue.

`nexus/paper_policy.py` centralizes a versioned immutable experiment definition and SHA-256 parameter fingerprint. Only its explicit ID is admitted by the forward runner. Research branch IDs, arbitrary IDs, AI-generated policy identifiers and unrecognized start-request fields cannot alter thresholds or grant execution authority. A successful research ranking still does not promote any strategy automatically. The admitted experiment remains unvalidated and paper-only.

Unavailable/malformed observations and long observation gaps clear the entry history; recovery must accumulate fresh warmup observations. None/non-dict quotes, missing timestamps, stale observations and provider errors follow the same bounded failure pause. Tests exercise temporary outage recovery, long-gap warmup reset, three failures while holding, paused persisted state, explicit tier-off exit resume, session-counter transaction rollback, restart/stale evidence, immutable policy copies, unknown policy rejection, API bounds and session-cap admission.

No real-feed forward run was started for this batch. Next remains sustained public-data/outage evidence, independent strategy review and bounded broader scheduling. M7 remains partial; green injected-data tests do not close forward-operation or profitability gates.

## v0.60.0 — forward quote provenance and execution-evidence review

Each fetched forward observation is now recorded before it can cause a modeled fill. Evidence contains only allowlisted quote fields, evaluation/cycle/session identifiers, eligibility outcome, source classification and policy fingerprint. Standard reader observations are labeled single-source/unattested; supplied transports are labeled injected/unattested. Extra account/key fields are never retained. Missing or malformed quote outcomes are recorded as rejected evidence when storage is available.

Evidence uses a per-session local hash chain and transactional head/count record. Append checks the current head/tail; review checks all retained rows, order, identities, chain continuity and count/head agreement. There is a 100,000-observation global cap with no automatic deletion. Evidence write failure, cap exhaustion or head corruption pauses forward operation before a new order is submitted. Local hashes are not externally anchored and cannot prevent coordinated rewrites.

`GET /api/paper/forward/sessions/{id}/evidence` and PORTAL review summarize observation span, gaps, rejection counts, distinct/repeated exchange timestamps, source modes and local integrity. Read-only lookup retrieves both active and archived session orders from the execution journal. Modeled fills are compared with the corresponding recorded bid, ask and receipt timestamp; session BUY/SELL counts are checked against filled orders. Policy payload fingerprints are recomputed. Disagreements are reported, never silently repaired.

Review while a session is running is explicitly provisional: session evidence and accounting are separate database snapshots. No report grants release approval, verifies profitability or certifies independent feed truth. Pre-evidence legacy sessions correctly report missing quote evidence; historical observations are not reconstructed or invented.

The public ticker adapter now timestamps actual receipt after the default HTTP transport returns, while preserving deterministic caller-supplied times and separate exchange-time freshness checks. Tests cover delayed transport receipt, quote/fill agreement, injected-source labeling, payload edits, tail deletion, head corruption, evidence-cap admission, failed writes, extra-field exclusion, counter mismatch and review API boundaries. No sustained real-feed session was run for this batch.

Next: sustained real-source forward evidence, independent attestation/strategy review and broader scheduling/physical history offload. These remain acceptance work; M2/M6/M7 are not complete.

## v0.61 — portable forward review and operator continuity

Added a read-only downloadable session review summary with explicit active/interrupted-session and injected-provenance blockers, deterministic SHA256 integrity envelope and offline verification CLI. FORWARD_RUNBOOK.md documents start/monitor/stop, policy fingerprints, authoritative accounting, stopped backups and recovery evidence. Summaries do not replace raw databases or independently attest public sources. Both v0.60 Windows workflows passed before this batch. Sustained real-feed validation, independent review and clean-machine acceptance remain open; no session or broker connection was started for this change.

## v0.62 — installed-process acceptance and distribution integrity

Added version/source-bound SHA256 distribution manifests with offline verification, duplicate/path rejection and changed/missing-file tests. Windows packaging now runs silent install, installed EXE startup, halt persistence, same-version reinstall/recovery and data-preserving uninstall, with installer/startup/result artifacts. Windows acceptance scope is documented in WINDOWS_ACCEPTANCE.md; native-window, default-path and cross-version clean-machine acceptance remain open. Local suite: 316 passed; compilation passed. Windows CI is required to establish the new installer result; no sustained feed or broker session started.

## v0.63 — verified offline recovery bundles

Added explicit application-closed backup, verification and restore CLI. It preserves ordinary files recursively, checks content and SQLite integrity, detects source changes during copying, refuses existing/nested destinations and symlinks, and stages output before publication. Restore only creates a new directory and never starts or reconfigures Nexus. Pending journal/WAL/SHM files require separate offline review; they are never deleted or replayed by this tool. Tests restore real order IDs, partial fills, fees and persistent halt, and cover corruption, changed-source rollback, manifest path injection, CLI confirmation and sidecar refusal. This extends recovery tooling, not independent clean-machine or sustained-feed acceptance.

Windows build concurrency now lets a running build finish when a newer sprint is pushed, preserving installer acceptance evidence rather than repeatedly cancelling the active build. At this batch review, v0.62 Windows checks were still in progress; v0.61 packaging had been cancelled by the previous concurrency setting. Local v0.63 validation: 326 tests passed and compilation passed.

## v0.64 — fresh-process complete paper-data recovery drill

Added disposable injected-quote BUY/SELL, tier-off exit, stop/halt/archive and four-database backup/restore exercise. A fresh child process checks restored archived orders, account fingerprint, linked ledger, quote-chain head, policy and stopped/halted behavior against the baseline; missing or damaged ledger/quote databases and mismatched recovery fingerprints fail. Caller environment and data stay isolated. Both v0.62 Windows workflows passed at commit 35239e562c72078a0d7ea32e240518c35a57e247, including installer and checksum steps. Native-window, cross-version, independent review and sustained real-feed evidence remain open.

Validation: 332 tests passed; Python compilation passed. The drill itself ran successfully with one modeled buy, one modeled sell, two archived orders, eight injected observations and four verified database files. Current v0.63 Windows checks were in progress at review; no sustained public-feed session was started.

## v0.65 — exclusive local forward ownership

Added an OS-held lock before forward preparation can mutate accounts/sessions or fetch data. Competing process/session starts are rejected. Stop retains ownership through an active fetch/cycle; normal stop, failure pauses, failed preparation/worker start and process death release it. Restart still starts stopped and requires explicit admission. Tests cover competing runners without state changes, forced child-process death, failed policy admission, blocked-fetch fencing and failure-pause release. Coordination applies to one local data directory, not distributed storage or independent manual simulation. Both v0.62 Windows workflows passed; v0.64 Windows checks were pending/in progress at review.

Both v0.63 Windows workflows now passed at commit a74496f3a977822ba3826213043655034c1da405. v0.64 safety regression passed; packaging remained in progress. Validation for this batch includes six dedicated ownership cases, full regression and compilation.

## v0.66 — runtime clock continuity and timestamp-order fencing

Added independent wall/monotonic elapsed-time checks around fetch and order writes. Backward/nonfinite clocks or drift above two seconds pause, clear warmup and release ownership after cycle completion; submitted but unfilled orders cancel. Invalid-clock diagnostics use a valid fallback timestamp and label the invalid sample. Regressing exchange timestamps clear warmup, retain rejected quote evidence and count toward the three-failure pause. Tests cover forward/backward/nonfinite jumps, transport and post-submit jumps, repeated exchange regressions and normal long-gap rewarming. This is safety validation with injected observations, not sustained market evidence.

Validation: 347 tests passed; Python compilation passed. v0.64 Windows safety tests passed, packaging remained in progress; v0.65 Windows checks were pending/in progress. No real-feed or broker session was started.

## v0.67 — retained-storage admission and PORTAL capacity diagnostics

Moved session/quote retention admission before account initialization/linking, retaining append-time hard caps. Exhausted quote storage pauses an existing run before another fetch. Read-only status and PORTAL expose retained counts, remaining slots and near-limit/full warnings without deleting evidence or granting execution permission. Tests verify no accounting files are created on full-storage rejection, no fetch occurs at exhaustion, and diagnostics leave orders/session evidence unchanged. Both v0.64 Windows workflows passed; v0.65 safety passed and its pending packaging run was superseded. Full sustained operation/physical offload remains a release gate.

Validation: 351 tests passed, Python compilation passed and PORTAL JavaScript syntax passed. v0.66 Windows checks were still in progress. No public-feed forward session or external broker connection was started.

## v0.68 — restored paper data through the installed application

Extended Windows installer smoke with a disposable four-database paper fixture, verified backup/new-directory restore and installed-EXE HTTP comparison of account and archived-order fingerprints, quote chain/count, policy, modeled buy/sell counts and stopped/halted behavior. The original backup is reverified afterward; baseline, process log and JSON results are retained in installer evidence. Unit tests reject changed account/order/quote/policy baselines. A source-process end-to-end harness is also run locally; only Windows CI establishes packaged behavior. This is same-source recovery, not a cross-version upgrade or sustained public-feed run.

Validation: 356 tests passed, Python compilation passed and the full restored-data HTTP harness passed using a source launcher. Both v0.66 Windows workflows passed at commit 13694d0ef90c504e4bc396a19f9fd92c688c4811; v0.67 checks were still in progress. New installed-EXE recovery coverage awaits its own Windows run.

## v0.69 — execution model and recorded-policy evidence crosschecks

Extended session evidence beyond quote bid/ask/time matching: recompute modeled price/fee arithmetic; require positive finite quantity/prices, adequate assumed capacity, unique positive sequences and evaluation before policy lifetime; compare fee/slippage rates with the recorded policy and event notional/fee sums with order totals. Report exact decimal modeled buy/sell notionals and fees, clearly scoped as local diagnostics rather than returns or liquidity validation. Tests inject price, quantity, fee, sequence, cost-rate, capacity, timing and total disagreements without rewriting persisted evidence.

Malformed session policy/counter metadata now produces a fail-closed unavailable-evidence response, including list/null values and boolean counters. Tests verify this API behavior. v0.67 safety passed while packaging was in progress; v0.68 checks remained pending/in progress.

Validation: 371 tests passed and Python compilation passed. No real-feed or external broker session was started.

## v0.69.1 — session metadata validation on read and write

Centralized timestamp/state/policy/counter/event validation for evidence, monitoring, history, cycle admission and diagnostic writes. Corrupt records pause before fetch; a changed stored policy is never overwritten by diagnostic updates. Stop fences/releases ownership even when its record cannot persist. Unavailable corrupted diagnostics return HTTP 503, while invalid caller limits remain HTTP 422. Tests verify no fetch/order/evidence mutation for corrupt metadata, preserved changed policy, safe failed STOP and API error classification. v0.68 safety passed; its pending Windows packaging run was superseded, so installed-EXE restoration remains awaiting packaged CI evidence.

Validation: 381 tests passed; Python compilation passed. Both v0.67 Windows workflows passed at d9836c87eda9826f30847beaef2f3d7706fbe166; v0.69 checks were still in progress. No real-feed/broker session started.
