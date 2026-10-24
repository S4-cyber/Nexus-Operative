"""Pure disconnected lookup-outcome policy. No HTTP client or retry execution."""
from email.utils import parsedate_to_datetime
import math
import re

LOOKUP_URL = 'https://paper-api.alpaca.markets/v2/orders:by_client_order_id'
MAX_LOOKUPS_PER_REQUEST = 4
MIN_LOOKUP_INTERVAL = 1
MAX_RETRY_AFTER = 86400
OUTCOMES = frozenset(('FOUND','NOT_FOUND','TIMEOUT','NETWORK_ERROR','RATE_LIMITED','SERVER_ERROR','AUTH_DENIED','REVIEW_REQUIRED'))


def clock(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not 0<=value<=1e12 or not math.isfinite(value):
        raise ValueError('INVALID_BROKER_LOOKUP_CLOCK')
    return value


def retry_after_seconds(value, *, evaluated_at):
    """Normalize one required, bounded Retry-After value.

    This pure helper performs no request, sleep, or retry.  Callers decide
    which exact HTTP statuses may carry a provider-directed delay.
    """
    now=clock(evaluated_at)
    if (not isinstance(value,str) or len(value)>128 or not value.isascii() or
            '\r' in value or '\n' in value):
        raise ValueError('INVALID_RETRY_AFTER')
    if re.fullmatch(r'[0-9]{1,6}',value):delay=int(value)
    else:
        stamp=parsedate_to_datetime(value)
        if stamp.tzinfo is None:raise ValueError('INVALID_RETRY_AFTER')
        delay=math.ceil(stamp.timestamp()-now)
    delay=max(1,delay)
    if delay>MAX_RETRY_AFTER:raise ValueError('RETRY_AFTER_REQUIRES_REVIEW')
    return delay


def classify(*, status_code=None, transport_error=None, retry_after=None, evaluated_at):
    now=clock(evaluated_at)
    if transport_error is not None:
        if status_code is not None or transport_error not in ('TIMEOUT','NETWORK_ERROR'):
            raise ValueError('INVALID_BROKER_LOOKUP_RESULT')
        return dict(outcome=transport_error,retry_after_seconds=0)
    if type(status_code) is not int or not 100<=status_code<=599:
        raise ValueError('INVALID_BROKER_LOOKUP_RESULT')
    if status_code==200:outcome='FOUND'
    elif status_code==404:outcome='NOT_FOUND'
    elif status_code in (401,403):outcome='AUTH_DENIED'
    elif 500<=status_code<=599:outcome='SERVER_ERROR'
    elif status_code==429:
        if retry_after is None:delay=60  # Local conservative fallback, not a provider quota.
        else:
            try:
                delay=retry_after_seconds(retry_after,evaluated_at=now)
            except (ValueError,TypeError,OverflowError,OSError):
                return dict(outcome='REVIEW_REQUIRED',retry_after_seconds=0)
        return dict(outcome='RATE_LIMITED',retry_after_seconds=delay)
    else:outcome='REVIEW_REQUIRED'  # Includes redirects; no follow or fallback route.
    return dict(outcome=outcome,retry_after_seconds=0)
