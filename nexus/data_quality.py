"""Chronological health diagnostics on locally journaled public price comparisons.

Reports observable sampling gaps and evidence integrity only. This is not a
continuous historical market-price feed or statistically validated backtest.
"""
import math
from nexus.market_evidence import recent
from nexus.public_data import PRODUCTS

def quality(product,limit=100,gap_seconds=60):
    if product not in PRODUCTS:raise ValueError("Unknown market product")
    if type(gap_seconds) not in (float,int) or isinstance(gap_seconds,bool) or not 1<=gap_seconds<=3600:
        raise ValueError("Invalid gap threshold")
    if type(limit) is not int or not 2<=limit<=100:
        raise ValueError("Invalid sample limit")
    data=recent(product,limit)["observations"]
    chronological=list(reversed(data))
    gaps=[]
    regressions=[]
    compromised=[]
    veto_count=0
    approved_count=0
    for item in chronological:
        if not item["local_record_integrity"]:
            compromised.append(item["id"])
        if item["decision"]=="VETO":
            veto_count+=1
        elif item["decision"]=="PROVISIONAL_AGREEMENT":
            approved_count+=1
    for a,b in zip(chronological,chronological[1:]):
        if not a["local_record_integrity"] or not b["local_record_integrity"]:
            continue
        elapsed=b["observed_at"]-a["observed_at"]
        if not math.isfinite(elapsed):
            compromised.append(b["id"])
        elif elapsed<0:
            regressions.append({"previous_id":a["id"],"next_id":b["id"]})
        elif elapsed>gap_seconds:
            gaps.append({"from_id":a["id"],"to_id":b["id"],"elapsed_seconds":round(elapsed,3)})
    issues=[]
    if len(data)<2:issues.append("INSUFFICIENT_CHRONOLOGICAL_SAMPLES")
    if compromised:issues.append("LOCAL_EVIDENCE_INTEGRITY_FAILURE")
    if regressions:issues.append("OBSERVATION_CLOCK_REGRESSION")
    if gaps:issues.append("OBSERVATION_SAMPLING_GAPS")
    if veto_count:issues.append("SOURCE_QUALITY_VETOES")
    return {"mode":"LOCAL_OBSERVATION_HEALTH",
        "product":product,"sample_count":len(data),
        "provisional_agreements":approved_count,"veto_observations":veto_count,
        "sampling_gap_count":len(gaps),"gaps":gaps[:20],
        "clock_regressions":regressions[:20],"invalid_record_ids":compromised[:20],
        "issues":issues,"quality_pass":not issues,
        "suitable_for_backtesting":False,
        "exchange_timestamps_verified":False,
        "live_orders_enabled":False,
        "note":"A bounded journal of discrete comparisons; missing intervals and exchange timestamp uncertainty prohibit use as validated OHLCV history."}
