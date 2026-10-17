"""Explicitly started BTC-only experimental paper forward runner.

Public quotes are observations; fills assume available quantity, slippage and
fees, not actual matching/depth. No broker IO, validated edge or live authority.
Restarts always require an explicit new start. The read-only observer is separate.
"""
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from collections import deque
from contextlib import closing
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from nexus.durable_orders import gateway
from nexus.feed_guard import FeedGuard
from nexus.public_data import read_ticker
from nexus.tier_permissions import require_entry, TIERS
from nexus.state import load_settings
from nexus.paper_policy import admit, POLICY
from nexus import forward_evidence
from nexus.forward_ownership import ForwardOwnership

INTERVAL=POLICY['interval_seconds']
RANKING_INTERVAL=POLICY['ranking_seconds']
PRODUCT=POLICY['product']
CLOCK_DISCONTINUITY_TOLERANCE_SECONDS=2
SESSION_LIMIT=1000


class PaperForwardRunner:
    def __init__(self,orders=gateway,fetcher=read_ticker,clock=time.time,monotonic_clock=None):
        self.orders=orders;self.fetcher=fetcher;self.clock=clock
        self.monotonic_clock=monotonic_clock or (time.monotonic if clock is time.time else clock)
        self._clock_anchor=None
        self._stop=threading.Event();self._stop.set()
        self._thread=None;self._lock=threading.RLock();self._cycle_lock=threading.Lock()
        self._execution_lock=threading.RLock();self._control_lock=threading.Lock()
        self._session=None;self._tier=None;self._prices=deque(maxlen=POLICY['warmup_observations'])
        self._guard=FeedGuard();self._stamp=None;self._last_rank=0
        self._failures=0;self._cycles=0;self._last=None;self._error=None
        self._policy=admit(POLICY["id"])
        self._observation_hash=None
        self._ownership=None

    def _db(self):
        root=Path(os.environ.get('NEXUS_PORTABLE_DATA',str(Path.home()/'.nexus-operative')))
        root.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(root/'paper_forward.sqlite3',timeout=10)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS cycles(id INTEGER PRIMARY KEY,at REAL NOT NULL,detail TEXT NOT NULL)')
        db.execute("""CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY,started_at REAL NOT NULL,updated_at REAL NOT NULL,
            state TEXT NOT NULL,policy_json TEXT NOT NULL,counts_json TEXT NOT NULL,last_json TEXT NOT NULL)""")
        forward_evidence.schema(db)
        return db

    def _record(self,decision,reason,**extra):
        observed=self.clock()
        valid=type(observed) in (int,float) and math.isfinite(observed)
        recorded=observed if valid else (self._clock_anchor[0] if self._clock_anchor else time.time())
        row=dict(at=recorded,clock_sample_valid=valid,session=self._session,cycle=self._cycles,quote_evidence_sha256=self._observation_hash,decision=decision,reason=reason,
                 product=PRODUCT,tier=self._tier,simulation_only=True,**extra)
        row['policy_id']=self._policy['id'];row['policy_sha256']=self._policy['policy_sha256']
        with closing(self._db()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT started_at,updated_at,state,counts_json FROM sessions WHERE id=?',(self._session,)).fetchone()
            if old is None:
                if decision!='START':raise ValueError('FORWARD_SESSION_MISSING')
                if db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]>=SESSION_LIMIT:raise ValueError('FORWARD_SESSION_HISTORY_CAP')
                started=row['at'];state='RUNNING';counts={}
            else:
                if decision=='START':raise ValueError('FORWARD_SESSION_ID_REUSED')
                started,updated,state,raw=old;counts=json.loads(raw)
                row['at']=max(row['at'],updated)
            counts[decision]=counts.get(decision,0)+1
            if decision=='PAUSED':state='PAUSED'
            elif decision=='STOP':state='STOPPED'
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?)',
                       (self._session,started,row['at'],state,json.dumps(self._policy,sort_keys=True),json.dumps(counts,sort_keys=True),json.dumps(row,allow_nan=False)))
            db.execute('INSERT INTO cycles(at,detail) VALUES (?,?)',(row['at'],json.dumps(row,allow_nan=False)))
            db.execute('DELETE FROM cycles WHERE id NOT IN (SELECT id FROM cycles ORDER BY id DESC LIMIT 1000)')
        with self._lock:self._last=row
        return row

    def _record_quote(self,quote,eligible,error=None):
        with closing(self._db()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            self._observation_hash=forward_evidence.append(db,self._session,self._cycles,self.clock(),quote,eligible,
                'PUBLIC_READER_SINGLE_SOURCE_UNATTESTED' if self.fetcher is read_ticker else 'INJECTED_TRANSPORT_UNATTESTED',
                self._policy['policy_sha256'],error)

    def prepare(self,tier,policy_id=POLICY['id']):
        """Validate/start session state without starting a thread (also testable offline)."""
        with self._cycle_lock,self._lock:
            if not self._stop.is_set() or (self._thread and self._thread.is_alive()):raise ValueError('PAPER_FORWARD_ALREADY_ACTIVE')
            if self._ownership and self._ownership.held:
                raise ValueError('PAPER_FORWARD_PREVIOUS_CYCLE_STILL_ACTIVE')
            self._ownership=ForwardOwnership(self.orders._path().parent)
            self._ownership.acquire()
            try:
                self._clock_anchor=None
                reason=self._clock_check()
                if reason:raise ValueError(reason)
                selected_policy=admit(policy_id)
                if tier not in TIERS:raise ValueError('UNKNOWN_RISK_TIER')
                storage=self.storage_capacity()
                if not storage['session_intake_allowed']:raise ValueError('FORWARD_SESSION_HISTORY_CAP')
                if not storage['quote_intake_allowed']:raise ValueError('FORWARD_QUOTE_EVIDENCE_CAP')
                report=self.orders.recovery_report()
                existing_positions=(report['execution_account']['account'] or {}).get('positions',{})
                if not existing_positions:require_entry(tier,1)
                if report['halted']:raise ValueError('EMERGENCY_HALT_ACTIVE')
                if not report['execution_account']['initialized']:
                    self.orders.initialize_account(load_settings()['paper_budget'])
                    report=self.orders.recovery_report()
                if not report['portfolio_reconciled']:
                    self.orders.link_portfolio()
                    report=self.orders.recovery_report()
                if any(p!=PRODUCT for p in report['execution_account']['account']['positions']):
                    raise ValueError('FORWARD_RUNNER_REQUIRES_BTC_ONLY_ACCOUNT')
                if any(o['status'] in ('SIMULATED_ACK_ONLY','SIMULATED_PARTIALLY_FILLED') for o in report['orders']):
                    raise ValueError('REVIEW_OUTSTANDING_PAPER_ORDERS_FIRST')
                holding=bool(report['execution_account']['account']['positions'])
                if not report['capacity']['lifecycle_intake_allowed'] or report['capacity']['ordinary_commands_remaining']<(4 if holding else 12) or report['capacity']['intents_remaining']<(1 if holding else 2):
                    raise ValueError('INSUFFICIENT_FORWARD_JOURNAL_CAPACITY')
                self._policy=selected_policy
                self._session=uuid.uuid4().hex[:16];self._tier=tier
                self._prices.clear();self._guard=FeedGuard();self._stamp=None;self._last_rank=0
                self._cycles=0;self._failures=0;self._error=None;self._observation_hash=None;self._stop.clear()
                try:self._record('START','EXPERIMENTAL_MOMENTUM_PAPER_POLICY')
                except (OSError,sqlite3.Error,ValueError):
                    self._stop.set();self._error='START_DIAGNOSTIC_STORAGE_FAILURE';raise
                return self.status()
            except BaseException:
                self._stop.set();self._ownership.release();raise

    def _release_stopped_ownership(self):
        if self._stop.is_set() and self._ownership:
            self._ownership.release()

    def start(self,tier,policy_id=POLICY['id']):
        with self._control_lock:
            if self._thread and self._thread.is_alive():raise ValueError('PAPER_FORWARD_ALREADY_ACTIVE')
            self.prepare(tier,policy_id)
            self._thread=threading.Thread(target=self._run,daemon=True,name='nexus-paper-forward')
            try:self._thread.start()
            except RuntimeError:
                self._stop.set();self._release_stopped_ownership();self._error='WORKER_START_FAILED';raise
            return self.status()

    def stop(self,reason='MANUAL_STOP'):
        with self._control_lock:
            was_enabled=not self._stop.is_set()
            self._stop.set()
            # Fence the full local order sequence before acknowledging stop.
            with self._execution_lock:pass
            worker=self._thread
            if worker and worker.is_alive() and worker is not threading.current_thread():worker.join(timeout=6)
            if was_enabled and self._session:
                try:self._record('STOP',reason)
                except (OSError,sqlite3.Error,ValueError):self._error='STOP_DIAGNOSTIC_STORAGE_FAILURE'
            if self._cycle_lock.acquire(blocking=False):
                try:self._release_stopped_ownership()
                finally:self._cycle_lock.release()
            return self.status()

    def _run(self):
        while not self._stop.is_set():
            try:self.single_cycle()
            except Exception as exc:
                # Unknown worker failures pause rather than creating an unmonitored loop.
                self._error=type(exc).__name__;self._stop.set()
                try:self._record('PAUSED','UNHANDLED_WORKER_FAILURE',error_type=type(exc).__name__)
                except (OSError,sqlite3.Error,ValueError):pass
            self._stop.wait(INTERVAL)
        self._release_stopped_ownership()

    def _execute(self,*args):
        with self._execution_lock:return self._execute_locked(*args)

    def _execute_locked(self,side,quantity,bid,ask,received,now,notional):
        clock_reason=self._clock_check()
        if clock_reason:return self._pause_clock(clock_reason)
        if self._stop.is_set():return self._record('HOLD','RUNNER_STOPPED_BEFORE_SUBMIT')
        oid='pf_'+self._session+'_'+str(self._cycles)+'_'+side
        self.orders.submit(oid,PRODUCT,side,str(notional),tier=self._tier)
        try:
            if self._stop.is_set():return self._record('HOLD','RUNNER_STOPPED_AFTER_SUBMIT')
            self.orders.set_execution_terms(oid,now,self._policy['order_lifetime_seconds'])
            # Quote may precede order creation by transport latency; use a fresh
            # observation boundary so the timestamp model retains source age.
            if self._stop.is_set():return self._record('HOLD','RUNNER_STOPPED_BEFORE_FILL')
            clock_reason=self._clock_check()
            if clock_reason:return self._pause_clock(clock_reason)
            result=self.orders.simulate_quote_fill(oid,'fill',1,str(quantity),str(bid),str(ask),str(quantity),received,self.clock(),fee_bps=self._policy['fee_bps'],slippage_bps=self._policy['slippage_bps'])
            if result.get('expired'):return self._record('VETO','ORDER_EXPIRED_WITHOUT_FILL',order_id=oid)
            return self._record(side,'MODELED_PUBLIC_QUOTE_FILL',order_id=oid,
                                liquidity='ASSUMED_QUANTITY_NO_DEPTH',strategy_validated=False)
        finally:
            self.orders.cancel(oid)

    def _clock_check(self):
        wall=self.clock();steady=self.monotonic_clock()
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (wall,steady)):
            return 'INVALID_RUNTIME_CLOCK'
        if self._clock_anchor:
            old_wall,old_steady=self._clock_anchor
            elapsed=steady-old_steady
            if elapsed<0 or wall<old_wall or abs((wall-old_wall)-elapsed)>CLOCK_DISCONTINUITY_TOLERANCE_SECONDS:
                return 'RUNTIME_CLOCK_DISCONTINUITY'
        self._clock_anchor=(wall,steady)
        return None

    def _pause_clock(self,reason):
        self._prices.clear();self._last_rank=0;self._error=reason;self._stop.set()
        return self._record('PAUSED',reason)

    def single_cycle(self):
        if not self._cycle_lock.acquire(blocking=False):return dict(decision='HOLD',reason='CYCLE_BUSY')
        try:
            if self._stop.is_set():return dict(decision='HOLD',reason='PAPER_FORWARD_STOPPED')
            self._cycles+=1
            self._observation_hash=None
            clock_reason=self._clock_check()
            if clock_reason:return self._pause_clock(clock_reason)
            storage=self.storage_capacity()
            if not storage['quote_intake_allowed']:
                self._stop.set();return self._record('PAUSED','FORWARD_QUOTE_EVIDENCE_CAP')
            report=self.orders.recovery_report()
            if report['halted'] or not report['portfolio_reconciled']:
                self._stop.set();return self._record('PAUSED','HALT_OR_RECONCILIATION_VETO')
            has_position=bool(report['execution_account']['account']['positions'])
            required_writes=4 if has_position else 12
            required_ids=1 if has_position else 2
            if report['capacity']['ordinary_commands_remaining']<required_writes or report['capacity']['intents_remaining']<required_ids or not report['capacity']['new_intents_allowed']:
                self._stop.set();return self._record('PAUSED','JOURNAL_OR_ID_CAPACITY_VETO')
            quote=None
            try:
                quote=self.fetcher(PRODUCT);now=self.clock()
                clock_reason=self._clock_check()
                if clock_reason:return self._pause_clock(clock_reason)
                if not isinstance(quote,dict):raise ValueError('MISSING_FORWARD_QUOTE')
                if self._stop.is_set():return self._record('HOLD','STOPPED_DURING_FETCH')
                feed=self._guard.evaluate(quote,now)
                if quote.get('product')!=PRODUCT:raise ValueError('QUOTE_PRODUCT_MISMATCH')
                if not isinstance(quote.get('exchange_time'),str):raise ValueError('INVALID_EXCHANGE_TIMESTAMP')
                exchange=datetime.fromisoformat(quote['exchange_time'].replace('Z','+00:00'))
                if exchange.tzinfo is None:raise ValueError('QUOTE_TIMEZONE_MISSING')
                stamp=exchange.timestamp()
                received=float(quote['received_at'])
                if feed['decision']=='VETO' or not 0<=now-received<=self._policy['receive_age_seconds'] or not 0<=now-stamp<=self._policy['exchange_age_seconds']:
                    raise ValueError('STALE_OR_UNVERIFIED_FORWARD_QUOTE')
                bid=Decimal(str(quote['bid']));ask=Decimal(str(quote['ask']))
                if not bid.is_finite() or not ask.is_finite():raise ValueError('INVALID_QUOTE')
            except (ValueError,KeyError,TypeError,OSError,TimeoutError) as exc:
                self._record_quote(quote,False,type(exc).__name__)
                self._prices.clear();self._last_rank=0
                self._failures+=1;self._error=type(exc).__name__
                if self._failures>=self._policy['consecutive_failure_limit']:self._stop.set()
                return self._record('PAUSED' if self._stop.is_set() else 'VETO','QUOTE_UNAVAILABLE_OR_INVALID',consecutive_failures=self._failures)
            regressed=self._stamp is not None and stamp<self._stamp
            self._record_quote(quote,not regressed,'EXCHANGE_TIME_REGRESSION' if regressed else None)
            if stamp==self._stamp:return self._record('HOLD','DUPLICATE_EXCHANGE_TICK')
            if self._stamp is not None and stamp<self._stamp:
                self._prices.clear();self._last_rank=0
                self._failures+=1
                if self._failures>=self._policy['consecutive_failure_limit']:self._stop.set()
                return self._record('PAUSED' if self._stop.is_set() else 'VETO','EXCHANGE_TIME_REGRESSION')
            self._failures=0;self._error=None
            self._stamp=stamp
            if self._prices and now-self._prices[-1][0]>2*INTERVAL:self._prices.clear();self._last_rank=0
            self._prices.append((now,(bid+ask)/2))
            account=report['execution_account']['account']
            if any(p!=PRODUCT for p in account['positions']):
                self._stop.set();return self._record('PAUSED','BTC_ONLY_ACCOUNT_INVARIANT')
            position=account['positions'].get(PRODUCT)
            if position:
                qty=Decimal(position['quantity']);entry=Decimal(position['entry'])
                move=(bid/entry-1)*10000
                equity=Decimal(account['cash'])+qty*bid*(1-Decimal(self._policy['fee_bps'])/10000)
                if abs(move)<self._policy['exit_threshold_bps'] and equity>Decimal(account['initial'])*(1-Decimal(self._policy['capital_loss_bps'])/10000):
                    return self._record('HOLD','POSITION_WITHIN_EXPERIMENTAL_EXIT_BANDS')
                px=bid*(1-Decimal(self._policy['slippage_bps'])/10000);amount=min(Decimal(200),qty*px)
                quantity=min(qty,(amount/px).quantize(Decimal('1e-12'),rounding=ROUND_DOWN))
                side='SELL'
            else:
                if now-self._last_rank<RANKING_INTERVAL:return self._record('HOLD','ENTRY_RANKING_INTERVAL')
                self._last_rank=now
                if len(self._prices)<self._policy['warmup_observations'] or now-self._prices[0][0]<self._policy['warmup_seconds']:
                    return self._record('HOLD','WARMUP_REQUIRES_SIX_DISTINCT_TICKS')
                move=(self._prices[-1][1]/self._prices[0][1]-1)*10000
                if move<self._policy['entry_threshold_bps']:return self._record('HOLD','EXPERIMENTAL_ENTRY_THRESHOLD_NOT_MET')
                amount=min(Decimal(self._policy['maximum_entry_usd']),Decimal(account['initial'])*Decimal(self._policy['initial_entry_fraction_bps'])/10000)
                try:require_entry(self._tier,str(amount))
                except ValueError as exc:return self._record('VETO',str(exc))
                if Decimal(account['cash'])<=Decimal(account['initial'])*(1-Decimal(self._policy['capital_loss_bps'])/10000):
                    self._stop.set();return self._record('PAUSED','INITIAL_CAPITAL_LOSS_THRESHOLD')
                px=ask*(1+Decimal(self._policy['slippage_bps'])/10000)
                quantity=(amount/px).quantize(Decimal('1e-12'),rounding=ROUND_DOWN)
                side='BUY'
            if quantity<=0:return self._record('VETO','QUANTITY_BELOW_PRECISION')
            # The immutable lifetime starts at the quote observation, not a
            # later wall-clock value; all timestamps are preserved in the model.
            try:return self._execute(side,quantity,bid,ask,received,received,amount)
            except ValueError as exc:
                return self._record('VETO',str(exc))
        except (OSError,sqlite3.Error,ValueError) as exc:
            self._error=type(exc).__name__;self._stop.set()
            return dict(decision='PAUSED',reason='FORWARD_STORAGE_OR_INTEGRITY_FAILURE')
        finally:
            self._release_stopped_ownership()
            self._cycle_lock.release()

    def status(self):
        with self._lock:
            return dict(mode='EXPERIMENTAL_PUBLIC_QUOTE_PAPER_FORWARD',enabled=not self._stop.is_set(),
                        worker_alive=bool(self._thread and self._thread.is_alive()),ownership_held=bool(self._ownership and self._ownership.held),session=self._session,tier=self._tier,
                        product=PRODUCT,interval_seconds=INTERVAL,entry_ranking_seconds=RANKING_INTERVAL,
                        cycles=self._cycles,consecutive_feed_failures=self._failures,last_error=self._error,last_event=self._last,
                        live_orders_enabled=False,broker_connected=False,strategy_validated=False,
                        liquidity_model='ASSUMED_QUANTITY_NO_DEPTH',automatic_restart=False,clock_guard_tolerance_seconds=CLOCK_DISCONTINUITY_TOLERANCE_SECONDS,policy=dict(self._policy))

    def evidence_report(self,session_id):
        import re
        if not isinstance(session_id,str) or not re.fullmatch(r'[a-f0-9]{16}',session_id):raise ValueError('INVALID_FORWARD_SESSION_ID')
        with closing(self._db()) as db,db:
            db.execute('BEGIN')
            session=db.execute('SELECT started_at,updated_at,state,policy_json,counts_json FROM sessions WHERE id=?',(session_id,)).fetchone()
            if session is None:raise ValueError('UNKNOWN_FORWARD_SESSION')
            quotes=forward_evidence.review(db,session_id)
        orders=self.orders.session_orders(session_id)
        counts=json.loads(session[4]);policy=json.loads(session[3])
        checked=forward_evidence.crosscheck(quotes,orders,counts)
        reasons=quotes['reasons']+checked['reasons']
        import hashlib
        policy_digest=hashlib.sha256(forward_evidence.canonical({k:v for k,v in policy.items() if k!='policy_sha256'}).encode()).hexdigest()
        if policy_digest!=policy.get('policy_sha256'):reasons.append('SESSION_POLICY_FINGERPRINT_DISAGREEMENT')
        if not quotes['observations']:reasons.append('NO_RECORDED_QUOTE_EVIDENCE')
        if any(p['policy_sha256']!=policy['policy_sha256'] for p in quotes['points']):reasons.append('QUOTE_POLICY_FINGERPRINT_DISAGREEMENT')
        summary={k:v for k,v in quotes.items() if k!='points'}
        return dict(mode='READ_ONLY_FORWARD_EVIDENCE_REVIEW',read_only=True,session=session_id,
                    started_at=session[0],updated_at=session[1],stored_state=session[2],policy=policy,
                    counters=counts,quote_evidence=summary,execution_crosscheck=checked,
                    provisional_active_session=session_id==self._session and not self._stop.is_set(),
                    local_consistency_ok=not reasons,reasons=sorted(set(reasons)),
                    live_orders_enabled=False,release_approved=False,strategy_validated=False,
                    limitations='Local consistency diagnostics, not independent market attestation or sustained-run acceptance. Injected transports are labeled separately. Counters may miss a committed fill after a process crash; the order ledger is authoritative. Local hashes cannot prevent coordinated database rewrites. Review while stopped for a stable cross-database snapshot.')

    def sessions(self,limit=20):
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('INVALID_SESSION_HISTORY_LIMIT')
        with closing(self._db()) as db:
            rows=db.execute('SELECT id,started_at,updated_at,state,policy_json,counts_json,last_json FROM sessions ORDER BY started_at DESC,rowid DESC LIMIT ?',(limit,)).fetchall()
        results=[]
        for sid,started,updated,state,policy,counts,last in rows:
            interrupted=state=='RUNNING' and sid!=self._session
            stale=state=='RUNNING' and self.clock()-updated>20
            unrecorded_stop=state=='RUNNING' and sid==self._session and self._stop.is_set()
            results.append(dict(session=sid,started_at=started,updated_at=updated,stored_state=state,
                                effective_state='INTERRUPTED_OR_PRIOR_SESSION' if interrupted else 'STOPPED_WITHOUT_FINAL_RECORD' if unrecorded_stop else 'HEARTBEAT_STALE' if stale else state,
                                requires_review=interrupted or stale or unrecorded_stop,policy=json.loads(policy),counts=json.loads(counts),last_event=json.loads(last)))
        return dict(read_only=True,sessions=results,automatic_resume=False,session_limit=SESSION_LIMIT,
                    execution_source_of_truth='ORDER_JOURNAL_NOT_SESSION_COUNTERS')

    def storage_capacity(self):
        with closing(self._db()) as db,db:
            db.execute('BEGIN')
            sessions=db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
            quotes=db.execute('SELECT COUNT(*) FROM forward_quotes').fetchone()[0]
        sessions_left=max(0,SESSION_LIMIT-sessions);quotes_left=max(0,forward_evidence.LIMIT-quotes)
        warnings=[]
        for label,left,limit in (('SESSION_HISTORY',sessions_left,SESSION_LIMIT),('QUOTE_EVIDENCE',quotes_left,forward_evidence.LIMIT)):
            if left==0:warnings.append(label+'_FULL')
            elif left<=max(1,limit//10):warnings.append(label+'_NEAR_LIMIT')
        return dict(read_only=True,sessions_retained=sessions,session_limit=SESSION_LIMIT,sessions_remaining=sessions_left,
                    quote_observations_retained=quotes,quote_limit=forward_evidence.LIMIT,quote_observations_remaining=quotes_left,
                    session_intake_allowed=sessions_left>0,quote_intake_allowed=quotes_left>0,
                    new_session_storage_available=sessions_left>0 and quotes_left>0,warnings=warnings,
                    automatic_deletion=False,automatic_archival=False,
                    scope='Storage admission diagnostics only; does not grant execution permission or guarantee future exit evidence capacity. Back up and review limits; never delete retained evidence to admit a run.')

    def history(self):
        with closing(self._db()) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT detail FROM cycles ORDER BY id DESC LIMIT 30')]


runner=PaperForwardRunner()
