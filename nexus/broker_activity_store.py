"""Explicit bounded local retention of unverified BTC/USD paper activities.

No default path, credential access, network, ledger writes or resume permission.
Hashes detect inconsistent retained history, not source authenticity or tail deletion.
"""
from contextlib import closing
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from nexus import alpaca_activity_normalizer as adapter
from nexus.alpaca_activity_collection import _window
from nexus.alpaca_paper_contract import canonical, sha

SCHEMA = 'nexus.paper-activity-retention.v1'
MAX_BATCHES = 128
MAX_ACTIVITIES = 2000
MAX_RECEIPT_BYTES = 256 * 1024
MAX_RETAINED_BYTES = 8 * 1024 * 1024
MAX_DATABASE_BYTES = 16 * 1024 * 1024
ZERO = '0' * 64


def _fail(code):
    raise ValueError('BROKER_ACTIVITY_STORE_' + code)


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

    def _check_path(self):
        if self.path.is_symlink(): _fail('SYMLINK')
        if self.path.exists() and self.path.stat().st_size > MAX_DATABASE_BYTES: _fail('DATABASE_CAP')

    def _connect(self, write=False):
        self._check_path(); new = False
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor); new = True
            except FileExistsError: pass
        db = sqlite3.connect(self.path.resolve().as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=5)
        try:
            if write:
                db.execute('PRAGMA journal_mode=DELETE'); db.execute('PRAGMA synchronous=FULL')
                size = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES // size}')
                db.execute('BEGIN IMMEDIATE')
                if new:
                    db.execute('CREATE TABLE receipts (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
                    self._insert(db, 0, dict(schema=SCHEMA, account_sha256=self.account_digest), ZERO)
            return db
        except BaseException:
            db.close(); raise

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
                receipts[identity] = dict(after=body['after'], until=body['until'], activity_ids=ids, orders=data['orders'], **observation)
            previous = digest
        return activities, receipts, previous, total

    def inspect(self):
        """Read-only full replay; missing state never initializes a database."""
        self._check_path()
        if not self.path.exists(): return self._report({}, {}, ZERO)
        with closing(self._connect()) as db:
            activities, receipts, head, _ = self._load(db)
            return self._report(activities, receipts, head)

    def review(self):
        """Read-only combined diagnostic; never infer complete coverage or funds."""
        from nexus.broker_activity_review import review
        return review(self)

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
        with closing(self._connect(write=True)) as db:
            with db:
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
