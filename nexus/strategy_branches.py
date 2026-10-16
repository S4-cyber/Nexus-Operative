"""Research-only strategy branch registry: NO venue connections or executable orders.

Asset permissions, market calendars, margin/borrow/option eligibility,
contract settlement, and regulatory restrictions must be implemented before
any instrument can become a tradable candidate.
"""
BRANCHES=(
 {"id":"spot_momentum","label":"Intraday spot momentum","markets":["crypto_spot","equities"],"status":"RESEARCH_ONLY","authorization":"No trading enabled","limitations":"Liquidity, spread, trading halts, settlement and account limits"},
 {"id":"mean_reversion","label":"Intraday mean reversion","markets":["crypto_spot","equities"],"status":"RESEARCH_ONLY","authorization":"No trading enabled","limitations":"Trend regime changes, slippage and false reversals"},
 {"id":"options_defined_risk","label":"Defined-risk options research","markets":["listed_options"],"status":"RESEARCH_ONLY","authorization":"Requires option approval and complete contract specifications","limitations":"Premium loss, theta decay, assignment/exercise and illiquidity"},
 {"id":"event_contracts","label":"Event / prediction contract research","markets":["regulated_event_contracts"],"status":"RESEARCH_ONLY","authorization":"Venue and jurisdiction eligibility required","limitations":"Event resolution, settlement, liquidity and potentially total stake loss"},
 {"id":"relative_value","label":"Cross-market relative value research","markets":["equities","crypto_spot"],"status":"RESEARCH_ONLY","authorization":"No broker connections","limitations":"Basis risk, fees, data timing and inventory constraints"}
)

def available_branches():
    return {"mode":"RESEARCH_BRANCH_CATALOG","live_execution_enabled":False,
        "automated_bets_enabled":False,"branches":list(BRANCHES),
        "notice":"Research capabilities only; listing does not establish profitability or legal eligibility."}
