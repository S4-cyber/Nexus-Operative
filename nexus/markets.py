"""Multi-market discovery registry. Descriptors only; no orders, credentials, or live sockets.

Each market must be enabled by verified broker entitlements, jurisdiction, account
permissions, and an explicit sandbox connection before obtaining live prices.
Never confuse instrument discovery with execution permission.
"""
from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class MarketProvider:
    id: str
    name: str
    assets: tuple[str, ...]
    scope: str
    connectivity: str = "not_connected"
    execution: str = "disabled"
    notes: str = ""

PROVIDERS = (
    MarketProvider("alpaca", "Alpaca", ("us_equities","us_etfs","crypto","us_options"), "US-first broker; asset access depends on account approvals",notes="Recommended first paper API"),
    MarketProvider("ibkr", "Interactive Brokers", ("global_equities","etfs","options","futures","forex","bonds","funds"), "Global exchanges subject to entitlements",notes="Session-based auth and market data subscriptions"),
    MarketProvider("coinbase", "Coinbase Advanced", ("spot_crypto",), "Crypto pairs subject to regional eligibility",notes="Separate authenticated data and trading permissions"),
)
ASSET_DESCRIPTIONS = {
  "us_equities":"US-listed stocks", "us_etfs":"US-listed ETFs",
  "crypto":"Broker-supported crypto", "us_options":"US equity options",
  "global_equities":"International listed stocks","etfs":"Exchange-traded funds",
  "options":"Exchange-listed options","futures":"Futures contracts",
  "forex":"Currencies","bonds":"Fixed income","funds":"Managed investment funds",
  "spot_crypto":"Spot crypto pairs"
}

def list_markets():
    return {
      "mode":"CATALOG_ONLY",
      "automatic_connection":False,
      "real_time_quotes":False,
      "order_routing":False,
      "providers":[{**asdict(p),"assets":list(p.assets)} for p in PROVIDERS],
      "assets":ASSET_DESCRIPTIONS,
      "notice":"Discovery catalog only. No active exchange feed, account, order routing, or claim of complete market access."
    }
