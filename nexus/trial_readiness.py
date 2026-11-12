"""Explicit local coverage/quote-capacity review, never session admission."""
from contextlib import contextmanager,nullcontext
from datetime import datetime,timezone
import math
import os
import stat
import time
from pathlib import Path
from nexus import forward_trial_review as trial,forward_evidence
from nexus.durable_orders import DurableOrderGateway
from nexus.execution_capacity import CONTROL_RESERVE
from nexus.forward_ownership import ForwardOwnership
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
    def __init__(self,root,paths,*,identity_only_indexes=()):
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
        identity_only=frozenset(identity_only_indexes)
        self.identity_only=identity_only
        self.present=tuple(self._present(path,identity_only=index in identity_only)
            for index,path in enumerate(self.paths))
        # The owner marker is validated through the descriptor that holds its
        # byte-range lock.  Windows denies a second handle while that lock is
        # active, so the generic pathname-open identity helper must not reopen
        # it. ForwardOwnership still checks its exact path/descriptor identity,
        # marker byte, directory and finalization while the lease is held.
        existing=tuple(path for index,(path,present) in
            enumerate(zip(self.paths,self.present))
            if present and index not in identity_only)
        self.existing=existing
        self.identities=(capture_file_identities(existing,FILE_PREFIX) if existing else ())
        self.identity_only_identities={}
        for index in identity_only:
            if index>=len(self.paths) or not self.present[index]:continue
            identity=capture_file_identities((self.paths[index],),FILE_PREFIX)[0]
            self.identity_only_identities[index]=(identity[1],identity[2])
        self.fingerprinted=tuple(path for index,(path,present) in
            enumerate(zip(self.paths,self.present))
            if present and index not in identity_only)
        self.fingerprints=(capture_file_fingerprints(self.fingerprinted,
            (MAX_FILE_BYTES,)*len(self.fingerprinted),FILE_PREFIX)
            if self.fingerprinted else ())

    @staticmethod
    def _fail(code):raise ValueError(FILE_PREFIX+code) from None

    @staticmethod
    def _rollback_clear(path):
        try:Path(str(path)+'-journal').lstat()
        except FileNotFoundError:return
        except OSError:_ReadinessFiles._fail('FILE_SET_UNAVAILABLE')
        _ReadinessFiles._fail('SQLITE_SIDECAR_REVIEW_REQUIRED')

    def _present(self,path,*,identity_only=False):
        self._rollback_clear(path)
        if identity_only:
            for suffix in ('-wal','-shm'):
                try:Path(str(path)+suffix).lstat()
                except FileNotFoundError:continue
                except OSError:self._fail('FILE_UNAVAILABLE')
                self._fail('WAL_SIDECAR_REVIEW_REQUIRED')
            try:observed=path.lstat()
            except FileNotFoundError:return False
            except OSError:self._fail('FILE_UNAVAILABLE')
            if (stat.S_ISLNK(observed.st_mode) or
                    not stat.S_ISREG(observed.st_mode) or
                    observed.st_nlink!=1 or observed.st_size>MAX_FILE_BYTES):
                self._fail('FILE_UNAVAILABLE')
            return True
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
        try:present=tuple(self._present(path,
            identity_only=index in self.identity_only)
            for index,path in enumerate(self.paths))
        except ValueError:self._fail('FILE_SET_CHANGED')
        if present!=self.present:self._fail('FILE_SET_CHANGED')
        if self.existing:
            try:
                require_file_identities(self.existing,self.identities,FILE_PREFIX)
                if self.fingerprinted:
                    require_file_fingerprints(self.fingerprinted,
                        (MAX_FILE_BYTES,)*len(self.fingerprinted),
                        self.fingerprints,FILE_PREFIX)
            except ValueError:self._fail('FILE_SET_CHANGED')

    def require_identity_only_lease(self,index,lease):
        expected=self.identity_only_identities.get(index)
        if expected is None:self._fail('FILE_SET_CHANGED')
        try:lease.require_identity(expected)
        except ValueError:self._fail('FILE_SET_CHANGED')


@contextmanager
def _readiness_scope(root,paths,*,owner_index=None,history_index=0):
    binding=_ReadinessFiles(root,paths,
        identity_only_indexes=(() if owner_index is None else (owner_index,)))
    owner_present=owner_index is not None and binding.present[owner_index]
    history_present=binding.present[history_index]
    if owner_index is not None and history_present and not owner_present:
        binding.require_current();_ReadinessFiles._fail('OWNER_REVIEW_REQUIRED')
    owner=ForwardOwnership(root) if owner_present else None
    lease=owner.existing_lease() if owner_present else nullcontext()
    state=('LOCAL_OWNER_LEASE_HELD_DURING_REVIEW' if owner_present else
        'OWNER_PATH_ABSENCE_BOUND') if owner_index is not None else 'CUSTOM_GATEWAY_NOT_CHECKED'
    try:
        with lease:
            if owner_present:binding.require_identity_only_lease(owner_index,owner)
            try:yield state
            except BaseException as error:
                if not isinstance(error,Exception):raise
                binding.require_current()
                if owner_present:binding.require_identity_only_lease(owner_index,owner)
                raise
            binding.require_current()
            if owner_present:binding.require_identity_only_lease(owner_index,owner)
    except ValueError as error:
        if str(error).startswith('PAPER_FORWARD_'):
            binding.require_current();_ReadinessFiles._fail('OWNER_REVIEW_REQUIRED')
        raise

def build(path,*,hours=72,now=None,order_gateway=None):
    if type(hours) is not int or hours not in HOURS:raise ValueError('INVALID_TRIAL_WINDOW')
    now=time.time() if now is None else now
    if type(now) not in (int,float) or not math.isfinite(now) or not 7*86400<now<253402300799:
        raise ValueError('INVALID_TRIAL_CLOCK')
    path=Path(path);root=path.parent
    order_path=root/'order_lifecycle.sqlite3'
    gateway=order_gateway or DurableOrderGateway(order_path)
    gateway_path=getattr(gateway,'_path',None)
    owner_index=None
    if callable(gateway_path):
        order_path=Path(gateway_path())
        if order_path.parent.resolve()!=root.resolve():
            raise ValueError(FILE_PREFIX+'STORAGE_ROOT_MISMATCH')
        paths=tuple(check_path_set(
            (path,order_path,root/'paper_portfolio.sqlite3',
             root/'paper_forward.owner.lock'),FILE_PREFIX))
        # check_path_set preserves the reviewed order while resolving every
        # pathname.  Do not search that canonical tuple with the original
        # lexical owner path: Windows may normalize drive spelling, case or a
        # reparse alias during resolve(), making an equivalent path compare
        # differently.  The fourth checked path is the owner path by
        # construction, and binding by position keeps it inside the same
        # four-path fence without another normalization boundary.
        owner_index=3
        path=paths[0]
    else:paths=(path,)
    with _readiness_scope(root,paths,owner_index=owner_index) as owner_state:
        end=int(now);seconds=hours*3600
        utc=lambda value:datetime.fromtimestamp(value,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        after,until=utc(end-seconds),utc(end)
        present=trial.existing_history(path)
        review=trial.review(path,after=after,until=until) if present else None
        retained=review['retained_observations'] if review else 0
        sessions=review['retained_sessions'] if review else 0
        remaining=max(0,forward_evidence.LIMIT-retained)
        needed=math.ceil(seconds/POLICY['interval_seconds'])+1
        orders=gateway.recovery_report()
        try:
            capacity=orders['capacity']
            ordinary_remaining=capacity['ordinary_commands_remaining']
            intents_remaining=capacity['intents_remaining']
            total_remaining=capacity['total_commands_remaining']
            control_reserved=capacity['control_commands_reserved']
            lifecycle_allowed=capacity['lifecycle_intake_allowed']
        except (KeyError,TypeError):
            raise ValueError(FILE_PREFIX+'ORDER_CAPACITY_INVALID') from None
        if (any(type(value) is not int or value<0 for value in
                (ordinary_remaining,intents_remaining,total_remaining,
                 control_reserved)) or
                type(lifecycle_allowed) is not bool or
                control_reserved!=min(CONTROL_RESERVE,total_remaining)):
            raise ValueError(FILE_PREFIX+'ORDER_CAPACITY_INVALID')
        emergency_reserve_clear=(control_reserved==CONTROL_RESERVE)
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
            ordinary_commands_remaining=ordinary_remaining,intents_remaining=intents_remaining,
            total_commands_remaining=total_remaining,
            control_commands_reserved=control_reserved,
            required_control_command_reserve=CONTROL_RESERVE,
            emergency_control_reserve_clear=emergency_reserve_clear,
            commands_per_cycle_upper_bound=writes_per_cycle,intents_per_cycle_upper_bound=intents_per_cycle,
            estimated_cycle_observations=needed,estimated_upper_bound_commands=forecast_commands,
            estimated_upper_bound_intents=forecast_intents,
            next_cycle_reserve_clear=(ordinary_remaining>=writes_per_cycle and
                intents_remaining>=intents_per_cycle and lifecycle_allowed and
                emergency_reserve_clear),
            room_for_conservative_requested_window=(ordinary_remaining>=forecast_commands and
                intents_remaining>=forecast_intents and lifecycle_allowed and
                emergency_reserve_clear),
            scope='Conservative local upper bound using the runner admission reserve on every policy-cadence cycle. The fixed control reserve is reported separately for halt, cancel and expiry paths; it is not a guarantee that an operation will succeed. This is not an expected trade count, a storage reservation, strategy evidence or permission. Logical archival preserves retained commands and cannot create indefinite capacity.'),
        local_owner_quiescence=dict(checked=owner_index is not None,state=owner_state,
            retained_history_requires_owner_lock=True,active_owner_detected=False,
            cross_host_or_distributed_owner_checked=False,owner_file_created=False,
            scope='Existing local OS owner lease held across retained review, or owner-path absence bound while no history exists. This is not distributed ownership, an ACL, administrator isolation or execution permission.'),
        **{flag:False for flag in trial.FLAGS},
        limitations='Local sampled operational review only. The existing local owner lease prevents a cooperating paper-forward process from running during retained review, but the forward/order/portfolio file-set fence is not an atomic snapshot, distributed lock, ACL or administrator boundary. Clear quote, session or order capacity estimates and a declared coverage target never accept a sustained trial, strategy, source, account, broker or release. Stop for stable final evidence. No engine/session starts, vault/provider access, repairs or account writes.')
