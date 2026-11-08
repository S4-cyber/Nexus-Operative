"""Disposable process-death evidence for private activity acceptance history.

This fixture uses only temporary local journals, synthetic digests and injected
activity.  It never reads credentials, contacts a provider or grants authority.
"""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile

from nexus.paper_read_projection import digest as projection_digest
from nexus.alpaca_paper_contract import sha
from nexus import alpaca_activity_normalizer as adapter
from nexus.activity_collection_evidence import SCHEMA, page_observation, validate
from nexus.alpaca_activity_collection import PAGE_SIZE, activity_url
from nexus.alpaca_paper_inspection import URLS
from nexus.broker_activity_store import BrokerActivityStore
from nexus import paper_activity_acceptance_store as acceptance_storage
from nexus.paper_activity_acceptance_probe import status
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore, PROFILE
from nexus.paper_read_pacing import PaperReadPacing
from nexus import sqlite_journal_guard as journal_guard

ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
AFTER = '2026-10-17T00:00:00Z'
UNTIL = '2026-10-18T00:00:00Z'
SQLITE_NAMES = tuple(
    name + suffix
    for name in ('pacing.sqlite3', 'activity.sqlite3', 'acceptance.sqlite3')
    for suffix in ('', '-journal', '-wal', '-shm')
)
MODE = 'DISPOSABLE_CROSS_JOURNAL_ACTIVITY_ACCEPTANCE_RECOVERY'
SOURCE = 'SYNTHETIC_LOCAL_PROCESS_DEATH_FIXTURE'
LIMITATIONS = ('Temporary synthetic local journals and forced process exits only. '
    'Full-file-set fingerprints cover the three main files and their rollback/WAL/shared-memory '
    'sidecars; replaying both accepted content records and exact journal transitions demonstrates '
    'only SQLite commit-boundary and local-history consistency for cooperating files, not provider '
    'causality, source or activity coverage, fee links, account reconciliation, broker-wide '
    'ownership, funds, routing or release acceptance.')
FIXTURE_CHECKS = (
    'passed', 'missing_status_created_nothing',
    'precommit_death_retained_unbound_transition',
    'postcommit_death_replayed_bound_transition',
    'precommit_rollback_journal_observed', 'fresh_process_journals_unchanged',
    'fresh_process_vault_silent', 'postcommit_latest_content_replayed',
    'postcommit_all_content_replayed', 'postcommit_all_pacing_operations_typed',
    'postcommit_all_pacing_result_receipts_bound',
    'postcommit_all_pacing_request_targets_bound',
    'postcommit_all_normalized_results_bound', 'acceptance_store_binding_pinned',
    'acceptance_object_scope_pinned', 'acceptance_cleanup_path_pinned',
    'acceptance_connection_binding_pinned', 'acceptance_connection_content_pinned',
    'acceptance_read_close_requires_explicit_retry',
    'acceptance_connection_content_continuity_pinned',
    'acceptance_write_binding_pinned', 'acceptance_write_ack_replayed',
    'acceptance_write_connection_pinned',
    'acceptance_write_connection_content_pinned',
    'acceptance_write_connection_content_continuity_pinned',
    'acceptance_write_file_identity_pinned',
    'acceptance_write_committed_content_pinned',
    'acceptance_commit_durability_replayed',
    'acceptance_commit_sync_preserves_process_control',
    'acceptance_read_finalization_preserves_process_control',
    'acceptance_write_finalization_preserves_process_control',
    'acceptance_write_close_requires_explicit_recovery',
    'acceptance_initialization_identity_continuity_pinned',
    'acceptance_uncommitted_initialization_replayed',
    'acceptance_rebind_boundary_pinned', 'legacy_normalized_result_not_invented',
    'legacy_normalized_history_preserved', 'changed_creation_window_targets_refused',
    'changed_cursor_target_refused', 'request_target_disagreement_history_preserved')
FIXTURE_AUTHORITY_FLAGS = (
    'actual_provider_access', 'actual_vault_access', 'source_attested',
    'activity_coverage_verified', 'fee_execution_links_verified',
    'broker_account_reconciled', 'spendable_funds_verified', 'ready_for_routing',
    'broker_connected', 'live_orders_enabled', 'money_movement_enabled',
    'release_approved')
PUBLIC_FIXTURE_KEYS = frozenset((
    'mode', 'source', 'accepted_transitions_after_commit', 'limitations',
    *FIXTURE_CHECKS, *FIXTURE_AUTHORITY_FLAGS))


def _sqlite_files_snapshot(root):
    """Fingerprint every cooperating SQLite main file and possible sidecar."""
    snapshot=[]
    for name in SQLITE_NAMES:
        path=root/name
        if not path.exists():
            continue
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(64*1024),b''):
                digest.update(chunk)
        snapshot.append((name,path.stat().st_size,digest.hexdigest()))
    return tuple(snapshot)


def _scope(root,index=1,*,full_page=False,request_targets=None,bind_normalized=True):
    after,until=((AFTER,UNTIL) if index==1 else
        ('2026-10-18T00:00:00Z','2026-10-19T00:00:00Z'))
    at=1000+(index-1)*60
    pacing=PaperReadPacing(root/'pacing.sqlite3',ACCOUNT)
    activity=BrokerActivityStore(root/'activity.sqlite3',ACCOUNT)
    acceptance=PaperActivityAcceptanceStore(root/'acceptance.sqlite3',ACCOUNT)
    before_pacing=pacing.inspect();before_activity=activity.inspect()
    acceptance.bind(pacing_head=before_pacing['head_sha256'],retention_head=before_activity['head_sha256'],
        pacing_sessions=before_pacing['sessions'],retained_batches=before_activity['retained_batches'])
    rows=[dict(id=f'2026101{6+index}000000000::11234567-89ab-cdef-0123-{index*1000+i:012d}',
        activity_type='FEE',status='executed',net_amount='-0.01',date=f'2026-10-{15+index:02d}')
        for i in range(1,(PAGE_SIZE if full_page else 1)+1)]
    normalized=adapter.normalize(rows,[],expected_account_id=ACCOUNT)
    observed_at=1792281600+(index-1)*86400
    observation=validate(dict(schema=SCHEMA,declared_transport='INJECTED_UNATTESTED',
        read_started_at=observed_at,read_completed_at=observed_at,
        account_fields_sha256=sha(dict(fixture='process-death',kind='account',index=index)),
        pages=[page_observation(rows,normalized['normalized_activities'],after=after,until=until,
            request_cursor=None,requested_at=observed_at,received_at=observed_at)]),
        dict(activities=normalized['normalized_activities']),after=after,until=until)
    receipt=activity.retain(rows,[],after=after,until=until,collection_observation=observation)
    with pacing.ownership() as owner:
        token=pacing.begin(admission=lambda:at,ownership=owner,operation='ACTIVITY_COLLECTION')
        kinds=['ACCOUNT','ACTIVITY_PAGE','ACCOUNT']
        pacing.finish(token,admission=lambda:at,status_code=200,ownership=owner,
            receipt=dict(operation='ACTIVITY_COLLECTION',request_kinds=kinds,
                request_sha256=([sha(dict(method='GET',target=url)) for url in (URLS[0],activity_url(after,until),URLS[0])]
                    if request_targets is None else list(request_targets)),
                response_sha256=[sha(dict(kind=kind,index=index,at=at)) for index,kind in enumerate(kinds)],
                **({'result_projection_sha256':projection_digest('ACTIVITY_COLLECTION',receipt['receipt_id'])} if bind_normalized else {})))
    after_pacing=pacing.inspect();after_activity=activity.inspect()
    scope=dict(profile=PROFILE,account_sha256=after_activity['account_sha256'],
        collection_scope_sha256=sha(dict(account_sha256=after_activity['account_sha256'],after=after,
            until=until,direction='asc',page_size=PAGE_SIZE)),
        account_fields_sha256=observation['account_fields_sha256'],
        collection_observation_sha256=sha(observation),activity_input_sha256=normalized['input_sha256'],
        retained_receipt_sha256=receipt['receipt_id'],
        pacing_before_head_sha256=before_pacing['head_sha256'],pacing_after_head_sha256=after_pacing['head_sha256'],
        retention_before_head_sha256=before_activity['head_sha256'],retention_after_head_sha256=after_activity['head_sha256'])
    return pacing,activity,acceptance,scope


def _die_at_commit(root,scope,phase):
    script='''
import json,os,sys
from nexus.broker_activity_store import BrokerActivityStore
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore
from nexus.paper_read_pacing import PaperReadPacing
root,account,phase,encoded=sys.argv[1:]
root=__import__('pathlib').Path(root);scope=json.loads(encoded)
target=PaperActivityAcceptanceStore(root/'acceptance.sqlite3',account)
original=target._connect
class Boundary:
 def __init__(self,db): self.db=db
 def __getattr__(self,name): return getattr(self.db,name)
 def __enter__(self): self.db.__enter__(); return self
 def __exit__(self,*args):
  if phase=='before': os._exit(71)
  self.db.__exit__(*args)
  os._exit(72)
def connect(write=False):
 db,new=original(write)
 return (Boundary(db) if write else db),new
target._connect=connect
target.append(scope,admission=lambda:(PaperReadPacing(root/'pacing.sqlite3',account).inspect()['head_sha256'],BrokerActivityStore(root/'activity.sqlite3',account).inspect()['head_sha256']))
'''
    child=subprocess.run([sys.executable,'-c',script,str(root),ACCOUNT,phase,json.dumps(scope,sort_keys=True)],
        capture_output=True,text=True,timeout=20)
    if child.returncode!=(71 if phase=='before' else 72):
        raise ValueError('PAPER_ACTIVITY_ACCEPTANCE_EVIDENCE_PROCESS_BOUNDARY')


def _fresh_status(root):
    script='''
import json,sys
from pathlib import Path
attempts={'external_connections':0,'credential_operations':0}
def audit(event,args):
 if event=='socket.connect': attempts['external_connections']+=1;raise RuntimeError('NETWORK_DENIED')
sys.addaudithook(audit)
from nexus import paper_credentials
from nexus.paper_activity_acceptance_evidence import _sqlite_files_snapshot
def denied(): attempts['credential_operations']+=1;raise RuntimeError('VAULT_DENIED')
paper_credentials._vault=denied
from nexus.paper_activity_acceptance_probe import status
root=Path(sys.argv[1]);paths=[root/name for name in ('pacing.sqlite3','activity.sqlite3','acceptance.sqlite3')]
before=_sqlite_files_snapshot(root)
result=status(expected_account_id=sys.argv[2],pacing_path=paths[0],activity_store_path=paths[1],acceptance_store_path=paths[2])
after=_sqlite_files_snapshot(root)
print(json.dumps({'recovery':result['cross_journal_recovery_state'],'heads_match':result['cross_journal_heads_match'],
 'latest_content_replayed':result['latest_acceptance_record_replayed'],
 'all_content_replayed':result['all_accepted_transitions_replayed'],
 'all_operations_typed':result['all_pacing_operations_typed'],
 'all_receipts_bound':result['all_pacing_result_receipts_bound'],
 'all_request_targets_bound':result['all_pacing_request_targets_bound'],
 'all_normalized_results_bound':result['all_pacing_normalized_results_bound'],
 'replayed_records':result['replayed_accepted_transitions'],'records':result['accepted_transitions'],
 'unchanged':before==after,'rollback_journal_observed':any(name.endswith('-journal') for name,_,_ in before),**attempts}))
'''
    child=subprocess.run([sys.executable,'-c',script,str(root),ACCOUNT],capture_output=True,text=True,timeout=20)
    if child.returncode!=0:raise ValueError('PAPER_ACTIVITY_ACCEPTANCE_EVIDENCE_RESTART')
    return json.loads(child.stdout)


def fixture_report():
    with tempfile.TemporaryDirectory(prefix='nexus-activity-acceptance-evidence-') as directory:
        base=Path(directory)
        missing=base/'missing';missing.mkdir()
        empty=status(expected_account_id=ACCOUNT,pacing_path=missing/'pacing.sqlite3',
            activity_store_path=missing/'activity.sqlite3',acceptance_store_path=missing/'acceptance.sqlite3')
        missing_created_nothing=empty['cross_journal_recovery_state']=='NO_LOCAL_READ_HISTORY' and not list(missing.iterdir())

        before_root=base/'before';before_root.mkdir();_,_,before_acceptance,before_scope=_scope(before_root)
        _die_at_commit(before_root,before_scope,'before')
        before=_fresh_status(before_root)
        before_unbound=(before_acceptance.inspect()['records']==0 and
            before['recovery']=='UNBOUND_CROSS_JOURNAL_TRANSITION_REVIEW_REQUIRED' and not before['heads_match'])

        after_root=base/'after';after_root.mkdir();_,_,after_acceptance,after_scope=_scope(after_root)
        _die_at_commit(after_root,after_scope,'after')
        second_pacing,second_activity,_,second_scope=_scope(after_root,2)
        after_acceptance.append(second_scope,admission=lambda:(second_pacing.inspect()['head_sha256'],
            second_activity.inspect()['head_sha256']))
        after=_fresh_status(after_root)
        after_bound=(after_acceptance.inspect()['records']==2 and
            after['recovery']=='BOUND_TRANSITIONS_REPLAYED_REVIEW_REQUIRED' and after['heads_match'] and
            after['latest_content_replayed'] and after['all_content_replayed'] and
            after['all_operations_typed'] and after['all_receipts_bound'] and after['all_request_targets_bound'] and after['all_normalized_results_bound'] and
            after['replayed_records']==after['records']==2)
        foreign_root=base/'inspection-foreign';foreign_root.mkdir()
        fp,fa,foreign_acceptance,foreign_scope=_scope(foreign_root)
        foreign_acceptance.append(foreign_scope,admission=lambda:(
            fp.inspect()['head_sha256'],fa.inspect()['head_sha256']))
        acceptance_connect=after_acceptance._connect
        acceptance_path=after_acceptance.path
        inspection_bytes={path:path.read_bytes() for path in
            (acceptance_path,foreign_acceptance.path)}
        after_acceptance._connect=foreign_acceptance._connect
        acceptance_connection_denied=False
        try:after_acceptance.inspect()
        except ValueError as error:
            acceptance_connection_denied=(str(error)==
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_CONNECTION_BINDING_CHANGED')
        finally:after_acceptance._connect=acceptance_connect
        class SpoofedAcceptanceConnection(sqlite3.Connection):
            def execute(self,statement,*args,**kwargs):
                if statement=='PRAGMA database_list':
                    class StaticRows:
                        def fetchall(self):return [
                            (0,'main',str(acceptance_path.resolve()))]
                    return StaticRows()
                return super().execute(statement,*args,**kwargs)
        def substitute_acceptance_content(*args,**kwargs):
            db=sqlite3.connect(
                foreign_acceptance.path.resolve().as_uri()+'?mode=ro',
                uri=True,timeout=10,factory=SpoofedAcceptanceConnection)
            return db,False
        acceptance_content_denied=False
        after_acceptance._connect=substitute_acceptance_content
        try:after_acceptance.replay_scopes()
        except ValueError as error:
            acceptance_content_denied=(str(error)==
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_CONNECTION_CONTENT_CHANGED')
        finally:after_acceptance._connect=acceptance_connect
        def retarget_acceptance(*args,**kwargs):
            after_acceptance.path=foreign_acceptance.path
            return acceptance_connect(*args,**kwargs)
        acceptance_binding_denied=False
        after_acceptance._connect=retarget_acceptance
        try:after_acceptance.inspect()
        except ValueError as error:
            acceptance_binding_denied=(str(error)==
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_STORE_BINDING_CHANGED')
        finally:
            after_acceptance.path=acceptance_path
            after_acceptance._connect=acceptance_connect
        acceptance_inspection_bound=(acceptance_connection_denied and
            acceptance_content_denied and acceptance_binding_denied and
            inspection_bytes=={path:path.read_bytes() for path in
                inspection_bytes} and
            after_acceptance.inspect()['records']==2 and
            len(after_acceptance.replay_scopes())==2)
        object_load=after_acceptance._load
        object_account=after_acceptance.account
        object_digest=after_acceptance.account_digest
        object_scope_denied=False
        def retarget_object(db):
            result=object_load(db)
            after_acceptance.account_digest='f'*64
            return result
        after_acceptance._load=retarget_object
        try:
            try:after_acceptance.inspect()
            except ValueError as error:
                object_scope_denied=(str(error)==
                    'PAPER_ACTIVITY_ACCEPTANCE_STORE_STORE_BINDING_CHANGED')
        finally:
            after_acceptance.account=object_account
            after_acceptance.account_digest=object_digest
            after_acceptance._load=object_load
        acceptance_object_scope_pinned=(object_scope_denied and
            inspection_bytes[acceptance_path]==acceptance_path.read_bytes() and
            after_acceptance.inspect()['records']==2)
        cleanup_binding_root=base/'cleanup-object-binding'
        cleanup_binding_root.mkdir()
        cleanup_binding_path=cleanup_binding_root/'acceptance.sqlite3'
        cleanup_binding_decoy=cleanup_binding_root/'decoy.sqlite3'
        cleanup_binding_decoy.write_bytes(b'do-not-delete')
        cleanup_binding_store=PaperActivityAcceptanceStore(
            cleanup_binding_path,ACCOUNT)
        cleanup_binding_require=acceptance_storage.require_file_identities
        cleanup_binding_denied=False
        def retarget_cleanup(paths,identities,prefix):
            cleanup_binding_require(paths,identities,prefix)
            cleanup_binding_store.path=cleanup_binding_decoy
            raise RuntimeError('injected object retarget')
        acceptance_storage.require_file_identities=retarget_cleanup
        try:
            try:cleanup_binding_store._connect(write=True)
            except RuntimeError as error:
                cleanup_binding_denied=(str(error)=='injected object retarget')
        finally:
            acceptance_storage.require_file_identities=cleanup_binding_require
        acceptance_cleanup_path_pinned=(cleanup_binding_denied and
            not cleanup_binding_path.exists() and
            cleanup_binding_decoy.read_bytes()==b'do-not-delete')
        read_close_before=acceptance_path.read_bytes()
        class ReadCloseFailureConnection(sqlite3.Connection):
            def close(self):
                super().close()
                raise RuntimeError('injected acceptance read close failure')
        def read_close_failure(write=False):
            if write:return acceptance_connect(write=write)
            return (sqlite3.connect(
                acceptance_path.resolve().as_uri()+'?mode=ro',uri=True,
                timeout=10,factory=ReadCloseFailureConnection),False)
        acceptance_read_close_requires_explicit_retry=False
        after_acceptance._connect=read_close_failure
        try:after_acceptance.inspect()
        except ValueError as error:
            acceptance_read_close_requires_explicit_retry=(str(error)==
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_READ_ACKNOWLEDGEMENT_UNCERTAIN')
        finally:after_acceptance._connect=acceptance_connect
        acceptance_read_close_requires_explicit_retry=(
            acceptance_read_close_requires_explicit_retry and
            acceptance_path.read_bytes()==read_close_before and
            after_acceptance.inspect()['records']==2 and
            len(after_acceptance.replay_scopes())==2)
        continuity_before=acceptance_path.read_bytes()
        continuity_fingerprint=acceptance_storage._main_fingerprint
        continuity_changed=False
        class SerializationRaceConnection(sqlite3.Connection):
            def serialize(self,*args,**kwargs):
                nonlocal continuity_changed
                result=super().serialize(*args,**kwargs)
                continuity_changed=True
                return result
        def serialization_race_connection(*args,**kwargs):
            if args or kwargs:raise AssertionError('unexpected connection arguments')
            return (sqlite3.connect(
                acceptance_path.resolve().as_uri()+'?mode=ro',uri=True,
                timeout=10,factory=SerializationRaceConnection),False)
        def serialization_race_fingerprint(path):
            result=continuity_fingerprint(path)
            if (continuity_changed and
                    Path(path).resolve()==acceptance_path.resolve()):
                result=(*result[:7],
                    ('0'*64 if result[7]!='0'*64 else '1'*64))
            return result
        acceptance_connection_content_continuity_pinned=False
        after_acceptance._connect=serialization_race_connection
        acceptance_storage._main_fingerprint=serialization_race_fingerprint
        try:after_acceptance.inspect()
        except ValueError as error:
            acceptance_connection_content_continuity_pinned=(str(error)==
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_FILE_SET_CHANGED')
        finally:
            after_acceptance._connect=acceptance_connect
            acceptance_storage._main_fingerprint=continuity_fingerprint
        acceptance_connection_content_continuity_pinned=(
            acceptance_connection_content_continuity_pinned and
            acceptance_path.read_bytes()==continuity_before and
            after_acceptance.inspect()['records']==2 and
            len(after_acceptance.replay_scopes())==2)
        write_root=base/'write-ack';write_root.mkdir()
        wp,wa,write_acceptance,write_scope=_scope(write_root)
        write_path=write_acceptance.path;foreign_before=foreign_acceptance.path.read_bytes()
        write_calls=0
        def lose_write_acknowledgement():
            nonlocal write_calls
            write_calls+=1
            if write_calls==2:
                write_acceptance.path=foreign_acceptance.path
            return wp.inspect()['head_sha256'],wa.inspect()['head_sha256']
        acceptance_write_binding_pinned=False
        try:write_acceptance.append(write_scope,
            admission=lose_write_acknowledgement)
        except ValueError as error:
            acceptance_write_binding_pinned=(str(error)==
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_STORE_BINDING_CHANGED')
        finally:write_acceptance.path=write_path
        committed_write=write_acceptance.inspect()
        recovered_write=write_acceptance.append(write_scope,admission=lambda:(
            wp.inspect()['head_sha256'],wa.inspect()['head_sha256']))
        acceptance_write_ack_replayed=(write_calls==2 and
            committed_write['records']==1 and recovered_write['duplicate'] is True and
            recovered_write['head_sha256']==committed_write['head_sha256'] and
            foreign_acceptance.path.read_bytes()==foreign_before)
        connection_root=base/'write-connection';connection_root.mkdir()
        cp,ca,connection_acceptance,connection_scope=_scope(connection_root)
        connection_foreign_root=base/'write-connection-foreign';connection_foreign_root.mkdir()
        _cfp,_cfa,connection_foreign,_cfs=_scope(connection_foreign_root,2)
        connection_foreign.append(_cfs,admission=lambda:(
            _cfp.inspect()['head_sha256'],_cfa.inspect()['head_sha256']))
        connection_before=(connection_acceptance.path.read_bytes(),
            connection_foreign.path.read_bytes())
        connection_open=connection_acceptance._connect
        connection_acceptance._connect=connection_foreign._connect
        acceptance_write_connection_pinned=False
        try:connection_acceptance.append(connection_scope,admission=lambda:(
            cp.inspect()['head_sha256'],ca.inspect()['head_sha256']))
        except ValueError as error:
            acceptance_write_connection_pinned=(str(error)==
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_CONNECTION_BINDING_CHANGED')
        finally:connection_acceptance._connect=connection_open
        acceptance_write_connection_pinned=(acceptance_write_connection_pinned and
            connection_before==(connection_acceptance.path.read_bytes(),
                connection_foreign.path.read_bytes()))
        connection_path=connection_acceptance.path
        class SpoofedWriteConnection(sqlite3.Connection):
            def execute(self,statement,*args,**kwargs):
                if statement=='PRAGMA database_list':
                    class StaticRows:
                        def fetchall(self):return [
                            (0,'main',str(connection_path.resolve()))]
                    return StaticRows()
                return super().execute(statement,*args,**kwargs)
        def substitute_write_content(*args,**kwargs):
            db=sqlite3.connect(
                connection_foreign.path.resolve().as_uri()+'?mode=rw',
                uri=True,timeout=10,factory=SpoofedWriteConnection)
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            return db,False
        acceptance_write_connection_content_pinned=False
        connection_acceptance._connect=substitute_write_content
        try:connection_acceptance.append(connection_scope,admission=lambda:(
            cp.inspect()['head_sha256'],ca.inspect()['head_sha256']))
        except ValueError as error:
            acceptance_write_connection_content_pinned=(str(error)==
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_CONNECTION_CONTENT_CHANGED')
        finally:connection_acceptance._connect=connection_open
        acceptance_write_connection_content_pinned=(
            acceptance_write_connection_content_pinned and
            connection_before==(connection_acceptance.path.read_bytes(),
                connection_foreign.path.read_bytes()))
        continuity_root=base/'write-connection-content-continuity'
        continuity_root.mkdir()
        ccp,cca,continuity_acceptance,continuity_scope=_scope(continuity_root)
        continuity_connect=continuity_acceptance._connect
        continuity_fingerprint=acceptance_storage._main_fingerprint
        continuity_serialize_calls=0;continuity_changed=False
        class ObservedContinuityConnection(sqlite3.Connection):
            def serialize(self,*args,**kwargs):
                nonlocal continuity_serialize_calls,continuity_changed
                result=super().serialize(*args,**kwargs)
                continuity_serialize_calls+=1
                if continuity_serialize_calls==2:continuity_changed=True
                return result
        def observed_continuity_connection(write=False):
            if write is not True:raise RuntimeError('fixture write required')
            db=sqlite3.connect(
                continuity_acceptance.path.resolve().as_uri()+'?mode=rw',
                uri=True,timeout=10,factory=ObservedContinuityConnection)
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            return db,False
        def changed_continuity_fingerprint(path):
            result=continuity_fingerprint(path)
            if continuity_changed:
                replacement=('f'*64 if result[-1]!='f'*64 else 'e'*64)
                return (*result[:-1],replacement)
            return result
        acceptance_write_connection_content_continuity_pinned=False
        continuity_acceptance._connect=observed_continuity_connection
        acceptance_storage._main_fingerprint=changed_continuity_fingerprint
        try:
            try:continuity_acceptance.append(continuity_scope,admission=lambda:(
                ccp.inspect()['head_sha256'],cca.inspect()['head_sha256']))
            except ValueError as error:
                acceptance_write_connection_content_continuity_pinned=(
                    str(error)==
                    'PAPER_ACTIVITY_ACCEPTANCE_STORE_CONNECTION_CONTENT_CHANGED')
        finally:
            continuity_acceptance._connect=continuity_connect
            acceptance_storage._main_fingerprint=continuity_fingerprint
        retained_continuity=continuity_acceptance.inspect()
        recovered_continuity=continuity_acceptance.append(continuity_scope,
            admission=lambda:(ccp.inspect()['head_sha256'],
                cca.inspect()['head_sha256']))
        acceptance_write_connection_content_continuity_pinned=(
            acceptance_write_connection_content_continuity_pinned and
            continuity_serialize_calls==2 and
            retained_continuity['records']==1 and
            recovered_continuity['duplicate'] is True and
            recovered_continuity['head_sha256']==
                retained_continuity['head_sha256'])
        identity_root=base/'write-identity';identity_root.mkdir()
        ip,ia,identity_acceptance,identity_scope=_scope(identity_root)
        identity_before=identity_acceptance.path.read_bytes()
        identity_capture=journal_guard.capture_file_identities
        identity_insert=identity_acceptance._insert
        identity_changed=False
        def changed_identity_capture(paths,prefix):
            result=identity_capture(paths,prefix)
            if identity_changed:
                path,device,inode=result[0]
                return ((path,device,inode+1),)
            return result
        def change_identity_after_insert(db,seq,body,previous):
            nonlocal identity_changed
            result=identity_insert(db,seq,body,previous)
            identity_changed=True;return result
        acceptance_write_file_identity_pinned=False
        journal_guard.capture_file_identities=changed_identity_capture
        identity_acceptance._insert=change_identity_after_insert
        try:
            try:identity_acceptance.append(identity_scope,admission=lambda:(
                ip.inspect()['head_sha256'],ia.inspect()['head_sha256']))
            except ValueError as error:
                acceptance_write_file_identity_pinned=(str(error)==
                    'PAPER_ACTIVITY_ACCEPTANCE_STORE_FILE_IDENTITY_CHANGED')
        finally:
            journal_guard.capture_file_identities=identity_capture
            identity_acceptance._insert=identity_insert
        acceptance_write_file_identity_pinned=(
            acceptance_write_file_identity_pinned and
            identity_acceptance.path.read_bytes()==identity_before and
            identity_acceptance.inspect()['records']==0)
        content_root=base/'write-committed-content';content_root.mkdir()
        ctp,cta,content_acceptance,content_scope=_scope(content_root)
        content_fingerprint=acceptance_storage._main_fingerprint
        content_sync=acceptance_storage._sync_committed_store
        content_changed=False
        def changed_content_fingerprint(path):
            result=content_fingerprint(path)
            if content_changed:
                replacement=('f'*64 if result[-1]!='f'*64 else 'e'*64)
                return (*result[:-1],replacement)
            return result
        def change_content_after_sync(*args,**kwargs):
            nonlocal content_changed
            result=content_sync(*args,**kwargs)
            content_changed=True
            return result
        acceptance_write_committed_content_pinned=False
        acceptance_storage._main_fingerprint=changed_content_fingerprint
        acceptance_storage._sync_committed_store=change_content_after_sync
        try:
            try:content_acceptance.append(content_scope,admission=lambda:(
                ctp.inspect()['head_sha256'],cta.inspect()['head_sha256']))
            except ValueError as error:
                acceptance_write_committed_content_pinned=(str(error)==
                    'PAPER_ACTIVITY_ACCEPTANCE_STORE_FILE_SET_CHANGED')
        finally:
            acceptance_storage._main_fingerprint=content_fingerprint
            acceptance_storage._sync_committed_store=content_sync
        retained_content=content_acceptance.inspect()
        recovered_content=content_acceptance.append(content_scope,
            admission=lambda:(ctp.inspect()['head_sha256'],cta.inspect()['head_sha256']))
        acceptance_write_committed_content_pinned=(
            acceptance_write_committed_content_pinned and
            retained_content['records']==1 and
            recovered_content['duplicate'] is True and
            recovered_content['head_sha256']==retained_content['head_sha256'])
        durability_root=base/'write-durability';durability_root.mkdir()
        dp,da,durability_acceptance,durability_scope=_scope(durability_root)
        original_fsync=acceptance_storage.os.fsync
        acceptance_commit_durability_replayed=False
        def fail_fsync(_descriptor):raise OSError('injected')
        acceptance_storage.os.fsync=fail_fsync
        try:
            try:durability_acceptance.append(durability_scope,admission=lambda:(
                dp.inspect()['head_sha256'],da.inspect()['head_sha256']))
            except ValueError as error:
                acceptance_commit_durability_replayed=(str(error)==
                    'PAPER_ACTIVITY_ACCEPTANCE_STORE_COMMIT_DURABILITY_UNCERTAIN')
        finally:acceptance_storage.os.fsync=original_fsync
        retained_durability=durability_acceptance.inspect()
        recovered_durability=durability_acceptance.append(durability_scope,
            admission=lambda:(dp.inspect()['head_sha256'],da.inspect()['head_sha256']))
        acceptance_commit_durability_replayed=(
            acceptance_commit_durability_replayed and
            retained_durability['records']==1 and
            recovered_durability['duplicate'] is True and
            recovered_durability['head_sha256']==retained_durability['head_sha256'])
        signal_root=base/'write-process-control';signal_root.mkdir()
        sp,sa,signal_acceptance,signal_scope=_scope(signal_root)
        signal_raised=False
        def interrupt_commit_sync(_descriptor):raise KeyboardInterrupt
        acceptance_storage.os.fsync=interrupt_commit_sync
        try:
            try:signal_acceptance.append(signal_scope,admission=lambda:(
                sp.inspect()['head_sha256'],sa.inspect()['head_sha256']))
            except KeyboardInterrupt:signal_raised=True
        finally:acceptance_storage.os.fsync=original_fsync
        retained_signal=signal_acceptance.inspect()
        recovered_signal=signal_acceptance.append(signal_scope,admission=lambda:(
            sp.inspect()['head_sha256'],sa.inspect()['head_sha256']))
        acceptance_commit_sync_preserves_process_control=(signal_raised and
            retained_signal['records']==1 and
            recovered_signal['duplicate'] is True and
            recovered_signal['head_sha256']==retained_signal['head_sha256'])
        read_signal_root=base/'read-finalization-process-control';read_signal_root.mkdir()
        rsp,rsa,read_signal_acceptance,read_signal_scope=_scope(read_signal_root)
        read_signal_acceptance.append(read_signal_scope,admission=lambda:(
            rsp.inspect()['head_sha256'],rsa.inspect()['head_sha256']))
        read_signal_bytes=read_signal_acceptance.path.read_bytes()
        read_signal_connect=read_signal_acceptance._connect
        read_signal_raised=False
        def interrupt_read_finalization(write=False):
            observed=read_signal_acceptance.path.stat()
            os.utime(read_signal_acceptance.path,ns=(observed.st_atime_ns,
                observed.st_mtime_ns+1_000_000_000))
            raise KeyboardInterrupt
        read_signal_acceptance._connect=interrupt_read_finalization
        try:
            try:read_signal_acceptance.inspect()
            except KeyboardInterrupt:read_signal_raised=True
        finally:read_signal_acceptance._connect=read_signal_connect
        read_signal_replay=read_signal_acceptance.inspect()
        acceptance_read_finalization_preserves_process_control=(
            read_signal_raised and
            read_signal_acceptance.path.read_bytes()==read_signal_bytes and
            read_signal_replay['records']==1 and
            read_signal_replay['last_record_sha256']==sha(read_signal_scope))
        write_finalization_root=base/'write-finalization-process-control'
        write_finalization_root.mkdir()
        wfp,wfa,write_finalization_acceptance,write_finalization_scope=(
            _scope(write_finalization_root))
        write_finalization_calls=0;write_finalization_raised=False
        write_finalization_admitted=(wfp.inspect()['head_sha256'],
            wfa.inspect()['head_sha256'])
        def interrupt_write_finalization():
            nonlocal write_finalization_calls
            write_finalization_calls+=1
            if write_finalization_calls==2:
                observed=write_finalization_acceptance.path.stat()
                os.utime(write_finalization_acceptance.path,ns=(
                    observed.st_atime_ns,
                    observed.st_mtime_ns+1_000_000_000))
                raise KeyboardInterrupt
            return write_finalization_admitted
        try:write_finalization_acceptance.append(
            write_finalization_scope,
            admission=interrupt_write_finalization)
        except KeyboardInterrupt:write_finalization_raised=True
        write_finalization_replay=write_finalization_acceptance.inspect()
        write_finalization_retry=write_finalization_acceptance.append(
            write_finalization_scope,
            admission=lambda:write_finalization_admitted)
        acceptance_write_finalization_preserves_process_control=(
            write_finalization_raised and write_finalization_calls==2 and
            write_finalization_replay['records']==1 and
            write_finalization_retry['duplicate'] is True and
            write_finalization_retry['head_sha256']==
                write_finalization_replay['head_sha256'])
        close_root=base/'write-close';close_root.mkdir()
        clp,cla,close_acceptance,close_scope=_scope(close_root)
        close_connect=close_acceptance._connect
        class WriteCloseFailureConnection(sqlite3.Connection):
            def close(self):
                super().close()
                raise RuntimeError('injected acceptance write close failure')
        def write_close_failure(write=False):
            if not write:return close_connect(write=write)
            db=sqlite3.connect(
                close_acceptance.path.resolve().as_uri()+'?mode=rw',uri=True,
                timeout=10,factory=WriteCloseFailureConnection)
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            return db,False
        acceptance_write_close_requires_explicit_recovery=False
        close_acceptance._connect=write_close_failure
        try:close_acceptance.append(close_scope,admission=lambda:(
            clp.inspect()['head_sha256'],cla.inspect()['head_sha256']))
        except ValueError as error:
            acceptance_write_close_requires_explicit_recovery=(str(error)==
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_COMMIT_DURABILITY_UNCERTAIN')
        finally:close_acceptance._connect=close_connect
        retained_close=close_acceptance.inspect()
        recovered_close=close_acceptance.append(close_scope,admission=lambda:(
            clp.inspect()['head_sha256'],cla.inspect()['head_sha256']))
        acceptance_write_close_requires_explicit_recovery=(
            acceptance_write_close_requires_explicit_recovery and
            retained_close['records']==1 and
            recovered_close['duplicate'] is True and
            recovered_close['head_sha256']==retained_close['head_sha256'])
        identity_continuity_root=base/'write-initialization-identity'
        identity_continuity_root.mkdir()
        identity_continuity_path=(identity_continuity_root/
            'acceptance.sqlite3')
        identity_continuity_store=PaperActivityAcceptanceStore(
            identity_continuity_path,ACCOUNT)
        identity_continuity_target=identity_continuity_path.resolve().as_uri()
        original_connect=acceptance_storage.sqlite3.connect
        original_require=acceptance_storage.require_file_identities
        identity_continuity_sql=None;identity_continuity_failures=0
        identity_continuity_connects=0;created_identity=None
        class IdentityContinuityConnection:
            def __init__(self,db):self.db=db
            def execute(self,sql,*args,**kwargs):
                nonlocal identity_continuity_sql
                result=self.db.execute(sql,*args,**kwargs)
                identity_continuity_sql=sql
                return result
            def close(self):return self.db.close()
            def __getattr__(self,name):return getattr(self.db,name)
        def identity_continuity_connect(*args,**kwargs):
            nonlocal identity_continuity_connects,created_identity
            if str(args[0]).startswith(identity_continuity_target):
                identity_continuity_connects+=1
                created_identity=acceptance_storage.capture_file_identities(
                    (identity_continuity_path,),
                    'PAPER_ACTIVITY_ACCEPTANCE_STORE_')
            db=original_connect(*args,**kwargs)
            if str(args[0]).startswith(identity_continuity_target):
                return IdentityContinuityConnection(db)
            return db
        def identity_continuity_require(paths,expected,prefix):
            nonlocal identity_continuity_failures
            if (identity_continuity_failures==0 and
                    type(identity_continuity_sql) is str and
                    identity_continuity_sql.startswith(
                        'PRAGMA synchronous')):
                identity_continuity_failures+=1
                raise ValueError(prefix+'FILE_IDENTITY_CHANGED')
            return original_require(paths,expected,prefix)
        initialization_identity_denied=False
        acceptance_storage.sqlite3.connect=identity_continuity_connect
        acceptance_storage.require_file_identities=(
            identity_continuity_require)
        try:
            try:identity_continuity_store.bind(pacing_head='0'*64,
                retention_head='0'*64,pacing_sessions=0,
                retained_batches=0)
            except ValueError as error:
                initialization_identity_denied=(str(error) in (
                    'PAPER_ACTIVITY_ACCEPTANCE_STORE_FILE_IDENTITY_CHANGED',
                    'PAPER_ACTIVITY_ACCEPTANCE_STORE_UNCOMMITTED_CLEANUP_UNCERTAIN'))
        finally:
            acceptance_storage.sqlite3.connect=original_connect
            acceptance_storage.require_file_identities=original_require
        identity_continuity_sidecars=all(not Path(
            str(identity_continuity_path)+suffix).exists() for suffix in
            ('-journal','-wal','-shm'))
        if identity_continuity_path.exists():
            retained_identity=(
                identity_continuity_path.stat().st_size>0 and
                acceptance_storage.capture_file_identities(
                    (identity_continuity_path,),
                    'PAPER_ACTIVITY_ACCEPTANCE_STORE_')==created_identity)
            retry_path=(identity_continuity_root/'retry.sqlite3')
        else:
            retained_identity=True;retry_path=identity_continuity_path
        identity_continuity_retry=PaperActivityAcceptanceStore(
            retry_path,ACCOUNT).bind(pacing_head='0'*64,
                retention_head='0'*64,pacing_sessions=0,
                retained_batches=0)
        acceptance_initialization_identity_continuity_pinned=(
            identity_continuity_failures==1 and
            identity_continuity_connects==1 and
            initialization_identity_denied and retained_identity and
            identity_continuity_sidecars and
            identity_continuity_retry['records']==0)
        cleanup_root=base/'write-initialization-cleanup';cleanup_root.mkdir()
        cleanup_acceptance=PaperActivityAcceptanceStore(
            cleanup_root/'acceptance.sqlite3',ACCOUNT)
        cleanup_insert=cleanup_acceptance._insert
        cleanup_failed=False
        cleanup_acceptance._insert=lambda *args,**kwargs:(_ for _ in ()).throw(
            RuntimeError('injected initialization failure'))
        try:
            try:cleanup_acceptance.bind(pacing_head='0'*64,
                retention_head='0'*64,pacing_sessions=0,
                retained_batches=0)
            except RuntimeError as error:
                cleanup_failed=(str(error)=='injected initialization failure')
        finally:cleanup_acceptance._insert=cleanup_insert
        cleanup_absent=(not cleanup_acceptance.path.exists() and
            not tuple(cleanup_root.iterdir()))
        cleanup_replayed=cleanup_acceptance.bind(pacing_head='0'*64,
            retention_head='0'*64,pacing_sessions=0,retained_batches=0)
        acceptance_uncommitted_initialization_replayed=(cleanup_failed and
            cleanup_absent and cleanup_replayed['records']==0 and
            cleanup_acceptance.inspect()['initialized'] is True)
        baseline_bytes=cleanup_acceptance.path.read_bytes()
        rebind_disagreement=False
        try:cleanup_acceptance.bind(pacing_head='0'*64,
            retention_head='0'*64,pacing_sessions=1,retained_batches=0)
        except ValueError as error:
            rebind_disagreement=(str(error)==
                'PAPER_ACTIVITY_ACCEPTANCE_STORE_REBIND_DISAGREEMENT')
        baseline_replayed=cleanup_acceptance.bind(pacing_head='0'*64,
            retention_head='0'*64,pacing_sessions=0,retained_batches=0)
        acceptance_rebind_boundary_pinned=(rebind_disagreement and
            cleanup_acceptance.path.read_bytes()==baseline_bytes and
            baseline_replayed['records']==0)
        silent=all(before[name]==after[name]==0 for name in ('external_connections','credential_operations'))
        unchanged=before['unchanged'] is after['unchanged'] is True
        rollback_observed=before['rollback_journal_observed'] is True
        scope_checks=[];scope_unchanged=True
        for label,target in (
            ('window',activity_url('2026-10-16T00:00:00Z','2026-10-17T00:00:00Z')),
            ('cursor',activity_url(AFTER,UNTIL,'20261017000000000::11234567-89AB-CDEF-0123-456789ABCDEF'))):
            root=base/label;root.mkdir()
            digests=[sha(dict(method='GET',target=url)) for url in (URLS[0],target,URLS[0])]
            p,a,accepted,bound_scope=_scope(root,request_targets=digests)
            accepted.append(bound_scope,admission=lambda:(p.inspect()['head_sha256'],a.inspect()['head_sha256']))
            original=_sqlite_files_snapshot(root)
            checked=_fresh_status(root)
            scope_checks.append(checked['heads_match'] and not checked['all_request_targets_bound'] and
                checked['recovery']=='ACCEPTED_PACING_REQUEST_TARGET_UNVERIFIED_REVIEW_REQUIRED')
            scope_unchanged=scope_unchanged and checked['unchanged'] and original==_sqlite_files_snapshot(root)
        legacy_root=base/'legacy-result';legacy_root.mkdir()
        p,a,accepted,legacy_scope=_scope(legacy_root,bind_normalized=False)
        accepted.append(legacy_scope,admission=lambda:(p.inspect()['head_sha256'],a.inspect()['head_sha256']))
        legacy_before=_sqlite_files_snapshot(legacy_root);legacy=_fresh_status(legacy_root)
        legacy_refused=(legacy['recovery']=='ACCEPTED_NORMALIZED_RESULT_UNVERIFIED_REVIEW_REQUIRED' and
            not legacy['all_normalized_results_bound'] and legacy['all_request_targets_bound'])
        legacy_preserved=legacy['unchanged'] and legacy_before==_sqlite_files_snapshot(legacy_root)
        passed=(missing_created_nothing and before_unbound and after_bound and
            acceptance_inspection_bound and acceptance_object_scope_pinned and
            acceptance_cleanup_path_pinned and
            acceptance_read_close_requires_explicit_retry and
            acceptance_connection_content_continuity_pinned and
            acceptance_write_binding_pinned and
            acceptance_write_ack_replayed and acceptance_write_connection_pinned and
            acceptance_write_connection_content_pinned and
            acceptance_write_connection_content_continuity_pinned and
            acceptance_write_file_identity_pinned and
            acceptance_write_committed_content_pinned and
            acceptance_commit_durability_replayed and
            acceptance_commit_sync_preserves_process_control and
            acceptance_read_finalization_preserves_process_control and
            acceptance_write_finalization_preserves_process_control and
            acceptance_write_close_requires_explicit_recovery and
            acceptance_initialization_identity_continuity_pinned and
            acceptance_uncommitted_initialization_replayed and
            acceptance_rebind_boundary_pinned and silent and unchanged and
            rollback_observed and all(scope_checks) and scope_unchanged and legacy_refused and legacy_preserved)
        return dict(mode=MODE,passed=passed,
            source=SOURCE,missing_status_created_nothing=missing_created_nothing,
            precommit_death_retained_unbound_transition=before_unbound,
            postcommit_death_replayed_bound_transition=after_bound,
            precommit_rollback_journal_observed=rollback_observed,
            fresh_process_journals_unchanged=unchanged,fresh_process_vault_silent=silent,
            postcommit_latest_content_replayed=after['latest_content_replayed'],
            postcommit_all_content_replayed=after['all_content_replayed'],
            postcommit_all_pacing_operations_typed=after['all_operations_typed'],
            postcommit_all_pacing_result_receipts_bound=after['all_receipts_bound'],
            postcommit_all_pacing_request_targets_bound=after['all_request_targets_bound'],
            postcommit_all_normalized_results_bound=after['all_normalized_results_bound'],
            acceptance_store_binding_pinned=acceptance_binding_denied,
            acceptance_object_scope_pinned=acceptance_object_scope_pinned,
            acceptance_cleanup_path_pinned=acceptance_cleanup_path_pinned,
            acceptance_connection_binding_pinned=acceptance_connection_denied,
            acceptance_connection_content_pinned=acceptance_content_denied,
            acceptance_read_close_requires_explicit_retry=
                acceptance_read_close_requires_explicit_retry,
            acceptance_connection_content_continuity_pinned=
                acceptance_connection_content_continuity_pinned,
            acceptance_write_binding_pinned=acceptance_write_binding_pinned,
            acceptance_write_ack_replayed=acceptance_write_ack_replayed,
            acceptance_write_connection_pinned=acceptance_write_connection_pinned,
            acceptance_write_connection_content_pinned=
                acceptance_write_connection_content_pinned,
            acceptance_write_connection_content_continuity_pinned=
                acceptance_write_connection_content_continuity_pinned,
            acceptance_write_file_identity_pinned=
                acceptance_write_file_identity_pinned,
            acceptance_write_committed_content_pinned=
                acceptance_write_committed_content_pinned,
            acceptance_commit_durability_replayed=acceptance_commit_durability_replayed,
            acceptance_commit_sync_preserves_process_control=
                acceptance_commit_sync_preserves_process_control,
            acceptance_read_finalization_preserves_process_control=
                acceptance_read_finalization_preserves_process_control,
            acceptance_write_finalization_preserves_process_control=
                acceptance_write_finalization_preserves_process_control,
            acceptance_write_close_requires_explicit_recovery=
                acceptance_write_close_requires_explicit_recovery,
            acceptance_initialization_identity_continuity_pinned=
                acceptance_initialization_identity_continuity_pinned,
            acceptance_uncommitted_initialization_replayed=
                acceptance_uncommitted_initialization_replayed,
            acceptance_rebind_boundary_pinned=acceptance_rebind_boundary_pinned,
            legacy_normalized_result_not_invented=legacy_refused,legacy_normalized_history_preserved=legacy_preserved,
            changed_creation_window_targets_refused=scope_checks[0],
            changed_cursor_target_refused=scope_checks[1],
            request_target_disagreement_history_preserved=scope_unchanged,
            accepted_transitions_after_commit=after['records'],
            actual_provider_access=False,actual_vault_access=False,source_attested=False,
            activity_coverage_verified=False,fee_execution_links_verified=False,
            broker_account_reconciled=False,spendable_funds_verified=False,ready_for_routing=False,
            broker_connected=False,live_orders_enabled=False,money_movement_enabled=False,release_approved=False,
            limitations=LIMITATIONS)
