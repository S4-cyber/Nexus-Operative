"""Content-free durable receipts for completed local mixed-read reviews.

The private normalized observations remain caller-supplied and are never stored.
This journal proves only that one bounded local review was completed against the
named pacing head and binding digests; it grants no provider or order authority.
"""
from contextlib import closing,contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3

from nexus.alpaca_paper_contract import canonical,sha
from nexus.sqlite_journal_guard import check_existing,check_path_set

SCHEMA='nexus.paper-read-review-history.v1'
PROFILE='nexus.paper-read-review-receipt.v1'
MAX_RECORDS=256
MAX_RECORD_BYTES=8192
MAX_DATABASE_BYTES=4*1024*1024
DB_BUSY_TIMEOUT_MS=10_000
ZERO='0'*64


def _fail(code):raise ValueError('PAPER_READ_REVIEW_STORE_'+code) from None


def _database_busy(error):
    code=getattr(error,'sqlite_errorcode',None)
    return (code in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) or
        any(word in str(error).lower() for word in ('busy','locked')))


@contextmanager
def _transaction(db):
    """Normalize bounded SQLite statement/commit contention without retry."""
    try:
        with db:yield
    except sqlite3.OperationalError as error:
        if _database_busy(error):_fail('DATABASE_BUSY')
        raise


def _digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:_fail('DIGEST_INVALID')
    return value


class PaperReadReviewStore:
    """Hash-chained aggregate receipts bound to one pacing journal/account."""
    def __init__(self,path,gate):
        from nexus.paper_read_pacing import PaperReadPacing
        if type(gate) is not PaperReadPacing:_fail('PACING_REQUIRED')
        self.path=Path(path);self.gate=gate;self.account=gate.account_digest
        check_path_set((self.path,gate.path),'PAPER_READ_REVIEW_STORE_')

    def _check(self):
        check_path_set((self.path,self.gate.path),'PAPER_READ_REVIEW_STORE_')
        check_existing(self.path,MAX_DATABASE_BYTES,'PAPER_READ_REVIEW_STORE_')

    def _connect(self,write=False):
        self._check();new=False
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True,exist_ok=True)
            try:
                fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd);new=True
            except FileExistsError:pass
            self._check()
        db=sqlite3.connect(self.path.resolve().as_uri()+('?mode=rw' if write else '?mode=ro'),
            uri=True,timeout=DB_BUSY_TIMEOUT_MS/1000)
        try:
            db.execute(f'PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}')
            if write:
                db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=FULL')
                size=db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES//size}');db.execute('BEGIN IMMEDIATE')
                if new:db.execute('CREATE TABLE records (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
            return db
        except sqlite3.OperationalError as error:
            db.close()
            if _database_busy(error):_fail('DATABASE_BUSY')
            raise
        except BaseException:db.close();raise

    @staticmethod
    def _insert(db,seq,body,previous):
        encoded=canonical(body)
        if len(encoded.encode())>MAX_RECORD_BYTES:_fail('RECORD_CAP')
        digest=sha(dict(seq=seq,body=body,previous=previous))
        db.execute('INSERT INTO records VALUES (?,?,?,?)',(seq,encoded,previous,digest));return digest

    def _scope(self,value):
        keys={'profile','account_sha256','pacing_head_sha256','review_sha256','binding_manifest_sha256',
            'sessions_replayed','supplied_bindings','matched_sessions','operation_counts',
            'all_five_purposes_observed','complete_mixed_history_consistent'}
        if type(value) is not dict or set(value)!=keys or value['profile']!=PROFILE or value['account_sha256']!=self.account:_fail('SCOPE_INVALID')
        for key in ('pacing_head_sha256','review_sha256','binding_manifest_sha256'):_digest(value[key])
        for key in ('sessions_replayed','supplied_bindings','matched_sessions'):
            if type(value[key]) is not int or not 0<value[key]<=256:_fail('SCOPE_INVALID')
        if not value['sessions_replayed']==value['supplied_bindings']==value['matched_sessions']:_fail('SCOPE_INVALID')
        from nexus.paper_read_pacing import OPERATIONS,LEGACY_OPERATION
        counts=value['operation_counts']
        if (type(counts) is not dict or set(counts)!={*OPERATIONS,LEGACY_OPERATION} or
            any(type(v) is not int or v<0 for v in counts.values()) or sum(counts.values())!=value['sessions_replayed'] or
            counts[LEGACY_OPERATION]!=0):_fail('SCOPE_INVALID')
        if value['all_five_purposes_observed'] is not True or value['complete_mixed_history_consistent'] is not True:_fail('SCOPE_INVALID')
        return value

    def _load(self,db):
        rows=db.execute('SELECT seq,body,previous,digest FROM records ORDER BY seq LIMIT ?',(MAX_RECORDS+2,)).fetchall()
        if not rows:_fail('BINDING_MISSING')
        if len(rows)>MAX_RECORDS+1:_fail('RECORD_CAP')
        previous=ZERO;receipts=[]
        for index,(seq,encoded,parent,digest) in enumerate(rows):
            if type(encoded) is not str or len(encoded.encode())>MAX_RECORD_BYTES:_fail('RECORD_CAP')
            try:body=json.loads(encoded)
            except (ValueError,TypeError):_fail('PAYLOAD_INVALID')
            if seq!=index or parent!=previous or canonical(body)!=encoded or digest!=sha(dict(seq=seq,body=body,previous=parent)):_fail('CHAIN_DISAGREEMENT')
            if index==0:
                if body!=dict(schema=SCHEMA,account_sha256=self.account):_fail('ACCOUNT_OR_SCHEMA')
            else:
                if type(body) is not dict or set(body)!={'scope','receipt_sha256'}:_fail('PAYLOAD_INVALID')
                scope=self._scope(body['scope']);receipt=sha(scope)
                if body['receipt_sha256']!=receipt:_fail('RECEIPT_DIGEST_INVALID')
                receipts.append(dict(scope=scope,receipt_sha256=receipt,after_head_sha256=digest))
            previous=digest
        return previous,receipts

    def inspect(self):
        self._check()
        if not self.path.exists():
            return dict(mode='LOCAL_MIXED_READ_REVIEW_HISTORY',initialized=False,account_sha256=self.account,
                head_sha256=ZERO,retained_receipts=0,receipts=[],read_only=True,ready_for_routing=False,release_approved=False)
        with closing(self._connect()) as db:
            with _transaction(db):head,receipts=self._load(db)
        return dict(mode='LOCAL_MIXED_READ_REVIEW_HISTORY',initialized=True,account_sha256=self.account,
            head_sha256=head,retained_receipts=len(receipts),receipts=receipts,read_only=True,
            ready_for_routing=False,release_approved=False)

    def append(self,scope,*,expected_head,admission,precommit_check=lambda:True):
        """Append one verified aggregate scope; changed inputs lose acknowledgment."""
        scope=self._scope(scope);expected_head=_digest(expected_head);receipt=sha(scope)
        current=self.inspect()
        if current['head_sha256']!=expected_head:_fail('HEAD_CHANGED')
        if admission()!=receipt:_fail('ADMISSION_CHANGED')
        if precommit_check() is not True:_fail('INPUT_REVOKED')
        with closing(self._connect(write=True)) as db:
            with _transaction(db):
                count=db.execute('SELECT count(*) FROM records').fetchone()[0]
                if count==0:
                    head=self._insert(db,0,dict(schema=SCHEMA,account_sha256=self.account),ZERO)
                    receipts=[]
                else:head,receipts=self._load(db)
                if expected_head==ZERO and count==0:pass
                elif head!=expected_head:_fail('HEAD_CHANGED')
                duplicate=bool(receipts and receipts[-1]['receipt_sha256']==receipt)
                if not duplicate:
                    if len(receipts)>=MAX_RECORDS:_fail('RECORD_CAP')
                    head=self._insert(db,len(receipts)+1,dict(scope=scope,receipt_sha256=receipt),head)
                    self._load(db)
                if admission()!=receipt:_fail('ADMISSION_CHANGED')
                if precommit_check() is not True:_fail('INPUT_REVOKED')
            if admission()!=receipt:_fail('ADMISSION_CHANGED_AFTER_COMMIT')
            if precommit_check() is not True:_fail('INPUT_REVOKED_AFTER_COMMIT')
            return dict(mode='LOCAL_MIXED_READ_REVIEW_RECEIPT',head_sha256=head,
                receipt_sha256=receipt,duplicate=duplicate,read_only=True,
                ready_for_routing=False,release_approved=False)
