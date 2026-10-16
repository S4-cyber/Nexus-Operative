"""Fixed, transparent challenge suite for Nexus governance.

Each case represents a controlled research assertion, not a recorded exchange
event. This measures whether the rule-based Council spots known hazards, NOT
model intelligence, predictive accuracy, or trading profitability.
"""
from copy import deepcopy
from nexus.council import council
from nexus.evidence_auditor import audit_evidence
from nexus.model_adapters import coordinate

def baseline():
    return {
        "symbol":"BTC-USD","data_source":"coinbase_exchange_public",
        "data_verified":True,"quote_age_seconds":1,
        "spread_bps":3,"estimated_roundtrip_fees_bps":5,
        "estimated_slippage_bps":6,"conservative_edge_bps":65,
        "history_observations":1000,"out_of_sample_trades":60,
        "out_of_sample_net_pnl":25,"equity":10000,
        "proposed_notional":50,"current_exposure":100,
        "daily_loss":0,"stress_loss_estimate":20,
        "emergency_halt":False,"account_reconciled":True,"market_eligible":True,
    }

CHALLENGES=(
    ("stale_quote",{"quote_age_seconds":60},"market_regime"),
    ("unverified_market",{"data_verified":False},"market_regime"),
    ("spread_crisis",{"spread_bps":150},"execution_quality"),
    ("no_holdout",{"out_of_sample_trades":3},"strategy_research"),
    ("daily_loss_cutoff",{"daily_loss":120},"portfolio_risk"),
    ("portfolio_overexposed",{"proposed_notional":2500},"portfolio_risk"),
    ("emergency_halt",{"emergency_halt":True},"portfolio_risk"),
    ("unreconciled",{"account_reconciled":False},"portfolio_risk"),
    ("implausible_edge",{"conservative_edge_bps":1500},"adversarial_review"),
    ("no_net_edge",{"conservative_edge_bps":10},"execution_quality"),
)

def benchmark():
    results=[]
    for name,patch,expected_role in CHALLENGES:
        evidence=baseline()
        evidence.update(patch)
        evaluation=council(evidence)
        caught=expected_role in evaluation["vetoes"] and evaluation["consensus"]=="REJECT"
        results.append({"scenario":name,"expected_veto":expected_role,
                        "caught":caught,"vetoes":evaluation["vetoes"]})
    controls=baseline()
    nominal=council(controls)
    audit=audit_evidence(controls)
    advisory=coordinate({"purpose":"offline_safety_benchmark","symbol":"BTC-USD"})
    passed=sum(x["caught"] for x in results)
    return {
        "mode":"FIXED_SYNTHETIC_SAFETY_BENCHMARK",
        "evaluated":len(results),"caught":passed,
        "detection_pct":round(100*passed/len(results),1),
        "all_hazards_detected":passed==len(results),
        "control_has_no_specialist_vetoes":not nominal["vetoes"],
        "unverified_control_blocked":audit["decision"]=="UNVERIFIED",
        "advisory_fixture_abstains":advisory["all_abstained"],
        "execution_authorized":False,
        "live_trading_enabled":False,
        "cases":results,
        "limitations":"Preset synthetic hazards only; no claim of model accuracy, profit probability, live-fill quality, or real-market validation."
    }
