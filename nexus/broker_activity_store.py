"""Explicit bounded local retention of unverified BTC/USD paper activities.

No default path, credential access, network, ledger writes or resume permission.
Hashes detect inconsistent retained history, not source authenticity or tail deletion.
"""
from contextlib import closing, contextmanager
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from nexus import alpaca_activity_normalizer as adapter
from nexus.alpaca_activity_collection import _window
from nexus.alpaca_paper_contract import canonical, sha
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints, capture_file_identities, check_existing,
    require_file_identities)

SCHEMA = 'nexus.paper-activity-retention.v1'
MAX_BATCHES = 128
MAX_ACTIVITIES = 2000
MAX_RECEIPT_BYTES = 256 * 1024
MAX_RETAINED_BYTES = 8 * 1024 * 1024
MAX_DATABASE_BYTES = 16 * 1024 * 1024
DB_BUSY_TIMEOUT_MS = 10_000
ZERO = '0' * 64


def _fail(code):
    raise ValueError('BROKER_ACTIVITY_STORE_' + code) from None


def _database_busy(error):
    code = getattr(error, 'sqlite_errorcode', None)
    return (code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or
        any(word in str(error).lower() for word in ('busy', 'locked')))


def _main_fingerprint(path):
    """Hash the exact bounded activity-retention main file."""
    def fail():
        _fail('CONNECTION_CONTENT_CHANGED')
    path = Path(path)
    try:
        observed = path.lstat()
        if (stat.S_ISLNK(observed.st_mode) or
                not stat.S_ISREG(observed.st_mode) or
                observed.st_nlink != 1 or
                not 1 <= observed.st_size <= MAX_DATABASE_BYTES):
            fail()
        resolved = path.resolve()
        flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0) |
            getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        digest = hashlib.sha256(); remaining = MAX_DATABASE_BYTES + 1
        with os.fdopen(os.open(resolved, flags), 'rb', buffering=0) as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    not 1 <= before.st_size <= MAX_DATABASE_BYTES or
                    (before.st_dev, before.st_ino) !=
                        (observed.st_dev, observed.st_ino)):
                fail()
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    break
                digest.update(block); remaining -= len(block)
            after = os.fstat(stream.fileno())
        final = path.lstat()
        def boundary(value, *, pathname=False):
            return (value.st_dev, value.st_ino, value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns if os.name != 'nt' or not pathname else None,
                value.st_nlink)
        if (remaining == 0 or boundary(observed, pathname=True) !=
                boundary(before, pathname=True) or
                boundary(before) != boundary(after) or
                boundary(after, pathname=True) !=
                    boundary(final, pathname=True) or
                not stat.S_ISREG(final.st_mode)):
            fail()
        return (str(resolved), after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns if os.name != 'nt' else None,
            after.st_nlink, digest.hexdigest())
    except ValueError:
        raise
    except (OSError, RuntimeError, TypeError):
        fail()


def _require_main_connection(db, bound_path):
    """Require SQLite to report exactly the selected activity main file."""
    try:
        rows = db.execute('PRAGMA database_list').fetchall()
        if (len(rows) != 1 or len(rows[0]) != 3 or
                type(rows[0][0]) is not int or rows[0][1] != 'main' or
                type(rows[0][2]) is not str or not rows[0][2] or
                Path(rows[0][2]).resolve() != bound_path):
            _fail('CONNECTION_BINDING_CHANGED')
    except ValueError:
        raise
    except sqlite3.OperationalError as error:
        if _database_busy(error):
            _fail('DATABASE_BUSY')
        _fail('CONNECTION_BINDING_CHANGED')
    except (OSError, RuntimeError, sqlite3.Error, TypeError):
        _fail('CONNECTION_BINDING_CHANGED')


def _require_main_connection_content(db, bound_path):
    """Bind committed connection bytes to one stable pathname fingerprint."""
    try:
        serialized = db.serialize(name='main')
        observed = _main_fingerprint(bound_path)
        if (type(serialized) is not bytes or
                len(serialized) != observed[3] or
                hashlib.sha256(serialized).hexdigest() != observed[7]):
            _fail('CONNECTION_CONTENT_CHANGED')
        return observed
    except ValueError:
        raise
    except sqlite3.OperationalError as error:
        if _database_busy(error):
            _fail('DATABASE_BUSY')
        _fail('CONNECTION_CONTENT_CHANGED')
    except (MemoryError, OSError, OverflowError, RuntimeError,
            sqlite3.Error, TypeError):
        _fail('CONNECTION_CONTENT_CHANGED')


def _active_fingerprint_boundary(item, host=None):
    host = os.name if host is None else host
    return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
        item.st_ctime_ns if host != 'nt' else None, item.st_nlink)


def _committed_fingerprint_boundary(fingerprint, host=None):
    host = os.name if host is None else host
    if type(fingerprint) is not tuple or len(fingerprint) != 8:
        raise RuntimeError
    path, device, inode, size, mtime, ctime, links, digest = fingerprint
    if (type(path) is not str or any(type(value) is not int for value in
            (device, inode, size, mtime, links)) or
            (type(ctime) is not int if host != 'nt' else ctime is not None) or
            re.fullmatch(r'[0-9a-f]{64}', digest) is None):
        raise RuntimeError
    return (path, device, inode, size, mtime,
        ctime if host != 'nt' else None, links, digest)


def _captured_fingerprint_boundary(fingerprint, host=None):
    """Normalize the generic cold-file sample to the committed schema."""
    host = os.name if host is None else host
    if type(fingerprint) is not tuple or len(fingerprint) != 8:
        raise RuntimeError
    path, device, inode, size, mtime, ctime, links, digest = fingerprint
    if (type(path) is not str or any(type(value) is not int for value in
            (device, inode, size, mtime, ctime, links)) or
            re.fullmatch(r'[0-9a-f]{64}', digest) is None):
        raise RuntimeError
    return (path, device, inode, size, mtime,
        ctime if host != 'nt' else None, links, digest)


def _require_committed_fingerprint(path, fingerprint, host=None):
    """Require no sidecars and the exact portable committed main bytes."""
    expected = _committed_fingerprint_boundary(fingerprint, host=host)
    observed = capture_file_fingerprints((path,), (MAX_DATABASE_BYTES,),
        'BROKER_ACTIVITY_STORE_')
    if (len(observed) != 1 or
            _captured_fingerprint_boundary(observed[0], host=host) != expected):
        raise RuntimeError


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
    """Flush only the exact POSIX activity-store parent identity."""
    parent = resolved.parent
    before = parent.lstat()
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
    """Flush the exact activity main and a newly published POSIX entry."""
    try:
        if (type(new) is not bool or type(identity) is not tuple or
                len(identity) != 3 or type(identity[0]) is not str):
            raise RuntimeError
        path = Path(path); resolved = path.resolve()
        expected_path, expected_device, expected_inode = identity
        if str(resolved) != expected_path:
            raise RuntimeError
        expected_descriptor = _committed_fingerprint_boundary(fingerprint)
        require_file_identities((path,), (identity,),
            'BROKER_ACTIVITY_STORE_')
        _require_committed_fingerprint(path, fingerprint)
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
        require_file_identities((path,), (identity,),
            'BROKER_ACTIVITY_STORE_')
        _require_committed_fingerprint(path, fingerprint)
        if new and os.name != 'nt':
            _sync_parent_directory(resolved)
            require_file_identities((path,), (identity,),
                'BROKER_ACTIVITY_STORE_')
            _require_committed_fingerprint(path, fingerprint)
    except Exception:
        _fail('COMMIT_DURABILITY_UNCERTAIN')


@contextmanager
def _transaction(db):
    """Normalize bounded SQLite statement/commit contention without retry."""
    try:
        with db:
            yield
    except sqlite3.OperationalError as error:
        if _database_busy(error):
            _fail('DATABASE_BUSY')
        raise


def _raw(record):
    """Revalidate canonical records through the original strict source parser."""
    if record['kind'] == 'FILL':
        return dict(id=record['activity_id'], activity_type='FILL', type=record['fill_type'],
            order_id=record['broker_order_id'], client_order_id=record['client_order_id'],
            symbol='BTCUSD', side=record['side'].lower(), qty=record['quantity'], price=record['price'],
            transaction_time=record['occurred_at'], cum_qty=record['cumulative_quantity'], leaves_qty=record['leaves_quantity'])
    raw = dict(id=record['activity_id'], activity_type=record['kind'], status='executed',
               date=record['reported_date'], order_id=record['order_reference'],
               execution_id=record['unverified_execution_reference'])
    debit = str(Decimal(record['amount']).copy_negate())
    if record['kind'] == 'CFEE': raw.update(symbol='BTCUSD', qty=debit, net_amount='0')
    else: raw.update(net_amount=debit)
    return raw


def _inputs(rows, orders, account):
    _, order_map, records, _, _ = adapter.canonical_inputs(rows, orders, expected_account_id=account)
    return dict(orders=sorted(order_map.values(), key=lambda r:r['broker_order_id']), activities=records)


class BrokerActivityStore:
    def __init__(self, path, paper_account_id):
        self.path = Path(path)
        self.account = adapter._uuid(paper_account_id)
        self.account_digest = hashlib.sha256(self.account.encode()).hexdigest()
        self._connection_contexts = {}

    def _check_path(self):
        check_existing(self.path, MAX_DATABASE_BYTES, 'BROKER_ACTIVITY_STORE_')

    def _connect(self, write=False):
        try:
            bound_path = self.path.resolve()
        except (OSError, RuntimeError, TypeError):
            _fail('CONNECTION_BINDING_CHANGED')
        self._check_path(); new = False; bound_identity = None
        if self.path.exists():
            bound_identity = capture_file_identities((self.path,),
                'BROKER_ACTIVITY_STORE_')
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    opened = os.fstat(descriptor)
                    bound_identity = ((str(bound_path), opened.st_dev,
                        opened.st_ino),)
                finally:
                    os.close(descriptor)
                new = True
            except FileExistsError: pass
            self._check_path()
        if bound_identity is None and self.path.exists():
            bound_identity = capture_file_identities((self.path,),
                'BROKER_ACTIVITY_STORE_')
        def require_bound_path():
            try:
                if self.path.resolve() != bound_path:
                    _fail('CONNECTION_BINDING_CHANGED')
                if bound_identity is not None:
                    require_file_identities((self.path,), bound_identity,
                        'BROKER_ACTIVITY_STORE_')
            except ValueError:
                raise
            except (OSError, RuntimeError, TypeError):
                _fail('CONNECTION_BINDING_CHANGED')
        require_bound_path()
        db = sqlite3.connect(bound_path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
            uri=True, timeout=DB_BUSY_TIMEOUT_MS / 1000)
        try:
            require_bound_path()
            _require_main_connection(db, bound_path)
            require_bound_path()
            db.execute(f'PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}')
            if write:
                db.execute('PRAGMA journal_mode=DELETE'); db.execute('PRAGMA synchronous=FULL')
                size = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES // size}')
                db.execute('BEGIN IMMEDIATE')
                if new:
                    db.execute('CREATE TABLE receipts (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
                    self._insert(db, 0, dict(schema=SCHEMA, account_sha256=self.account_digest), ZERO)
                require_bound_path()
                self._connection_contexts[id(db)] = (new,
                    bound_identity[0], bound_path, require_bound_path)
            return db
        except sqlite3.OperationalError as error:
            db.close()
            if _database_busy(error):
                _fail('DATABASE_BUSY')
            raise
        except BaseException:
            db.close(); raise

    def _connection_context(self, db):
        """Find write context through the small test/runtime proxy surface."""
        candidate = db; seen = set()
        while candidate is not None and id(candidate) not in seen:
            seen.add(id(candidate))
            context = self._connection_contexts.get(id(candidate))
            if context is not None:
                return id(candidate), context
            try:
                candidate = getattr(candidate, 'db')
            except (AttributeError, TypeError):
                candidate = None
        _fail('CONNECTION_BINDING_CHANGED')

    @contextmanager
    def _write_connection(self):
        """Require exact committed activity bytes through explicit flush."""
        db = self._connect(write=True)
        raw_id, (new, main_identity, bound_path,
            require_bound_path) = self._connection_context(db)
        try:
            yield db
        except BaseException:
            try:
                db.close()
            finally:
                self._connection_contexts.pop(raw_id, None)
            raise
        else:
            acknowledgement_error = None
            committed_fingerprint = None
            uncommitted = db.in_transaction
            try:
                require_bound_path()
                _require_main_connection(db, bound_path)
                if not uncommitted:
                    committed_fingerprint = (
                        _require_main_connection_content(db, bound_path))
                require_bound_path()
            except BaseException as error:
                acknowledgement_error = error
            try:
                db.close()
            except BaseException as error:
                if acknowledgement_error is None:
                    acknowledgement_error = error
            finally:
                self._connection_contexts.pop(raw_id, None)
            if acknowledgement_error is not None:
                raise acknowledgement_error
            require_bound_path()
            if committed_fingerprint is not None:
                _sync_committed_store(bound_path, main_identity,
                    committed_fingerprint, new=new)
            if uncommitted:
                _fail('UNCOMMITTED_TRANSACTION')

    @staticmethod
    def _insert(db, seq, body, previous):
        encoded = canonical(body)
        if len(encoded.encode()) > MAX_RECEIPT_BYTES: _fail('RECEIPT_CAP')
        digest = sha(dict(seq=seq, body=body, previous=previous))
        db.execute('INSERT INTO receipts VALUES (?,?,?,?)', (seq, encoded, previous, digest))
        return digest

    def _load(self, db):
        rows = db.execute('SELECT seq,body,previous,digest FROM receipts ORDER BY seq LIMIT ?', (MAX_BATCHES + 2,)).fetchall()
        if not rows: _fail('BINDING_MISSING')
        if len(rows) > MAX_BATCHES + 1: _fail('BATCH_CAP')
        previous = ZERO; activities = {}; receipts = {}; total = 0; bindings = {}; clients = {}
        for index, (seq, encoded, parent, digest) in enumerate(rows):
            if not isinstance(encoded, str) or len(encoded.encode()) > MAX_RECEIPT_BYTES: _fail('RECEIPT_CAP')
            total += len(encoded.encode())
            if total > MAX_RETAINED_BYTES: _fail('RETAINED_BYTE_CAP')
            try: body = json.loads(encoded)
            except (ValueError, TypeError): _fail('PAYLOAD_INVALID')
            if seq != index or parent != previous or canonical(body) != encoded or digest != sha(dict(seq=seq, body=body, previous=parent)):
                _fail('CHAIN_DISAGREEMENT')
            if index == 0:
                if body != dict(schema=SCHEMA, account_sha256=self.account_digest): _fail('ACCOUNT_OR_SCHEMA')
            else:
                if not isinstance(body, dict) or set(body) not in ({'after', 'until', 'orders', 'activity_ids', 'new_activities', 'receipt_id'}, {'after', 'until', 'orders', 'activity_ids', 'new_activities', 'receipt_id', 'collection_observation'}): _fail('PAYLOAD_INVALID')
                _window(body['after'], body['until'])
                ids = body['activity_ids']; added = body['new_activities']
                if not isinstance(ids, list) or len(ids) > MAX_ACTIVITIES or ids != sorted(set(ids)): _fail('IDENTITY_LIST_INVALID')
                if not isinstance(added, list) or len(added) > MAX_ACTIVITIES: _fail('ACTIVITY_CAP')
                for record in added:
                    identity = record['activity_id']
                    if identity in activities or identity not in ids: _fail('DUPLICATE_OR_UNREFERENCED_ACTIVITY')
                    activities[identity] = record
                if len(activities) > MAX_ACTIVITIES: _fail('ACTIVITY_CAP')
                try:
                    selected = [activities[key] for key in ids]
                    data = _inputs([_raw(record) for record in selected],
                        [dict(order, symbol='BTC/USD') for order in body['orders']], self.account)
                except (KeyError, TypeError, AttributeError): _fail('PAYLOAD_INVALID')
                if data != dict(orders=body['orders'], activities=selected): _fail('NONCANONICAL_INPUT')
                observation = {}
                if 'collection_observation' in body:
                    from nexus.activity_collection_evidence import validate
                    checked = validate(body['collection_observation'], data, after=body['after'], until=body['until'])
                    if checked != body['collection_observation']: _fail('NONCANONICAL_OBSERVATION')
                    observation = dict(collection_observation=checked)
                identity = sha(dict(account_sha256=self.account_digest, after=body['after'], until=body['until'], **data, **observation))
                if body['receipt_id'] != identity or identity in receipts: _fail('RECEIPT_ID_DISAGREEMENT')
                for order in data['orders']:
                    oid = order['broker_order_id']; binding = (order['client_order_id'], order['side'])
                    if oid in bindings and bindings[oid] != binding: _fail('ORDER_BINDING_CONFLICT')
                    if binding[0] in clients and clients[binding[0]] != oid: _fail('CLIENT_BINDING_CONFLICT')
                    bindings[oid] = binding; clients[binding[0]] = oid
                receipts[identity] = dict(after=body['after'], until=body['until'], activity_ids=ids, orders=data['orders'],
                    retention_before_head_sha256=ZERO if index==1 else parent,
                    retention_after_head_sha256=digest, **observation)
            previous = digest
        return activities, receipts, previous, total

    def inspect(self):
        """Read-only full replay; missing state never initializes a database."""
        self._check_path()
        if not self.path.exists(): return self._report({}, {}, ZERO)
        with closing(self._connect()) as db:
            with _transaction(db):
                activities, receipts, head, _ = self._load(db)
            return self._report(activities, receipts, head)

    def review(self):
        """Read-only combined diagnostic; never infer complete coverage or funds."""
        from nexus.broker_activity_review import review
        return review(self)

    def review_balances(self, **scope):
        """Explicit head-bound gross-fill/residual diagnostic; no funds authority."""
        from nexus.retained_balance_review import review
        return review(self, **scope)

    def _report(self, activities, receipts, head):
        return dict(mode='UNATTESTED_LOCAL_PAPER_ACTIVITY_RETENTION', account_sha256=self.account_digest,
            head_sha256=head, retained_batches=len(receipts), retained_activities=len(activities),
            retained_collection_observations=sum('collection_observation' in r for r in receipts.values()),
            collection_source_attested=False, receipts=receipts, normalized_activities=[activities[key] for key in sorted(activities)],
            source='SUPPLIED_UNATTESTED', activity_coverage_verified=False, fee_coverage_complete=False,
            broker_account_reconciled=False, spendable_funds_verified=False, ledger_applied=False,
            broker_connected=False, ready_for_routing=False, live_orders_enabled=False, automatic_continuation=False,
            limitations='Local integrity replay only; no source attestation, complete coverage, verified fee links, posting instants, resume permission or protection from valid-tail deletion. No eviction or repair.')

    def retain(self, activities, orders, *, after, until, admission=lambda: True, collection_observation=None):
        """Commit a validated batch atomically; exact replay appends nothing.

        Internal admission hook is rechecked under the write lock and before commit.
        A lost return is not proof of rollback; inspect retained receipts explicitly.
        """
        _window(after, until)
        data = _inputs(activities, orders, self.account)
        observation = {}
        if collection_observation is not None:
            from nexus.activity_collection_evidence import validate
            observation = dict(collection_observation=validate(collection_observation, data, after=after, until=until))
        identity = sha(dict(account_sha256=self.account_digest, after=after, until=until, **data, **observation))
        with self._write_connection() as db:
            with _transaction(db):
                retained, receipts, head, total = self._load(db)
                if admission() is not True: _fail('ADMISSION_REVOKED')
                if identity in receipts:
                    return dict(receipt_id=identity, duplicate=True, head_sha256=head)
                if len(receipts) >= MAX_BATCHES: _fail('BATCH_CAP')
                added = []
                for record in data['activities']:
                    key = record['activity_id']
                    if key in retained and retained[key] != record: _fail('ACTIVITY_CONFLICT')
                    if key not in retained: added.append(record)
                body = dict(after=after, until=until, orders=data['orders'],
                    activity_ids=[r['activity_id'] for r in data['activities']], new_activities=added, receipt_id=identity, **observation)
                if total + len(canonical(body).encode()) > MAX_RETAINED_BYTES: _fail('RETAINED_BYTE_CAP')
                head = self._insert(db, len(receipts) + 1, body, head)
                self._load(db)  # Revalidate cross-batch identity/binding and all caps before acknowledgment.
                if admission() is not True: _fail('ADMISSION_REVOKED')
            return dict(receipt_id=identity, duplicate=False, head_sha256=head)
