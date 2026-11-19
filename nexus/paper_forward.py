"""Explicitly started BTC-only experimental paper forward runner.

Public quotes are observations; fills assume available quantity, slippage and
fees, not actual matching/depth. No broker IO, validated edge or live authority.
Restarts always require an explicit new start. The read-only observer is separate.
"""
import json
import math
import os
import sqlite3
import stat
import threading
import time
import uuid
from collections import deque
from contextlib import closing, contextmanager
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from nexus.durable_orders import gateway
from nexus.feed_guard import FeedGuard
from nexus.public_data import PUBLIC_PRICE_MAX, PUBLIC_PRICE_MIN, read_ticker
from nexus.tier_permissions import require_entry, TIERS
from nexus.state import load_settings
from nexus.paper_policy import admit, POLICY
from nexus import emergency_intent,forward_evidence,forward_sessions
from nexus.forward_ownership import ForwardOwnership
from nexus.sqlite_journal_guard import (
    capture_file_identities, check_existing, committed_main_fingerprint,
    require_main_connection, sync_committed_main)

INTERVAL=POLICY['interval_seconds']
STOP_JOIN_TIMEOUT=6
RANKING_INTERVAL=POLICY['ranking_seconds']
PRODUCT=POLICY['product']
CLOCK_DISCONTINUITY_TOLERANCE_SECONDS=2
MAX_WALL_CLOCK_SECONDS=253402300799
SESSION_LIMIT=1000
MAX_DATABASE_BYTES=256*1024*1024
DB_BUSY_TIMEOUT_MS=10_000
COMMIT_DURABILITY_CHECKS=('commit_durability_failure_retained_for_review',
    'commit_sync_preserves_process_control')
COMMIT_DURABILITY_PUBLIC_KEYS=frozenset(('source','passed',
    *COMMIT_DURABILITY_CHECKS,'actual_provider_access','broker_connected',
    'live_orders_enabled','sustained_forward_accepted','release_approved'))
QUOTE_FIELDS=frozenset(('provider','product','bid','ask','last','received_at',
    'exchange_time','read_only','orders_allowed'))


def _require_quote(quote):
    """Require the exact production-adapter output types before any coercion."""
    if (type(quote) is not dict or
            any(type(key) is not str for key in quote) or
            not QUOTE_FIELDS.issubset(quote)):
        raise ValueError('INVALID_FORWARD_QUOTE')
    if (type(quote['provider']) is not str or
            quote['provider']!='coinbase_exchange_public' or
            type(quote['product']) is not str or quote['product']!=PRODUCT or
            type(quote['exchange_time']) is not str or
            type(quote['read_only']) is not bool or quote['read_only'] is not True or
            type(quote['orders_allowed']) is not bool or quote['orders_allowed'] is not False):
        raise ValueError('INVALID_FORWARD_QUOTE')
    for field in ('bid','ask','last'):
        value=quote[field]
        if (type(value) not in (int,float) or
                (type(value) is float and not math.isfinite(value))):
            raise ValueError('INVALID_FORWARD_QUOTE')
        numeric=Decimal(value) if type(value) is int else Decimal(str(value))
        if not PUBLIC_PRICE_MIN <= numeric <= PUBLIC_PRICE_MAX:
            raise ValueError('INVALID_FORWARD_QUOTE')
    received=quote['received_at']
    if (type(received) not in (int,float) or
            (type(received) is float and not math.isfinite(received)) or
            not 0<=received<MAX_WALL_CLOCK_SECONDS):
        raise ValueError('INVALID_FORWARD_QUOTE')
    return quote


def _database_busy(error):
    code=getattr(error,'sqlite_errorcode',None)
    return (code in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) or
        any(word in str(error).lower() for word in ('busy','locked')))


def _busy():raise ValueError('PAPER_FORWARD_DATABASE_BUSY') from None


@contextmanager
def _transaction(db):
    """Normalize bounded statement/final-commit contention without retry."""
    try:
        with db:yield
    except sqlite3.OperationalError as error:
        if _database_busy(error):_busy()
        raise


class OrderCleanupFailure(Exception):
    def __init__(self,order_id,error):
        self.order_id=order_id;self.error_type=type(error).__name__
        super().__init__('ORDER_CLEANUP_FAILED_REVIEW_REQUIRED')


class PaperForwardRunner:
    def __init__(self,orders=gateway,fetcher=read_ticker,clock=time.time,monotonic_clock=None,
            scheduler_clock=None,scheduler_wait=None):
        self.orders=orders;self.fetcher=fetcher;self.clock=clock
        self.monotonic_clock=monotonic_clock or (time.monotonic if clock is time.time else clock)
        self._clock_anchor=None
        self._stop=threading.Event();self._stop.set()
        self.scheduler_clock=scheduler_clock or time.monotonic
        self.scheduler_wait=scheduler_wait or self._stop.wait
        self._cut_requested=threading.Event()
        self._thread=None;self._lock=threading.RLock();self._cycle_lock=threading.Lock()
        self._execution_lock=threading.RLock();self._control_lock=threading.Lock()
        self._session=None;self._tier=None;self._prices=deque(maxlen=POLICY['warmup_observations'])
        self._guard=FeedGuard();self._stamp=None;self._last_rank=0
        self._failures=0;self._cycles=0;self._last=None;self._error=None
        self._policy=admit(POLICY["id"])
        self._observation_hash=None
        self._ownership=None

    @staticmethod
    def _clock_value(callback,wall=False):
        """Return one bounded ordinary sample; keep process-control signals visible."""
        try:
            value=callback()
            valid=type(value) in (int,float) and math.isfinite(value)
        except Exception:return None
        if not valid or (wall and not 0<=value<MAX_WALL_CLOCK_SECONDS):return None
        return value

    def _scheduler_value(self):
        value=self._clock_value(self.scheduler_clock)
        return value if value is not None and 0<=value<MAX_WALL_CLOCK_SECONDS else None

    @staticmethod
    def _write_path():
        """Freeze one exact non-symlink data root for this write attempt."""
        root=Path(os.environ.get('NEXUS_PORTABLE_DATA',
            str(Path.home()/'.nexus-operative'))).expanduser()
        try:root.mkdir(parents=True,exist_ok=True)
        except OSError:raise ValueError('PAPER_FORWARD_STORAGE_ROOT_UNAVAILABLE') from None
        try:
            before=root.lstat()
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise ValueError('PAPER_FORWARD_STORAGE_ROOT_UNAVAILABLE')
            resolved=root.resolve(strict=True);after=root.lstat()
            if (stat.S_ISLNK(after.st_mode) or not stat.S_ISDIR(after.st_mode) or
                    not os.path.samestat(before,after)):
                raise ValueError('PAPER_FORWARD_STORAGE_ROOT_CHANGED')
            return resolved/'paper_forward.sqlite3'
        except ValueError:raise
        except (OSError,RuntimeError,TypeError):
            raise ValueError('PAPER_FORWARD_STORAGE_ROOT_CHANGED') from None

    def _db(self,*,_details=False):
        bound_path=self._write_path();new=False
        if not check_existing(bound_path,MAX_DATABASE_BYTES,'PAPER_FORWARD_'):
            try:
                descriptor=os.open(bound_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                try:os.close(descriptor)
                except BaseException:
                    raise
                new=True
            except FileExistsError:pass
            check_existing(bound_path,MAX_DATABASE_BYTES,'PAPER_FORWARD_')
        db=None
        try:
            db=sqlite3.connect(bound_path.as_uri()+'?mode=rw',uri=True,
                timeout=DB_BUSY_TIMEOUT_MS/1000)
            require_main_connection(db,bound_path,'PAPER_FORWARD_')
            db.execute(f'PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}')
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            page_size=db.execute('PRAGMA page_size').fetchone()[0]
            db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES//page_size}')
            db.execute('CREATE TABLE IF NOT EXISTS cycles(id INTEGER PRIMARY KEY,at REAL NOT NULL,detail TEXT NOT NULL)')
            db.execute("""CREATE TABLE IF NOT EXISTS sessions(
                id TEXT PRIMARY KEY,started_at REAL NOT NULL,updated_at REAL NOT NULL,
                state TEXT NOT NULL,policy_json TEXT NOT NULL,counts_json TEXT NOT NULL,last_json TEXT NOT NULL)""")
            forward_evidence.schema(db)
            return (db,new,bound_path) if _details else db
        except sqlite3.OperationalError as error:
            if db is not None:db.close()
            if _database_busy(error):_busy()
            raise
        except BaseException:
            if db is not None:db.close()
            raise

    @contextmanager
    def _write_db(self):
        """Acknowledge only one exact committed and explicitly flushed main."""
        db,new,bound_path=self._db(_details=True)
        try:
            identity=capture_file_identities((bound_path,),'PAPER_FORWARD_')[0]
        except BaseException:
            db.close();raise
        try:yield db
        except BaseException:
            db.close();raise
        else:
            error=None;fingerprint=None;uncommitted=db.in_transaction
            try:
                require_main_connection(db,bound_path,'PAPER_FORWARD_')
                if not uncommitted:
                    fingerprint=committed_main_fingerprint(db,bound_path,
                        MAX_DATABASE_BYTES,'PAPER_FORWARD_')
            except BaseException as caught:error=caught
            try:db.close()
            except BaseException as caught:
                if error is None:error=caught
            if error is not None:raise error
            if fingerprint is not None:
                sync_committed_main(bound_path,identity,fingerprint,
                    MAX_DATABASE_BYTES,'PAPER_FORWARD_',new=new)
            if uncommitted:
                raise ValueError('PAPER_FORWARD_UNCOMMITTED_TRANSACTION') from None

    @contextmanager
    def _read_db(self):
        """Observe existing evidence without creating storage or migrating it."""
        path=Path(os.environ.get('NEXUS_PORTABLE_DATA',str(Path.home()/'.nexus-operative')))/'paper_forward.sqlite3'
        try:observed=path.lstat()
        except FileNotFoundError:
            yield None
            return
        if stat.S_ISLNK(observed.st_mode):raise ValueError('FORWARD_HISTORY_SYMLINK')
        if not stat.S_ISREG(observed.st_mode):
            raise ValueError('FORWARD_HISTORY_REGULAR_FILE_REQUIRED')
        if observed.st_nlink!=1:raise ValueError('FORWARD_HISTORY_HARDLINK')
        db=None
        try:
            db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,
                timeout=DB_BUSY_TIMEOUT_MS/1000)
            db.execute(f'PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}')
            db.execute('BEGIN')
            yield db
        except sqlite3.OperationalError as error:
            if _database_busy(error):_busy()
            raise
        finally:
            if db is not None:db.close()

    def _record(self,decision,reason,**extra):
        self._require_ownership()
        observed=self._clock_value(self.clock,wall=True)
        valid=observed is not None
        recorded=observed if valid else (self._clock_anchor[0] if self._clock_anchor else 0)
        if not valid and decision not in ('STOP','PAUSED'):
            decision='PAUSED';reason='INVALID_RUNTIME_CLOCK'
            self._error=reason;self._stop.set()
        row=dict(at=recorded,clock_sample_valid=valid,session=self._session,cycle=self._cycles,quote_evidence_sha256=self._observation_hash,decision=decision,reason=reason,
                 product=PRODUCT,tier=self._tier,simulation_only=True,**extra)
        row['policy_id']=self._policy['id'];row['policy_sha256']=self._policy['policy_sha256']
        with self._write_db() as db:
            with _transaction(db):
                db.execute('BEGIN IMMEDIATE')
                old=db.execute('SELECT started_at,updated_at,state,policy_json,counts_json,last_json FROM sessions WHERE id=?',(self._session,)).fetchone()
                if old is None:
                    if decision!='START':raise ValueError('FORWARD_SESSION_MISSING')
                    if db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]>=SESSION_LIMIT:raise ValueError('FORWARD_SESSION_HISTORY_CAP')
                    started=row['at'];state='RUNNING';counts={}
                else:
                    if decision=='START':raise ValueError('FORWARD_SESSION_ID_REUSED')
                    policy,counts,last=forward_sessions.parse(*old)
                    if policy!=self._policy:raise ValueError('FORWARD_SESSION_POLICY_CHANGED')
                    started,updated,state=old[:3]
                    row['at']=max(row['at'],updated)
                    # A concurrently acknowledged terminal record owns the
                    # durable session tail. The in-flight caller may return its
                    # local reason, but can never replace STOP/PAUSED history.
                    terminal='STOP' if state=='STOPPED' else 'PAUSED' if state=='PAUSED' else None
                    if terminal is not None and decision!=terminal:return row
                counts[decision]=counts.get(decision,0)+1
                if decision=='PAUSED':state='PAUSED'
                elif decision=='STOP':state='STOPPED'
                db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?)',
                           (self._session,started,row['at'],state,json.dumps(self._policy,sort_keys=True),json.dumps(counts,sort_keys=True),json.dumps(row,allow_nan=False)))
                db.execute('INSERT INTO cycles(at,detail) VALUES (?,?)',(row['at'],json.dumps(row,allow_nan=False)))
                db.execute('DELETE FROM cycles WHERE id NOT IN (SELECT id FROM cycles ORDER BY id DESC LIMIT 1000)')
        self._require_ownership()
        with self._lock:self._last=row
        return row

    def _record_quote(self,quote,eligible,error=None):
        self._require_ownership()
        if self._clock_anchor is None:raise ValueError('INVALID_RUNTIME_CLOCK')
        with self._write_db() as db:
            with _transaction(db):
                db.execute('BEGIN IMMEDIATE')
                candidate=forward_evidence.append(db,self._session,self._cycles,self._clock_anchor[0],quote,eligible,
                    'PUBLIC_READER_SINGLE_SOURCE_UNATTESTED' if self.fetcher is read_ticker else 'INJECTED_TRANSPORT_UNATTESTED',
                    self._policy['policy_sha256'],error)
        self._require_ownership()
        self._observation_hash=candidate

    def request_cut(self):
        """Signal only: no locks owned by execution, IO, ownership release or acknowledgment."""
        self._cut_requested.set()

    def clear_cut_signal(self):
        """Coordinator only: after durable reset and its final epoch check. Never starts work."""
        self._cut_requested.clear()

    def _cut_active(self):
        if self._cut_requested.is_set():return True
        try:return emergency_intent.requested(self.orders._path())
        except (ValueError,OSError):return True

    def _intake_stopped(self):
        return self._stop.is_set() or self._cut_active()

    def _require_ownership(self):
        owner=self._ownership
        try:
            if owner is None:raise ValueError('PAPER_FORWARD_OWNER_NOT_HELD')
            owner.validate()
        except ValueError:
            self._error='PAPER_FORWARD_OWNER_STORE_CHANGED';self._stop.set()
            raise

    def prepare(self,tier,policy_id=POLICY['id']):
        """Validate/start session state without starting a thread (also testable offline)."""
        with self._cycle_lock,self._lock:
            if self._cut_active():raise ValueError('EMERGENCY_CUT_REQUESTED')
            if not self._stop.is_set() or (self._thread and self._thread.is_alive()):raise ValueError('PAPER_FORWARD_ALREADY_ACTIVE')
            if self._ownership and self._ownership.held:
                raise ValueError('PAPER_FORWARD_PREVIOUS_CYCLE_STILL_ACTIVE')
            self._ownership=ForwardOwnership(self.orders._path().parent)
            self._ownership.acquire()
            try:
                self._require_ownership()
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
                    self._require_ownership()
                    self.orders.initialize_account(load_settings()['paper_budget'])
                    report=self.orders.recovery_report()
                if not report['portfolio_reconciled']:
                    self._require_ownership()
                    self.orders.link_portfolio()
                    report=self.orders.recovery_report()
                if any(p!=PRODUCT for p in report['execution_account']['account']['positions']):
                    raise ValueError('FORWARD_RUNNER_REQUIRES_BTC_ONLY_ACCOUNT')
                if any(o['status'] in ('SIMULATED_ACK_ONLY','SIMULATED_PARTIALLY_FILLED') for o in report['orders']):
                    raise ValueError('REVIEW_OUTSTANDING_PAPER_ORDERS_FIRST')
                holding=bool(report['execution_account']['account']['positions'])
                intent_capacity=(report['capacity']['exit_intents_remaining'] if holding else
                    min(report['capacity']['ordinary_intents_remaining'],
                        report['capacity']['exit_intents_remaining']))
                if (not report['capacity']['lifecycle_intake_allowed'] or
                        report['capacity']['ordinary_commands_remaining']<(4 if holding else 12) or
                        intent_capacity<1):
                    raise ValueError('INSUFFICIENT_FORWARD_JOURNAL_CAPACITY')
                if self._cut_active():raise ValueError('EMERGENCY_CUT_REQUESTED')
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
            if worker and worker.is_alive() and worker is not threading.current_thread():
                worker.join(timeout=STOP_JOIN_TIMEOUT)
                if worker.is_alive():
                    self._error='STOP_QUIESCENCE_TIMEOUT'
                    raise ValueError('PAPER_FORWARD_STOP_QUIESCENCE_TIMEOUT')
            try:
                if was_enabled and self._session:
                    try:self._record('STOP',reason)
                    except (OSError,sqlite3.Error,ValueError):self._error='STOP_DIAGNOSTIC_STORAGE_FAILURE'
            finally:
                # Process-control signals from the terminal diagnostic must
                # remain visible without stranding the local ownership lease.
                if self._cycle_lock.acquire(blocking=False):
                    try:self._release_stopped_ownership()
                    finally:self._cycle_lock.release()
            return self.status()

    def _run(self):
        try:
            sampled=self._scheduler_value()
            if sampled is None:
                self._pause_scheduler('INVALID_SCHEDULER_CLOCK')
                return
            deadline=sampled
            previous=sampled
            while not self._intake_stopped():
                started=self._scheduler_value()
                if started is None:
                    self._pause_scheduler('INVALID_SCHEDULER_CLOCK')
                    return
                if started<previous:
                    self._pause_scheduler('SCHEDULER_CLOCK_REGRESSION')
                    return
                previous=started
                try:outcome=self.single_cycle()
                except Exception as exc:
                    # Unknown worker failures pause rather than creating an unmonitored loop.
                    self._error=type(exc).__name__;self._stop.set()
                    try:self._record('PAUSED','UNHANDLED_WORKER_FAILURE',error_type=type(exc).__name__)
                    except (OSError,sqlite3.Error,ValueError):pass
                else:
                    if outcome.get('reason')=='CYCLE_BUSY':
                        self._pause_scheduler('WORKER_CYCLE_CONCURRENCY')
                        return
                if self._intake_stopped():return
                completed=self._scheduler_value()
                if completed is None:
                    self._pause_scheduler('INVALID_SCHEDULER_CLOCK')
                    return
                if completed<previous:
                    self._pause_scheduler('SCHEDULER_CLOCK_REGRESSION')
                    return
                previous=completed
                nominal=deadline+INTERVAL
                if completed>nominal:
                    try:self._record('HOLD','SCHEDULER_OVERRUN',
                            scheduler_interval_seconds=INTERVAL,
                            scheduler_cycle_duration_seconds=completed-started,
                            scheduler_overrun_seconds=completed-nominal,
                            scheduler_clock_valid=True,burst_catchup_allowed=False)
                    except (OSError,sqlite3.Error,ValueError):
                        self._error='SCHEDULER_EVIDENCE_STORAGE_FAILURE';self._stop.set()
                        return
                    if self._intake_stopped():return
                    deadline=completed+INTERVAL
                    after_record=self._scheduler_value()
                    if after_record is None:
                        self._pause_scheduler('INVALID_SCHEDULER_CLOCK')
                        return
                    if after_record<previous:
                        self._pause_scheduler('SCHEDULER_CLOCK_REGRESSION')
                        return
                    previous=after_record
                    if previous>deadline:deadline=previous+INTERVAL
                else:deadline=nominal
                while not self._intake_stopped() and previous<deadline:
                    before=previous
                    try:self.scheduler_wait(deadline-before)
                    except Exception:
                        self._pause_scheduler('SCHEDULER_WAIT_FAILURE')
                        return
                    if self._intake_stopped():return
                    after=self._scheduler_value()
                    if after is None:
                        self._pause_scheduler('INVALID_SCHEDULER_CLOCK')
                        return
                    if after<before:
                        self._pause_scheduler('SCHEDULER_CLOCK_REGRESSION')
                        return
                    if after==before:
                        self._pause_scheduler('SCHEDULER_CLOCK_STALLED')
                        return
                    previous=after
        finally:
            # Never leave a dead worker enabled or retaining its process
            # owner. BaseException remains observable by threading.excepthook.
            self._stop.set()
            self._release_stopped_ownership()

    def _pause_scheduler(self,reason):
        """Retain a sanitized durable stop when worker scheduling is untrustworthy."""
        self._prices.clear();self._last_rank=0;self._error=reason;self._stop.set()
        try:
            return self._record('PAUSED',reason,scheduler_clock_valid=False,
                scheduler_interval_seconds=INTERVAL,burst_catchup_allowed=False)
        except (OSError,sqlite3.Error,ValueError):
            return dict(decision='PAUSED',reason=reason)

    def _execute(self,*args):
        with self._execution_lock:return self._execute_locked(*args)

    def _execution_now(self,exchange_at):
        self._require_ownership()
        if self._intake_stopped():raise ValueError('RUNNER_STOPPED_BEFORE_FILL')
        reason=self._clock_check()
        if reason:raise ValueError(reason)
        now=self._clock_anchor[0]
        if not 0<=now-exchange_at<=self._policy['exchange_age_seconds']:
            raise ValueError('STALE_OR_FUTURE_EXCHANGE_QUOTE_AT_FILL')
        return now

    def _execute_locked(self,side,quantity,bid,ask,received,now,notional,exchange_at,
                        expected_position_quantity=None):
        self._require_ownership()
        clock_reason=self._clock_check()
        if clock_reason:return self._pause_clock(clock_reason)
        if self._intake_stopped():return dict(decision='HOLD',reason='RUNNER_STOPPED_BEFORE_SUBMIT')
        oid='pf_'+self._session+'_'+str(self._cycles)+'_'+side
        self._require_ownership()
        if side=='SELL':
            self.orders.submit_reducing(oid,PRODUCT,str(notional),str(quantity),
                str(expected_position_quantity),tier=self._tier)
        else:
            self.orders.submit(oid,PRODUCT,side,str(notional),tier=self._tier)
        try:
            if self._intake_stopped():return dict(decision='HOLD',reason='RUNNER_STOPPED_AFTER_SUBMIT')
            self._require_ownership()
            self.orders.set_execution_terms(oid,now,self._policy['order_lifetime_seconds'])
            # Quote may precede order creation by transport latency; use a fresh
            # observation boundary so the timestamp model retains source age.
            if self._intake_stopped():return dict(decision='HOLD',reason='RUNNER_STOPPED_BEFORE_FILL')
            self._require_ownership()
            clock_reason=self._clock_check()
            if clock_reason:return self._pause_clock(clock_reason)
            result=self.orders.simulate_quote_fill(oid,'fill',1,str(quantity),str(bid),str(ask),str(quantity),received,now,fee_bps=self._policy['fee_bps'],slippage_bps=self._policy['slippage_bps'],execution_clock=lambda:self._execution_now(exchange_at))
            if result.get('expired'):
                self._prices.clear();self._last_rank=0
                return self._record('VETO','ORDER_EXPIRED_WITHOUT_FILL',order_id=oid)
            return self._record(side,'MODELED_PUBLIC_QUOTE_FILL',order_id=oid,
                                liquidity='ASSUMED_QUANTITY_NO_DEPTH',strategy_validated=False)
        finally:
            try:self.orders.cancel(oid)
            except (OSError,sqlite3.Error,ValueError) as exc:
                self._stop.set()
                raise OrderCleanupFailure(oid,exc) from exc

    def _clock_check(self):
        wall=self._clock_value(self.clock,wall=True)
        steady=self._clock_value(self.monotonic_clock)
        if wall is None or steady is None:
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
            if self._intake_stopped():return dict(decision='HOLD',reason='PAPER_FORWARD_STOPPED')
            self._require_ownership()
            self._cycles+=1
            self._observation_hash=None
            with self._read_db() as db:
                if db is None:raise ValueError('FORWARD_SESSION_MISSING')
                metadata=db.execute('SELECT started_at,updated_at,state,policy_json,counts_json,last_json FROM sessions WHERE id=?',(self._session,)).fetchone()
                if metadata is None:raise ValueError('FORWARD_SESSION_MISSING')
                stored_policy,_,_=forward_sessions.parse(*metadata)
                if stored_policy!=self._policy:raise ValueError('FORWARD_SESSION_POLICY_CHANGED')
            self._require_ownership()
            clock_reason=self._clock_check()
            if clock_reason:return self._pause_clock(clock_reason)
            storage=self.storage_capacity()
            if not storage['quote_intake_allowed']:
                self._stop.set();return self._record('PAUSED','FORWARD_QUOTE_EVIDENCE_CAP')
            report=self.orders.recovery_report()
            self._require_ownership()
            if report['halted'] or not report['portfolio_reconciled']:
                self._stop.set();return self._record('PAUSED','HALT_OR_RECONCILIATION_VETO')
            has_position=bool(report['execution_account']['account']['positions'])
            required_writes=4 if has_position else 12
            id_capacity=(report['capacity']['exit_intents_remaining'] if has_position else
                min(report['capacity']['ordinary_intents_remaining'],
                    report['capacity']['exit_intents_remaining']))
            if (report['capacity']['ordinary_commands_remaining']<required_writes or
                    id_capacity<1 or
                    (not has_position and not report['capacity']['new_intents_allowed'])):
                self._stop.set();return self._record('PAUSED','JOURNAL_OR_ID_CAPACITY_VETO')
            quote=None
            admitted_quote=None
            try:
                self._require_ownership()
                quote=self.fetcher(PRODUCT)
                self._require_ownership()
                clock_reason=self._clock_check()
                if clock_reason:return self._pause_clock(clock_reason)
                now=self._clock_anchor[0]
                quote=_require_quote(quote);admitted_quote=quote
                if self._intake_stopped():return dict(decision='HOLD',reason='STOPPED_DURING_FETCH')
                feed=self._guard.evaluate(quote,now)
                exchange=datetime.fromisoformat(quote['exchange_time'].replace('Z','+00:00'))
                if exchange.tzinfo is None:raise ValueError('QUOTE_TIMEZONE_MISSING')
                stamp=exchange.timestamp()
                received=quote['received_at']
                if feed['decision']=='VETO' or not 0<=now-received<=self._policy['receive_age_seconds'] or not 0<=now-stamp<=self._policy['exchange_age_seconds']:
                    raise ValueError('STALE_OR_UNVERIFIED_FORWARD_QUOTE')
                bid=Decimal(str(quote['bid']));ask=Decimal(str(quote['ask']))
                if not bid.is_finite() or not ask.is_finite():raise ValueError('INVALID_QUOTE')
            except (ValueError,KeyError,TypeError,OSError,TimeoutError) as exc:
                self._record_quote(admitted_quote,False,type(exc).__name__)
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
                px=bid*(1-Decimal(self._policy['slippage_bps'])/10000)
                position_notional=qty*px
                if position_notional<=Decimal(200):
                    # Preserve the exact reconciled holding quantity, including
                    # older priced fills with more precision than new entries.
                    quantity=qty;amount=position_notional
                else:
                    amount=Decimal(200)
                    quantity=(amount/px).quantize(Decimal('1e-12'),rounding=ROUND_DOWN)
                side='SELL'
            else:
                if now-self._last_rank<RANKING_INTERVAL:return self._record('HOLD','ENTRY_RANKING_INTERVAL')
                self._last_rank=now
                if len(self._prices)<self._policy['warmup_observations'] or now-self._prices[0][0]<self._policy['warmup_seconds']:
                    return self._record('HOLD','WARMUP_REQUIRES_SIX_DISTINCT_TICKS')
                move=(self._prices[-1][1]/self._prices[0][1]-1)*10000
                if move<self._policy['entry_threshold_bps']:return self._record('HOLD','EXPERIMENTAL_ENTRY_THRESHOLD_NOT_MET')
                amount=min(Decimal(self._policy['maximum_entry_usd']),Decimal(account['initial'])*Decimal(self._policy['initial_entry_fraction_bps'])/10000)
                try:require_entry(self._tier,amount)
                except ValueError as exc:return self._record('VETO',str(exc))
                if Decimal(account['cash'])<=Decimal(account['initial'])*(1-Decimal(self._policy['capital_loss_bps'])/10000):
                    self._stop.set();return self._record('PAUSED','INITIAL_CAPITAL_LOSS_THRESHOLD')
                px=ask*(1+Decimal(self._policy['slippage_bps'])/10000)
                quantity=(amount/px).quantize(Decimal('1e-12'),rounding=ROUND_DOWN)
                side='BUY'
            if quantity<=0:return self._record('VETO','QUANTITY_BELOW_PRECISION')
            # The immutable lifetime starts at the quote observation, not a
            # later wall-clock value; all timestamps are preserved in the model.
            try:return self._execute(side,quantity,bid,ask,received,received,amount,stamp,
                                     qty if side=='SELL' else None)
            except OrderCleanupFailure as exc:
                self._prices.clear();self._last_rank=0;self._error=str(exc)
                return self._record('PAUSED',str(exc),order_id=exc.order_id,
                                    error_type=exc.error_type,cleanup_acknowledged=False,
                                    order_ledger_authoritative=True)
            except ValueError as exc:
                reason=str(exc)
                if reason in ('INVALID_RUNTIME_CLOCK','RUNTIME_CLOCK_DISCONTINUITY'):return self._pause_clock(reason)
                if reason in ('STALE_OR_FUTURE_SYNTHETIC_QUOTE','STALE_OR_FUTURE_EXCHANGE_QUOTE_AT_FILL'):self._prices.clear();self._last_rank=0
                if reason=='RUNNER_STOPPED_BEFORE_FILL':return dict(decision='HOLD',reason=reason)
                # The independent cut owns the durable halt/cancel audit. Do
                # not attempt an optional forward-session write after that
                # authority has closed runner ownership: the write can race
                # the completed cut and obscure the authoritative veto as a
                # generic storage failure even though the fill rolled back.
                if reason=='EMERGENCY_CUT_REQUESTED':return dict(decision='VETO',reason=reason)
                return self._record('VETO',reason)
        except (OSError,sqlite3.Error,ValueError) as exc:
            self._error=type(exc).__name__;self._stop.set()
            return dict(decision='PAUSED',reason='FORWARD_STORAGE_OR_INTEGRITY_FAILURE')
        finally:
            self._release_stopped_ownership()
            self._cycle_lock.release()

    def status(self):
        with self._lock:
            cut_requested=self._cut_active()
            return dict(mode='EXPERIMENTAL_PUBLIC_QUOTE_PAPER_FORWARD',enabled=not self._stop.is_set() and not cut_requested,cut_requested=cut_requested,
                        worker_alive=bool(self._thread and self._thread.is_alive()),ownership_held=bool(self._ownership and self._ownership.held),session=self._session,tier=self._tier,
                        product=PRODUCT,interval_seconds=INTERVAL,entry_ranking_seconds=RANKING_INTERVAL,
                        cycles=self._cycles,consecutive_feed_failures=self._failures,last_error=self._error,last_event=self._last,
                        live_orders_enabled=False,broker_connected=False,strategy_validated=False,
                        liquidity_model='ASSUMED_QUANTITY_NO_DEPTH',automatic_restart=False,clock_guard_tolerance_seconds=CLOCK_DISCONTINUITY_TOLERANCE_SECONDS,policy=dict(self._policy))

    def evidence_report(self,session_id):
        import re
        if not isinstance(session_id,str) or not re.fullmatch(r'[a-f0-9]{16}',session_id):raise ValueError('INVALID_FORWARD_SESSION_ID')
        order_snapshot=self.orders.session_orders_snapshot(session_id)
        with self._read_db() as db:
            if db is None:raise ValueError('UNKNOWN_FORWARD_SESSION')
            session=db.execute('SELECT started_at,updated_at,state,policy_json,counts_json,last_json FROM sessions WHERE id=?',(session_id,)).fetchone()
            if session is None:raise ValueError('UNKNOWN_FORWARD_SESSION')
            quotes=forward_evidence.review(db,session_id)
        final_order_snapshot=self.orders.session_orders_snapshot(session_id)
        orders=order_snapshot['orders']
        policy,counts,last=forward_sessions.parse(*session)
        checked=forward_evidence.crosscheck(quotes,orders,counts,policy)
        terminal=forward_sessions.binding_reasons(last,session[2],session_id,policy,
            quotes['head_sha256'],quotes['observations'],quotes['points'][-1]['cycle'] if quotes['points'] else 0,
            quotes['parent_head_sha256'])
        reasons=quotes['reasons']+checked['reasons']+terminal
        journal_snapshot_stable=(order_snapshot['journal_present']==final_order_snapshot['journal_present']
            and order_snapshot['commands']==final_order_snapshot['commands']
            and order_snapshot['journal_sha256']==final_order_snapshot['journal_sha256'])
        portfolio_snapshot_stable=(order_snapshot['portfolio_linked']==final_order_snapshot['portfolio_linked']
            and order_snapshot['portfolio_rows']==final_order_snapshot['portfolio_rows']
            and order_snapshot['portfolio_sha256']==final_order_snapshot['portfolio_sha256'])
        order_snapshot_stable=(journal_snapshot_stable and portfolio_snapshot_stable
            and order_snapshot['snapshot_sha256']==final_order_snapshot['snapshot_sha256'])
        if not journal_snapshot_stable:reasons.append('ORDER_JOURNAL_CHANGED_DURING_REVIEW')
        if not portfolio_snapshot_stable:reasons.append('LINKED_PORTFOLIO_CHANGED_DURING_REVIEW')
        if last['reason']=='ORDER_CLEANUP_FAILED_REVIEW_REQUIRED':reasons.append('ORDER_CLEANUP_REQUIRES_REVIEW')
        import hashlib
        policy_digest=hashlib.sha256(forward_evidence.canonical({k:v for k,v in policy.items() if k!='policy_sha256'}).encode()).hexdigest()
        if policy_digest!=policy.get('policy_sha256'):reasons.append('SESSION_POLICY_FINGERPRINT_DISAGREEMENT')
        if not quotes['observations']:reasons.append('NO_RECORDED_QUOTE_EVIDENCE')
        if any(p['policy_sha256']!=policy['policy_sha256'] for p in quotes['points']):reasons.append('QUOTE_POLICY_FINGERPRINT_DISAGREEMENT')
        summary={k:v for k,v in quotes.items() if k!='points'}
        return dict(mode='READ_ONLY_FORWARD_EVIDENCE_REVIEW',read_only=True,session=session_id,
                    started_at=session[0],updated_at=session[1],stored_state=session[2],policy=policy,
                    counters=counts,quote_evidence=summary,execution_crosscheck=checked,
                    order_journal_snapshot=dict(journal_present=order_snapshot['journal_present'],
                        commands=order_snapshot['commands'],portfolio_linked=order_snapshot['portfolio_linked'],
                        portfolio_rows=order_snapshot['portfolio_rows'],snapshot_sha256=order_snapshot['snapshot_sha256'],
                        journal_stable_during_review=journal_snapshot_stable,
                        portfolio_stable_during_review=portfolio_snapshot_stable,
                        stable_during_review=order_snapshot_stable),
                    provisional_active_session=session_id==self._session and not self._stop.is_set(),
                    local_consistency_ok=not reasons,reasons=sorted(set(reasons)),
                    live_orders_enabled=False,release_approved=False,strategy_validated=False,
                    limitations='Local consistency diagnostics, not independent market attestation or sustained-run acceptance. Injected transports are labeled separately. Counters may miss a committed fill after a process crash; the order ledger is authoritative. Two replay-validated order-and-linked-ledger snapshots bracket the separate forward-database read; any observed reconciliation snapshot change fails the review closed. Local hashes cannot prevent coordinated database rewrites. Review while stopped for a stable cross-database snapshot.')

    def sessions(self,limit=20):
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('INVALID_SESSION_HISTORY_LIMIT')
        with self._read_db() as db:
            rows=[] if db is None else db.execute('SELECT id,started_at,updated_at,state,policy_json,counts_json,last_json FROM sessions ORDER BY started_at DESC,rowid DESC LIMIT ?',(limit,)).fetchall()
        observed=None;observed_valid=False
        if any(row[3]=='RUNNING' for row in rows):
            observed=self._clock_value(self.clock,wall=True)
            observed_valid=observed is not None
        results=[]
        for sid,started,updated,state,policy,counts,last in rows:
            policy,counts,last=forward_sessions.parse(started,updated,state,policy,counts,last)
            interrupted=state=='RUNNING' and sid!=self._session
            clock_untrusted=state=='RUNNING' and (not observed_valid or observed<updated)
            stale=state=='RUNNING' and not clock_untrusted and observed-updated>20
            unrecorded_stop=state=='RUNNING' and sid==self._session and self._stop.is_set()
            review_reasons=[]
            if interrupted:review_reasons.append('INTERRUPTED_OR_PRIOR_SESSION')
            if clock_untrusted:review_reasons.append('SESSION_MONITOR_CLOCK_UNTRUSTED')
            if stale:review_reasons.append('HEARTBEAT_STALE')
            if unrecorded_stop:review_reasons.append('STOPPED_WITHOUT_FINAL_RECORD')
            if last['reason']=='ORDER_CLEANUP_FAILED_REVIEW_REQUIRED':review_reasons.append('ORDER_CLEANUP_REQUIRES_REVIEW')
            results.append(dict(session=sid,started_at=started,updated_at=updated,stored_state=state,
                                effective_state='INTERRUPTED_OR_PRIOR_SESSION' if interrupted else 'STOPPED_WITHOUT_FINAL_RECORD' if unrecorded_stop else 'CLOCK_REVIEW_REQUIRED' if clock_untrusted else 'HEARTBEAT_STALE' if stale else state,
                                requires_review=bool(review_reasons),review_reasons=review_reasons,policy=policy,counts=counts,last_event=last))
        return dict(read_only=True,sessions=results,automatic_resume=False,session_limit=SESSION_LIMIT,
                    execution_source_of_truth='ORDER_JOURNAL_NOT_SESSION_COUNTERS')

    def recovery_status(self,limit=20):
        from nexus.forward_recovery import build
        history=self.sessions(limit)
        return build(self.status(),self.orders.recovery_report(),history)

    def storage_capacity(self):
        with self._read_db() as db:
            return self._capacity_snapshot(db)

    @staticmethod
    def _capacity_snapshot(db):
        sessions=quotes=0
        if db is not None:
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
        with self._read_db() as db:
            return self._history_snapshot(db)

    @staticmethod
    def _history_snapshot(db):
        return [] if db is None else [forward_sessions.event(row[0]) for row in db.execute('SELECT detail FROM cycles ORDER BY id DESC LIMIT 30')]

    def diagnostics(self):
        # Only these SQLite observations share a transaction. In-memory runner
        # status and the separate order ledger are not an atomic combined view.
        with self._read_db() as db:
            capacity=self._capacity_snapshot(db)
            history=self._history_snapshot(db)
        return dict(history=history,storage_capacity=capacity)


def commit_durability_fixture_report():
    """Disposable retained-session proof; no public feed or broker access."""
    import tempfile
    from nexus import sqlite_journal_guard as durability
    from nexus.durable_orders import DurableOrderGateway
    from nexus.tier_permissions import configure

    previous=os.environ.get('NEXUS_PORTABLE_DATA');original=durability.os.fsync

    def exercise(root,interruption=False):
        root=Path(root);os.environ['NEXUS_PORTABLE_DATA']=str(root)
        configure('terrestrial',True)
        store=root/'paper_forward.sqlite3';observed=False

        def fsync(descriptor):
            nonlocal observed
            if store.exists():
                try:opened=os.fstat(descriptor);current=store.stat()
                except OSError:pass
                else:
                    if ((opened.st_dev,opened.st_ino)==
                            (current.st_dev,current.st_ino)):
                        observed=True
                        if interruption:raise KeyboardInterrupt
                        raise OSError('injected private forward flush failure')
            return original(descriptor)

        candidate=PaperForwardRunner(DurableOrderGateway(root/'orders.sqlite3'),
            fetcher=lambda product:None,clock=lambda:1000)
        durability.os.fsync=fsync;failure=None
        try:candidate.prepare('terrestrial')
        except BaseException as error:failure=error
        finally:durability.os.fsync=original
        cold=PaperForwardRunner(DurableOrderGateway(root/'orders.sqlite3'),
            fetcher=lambda product:None,clock=lambda:1000)
        sessions=cold.sessions()['sessions']
        retained=(len(sessions)==1 and sessions[0]['stored_state']=='RUNNING' and
            sessions[0]['effective_state']=='INTERRUPTED_OR_PRIOR_SESSION' and
            sessions[0]['requires_review'] is True)
        return dict(observed=observed,failure=failure,retained=retained,
            disabled=candidate.status()['enabled'] is False,
            ownership_released=candidate.status()['ownership_held'] is False)

    try:
        with tempfile.TemporaryDirectory(
                prefix='nexus-forward-commit-durability-') as temporary:
            failed=exercise(Path(temporary)/'failure')
            interrupted=exercise(Path(temporary)/'interrupt',True)
    finally:
        durability.os.fsync=original
        if previous is None:os.environ.pop('NEXUS_PORTABLE_DATA',None)
        else:os.environ['NEXUS_PORTABLE_DATA']=previous
    checks=dict(
        commit_durability_failure_retained_for_review=failed['observed'] and
            type(failed['failure']) is ValueError and
            str(failed['failure'])=='PAPER_FORWARD_COMMIT_DURABILITY_UNCERTAIN' and
            failed['retained'] and failed['disabled'] and
            failed['ownership_released'],
        commit_sync_preserves_process_control=interrupted['observed'] and
            type(interrupted['failure']) is KeyboardInterrupt and
            interrupted['retained'] and interrupted['disabled'] and
            interrupted['ownership_released'])
    return dict(source='TEMPORARY_INJECTED_FORWARD_COMMIT_DURABILITY_FIXTURE',
        passed=all(checks.values()),**checks,actual_provider_access=False,
        broker_connected=False,live_orders_enabled=False,
        sustained_forward_accepted=False,release_approved=False)


runner=PaperForwardRunner()
