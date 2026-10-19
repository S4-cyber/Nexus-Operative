"""Whole mixed read history review, supplied fixtures only and no authority."""
from copy import deepcopy
import json
import subprocess
import sys
import pytest
from nexus import paper_read_review as module,paper_read_pacing as pacing
from nexus.alpaca_paper_contract import sha
from test_alpaca_paper_inspection import ACCOUNT,OTHER


def files(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def history(tmp_path):return module._fixture(tmp_path)


def review(gate,bindings):return module.review(gate,bindings,expected_head=gate.inspect()['head_sha256'])


def test_five_actual_results_match_without_exposing_inputs_or_granting_authority(history,tmp_path):
    gate,bindings=history;before=files(tmp_path);original=deepcopy(bindings);result=review(gate,bindings)
    assert result['receipt_bindings_consistent'] and result['complete_mixed_history_consistent']
    assert result['all_five_purposes_observed'] and result['matched_sessions']==result['sessions_replayed']==5
    assert result['status']=='REVIEW_REQUIRED' and result['review_reasons']==[]
    assert all(result[key] is False for key in module.FLAGS) and not result['admission_reserved']
    text=json.dumps(result)
    assert all(value not in text for value in (ACCOUNT,'nx_review_fixture','fixture_key_only_123','fixture_secret_only_123','pacing.sqlite3'))
    assert bindings==original and files(tmp_path)==before


@pytest.mark.parametrize('index',range(5))
def test_no_successful_session_may_be_omitted(history,index):
    gate,bindings=history;bindings.pop(index);r=review(gate,bindings)
    assert not r['receipt_bindings_consistent'] and not r['complete_mixed_history_consistent']
    assert 'SUCCESSFUL_RESULT_BINDING_MISSING' in r['review_reasons']


@pytest.mark.parametrize('index',range(5))
def test_changed_result_cannot_keep_local_agreement(history,index):
    gate,bindings=history
    if index==4:bindings[index]['normalized_result']['quote']['bid']='99'
    elif index==2:bindings[index]['normalized_result']='f'*64
    else:bindings[index]['normalized_result']['private_extra']='fixture_private_content'
    r=review(gate,bindings)
    assert 'NORMALIZED_RESULT_UNVERIFIED' in r['review_reasons'] and not r['receipt_bindings_consistent']
    assert 'fixture_private_content' not in json.dumps(r)


@pytest.mark.parametrize('operation',pacing.OPERATIONS)
@pytest.mark.parametrize('change',['target','v2'])
def test_self_consistent_receipt_with_wrong_target_or_old_projection_is_unverified(tmp_path,monkeypatch,operation,change):
    original=pacing.PaperReadPacing.finish
    def altered(self,token,**kw):
        receipt=deepcopy(kw['receipt'])
        if receipt['operation']==operation:
            if change=='v2':receipt.pop('result_projection_sha256',None)
            else:receipt['request_sha256'][1]='f'*64
        return original(self,token,**dict(kw,receipt=receipt))
    monkeypatch.setattr(pacing.PaperReadPacing,'finish',altered)
    gate,bindings=module._fixture(tmp_path);before=files(tmp_path);r=review(gate,bindings)
    assert not r['receipt_bindings_consistent'] and r['matched_sessions']==4
    assert ('REQUEST_SCOPE_UNVERIFIED' if change=='target' else 'NORMALIZED_RESULT_UNVERIFIED') in r['review_reasons']
    assert files(tmp_path)==before


@pytest.mark.parametrize('index',range(5))
def test_each_scope_checks_account_or_original_target_independently(history,index):
    gate,bindings=history;record=gate.operation_history()[index];binding=bindings[index]
    if index in (0,1):binding['normalized_result']['account_id']=OTHER
    elif index==4:binding['normalized_result']['account_sha256']='f'*64
    elif index==3:
        binding['normalized_result']['client_order_id']='other'
        binding['normalized_result']['journal']['client_order_id']='other'
    else:
        from nexus.alpaca_activity_collection import activity_url
        payload=binding['request_scope'];payload['after']='2026-10-16T00:00:00Z';payload['until']='2026-10-17T00:00:00Z'
        payload['collection_observation']['pages'][0]['query_sha256']=sha(activity_url(payload['after'],payload['until'],None))
        binding['normalized_result']=sha(payload)
    assert not module._scope_matches(record,binding,gate)


@pytest.mark.parametrize('kind',['legacy','failed','pending','interrupted'])
def test_later_uncertain_or_legacy_tail_cannot_be_hidden_by_five_valid_results(history,kind,tmp_path):
    gate,bindings=history
    if kind=='legacy':
        token=gate.begin(admission=lambda:1792368400);gate.finish(token,admission=lambda:1792368400,status_code=200)
    else:
        with gate.ownership() as owner:
            token=gate.begin(admission=lambda:1792368400,ownership=owner,operation='BTC_QUOTE_OBSERVATION')
            if kind=='failed':
                gate.finish(token,admission=lambda:1792368400,status_code=429,retry_after='120',ownership=owner,
                    receipt=dict(operation='BTC_QUOTE_OBSERVATION',request_kinds=['ACCOUNT'],request_sha256=['f'*64],response_sha256=[]))
        if kind=='interrupted':gate.interrupt(token,expected_head=gate.inspect()['head_sha256'],expected_account_id=ACCOUNT,confirm_interrupted=True,admission=lambda:1792368401)
    before=files(tmp_path);r=review(gate,bindings)
    assert r['matched_sessions']==5 and r['all_five_purposes_observed']
    assert not r['receipt_bindings_consistent'] and not r['complete_mixed_history_consistent'] and r['review_reasons']
    assert files(tmp_path)==before


def test_unknown_binding_does_not_invent_a_successful_session(history):
    gate,bindings=history;extra=deepcopy(bindings[0]);extra['after_head_sha256']='f'*64;bindings.append(extra)
    r=review(gate,bindings)
    assert not r['receipt_bindings_consistent'] and 'BINDING_HEAD_NOT_A_SUCCESSFUL_SESSION' in r['review_reasons']


@pytest.mark.parametrize('change',['duplicate','extra_key','bad_head','not_list','too_many','nonfinite','deep','cycle','nonstring_key'])
def test_invalid_bindings_fail_with_a_fixed_code_and_unchanged_files(history,tmp_path,change):
    gate,bindings=history
    if change=='duplicate':bindings.append(deepcopy(bindings[0]))
    if change=='extra_key':bindings[0]['private']='never_echo_private_value'
    if change=='bad_head':bindings[0]['after_head_sha256']=True
    if change=='not_list':bindings=tuple(bindings)
    if change=='too_many':bindings=[bindings[0]]*(pacing.MAX_SESSIONS+1)
    if change=='nonfinite':bindings[0]['normalized_result']['cash']=float('nan')
    if change=='cycle':bindings[0]['normalized_result']=bindings
    if change=='nonstring_key':bindings[0]['normalized_result'][1]='fixture'
    if change=='deep':
        value=[]
        for _ in range(2000):value=[value]
        bindings[0]['normalized_result']=value
    before=files(tmp_path)
    with pytest.raises(ValueError,match='^PAPER_READ_REVIEW_BINDINGS_INVALID$'):review(gate,bindings)
    assert files(tmp_path)==before


@pytest.mark.parametrize('cap',['MAX_BINDING_BYTES','MAX_SCOPE_BYTES','MAX_NODES','MAX_DEPTH'])
def test_independent_serialized_input_caps_are_enforced(history,monkeypatch,cap):
    gate,bindings=history;monkeypatch.setattr(module,cap,1)
    with pytest.raises(ValueError,match='BINDINGS_INVALID'):review(gate,bindings)


def test_missing_history_review_creates_nothing(tmp_path):
    root=tmp_path/'missing';gate=pacing.PaperReadPacing(root/'pacing.sqlite3',ACCOUNT)
    result=module.review(gate,[],expected_head=pacing.ZERO)
    assert not result['receipt_bindings_consistent'] and result['review_reasons']==['NO_RETAINED_HISTORY']
    assert not root.exists()


def test_one_purpose_is_not_complete_mixed_purpose_evidence(history):
    gate,bindings=history
    # Review a real full history: supplying only one purpose cannot hide others.
    result=review(gate,bindings[:1])
    assert result['matched_sessions']==1 and not result['all_five_purposes_observed']
    assert not result['complete_mixed_history_consistent']


@pytest.mark.parametrize('head',[True,'invalid',None])
def test_expected_head_is_strict_and_private(history,head):
    gate,bindings=history
    with pytest.raises(ValueError,match='^PAPER_READ_REVIEW_HEAD_INVALID$'):module.review(gate,bindings,expected_head=head)


def test_stale_or_changed_review_head_fails_without_retry(history,monkeypatch):
    gate,bindings=history;head=gate.inspect()['head_sha256']
    with pytest.raises(ValueError,match='HEAD_CHANGED'):module.review(gate,bindings,expected_head=pacing.ZERO)
    original=gate.operation_history;changed=[]
    def race():
        result=original()
        token=gate.begin(admission=lambda:1792368400);gate.finish(token,admission=lambda:1792368400,status_code=200)
        changed.append(gate.path.read_bytes());return result
    monkeypatch.setattr(gate,'operation_history',race)
    with pytest.raises(ValueError,match='HEAD_CHANGED'):module.review(gate,bindings,expected_head=head)
    assert len(changed)==1 and gate.path.read_bytes()==changed[0]


def test_input_is_frozen_before_journal_callbacks(history,monkeypatch):
    gate,bindings=history;head=gate.inspect()['head_sha256'];original=gate.inspect
    def mutate():bindings[0]['normalized_result']['private_extra']='fixture_mutation';return original()
    monkeypatch.setattr(gate,'inspect',mutate)
    assert module.review(gate,bindings,expected_head=head)['complete_mixed_history_consistent']


def test_fresh_process_review_has_no_vault_or_network_authority(history,tmp_path):
    gate,bindings=history;path=tmp_path/'supplied.json';path.write_text(json.dumps(bindings));before=files(tmp_path)
    script='''import json,sys,socket
from pathlib import Path
from nexus import paper_credentials
from nexus.paper_read_pacing import PaperReadPacing
from nexus.paper_read_review import review
socket.socket=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
paper_credentials._vault=lambda:(_ for _ in ()).throw(AssertionError('VAULT_FORBIDDEN'))
g=PaperReadPacing(sys.argv[1],sys.argv[2])
r=review(g,json.loads(Path(sys.argv[3]).read_text()),expected_head=sys.argv[4])
print(json.dumps(r))
'''
    result=subprocess.run([sys.executable,'-c',script,str(gate.path),ACCOUNT,str(path),gate.inspect()['head_sha256']],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['complete_mixed_history_consistent'] and files(tmp_path)==before


def test_mandatory_fixture_preserves_authority_boundary():
    result=module.fixture_report()
    assert result['passed'] and all(result[k] is True for k in module.CHECKS)
    assert all(result[k] is False for k in module.FLAGS)


def test_two_page_nonempty_activity_scope_revalidates_canonical_content(tmp_path):
    gate,bindings=module._fixture(tmp_path,full_activity=True)
    assert review(gate,bindings)['complete_mixed_history_consistent']
    binding=bindings[2];scope=binding['request_scope']
    assert len(scope['activities'])==100 and len(scope['collection_observation']['pages'])==2
    scope['activities'][0]['amount']='0.02';binding['normalized_result']=sha(scope)
    assert not module._scope_matches(gate.operation_history()[2],binding,gate)
    assert not review(gate,bindings)['receipt_bindings_consistent']


def test_repeated_text_is_bounded_before_serialization(history,monkeypatch):
    gate,bindings=history;bindings[0]['normalized_result']={'repeated':['fixture_text'*100]*2000}
    def forbidden(*args,**kwargs):raise AssertionError('SERIALIZATION_MUST_NOT_START')
    monkeypatch.setattr(module,'canonical',forbidden)
    with pytest.raises(ValueError,match='BINDINGS_INVALID'):review(gate,bindings)
