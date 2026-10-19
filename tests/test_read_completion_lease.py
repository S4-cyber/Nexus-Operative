"""Typed outcomes require the original uninterrupted local ownership acquisition."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from nexus import paper_read_pacing as pacing
from nexus.alpaca_paper_contract import sha
from test_alpaca_paper_inspection import ACCOUNT


@pytest.fixture
def gate(tmp_path):return pacing.PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)


def begin(gate,owner,operation='BTC_QUOTE_OBSERVATION'):
    return gate.begin(admission=lambda:1000,ownership=owner,operation=operation)


def receipt(operation='BTC_QUOTE_OBSERVATION',success=True):
    kinds=list(pacing.OPERATION_PATTERNS[operation][0] if success else pacing.OPERATION_PATTERNS[operation][0][:1])
    count=len(kinds) if success else len(kinds)-1
    return dict(operation=operation,request_kinds=kinds,
        request_sha256=[sha(dict(operation=operation,target=index)) for index in range(len(kinds))],
        response_sha256=[sha(dict(operation=operation,index=index)) for index in range(count)])


@pytest.mark.parametrize('operation',pacing.OPERATIONS)
def test_no_owner_completion_refuses_before_admission_and_preserves_typed_uncertainty(gate,operation):
    with gate.ownership() as owner:
        token=begin(gate,owner,operation);before=gate.path.read_bytes()
        with pytest.raises(ValueError,match='OPERATION_OWNER_REQUIRED'):
            gate.finish(token,admission=lambda:pytest.fail('unauthorized admission callback'),status_code=200)
    assert gate.path.read_bytes()==before and gate.inspect()['pending_token']==token


@pytest.mark.parametrize('outcome',[
    dict(status_code=200),dict(status_code=404),dict(status_code=429,retry_after='120'),
    dict(status_code=401),dict(status_code=500),dict(transport_error='TIMEOUT'),dict(transport_error='NETWORK_ERROR')],
    ids=['success','not-found','rate','auth','server','timeout','network'])
def test_original_held_lease_can_record_each_outcome(gate,outcome):
    with gate.ownership() as owner:
        token=begin(gate,owner);head=gate.finish(token,admission=lambda:1001,ownership=owner,
            receipt=receipt(success=outcome.get('status_code')==200),**outcome)
    assert gate.inspect()['pending_token'] is None and gate.inspect()['head_sha256']==head


@pytest.mark.parametrize('replacement',['same_object','new_object','cold_instance'])
def test_reacquired_or_restarted_lease_cannot_finalize_original_ticket(gate,replacement):
    original=gate.ownership()
    with original:token=begin(gate,original)
    before=gate.path.read_bytes()
    current=pacing.PaperReadPacing(gate.path,ACCOUNT) if replacement=='cold_instance' else gate
    owner=original if replacement=='same_object' else current.ownership()
    with owner:
        with pytest.raises(ValueError,match='ORIGINAL_LEASE_REQUIRED'):current.finish(token,admission=lambda:1001,ownership=owner,status_code=200)
    assert gate.path.read_bytes()==before and current.inspect()['pending_token']==token
    state=current.inspect()
    current.interrupt(token,expected_head=state['head_sha256'],expected_account_id=ACCOUNT,confirm_interrupted=True,admission=lambda:1001)
    assert current.inspect()['last_outcome']=='INTERRUPTED_UNKNOWN' and current.inspect()['next_allowed_at']==87401


def test_owner_release_after_result_insert_rolls_back_and_keeps_pending(gate,monkeypatch):
    owner=gate.ownership();owner.__enter__();token=begin(gate,owner);before=gate.path.read_bytes();insert=gate._append
    def append(db,seq,body,previous):
        result=insert(db,seq,body,previous)
        if body.get('kind')=='RESULT':owner.__exit__()
        return result
    monkeypatch.setattr(gate,'_append',append)
    with pytest.raises(ValueError,match='OWNER_REQUIRED'):gate.finish(token,admission=lambda:1001,ownership=owner,
        status_code=200,receipt=receipt())
    assert gate.path.read_bytes()==before and gate.inspect()['pending_token']==token


def test_fresh_process_denies_completion_and_preserves_bytes(gate):
    with gate.ownership() as owner:token=begin(gate,owner)
    before=gate.path.read_bytes()
    script='''
import sys
from nexus.paper_read_pacing import PaperReadPacing
p=PaperReadPacing(sys.argv[1],sys.argv[2])
with p.ownership() as owner:
 try:p.finish(sys.argv[3],admission=lambda:1001,status_code=200,ownership=owner)
 except ValueError as error:assert str(error)=='PAPER_READ_PACING_OPERATION_ORIGINAL_LEASE_REQUIRED'
 else:raise AssertionError('cold completion accepted')
assert p.inspect()['pending_token']==sys.argv[3]
'''
    result=subprocess.run([sys.executable,'-c',script,str(gate.path),ACCOUNT,token],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert gate.path.read_bytes()==before


def test_lost_result_return_retains_committed_outcome_and_never_rearms(gate,monkeypatch):
    with gate.ownership() as owner:
        token=begin(gate,owner);connect=gate._connect
        class LostReturn:
            def __init__(self,db):self.db=db
            def __getattr__(self,name):return getattr(self.db,name)
            def __enter__(self):self.db.__enter__();return self
            def __exit__(self,*args):
                self.db.__exit__(*args)
                if args[0] is None:raise OSError('lost commit acknowledgment')
        monkeypatch.setattr(gate,'_connect',lambda write=False:LostReturn(connect(write)) if write else connect())
        with pytest.raises(OSError):gate.finish(token,admission=lambda:1001,status_code=200,ownership=owner,receipt=receipt())
        monkeypatch.setattr(gate,'_connect',connect)
        before=gate.path.read_bytes()
        with pytest.raises(ValueError,match='TOKEN_DISAGREEMENT'):gate.finish(token,admission=lambda:1002,status_code=200,ownership=owner)
    assert gate.path.read_bytes()==before and gate.inspect()['last_outcome']=='SUCCESS'


def test_legacy_untyped_completion_stays_backward_compatible(gate):
    token=gate.begin(admission=lambda:1000);gate.finish(token,admission=lambda:1001,status_code=200)
    assert gate.operation_history()[0]['operation']=='LEGACY_UNSPECIFIED'
    assert gate.inspect()['operation_identity_attested'] is False


def test_fixture_is_aggregate_only_and_does_not_promote_ownership_attestation():
    r=pacing.completion_fixture_report()
    assert r['passed'] and all(r[k] is True for k in pacing.COMPLETION_CHECKS)
    assert all(r[k] is False for k in pacing.OPERATION_FLAGS)
    assert set(r)=={'source','passed',*pacing.COMPLETION_CHECKS,*pacing.OPERATION_FLAGS}


@pytest.mark.parametrize('field',pacing.COMPLETION_CHECKS)
def test_release_requires_each_original_lease_check(field):
    from nexus import release_trials,release_roadmap
    r=json.loads((Path(__file__).resolve().parents[1]/'validation'/('release_trials_'+release_roadmap.REVISION.replace('.','_')+'.json')).read_text())['profiles']['pacing']
    r['read_completion'][field]=False
    with pytest.raises(ValueError,match='READ_COMPLETION_LEASE'):release_trials._validate('pacing',r)


@pytest.mark.parametrize('field',pacing.OPERATION_FLAGS)
def test_completion_fixture_cannot_grant_source_or_trading_authority(field):
    from nexus import release_trials,release_roadmap
    r=json.loads((Path(__file__).resolve().parents[1]/'validation'/('release_trials_'+release_roadmap.REVISION.replace('.','_')+'.json')).read_text())['profiles']['pacing']
    r['read_completion'][field]=True
    with pytest.raises(ValueError,match='READ_COMPLETION_AUTHORITY'):release_trials._validate('pacing',r)
