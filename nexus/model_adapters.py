"""Advisory model orchestration with strict contracts; no network or broker APIs.

Adapters here are deterministic fixtures for testing the model boundary.
Actual LLM providers are NOT connected. Future model responses are untrusted;
only an independent deterministic governor could ever evaluate their claims.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import threading
import time

ROLES=("market_regime","strategy_research","execution_quality","portfolio_risk","adversarial_review")
MAX_RESPONSE_BYTES=8192
BUDGET_USD=Decimal("0.05")
_lock=threading.Lock()
_spend=Decimal("0")

def finite(value):
    if isinstance(value,bool):raise ValueError("Boolean number")
    try:v=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError) as exc:raise ValueError("Invalid decimal") from exc
    if not v.is_finite() or v<0:raise ValueError("Negative or nonfinite number")
    return v

def validate_response(value,role):
    if not isinstance(value,dict) or len(json.dumps(value,allow_nan=False).encode())>MAX_RESPONSE_BYTES:
        raise ValueError("Invalid advisory payload size")
    if value.get("role")!=role or value.get("verdict") not in ("ABSTAIN","CAUTION","FURTHER_RESEARCH"):
        raise ValueError("Invalid role or verdict")
    if type(value.get("reason")) is not str or not 1<=len(value["reason"])<=300:
        raise ValueError("Missing bounded reason")
    if value.get("order") is not None or value.get("credentials") is not None:
        raise ValueError("Untrusted trading or credential request")
    cost=finite(value.get("estimated_cost_usd",0))
    if cost>BUDGET_USD:raise ValueError("Single-adapter cost exceeds budget")
    return {"role":role,"verdict":value["verdict"],"reason":value["reason"],
            "estimated_cost_usd":float(cost),"adapter_type":"UNTRUSTED_ADVISORY"}

def fixture_adapter(role,snapshot):
    """Local synthetic mock, deliberately abstaining even on attractive claims."""
    return {"role":role,"verdict":"ABSTAIN",
            "reason":"No authenticated inference provider or independently verified edge",
            "estimated_cost_usd":0}

def coordinate(snapshot,adapters=None,timeout_seconds=1.0):
    if not isinstance(snapshot,dict):raise ValueError("Snapshot must be a mapping")
    if type(timeout_seconds) not in (float,int) or not 0<timeout_seconds<=5:
        raise ValueError("Invalid timeout")
    # Reject oversized or malformed input before any adapter runs.
    serialized=json.dumps(snapshot,sort_keys=True,allow_nan=False)
    if len(serialized.encode())>16000:raise ValueError("Snapshot too large")
    adapters=adapters or {}
    results=[]
    costs=Decimal("0")
    started=time.monotonic()
    # A distinct daemon worker for each role; never block indefinitely on a
    # misbehaving adapter or give advisory code trading permissions.
    for role in ROLES:
        worker=adapters.get(role,fixture_adapter)
        outcome={}
        completed=threading.Event()
        def invoke(worker=worker,role=role,outcome=outcome,completed=completed):
            try:outcome["value"]=worker(role,json.loads(serialized))
            except Exception as exc:outcome["error"]=type(exc).__name__
            finally:completed.set()
        thread=threading.Thread(target=invoke,daemon=True,name="nexus-advisory-"+role)
        thread.start()
        remaining=min(float(timeout_seconds), max(0,5-(time.monotonic()-started)))
        if not completed.wait(remaining):
            result={"role":role,"verdict":"ABSTAIN","reason":"MODEL_TIMEOUT","estimated_cost_usd":0,"adapter_type":"FAILED_CLOSED"}
        elif "error" in outcome:
            result={"role":role,"verdict":"ABSTAIN","reason":"MODEL_ERROR_"+outcome["error"],"estimated_cost_usd":0,"adapter_type":"FAILED_CLOSED"}
        else:
            try:result=validate_response(outcome["value"],role)
            except (ValueError,TypeError,OverflowError):
                result={"role":role,"verdict":"ABSTAIN","reason":"INVALID_MODEL_RESPONSE","estimated_cost_usd":0,"adapter_type":"FAILED_CLOSED"}
        results.append(result)
        costs+=Decimal(str(result["estimated_cost_usd"]))
    with _lock:
        global _spend
        if _spend+costs>BUDGET_USD:
            results=[{**x,"verdict":"ABSTAIN","reason":"INFERENCE_BUDGET_EXCEEDED"} for x in results]
            budget_status="EXCEEDED"
        else:
            _spend+=costs
            budget_status="WITHIN_LIMIT"
    votes={x["verdict"] for x in results}
    return {"mode":"ADVISORY_MODEL_SANDBOX","provider_connected":False,
        "specialists":results,"disagreement":len(votes)>1,
        "all_abstained":all(x["verdict"]=="ABSTAIN" for x in results),
        "budget_status":budget_status,"estimated_session_cost_usd":float(_spend),
        "snapshot_sha256":sha256(serialized.encode()).hexdigest(),
        "governor":"NO_ORDER_AUTHORITY","paper_orders_enabled":False,
        "live_orders_enabled":False,
        "note":"Fixture agents only. No external LLMs, verified profits, or broker execution."}
