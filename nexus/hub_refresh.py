"""Explicit bounded research refresh; public collection is a separate opt-in. Starts stopped after restart."""
import math
import threading
import time
from pathlib import Path
from nexus import market_hub as hub,hub_prediction
from nexus.forward_ownership import ForwardOwnership
from nexus.hub_jobs import BUILD_LOCK
from nexus import hub_collection as collection

INTERVAL_SECONDS=60
MAX_POLLS=60
MAX_FITS=8


class RefreshOwnership(ForwardOwnership):
    def __init__(self,directory):
        super().__init__(directory)
        self.path=Path(directory)/'hub_refresh.owner.lock'

    def acquire(self):
        try:super().acquire()
        except ValueError:raise ValueError('REFRESH_OWNED_OR_ALREADY_ACTIVE') from None


class ResearchRefresh:
    def __init__(self,clock=None,wall_clock=None,transport=None):
        self.clock=clock or time.monotonic
        self.wall_clock=wall_clock or time.time;self.transport=transport
        self._lock=threading.RLock();self._cycle=threading.Lock();self._stop=threading.Event();self._stop.set()
        self._thread=None;self._owner=None;self._root=None;self._admission=lambda:True
        self._config=None;self._phase='STOPPED';self._reason=None
        self._polls=self._fits=self._skips=self._reuses=self._busy=0
        self._last_clock=None;self._next_due=0;self._generation=None;self._model=None
        self._source_requests=0;self._frontier=None;self._wall_anchor=None

    def status(self):
        with self._lock:
            active=self._cycle.locked()
            phase='STOPPED' if self._phase=='STOPPING' and not active else self._phase
            return dict(mode='LOCAL_STORED_DATA_REFRESH',enabled=not self._stop.is_set(),phase=phase,reason=self._reason,
                worker_alive=bool(self._thread and self._thread.is_alive()),cycle_active=active,
                ownership_held=bool(self._owner and self._owner.held),configuration=dict(self._config) if self._config else None,
                polls=self._polls,fits=self._fits,unchanged_skips=self._skips,cached_reuses=self._reuses,busy_deferrals=self._busy,
                interval_seconds=INTERVAL_SECONDS,poll_limit=MAX_POLLS,fit_limit=MAX_FITS,
                last_model_content_sha256=self._model,starts_automatically=False,
                live_orders_enabled=False,paper_orders_enabled=False,provider_requests_enabled=False,
                public_history_enabled=bool(self._config and self._config.get('collect_public_history')),
                external_requests_enabled=not self._stop.is_set() and bool(self._config and self._config.get('collect_public_history')),
                source_requests=self._source_requests,source_request_limit=collection.MAX_SOURCE_REQUESTS,
                note='Portal/UI select bounded public history by default; standalone legacy API/core calls remain saved-only unless explicitly selected. One-process job admission; OS ownership excludes other refresh workers on this directory. Model research never replaces the forward policy.')

    def _arm(self,product,granularity,limit,admission=None,*,collect_public_history=False):
        with self._lock:
            if not self._stop.is_set() or self._cycle.locked() or (self._owner and self._owner.held) or (self._thread and self._thread.is_alive()):
                raise ValueError('REFRESH_OWNED_OR_ALREADY_ACTIVE')
            if product not in hub.PRODUCTS or granularity not in (300,3600,86400) or type(granularity) is not int or type(limit) is not int or not 240<=limit<=1000:
                raise ValueError('REFRESH_SCOPE_240_TO_1000_BARS')
            if type(collect_public_history) is not bool:raise ValueError('REFRESH_PUBLIC_OPT_IN_MUST_BE_BOOL')
            guard=admission or (lambda:True)
            if not guard():raise ValueError('PORTAL_MUST_BE_ACTIVE')
            root=hub.root().resolve();owner=RefreshOwnership(root);owner.acquire()
            self._owner=owner;self._root=root;self._admission=guard
            self._config=dict(product=product,granularity=granularity,limit=limit,collect_public_history=collect_public_history)
            self._polls=self._fits=self._skips=self._reuses=self._busy=0
            self._last_clock=None;self._next_due=0;self._generation=None;self._model=None
            self._source_requests=0;self._frontier=None;self._wall_anchor=None
            self._phase='WAITING';self._reason=None;self._stop.clear()

    def start(self,product,granularity=3600,limit=1000,admission=None,*,collect_public_history=False):
        with self._lock:
            self._arm(product,granularity,limit,admission,collect_public_history=collect_public_history)
            self._thread=threading.Thread(target=self._run,name='nexus-local-learning',daemon=True)
            try:self._thread.start()
            except BaseException:
                self._stop.set();self._phase='PAUSED';self._reason='WORKER_START_FAILED';self._owner.release();raise
        return self.status()

    def stop(self,reason='USER_STOP'):
        with self._lock:
            self._stop.set();self._reason=reason;self._phase='STOPPING' if self._cycle.locked() else 'STOPPED'
        if self._cycle.acquire(False):
            try:
                with self._lock:
                    if self._owner:self._owner.release()
            finally:self._cycle.release()
        return self.status()

    def shutdown(self):
        self.stop('APPLICATION_SHUTDOWN')
        thread=self._thread
        if thread and thread is not threading.current_thread():thread.join(timeout=10)

    def _pause(self,reason,phase='PAUSED'):
        with self._lock:self._stop.set();self._phase=phase;self._reason=reason

    def _before_train(self):
        with self._lock:
            if self._stop.is_set() or not self._admission():raise ValueError('REFRESH_STOPPED')
            if self._fits>=MAX_FITS:raise ValueError('REFRESH_FIT_BUDGET')
            self._fits+=1

    def _publish(self,product,g,sha):
        # Stop and publication serialize on this lock: no reference update after
        # stop acknowledgment. Computation/sealed candidate files may finish.
        with self._lock:
            if self._stop.is_set() or not self._admission():raise ValueError('REFRESH_STOPPED')
            if hub.root().resolve()!=self._root:raise ValueError('REFRESH_DIRECTORY_CHANGED')
            now=self.clock()
            if type(now) not in (int,float) or not math.isfinite(now) or (self._last_clock is not None and now<self._last_clock):raise ValueError('REFRESH_CLOCK_FAULT')
            if self._config and self._config.get('collect_public_history'):self._collection_clock()
            hub.register_model(product,g,sha)

    def _collection_clock(self):
        mono=self.clock();wall=self.wall_clock()
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (mono,wall)) or wall<=600*self._config['granularity'] or (self._last_clock is not None and mono<self._last_clock):
            raise ValueError('REFRESH_CLOCK_FAULT')
        if self._wall_anchor is None:self._wall_anchor=(wall,mono)
        old_wall,old_mono=self._wall_anchor
        if abs((wall-old_wall)-(mono-old_mono))>2:raise ValueError('REFRESH_CLOCK_FAULT')
        return wall

    def _collect_public(self,config):
        with self._lock:
            if self._stop.is_set() or not self._admission():raise ValueError('REFRESH_STOPPED')
            end=int((self._collection_clock()-collection.SETTLE_SECONDS)//config['granularity'])*config['granularity']
            if self._frontier is not None and end<self._frontier:raise ValueError('REFRESH_CLOCK_FAULT')
            if end==self._frontier:return
        pages=4 if self._frontier is None else 1
        for page in range(pages):
            with self._lock:
                if self._stop.is_set() or not self._admission():raise ValueError('REFRESH_STOPPED')
                if hub.root().resolve()!=self._root:raise ValueError('REFRESH_DIRECTORY_CHANGED')
                self._collection_clock();collection.preflight()
                if self._source_requests>=collection.MAX_SOURCE_REQUESTS:raise ValueError('REFRESH_SOURCE_BUDGET')
                # Admission is charged before network, including failures. A
                # request admitted before stop may finish; its body is discarded.
                self._source_requests+=1;self._phase='COLLECTING'
            cutoff=end-page*150*config['granularity']
            try:rows=collection.read_window(config['product'],config['granularity'],cutoff,self.transport)
            except Exception:raise ValueError('REFRESH_SOURCE_FAILED') from None
            with self._lock:
                if self._stop.is_set() or not self._admission():raise ValueError('REFRESH_STOPPED')
                if hub.root().resolve()!=self._root:raise ValueError('REFRESH_DIRECTORY_CHANGED')
                received=self._collection_clock()
                if rows:hub.ingest(config['product'],config['granularity'],rows,now=received)
        with self._lock:self._frontier=end

    def step(self):
        self._step_once()
        return self.status()

    def _step_once(self):
        if not self._cycle.acquire(False):return None
        admitted=False
        try:
            with self._lock:
                if self._stop.is_set():return None
                now=self.clock()
                if type(now) not in (int,float) or not math.isfinite(now) or (self._last_clock is not None and now<self._last_clock):
                    self._pause('CLOCK_FAULT');return None
                if not self._admission():self._pause('PORTAL_INACTIVE');return None
                if now<self._next_due:return None
                self._last_clock=now;self._next_due=now+INTERVAL_SECONDS;self._polls+=1
                config=dict(self._config);self._phase='CHECKING';self._reason=None
            if hub.root().resolve()!=self._root:raise ValueError('REFRESH_DIRECTORY_CHANGED')
            admitted=BUILD_LOCK.acquire(False)
            if not admitted:
                with self._lock:self._busy+=1;self._phase='WAITING';self._reason='RESEARCH_JOB_BUSY'
                return None
            if config['collect_public_history']:self._collect_public(config)
            try:hub.import_local(config['product'],config['granularity'],config['limit'])
            except ValueError as exc:
                if str(exc)!='COLLECT_LOCAL_CANDLES_FIRST':raise
            with self._lock:
                if self._stop.is_set():return None
            generation=hub.generation(config['product'],config['granularity'])
            if generation==self._generation:
                with self._lock:self._skips+=1;self._phase='WAITING';self._reason='DATA_UNCHANGED'
                return None
            with self._lock:self._phase='TRAINING'
            result=hub_prediction.prepare(config['product'],config['granularity'],config['limit'],import_cache=False,publisher=self._publish,before_train=self._before_train)
            with self._lock:
                # A completed pre-stop publish is valid but must not erase a stop.
                self._generation=result['model']['dataset_generation'];self._model=result['model_artifact']['content_sha256']
                if result['model_reused']:self._reuses+=1
                if not self._stop.is_set():self._phase='WAITING';self._reason=None
        except ValueError as exc:
            code=str(exc)
            if code in ('HUB_NO_DATA','HUB_MODEL_REQUIRES_240_TO_10000_BARS','HUB_SNAPSHOT_REQUIRES_DATA'):
                with self._lock:
                    if not self._stop.is_set():self._phase='WAITING';self._reason='WAITING_FOR_STORED_DATA'
            elif code=='HUB_DATA_CHANGED_DURING_TRAINING':
                with self._lock:
                    if not self._stop.is_set():self._phase='WAITING';self._reason='DATA_CHANGED_RETRY_LATER'
            elif code=='REFRESH_COLLECTION_CAPACITY':self._pause('COLLECTION_CAPACITY_REACHED')
            elif code=='REFRESH_SOURCE_FAILED':self._pause('PUBLIC_SOURCE_FAILED')
            elif code=='REFRESH_SOURCE_BUDGET':self._pause('SOURCE_REQUEST_BUDGET_REACHED','COMPLETE')
            elif code=='REFRESH_CLOCK_FAULT':self._pause('CLOCK_FAULT')
            elif code=='REFRESH_STOPPED':
                if not self._stop.is_set():self._pause('PORTAL_INACTIVE')
            else:self._pause('DATA_DIRECTORY_CHANGED' if code=='REFRESH_DIRECTORY_CHANGED' else 'DATA_OR_MODEL_REVIEW_REQUIRED')
        except Exception:self._pause('STORAGE_OR_MODEL_FAILURE')
        finally:
            if admitted:BUILD_LOCK.release()
            with self._lock:
                if not self._stop.is_set() and (self._fits>=MAX_FITS or self._polls>=MAX_POLLS or self._source_requests>=collection.MAX_SOURCE_REQUESTS):self._pause('SESSION_BUDGET_REACHED','COMPLETE')
                if self._stop.is_set():
                    if self._owner:self._owner.release()
                    if self._phase=='STOPPING':self._phase='STOPPED'
            self._cycle.release()
        return None

    def _run(self):
        try:
            while not self._stop.is_set():
                self.step()
                if not self._stop.is_set():self._stop.wait(INTERVAL_SECONDS)
        finally:
            with self._lock:
                if not self._stop.is_set():self._pause('WORKER_EXITED')
                if self._owner:self._owner.release()


refresh_worker=ResearchRefresh()
