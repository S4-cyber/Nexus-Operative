"""Recovery acknowledgment must match original ID, local transition and replay."""
from copy import deepcopy
import pytest
from nexus import paper_recovery_step as step
from nexus import paper_credentials as credentials
from nexus import paper_read_pacing as pacing
from nexus.broker_request_journal import BrokerRequestJournal
from test_alpaca_paper_contract import plan,response
from test_alpaca_paper_inspection import ACCOUNT,KEY,SECRET,Opener,Response,account
from test_paper_credentials import vault,save


def prepared(tmp_path):
    journal=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    journal.prepare(plan());journal.mark_submission_uncertain('nx_fixture')
    gate=pacing.PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)
    return journal,gate


def run(journal,gate,*,saved=False,found=True):
    opener=Opener([account(),response(status='filled',filled_qty='2',filled_avg_price='99'),account()] if found else [account(),Response({},status=404)])
    args=dict(expected_account_id=ACCOUNT,confirm_read_only_paper=True,pacing=gate,
        opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50)
    return (step.recover_next_saved(journal,**args) if saved else step.recover_next(journal,KEY,SECRET,**args))


@pytest.mark.parametrize('saved',[False,True])
@pytest.mark.parametrize('change',['snapshot','identity','lookup','different_target','v2','extra_session','journal_tail'])
def test_changed_acknowledgment_is_refused_without_erasing_committed_evidence(tmp_path,monkeypatch,vault,saved,change):
    save();journal,gate=prepared(tmp_path)
    name='recover_saved' if saved else 'recover_original';module=credentials if saved else step
    original=getattr(module,name)
    if change in ('different_target','v2'):
        finish=pacing.PaperReadPacing.finish
        def altered(self,token,**kw):
            receipt=dict(kw['receipt'])
            if change=='v2':receipt.pop('result_projection_sha256',None)
            else:
                targets=list(receipt['request_sha256']);targets[1]='f'*64;receipt['request_sha256']=targets
            return finish(self,token,**dict(kw,receipt=receipt))
        monkeypatch.setattr(pacing.PaperReadPacing,'finish',altered)
    def changed(*args,**kw):
        result=deepcopy(original(*args,**kw))
        if change=='snapshot':result['journal']['snapshot']['filled_quantity']='1'
        if change=='identity':result['journal']['client_order_id']='other'
        if change=='lookup':result['journal']['lookup']['attempts']=2
        if change=='extra_session':
            token=gate.begin(admission=lambda:1060);gate.finish(token,admission=lambda:1060,status_code=200)
        if change=='journal_tail':journal.prepare(plan(client_order_id='newer'))
        return result
    monkeypatch.setattr(module,name,changed)
    with pytest.raises(ValueError,match='PAPER_RECOVERY_STEP_RECEIPT_DISAGREEMENT'):run(journal,gate,saved=saved)
    cold=BrokerRequestJournal(journal.path,ACCOUNT).recovery()
    assert cold['requests'][0]['snapshot']['filled_quantity']=='2'
    assert cold['requests'][0]['lookup']['attempts']==1
    assert pacing.PaperReadPacing(gate.path,ACCOUNT).inspect()['pending_token'] is None
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    journal.recovery();gate.operation_history()
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize('saved',[False,True])
@pytest.mark.parametrize('found',[False,True])
def test_matching_found_or_not_found_stays_one_step_without_authority(tmp_path,vault,saved,found):
    save();journal,gate=prepared(tmp_path);result=run(journal,gate,saved=saved,found=found)
    assert result['selected_client_order_id']=='nx_fixture'
    assert all(result[key] is False for key in step.FLAGS)
    record=gate.operation_history()[0]
    assert record['outcome']==('SUCCESS' if found else 'NOT_FOUND')
    assert record['normalized_result_bound'] is found


@pytest.mark.parametrize('change',['identity','lookup','target','boolean_attempt'])
def test_not_found_does_not_bypass_identity_outcome_or_target_checks(tmp_path,monkeypatch,change):
    journal,gate=prepared(tmp_path);original=step.recover_original
    if change=='target':
        finish=pacing.PaperReadPacing.finish
        def altered(self,token,**kw):
            receipt=dict(kw['receipt'],request_sha256=['f'*64]*2)
            return finish(self,token,**dict(kw,receipt=receipt))
        monkeypatch.setattr(pacing.PaperReadPacing,'finish',altered)
    def changed(*args,**kw):
        result=deepcopy(original(*args,**kw))
        if change=='identity':result['journal']['client_order_id']='other'
        if change=='lookup':result['journal']['lookup']['last_outcome']='FOUND'
        if change=='boolean_attempt':result['journal']['lookup']['attempts']=True
        return result
    monkeypatch.setattr(step,'recover_original',changed)
    with pytest.raises(ValueError,match='RECEIPT_DISAGREEMENT'):run(journal,gate,found=False)
    assert journal.recovery()['requests'][0]['lookup']['last_outcome']=='NOT_FOUND'
    assert gate.operation_history()[0]['normalized_result_bound'] is False


@pytest.mark.parametrize('status,outcome',[(401,'AUTH_DENIED'),(403,'AUTH_DENIED'),(429,'RATE_LIMITED'),(500,'SERVER_ERROR')])
def test_failed_lookup_preserves_real_outcome_and_never_claims_normalized_success(tmp_path,status,outcome):
    journal,gate=prepared(tmp_path)
    result=step.recover_next(journal,KEY,SECRET,expected_account_id=ACCOUNT,confirm_read_only_paper=True,pacing=gate,
        opener=Opener([account(),Response({},status=status)]),wall_clock=lambda:1000,elapsed_clock=lambda:50)
    assert result['recovery']['journal']['lookup']['last_outcome']==outcome
    record=gate.operation_history()[0]
    assert record['outcome']==outcome and not record['normalized_result_bound']
    assert all(result[key] is False for key in step.FLAGS)


def test_interruption_still_retains_both_pending_journals(tmp_path):
    journal,gate=prepared(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        step.recover_next(journal,KEY,SECRET,expected_account_id=ACCOUNT,confirm_read_only_paper=True,pacing=gate,
            opener=Opener([account(),KeyboardInterrupt()]),wall_clock=lambda:1000,elapsed_clock=lambda:50)
    assert journal.recovery()['requests'][0]['lookup']['pending']
    assert gate.inspect()['pending_token'] is not None
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()};reader=Opener()
    with pytest.raises(ValueError,match='LOCAL_REVIEW_REQUIRED'):
        step.recover_next(journal,KEY,SECRET,expected_account_id=ACCOUNT,confirm_read_only_paper=True,pacing=gate,
            opener=reader,wall_clock=lambda:2000,elapsed_clock=lambda:50)
    assert not reader.requests and before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize('phase',['before','after'])
def test_composition_detects_append_between_sampled_replays(tmp_path,monkeypatch,phase):
    journal,gate=prepared(tmp_path);original=gate.inspect;count=0
    def race():
        nonlocal count
        state=original();count+=1
        # review_next takes two samples; _receipt_before takes two more.
        if phase=='before' and count==4:journal.prepare(plan(client_order_id='newer'))
        if phase=='after' and state['sessions']==1 and state['pending_token'] is None:
            if not any(row['client_order_id']=='newer' for row in journal.recovery()['requests']):
                journal.prepare(plan(client_order_id='newer'))
        return state
    monkeypatch.setattr(gate,'inspect',race)
    with pytest.raises(ValueError,match='RECEIPT_DISAGREEMENT'):run(journal,gate)
    assert any(row['client_order_id']=='newer' for row in journal.recovery()['requests'])
