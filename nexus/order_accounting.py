"""Synthetic execution accounting, optionally linked atomically to the paper ledger.

Priced fills and accounting are replayed from the same durable command transaction.
Unpriced historical events remain explicitly unreconciled; no quantity is inferred.
"""
import copy
import hashlib
import json
from bisect import bisect_left
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


MAX_ARCHIVED_INTENTS=10000
MAX_ARCHIVE_BATCHES=100
MAX_RETIRED_ORDER_ID_FINGERPRINTS=100000
ZERO_SHA256='0'*64
RETIRED_ID_POLICY='nexus.retired-order-id-fingerprint.v1'
RETIRED_ID_BASELINE_KIND='OPERATOR_REVIEWED_UNATTESTED'


def order_id_fingerprint(client_order_id):
    """Return a bounded pseudonymous membership key for a retired ID.

    A truncated digest collision can only reject a new local-paper ID; it can
    never permit reuse. The original ID is not copied into a fresh epoch, but
    low-entropy IDs can still be guessed and remain private operational data.
    """
    if not isinstance(client_order_id,str):raise ValueError('INVALID_ORDER_ID')
    return hashlib.sha256((RETIRED_ID_POLICY+'\0'+client_order_id).encode()).hexdigest()[:32]


def retired_id_set_sha256(fingerprints):
    return hashlib.sha256(json.dumps(list(fingerprints),separators=(',',':')).encode()).hexdigest()

class AccountingOrderGateway(OrderGateway):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.account = None
        self.executions = {}
        self.portfolio_linked = False
        self.order_terms = {}
        self.quote_events = {}
        self._applying_quote = False
        self.archived_intents = {}
        self.archive_batches = {}
        self.archival_boundary = 0
        self.epoch_lineage = None
        self.retired_order_id_fingerprints = []

    def bind_replacement_epoch(self,generation,predecessor_archive_sha256,predecessor_epoch_sha256,
                               retired_order_id_fingerprints=None,retired_order_id_baseline_review_sha256=None):
        """Bind a fresh halted journal to its exact predecessor without importing history."""
        if (type(generation) is not int or not 1<=generation<=1000000 or
            not isinstance(predecessor_archive_sha256,str) or len(predecessor_archive_sha256)!=64 or
            any(character not in '0123456789abcdef' for character in predecessor_archive_sha256) or
            not isinstance(predecessor_epoch_sha256,str) or len(predecessor_epoch_sha256)!=64 or
            any(character not in '0123456789abcdef' for character in predecessor_epoch_sha256) or
            (generation==1)!=(predecessor_epoch_sha256==ZERO_SHA256)):
            raise ValueError('INVALID_REPLACEMENT_EPOCH_LINEAGE')
        body=dict(generation=generation,predecessor_archive_sha256=predecessor_archive_sha256,
                  predecessor_epoch_sha256=predecessor_epoch_sha256)
        fingerprints=None if retired_order_id_fingerprints is None else list(retired_order_id_fingerprints)
        if fingerprints is not None:
            if (len(fingerprints)>MAX_RETIRED_ORDER_ID_FINGERPRINTS or
                fingerprints!=sorted(set(fingerprints)) or
                any(not isinstance(value,str) or len(value)!=32 or
                    any(character not in '0123456789abcdef' for character in value)
                    for value in fingerprints)):
                raise ValueError('INVALID_RETIRED_ORDER_ID_FINGERPRINTS')
            body.update(retired_order_id_policy=RETIRED_ID_POLICY,
                retired_order_id_fingerprint_count=len(fingerprints),
                retired_order_id_fingerprints_sha256=retired_id_set_sha256(fingerprints))
        if retired_order_id_baseline_review_sha256 is not None:
            if (fingerprints is None or not isinstance(retired_order_id_baseline_review_sha256,str) or
                len(retired_order_id_baseline_review_sha256)!=64 or
                any(character not in '0123456789abcdef'
                    for character in retired_order_id_baseline_review_sha256)):
                raise ValueError('INVALID_RETIRED_ORDER_ID_BASELINE_REVIEW')
            body.update(retired_order_id_baseline_kind=RETIRED_ID_BASELINE_KIND,
                retired_order_id_baseline_review_sha256=retired_order_id_baseline_review_sha256)
        lineage={**body,'epoch_sha256':hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
        if self.epoch_lineage is not None:
            if self.epoch_lineage!=lineage or (fingerprints is not None and self.retired_order_id_fingerprints!=fingerprints):
                raise ValueError('REPLACEMENT_EPOCH_LINEAGE_CONFLICT')
            return {**copy.deepcopy(self.epoch_lineage),'idempotent_replay':True}
        if (not self.halted or not self.portfolio_linked or self.account is None or
            self.account['positions'] or self.intents or self.archived_intents or self.executions or
            self.quote_events or self.order_terms or self.archive_batches):
            raise ValueError('REPLACEMENT_EPOCH_REQUIRES_FRESH_HALTED_FLAT_ACCOUNT')
        self.epoch_lineage=lineage
        if fingerprints is not None:self.retired_order_id_fingerprints=fingerprints
        return {**copy.deepcopy(lineage),'idempotent_replay':False}

    def submit(self,client_order_id,*args,**kwargs):
        fingerprint=order_id_fingerprint(client_order_id)
        index=bisect_left(self.retired_order_id_fingerprints,fingerprint)
        if (index<len(self.retired_order_id_fingerprints) and
            self.retired_order_id_fingerprints[index]==fingerprint):
            raise ValueError('RETIRED_ORDER_ID_REUSE')
        return super().submit(client_order_id,*args,**kwargs)

    def lookup(self,client_order_id):
        return self.intents.get(client_order_id) or self.archived_intents.get(client_order_id)

    def archive_terminal(self,batch_id,sequence_boundary):
        if not isinstance(batch_id,str) or not 1<=len(batch_id)<=64 or not batch_id.isascii() or not batch_id.replace('-','').replace('_','').isalnum():
            raise ValueError('INVALID_ARCHIVE_BATCH_ID')
        if batch_id in self.archive_batches:
            return {**copy.deepcopy(self.archive_batches[batch_id]),'idempotent_replay':True}
        if type(sequence_boundary) is not int or sequence_boundary<=self.archival_boundary:
            raise ValueError('INVALID_ARCHIVE_BOUNDARY')
        if not self.halted:raise ValueError('ARCHIVAL_REQUIRES_HALT')
        if self.account is None or self.account['positions'] or not self.account_report()['reconciled']:
            raise ValueError('ARCHIVAL_REQUIRES_RECONCILED_FLAT_ACCOUNT')
        if not self.intents:raise ValueError('NO_TERMINAL_ORDERS_TO_ARCHIVE')
        terminal=('SIMULATED_FILLED','SIMULATED_CANCELLED','SIMULATED_REJECTED','SIMULATED_EXPIRED')
        if any(record['status'] not in terminal for record in self.intents.values()):raise ValueError('ARCHIVAL_HAS_OPEN_ORDERS')
        if len(self.archived_intents)+len(self.intents)>MAX_ARCHIVED_INTENTS or len(self.archive_batches)>=MAX_ARCHIVE_BATCHES:
            raise ValueError('RETAINED_ARCHIVE_LIMIT')
        ids=sorted(self.intents)
        evidence=dict(orders={oid:self.intents[oid] for oid in ids},
                      executions=[[list(k),v] for k,v in sorted(self.executions.items()) if k[0] in self.intents],
                      quote_events=[[list(k),v] for k,v in sorted(self.quote_events.items()) if k[0] in self.intents],
                      terms={oid:self.order_terms[oid] for oid in ids if oid in self.order_terms})
        digest=hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        manifest=dict(batch_id=batch_id,sequence_boundary=sequence_boundary,order_ids=ids,order_count=len(ids),evidence_sha256=digest)
        self.archived_intents.update(self.intents)
        self.intents.clear()
        self.archive_batches[batch_id]=manifest
        self.archival_boundary=sequence_boundary
        return {**copy.deepcopy(manifest),'idempotent_replay':False}

    def initialize_account(self, capital):
        amount = number(capital)
        if amount > 1000000:
            raise ValueError('PAPER_CAPITAL_LIMIT')
        if self.account is not None:
            raise ValueError('ACCOUNT_ALREADY_INITIALIZED')
        self.account = dict(initial=str(amount), cash=str(amount), positions={})
        return self.account_report()

    def link_portfolio(self):
        if self.portfolio_linked:
            return {"linked":True,"idempotent_replay":True}
        if self.account is None or self.executions or self.account['positions'] or self.account['cash']!=self.account['initial']:
            raise ValueError('LINK_REQUIRES_NEW_EXECUTION_ACCOUNT')
        if any(event['kind']=='FILL' for order in self.intents.values() for event in order['lifecycle_events'].values()):
            raise ValueError('LINK_REQUIRES_NO_EXISTING_FILLS')
        self.portfolio_linked = True
        return {"linked":True,"idempotent_replay":False}

    def set_execution_terms(self, client_order_id, created_at, ttl_seconds):
        created=number(created_at);ttl=number(ttl_seconds)
        if ttl>3600 or created+ttl>Decimal('1e12'):raise ValueError('ORDER_TTL_LIMIT')
        terms=dict(created_at=str(created.normalize()),expires_at=str((created+ttl).normalize()))
        with self.lock:
            record=self.lookup(client_order_id)
            if record is None:raise ValueError('UNKNOWN_ORDER_ID')
            if client_order_id in self.order_terms:
                if self.order_terms[client_order_id]!=terms:raise ValueError('ORDER_TERMS_CONFLICT')
                return {**copy.deepcopy(record),'idempotent_replay':True}
            if self.halted:raise ValueError('EMERGENCY_HALT_ACTIVE')
            if record['status']!='SIMULATED_ACK_ONLY' or record['lifecycle_events']:
                raise ValueError('TERMS_REQUIRE_UNFILLED_ORDER')
            self.order_terms[client_order_id]=terms
            return {**copy.deepcopy(record),'idempotent_replay':False}

    def expire_order(self, client_order_id, evaluated_at):
        now=number(evaluated_at)
        with self.lock:
            record=self.lookup(client_order_id)
            if record is None:raise ValueError('UNKNOWN_ORDER_ID')
            if client_order_id not in self.order_terms:raise ValueError('MISSING_EXECUTION_TERMS')
            if now<Decimal(self.order_terms[client_order_id]['expires_at']):raise ValueError('ORDER_NOT_EXPIRED')
            changed=record['status'] in ('SIMULATED_ACK_ONLY','SIMULATED_PARTIALLY_FILLED')
            if changed:record['status']='SIMULATED_EXPIRED'
            return {**copy.deepcopy(record),'expired':record['status']=='SIMULATED_EXPIRED','idempotent_replay':not changed}

    def simulate_quote_fill(self, client_order_id, event_id, sequence, quantity, bid, ask,
                            available_quantity, quote_at, evaluated_at, fee_bps=10, slippage_bps=5):
        from nexus.quote_execution import plan
        if type(sequence) is not int or not 1<=sequence<=1000:raise ValueError('INVALID_EVENT_SEQUENCE')
        if not isinstance(event_id,str) or not 1<=len(event_id)<=64 or not event_id.isascii() or not event_id.replace('-','').replace('_','').isalnum():
            raise ValueError('INVALID_EVENT_ID')
        with self.lock:
            record=self.lookup(client_order_id)
            if record is None:raise ValueError('UNKNOWN_ORDER_ID')
            modeled=plan(record['side'],quantity,bid,ask,available_quantity,quote_at,evaluated_at,fee_bps,slippage_bps)
            detail=dict(sequence=sequence,**modeled)
            key=(client_order_id,event_id)
            if key in self.quote_events:
                if self.quote_events[key]!=detail:raise ValueError('DUPLICATE_QUOTE_EVENT_CONFLICT')
                return {**copy.deepcopy(record),'idempotent_replay':True}
            if client_order_id not in self.order_terms:raise ValueError('MISSING_EXECUTION_TERMS')
            if self.halted:raise ValueError('EMERGENCY_HALT_ACTIVE')
            if number(quote_at)<Decimal(self.order_terms[client_order_id]['created_at']):raise ValueError('QUOTE_PRECEDES_ORDER')
            previous_times=[Decimal(v['assumptions']['evaluated_at']) for (oid,_),v in self.quote_events.items() if oid==client_order_id]
            if previous_times and number(evaluated_at)<max(previous_times):raise ValueError('QUOTE_TIME_REGRESSION')
            if number(evaluated_at)>=Decimal(self.order_terms[client_order_id]['expires_at']):
                return self.expire_order(client_order_id,evaluated_at)
            self._applying_quote=True
            try:
                result=self.simulate_priced_fill(client_order_id,event_id,sequence,modeled['quantity'],modeled['price'],modeled['fee'])
            finally:
                self._applying_quote=False
            self.quote_events[key]=detail
            return result

    def simulate_event(self, client_order_id, event_id, sequence, kind, fill_notional='0', fee='0'):
        if client_order_id in self.order_terms and kind=='FILL':
            raise ValueError('TIMED_ORDER_REQUIRES_QUOTE_MODEL')
        return super().simulate_event(client_order_id,event_id,sequence,kind,fill_notional,fee)

    def expire_due(self, evaluated_at):
        now=number(evaluated_at)
        with self.lock:
            due=[oid for oid,terms in self.order_terms.items()
                 if now>=Decimal(terms['expires_at']) and self.intents.get(oid,{}).get('status') in ('SIMULATED_ACK_ONLY','SIMULATED_PARTIALLY_FILLED')]
            for oid in due:self.expire_order(oid,evaluated_at)
            return dict(expired_order_ids=due,expired_count=len(due),live_orders_enabled=False)

    def simulate_priced_fill(self, client_order_id, event_id, sequence, quantity, price, fee='0'):
        if client_order_id in self.order_terms and not self._applying_quote:
            raise ValueError('TIMED_ORDER_REQUIRES_QUOTE_MODEL')
        if type(sequence) is not int or not 1 <= sequence <= 1000:
            raise ValueError('INVALID_EVENT_SEQUENCE')
        qty = number(quantity); px = number(price); cost = number(fee, zero=True)
        detail = dict(quantity=str(qty.normalize()), price=str(px.normalize()), fee=str(cost.normalize()), sequence=sequence)
        key = (client_order_id, event_id)
        with self.lock:
            if key in self.executions:
                if self.executions[key] != detail:
                    raise ValueError('DUPLICATE_EXECUTION_CONFLICT')
                return {**copy.deepcopy(self.lookup(client_order_id)), 'idempotent_replay':True}
            if self.account is None:
                raise ValueError('INITIALIZE_EXECUTION_ACCOUNT_FIRST')
            record = self.lookup(client_order_id)
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
        unpriced = sum(1 for oid, order in list(self.intents.items())+list(self.archived_intents.items()) for eid, event in order['lifecycle_events'].items()
                       if event['kind']=='FILL' and (oid, eid) not in self.executions)
        return dict(mode='SYNTHETIC_EXECUTION_ACCOUNT', simulation_only=True, live_orders_enabled=False,
                    initialized=self.account is not None, account=copy.deepcopy(self.account),
                    priced_fills=len(self.executions), unpriced_fills=unpriced,
                    reconciled=self.account is not None and unpriced == 0,
                    shared_with_quote_portfolio=self.portfolio_linked)
