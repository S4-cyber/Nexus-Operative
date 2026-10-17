"""Deterministic synthetic quote execution assumptions; no market or broker IO."""
from decimal import Decimal
from nexus.order_accounting import number


def plan(side, quantity, bid, ask, available_quantity, quote_at, evaluated_at,
         fee_bps=10, slippage_bps=5):
    qty=number(quantity);bid=number(bid);ask=number(ask);capacity=number(available_quantity)
    stamp=number(quote_at);now=number(evaluated_at)
    fee=number(fee_bps,zero=True);slip=number(slippage_bps,zero=True)
    if side not in ('BUY','SELL'):raise ValueError('INVALID_SIDE')
    if not Decimal(0)<=now-stamp<=Decimal(2):raise ValueError('STALE_OR_FUTURE_SYNTHETIC_QUOTE')
    if bid>ask:raise ValueError('CROSSED_SYNTHETIC_QUOTE')
    if (ask-bid)/((ask+bid)/2)*10000>150:raise ValueError('EXCESSIVE_SYNTHETIC_SPREAD')
    if fee>100 or slip>100:raise ValueError('EXECUTION_COST_ASSUMPTION_LIMIT')
    if qty>capacity:raise ValueError('INSUFFICIENT_SYNTHETIC_LIQUIDITY')
    price=ask*(1+slip/10000) if side=='BUY' else bid*(1-slip/10000)
    notional=qty*price
    text=lambda value:str(value.normalize())
    return dict(quantity=text(qty),price=text(price),fee=text(notional*fee/10000),
                assumptions=dict(bid=text(bid),ask=text(ask),available_quantity=text(capacity),
                                 quote_at=text(stamp),evaluated_at=text(now),fee_bps=text(fee),slippage_bps=text(slip)))
