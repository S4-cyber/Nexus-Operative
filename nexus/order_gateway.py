"""Provider-neutral order-intent gateway: NO broker/network IO.

Designed for broker adapters (Alpaca paper, IBKR paper, Coinbase) once separately
authorized. Current adapter always simulates an acknowledgement in-memory.
Never implies broker submission, matched fills, or actual asset ownership.
"""
import hashlib,json,threading,time
from decimal import Decimal,InvalidOperation

ALLOWED_PROVIDERS=("nexus_local_paper","alpaca_paper","ibkr_paper","coinbase_advanced")
MAX_INTENTS=1000

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

    def connections(self):
        return {"mode":"BROKER_ADAPTER_DESIGN_ONLY","providers":[
            {"id":p,"connection":"LOCAL_SIMULATION" if p=="nexus_local_paper" else "NOT_CONNECTED",
             "paper_submission_supported":p=="nexus_local_paper",
             "live_submission_supported":False} for p in ALLOWED_PROVIDERS],
            "live_orders_enabled":False,"broker_connected":False,
            "note":"External providers are placeholders; no credentials, broker calls or order routing."}

    def submit(self,client_order_id,product,side,notional,provider="nexus_local_paper"):
        if not isinstance(client_order_id,str) or not 1<=len(client_order_id)<=64 or not client_order_id.replace("-","").replace("_","").isalnum():
            raise ValueError("INVALID_ORDER_ID")
        if not isinstance(product,str) or product not in ("BTC-USD","ETH-USD","SOL-USD"):
            raise ValueError("PRODUCT_NOT_ELIGIBLE_FOR_SIMULATION")
        if side not in ("BUY","SELL"):raise ValueError("INVALID_SIDE")
        amount=_positive(notional)
        if provider not in ALLOWED_PROVIDERS:raise ValueError("UNKNOWN_PROVIDER")
        payload={"id":client_order_id,"product":product,"side":side,
                 "notional":str(amount),"provider":provider}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        with self.lock:
            old=self.intents.get(client_order_id)
            if old:
                if old["intent_sha256"]!=digest:raise ValueError("DUPLICATE_ID_CONFLICT")
                return {**old,"idempotent_replay":True}
            if self.halted:raise ValueError("EMERGENCY_HALT_ACTIVE")
            if provider!="nexus_local_paper":raise ValueError("EXTERNAL_BROKER_NOT_AUTHORIZED")
            if len(self.intents)>=MAX_INTENTS:raise ValueError("INTENT_CAP_REACHED")
            record={**payload,"intent_sha256":digest,"status":"SIMULATED_ACK_ONLY",
                "simulated_filled_notional":"0","created_monotonic":self.clock(),
                "sent_to_broker":False,"live_orders_enabled":False}
            self.intents[client_order_id]=record
            return {**record,"idempotent_replay":False}

    def cancel(self,client_order_id):
        with self.lock:
            record=self.intents.get(client_order_id)
            if record is None:raise ValueError("UNKNOWN_ORDER_ID")
            if record["status"]=="SIMULATED_ACK_ONLY":record["status"]="SIMULATED_CANCELLED"
            return {**record,"sent_to_broker":False}

    def emergency_halt(self):
        with self.lock:
            self.halted=True
            for record in self.intents.values():
                if record["status"]=="SIMULATED_ACK_ONLY":
                    record["status"]="SIMULATED_CANCELLED"
            return {"halted":True,"broker_connected":False,"live_orders_enabled":False,
                    "outstanding_simulated_intents":0}

    def reset(self):
        with self.lock:
            self.halted=False
            return {"halted":False,"paused":True,"live_orders_enabled":False}

gateway=OrderGateway()
