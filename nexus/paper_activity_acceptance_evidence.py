"""Disposable process-death evidence for private activity acceptance history.

This fixture uses only temporary local journals, synthetic digests and injected
activity.  It never reads credentials, contacts a provider or grants authority.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from nexus.alpaca_paper_contract import sha
from nexus.broker_activity_store import BrokerActivityStore
from nexus.paper_activity_acceptance_probe import status
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore, PROFILE
from nexus.paper_read_pacing import PaperReadPacing

ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
AFTER = '2026-10-17T00:00:00Z'
UNTIL = '2026-10-18T00:00:00Z'


def _scope(root):
    pacing=PaperReadPacing(root/'pacing.sqlite3',ACCOUNT)
    activity=BrokerActivityStore(root/'activity.sqlite3',ACCOUNT)
    acceptance=PaperActivityAcceptanceStore(root/'acceptance.sqlite3',ACCOUNT)
    before_pacing=pacing.inspect();before_activity=activity.inspect()
    acceptance.bind(pacing_head=before_pacing['head_sha256'],retention_head=before_activity['head_sha256'],
        pacing_sessions=before_pacing['sessions'],retained_batches=before_activity['retained_batches'])
    token=pacing.begin(admission=lambda:1000)
    pacing.finish(token,admission=lambda:1000,status_code=200)
    row=dict(id='20261017000000000::11234567-89ab-cdef-0123-000000000001',
        activity_type='FEE',status='executed',net_amount='-0.01',date='2026-10-16')
    receipt=activity.retain([row],[],after=AFTER,until=UNTIL)
    after_pacing=pacing.inspect();after_activity=activity.inspect()
    scope=dict(profile=PROFILE,account_sha256=after_activity['account_sha256'],
        collection_scope_sha256=sha(dict(fixture='process-death',kind='scope')),
        account_fields_sha256=sha(dict(fixture='process-death',kind='account')),
        collection_observation_sha256=sha(dict(fixture='process-death',kind='observation')),
        activity_input_sha256=sha(dict(fixture='process-death',kind='input')),
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
def denied(): attempts['credential_operations']+=1;raise RuntimeError('VAULT_DENIED')
paper_credentials._vault=denied
from nexus.paper_activity_acceptance_probe import status
root=Path(sys.argv[1]);paths=[root/name for name in ('pacing.sqlite3','activity.sqlite3','acceptance.sqlite3')]
before=[path.read_bytes() for path in paths]
result=status(expected_account_id=sys.argv[2],pacing_path=paths[0],activity_store_path=paths[1],acceptance_store_path=paths[2])
print(json.dumps({'recovery':result['cross_journal_recovery_state'],'heads_match':result['cross_journal_heads_match'],'records':result['accepted_transitions'],'unchanged':before==[path.read_bytes() for path in paths],**attempts}))
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
        after=_fresh_status(after_root)
        after_bound=(after_acceptance.inspect()['records']==1 and
            after['recovery']=='BOUND_TRANSITIONS_REPLAYED_REVIEW_REQUIRED' and after['heads_match'] and after['records']==1)
        silent=all(before[name]==after[name]==0 for name in ('external_connections','credential_operations'))
        unchanged=before['unchanged'] is after['unchanged'] is True
        passed=missing_created_nothing and before_unbound and after_bound and silent and unchanged
        return dict(mode='DISPOSABLE_CROSS_JOURNAL_ACTIVITY_ACCEPTANCE_RECOVERY',passed=passed,
            source='SYNTHETIC_LOCAL_PROCESS_DEATH_FIXTURE',missing_status_created_nothing=missing_created_nothing,
            precommit_death_retained_unbound_transition=before_unbound,
            postcommit_death_replayed_bound_transition=after_bound,
            fresh_process_journals_unchanged=unchanged,fresh_process_vault_silent=silent,
            accepted_transitions_after_commit=after['records'],
            actual_provider_access=False,actual_vault_access=False,source_attested=False,
            activity_coverage_verified=False,fee_execution_links_verified=False,
            broker_account_reconciled=False,spendable_funds_verified=False,ready_for_routing=False,
            broker_connected=False,live_orders_enabled=False,money_movement_enabled=False,release_approved=False,
            limitations='Temporary synthetic local journals and forced process exits only. A replayed binding demonstrates SQLite commit-boundary recovery for cooperating files, not provider causality, source or activity coverage, fee links, account reconciliation, broker-wide ownership, funds, routing or release acceptance.')
