"""Latency Observatory: bounded in-process timing measurements for research.

This measures software response time only, NOT network-to-exchange latency or
realistic broker fill speed. High latency can veto paper research progression.
"""
import threading
import time
from collections import deque

MAX_SAMPLES=250
LIMIT_MS=1000.0

class LatencyObservatory:
    def __init__(self,clock=time.perf_counter):
        self.clock=clock
        self._lock=threading.Lock()
        self._samples=deque(maxlen=MAX_SAMPLES)

    def measure(self,name,callback):
        if name not in ("evidence_audit","council_decision","paper_replay"):
            raise ValueError("Unrecognized instrumented research operation")
        started=self.clock()
        try:
            return callback()
        finally:
            delta=(self.clock()-started)*1000
            if not isinstance(delta,(float,int)) or delta<0 or delta!=delta:
                delta=float("inf")
            with self._lock:
                self._samples.append({"operation":name,"elapsed_ms":round(delta,4),
                   "within_local_budget":delta<=LIMIT_MS})

    def summary(self):
        with self._lock:
            data=list(self._samples)
        times=sorted(x["elapsed_ms"] for x in data)
        p95=times[min(len(times)-1,int((len(times)-1)*.95))] if times else None
        maximum=max(times) if times else None
        exceeded=sum(1 for x in data if not x["within_local_budget"])
        return {"mode":"LOCAL_PROCESS_MEASUREMENTS","observations":len(data),
            "p95_ms":p95,"max_ms":maximum,"budget_ms":LIMIT_MS,
            "within_budget":bool(data) and exceeded==0,
            "budget_exceeded_count":exceeded,
            "exchange_network_latency_measured":False,
            "order_fill_latency_measured":False,
            "orders_enabled":False,
            "latest":data[-10:]}

latency_observatory=LatencyObservatory()
