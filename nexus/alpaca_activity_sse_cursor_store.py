"""Private crash-recoverable checkpoints for reviewed Activity SSE cursors.

The store accepts only a completed supplied reconnect review.  It persists the
actual ULID cursor needed for a later ``since_id`` plus content hashes and
aggregate counts; it never stores the supplied events, credentials, orders,
fees, or account identifier.  SQLite durability is local process-crash
recovery, not native power-loss acceptance or provider delivery evidence.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3

from nexus.alpaca_activity_normalizer import _uuid
from nexus.alpaca_activity_sse_reconnect_review import (
    FLAGS as REVIEW_FLAGS, POLICY as REVIEW_POLICY)
from nexus.alpaca_activity_sse_review import SOURCES, ULID
from nexus.alpaca_paper_contract import canonical, sha
from nexus.sqlite_journal_guard import check_existing


SCHEMA = 'nexus.private-alpaca-activity-sse-cursor-store.v1'
PROFILE = 'nexus.reviewed-alpaca-activity-sse-cursor-checkpoint.v1'
ZERO = '0' * 64
MAX_CHECKPOINTS = 256
MAX_RECORD_BYTES = 8192
MAX_DATABASE_BYTES = 4 * 1024 * 1024
FLAGS = ('actual_provider_access', 'actual_vault_access',
    'actual_stream_connection', 'provider_source_attested',
    'stream_delivery_verified', 'historical_replay_complete',
    'native_power_loss_durability_verified', 'fee_coverage_complete',
    'broker_account_reconciled', 'ready_for_routing',
    'live_orders_enabled', 'money_movement_enabled', 'release_approved')


def _fail(code):
    raise ValueError('ALPACA_ACTIVITY_SSE_CURSOR_STORE_' + code) from None


def _digest(value, code='DIGEST_INVALID'):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        _fail(code)
    return value


def _cursor(value, code='CURSOR_INVALID'):
    if type(value) is not str or ULID.fullmatch(value) is None:
        _fail(code)
    return value


def _account(value):
    try:
        return _uuid(value)
    except (ValueError, TypeError, AttributeError):
        _fail('ACCOUNT_INVALID')


def _positive(value, code):
    if type(value) is not int or not 0 < value <= 2000:
        _fail(code)
    return value


class ActivitySSECursorStore:
    """One account-bound, exact-head Activity SSE cursor journal."""

    def __init__(self, path, paper_account_id):
        self.path = Path(path)
        self.account_sha256 = sha(_account(paper_account_id))

    def _check(self):
        check_existing(self.path, MAX_DATABASE_BYTES,
            'ALPACA_ACTIVITY_SSE_CURSOR_STORE_')
        try:
            self.path.with_name(self.path.name + '-journal').lstat()
        except FileNotFoundError:
            pass
        except OSError:
            _fail('FILE_UNAVAILABLE')
        else:
            _fail('ROLLBACK_REVIEW_REQUIRED')

    def _connect(self, write=False):
        self._check()
        new = False
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
                new = True
            except FileExistsError:
                pass
            self._check()
        db = sqlite3.connect(self.path.resolve().as_uri() +
            ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=5)
        try:
            if write:
                db.execute('PRAGMA journal_mode=DELETE')
                db.execute('PRAGMA synchronous=FULL')
                page_size = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}')
                db.execute('BEGIN IMMEDIATE')
                if new:
                    db.execute('CREATE TABLE checkpoints (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
                    self._insert(db, 0, dict(schema=SCHEMA,
                        account_sha256=self.account_sha256), ZERO)
            else:
                db.execute('BEGIN')
            return db, new
        except BaseException:
            db.close()
            raise

    @staticmethod
    def _insert(db, seq, body, previous):
        encoded = canonical(body)
        if len(encoded.encode()) > MAX_RECORD_BYTES:
            _fail('RECORD_CAP')
        digest = sha(dict(seq=seq, body=body, previous=previous))
        db.execute('INSERT INTO checkpoints VALUES (?,?,?,?)',
            (seq, encoded, previous, digest))
        return digest

    def _review_scope(self, value, previous_cursor, next_cursor, expected_head):
        if type(value) is not dict:
            _fail('REVIEW_INVALID')
        required = {'mode', 'status', 'read_only', 'input_sha256',
            'account_sha256', 'reconnect_reason', 'initial_events_reviewed',
            'replay_events_reviewed', 'redelivered_events_deduplicated',
            'new_events_processed', 'unique_events_reviewed',
            'persisted_cursor_sha256', 'reconnect_since_id_sha256',
            'next_cursor_sha256', 'exact_since_id_reused',
            'duplicate_ref_ids_idempotent', 'duplicate_payloads_immutable',
            'cursor_advanced', 'all_unique_fees_parent_linked', 'sources'}
        if (not required <= set(value) or
                value['mode'] != 'SUPPLIED_ALPACA_ACTIVITY_SSE_RECONNECT_REVIEW' or
                value['status'] != 'REVIEW_REQUIRED' or value['read_only'] is not True or
                value['account_sha256'] != self.account_sha256 or
                tuple(value['sources']) != SOURCES or
                any(value.get(flag) is not False for flag in REVIEW_FLAGS) or
                any(value[key] is not True for key in (
                    'exact_since_id_reused', 'duplicate_ref_ids_idempotent',
                    'duplicate_payloads_immutable', 'cursor_advanced',
                    'all_unique_fees_parent_linked'))):
            _fail('REVIEW_INVALID')
        before = _cursor(previous_cursor, 'PREVIOUS_CURSOR_INVALID')
        after = _cursor(next_cursor, 'NEXT_CURSOR_INVALID')
        if after <= before:
            _fail('CURSOR_REGRESSION')
        if (value['persisted_cursor_sha256'] != sha(before) or
                value['reconnect_since_id_sha256'] != sha(before) or
                value['next_cursor_sha256'] != sha(after)):
            _fail('REVIEW_CURSOR_DISAGREEMENT')
        input_digest = _digest(value['input_sha256'], 'REVIEW_DIGEST_INVALID')
        initial = _positive(value['initial_events_reviewed'], 'COUNT_INVALID')
        replay = _positive(value['replay_events_reviewed'], 'COUNT_INVALID')
        duplicates = _positive(value['redelivered_events_deduplicated'],
            'COUNT_INVALID')
        new = _positive(value['new_events_processed'], 'COUNT_INVALID')
        unique = _positive(value['unique_events_reviewed'], 'COUNT_INVALID')
        if duplicates > replay or new > replay or unique < new:
            _fail('COUNT_INVALID')
        scope = dict(profile=PROFILE, review_policy=REVIEW_POLICY,
            account_sha256=self.account_sha256,
            expected_head_sha256=expected_head,
            previous_cursor=before, next_cursor=after,
            review_input_sha256=input_digest,
            reconnect_reason=value['reconnect_reason'],
            initial_events_reviewed=initial,
            replay_events_reviewed=replay,
            redelivered_events_deduplicated=duplicates,
            new_events_processed=new, unique_events_reviewed=unique)
        return dict(kind='CHECKPOINT', scope=scope,
            receipt_sha256=sha(scope))

    def _load(self, db):
        rows = db.execute(
            'SELECT seq,body,previous,digest FROM checkpoints ORDER BY seq LIMIT ?',
            (MAX_CHECKPOINTS + 2,)).fetchall()
        if not rows:
            _fail('BINDING_MISSING')
        if len(rows) > MAX_CHECKPOINTS + 1:
            _fail('CHECKPOINT_CAP')
        previous = ZERO
        records = []
        current_cursor = None
        for index, (seq, encoded, parent, digest) in enumerate(rows):
            if type(encoded) is not str or len(encoded.encode()) > MAX_RECORD_BYTES:
                _fail('RECORD_CAP')
            try:
                body = json.loads(encoded)
            except (ValueError, TypeError):
                _fail('PAYLOAD_INVALID')
            if (seq != index or parent != previous or canonical(body) != encoded or
                    digest != sha(dict(seq=seq, body=body, previous=parent))):
                _fail('CHAIN_DISAGREEMENT')
            if index == 0:
                if body != dict(schema=SCHEMA,
                        account_sha256=self.account_sha256):
                    _fail('ACCOUNT_OR_SCHEMA')
            else:
                if (type(body) is not dict or set(body) != {
                        'kind', 'scope', 'receipt_sha256'} or
                        body['kind'] != 'CHECKPOINT'):
                    _fail('PAYLOAD_INVALID')
                scope = body['scope']
                if type(scope) is not dict or set(scope) != {
                        'profile', 'review_policy', 'account_sha256',
                        'expected_head_sha256',
                        'previous_cursor', 'next_cursor',
                        'review_input_sha256', 'reconnect_reason',
                        'initial_events_reviewed', 'replay_events_reviewed',
                        'redelivered_events_deduplicated',
                        'new_events_processed', 'unique_events_reviewed'}:
                    _fail('PAYLOAD_INVALID')
                if (scope['profile'] != PROFILE or
                        scope['review_policy'] != REVIEW_POLICY or
                        scope['account_sha256'] != self.account_sha256 or
                        body['receipt_sha256'] != sha(scope)):
                    _fail('SCOPE_INVALID')
                _digest(scope['expected_head_sha256'])
                logical_parent = ZERO if not records else parent
                if scope['expected_head_sha256'] != logical_parent:
                    _fail('HEAD_CHAIN_DISAGREEMENT')
                before = _cursor(scope['previous_cursor'])
                after = _cursor(scope['next_cursor'])
                _digest(scope['review_input_sha256'])
                for key in ('initial_events_reviewed',
                        'replay_events_reviewed',
                        'redelivered_events_deduplicated',
                        'new_events_processed', 'unique_events_reviewed'):
                    _positive(scope[key], 'COUNT_INVALID')
                if after <= before or (current_cursor is not None and
                        before != current_cursor):
                    _fail('CURSOR_CHAIN_DISAGREEMENT')
                current_cursor = after
                records.append(dict(body=body, before_head_sha256=parent,
                    after_head_sha256=digest))
            previous = digest
        return previous, current_cursor, records

    def inspect(self):
        """Replay the complete chain and return the private current cursor."""
        self._check()
        if not self.path.exists():
            head, cursor, records = ZERO, None, []
        else:
            with closing(self._connect()[0]) as db:
                head, cursor, records = self._load(db)
        return dict(mode='PRIVATE_ALPACA_ACTIVITY_SSE_CURSOR_HISTORY',
            status='REVIEW_REQUIRED', initialized=bool(records),
            account_sha256=self.account_sha256, head_sha256=head,
            current_event_id=cursor,
            current_cursor_sha256=None if cursor is None else sha(cursor),
            retained_checkpoints=len(records), read_only=True,
            local_cursor_checkpoint_committed=bool(records),
            process_restart_recoverable=bool(records),
            **{flag: False for flag in FLAGS})

    def checkpoint(self, review, *, previous_cursor, next_cursor,
            expected_head, admission=None):
        """Commit one reviewed advance; exact lost-ack replay is idempotent."""
        expected = _digest(expected_head, 'HEAD_INVALID')
        body = self._review_scope(review, previous_cursor, next_cursor,
            expected)
        expected_input = body['scope']['review_input_sha256']
        if admission is None:
            admission = lambda: expected_input

        def admitted(code):
            if admission() != expected_input:
                _fail(code)

        admitted('INPUT_REVOKED')
        current = self.inspect()
        if current['head_sha256'] != expected:
            if not self.path.exists():
                _fail('HEAD_CHANGED')
            with closing(self._connect()[0]) as db:
                head, _, records = self._load(db)
            last = records[-1] if records else None
            if (head == current['head_sha256'] and last is not None and
                    last['body']['scope']['expected_head_sha256'] == expected and
                    last['body'] == body):
                admitted('INPUT_REVOKED')
                return self._receipt(last['after_head_sha256'], body, True)
            _fail('HEAD_CHANGED')
        if (current['current_event_id'] is not None and
                current['current_event_id'] != previous_cursor):
            _fail('CURSOR_CHANGED')

        db, new = self._connect(write=True)
        with closing(db):
            with db:
                head, cursor, records = self._load(db)
                comparable = ZERO if new and not records else head
                if comparable != expected:
                    _fail('HEAD_CHANGED')
                if cursor is not None and cursor != previous_cursor:
                    _fail('CURSOR_CHANGED')
                admitted('INPUT_REVOKED')
                if len(records) >= MAX_CHECKPOINTS:
                    _fail('CHECKPOINT_CAP')
                head = self._insert(db, len(records) + 1, body, head)
                loaded_head, loaded_cursor, _ = self._load(db)
                if loaded_head != head or loaded_cursor != next_cursor:
                    _fail('COMMIT_REPLAY_DISAGREEMENT')
                admitted('INPUT_REVOKED')
            # If this raises, the committed record is deliberately retained.
            # A caller must reopen and submit the exact same expected-head
            # transition; it cannot skip or invent a cursor.
            admitted('INPUT_REVOKED_AFTER_COMMIT')
        return self._receipt(head, body, False)

    def _receipt(self, head, body, duplicate):
        return dict(mode='PRIVATE_ALPACA_ACTIVITY_SSE_CURSOR_CHECKPOINT',
            status='REVIEW_REQUIRED', account_sha256=self.account_sha256,
            head_sha256=head, cursor_sha256=sha(body['scope']['next_cursor']),
            receipt_sha256=body['receipt_sha256'], duplicate=duplicate,
            local_cursor_checkpoint_committed=True,
            process_restart_recoverable=True,
            **{flag: False for flag in FLAGS})


def fixture_report():
    """Disposable aggregate-only checkpoint/restart/lost-ack evidence."""
    import tempfile
    from nexus.alpaca_activity_sse_reconnect_review import fixture_report as reconnect_fixture
    from nexus.alpaca_activity_sse_reconnect_review import review
    from nexus.alpaca_activity_sse_review import _event

    account = '01234567-89ab-cdef-0123-456789abcdef'
    first = [_event('01K00000000000000000000001',
        '11111111-1111-1111-1111-111111111111', 'TRD'),
        _event('01K00000000000000000000002',
        '22222222-2222-2222-2222-222222222222', 'FEE')]
    trade = _event('01K00000000000000000000003',
        '33333333-3333-3333-3333-333333333333', 'TRD',
        details={**first[0]['details'],
            'order_id': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
            'client_order_id': 'fixture_client_2'},
        at='2026-10-18T18:02:00.100000000Z',
        executed_at='2026-10-18T18:02:00.000000000Z')
    fee = _event('01K00000000000000000000004',
        '44444444-4444-4444-4444-444444444444', 'FEE',
        details=dict(parent_id=trade['ref_id'], system_date='2026-10-18'),
        at='2026-10-18T18:03:00.100000000Z',
        executed_at='2026-10-18T18:03:00.000000000Z')
    checked = review(first, [*first, trade, fee],
        expected_account_id=account, persisted_event_id=first[-1]['event_id'],
        reconnect_since_id=first[-1]['event_id'],
        reconnect_reason='CONNECTION_DROPPED')
    with tempfile.TemporaryDirectory(prefix='nexus-sse-cursor-') as directory:
        path = Path(directory) / 'cursor.sqlite3'
        store = ActivitySSECursorStore(path, account)
        first_receipt = store.checkpoint(checked,
            previous_cursor=first[-1]['event_id'],
            next_cursor=fee['event_id'], expected_head=ZERO)
        cold = ActivitySSECursorStore(path, account).inspect()
        duplicate = store.checkpoint(checked,
            previous_cursor=first[-1]['event_id'],
            next_cursor=fee['event_id'], expected_head=ZERO)
        checks = dict(review_fixture_passed=reconnect_fixture()['passed'],
            checkpoint_committed=first_receipt['local_cursor_checkpoint_committed'],
            cold_restart_recovers_cursor=cold['process_restart_recoverable'] and
                cold['current_cursor_sha256'] == first_receipt['cursor_sha256'],
            exact_stale_head_replay_idempotent=duplicate['duplicate'] and
                duplicate['head_sha256'] == first_receipt['head_sha256'],
            output_never_authoritative=all(first_receipt[flag] is False
                for flag in FLAGS))
    return dict(source='TEMPORARY_PRIVATE_ACTIVITY_SSE_CURSOR_STORE_FIXTURE',
        passed=all(checks.values()), **checks,
        actual_storage_retained=False,
        **{flag: False for flag in FLAGS})
