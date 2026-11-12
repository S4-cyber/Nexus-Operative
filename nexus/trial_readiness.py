"""Explicit local coverage/quote-capacity review, never session admission."""
from contextlib import contextmanager
from datetime import datetime,timezone
import math
import os
import stat
import time
from pathlib import Path
from nexus import forward_trial_review as trial,forward_evidence
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import SESSION_LIMIT
from nexus.paper_policy import POLICY
from nexus.sqlite_journal_guard import (capture_file_fingerprints,
    capture_file_identities,check_existing,check_path_set,
    require_file_fingerprints,require_file_identities)

HOURS=(1,24,72,168)
MAX_FILE_BYTES=1024*1024*1024
FILE_PREFIX='TRIAL_READINESS_'


class _ReadinessFiles:
    """Bind one storage root and every file used by a readiness result.

    This is a sampled local evidence fence, not a lock or atomic snapshot.  It
    prevents one public diagnostic from acknowledging a mixture of file
    generations and also binds absence: a file that appears during review is
    a changed result, not still "no retained history".
    """
    def __init__(self,root,paths):
        self.configured_root=Path(root)
        try:observed=self.configured_root.lstat()
        except FileNotFoundError:
            self.root_observed=None;self.root=self.configured_root
        except OSError:self._fail('STORAGE_ROOT_CHANGED')
        else:
            if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
                self._fail('STORAGE_ROOT_CHANGED')
            try:
                resolved=self.configured_root.resolve(strict=True)
                after=self.configured_root.lstat()
            except OSError:self._fail('STORAGE_ROOT_CHANGED')
            if (stat.S_ISLNK(after.st_mode) or not stat.S_ISDIR(after.st_mode) or
                    not os.path.samestat(observed,after)):
                self._fail('STORAGE_ROOT_CHANGED')
            self.root_observed=observed;self.root=resolved
        self.paths=tuple(Path(path) for path in paths)
        self.present=tuple(self._present(path) for path in self.paths)
        existing=tuple(path for path,present in zip(self.paths,self.present) if present)
        self.existing=existing
        self.identities=(capture_file_identities(existing,FILE_PREFIX) if existing else ())
        self.fingerprints=(capture_file_fingerprints(existing,
            (MAX_FILE_BYTES,)*len(existing),FILE_PREFIX) if existing else ())

    @staticmethod
    def _fail(code):raise ValueError(FILE_PREFIX+code) from None

    @staticmethod
    def _rollback_clear(path):
        try:Path(str(path)+'-journal').lstat()
        except FileNotFoundError:return
        except OSError:_ReadinessFiles._fail('FILE_SET_UNAVAILABLE')
        _ReadinessFiles._fail('SQLITE_SIDECAR_REVIEW_REQUIRED')

    def _present(self,path):
        self._rollback_clear(path)
        return check_existing(path,MAX_FILE_BYTES,FILE_PREFIX)

    def require_current(self):
        try:current=self.configured_root.lstat()
        except FileNotFoundError:
            if self.root_observed is not None:self._fail('STORAGE_ROOT_CHANGED')
        except OSError:self._fail('STORAGE_ROOT_CHANGED')
        else:
            if (self.root_observed is None or stat.S_ISLNK(current.st_mode) or
                    not stat.S_ISDIR(current.st_mode) or
                    not os.path.samestat(self.root_observed,current)):
                self._fail('STORAGE_ROOT_CHANGED')
        try:present=tuple(self._present(path) for path in self.paths)
        except ValueError:self._fail('FILE_SET_CHANGED')
        if present!=self.present:self._fail('FILE_SET_CHANGED')
        if self.existing:
            try:
                require_file_identities(self.existing,self.identities,FILE_PREFIX)
                require_file_fingerprints(self.existing,
                    (MAX_FILE_BYTES,)*len(self.existing),self.fingerprints,FILE_PREFIX)
            except ValueError:self._fail('FILE_SET_CHANGED')


@contextmanager
def _readiness_scope(root,paths):
    binding=_ReadinessFiles(root,paths)
    try:yield
    except BaseException as error:
        if not isinstance(error,Exception):raise
        binding.require_current()
        raise
    binding.require_current()

def build(path,*,hours=72,now=None,order_gateway=None):
    if type(hours) is not int or hours not in HOURS:raise ValueError('INVALID_TRIAL_WINDOW')
    now=time.time() if now is None else now
    if type(now) not in (int,float) or not math.isfinite(now) or not 7*86400<now<253402300799:
        raise ValueError('INVALID_TRIAL_CLOCK')
    path=Path(path);root=path.parent
    order_path=root/'order_lifecycle.sqlite3'
    gateway=order_gateway or DurableOrderGateway(order_path)
    gateway_path=getattr(gateway,'_path',None)
    if callable(gateway_path):
        order_path=Path(gateway_path())
        if order_path.parent.resolve()!=root.resolve():
            raise ValueError(FILE_PREFIX+'STORAGE_ROOT_MISMATCH')
        paths=tuple(check_path_set(
            (path,order_path,root/'paper_portfolio.sqlite3'),FILE_PREFIX))
    else:paths=(path,)
    with _readiness_scope(root,paths):
        end=int(now);seconds=hours*3600
        utc=lambda value:datetime.fromtimestamp(value,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        after,until=utc(end-seconds),utc(end)
        present=trial.existing_history(path)
        review=trial.review(path,after=after,until=until) if present else None
        retained=review['retained_observations'] if review else 0
        sessions=review['retained_sessions'] if review else 0
        remaining=max(0,forward_evidence.LIMIT-retained)
        needed=math.ceil(seconds/POLICY['interval_seconds'])+1
        orders=gateway.recovery_report();capacity=orders['capacity']
        account=orders['execution_account']['account'] or {}
        retained_position=bool(account.get('positions'))
        # Mirror the runner's per-cycle admission reserve. This deliberately assumes
        # every cycle could need the larger entry/exit budget; it is a bound, not a
        # strategy forecast or a reason to delete retained history.
        writes_per_cycle=4 if retained_position else 12
        intents_per_cycle=1 if retained_position else 2
        forecast_commands=needed*writes_per_cycle
        forecast_intents=needed*intents_per_cycle
        return dict(mode='READ_ONLY_PAPER_TRIAL_READINESS',read_only=True,
        status='REVIEW_REQUIRED' if present else 'NO_RETAINED_HISTORY',hours=hours,after=after,until=until,
        minimum_trial_hours=72,history_present=present,review=review,
        quote_capacity=dict(retained=retained,limit=forward_evidence.LIMIT,remaining=remaining,
            estimated_additional_observations=needed,policy_interval_seconds=POLICY['interval_seconds'],
            room_for_additional_requested_window=remaining>=needed,
            scope='Quote records only, estimated at policy cadence; no reservation, uptime, order/ID/session/disk capacity or exit guarantee. No pruning or automatic repair.'),
        session_capacity=dict(retained=sessions,limit=SESSION_LIMIT,remaining=max(0,SESSION_LIMIT-sessions),
            additional_sessions_required=1,room_for_additional_session=sessions<SESSION_LIMIT,
            scope='One additional local session record only; no ownership, uptime, cleanup or automatic-start guarantee.'),
        order_capacity=dict(journal_present=orders['journal_present'],retained_position=retained_position,
            ordinary_commands_remaining=capacity['ordinary_commands_remaining'],intents_remaining=capacity['intents_remaining'],
            commands_per_cycle_upper_bound=writes_per_cycle,intents_per_cycle_upper_bound=intents_per_cycle,
            estimated_cycle_observations=needed,estimated_upper_bound_commands=forecast_commands,
            estimated_upper_bound_intents=forecast_intents,
            next_cycle_reserve_clear=(capacity['ordinary_commands_remaining']>=writes_per_cycle and
                capacity['intents_remaining']>=intents_per_cycle and capacity['lifecycle_intake_allowed']),
            room_for_conservative_requested_window=(capacity['ordinary_commands_remaining']>=forecast_commands and
                capacity['intents_remaining']>=forecast_intents and capacity['lifecycle_intake_allowed']),
            scope='Conservative local upper bound using the runner admission reserve on every policy-cadence cycle. It is not an expected trade count, a storage reservation, strategy evidence or permission. Logical archival preserves retained commands and cannot create indefinite capacity.'),
        **{flag:False for flag in trial.FLAGS},
        limitations='Local sampled operational review only. The forward/order/portfolio file-set fence is not a lock or atomic snapshot. Clear quote, session or order capacity estimates and a declared coverage target never accept a sustained trial, strategy, source, account, broker or release. Stop for stable final evidence. No engine/session starts, vault/provider access, repairs or account writes.')
