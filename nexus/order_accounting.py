"""Separate synthetic execution account; never changes the quote-driven portfolio.

Priced fills and accounting are replayed from the same durable command transaction.
Unpriced historical events remain explicitly unreconciled; no quantity is inferred.
"""
import copy
from decimal import Decimal, InvalidOperation
from nexus.order_gateway import OrderGateway


def number(value, zero=False):
    if isinstance(value, bool):
        raise ValueError('INVALID_EXECUTION_NUMBER')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('INVALID_EXECUTION_NUMBER')
    if not result.is_finite() or result > Decimal('1e12') or result < 0:
        raise ValueError('INVALID_EXECUTION_NUMBER')
    if not zero and result < Decimal('1e-12'):
        raise ValueError('INVALID_EXECUTION_NUMBER')
    return result


class AccountingOrderGateway(OrderGateway):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.account = None
        self.executions = {}

    def initialize_account(self, capital):
        amount = number(capital)
        if amount > 1000000:
            raise ValueError('PAPER_CAPITAL_LIMIT')
        if self.account is not None:
            raise ValueError('ACCOUNT_ALREADY_INITIALIZED')
        self.account = dict(initial=str(amount), cash=str(amount), positions={})
        return self.account_report()

    def simulate_priced_fill(self, client_order_id, event_id, sequence, quantity, price, fee='0'):
        if type(sequence) is not int or not 1 <= sequence <= 1000:
            raise ValueError('INVALID_EVENT_SEQUENCE')
        qty = number(quantity); px = number(price); cost = number(fee, zero=True)
        detail = dict(quantity=str(qty.normalize()), price=str(px.normalize()), fee=str(cost.normalize()), sequence=sequence)
        key = (client_order_id, event_id)
        with self.lock:
            if key in self.executions:
                if self.executions[key] != detail:
                    raise ValueError('DUPLICATE_EXECUTION_CONFLICT')
                return {**copy.deepcopy(self.intents[client_order_id]), 'idempotent_replay':True}
            if self.account is None:
                raise ValueError('INITIALIZE_EXECUTION_ACCOUNT_FIRST')
            record = self.intents.get(client_order_id)
            if record is None:
                raise ValueError('UNKNOWN_ORDER_ID')
            # Mixing reference-only and quantity-based fills in one order is ambiguous.
            if any((client_order_id, eid) not in self.executions for eid, event in record['lifecycle_events'].items() if event['kind']=='FILL'):
                raise ValueError('ORDER_HAS_UNPRICED_FILLS')
            amount = qty * px
            cash = Decimal(self.account['cash'])
            position = self.account['positions'].get(record['product'], dict(quantity='0', entry='0'))
            held = Decimal(position['quantity'])
            if record['side'] == 'BUY':
                if amount + cost > cash:
                    raise ValueError('INSUFFICIENT_SIMULATED_CASH')
                new_qty = held + qty
                entry = (held * Decimal(position['entry']) + amount) / new_qty
                delta = -amount - cost
            else:
                if qty > held:
                    raise ValueError('INSUFFICIENT_SIMULATED_POSITION')
                new_qty = held - qty
                entry = Decimal(position['entry'])
                delta = amount - cost
                if cash + delta < 0:
                    raise ValueError('INSUFFICIENT_SIMULATED_CASH')
            result = super().simulate_event(client_order_id, event_id, sequence, 'FILL', str(amount), str(cost))
            if result['idempotent_replay']:
                raise ValueError('EVENT_ALREADY_EXISTS_WITHOUT_EXECUTION')
            self.account['cash'] = str(cash + delta)
            if new_qty:
                self.account['positions'][record['product']] = dict(quantity=str(new_qty), entry=str(entry))
            else:
                self.account['positions'].pop(record['product'], None)
            self.executions[key] = detail
            return result

    def account_report(self):
        unpriced = sum(1 for oid, order in self.intents.items() for eid, event in order['lifecycle_events'].items()
                       if event['kind']=='FILL' and (oid, eid) not in self.executions)
        return dict(mode='SYNTHETIC_EXECUTION_ACCOUNT', simulation_only=True, live_orders_enabled=False,
                    initialized=self.account is not None, account=copy.deepcopy(self.account),
                    priced_fills=len(self.executions), unpriced_fills=unpriced,
                    reconciled=self.account is not None and unpriced == 0,
                    shared_with_quote_portfolio=False)
