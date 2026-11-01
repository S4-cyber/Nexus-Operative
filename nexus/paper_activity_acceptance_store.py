"""Durable aggregate binding between paper-read pacing and activity retention.

This private local journal records only content digests and before/after heads.
It never reads credentials, contacts a provider, retries a read or grants order,
funding, reconciliation, coverage, source or release authority.
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
from nexus.sqlite_journal_guard import (
    capture_file_identities,check_existing,require_file_identities)

SCHEMA = 'nexus.paper-activity-acceptance-history.v1'
PROFILE = 'nexus.paper-activity-acceptance-record.v2'
REQUEST_PROFILE = 'nexus.paper-activity-acceptance-record.v3'
MAX_RECORDS = 128
MAX_DATABASE_BYTES = 4 * 1024 * 1024
DB_BUSY_TIMEOUT_MS = 10_000
MAX_RECORD_BYTES = 4096
ZERO = '0' * 64
HEAD_KEYS = ('pacing_before_head_sha256','pacing_after_head_sha256',
    'retention_before_head_sha256','retention_after_head_sha256')
REQUEST_KEYS = {'request_head_sha256','request_orders_sha256'}
SCOPE_KEYS = {'profile','account_sha256','collection_scope_sha256','account_fields_sha256',
    'collection_observation_sha256','activity_input_sha256','retained_receipt_sha256',*HEAD_KEYS}


def _fail(code):
    raise ValueError('PAPER_ACTIVITY_ACCEPTANCE_STORE_' + code) from None


def _database_busy(error):
    code = getattr(error, 'sqlite_errorcode', None)
    return (code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or
        any(word in str(error).lower() for word in ('busy', 'locked')))


def _main_fingerprint(path):
    """Hash one bounded main file while retaining any rollback evidence."""
    try:
        path=Path(path);observed=path.lstat()
        if (stat.S_ISLNK(observed.st_mode) or
                not stat.S_ISREG(observed.st_mode) or
                observed.st_nlink!=1 or
                not 1<=observed.st_size<=MAX_DATABASE_BYTES):
            _fail('FILE_SET_CHANGED')
        flags=(os.O_RDONLY|getattr(os,'O_BINARY',0)|
            getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        digest=hashlib.sha256();remaining=MAX_DATABASE_BYTES+1
        with os.fdopen(os.open(path.resolve(),flags),'rb',buffering=0) as stream:
            before=os.fstat(stream.fileno())
            while remaining:
                block=stream.read(min(1024*1024,remaining))
                if not block:break
                digest.update(block);remaining-=len(block)
            after=os.fstat(stream.fileno())
        final=path.lstat()
        boundary=lambda item:(item.st_dev,item.st_ino,item.st_size,
            item.st_mtime_ns,item.st_ctime_ns if os.name!='nt' else None,
            item.st_nlink)
        if (remaining==0 or boundary(observed)!=boundary(before) or
                boundary(before)!=boundary(after) or
                boundary(after)!=boundary(final) or
                before.st_ctime_ns!=after.st_ctime_ns or
                not stat.S_ISREG(final.st_mode)):
            _fail('FILE_SET_CHANGED')
        return (str(path.resolve()),after.st_dev,after.st_ino,after.st_size,
            after.st_mtime_ns,final.st_ctime_ns if os.name!='nt' else 0,
            after.st_nlink,
            digest.hexdigest())
    except ValueError:
        raise
    except (OSError,RuntimeError,TypeError):
        _fail('FILE_SET_CHANGED')


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


def _head(value):
    if not isinstance(value,str) or re.fullmatch(r'[0-9a-f]{64}',value) is None:_fail('HEAD_INVALID')
    return value


class PaperActivityAcceptanceStore:
    def __init__(self,path,paper_account_id):
        self.path=Path(path);self.account=_uuid(paper_account_id)
        self.account_digest=hashlib.sha256(self.account.encode()).hexdigest()

    def _check(self):check_existing(self.path,MAX_DATABASE_BYTES,'PAPER_ACTIVITY_ACCEPTANCE_STORE_')

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
            return db,new
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
        if not isinstance(value,dict):_fail('SCOPE_INVALID')
        expected=(SCOPE_KEYS if value.get('profile')==PROFILE else SCOPE_KEYS|REQUEST_KEYS if value.get('profile')==REQUEST_PROFILE else None)
        if expected is None or set(value)!=expected or value['account_sha256']!=self.account_digest:_fail('SCOPE_INVALID')
        for key in expected-{'profile'}:_head(value[key])
        if (value['pacing_before_head_sha256']==value['pacing_after_head_sha256'] or
            value['retention_before_head_sha256']==value['retention_after_head_sha256']):_fail('TRANSITION_MISSING')
        return value

    def _load(self,db):
        rows=db.execute('SELECT seq,body,previous,digest FROM records ORDER BY seq LIMIT ?',(MAX_RECORDS+2,)).fetchall()
        if not rows:_fail('BINDING_MISSING')
        if len(rows)>MAX_RECORDS+1:_fail('RECORD_CAP')
        previous=ZERO;state=None
        for index,(seq,encoded,parent,digest) in enumerate(rows):
            if not isinstance(encoded,str) or len(encoded.encode())>MAX_RECORD_BYTES:_fail('RECORD_CAP')
            try:body=json.loads(encoded)
            except (ValueError,TypeError):_fail('PAYLOAD_INVALID')
            if seq!=index or parent!=previous or canonical(body)!=encoded or digest!=sha(dict(seq=seq,body=body,previous=parent)):_fail('CHAIN_DISAGREEMENT')
            if index==0:
                keys={'schema','account_sha256','baseline_pacing_head_sha256','baseline_retention_head_sha256',
                    'baseline_pacing_sessions','baseline_retained_batches','preexisting_history_unverified'}
                if not isinstance(body,dict) or set(body)!=keys or body['schema']!=SCHEMA or body['account_sha256']!=self.account_digest:_fail('ACCOUNT_OR_SCHEMA')
                _head(body['baseline_pacing_head_sha256']);_head(body['baseline_retention_head_sha256'])
                for key in ('baseline_pacing_sessions','baseline_retained_batches'):
                    if type(body[key]) is not int or body[key]<0:_fail('BASELINE_INVALID')
                expected=bool(body['baseline_pacing_sessions'] or body['baseline_retained_batches'] or
                    body['baseline_pacing_head_sha256']!=ZERO or body['baseline_retention_head_sha256']!=ZERO)
                if type(body['preexisting_history_unverified']) is not bool or body['preexisting_history_unverified'] is not expected:_fail('BASELINE_INVALID')
                state=dict(head_sha256=digest,records=0,pacing_bound_head_sha256=body['baseline_pacing_head_sha256'],
                    retention_bound_head_sha256=body['baseline_retention_head_sha256'],
                    baseline_pacing_sessions=body['baseline_pacing_sessions'],baseline_retained_batches=body['baseline_retained_batches'],
                    preexisting_history_unverified=expected,last_record_sha256=None,last_record_scope=None,record_scopes=[])
            else:
                if not isinstance(body,dict) or set(body)!={'scope','record_sha256'}:_fail('PAYLOAD_INVALID')
                scope=self._scope(body['scope']);record_sha=sha(scope)
                if body['record_sha256']!=record_sha:_fail('RECORD_DIGEST_INVALID')
                if (scope['pacing_before_head_sha256']!=state['pacing_bound_head_sha256'] or
                    scope['retention_before_head_sha256']!=state['retention_bound_head_sha256']):_fail('CONTINUITY_DISAGREEMENT')
                state.update(head_sha256=digest,records=index,
                    pacing_bound_head_sha256=scope['pacing_after_head_sha256'],
                    retention_bound_head_sha256=scope['retention_after_head_sha256'],last_record_sha256=record_sha,
                    last_record_scope=scope)
                state['record_scopes'].append(scope)
            previous=digest
        return state

    def _read_state(self):
        """Replay one exact bounded store through the connection that opened it."""
        bound_path=Path(self.path).resolve()
        def require_binding():
            try:current=Path(self.path).resolve()
            except (OSError,RuntimeError,TypeError,ValueError):
                _fail('STORE_BINDING_CHANGED')
            if current!=bound_path:_fail('STORE_BINDING_CHANGED')
        self._check();require_binding()
        identity=capture_file_identities((bound_path,),
            'PAPER_ACTIVITY_ACCEPTANCE_STORE_')
        fingerprint=_main_fingerprint(bound_path)
        def require_store():
            require_binding()
            require_file_identities((bound_path,),identity,
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_')
            if _main_fingerprint(bound_path)!=fingerprint:
                _fail('FILE_SET_CHANGED')
        def require_connection(db,*,content=True):
            try:
                rows=db.execute('PRAGMA database_list').fetchall()
                if (len(rows)!=1 or len(rows[0])!=3 or
                        type(rows[0][0]) is not int or rows[0][1]!='main' or
                        type(rows[0][2]) is not str or not rows[0][2]):
                    _fail('CONNECTION_BINDING_CHANGED')
                connected=Path(rows[0][2]).resolve()
                if connected!=bound_path:_fail('CONNECTION_BINDING_CHANGED')
                if not content:return
                serialized=db.serialize(name='main');expected=fingerprint
                if (type(serialized) is not bytes or
                        len(serialized)!=expected[3] or
                        hashlib.sha256(serialized).hexdigest()!=expected[7]):
                    _fail('CONNECTION_CONTENT_CHANGED')
            except ValueError:
                raise
            except sqlite3.OperationalError as error:
                if _database_busy(error):_fail('DATABASE_BUSY')
                _fail('CONNECTION_BINDING_CHANGED')
            except (MemoryError,OSError,OverflowError,RuntimeError,
                    sqlite3.Error,TypeError):
                _fail('CONNECTION_BINDING_CHANGED')
        try:
            db,_=self._connect()
            with closing(db):
                require_store();require_connection(db,content=False)
                with _transaction(db):
                    db.execute('BEGIN')
                    db.execute('SELECT count(*) FROM sqlite_master').fetchone()
                    require_store();require_connection(db)
                    state=self._load(db)
                    require_connection(db);require_store()
        finally:require_store()
        return state

    def inspect(self):
        self._check()
        if not self.path.exists():
            return dict(mode='PAPER_ACTIVITY_ACCEPTANCE_HISTORY',initialized=False,account_sha256=self.account_digest,
                head_sha256=ZERO,records=0,pacing_bound_head_sha256=None,retention_bound_head_sha256=None,
                baseline_pacing_sessions=0,baseline_retained_batches=0,preexisting_history_unverified=False,last_record_sha256=None,
                last_record_scope=None,
                automatic_retry=False,ready_for_routing=False)
        state=self._read_state()
        public=dict(state);public.pop('record_scopes')
        return dict(mode='PAPER_ACTIVITY_ACCEPTANCE_HISTORY',initialized=True,account_sha256=self.account_digest,**public,
            automatic_retry=False,ready_for_routing=False)

    def replay_scopes(self):
        """Return validated aggregate scopes in retained order for local replay."""
        self._check()
        if not self.path.exists(): return ()
        state=self._read_state()
        return tuple(dict(scope) for scope in state['record_scopes'])

    def bind(self,*,pacing_head,retention_head,pacing_sessions,retained_batches):
        pacing_head=_head(pacing_head);retention_head=_head(retention_head)
        if any(type(v) is not int or v<0 for v in (pacing_sessions,retained_batches)):_fail('BASELINE_INVALID')
        with closing(self._connect(write=True)[0]) as db:
            with _transaction(db):
                count=db.execute('SELECT count(*) FROM records').fetchone()[0]
                if count==0:
                    body=dict(schema=SCHEMA,account_sha256=self.account_digest,
                        baseline_pacing_head_sha256=pacing_head,baseline_retention_head_sha256=retention_head,
                        baseline_pacing_sessions=pacing_sessions,baseline_retained_batches=retained_batches,
                        preexisting_history_unverified=bool(pacing_sessions or retained_batches or pacing_head!=ZERO or retention_head!=ZERO))
                    self._insert(db,0,body,ZERO)
                state=self._load(db)
            return state

    def append(self,scope,*,admission,precommit_check=lambda:True):
        scope=self._scope(scope);record_sha=sha(scope)
        with closing(self._connect(write=True)[0]) as db:
            with _transaction(db):
                state=self._load(db);current=admission()
                if current!=(scope['pacing_after_head_sha256'],scope['retention_after_head_sha256']):_fail('ADMISSION_CHANGED')
                if state['last_record_sha256']==record_sha:
                    return dict(head_sha256=state['head_sha256'],record_sha256=record_sha,duplicate=True)
                if state['records']>=MAX_RECORDS:_fail('RECORD_CAP')
                if (state['pacing_bound_head_sha256']!=scope['pacing_before_head_sha256'] or
                    state['retention_bound_head_sha256']!=scope['retention_before_head_sha256']):_fail('CONTINUITY_DISAGREEMENT')
                head=self._insert(db,state['records']+1,dict(scope=scope,record_sha256=record_sha),state['head_sha256'])
                self._load(db)
                if precommit_check() is not True:_fail('INPUT_REVOKED')
            # A changed post-commit sample loses the acknowledgment but retains
            # the record so status/replay can show the exact uncertain boundary.
            if admission()!=(scope['pacing_after_head_sha256'],scope['retention_after_head_sha256']):_fail('ADMISSION_CHANGED')
            return dict(head_sha256=head,record_sha256=record_sha,duplicate=False)
