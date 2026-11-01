"""Content-free durable receipts for local provider/account read review."""
from contextlib import closing,contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from nexus.alpaca_paper_contract import canonical,sha
from nexus.paper_provider_read_review import MAX_ACCOUNTS,PROFILE,ZERO
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints,capture_file_identities,check_existing,
    check_path_set,guard_file_set_finalization,require_file_fingerprints,
    require_file_identities)

SCHEMA='nexus.paper-provider-read-review-history.v1'
RESULT_PROFILE='nexus.paper-provider-result-review-receipt.v1'
MAX_RECORDS=256
MAX_RECORD_BYTES=4096
MAX_DATABASE_BYTES=4*1024*1024
DB_BUSY_TIMEOUT_MS=10_000


def _fail(code):raise ValueError('PAPER_PROVIDER_READ_REVIEW_STORE_'+code) from None


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
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}',value) is None:_fail('DIGEST_INVALID')
    return value


def _active_fingerprint_boundary(item,host=None):
    """Use fields shared by pathname and open-handle APIs on this host."""
    host=os.name if host is None else host
    return (item.st_dev,item.st_ino,item.st_size,item.st_mtime_ns,
        item.st_ctime_ns if host!='nt' else None,item.st_nlink)


def _active_file_fingerprint(path):
    """Bounded same-file sample while this writer owns an active transaction.

    DELETE-mode SQLite legitimately has a rollback sidecar at this boundary,
    so the ordinary quiescent file-set sampler cannot be used.  This samples
    only the already-open writer's main file around an untrusted callback; the
    quiescent complete file-set proof still runs before and after the write.
    """
    try:
        observed=path.lstat()
        if (stat.S_ISLNK(observed.st_mode) or
                not stat.S_ISREG(observed.st_mode) or
                observed.st_nlink!=1 or
                not 0<=observed.st_size<=MAX_DATABASE_BYTES):
            _fail('FILE_SET_CHANGED')
        flags=(os.O_RDONLY|getattr(os,'O_BINARY',0)|
            getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        digest=hashlib.sha256()
        with os.fdopen(os.open(path.resolve(),flags),'rb',buffering=0) as stream:
            before=os.fstat(stream.fileno());remaining=MAX_DATABASE_BYTES+1
            while remaining:
                block=stream.read(min(1024*1024,remaining))
                if not block:break
                digest.update(block);remaining-=len(block)
            after=os.fstat(stream.fileno())
        final=path.lstat()
        if (remaining==0 or
                _active_fingerprint_boundary(observed)!=
                    _active_fingerprint_boundary(before) or
                _active_fingerprint_boundary(before)!=
                    _active_fingerprint_boundary(after) or
                _active_fingerprint_boundary(after)!=
                    _active_fingerprint_boundary(final) or
                not stat.S_ISREG(final.st_mode)):
            _fail('FILE_SET_CHANGED')
        return (*_active_fingerprint_boundary(final),digest.hexdigest())
    except ValueError:
        raise
    except (OSError,RuntimeError,TypeError):
        _fail('FILE_SET_CHANGED')


def _committed_fingerprint_boundary(fingerprint,host=None):
    """Normalize a one-file pathname fingerprint for descriptor comparison."""
    host=os.name if host is None else host
    if (type(fingerprint) is not tuple or len(fingerprint)!=1 or
            type(fingerprint[0]) is not tuple or len(fingerprint[0])!=8):
        raise RuntimeError
    path,device,inode,size,mtime,ctime,links,digest=fingerprint[0]
    if (type(path) is not str or any(type(value) is not int for value in
            (device,inode,size,mtime,ctime,links)) or
            re.fullmatch(r'[0-9a-f]{64}',digest) is None):
        raise RuntimeError
    return (path,device,inode,size,mtime,ctime if host!='nt' else None,
        links,digest)


def _descriptor_file_fingerprint(descriptor,resolved):
    """Hash bounded bytes through the exact writable committed descriptor."""
    before=os.fstat(descriptor)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or
            not 1<=before.st_size<=MAX_DATABASE_BYTES):
        raise RuntimeError
    original_offset=os.lseek(descriptor,0,os.SEEK_CUR)
    digest=hashlib.sha256();remaining=MAX_DATABASE_BYTES+1
    try:
        os.lseek(descriptor,0,os.SEEK_SET)
        while remaining:
            block=os.read(descriptor,min(1024*1024,remaining))
            if not block:break
            digest.update(block);remaining-=len(block)
    finally:
        os.lseek(descriptor,original_offset,os.SEEK_SET)
    after=os.fstat(descriptor)
    if (remaining==0 or
            _active_fingerprint_boundary(before)!=
                _active_fingerprint_boundary(after) or
            before.st_ctime_ns!=after.st_ctime_ns):
        raise RuntimeError
    return (str(resolved),after.st_dev,after.st_ino,after.st_size,
        after.st_mtime_ns,after.st_ctime_ns if os.name!='nt' else None,
        after.st_nlink,digest.hexdigest())


def _sync_parent_directory(resolved):
    """Flush only the exact POSIX parent directory identity."""
    parent=resolved.parent
    before=parent.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise RuntimeError
    expected=(before.st_dev,before.st_ino)
    flags=(os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|
        getattr(os,'O_CLOEXEC',0)|getattr(os,'O_NOFOLLOW',0))
    descriptor=os.open(parent,flags)
    try:
        opened=os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev,
                opened.st_ino)!=expected:
            raise RuntimeError
        os.fsync(descriptor)
        final=os.fstat(descriptor)
        observed=parent.lstat()
        if (not stat.S_ISDIR(final.st_mode) or
                (final.st_dev,final.st_ino)!=expected or
                stat.S_ISLNK(observed.st_mode) or
                not stat.S_ISDIR(observed.st_mode) or
                (observed.st_dev,observed.st_ino)!=expected):
            raise RuntimeError
    finally:
        os.close(descriptor)


def _discard_uncommitted_new(path,identity):
    """Remove only the exact empty file created for an aborted first write.

    SQLite rolls a failed first transaction back to an empty main file.  That
    file is not an initialized store and must not poison an exact retry.  This
    is deliberately a narrow, sampled cleanup: a different identity, a
    sidecar, a link, a non-regular file or any bytes make the outcome
    uncertain and retain the path fail-closed.
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


def _sync_committed_store(path,identity,fingerprint,*,new):
    """Explicitly flush one committed main file and its new POSIX entry.

    SQLite ``synchronous=FULL`` remains the transaction durability policy.
    This additional descriptor boundary makes the caller observe a failed
    file flush instead of accepting a merely replayable commit.  The exact
    post-commit bounded fingerprint is fixed before the flush and rechecked
    around both the file and directory boundaries.  The exact writable
    descriptor is independently hashed before and after its flush, so a
    pathname sample cannot hide different descriptor bytes.  A new POSIX
    pathname also flushes only a descriptor proven to be its exact parent
    directory identity.  Python has no portable
    Windows directory-fsync API, so Windows retains the explicit writable
    main-file flush and the existing installer/native acceptance gate.  Any
    uncertainty preserves the committed store and fails closed.
    """
    try:
        if (type(new) is not bool or type(identity) is not tuple or
                len(identity)!=1 or type(identity[0]) is not tuple or
                len(identity[0])!=3):
            raise RuntimeError
        path=Path(path);resolved=path.resolve()
        expected_path,expected_device,expected_inode=identity[0]
        if str(resolved)!=expected_path:raise RuntimeError
        expected_descriptor=_committed_fingerprint_boundary(fingerprint)
        require_file_identities((path,),identity,
            'PAPER_PROVIDER_READ_REVIEW_STORE_')
        require_file_fingerprints((path,),(MAX_DATABASE_BYTES,),fingerprint,
            'PAPER_PROVIDER_READ_REVIEW_STORE_')
        flags=(os.O_RDWR|getattr(os,'O_BINARY',0)|
            getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        descriptor=os.open(resolved,flags)
        try:
            before=os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or
                    not 1<=before.st_size<=MAX_DATABASE_BYTES or
                    (before.st_dev,before.st_ino)!=(expected_device,
                        expected_inode)):
                raise RuntimeError
            if _descriptor_file_fingerprint(descriptor,resolved)!=expected_descriptor:
                raise RuntimeError
            os.fsync(descriptor)
            if _descriptor_file_fingerprint(descriptor,resolved)!=expected_descriptor:
                raise RuntimeError
        finally:
            os.close(descriptor)
        require_file_identities((path,),identity,
            'PAPER_PROVIDER_READ_REVIEW_STORE_')
        require_file_fingerprints((path,),(MAX_DATABASE_BYTES,),fingerprint,
            'PAPER_PROVIDER_READ_REVIEW_STORE_')
        if new and os.name!='nt':
            _sync_parent_directory(resolved)
            require_file_identities((path,),identity,
                'PAPER_PROVIDER_READ_REVIEW_STORE_')
            require_file_fingerprints((path,),(MAX_DATABASE_BYTES,),fingerprint,
                'PAPER_PROVIDER_READ_REVIEW_STORE_')
    except BaseException:
        _fail('COMMIT_DURABILITY_UNCERTAIN')


class PaperProviderReadReviewStore:
    def __init__(self,path,provider_budget,gates):
        from nexus.paper_provider_read_budget import PaperProviderReadBudget
        from nexus.paper_read_pacing import PaperReadPacing
        if type(provider_budget) is not PaperProviderReadBudget:_fail('PROVIDER_BUDGET_REQUIRED')
        if type(gates) not in (list,tuple) or not 1<=len(gates)<=MAX_ACCOUNTS or any(type(g) is not PaperReadPacing for g in gates):
            _fail('ACCOUNTS_INVALID')
        self.path=Path(path);self.provider=provider_budget.provider_digest
        self.protected=(provider_budget.path,*(gate.path for gate in gates))
        check_path_set((self.path,*self.protected),'PAPER_PROVIDER_READ_REVIEW_STORE_')

    def _check(self):
        check_path_set((self.path,*self.protected),'PAPER_PROVIDER_READ_REVIEW_STORE_')
        check_existing(self.path,MAX_DATABASE_BYTES,'PAPER_PROVIDER_READ_REVIEW_STORE_')

    def _connect(self,write=False):
        self._check();new=False;created_identity=None;db=None
        try:
            if write and not self.path.exists():
                self.path.parent.mkdir(parents=True,exist_ok=True)
                try:
                    fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                except FileExistsError:pass
                else:
                    new=True
                    try:
                        opened=os.fstat(fd)
                        created_identity=((str(self.path.resolve()),
                            opened.st_dev,opened.st_ino),)
                    finally:os.close(fd)
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
        keys={'profile','provider_sha256','provider_head_sha256','account_manifest_sha256','review_sha256',
            'account_journals','sessions_replayed','matched_sessions','operation_counts',
            'all_five_purposes_observed','local_provider_account_history_consistent'}
        if type(value) is dict and value.get('profile')==RESULT_PROFILE:
            keys|={'result_manifest_sha256','normalized_sessions_replayed'}
        if type(value) is not dict or set(value)!=keys or value['profile'] not in (PROFILE,RESULT_PROFILE) or value['provider_sha256']!=self.provider:
            _fail('SCOPE_INVALID')
        for key in ('provider_sha256','provider_head_sha256','account_manifest_sha256','review_sha256'):
            try:_digest(value[key])
            except ValueError:_fail('SCOPE_INVALID')
        for key in ('account_journals','sessions_replayed','matched_sessions'):
            if type(value[key]) is not int or not 1<=value[key]<=256:_fail('SCOPE_INVALID')
        if value['account_journals']>MAX_ACCOUNTS or value['sessions_replayed']!=value['matched_sessions']:_fail('SCOPE_INVALID')
        if value['profile']==RESULT_PROFILE:
            _digest(value['result_manifest_sha256'])
            if type(value['normalized_sessions_replayed']) is not int or value['normalized_sessions_replayed']!=value['matched_sessions']:_fail('SCOPE_INVALID')
        from nexus.paper_read_pacing import OPERATIONS
        counts=value['operation_counts']
        if (type(counts) is not dict or set(counts)!=set(OPERATIONS) or any(type(v) is not int or v<0 for v in counts.values()) or
                sum(counts.values())!=value['sessions_replayed']):_fail('SCOPE_INVALID')
        if value['all_five_purposes_observed'] is not True or value['local_provider_account_history_consistent'] is not True:
            _fail('SCOPE_INVALID')
        return value

    def _load(self,db):
        rows=db.execute('SELECT seq,body,previous,digest FROM records ORDER BY seq LIMIT ?',(MAX_RECORDS+2,)).fetchall()
        if not rows:_fail('BINDING_MISSING')
        if len(rows)>MAX_RECORDS+1:_fail('RECORD_CAP')
        previous=ZERO;receipts=[]
        for index,(seq,encoded,parent,digest) in enumerate(rows):
            if type(encoded) is not str or len(encoded.encode())>MAX_RECORD_BYTES:_fail('RECORD_CAP')
            try:body=json.loads(encoded)
            except (TypeError,ValueError):_fail('PAYLOAD_INVALID')
            if seq!=index or parent!=previous or canonical(body)!=encoded or digest!=sha(dict(seq=seq,body=body,previous=parent)):
                _fail('CHAIN_DISAGREEMENT')
            if index==0:
                if body!=dict(schema=SCHEMA,provider_sha256=self.provider):_fail('PROVIDER_OR_SCHEMA')
            else:
                if type(body) is not dict or set(body)!={'scope','receipt_sha256'}:_fail('PAYLOAD_INVALID')
                scope=self._scope(body['scope']);receipt=sha(scope)
                if body['receipt_sha256']!=receipt:_fail('RECEIPT_DIGEST_INVALID')
                receipts.append(dict(scope=scope,receipt_sha256=receipt,after_head_sha256=digest))
            previous=digest
        return previous,receipts

    def inspect(self):
        bound_path=Path(self.path).resolve()
        def require_binding():
            try:current=Path(self.path).resolve()
            except (OSError,RuntimeError,TypeError,ValueError):
                _fail('STORE_BINDING_CHANGED')
            if current!=bound_path:_fail('STORE_BINDING_CHANGED')
        self._check();require_binding()
        if not bound_path.exists():
            require_binding()
            if bound_path.exists():_fail('FILE_IDENTITY_CHANGED')
            return dict(mode='LOCAL_PROVIDER_READ_REVIEW_HISTORY',initialized=False,
            provider_sha256=self.provider,head_sha256=ZERO,retained_receipts=0,receipts=[],read_only=True,
            ready_for_routing=False,release_approved=False)
        identity=capture_file_identities((bound_path,),
            'PAPER_PROVIDER_READ_REVIEW_STORE_')
        fingerprint=capture_file_fingerprints((bound_path,),
            (MAX_DATABASE_BYTES,),'PAPER_PROVIDER_READ_REVIEW_STORE_')
        def require_store():
            require_binding()
            require_file_identities((bound_path,),identity,
                'PAPER_PROVIDER_READ_REVIEW_STORE_')
            require_file_fingerprints((bound_path,),(MAX_DATABASE_BYTES,),
                fingerprint,'PAPER_PROVIDER_READ_REVIEW_STORE_')
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
                serialized=db.serialize(name='main')
                require_store()
                expected=fingerprint[0]
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
                    head,receipts=self._load(db)
                    require_connection(db);require_store()
        finally:require_store()
        return dict(mode='LOCAL_PROVIDER_READ_REVIEW_HISTORY',initialized=True,provider_sha256=self.provider,
            head_sha256=head,retained_receipts=len(receipts),receipts=receipts,read_only=True,
            ready_for_routing=False,release_approved=False)

    def append(self,scope,*,expected_head,admission,precommit_check=lambda:True):
        scope=self._scope(scope);expected_head=_digest(expected_head);receipt=sha(scope)
        current=self.inspect()
        if current['head_sha256']!=expected_head:_fail('HEAD_CHANGED')
        existed=current['initialized']
        if existed:
            initial_identity=capture_file_identities((self.path,),
                'PAPER_PROVIDER_READ_REVIEW_STORE_')
            initial_fingerprint=capture_file_fingerprints((self.path,),
                (MAX_DATABASE_BYTES,),'PAPER_PROVIDER_READ_REVIEW_STORE_')
        else:
            initial_identity=initial_fingerprint=None
        def require_initial():
            if not existed:
                if self.path.exists():_fail('FILE_IDENTITY_CHANGED')
                return
            require_file_identities((self.path,),initial_identity,
                'PAPER_PROVIDER_READ_REVIEW_STORE_')
            require_file_fingerprints((self.path,),(MAX_DATABASE_BYTES,),
                initial_fingerprint,'PAPER_PROVIDER_READ_REVIEW_STORE_')
        def initial_check(callback):
            try:value=callback()
            finally:require_initial()
            return value
        if initial_check(admission)!=receipt:_fail('ADMISSION_CHANGED')
        if initial_check(precommit_check) is not True:_fail('INPUT_REVOKED')
        require_initial()
        db,new=self._connect(write=True);committed=False;transaction_identity=None
        try:
            with closing(db):
                if new==existed:_fail('FILE_IDENTITY_CHANGED')
                transaction_identity=capture_file_identities((self.path,),
                    'PAPER_PROVIDER_READ_REVIEW_STORE_')
                if existed and transaction_identity!=initial_identity:
                    _fail('FILE_IDENTITY_CHANGED')
                def transaction_check(callback):
                    transaction_fingerprint=_active_file_fingerprint(self.path)
                    try:value=callback()
                    finally:
                        require_file_identities((self.path,),transaction_identity,
                            'PAPER_PROVIDER_READ_REVIEW_STORE_')
                        if _active_file_fingerprint(self.path)!=transaction_fingerprint:
                            _fail('FILE_SET_CHANGED')
                    return value
                with _transaction(db):
                    count=db.execute('SELECT count(*) FROM records').fetchone()[0]
                    if count==0:
                        head=self._insert(db,0,dict(schema=SCHEMA,provider_sha256=self.provider),ZERO);receipts=[]
                    else:head,receipts=self._load(db)
                    if not (expected_head==ZERO and count==0) and head!=expected_head:_fail('HEAD_CHANGED')
                    duplicate=bool(receipts and receipts[-1]['receipt_sha256']==receipt)
                    if not duplicate:
                        if len(receipts)>=MAX_RECORDS:_fail('RECORD_CAP')
                        head=self._insert(db,len(receipts)+1,dict(scope=scope,receipt_sha256=receipt),head);self._load(db)
                    if transaction_check(admission)!=receipt:
                        _fail('ADMISSION_CHANGED')
                    if transaction_check(precommit_check) is not True:
                        _fail('INPUT_REVOKED')
                committed=True
                committed_identity=capture_file_identities((self.path,),
                    'PAPER_PROVIDER_READ_REVIEW_STORE_')
                committed_fingerprint=capture_file_fingerprints((self.path,),
                    (MAX_DATABASE_BYTES,),'PAPER_PROVIDER_READ_REVIEW_STORE_')
                _sync_committed_store(self.path,committed_identity,
                    committed_fingerprint,new=new)
                with guard_file_set_finalization((self.path,),
                        (MAX_DATABASE_BYTES,),committed_identity,
                        committed_fingerprint,'PAPER_PROVIDER_READ_REVIEW_STORE_'):
                    if admission()!=receipt:
                        _fail('ADMISSION_CHANGED_AFTER_COMMIT')
                    if precommit_check() is not True:
                        _fail('INPUT_REVOKED_AFTER_COMMIT')
                    return dict(mode='LOCAL_PROVIDER_READ_REVIEW_RECEIPT',
                        head_sha256=head,receipt_sha256=receipt,
                        duplicate=duplicate,read_only=True,
                        ready_for_routing=False,release_approved=False)
        except BaseException:
            if new and not committed:
                _discard_uncommitted_new(self.path,transaction_identity)
            raise
