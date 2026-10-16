"""Nexus Council: isolated specialist *deterministic* evaluators and veto governor.

This is an auditable interface for future independently operated AI models,
NOT an installed LLM or a profitable predictive system. All advice remains
paper research and cannot become broker orders by consensus.
"""
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import hashlib
import json

ROLES=("market_regime","strategy_research","execution_quality","portfolio_risk","adversarial_review")

def dec(value):
    if isinstance(value,bool):raise ValueError("Boolean is not a numeric measurement")
    try: x=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError) as exc:raise ValueError("Invalid numeric evidence") from exc
    if not x.is_finite():raise ValueError("Nonfinite measurement")
    return x

def opinion(role,verdict,reasons,metrics=None):
    return {"specialist":role,"implementation":"DETERMINISTIC_RULES_NOT_AI_MODEL",
        "verdict":verdict,"reasons":reasons,"metrics":metrics or {}}

def council(evidence):
    if not isinstance(evidence,dict):raise ValueError("Evidence must be a mapping")
    required=("symbol","data_source","data_verified","quote_age_seconds",
              "spread_bps","estimated_roundtrip_fees_bps","estimated_slippage_bps",
              "conservative_edge_bps","history_observations","out_of_sample_trades",
              "out_of_sample_net_pnl","equity","proposed_notional",
              "current_exposure","daily_loss","stress_loss_estimate",
              "emergency_halt","account_reconciled","market_eligible")
    missing=[x for x in required if x not in evidence]
    if missing:raise ValueError("Missing council evidence: "+", ".join(missing))
    numbers={k:dec(evidence[k]) for k in (
        "quote_age_seconds","spread_bps","estimated_roundtrip_fees_bps",
        "estimated_slippage_bps","conservative_edge_bps","history_observations",
        "out_of_sample_trades","out_of_sample_net_pnl","equity",
        "proposed_notional","current_exposure","daily_loss","stress_loss_estimate")}
    if any(numbers[k]<0 for k in ("spread_bps","estimated_roundtrip_fees_bps",
         "estimated_slippage_bps","history_observations","out_of_sample_trades",
         "current_exposure","daily_loss","stress_loss_estimate")):
        raise ValueError("Invalid negative exposure, costs or sample counts")
    e=numbers
    regime=[]
    if evidence["data_verified"] is not True or evidence["data_source"] not in ("coinbase_exchange_public","verified_historical"):
        regime.append("NO_TRUSTED_DATA_PROVENANCE")
    if not 0<=e["quote_age_seconds"]<=10:regime.append("STALE_QUOTE")
    if evidence["market_eligible"] is not True:regime.append("MARKET_ELIGIBILITY_UNVERIFIED")
    research=[]
    if e["history_observations"]<100:research.append("INSUFFICIENT_PRICE_HISTORY")
    if e["out_of_sample_trades"]<30:research.append("INSUFFICIENT_HOLDOUT_TRADES")
    if e["out_of_sample_net_pnl"]<=0:research.append("NO_POSITIVE_HOLDOUT_EVIDENCE")
    execution=[]
    costs=e["spread_bps"]+e["estimated_roundtrip_fees_bps"]+e["estimated_slippage_bps"]
    if costs>50:execution.append("HIGH_TRADING_FRICTION")
    if e["conservative_edge_bps"] <= costs*Decimal("1.5")+10:execution.append("ESTIMATED_EDGE_INSUFFICIENT")
    risk=[]
    if e["equity"]<=0 or e["proposed_notional"]<=0:risk.append("INVALID_CAPITAL")
    if e["equity"]>0 and e["proposed_notional"]+e["current_exposure"]>e["equity"]*Decimal(".10"):
        risk.append("EXPOSURE_LIMIT")
    if e["equity"]>0 and e["stress_loss_estimate"]>e["equity"]*Decimal(".005"):
        risk.append("STRESS_LOSS_LIMIT")
    if e["equity"]>0 and e["daily_loss"]>=e["equity"]*Decimal(".01"):
        risk.append("DAILY_LOSS_CUTOFF")
    if evidence["emergency_halt"] is not False:risk.append("HALT_ASSERTED_OR_UNKNOWN")
    if evidence["account_reconciled"] is not True:risk.append("UNRECONCILED_ACCOUNT")
    adversarial=[]
    if e["conservative_edge_bps"]>500:adversarial.append("IMPLAUSIBLE_EDGE_REQUIRES_REVIEW")
    if evidence["data_source"]=="verified_historical":adversarial.append("HISTORICAL_ONLY_NOT_LIVE")
    if e["history_observations"]<=e["out_of_sample_trades"]:adversarial.append("INSUFFICIENT_SEPARATION")
    checks=[
        opinion(ROLES[0],"VETO" if regime else "PASS_RESEARCH",regime,{"quote_age_seconds":float(e["quote_age_seconds"])}),
        opinion(ROLES[1],"VETO" if research else "PASS_RESEARCH",research,{"holdout_trades":int(e["out_of_sample_trades"])}),
        opinion(ROLES[2],"VETO" if execution else "PASS_RESEARCH",execution,{"roundtrip_cost_bps":float(costs)}),
        opinion(ROLES[3],"VETO" if risk else "PASS_RESEARCH",risk,{"stress_loss":float(e["stress_loss_estimate"])}),
        opinion(ROLES[4],"VETO" if adversarial else "PASS_RESEARCH",adversarial)]
    vetoes=[role["specialist"] for role in checks if role["verdict"]=="VETO"]
    fingerprint=hashlib.sha256(json.dumps(evidence,sort_keys=True,default=str).encode()).hexdigest()
    return {"architecture":"NEXUS_COUNCIL_V1","implementation":"DETERMINISTIC_SPECIALISTS",
       "specialists":checks,"vetoes":vetoes,"evidence_sha256":fingerprint,
       "consensus":"REJECT" if vetoes else "FURTHER_PAPER_VALIDATION",
       "governor_decision":"NO_ORDER_AUTHORITY",
       "human_review_required":True,"simulation_only":True,
       "live_orders_enabled":False,"automatic_paper_orders_enabled":False,
       "disclaimer":"Research rules, not connected AI models or a calibrated profit predictor. Neither consensus nor confidence can authorize a trade."}
