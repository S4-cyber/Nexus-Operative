"""Multi-asset expansion roadmap with fail-closed evidence gates.

Avenues are research categories, NOT verified individual investments.
No discovery or score is an order authorization. No user credentials or
market data fetched by this module.
"""
from decimal import Decimal,InvalidOperation

AVENUES=(
 ("spot_crypto","Spot cryptocurrency","PUBLIC_CATALOG_AVAILABLE","Coinbase public USD products; each market needs quote, volume and history checks"),
 ("us_equities","US stocks","CONNECTOR_REQUIRED","Licensed market-data source, splits, delistings, survivorship and corporate-action adjustments"),
 ("us_etfs","Exchange-traded funds","CONNECTOR_REQUIRED","Underlying exposures, fund liquidity, spread and creation/redemption risks"),
 ("global_equities","International equities","CONNECTOR_REQUIRED","Exchange entitlements, local holidays, currency and jurisdiction checks"),
 ("fx_spot","Foreign exchange","CONNECTOR_REQUIRED","Reliable time-aligned bid/ask, rollover, trading venue and legal eligibility"),
 ("futures","Futures and micro futures","CONNECTOR_REQUIRED","Contract rolls, margin, expiration, basis and gap-risk stress"),
 ("options","Listed options","CONNECTOR_REQUIRED","Full chains, greeks, volatility surfaces, liquidity and expiry assignment risks"),
 ("commodities","Commodities","CONNECTOR_REQUIRED","Spot proxies vs listed contracts and physical-delivery exposure"),
 ("rates_bonds","Rates and bonds","CONNECTOR_REQUIRED","Duration, accrued interest, yield curve and venue liquidity"),
 ("new_listings","New listings and newly launched markets","EARLY_STAGE_HIGH_RISK","Listing age, manipulation, rug pull and liquidity checks; strict quarantine by default"),
 ("structured_products","Structured and leveraged products","EARLY_STAGE_HIGH_RISK","Issuer, leverage, path-dependence and embedded funding costs"),
 ("digital_derivatives","Digital asset derivatives","EARLY_STAGE_HIGH_RISK","Regulatory restrictions, liquidation, counterparty, venue and perpetual funding risk"),
)
REQUIRED=("independent_sources","fresh_quotes","historical_samples",
          "spread_bps","daily_usd_volume","holdout_trades","net_holdout_after_costs",
          "jurisdiction_eligible","venue_eligible","no_market_halt",
          "counterparty_reviewed","risk_reviewed")

def roadmap():
    return {"mode":"EXPANSION_INITIATIVE","avenues":[
        {"id":i,"name":n,"status":status,"research_requirements":notes,
         "verified_productive":False,"discovery_enabled":i=="spot_crypto",
         "paper_execution_approved":False,"live_orders_enabled":False}
        for i,n,status,notes in AVENUES],
        "gate_fields":list(REQUIRED),
        "note":"Open exploration across asset classes is not market access, verified profitability, or permission to trade.",
        "live_orders_enabled":False,"autonomous_trading_enabled":False}

def assess_evidence(avenue,evidence):
    if avenue not in {row[0] for row in AVENUES}:raise ValueError("Unknown expansion avenue")
    if not isinstance(evidence,dict):raise ValueError("Evidence must be a mapping")
    missing=[name for name in REQUIRED if name not in evidence]
    reasons=["HUMAN_REVIEW_REQUIRED","NO_LIVE_EXECUTION_AUTHORITY"]
    if missing:reasons.append("MISSING_VERIFICATION_FIELDS")
    # Explicit fail-closed types: do not accept model-generated true-ish strings.
    for name in ("jurisdiction_eligible","venue_eligible","no_market_halt","counterparty_reviewed","risk_reviewed"):
        if evidence.get(name) is not True:reasons.append("UNVERIFIED_"+name.upper())
    def number(name):
        raw=evidence.get(name)
        if isinstance(raw,bool):return None
        try:
            value=Decimal(str(raw))
            return value if value.is_finite() else None
        except (TypeError,InvalidOperation,ValueError):return None
    criteria={"independent_sources":2,"fresh_quotes":1,"historical_samples":250,
              "spread_bps":50,"daily_usd_volume":1_000_000,"holdout_trades":30}
    for key,cutoff in criteria.items():
        value=number(key)
        if value is None or value<0 or (value>cutoff if key=="spread_bps" else value<cutoff):
            reasons.append("UNVERIFIED_"+key.upper())
    if evidence.get("net_holdout_after_costs") is not True:
        reasons.append("NO_VERIFIED_POST_COST_HOLDOUT")
    if avenue in {"new_listings","structured_products","digital_derivatives"}:
        reasons.append("HIGH_RISK_QUARANTINE")
    return {"mode":"EXPANSION_EVIDENCE_SCREEN","avenue":avenue,
        "evidence_completeness":len(REQUIRED)-len(missing),"required_fields":len(REQUIRED),
        "screen_status":"INCOMPLETE" if missing else "MANUAL_VALIDATION_REQUIRED",
        "blockers":sorted(set(reasons)),"research_claim_validated":False,
        "paper_execution_approved":False,"live_orders_enabled":False,
        "note":"Thresholds are conservative screening heuristics, not predictive proof. No input can approve a trade."}
