# Disconnected Alpaca paper contract foundation

This component prepares and validates fixture data only. It contains no HTTP client, credentials, environment access, account writes or automatic strategy routing. The existing gateway continues to reject Alpaca submissions. No broker was connected or tested.

## Implemented components

- BTC-only quantity-based IOC limit previews at the fixed paper Trading API URL. Preview output is explicitly unsendable, with outstanding authorization, account and accounting blockers. ETH/SOL, market orders, notional requests, replacements and shorting are outside this contract.
- Caller-supplied crypto asset metadata must match BTC/USD, be active/tradable/fractionable and be no more than 60 seconds old in the caller's supplied clock. Minimum quantity, quantity step and price tick are checked without rounding. These are unverified metadata inputs, not broker attestations.
- Nexus applies a conservative quantity-multiple-of-step rule and a quantity-times-limit screen of at most $200. This screen does not guarantee the final fill notional, particularly for price-improved sells. Decimal values are bounded to 18 decimal places and calculated at high precision for the cap check.
- Canonical request digests, ASCII client IDs, broker UUID/identity/terms checks, cumulative quantity bounds, limit-price checks, timezone-aware update ordering and a narrow status-transition subset. Unknown or conflicting observations require review and leave the tracker unchanged.
- Pending cancellation remains nonterminal. A partial fill survives confirmed cancellation; a fill can complete while cancellation is pending. Exact current duplicates produce zero observed quantity increase. Earlier observations cannot restore an older state, and terminal rewrites or fill corrections require review.
- The tracker keeps only its latest allowlisted snapshot and at most 256 historical observation digests, without eviction. It is serial in-memory diagnostic tooling, not a durable broker journal. Extra response fields, credentials and account fields are not retained.

Cumulative average price is not treated as an incremental execution price. Quantities are observations, not credited holdings. Fee and position reconciliation stay false on every result. Crypto fees can reduce the received asset; the current synthetic USD-fee ledger must not ingest these observations as if they were its own modeled fills.

## Inspect and exercise

Use `GET /api/execution/alpaca-paper/contract` or the existing Execution Connectors view for read-only status. There is no preview/submission write endpoint. Run `python -m nexus.alpaca_paper_contract` for the repeatable in-memory partial-fill/pending-cancel/canceled fixture. It creates no application files.

An uncertain submission must eventually be recovered by the original client order ID before considering a retry. A timeout is not evidence of no fill. This component does not perform that lookup or authorize resubmission.

## Remaining acceptance

Explicit paper-connection authorization; credential isolation and paper account/region/asset eligibility; durable request and broker-ID recovery; execution IDs, corrections and asset-denominated fees; broker positions/cash reconciliation; pacing and outage handling; independent security/risk review. None of these is established by fixture success. The forward runner remains local synthetic paper only.

## Primary references reviewed 2026-10-17

- [Alpaca crypto spot trading](https://docs.alpaca.markets/us/docs/crypto-trading): symbols, asset increments, supported crypto orders and fee denomination.
- [Alpaca order lifecycle](https://docs.alpaca.markets/us/docs/orders-at-alpaca): statuses, cancellation and time in force.
- [Trading API create order](https://docs.alpaca.markets/us/reference/postorder): paper order endpoint and request fields.

This uses the paper **Trading API** contract, not the separate Broker API sandbox. Documentation may change; future connection work must revalidate it.

## Explicit execution and asset-fee projection

`nexus/broker_fee_projection.py` adds pure aggregate interval balance arithmetic for caller-supplied BTC/USD observations. It does not parse raw broker activities, query an account or write a ledger. Inputs include declared opening/closing balances and timestamps, normalized order snapshots, explicit execution IDs with actual execution prices, and fee activity IDs explicitly linked to executions. No execution or price is invented from a cumulative order average.

BUY increases gross BTC and reduces USD by explicit quantity times price. Its supported received-asset fee reduces BTC. SELL reduces gross BTC and adds USD; its supported received-asset fee reduces USD. Quantities, gross notionals and fees remain separate, without a hypothetical BTC-to-USD fee conversion. The fixed example buys 1 BTC at a synthetic $100, deducts a 0.001 BTC fee, sells the remaining 0.999 BTC at a synthetic $110 and deducts a $0.10989 fee. Its ending declared balances agree at BTC 0 and USD 1009.78011 from an opening USD 1000. These are fixture values, not market prices, expected returns or actual holdings.

The projection deduplicates identical order/execution/fee identities, rejects conflicting reuse, bounds input counts, validates identity/time links, compares explicit fills with snapshot quantities/averages and checks declared balances exactly. Missing fee records, unknown fee links, unsupported received assets, unknown coverage or negative projected balances block local consistency. Explicit zero fees require records; missing fees are never silently set to zero. Cumulative snapshot rounding or corrections may require manual review rather than automatic tolerances.

`fee_coverage_complete` is only a caller declaration. Even a matching projection always reports `broker_account_reconciled=false`, `ready_for_routing=false`, `ledger_applied=false` and no broker connection. This is aggregate balance conservation, not proof of intraperiod spending capacity, authenticated coverage or account reconciliation. Deposits, withdrawals, corrections, historical fee links and other assets need a future authorized adapter and coverage evidence.

Run `python -m nexus.broker_fee_projection`, or select **Check Broker Fee Fixture** in PORTAL. The read-only `GET /api/execution/alpaca-paper/fee-projection-fixture` exposes only that fixed example and has no write operation. Existing synthetic accounts and the forward runner remain unchanged.

## v0.90 durable disconnected request recovery

`nexus/broker_request_journal.py` adds a separate explicit-path SQLite journal bound to the hash of a caller-supplied paper account UUID. It has no credentials, transport, broker lookup, environment-default data path, strategy routing or synthetic-account writes. Account identity is unverified input, not an authenticated account attestation. Existing preview and in-memory tracker remain disconnected; their snapshots are cumulative observations rather than execution/fee records.

The journal retains canonical BTC IOC-limit request terms and their original client IDs. Identical preparations replay; conflicting terms cannot reuse an ID. An explicit `mark_submission_uncertain` commits a one-time conservative attempt boundary before any future transport could run. It does not send an order. A missing observation after that boundary remains SUBMISSION_UNKNOWN_LOOKUP_REQUIRED across restart. No API can clear the marker or grant automatic resubmission, even if a caller reports a timeout or not-found result. An authorized future adapter must query the original client ID and independently reconcile its response; a lookup alone will not establish fee/position completeness.

Allowlisted observations pass the existing identity, UUID, terms, cumulative quantity, timestamp and transition checks. Broker UUIDs cannot map to two client IDs. Pending cancellation remains nonterminal; a confirmed cancellation preserves partial quantity and cumulative average without crediting holdings/cash or deriving incremental fill prices. Current duplicates do not append; older/conflicting observations cannot roll state back. All accepted requests/attempts/observations acknowledge only after FULL-synchronous commit. Concurrent writers serialize through SQLite BEGIN IMMEDIATE, with a five-second local busy wait. A lost commit acknowledgment is resolved by replay, not assumed failure.

Every access validates the retained hash chain and rebuilds account-scoped state. Caps are 128 retained requests, 4,096 events including binding, 4,096 bytes per canonical event and a 64 MiB SQLite page/file admission limit; the existing tracker also limits distinct snapshots per request to 256. No history is evicted to admit more. These are local diagnostic limits, not broker pacing, whole-directory disk quotas or sustained capacity evidence. Hash consistency does not detect a deliberately removed valid tail, prove authenticity or encrypt data. Private local paths and controlled directory access remain necessary. A pre-existing empty/foreign/damaged database is not silently initialized. Read-only recovery never creates a missing journal or grants running permission; an interrupted SQLite transaction needing hot-journal recovery may require explicit offline SQLite recovery before read-only inspection. No automatic repair is supplied.

Run `python -m nexus.broker_request_journal` or Accounts → **Check Broker Request Recovery** for a temporary injected example: prepared request → uncertain boundary → reopened recovery → partial fill → pending cancellation → confirmed cancellation → duplicate replay. The GET fixture route `/api/execution/alpaca-paper/request-recovery-fixture` uses disposable storage and never reads or changes application accounts. It returns no-store diagnostics; there is no journal write, broker connection, lookup or submit HTTP endpoint. The button has beginner help and duplicate-request protection. Read-only contract/catalogue views still perform no storage access.

Regression includes separate real child-process deaths after attempt/observation commit and fresh-process review, a commit that succeeds but loses its acknowledgment, rollback after inserted data, account mismatch, reused broker UUIDs, concurrent duplicates, invalid transitions, retention/byte caps, changed/missing chain prefixes and unchanged application storage. These are fixture results, not authenticated broker recovery or execution acceptance. Remaining gates include paper-connection authorization, scoped credential handling, actual broker lookup/streaming/cancel/pacing, execution IDs/corrections and received-asset fees, complete cash/position reconciliation and independent review.

Additional primary reference reviewed 2026-10-17: [Get Order by Client Order ID](https://docs.alpaca.markets/us/reference/getorderbyclientorderid) documents the paper Trading API lookup at `/v2/orders:by_client_order_id`. The journal does not perform that request.

## v0.91 retained original-ID lookup admission and outcomes

The disconnected journal now supports explicit `begin_lookup` / `finish_lookup` records. They never call HTTP, obtain credentials, retry an order or run on startup. The returned hypothetical plan is GET-only at the fixed paper Trading API original-client-ID lookup path, with the journal's original ID and `sendable=false`. The pure `broker_lookup_policy.py` classifies caller-supplied status/transport results; neither the HTTP status nor the supplied account identity is authenticated.

A lookup begins only after a retained uncertain-submission boundary, while that order is not terminal. Admission commits a deterministic account/request/attempt/time ticket and charges one of four total lookup attempts for that retained request. One pending lookup per journal blocks further lookup admission and ordinary journal writes. It reserves a completion event slot before admission so capacity exhaustion cannot consume the only result slot. A process death or lost begin acknowledgment leaves a charged pending ticket for explicit review; no timer, expiry, auto-clear or automatic resume exists. This is deliberately conservative diagnostic recovery, not an unattended broker adapter. It does not coordinate separate journals, processes making unrelated requests, accounts or remote instances.

Lookup completion validates the ticket and supplied clock, then commits its outcome and any accepted allowlisted order observation together. A successful FOUND observation uses the existing identity/terms/state/cumulative checks; it never credits a ledger or reconciles fees/positions. Bad/stale/conflicting observations leave the lookup pending and retained state unchanged. Exact completed-event replays append nothing and cannot clear a newer pending lookup. A committed FOUND response survives a lost completion acknowledgment; replay resolves local truth. Current terminal orders stay in the reconciliation stage rather than authorizing more lookup attempts or resubmission.

Local outcome rules, not provider quotas or trading permissions:

| Supplied result | Retained behavior |
| --- | --- |
| 200 | Validate and retain the cumulative snapshot atomically with completion; no resubmit permission. |
| 404 | Keep submission uncertainty and require original-ID review; never infer no order/no fill. |
| TIMEOUT / NETWORK_ERROR / 5xx | Keep uncertainty; retain a local 5/10/20/40-second backoff after charged attempts. |
| 429 | Apply a shared journal cooldown using valid Retry-After integer seconds or a timezone-aware HTTP date; no header uses a conservative local 60-second wait. |
| 401 / 403 | Retain an account-review block on new lookup admission; no credential retry loop. |
| Redirect, unsupported status or unsupported Retry-After | Retain a review block; no redirect/fallback route or short silent retry. |

A minimum one-second interval applies even after successful completion. Waits are shared across requests in this account-scoped journal. Retry-After waits are never shortened to an arbitrary upper cap: malformed, overlong, non-ASCII, CR/LF-bearing or greater-than-one-day waits require review. Zero/elapsed waits retain the local one-second minimum. Raw error bodies, headers, location fields and credentials are not stored. Outcomes/cooldowns/attempts survive reopening and cannot be reset by repeated preparation. Read-only recovery includes their diagnostic state without any admission or network work.

Times are explicit caller-supplied wall-clock samples. Regression blocks backward samples relative to retained lookup activity. This is not trusted time or a monotonic elapsed-time guarantee across OS clock changes/restarts: a future authenticated adapter still needs clock continuity, cancellation/stream coordination, actual provider pacing and distributed fencing. The shared write fence is unsuitable as a completed streaming adapter by itself. New lookup event kinds are additive to the v1 local journal; v0.90 readers reject them rather than silently discarding them. Future policy changes require explicit compatibility review; no running or retry permission is restored by reading old records.

Accounts → Check Broker Request Recovery now also shows two injected lookup attempts, a retained timeout and a 30-second fixture rate-limit wait. The example uses a second temporary journal and does not delay or contact a service. Original request/partial-cancellation checks remain. CLI/API/controller/browser fixtures cover the added readout; no general lookup/write/connection HTTP endpoint is exposed.

## v0.92 retrospective fee-reserved cash and BTC funding path

`nexus/broker_cash_path.py` checks whether the declared opening USD/BTC and supplied execution sequence can cover each debit after reserving every supplied execution-linked fee. It shares the aggregate projection's bounded normalization, duplicate/identity/time/coverage checks and high-precision arithmetic in one pass; public `broker_fee_projection.reconcile` retains its aggregate result/API. The fixture adds a separate `cash_path` result. Neither operation reads a broker/account, grants routing, verifies deposits or writes a ledger.

Matching ending totals alone can conceal an early cash deficit or a sale before acquisition. The new audit groups executions by UTC time, checks each group's debits against its pre-group funds, then applies its net receipts. All declared linked fees are reserved from their own execution's received asset, including fees posted later in the supplied interval. A later buy or sale cannot repair an earlier funding violation. No missing execution, price, fee or holding is inferred. Fees exceeding their own execution receipt require review even if other holdings could cover them. Incomplete/inconsistent aggregate evidence prevents path evaluation rather than producing a partially invented history.

Equal-time executions have no independently verified internal order. If all debits are prefunded before the group, the audit can cover them without choosing an order; otherwise incoming funds from another execution in that group cannot be assumed available. A nonnegative final total in that situation still reports AMBIGUOUS_SAME_TIME_FUNDING. UTC timezone equivalents group consistently regardless of input order. Python datetime resolution is microseconds: submicrosecond timestamps collapse conservatively to one group, never an invented nanosecond sequence. Reported minimum balances cover opening/group-end boundaries only, not unknown intragroup minima.

Any supplied nonterminal order requires reservation review, even when historical debits are covered. Missing fee coverage, unknown links, conflicting duplicates, aggregate imbalance and input caps retain their existing blockers. Successful supplied-history arithmetic remains `spendable_funds_verified=false`, `broker_account_reconciled=false`, `ready_for_routing=false` and `ledger_applied=false`. Other open orders, holds, settled/available funds, transfers, deposits, corrections, fee accruals, account/asset eligibility and authenticated source coverage need separate broker reconciliation. Review output retains at most 20 samples while preserving the full violation count/reasons. No stored history is evicted or changed.

This is a **retrospective audit**, not a causal strategy simulation: later-known fee amounts may be used to check historical obligations, but must never enter earlier strategy choices, model features or profitability claims. Future execution needs contemporaneous, governed fee estimates/reserves and authenticated available-funds checks. Caller-declared opening funds and fee/execution links are not attestations; an empty supplied execution list is not funding or account acceptance.

Accounts → Check Broker Fee Fixture now displays the fee-reserved supplied-history result alongside the existing BTC/USD fee arithmetic. The result is no-store, the button prevents duplicate clicks and restores on failure, and failures hide raw errors. Its black/purple help explains delayed-fee reservation and offline scope. Missing/unknown path data displays review required. CLI `python -m nexus.broker_cash_path` and the existing fee fixture CLI/API run only the fixed example; no general reconciliation write or connection endpoint exists. Node and intercepted browser acceptance exercise the updated control.

Primary crypto reference rechecked 2026-10-17: [Alpaca Crypto Spot Trading](https://docs.alpaca.markets/us/docs/crypto-trading) describes received-asset fees and potentially delayed fee posting. No actual activity adapter or authenticated posting behavior was tested, and fixture prices/fees are not a provider schedule or expected return.
