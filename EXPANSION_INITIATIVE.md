# Nexus Expansion Initiative — Broad, Evidence-First Discovery

Goal: cover hundreds or thousands of candidate markets across the world **without** indiscriminately placing trades. Market coverage is a discovery problem; productive trading is a separate, long-running validation problem.

## Now built
- Coinbase public USD spot-product catalog: paginated search with provider restrictions, no credentials, rate-limited local cache, **discovered != eligible**.
- Twelve research avenue categories: spot crypto, US equities, ETFs, international equities, FX, futures, listed options, commodities, bonds/rates, new listings, structured/leveraged products, and digital-asset derivatives.
- Read-only PORTAL **Market Universe Explorer** and **Explore New Trade Avenues**.
- Deterministic expansion evidence-screen API and high-risk quarantine for newly listed/leveraged/derivative products.
- No automatic brokerage account connection, paid feed, orders, leverage, live trading, or money movement.

## Next connectors, in safe development order
1. Add publicly licensed read-only US equities/ETF reference and split-adjusted historic data. Identify stale, halted, delisted and survivorship-biased securities.
2. Add verified instrument identifiers (venue, asset class, trading currency, expiration) with independent second-source cross-checks.
3. Add scheduled *bounded* scanner jobs with provider caching, pagination and explicit failure reasons. Thousands of possible instruments do not mean thousands of simultaneous network requests.
4. Add minimum track-record, spread, liquidity, regime stability, manipulation and estimated execution-cost screens. Newly listed assets remain quarantined with stronger evidence requirements.
5. Evaluate across chronological training/validation/test periods, multiple cost regimes, paper-only forward sessions and capacity limits. Record invalid/failed candidates as well as winners to avoid selection bias.
6. Make connector permissions and provider charges visible in PORTAL, with opt-in whenever required. Add new instruments to simulated portfolios only after full eligibility review and independent risk vetoes.
7. Defer live trading to separate order-system certification and explicit user authorization, regardless of research performance.

## Operational principles
- “Productive” means risk-adjusted *out-of-sample* performance robust to fees, slippage, liquidity and negative regimes, **not a return guarantee**.
- Do not confuse discovery of newly launched assets with legitimate venue access or trustworthiness. Protect against wash trading, inflated reported volume, rug pulls and market halts.
- Never infer an actionable strategy from a model's confidence or from a single exchange's indicative price.
- Multi-asset domains have distinct jurisdiction, market-data entitlements, margin, liquidity and settlement requirements. Each class requires a separate integration and testing plan.
- Initial security policy: research-only, no paper auto-execution on unverified listings, no live orders.
