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


def _descriptor_fingerprint(descriptor,resolved):
    """Hash bounded bytes through the exact writable main-file descriptor."""
    before=os.fstat(descriptor)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or
            not 1<=before.st_size<=MAX_DATABASE_BYTES):
        raise RuntimeError
    original=os.lseek(descriptor,0,os.SEEK_CUR)
    digest=hashlib.sha256();remaining=MAX_DATABASE_BYTES+1
    try:
        os.lseek(descriptor,0,os.SEEK_SET)
        while remaining:
            block=os.read(descriptor,min(1024*1024,remaining))
            if not block:break
            digest.update(block);remaining-=len(block)
    finally:os.lseek(descriptor,original,os.SEEK_SET)
    after=os.fstat(descriptor)
    boundary=lambda item:(item.st_dev,item.st_ino,item.st_size,
        item.st_mtime_ns,item.st_ctime_ns if os.name!='nt' else 0,
        item.st_nlink)
    if remaining==0 or boundary(before)!=boundary(after):raise RuntimeError
    return (str(resolved),*boundary(after),digest.hexdigest())


def _sync_parent_directory(resolved):
    """Flush only the exact POSIX parent directory identity."""
    parent=resolved.parent;before=parent.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise RuntimeError
    expected=(before.st_dev,before.st_ino)
    flags=(os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|
        getattr(os,'O_CLOEXEC',0)|getattr(os,'O_NOFOLLOW',0))
    descriptor=os.open(parent,flags)
    try:
        opened=os.fstat(descriptor)
        if (not stat.S_ISDIR(opened.st_mode) or
                (opened.st_dev,opened.st_ino)!=expected):raise RuntimeError
        os.fsync(descriptor)
        final=os.fstat(descriptor);observed=parent.lstat()
        if (not stat.S_ISDIR(final.st_mode) or
                (final.st_dev,final.st_ino)!=expected or
                stat.S_ISLNK(observed.st_mode) or
                not stat.S_ISDIR(observed.st_mode) or
                (observed.st_dev,observed.st_ino)!=expected):
            raise RuntimeError
    finally:os.close(descriptor)


def _sync_committed_store(path,fingerprint,*,new):
    """Explicitly flush the exact committed main file before acknowledgement."""
    try:
        if (type(new) is not bool or type(fingerprint) is not tuple or
                len(fingerprint)!=8):raise RuntimeError
        path=Path(path);resolved=path.resolve()
        if fingerprint[0]!=str(resolved):raise RuntimeError
        flags=(os.O_RDWR|getattr(os,'O_BINARY',0)|
            getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        descriptor=os.open(resolved,flags)
        try:
            if _descriptor_fingerprint(descriptor,resolved)!=fingerprint:
                raise RuntimeError
            os.fsync(descriptor)
            if _descriptor_fingerprint(descriptor,resolved)!=fingerprint:
                raise RuntimeError
        finally:os.close(descriptor)
        if _main_fingerprint(path)!=fingerprint:raise RuntimeError
        if new and os.name!='nt':
            _sync_parent_directory(resolved)
            if _main_fingerprint(path)!=fingerprint:raise RuntimeError
    except BaseException:_fail('COMMIT_DURABILITY_UNCERTAIN')


def _require_main_connection(db,bound_path):
    """Require a writer to target exactly the captured SQLite main path."""
    try:
        rows=db.execute('PRAGMA database_list').fetchall()
        if (len(rows)!=1 or len(rows[0])!=3 or
                type(rows[0][0]) is not int or rows[0][1]!='main' or
                type(rows[0][2]) is not str or not rows[0][2] or
                Path(rows[0][2]).resolve()!=bound_path):
            _fail('CONNECTION_BINDING_CHANGED')
    except ValueError:raise
    except sqlite3.OperationalError as error:
        if _database_busy(error):_fail('DATABASE_BUSY')
        _fail('CONNECTION_BINDING_CHANGED')
    except (OSError,RuntimeError,sqlite3.Error,TypeError):
        _fail('CONNECTION_BINDING_CHANGED')


def _require_main_connection_content(db,bound_path):
    """Require an existing writer's exact main bytes before any mutation."""
    expected=_main_fingerprint(bound_path)
    try:
        serialized=db.serialize(name='main')
        if (type(serialized) is not bytes or
                len(serialized)!=expected[3] or
                hashlib.sha256(serialized).hexdigest()!=expected[7]):
            _fail('CONNECTION_CONTENT_CHANGED')
    except ValueError:raise
    except sqlite3.OperationalError as error:
        if _database_busy(error):_fail('DATABASE_BUSY')
        _fail('CONNECTION_CONTENT_CHANGED')
    except (MemoryError,OSError,OverflowError,RuntimeError,
            sqlite3.Error,TypeError):
        _fail('CONNECTION_CONTENT_CHANGED')


def _discard_uncommitted_new(path,identity):
    """Remove only the exact empty file from an aborted first write.

    A failed first SQLite transaction is not an initialized acceptance store.
    Cleanup is deliberately narrower than ordinary recovery: replacement,
    links, bytes or any SQLite sidecar retain the path and fail closed.
    """
    path=Path(path);sidecars=tuple(Path(str(path)+suffix) for suffix in
        ('-journal','-wal','-shm'))
    try:
        if (type(identity) is not tuple or len(identity)!=1 or
                type(identity[0]) is not tuple or len(identity[0])!=3):
            raise RuntimeError
        observed=path.lstat();resolved=path.resolve()
        if ((str(resolved),observed.st_dev,observed.st_ino)!=identity[0] or
                stat.S_ISLNK(observed.st_mode) or
                not stat.S_ISREG(observed.st_mode) or
                observed.st_nlink!=1 or observed.st_size!=0):
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
        self._check();new=False;created_identity=None;db=None
        try:
            if write and not self.path.exists():
                self.path.parent.mkdir(parents=True,exist_ok=True)
                try:
                    descriptor=os.open(self.path,os.O_CREAT|os.O_EXCL|
                        os.O_WRONLY,0o600)
                except FileExistsError:pass
                else:
                    new=True
                    try:
                        opened=os.fstat(descriptor)
                        created_identity=((str(self.path.resolve()),
                            opened.st_dev,opened.st_ino),)
                    finally:os.close(descriptor)
                self._check()
            db=sqlite3.connect(self.path.resolve().as_uri()+
                ('?mode=rw' if write else '?mode=ro'),uri=True,
                timeout=DB_BUSY_TIMEOUT_MS/1000)
            db.execute(f'PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}')
            if write:
                db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=FULL')
                size=db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES//size}');db.execute('BEGIN IMMEDIATE')
                if new:db.execute('CREATE TABLE records (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
            return db,new
        except sqlite3.OperationalError as error:
            if db is not None:
                try:db.close()
                except BaseException:
                    if new:_fail('UNCOMMITTED_CLEANUP_UNCERTAIN')
                    raise
            if new:_discard_uncommitted_new(self.path,created_identity)
            if _database_busy(error):_fail('DATABASE_BUSY')
            raise
        except BaseException:
            if db is not None:
                try:db.close()
                except BaseException:
                    if new:_fail('UNCOMMITTED_CLEANUP_UNCERTAIN')
                    raise
            if new:_discard_uncommitted_new(self.path,created_identity)
            raise

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
        baseline=dict(schema=SCHEMA,account_sha256=self.account_digest,
            baseline_pacing_head_sha256=pacing_head,baseline_retention_head_sha256=retention_head,
            baseline_pacing_sessions=pacing_sessions,baseline_retained_batches=retained_batches,
            preexisting_history_unverified=bool(pacing_sessions or retained_batches or pacing_head!=ZERO or retention_head!=ZERO))
        bound_path=Path(self.path).resolve();bound_account=self.account_digest
        def require_binding():
            try:current=Path(self.path).resolve()
            except (OSError,RuntimeError,TypeError,ValueError):
                _fail('STORE_BINDING_CHANGED')
            if current!=bound_path or self.account_digest!=bound_account:
                _fail('STORE_BINDING_CHANGED')
        self._check();require_binding();db,new=self._connect(write=True)
        committed=False;transaction_identity=None
        def require_identity():
            if transaction_identity is None:_fail('FILE_IDENTITY_CHANGED')
            require_file_identities((bound_path,),transaction_identity,
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_')
        try:
            try:
                with closing(db):
                    require_binding();_require_main_connection(db,bound_path)
                    transaction_identity=capture_file_identities((bound_path,),
                        'PAPER_ACTIVITY_ACCEPTANCE_STORE_')
                    require_identity()
                    if not new:
                        _require_main_connection_content(db,bound_path)
                    try:
                        with _transaction(db):
                            require_identity()
                            count=db.execute('SELECT count(*) FROM records').fetchone()[0]
                            if count==0:
                                self._insert(db,0,baseline,ZERO)
                            expected=self._load(db)
                            if count:
                                if (pacing_head!=expected['pacing_bound_head_sha256'] or
                                    retention_head!=expected['retention_bound_head_sha256'] or
                                    pacing_sessions!=expected['baseline_pacing_sessions']+expected['records'] or
                                    retained_batches!=expected['baseline_retained_batches']+expected['records']):
                                    _fail('REBIND_DISAGREEMENT')
                            require_identity()
                    finally:require_identity()
                    committed=True
                    require_binding();_require_main_connection(db,bound_path)
                    require_identity()
                    fingerprint=_main_fingerprint(bound_path)
                    _sync_committed_store(bound_path,fingerprint,new=new)
                    require_identity()
            finally:
                require_binding()
                if transaction_identity is not None:require_identity()
        except BaseException:
            if new and not committed:
                _discard_uncommitted_new(bound_path,transaction_identity)
            raise
        require_identity();first=self._read_state();require_identity()
        second=self._read_state();require_identity()
        if first!=second or second!=expected:_fail('ACKNOWLEDGEMENT_UNCERTAIN')
        return second

    def append(self,scope,*,admission,precommit_check=lambda:True):
        scope=self._scope(scope);record_sha=sha(scope)
        bound_path=Path(self.path).resolve();bound_account=self.account_digest
        def require_binding():
            try:current_path=Path(self.path).resolve()
            except (OSError,RuntimeError,TypeError,ValueError):
                _fail('STORE_BINDING_CHANGED')
            if (current_path!=bound_path or
                    self.account_digest!=bound_account):
                _fail('STORE_BINDING_CHANGED')
        self._check();require_binding()
        transaction_identity=None
        def require_identity():
            if transaction_identity is None:_fail('FILE_IDENTITY_CHANGED')
            require_file_identities((bound_path,),transaction_identity,
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_')
        def checked(callback):
            require_identity()
            before=_main_fingerprint(bound_path)
            try:value=callback()
            finally:
                require_binding()
                require_identity()
                if _main_fingerprint(bound_path)!=before:
                    _fail('FILE_SET_CHANGED')
            return value
        db,new=self._connect(write=True);committed=False
        try:
            try:
                with closing(db):
                    require_binding();_require_main_connection(db,bound_path)
                    transaction_identity=capture_file_identities((bound_path,),
                        'PAPER_ACTIVITY_ACCEPTANCE_STORE_')
                    require_identity()
                    if not new:
                        _require_main_connection_content(db,bound_path)
                    try:
                        with _transaction(db):
                            require_identity()
                            state=self._load(db);current=checked(admission)
                            if current!=(scope['pacing_after_head_sha256'],scope['retention_after_head_sha256']):_fail('ADMISSION_CHANGED')
                            duplicate=state['last_record_sha256']==record_sha
                            if duplicate:
                                expected_head=state['head_sha256']
                            else:
                                if state['records']>=MAX_RECORDS:_fail('RECORD_CAP')
                                if (state['pacing_bound_head_sha256']!=scope['pacing_before_head_sha256'] or
                                    state['retention_bound_head_sha256']!=scope['retention_before_head_sha256']):_fail('CONTINUITY_DISAGREEMENT')
                                body=dict(scope=scope,record_sha256=record_sha)
                                expected_head=sha(dict(seq=state['records']+1,
                                    body=body,previous=state['head_sha256']))
                                self._insert(db,state['records']+1,body,
                                    state['head_sha256'])
                                loaded=self._load(db)
                                if (loaded['head_sha256']!=expected_head or
                                        loaded['last_record_sha256']!=record_sha):
                                    _fail('ACKNOWLEDGEMENT_UNCERTAIN')
                            require_identity()
                            if checked(precommit_check) is not True:_fail('INPUT_REVOKED')
                            require_identity()
                    finally:require_identity()
                    committed=True
                    require_binding();_require_main_connection(db,bound_path)
                    require_identity()
                    fingerprint=_main_fingerprint(bound_path)
                    _sync_committed_store(bound_path,fingerprint,new=new)
                    require_identity()
                    # A changed post-commit sample loses the acknowledgment but
                    # retains the record for explicit cold-read recovery.
                    if checked(admission)!=(scope['pacing_after_head_sha256'],scope['retention_after_head_sha256']):_fail('ADMISSION_CHANGED')
            finally:
                require_binding()
                if transaction_identity is not None:require_identity()
        except BaseException:
            if new and not committed:
                _discard_uncommitted_new(bound_path,transaction_identity)
            raise
        require_identity();first=self._read_state();require_identity()
        second=self._read_state();require_identity()
        if (first!=second or second['head_sha256']!=expected_head or
                second['last_record_sha256']!=record_sha or
                second['last_record_scope']!=scope):
            _fail('ACKNOWLEDGEMENT_UNCERTAIN')
        return dict(head_sha256=second['head_sha256'],
            record_sha256=record_sha,duplicate=duplicate)
