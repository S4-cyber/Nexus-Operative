"""Create or verify an offline, replay-verified physical order-history copy.

Nexus must be closed before creating a copy.  This tool never deletes or
compacts the live journal; capacity is not reclaimed until a separately
reviewed compaction design exists.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile

from nexus.artifact_manifest import file_entry
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_activity_acceptance_probe import _private_path


SCHEMA='nexus.order-physical-offload.v1'
MANIFEST='nexus-order-offload-manifest.json'
ORDER_FILE='order_lifecycle.sqlite3'
PORTFOLIO_FILE='paper_portfolio.sqlite3'
MAX_FILE_BYTES=512*1024*1024
MAX_MANIFEST_BYTES=1024*1024
AUTHORITY_FLAGS=('broker_connected','live_orders_enabled','money_movement_enabled',
                 'release_approved','capacity_reclaimed','source_history_deleted')
CHECKS=('copy_verified','source_unchanged','archived_order_replay',
        'new_destination_required','application_closed_required')


def _fail(code):
    raise ValueError('ORDER_OFFLOAD_'+code) from None


def _regular(path):
    path=Path(path)
    for suffix in ('-journal','-wal','-shm'):
        try:path.with_name(path.name+suffix).lstat()
        except FileNotFoundError:continue
        except OSError:_fail('SOURCE_UNAVAILABLE')
        _fail('SQLITE_SIDECAR_REVIEW_REQUIRED')
    try:observed=path.lstat()
    except FileNotFoundError:_fail('SOURCE_REQUIRED')
    except OSError:_fail('SOURCE_UNAVAILABLE')
    if stat.S_ISLNK(observed.st_mode):_fail('SYMLINK_NOT_ALLOWED')
    if not stat.S_ISREG(observed.st_mode):_fail('REGULAR_FILE_REQUIRED')
    if observed.st_nlink!=1:_fail('HARDLINK_NOT_ALLOWED')
    if not 1<=observed.st_size<=MAX_FILE_BYTES:_fail('FILE_SIZE_REVIEW_REQUIRED')
    return observed


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _replay_scope(gateway,report):
    ids=[]
    for batch in report['archival']['batches']:
        ids.extend(batch['order_ids'])
    if len(ids)!=len(set(ids)) or len(ids)!=report['archival']['archived_order_count']:_fail('ARCHIVE_ID_SCOPE_INVALID')
    records=[gateway.order_report(order_id) for order_id in sorted(ids)]
    stable={key:report[key] for key in ('commands','halted','orders','execution_terms','quote_model_events',
                                        'portfolio_reconciled','execution_account','retention','capacity','archival')}
    return dict(recovery=stable,archived_orders=records)


def _snapshot(path):
    gateway=DurableOrderGateway(path)
    report=gateway.recovery_report()
    return report,_digest(_replay_scope(gateway,report))


def _files(root):
    root=Path(root);result=[]
    for name in (ORDER_FILE,PORTFOLIO_FILE):
        path=root/name
        if path.exists():
            _regular(path);entry=file_entry(path);entry['name']=name;result.append(entry)
    return result


def _load_manifest(root):
    path=Path(root)/MANIFEST
    flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)
    try:
        with os.fdopen(os.open(path,flags),'rb') as stream:
            before=os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or
                not 1<=before.st_size<=MAX_MANIFEST_BYTES):_fail('MANIFEST_INVALID')
            raw=stream.read(MAX_MANIFEST_BYTES+1);after=os.fstat(stream.fileno())
            if len(raw)!=before.st_size or any(getattr(before,key)!=getattr(after,key)
                for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')):_fail('MANIFEST_INVALID')
    except (OSError,ValueError):_fail('MANIFEST_INVALID')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:_fail('MANIFEST_INVALID')
            result[key]=value
        return result
    try:return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,
                          parse_constant=lambda _:_fail('MANIFEST_INVALID'))
    except (OSError,UnicodeError,json.JSONDecodeError,TypeError,RecursionError):_fail('MANIFEST_INVALID')


def verify(directory):
    root,_=_private_path(directory)
    if root.is_symlink() or not root.is_dir():_fail('DIRECTORY_REQUIRED')
    document=_load_manifest(root)
    required={'schema','application_closed_confirmation','files','recovery_sha256','commands',
              'archived_orders','archive_batches','copy_only','capacity_reclaimed',
              'source_history_deleted','broker_connected','live_orders_enabled',
              'money_movement_enabled','release_approved','scope'}
    if set(document)!=required or document['schema']!=SCHEMA or document['application_closed_confirmation'] is not True:
        _fail('MANIFEST_INVALID')
    actual=_files(root)
    if actual!=document['files'] or [row['name'] for row in actual] != [ORDER_FILE,PORTFOLIO_FILE]:
        _fail('CONTENT_DISAGREEMENT')
    if set(path.name for path in root.iterdir())!={MANIFEST,*(row['name'] for row in actual)}:
        _fail('CONTENT_DISAGREEMENT')
    for name in (ORDER_FILE,PORTFOLIO_FILE):
        # Connection context managers commit/roll back but do not close.  An
        # explicit close is required before Windows can rename or remove the
        # staged directory after verification.
        with closing(sqlite3.connect((root/name).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:_fail('SQLITE_INTEGRITY_FAILED')
    report,recovery_sha256=_snapshot(root/ORDER_FILE)
    expected=(document['commands'],document['archived_orders'],document['archive_batches'])
    observed=(report['commands'],report['archival']['archived_order_count'],len(report['archival']['batches']))
    if (recovery_sha256!=document['recovery_sha256'] or expected!=observed or report['orders'] or
        not report['halted'] or not report['portfolio_reconciled'] or report['archival']['active_epoch_commands']!=0):
        _fail('REPLAY_DISAGREEMENT')
    if document['copy_only'] is not True or any(document[flag] is not False for flag in AUTHORITY_FLAGS):
        _fail('MANIFEST_INVALID')
    if _files(root)!=actual:_fail('CONTENT_CHANGED_DURING_VERIFICATION')
    return dict(mode='OFFLINE_ORDER_PHYSICAL_COPY',verified=True,commands=report['commands'],
                archived_orders=report['archival']['archived_order_count'],archive_batches=len(report['archival']['batches']),
                copy_only=True,automatic_resume=False,**{flag:False for flag in AUTHORITY_FLAGS})


def create(source,destination,*,application_closed=False):
    if application_closed is not True:_fail('APPLICATION_MUST_BE_CLOSED')
    source,_=_private_path(source);destination,destination_resolved=_private_path(destination)
    _regular(source)
    source_resolved=source.resolve();live_root=source_resolved.parent
    if destination.exists() or destination.is_symlink():_fail('DESTINATION_MUST_BE_NEW')
    if destination_resolved==live_root or destination_resolved.is_relative_to(live_root):
        _fail('DESTINATION_OUTSIDE_LIVE_DATA_REQUIRED')
    report,before_sha=_snapshot(source)
    if (not report['journal_present'] or not report['halted'] or report['orders'] or
        not report['portfolio_reconciled'] or report['archival']['archived_order_count']<1 or
        not report['archival']['batches'] or report['archival']['active_epoch_commands']!=0):
        _fail('HALTED_FLAT_LOGICAL_ARCHIVE_REQUIRED')
    portfolio=source.parent/PORTFOLIO_FILE
    _regular(portfolio)
    before=[file_entry(source),file_entry(portfolio)]
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='nexus-order-offload-',dir=destination.parent))
    try:
        os.chmod(stage,0o700)
        for original,name in ((source,ORDER_FILE),(portfolio,PORTFOLIO_FILE)):
            shutil.copy2(original,stage/name);os.chmod(stage/name,0o600)
        if before!=[file_entry(source),file_entry(portfolio)] or _snapshot(source)[1]!=before_sha:
            _fail('SOURCE_CHANGED_DURING_COPY')
        copied_report,copied_sha=_snapshot(stage/ORDER_FILE)
        if copied_sha!=before_sha:_fail('COPY_REPLAY_DISAGREEMENT')
        files=_files(stage)
        document=dict(schema=SCHEMA,application_closed_confirmation=True,files=files,
            recovery_sha256=copied_sha,commands=copied_report['commands'],
            archived_orders=copied_report['archival']['archived_order_count'],
            archive_batches=len(copied_report['archival']['batches']),copy_only=True,
            capacity_reclaimed=False,source_history_deleted=False,broker_connected=False,
            live_orders_enabled=False,money_movement_enabled=False,release_approved=False,
            scope='Verified offline physical copy only; source retained, live capacity unchanged, no automatic resume or authority.')
        (stage/MANIFEST).write_text(json.dumps(document,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        os.chmod(stage/MANIFEST,0o600)
        verify(stage)
        if destination.exists():_fail('DESTINATION_MUST_BE_NEW')
        os.rename(stage,destination)
        result=verify(destination)
        if before!=[file_entry(source),file_entry(portfolio)] or _snapshot(source)[1]!=before_sha:
            _fail('SOURCE_CHANGED_DURING_PUBLICATION')
        return {**result,'source_unchanged':True,'new_destination':True}
    finally:
        if stage.exists():shutil.rmtree(stage)


def fixture_report():
    from nexus.tier_permissions import configure
    with tempfile.TemporaryDirectory(prefix='nexus-order-offload-fixture-') as temporary:
        root=Path(temporary);live=root/'live';live.mkdir();output=root/'archive'/'epoch'
        configure('terrestrial',True)
        gateway=DurableOrderGateway(live/ORDER_FILE);gateway.initialize_account(10000);gateway.link_portfolio()
        for side in ('BUY','SELL'):
            oid=side.lower();gateway.submit(oid,'BTC-USD',side,50,tier='terrestrial')
            gateway.set_execution_terms(oid,1000,10)
            gateway.simulate_quote_fill(oid,'fill',1,1,25,25,2,1001,1001)
            gateway.cancel(oid)
        gateway.emergency_halt();gateway.archive_terminal('fixture_epoch')
        source_before=(live/ORDER_FILE).read_bytes();result=create(live/ORDER_FILE,output,application_closed=True)
        checked=verify(output);copied=DurableOrderGateway(output/ORDER_FILE)
        checks=dict(copy_verified=result['verified'] and checked['verified'],
            source_unchanged=(live/ORDER_FILE).read_bytes()==source_before and result['source_unchanged'],
            archived_order_replay=copied.order_report('buy')['archived'] and copied.order_report('sell')['archived'],
            new_destination_required=result['new_destination'],application_closed_required=False)
        try:create(live/ORDER_FILE,root/'unconfirmed')
        except ValueError as error:checks['application_closed_required']=str(error)=='ORDER_OFFLOAD_APPLICATION_MUST_BE_CLOSED'
        return dict(source='TEMPORARY_OFFLINE_ORDER_OFFLOAD_FIXTURE',passed=all(checks.values()),**checks,
                    **{flag:False for flag in AUTHORITY_FLAGS})


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'ORDER_OFFLOAD_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    create_command=sub.add_parser('create');create_command.add_argument('source',type=Path)
    create_command.add_argument('destination',type=Path);create_command.add_argument('--confirm-application-closed',action='store_true')
    verify_command=sub.add_parser('verify');verify_command.add_argument('directory',type=Path)
    args=parser.parse_args()
    try:
        result=(create(args.source,args.destination,application_closed=args.confirm_application_closed)
                if args.action=='create' else verify(args.directory))
        print(json.dumps(result,sort_keys=True))
        return 0
    except (ValueError,OSError,sqlite3.Error,TypeError,KeyError):
        print('ORDER_OFFLOAD_FAILED');return 2


if __name__=='__main__':raise SystemExit(main())
