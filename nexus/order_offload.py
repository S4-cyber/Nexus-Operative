"""Create, verify, install, recover, or compare offline order-history artifacts.

Nexus must be closed before creating a copy.  This tool never deletes or
compacts the live journal.  ``rotation-plan`` is a read-only exact-binding
preflight.  Replacement preparation creates a new halted candidate beside the
verified archive.  Explicit installation retains a separate exact recovery
copy and stays halted; recovery and rollback never resume operation.
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
from nexus.durable_orders import DurableOrderGateway, ROTATION_MARKER
from nexus.paper_activity_acceptance_probe import _private_path


SCHEMA='nexus.order-physical-offload.v1'
MANIFEST='nexus-order-offload-manifest.json'
REPLACEMENT_SCHEMA_V1='nexus.order-replacement-candidate.v1'
REPLACEMENT_SCHEMA='nexus.order-replacement-candidate.v2'
REPLACEMENT_MANIFEST='nexus-order-replacement-manifest.json'
INSTALL_SCHEMA_V1='nexus.order-replacement-install.v1'
INSTALL_SCHEMA='nexus.order-replacement-install.v2'
INSTALL_MANIFEST='nexus-order-install-recovery-manifest.json'
ORDER_FILE='order_lifecycle.sqlite3'
PORTFOLIO_FILE='paper_portfolio.sqlite3'
INSTALL_ORDER_TEMP='.nexus-order-install-order.tmp'
INSTALL_PORTFOLIO_TEMP='.nexus-order-install-portfolio.tmp'
MAX_FILE_BYTES=512*1024*1024
MAX_MANIFEST_BYTES=1024*1024
AUTHORITY_FLAGS=('broker_connected','live_orders_enabled','money_movement_enabled',
                 'release_approved','capacity_reclaimed','source_history_deleted')
CHECKS=('copy_verified','source_unchanged','archived_order_replay',
        'new_destination_required','application_closed_required',
        'rotation_plan_exact_source_bound','rotation_plan_non_mutating',
        'replacement_candidate_verified','replacement_candidate_non_installing',
        'replacement_install_reclaims_capacity','replacement_rollback_restores_source',
        'incomplete_install_blocks_gateway','multi_epoch_generation_chain',
        'multi_epoch_capacity_reclaimed','multi_epoch_artifacts_replay')
MULTI_EPOCH_FIXTURE_GENERATIONS=4
MULTI_EPOCH_FIXTURE_ORDERS=12


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


def _next_lineage(source_report,archive_manifest_sha256):
    source=source_report['lineage'];body=dict(generation=source['generation']+1,
        predecessor_archive_sha256=archive_manifest_sha256,
        predecessor_epoch_sha256=source['epoch_sha256'])
    return {**body,'epoch_sha256':_digest(body)}


def _replay_scope(gateway,report):
    ids=[]
    for batch in report['archival']['batches']:
        ids.extend(batch['order_ids'])
    if len(ids)!=len(set(ids)) or len(ids)!=report['archival']['archived_order_count']:_fail('ARCHIVE_ID_SCOPE_INVALID')
    records=[gateway.order_report(order_id) for order_id in sorted(ids)]
    stable={key:report[key] for key in ('commands','halted','orders','execution_terms','quote_model_events',
                                        'portfolio_reconciled','execution_account','retention','capacity','archival')}
    return dict(recovery=stable,archived_orders=records)


def _snapshot(path,*,rotation_recovery=False):
    gateway=DurableOrderGateway(path,rotation_recovery=rotation_recovery)
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
    legacy_required={'schema','application_closed_confirmation','archive_manifest_sha256',
              'archive_recovery_sha256','candidate_recovery_sha256','account_transition_sha256',
              'files','commands','source_commands','source_archived_orders','source_archive_batches',
              'replacement_candidate_only','source_preserved','archive_preserved','capacity_reclaimed',
              'source_history_deleted','broker_connected','live_orders_enabled','money_movement_enabled',
              'release_approved','scope'}
    required=legacy_required|{'source_epoch_sha256','candidate_epoch_sha256','candidate_generation'}
    if ((document.get('schema')==REPLACEMENT_SCHEMA and set(document)!=required) or
        (document.get('schema')==REPLACEMENT_SCHEMA_V1 and set(document)!=legacy_required) or
        document.get('schema') not in (REPLACEMENT_SCHEMA,REPLACEMENT_SCHEMA_V1) or
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
    expected_lineage=_next_lineage(source_report,archive_manifest_sha)
    lineage_valid=(candidate_report['lineage']==expected_lineage and
        document.get('source_epoch_sha256')==source_report['lineage']['epoch_sha256'] and
        document.get('candidate_epoch_sha256')==expected_lineage['epoch_sha256'] and
        document.get('candidate_generation')==expected_lineage['generation'])
    if document['schema']==REPLACEMENT_SCHEMA_V1:
        lineage_valid=(candidate_report['lineage']['generation']==0)
    expected_commands=4 if document['schema']==REPLACEMENT_SCHEMA else 3
    if (archive_manifest_sha!=document['archive_manifest_sha256'] or
        source_sha!=document['archive_recovery_sha256'] or candidate_sha!=document['candidate_recovery_sha256'] or
        transition_sha!=document['account_transition_sha256'] or
        archived['commands']!=document['source_commands'] or
        archived['archived_orders']!=document['source_archived_orders'] or
        archived['archive_batches']!=document['source_archive_batches'] or
        candidate_report['commands']!=document['commands'] or candidate_report['commands']!=expected_commands or
        not lineage_valid or
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
        generation=candidate_report['lineage']['generation'],epoch_sha256=candidate_report['lineage']['epoch_sha256'],
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
        archive_manifest_sha=_manifest_entry(archive)['sha256']
        lineage=_next_lineage(source_report,archive_manifest_sha)
        candidate.bind_replacement_epoch(lineage['generation'],lineage['predecessor_archive_sha256'],
                                         lineage['predecessor_epoch_sha256'])
        for name in (ORDER_FILE,PORTFOLIO_FILE):os.chmod(stage/name,0o600)
        candidate_report,candidate_sha=_snapshot(stage/ORDER_FILE)
        candidate_account=candidate_report['execution_account']['account']
        document=dict(schema=REPLACEMENT_SCHEMA,application_closed_confirmation=True,
            archive_manifest_sha256=archive_manifest_sha,archive_recovery_sha256=source_sha,
            candidate_recovery_sha256=candidate_sha,
            source_epoch_sha256=source_report['lineage']['epoch_sha256'],
            candidate_epoch_sha256=candidate_report['lineage']['epoch_sha256'],
            candidate_generation=candidate_report['lineage']['generation'],
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


def _sync_file(path):
    # Windows' CRT rejects _commit/fsync on a read-only descriptor with EBADF.
    # Open the already validated private file for update without writing it.
    with open(path,'r+b',buffering=0) as stream:os.fsync(stream.fileno())


def _sync_directory(path):
    # Windows does not expose a portable directory handle suitable for fsync.
    # File flushes plus atomic replacement are still interruption-recoverable;
    # native power-loss acceptance remains a separate release gate.
    if os.name=='nt':return
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def _content(entry):
    return entry['bytes'],entry['sha256']


def _remove_install_temp(path):
    path=Path(path)
    try:observed=path.lstat()
    except FileNotFoundError:return
    except OSError:_fail('INSTALL_STATE_UNAVAILABLE')
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink!=1:
        _fail('INSTALL_TEMP_REVIEW_REQUIRED')
    path.unlink()


def _write_rotation_marker(root,transaction_sha256):
    root=Path(root);marker=root/ROTATION_MARKER;temporary=root/(ROTATION_MARKER+'.tmp')
    if marker.exists() or marker.is_symlink() or temporary.exists() or temporary.is_symlink():
        _fail('INSTALL_RECOVERY_REQUIRED')
    document=dict(schema=INSTALL_SCHEMA,transaction_manifest_sha256=transaction_sha256,
                  application_closed_confirmation=True,automatic_resume=False)
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
    try:
        descriptor=os.open(temporary,flags,0o600)
        with os.fdopen(descriptor,'wb') as stream:
            stream.write((json.dumps(document,sort_keys=True)+'\n').encode());stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,marker);_sync_directory(root)
    finally:
        if temporary.exists():_remove_install_temp(temporary)
    return marker


def _load_rotation_marker(root,transaction_sha256):
    marker=Path(root)/ROTATION_MARKER
    document=_load_named_manifest(root,ROTATION_MARKER)
    required={'schema','transaction_manifest_sha256','application_closed_confirmation','automatic_resume'}
    if (set(document)!=required or document['schema'] not in (INSTALL_SCHEMA,INSTALL_SCHEMA_V1) or
        document['transaction_manifest_sha256']!=transaction_sha256 or
        document['application_closed_confirmation'] is not True or document['automatic_resume'] is not False):
        _fail('INSTALL_MARKER_INVALID')
    return marker


def _clear_rotation_marker(root,transaction_sha256):
    marker=_load_rotation_marker(root,transaction_sha256)
    try:
        marker.unlink();_sync_directory(root)
    except OSError:
        # If the directory flush cannot confirm that clearing the fence is
        # durable, put the fence back before reporting failure.  A caller may
        # retry explicit recovery; ordinary gateway access must stay blocked.
        if not marker.exists():_write_rotation_marker(root,transaction_sha256)
        raise


def verify_install_recovery(archive,directory):
    """Verify the immutable source recovery copy retained for install/rollback."""
    archive,_=_private_path(archive);root,_=_private_path(directory)
    if archive.is_symlink() or not archive.is_dir() or root.is_symlink() or not root.is_dir():
        _fail('DIRECTORY_REQUIRED')
    document=_load_named_manifest(root,INSTALL_MANIFEST)
    legacy_required={'schema','application_closed_confirmation','archive_manifest_sha256',
              'source_recovery_sha256','candidate_recovery_sha256','source_files','candidate_files',
              'source_commands','source_archived_orders','source_archive_batches','candidate_commands',
              'source_recovery_copy','install_requires_closed_application','automatic_resume',
              'capacity_reclaimed','source_history_deleted','broker_connected','live_orders_enabled',
              'money_movement_enabled','release_approved','scope'}
    required=legacy_required|{'candidate_generation','candidate_epoch_sha256'}
    if ((document.get('schema')==INSTALL_SCHEMA and set(document)!=required) or
        (document.get('schema')==INSTALL_SCHEMA_V1 and set(document)!=legacy_required) or
        document.get('schema') not in (INSTALL_SCHEMA,INSTALL_SCHEMA_V1) or
        document['application_closed_confirmation'] is not True):
        _fail('INSTALL_MANIFEST_INVALID')
    actual=_files(root)
    if (actual!=document['source_files'] or [row['name'] for row in actual]!=[ORDER_FILE,PORTFOLIO_FILE] or
        set(path.name for path in root.iterdir())!={INSTALL_MANIFEST,ORDER_FILE,PORTFOLIO_FILE}):
        _fail('INSTALL_RECOVERY_CONTENT_DISAGREEMENT')
    archived=verify(archive);report,recovery_sha=_snapshot(root/ORDER_FILE)
    if (_manifest_entry(archive)['sha256']!=document['archive_manifest_sha256'] or
        recovery_sha!=document['source_recovery_sha256'] or
        report['commands']!=document['source_commands'] or
        report['archival']['archived_order_count']!=document['source_archived_orders'] or
        len(report['archival']['batches'])!=document['source_archive_batches'] or
        archived['commands']!=document['source_commands'] or
        archived['archived_orders']!=document['source_archived_orders'] or
        archived['archive_batches']!=document['source_archive_batches']):
        _fail('INSTALL_RECOVERY_REPLAY_DISAGREEMENT')
    if (document['source_recovery_copy'] is not True or
        document['install_requires_closed_application'] is not True or
        document['automatic_resume'] is not False or
        any(document[flag] is not False for flag in AUTHORITY_FLAGS)):
        _fail('INSTALL_MANIFEST_INVALID')
    if _files(root)!=actual:_fail('CONTENT_CHANGED_DURING_VERIFICATION')
    return dict(mode='OFFLINE_ORDER_INSTALL_RECOVERY',verified=True,source_preserved=True,
        source_commands=report['commands'],source_archived_orders=report['archival']['archived_order_count'],
        source_archive_batches=len(report['archival']['batches']),candidate_commands=document['candidate_commands'],
        candidate_generation=document.get('candidate_generation',0),
        candidate_epoch_sha256=document.get('candidate_epoch_sha256','0'*64),
        rollback_available=True,automatic_resume=False,**{flag:False for flag in AUTHORITY_FLAGS})


def _publish_install_recovery(source,archive,candidate,destination):
    destination,destination_resolved=_private_path(destination)
    if destination.exists() or destination.is_symlink():_fail('DESTINATION_MUST_BE_NEW')
    roots=(source.resolve().parent,archive.resolve(),candidate.resolve())
    if any(destination_resolved==root or destination_resolved.is_relative_to(root) for root in roots):
        _fail('DESTINATION_OUTSIDE_SOURCE_ARCHIVE_AND_CANDIDATE_REQUIRED')
    source_report,source_sha=_snapshot(source);candidate_report,candidate_sha=_snapshot(candidate/ORDER_FILE)
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='nexus-order-install-recovery-',dir=destination.parent))
    try:
        os.chmod(stage,0o700)
        for original,name in ((source,ORDER_FILE),(source.parent/PORTFOLIO_FILE,PORTFOLIO_FILE)):
            shutil.copy2(original,stage/name);os.chmod(stage/name,0o600);_sync_file(stage/name)
        document=dict(schema=INSTALL_SCHEMA,application_closed_confirmation=True,
            archive_manifest_sha256=_manifest_entry(archive)['sha256'],source_recovery_sha256=source_sha,
            candidate_recovery_sha256=candidate_sha,source_files=_files(stage),candidate_files=_files(candidate),
            source_commands=source_report['commands'],
            source_archived_orders=source_report['archival']['archived_order_count'],
            source_archive_batches=len(source_report['archival']['batches']),
            candidate_commands=candidate_report['commands'],source_recovery_copy=True,
            candidate_generation=candidate_report['lineage']['generation'],
            candidate_epoch_sha256=candidate_report['lineage']['epoch_sha256'],
            install_requires_closed_application=True,automatic_resume=False,capacity_reclaimed=False,
            source_history_deleted=False,broker_connected=False,live_orders_enabled=False,
            money_movement_enabled=False,release_approved=False,
            scope='Immutable source recovery copy for one explicit halted replacement install; no resume or authority.')
        (stage/INSTALL_MANIFEST).write_text(json.dumps(document,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        os.chmod(stage/INSTALL_MANIFEST,0o600);_sync_file(stage/INSTALL_MANIFEST);_sync_directory(stage)
        verify_install_recovery(archive,stage)
        if destination.exists():_fail('DESTINATION_MUST_BE_NEW')
        os.rename(stage,destination);_sync_directory(destination.parent)
        return verify_install_recovery(archive,destination)
    finally:
        if stage.exists():shutil.rmtree(stage)


def _copy_install_pair(root,source_root):
    order_temp=root/INSTALL_ORDER_TEMP;portfolio_temp=root/INSTALL_PORTFOLIO_TEMP
    for path in (order_temp,portfolio_temp):_remove_install_temp(path)
    for original,target in ((source_root/ORDER_FILE,order_temp),(source_root/PORTFOLIO_FILE,portfolio_temp)):
        _regular(original);shutil.copy2(original,target);os.chmod(target,0o600);_sync_file(target)
    return order_temp,portfolio_temp


def _replace_install_pair(root,order_temp,portfolio_temp):
    os.replace(order_temp,root/ORDER_FILE);_sync_file(root/ORDER_FILE);_sync_directory(root)
    os.replace(portfolio_temp,root/PORTFOLIO_FILE);_sync_file(root/PORTFOLIO_FILE);_sync_directory(root)


def install_replacement(source,archive,candidate,recovery,*,application_closed=False):
    """Install a verified halted candidate while retaining an exact rollback copy."""
    if application_closed is not True:_fail('APPLICATION_MUST_BE_CLOSED')
    source,_=_private_path(source);archive,_=_private_path(archive);candidate,_=_private_path(candidate)
    _regular(source);_regular(source.parent/PORTFOLIO_FILE)
    live_root=source.resolve().parent;archive_root=archive.resolve();candidate_root=candidate.resolve()
    if (candidate_root==live_root or candidate_root.is_relative_to(live_root) or
        candidate_root==archive_root or candidate_root.is_relative_to(archive_root)):
        _fail('CANDIDATE_OUTSIDE_SOURCE_AND_ARCHIVE_REQUIRED')
    rotation_plan(source,archive,application_closed=True);candidate_result=verify_replacement(archive,candidate)
    recovery_result=_publish_install_recovery(source,archive,candidate,recovery)
    rotation_plan(source,archive,application_closed=True);verify_replacement(archive,candidate)
    transaction_sha=_manifest_entry(recovery,INSTALL_MANIFEST)['sha256'];root=source.parent
    marker=_write_rotation_marker(root,transaction_sha)
    try:
        if _files(root)!=_load_named_manifest(recovery,INSTALL_MANIFEST)['source_files']:
            _fail('SOURCE_CHANGED_DURING_INSTALL')
        order_temp,portfolio_temp=_copy_install_pair(root,candidate)
        expected=_load_named_manifest(recovery,INSTALL_MANIFEST)['candidate_files']
        if [_content(file_entry(order_temp)),_content(file_entry(portfolio_temp))]!=[_content(row) for row in expected]:
            _fail('INSTALL_STAGE_DISAGREEMENT')
        _replace_install_pair(root,order_temp,portfolio_temp)
        report,recovery_sha=_snapshot(source,rotation_recovery=True)
        document=_load_named_manifest(recovery,INSTALL_MANIFEST)
        if (_files(root)!=document['candidate_files'] or recovery_sha!=document['candidate_recovery_sha256'] or
            report['commands']!=document['candidate_commands'] or report['orders'] or not report['halted'] or
            not report['portfolio_reconciled'] or
            (document['schema']==INSTALL_SCHEMA and
             (report['lineage']['generation']!=document['candidate_generation'] or
              report['lineage']['epoch_sha256']!=document['candidate_epoch_sha256']))):
            _fail('INSTALLED_REPLACEMENT_DISAGREEMENT')
        _clear_rotation_marker(root,transaction_sha)
    except Exception:
        # The marker intentionally remains. Normal gateway reads/writes fail
        # closed until explicit recover-install restores the preserved source.
        if not marker.exists():_fail('INSTALL_MARKER_LOST')
        raise
    return dict(mode='OFFLINE_ORDER_REPLACEMENT_INSTALLED',installed=True,halted=True,flat=True,
        portfolio_reconciled=True,capacity_reclaimed=True,source_history_deleted=False,
        source_preserved=True,archive_preserved=True,recovery_preserved=True,rollback_available=True,
        automatic_resume=False,broker_connected=False,live_orders_enabled=False,money_movement_enabled=False,
        release_approved=False,candidate_commands=candidate_result['commands'],
        generation=candidate_result['generation'],epoch_sha256=candidate_result['epoch_sha256'],
        source_commands=recovery_result['source_commands'])


def _restore_install_source(source,archive,recovery,*,require_marker):
    source,_=_private_path(source);archive,_=_private_path(archive);recovery,_=_private_path(recovery)
    _regular(source);_regular(source.parent/PORTFOLIO_FILE)
    verified=verify_install_recovery(archive,recovery);root=source.parent
    transaction_sha=_manifest_entry(recovery,INSTALL_MANIFEST)['sha256']
    marker_path=root/ROTATION_MARKER
    if require_marker:
        _load_rotation_marker(root,transaction_sha)
    else:
        if marker_path.exists() or marker_path.is_symlink():_fail('INSTALL_RECOVERY_REQUIRED')
        _write_rotation_marker(root,transaction_sha)
    try:
        order_temp,portfolio_temp=_copy_install_pair(root,recovery)
        _replace_install_pair(root,order_temp,portfolio_temp)
        document=_load_named_manifest(recovery,INSTALL_MANIFEST)
        report,recovery_sha=_snapshot(source,rotation_recovery=True)
        if (_files(root)!=document['source_files'] or recovery_sha!=document['source_recovery_sha256'] or
            report['commands']!=document['source_commands'] or report['orders'] or not report['halted'] or
            not report['portfolio_reconciled']):
            _fail('ROLLBACK_SOURCE_DISAGREEMENT')
        _clear_rotation_marker(root,transaction_sha)
    except Exception:
        if not marker_path.exists():_fail('INSTALL_MARKER_LOST')
        raise
    return dict(mode='OFFLINE_ORDER_REPLACEMENT_ROLLED_BACK',restored=True,halted=True,flat=True,
        portfolio_reconciled=True,capacity_reclaimed=False,source_history_deleted=False,
        source_preserved=True,archive_preserved=True,recovery_preserved=True,rollback_available=True,
        automatic_resume=False,broker_connected=False,live_orders_enabled=False,money_movement_enabled=False,
        release_approved=False,source_commands=verified['source_commands'])


def recover_install(source,archive,recovery,*,application_closed=False):
    if application_closed is not True:_fail('APPLICATION_MUST_BE_CLOSED')
    return _restore_install_source(source,archive,recovery,require_marker=True)


def rollback_install(source,archive,recovery,*,application_closed=False):
    if application_closed is not True:_fail('APPLICATION_MUST_BE_CLOSED')
    return _restore_install_source(source,archive,recovery,require_marker=False)


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


def _multi_epoch_fixture(root):
    """Exercise several bounded replacement epochs with retained artifacts.

    This is deliberately synthetic and private.  It proves that repeated
    archive/candidate/install cycles preserve their replay evidence and return
    the live journal to its small halted baseline.  It is not a duration,
    market-throughput, caller-ID policy, or native power-loss acceptance test.
    """
    root=Path(root);root.mkdir();live=root/'multi-live';live.mkdir()
    gateway=DurableOrderGateway(live/ORDER_FILE)
    gateway.initialize_account(1000000);gateway.link_portfolio()
    predecessor_epoch_sha256='0'*64;artifacts=[];max_source_commands=0;max_live_commands=0
    generation_chain=True;capacity_reclaimed=True
    for index in range(MULTI_EPOCH_FIXTURE_GENERATIONS):
        if index:gateway.reset()
        for order_index in range(MULTI_EPOCH_FIXTURE_ORDERS):
            side='BUY' if order_index%2==0 else 'SELL'
            order_id=f'epoch-{index}-order-{order_index}'
            gateway.submit(order_id,'BTC-USD',side,50,tier='terrestrial')
            gateway.set_execution_terms(order_id,1000,10)
            gateway.simulate_quote_fill(order_id,f'fill-{index}-{order_index}',1,1,25,25,2,
                                        1001+index,1001+index)
            gateway.cancel(order_id)
        gateway.emergency_halt();gateway.archive_terminal(f'fixture_epoch_{index}')
        source_report=gateway.recovery_report();max_source_commands=max(max_source_commands,source_report['commands'])
        archive=root/f'multi-archive-{index}';candidate=root/f'multi-candidate-{index}'
        recovery=root/f'multi-recovery-{index}'
        create(gateway.path,archive,application_closed=True)
        prepared=prepare_replacement(gateway.path,archive,candidate,application_closed=True)
        installed=install_replacement(gateway.path,archive,candidate,recovery,application_closed=True)
        gateway=DurableOrderGateway(gateway.path);live_report=gateway.recovery_report()
        lineage=live_report['lineage'];max_live_commands=max(max_live_commands,live_report['commands'])
        expected_generation=index+1
        generation_chain=(generation_chain and prepared['generation']==expected_generation and
            installed['generation']==expected_generation and lineage['generation']==expected_generation and
            lineage['predecessor_epoch_sha256']==predecessor_epoch_sha256)
        capacity_reclaimed=(capacity_reclaimed and installed['capacity_reclaimed'] is True and
            live_report['commands']==4 and live_report['orders']==[] and live_report['halted'] is True and
            live_report['portfolio_reconciled'] is True)
        predecessor_epoch_sha256=lineage['epoch_sha256']
        artifacts.append((archive,candidate,recovery))
    artifacts_replay=all(verify(archive)['verified'] and
        verify_replacement(archive,candidate)['verified'] and
        verify_install_recovery(archive,recovery)['rollback_available']
        for archive,candidate,recovery in artifacts)
    return dict(multi_epoch_generation_chain=generation_chain,
        multi_epoch_capacity_reclaimed=capacity_reclaimed,
        multi_epoch_artifacts_replay=artifacts_replay,
        multi_epoch_generations=MULTI_EPOCH_FIXTURE_GENERATIONS,
        multi_epoch_orders=MULTI_EPOCH_FIXTURE_GENERATIONS*MULTI_EPOCH_FIXTURE_ORDERS,
        multi_epoch_max_source_commands=max_source_commands,
        multi_epoch_max_live_commands=max_live_commands)


def fixture_report():
    from nexus.tier_permissions import configure
    with tempfile.TemporaryDirectory(prefix='nexus-order-offload-fixture-') as temporary:
        root=Path(temporary);live=root/'live';live.mkdir();output=root/'archive'/'epoch'
        replacement=root/'replacement'/'epoch';install_recovery=root/'install-recovery'/'epoch'
        configure('terrestrial',True)
        gateway=DurableOrderGateway(live/ORDER_FILE);gateway.initialize_account(10000);gateway.link_portfolio()
        for side in ('BUY','SELL'):
            oid=side.lower();gateway.submit(oid,'BTC-USD',side,50,tier='terrestrial')
            gateway.set_execution_terms(oid,1000,10)
            gateway.simulate_quote_fill(oid,'fill',1,1,25,25,2,1001,1001)
            gateway.cancel(oid)
        gateway.emergency_halt();gateway.archive_terminal('fixture_epoch')
        source_before=((live/ORDER_FILE).read_bytes(),(live/PORTFOLIO_FILE).read_bytes())
        result=create(live/ORDER_FILE,output,application_closed=True)
        checked=verify(output);copied=DurableOrderGateway(output/ORDER_FILE)
        planned=rotation_plan(live/ORDER_FILE,output,application_closed=True)
        prepared=prepare_replacement(live/ORDER_FILE,output,replacement,application_closed=True)
        replacement_checked=verify_replacement(output,replacement)
        candidate_non_installing=((live/ORDER_FILE).read_bytes(),(live/PORTFOLIO_FILE).read_bytes())==source_before
        installed=install_replacement(live/ORDER_FILE,output,replacement,install_recovery,application_closed=True)
        installed_report=DurableOrderGateway(live/ORDER_FILE).recovery_report()
        rolled_back=rollback_install(live/ORDER_FILE,output,install_recovery,application_closed=True)
        transaction_sha=_manifest_entry(install_recovery,INSTALL_MANIFEST)['sha256']
        _write_rotation_marker(live,transaction_sha)
        blocked=False
        try:DurableOrderGateway(live/ORDER_FILE).recovery_report()
        except ValueError as error:blocked=str(error)=='ORDER_ROTATION_RECOVERY_REQUIRED'
        recovered=recover_install(live/ORDER_FILE,output,install_recovery,application_closed=True)
        pressure=_multi_epoch_fixture(root/'multi-epoch')
        checks=dict(copy_verified=result['verified'] and checked['verified'],
            source_unchanged=((live/ORDER_FILE).read_bytes(),(live/PORTFOLIO_FILE).read_bytes())==source_before and result['source_unchanged'],
            archived_order_replay=copied.order_report('buy')['archived'] and copied.order_report('sell')['archived'],
            new_destination_required=result['new_destination'],application_closed_required=False,
            rotation_plan_exact_source_bound=planned['exact_source_bound'],
            rotation_plan_non_mutating=planned['dry_run_only'] and not planned['compaction_performed'] and
                (live/ORDER_FILE).read_bytes()==source_before[0],
            replacement_candidate_verified=prepared['verified'] and replacement_checked['verified'] and
                prepared['halted'] and prepared['flat'],
            replacement_candidate_non_installing=not prepared['automatic_install'] and
                not prepared['capacity_reclaimed'] and not prepared['source_history_deleted'] and
                candidate_non_installing and installed_report['commands']==4 and
                installed_report['lineage']['generation']==1,
            replacement_install_reclaims_capacity=installed['capacity_reclaimed'] and installed['installed'] and
                not installed['automatic_resume'] and installed['source_preserved'] and installed['rollback_available'],
            replacement_rollback_restores_source=rolled_back['restored'] and recovered['restored'] and
                ((live/ORDER_FILE).read_bytes(),(live/PORTFOLIO_FILE).read_bytes())==source_before,
            incomplete_install_blocks_gateway=blocked and not (live/ROTATION_MARKER).exists(),
            **{key:pressure[key] for key in CHECKS if key.startswith('multi_epoch_')})
        try:create(live/ORDER_FILE,root/'unconfirmed')
        except ValueError as error:checks['application_closed_required']=str(error)=='ORDER_OFFLOAD_APPLICATION_MUST_BE_CLOSED'
        return dict(source='TEMPORARY_OFFLINE_ORDER_OFFLOAD_FIXTURE',passed=all(checks.values()),**checks,
                    **{key:value for key,value in pressure.items() if key not in checks},
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
    install_command=sub.add_parser('install-replacement');install_command.add_argument('source',type=Path)
    install_command.add_argument('archive',type=Path);install_command.add_argument('candidate',type=Path)
    install_command.add_argument('recovery',type=Path);install_command.add_argument('--confirm-application-closed',action='store_true')
    verify_install_command=sub.add_parser('verify-install-recovery');verify_install_command.add_argument('archive',type=Path)
    verify_install_command.add_argument('directory',type=Path)
    recover_command=sub.add_parser('recover-install');recover_command.add_argument('source',type=Path)
    recover_command.add_argument('archive',type=Path);recover_command.add_argument('recovery',type=Path)
    recover_command.add_argument('--confirm-application-closed',action='store_true')
    rollback_command=sub.add_parser('rollback-install');rollback_command.add_argument('source',type=Path)
    rollback_command.add_argument('archive',type=Path);rollback_command.add_argument('recovery',type=Path)
    rollback_command.add_argument('--confirm-application-closed',action='store_true')
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
        elif args.action=='install-replacement':
            result=install_replacement(args.source,args.archive,args.candidate,args.recovery,
                application_closed=args.confirm_application_closed)
        elif args.action=='verify-install-recovery':
            result=verify_install_recovery(args.archive,args.directory)
        elif args.action=='recover-install':
            result=recover_install(args.source,args.archive,args.recovery,
                application_closed=args.confirm_application_closed)
        elif args.action=='rollback-install':
            result=rollback_install(args.source,args.archive,args.recovery,
                application_closed=args.confirm_application_closed)
        else:result=verify(args.directory)
        print(json.dumps(result,sort_keys=True))
        return 0
    except (ValueError,OSError,sqlite3.Error,TypeError,KeyError):
        print('ORDER_OFFLOAD_FAILED');return 2


if __name__=='__main__':raise SystemExit(main())
