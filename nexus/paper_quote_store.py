"""Bounded private supplied-quote retention against original request prefixes.

Offline only. A stored source label/digest is not provider attestation, proof of
capture causality, a decision/arrival benchmark, encryption or order authority.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3

from nexus import paper_quote_observation as observation
from nexus.alpaca_paper_contract import canonical,sha
from nexus.broker_lookup_policy import clock
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_activity_acceptance_probe import _private_path
from nexus.sqlite_journal_guard import check_existing

PROFILE='nexus.paper-request-quote-retention.v1'
ZERO='0'*64
MAX_RECORDS=128
MAX_RECORD_BYTES=8192
MAX_DATABASE_BYTES=4*1024*1024
CHECKS=('original_prefix_bound','exact_nanoseconds_retained','duplicate_reused',
    'restart_replay','request_bytes_unchanged','authority_withheld')
SCOPE_KEYS=('profile','account_sha256','quote','source','account_fields_sha256','read_timing')


def _fail(code):raise ValueError('PAPER_QUOTE_STORE_'+code)


def _head(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:_fail('HEAD_INVALID')
    return value


def _observation(value,account):
    """Copy only allowlisted input; source and local clock claims stay unverified."""
    if type(value) is not dict:_fail('OBSERVATION_INVALID')
    if len(canonical(value).encode())>MAX_RECORD_BYTES:_fail('RECORD_CAP')
    expected={*SCOPE_KEYS,'mode','status','observation_sha256','fixed_get_requests',
        'sequential_account_fields_stable','timestamp_scope','limitations',*observation.FLAGS}
    if (set(value)!=expected or value['mode']!='READ_ONLY_PAPER_BTC_QUOTE_OBSERVATION' or
        value['status']!='REVIEW_REQUIRED' or value['profile']!=observation.PROFILE or
        value['account_sha256']!=account or value['source'] not in ('INJECTED_UNATTESTED','ALPACA_HTTPS_READS') or
        type(value['fixed_get_requests']) is not int or value['fixed_get_requests']!=3 or
        value['sequential_account_fields_stable'] is not True or
        value['timestamp_scope']!='EXCHANGE_NS_EXACT_LOCAL_RECEIPT_FLOAT_SECONDS_NOT_CLOCK_SYNCHRONIZATION' or
        any(value[k] is not False for k in observation.FLAGS)):_fail('OBSERVATION_INVALID')
    _head(value['account_fields_sha256']);_head(value['observation_sha256'])
    scope={k:value[k] for k in SCOPE_KEYS}
    if sha(scope)!=value['observation_sha256']:_fail('OBSERVATION_DIGEST')
    timing=scope['read_timing'];quote=scope['quote']
    if type(timing) is not dict or set(timing)!={'started_at','quote_received_at','completed_at','finalized_at','elapsed_seconds'}:_fail('TIMING_INVALID')
    times=[clock(timing[k]) for k in ('started_at','quote_received_at','completed_at','finalized_at')]
    if times!=sorted(times) or times[-1]-times[0]>20 or not 0<=clock(timing['elapsed_seconds'])<=20:_fail('TIMING_INVALID')
    if type(quote) is not dict or set(quote)!={'product','location','bid','ask','bid_size','ask_size','exchange_at','exchange_time_ns','sampled_age_ns','checks'}:_fail('QUOTE_INVALID')
    raw={'quotes':{'BTC/USD':dict(bp=quote['bid'],ap=quote['ask'],bs=quote['bid_size'],
        **{'as':quote['ask_size']},t=quote['exchange_at'])}}
    # Reproduce exactly what the reader could normalize. These old sampled
    # times validate the record, never make an old record fresh for execution.
    if observation._quote(raw,times[1])!=quote:_fail('QUOTE_INVALID')
    for at in times[2:]:observation._quote(raw,at)
    return json.loads(canonical(scope))


class PaperQuoteStore:
    def __init__(self,path,paper_account_id,request_journal_path):
        self.path=Path(path);self.request_path=Path(request_journal_path)
        if _private_path(self.path)[1]==_private_path(self.request_path)[1]:_fail('DISTINCT_PATHS_REQUIRED')
        self.journal=BrokerRequestJournal(self.request_path,paper_account_id)
        self.account=self.journal.account_digest

    def _check(self):
        _private_path(self.path);_private_path(self.request_path)
        check_existing(self.path,MAX_DATABASE_BYTES,'PAPER_QUOTE_STORE_')
        try:self.path.with_name(self.path.name+'-journal').lstat()
        except FileNotFoundError:pass
        else:_fail('ROLLBACK_REVIEW_REQUIRED')

    def _requests(self,expected_head):
        _head(expected_head);self.journal._check_path()
        if not self.request_path.exists():_fail('ORIGINAL_REQUEST_REQUIRED')
        with closing(self.journal._connect()) as db:
            db.execute('BEGIN')
            requests,_,_,head=self.journal._load(db)  # Validate complete tail, not just a selected prefix.
            if head!=expected_head:_fail('REQUEST_HEAD_CHANGED')
            rows=db.execute('SELECT seq,kind,body,digest FROM events ORDER BY seq').fetchall()
        heads={digest:seq for seq,_,_,digest in rows}
        prepared={json.loads(body)['payload']['client_order_id']:seq for seq,kind,body,_ in rows if kind=='PREPARE'}
        return dict(heads=heads,prepared=prepared,plans={k:v['plan'] for k,v in requests.items()})

    def _binding(self,body,requests):
        if type(body) is not dict or set(body)!={'profile','account_sha256','request_head_sha256','client_order_id','plan_sha256','payload_sha256','observation'}:_fail('RECORD_INVALID')
        if body['profile']!=PROFILE or body['account_sha256']!=self.account:_fail('ACCOUNT_OR_SCHEMA')
        head=_head(body['request_head_sha256']);identity=body['client_order_id']
        if type(identity) is not str or re.fullmatch('[A-Za-z0-9_-]{1,64}',identity) is None:_fail('CLIENT_ID')
        plan=requests['plans'].get(identity)
        if (plan is None or head not in requests['heads'] or requests['prepared'][identity]>requests['heads'][head] or
            body['plan_sha256']!=sha(plan) or body['payload_sha256']!=plan['payload_sha256']):_fail('ORIGINAL_PREFIX_DISAGREEMENT')
        # Stored scope is deliberately smaller than a reader response. Rebuild
        # the fixed false flags rather than allowing authority into the journal.
        if type(body['observation']) is not dict or set(body['observation'])!=set(SCOPE_KEYS):_fail('RECORD_INVALID')
        supplied=dict(body['observation'],mode='READ_ONLY_PAPER_BTC_QUOTE_OBSERVATION',status='REVIEW_REQUIRED',
            observation_sha256=sha(body['observation']),fixed_get_requests=3,sequential_account_fields_stable=True,
            timestamp_scope='EXCHANGE_NS_EXACT_LOCAL_RECEIPT_FLOAT_SECONDS_NOT_CLOCK_SYNCHRONIZATION',limitations='',
            **{k:False for k in observation.FLAGS})
        if _observation(supplied,self.account)!=body['observation']:_fail('RECORD_INVALID')

    def _connect(self,write=False):
        self._check();new=False
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True,exist_ok=True)
            try:
                fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd);new=True
            except FileExistsError:pass
            self._check()
        db=sqlite3.connect(self.path.resolve().as_uri()+('?mode=rw' if write else '?mode=ro'),uri=True,timeout=5)
        try:
            if write:
                db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=FULL')
                size=db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES//size}');db.execute('BEGIN IMMEDIATE')
                if new:db.execute('CREATE TABLE records (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
                if new:
                    self._insert(db,0,dict(profile=PROFILE,account_sha256=self.account),ZERO)
            else:db.execute('BEGIN')
            return db,new
        except BaseException:db.close();raise

    @staticmethod
    def _insert(db,seq,body,previous):
        encoded=canonical(body)
        if len(encoded.encode())>MAX_RECORD_BYTES:_fail('RECORD_CAP')
        digest=sha(dict(seq=seq,body=body,previous=previous))
        db.execute('INSERT INTO records VALUES (?,?,?,?)',(seq,encoded,previous,digest));return digest

    def _load(self,db,requests):
        rows=db.execute('SELECT seq,body,previous,digest FROM records ORDER BY seq LIMIT ?',(MAX_RECORDS+2,)).fetchall()
        if not rows:_fail('BINDING_MISSING')
        if len(rows)>MAX_RECORDS+1:_fail('RECORD_CAP')
        previous=ZERO;records=[];identities=set()
        for index,(seq,encoded,parent,digest) in enumerate(rows):
            if type(encoded) is not str or len(encoded.encode())>MAX_RECORD_BYTES:_fail('RECORD_CAP')
            body=json.loads(encoded)
            if seq!=index or parent!=previous or canonical(body)!=encoded or digest!=sha(dict(seq=seq,body=body,previous=parent)):_fail('CHAIN_DISAGREEMENT')
            if index==0:
                if body!=dict(profile=PROFILE,account_sha256=self.account):_fail('ACCOUNT_OR_SCHEMA')
            else:
                self._binding(body,requests);identity=sha(body)
                if identity in identities:_fail('DUPLICATE_RECORD')
                identities.add(identity);records.append(body)
            previous=digest
        return previous,records

    def inspect(self,*,expected_request_head):
        self._check();requests=self._requests(expected_request_head)
        if self.path.exists():
            with closing(self._connect()[0]) as db:head,records=self._load(db,requests)
        else:head,records=ZERO,[]
        if self._requests(expected_request_head)!=requests:_fail('REQUEST_CHANGED_DURING_REVIEW')
        return dict(mode='PAPER_REQUEST_QUOTE_HISTORY',profile=PROFILE,status='REVIEW_REQUIRED',
            account_sha256=self.account,head_sha256=head,request_head_sha256=expected_request_head,
            records=records,retained_records=len(records),read_only=True,
            input_scope='SUPPLIED_UNATTESTED_OBSERVATIONS_BOUND_TO_LOCAL_ORIGINAL_REQUEST_PREFIXES',
            **{k:False for k in observation.FLAGS})

    def retain(self,value,*,client_order_id,expected_request_head,expected_quote_head,precommit_check=lambda:True):
        self._check();_head(expected_quote_head)
        scope=_observation(value,self.account);requests=self._requests(expected_request_head)
        if type(client_order_id) is not str:_fail('CLIENT_ID')
        plan=requests['plans'].get(client_order_id)
        if plan is None:_fail('ORIGINAL_REQUEST_REQUIRED')
        body=dict(profile=PROFILE,account_sha256=self.account,request_head_sha256=expected_request_head,
            client_order_id=client_order_id,plan_sha256=sha(plan),payload_sha256=plan['payload_sha256'],observation=scope)
        self._binding(body,requests)
        # Missing storage stays absent on stale expected heads or revoked input.
        current=self.inspect(expected_request_head=expected_request_head)
        if current['head_sha256']!=expected_quote_head:_fail('QUOTE_HEAD_CHANGED')
        if precommit_check() is not True:_fail('INPUT_REVOKED')
        db,new=self._connect(write=True)
        with closing(db):
            with db:
                head,records=self._load(db,requests)
                if new and expected_quote_head==ZERO:pass  # First binding row is created in this transaction.
                elif head!=expected_quote_head:_fail('QUOTE_HEAD_CHANGED')
                duplicate=body in records
                if not duplicate:
                    if len(records)>=MAX_RECORDS:_fail('RECORD_CAP')
                    head=self._insert(db,len(records)+1,body,head)
                    self._load(db,requests)
                if self._requests(expected_request_head)!=requests:_fail('REQUEST_CHANGED_DURING_WRITE')
                if precommit_check() is not True:_fail('INPUT_REVOKED')
            # Lost post-commit acknowledgment preserves evidence; never repair,
            # delete, retry automatically or imply cross-database atomicity.
            if self._requests(expected_request_head)!=requests:_fail('REQUEST_CHANGED_AFTER_COMMIT')
            if precommit_check() is not True:_fail('INPUT_REVOKED_AFTER_COMMIT')
            return dict(mode='PAPER_REQUEST_QUOTE_RECEIPT',head_sha256=head,record_sha256=sha(body),duplicate=duplicate,
                **{k:False for k in observation.FLAGS})


def fixture_report():
    """Aggregate-only disposable supplied evidence; no provider or credential IO."""
    import tempfile
    from nexus.alpaca_paper_contract import preview
    account='01234567-89ab-cdef-0123-456789abcdef'
    with tempfile.TemporaryDirectory(prefix='nexus-paper-quotes-') as directory:
        root=Path(directory);store=PaperQuoteStore(root/'quotes.sqlite3',account,root/'requests.sqlite3')
        asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,
            min_order_size='.5',min_trade_increment='.5',price_increment='.5')
        plan=preview('nx_quote_fixture','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
        store.journal.prepare(plan);head=store.journal.recovery()['head_sha256'];before=store.request_path.read_bytes()
        scope=dict(profile=observation.PROFILE,account_sha256=store.account,source='INJECTED_UNATTESTED',account_fields_sha256=sha({'fixture':True}),
            quote=observation._quote({'quotes':{'BTC/USD':dict(bp='100',ap='101',bs='2',**{'as':'3'},t='2026-10-17T00:00:00.123456789Z')}},1792195201),
            read_timing=dict(started_at=1792195201,quote_received_at=1792195201,completed_at=1792195201,finalized_at=1792195201,elapsed_seconds=0))
        value=dict(scope,mode='READ_ONLY_PAPER_BTC_QUOTE_OBSERVATION',status='REVIEW_REQUIRED',observation_sha256=sha(scope),fixed_get_requests=3,
            sequential_account_fields_stable=True,timestamp_scope='EXCHANGE_NS_EXACT_LOCAL_RECEIPT_FLOAT_SECONDS_NOT_CLOCK_SYNCHRONIZATION',limitations='',**{k:False for k in observation.FLAGS})
        args=dict(client_order_id='nx_quote_fixture',expected_request_head=head,expected_quote_head=ZERO)
        receipt=store.retain(value,**args);args['expected_quote_head']=receipt['head_sha256']
        saved=store.path.read_bytes();duplicate=store.retain(value,**args)
        review=PaperQuoteStore(store.path,account,store.request_path).inspect(expected_request_head=head)
        unchanged=store.request_path.read_bytes()==before
        store.journal.mark_submission_uncertain('nx_quote_fixture')
        later=store.inspect(expected_request_head=store.journal.recovery()['head_sha256'])
        checks=dict(original_prefix_bound=later['records'][0]['request_head_sha256']==head and later['records'][0]['payload_sha256']==plan['payload_sha256'],
            exact_nanoseconds_retained=review['records'][0]['observation']['quote']['exchange_time_ns']=='1792195200123456789',
            duplicate_reused=duplicate['duplicate'] is True and store.path.read_bytes()==saved,
            restart_replay=review['retained_records']==1 and review['head_sha256']==receipt['head_sha256'],
            request_bytes_unchanged=unchanged,authority_withheld=all(review[k] is False and receipt[k] is False for k in observation.FLAGS))
        return dict(source='SYNTHETIC_ORIGINAL_REQUEST_QUOTE_RETENTION_FIXTURE',passed=all(checks.values()),**checks,
            actual_provider_access=False,actual_vault_access=False,**{k:False for k in observation.FLAGS})
