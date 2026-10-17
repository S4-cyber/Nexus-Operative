"""Disconnected, replay-validated Alpaca paper request recovery foundation.

Explicit local paths and caller-supplied account identity only. No transport,
credentials, automatic retries, synthetic ledger writes or trading authority.
"""
from contextlib import closing
from decimal import localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from uuid import UUID
from nexus import alpaca_paper_contract as contract

MAX_REQUESTS = 128
MAX_EVENTS = 4096
MAX_EVENT_BYTES = 4096
MAX_DATABASE_BYTES = 64 * 1024 * 1024
ZERO = '0' * 64
SCHEMA = 'nexus.alpaca-paper-request-journal.v1'


def _plan(value):
    tracker = contract.SnapshotTracker(value)
    payload = tracker._payload
    if not isinstance(payload['client_order_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', payload['client_order_id']):
        raise ValueError('INVALID_BROKER_CLIENT_ID')
    quantity = contract.number(payload['qty']); price = contract.number(payload['limit_price'])
    with localcontext() as context:
        context.prec = 80
        if quantity * price > 200:raise ValueError('BROKER_PREVIEW_LOCAL_NOTIONAL_CAP')
    if payload['qty'] != contract.text(quantity) or payload['limit_price'] != contract.text(price):
        raise ValueError('BROKER_JOURNAL_CANONICAL_TERMS_REQUIRED')
    # Retain only allowlisted request fields, never extra plan metadata or keys.
    return dict(mode='DISCONNECTED_ALPACA_PAPER_PREVIEW', sendable=False,
                payload=payload, payload_sha256=contract.sha(payload))


def _observation(response, payload):
    current = contract.normalize(response, payload)
    # Cumulative observations only; never derive an incremental execution price.
    return dict(**payload, id=current['broker_order_id'], asset_class='crypto',
                status=current['status'], filled_qty=current['filled_quantity'],
                filled_avg_price=current['average_fill_price'], updated_at=current['updated_at'])


class BrokerRequestJournal:
    """Append-only bounded local fixture journal, bound to one supplied paper account."""
    def __init__(self, path, paper_account_id):
        self.path = Path(path)
        try:account = str(UUID(paper_account_id))
        except (ValueError,TypeError,AttributeError):raise ValueError('INVALID_PAPER_ACCOUNT_SCOPE')
        self.account_digest = hashlib.sha256(account.encode()).hexdigest()

    def _check_path(self):
        if self.path.is_symlink():raise ValueError('BROKER_JOURNAL_SYMLINK_NOT_ALLOWED')
        if self.path.exists() and self.path.stat().st_size > MAX_DATABASE_BYTES:
            raise ValueError('BROKER_JOURNAL_BYTE_CAP')

    def _connect(self, write=False):
        self._check_path()
        new = False
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd); new = True
            except FileExistsError:pass
        db = sqlite3.connect(self.path.resolve().as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=5)
        try:
            if write:
                db.execute('PRAGMA journal_mode=DELETE')
                db.execute('PRAGMA synchronous=FULL')
                page_size = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}')
                db.execute('BEGIN IMMEDIATE')
                if new:
                    db.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
                    self._insert(db, 1, 'BIND', dict(schema=SCHEMA, account_sha256=self.account_digest), ZERO)
            return db
        except BaseException:db.close();raise

    def _insert(self, db, seq, kind, body, previous):
        encoded = contract.canonical(body)
        if len(encoded.encode()) > MAX_EVENT_BYTES:raise ValueError('BROKER_JOURNAL_EVENT_BYTE_CAP')
        digest = contract.sha(dict(seq=seq,kind=kind,body=body,previous=previous))
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (seq,kind,encoded,previous,digest))

    def _load(self, db):
        rows = db.execute('SELECT seq,kind,body,previous,digest FROM events ORDER BY seq LIMIT ?', (MAX_EVENTS+1,)).fetchall()
        if not rows:raise ValueError('BROKER_JOURNAL_BINDING_MISSING')
        if len(rows)>MAX_EVENTS:raise ValueError('BROKER_JOURNAL_EVENT_CAP')
        previous=ZERO; requests={}; broker_ids={}
        for expected,(seq,kind,encoded,parent,digest) in enumerate(rows,1):
            if not isinstance(encoded,str) or len(encoded.encode())>MAX_EVENT_BYTES:
                raise ValueError('BROKER_JOURNAL_EVENT_BYTE_CAP')
            try:body=json.loads(encoded)
            except (ValueError,TypeError):raise ValueError('BROKER_JOURNAL_PAYLOAD_INVALID')
            if seq!=expected or parent!=previous or encoded!=contract.canonical(body) or digest!=contract.sha(dict(seq=seq,kind=kind,body=body,previous=parent)):
                raise ValueError('BROKER_JOURNAL_CHAIN_DISAGREEMENT')
            if seq==1:
                if kind!='BIND' or body!=dict(schema=SCHEMA,account_sha256=self.account_digest):
                    raise ValueError('BROKER_JOURNAL_ACCOUNT_OR_SCHEMA_DISAGREEMENT')
            else:self._apply(requests,broker_ids,kind,body)
            previous=digest
        return requests,broker_ids,len(rows),previous

    def _apply(self, requests, broker_ids, kind, body):
        if not isinstance(body,dict):raise ValueError('BROKER_JOURNAL_PAYLOAD_INVALID')
        if kind=='PREPARE':
            plan=_plan(body)
            if plan!=body:raise ValueError('BROKER_JOURNAL_PAYLOAD_INVALID')
            identity=plan['payload']['client_order_id']
            if identity in requests:raise ValueError('BROKER_JOURNAL_DUPLICATE_PREPARE')
            if len(requests)>=MAX_REQUESTS:raise ValueError('BROKER_JOURNAL_REQUEST_CAP')
            requests[identity]=dict(plan=plan,tracker=contract.SnapshotTracker(plan),uncertain=False)
            return
        if kind not in ('SUBMISSION_UNCERTAIN','OBSERVE') or set(body)!=({'client_order_id'} if kind=='SUBMISSION_UNCERTAIN' else {'client_order_id','response'}):
            raise ValueError('BROKER_JOURNAL_EVENT_INVALID')
        identity=body['client_order_id']
        if not isinstance(identity,str) or identity not in requests:raise ValueError('BROKER_JOURNAL_REQUEST_UNKNOWN')
        request=requests[identity]
        if kind=='SUBMISSION_UNCERTAIN':
            if request['uncertain']:raise ValueError('BROKER_JOURNAL_DUPLICATE_ATTEMPT')
            request['uncertain']=True
            return
        if not request['uncertain']:raise ValueError('BROKER_JOURNAL_NO_ATTEMPT_BOUNDARY')
        raw=body['response']
        if _observation(raw,request['plan']['payload'])!=raw:raise ValueError('BROKER_JOURNAL_PAYLOAD_INVALID')
        result=request['tracker'].observe(raw)
        if result['duplicate']:raise ValueError('BROKER_JOURNAL_DUPLICATE_OBSERVATION')
        assigned=result['snapshot']['broker_order_id']
        if assigned in broker_ids and broker_ids[assigned]!=identity:raise ValueError('BROKER_JOURNAL_BROKER_ID_REUSED')
        broker_ids[assigned]=identity

    def _summary(self, identity, request):
        latest=request['tracker'].status()['latest']
        state=('OBSERVED_TERMINAL_RECONCILIATION_REQUIRED' if latest['terminal'] else 'OBSERVED_NONTERMINAL_REVIEW_REQUIRED') if latest else ('SUBMISSION_UNKNOWN_LOOKUP_REQUIRED' if request['uncertain'] else 'PREPARED_UNSENT_DIAGNOSTIC')
        return dict(client_order_id=identity,payload_sha256=request['plan']['payload_sha256'],
                    recovery_state=state,snapshot=latest,lookup_by_original_client_id_required=request['uncertain'] and not latest,
                    submission_attempt_marked=request['uncertain'],automatic_retry=False,sendable=False,
                    broker_connected=False,ledger_applied=False,broker_account_reconciled=False)

    def _write(self, kind, body):
        if kind!='PREPARE' and not self.path.exists():
            raise ValueError('BROKER_JOURNAL_REQUEST_UNKNOWN')
        with closing(self._connect(write=True)) as db:
            try:
                requests,broker_ids,count,previous=self._load(db)
                identity=body['payload']['client_order_id'] if kind=='PREPARE' else body['client_order_id']
                existing=requests.get(identity)
                duplicate=False
                if kind=='PREPARE' and existing:
                    if body!=existing['plan']:raise ValueError('BROKER_JOURNAL_CLIENT_ID_CONFLICT')
                    duplicate=True
                elif kind=='SUBMISSION_UNCERTAIN' and existing and existing['uncertain']:duplicate=True
                elif kind=='OBSERVE' and existing:
                    # Normalize and compare under the same write transaction. New events
                    # are checked/applied once below, without another full-prefix replay.
                    raw=_observation(body['response'],existing['plan']['payload'])
                    body=dict(client_order_id=identity,response=raw)
                    duplicate=contract.normalize(raw,existing['plan']['payload'])==existing['tracker'].status()['latest']
                if not duplicate:
                    if count>=MAX_EVENTS:raise ValueError('BROKER_JOURNAL_EVENT_CAP')
                    self._apply(requests,broker_ids,kind,body)
                    self._insert(db,count+1,kind,body,previous)
                db.commit()  # Never acknowledge a request/observation before its commit returns.
                return dict(**self._summary(identity,requests[identity]),duplicate=duplicate)
            except BaseException:db.rollback();raise

    def prepare(self, plan):return self._write('PREPARE',_plan(plan))

    def mark_submission_uncertain(self, client_order_id):
        self._identity(client_order_id)
        return self._write('SUBMISSION_UNCERTAIN',dict(client_order_id=client_order_id))

    @staticmethod
    def _identity(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value):
            raise ValueError('INVALID_BROKER_CLIENT_ID')

    def observe(self, client_order_id, response):
        self._identity(client_order_id)
        # Validation and append share one locked replay; extras are stripped there.
        return self._write('OBSERVE',dict(client_order_id=client_order_id,response=response))

    def recovery(self):
        self._check_path()
        if not self.path.exists():
            requests={};count=0;head=ZERO
        else:
            with closing(self._connect()) as db:requests,_,count,head=self._load(db)
        return dict(mode='DISCONNECTED_DURABLE_BROKER_REQUEST_DIAGNOSTIC',requests=[self._summary(k,v) for k,v in requests.items()],
                    retained_events=count,head_sha256=head,automatic_resume=False,automatic_submission=False,
                    broker_connected=False,live_orders_enabled=False,ledger_applied=False,
                    limits=dict(requests=MAX_REQUESTS,events=MAX_EVENTS,database_bytes=MAX_DATABASE_BYTES),
                    scope='Caller-supplied paper account and order observations only. Hash-chain replay is local consistency, not broker attestation, encryption, account reconciliation or proof against tail deletion.')


def fixture_report():
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,
               min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    plan=contract.preview('nx_durable_fixture','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
    with tempfile.TemporaryDirectory(prefix='nexus-broker-journal-') as temporary:
        path=Path(temporary)/'fixture.sqlite3';account='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        journal=BrokerRequestJournal(path,account);journal.prepare(plan)
        journal.mark_submission_uncertain('nx_durable_fixture')
        unknown=BrokerRequestJournal(path,account).recovery()['requests'][0]['recovery_state']
        for i,(status,quantity,average) in enumerate((('partially_filled','1','99'),('pending_cancel','1','99'),('canceled','1','99'))):
            raw=dict(**plan['payload'],id='01234567-89ab-cdef-0123-456789abcdef',asset_class='crypto',
                     status=status,filled_qty=quantity,filled_avg_price=average,updated_at=f'2026-10-17T18:00:0{i}+00:00')
            journal.observe('nx_durable_fixture',raw)
        duplicate=BrokerRequestJournal(path,account).observe('nx_durable_fixture',raw)['duplicate']
        recovered=BrokerRequestJournal(path,account).recovery()
        return dict(mode='DISPOSABLE_DURABLE_BROKER_REQUEST_FIXTURE',passed=True,uncertain_recovery_state=unknown,
                    current_replay_duplicate=duplicate,recovered=recovered,broker_connected=False,ledger_applied=False,
                    limitations='Temporary injected observations and reopened connections; not broker integration, authenticated lookup, sustained operation or market execution.')


if __name__=='__main__':print(json.dumps(fixture_report(),sort_keys=True,indent=2))
