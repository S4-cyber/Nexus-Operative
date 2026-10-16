"""Fail-closed diagnostic checks on public read-only ticker snapshots.

This compares consecutive observations from ONE provider, not independent feeds,
and is NOT a volatility forecast or verified market-price oracle.
"""
from decimal import Decimal, InvalidOperation
import threading
import time

MAX_SPREAD_BPS=Decimal("150")
MAX_QUOTE_AGE_SECONDS=10
MAX_JUMP_PCT=Decimal("15")
MAX_GAP_SECONDS=90

class FeedGuard:
    def __init__(self):
        self._lock=threading.Lock()
        self._previous={}

    @staticmethod
    def number(value):
        if isinstance(value,bool):
            raise ValueError("BAD_NUMERIC_QUOTE")
        try: out=Decimal(str(value))
        except (InvalidOperation,ValueError,TypeError) as exc:
            raise ValueError("BAD_NUMERIC_QUOTE") from exc
        if not out.is_finite() or out<=0:
            raise ValueError("BAD_NUMERIC_QUOTE")
        return out

    def evaluate(self,quote,now=None):
        now=time.time() if now is None else now
        reasons=[]
        if not isinstance(quote,dict):
            return self._report(["MISSING_QUOTE"],"UNKNOWN")
        product=quote.get("product")
        if product not in ("BTC-USD","ETH-USD","SOL-USD"):
            reasons.append("PRODUCT_NOT_ALLOWLISTED")
        if quote.get("provider")!="coinbase_exchange_public" or quote.get("read_only") is not True or quote.get("orders_allowed") is not False:
            reasons.append("UNVERIFIED_READ_ONLY_PROVENANCE")
        try:
            bid=self.number(quote.get("bid"))
            ask=self.number(quote.get("ask"))
            last=self.number(quote.get("last"))
            received=float(quote["received_at"])
            if not (received==received and -2<=now-received<=MAX_QUOTE_AGE_SECONDS):
                reasons.append("QUOTE_TOO_OLD")
            if bid>ask:
                reasons.append("CROSSED_MARKET")
            if (ask-bid)/((ask+bid)/2)*10000>MAX_SPREAD_BPS:
                reasons.append("EXCESSIVE_SPREAD")
            # Coinbase last-traded can be outside current bid/ask; do NOT reject
            # that condition without independent timestamps and sequence IDs.
        except (ValueError,TypeError,KeyError,OverflowError):
            reasons.append("INVALID_QUOTE_FIELDS")
        with self._lock:
            previous=self._previous.get(product)
            if not reasons and previous is not None:
                prev,last_seen=previous
                elapsed=now-last_seen
                if 0<=elapsed<=MAX_GAP_SECONDS:
                    change=abs(last-prev)/prev*100
                    if change>MAX_JUMP_PCT:
                        reasons.append("UNCONFIRMED_PRICE_JUMP")
                elif elapsed<0:
                    reasons.append("CLOCK_REGRESSION")
            if not reasons:
                self._previous[product]=(last,now)
        return self._report(reasons,product or "UNKNOWN")

    @staticmethod
    def _report(reasons,product):
        return {"component":"SINGLE_SOURCE_FEED_GUARD","product":product,
                "decision":"VETO" if reasons else "READ_ONLY_OBSERVATION",
                "reasons":reasons,"independent_price_verification":False,
                "live_orders_enabled":False,
                "note":"Public single-source integrity heuristics only; high-volatility price jumps require another source."}

feed_guard=FeedGuard()
