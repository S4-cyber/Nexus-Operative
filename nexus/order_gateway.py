"""Provider-neutral order-intent gateway: NO broker/network IO.

Designed for broker adapters (Alpaca paper, IBKR paper, Coinbase) once separately
authorized. Current adapter models acknowledgements and lifecycle events in-memory.
Never implies broker submission, matched fills, or actual asset ownership.
"""
import copy,hashlib,json,threading,time
from decimal import Decimal,InvalidOperation

ALLOWED_PROVIDERS=("nexus_local_paper","alpaca_paper","ibkr_paper","coinbase_advanced")
MAX_INTENTS=1000


def _require_order_id(value):
    if (type(value) is not str or not 1<=len(value)<=64 or
            not value.replace("-","").replace("_","").isalnum()):
        raise ValueError("INVALID_ORDER_ID")
    return value


def _require_event_id(value):
    if (type(value) is not str or not 1<=len(value)<=64 or
            not value.isascii() or
            not value.replace("-","").replace("_","").isalnum()):
        raise ValueError("INVALID_EVENT_ID")
    return value


def _require_event_kind(value):
    if type(value) is not str or value not in ("FILL","REJECT"):
        raise ValueError("INVALID_EVENT_KIND")
    return value


def _positive(value):
    if isinstance(value,bool):raise ValueError("BOOLEAN_NOTIONAL")
    try:v=Decimal(str(value))
    except (ValueError,TypeError,InvalidOperation):raise ValueError("INVALID_NOTIONAL")
    if not v.is_finite() or v<=0 or v>Decimal("200"):raise ValueError("INVALID_NOTIONAL")
    return v

class OrderGateway:
    def __init__(self,clock=time.monotonic):
        self.clock=clock
        self.lock=threading.RLock()
        self.intents={}
        self.halted=False

    def lookup(self,client_order_id):
        _require_order_id(client_order_id)
        return self.intents.get(client_order_id)

    def connections(self):
        return {"mode":"BROKER_ADAPTER_DESIGN_ONLY","providers":[
            {"id":p,"connection":"LOCAL_SIMULATION" if p=="nexus_local_paper" else "NOT_CONNECTED",
             "paper_submission_supported":p=="nexus_local_paper",
             "live_submission_supported":False} for p in ALLOWED_PROVIDERS],
            "live_orders_enabled":False,"broker_connected":False,
            "note":"External providers are placeholders; no credentials, broker calls or order routing."}

    def _submit(self,client_order_id,product,side,notional,provider="nexus_local_paper",tier=None,
                extra_payload=None):
        _require_order_id(client_order_id)
        if type(product) is not str or product not in ("BTC-USD","ETH-USD","SOL-USD"):
            raise ValueError("PRODUCT_NOT_ELIGIBLE_FOR_SIMULATION")
        if type(side) is not str or side not in ("BUY","SELL"):
            raise ValueError("INVALID_SIDE")
        amount=_positive(notional)
        if type(provider) is not str or provider not in ALLOWED_PROVIDERS:
            raise ValueError("UNKNOWN_PROVIDER")
        if (tier is not None and (type(tier) is not str or
                tier not in ("terrestrial","solar","universal"))):
            raise ValueError("UNKNOWN_RISK_TIER")
        payload={"id":client_order_id,"product":product,"side":side,
                 "notional":str(amount),"provider":provider,"tier":tier}
        payload["notional"]=str(amount.normalize())
        if extra_payload is not None:
            if type(extra_payload) is not dict or any(type(key) is not str for key in extra_payload):
                raise ValueError("INVALID_INTENT_SCOPE")
            payload.update(extra_payload)
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        with self.lock:
            old=self.lookup(client_order_id)
            if old:
                if old["intent_sha256"]!=digest:raise ValueError("DUPLICATE_ID_CONFLICT")
                return {**copy.deepcopy(old),"idempotent_replay":True}
            if self.halted:raise ValueError("EMERGENCY_HALT_ACTIVE")
            if provider!="nexus_local_paper":raise ValueError("EXTERNAL_BROKER_NOT_AUTHORIZED")
            if len(self.intents)>=MAX_INTENTS:raise ValueError("INTENT_CAP_REACHED")
            record={**payload,"intent_sha256":digest,"status":"SIMULATED_ACK_ONLY",
                "simulated_filled_notional":"0","created_monotonic":self.clock(),
                "simulated_fee_total":"0","lifecycle_events":{},
                "sent_to_broker":False,"live_orders_enabled":False}
            self.intents[client_order_id]=record
            return {**copy.deepcopy(record),"idempotent_replay":False}

    def submit(self,client_order_id,product,side,notional,provider="nexus_local_paper",tier=None):
        return self._submit(client_order_id,product,side,notional,provider,tier)

    def simulate_event(self,client_order_id,event_id,sequence,kind,fill_notional="0",fee="0"):
        """Apply an ordered offline lifecycle event; never updates a portfolio.

        Fill amounts are incremental reference notionals, not exchange executions.
        Fees are explicit synthetic costs. Replay returns current order state.
        Cancellation of a partial fill preserves its filled amount and costs.
        """
        _require_order_id(client_order_id)
        _require_event_id(event_id)
        if type(sequence) is not int or not 1<=sequence<=1000:
            raise ValueError("INVALID_EVENT_SEQUENCE")
        _require_event_kind(kind)
        def nonnegative(value):
            if isinstance(value,bool):raise ValueError("INVALID_EVENT_AMOUNT")
            try:amount=Decimal(str(value))
            except (ValueError,TypeError,InvalidOperation):raise ValueError("INVALID_EVENT_AMOUNT")
            if not amount.is_finite() or not 0<=amount<=200:
                raise ValueError("INVALID_EVENT_AMOUNT")
            return amount
        amount=nonnegative(fill_notional);cost=nonnegative(fee)
        if kind=="FILL" and amount<=0:raise ValueError("INVALID_EVENT_AMOUNT")
        if kind=="REJECT" and (amount or cost):raise ValueError("REJECTION_HAS_FILL_COST")
        event={"sequence":sequence,"kind":kind,"fill_notional":str(amount.normalize()),"fee":str(cost.normalize())}
        with self.lock:
            record=self.lookup(client_order_id)
            if record is None:raise ValueError("UNKNOWN_ORDER_ID")
            events=record["lifecycle_events"]
            if event_id in events:
                if events[event_id]!=event:raise ValueError("DUPLICATE_EVENT_CONFLICT")
                return {**copy.deepcopy(record),"idempotent_replay":True}
            if sequence!=len(events)+1:raise ValueError("OUT_OF_ORDER_EVENT")
            if self.halted:raise ValueError("EMERGENCY_HALT_ACTIVE")
            if record["status"] not in ("SIMULATED_ACK_ONLY","SIMULATED_PARTIALLY_FILLED"):
                raise ValueError("ORDER_TERMINAL")
            filled=Decimal(record["simulated_filled_notional"])+amount
            requested=Decimal(record["notional"])
            if filled>requested:raise ValueError("SIMULATED_OVERFILL")
            if kind=="REJECT" and filled:raise ValueError("CANNOT_REJECT_PARTIAL_FILL")
            record["status"]=("SIMULATED_REJECTED" if kind=="REJECT" else
                "SIMULATED_FILLED" if filled==requested else "SIMULATED_PARTIALLY_FILLED")
            record["simulated_filled_notional"]=str(filled)
            record["simulated_fee_total"]=str(Decimal(record["simulated_fee_total"])+cost)
            events[event_id]=event
            return {**copy.deepcopy(record),"idempotent_replay":False}

    def cancel(self,client_order_id):
        _require_order_id(client_order_id)
        with self.lock:
            record=self.lookup(client_order_id)
            if record is None:raise ValueError("UNKNOWN_ORDER_ID")
            if record["status"] in ("SIMULATED_ACK_ONLY","SIMULATED_PARTIALLY_FILLED"):
                record["status"]="SIMULATED_CANCELLED"
            return {**copy.deepcopy(record),"sent_to_broker":False}

    def emergency_halt(self):
        with self.lock:
            self.halted=True
            for record in self.intents.values():
                if record["status"] in ("SIMULATED_ACK_ONLY","SIMULATED_PARTIALLY_FILLED"):
                    record["status"]="SIMULATED_CANCELLED"
            return {"halted":True,"broker_connected":False,"live_orders_enabled":False,
                    "outstanding_simulated_intents":0}

    def reset(self):
        with self.lock:
            self.halted=False
            return {"halted":False,"paused":True,"live_orders_enabled":False}

gateway=OrderGateway()

