# Multi-market roadmap — Nexus Operative

The providers displayed in the dashboard are **potential integrations**, not connected brokers.

Initial provider targets:
- Alpaca paper API for US stocks and approved other classes
- Interactive Brokers for broader global exchange and instrument coverage, requiring appropriate login and entitlements
- Coinbase Advanced API for spot crypto pairs (subject to eligibility)

This registry currently contains static capability metadata only; no live quotes, user accounts, automated trading, funded deposits, or cloud worker exist. Never infer that an asset is tradable simply because a provider supports the class.

A later implementation needs provider-hosted authorization, entitlement discovery, asset-specific market calendars, instrument IDs, quote freshness, order sizing, order lifecycle and reconciliation, slippage/fee modelling, circuit breakers, and paper-mode burn-in. Each account must have separate read/trade permissions. No arbitrary URL or third-party plugin may bypass risk enforcement. Keep live order routing and money movement disabled unless explicitly authorized and independently reviewed.
