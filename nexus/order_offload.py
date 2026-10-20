"""Create, verify, or compare offline order-history rotation artifacts.

Nexus must be closed before creating a copy.  This tool never deletes or
compacts the live journal.  ``rotation-plan`` is a read-only exact-binding
preflight; capacity is not reclaimed until a separately reviewed compaction
design exists.  Replacement preparation creates a new halted candidate beside
the verified archive but never installs it over the live journal.
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
REPLACEMENT_SCHEMA='nexus.order-replacement-candidate.v1'
REPLACEMENT_MANIFEST='nexus-order-replacement-manifest.json'
ORDER_FILE='order_lifecycle.sqlite3'
PORTFOLIO_FILE='paper_portfolio.sqlite3'
MAX_FILE_BYTES=512*1024*1024
MAX_MANIFEST_BYTES=1024*1024
AUTHORITY_FLAGS=('broker_connected','live_orders_enabled','money_movement_enabled',
                 'release_approved','capacity_reclaimed','source_history_deleted')
CHECKS=('copy_verified','source_unchanged','archived_order_replay',
        'new_destination_required','application_closed_required',
        'rotation_plan_exact_source_bound','rotation_plan_non_mutating',
        'replacement_candidate_verified','replacement_candidate_non_installing')


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
    return _load_named_manifest(root,MANIFEST)


def _load_named_manifest(root,name):
    path=Path(root)/name
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


def _manifest_entry(root,name=MANIFEST):
    entry=file_entry(Path(root)/name);entry['name']=name
    return entry


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


def rotation_plan(source,directory,*,application_closed=False):
    """Prove a verified copy still exactly binds the closed live source.

    This is intentionally a double-ended read-only preflight.  It does not
    produce a replacement database or grant permission to delete the source.
    Both the archive and live replay/file identities are checked again after
    comparison so a concurrent change is rejected rather than acknowledged.
    """
    if application_closed is not True:_fail('APPLICATION_MUST_BE_CLOSED')
    source,_=_private_path(source);root,root_resolved=_private_path(directory)
    _regular(source);portfolio=source.parent/PORTFOLIO_FILE;_regular(portfolio)
    if root_resolved==source.resolve().parent or root_resolved.is_relative_to(source.resolve().parent):
        _fail('ARCHIVE_OUTSIDE_LIVE_DATA_REQUIRED')
    if root.is_symlink() or not root.is_dir():_fail('DIRECTORY_REQUIRED')
    document=_load_manifest(root)
    archive_before=[file_entry(root/MANIFEST),*_files(root)]
    verified=verify(root)
    if [file_entry(root/MANIFEST),*_files(root)]!=archive_before:
        _fail('ARCHIVE_CHANGED_DURING_ROTATION_PLAN')
    live_before=_files(source.parent);report,recovery_sha256=_snapshot(source)
    if (live_before!=document['files'] or recovery_sha256!=document['recovery_sha256'] or
        report['commands']!=document['commands'] or
        report['archival']['archived_order_count']!=document['archived_orders'] or
        len(report['archival']['batches'])!=document['archive_batches'] or
        report['orders'] or not report['halted'] or not report['portfolio_reconciled'] or
        report['archival']['active_epoch_commands']!=0):
        _fail('ROTATION_SOURCE_DISAGREEMENT')
    verified_after=verify(root)
    archive_after=[file_entry(root/MANIFEST),*_files(root)]
    if verified_after!=verified or archive_after!=archive_before:
        _fail('ARCHIVE_CHANGED_DURING_ROTATION_PLAN')
    if _files(source.parent)!=live_before or _snapshot(source)[1]!=recovery_sha256:
        _fail('SOURCE_CHANGED_DURING_ROTATION_PLAN')
    return dict(mode='OFFLINE_ORDER_ROTATION_PLAN',verified_copy=True,
        exact_source_bound=True,application_closed_confirmation=True,
        source_unchanged=True,archive_unchanged=True,rotation_preflight_only=True,
        dry_run_only=True,commands=report['commands'],
        archived_orders=report['archival']['archived_order_count'],
        archive_batches=len(report['archival']['batches']),compaction_performed=False,
        automatic_resume=False,**{flag:False for flag in AUTHORITY_FLAGS})


def verify_replacement(archive,directory):
    """Verify a halted fresh candidate and its immutable archive binding."""
    archive,_=_private_path(archive);root,_=_private_path(directory)
    if archive.is_symlink() or not archive.is_dir() or root.is_symlink() or not root.is_dir():
        _fail('DIRECTORY_REQUIRED')
    archive_before=[_manifest_entry(archive),*_files(archive)]
    archived=verify(archive)
    document=_load_named_manifest(root,REPLACEMENT_MANIFEST)
    required={'schema','application_closed_confirmation','archive_manifest_sha256',
              'archive_recovery_sha256','candidate_recovery_sha256','account_transition_sha256',
              'files','commands','source_commands','source_archived_orders','source_archive_batches',
              'replacement_candidate_only','source_preserved','archive_preserved','capacity_reclaimed',
              'source_history_deleted','broker_connected','live_orders_enabled','money_movement_enabled',
              'release_approved','scope'}
    if (set(document)!=required or document['schema']!=REPLACEMENT_SCHEMA or
        document['application_closed_confirmation'] is not True):
        _fail('REPLACEMENT_MANIFEST_INVALID')
    actual=_files(root)
    if (actual!=document['files'] or [row['name'] for row in actual]!=[ORDER_FILE,PORTFOLIO_FILE] or
        set(path.name for path in root.iterdir())!={REPLACEMENT_MANIFEST,ORDER_FILE,PORTFOLIO_FILE}):
        _fail('REPLACEMENT_CONTENT_DISAGREEMENT')
    for name in (ORDER_FILE,PORTFOLIO_FILE):
        with closing(sqlite3.connect((root/name).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:_fail('SQLITE_INTEGRITY_FAILED')
    source_report,source_sha=_snapshot(archive/ORDER_FILE)
    candidate_report,candidate_sha=_snapshot(root/ORDER_FILE)
    source_account=source_report['execution_account']['account']
    candidate_account=candidate_report['execution_account']['account']
    if source_account is None or candidate_account is None:_fail('REPLACEMENT_REPLAY_DISAGREEMENT')
    transition_sha=_digest(dict(source_final_account=source_account,replacement_initial_account=candidate_account))
    archive_manifest_sha=_manifest_entry(archive)['sha256']
    if (archive_manifest_sha!=document['archive_manifest_sha256'] or
        source_sha!=document['archive_recovery_sha256'] or candidate_sha!=document['candidate_recovery_sha256'] or
        transition_sha!=document['account_transition_sha256'] or
        archived['commands']!=document['source_commands'] or
        archived['archived_orders']!=document['source_archived_orders'] or
        archived['archive_batches']!=document['source_archive_batches'] or
        candidate_report['commands']!=document['commands'] or candidate_report['commands']!=3 or
        candidate_report['orders'] or not candidate_report['halted'] or
        not candidate_report['portfolio_reconciled'] or
        candidate_report['archival']['archived_order_count']!=0 or
        candidate_report['archival']['batches'] or
        source_account['positions'] or
        candidate_account!={'initial':source_account['cash'],'cash':source_account['cash'],'positions':{}}):
        _fail('REPLACEMENT_REPLAY_DISAGREEMENT')
    if (document['replacement_candidate_only'] is not True or document['source_preserved'] is not True or
        document['archive_preserved'] is not True or any(document[flag] is not False for flag in AUTHORITY_FLAGS)):
        _fail('REPLACEMENT_MANIFEST_INVALID')
    if (_files(root)!=actual or [_manifest_entry(archive),*_files(archive)]!=archive_before):
        _fail('CONTENT_CHANGED_DURING_VERIFICATION')
    return dict(mode='OFFLINE_ORDER_REPLACEMENT_CANDIDATE',verified=True,
        replacement_candidate_only=True,halted=True,flat=True,portfolio_reconciled=True,
        commands=candidate_report['commands'],source_commands=source_report['commands'],
        source_archived_orders=source_report['archival']['archived_order_count'],
        source_archive_batches=len(source_report['archival']['batches']),
        source_preserved=True,archive_preserved=True,automatic_install=False,automatic_resume=False,
        **{flag:False for flag in AUTHORITY_FLAGS})


def prepare_replacement(source,archive,destination,*,application_closed=False):
    """Publish a fresh halted candidate without touching the live source."""
    if application_closed is not True:_fail('APPLICATION_MUST_BE_CLOSED')
    source,_=_private_path(source);archive,archive_resolved=_private_path(archive)
    destination,destination_resolved=_private_path(destination)
    _regular(source)
    if destination.exists() or destination.is_symlink():_fail('DESTINATION_MUST_BE_NEW')
    live_root=source.resolve().parent
    if (destination_resolved==live_root or destination_resolved.is_relative_to(live_root) or
        destination_resolved==archive_resolved or destination_resolved.is_relative_to(archive_resolved)):
        _fail('DESTINATION_OUTSIDE_SOURCE_AND_ARCHIVE_REQUIRED')
    rotation_plan(source,archive,application_closed=True)
    source_report,source_sha=_snapshot(source)
    source_account=source_report['execution_account']['account']
    if source_account is None or source_account['positions']:
        _fail('HALTED_FLAT_LOGICAL_ARCHIVE_REQUIRED')
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='nexus-order-replacement-',dir=destination.parent))
    try:
        os.chmod(stage,0o700)
        candidate=DurableOrderGateway(stage/ORDER_FILE)
        try:candidate.initialize_account(source_account['cash'])
        except ValueError:_fail('REPLACEMENT_BALANCE_REVIEW_REQUIRED')
        candidate.link_portfolio();candidate.emergency_halt()
        for name in (ORDER_FILE,PORTFOLIO_FILE):os.chmod(stage/name,0o600)
        candidate_report,candidate_sha=_snapshot(stage/ORDER_FILE)
        candidate_account=candidate_report['execution_account']['account']
        document=dict(schema=REPLACEMENT_SCHEMA,application_closed_confirmation=True,
            archive_manifest_sha256=_manifest_entry(archive)['sha256'],archive_recovery_sha256=source_sha,
            candidate_recovery_sha256=candidate_sha,
            account_transition_sha256=_digest(dict(source_final_account=source_account,replacement_initial_account=candidate_account)),
            files=_files(stage),commands=candidate_report['commands'],source_commands=source_report['commands'],
            source_archived_orders=source_report['archival']['archived_order_count'],
            source_archive_batches=len(source_report['archival']['batches']),replacement_candidate_only=True,
            source_preserved=True,archive_preserved=True,capacity_reclaimed=False,source_history_deleted=False,
            broker_connected=False,live_orders_enabled=False,money_movement_enabled=False,release_approved=False,
            scope='Fresh halted replacement candidate only; live source and verified archive retained; no installation, resume or authority.')
        (stage/REPLACEMENT_MANIFEST).write_text(json.dumps(document,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        os.chmod(stage/REPLACEMENT_MANIFEST,0o600)
        verify_replacement(archive,stage)
        rotation_plan(source,archive,application_closed=True)
        if destination.exists():_fail('DESTINATION_MUST_BE_NEW')
        os.rename(stage,destination)
        result=verify_replacement(archive,destination)
        rotation_plan(source,archive,application_closed=True)
        return {**result,'new_destination':True}
    finally:
        if stage.exists():shutil.rmtree(stage)


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
        replacement=root/'replacement'/'epoch'
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
        planned=rotation_plan(live/ORDER_FILE,output,application_closed=True)
        prepared=prepare_replacement(live/ORDER_FILE,output,replacement,application_closed=True)
        replacement_checked=verify_replacement(output,replacement)
        checks=dict(copy_verified=result['verified'] and checked['verified'],
            source_unchanged=(live/ORDER_FILE).read_bytes()==source_before and result['source_unchanged'],
            archived_order_replay=copied.order_report('buy')['archived'] and copied.order_report('sell')['archived'],
            new_destination_required=result['new_destination'],application_closed_required=False,
            rotation_plan_exact_source_bound=planned['exact_source_bound'],
            rotation_plan_non_mutating=planned['dry_run_only'] and not planned['compaction_performed'] and
                (live/ORDER_FILE).read_bytes()==source_before,
            replacement_candidate_verified=prepared['verified'] and replacement_checked['verified'] and
                prepared['halted'] and prepared['flat'],
            replacement_candidate_non_installing=not prepared['automatic_install'] and
                not prepared['capacity_reclaimed'] and not prepared['source_history_deleted'] and
                (live/ORDER_FILE).read_bytes()==source_before)
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
    plan_command=sub.add_parser('rotation-plan');plan_command.add_argument('source',type=Path)
    plan_command.add_argument('directory',type=Path);plan_command.add_argument('--confirm-application-closed',action='store_true')
    prepare_command=sub.add_parser('prepare-replacement');prepare_command.add_argument('source',type=Path)
    prepare_command.add_argument('archive',type=Path);prepare_command.add_argument('destination',type=Path)
    prepare_command.add_argument('--confirm-application-closed',action='store_true')
    replacement_command=sub.add_parser('verify-replacement');replacement_command.add_argument('archive',type=Path)
    replacement_command.add_argument('directory',type=Path)
    args=parser.parse_args()
    try:
        if args.action=='create':
            result=create(args.source,args.destination,application_closed=args.confirm_application_closed)
        elif args.action=='rotation-plan':
            result=rotation_plan(args.source,args.directory,application_closed=args.confirm_application_closed)
        elif args.action=='prepare-replacement':
            result=prepare_replacement(args.source,args.archive,args.destination,
                application_closed=args.confirm_application_closed)
        elif args.action=='verify-replacement':
            result=verify_replacement(args.archive,args.directory)
        else:result=verify(args.directory)
        print(json.dumps(result,sort_keys=True))
        return 0
    except (ValueError,OSError,sqlite3.Error,TypeError,KeyError):
        print('ORDER_OFFLOAD_FAILED');return 2


if __name__=='__main__':raise SystemExit(main())
