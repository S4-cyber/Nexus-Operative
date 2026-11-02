"""Content-free durable receipts for completed local mixed-read reviews.

The private normalized observations remain caller-supplied and are never stored.
This journal proves only that one bounded local review was completed against the
named pacing head and binding digests; it grants no provider or order authority.
"""
from contextlib import closing,contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from nexus.alpaca_paper_contract import canonical,sha
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints,check_existing,check_path_set)

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


def _require_main_connection(db,bound_path):
    """Require the opened SQLite connection to target the captured store."""
    try:
        rows=db.execute('PRAGMA database_list').fetchall()
        if (len(rows)!=1 or len(rows[0])!=3 or
                type(rows[0][0]) is not int or rows[0][1]!='main' or
                type(rows[0][2]) is not str or not rows[0][2] or
                Path(rows[0][2]).resolve()!=bound_path):
            _fail('CONNECTION_BINDING_CHANGED')
    except ValueError:
        raise
    except sqlite3.OperationalError as error:
        if _database_busy(error):_fail('DATABASE_BUSY')
        _fail('CONNECTION_BINDING_CHANGED')
    except (OSError,RuntimeError,sqlite3.Error,TypeError):
        _fail('CONNECTION_BINDING_CHANGED')


def _main_fingerprint(path):
    """Return one exact bounded quiescent main-file fingerprint."""
    return capture_file_fingerprints((path,),(MAX_DATABASE_BYTES,),
        'PAPER_READ_REVIEW_STORE_')[0]


def _require_main_connection_content(db,bound_path):
    """Require the connection's serialized main bytes to match its path."""
    expected=_main_fingerprint(bound_path)
    try:
        serialized=db.serialize(name='main')
        observed=_main_fingerprint(bound_path)
        if (observed!=expected or type(serialized) is not bytes or
                len(serialized)!=expected[3] or
                hashlib.sha256(serialized).hexdigest()!=expected[7]):
            _fail('CONNECTION_CONTENT_CHANGED')
    except ValueError:
        raise
    except sqlite3.OperationalError as error:
        if _database_busy(error):_fail('DATABASE_BUSY')
        # sqlite3.Connection.serialize() discards SQLITE_BUSY and reports the
        # code-less "unable to serialize 'main'" on an exclusive lock.  A
        # bounded, read-only schema probe recovers the real lock condition;
        # every other serializer failure remains a content-integrity failure.
        try:db.execute('SELECT 1 FROM sqlite_schema LIMIT 1').fetchone()
        except sqlite3.Error as probe:
            if isinstance(probe,sqlite3.OperationalError) and _database_busy(probe):
                _fail('DATABASE_BUSY')
        _fail('CONNECTION_CONTENT_CHANGED')
    except (MemoryError,OSError,OverflowError,RuntimeError,
            sqlite3.Error,TypeError):
        _fail('CONNECTION_CONTENT_CHANGED')


def _discard_uncommitted_new(path):
    """Remove only the exact empty target created before a rejected connect."""
    path=Path(path);sidecars=tuple(Path(str(path)+suffix) for suffix in
        ('-journal','-wal','-shm'))
    try:
        observed=path.lstat()
        if (stat.S_ISLNK(observed.st_mode) or
                not stat.S_ISREG(observed.st_mode) or observed.st_nlink!=1 or
                observed.st_size!=0):
            raise RuntimeError
        for sidecar in sidecars:
            try:sidecar.lstat()
            except FileNotFoundError:continue
            raise RuntimeError
        path.unlink()
        for candidate in (path,*sidecars):
            try:candidate.lstat()
            except FileNotFoundError:continue
            raise RuntimeError
    except (OSError,RuntimeError,TypeError,ValueError):
        _fail('UNCOMMITTED_CLEANUP_UNCERTAIN')


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
        self._check();new=False;bound_path=self.path.resolve()
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True,exist_ok=True)
            try:
                fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd);new=True
            except FileExistsError:pass
            self._check()
        try:
            db=sqlite3.connect(bound_path.as_uri()+('?mode=rw' if write else '?mode=ro'),
                uri=True,timeout=DB_BUSY_TIMEOUT_MS/1000)
        except BaseException:
            if new:_discard_uncommitted_new(bound_path)
            raise
        try:
            _require_main_connection(db,bound_path)
            if not new:_require_main_connection_content(db,bound_path)
            db.execute(f'PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}')
            if write:
                db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=FULL')
                size=db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES//size}');db.execute('BEGIN IMMEDIATE')
                if new:db.execute('CREATE TABLE records (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
            return db
        except sqlite3.OperationalError as error:
            try:db.close()
            finally:
                if new:_discard_uncommitted_new(bound_path)
            if _database_busy(error):_fail('DATABASE_BUSY')
            raise
        except BaseException:
            try:db.close()
            finally:
                if new:_discard_uncommitted_new(bound_path)
            raise

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
        try:
            with closing(self._connect()) as db:
                with _transaction(db):head,receipts=self._load(db)
        except BaseException as error:
            # A normal runtime failure after the read may include connection
            # close, so the caller cannot treat the result as acknowledged.
            # Preserve fixed semantic ValueErrors and process-control signals.
            if isinstance(error,Exception) and type(error) is not ValueError:
                _fail('READ_ACKNOWLEDGEMENT_UNCERTAIN')
            raise
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
        db=self._connect(write=True);committed=False;result=None
        try:
            with closing(db):
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
                committed=True
                if admission()!=receipt:_fail('ADMISSION_CHANGED_AFTER_COMMIT')
                if precommit_check() is not True:_fail('INPUT_REVOKED_AFTER_COMMIT')
                result=dict(mode='LOCAL_MIXED_READ_REVIEW_RECEIPT',head_sha256=head,
                    receipt_sha256=receipt,duplicate=duplicate,read_only=True,
                    ready_for_routing=False,release_approved=False)
            return result
        except BaseException as error:
            # A committed row may exist even when connection-close
            # acknowledgement fails. Never reconstruct success or retry it
            # automatically; require an explicit exact-head duplicate replay.
            if committed and isinstance(error,Exception) and type(error) is not ValueError:
                _fail('COMMIT_DURABILITY_UNCERTAIN')
            raise
