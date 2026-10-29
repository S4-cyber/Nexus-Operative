"""Private normalized evidence must match every shared provider reservation."""
from copy import deepcopy
import json
import os
import shutil
import pytest
from nexus import paper_provider_result_review as subject, paper_provider_read_review as provider
from nexus import paper_provider_read_review_store as stores, paper_read_review as accounts
from nexus.paper_provider_read_budget import ZERO
from test_alpaca_paper_inspection import OTHER


def files(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}

@pytest.fixture
def history(tmp_path):
    budget,gates,bindings=subject._fixture(tmp_path)
    options=dict(expected_provider_head=budget.inspect()['head_sha256'],
        expected_account_heads={g.account_digest:g.inspect()['head_sha256'] for g in gates})
    return budget,gates,bindings,options


def test_actual_readers_compose_without_mutation_or_authority(history,tmp_path):
    budget,gates,bindings,options=history;before=files(tmp_path)
    result=subject.review(budget,gates,bindings,**options)
    assert result['local_provider_results_consistent'] and result['normalized_sessions_matched']==5
    assert not result['review_reasons'] and all(result[k] is False for k in subject.FLAGS)
    assert not result['admission_reserved'] and files(tmp_path)==before
    assert result['journal_files_rechecked_unchanged']
    assert result['semantic_reader_failures_rechecked']
    assert gates[0].account not in json.dumps(result) and 'BTC/USD' not in json.dumps(result)


def _mutate_metadata_and_fail(path):
    status=os.stat(path)
    os.utime(path,ns=(status.st_atime_ns,status.st_mtime_ns+1_000_000_000))
    raise RuntimeError('injected provider result review failure')


@pytest.mark.parametrize(('reader','target'),(
    ('initial_provider','provider'),('initial_provider','account'),
    ('account','provider'),('account','account'),
    ('final_provider','provider'),('final_provider','account')))
def test_semantic_reader_failure_rechecks_every_selected_journal(
        history,monkeypatch,reader,target):
    budget,gates,bindings,options=history
    paths=dict(provider=budget.path,account=gates[0].path)
    original_provider=provider.review;original_account=accounts.review
    calls=[]
    def changed_provider(*args,**kwargs):
        result=original_provider(*args,**kwargs);calls.append(1)
        if ((reader=='initial_provider' and len(calls)==1) or
                (reader=='final_provider' and len(calls)==2)):
            _mutate_metadata_and_fail(paths[target])
        return result
    def changed_account(*args,**kwargs):
        result=original_account(*args,**kwargs)
        if reader=='account':_mutate_metadata_and_fail(paths[target])
        return result
    monkeypatch.setattr(provider,'review',changed_provider)
    monkeypatch.setattr(accounts,'review',changed_account)
    with pytest.raises(ValueError,match=
            'PAPER_PROVIDER_RESULT_REVIEW_FILE_SET_CHANGED'):
        subject.review(budget,gates,bindings,**options)


@pytest.mark.parametrize('target',('provider','account'))
def test_nested_reader_cannot_retarget_selected_store(
        history,tmp_path,monkeypatch,target):
    budget,gates,bindings,options=history
    store=budget if target=='provider' else gates[0]
    original_path=store.path;clone=tmp_path/f'{target}-clone.sqlite3'
    original=accounts.review
    def retarget(*args,**kwargs):
        result=original(*args,**kwargs)
        shutil.copyfile(original_path,clone);store.path=clone
        return result
    monkeypatch.setattr(accounts,'review',retarget)
    with pytest.raises(ValueError,match=
            'PAPER_PROVIDER_RESULT_REVIEW_STORE_BINDING_CHANGED'):
        subject.review(budget,gates,bindings,**options)


@pytest.mark.parametrize('index',range(5))
@pytest.mark.parametrize('change',['omit','result','scope'])
def test_counts_cannot_hide_changed_or_missing_normalized_evidence(history,tmp_path,index,change):
    budget,gates,bindings,options=history;values=bindings[gates[0].account_digest]
    if change=='omit':values.pop(index)
    elif change=='result':values[index]['normalized_result']={'private':'never_echo_private'}
    else:values[index]['request_scope']={'private':'never_echo_private'}
    before=files(tmp_path)
    assert provider.review(budget,gates,**options)['local_provider_account_history_consistent']
    result=subject.review(budget,gates,bindings,**options)
    assert not result['local_provider_results_consistent']
    assert 'ACCOUNT_NORMALIZED_EVIDENCE_UNVERIFIED' in result['review_reasons']
    assert 'never_echo_private' not in json.dumps(result) and files(tmp_path)==before
    path=tmp_path/'receipt.sqlite3'
    with pytest.raises(ValueError,match='RESULT_REVIEW_INCOMPLETE'):
        subject.retain_review(path,budget,gates,bindings,**options)
    assert not path.exists()


@pytest.mark.parametrize('change',['missing','foreign','tuple','duplicate','cycle','oversize'])
def test_aggregate_input_shape_is_bounded_and_private(history,tmp_path,change):
    budget,gates,bindings,options=history;key=gates[0].account_digest
    if change=='missing':bindings={}
    if change=='foreign':bindings['f'*64]=[]
    if change=='tuple':bindings[key]=tuple(bindings[key])
    if change=='duplicate':bindings[key].append(deepcopy(bindings[key][0]))
    if change=='cycle':bindings[key][0]['normalized_result']=bindings
    if change=='oversize':bindings[key][0]['normalized_result']='x'*(accounts.MAX_BINDING_BYTES+1)
    before=files(tmp_path)
    with pytest.raises(ValueError,match='^PAPER_PROVIDER_RESULT_REVIEW_ACCOUNT_BINDINGS_INVALID$'):
        subject.review(budget,gates,bindings,**options)
    assert files(tmp_path)==before


@pytest.mark.parametrize('which',['provider','account'])
def test_stale_history_heads_fail(history,which):
    budget,gates,bindings,options=history
    if which=='provider':options['expected_provider_head']=ZERO
    else:options['expected_account_heads'][gates[0].account_digest]=ZERO
    with pytest.raises(ValueError,match='HEAD_CHANGED'):subject.review(budget,gates,bindings,**options)


def test_two_real_account_histories_have_exact_independent_bindings(history,tmp_path):
    budget,gates,bindings,options=history
    gate,values=accounts._fixture(tmp_path/'other',provider_budget=budget,account_id=OTHER,epoch=1792368600)
    gates.append(gate);bindings[gate.account_digest]=values
    options=dict(expected_provider_head=budget.inspect()['head_sha256'],
        expected_account_heads={g.account_digest:g.inspect()['head_sha256'] for g in gates})
    result=subject.review(budget,gates,bindings,**options)
    assert result['local_provider_results_consistent'] and result['normalized_sessions_matched']==10
    bindings[gates[0].account_digest],bindings[gate.account_digest]=bindings[gate.account_digest],bindings[gates[0].account_digest]
    assert not subject.review(budget,gates,bindings,**options)['local_provider_results_consistent']


def test_external_mutation_after_freeze_cannot_replace_evidence(history,monkeypatch):
    budget,gates,bindings,options=history;original=provider.review
    def mutate(*args,**kwargs):
        bindings.clear()
        return original(*args,**kwargs)
    monkeypatch.setattr(provider,'review',mutate)
    assert subject.review(budget,gates,bindings,**options)['local_provider_results_consistent']


def test_history_append_during_account_review_fails(history,monkeypatch):
    budget,gates,bindings,options=history;original=accounts.review
    def append(*args,**kwargs):
        result=original(*args,**kwargs);gate=gates[0]
        with gate.ownership() as owner:
            gate.begin(admission=lambda:1792369000,ownership=owner,operation='ACCOUNT_INVENTORY')
        return result
    monkeypatch.setattr(accounts,'review',append)
    with pytest.raises(ValueError,match='PAPER_PROVIDER_RESULT_REVIEW_FILE_SET_CHANGED'):
        subject.review(budget,gates,bindings,**options)


def test_versioned_receipts_preserve_legacy_meaning_and_replay(history,tmp_path):
    budget,gates,bindings,options=history;path=tmp_path/'review.sqlite3'
    old=provider.retain_review(path,budget,gates,**options)
    new=subject.retain_review(path,budget,gates,bindings,expected_store_head=old['head_sha256'],**options)
    store=stores.PaperProviderReadReviewStore(path,budget,gates);replay=store.inspect()
    scopes=[r['scope'] for r in replay['receipts']]
    assert [s['profile'] for s in scopes]==[provider.PROFILE,stores.RESULT_PROFILE]
    assert 'result_manifest_sha256' not in scopes[0]
    assert scopes[1]['normalized_sessions_replayed']==scopes[1]['matched_sessions']==5
    before=path.read_bytes()
    assert subject.retain_review(path,budget,gates,bindings,expected_store_head=new['head_sha256'],**options)['duplicate']
    assert path.read_bytes()==before and gates[0].account.encode() not in before and b'BTC/USD' not in before
    with pytest.raises(ValueError,match='HEAD_CHANGED'):subject.retain_review(path,budget,gates,bindings,**options)
    with pytest.raises(ValueError,match='INPUT_REVOKED'):
        subject.retain_review(path,budget,gates,bindings,expected_store_head=new['head_sha256'],precommit_check=lambda:False,**options)
    assert path.read_bytes()==before


@pytest.mark.parametrize('calls,committed',[(1,False),(2,False),(3,True)])
def test_revocation_before_and_after_commit_is_explicit(history,tmp_path,calls,committed):
    budget,gates,bindings,options=history;path=tmp_path/'review.sqlite3';seen=[]
    def permission():
        seen.append(1);return len(seen)!=calls
    with pytest.raises(ValueError,match='INPUT_REVOKED'):
        subject.retain_review(path,budget,gates,bindings,precommit_check=permission,**options)
    if committed:assert stores.PaperProviderReadReviewStore(path,budget,gates).inspect()['retained_receipts']==1
    elif path.exists():
        import sqlite3
        with sqlite3.connect(path) as db:assert not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()


@pytest.mark.parametrize('change',['missing','extra','count','bool','digest','legacy'])
def test_new_receipt_profile_has_strict_nonpromotable_fields(history,tmp_path,change):
    budget,gates,bindings,options=history;path=tmp_path/'review.sqlite3'
    subject.retain_review(path,budget,gates,bindings,**options)
    store=stores.PaperProviderReadReviewStore(path,budget,gates)
    value=deepcopy(store.inspect()['receipts'][0]['scope'])
    if change=='missing':value.pop('result_manifest_sha256')
    if change=='extra':value['private']='never_echo_private'
    if change=='count':value['normalized_sessions_replayed']=4
    if change=='bool':value['normalized_sessions_replayed']=True
    if change=='digest':value['result_manifest_sha256']='never_echo_private'
    if change=='legacy':value['profile']=provider.PROFILE
    with pytest.raises(ValueError,match='PAPER_PROVIDER_READ_REVIEW_STORE_'):store._scope(value)


def test_store_cannot_alias_source_files(history):
    budget,gates,bindings,options=history
    for path in (budget.path,gates[0].path,budget.path.with_name(budget.path.name+'-wal')):
        with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):
            subject.retain_review(path,budget,gates,bindings,**options)


def test_fixture_requires_all_checks_and_false_authority():
    result=subject.fixture_report()
    assert result['passed'] and all(result[k] is True for k in subject.CHECKS)
    assert all(result[k] is False for k in subject.FLAGS)


@pytest.mark.parametrize('operation',tuple(__import__('nexus.paper_read_pacing',fromlist=['OPERATIONS']).OPERATIONS))
def test_self_consistent_account_receipt_with_wrong_target_fails_composition(tmp_path,monkeypatch,operation):
    from nexus.paper_read_pacing import PaperReadPacing
    original=PaperReadPacing.finish
    def changed(self,token,**kwargs):
        receipt=deepcopy(kwargs['receipt'])
        if receipt['operation']==operation:receipt['request_sha256'][1]='f'*64
        return original(self,token,**dict(kwargs,receipt=receipt))
    monkeypatch.setattr(PaperReadPacing,'finish',changed)
    budget,gates,bindings=subject._fixture(tmp_path)
    options=dict(expected_provider_head=budget.inspect()['head_sha256'],
        expected_account_heads={g.account_digest:g.inspect()['head_sha256'] for g in gates})
    assert provider.review(budget,gates,**options)['local_provider_account_history_consistent']
    assert not subject.review(budget,gates,bindings,**options)['local_provider_results_consistent']


def test_fixture_runs_in_cold_process_with_sockets_and_vault_access_denied():
    import subprocess
    import sys
    code='''
import socket,json
from nexus import paper_credentials
from nexus.paper_provider_result_review import fixture_report
def denied(*a,**k):raise AssertionError('unexpected external access')
socket.socket=denied
paper_credentials._vault=denied
paper_credentials._capture=denied
print(json.dumps(fixture_report()))
'''
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=30,check=True)
    assert json.loads(result.stdout)['passed']
