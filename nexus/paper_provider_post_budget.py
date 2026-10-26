"""Durable local provider-scope pacing for explicit paper order POSTs.

Callers coordinate only when they deliberately share this private journal path.
The budget does not discover other clients, reserve a broker quota, or grant
account, routing, funding, live-trading, or release authority.
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
from nexus.broker_lookup_policy import clock
from nexus.sqlite_journal_guard import check_existing

SCHEMA = 'nexus.paper-provider-post-budget.v1'
RECOVERY_POLICY = 'nexus.paper-provider-post-budget-resolved-unknown.v1'
MAX_ATTEMPTS = 256
MAX_RECOVERIES = MAX_ATTEMPTS
MAX_DATABASE_BYTES = 2 * 1024 * 1024
DB_BUSY_TIMEOUT_MS = 10_000
MAX_EVENT_BYTES = 1024
MIN_COOLDOWN = 60
UNKNOWN_COOLDOWN = 86400
ZERO = '0' * 64
OUTCOMES = frozenset(('RESPONSE_OBSERVED', 'NOT_SENT', 'OUTCOME_UNKNOWN'))


def _fail(code):
    raise ValueError('PAPER_PROVIDER_POST_BUDGET_' + code) from None


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


class PaperProviderPostBudget:
    """Hash-chained POST admission journal shared by cooperating callers."""

    def __init__(self, path, provider_scope='ALPACA_PAPER_ORDERS'):
        if (type(provider_scope) is not str or
                re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', provider_scope) is None):
            _fail('PROVIDER_SCOPE_INVALID')
        self.path = Path(path)
        self.provider_scope = provider_scope
        self.provider_digest = hashlib.sha256(provider_scope.encode()).hexdigest()
        self._active_completion = None

    def _path_check(self):
        check_existing(self.path, MAX_DATABASE_BYTES, 'PAPER_PROVIDER_POST_BUDGET_')

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
        if len(encoded.encode()) > MAX_EVENT_BYTES: _fail('EVENT_CAP')
        digest = sha(dict(seq=seq, body=body, previous=previous))
        db.execute('INSERT INTO events VALUES (?,?,?,?)', (seq, encoded, previous, digest))
        return digest

    @staticmethod
    def _empty():
        return dict(head_sha256=ZERO, events=0, attempts=0, pending=None,
                    last_evaluated_at=0, next_allowed_at=0, review_required=False,
                    last_outcome=None, completed=[], recoveries=0,
                    recovery_history=[], last_recovery=None)

    @staticmethod
    def _admit(state, now):
        if state['pending'] is not None: _fail('PENDING_REVIEW')
        if state['review_required']: _fail('BLOCKED_REVIEW')
        if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
        if now < state['next_allowed_at']: _fail('COOLDOWN')
        if state['attempts'] >= MAX_ATTEMPTS: _fail('ATTEMPT_CAP')

    def _load(self, db):
        max_events = 2 * MAX_ATTEMPTS + MAX_RECOVERIES
        rows = db.execute('SELECT seq,body,previous,digest FROM events ORDER BY seq LIMIT ?',
                          (max_events + 2,)).fetchall()
        if not rows: _fail('BINDING_MISSING')
        if len(rows) > max_events + 1: _fail('EVENT_CAP')
        state = self._empty()
        for index, (seq, encoded, previous, digest) in enumerate(rows):
            if type(encoded) is not str or len(encoded.encode()) > MAX_EVENT_BYTES: _fail('EVENT_CAP')
            try: body = json.loads(encoded)
            except (TypeError, ValueError): _fail('PAYLOAD_INVALID')
            if (seq != index or previous != state['head_sha256'] or canonical(body) != encoded or
                    digest != sha(dict(seq=seq, body=body, previous=previous))):
                _fail('CHAIN_DISAGREEMENT')
            if index == 0:
                if body != dict(schema=SCHEMA, provider_sha256=self.provider_digest):
                    _fail('PROVIDER_OR_SCHEMA')
            elif type(body) is not dict:
                _fail('PAYLOAD_INVALID')
            elif body.get('kind') == 'BEGIN' and set(body) == {
                    'kind','evaluated_at','account_sha256','request_before_head_sha256',
                    'payload_sha256','client_order_sha256','owner_sha256'}:
                now = clock(body['evaluated_at']); self._admit(state, now)
                for key in ('account_sha256','request_before_head_sha256','payload_sha256',
                            'client_order_sha256','owner_sha256'):
                    _digest(body[key], key.upper() + '_INVALID')
                state['pending'] = dict(token=digest, **{key: body[key] for key in (
                    'account_sha256','request_before_head_sha256','payload_sha256',
                    'client_order_sha256','owner_sha256')})
                state['attempts'] += 1; state['last_evaluated_at'] = now
            elif body.get('kind') == 'RESULT' and set(body) == {
                    'kind','evaluated_at','token','outcome','request_after_head_sha256','actual_posts'}:
                now = clock(body['evaluated_at']); pending = state['pending']
                if pending is None or body['token'] != pending['token']: _fail('TOKEN_DISAGREEMENT')
                if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                outcome = body['outcome']; posts = body['actual_posts']
                _digest(body['request_after_head_sha256'], 'REQUEST_HEAD_INVALID')
                if outcome not in OUTCOMES: _fail('OUTCOME_INVALID')
                if (outcome == 'NOT_SENT') != (posts == 0) or type(posts) is not int or posts not in (0, 1):
                    _fail('POST_COUNT_INVALID')
                state['completed'].append(dict(**pending, after_head_sha256=digest,
                    request_after_head_sha256=body['request_after_head_sha256'], outcome=outcome,
                    actual_posts=posts, completed_at=now))
                state['pending'] = None; state['last_evaluated_at'] = now
                state['last_outcome'] = outcome
                state['next_allowed_at'] = now + (UNKNOWN_COOLDOWN if outcome == 'OUTCOME_UNKNOWN' else MIN_COOLDOWN)
                state['review_required'] = outcome == 'OUTCOME_UNKNOWN'
            elif body.get('kind') == 'INTERRUPTED' and set(body) == {
                    'kind','evaluated_at','token','expected_head','owner_sha256',
                    'confirm_interrupted'}:
                now = clock(body['evaluated_at']); pending = state['pending']
                if body['confirm_interrupted'] is not True: _fail('CONFIRMATION_REQUIRED')
                if body['expected_head'] != previous: _fail('HEAD_DISAGREEMENT')
                if pending is None or body['token'] != pending['token']: _fail('TOKEN_DISAGREEMENT')
                if body['owner_sha256'] != pending['owner_sha256']: _fail('OWNER_SCOPE_CHANGED')
                if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                state['completed'].append(dict(**pending, after_head_sha256=digest,
                    request_after_head_sha256=None, outcome='INTERRUPTED_UNKNOWN',
                    actual_posts=None, completed_at=now))
                state['pending'] = None; state['last_evaluated_at'] = now
                state['last_outcome'] = 'INTERRUPTED_UNKNOWN'
                state['next_allowed_at'] = now + UNKNOWN_COOLDOWN
                state['review_required'] = True
            elif body.get('kind') == 'RECOVERY' and set(body) == {
                    'kind','policy','evaluated_at','expected_head','account_sha256',
                    'payload_sha256','client_order_sha256','blocked_attempt_head_sha256',
                    'blocked_outcome','request_resolution_head_sha256','owner_sha256',
                    'confirm_original_id_resolved','confirm_no_resubmission'}:
                now = clock(body['evaluated_at'])
                if body['policy'] != RECOVERY_POLICY: _fail('RECOVERY_POLICY_INVALID')
                if body['expected_head'] != previous: _fail('HEAD_DISAGREEMENT')
                if state['pending'] is not None: _fail('PENDING_REVIEW')
                if not state['review_required'] or not state['completed']:
                    _fail('RECOVERY_NOT_REQUIRED')
                if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                last = state['completed'][-1]
                for key in ('account_sha256','payload_sha256','client_order_sha256',
                            'blocked_attempt_head_sha256','request_resolution_head_sha256',
                            'owner_sha256'):
                    _digest(body[key], key.upper() + '_INVALID')
                if (last['outcome'] != 'OUTCOME_UNKNOWN' or
                        body['account_sha256'] != last['account_sha256'] or
                        body['payload_sha256'] != last['payload_sha256'] or
                        body['client_order_sha256'] != last['client_order_sha256'] or
                        body['blocked_attempt_head_sha256'] != last['after_head_sha256'] or
                        body['blocked_outcome'] != last['outcome'] or
                        body['request_resolution_head_sha256'] == last['request_after_head_sha256']):
                    _fail('RECOVERY_SCOPE_DISAGREEMENT')
                if (body['confirm_original_id_resolved'] is not True or
                        body['confirm_no_resubmission'] is not True):
                    _fail('CONFIRMATION_REQUIRED')
                if state['recoveries'] >= MAX_RECOVERIES: _fail('RECOVERY_CAP')
                state['review_required'] = False; state['recoveries'] += 1
                state['last_evaluated_at'] = now
                recovery = dict(recovered_at=now,
                    account_sha256=body['account_sha256'],
                    payload_sha256=body['payload_sha256'],
                    client_order_sha256=body['client_order_sha256'],
                    blocked_attempt_head_sha256=last['after_head_sha256'],
                    request_resolution_head_sha256=body['request_resolution_head_sha256'],
                    recovery_head_sha256=digest)
                state['recovery_history'].append(recovery)
                state['last_recovery'] = dict(recovery)
            elif index:
                _fail('PAYLOAD_INVALID')
            state['head_sha256'] = digest; state['events'] = index
        return state

    def inspect(self):
        self._path_check()
        if not self.path.exists(): state = self._empty()
        else:
            with closing(self._connect()) as db:
                with _transaction(db):
                    db.execute('BEGIN'); state = self._load(db)
        completed = state.pop('completed'); state.pop('recovery_history')
        pending = state.pop('pending')
        return dict(state, provider_sha256=self.provider_digest,
            completed_attempts=len(completed), pending_token=None if pending is None else pending['token'],
            min_post_cooldown_seconds=MIN_COOLDOWN, unknown_cooldown_seconds=UNKNOWN_COOLDOWN,
            recovery_policy=RECOVERY_POLICY,
            automatic_retry=False, automatic_recovery=False, uncertainty_preserved=True,
            local_provider_scope_coordinated=bool(completed), broker_wide_quota_verified=False,
            distributed_ownership_verified=False, ready_for_routing=False, release_approved=False,
            limitations='One explicitly shared local POST journal for cooperating callers only; it cannot discover another path, credential, host, process or machine and does not attest broker quotas, account acceptance, fills, fees, holds, funds, routing or release.')

    def history(self):
        self._path_check()
        if not self.path.exists(): return ()
        with closing(self._connect()) as db:
            with _transaction(db):
                db.execute('BEGIN'); state = self._load(db)
        return tuple(dict(row) for row in state['completed'])

    def recovery_history(self):
        """Return content-free recovery bindings after full chain replay."""
        self._path_check()
        if not self.path.exists(): return ()
        with closing(self._connect()) as db:
            with _transaction(db):
                db.execute('BEGIN'); state = self._load(db)
        return tuple(dict(row) for row in state['recovery_history'])

    def ownership(self):
        return _PostOwnership(self)

    def _owned(self, owner):
        if type(owner) is not _PostOwnership or owner.budget is not self: _fail('OWNER_REQUIRED')
        owner.validate()

    def begin(self, *, account_id, request_before_head_sha256, payload_sha256,
              client_order_id, admission, ownership, expected_head):
        account = _uuid(account_id)
        request_head = _digest(request_before_head_sha256, 'REQUEST_HEAD_INVALID')
        payload = _digest(payload_sha256, 'PAYLOAD_DIGEST_INVALID')
        expected = _digest(expected_head, 'HEAD_INVALID')
        if type(client_order_id) is not str or not 1 <= len(client_order_id.encode()) <= 128:
            _fail('CLIENT_ORDER_ID_INVALID')
        self._owned(ownership)
        with closing(self._connect(write=True)) as db:
            with _transaction(db):
                state = self._load(db); self._owned(ownership); now = clock(admission())
                self._admit(state, now)
                # A missing journal is represented by ZERO during read-only
                # admission; initialization adds the immutable scope record.
                # Permit ZERO only while that remains the sole event.
                comparable = ZERO if state['events'] == 0 and state['attempts'] == 0 else state['head_sha256']
                if comparable != expected: _fail('HEAD_CHANGED')
                body = dict(kind='BEGIN', evaluated_at=now,
                    account_sha256=hashlib.sha256(account.encode()).hexdigest(),
                    request_before_head_sha256=request_head, payload_sha256=payload,
                    client_order_sha256=hashlib.sha256(client_order_id.encode()).hexdigest(),
                    owner_sha256=ownership.identity())
                token = self._append(db, state['events'] + 1, body, state['head_sha256']); self._load(db)
                if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                self._owned(ownership)
            self._active_completion = (token, ownership, ownership.lease)
            return token

    def finish(self, token, *, outcome, request_after_head_sha256, actual_posts,
               admission, ownership):
        after = _digest(request_after_head_sha256, 'REQUEST_HEAD_INVALID'); self._owned(ownership)
        if outcome not in OUTCOMES: _fail('OUTCOME_INVALID')
        if type(actual_posts) is not int or actual_posts not in (0, 1): _fail('POST_COUNT_INVALID')
        if (outcome == 'NOT_SENT') != (actual_posts == 0): _fail('POST_COUNT_INVALID')
        with closing(self._connect(write=True)) as db:
            with _transaction(db):
                state = self._load(db); pending = state['pending']
                if pending is None or token != pending['token']: _fail('TOKEN_DISAGREEMENT')
                active = self._active_completion
                if active is None or active != (token, ownership, ownership.lease):
                    _fail('ORIGINAL_LEASE_REQUIRED')
                if pending['owner_sha256'] != ownership.identity(): _fail('OWNER_SCOPE_CHANGED')
                now = clock(admission())
                if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                body = dict(kind='RESULT', evaluated_at=now, token=token, outcome=outcome,
                    request_after_head_sha256=after, actual_posts=actual_posts)
                head = self._append(db, state['events'] + 1, body, state['head_sha256']); self._load(db)
                if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                self._owned(ownership)
            self._active_completion = None
            return head

    def interrupt(self, token, *, expected_head, confirm_interrupted=False, admission):
        """Convert one abandoned exact tail into retained unknown outcome."""
        _digest(token, 'TOKEN_INVALID'); expected = _digest(expected_head, 'HEAD_INVALID')
        if confirm_interrupted is not True: _fail('CONFIRMATION_REQUIRED')
        with self.ownership() as owner:
            with closing(self._connect(write=True)) as db:
                with _transaction(db):
                    state = self._load(db); self._owned(owner); now = clock(admission())
                    pending = state['pending']
                    if state['head_sha256'] != expected: _fail('HEAD_DISAGREEMENT')
                    if pending is None or pending['token'] != token: _fail('TOKEN_DISAGREEMENT')
                    if pending['owner_sha256'] != owner.identity(): _fail('OWNER_SCOPE_CHANGED')
                    body = dict(kind='INTERRUPTED', evaluated_at=now, token=token,
                        expected_head=expected, owner_sha256=owner.identity(),
                        confirm_interrupted=True)
                    head = self._append(db, state['events'] + 1, body, state['head_sha256'])
                    self._load(db)
                    if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                    self._owned(owner)
                return dict(head_sha256=head, interruption_recorded=True,
                    outcome='INTERRUPTED_UNKNOWN', uncertainty_preserved=True,
                    automatic_retry=False, automatic_recovery=False,
                    ready_for_routing=False, release_approved=False)

    def recover_resolved_unknown(self, *, expected_head, expected_account_id,
            expected_payload_sha256, expected_client_order_id,
            expected_request_resolution_head, resolution_check,
            confirm_original_id_resolved=False, confirm_no_resubmission=False,
            admission, ownership):
        """Clear one exact block only after a terminal original-ID proof.

        The caller-supplied check is sampled twice under this journal's write
        ownership.  It must return the exact content-free proof each time.
        """
        head = _digest(expected_head, 'HEAD_INVALID')
        account = _uuid(expected_account_id)
        payload = _digest(expected_payload_sha256, 'PAYLOAD_DIGEST_INVALID')
        request_head = _digest(expected_request_resolution_head, 'REQUEST_HEAD_INVALID')
        if type(expected_client_order_id) is not str or not 1 <= len(expected_client_order_id.encode()) <= 128:
            _fail('CLIENT_ORDER_ID_INVALID')
        if not callable(resolution_check): _fail('RESOLUTION_CHECK_REQUIRED')
        if confirm_original_id_resolved is not True or confirm_no_resubmission is not True:
            _fail('CONFIRMATION_REQUIRED')
        proof = dict(account_sha256=hashlib.sha256(account.encode()).hexdigest(),
            payload_sha256=payload,
            client_order_sha256=hashlib.sha256(expected_client_order_id.encode()).hexdigest(),
            request_resolution_head_sha256=request_head,
            recovery_state='OBSERVED_TERMINAL_RECONCILIATION_REQUIRED',
            lookup_by_original_client_id_required=False)
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
                if (last['outcome'] != 'OUTCOME_UNKNOWN' or
                        proof['account_sha256'] != last['account_sha256'] or
                        proof['payload_sha256'] != last['payload_sha256'] or
                        proof['client_order_sha256'] != last['client_order_sha256'] or
                        request_head == last['request_after_head_sha256']):
                    _fail('RECOVERY_SCOPE_DISAGREEMENT')
                if resolution_check() != proof: _fail('RESOLUTION_PROOF_DISAGREEMENT')
                body = dict(kind='RECOVERY', policy=RECOVERY_POLICY,
                    evaluated_at=now, expected_head=head,
                    account_sha256=proof['account_sha256'], payload_sha256=payload,
                    client_order_sha256=proof['client_order_sha256'],
                    blocked_attempt_head_sha256=last['after_head_sha256'],
                    blocked_outcome=last['outcome'],
                    request_resolution_head_sha256=request_head,
                    owner_sha256=ownership.identity(),
                    confirm_original_id_resolved=True, confirm_no_resubmission=True)
                recovered_head = self._append(db, state['events'] + 1, body, head)
                recovered = self._load(db)
                if resolution_check() != proof: _fail('RESOLUTION_PROOF_CHANGED')
                if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                self._owned(ownership)
            return dict(mode='PAPER_PROVIDER_POST_BUDGET_RESOLVED_UNKNOWN_RECOVERY',
                status='REVIEW_REQUIRED', head_sha256=recovered_head,
                request_resolution_head_sha256=request_head,
                blocked_outcome=last['outcome'], review_block_cleared=True,
                next_allowed_at=recovered['next_allowed_at'],
                cooldown_shortened=False, uncertainty_preserved=True,
                original_request_rearmed=False, history_rewritten=False,
                attempt_capacity_reclaimed=False, admission_reserved=False,
                automatic_retry=False, automatic_continuation=False,
                broker_wide_quota_verified=False, distributed_ownership_verified=False,
                broker_account_reconciled=False, ready_for_routing=False,
                live_orders_enabled=False, money_movement_enabled=False,
                release_approved=False)


class _PostOwnership:
    def __init__(self, budget):
        self.budget = budget
        self.path = budget.path.resolve().with_name(budget.path.name + '.owner.lock')
        self.fd = None; self.lease = None

    def validate(self):
        if self.fd is None or self.path.is_symlink(): _fail('OWNER_REQUIRED')
        current, opened = self.path.stat(), os.fstat(self.fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size != 1 or
                (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)):
            _fail('OWNER_FILE_CHANGED')

    def identity(self):
        self.validate(); info = os.fstat(self.fd)
        return sha(dict(journal_path=str(self.budget.path.resolve()),
                        lock_device=info.st_dev, lock_inode=info.st_ino))

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
        fd, self.fd = self.fd, None
        if fd is not None: os.close(fd)
