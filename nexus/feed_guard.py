"""Fail-closed diagnostic checks on public read-only ticker snapshots.

This compares consecutive observations from ONE provider, not independent feeds,
and is NOT a volatility forecast or verified market-price oracle.
"""
from decimal import Decimal, InvalidOperation
import math
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
        # Retained evidence canonicalizes decimals to exact built-in text.
        # Text subclasses remain rejected before comparison or conversion.
        if type(value) not in (int,float,Decimal,str):
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
        if type(now) not in (int,float) or not math.isfinite(now):
            return self._report(["INVALID_RUNTIME_CLOCK"],"UNKNOWN")
        if quote is None:
            return self._report(["MISSING_QUOTE"],"UNKNOWN")
        if type(quote) is not dict or any(type(key) is not str for key in quote):
            return self._report(["INVALID_QUOTE_FIELDS"],"UNKNOWN")
        product=quote.get("product")
        if type(product) is not str or product not in ("BTC-USD","ETH-USD","SOL-USD"):
            reasons.append("PRODUCT_NOT_ALLOWLISTED")
        provider=quote.get("provider")
        if (type(provider) is not str or provider!="coinbase_exchange_public" or
                quote.get("read_only") is not True or quote.get("orders_allowed") is not False):
            reasons.append("UNVERIFIED_READ_ONLY_PROVENANCE")
        try:
            bid=self.number(quote.get("bid"))
            ask=self.number(quote.get("ask"))
            last=self.number(quote.get("last"))
            received=quote["received_at"]
            if type(received) not in (int,float) or not math.isfinite(received):
                raise ValueError("BAD_RECEIPT_CLOCK")
            if not -2<=now-received<=MAX_QUOTE_AGE_SECONDS:
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
                prior,last_seen=previous
                elapsed=now-last_seen
                if 0<=elapsed<=MAX_GAP_SECONDS:
                    prices=(bid,ask,last)
                    if any(abs(current-prev)/prev*100>MAX_JUMP_PCT
                            for current,prev in zip(prices,prior)):
                        reasons.append("UNCONFIRMED_PRICE_JUMP")
                elif elapsed<0:
                    reasons.append("CLOCK_REGRESSION")
            if not reasons:
                self._previous[product]=((bid,ask,last),now)
        return self._report(reasons,product or "UNKNOWN")

    @staticmethod
    def _report(reasons,product):
        return {"component":"SINGLE_SOURCE_FEED_GUARD","product":product,
                "decision":"VETO" if reasons else "READ_ONLY_OBSERVATION",
                "reasons":reasons,"independent_price_verification":False,
                "live_orders_enabled":False,
                "note":"Public single-source integrity heuristics only; high-volatility price jumps require another source."}

feed_guard=FeedGuard()
