"""Explicit paced Alpaca BTC/USD observation, never an executable benchmark.

Three fixed GETs: paper account, US market-data quote, same paper account.
No order methods, arbitrary host/URL, retries, default storage or startup IO.
"""
from datetime import datetime,timezone
from decimal import Decimal,localcontext
import time
import hashlib

from nexus import alpaca_paper_inspection as reader
from nexus import alpaca_activity_normalizer as timestamps
from nexus.alpaca_paper_contract import number,text,sha
from nexus.paper_read_pacing import PaperReadPacing

PROFILE='nexus.paper-btc-quote-observation.v1'
MAX_AGE_NS=10_000_000_000
MAX_FUTURE_NS=2_000_000_000
FLAGS=('source_attested','clock_domains_verified','benchmark_source_verified',
    'benchmark_request_event_time_verified','broker_arrival_observed','venue_eligibility_verified',
    'executable_liquidity_verified','broker_latency_measured','actual_fee_coverage_verified',
    'atomic_snapshot','broker_account_reconciled','spendable_funds_verified','broker_connected',
    'ready_for_routing','ledger_applied','live_orders_enabled','money_movement_enabled',
    'automatic_retry','automatic_continuation','release_approved')


def _fail(code):raise reader.PaperInspectionError('PAPER_QUOTE_'+code)


def _scope(expected_account_id,pacing):
    account=reader._uuid(expected_account_id)
    if type(pacing) is not PaperReadPacing or pacing.account!=account:_fail('PACING_ACCOUNT_REQUIRED')
    from nexus.paper_activity_acceptance_probe import _private_path
    _private_path(pacing.path)
    pacing.inspect()  # Read-only corruption/foreign-account preflight; absent stays absent.
    return account


def _instant_ns(value):
    normalized=timestamps._instant(value);base,_,fraction=normalized[:-6].partition('.')
    delta=datetime.fromisoformat(base).replace(tzinfo=timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
    ns=(delta.days*86400+delta.seconds)*1_000_000_000+int(fraction.ljust(9,'0'))
    if not 0<=ns<=2**63-1:_fail('TIME_RANGE')
    return normalized,ns


def _quote(raw,received_at):
    if type(raw) is not dict or set(raw)!={'quotes'} or type(raw['quotes']) is not dict or set(raw['quotes'])!={'BTC/USD'}:
        _fail('RESPONSE_SCOPE')
    row=raw['quotes']['BTC/USD']
    if type(row) is not dict or set(row)!={'bp','ap','bs','as','t'}:_fail('QUOTE_FIELDS')
    bid=number(row['bp']);ask=number(row['ap']);bid_size=number(row['bs'],zero=True);ask_size=number(row['as'],zero=True)
    if bid>ask:_fail('CROSSED_QUOTE')
    at,ns=_instant_ns(row['t'])
    with localcontext() as ctx:
        ctx.prec=80
        # Preserve source nanoseconds exactly. The sampled local clock remains
        # float seconds and is never claimed to have nanosecond accuracy.
        age=Decimal(str(received_at))*1_000_000_000-ns
        if age>MAX_AGE_NS:_fail('STALE_QUOTE')
        if age < -MAX_FUTURE_NS:_fail('FUTURE_QUOTE')
    return dict(product='BTC-USD',location='us',bid=text(bid),ask=text(ask),bid_size=text(bid_size),ask_size=text(ask_size),
        exchange_at=at,exchange_time_ns=str(ns),sampled_age_ns=text(age),
        checks='REPORTED_FIELDS_COVERED' if bid_size and ask_size else 'ZERO_REPORTED_SIZE_REVIEW_REQUIRED')


def inspect_quote(api_key,api_secret,*,expected_account_id,confirm_read_only_paper=False,pacing=None,
                  opener=None,wall_clock=time.time,elapsed_clock=time.monotonic,permission_check=lambda:True,expected_pacing_head=None):
    """Capture once after explicit confirmation; shared local pacing is mandatory."""
    if confirm_read_only_paper is not True:_fail('EXPLICIT_CONFIRMATION_REQUIRED')
    account=_scope(expected_account_id,pacing)
    session=reader._ReadSession(api_key,api_secret,confirm_read_only_paper=True,expected_account_id=account,
        pacing=pacing,opener=opener,wall_clock=wall_clock,elapsed_clock=elapsed_clock,permission_check=permission_check,expected_pacing_head=expected_pacing_head,operation='BTC_QUOTE_OBSERVATION')
    try:
        with session:
            first=reader._account(session.get(reader.URLS[0]))
            if first['id']!=account:_fail('ACCOUNT_DISAGREEMENT')
            raw=session.get_btc_quote();received=session.evaluated_at()
            quote=_quote(raw,received)
            last=reader._account(session.get(reader.URLS[0]))
            if last!=first:_fail('ACCOUNT_CHANGED_DURING_READ')
            completed=session.evaluated_at()
            # A slow final account read must not return a now-expired quote.
            _quote(raw,completed)
            scope=dict(profile=PROFILE,account_sha256=hashlib.sha256(account.encode()).hexdigest(),quote=quote,
                source='INJECTED_UNATTESTED' if session.injected else 'ALPACA_HTTPS_READS',
                account_fields_sha256=sha(first),
                read_timing=dict(started_at=session.start[0],quote_received_at=received,completed_at=completed,
                    elapsed_seconds=session.previous[1]-session.start[1]))
            result=dict(mode='READ_ONLY_PAPER_BTC_QUOTE_OBSERVATION',status='REVIEW_REQUIRED',**scope,
                observation_sha256=sha(scope),fixed_get_requests=3,sequential_account_fields_stable=True,
                timestamp_scope='EXCHANGE_NS_EXACT_LOCAL_RECEIPT_FLOAT_SECONDS_NOT_CLOCK_SYNCHRONIZATION',
                **{k:False for k in FLAGS},
                limitations='One explicitly confirmed BTC/USD quote from Alpaca US market data between two stable normalized paper-account reads. Quote sizes are reported data, not executable liquidity, venue entitlement or guaranteed fills. Receipt uses the local sampled clock; exchange nanoseconds do not prove synchronization, decision/order arrival or broker latency. Separate GETs are not atomic. Source/fees/funds/order/benchmark acceptance remains unverified. No order submission, retry or automatic retention. Keep account-bound observations private.')
            session.bind_result_projection(result)
        # Revalidate after the durable pacing result as well: SQLite waiting
        # must not turn a once-fresh observation into a fresh-looking return.
        _quote(raw,session.previous[0])
        scope['read_timing']['finalized_at']=session.previous[0]
        result['observation_sha256']=sha(scope)
        return result
    except reader.PaperInspectionError:raise
    except Exception:
        _fail('RESPONSE_INVALID')


CHECKS=('three_fixed_gets_checked','exchange_nanoseconds_preserved','same_account_checked',
    'shared_cooldown_preserved','quote_fields_allowlisted','pacing_result_retained')


def fixture_report():
    """Disposable real reader/pacing with injected HTTPS; no provider or vault."""
    import io,json,tempfile
    from pathlib import Path
    account='01234567-89ab-cdef-0123-456789abcdef';epoch=1792195200
    raw_account=dict(id=account,currency='USD',status='ACTIVE',crypto_status='ACTIVE',account_blocked=False,
        trading_blocked=False,trade_suspended_by_user=False,cash='1000',non_marginable_buying_power='900',
        pending_transfer_in='0',pending_transfer_out='0',accrued_fees='0')
    raw_quote={'quotes':{'BTC/USD':dict(bp='100',ap='101',bs='2',**{'as':'3'},t='2026-10-17T00:00:00.123456789Z')}}
    class Response:
        status=200;headers={'Content-Type':'application/json'}
        def __init__(self,url,value):self.url=url;self.body=io.BytesIO(json.dumps(value).encode())
        def geturl(self):return self.url
        def read(self,size):return self.body.read(size)
        def __enter__(self):return self
        def __exit__(self,*args):self.body.close()
    class Opener:
        def __init__(self):self.values=iter([raw_account,raw_quote,raw_account]);self.requests=[]
        def open(self,req,timeout):self.requests.append(req);return Response(req.full_url,next(self.values))
    with tempfile.TemporaryDirectory(prefix='nexus-quote-observation-') as directory:
        gate=PaperReadPacing(Path(directory)/'pacing.sqlite3',account);opener=Opener()
        result=inspect_quote('fixture_key_only_123','fixture_secret_only_123',expected_account_id=account,
            confirm_read_only_paper=True,pacing=gate,opener=opener,wall_clock=lambda:epoch+1,elapsed_clock=lambda:50)
        before=gate.path.read_bytes();blocked=Opener();cooldown=False
        try:
            reader.inspect_account('fixture_key_only_123','fixture_secret_only_123',expected_account_id=account,
                confirm_read_only_paper=True,pacing=gate,opener=blocked,wall_clock=lambda:epoch+1,elapsed_clock=lambda:50)
        except ValueError:cooldown=not blocked.requests and gate.path.read_bytes()==before
        checks=dict(three_fixed_gets_checked=[r.full_url for r in opener.requests]==[reader.URLS[0],reader.BTC_QUOTE_URL,reader.URLS[0]] and all(r.method=='GET' and r.data is None for r in opener.requests),
            exchange_nanoseconds_preserved=result['quote']['exchange_time_ns']=='1792195200123456789',
            same_account_checked=result['sequential_account_fields_stable'],shared_cooldown_preserved=cooldown,
            quote_fields_allowlisted=set(result['quote'])=={'product','location','bid','ask','bid_size','ask_size','exchange_at','exchange_time_ns','sampled_age_ns','checks'},
            pacing_result_retained=gate.inspect()['last_outcome']=='SUCCESS' and gate.inspect()['sessions']==1)
        return dict(source='INJECTED_ALPACA_BTC_QUOTE_OBSERVATION_FIXTURE',passed=all(checks.values()),**checks,
            actual_provider_access=False,actual_vault_access=False,**{k:False for k in FLAGS})
