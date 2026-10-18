"""Replay-validated Alpaca paper request recovery journal.

Explicit local paths and caller-supplied account identity only. The journal has no transport,
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
from nexus import broker_lookup_policy as lookup
from nexus.sqlite_journal_guard import check_existing

MAX_REQUESTS = 128
MAX_EVENTS = 4096
MAX_EVENT_BYTES = 4096
MAX_DATABASE_BYTES = 64 * 1024 * 1024
ZERO = '0' * 64
SCHEMA = 'nexus.alpaca-paper-request-journal.v1'
ADMISSION_PROFILE = 'nexus.paper-submission-admission.v1'
ADMISSION_SCOPE = 'CALLER_WALL_SECONDS_NOT_SEND_OR_ARRIVAL'


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
    """Append-only bounded local journal, bound to one supplied paper account."""
    def __init__(self, path, paper_account_id):
        self.path = Path(path)
        try:account = str(UUID(paper_account_id))
        except (ValueError,TypeError,AttributeError):raise ValueError('INVALID_PAPER_ACCOUNT_SCOPE')
        self.account_digest = hashlib.sha256(account.encode()).hexdigest()

    def _check_path(self):
        check_existing(self.path, MAX_DATABASE_BYTES, 'BROKER_JOURNAL_',
            cap_code='BYTE_CAP', symlink_code='SYMLINK_NOT_ALLOWED')

    def _connect(self, write=False):
        self._check_path()
        new = False
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd); new = True
            except FileExistsError:pass
            self._check_path()
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
            else:
                if kind=='SUBMISSION_UNCERTAIN' and isinstance(body,dict) and 'admission' in body:
                    if not isinstance(body['admission'],dict) or body['admission'].get('expected_head_sha256')!=parent:
                        raise ValueError('BROKER_SUBMISSION_ADMISSION_HEAD_DISAGREEMENT')
                if kind=='LOOKUP_INTERRUPTED' and (not isinstance(body,dict) or body.get('expected_head')!=parent):
                    raise ValueError('BROKER_LOOKUP_REVIEW_HEAD_DISAGREEMENT')
                self._apply(requests,broker_ids,kind,body)
            previous=digest
        return requests,broker_ids,len(rows),previous

    def _apply(self, requests, broker_ids, kind, body, *, lookup_completion=False):
        if not isinstance(body,dict):raise ValueError('BROKER_JOURNAL_PAYLOAD_INVALID')
        if not lookup_completion and kind not in ('LOOKUP_BEGIN','LOOKUP_RESULT','LOOKUP_INTERRUPTED') and any(r['lookup']['pending'] for r in requests.values()):
            raise ValueError('BROKER_LOOKUP_PENDING_REVIEW_REQUIRED')
        if kind=='PREPARE':
            plan=_plan(body)
            if plan!=body:raise ValueError('BROKER_JOURNAL_PAYLOAD_INVALID')
            identity=plan['payload']['client_order_id']
            if identity in requests:raise ValueError('BROKER_JOURNAL_DUPLICATE_PREPARE')
            if len(requests)>=MAX_REQUESTS:raise ValueError('BROKER_JOURNAL_REQUEST_CAP')
            requests[identity]=dict(plan=plan,tracker=contract.SnapshotTracker(plan),uncertain=False,submission_admission=None,lookup=dict(attempts=0,pending=None,last_at=0,next_at=0,blocked=False,last_result=None,last_outcome=None,interruptions=0,last_interruption=None))
            return
        if kind in ('LOOKUP_BEGIN','LOOKUP_RESULT','LOOKUP_INTERRUPTED'):
            self._apply_lookup(requests,broker_ids,kind,body)
            return
        shapes=({'client_order_id'},{'client_order_id','admission'}) if kind=='SUBMISSION_UNCERTAIN' else ({'client_order_id','response'},)
        if kind not in ('SUBMISSION_UNCERTAIN','OBSERVE') or set(body) not in shapes:
            raise ValueError('BROKER_JOURNAL_EVENT_INVALID')
        identity=body['client_order_id']
        if not isinstance(identity,str) or identity not in requests:raise ValueError('BROKER_JOURNAL_REQUEST_UNKNOWN')
        request=requests[identity]
        if kind=='SUBMISSION_UNCERTAIN':
            if request['uncertain']:raise ValueError('BROKER_JOURNAL_DUPLICATE_ATTEMPT')
            if 'admission' in body:
                marker=body['admission']
                if (type(marker) is not dict or set(marker)!={'profile','clock_scope','evaluated_at','expected_head_sha256','payload_sha256'} or
                    marker['profile']!=ADMISSION_PROFILE or marker['clock_scope']!=ADMISSION_SCOPE or
                    type(marker['expected_head_sha256']) is not str or re.fullmatch('[0-9a-f]{64}',marker['expected_head_sha256']) is None or
                    marker['payload_sha256']!=request['plan']['payload_sha256']):
                    raise ValueError('BROKER_SUBMISSION_ADMISSION_INVALID')
                self._submission_time(requests,marker['evaluated_at'])
                if any(r['lookup']['blocked'] for r in requests.values()):raise ValueError('BROKER_SUBMISSION_ACCOUNT_REVIEW_REQUIRED')
                request['submission_admission']=dict(marker)
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

    def _apply_lookup(self, requests, broker_ids, kind, body):
        identity=body.get('client_order_id')
        if not isinstance(identity,str) or identity not in requests:raise ValueError('BROKER_JOURNAL_REQUEST_UNKNOWN')
        request=requests[identity]; state=request['lookup']; now=lookup.clock(body.get('evaluated_at'))
        if now<self._clock_floor(requests):raise ValueError('BROKER_LOOKUP_CLOCK_REGRESSION')
        if kind=='LOOKUP_INTERRUPTED':
            if set(body)!={'client_order_id','token','evaluated_at','expected_head','confirm_interrupted'} or body['confirm_interrupted'] is not True:
                raise ValueError('BROKER_LOOKUP_EVENT_INVALID')
            if not state['pending'] or body['token']!=state['pending']:
                raise ValueError('BROKER_LOOKUP_PENDING_TOKEN_REQUIRED')
            wait=min(5*2**(state['attempts']-1),60)
            state.update(pending=None,last_at=now,next_at=max(state['next_at'],now+wait),
                         interruptions=state['interruptions']+1,last_interruption=body,last_outcome='INTERRUPTED')
            return
        if kind=='LOOKUP_BEGIN':
            if set(body)!={'client_order_id','token','evaluated_at'}:raise ValueError('BROKER_LOOKUP_EVENT_INVALID')
            if not request['uncertain']:raise ValueError('BROKER_JOURNAL_NO_ATTEMPT_BOUNDARY')
            latest=request['tracker'].status()['latest']
            if latest and latest['terminal']:raise ValueError('BROKER_LOOKUP_TERMINAL_REQUIRES_RECONCILIATION')
            if any(r['lookup']['pending'] for r in requests.values()):raise ValueError('BROKER_LOOKUP_PENDING_REVIEW_REQUIRED')
            if any(r['lookup']['blocked'] for r in requests.values()):raise ValueError('BROKER_LOOKUP_ACCOUNT_REVIEW_REQUIRED')
            if now<max(r['lookup']['next_at'] for r in requests.values()):raise ValueError('BROKER_LOOKUP_COOLDOWN_ACTIVE')
            if state['attempts']>=lookup.MAX_LOOKUPS_PER_REQUEST:raise ValueError('BROKER_LOOKUP_ATTEMPT_CAP')
            expected=contract.sha(dict(account=self.account_digest,client_order_id=identity,attempt=state['attempts']+1,evaluated_at=now))
            if body['token']!=expected:raise ValueError('BROKER_LOOKUP_TOKEN_DISAGREEMENT')
            state.update(attempts=state['attempts']+1,pending=expected,last_at=now,next_at=now+lookup.MIN_LOOKUP_INTERVAL)
            return
        if set(body)!={'client_order_id','token','evaluated_at','outcome','retry_after_seconds','response'}:
            raise ValueError('BROKER_LOOKUP_EVENT_INVALID')
        if not state['pending'] or body['token']!=state['pending']:raise ValueError('BROKER_LOOKUP_PENDING_TOKEN_REQUIRED')
        outcome=body['outcome'];delay=body['retry_after_seconds']
        if not isinstance(outcome,str) or outcome not in lookup.OUTCOMES or type(delay) is not int or not 0<=delay<=lookup.MAX_RETRY_AFTER:
            raise ValueError('BROKER_LOOKUP_EVENT_INVALID')
        if (outcome=='RATE_LIMITED' and delay<1) or (outcome!='RATE_LIMITED' and delay!=0):raise ValueError('BROKER_LOOKUP_EVENT_INVALID')
        if outcome=='FOUND':
            raw=body['response']
            if _observation(raw,request['plan']['payload'])!=raw:raise ValueError('BROKER_JOURNAL_PAYLOAD_INVALID')
            current=contract.normalize(raw,request['plan']['payload'])
            if current!=request['tracker'].status()['latest']:
                self._apply(requests,broker_ids,'OBSERVE',dict(client_order_id=identity,response=raw),lookup_completion=True)
        elif body['response'] is not None:raise ValueError('BROKER_LOOKUP_EVENT_INVALID')
        wait=(delay if outcome=='RATE_LIMITED' else min(5*2**(state['attempts']-1),60) if outcome in ('NOT_FOUND','TIMEOUT','NETWORK_ERROR','SERVER_ERROR') else lookup.MIN_LOOKUP_INTERVAL)
        state.update(pending=None,last_at=now,next_at=max(state['next_at'],now+wait),
                     blocked=outcome in ('AUTH_DENIED','REVIEW_REQUIRED'),last_result=body,last_outcome=outcome)

    def _summary(self, identity, request):
        latest=request['tracker'].status()['latest']
        state=('OBSERVED_TERMINAL_RECONCILIATION_REQUIRED' if latest['terminal'] else 'OBSERVED_NONTERMINAL_REVIEW_REQUIRED') if latest else ('SUBMISSION_UNKNOWN_LOOKUP_REQUIRED' if request['uncertain'] else 'PREPARED_UNSENT_DIAGNOSTIC')
        return dict(client_order_id=identity,payload_sha256=request['plan']['payload_sha256'],
                    recovery_state=state,snapshot=latest,lookup_by_original_client_id_required=request['uncertain'] and not latest,
                    submission_attempt_marked=request['uncertain'],submission_admission=None if request['submission_admission'] is None else dict(request['submission_admission']),lookup=dict(attempts=request['lookup']['attempts'],attempt_limit=lookup.MAX_LOOKUPS_PER_REQUEST,pending=request['lookup']['pending'] is not None,next_allowed_at=request['lookup']['next_at'],review_blocked=request['lookup']['blocked'],interruptions=request['lookup']['interruptions'],last_outcome=request['lookup']['last_outcome']),automatic_retry=False,sendable=False,
                    broker_connected=False,ledger_applied=False,broker_account_reconciled=False)

    @staticmethod
    def _recovery_reserve(requests):
        # Protect a next original-ID lookup begin/result pair for each unresolved
        # attempt. A pending lookup already consumed begin and needs one result.
        return sum(1 if r['lookup']['pending'] else 2 for r in requests.values()
            if r['uncertain'] and not ((r['tracker'].status()['latest'] or {}).get('terminal',False)))

    @classmethod
    def _capacity(cls,requests,count):
        remaining=max(0,MAX_EVENTS-count);reserved=cls._recovery_reserve(requests)
        return dict(event_slots_remaining=remaining,recovery_slots_reserved=reserved,
            ordinary_event_slots_remaining=max(0,remaining-reserved),recovery_reserve_deficit=max(0,reserved-remaining),
            scope='Local event headroom only; no byte/disk/provider quota guarantee, reservation ownership, order routing or resubmission permission.')

    @staticmethod
    def _clock_floor(requests):
        return max((max(r['lookup']['last_at'],(r['submission_admission'] or {}).get('evaluated_at',0)) for r in requests.values()),default=0)

    @staticmethod
    def _submission_time(requests,value):
        now=lookup.clock(value)
        if now<BrokerRequestJournal._clock_floor(requests):raise ValueError('BROKER_SUBMISSION_CLOCK_REGRESSION')
        # Conservative reuse of a known retained rate-limit block, not proof of
        # provider-wide POST quotas. Terminal observations/cleanup remain allowed.
        deadline=max((r['lookup']['next_at'] for r in requests.values()
            if r['lookup']['last_outcome']=='RATE_LIMITED'),default=0)
        if now<deadline:raise ValueError('BROKER_SUBMISSION_ACCOUNT_COOLDOWN_ACTIVE')
        return now

    def _write(self, kind, body, *, expected_head=None, expected_payload_sha256=None, admission=None):
        if kind!='PREPARE' and not self.path.exists():
            raise ValueError('BROKER_JOURNAL_REQUEST_UNKNOWN')
        with closing(self._connect(write=True)) as db:
            try:
                requests,broker_ids,count,previous=self._load(db)
                identity=body['payload']['client_order_id'] if kind=='PREPARE' else body['client_order_id']
                existing=requests.get(identity)
                if expected_head is not None:
                    if previous!=expected_head:raise ValueError('BROKER_SUBMISSION_HEAD_CHANGED')
                    if not existing or existing['plan']['payload_sha256']!=expected_payload_sha256:raise ValueError('BROKER_SUBMISSION_TERMS_CHANGED')
                    if existing['uncertain']:raise ValueError('BROKER_SUBMISSION_ALREADY_MARKED_LOOKUP_REQUIRED')
                    if any(r['lookup']['blocked'] for r in requests.values()):raise ValueError('BROKER_SUBMISSION_ACCOUNT_REVIEW_REQUIRED')
                admitted=self._submission_time(requests,admission()) if admission is not None else None
                if kind=='SUBMISSION_UNCERTAIN' and expected_head is not None:
                    body=dict(body,admission=dict(profile=ADMISSION_PROFILE,clock_scope=ADMISSION_SCOPE,evaluated_at=admitted,
                        expected_head_sha256=previous,payload_sha256=expected_payload_sha256))
                if any(r['lookup']['pending'] for r in requests.values()):raise ValueError('BROKER_LOOKUP_PENDING_REVIEW_REQUIRED')
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
                    reserved_before=self._recovery_reserve(requests)
                    self._apply(requests,broker_ids,kind,body)
                    reserved_after=self._recovery_reserve(requests)
                    # A terminal observation releases its own reserve. Preserve
                    # that cleanup path even when older history has a deficit.
                    if count+1+reserved_after>MAX_EVENTS and reserved_after>=reserved_before:raise ValueError('BROKER_JOURNAL_RECOVERY_RESERVE')
                    self._insert(db,count+1,kind,body,previous)
                if admission is not None and self._submission_time(requests,admission())<admitted:raise ValueError('BROKER_SUBMISSION_CLOCK_REGRESSION')
                db.commit()  # Never acknowledge a request/observation before its commit returns.
                return dict(**self._summary(identity,requests[identity]),duplicate=duplicate)
            except BaseException:db.rollback();raise

    def prepare(self, plan):return self._write('PREPARE',_plan(plan))

    def mark_submission_uncertain(self, client_order_id):
        self._identity(client_order_id)
        return self._write('SUBMISSION_UNCERTAIN',dict(client_order_id=client_order_id))

    def submission_review(self,client_order_id,*,evaluated_at):
        """Read local marker blockers only; no reservation or caller admission.

        A clear snapshot is neither transport permission nor account/risk/funds
        acceptance. The checked writer must re-evaluate its own current state.
        """
        self._identity(client_order_id);now=lookup.clock(evaluated_at);self._check_path()
        present=self.path.exists();requests={};count=0;head=ZERO
        if present:
            with closing(self._connect()) as db:requests,_,count,head=self._load(db)
        request=requests.get(client_order_id);reasons=[]
        floor=self._clock_floor(requests)
        deadline=max((r['lookup']['next_at'] for r in requests.values()
            if r['lookup']['last_outcome']=='RATE_LIMITED'),default=0)
        if not request:reasons.append('REQUEST_NOT_PREPARED')
        if request and request['uncertain']:reasons.append('ORIGINAL_ID_RECOVERY_REQUIRED')
        if any(r['lookup']['blocked'] for r in requests.values()):reasons.append('ACCOUNT_REVIEW_REQUIRED')
        if any(r['lookup']['pending'] for r in requests.values()):reasons.append('LOOKUP_PENDING_REVIEW_REQUIRED')
        if now<floor:reasons.append('CLOCK_REGRESSION')
        if now<deadline:reasons.append('ACCOUNT_COOLDOWN_ACTIVE')
        required=1+self._recovery_reserve(requests)+(2 if request and not request['uncertain'] else 0)
        if request and not request['uncertain'] and count+required>MAX_EVENTS:reasons.append('RECOVERY_RESERVE_REQUIRED')
        return dict(mode='READ_ONLY_BROKER_SUBMISSION_REVIEW',read_only=True,journal_present=present,
            client_order_id=client_order_id,account_sha256=self.account_digest,head_sha256=head,
            payload_sha256=request['plan']['payload_sha256'] if request else None,evaluated_at=now,
            lookup_clock_floor=floor,retained_rate_limit_until=deadline,wait_seconds=max(0,deadline-now),
            status='BLOCKED' if reasons else 'LOCAL_MARKER_CHECKS_CLEAR',reasons=reasons,
            capacity=self._capacity(requests,count),local_marker_checks_clear=not reasons,
            capacity_reserved=False,admission_checked=False,sendable=False,routing_authorized=False,
            transport_invoked=False,automatic_submission=False,automatic_retry=False,
            broker_connected=False,broker_account_reconciled=False,ledger_applied=False,
            live_orders_enabled=False,money_movement_enabled=False,release_approved=False,
            scope='Sampled local journal only. A clear review does not reserve capacity, check caller permission/risk/funds, approve a broker account or permit IO. Recheck current head/terms/admission with the checked marker and separately authorize any transport.')

    def begin_submission_checked(self,client_order_id,*,expected_head,expected_payload_sha256,admission):
        """One new durable attempt marker only; no transport or send permission.

        Check caller admission after SQLite write ownership and before commit.
        Lost return/duplicate calls must use original-ID recovery, never resend.
        """
        self._identity(client_order_id)
        if not callable(admission):raise ValueError('BROKER_SUBMISSION_ADMISSION_REQUIRED')
        for value in (expected_head,expected_payload_sha256):
            if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:raise ValueError('BROKER_SUBMISSION_REFERENCE_INVALID')
        result=self._write('SUBMISSION_UNCERTAIN',dict(client_order_id=client_order_id),
            expected_head=expected_head,expected_payload_sha256=expected_payload_sha256,admission=admission)
        return dict(**result,attempt_marker_commit_observed=True,transport_invoked=False,
            routing_authorized=False,live_orders_enabled=False)

    @staticmethod
    def _identity(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value):
            raise ValueError('INVALID_BROKER_CLIENT_ID')

    def observe(self, client_order_id, response):
        self._identity(client_order_id)
        # Validation and append share one locked replay; extras are stripped there.
        return self._write('OBSERVE',dict(client_order_id=client_order_id,response=response))

    def _lookup_write(self, identity, now, result=None, admission=None, expected_head=None):
        self._identity(identity);lookup.clock(now)
        if not self.path.exists():raise ValueError('BROKER_JOURNAL_REQUEST_UNKNOWN')
        with closing(self._connect(write=True)) as db:
            try:
                requests,broker_ids,count,previous=self._load(db)
                if expected_head is not None and previous!=expected_head:raise ValueError('BROKER_LOOKUP_HEAD_CHANGED')
                if identity not in requests:raise ValueError('BROKER_JOURNAL_REQUEST_UNKNOWN')
                request=requests[identity];state=request['lookup']
                if admission is not None:
                    sampled=lookup.clock(admission())
                    if sampled<now:raise ValueError('BROKER_LOOKUP_CLOCK_REGRESSION')
                    now=sampled
                if callable(result):result=result(now)
                if result is None:
                    kind='LOOKUP_BEGIN'
                    # Keep a completion slot; ordinary writes are fenced while pending.
                    others={key:value for key,value in requests.items() if key!=identity}
                    if count+2+self._recovery_reserve(others)>MAX_EVENTS:raise ValueError('BROKER_JOURNAL_LOOKUP_COMPLETION_RESERVE')
                    token=contract.sha(dict(account=self.account_digest,client_order_id=identity,attempt=state['attempts']+1,evaluated_at=now))
                    body=dict(client_order_id=identity,token=token,evaluated_at=now)
                else:
                    kind='LOOKUP_RESULT';body=dict(client_order_id=identity,evaluated_at=now,**result)
                    if body['outcome']=='FOUND':body['response']=_observation(body['response'],request['plan']['payload'])
                    if body==state['last_result']:
                        db.commit()
                        return dict(**self._summary(identity,request),duplicate=True,lookup_token=body['token'])
                    if count>=MAX_EVENTS:raise ValueError('BROKER_JOURNAL_EVENT_CAP')
                self._apply(requests,broker_ids,kind,body)
                self._insert(db,count+1,kind,body,previous)
                if admission is not None and lookup.clock(admission())<now:raise ValueError('BROKER_LOOKUP_CLOCK_REGRESSION')
                db.commit()
                return dict(**self._summary(identity,request),duplicate=False,lookup_token=body['token'],
                            hypothetical_lookup=dict(method='GET',url=lookup.LOOKUP_URL,query=dict(client_order_id=identity),sendable=False))
            except BaseException:db.rollback();raise

    def begin_lookup(self, client_order_id, *, evaluated_at):
        return self._lookup_write(client_order_id,evaluated_at)

    def finish_lookup(self, client_order_id, token, *, evaluated_at, status_code=None, transport_error=None, retry_after=None, response=None):
        outcome=lookup.classify(status_code=status_code,transport_error=transport_error,retry_after=retry_after,evaluated_at=evaluated_at)
        return self._lookup_write(client_order_id,evaluated_at,dict(token=token,**outcome,response=response if outcome['outcome']=='FOUND' else None))

    def lookup_request(self, client_order_id):
        """Copy immutable original terms without initializing or admitting IO."""
        self._identity(client_order_id);self._check_path()
        if not self.path.exists():raise ValueError('BROKER_JOURNAL_REQUEST_UNKNOWN')
        with closing(self._connect()) as db:
            requests,_,_,head=self._load(db)
        if client_order_id not in requests:raise ValueError('BROKER_JOURNAL_REQUEST_UNKNOWN')
        return dict(plan=json.loads(contract.canonical(requests[client_order_id]['plan'])),
                    account_sha256=self.account_digest,head_sha256=head)

    def begin_lookup_checked(self, client_order_id, *, admission, expected_head=None):
        """Internal runtime hook: sample permission/time again under write admission."""
        if expected_head is not None and (type(expected_head) is not str or re.fullmatch('[0-9a-f]{64}',expected_head) is None):raise ValueError('BROKER_LOOKUP_HEAD_INVALID')
        return self._lookup_write(client_order_id,lookup.clock(admission()),admission=admission,**({'expected_head':expected_head} if expected_head is not None else {}))

    def finish_lookup_checked(self, client_order_id, token, *, admission, status_code=None,
                              transport_error=None, retry_after=None, response=None):
        def result(now):
            outcome=lookup.classify(status_code=status_code,transport_error=transport_error,
                                    retry_after=retry_after,evaluated_at=now)
            return dict(token=token,**outcome,response=response if outcome['outcome']=='FOUND' else None)
        return self._lookup_write(client_order_id,lookup.clock(admission()),result,admission=admission)

    def observations_at_heads(self,heads):
        """Read selected historical prefixes after validating the complete tail."""
        if type(heads) not in (tuple,list) or len(heads)>128:raise ValueError('BROKER_PREFIX_HEADS_INVALID')
        if any(type(h) is not str or re.fullmatch('[0-9a-f]{64}',h) is None for h in heads):raise ValueError('BROKER_PREFIX_HEADS_INVALID')
        wanted=set(heads);self._check_path();found={};current=ZERO;present=self.path.exists()
        if present:
            with closing(self._connect()) as db:
                db.execute('BEGIN')
                _,_,_,current=self._load(db)
                rows=db.execute('SELECT seq,kind,body,digest FROM events ORDER BY seq LIMIT ?',(MAX_EVENTS+1,)).fetchall()
                requests={};broker_ids={}
                for seq,kind,encoded,digest in rows:
                    if seq>1:self._apply(requests,broker_ids,kind,json.loads(encoded))
                    if digest in wanted:
                        found[digest]=[self._summary(identity,r) for identity,r in sorted(requests.items())]
        return dict(mode='READ_ONLY_REQUEST_OBSERVATION_PREFIXES',read_only=True,
            account_sha256=self.account_digest,current_head_sha256=current,
            observations_by_head=found,missing_heads=sorted(wanted-set(found)),
            complete_current_tail_replayed=present,admission_reserved=False,
            source_attested=False,automatic_retry=False,ready_for_routing=False,
            broker_connected=False,ledger_applied=False,live_orders_enabled=False,
            limitations='Bounded historical local replay only; complete current tail is validated in the same read transaction. Prefix hashes are not source signatures, encryption, valid-tail deletion/ABA protection or permission. Actual IDs and reports are private; no writes or missing-state initialization.')

    def lookup_review(self,*,evaluated_at):
        """Passive bounded next-lookup selection; no reservation, vault or IO."""
        now=lookup.clock(evaluated_at);self._check_path();requests={};count=0;head=ZERO
        present=self.path.exists()
        if present:
            with closing(self._connect()) as db:requests,_,count,head=self._load(db)
        reasons=[];eligible=[];terminal=exhausted=prepared=0
        floor=self._clock_floor(requests)
        deadline=max((r['lookup']['next_at'] for r in requests.values()),default=0)
        if any(r['lookup']['pending'] for r in requests.values()):reasons.append('PENDING_LOOKUP_REVIEW_REQUIRED')
        if any(r['lookup']['blocked'] for r in requests.values()):reasons.append('ACCOUNT_LOOKUP_REVIEW_REQUIRED')
        if now<floor:reasons.append('LOOKUP_CLOCK_REGRESSION')
        if now<deadline:reasons.append('LOOKUP_COOLDOWN_ACTIVE')
        for identity,r in requests.items():
            if not r['uncertain']:prepared+=1;continue
            if r['lookup']['pending']:continue
            latest=r['tracker'].status()['latest']
            if latest and latest['terminal']:terminal+=1;continue
            if r['lookup']['attempts']>=lookup.MAX_LOOKUPS_PER_REQUEST:exhausted+=1;continue
            eligible.append((r['lookup']['next_at'],r['lookup']['attempts'],identity))
        if not eligible:reasons.append('NO_ELIGIBLE_UNRESOLVED_REQUEST')
        if eligible and count+2+self._recovery_reserve({k:r for k,r in requests.items() if k!=min(eligible)[2]})>MAX_EVENTS:reasons.append('LOOKUP_RECOVERY_HEADROOM_REQUIRED')
        selected=min(eligible)[2] if eligible and not reasons else None
        return dict(mode='PAPER_RECOVERY_SELECTION',read_only=True,journal_present=present,
            account_sha256=self.account_digest,head_sha256=head,evaluated_at=now,
            status='LOCAL_CANDIDATE_SELECTED' if selected else 'REVIEW_REQUIRED',
            selected_client_order_id=selected,eligible_requests=len(eligible),
            skipped_prepared=prepared,skipped_terminal=terminal,skipped_attempt_cap=exhausted,
            next_allowed_at=deadline,wait_seconds=max(0,deadline-now),reasons=reasons,
            selection_policy='EARLIEST_RETAINED_DUE_TIME_THEN_ATTEMPTS_THEN_CLIENT_ID',
            capacity=self._capacity(requests,count),lookup_admitted=False,admission_reserved=False,
            automatic_continuation=False,automatic_retry=False,automatic_submission=False,
            broker_connected=False,live_orders_enabled=False,money_movement_enabled=False,
            ready_for_routing=False,release_approved=False,
            scope='Sampled local next-lookup choice only; skipped terminal/exhausted requests still need reconciliation/review. A candidate reserves nothing and grants no provider/trading permission. One separately confirmed read must recheck the exact head under write ownership.')

    def pending_lookup(self):
        """Read-only review snapshot; no timeout inference, IO admission or repair."""
        self._check_path()
        requests={};head=ZERO
        if self.path.exists():
            with closing(self._connect()) as db:requests,_,_,head=self._load(db)
        pending=[(identity,r) for identity,r in requests.items() if r['lookup']['pending']]
        if len(pending)>1:raise ValueError('BROKER_LOOKUP_PENDING_STATE_DISAGREEMENT')
        result=dict(mode='READ_ONLY_PENDING_BROKER_LOOKUP',read_only=True,head_sha256=head,
                    pending=bool(pending),automatic_resume=False,automatic_submission=False,
                    broker_connected=False,live_orders_enabled=False)
        if pending:
            identity,request=pending[0];state=request['lookup']
            result.update(client_order_id=identity,lookup_token=state['pending'],
                          payload_sha256=request['plan']['payload_sha256'],started_at=state['last_at'],
                          attempts=state['attempts'],next_allowed_at=state['next_at'],
                          review_action='Explicitly record interruption only after reviewing the current ticket; this cannot cancel a network request or resolve the original order.')
        return result

    def interrupt_lookup(self, client_order_id, token, *, expected_head, evaluated_at, confirm_interrupted=False):
        """Explicit local review event, never cancellation, repair or resubmission."""
        self._identity(client_order_id);lookup.clock(evaluated_at)
        if confirm_interrupted is not True:raise ValueError('BROKER_LOOKUP_INTERRUPTION_CONFIRMATION_REQUIRED')
        for value in (token,expected_head):
            if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}',value):
                raise ValueError('INVALID_BROKER_LOOKUP_REVIEW_REFERENCE')
        if not self.path.exists():raise ValueError('BROKER_JOURNAL_REQUEST_UNKNOWN')
        body=dict(client_order_id=client_order_id,token=token,expected_head=expected_head,
                  evaluated_at=evaluated_at,confirm_interrupted=True)
        with closing(self._connect(write=True)) as db:
            try:
                requests,broker_ids,count,previous=self._load(db)
                if client_order_id not in requests:raise ValueError('BROKER_JOURNAL_REQUEST_UNKNOWN')
                request=requests[client_order_id]
                if body==request['lookup']['last_interruption']:
                    db.commit()
                    return dict(**self._summary(client_order_id,request),duplicate=True,interruption_recorded=True)
                if expected_head!=previous:raise ValueError('BROKER_LOOKUP_REVIEW_HEAD_DISAGREEMENT')
                if count>=MAX_EVENTS:raise ValueError('BROKER_JOURNAL_EVENT_CAP')
                self._apply(requests,broker_ids,'LOOKUP_INTERRUPTED',body)
                self._insert(db,count+1,'LOOKUP_INTERRUPTED',body,previous);db.commit()
                return dict(**self._summary(client_order_id,request),duplicate=False,interruption_recorded=True)
            except BaseException:db.rollback();raise

    def recovery(self):
        self._check_path()
        if not self.path.exists():
            requests={};count=0;head=ZERO
        else:
            with closing(self._connect()) as db:requests,_,count,head=self._load(db)
        return dict(mode='DISCONNECTED_DURABLE_BROKER_REQUEST_DIAGNOSTIC',requests=[self._summary(k,v) for k,v in requests.items()],
                    retained_events=count,head_sha256=head,capacity=self._capacity(requests,count),automatic_resume=False,automatic_submission=False,
                    broker_connected=False,live_orders_enabled=False,ledger_applied=False,
                    limits=dict(requests=MAX_REQUESTS,events=MAX_EVENTS,database_bytes=MAX_DATABASE_BYTES),
                    scope='Caller-supplied paper account and order observations only. Hash-chain replay is local consistency, not broker attestation, encryption, account reconciliation or proof against tail deletion.')


def submission_admission_fixture():
    """Disposable composition; no transport, credentials or runtime path."""
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,
        min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    original=contract.preview('nx_admission_fixture','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
    with tempfile.TemporaryDirectory(prefix='nexus-submission-admission-') as directory:
        journal=BrokerRequestJournal(Path(directory)/'request.sqlite3','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        journal.prepare(original)
        def begin(admission):
            retained=journal.lookup_request('nx_admission_fixture')
            return journal.begin_submission_checked('nx_admission_fixture',expected_head=retained['head_sha256'],
                expected_payload_sha256=retained['plan']['payload_sha256'],admission=admission)
        before=journal.path.read_bytes();preflight=journal.submission_review('nx_admission_fixture',evaluated_at=1000)
        preflight_passive=preflight['local_marker_checks_clear'] and journal.path.read_bytes()==before and not preflight['sendable'] and not preflight['capacity_reserved']
        revoked=False
        try:begin(lambda:False)
        except ValueError:revoked=journal.path.read_bytes()==before
        marked=begin(lambda:1000);reserved=journal.recovery()['capacity']['recovery_slots_reserved']==2
        marker=marked['submission_admission'];cold=BrokerRequestJournal(journal.path,'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        marker_replayed=cold.recovery()['requests'][0]['submission_admission']==marker and marker==dict(
            profile=ADMISSION_PROFILE,clock_scope=ADMISSION_SCOPE,evaluated_at=1000,
            expected_head_sha256=preflight['head_sha256'],payload_sha256=original['payload_sha256'])
        marker_bytes=journal.path.read_bytes();marker_clock_blocked=False
        try:cold.begin_lookup('nx_admission_fixture',evaluated_at=999)
        except ValueError as error:marker_clock_blocked=str(error)=='BROKER_LOOKUP_CLOCK_REGRESSION' and journal.path.read_bytes()==marker_bytes
        marked_review=journal.submission_review('nx_admission_fixture',evaluated_at=1000)
        marked_blocked='ORIGINAL_ID_RECOVERY_REQUIRED' in marked_review['reasons'] and not marked_review['local_marker_checks_clear']
        duplicate=False;before=journal.path.read_bytes()
        try:begin(lambda:1001)
        except ValueError as error:duplicate=str(error)=='BROKER_SUBMISSION_ALREADY_MARKED_LOOKUP_REQUIRED' and journal.path.read_bytes()==before
        ticket=journal.begin_lookup('nx_admission_fixture',evaluated_at=1001)
        completion=journal.recovery()['capacity']['recovery_slots_reserved']==1
        raw=dict(**original['payload'],id='01234567-89ab-cdef-0123-456789abcdef',asset_class='crypto',
            status='filled',filled_qty='2',filled_avg_price='99',updated_at='2026-10-17T18:00:00+00:00')
        journal.finish_lookup('nx_admission_fixture',ticket['lookup_token'],evaluated_at=1002,status_code=200,response=raw)
        state=BrokerRequestJournal(journal.path,'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa').recovery()
        blocked=BrokerRequestJournal(Path(directory)/'blocked.sqlite3','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        blocked.prepare(original);blocked.mark_submission_uncertain('nx_admission_fixture')
        legacy_untimed=blocked.recovery()['requests'][0]['submission_admission'] is None
        token=blocked.begin_lookup('nx_admission_fixture',evaluated_at=1000)['lookup_token']
        blocked.finish_lookup('nx_admission_fixture',token,evaluated_at=1001,status_code=401)
        other=contract.preview('nx_blocked_fixture','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
        blocked.prepare(other);reference=blocked.lookup_request('nx_blocked_fixture');before=blocked.path.read_bytes();account_block=False
        try:blocked.begin_submission_checked('nx_blocked_fixture',expected_head=reference['head_sha256'],expected_payload_sha256=other['payload_sha256'],admission=lambda:1002)
        except ValueError as error:account_block=str(error)=='BROKER_SUBMISSION_ACCOUNT_REVIEW_REQUIRED' and blocked.path.read_bytes()==before
        limited=BrokerRequestJournal(Path(directory)/'limited.sqlite3','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        limited.prepare(original);limited.prepare(other);limited.mark_submission_uncertain('nx_admission_fixture')
        token=limited.begin_lookup('nx_admission_fixture',evaluated_at=1000)['lookup_token']
        limited.finish_lookup('nx_admission_fixture',token,evaluated_at=1001,status_code=429,retry_after='30')
        reference=limited.lookup_request('nx_blocked_fixture');before=limited.path.read_bytes();denials=[]
        for stamp,code in ((1000,'BROKER_SUBMISSION_CLOCK_REGRESSION'),(1020,'BROKER_SUBMISSION_ACCOUNT_COOLDOWN_ACTIVE')):
            try:limited.begin_submission_checked('nx_blocked_fixture',expected_head=reference['head_sha256'],expected_payload_sha256=other['payload_sha256'],admission=lambda:stamp)
            except ValueError as error:denials.append(str(error)==code and limited.path.read_bytes()==before)
        resumed=limited.begin_submission_checked('nx_blocked_fixture',expected_head=reference['head_sha256'],expected_payload_sha256=other['payload_sha256'],admission=lambda:1031)
        clocks=BrokerRequestJournal(Path(directory)/'clocks.sqlite3','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        clocks.prepare(original);clocks.mark_submission_uncertain('nx_admission_fixture')
        clock_denials=[]
        for finishing in (False,True):
            before=clocks.path.read_bytes();samples=iter([1001,1001,1000] if finishing else [1000,1000,999])
            try:
                if finishing:clocks.finish_lookup_checked('nx_admission_fixture',ticket['lookup_token'],admission=lambda:next(samples),status_code=404)
                else:clocks.begin_lookup_checked('nx_admission_fixture',admission=lambda:next(samples))
            except ValueError as error:clock_denials.append(str(error)=='BROKER_LOOKUP_CLOCK_REGRESSION' and clocks.path.read_bytes()==before)
            if not finishing:ticket=clocks.begin_lookup_checked('nx_admission_fixture',admission=lambda:1000)
        return dict(source='TEMPORARY_LOCAL_SUBMISSION_ADMISSION_FIXTURE',
            admission_marker_replayed=marker_replayed,admission_clock_floor_retained=marker_clock_blocked,legacy_marker_untimed=legacy_untimed,
            preflight_passive_no_authority=preflight_passive,preflight_marked_requires_recovery=marked_blocked,
            lookup_begin_clock_rollback=bool(clock_denials and clock_denials[0]),lookup_result_clock_rollback=len(clock_denials)==2 and clock_denials[1],
            retained_clock_rejected=bool(denials and denials[0]),rate_cooldown_rejected=len(denials)==2 and denials[1],rate_deadline_allows_marker_only=resumed['attempt_marker_commit_observed'] and not resumed['routing_authorized'],
            account_review_blocks_begin=account_block,marker_commit_observed=marked['attempt_marker_commit_observed'],recovery_pair_reserved=reserved,
            duplicate_begin_rejected=duplicate,revoked_admission_unchanged=revoked,
            pending_lookup_completion_reserved=completion,terminal_lookup_releases_reserve=state['capacity']['recovery_slots_reserved']==0,
            reopened_terminal_observation=state['requests'][0]['recovery_state']=='OBSERVED_TERMINAL_RECONCILIATION_REQUIRED',
            transport_invoked=False,routing_authorized=False,broker_connected=False,ledger_applied=False,
            broker_account_reconciled=False,live_orders_enabled=False,money_movement_enabled=False,release_approved=False)


def fixture_report():
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,
               min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    plan=contract.preview('nx_durable_fixture','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
    with tempfile.TemporaryDirectory(prefix='nexus-broker-journal-') as temporary:
        path=Path(temporary)/'fixture.sqlite3';account='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        journal=BrokerRequestJournal(path,account);journal.prepare(plan)
        before=journal.path.read_bytes();preflight=journal.submission_review('nx_durable_fixture',evaluated_at=1000)
        passive=journal.path.read_bytes()==before
        journal.mark_submission_uncertain('nx_durable_fixture')
        after_review=journal.submission_review('nx_durable_fixture',evaluated_at=1000)
        unknown=BrokerRequestJournal(path,account).recovery()['requests'][0]['recovery_state']
        for i,(status,quantity,average) in enumerate((('partially_filled','1','99'),('pending_cancel','1','99'),('canceled','1','99'))):
            raw=dict(**plan['payload'],id='01234567-89ab-cdef-0123-456789abcdef',asset_class='crypto',
                     status=status,filled_qty=quantity,filled_avg_price=average,updated_at=f'2026-10-17T18:00:0{i}+00:00')
            journal.observe('nx_durable_fixture',raw)
        duplicate=BrokerRequestJournal(path,account).observe('nx_durable_fixture',raw)['duplicate']
        recovered=BrokerRequestJournal(path,account).recovery()
        pacing=BrokerRequestJournal(Path(temporary)/'lookup.sqlite3',account)
        pacing.prepare(plan);pacing.mark_submission_uncertain('nx_durable_fixture')
        ticket=pacing.begin_lookup('nx_durable_fixture',evaluated_at=1000)['lookup_token']
        timeout=pacing.finish_lookup('nx_durable_fixture',ticket,evaluated_at=1001,transport_error='TIMEOUT')
        pacing=BrokerRequestJournal(pacing.path,account)
        ticket=pacing.begin_lookup('nx_durable_fixture',evaluated_at=1006)['lookup_token']
        limited=pacing.finish_lookup('nx_durable_fixture',ticket,evaluated_at=1007,status_code=429,retry_after='30')
        pacing.begin_lookup('nx_durable_fixture',evaluated_at=1037)
        pacing=BrokerRequestJournal(pacing.path,account)
        review=pacing.pending_lookup()
        interrupted=pacing.interrupt_lookup('nx_durable_fixture',review['lookup_token'],
                expected_head=review['head_sha256'],evaluated_at=1038,confirm_interrupted=True)
        late_rejected=False
        try:pacing.finish_lookup('nx_durable_fixture',review['lookup_token'],evaluated_at=1039,status_code=404)
        except ValueError as exc:late_rejected=str(exc)=='BROKER_LOOKUP_PENDING_TOKEN_REQUIRED'
        if not late_rejected:raise ValueError('BROKER_FIXTURE_LATE_COMPLETION_NOT_FENCED')
        from nexus.broker_request_activity_review import fixture_report as link_fixture
        from nexus.paper_recovery_step import fixture_report as step_fixture
        return dict(mode='DISPOSABLE_DURABLE_BROKER_REQUEST_FIXTURE',passed=True,bounded_recovery_step=step_fixture(),activity_link_review=link_fixture(),submission_review=dict(read_only=True,passive=passive,clear_before_marker=preflight['local_marker_checks_clear'],recovery_required_after_marker='ORIGINAL_ID_RECOVERY_REQUIRED' in after_review['reasons'],sendable=False,routing_authorized=False,capacity_reserved=False),uncertain_recovery_state=unknown,
                    current_replay_duplicate=duplicate,recovered=recovered,lookup_recovery=dict(timeout_keeps_uncertainty=timeout['lookup_by_original_client_id_required'],rate_limit_next_allowed_at=limited['lookup']['next_allowed_at'],rate_limit_wait_seconds=30,attempts=limited['lookup']['attempts'],interruption_recorded=interrupted['interruption_recorded'],interruption_keeps_uncertainty=interrupted['lookup_by_original_client_id_required'],late_completion_rejected=late_rejected,attempts_after_interruption=interrupted['lookup']['attempts'],automatic_retry=False),broker_connected=False,ledger_applied=False,
                    limitations='Temporary injected observations and reopened connections; not broker integration, authenticated lookup, sustained operation or market execution.')


if __name__=='__main__':print(json.dumps(fixture_report(),sort_keys=True,indent=2))
