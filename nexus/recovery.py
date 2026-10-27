"""Conservative paper-account recovery diagnostics and local SQLite snapshots.

Never fabricates transaction history, upgrades old ledgers, resets balances,
touches brokers, or alters the original account. SQLite's online backup API
preserves a consistent snapshot while normal journal locks are respected.
"""
import hashlib
import os
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path
from nexus.paper_portfolio import LOCK, _connect
from nexus.portfolio_governor import audit_portfolio

SNAPSHOT_BUSY_TIMEOUT_MS=10_000


def _fail(code):raise ValueError('PAPER_SNAPSHOT_'+code) from None


def _regular_identity(path,scope):
    """Sample one regular, single-link path without following a final symlink."""
    try:observed=path.lstat()
    except FileNotFoundError:return None
    except OSError:_fail(scope+'_FILE_UNAVAILABLE')
    if stat.S_ISLNK(observed.st_mode):_fail(scope+'_SYMLINK')
    if not stat.S_ISREG(observed.st_mode):_fail(scope+'_REGULAR_FILE_REQUIRED')
    if observed.st_nlink!=1:_fail(scope+'_HARDLINK_REVIEW_REQUIRED')
    flags=(os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|
           getattr(os,'O_NONBLOCK',0))
    try:
        with os.fdopen(os.open(path,flags),'rb',buffering=0) as stream:
            opened=os.fstat(stream.fileno())
    except OSError:_fail(scope+'_FILE_UNAVAILABLE')
    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink!=1 or
        (opened.st_dev,opened.st_ino)!=(observed.st_dev,observed.st_ino)):
        _fail(scope+'_FILE_CHANGED')
    return (opened.st_dev,opened.st_ino)


def _require_directory(path):
    try:observed=path.lstat()
    except FileNotFoundError:
        try:path.mkdir(mode=0o700)
        except FileExistsError:pass
        except OSError:_fail('DIRECTORY_UNAVAILABLE')
        try:observed=path.lstat()
        except OSError:_fail('DIRECTORY_UNAVAILABLE')
    except OSError:_fail('DIRECTORY_UNAVAILABLE')
    if stat.S_ISLNK(observed.st_mode):_fail('DIRECTORY_SYMLINK')
    if not stat.S_ISDIR(observed.st_mode):_fail('DIRECTORY_REQUIRED')


def _backup_with_deadline(source,target):
    deadline=time.monotonic()+SNAPSHOT_BUSY_TIMEOUT_MS/1000
    def progress(status,_remaining,_total):
        if status in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) and time.monotonic()>=deadline:
            _fail('DATABASE_BUSY')
    try:
        source.backup(target,pages=256,progress=progress,sleep=.05)
    except sqlite3.OperationalError as error:
        code=getattr(error,'sqlite_errorcode',None)
        if (code in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) or
            any(word in str(error).lower() for word in ('busy','locked'))):
            _fail('DATABASE_BUSY')
        raise


def _digest_regular(path,expected):
    flags=(os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|
           getattr(os,'O_NONBLOCK',0))
    digest=hashlib.sha256()
    try:
        with os.fdopen(os.open(path,flags),'rb',buffering=0) as stream:
            opened=os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink!=1 or
                (opened.st_dev,opened.st_ino)!=expected):
                _fail('DESTINATION_FILE_CHANGED')
            for chunk in iter(lambda:stream.read(1048576),b''):digest.update(chunk)
            final=os.fstat(stream.fileno())
    except ValueError:raise
    except OSError:_fail('DESTINATION_FILE_UNAVAILABLE')
    if ((final.st_dev,final.st_ino)!=expected or final.st_size!=opened.st_size):
        _fail('DESTINATION_FILE_CHANGED')
    return digest.hexdigest(),final.st_size

def recovery_report():
    audit=audit_portfolio()
    with LOCK,_connect() as db:
        meta=dict(db.execute("SELECT key,value FROM meta").fetchall())
        ledger_count=db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        event_count=db.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
        seal_count=db.execute("SELECT COUNT(*) FROM chain_seals").fetchone()[0]
    reasons=list(audit["reasons"])
    is_legacy=meta.get("position_journal_version")!="2" or meta.get("hash_chain_version")!="1"
    if not meta.get("initial"):
        status="UNINITIALIZED"
    elif is_legacy:
        status="LEGACY_REQUIRES_MANUAL_REVIEW"
    elif not audit["integrity_ok"]:
        status="INTEGRITY_FAILED"
    else:
        status="VERIFIED_LOCAL_STRUCTURE"
    return {"mode":"READ_ONLY_PAPER_RECOVERY","status":status,
      "integrity_ok":audit["integrity_ok"],"reasons":reasons,
      "ledger_rows":ledger_count,"position_event_rows":event_count,"seal_rows":seal_count,
      "migration_performed":False,"balance_changed":False,"live_orders_enabled":False,
      "recommended_action":("Export a local snapshot and review missing historical transaction evidence before any migration."
                            if status!="VERIFIED_LOCAL_STRUCTURE" else
                            "Local structural checks passed; this is not proof of actual market fills."),
      "limitations":"Historical quantity and price details missing from legacy records cannot safely be inferred from cash deltas."}

def backup_snapshot():
    destination=None
    try:
        with LOCK:
            root=Path(os.environ.get('NEXUS_PORTABLE_DATA',str(Path.home()/'.nexus-operative')))
            if root.is_symlink():_fail('ROOT_SYMLINK')
            source_path=root/'paper_portfolio.sqlite3'
            before=_regular_identity(source_path,'SOURCE')
            try:source_context=_connect()
            except ValueError as error:
                if str(error)=='PAPER_PORTFOLIO_DATABASE_BUSY':_fail('DATABASE_BUSY')
                raise
            with closing(source_context) as source:
                opened_path=Path(source.execute("PRAGMA database_list").fetchone()[2])
                if opened_path!=source_path.resolve():_fail('SOURCE_PATH_CHANGED')
                opened=_regular_identity(source_path,'SOURCE')
                if before is not None and opened!=before:_fail('SOURCE_FILE_CHANGED')
                directory=source_path.parent/"recovery_snapshots"
                _require_directory(directory)
                # A UTC timestamp plus no-follow exclusive create avoids replacing
                # previous exports and requests private POSIX permissions.
                filename="paper_snapshot_"+str(time.time_ns())+".sqlite3"
                destination=directory/filename
                flags=(os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|
                       getattr(os,'O_NOFOLLOW',0))
                descriptor=os.open(destination,flags,0o600)
                os.close(descriptor)
                with sqlite3.connect(destination,timeout=SNAPSHOT_BUSY_TIMEOUT_MS/1000) as target:
                    target.execute(f'PRAGMA busy_timeout={SNAPSHOT_BUSY_TIMEOUT_MS}')
                    _backup_with_deadline(source,target)
                if _regular_identity(source_path,'SOURCE')!=opened:_fail('SOURCE_FILE_CHANGED')
                destination_identity=_regular_identity(destination,'DESTINATION')
                digest,size=_digest_regular(destination,destination_identity)
    except Exception:
        if destination is not None:
            try:destination.unlink(missing_ok=True)
            except OSError:pass
        raise
    return {"mode":"LOCAL_SQLITE_BACKUP","filename":filename,"bytes":size,
            "sha256":digest,"stored_in":"Local NEXUS_PORTABLE_DATA/recovery_snapshots",
            "source_modified":False,"migration_performed":False,
            "orders_enabled":False,
            "note":"File remains on this computer. Backups may include sensitive simulated account history; protect access."}
