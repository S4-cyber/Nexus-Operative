"""Explicit account-bound local paper-read reservations, never provider authority.

One <=4-GET session per journal, with a fixed 60-second recorded-result cooldown.
No default path, retries, expiry, repair, eviction or clearing of auth/review blocks.
Explicit owned interruption retains an unknown outcome and full-day cooldown.
Hashes detect inconsistent history, not valid-tail deletion or broker identity.
"""
from contextlib import closing
import hashlib
import json
import os
import re
import stat
from pathlib import Path
import sqlite3

from nexus.alpaca_paper_contract import canonical, sha
from nexus.alpaca_paper_inspection import _uuid
from nexus.broker_lookup_policy import classify, clock
from nexus.sqlite_journal_guard import check_existing

SCHEMA = 'nexus.paper-read-pacing.v1'
MAX_SESSIONS = 256
MAX_DATABASE_BYTES = 4 * 1024 * 1024
MIN_COOLDOWN = 60
INTERRUPTED_COOLDOWN = 86400
ZERO = '0' * 64


def _fail(code):
    raise ValueError('PAPER_READ_PACING_' + code)


class PaperReadPacing:
    def __init__(self, path, paper_account_id):
        self.path = Path(path)
        self.account = _uuid(paper_account_id)
        self.account_digest = hashlib.sha256(self.account.encode()).hexdigest()

    def _path_check(self):
        check_existing(self.path, MAX_DATABASE_BYTES, 'PAPER_READ_PACING_')

    def _connect(self, write=False):
        self._path_check(); new = False
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd); new = True
            except FileExistsError: pass
            self._path_check()
        db = sqlite3.connect(self.path.resolve().as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=5)
        try:
            if write:
                db.execute('PRAGMA journal_mode=DELETE'); db.execute('PRAGMA synchronous=FULL')
                size = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES // size}')
                db.execute('BEGIN IMMEDIATE')
                if new:
                    db.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
                    self._append(db, 0, dict(schema=SCHEMA, account_sha256=self.account_digest), ZERO)
            return db
        except BaseException:
            db.close(); raise

    @staticmethod
    def _append(db, seq, body, previous):
        encoded = canonical(body)
        if len(encoded.encode()) > 1024: _fail('EVENT_CAP')
        digest = sha(dict(seq=seq, body=body, previous=previous))
        db.execute('INSERT INTO events VALUES (?,?,?,?)', (seq, encoded, previous, digest))
        return digest

    def _load(self, db):
        rows = db.execute('SELECT seq,body,previous,digest FROM events ORDER BY seq LIMIT ?', (2 * MAX_SESSIONS + 2,)).fetchall()
        if not rows: _fail('BINDING_MISSING')
        if len(rows) > 2 * MAX_SESSIONS + 1: _fail('EVENT_CAP')
        state = dict(head_sha256=ZERO, events=0, sessions=0, pending_token=None,
                     last_evaluated_at=0, next_allowed_at=0, review_required=False, last_outcome=None,
                     pending_owner_fenced=False, pending_owner_sha256=None, pending_before_head_sha256=None,
                     completed_sessions=[], interruptions=0, last_interruption=None, last_interruption_head=None)
        for index, (seq, encoded, parent, digest) in enumerate(rows):
            if not isinstance(encoded, str) or len(encoded.encode()) > 1024: _fail('EVENT_CAP')
            try: body = json.loads(encoded)
            except (ValueError, TypeError): _fail('PAYLOAD_INVALID')
            if seq != index or parent != state['head_sha256'] or canonical(body) != encoded or digest != sha(dict(seq=seq, body=body, previous=parent)):
                _fail('CHAIN_DISAGREEMENT')
            if index == 0:
                if body != dict(schema=SCHEMA, account_sha256=self.account_digest): _fail('ACCOUNT_OR_SCHEMA')
            else:
                if not isinstance(body, dict): _fail('PAYLOAD_INVALID')
                kind = body.get('kind')
                if kind == 'BEGIN' and set(body) in ({'kind', 'evaluated_at'}, {'kind', 'evaluated_at', 'owner_fenced', 'owner_sha256'}):
                    if 'owner_fenced' in body and (body['owner_fenced'] is not True or not isinstance(body['owner_sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', body['owner_sha256']) is None): _fail('OWNER_MARKER_INVALID')
                    now = clock(body['evaluated_at'])
                    self._admit(state, now)
                    state['pending_token'] = digest
                    state['pending_before_head_sha256'] = ZERO if index==1 else parent
                    state['pending_owner_fenced'] = body.get('owner_fenced', False)
                    state['pending_owner_sha256'] = body.get('owner_sha256')
                    state['sessions'] += 1
                elif kind == 'RESULT' and set(body) == {'kind', 'evaluated_at', 'token', 'outcome', 'delay'}:
                    now = clock(body['evaluated_at'])
                    if state['pending_token'] is None or body['token'] != state['pending_token']: _fail('TOKEN_DISAGREEMENT')
                    if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                    outcome, delay = body['outcome'], body['delay']
                    if outcome not in ('SUCCESS', 'NOT_FOUND', 'TIMEOUT', 'NETWORK_ERROR', 'RATE_LIMITED', 'SERVER_ERROR', 'AUTH_DENIED', 'REVIEW_REQUIRED'): _fail('OUTCOME_INVALID')
                    if type(delay) is not int or (outcome == 'RATE_LIMITED' and not 1 <= delay <= 86400) or (outcome != 'RATE_LIMITED' and delay != 0): _fail('DELAY_INVALID')
                    state['pending_token'] = None
                    state['completed_sessions'].append(dict(before_head_sha256=state['pending_before_head_sha256'],
                        after_head_sha256=digest,outcome=outcome))
                    state['pending_before_head_sha256'] = None
                    state['pending_owner_fenced'] = False
                    state['pending_owner_sha256'] = None
                    state['review_required'] = outcome in ('AUTH_DENIED', 'REVIEW_REQUIRED')
                    state['next_allowed_at'] = now + max(MIN_COOLDOWN, delay)
                    state['last_outcome'] = outcome
                elif kind == 'INTERRUPTED' and set(body) == {'kind', 'evaluated_at', 'token', 'expected_head', 'confirm_interrupted'}:
                    now = clock(body['evaluated_at'])
                    if body['confirm_interrupted'] is not True: _fail('CONFIRMATION_REQUIRED')
                    if body['expected_head'] != parent: _fail('HEAD_DISAGREEMENT')
                    if state['pending_token'] is None or body['token'] != state['pending_token']: _fail('TOKEN_DISAGREEMENT')
                    if not state['pending_owner_fenced']: _fail('LEGACY_OWNER_UNVERIFIED')
                    if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                    state['pending_token'] = None
                    state['pending_before_head_sha256'] = None
                    state['pending_owner_fenced'] = False
                    state['pending_owner_sha256'] = None
                    state['next_allowed_at'] = max(state['next_allowed_at'], now + INTERRUPTED_COOLDOWN)
                    state['last_outcome'] = 'INTERRUPTED_UNKNOWN'
                    state['interruptions'] += 1
                    state['last_interruption'] = body
                    state['last_interruption_head'] = digest
                else: _fail('PAYLOAD_INVALID')
                state['last_evaluated_at'] = now
            state['head_sha256'] = digest
            state['events'] = index
        return state

    @staticmethod
    def _admit(state, now):
        if state['pending_token'] is not None: _fail('PENDING_REVIEW')
        if state['review_required']: _fail('BLOCKED_REVIEW')
        if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
        if now < state['next_allowed_at']: _fail('COOLDOWN')
        if state['sessions'] >= MAX_SESSIONS: _fail('SESSION_CAP')

    def inspect(self):
        """Full read-only replay; a missing path stays missing."""
        self._path_check()
        if not self.path.exists():
            state = dict(head_sha256=ZERO, events=0, sessions=0, pending_token=None,
                         last_evaluated_at=0, next_allowed_at=0, review_required=False, last_outcome=None,
                     pending_owner_fenced=False, pending_owner_sha256=None, pending_before_head_sha256=None,
                     completed_sessions=[], interruptions=0, last_interruption=None, last_interruption_head=None)
        else:
            with closing(self._connect()) as db: state = self._load(db)
        public=dict(state);public.pop('pending_before_head_sha256');public.pop('completed_sessions')
        return dict(public, account_sha256=self.account_digest, max_gets_per_session=4,
                    min_post_result_cooldown_seconds=MIN_COOLDOWN, automatic_retry=False,
                    broker_wide_quota_verified=False, ready_for_routing=False,
                    limitations='Only cooperating callers using this exact local journal are coordinated. No provider quota, distributed ownership, source attestation, automatic interrupted-read release or valid-tail-deletion protection.')

    def completed_sessions(self):
        """Return replayed aggregate head transitions without request timing/content."""
        self._path_check()
        if not self.path.exists(): return ()
        with closing(self._connect()) as db:
            state=self._load(db)
        return tuple(dict(item) for item in state['completed_sessions'])

    def ownership(self):
        """Local OS lease; runtime readers hold it through all IO and cleanup."""
        return _ReadOwnership(self)

    def begin(self, *, admission, ownership=None):
        """Internal callback samples permission/clock after lock and before commit."""
        with closing(self._connect(write=True)) as db:
            with db:
                state = self._load(db)
                if ownership is not None: self._owned(ownership)
                now = clock(admission())
                self._admit(state, now)
                body = dict(kind='BEGIN', evaluated_at=now)
                if ownership is not None:
                    body.update(owner_fenced=True, owner_sha256=ownership.identity())
                token = self._append(db, state['events'] + 1, body, state['head_sha256'])
                self._load(db)
                if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                if ownership is not None: self._owned(ownership)
            return token

    def finish(self, token, *, admission, status_code=None, transport_error=None, retry_after=None):
        """Retain fixed outcome/delay only; a lost return never erases the result."""
        self._path_check()
        if not self.path.exists(): _fail('BINDING_MISSING')
        with closing(self._connect(write=True)) as db:
            with db:
                state = self._load(db)
                now = clock(admission())
                if state['pending_token'] is None or token != state['pending_token']: _fail('TOKEN_DISAGREEMENT')
                if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                result = classify(status_code=status_code, transport_error=transport_error,
                                  retry_after=retry_after, evaluated_at=now)
                outcome = 'SUCCESS' if result['outcome'] == 'FOUND' else result['outcome']
                head = self._append(db, state['events'] + 1,
                    dict(kind='RESULT', evaluated_at=now, token=token, outcome=outcome,
                         delay=result['retry_after_seconds']), state['head_sha256'])
                self._load(db)
                if clock(admission()) < now: _fail('CLOCK_REGRESSION')
            return head

    def _owned(self, ownership):
        if type(ownership) is not _ReadOwnership or ownership.gate is not self:
            _fail('OWNER_REQUIRED')
        ownership.validate()

    def interrupt(self, token, *, expected_head, expected_account_id,
                  confirm_interrupted=False, admission):
        """Explicit review of a stopped owned read; never cancel, retry or reconcile.

        Legacy reservations lack a held-through-IO owner contract and cannot be
        cleared here. Auth/review blocks and retained session counts stay intact.
        """
        if confirm_interrupted is not True: _fail('CONFIRMATION_REQUIRED')
        if _uuid(expected_account_id) != self.account: _fail('ACCOUNT_DISAGREEMENT')
        if any(not isinstance(v, str) or re.fullmatch(r'[0-9a-f]{64}', v) is None for v in (token, expected_head)):
            _fail('REVIEW_REFERENCE_INVALID')
        self._path_check()
        if not self.path.exists(): _fail('BINDING_MISSING')
        with self.ownership() as owner:
            with closing(self._connect(write=True)) as db:
                with db:
                    state = self._load(db)
                    self._owned(owner)
                    now = clock(admission())
                    if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                    # A lost acknowledgment may be inspected/replayed only while
                    # this exact interruption remains the latest retained event.
                    last = state['last_interruption']
                    if last is not None and state['head_sha256'] == state['last_interruption_head'] and state['pending_token'] is None and last['token'] == token and last['expected_head'] == expected_head:
                        if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                        self._owned(owner)
                        return dict(head_sha256=state['head_sha256'], duplicate=True,
                                    interruption_recorded=True, automatic_retry=False, ready_for_routing=False)
                    if expected_head != state['head_sha256']: _fail('HEAD_DISAGREEMENT')
                    if state['pending_token'] is None or token != state['pending_token']: _fail('TOKEN_DISAGREEMENT')
                    if not state['pending_owner_fenced']: _fail('LEGACY_OWNER_UNVERIFIED')
                    if state['pending_owner_sha256'] != owner.identity(): _fail('OWNER_SCOPE_CHANGED')
                    body = dict(kind='INTERRUPTED', evaluated_at=now, token=token,
                                expected_head=expected_head, confirm_interrupted=True)
                    head = self._append(db, state['events'] + 1, body, state['head_sha256'])
                    self._load(db)
                    if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                    self._owned(owner)
                return dict(head_sha256=head, duplicate=False, interruption_recorded=True,
                            automatic_retry=False, ready_for_routing=False)


class _ReadOwnership:
    """Nonblocking process/thread OS ownership of one local pacing lock file."""
    def __init__(self, gate):
        self.gate = gate
        self.path = gate.path.resolve().with_name(gate.path.name + '.owner.lock')
        self.fd = None

    def validate(self):
        if self.fd is None or self.path.is_symlink(): _fail('OWNER_REQUIRED')
        current, opened = self.path.stat(), os.fstat(self.fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size != 1 or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            _fail('OWNER_FILE_CHANGED')

    def identity(self):
        self.validate()
        info = os.fstat(self.fd)
        return sha(dict(journal_path=str(self.gate.path.resolve()),
                        lock_device=info.st_dev, lock_inode=info.st_ino))

    def __enter__(self):
        if self.fd is not None: _fail('OWNER_ALREADY_HELD')
        self.gate._path_check()
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
            self.fd = fd
            self.validate()
            return self
        except BaseException as error:
            self.fd = None
            os.close(fd)
            if isinstance(error, OSError): _fail('OWNER_BUSY_OR_UNAVAILABLE')
            raise

    def __exit__(self, *args):
        fd, self.fd = self.fd, None
        if fd is not None: os.close(fd)
