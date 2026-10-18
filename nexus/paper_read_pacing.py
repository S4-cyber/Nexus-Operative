"""Explicit account-bound local paper-read reservations, never provider authority.

One <=4-GET session per journal, with a fixed 60-second recorded-result cooldown.
No default path, retries, expiry, repair, eviction or clearing of review blocks.
Hashes detect inconsistent history, not valid-tail deletion or broker identity.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from nexus.alpaca_paper_contract import canonical, sha
from nexus.alpaca_paper_inspection import _uuid
from nexus.broker_lookup_policy import classify, clock

SCHEMA = 'nexus.paper-read-pacing.v1'
MAX_SESSIONS = 256
MAX_DATABASE_BYTES = 4 * 1024 * 1024
MIN_COOLDOWN = 60
ZERO = '0' * 64


def _fail(code):
    raise ValueError('PAPER_READ_PACING_' + code)


class PaperReadPacing:
    def __init__(self, path, paper_account_id):
        self.path = Path(path)
        self.account = _uuid(paper_account_id)
        self.account_digest = hashlib.sha256(self.account.encode()).hexdigest()

    def _path_check(self):
        if self.path.is_symlink(): _fail('SYMLINK')
        if self.path.exists() and self.path.stat().st_size > MAX_DATABASE_BYTES: _fail('DATABASE_CAP')

    def _connect(self, write=False):
        self._path_check(); new = False
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd); new = True
            except FileExistsError: pass
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
                     last_evaluated_at=0, next_allowed_at=0, review_required=False, last_outcome=None)
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
                if kind == 'BEGIN' and set(body) == {'kind', 'evaluated_at'}:
                    now = clock(body['evaluated_at'])
                    self._admit(state, now)
                    state['pending_token'] = digest
                    state['sessions'] += 1
                elif kind == 'RESULT' and set(body) == {'kind', 'evaluated_at', 'token', 'outcome', 'delay'}:
                    now = clock(body['evaluated_at'])
                    if state['pending_token'] is None or body['token'] != state['pending_token']: _fail('TOKEN_DISAGREEMENT')
                    if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                    outcome, delay = body['outcome'], body['delay']
                    if outcome not in ('SUCCESS', 'NOT_FOUND', 'TIMEOUT', 'NETWORK_ERROR', 'RATE_LIMITED', 'SERVER_ERROR', 'AUTH_DENIED', 'REVIEW_REQUIRED'): _fail('OUTCOME_INVALID')
                    if type(delay) is not int or (outcome == 'RATE_LIMITED' and not 1 <= delay <= 86400) or (outcome != 'RATE_LIMITED' and delay != 0): _fail('DELAY_INVALID')
                    state['pending_token'] = None
                    state['review_required'] = outcome in ('AUTH_DENIED', 'REVIEW_REQUIRED')
                    state['next_allowed_at'] = now + max(MIN_COOLDOWN, delay)
                    state['last_outcome'] = outcome
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
                         last_evaluated_at=0, next_allowed_at=0, review_required=False, last_outcome=None)
        else:
            with closing(self._connect()) as db: state = self._load(db)
        return dict(state, account_sha256=self.account_digest, max_gets_per_session=4,
                    min_post_result_cooldown_seconds=MIN_COOLDOWN, automatic_retry=False,
                    broker_wide_quota_verified=False, ready_for_routing=False,
                    limitations='Only cooperating callers using this exact local journal are coordinated. No provider quota, distributed ownership, source attestation, automatic interrupted-read release or valid-tail-deletion protection.')

    def begin(self, *, admission):
        """Internal callback samples permission/clock after lock and before commit."""
        with closing(self._connect(write=True)) as db:
            with db:
                state = self._load(db)
                now = clock(admission())
                self._admit(state, now)
                token = self._append(db, state['events'] + 1, dict(kind='BEGIN', evaluated_at=now), state['head_sha256'])
                self._load(db)
                if clock(admission()) < now: _fail('CLOCK_REGRESSION')
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
