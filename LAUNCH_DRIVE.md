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

## v0.70 — pinned prior-version data recovery in Windows CI

Added a separate checkout pinned to reviewed v0.64 source and source-origin-checked child fixture generation. The helper verifies HEAD, rejects tracked modifications, records fixture version/commit and requires an older fixture than the target. Windows installer smoke now runs both same-source recovery and prior-version fixture recovery against the installed EXE, preserving distinct evidence. Tests exercise source selection, commit mismatch, tracked modifications and missing pins. This validates a prior generated data fixture when CI passes; it does not claim prior-installer upgrade, old dependency reconstruction or broad historical-schema acceptance.

Also pinned push checkout to the triggering SHA and added an explicit HEAD assertion. Previously queued packaging jobs checked the moving branch name, so their workflow status alone did not establish which source tip was packaged; the distribution manifest is the source reference. New push evidence will bind to the triggering commit.

Validation: 383 tests passed; compilation and workflow YAML parsing passed. Pinned v0.64 data recovery and exact push-SHA packaging require the new Windows run; local source-selection tests do not establish those packaged outcomes.

## v0.70.1 — research decision-lane retention and clock correctness

Bounded retained duplicate IDs independently from queued events, with no silent eviction on consumption or halt. Full retention rejects new IDs while preserving duplicate detection. Invalid/nonfinite clock samples now reject research signals instead of bypassing expiry comparisons. Added defensive read-only capacity/metric snapshots to the local benchmark report. Tests cover retention exhaustion, duplicate preservation, queue-overflow retry, invalid clocks, configuration bounds and defensive snapshots. This remains an offline research benchmark: no paper/broker orders are created and no market-path throughput is established.

Validation: 396 tests passed and Python compilation passed. Both v0.69 Windows workflows completed successfully, but its pre-pin packaging workflow used a moving source branch, so consult the artifact manifest for the packaged source SHA. v0.70 exact-SHA/pinned-prior-fixture workflows were still in progress.

## v0.70.2 — exact risk-reducing exit quantities

Uncapped paper exits now preserve the exact reconciled holding quantity, avoiding dust when earlier priced fills carry more than 12 decimal places. Capped partial exits still round down and remain bounded by the existing notional limit. Regression tests close a high-precision earlier holding with the entry tier disabled, and close an above-cap holding across two qualifying exits while checking every order cap, flat/reconciled final accounting and consistent retained quote evidence. These are injected fixture tests, not market execution evidence.

Validation: 398 tests passed and Python compilation passed; the v0.70 exact-commit Windows build had completed both checkouts and SHA binding but remained in Install and test. No packaged prior-version recovery outcome is claimed yet.

## Forward crash acceptance extension — v0.70.2 development

Added actual child-process termination at the forward fill boundary: before fill and after committed fill/before BUY diagnostic record. Verified linked accounting, seven retained observations, interrupted-session review, OS ownership release, no automatic resume, outstanding-order admission veto and idempotent duplicate submission. Explicit cancellation permits a new tier-off risk-reducing exit after the committed-fill crash; the original counter disagreement remains visible. Flat tier-off reentry is rejected. This batch adds acceptance tests and runbook evidence without changing the runtime version.

Validation: 400 tests passed and Python compilation passed. The exact v0.70 Windows run 38995983189 passed SHA binding, installer/reinstall/uninstall including pinned v0.64 data recovery, checksum generation and distribution upload; remaining evidence upload/job cleanup was in progress. Recorded these automated component outcomes in the read-only roadmap while retaining M1 partial.

## Default Windows data-layout acceptance extension — v0.70.2 development

Added installed-EXE smoke mode without a portable-data override, using a disposable child-only LocalAppData root. The installer workflow checks initial default-layout startup, halt persistence after restart, absence of order data beneath installation, and preservation of the default journal on uninstall. Child smoke environments clear inherited auxiliary database path overrides. Unit tests check environment isolation, expected layout and exclusive option selection. This is acceptance tooling without a runtime version change; actual default-layout results require the new Windows run.

Validation: 403 cases passed in the full 404-case run; the roadmap consistency case saw a module/file mismatch during regeneration and then passed in a fresh process. All six roadmap/layout rechecks passed, covering the remaining case and all new environment tests. Compilation and the explicit-layout source-process HTTP recovery harness passed. Both Windows workflows for 8f2d2be999c416c16608e60fa904cb510e140fae completed successfully; default-layout packaged verification awaits this new commit.

## v0.71 — bounded linked-ledger fixture burst treatment

Extended the existing durability benchmark with an eight-item serially drained fixture queue, bounded admission and duplicate-ID retention, overflow rejection and 250 ms queue-age expiry. Admitted work executes actual temporary linked-ledger BUY/SELL quote-model roundtrips including duplicate fill delivery; reports queue wait, execution and admitted-to-completion p50/p95/p99 separately from cold recovery. Halt can flush queued work before execution. Regression tests check overload accounting, stale rejection without intents, idempotent delivery, halt flushing, bounded input and application-data isolation. PORTAL renders the new treatment separately. Fixed synthetic market times isolate local queue/storage cost; feed, strategy, streaming and broker timing remain excluded. Both Windows workflows for ad59507feafec4db2489fd05bc3f91cd9ee4c9a2 passed, including default AppData-layout startup/recovery/uninstall preservation.

Validation: 411 tests passed, Python compilation passed and PORTAL JavaScript syntax passed. A real-timing 12-item local fixture burst admitted eight and rejected four as overflow; two roundtrips completed and six expired at the 250 ms queue-age check. Queue p95 was 277.482 ms, completed-item admitted-to-completion p95 277.469 ms, cold replay 6.085 ms; the account finished flat, halted and reconciled. These host-specific fixture measurements do not validate market throughput. Percentiles for execution/completion cover completed items only; queue wait covers dequeued items.

## v0.72 — opt-in atomic quote-order transaction treatment

Refactored existing single-command transactions through shared per-command application rules, preserving replay/ledger/tier/cap/checkpoint behavior. Added a strictly bounded offline submit/terms/quote-fill/cancel transaction retaining each ordinary journal command while committing the new order and linked accounting together. Failures roll back intent, terms, fill, ledger and intermediate checkpoints; successful replay remains idempotent. The forward runner still uses its existing per-command path. Added a separate atomic-order burst benchmark treatment for diagnostic comparison and PORTAL display. Tests cover tier/stale quote/cap/cancel failures, expiry without fill, duplicates/conflicts, cold checkpoint recovery and actual process death before/after commit. No broker/strategy connection or automatic runtime promotion was added.

Validation: 420 tests passed, Python compilation passed and PORTAL JavaScript syntax passed. All prior durable accounting, checkpoint, archival and forward interruption tests remain green. Atomic grouping remains offline/diagnostic only and retains state-changing logical commands; idempotent/no-op actions still add no journal rows. It cannot roll back an external broker fill and is not a broker execution transaction.

One illustrative real-timing 12-item comparison admitted eight in each treatment: ordinary transactions completed three roundtrips before queue expiry (five expired), atomic orders completed four (four expired), and both ended flat/halted/reconciled. Completed-item p95 was 291.387 ms ordinary and 279.880 ms atomic. This small sequential host run is not a statistical speedup result or evidence of market throughput.

## v0.72.1 — post-lock execution freshness and stop fencing

Moved runtime quote evaluation to after SQLite write-lock acquisition and state restoration. The runner rechecks stop and wall/monotonic continuity at that boundary, journals the actual sampled evaluation time and preserves deterministic replay. Invalid runtime samples fail closed. Delayed stale/expired quotes clear entry warmup, and the existing finally cancellation preserves no-fill cleanup. The optional atomic fixture path uses the same hook without being promoted into the forward runner. Regression tests cover a real competing SQLite writer, replay delay, stop during replay, timestamp retention, invalid samples and full rollback.

v0.72 safety CI completed successfully; its Windows packaging run was pending at this review. No real-feed or broker session was started.

Validation: all 430 tests passed; Python compilation passed. The ten new cases include actual SQLite write-lock contention and fail-closed replay-delay/stop checks. The dependency stack emitted one existing Starlette TestClient/httpx deprecation warning. Windows packaging for this fix awaits CI.

## v0.72.2 — repeatable forward-operation acceptance drill

Added a bounded CLI exercising actual paper runner cycles with temporary child-process storage. Each round verifies warmup/entry, duplicate tick suppression, hold, tier-off exit and reentry veto, three-failure pause and released ownership. Reconstructed gateways retain all prior session/order/quote evidence; a separate process verifies flat/halted/reconciled accounting, session fill counts, source classifications and full account/order/evidence fingerprints. Reports include serial runner-cycle latency percentiles and explicitly exclude real-feed/scheduler/broker throughput. Input bounds, caller-data isolation, cross-process disagreement, missing database and ledger/quote corruption are regression-tested.

Both Windows workflows for exact v0.72.1 commit 05564202aa1c631ff3afb963633779b30ec7f61c passed: safety run 39049338459 and packaging run 39049338431. M1 remains partial pending manual clean-machine/profile/upgrade acceptance. No broker or public-feed session was started.

Validation: all 443 tests passed (13 new drill cases) and Python compilation passed. A maximum-bound 20-round run completed 280 runner cycles and retained 280 observations, 20 BUY orders and 20 SELL orders; all 143 state-changing commands recovered with flat/halted/reconciled accounting and identical fresh-process fingerprints. This host run measured cycle p50 5.385 ms, p95 25.896 ms and p99 29.223 ms. Synthetic-time fixture timings are not a scheduler or market-throughput guarantee. One existing Starlette TestClient/httpx deprecation warning remains. Windows verification of this new tooling awaits its CI run.

## v0.72.3 — public quote transport destination enforcement

Replaced redirect-following default transport with a redirect-rejecting opener and exact response-URL check before body reads. Hard-coded product paths can no longer silently redirect to another destination and retain Coinbase provenance labels. Existing GET-only requests, timeout/body limits, receipt-time capture and default-success-only cache remain. Regression tests exercise the actual urllib redirect handler for 301/302/303/307/308 across external, downgrade and same-origin product destinations; invalid response URLs/status/size/JSON, successful GET/cache behavior and actual flat/holding runner failure pauses are also covered. Existing positions and journal commands remain unchanged on rejected transport, with no invented fills.

v0.72.2 Windows safety and packaging workflows were in progress at the initial review. No real-feed or broker session was started; source review and independent provenance acceptance remain open.

Validation: all 466 tests passed (23 new transport/integration cases) and Python compilation passed. Actual redirect-handler tests verify a single request only and closure of the rejected response, preventing redirect socket leaks. The existing Starlette TestClient/httpx deprecation warning remains. Windows CI for this fix is pending submission.

## v0.72.4 — exchange freshness at the durable fill boundary

Closed an exchange-time gap remaining after receipt-time refresh: a quote could pass its ten-second exchange-age check at intake but cross that boundary during write-lock/replay delay while its receipt remained less than two seconds old. The runner now carries exchange time into its post-lock runtime hook and checks age against the exact continuity-checked wall sample, removing a second unchecked wall-clock read. Both entries and exits fail closed, clear warmup and cancel unfilled orders; prior holdings remain authoritative. Retained-evidence crosschecks independently compare fill evaluation to timezone-aware source exchange timestamps and preserve disagreements in portable review blockers. Fixed policy parameters/fingerprint remain unchanged.

New regression cases cover BUY and tier-off SELL across the exact ten-second boundary, stale and still-valid replay delays, old/future/invalid retained timestamps and portable blocker propagation without accounting mutation. v0.72.3 safety was in progress and packaging pending at the initial review. No real-feed or broker session was started.

Validation: all 479 tests passed (13 new exchange-age cases) and Python compilation passed. BUY and tier-off SELL retain valid exact-boundary fills and veto stale delayed fills without accounting mutation; portable review integrity verification succeeds while preserving the explicit exchange-time blocker. The existing Starlette TestClient/httpx deprecation warning remains. Windows verification of this fix awaits CI.

## v0.72.5 — fail-closed order cleanup and uncertain acknowledgments

Cancellation errors previously entered the ordinary ValueError veto path and could leave the runner enabled with a pending remainder. Introduced a dedicated cleanup-failure path: stop intake before propagating, clear warmup, record PAUSED with order ID/error class and unacknowledged-cleanup marker, then release ownership when the active cycle ends. Committed fills and session fill counters remain authoritative; no fill is invented or reversed. Retained evidence/export keeps an explicit cleanup-review blocker. Failed diagnostic persistence still leaves the runner stopped with interrupted-session/outstanding-order admission protection.

Tests cover ValueError/SQLite/OSError cancellation failures before and after fill, actual SQLite-triggered cancellation rollback, post-commit lost acknowledgment/idempotent retry, failed PAUSED diagnostics and explicit pending cancellation followed by tier-off risk-reducing exit. The original session marker remains after manual cleanup. v0.72.4 safety was in progress and packaging pending at this review. No real-feed or broker session was started.

Validation: all 488 tests passed (nine new cleanup cases) and Python compilation passed. The actual SQLite cancellation-trigger failure preserves the committed fill and rolls back cancellation; lost-ack retry adds no command. Explicit cleanup permits a new tier-off exit while preserving the original review blocker. The existing Starlette TestClient/httpx deprecation warning remains. v0.72.4 Windows safety and packaging were both in progress at the final review; this new fix requires its own CI.

## v0.73 — operator recovery snapshot and pending-remainder controls

Added read-only forward recovery API and PORTAL panel showing pending remainders, retained holdings, recent flagged sessions and provisional local-cycle state. Session history now explicitly flags cleanup-failure PAUSED records and includes review reasons. Inspect uses existing read-only order history; explicit cancellation uses the existing local-paper API, is disabled during local cycle ownership and rechecks local state before posting. No refresh/inspection starts or cancels anything. Confirmed cleanup preserves fills/holdings and the original session review marker. Scope is bounded recent-session/local-runner diagnostics, with no external owner probing, atomic cross-database claim or admission reservation.

Tests cover fresh uninitialized snapshots, stopped holdings, cleanup history and explicit API cancellation, stop during blocked fetch, invalid limits, corrupt metadata without repair and absence of a recovery write endpoint. A Node DOM/API harness exercised actual panel handlers for read-only refresh/inspect, explicit cancellation, retained-position reporting and active-cycle rejection; full PORTAL script syntax passed. This is not native Windows visual acceptance. v0.72.5 safety was in progress and packaging pending at the initial review.

Windows CI follow-up: v0.72.5 safety run 39055424530 failed with 487 passing cases and two setup/teardown errors on the oversized transport-body case. The generated pytest node ID embedded the entire 65,537-byte body, exceeding Windows' 32,767-character environment-variable limit for PYTEST_CURRENT_TEST. Added explicit short IDs to all response-error cases without changing body size, assertions or production transport code. The fix requires new Windows confirmation; Linux success is not that confirmation.

Standing UI help requirement added to v0.73: all 86 static buttons/editable parameters now carry beginner descriptions; dynamic tier and recovery buttons use a required-description factory. A fixed black/purple bottom-right panel supports hover and keyboard focus, preserves existing accessibility descriptions, and hides on hover-off, blur, Escape, scrolling/resizing or removal. It never reads entered values or invokes actions. UI_STANDARDS.md and AGENTS.md preserve this requirement for future controls; coverage, contrast and Node event/factory checks enforce the source contract.

Browser visual verification remains pending: the local browser-runtime download returned an invalid archive. Source contrast/positioning and Node event/DOM checks are verified, but no native-window or real-browser hover result is claimed.

Final v0.73 validation: all 500 tests passed (eight new recovery cases and four control-help cases), Python compilation and full PORTAL JavaScript syntax passed. Both actual-handler Node harnesses passed. Purple #e6a4ff on black has 11.12:1 text contrast; all 86 static controls and dynamic tier/recovery buttons carry help. Short test-ID collection now tops out at 151 characters while retaining the full 65,537-byte response fixture. Windows confirmation of the CI portability fix and this operator UI requires the new commit’s runs. One existing Starlette TestClient/httpx deprecation warning remains.

## v0.73.1 — real-browser control-help acceptance tooling

Added isolated Playwright acceptance of the full PORTAL against intercepted page/API fixtures. It requires at least 90 visible static/dynamic/disabled controls, verifies exact help copy on hover and disappearance on hover-off, dropdown selection, focus/Escape, private-value isolation, computed black/purple styles, viewport bounds and unclipped text at three desktop/narrow sizes. No application backend, accounts or market feed are used. Windows safety workflow installs optional UI tooling/browser and retains screenshots plus an HTML-digest/commit/version-bound report, with assertion-failure screenshots and no silent visual pass.

Hardened keyboard focus help across automatic page scroll while the focused control stays visible; offscreen focus and pointer hover-off hide it. The Node controller smoke now exercises both cases. Local browser archives still failed to download, so actual browser outcomes require the new CI run; no screenshot/real-browser/native-window pass is claimed locally. v0.73 safety and packaging were both in progress at the review.

Validation: all 500 local tests passed; compilation of nexus/tests/tools, full PORTAL script syntax, Node controller smoke and workflow YAML parsing passed. The optional browser tooling installed, but the browser archive was invalid locally; actual browser validation remains awaiting CI. Exact v0.73 safety run 39056258272/job 117464716610 completed successfully with 500 passed, confirming the Windows test-ID portability fix. v0.73 EXE packaging was still in progress. No public-feed or broker session was started.

## v0.74 — disconnected broker contract foundation and real-browser hover fix

Added a no-network/no-credentials Alpaca paper Trading API contract study, BTC quantity-based IOC limit previews with exact metadata/step/tick/cap checks, canonical digests and explicit unsendable blockers. A bounded serial snapshot tracker checks broker/client identities, requested terms, cumulative quantity/limit consistency, time/status progression, duplicates, cancellation races and terminal corrections. It retains only allowlisted latest data plus observation digests, never credits a ledger and always reports fee/position reconciliation incomplete. A read-only catalogue, connector-view summary, repeatable CLI fixture and source-reviewed integration notes expose development status while external submission remains denied. No broker was connected or account data read.

Reviewed current primary Alpaca docs, including received-asset crypto fee treatment; existing synthetic USD-fee accounting cannot be reused silently. M8 remains OPEN pending actual authorized durable integration, fees/positions, pacing and eligibility acceptance. Tests cover invalid metadata/numbers/identity/states, exact high-precision cap checks, stale/conflicting observations, fill-versus-cancel races, bounded duplicate retention, source-field allowlisting, no account file creation and unchanged external-submission denial.

Windows v0.73.1 safety run 39056727465 passed its Python suite but failed real-browser hover visibility during automatic scrolling; retained screenshot showed the panel hidden over the top of PORTAL. Fixed the scroll handler to preserve help for a visibly hovered control as well as visible keyboard focus. Added the queued-scroll regression to the Node controller harness and control/event JSON diagnostics to browser failure artifacts. Browser assertions remain strict; actual fixed-browser acceptance requires this new run. v0.73.1 packaging was in installer/reinstall/uninstall smoke at the review.

Final v0.74 evidence: exact v0.73.1 packaging run 39056727410 completed successfully, including installed/reinstall/uninstall recovery checks. Its safety run 39056727465 failed only the real-browser hover assertion after the Python suite passed; the new scroll fix has Node regression coverage but still requires a new browser run. The disconnected contract CLI preserved one observed partial quantity after cancellation and replayed the current snapshot idempotently without creating account files.

Validation: all 549 tests passed (49 new broker-contract cases), followed by eight roadmap/version/help rechecks after recording the latest packaging evidence. Compilation of nexus/tests/tools, PORTAL JavaScript syntax and the Node help controller passed. One existing Starlette TestClient/httpx deprecation warning remains. The browser fix and new contract require this commit’s Windows runs; no broker connection, credentials, real order or accounting migration was performed.

## v0.75 — received-asset fee and balance projection foundation

Added a bounded pure BTC/USD interval projection driven by explicit execution IDs/prices and execution-linked fee activities. It applies BTC buy fees to received quantity and USD sell fees to cash, keeping both fee units separate. Identical identities deduplicate without changing amounts; conflicting IDs fail, missing/unlinked fees and unknown coverage remain blockers. Explicit fill sums/averages are compared with order snapshots, timestamps are checked and supplied closing balances must match exactly. No prices/fills are derived from cumulative averages, no missing fee becomes zero, and no existing account is written.

Even consistent input always leaves broker-account reconciliation, routing and ledger application false because opening balances, activity links and completeness are caller supplied. The scope is aggregate interval balance conservation, not authenticated coverage or intraperiod risk permission. Added a repeatable CLI fixture, read-only fixture API and beginner-described PORTAL control. The fixture demonstrates 1 gross BTC bought, a 0.001 BTC fee, sale of only 0.999 BTC and a USD sell fee, with no real market prices or return claim. M8 remains open.

Tests cover exact balance conservation, replay/order independence, conflicting duplicates, missing/unknown/mismatched fee links, negative balances, wrong execution identities/times, missing executions without inference, cumulative-average disagreement, zero-fee records versus missing fees, exact tiny balance discrepancies, malformed data/caps/private-field allowlisting and no account file creation via CLI/API. v0.74 safety and packaging were still in progress at initial review.

Final validation: 580 tests passed, Python compilation, full PORTAL JavaScript syntax and the Node help controller passed. An additional 100 deterministic synthetic fee/balance projections agreed exactly; these were offline arithmetic checks, not broker evidence. One existing Starlette/httpx deprecation warning remains.

Exact v0.74 packaging run 39057450895 passed installer/reinstall/uninstall and pinned prior-version recovery. Safety run 39057450904 passed its Python suite and the desktop hover checks, then detected repeated null-control pointerout errors. Retained event diagnostics identify background pointer events when no control was active: null equaled null before calling contains. Added an explicit non-null guard and regression events for background-to-background and background-to-document leaves. This commit still requires Windows browser verification across all three viewport sizes; no local browser binaries were available.

## v0.76 — Singularity, Fractal and explicit expansion/cloud goals

Added an account Matrix with Singularity whole-account liquidation estimates and Fractal per-product unrealized USD P/L, a minute/hour/day/month/year/YTD/all-retained selector and current journal-adjustment inspection. These are actual observed local paper-account samples, separate from the existing public ticker graph and decorative/synthetic stream. A separate bounded durable store retains observations across application restarts. Incomplete marks leave equity gaps; no cash-only portfolio substitute or historical backfill. Multi-product quote capture discards mixed account revisions, validates receipt and exchange freshness, throttles and caps intake without deleting evidence. Graphs break across missing holdings/time gaps; sampled long-range responses display spaced dots. Current 100,000-sample retention (~7 active days at six seconds) is an explicit scale limit, with durable rollups/offload still planned. Read MATRIX_ACCOUNT.md.

All new controls include standing black/purple beginner help. Added actual-controller Node checks for account versus per-position series, gap/downsample behavior and read-only adjustment/range inspection. Windows browser tooling now exposes explicit account fixtures without activating PORTAL, verifies both modes and inspection and retains per-viewport Matrix screenshots. No actual account/feed is contacted by those fixtures.

Added explicit global-expansion and computer-off cloud goals to the immutable-source roadmap, Markdown and PORTAL reference. CLOUD_OPERATION.md records current primary-source free-tier constraints, user preference for no separate dedicated account/free usage, absence of a selected anonymous always-on host, and required authenticated headless control, durable single-owner coordination, encryption/key handling, quota limits and recovery evidence. No hosting account, deployment, remote API exposure or billing was enabled. Current local Matrix/runner still need the local application running; future cloud operation remains planned.

Exact v0.75 commit cd3684ab15c5d8f86756e05ec142d7d179d481d8 passed both Windows workflows: safety 39058121903 (including real-browser help acceptance) and packaging 39058121974. This confirms the prior background pointerout fix; it does not validate the new Matrix until its own Windows run.

Validation: 595 tests passed (15 new Matrix cases, including actual renderer/controller behavior); Python compilation of nexus/tests/tools, complete PORTAL JavaScript syntax and both Node controller checks passed. One existing Starlette/httpx deprecation warning remains. Windows Matrix screenshots and installer acceptance require the new commit's workflows. No production account was initialized, no broker or live-feed session was started in validation, and no cloud resource was created.

## v0.77 — retained Matrix history beyond the active sampling window

Added a retained-history table alongside the active Matrix observations. At the active cap, completed UTC days move in one FULL-synchronous transaction; all original IDs, timestamps and payloads remain. The latest active ID is preserved to prevent identity reuse after long downtime. Independent total admission caps retained observations at six million (about 416 active days at the current six-second UI cadence, not guaranteed continuous coverage). Source paper accounts and execution journals remain untouched. This is logical retention within a growing database, not physical offload, disk reclamation, encrypted storage or unlimited history.

Range reads combine both tables under one read transaction, rank only IDs and load at most 1,200 selected original JSON points. Downsampled graphs remain dots and do not claim to preserve every extremum. Existing databases remain readable without GET schema writes; the next observation adds retention tables. PORTAL now displays retained counts and total capacity. Headless browser fixtures exercise the capacity readout as well as Matrix modes.

Tests cover preserved payloads/IDs across closed-day moves, independent retained admission before network IO, failure rollback between copy and removal, retry after rollback, pre-archive database reads/upgrades, mixed-table bounded query order, day/month/year/YTD boundaries and verified offline backup/fresh-process restore. A disposable 100,000-row synthetic check retained all observations, moved 86,234 into history and returned 1,192 points; local archive/read times were 0.129/0.084 seconds with a 72,019,968-byte database. These are single offline fixture measurements, not production-scale capacity, market throughput or a cloud cost estimate.

Final v0.77 validation: 607 tests passed, including 12 new retention/restore/calendar/head-integrity cases. Python compilation of nexus/tests/tools, full PORTAL JavaScript syntax and both actual Node controllers passed. Retained-head disagreement now blocks capture before quote IO rather than reusing IDs. One existing Starlette/httpx deprecation warning remains.

Exact v0.76 safety run 39058732933 passed Windows tests and headless browser acceptance. Its retained browser-report confirms all 95 visible controls at each of 1280x900, 390x844 and 320x568, both Matrix modes, adjustment inspection, keyboard/Escape, private-value isolation and no hover writes. Reviewed desktop and narrow Fractal screenshots: separate green/purple position lines render coherently and narrow text wraps. Improved future screenshots to move the pointer off controls so help does not cover the graph and visibly label intercepted fixtures. v0.76 packaging was at installer creation during review. The new retention commit still requires its own Windows runs; multi-million-row scale, physical offload, remote cloud operation and release acceptance remain open.

Packaging follow-up: exact v0.76 Windows run 39058732934 completed successfully, including installer/reinstall/uninstall and pinned prior-version recovery. Both v0.76 Windows workflows passed. M1 remains partial pending native clean-machine/profile/ACL and prior-installer upgrade acceptance.

## v0.78 — byte-aware Matrix capacity and failure-safe collection

Added independent observation database byte and free-space admission alongside row retention limits. The collector uses a 1 GiB SQLite page ceiling, 256 MiB free-space reserve plus rollback-journal/write headroom and a conservative temporary archive-copy estimate. Checks run before quote IO and again after fetch; post-fetch denial rolls back any pending archive move. New point payloads are bounded at 64 KiB. An actual SQLite page-allocation failure returns an unrecorded result with the prior database intact. Missing-volume path traversal fails explicitly rather than looping, disk-probe errors release the local guard, and connections close on all paths.

The read-only history report exposes database/disk headroom and warnings; PORTAL shows MiB capacity and practical collection messages without raw storage codes. Node and headless browser fixtures cover the added capacity display. These are local observation-only budgets: no execution stop, broker permission, automatic pruning, paid service, encryption or cloud deployment is introduced. Journal/backup/other data usage and external disk races remain distinct; byte limits may stop collection before the row-based approximate year capacity.

Tests cover read-only telemetry without creating the data directory, byte and disk denial before transport, temporary archive byte/disk budget denial, post-fetch disk loss with rollback/retry, actual SQLite allocation failure with quick_check, oversized sample rejection/retry, disk-probe failure cleanup and nonexistent-volume termination. Existing archival recovery and verified offline backup/restore tests remain in the suite.

Final v0.78 validation: 618 tests passed, including 11 new storage-admission/failure cases. Full Python compilation, PORTAL JavaScript syntax and both actual Node controllers passed. Preexisting databases above the byte budget are also blocked without truncation, even if they contain reusable pages. One existing Starlette/httpx deprecation warning remains.

Exact v0.77 safety run 39061647280 completed successfully, including Windows regression and intercepted real-browser Matrix/help acceptance. Packaging run 39061647318 was uploading its standalone EXE during final review; installer acceptance remains pending for that run. The new v0.78 capacity display/limits require their own Windows verification. No broker, live-money or cloud resource was connected.
