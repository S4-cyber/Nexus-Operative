"""Conservative adverse-move stress screening for SIMULATED positions.

This is a static model, not a maximum-loss guarantee. Gaps, liquidity crises,
short exposure, outages, exchange restrictions and counterparty failures can
produce losses beyond these scenarios. No live order authority.
"""
from decimal import Decimal, InvalidOperation

SCENARIOS = (
    ("ordinary_volatility", "0.02", "0.003"),
    ("overnight_gap", "0.08", "0.012"),
    ("liquidity_crisis", "0.20", "0.05"),
    ("extreme_dislocation", "0.50", "0.10"),
)

def dec(v, *, strictly_positive=True):
    if isinstance(v,bool):raise ValueError("Invalid boolean amount")
    try: n=Decimal(str(v))
    except (InvalidOperation,ValueError,TypeError):raise ValueError("Invalid decimal input")
    if not n.is_finite() or (n<=0 if strictly_positive else n<0):
        raise ValueError("Amount must be finite and nonnegative")
    return n

def stress_screen(equity, notional, direction="UP", daily_loss=0, max_stressed_loss_fraction="0.005"):
    equity,notional=dec(equity),dec(notional)
    daily_loss=dec(daily_loss,strictly_positive=False)
    cap=dec(max_stressed_loss_fraction)
    if direction not in ("UP","DOWN"):
        raise ValueError("Unsupported direction")
    if notional>equity or cap>Decimal("0.01"):
        raise ValueError("Notional exceeds unleveraged equity, or stress budget too permissive")
    # DOWN here is purely simulated directional hypothesis, NOT a short-sale implementation.
    rows=[]
    for name, adverse_move, friction in SCENARIOS:
        loss=notional*(Decimal(adverse_move)+Decimal(friction))
        rows.append({"scenario":name,"adverse_move_pct":float(Decimal(adverse_move)*100),
            "friction_pct":float(Decimal(friction)*100),
            "modeled_loss":round(float(loss),6),
            "equity_loss_pct":round(float(loss/equity*100),5)})
    worst=max(Decimal(str(x["modeled_loss"])) for x in rows)
    reasons=[]
    if daily_loss>=equity*Decimal("0.01"):reasons.append("DAILY_LOSS_LIMIT")
    if worst+daily_loss>equity*cap:reasons.append("STRESSED_LOSS_BUDGET_EXCEEDED")
    return {"decision":"PAPER_RESEARCH_ONLY" if not reasons else "REJECT",
            "reasons":reasons,"mode":"STATIC_ADVERSE_SCENARIOS",
            "simulation_only":True,"live_execution_authorized":False,
            "max_modeled_loss":float(worst),"stress_budget":float(equity*cap),
            "scenarios":rows,
            "warning":"Scenario set is not exhaustive; actual losses may exceed all modeled losses."}
