"""Observed local-paper valuation history. Never routes orders or rewrites accounts."""
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from nexus.public_data import PRODUCTS, read_ticker
from nexus.paper_portfolio import check_quote, FEE

LOCK=threading.Lock()
MAX_SAMPLES=100000
MAX_RETAINED_SAMPLES=6000000
MIN_INTERVAL=5
RANGES={'1m':60,'1h':3600,'1d':86400,'1mo':30*86400,'1y':365*86400,'ytd':None,'all':None}


def root():return Path(os.environ.get('NEXUS_PORTABLE_DATA',str(Path.home()/'.nexus-operative')))


def decimal(value,positive=False):
    if isinstance(value,bool):raise ValueError('MATRIX_INVALID_ACCOUNT_NUMBER')
    try:d=Decimal(str(value))
    except Exception:raise ValueError('MATRIX_INVALID_ACCOUNT_NUMBER')
    if not d.is_finite() or d<0 or (positive and not d):raise ValueError('MATRIX_INVALID_ACCOUNT_NUMBER')
    return d


def account():
    path=root()/'paper_portfolio.sqlite3'
    if not path.exists():return None
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as db:
        db.execute('BEGIN')
        meta=dict(db.execute('SELECT key,value FROM meta'))
        if 'initial' not in meta:return None
        positions=[dict(product=p,quantity=q,entry=e) for p,q,e in db.execute('SELECT product,quantity,entry FROM positions ORDER BY product')]
        revision=db.execute('SELECT COALESCE(MAX(id),0) FROM ledger').fetchone()[0]
        adjustments=[dict(id=i,at=at,action=a,product=p,cash_delta=c,quantity=q,entry=e)
            for i,at,a,p,c,q,e in db.execute('''SELECT l.id,l.at,l.action,l.product,l.cash_delta,p.quantity,p.entry
            FROM (SELECT * FROM ledger ORDER BY id DESC LIMIT 50) l
            LEFT JOIN position_events p ON p.ledger_id=l.id ORDER BY l.id DESC''')]
    state=dict(initial=meta['initial'],cash=meta['cash'],positions=positions,revision=revision)
    decimal(state['initial'],True);decimal(state['cash'])
    for pos in positions:decimal(pos['quantity'],True);decimal(pos['entry'],True)
    state['fingerprint']=hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()
    state['adjustments']=adjustments
    return state


def observation(state,quotes,now):
    """One actual snapshot; incomplete marks create a gap rather than a cash-only total."""
    positions=[];missing=[]
    with localcontext() as ctx:
        ctx.prec=80
        equity=decimal(state['cash']);initial=decimal(state['initial'],True)
        for pos in state['positions']:
            product=pos['product'];qty=decimal(pos['quantity'],True);entry=decimal(pos['entry'],True)
            try:
                if product not in PRODUCTS:raise ValueError('Unsupported position')
                quote=quotes[product];bid,_=check_quote(product,quote)
                # Both exchange and receipt clocks must be fresh at valuation, not just at fetch.
                stamp=datetime.fromisoformat(quote['exchange_time'].replace('Z','+00:00'))
                if stamp.tzinfo is None or not -1<=now-stamp.timestamp()<=10:raise ValueError('Stale exchange time')
                value=qty*bid*(1-FEE);pnl=value-qty*entry;equity+=value
                positions.append(dict(product=product,quantity=str(qty),entry=str(entry),bid=str(bid),
                    liquidation_value=str(value),unrealized_pnl=str(pnl),exchange_time=quote['exchange_time']))
            except (KeyError,ValueError,TypeError,AttributeError,OverflowError):missing.append(product)
        return dict(at=now,account_revision=state['revision'],cash=state['cash'],initial=state['initial'],
            equity=None if missing else str(equity),pnl=None if missing else str(equity-initial),
            positions=positions,missing_products=sorted(missing),complete=not missing,
            source='LOCAL_PAPER_ACCOUNT_PUBLIC_BID_ESTIMATE',accounting_verified=False)


def _open():
    root().mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(root()/'matrix_observations.sqlite3',timeout=2)
    db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,at REAL NOT NULL,payload TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS observations_at ON observations(at)')
    db.execute('CREATE TABLE IF NOT EXISTS archived_observations(id INTEGER PRIMARY KEY,at REAL NOT NULL,payload TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS archived_observations_at ON archived_observations(at)')
    return db



def retain_capacity(db,now):
    """Inside the capture transaction: move closed UTC days, retaining every row/ID."""
    active=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
    archived=db.execute('SELECT COUNT(*) FROM archived_observations').fetchone()[0]
    if active+archived>=MAX_RETAINED_SAMPLES:return 'RETAINED_OBSERVATION_CAP_REACHED'
    if active<MAX_SAMPLES:return None
    cutoff=math.floor(now/86400)*86400
    # Preserve the highest ID even after a long offline interval: SQLite's next ID
    # must never reuse an identity that is now in retained history.
    eligible='at<? AND id<(SELECT MAX(id) FROM observations)'
    count=db.execute('SELECT COUNT(*) FROM observations WHERE '+eligible,(cutoff,)).fetchone()[0]
    if not count:return 'OBSERVATION_CAP_REACHED'
    db.execute('INSERT INTO archived_observations SELECT * FROM observations WHERE '+eligible,(cutoff,))
    db.execute('DELETE FROM observations WHERE '+eligible,(cutoff,))
    return None


def capture(reader=read_ticker):
    # Nonblocking local guard plus SQLite lease: overlapping viewers never queue quote work.
    if not LOCK.acquire(False):return dict(recorded=False,reason='OBSERVATION_IN_PROGRESS')
    try:
        before=account()
        if before is None:return dict(recorded=False,reason='PAPER_ACCOUNT_UNINITIALIZED')
        with _open() as db:
            db.execute('BEGIN IMMEDIATE')
            last=db.execute('SELECT id,at FROM observations ORDER BY id DESC LIMIT 1').fetchone()
            retained_head=db.execute('SELECT MAX(id) FROM archived_observations').fetchone()[0]
            if retained_head is not None and (last is None or retained_head>=last[0]):
                raise ValueError('MATRIX_RETAINED_IDENTITY_HEAD_DISAGREEMENT')
            now=time.time()
            if last and now-last[1]<MIN_INTERVAL:return dict(recorded=False,reason='OBSERVATION_INTERVAL_OR_CLOCK')
            capacity=retain_capacity(db,now)
            if capacity:return dict(recorded=False,reason=capacity)
            quotes={}
            for pos in before['positions']:
                if pos['product'] not in PRODUCTS:continue
                try:quotes[pos['product']]=reader(pos['product'])
                except (ValueError,OSError,TimeoutError):pass
            after=account()
            if after is None or after['fingerprint']!=before['fingerprint']:
                return dict(recorded=False,reason='ACCOUNT_CHANGED_DURING_OBSERVATION')
            observed_at=time.time()
            if observed_at<now:return dict(recorded=False,reason='OBSERVATION_CLOCK_REGRESSION')
            point=observation(after,quotes,observed_at)
            db.execute('INSERT INTO observations(at,payload) VALUES(?,?)',(point['at'],json.dumps(point,sort_keys=True)))
        return dict(recorded=True,point=point)
    finally:LOCK.release()


def history(span='1d',now=None):
    if span not in RANGES:raise ValueError('MATRIX_UNSUPPORTED_RANGE')
    now=time.time() if now is None else now
    start=0 if span=='all' else datetime(datetime.fromtimestamp(now,timezone.utc).year,1,1,tzinfo=timezone.utc).timestamp() if span=='ytd' else now-RANGES[span]
    path=root()/'matrix_observations.sqlite3';points=[];count=0;first=None;active=0;archived=0
    if path.exists():
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as db:
            db.execute('BEGIN')
            tables=['observations']
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='archived_observations'").fetchone():tables.append('archived_observations')
            active=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
            if len(tables)>1:archived=db.execute('SELECT COUNT(*) FROM archived_observations').fetchone()[0]
            first_values=[db.execute('SELECT MIN(at) FROM '+table).fetchone()[0] for table in tables]
            first=min((v for v in first_values if v is not None),default=None)
            counts=[db.execute('SELECT COUNT(*) FROM '+table+' WHERE at>=? AND at<=?',(start,now)).fetchone()[0] for table in tables]
            count=sum(counts);step=max(1,math.ceil(count/1198))
            # Rank only IDs (not large JSON payloads), then load the bounded selected
            # original rows. Legacy pre-archive databases remain GET-only compatible.
            combined=' UNION ALL '.join('SELECT id,'+str(i)+' AS source FROM '+table+' WHERE at>=? AND at<=?' for i,table in enumerate(tables))
            selected='WITH combined AS ('+combined+'), ranked AS (SELECT id,source,ROW_NUMBER() OVER(ORDER BY id) n FROM combined), selected AS (SELECT id,source FROM ranked WHERE (n-1)%?=0 OR n=?) '
            payloads=' UNION ALL '.join('SELECT o.id,o.payload FROM selected s JOIN '+table+' o ON o.id=s.id WHERE s.source='+str(i) for i,table in enumerate(tables))
            params=[v for _ in tables for v in (start,now)]+[step,count]
            points=[json.loads(row[1]) for row in db.execute(selected+payloads+' ORDER BY 1',params)]
    state=account()
    return dict(mode='OBSERVED_LOCAL_PAPER_HISTORY',range=span,range_start=start,range_end=now,timezone='UTC',
        points=points,observations_in_range=count,retained_since=first,downsampled=count>len(points),
        retention=dict(active_samples=active,archived_samples=archived,total_samples=active+archived,active_limit=MAX_SAMPLES,retained_limit=MAX_RETAINED_SAMPLES,archive_scope='SAME_DATABASE_LOGICAL_RETENTION_NOT_PHYSICAL_OFFLOAD'),
        adjustments=state['adjustments'] if state else [],account_initialized=state is not None,
        current_revision=state['revision'] if state else None,broker_connected=False,live_orders_enabled=False,
        note='Observed samples only; no historical backfill. Equity estimates liquidation at public bid less modeled 10 bps exit fee. Fractal shows holding unrealized P/L, not lifetime realized return. Gaps mean incomplete prices. Up to 100000 active and 6000000 total samples; closed-day rows move transactionally into retained history without deleting payloads. This is logical retention in the same database, not physical offload or unlimited storage. Account integrity is not independently verified.')
