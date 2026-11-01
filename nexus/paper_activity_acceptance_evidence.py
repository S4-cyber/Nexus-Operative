"""Disposable process-death evidence for private activity acceptance history.

This fixture uses only temporary local journals, synthetic digests and injected
activity.  It never reads credentials, contacts a provider or grants authority.
"""
import json
import hashlib
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

ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
AFTER = '2026-10-17T00:00:00Z'
UNTIL = '2026-10-18T00:00:00Z'
SQLITE_NAMES = tuple(
    name + suffix
    for name in ('pacing.sqlite3', 'activity.sqlite3', 'acceptance.sqlite3')
    for suffix in ('', '-journal', '-wal', '-shm')
)


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
        _cfp,_cfa,connection_foreign,_cfs=_scope(connection_foreign_root)
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
            acceptance_inspection_bound and acceptance_write_binding_pinned and
            acceptance_write_ack_replayed and acceptance_write_connection_pinned and
            acceptance_commit_durability_replayed and silent and unchanged and
            rollback_observed and all(scope_checks) and scope_unchanged and legacy_refused and legacy_preserved)
        return dict(mode='DISPOSABLE_CROSS_JOURNAL_ACTIVITY_ACCEPTANCE_RECOVERY',passed=passed,
            source='SYNTHETIC_LOCAL_PROCESS_DEATH_FIXTURE',missing_status_created_nothing=missing_created_nothing,
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
            acceptance_connection_binding_pinned=acceptance_connection_denied,
            acceptance_connection_content_pinned=acceptance_content_denied,
            acceptance_write_binding_pinned=acceptance_write_binding_pinned,
            acceptance_write_ack_replayed=acceptance_write_ack_replayed,
            acceptance_write_connection_pinned=acceptance_write_connection_pinned,
            acceptance_commit_durability_replayed=acceptance_commit_durability_replayed,
            legacy_normalized_result_not_invented=legacy_refused,legacy_normalized_history_preserved=legacy_preserved,
            changed_creation_window_targets_refused=scope_checks[0],
            changed_cursor_target_refused=scope_checks[1],
            request_target_disagreement_history_preserved=scope_unchanged,
            accepted_transitions_after_commit=after['records'],
            actual_provider_access=False,actual_vault_access=False,source_attested=False,
            activity_coverage_verified=False,fee_execution_links_verified=False,
            broker_account_reconciled=False,spendable_funds_verified=False,ready_for_routing=False,
            broker_connected=False,live_orders_enabled=False,money_movement_enabled=False,release_approved=False,
            limitations='Temporary synthetic local journals and forced process exits only. Full-file-set fingerprints cover the three main files and their rollback/WAL/shared-memory sidecars; replaying both accepted content records and exact journal transitions demonstrates only SQLite commit-boundary and local-history consistency for cooperating files, not provider causality, source or activity coverage, fee links, account reconciliation, broker-wide ownership, funds, routing or release acceptance.')
