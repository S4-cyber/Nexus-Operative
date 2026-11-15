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
from nexus.local_owner_scope import (
    ProcessOwnerClaim, open_owner_file, owner_directory_identity,
    owner_directory_matches,
    owner_entry_parent_identity, owner_entry_parent_matches,
    owner_marker_valid, posix_owner_directory_safe, posix_owner_file_safe,
    prepare_owner_file)
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints, check_existing, require_file_fingerprints)

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


def _frozen_path(value, code='PATH_INVALID'):
    """Pin one selected parent while retaining the final leaf for lstat."""
    try:
        selected = Path(value).absolute()
        return selected.parent.resolve() / selected.name
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        _fail(code)


def _file_identity(path, code='FILE_CHANGED'):
    """Capture one regular single-link main-file identity without following."""
    try:
        path = Path(path); observed = path.lstat()
        if (stat.S_ISLNK(observed.st_mode) or
                not stat.S_ISREG(observed.st_mode) or
                observed.st_nlink != 1):
            raise RuntimeError
        return str(path.resolve()), observed.st_dev, observed.st_ino
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        _fail(code)


def _require_file_identity(path, identity):
    if _file_identity(path) != identity:
        _fail('FILE_CHANGED')


def _require_main_connection(db, path):
    """Reject a valid SQLite handle that is not the captured main pathname."""
    try:
        rows = db.execute('PRAGMA database_list').fetchall()
        if (len(rows) != 1 or len(rows[0]) != 3 or
                type(rows[0][0]) is not int or rows[0][1] != 'main' or
                type(rows[0][2]) is not str or not rows[0][2] or
                _frozen_path(rows[0][2], 'CONNECTION_BINDING_CHANGED') != path):
            _fail('CONNECTION_BINDING_CHANGED')
    except ValueError:
        raise
    except (AttributeError, OSError, RuntimeError, sqlite3.Error, TypeError):
        _fail('CONNECTION_BINDING_CHANGED')


def _main_fingerprint(path):
    """Return one exact bounded quiescent main-file fingerprint."""
    return capture_file_fingerprints((path,), (MAX_DATABASE_BYTES,),
        'PAPER_PROVIDER_POST_BUDGET_')[0]


def _require_main_connection_content(db, path):
    """Require serialized main bytes to match stable pathname bytes."""
    expected = _main_fingerprint(path)
    try:
        serialized = db.serialize(name='main')
        observed = _main_fingerprint(path)
        if (observed != expected or type(serialized) is not bytes or
                len(serialized) != expected[3] or
                hashlib.sha256(serialized).hexdigest() != expected[7]):
            _fail('CONNECTION_CONTENT_CHANGED')
        return (observed,)
    except ValueError:
        raise
    except sqlite3.OperationalError as error:
        if _database_busy(error):
            _fail('DATABASE_BUSY')
        try:
            db.execute('SELECT 1 FROM sqlite_schema LIMIT 1').fetchone()
        except sqlite3.Error as probe:
            if (isinstance(probe, sqlite3.OperationalError) and
                    _database_busy(probe)):
                _fail('DATABASE_BUSY')
        _fail('CONNECTION_CONTENT_CHANGED')
    except (MemoryError, OSError, OverflowError, RuntimeError,
            sqlite3.Error, TypeError):
        _fail('CONNECTION_CONTENT_CHANGED')


def _active_fingerprint_boundary(item, host=None):
    """Use fields shared by pathname and open-handle APIs on this host."""
    host = os.name if host is None else host
    return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
        item.st_ctime_ns if host != 'nt' else None, item.st_nlink)


def _committed_fingerprint_boundary(fingerprint, host=None):
    """Normalize a one-file pathname fingerprint for descriptor comparison."""
    host = os.name if host is None else host
    if (type(fingerprint) is not tuple or len(fingerprint) != 1 or
            type(fingerprint[0]) is not tuple or len(fingerprint[0]) != 8):
        raise RuntimeError
    path, device, inode, size, mtime, ctime, links, digest = fingerprint[0]
    if (type(path) is not str or any(type(value) is not int for value in
            (device, inode, size, mtime, ctime, links)) or
            re.fullmatch(r'[0-9a-f]{64}', digest) is None):
        raise RuntimeError
    return (path, device, inode, size, mtime,
        ctime if host != 'nt' else None, links, digest)


def _descriptor_file_fingerprint(descriptor, resolved):
    """Hash bounded bytes through the exact writable committed descriptor."""
    before = os.fstat(descriptor)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            not 1 <= before.st_size <= MAX_DATABASE_BYTES):
        raise RuntimeError
    original_offset = os.lseek(descriptor, 0, os.SEEK_CUR)
    digest = hashlib.sha256(); remaining = MAX_DATABASE_BYTES + 1
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                break
            digest.update(block); remaining -= len(block)
    finally:
        os.lseek(descriptor, original_offset, os.SEEK_SET)
    after = os.fstat(descriptor)
    if (remaining == 0 or
            _active_fingerprint_boundary(before) !=
                _active_fingerprint_boundary(after) or
            before.st_ctime_ns != after.st_ctime_ns):
        raise RuntimeError
    return (str(resolved), after.st_dev, after.st_ino, after.st_size,
        after.st_mtime_ns, after.st_ctime_ns if os.name != 'nt' else None,
        after.st_nlink, digest.hexdigest())


def _sync_parent_directory(resolved):
    """Flush only the exact POSIX parent directory identity."""
    parent = resolved.parent; before = parent.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise RuntimeError
    expected = (before.st_dev, before.st_ino)
    flags = (os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) |
        getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0))
    descriptor = os.open(parent, flags)
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISDIR(opened.st_mode) or
                (opened.st_dev, opened.st_ino) != expected):
            raise RuntimeError
        os.fsync(descriptor)
        final = os.fstat(descriptor); observed = parent.lstat()
        if (not stat.S_ISDIR(final.st_mode) or
                (final.st_dev, final.st_ino) != expected or
                stat.S_ISLNK(observed.st_mode) or
                not stat.S_ISDIR(observed.st_mode) or
                (observed.st_dev, observed.st_ino) != expected):
            raise RuntimeError
    finally:
        os.close(descriptor)


def _sync_committed_store(path, identity, fingerprint, *, new):
    """Flush the exact committed main file and a new POSIX directory entry."""
    try:
        if (type(new) is not bool or type(identity) is not tuple or
                len(identity) != 3 or type(identity[0]) is not str):
            raise RuntimeError
        path = Path(path); resolved = path.resolve()
        expected_path, expected_device, expected_inode = identity
        if str(resolved) != expected_path:
            raise RuntimeError
        expected_descriptor = _committed_fingerprint_boundary(fingerprint)
        _require_file_identity(path, identity)
        require_file_fingerprints((path,), (MAX_DATABASE_BYTES,), fingerprint,
            'PAPER_PROVIDER_POST_BUDGET_')
        flags = (os.O_RDWR | getattr(os, 'O_BINARY', 0) |
            getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        descriptor = os.open(resolved, flags)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    not 1 <= before.st_size <= MAX_DATABASE_BYTES or
                    (before.st_dev, before.st_ino) !=
                        (expected_device, expected_inode)):
                raise RuntimeError
            if (_descriptor_file_fingerprint(descriptor, resolved) !=
                    expected_descriptor):
                raise RuntimeError
            os.fsync(descriptor)
            if (_descriptor_file_fingerprint(descriptor, resolved) !=
                    expected_descriptor):
                raise RuntimeError
        finally:
            os.close(descriptor)
        _require_file_identity(path, identity)
        require_file_fingerprints((path,), (MAX_DATABASE_BYTES,), fingerprint,
            'PAPER_PROVIDER_POST_BUDGET_')
        if new and os.name != 'nt':
            _sync_parent_directory(resolved)
            _require_file_identity(path, identity)
            require_file_fingerprints((path,), (MAX_DATABASE_BYTES,),
                fingerprint, 'PAPER_PROVIDER_POST_BUDGET_')
    except Exception:
        _fail('COMMIT_DURABILITY_UNCERTAIN')


def _discard_uncommitted_new(path, identity):
    """Remove only the exact empty file from an aborted first transaction.

    A failed first SQLite transaction can roll the new main file back to zero
    bytes.  That is not an initialized POST budget and would otherwise poison
    an exact operator retry.  Cleanup is deliberately narrow: replacement,
    links, bytes, a non-regular file or any SQLite sidecar retain the path and
    fail closed for inspection.
    """
    path = Path(path)
    sidecars = tuple(Path(str(path) + suffix) for suffix in
                     ('-journal', '-wal', '-shm'))
    try:
        if (type(identity) is not tuple or len(identity) != 3 or
                type(identity[0]) is not str):
            raise RuntimeError
        observed = path.lstat()
        if ((str(path.resolve()), observed.st_dev, observed.st_ino) != identity or
                stat.S_ISLNK(observed.st_mode) or
                not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or
                observed.st_size != 0):
            raise RuntimeError
        for sidecar in sidecars:
            try:
                sidecar.lstat()
            except FileNotFoundError:
                continue
            raise RuntimeError
        path.unlink()
        for candidate in (path, *sidecars):
            try:
                candidate.lstat()
            except FileNotFoundError:
                continue
            raise RuntimeError
    except (OSError, RuntimeError, TypeError, ValueError):
        _fail('UNCOMMITTED_CLEANUP_UNCERTAIN')


class PaperProviderPostBudget:
    """Hash-chained POST admission journal shared by cooperating callers."""

    def __init__(self, path, provider_scope='ALPACA_PAPER_ORDERS'):
        if (type(provider_scope) is not str or
                re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', provider_scope) is None):
            _fail('PROVIDER_SCOPE_INVALID')
        self.path = _frozen_path(path)
        self.provider_scope = provider_scope
        self.provider_digest = hashlib.sha256(provider_scope.encode()).hexdigest()
        self._active_completion = None

    def _capture_binding(self):
        """Bind mutable public attributes to one selected journal scope."""
        path = _frozen_path(self.path, 'STORE_BINDING_CHANGED')
        scope = self.provider_scope
        digest = self.provider_digest
        if (type(scope) is not str or
                re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', scope) is None or
                type(digest) is not str or
                hashlib.sha256(scope.encode()).hexdigest() != digest):
            _fail('STORE_BINDING_CHANGED')
        return path, scope, digest

    def _require_binding(self, binding):
        try:
            path, scope, digest = binding
            if (self._capture_binding() != (path, scope, digest) or
                    self.path != path):
                _fail('STORE_BINDING_CHANGED')
        except ValueError:
            raise
        except (AttributeError, OSError, RuntimeError, TypeError):
            _fail('STORE_BINDING_CHANGED')

    def _path_check(self, path=None):
        check_existing(self.path if path is None else path,
            MAX_DATABASE_BYTES, 'PAPER_PROVIDER_POST_BUDGET_')

    def _open(self, write=False):
        binding = self._capture_binding(); bound_path = binding[0]
        self._path_check(bound_path); self._require_binding(binding)
        new = False; created_identity = None; db = None
        try:
            main_identity = _file_identity(bound_path)
        except ValueError as error:
            if str(error) != 'PAPER_PROVIDER_POST_BUDGET_FILE_CHANGED' or bound_path.exists():
                raise
            main_identity = None
        if write and not bound_path.exists():
            bound_path.parent.mkdir(parents=True, exist_ok=True)
            self._require_binding(binding)
            try:
                fd = os.open(bound_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                new = True
                try:
                    opened = os.fstat(fd)
                    created_identity = (str(bound_path.resolve()),
                                        opened.st_dev, opened.st_ino)
                    main_identity = created_identity
                finally:
                    os.close(fd)
            self._path_check(bound_path); self._require_binding(binding)
        try:
            self._require_binding(binding)
            if main_identity is not None:
                _require_file_identity(bound_path, main_identity)
            db = sqlite3.connect(bound_path.resolve().as_uri() +
                                 ('?mode=rw' if write else '?mode=ro'),
                                 uri=True, timeout=DB_BUSY_TIMEOUT_MS / 1000)
            def setup(statement):
                self._require_binding(binding)
                _require_file_identity(bound_path, main_identity)
                result = db.execute(statement)
                _require_main_connection(db, bound_path)
                _require_file_identity(bound_path, main_identity)
                self._require_binding(binding)
                return result
            _require_main_connection(db, bound_path)
            setup(f'PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}')
            if write:
                setup('PRAGMA journal_mode=DELETE'); setup('PRAGMA synchronous=FULL')
                page_size = setup('PRAGMA page_size').fetchone()[0]
                setup(f'PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}')
                setup('BEGIN IMMEDIATE')
                if new:
                    setup('CREATE TABLE events (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
                    self._append(db, 0, dict(schema=SCHEMA, provider_sha256=self.provider_digest), ZERO)
                    self._require_binding(binding)
            return db, new, created_identity, binding, main_identity
        except sqlite3.OperationalError as error:
            if db is not None:
                try:
                    db.close()
                except BaseException:
                    if new: _fail('UNCOMMITTED_CLEANUP_UNCERTAIN')
                    raise
            if new: _discard_uncommitted_new(bound_path, created_identity)
            self._require_binding(binding)
            if _database_busy(error):
                _fail('DATABASE_BUSY')
            raise
        except BaseException:
            if db is not None:
                try:
                    db.close()
                except BaseException:
                    if new: _fail('UNCOMMITTED_CLEANUP_UNCERTAIN')
                    raise
            if new: _discard_uncommitted_new(bound_path, created_identity)
            self._require_binding(binding)
            raise

    def _connect(self):
        db, _new, _created, _binding, _main = self._open()
        return db

    @contextmanager
    def _write_connection(self):
        """Require the captured main file through write acknowledgement."""
        db, new, created_identity, binding, main_identity = self._open(write=True)
        bound_path = binding[0]
        try:
            yield db
        except BaseException:
            try:
                db.close()
            except BaseException:
                if new: _fail('UNCOMMITTED_CLEANUP_UNCERTAIN')
                raise
            if new: _discard_uncommitted_new(bound_path, created_identity)
            self._require_binding(binding)
            if not new:
                _require_file_identity(bound_path, main_identity)
            raise
        else:
            acknowledgement_error = None
            committed_fingerprint = None
            uncommitted = db.in_transaction
            try:
                self._require_binding(binding)
                _require_main_connection(db, bound_path)
                _require_file_identity(bound_path, main_identity)
                if not uncommitted:
                    committed_fingerprint = _require_main_connection_content(
                        db, bound_path)
                self._require_binding(binding)
            except BaseException as error:
                acknowledgement_error = error
            try:
                db.close()
            except BaseException:
                if new: _fail('UNCOMMITTED_CLEANUP_UNCERTAIN')
                raise
            if new and uncommitted:
                _discard_uncommitted_new(bound_path, created_identity)
            if acknowledgement_error is not None:
                raise acknowledgement_error
            self._require_binding(binding)
            if not (new and uncommitted):
                _require_file_identity(bound_path, main_identity)
            if committed_fingerprint is not None:
                require_file_fingerprints((bound_path,),
                    (MAX_DATABASE_BYTES,), committed_fingerprint,
                    'PAPER_PROVIDER_POST_BUDGET_')
                _sync_committed_store(bound_path, main_identity,
                    committed_fingerprint, new=new)
            if uncommitted:
                _fail('UNCOMMITTED_TRANSACTION')

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
                state['pending'] = dict(token=digest, admitted_at=now,
                    **{key: body[key] for key in (
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

    def ensure_initialized(self, *, expected_head):
        """Create only the immutable provider binding under local ownership.

        No attempt is reserved and no retry or routing authority is created.
        Existing journals must still match the caller's exact admitted head.
        """
        expected = _digest(expected_head, 'HEAD_INVALID')
        with self.ownership() as owner:
            with self._write_connection() as db:
                with _transaction(db):
                    state = self._load(db); self._owned(owner)
                    comparable = (ZERO if state['events'] == 0 and
                        state['attempts'] == 0 else state['head_sha256'])
                    if comparable != expected: _fail('HEAD_CHANGED')
                    self._owned(owner)
        return dict(head_sha256=comparable, provider_binding_initialized=True,
            attempt_reserved=False, automatic_retry=False,
            ready_for_routing=False, release_approved=False)

    def history(self):
        self._path_check()
        if not self.path.exists(): return ()
        with closing(self._connect()) as db:
            with _transaction(db):
                db.execute('BEGIN'); state = self._load(db)
        return tuple(dict(row) for row in state['completed'])

    def pending_receipt(self):
        """Cold-replay the exact pending BEGIN without exposing raw identity.

        The write-side return from :meth:`begin` is transport, not durable
        evidence.  This receipt is intentionally read-only and contains only
        the hash-bound fields already retained by the journal.
        """
        self._path_check()
        if not self.path.exists(): _fail('PENDING_MISSING')
        with closing(self._connect()) as db:
            with _transaction(db):
                db.execute('BEGIN'); state = self._load(db)
        pending = state['pending']
        if pending is None: _fail('PENDING_MISSING')
        return dict(pending, head_sha256=state['head_sha256'],
            exact_pending_reservation_replayed=True, read_only=True,
            automatic_retry=False, ready_for_routing=False,
            release_approved=False)

    def confirm_active_pending(self, token, *, account_id,
            request_before_head_sha256, payload_sha256, client_order_id,
            admitted_at, ownership):
        """Require the original in-process lease for a replayed reservation.

        A process restart or a replacement owner may inspect and interrupt a
        pending attempt, but cannot silently resume it into provider I/O.
        """
        value = _digest(token, 'TOKEN_INVALID')
        account = _uuid(account_id)
        request_head = _digest(request_before_head_sha256,
            'REQUEST_HEAD_INVALID')
        payload = _digest(payload_sha256, 'PAYLOAD_DIGEST_INVALID')
        if (type(client_order_id) is not str or
                not 1 <= len(client_order_id.encode()) <= 128):
            _fail('CLIENT_ORDER_ID_INVALID')
        self._owned(ownership)
        with closing(self._connect()) as db:
            with _transaction(db):
                db.execute('BEGIN'); state = self._load(db)
        pending = state['pending']
        expected = dict(token=value, admitted_at=clock(admitted_at),
            account_sha256=hashlib.sha256(account.encode()).hexdigest(),
            request_before_head_sha256=request_head,
            payload_sha256=payload,
            client_order_sha256=hashlib.sha256(client_order_id.encode()).hexdigest(),
            owner_sha256=ownership.identity())
        if (pending != expected or
                state['head_sha256'] != value or
                self._active_completion != (value, ownership, ownership.lease)):
            _fail('ORIGINAL_LEASE_REQUIRED')
        return value

    def recovery_history(self):
        """Return content-free recovery bindings after full chain replay."""
        self._path_check()
        if not self.path.exists(): return ()
        with closing(self._connect()) as db:
            with _transaction(db):
                db.execute('BEGIN'); state = self._load(db)
        return tuple(dict(row) for row in state['recovery_history'])

    def recovery_receipt(self):
        """Cold-replay the latest exact recovery without granting authority.

        The write-side return from :meth:`recover_resolved_unknown` is not
        durable evidence.  This read-only receipt binds the latest recovery to
        the current hash-chain head and exposes only retained digests and
        conservative state needed by the recovery command.
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
            recoveries=state['recoveries'],
            last_evaluated_at=state['last_evaluated_at'],
            next_allowed_at=state['next_allowed_at'],
            review_required=state['review_required'],
            pending_token=None if pending is None else pending['token'],
            exact_recovery_replayed=True, read_only=True,
            original_request_rearmed=False, automatic_retry=False,
            automatic_continuation=False, broker_account_reconciled=False,
            ready_for_routing=False, live_orders_enabled=False,
            money_movement_enabled=False, release_approved=False)

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
        with self._write_connection() as db:
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
        with self._write_connection() as db:
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
            with self._write_connection() as db:
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
        with self._write_connection() as db:
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
                if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                self._owned(ownership)
                # Keep the exact terminal proof as the final externally
                # observable operation before this transaction commits.
                if resolution_check() != proof: _fail('RESOLUTION_PROOF_CHANGED')
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
        self.budget_binding = budget._capture_binding()
        bound_path = self.budget_binding[0]
        self.path = bound_path.with_name(bound_path.name + '.owner.lock')
        self.fd = None; self.lease = None; self.directory_identity = None
        self.entry_parent_identity = None
        self.process_owner = ProcessOwnerClaim(self.path)

    def validate(self):
        self.budget._require_binding(self.budget_binding)
        if (self.fd is None or self.directory_identity is None or
                self.entry_parent_identity is None): _fail('OWNER_REQUIRED')
        if not owner_entry_parent_matches(self.path.parent.parent,
                self.entry_parent_identity):
            _fail('OWNER_FILE_CHANGED')
        if not owner_directory_matches(self.path.parent, self.directory_identity):
            _fail('OWNER_FILE_CHANGED')
        try:
            if self.path.is_symlink(): _fail('OWNER_FILE_CHANGED')
            current, opened = self.path.stat(), os.fstat(self.fd)
            directory = self.path.parent.stat()
        except OSError:
            _fail('OWNER_FILE_CHANGED')
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size != 1 or
                (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino) or
                not posix_owner_file_safe(current) or
                not posix_owner_file_safe(opened) or
                not posix_owner_directory_safe(directory) or
                not owner_marker_valid(self.fd)):
            _fail('OWNER_FILE_CHANGED')
        if not owner_directory_matches(self.path.parent, self.directory_identity):
            _fail('OWNER_FILE_CHANGED')
        if not owner_entry_parent_matches(self.path.parent.parent,
                self.entry_parent_identity):
            _fail('OWNER_FILE_CHANGED')

    def identity(self):
        self.validate(); info = os.fstat(self.fd)
        return sha(dict(journal_path=str(self.budget_binding[0]),
                        lock_device=info.st_dev, lock_inode=info.st_ino))

    def __enter__(self):
        if self.fd is not None: _fail('OWNER_ALREADY_HELD')
        self.budget._require_binding(self.budget_binding)
        self.budget._path_check(self.budget_binding[0])
        if self.path.is_symlink(): _fail('OWNER_SYMLINK')
        if not self.process_owner.acquire():
            _fail('OWNER_BUSY_OR_UNAVAILABLE')
        fd = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.directory_identity = owner_directory_identity(self.path.parent)
            self.entry_parent_identity = owner_entry_parent_identity(
                self.path.parent.parent)
            if (self.directory_identity is None or
                    self.entry_parent_identity is None):
                _fail('OWNER_FILE_INVALID')
            fd, created = open_owner_file(self.path)
            if not prepare_owner_file(self.path, fd, created,
                    self.directory_identity, self.entry_parent_identity):
                _fail('OWNER_FILE_INVALID')
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd = fd; self.validate(); self.lease = object(); return self
        except BaseException as error:
            self.fd = None; self.directory_identity = None
            self.entry_parent_identity = None
            self.process_owner.release()
            if fd is not None:
                os.close(fd)
            if isinstance(error, OSError): _fail('OWNER_BUSY_OR_UNAVAILABLE')
            raise

    def __exit__(self, *args):
        fd = self.fd
        if fd is None:
            return
        exit_error = None
        try:
            # Keep the opened ownership lease authoritative through the
            # complete context lifetime. A replacement lock pathname could
            # otherwise admit a second cooperating writer after the caller's
            # final durable proof but before this descriptor is released.
            self.validate()
        except BaseException as error:
            exit_error = error
        finally:
            self.fd = None; self.directory_identity = None
            self.entry_parent_identity = None
            try:
                os.close(fd)
            except BaseException:
                if exit_error is None:
                    raise
            finally:
                self.process_owner.release()
        if exit_error is not None:
            raise exit_error
