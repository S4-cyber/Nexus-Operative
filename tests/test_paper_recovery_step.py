"""Bounded next-original-ID recovery; disposable/injected history and HTTPS only."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest
from nexus import paper_recovery_step as module,paper_credentials as credentials
from nexus import broker_request_journal as storage
from test_alpaca_paper_contract import plan,response
from test_alpaca_paper_inspection import Opener,Response,account,ACCOUNT,OTHER,KEY,SECRET
from test_paper_credentials import vault,save


@pytest.fixture
def journal(tmp_path):
    j=storage.BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    for identity in ('nx_fixture','second'):
        j.prepare(plan(client_order_id=identity));j.mark_submission_uncertain(identity)
    return j


def step(j,now=1000,opener=None,**kw):
    return module.recover_next(j,KEY,SECRET,expected_account_id=ACCOUNT,confirm_read_only_paper=True,
        opener=opener or Opener([account(),Response({},status=404)]),wall_clock=lambda:now,elapsed_clock=lambda:50,**kw)


def test_missing_selection_is_passive_and_never_implies_lookup_permission(tmp_path):
    root=tmp_path/'missing';j=storage.BrokerRequestJournal(root/'r.sqlite3',ACCOUNT)
    r=j.lookup_review(evaluated_at=1000)
    assert not root.exists() and r['selected_client_order_id'] is None
    assert not r['lookup_admitted'] and not r['admission_reserved']


def test_one_step_reads_original_id_once_and_next_due_request_gets_attention(journal):
    before=journal.path.read_bytes();plan=journal.lookup_review(evaluated_at=1000)
    assert journal.path.read_bytes()==before and plan['selected_client_order_id']=='nx_fixture'
    opener=Opener([account(),Response({},status=404)])
    result=step(journal,opener=opener)
    assert result['selected_client_order_id']=='nx_fixture' and result['lookup_attempts_this_step']==1
    assert len(opener.requests)==2 and opener.requests[1][0].full_url.endswith('client_order_id=nx_fixture')
    assert all(r.method=='GET' for r,_ in opener.requests) and all(result[k] is False for k in module.FLAGS)
    before=journal.path.read_bytes();blocked=Opener()
    with pytest.raises(ValueError,match='LOCAL_REVIEW_REQUIRED'):step(journal,now=1004,opener=blocked)
    assert blocked.requests==[] and journal.path.read_bytes()==before
    second=Opener([account(),Response({},status=404)])
    assert step(journal,now=1005,opener=second)['selected_client_order_id']=='second'
    assert len(second.requests)==2


def test_prepared_terminal_and_capped_requests_are_skipped_without_deletion(journal):
    journal.prepare(plan(client_order_id='unsent'))
    journal.observe('nx_fixture',response(status='canceled'))
    r=journal.lookup_review(evaluated_at=1000)
    assert r['skipped_prepared']==1 and r['skipped_terminal']==1 and r['selected_client_order_id']=='second'
    assert len(journal.recovery()['requests'])==3


@pytest.mark.parametrize('outcome',['pending','auth','rate','clock','capacity'])
def test_local_blockers_stop_before_saved_vault_or_provider(journal,vault,monkeypatch,outcome):
    now=1000
    if outcome in ('pending','auth','rate','clock'):
        token=journal.begin_lookup('nx_fixture',evaluated_at=1000)['lookup_token']
        if outcome!='pending':journal.finish_lookup('nx_fixture',token,evaluated_at=1001,status_code=401 if outcome=='auth' else 429 if outcome=='rate' else 404,retry_after='30' if outcome=='rate' else None)
        now=2000 if outcome=='auth' else 1020 if outcome=='rate' else 1000
    else:monkeypatch.setattr(storage,'MAX_EVENTS',journal.recovery()['retained_events']+3)
    before=journal.path.read_bytes();opener=Opener()
    with pytest.raises(ValueError,match='LOCAL_REVIEW_REQUIRED'):
        module.recover_next_saved(journal,expected_account_id=ACCOUNT,confirm_read_only_paper=True,opener=opener,wall_clock=lambda:now,elapsed_clock=lambda:50)
    assert vault.reads==0 and not opener.requests and journal.path.read_bytes()==before


def test_exhausted_request_is_retained_and_never_rearmed(tmp_path):
    j=storage.BrokerRequestJournal(tmp_path/'r.sqlite3',ACCOUNT);j.prepare(plan());j.mark_submission_uncertain('nx_fixture')
    now=1000
    for _ in range(4):
        r=step(j,now=now);now=r['recovery']['journal']['lookup']['next_allowed_at']
    before=j.path.read_bytes();r=j.lookup_review(evaluated_at=now)
    assert r['skipped_attempt_cap']==1 and r['selected_client_order_id'] is None
    with pytest.raises(ValueError,match='LOCAL_REVIEW_REQUIRED'):step(j,now=now)
    assert j.path.read_bytes()==before


def test_found_terminal_snapshot_is_retained_without_ledger_or_resubmit_permission(journal):
    opener=Opener([account(),response(status='filled',filled_qty='2',filled_avg_price='99'),account()])
    result=step(journal,opener=opener)
    assert len(opener.requests)==3 and result['recovery']['journal']['snapshot']['terminal']
    assert journal.lookup_review(evaluated_at=1001)['skipped_terminal']==1
    assert result['ledger_applied'] is False and result['automatic_submission'] is False


def test_selection_head_is_rechecked_under_write_lock_before_any_provider_io(journal,monkeypatch):
    original=journal.begin_lookup_checked
    def race(*args,**kwargs):
        journal.prepare(plan(client_order_id='newer'))
        return original(*args,**kwargs)
    monkeypatch.setattr(journal,'begin_lookup_checked',race);opener=Opener()
    with pytest.raises(ValueError):step(journal,opener=opener)
    assert opener.requests==[]
    r=journal.recovery();assert len(r['requests'])==3 and all(r['lookup']['attempts']==0 for r in r['requests'])


def test_stale_direct_checked_reference_does_not_invoke_admission(journal):
    head=journal.recovery()['head_sha256'];journal.prepare(plan(client_order_id='newer'));before=journal.path.read_bytes();calls=[]
    with pytest.raises(ValueError,match='HEAD_CHANGED'):
        journal.begin_lookup_checked('nx_fixture',expected_head=head,admission=lambda:calls.append(1) or 1000)
    # The public hook samples once before lock; the lock-time callback is vetoed.
    assert calls==[1] and journal.path.read_bytes()==before


def test_saved_step_keeps_final_vault_readback_and_selects_no_second_request(journal,vault):
    save();opener=Opener([account(),Response({},status=404)])
    result=module.recover_next_saved(journal,expected_account_id=ACCOUNT,confirm_read_only_paper=True,opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50)
    assert result['lookup_attempts_this_step']==1 and len(opener.requests)==2 and vault.reads>=2
    assert all(value not in json.dumps(result) for value in (KEY,SECRET))


def test_explicit_confirmation_required_before_any_state_or_vault_work(journal,vault):
    before=journal.path.read_bytes()
    with pytest.raises(ValueError,match='CONFIRMATION_REQUIRED'):module.recover_next_saved(journal,expected_account_id=ACCOUNT)
    assert vault.reads==0 and journal.path.read_bytes()==before


def test_cli_local_review_is_vault_silent_and_creates_no_storage(tmp_path):
    root=tmp_path/'missing'
    result=subprocess.run([sys.executable,'-m','nexus.paper_recovery_step','--expected-account',ACCOUNT,'--journal',str(root/'r.sqlite3'),'--pacing',str(root/'p.sqlite3'),'--review-only'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    r=json.loads(result.stdout);assert r['read_only'] and r['selection']['selected_client_order_id'] is None
    assert r['pacing']['journal_present'] is False and not root.exists()


def test_cli_rejects_aliases_and_secret_arguments_with_fixed_errors(tmp_path):
    p=str(tmp_path/'same.sqlite3');base=[sys.executable,'-m','nexus.paper_recovery_step','--expected-account',ACCOUNT,'--journal',p,'--pacing',p,'--review-only']
    result=subprocess.run(base,capture_output=True,text=True)
    assert result.returncode==2 and result.stdout.strip()=='PAPER_RECOVERY_STEP_FAILED'
    result=subprocess.run(base+['--api-secret','PRIVATE_VALUE'],capture_output=True,text=True)
    assert result.returncode==2 and 'PRIVATE_VALUE' not in result.stderr+result.stdout


def test_interrupted_io_keeps_pending_and_prevents_another_step(journal,vault):
    opener=Opener([account(),KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):step(journal,opener=opener)
    assert journal.pending_lookup()['pending']
    with pytest.raises(ValueError,match='LOCAL_REVIEW_REQUIRED'):
        module.recover_next_saved(journal,expected_account_id=ACCOUNT,confirm_read_only_paper=True,wall_clock=lambda:2000)
    assert vault.reads==0


def test_lost_result_return_preserves_committed_observation_and_does_not_rearm(journal,monkeypatch):
    finish=journal.finish_lookup_checked
    def lose(*args,**kw):finish(*args,**kw);raise RuntimeError('LOST_RETURN_FIXTURE')
    monkeypatch.setattr(journal,'finish_lookup_checked',lose)
    with pytest.raises(ValueError):step(journal,opener=Opener([account(),response(status='partially_filled',filled_qty='1',filled_avg_price='99'),account()]))
    cold=storage.BrokerRequestJournal(journal.path,ACCOUNT);r=cold.recovery()['requests'][0]
    assert r['snapshot']['filled_quantity']=='1' and r['lookup']['attempts']==1 and not r['lookup']['pending']
    assert cold.lookup_review(evaluated_at=1001)['selected_client_order_id']=='second'


def test_disposable_mandatory_step_fixture():
    r=module.fixture_report();assert r['passed'] and r['get_requests']==4 and r['stale_head_prevented_io']
    assert all(r[k] is False for k in module.FLAGS)


@pytest.mark.parametrize('outcome',['pending','auth','cooldown','clock','capacity'])
def test_shared_pacing_blocks_before_saved_vault_even_with_local_candidate(journal,vault,monkeypatch,tmp_path,outcome):
    from nexus import paper_read_pacing as pacing_module
    gate=pacing_module.PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)
    token=gate.begin(admission=lambda:1000);now=1060
    if outcome!='pending':
        gate.finish(token,admission=lambda:1000,status_code=401 if outcome=='auth' else 200)
        if outcome=='cooldown':now=1059
        if outcome=='clock':now=999
        if outcome=='capacity':monkeypatch.setattr(pacing_module,'MAX_SESSIONS',1)
    assert journal.lookup_review(evaluated_at=now)['selected_client_order_id']=='nx_fixture'
    before=(journal.path.read_bytes(),gate.path.read_bytes());reader=Opener()
    review=module.review_next(journal,expected_account_id=ACCOUNT,evaluated_at=now,pacing=gate)
    assert not review['local_checks_clear'] and review['review_reasons']
    with pytest.raises(ValueError,match='LOCAL_REVIEW_REQUIRED'):
        module.recover_next_saved(journal,expected_account_id=ACCOUNT,confirm_read_only_paper=True,
            opener=reader,wall_clock=lambda:now,elapsed_clock=lambda:50,pacing=gate)
    assert vault.reads==0 and not reader.requests and before==(journal.path.read_bytes(),gate.path.read_bytes())


def test_shared_review_preserves_missing_pacing_and_never_reserves_admission(journal,tmp_path):
    from nexus.paper_read_pacing import PaperReadPacing
    gate=PaperReadPacing(tmp_path/'absent'/'p.sqlite3',ACCOUNT)
    r=module.review_next(journal,expected_account_id=ACCOUNT,evaluated_at=1000,pacing=gate)
    assert r['local_checks_clear'] and not gate.path.parent.exists()
    assert r['admission_reserved'] is False and all(r[k] is False for k in module.FLAGS)


@pytest.mark.parametrize('kind',['foreign','wrong-type','corrupt'])
def test_invalid_pacing_scope_or_content_denied_before_vault(journal,vault,tmp_path,kind):
    from nexus.paper_read_pacing import PaperReadPacing
    gate=PaperReadPacing(tmp_path/'p.sqlite3',OTHER if kind=='foreign' else ACCOUNT)
    if kind=='wrong-type':gate=object()
    if kind=='corrupt':gate.path.write_bytes(b'not a journal')
    with pytest.raises((ValueError,sqlite3.Error)):
        module.recover_next_saved(journal,expected_account_id=ACCOUNT,confirm_read_only_paper=True,wall_clock=lambda:1000,pacing=gate)
    assert vault.reads==0


def test_composed_review_rejects_request_append_between_snapshots(journal,tmp_path,monkeypatch):
    from nexus.paper_read_pacing import PaperReadPacing
    gate=PaperReadPacing(tmp_path/'p.sqlite3',ACCOUNT);original=gate.inspect;changed=False
    def race():
        nonlocal changed
        if not changed:journal.prepare(plan(client_order_id='appended'));changed=True
        return original()
    monkeypatch.setattr(gate,'inspect',race)
    with pytest.raises(ValueError,match='REVIEW_CHANGED'):
        module.review_next(journal,expected_account_id=ACCOUNT,evaluated_at=1000,pacing=gate)
    assert not gate.path.exists() and all(row['lookup']['attempts']==0 for row in journal.recovery()['requests'])


def test_composed_review_rejects_pacing_append_between_snapshots(journal,tmp_path,monkeypatch):
    from nexus.paper_read_pacing import PaperReadPacing
    gate=PaperReadPacing(tmp_path/'p.sqlite3',ACCOUNT);original=gate.inspect;calls=0
    def race():
        nonlocal calls
        calls+=1
        if calls==2:gate.begin(admission=lambda:1000)
        return original()
    monkeypatch.setattr(gate,'inspect',race)
    with pytest.raises(ValueError,match='REVIEW_CHANGED'):
        module.review_next(journal,expected_account_id=ACCOUNT,evaluated_at=1000,pacing=gate)
    assert gate.inspect()['pending_token'] is not None and all(row['lookup']['attempts']==0 for row in journal.recovery()['requests'])
