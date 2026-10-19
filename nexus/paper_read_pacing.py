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
LEGACY_OPERATION='LEGACY_UNSPECIFIED'
OPERATIONS=('ACCOUNT_INVENTORY','BTC_ASSET_RULES','ACTIVITY_COLLECTION','ORIGINAL_ID_RECOVERY','BTC_QUOTE_OBSERVATION')
RECEIPT_PROFILE_V1='nexus.paper-read-operation-receipt.v1'
RECEIPT_PROFILE='nexus.paper-read-operation-receipt.v2'  # Retained target-only profile.
RECEIPT_PROFILE_V3='nexus.paper-read-operation-receipt.v3'
MAX_EVENT_BYTES=1536
LEGACY_MAX_EVENT_BYTES=1024
OPERATION_PATTERNS={
    'ACCOUNT_INVENTORY':(('ACCOUNT','POSITIONS','OPEN_ORDERS','ACCOUNT'),),
    'BTC_ASSET_RULES':(('ACCOUNT','ASSET','ACCOUNT'),),
    'BTC_QUOTE_OBSERVATION':(('ACCOUNT','QUOTE','ACCOUNT'),),
    'ACTIVITY_COLLECTION':(('ACCOUNT','ACTIVITY_PAGE','ACCOUNT'),('ACCOUNT','ACTIVITY_PAGE','ACTIVITY_PAGE','ACCOUNT')),
    'ORIGINAL_ID_RECOVERY':(('ACCOUNT','ORIGINAL_LOOKUP','ACCOUNT'),),
}



def fixed_targets_match(record, operation):
    """Read-only endpoint consistency for the three fixed-target read purposes.

    Receipts are already replay-validated by the caller. Dynamic activity and
    original-ID targets need their own retained-scope comparisons. This does
    not authenticate responses, source identity, coverage or provider quotas.
    """
    if type(operation) is not str or operation not in ('ACCOUNT_INVENTORY','BTC_ASSET_RULES','BTC_QUOTE_OBSERVATION'):
        return False
    if (not isinstance(record, dict) or record.get('operation') != operation or
        record.get('outcome') != 'SUCCESS' or record.get('receipt_profile') not in (RECEIPT_PROFILE,RECEIPT_PROFILE_V3) or
        record.get('result_receipt_bound') is not True or record.get('request_targets_bound') is not True):
        return False
    from nexus import alpaca_paper_inspection as reader
    targets = (reader.URLS if operation == 'ACCOUNT_INVENTORY' else
        (reader.URLS[0], reader.ASSET_URL if operation == 'BTC_ASSET_RULES' else reader.BTC_QUOTE_URL, reader.URLS[0]))
    expected = tuple(sha(dict(method='GET', target=target)) for target in targets)
    return record.get('request_kinds') == OPERATION_PATTERNS[operation][0] and record.get('request_sha256') == expected

def _fail(code):
    raise ValueError('PAPER_READ_PACING_' + code)


class PaperReadPacing:
    def __init__(self, path, paper_account_id):
        self.path = Path(path)
        self.account = _uuid(paper_account_id)
        self.account_digest = hashlib.sha256(self.account.encode()).hexdigest()
        self._active_completion = None

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
        limit=MAX_EVENT_BYTES if body.get('receipt_profile')==RECEIPT_PROFILE_V3 else LEGACY_MAX_EVENT_BYTES
        if len(encoded.encode()) > limit: _fail('EVENT_CAP')
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
                     completed_sessions=[], operation_sessions=[], interruptions=0, last_interruption=None, last_interruption_head=None)
        for index, (seq, encoded, parent, digest) in enumerate(rows):
            if not isinstance(encoded, str) or len(encoded.encode()) > MAX_EVENT_BYTES: _fail('EVENT_CAP')
            try: body = json.loads(encoded)
            except (ValueError, TypeError): _fail('PAYLOAD_INVALID')
            limit=MAX_EVENT_BYTES if type(body) is dict and body.get('receipt_profile')==RECEIPT_PROFILE_V3 else LEGACY_MAX_EVENT_BYTES
            if len(encoded.encode())>limit:_fail('EVENT_CAP')
            if seq != index or parent != state['head_sha256'] or canonical(body) != encoded or digest != sha(dict(seq=seq, body=body, previous=parent)):
                _fail('CHAIN_DISAGREEMENT')
            if index == 0:
                if body != dict(schema=SCHEMA, account_sha256=self.account_digest): _fail('ACCOUNT_OR_SCHEMA')
            else:
                if not isinstance(body, dict): _fail('PAYLOAD_INVALID')
                kind = body.get('kind')
                if kind == 'BEGIN' and set(body) in ({'kind', 'evaluated_at'}, {'kind', 'evaluated_at', 'owner_fenced', 'owner_sha256'}, {'kind','evaluated_at','owner_fenced','owner_sha256','operation'}):
                    if 'owner_fenced' in body and (body['owner_fenced'] is not True or not isinstance(body['owner_sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', body['owner_sha256']) is None): _fail('OWNER_MARKER_INVALID')
                    if 'operation' in body and (type(body['operation']) is not str or body['operation'] not in OPERATIONS):_fail('OPERATION_INVALID')
                    now = clock(body['evaluated_at'])
                    self._admit(state, now)
                    state['pending_token'] = digest
                    state['pending_before_head_sha256'] = ZERO if index==1 else parent
                    state['pending_owner_fenced'] = body.get('owner_fenced', False)
                    state['pending_owner_sha256'] = body.get('owner_sha256')
                    state['sessions'] += 1
                    state['operation_sessions'].append(dict(operation=body.get('operation',LEGACY_OPERATION),
                        started_at=now,completed_at=None,before_head_sha256=state['pending_before_head_sha256'],
                        begin_head_sha256=digest,after_head_sha256=None,outcome='PENDING_UNKNOWN',
                        receipt_profile=None,request_kinds=(),request_sha256=(),response_sha256=(),receipt_sha256=None,
                        result_receipt_bound=False,request_targets_bound=False,
                        result_projection_sha256=None,normalized_result_bound=False))
                elif kind == 'RESULT' and set(body) in (
                    {'kind', 'evaluated_at', 'token', 'outcome', 'delay'},
                    {'kind','evaluated_at','token','outcome','delay','receipt_profile','request_kinds','response_sha256','receipt_sha256'},
                    {'kind','evaluated_at','token','outcome','delay','receipt_profile','request_kinds','request_sha256','response_sha256','receipt_sha256'},
                    {'kind','evaluated_at','token','outcome','delay','receipt_profile','request_kinds','request_sha256','response_sha256','receipt_sha256','result_projection_sha256'}):
                    now = clock(body['evaluated_at'])
                    if state['pending_token'] is None or body['token'] != state['pending_token']: _fail('TOKEN_DISAGREEMENT')
                    if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                    outcome, delay = body['outcome'], body['delay']
                    if outcome not in ('SUCCESS', 'NOT_FOUND', 'TIMEOUT', 'NETWORK_ERROR', 'RATE_LIMITED', 'SERVER_ERROR', 'AUTH_DENIED', 'REVIEW_REQUIRED'): _fail('OUTCOME_INVALID')
                    if type(delay) is not int or (outcome == 'RATE_LIMITED' and not 1 <= delay <= 86400) or (outcome != 'RATE_LIMITED' and delay != 0): _fail('DELAY_INVALID')
                    receipt=set(body)!={'kind','evaluated_at','token','outcome','delay'}
                    if receipt:
                        operation=state['operation_sessions'][-1]['operation']
                        profile=body['receipt_profile']
                        if operation==LEGACY_OPERATION or profile not in (RECEIPT_PROFILE_V1,RECEIPT_PROFILE,RECEIPT_PROFILE_V3):_fail('RECEIPT_INVALID')
                        if (profile==RECEIPT_PROFILE_V1) != ('request_sha256' not in body):_fail('RECEIPT_INVALID')
                        if (profile==RECEIPT_PROFILE_V3) != ('result_projection_sha256' in body):_fail('RECEIPT_INVALID')
                        if profile==RECEIPT_PROFILE_V3 and (outcome!='SUCCESS' or type(body['result_projection_sha256']) is not str or
                            re.fullmatch(r'[0-9a-f]{64}',body['result_projection_sha256']) is None):_fail('RECEIPT_PROJECTION_INVALID')
                        kinds=body['request_kinds'];requests=body.get('request_sha256',[]);responses=body['response_sha256']
                        if (type(kinds) is not list or not 1<=len(kinds)<=4 or
                            any(type(value) is not str for value in kinds) or
                            type(requests) is not list or (profile in (RECEIPT_PROFILE,RECEIPT_PROFILE_V3) and len(requests)!=len(kinds)) or
                            any(type(value) is not str or re.fullmatch(r'[0-9a-f]{64}',value) is None for value in requests) or
                            type(responses) is not list or len(responses)>len(kinds) or
                            any(type(value) is not str or re.fullmatch(r'[0-9a-f]{64}',value) is None for value in responses)):_fail('RECEIPT_INVALID')
                        patterns=OPERATION_PATTERNS[operation];sequence=tuple(kinds)
                        if outcome=='SUCCESS':
                            if sequence not in patterns or len(responses)!=len(kinds):_fail('RECEIPT_SEQUENCE')
                        elif not any(pattern[:len(sequence)]==sequence for pattern in patterns) or len(responses)!=len(kinds)-1:
                            _fail('RECEIPT_SEQUENCE')
                        scope=dict(profile=profile,operation=operation,
                            before_head_sha256=state['operation_sessions'][-1]['before_head_sha256'],
                            begin_head_sha256=state['operation_sessions'][-1]['begin_head_sha256'],
                            evaluated_at=now,outcome=outcome,request_kinds=kinds,response_sha256=responses)
                        if profile in (RECEIPT_PROFILE,RECEIPT_PROFILE_V3):scope['request_sha256']=requests
                        if profile==RECEIPT_PROFILE_V3:scope['result_projection_sha256']=body['result_projection_sha256']
                        if body['receipt_sha256']!=sha(scope):_fail('RECEIPT_DIGEST')
                    state['operation_sessions'][-1].update(completed_at=now,after_head_sha256=digest,outcome=outcome,
                        receipt_profile=body.get('receipt_profile'),request_kinds=tuple(body.get('request_kinds',())),
                        request_sha256=tuple(body.get('request_sha256',())),
                        response_sha256=tuple(body.get('response_sha256',())),receipt_sha256=body.get('receipt_sha256'),
                        result_receipt_bound=receipt,request_targets_bound=receipt and body.get('receipt_profile') in (RECEIPT_PROFILE,RECEIPT_PROFILE_V3),
                        result_projection_sha256=body.get('result_projection_sha256'),
                        normalized_result_bound=receipt and body.get('receipt_profile')==RECEIPT_PROFILE_V3)
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
                    state['operation_sessions'][-1].update(completed_at=now,after_head_sha256=digest,outcome='INTERRUPTED_UNKNOWN')
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
                     completed_sessions=[], operation_sessions=[], interruptions=0, last_interruption=None, last_interruption_head=None)
        else:
            with closing(self._connect()) as db: state = self._load(db)
        public=dict(state);public.pop('pending_before_head_sha256');public.pop('completed_sessions');operations=public.pop('operation_sessions')
        counts={kind:sum(r['operation']==kind for r in operations) for kind in (*OPERATIONS,LEGACY_OPERATION)}
        typed=[row for row in operations if row['operation']!=LEGACY_OPERATION]
        completed_typed=[row for row in typed if row['outcome'] not in ('PENDING_UNKNOWN','INTERRUPTED_UNKNOWN')]
        return dict(public, account_sha256=self.account_digest, max_gets_per_session=4,
                    operation_counts=counts,pending_operation=None if state['pending_token'] is None else operations[-1]['operation'],
                    typed_result_receipts=sum(row['result_receipt_bound'] for row in completed_typed),
                    unreceipted_typed_results=sum(not row['result_receipt_bound'] for row in completed_typed),
                    all_typed_results_receipted=bool(completed_typed) and all(row['result_receipt_bound'] for row in completed_typed),
                    target_bound_typed_results=sum(row['request_targets_bound'] for row in completed_typed),
                    untargeted_typed_results=sum(not row['request_targets_bound'] for row in completed_typed),
                    all_typed_request_targets_bound=bool(completed_typed) and all(row['request_targets_bound'] for row in completed_typed),
                    operation_identity_attested=False,mixed_operation_acceptance_verified=False,
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

    def operation_history(self):
        """Read-only declared operation/outcome replay; legacy purpose stays unknown."""
        self._path_check()
        if not self.path.exists():return ()
        with closing(self._connect()) as db:state=self._load(db)
        return tuple(dict(item) for item in state['operation_sessions'])

    def ownership(self):
        """Local OS lease; runtime readers hold it through all IO and cleanup."""
        return _ReadOwnership(self)

    def begin(self, *, admission, ownership=None, operation=None):
        """Internal callback samples permission/clock after lock and before commit."""
        if operation is not None:
            if type(operation) is not str or operation not in OPERATIONS:_fail('OPERATION_INVALID')
            if ownership is None:_fail('OPERATION_OWNER_REQUIRED')
            self._owned(ownership)
        with closing(self._connect(write=True)) as db:
            with db:
                state = self._load(db)
                if ownership is not None: self._owned(ownership)
                now = clock(admission())
                self._admit(state, now)
                body = dict(kind='BEGIN', evaluated_at=now)
                if ownership is not None:
                    body.update(owner_fenced=True, owner_sha256=ownership.identity())
                if operation is not None:body['operation']=operation
                token = self._append(db, state['events'] + 1, body, state['head_sha256'])
                self._load(db)
                if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                if ownership is not None: self._owned(ownership)
            if operation is not None:self._active_completion=(token,ownership,ownership.lease)
            return token

    def _completion_owner(self,token,ownership,state):
        if state['operation_sessions'][-1]['operation']==LEGACY_OPERATION:return
        if ownership is None:_fail('OPERATION_OWNER_REQUIRED')
        self._owned(ownership)
        active=self._active_completion
        if (active is None or active[0]!=token or active[1] is not ownership or active[2] is not ownership.lease):
            _fail('OPERATION_ORIGINAL_LEASE_REQUIRED')
        if state['pending_owner_sha256']!=ownership.identity():_fail('OWNER_SCOPE_CHANGED')

    def finish(self, token, *, admission, status_code=None, transport_error=None, retry_after=None,ownership=None,receipt=None):
        """Retain fixed outcome/delay only; a lost return never erases the result."""
        self._path_check()
        if not self.path.exists(): _fail('BINDING_MISSING')
        if ownership is not None:self._owned(ownership)
        with closing(self._connect(write=True)) as db:
            with db:
                state = self._load(db)
                if state['pending_token'] is None or token != state['pending_token']: _fail('TOKEN_DISAGREEMENT')
                self._completion_owner(token,ownership,state)
                now = clock(admission())
                if now < state['last_evaluated_at']: _fail('CLOCK_REGRESSION')
                result = classify(status_code=status_code, transport_error=transport_error,
                                  retry_after=retry_after, evaluated_at=now)
                outcome = 'SUCCESS' if result['outcome'] == 'FOUND' else result['outcome']
                body=dict(kind='RESULT', evaluated_at=now, token=token, outcome=outcome,
                    delay=result['retry_after_seconds'])
                operation=state['operation_sessions'][-1]['operation']
                if operation!=LEGACY_OPERATION:
                    if type(receipt) is not dict or set(receipt) not in ({'operation','request_kinds','request_sha256','response_sha256'},
                        {'operation','request_kinds','request_sha256','response_sha256','result_projection_sha256'}) or receipt['operation']!=operation:
                        _fail('RECEIPT_REQUIRED')
                    kinds=receipt['request_kinds'];requests=receipt['request_sha256'];responses=receipt['response_sha256']
                    # Reject malformed values before canonicalizing the receipt.
                    patterns=OPERATION_PATTERNS[operation]
                    if (type(kinds) is not list or not 1<=len(kinds)<=4 or any(type(value) is not str for value in kinds) or
                        type(requests) is not list or len(requests)!=len(kinds) or
                        any(type(value) is not str or re.fullmatch(r'[0-9a-f]{64}',value) is None for value in requests) or
                        type(responses) is not list or len(responses)>len(kinds) or
                        any(type(value) is not str or re.fullmatch(r'[0-9a-f]{64}',value) is None for value in responses)):_fail('RECEIPT_INVALID')
                    sequence=tuple(kinds)
                    if outcome=='SUCCESS':
                        if sequence not in patterns or len(responses)!=len(kinds):_fail('RECEIPT_SEQUENCE')
                    elif not any(pattern[:len(sequence)]==sequence for pattern in patterns) or len(responses)!=len(kinds)-1:
                        _fail('RECEIPT_SEQUENCE')
                    projected='result_projection_sha256' in receipt
                    if projected and (outcome!='SUCCESS' or type(receipt['result_projection_sha256']) is not str or
                        re.fullmatch(r'[0-9a-f]{64}',receipt['result_projection_sha256']) is None):_fail('RECEIPT_PROJECTION_INVALID')
                    profile=RECEIPT_PROFILE_V3 if projected else RECEIPT_PROFILE
                    scope=dict(profile=profile,operation=operation,
                        before_head_sha256=state['operation_sessions'][-1]['before_head_sha256'],
                        begin_head_sha256=state['operation_sessions'][-1]['begin_head_sha256'],evaluated_at=now,
                        outcome=outcome,request_kinds=kinds,response_sha256=responses)
                    scope['request_sha256']=requests
                    if projected:
                        scope['result_projection_sha256']=receipt['result_projection_sha256']
                        body['result_projection_sha256']=receipt['result_projection_sha256']
                    body.update(receipt_profile=profile,request_kinds=kinds,request_sha256=requests,response_sha256=responses,
                        receipt_sha256=sha(scope))
                elif receipt is not None:_fail('RECEIPT_LEGACY_DENIED')
                head = self._append(db, state['events'] + 1,body,state['head_sha256'])
                self._load(db)
                if clock(admission()) < now: _fail('CLOCK_REGRESSION')
                self._completion_owner(token,ownership,state)
            self._active_completion=None
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
        self.lease = None

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
            self.lease=object()
            return self
        except BaseException as error:
            self.fd = None
            os.close(fd)
            if isinstance(error, OSError): _fail('OWNER_BUSY_OR_UNAVAILABLE')
            raise

    def __exit__(self, *args):
        fd, self.fd = self.fd, None
        if fd is not None: os.close(fd)


OPERATION_CHECKS=('five_actual_readers_typed','five_request_receipts_bound','five_request_targets_bound',
    'three_fixed_targets_matched','wrong_fixed_targets_refused','v1_fixed_targets_unverified','five_normalized_results_bound','original_recovery_consumer_checked','five_stale_heads_refused_before_io','response_payloads_not_retained',
    'one_shared_cooldown','legacy_not_relabelled','legacy_receipt_not_invented',
    'completed_outcomes_preserved','restart_replay_matches','status_read_only')
OPERATION_FLAGS=('operation_identity_attested','mixed_operation_acceptance_verified','broker_wide_quota_verified',
    'actual_provider_access','actual_vault_access','ready_for_routing','live_orders_enabled','money_movement_enabled','release_approved')

COMPLETION_CHECKS=('original_lease_completes','reacquired_lease_refused','cold_instance_refused',
                   'pending_history_preserved','explicit_interruption_preserved')


def completion_fixture_report():
    """Temporary typed reservation completion only; no provider or vault work."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix='nexus-read-completion-') as directory:
        account='01234567-89ab-cdef-0123-456789abcdef';root=Path(directory)
        gate=PaperReadPacing(root/'completed.sqlite3',account)
        with gate.ownership() as owner:
            token=gate.begin(admission=lambda:1000,ownership=owner,operation='ACCOUNT_INVENTORY')
            kinds=list(OPERATION_PATTERNS['ACCOUNT_INVENTORY'][0])
            receipt=dict(operation='ACCOUNT_INVENTORY',request_kinds=kinds,
                request_sha256=[sha(dict(target=index)) for index in range(len(kinds))],
                response_sha256=[sha(dict(index=index)) for index in range(len(kinds))])
            gate.finish(token,admission=lambda:1001,status_code=200,ownership=owner,receipt=receipt)
        completed=gate.inspect()['last_outcome']=='SUCCESS'
        gate=PaperReadPacing(root/'pending.sqlite3',account);owner=gate.ownership()
        with owner:token=gate.begin(admission=lambda:1000,ownership=owner,operation='BTC_QUOTE_OBSERVATION')
        before=gate.path.read_bytes();reacquired=cold=False
        with owner:
            try:gate.finish(token,admission=lambda:1001,status_code=200,ownership=owner)
            except ValueError as error:reacquired=str(error)=='PAPER_READ_PACING_OPERATION_ORIGINAL_LEASE_REQUIRED'
        restarted=PaperReadPacing(gate.path,account)
        with restarted.ownership() as another:
            try:restarted.finish(token,admission=lambda:1001,status_code=200,ownership=another)
            except ValueError as error:cold=str(error)=='PAPER_READ_PACING_OPERATION_ORIGINAL_LEASE_REQUIRED'
        state=restarted.inspect();unchanged=before==gate.path.read_bytes() and state['pending_token']==token
        restarted.interrupt(token,expected_head=state['head_sha256'],expected_account_id=account,confirm_interrupted=True,admission=lambda:1001)
        after=restarted.inspect()
        checks=dict(original_lease_completes=completed,reacquired_lease_refused=reacquired,cold_instance_refused=cold,
            pending_history_preserved=unchanged,explicit_interruption_preserved=after['last_outcome']=='INTERRUPTED_UNKNOWN' and after['next_allowed_at']==1001+INTERRUPTED_COOLDOWN)
        return dict(source='TEMPORARY_ORIGINAL_READ_LEASE_FIXTURE',passed=all(checks.values()),**checks,**{k:False for k in OPERATION_FLAGS})


def typed_fixture_report():
    """Actual five reader flows on one disposable local budget; injected IO only."""
    import io,tempfile
    from nexus import alpaca_paper_inspection as reader
    from nexus.paper_asset_rules import inspect_rules
    from nexus.alpaca_activity_collection import collect_activities
    from nexus.paper_recovery_step import recover_next
    from nexus.paper_quote_observation import inspect_quote
    from nexus.broker_request_journal import BrokerRequestJournal
    from nexus.alpaca_paper_contract import preview
    account='01234567-89ab-cdef-0123-456789abcdef';epoch=1792368000
    raw_account=dict(id=account,currency='USD',status='ACTIVE',crypto_status='ACTIVE',account_blocked=False,
        trading_blocked=False,trade_suspended_by_user=False,cash='1000',non_marginable_buying_power='900',
        pending_transfer_in='0',pending_transfer_out='0',accrued_fees='0')
    asset=dict(id='11234567-89ab-cdef-0123-456789abcdef',symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,
        fractionable=True,min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    quote={'quotes':{'BTC/USD':dict(bp='100',ap='101',bs='2',**{'as':'3'},t='2026-10-19T00:04:00.123456789Z')}}
    class Response:
        status=200;headers={'Content-Type':'application/json'}
        def __init__(self,url,value):self.url=url;self.body=io.BytesIO(json.dumps(value).encode())
        def geturl(self):return self.url
        def read(self,size):return self.body.read(size)
        def __enter__(self):return self
        def __exit__(self,*args):self.body.close()
    class Opener:
        def __init__(self,values):self.values=iter(values);self.requests=[]
        def open(self,request,timeout):self.requests.append(request);return Response(request.full_url,next(self.values))
    with tempfile.TemporaryDirectory(prefix='nexus-typed-reads-') as directory:
        gate=PaperReadPacing(Path(directory)/'pacing.sqlite3',account)
        key='fixture_key_only_123';secret='fixture_secret_only_123'
        def args(values,at):return dict(expected_account_id=account,confirm_read_only_paper=True,pacing=gate,
            opener=Opener(values),wall_clock=lambda:at,elapsed_clock=lambda:50)
        reader.inspect_account(key,secret,**args([raw_account,[],[],raw_account],epoch))
        inspect_rules(key,secret,**args([raw_account,asset,raw_account],epoch+60))
        collect_activities(key,secret,[],after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z',
            **args([raw_account,[],raw_account],epoch+120))
        journal=BrokerRequestJournal(Path(directory)/'requests.sqlite3',account)
        plan=preview('nx_operation_fixture','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
        journal.prepare(plan);journal.mark_submission_uncertain('nx_operation_fixture')
        raw_order=dict(plan['payload'],id=asset['id'],asset_class='crypto',status='canceled',filled_qty='0',filled_avg_price=None,
            updated_at='2026-10-19T00:03:00Z')
        recovered=recover_next(journal,key,secret,**args([raw_account,raw_order,raw_account],epoch+180))
        inspect_quote(key,secret,**args([raw_account,quote,raw_account],epoch+241))
        typed_state=gate.inspect();typed_history=gate.operation_history();retained=gate.path.read_bytes()
        token=gate.begin(admission=lambda:epoch+301);gate.finish(token,admission=lambda:epoch+301,status_code=200)
        before={p.name:p.read_bytes() for p in Path(directory).iterdir()};history=gate.operation_history();state=gate.inspect()
        blocked=Opener([raw_account,[],[],raw_account]);cooldown=False
        try:reader.inspect_account(key,secret,**dict(args([],epoch+302),opener=blocked))
        except ValueError:cooldown=not blocked.requests and before=={p.name:p.read_bytes() for p in Path(directory).iterdir()}
        # The same stale sampled budget head must block every actual reader
        # before BEGIN, original-ID lookup admission, retention or any GET.
        from nexus.broker_paper_recovery import recover_original
        stale_readers=(
            lambda kw:reader.inspect_account(key,secret,**kw),
            lambda kw:inspect_rules(key,secret,**kw),
            lambda kw:collect_activities(key,secret,[],after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z',**kw),
            lambda kw:recover_original(journal,'nx_operation_fixture',key,secret,**kw),
            lambda kw:inspect_quote(key,secret,**kw))
        stale_refused=[]
        for run in stale_readers:
            blocked_reader=Opener([])
            options=dict(args([],epoch+400),opener=blocked_reader,expected_pacing_head=ZERO)
            try:run(options)
            except ValueError:stale_refused.append(not blocked_reader.requests and before=={p.name:p.read_bytes() for p in Path(directory).iterdir()})
            else:stale_refused.append(False)
        fixed=[typed_history[index] for index in (0,1,4)]
        wrong_fixed=True
        for record in fixed:
            for index in range(len(record['request_sha256'])):
                changed=list(record['request_sha256']);changed[index]=sha(dict(method='GET',target='wrong_fixture_target'))
                wrong_fixed=wrong_fixed and not fixed_targets_match(dict(record,request_sha256=tuple(changed)),record['operation'])
        checks=dict(five_actual_readers_typed=tuple(r['operation'] for r in history[:5])==OPERATIONS and all(state['operation_counts'][op]==1 for op in OPERATIONS),
            five_request_receipts_bound=len(typed_history)==5 and all(r['result_receipt_bound'] for r in typed_history) and
                typed_state['typed_result_receipts']==5 and typed_state['unreceipted_typed_results']==0 and typed_state['all_typed_results_receipted'],
            five_request_targets_bound=len(typed_history)==5 and all(r['request_targets_bound'] for r in typed_history) and
                typed_state['target_bound_typed_results']==5 and typed_state['untargeted_typed_results']==0 and
                typed_state['all_typed_request_targets_bound'],
            three_fixed_targets_matched=all(fixed_targets_match(record,record['operation']) for record in fixed),
            wrong_fixed_targets_refused=wrong_fixed,
            v1_fixed_targets_unverified=all(not fixed_targets_match(dict(record,receipt_profile=RECEIPT_PROFILE_V1,
                request_sha256=(),request_targets_bound=False),record['operation']) for record in fixed),
            five_normalized_results_bound=all(r['normalized_result_bound'] and r['receipt_profile']==RECEIPT_PROFILE_V3 for r in typed_history),
            original_recovery_consumer_checked=recovered['mode']=='EXPLICIT_SINGLE_PAPER_RECOVERY_STEP' and
                recovered['selected_client_order_id']=='nx_operation_fixture' and recovered['recovery']['journal']['lookup']['last_outcome']=='FOUND',
            five_stale_heads_refused_before_io=len(stale_refused)==5 and all(stale_refused),
            response_payloads_not_retained=all(value not in retained for value in
                (account.encode(),key.encode(),secret.encode(),b'nx_operation_fixture',b'2026-10-17T00:00:00Z')),
            one_shared_cooldown=cooldown,legacy_not_relabelled=history[-1]['operation']==LEGACY_OPERATION and state['operation_counts'][LEGACY_OPERATION]==1,
            legacy_receipt_not_invented=history[-1]['result_receipt_bound'] is False and history[-1]['receipt_sha256'] is None,
            completed_outcomes_preserved=len(history)==6 and all(r['outcome']=='SUCCESS' for r in history),
            restart_replay_matches=PaperReadPacing(gate.path,account).operation_history()==history,
            status_read_only=before=={p.name:p.read_bytes() for p in Path(directory).iterdir()})
        return dict(source='INJECTED_TYPED_PAPER_READ_BUDGET_FIXTURE',passed=all(checks.values()),**checks,
            **{k:False for k in OPERATION_FLAGS})
