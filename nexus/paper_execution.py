"""Fast deterministic PAPER execution simulator, no broker or network access.

Bid/ask spread, slippage, and explicit fees are subtracted on each fill.
Signals may evaluate UP or DOWN directions; DOWN is synthetic directional
exposure, NOT margin borrowing, short sale, or an executable broker order.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

D=Decimal
TIERS={"terrestrial":D("0.01"),"solar":D("0.02"),"universal":D("0.03")}
MAX_TOTAL_EXPOSURE=D("0.50")
MAX_DRAWDOWN=D("0.05")
COST_BPS=D("11")  # half-spread + modeled slippage per side
FEE=D("0.01")
SUPPORTED={"SPY","QQQ","IWM","BTC/USD","ETH/USD"}

def positive(n):
    try: v=D(str(n))
    except (InvalidOperation, ValueError, TypeError): raise ValueError("Invalid decimal")
    if not v.is_finite() or v<=0: raise ValueError("Value must be finite and positive")
    return v

def quote_age_ok(ts,now=None):
    if isinstance(ts,bool) or not isinstance(ts,(float,int)): return False
    now=datetime.now(timezone.utc).timestamp() if now is None else now
    return 0<=now-ts<=10

@dataclass
class PaperPosition:
    tier:str
    symbol:str
    direction:str
    quantity:Decimal
    reference_entry:Decimal
    opening_cost:Decimal
    reserved:Decimal

class PaperEngine:
    def __init__(self,capital="100",fee=FEE,cost_bps=COST_BPS):
        self.initial=positive(capital)
        self.cash=self.initial
        self.fee=positive(fee)
        self.cost_bps=positive(cost_bps)
        self.positions={}
        self.history=[]
        self.halted=False
        self.peak=self.initial

    def equity(self,prices):
        equity=self.cash
        for symbol,p in self.positions.items():
            if symbol not in prices:raise ValueError("Missing mark price")
            px=positive(prices[symbol])
            direction=D(1) if p.direction=="UP" else D(-1)
            equity+=p.reserved + p.quantity*(px-p.reference_entry)*direction
        return equity

    def _risk(self,prices):
        eq=self.equity(prices)
        self.peak=max(self.peak,eq)
        if eq<=self.peak*(D(1)-MAX_DRAWDOWN):
            self.halted=True
            raise ValueError("Max drawdown: engine halted")
        return eq

    def open(self,tier,symbol,direction,notional,market_price,timestamp,marks):
        if self.halted:raise ValueError("Engine halted")
        if tier not in TIERS or symbol not in SUPPORTED:raise ValueError("Unknown tier/symbol")
        if direction not in ("UP","DOWN"):raise ValueError("Unknown directional hypothesis")
        if not quote_age_ok(timestamp):raise ValueError("Quote too old")
        if symbol in self.positions:raise ValueError("Position already open")
        px=positive(market_price);amount=positive(notional)
        eq=self._risk(marks)
        exposure=sum((p.reserved for p in self.positions.values()),D(0))
        if amount>eq*TIERS[tier] or amount+exposure>eq*MAX_TOTAL_EXPOSURE:
            raise ValueError("Per-tier or aggregate risk cap exceeded")
        if amount+self.fee>self.cash:raise ValueError("Insufficient virtual cash")
        # Entry worse than mid on both directions.
        adjusted=px*(D(1)+self.cost_bps/D(10000) if direction=="UP" else D(1)-self.cost_bps/D(10000))
        qty=amount/px
        self.cash-=amount+self.fee
        p=PaperPosition(tier,symbol,direction,qty,adjusted,amount,amount)
        self.positions[symbol]=p
        event={"action":"OPEN","symbol":symbol,"tier":tier,"direction":direction,
               "paper_notional":float(amount),"price":float(adjusted),"fee":float(self.fee),"simulation_only":True}
        self.history.append(event)
        return event

    def close(self,symbol,market_price,timestamp,marks):
        if self.halted:raise ValueError("Engine halted")
        if symbol not in self.positions:raise ValueError("Position not open")
        if not quote_age_ok(timestamp):raise ValueError("Quote too old")
        px=positive(market_price)
        p=self.positions[symbol]
        # Closing fill worse than mid in opposite direction.
        factor=D(1)-self.cost_bps/D(10000) if p.direction=="UP" else D(1)+self.cost_bps/D(10000)
        exit_px=px*factor
        pnl=p.quantity*(exit_px-p.reference_entry)*(D(1) if p.direction=="UP" else D(-1))-self.fee-self.fee
        self.cash+=p.reserved + pnl + self.fee # opening fee was already removed from cash
        del self.positions[symbol]
        event={"action":"CLOSE","symbol":symbol,"tier":p.tier,"direction":p.direction,
               "realized_pnl_net_fees":float(pnl),"simulation_only":True}
        self.history.append(event)
        return event
