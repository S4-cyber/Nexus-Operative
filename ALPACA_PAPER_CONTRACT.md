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
