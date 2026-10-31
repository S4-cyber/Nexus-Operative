"""One explicit local provider-scope budget for cooperating paper GET readers.

The journal coordinates callers that deliberately share one path on one host.
It does not discover other clients, reserve an Alpaca quota, coordinate another
machine, or grant account, routing, funding, live-trading, or release authority.
"""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from nexus.alpaca_paper_contract import canonical, sha
from nexus.alpaca_paper_inspection import _uuid
from nexus.broker_lookup_policy import classify, clock
from nexus.paper_read_pacing import OPERATIONS, OPERATION_PATTERNS
from nexus.sqlite_journal_guard import check_existing

SCHEMA = 'nexus.paper-provider-read-budget.v1'
RECOVERY_POLICY = 'nexus.paper-provider-read-budget-recovery.v1'
MAX_SESSIONS = 256
MAX_RECOVERIES = MAX_SESSIONS
MAX_DATABASE_BYTES = 4 * 1024 * 1024
DB_BUSY_TIMEOUT_MS = 10_000
MIN_COOLDOWN = 60
INTERRUPTED_COOLDOWN = 86400
MAX_EVENT_BYTES = 1024
ZERO = '0' * 64


def _fail(code):
    raise ValueError('PAPER_PROVIDER_READ_BUDGET_' + code) from None


def _database_busy(error):
    code = getattr(error, 'sqlite_errorcode', None)
    return (code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or
        any(word in str(error).lower() for word in ('busy', 'locked')))


@contextmanager
def _transaction(db):
    """Normalize bounded SQLite operation/commit contention without retry."""
    try:
        with db:
            yield
    except sqlite3.OperationalError as error:
        if _database_busy(error):
            _fail('DATABASE_BUSY')
        raise


def _digest(value, code):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        _fail(code)
    return value


class PaperProviderReadBudget:
    """Hash-chained local budget shared across account pacing journals."""

    def __init__(self, path, provider_scope='ALPACA_PAPER'):
        if type(provider_scope) is not str or re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', provider_scope) is None:
            _fail('PROVIDER_SCOPE_INVALID')
        self.path = Path(path)
        self.provider_scope = provider_scope
        self.provider_digest = hashlib.sha256(provider_scope.encode()).hexdigest()
        self._active_completion = None

    def _path_check(self):
        check_existing(self.path, MAX_DATABASE_BYTES, 'PAPER_PROVIDER_READ_BUDGET_')

    def _connect(self, write=False):
        self._path_check(); new = False
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd); new = True
            except FileExistsError:
                pass
            self._path_check()
        db = sqlite3.connect(self.path.resolve().as_uri() + ('?mode=rw' if write else '?mode=ro'),
                             uri=True, timeout=DB_BUSY_TIMEOUT_MS / 1000)
        try:
            db.execute(f'PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}')
            if write:
                db.execute('PRAGMA journal_mode=DELETE'); db.execute('PRAGMA synchronous=FULL')
                page_size = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}')
                db.execute('BEGIN IMMEDIATE')
                if new:
                    db.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
                    self._append(db, 0, dict(schema=SCHEMA, provider_sha256=self.provider_digest), ZERO)
            return db
        except sqlite3.OperationalError as error:
            db.close()
            if _database_busy(error):
                _fail('DATABASE_BUSY')
            raise
        except BaseException:
            db.close()
            raise

    @staticmethod
    def _append(db, seq, body, previous):
        encoded = canonical(body)
        if len(encoded.encode()) > MAX_EVENT_BYTES:
            _fail('EVENT_CAP')
        digest = sha(dict(seq=seq, body=body, previous=previous))
        db.execute('INSERT INTO events VALUES (?,?,?,?)', (seq, encoded, previous, digest))
        return digest

    @staticmethod
    def _empty():
        return dict(head_sha256=ZERO, events=0, sessions=0, pending=None,
                    last_evaluated_at=0, next_allowed_at=0, review_required=False,
                    last_outcome=None, completed=[], interruptions=0,
                    recoveries=0, last_recovery=None)

    @staticmethod
    def _admit(state, now):
        if state['pending'] is not None: _fail('PENDING_REVIEW')
        if state['review_required']: _fail('BLOCKED_REVIEW')
        if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
        if now < state['next_allowed_at']: _fail('COOLDOWN')
        if state['sessions'] >= MAX_SESSIONS: _fail('SESSION_CAP')

    def _load(self, db):
        max_events = 2 * MAX_SESSIONS + MAX_RECOVERIES
        rows = db.execute('SELECT seq,body,previous,digest FROM events ORDER BY seq LIMIT ?',
                          (max_events + 2,)).fetchall()
        if not rows: _fail('BINDING_MISSING')
        if len(rows) > max_events + 1: _fail('EVENT_CAP')
        state = self._empty()
        for index, (seq, encoded, previous, digest) in enumerate(rows):
            if type(encoded) is not str or len(encoded.encode()) > MAX_EVENT_BYTES:
                _fail('EVENT_CAP')
            try: body = json.loads(encoded)
            except (TypeError, ValueError): _fail('PAYLOAD_INVALID')
            if (seq != index or previous != state['head_sha256'] or canonical(body) != encoded or
                    digest != sha(dict(seq=seq, body=body, previous=previous))):
                _fail('CHAIN_DISAGREEMENT')
            if index == 0:
                if body != dict(schema=SCHEMA, provider_sha256=self.provider_digest):
                    _fail('PROVIDER_OR_SCHEMA')
            else:
                if type(body) is not dict: _fail('PAYLOAD_INVALID')
                kind = body.get('kind')
                if kind == 'BEGIN' and set(body) == {'kind','evaluated_at','account_sha256','account_before_head_sha256','operation','reserved_gets','owner_sha256'}:
                    now = clock(body['evaluated_at']); self._admit(state, now)
                    _digest(body['account_sha256'], 'ACCOUNT_DIGEST_INVALID')
                    _digest(body['account_before_head_sha256'], 'ACCOUNT_HEAD_INVALID')
                    _digest(body['owner_sha256'], 'OWNER_MARKER_INVALID')
                    operation = body['operation']
                    if type(operation) is not str or operation not in OPERATIONS: _fail('OPERATION_INVALID')
                    reserved = max(map(len, OPERATION_PATTERNS[operation]))
                    if body['reserved_gets'] != reserved: _fail('RESERVATION_INVALID')
                    state['pending'] = dict(token=digest, owner_sha256=body['owner_sha256'],
                        account_sha256=body['account_sha256'], account_before_head_sha256=body['account_before_head_sha256'],
                        operation=operation, reserved_gets=reserved, started_at=now)
                    state['sessions'] += 1
                elif kind == 'RESULT' and set(body) == {'kind','evaluated_at','token','outcome','delay','account_after_head_sha256','actual_gets'}:
                    now = clock(body['evaluated_at']); pending = state['pending']
                    if pending is None or body['token'] != pending['token']: _fail('TOKEN_DISAGREEMENT')
                    if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                    outcome, delay = body['outcome'], body['delay']
                    if outcome not in ('SUCCESS','NOT_FOUND','TIMEOUT','NETWORK_ERROR','RATE_LIMITED','SERVER_ERROR','AUTH_DENIED','REVIEW_REQUIRED'):
                        _fail('OUTCOME_INVALID')
                    if type(delay) is not int or (outcome == 'RATE_LIMITED' and not 1 <= delay <= 86400) or (outcome != 'RATE_LIMITED' and delay != 0):
                        _fail('DELAY_INVALID')
                    _digest(body['account_after_head_sha256'], 'ACCOUNT_HEAD_INVALID')
                    actual = body['actual_gets']
                    lengths = tuple(map(len, OPERATION_PATTERNS[pending['operation']]))
                    if type(actual) is not int or not 1 <= actual <= pending['reserved_gets']:
                        _fail('ACTUAL_GETS_INVALID')
                    if outcome == 'SUCCESS' and actual not in lengths:
                        _fail('ACTUAL_GETS_INVALID')
                    state['completed'].append(dict(**pending, completed_at=now, after_head_sha256=digest,
                        account_after_head_sha256=body['account_after_head_sha256'], actual_gets=actual, outcome=outcome))
                    state['pending'] = None
                    state['last_outcome'] = outcome
                    state['review_required'] = outcome in ('AUTH_DENIED','REVIEW_REQUIRED')
                    state['next_allowed_at'] = now + max(MIN_COOLDOWN, delay)
                elif kind == 'INTERRUPTED' and set(body) == {'kind','evaluated_at','token','expected_head','confirm_interrupted'}:
                    now = clock(body['evaluated_at']); pending = state['pending']
                    if body['confirm_interrupted'] is not True: _fail('CONFIRMATION_REQUIRED')
                    if pending is None or body['token'] != pending['token']: _fail('TOKEN_DISAGREEMENT')
                    if body['expected_head'] != previous: _fail('HEAD_DISAGREEMENT')
                    if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                    state['completed'].append(dict(**pending, completed_at=now, after_head_sha256=digest,
                        account_after_head_sha256=None, actual_gets=None, outcome='INTERRUPTED_UNKNOWN'))
                    state['pending'] = None; state['last_outcome'] = 'INTERRUPTED_UNKNOWN'
                    state['next_allowed_at'] = max(state['next_allowed_at'], now + INTERRUPTED_COOLDOWN)
                    state['interruptions'] += 1; state['review_required'] = True
                elif kind == 'RECOVERY' and set(body) == {
                        'kind','policy','evaluated_at','expected_head','account_sha256',
                        'blocked_session_head_sha256','blocked_outcome','owner_sha256',
                        'confirm_block_reviewed','confirm_uncertainty_preserved'}:
                    now = clock(body['evaluated_at'])
                    if body['policy'] != RECOVERY_POLICY: _fail('RECOVERY_POLICY_INVALID')
                    if body['expected_head'] != previous: _fail('HEAD_DISAGREEMENT')
                    if state['pending'] is not None: _fail('PENDING_REVIEW')
                    if not state['review_required'] or not state['completed']:
                        _fail('RECOVERY_NOT_REQUIRED')
                    if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                    last = state['completed'][-1]
                    _digest(body['account_sha256'], 'ACCOUNT_DIGEST_INVALID')
                    _digest(body['blocked_session_head_sha256'], 'HEAD_INVALID')
                    _digest(body['owner_sha256'], 'OWNER_MARKER_INVALID')
                    if (body['account_sha256'] != last['account_sha256'] or
                            body['blocked_session_head_sha256'] != last['after_head_sha256'] or
                            body['blocked_outcome'] != last['outcome'] or
                            body['blocked_outcome'] not in ('AUTH_DENIED','REVIEW_REQUIRED','INTERRUPTED_UNKNOWN')):
                        _fail('RECOVERY_SCOPE_DISAGREEMENT')
                    if (body['confirm_block_reviewed'] is not True or
                            body['confirm_uncertainty_preserved'] is not True):
                        _fail('CONFIRMATION_REQUIRED')
                    if state['recoveries'] >= MAX_RECOVERIES: _fail('RECOVERY_CAP')
                    state['review_required'] = False
                    state['recoveries'] += 1
                    state['last_recovery'] = dict(recovered_at=now,
                        account_sha256=body['account_sha256'],
                        blocked_session_head_sha256=last['after_head_sha256'],
                        blocked_outcome=last['outcome'], recovery_head_sha256=digest)
                else:
                    _fail('PAYLOAD_INVALID')
                state['last_evaluated_at'] = now
            state['head_sha256'] = digest; state['events'] = index
        return state

    def inspect(self):
        self._path_check()
        if not self.path.exists(): state = self._empty()
        else:
            with closing(self._connect()) as db:
                with _transaction(db): state = self._load(db)
        completed = state.pop('completed'); pending = state.pop('pending')
        operation_counts = {name: sum(row['operation'] == name for row in completed) for name in OPERATIONS}
        account_count = len({row['account_sha256'] for row in completed})
        return dict(state, provider_sha256=self.provider_digest, operation_counts=operation_counts,
                    completed_sessions=len(completed), completed_account_count=account_count,
                    pending_token=None if pending is None else pending['token'],
                    pending_operation=None if pending is None else pending['operation'],
                    min_post_result_cooldown_seconds=MIN_COOLDOWN, automatic_retry=False,
                    recovery_policy=RECOVERY_POLICY, automatic_recovery=False,
                    uncertainty_preserved=True,
                    local_provider_scope_coordinated=bool(completed), broker_wide_quota_verified=False,
                    distributed_ownership_verified=False, ready_for_routing=False, release_approved=False,
                    limitations='One explicitly shared local provider-scope journal for cooperating callers only. It cannot discover another process using another path, credential, host or machine and does not attest provider quotas, source, coverage, funds, routing or release.')

    def history(self):
        self._path_check()
        if not self.path.exists(): return ()
        with closing(self._connect()) as db:
            with _transaction(db): state = self._load(db)
        return tuple(dict(row) for row in state['completed'])

    def preflight(self, *, admission, expected_head=None):
        if expected_head is not None: _digest(expected_head, 'HEAD_INVALID')
        self._path_check()
        if not self.path.exists(): state = self._empty()
        else:
            with closing(self._connect()) as db:
                with _transaction(db): state = self._load(db)
        now = clock(admission()); self._admit(state, now)
        if expected_head is not None and state['head_sha256'] != expected_head: _fail('HEAD_CHANGED')
        return state['head_sha256']

    def recovery_receipt(self):
        """Cold-replay the latest exact recovery without granting authority.

        The write-side return from :meth:`recover_review_block` is transport,
        not durable evidence.  This read-only receipt exposes only retained
        digests and conservative state needed to prove one exact unblock.
        """
        self._path_check()
        if not self.path.exists(): _fail('RECOVERY_MISSING')
        with closing(self._connect()) as db:
            with _transaction(db):
                db.execute('BEGIN'); state = self._load(db)
        recovery = state['last_recovery']
        if recovery is None: _fail('RECOVERY_MISSING')
        pending = state['pending']
        return dict(recovery, head_sha256=state['head_sha256'],
            recoveries=state['recoveries'], sessions=state['sessions'],
            last_evaluated_at=state['last_evaluated_at'],
            next_allowed_at=state['next_allowed_at'],
            review_required=state['review_required'],
            pending_token=None if pending is None else pending['token'],
            exact_recovery_replayed=True, read_only=True,
            uncertainty_preserved=True, history_rewritten=False,
            session_capacity_reclaimed=False, admission_reserved=False,
            automatic_retry=False, automatic_continuation=False,
            broker_wide_quota_verified=False,
            distributed_ownership_verified=False, ready_for_routing=False,
            live_orders_enabled=False, money_movement_enabled=False,
            release_approved=False)

    def ownership(self):
        return _ProviderOwnership(self)

    def _owned(self, owner):
        if type(owner) is not _ProviderOwnership or owner.budget is not self: _fail('OWNER_REQUIRED')
        owner.validate()

    def begin(self, *, account_id, account_before_head_sha256, operation, admission, ownership, expected_head=None):
        account = _uuid(account_id); account_head = _digest(account_before_head_sha256, 'ACCOUNT_HEAD_INVALID')
        if type(operation) is not str or operation not in OPERATIONS: _fail('OPERATION_INVALID')
        if expected_head is not None: _digest(expected_head, 'HEAD_INVALID')
        self._owned(ownership)
        with closing(self._connect(write=True)) as db:
            with _transaction(db):
                state = self._load(db); self._owned(ownership); now = clock(admission()); self._admit(state, now)
                if expected_head is not None and state['head_sha256'] != expected_head: _fail('HEAD_CHANGED')
                body = dict(kind='BEGIN', evaluated_at=now, account_sha256=hashlib.sha256(account.encode()).hexdigest(),
                    account_before_head_sha256=account_head, operation=operation,
                    reserved_gets=max(map(len, OPERATION_PATTERNS[operation])), owner_sha256=ownership.identity())
                token = self._append(db, state['events'] + 1, body, state['head_sha256']); self._load(db)
                if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                self._owned(ownership)
            self._active_completion = (token, ownership, ownership.lease)
            return token

    def finish(self, token, *, account_after_head_sha256, actual_gets, admission,
               ownership, status_code=None, transport_error=None, retry_after=None):
        after = _digest(account_after_head_sha256, 'ACCOUNT_HEAD_INVALID'); self._owned(ownership)
        with closing(self._connect(write=True)) as db:
            with _transaction(db):
                state = self._load(db); pending = state['pending']
                if pending is None or token != pending['token']: _fail('TOKEN_DISAGREEMENT')
                active = self._active_completion
                if active is None or active[0] != token or active[1] is not ownership or active[2] is not ownership.lease:
                    _fail('ORIGINAL_LEASE_REQUIRED')
                if pending['owner_sha256'] != ownership.identity(): _fail('OWNER_SCOPE_CHANGED')
                now = clock(admission())
                if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                result = classify(status_code=status_code, transport_error=transport_error,
                                  retry_after=retry_after, evaluated_at=now)
                outcome = 'SUCCESS' if result['outcome'] == 'FOUND' else result['outcome']
                body = dict(kind='RESULT', evaluated_at=now, token=token, outcome=outcome,
                    delay=result['retry_after_seconds'], account_after_head_sha256=after, actual_gets=actual_gets)
                head = self._append(db, state['events'] + 1, body, state['head_sha256']); self._load(db)
                if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                self._owned(ownership)
            self._active_completion = None
            return head

    def interrupt(self, token, *, expected_head, confirm_interrupted=False, admission):
        _digest(token, 'TOKEN_INVALID'); _digest(expected_head, 'HEAD_INVALID')
        if confirm_interrupted is not True: _fail('CONFIRMATION_REQUIRED')
        with self.ownership() as owner:
            with closing(self._connect(write=True)) as db:
                with _transaction(db):
                    state = self._load(db); self._owned(owner); now = clock(admission())
                    pending = state['pending']
                    if state['head_sha256'] != expected_head: _fail('HEAD_DISAGREEMENT')
                    if pending is None or pending['token'] != token: _fail('TOKEN_DISAGREEMENT')
                    if pending['owner_sha256'] != owner.identity(): _fail('OWNER_SCOPE_CHANGED')
                    body = dict(kind='INTERRUPTED', evaluated_at=now, token=token,
                                expected_head=expected_head, confirm_interrupted=True)
                    head = self._append(db, state['events'] + 1, body, state['head_sha256']); self._load(db)
                    if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                    self._owned(owner)
                return dict(head_sha256=head, interruption_recorded=True,
                            automatic_retry=False, ready_for_routing=False)

    def recover_review_block(self, *, expected_head, expected_account_id,
            confirm_block_reviewed=False, confirm_uncertainty_preserved=False,
            admission, ownership):
        """Append one reviewed unblock without shortening cooldown or erasing history."""
        head = _digest(expected_head, 'HEAD_INVALID')
        account = _uuid(expected_account_id)
        if confirm_block_reviewed is not True or confirm_uncertainty_preserved is not True:
            _fail('CONFIRMATION_REQUIRED')
        self._owned(ownership)
        with closing(self._connect(write=True)) as db:
            with _transaction(db):
                state = self._load(db); self._owned(ownership); now = clock(admission())
                if state['head_sha256'] != head: _fail('HEAD_DISAGREEMENT')
                if state['pending'] is not None: _fail('PENDING_REVIEW')
                if not state['review_required'] or not state['completed']:
                    _fail('RECOVERY_NOT_REQUIRED')
                if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                last = state['completed'][-1]
                account_sha256 = hashlib.sha256(account.encode()).hexdigest()
                if account_sha256 != last['account_sha256']:
                    _fail('RECOVERY_SCOPE_DISAGREEMENT')
                body = dict(kind='RECOVERY', policy=RECOVERY_POLICY,
                    evaluated_at=now, expected_head=head,
                    account_sha256=account_sha256,
                    blocked_session_head_sha256=last['after_head_sha256'],
                    blocked_outcome=last['outcome'], owner_sha256=ownership.identity(),
                    confirm_block_reviewed=True, confirm_uncertainty_preserved=True)
                recovered_head = self._append(db, state['events'] + 1, body, head)
                recovered = self._load(db)
                if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                self._owned(ownership)
            return dict(mode='PAPER_PROVIDER_READ_BUDGET_REVIEWED_RECOVERY',
                status='REVIEW_REQUIRED', head_sha256=recovered_head,
                blocked_outcome=last['outcome'], review_block_cleared=True,
                next_allowed_at=recovered['next_allowed_at'],
                cooldown_shortened=False, uncertainty_preserved=True,
                history_rewritten=False, session_capacity_reclaimed=False,
                admission_reserved=False, automatic_retry=False,
                automatic_continuation=False, broker_wide_quota_verified=False,
                distributed_ownership_verified=False, ready_for_routing=False,
                live_orders_enabled=False, money_movement_enabled=False,
                release_approved=False)


class _ProviderOwnership:
    def __init__(self, budget):
        self.budget = budget
        self.path = budget.path.resolve().with_name(budget.path.name + '.owner.lock')
        self.fd = None; self.lease = None

    def validate(self):
        if self.fd is None: _fail('OWNER_REQUIRED')
        try:
            if self.path.is_symlink(): _fail('OWNER_FILE_CHANGED')
            current, opened = self.path.stat(), os.fstat(self.fd)
        except OSError:
            _fail('OWNER_FILE_CHANGED')
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size != 1 or
                (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)):
            _fail('OWNER_FILE_CHANGED')

    def identity(self):
        self.validate(); info = os.fstat(self.fd)
        return sha(dict(journal_path=str(self.budget.path.resolve()), lock_device=info.st_dev, lock_inode=info.st_ino))

    def __enter__(self):
        if self.fd is not None: _fail('OWNER_ALREADY_HELD')
        self.budget._path_check()
        if self.path.is_symlink(): _fail('OWNER_SYMLINK')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode): _fail('OWNER_FILE_INVALID')
            if os.fstat(fd).st_size == 0: os.write(fd, b'0')
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd = fd; self.validate(); self.lease = object(); return self
        except BaseException as error:
            self.fd = None; os.close(fd)
            if isinstance(error, OSError): _fail('OWNER_BUSY_OR_UNAVAILABLE')
            raise

    def __exit__(self, *args):
        fd = self.fd
        if fd is None:
            return
        exit_error = None
        try:
            # Retain the opened ownership proof through caller finalization;
            # a replaced pathname must not turn a completed read into success.
            self.validate()
        except BaseException as error:
            exit_error = error
        finally:
            self.fd = None
            try:
                os.close(fd)
            except BaseException:
                if exit_error is None:
                    raise
        if exit_error is not None:
            raise exit_error
