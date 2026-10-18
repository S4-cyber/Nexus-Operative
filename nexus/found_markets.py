"""Bounded public discovery and deterministic council research, never order admission.

No credentials, account IO or persistent market history. At most four markets/two
history requests each per 15-minute burst plus one catalog. Six fresh prospects,
256 rolling summaries, 36 requests/hour across cooperating local processes.
Stop fences publication.
"""
import copy
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request,HTTPRedirectHandler,build_opener
from nexus.found_market_budget import SharedRequestBudget
from nexus.market_universe import parse as parse_catalog,PATTERN,URL,MAX_RESPONSE_BYTES

INTERVAL=900
BATCH=4
SELECTED=6
RETAINED=256
HOURLY_REQUESTS=36
TTL=3600
HORIZONS=(('short',3600,24),('medium',86400,30),('long',86400,180))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('DISCOVERY_REDIRECT')


def public_get(url):
    # URLs are constructed internally; never accept arbitrary hosts or user URLs.
    if url!=URL and not re.fullmatch(r'https://api\.exchange\.coinbase\.com/products/[A-Z0-9]{2,18}-USD/candles\?granularity=(3600|86400)&start=[^&]{1,80}&end=[^&]{1,80}',url):
        raise ValueError('DISCOVERY_PATH')
    maximum=MAX_RESPONSE_BYTES if url==URL else 131072
    request=Request(url,headers={'Accept':'application/json','User-Agent':'Nexus-Public-Discovery/1'})
    with build_opener(_NoRedirect()).open(request,timeout=6) as response:
        if response.status!=200 or response.geturl()!=url:raise ValueError('DISCOVERY_RESPONSE')
        raw=response.read(maximum+1)
        if len(raw)>maximum:raise ValueError('DISCOVERY_RESPONSE_SIZE')
        return json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('DISCOVERY_NUMBER')))


def _number(raw,zero=False):
    if type(raw) not in (int,float,str) or (type(raw) is str and len(raw)>64):raise ValueError('DISCOVERY_NUMBER')
    value=float(raw)
    if not math.isfinite(value) or value<0 or (not zero and value==0) or (value and value<1e-12) or value>1e18:raise ValueError('DISCOVERY_NUMBER')
    return value


def bars(rows,g,now):
    if type(rows) is not list or not 1<=len(rows)<=300:raise ValueError('DISCOVERY_HISTORY')
    result={};seen=set()
    for row in rows:
        if type(row) is not list or len(row)!=6:raise ValueError('DISCOVERY_HISTORY')
        t=row[0]
        if type(t) is not int or t<0 or t%g or t in seen:raise ValueError('DISCOVERY_HISTORY')
        seen.add(t)
        low,high,opening,close,volume=[_number(v,i==4) for i,v in enumerate(row[1:])]
        if not low<=opening<=high or not low<=close<=high:raise ValueError('DISCOVERY_HISTORY')
        if t+g<=now:result[t]=(t,close,volume)
    return [result[t] for t in sorted(result)]


def council_screen(hourly,daily,now):
    """Independent rule opinions per horizon; scores are historical, not forecasts."""
    result=[]
    for name,g,periods in HORIZONS:
        data=hourly if g==3600 else daily
        reasons=[];metrics={};score=None
        if len(data)<periods+1:reasons.append('INSUFFICIENT_HISTORY')
        else:
            window=data[-periods-1:]
            if not 0<=now-(window[-1][0]+g)<=g:reasons.append('STALE_HISTORY')
            if any(b[0]-a[0]!=g for a,b in zip(window,window[1:])):reasons.append('HISTORY_GAPS')
            prices=[row[1] for row in window];gain=prices[-1]/prices[0]-1
            peak=prices[0];drawdown=0
            for price in prices:peak=max(peak,price);drawdown=max(drawdown,1-price/peak)
            changes=[b/a-1 for a,b in zip(prices,prices[1:])]
            volatility=(sum(x*x for x in changes)/len(changes))**.5
            # Daily close*base volume is a coarse capacity proxy, never depth/spread.
            volume=daily[-1][1]*daily[-1][2] if daily else 0
            if not daily or not 0<=now-(daily[-1][0]+86400)<=86400:reasons.append('STALE_VOLUME_PROXY')
            if not math.isfinite(volume) or volume<1_000_000:reasons.append('LOW_OR_UNKNOWN_VOLUME')
            if gain<=0:reasons.append('NONPOSITIVE_TREND')
            if gain>3 or volatility>.15 or drawdown>.25:reasons.append('DOWNSIDE_OR_OUTLIER_RISK')
            metrics=dict(historical_return_pct=round(gain*100,3),maximum_drawdown_pct=round(drawdown*100,3),
                approximate_daily_turnover_usd=round(volume,2),bars=len(window))
            score=round(min(100,max(0,50+20*gain/(.02+volatility*math.sqrt(periods))-100*drawdown)),3)
        result.append(dict(horizon=name,lookback_periods=periods,interval_seconds=g,
            eligible_for_research=not reasons,score=score,reasons=reasons,metrics=metrics,
            specialists=['trend','data_quality','liquidity_proxy','downside_adversary']))
    return result


def _shared_budget_admit(admission):
    root=Path(os.environ.get('NEXUS_PORTABLE_DATA',str(Path.home()/'.nexus-operative')))
    return SharedRequestBudget(root/'found_market_request_budget.sqlite3').admit(admission)


class FoundMarkets:
    def __init__(self,transport=None,clock=None,wall=None,budget=None):
        public_transport=transport is None
        self.transport=transport or public_get;self.clock=clock or time.monotonic;self.wall=wall or time.time
        self._budget=_shared_budget_admit if public_transport and budget is None else budget
        self._lock=threading.RLock();self._cycle=threading.Lock();self._stop=threading.Event();self._stop.set()
        self._thread=None;self._admission=lambda:False;self._generation=0;self._rows={};self._requests=[]
        self._next=0;self._cursor=0;self._phase='STOPPED';self._reason=None;self._catalog_count=0
        self._bursts=0;self._attempts=0;self._anchor=None;self._selection_ready=False

    def _times(self):
        with self._lock:
            mono=self.clock();wall=self.wall()
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in (mono,wall)) or wall<20_000_000:raise ValueError('CLOCK')
            if self._anchor:
                w,m=self._anchor
                if mono<m or abs(wall-w-(mono-m))>2:raise ValueError('CLOCK')
            self._anchor=(wall,mono)
            return mono,wall

    def status(self):
        with self._lock:
            try:
                mono,wall=self._times()
                fresh=[copy.deepcopy(r) for r in self._rows.values() if 0<=mono-r['observed_mono']<TTL]
            except ValueError:
                fresh=[];self._selection_ready=False;self._reason='SOURCE_OR_BUDGET_REVIEW_REQUIRED'
            for row in fresh:row.pop('observed_mono',None)
            prospects=[r for r in fresh if any(h['eligible_for_research'] for h in r['horizons'])]
            prospects.sort(key=lambda r:(-max(h['score'] for h in r['horizons'] if h['eligible_for_research']),r['product']))
            current=self._selection_ready and not self._stop.is_set() and self._reason is None
            chosen=prospects[:SELECTED] if current else []
            return dict(mode='BOUNDED_FOUND_MARKETS_RESEARCH',read_only=True,enabled=not self._stop.is_set(),phase=self._phase,
                reason=self._reason,selection_current=current,worker_alive=bool(self._thread and self._thread.is_alive()),cycle_active=self._cycle.locked(),
                interval_seconds=INTERVAL,batch_limit=BATCH,selection_limit=SELECTED,summary_limit=RETAINED,
                hourly_request_limit=HOURLY_REQUESTS,requests_attempted=self._attempts,bursts=self._bursts,
                request_budget_scope='SHARED_COOPERATING_LOCAL_DATA_ROOT' if self._budget else 'INJECTED_PROCESS_ONLY',
                provider_or_ip_wide_budget_verified=False,
                catalog_candidates=self._catalog_count,summaries_retained=len(self._rows),fresh_summaries=len(fresh),
                selected=chosen,reviewed=sorted(fresh,key=lambda r:r['product'])[:RETAINED],
                source='coinbase_exchange_public',source_scope='USD spot crypto only; other asset classes need connectors',
                council_implementation='DETERMINISTIC_DISCOVERY_SPECIALISTS_NOT_TRAINED_FORECAST',
                persistence='SUMMARIES_IN_BOUNDED_PROCESS_MEMORY; REQUEST_ADMISSIONS_DURABLE_WHEN_PUBLIC',
                source_transport='INJECTED_FIXTURE' if self.transport is not public_get else 'PUBLIC_HTTP',
                paper_orders_enabled=False,live_orders_enabled=False,money_movement_enabled=False,
                policy_changed=False,profitability_validated=False,
                note='Automatic selection allocates research attention only. Historical momentum is not expected profit or a calibrated prediction. Single-source bars, coarse volume and missing spreads/costs/holdout/eligibility remain unverified. Current fixed BTC execution does not use this list. No exhaustive catalog coverage claim; stale summaries leave the shortlist.')

    def start(self,admission):
        with self._lock:
            if not admission():raise ValueError('PORTAL_REQUIRED')
            if not self._stop.is_set():return self.status()
            if self._cycle.locked() or (self._thread and self._thread.is_alive()):raise ValueError('DISCOVERY_FINISHING')
            self._admission=admission;self._generation+=1;self._phase='WAITING';self._reason=None;self._selection_ready=False;self._stop.clear()
            self._thread=threading.Thread(target=self._run,name='nexus-found-markets',daemon=True)
            try:self._thread.start()
            except RuntimeError:self._stop.set();self._phase='STOPPED';self._selection_ready=False;raise
            return self.status()

    def stop(self,reason='USER_STOP'):
        with self._lock:
            self._stop.set();self._generation+=1;self._phase='STOPPED';self._reason=reason;self._selection_ready=False
        return self.status()

    def shutdown(self):
        self.stop('APPLICATION_SHUTDOWN')
        if self._thread and self._thread is not threading.current_thread():self._thread.join(timeout=8)

    def _get(self,url,generation):
        with self._lock:
            if self._stop.is_set() or generation!=self._generation or not self._admission():raise ValueError('STOPPED')
            now,_=self._times();self._requests=[t for t in self._requests if 0<=now-t<3600]
            if len(self._requests)>=HOURLY_REQUESTS:raise ValueError('REQUEST_BUDGET')
            if self._budget:
                admit=self._budget.admit if hasattr(self._budget,'admit') else self._budget
                admit(lambda:self._times()[1])
            self._requests.append(now);self._attempts+=1
        return self.transport(url)

    def step(self):
        if not self._cycle.acquire(False):return self.status()
        try:
            with self._lock:
                now,wall=self._times()
                if self._stop.is_set() or not self._admission() or now<self._next:return self.status()
                self._next=now+INTERVAL;generation=self._generation;self._phase='SCANNING';self._reason=None
            catalog=parse_catalog(self._get(URL,generation))['products']
            symbols=[row['product'] for row in catalog];chosen=[]
            with self._lock:
                if self._stop.is_set() or generation!=self._generation or not self._admission():raise ValueError('STOPPED')
                self._catalog_count=len(symbols)
                if not symbols:raise ValueError('EMPTY_CATALOG')
                # Revisit expired prior leaders before exploring the rotating catalog.
                old=sorted((r for r in self._rows.values() if any(h['eligible_for_research'] for h in r['horizons'])),
                    key=lambda r:-max(h['score'] for h in r['horizons'] if h['eligible_for_research']))
                chosen=[r['product'] for r in old if r['product'] in symbols and now-r['observed_mono']>=TTL][:2]
                for _ in range(len(symbols)):
                    product=symbols[self._cursor%len(symbols)];self._cursor+=1
                    if product not in chosen and (product not in self._rows or now-self._rows[product]['observed_mono']>=TTL):chosen.append(product)
                    if len(chosen)>=BATCH:break
            staged=[]
            for product in chosen:
                histories={}
                for g in (3600,86400):
                    if self._stop.wait(1):raise ValueError('STOPPED')
                    _,sample_wall=self._times();end=int(sample_wall//g)*g
                    stamp=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
                    url=URL+'/'+product+'/candles?'+urlencode(dict(granularity=g,start=stamp(end-240*g),end=stamp(end)))
                    histories[g]=bars(self._get(url,generation),g,self.wall())
                mono,sample_wall=self._times()
                staged.append(dict(product=product,observed_at=sample_wall,observed_mono=mono,
                    horizons=council_screen(histories[3600],histories[86400],sample_wall),execution_allowed=False))
            with self._lock:
                if self._stop.is_set() or generation!=self._generation or not self._admission():raise ValueError('STOPPED')
                for row in staged:self._rows[row['product']]=row
                while len(self._rows)>RETAINED:del self._rows[min(self._rows,key=lambda p:self._rows[p]['observed_mono'])]
                self._bursts+=1;self._phase='WAITING';self._selection_ready=True
        except (ValueError,OSError,TypeError,KeyError,OverflowError):
            with self._lock:
                self._selection_ready=False
                if not self._stop.is_set():self._phase='WAITING';self._reason='SOURCE_OR_BUDGET_REVIEW_REQUIRED'
        finally:self._cycle.release()
        return self.status()

    def _run(self):
        try:
            while not self._stop.is_set():
                self.step()
                if self._stop.wait(1):break
        except Exception:
            with self._lock:self._stop.set();self._phase='STOPPED';self._reason='WORKER_REVIEW_REQUIRED';self._selection_ready=False


worker=FoundMarkets()
