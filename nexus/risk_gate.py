"""Independent paper-order admission gate. Fail closed on missing or unverified evidence.

All inputs are research assertions, not market observations. Gate approval is
NOT evidence of profit and never grants brokerage execution authority.
"""
from decimal import Decimal, InvalidOperation
from nexus.paper_execution import TIERS, positive, quote_age_ok

def number(value):
    if isinstance(value,bool): raise ValueError("Boolean is not a quantity")
    try: n=Decimal(str(value))
    except (TypeError,ValueError,InvalidOperation): raise ValueError("Malformed quantity")
    if not n.is_finite():raise ValueError("Non-finite quantity")
    return n

def evaluate(request):
    reject=[]
    if request.get("data_verified") is not True:reject.append("UNVERIFIED_MARKET_DATA")
    if request.get("instrument_authorized") is not True:reject.append("INSTRUMENT_NOT_AUTHORIZED")
    if request.get("market_open") is not True:reject.append("MARKET_NOT_OPEN")
    if request.get("account_reconciled") is not True:reject.append("ACCOUNT_NOT_RECONCILED")
    if request.get("emergency_halt") is not False:reject.append("EMERGENCY_HALT_OR_UNKNOWN")
    if request.get("cooldown_active") is not False:reject.append("COOLDOWN_ACTIVE_OR_UNKNOWN")
    if not quote_age_ok(request.get("quote_timestamp")):reject.append("STALE_OR_MISSING_QUOTE")
    tier=request.get("tier")
    if tier not in TIERS:reject.append("UNSUPPORTED_TIER")
    try:
        equity=number(request.get("equity"))
        notional=number(request.get("notional"))
        exposure=number(request.get("current_exposure"))
        daily_loss=number(request.get("realized_daily_loss"))
        fee_bps=number(request.get("roundtrip_fees_bps"))
        spread_bps=number(request.get("spread_bps"))
        slippage_bps=number(request.get("roundtrip_slippage_bps"))
        forecast_bps=number(request.get("conservative_expected_edge_bps"))
        worst_loss_bps=number(request.get("modeled_worst_loss_bps"))
        if equity<=0 or notional<=0 or exposure<0 or daily_loss<0:reject.append("INVALID_ACCOUNT_VALUES")
        if min(fee_bps,spread_bps,slippage_bps,worst_loss_bps)<0:reject.append("INVALID_COST_ESTIMATE")
        total_cost=fee_bps+spread_bps+slippage_bps
        # Require a cushion of at least another 50% of modeled costs + 10 bps.
        if forecast_bps <= total_cost*Decimal("1.5")+Decimal(10):
            reject.append("INSUFFICIENT_NET_EDGE_AFTER_COSTS")
        if worst_loss_bps>250:reject.append("EXCESSIVE_SCENARIO_LOSS")
        if daily_loss>=equity*Decimal(".01"):reject.append("DAILY_LOSS_BUDGET_EXHAUSTED")
        if equity>0 and notional>equity*TIERS.get(tier,Decimal(0)):
            reject.append("TIER_SIZE_CAP")
        if equity>0 and exposure+notional>equity*Decimal(".10"):
            reject.append("PORTFOLIO_EXPOSURE_CAP")
        if equity>0 and (notional*worst_loss_bps/Decimal(10000))>equity*Decimal(".001"):
            reject.append("SCENARIO_LOSS_BUDGET")
    except ValueError:
        total_cost=None
        reject.append("MISSING_OR_INVALID_NUMERIC_EVIDENCE")
    return {
        "decision":"REJECT" if reject else "PAPER_RESEARCH_CANDIDATE",
        "reasons":sorted(set(reject)),
        "live_execution_authorized":False,
        "simulation_only":True,
        "cost_bps":float(total_cost) if total_cost is not None else None,
        "meaning":"Admission gate for further paper research only. Estimates cannot guarantee outcomes."
    }
