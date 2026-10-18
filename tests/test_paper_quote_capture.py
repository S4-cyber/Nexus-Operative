"""Confirmed saved-pair quote capture, local bindings and vault-silent review."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from nexus import paper_quote_capture as subject,paper_quote_store as quotes,paper_credentials as credentials
from test_paper_quote_store import setup,snapshot
from test_paper_quote_observation import options,ACCOUNT,KEY,SECRET,quote,EPOCH
from test_alpaca_paper_inspection import Opener,account
from test_paper_credentials import vault,save


def args(tmp_path):
    store,value,retention=setup(tmp_path)
    # setup's injected quote used an independent fixture journal. Production
    # capture uses this selected absent quote budget, never bypasses that journal.
    return store,dict(expected_account_id=ACCOUNT,request_journal_path=store.request_path,quote_store_path=store.path,
        pacing_path=tmp_path/'capture-pacing.sqlite3',expected_request_head=retention['expected_request_head'],
        expected_quote_head=quotes.ZERO,expected_pacing_head=quotes.ZERO,client_order_id=retention['client_order_id'],
        confirm_read_only_paper=True,opener=Opener([account(),quote(),account()]),
        wall_clock=lambda:EPOCH+1,elapsed_clock=lambda:50)


def status_options(a):return {k:a[k] for k in ('expected_account_id','request_journal_path','quote_store_path','pacing_path','expected_request_head')}


def test_explicit_saved_capture_and_restart_status(vault,tmp_path):
    save();store,a=args(tmp_path);original=store.request_path.read_bytes()
    r=subject.capture(**a);before=snapshot(tmp_path)
    status=subject.status(**status_options(a));records=store.inspect(expected_request_head=a['expected_request_head'])['records']
    assert r['fixed_get_requests']==3 and len(a['opener'].requests)==3
    assert r['local_capture_history_agreement'] is True and status['local_capture_history_agreement'] is True
    assert records[0]['profile']==quotes.CAPTURE_PROFILE
    assert records[0]['pacing_after_head_sha256']==r['pacing_after_head_sha256']==status['pacing_head_sha256']
    assert records[0]['pacing_before_head_sha256']==quotes.ZERO
    assert store.request_path.read_bytes()==original and snapshot(tmp_path)==before
    assert all(r[k] is False and status[k] is False for k in subject.FLAGS)
    assert all(x not in json.dumps(r) for x in (KEY,SECRET,'cash','limit_price',str(tmp_path)))


@pytest.mark.parametrize('change',('confirmation','request_head','quote_head','pacing_head','unknown_request','alias',
    'pacing_owner_alias','quote_rollback_alias','request_wal_alias','source_path','capacity','corrupt'))
def test_denial_before_vault_or_network_or_file_creation(vault,tmp_path,monkeypatch,change):
    save();store,a=args(tmp_path)
    if change=='confirmation':a['confirm_read_only_paper']=False
    if change=='request_head':a['expected_request_head']='f'*64
    if change=='quote_head':a['expected_quote_head']='f'*64
    if change=='pacing_head':a['expected_pacing_head']='f'*64
    if change=='unknown_request':a['client_order_id']='nx_unknown'
    if change=='alias':a['pacing_path']=store.path
    if change=='pacing_owner_alias':a['quote_store_path']=Path(str(a['pacing_path'])+'.owner.lock')
    if change=='quote_rollback_alias':a['pacing_path']=Path(str(a['quote_store_path'])+'-journal')
    if change=='request_wal_alias':a['quote_store_path']=Path(str(a['request_journal_path'])+'-wal')
    if change=='source_path':a['quote_store_path']=Path(subject.__file__).parent/'capture.sqlite3'
    if change=='capacity':monkeypatch.setattr(quotes,'MAX_RECORDS',0)
    if change=='corrupt':store.path.write_bytes(b'PRIVATE CORRUPT')
    before=snapshot(tmp_path);reads=vault.reads
    with pytest.raises((ValueError,sqlite3.Error)):subject.capture(**a)
    assert vault.reads==reads and not a['opener'].requests and snapshot(tmp_path)==before


def test_successful_cooldown_refuses_before_vault(vault,tmp_path):
    save();_,a=args(tmp_path);r=subject.capture(**a)
    a.update(expected_quote_head=r['quote_head_sha256'],expected_pacing_head=r['pacing_after_head_sha256'],opener=Opener([account(),quote(),account()]))
    before=snapshot(tmp_path);reads=vault.reads
    with pytest.raises(ValueError,match='COOLDOWN'):subject.capture(**a)
    assert vault.reads==reads and not a['opener'].requests and snapshot(tmp_path)==before


def test_pending_malformed_read_is_unbound_and_cannot_auto_continue(vault,tmp_path):
    save();_,a=args(tmp_path);a['opener']=Opener([account(),quote(bp='102')])
    with pytest.raises(ValueError):subject.capture(**a)
    before=snapshot(tmp_path);r=subject.status(**status_options(a));reads=vault.reads
    assert r['pending_read'] and not r['local_capture_history_agreement'] and r['retained_quotes']==0
    with pytest.raises(ValueError,match='UNBOUND_HISTORY'):subject.capture(**a)
    assert vault.reads==reads and snapshot(tmp_path)==before


def test_request_changes_during_read_leave_pending_without_quote(vault,tmp_path):
    save();store,a=args(tmp_path);opener=a['opener'];original=opener.open
    def changed(req,timeout):
        result=original(req,timeout)
        if len(opener.requests)==2:store.journal.mark_submission_uncertain(a['client_order_id'])
        return result
    opener.open=changed
    with pytest.raises(ValueError):subject.capture(**a)
    assert len(opener.requests)==2 and not store.path.exists()
    from nexus.paper_read_pacing import PaperReadPacing
    assert PaperReadPacing(a['pacing_path'],ACCOUNT).inspect()['pending_token'] is not None


def test_key_removal_during_quote_revokes_and_keeps_pending(vault,tmp_path):
    save();store,a=args(tmp_path);opener=a['opener'];original=opener.open
    def revoked(req,timeout):
        result=original(req,timeout)
        if len(opener.requests)==2:credentials.remove(confirm_remove=True)
        return result
    opener.open=revoked
    with pytest.raises(ValueError):subject.capture(**a)
    assert len(opener.requests)==2 and not store.path.exists()
    assert not subject.status(**status_options(a))['local_capture_history_agreement']


def test_pair_replaced_before_retention_never_acknowledges(vault,tmp_path,monkeypatch):
    save();store,a=args(tmp_path);original=store.__class__.retain
    def replaced(self,*values,**kw):
        credentials.install(KEY+'replacement',SECRET+'replacement',confirm_store=True)
        return original(self,*values,**kw)
    monkeypatch.setattr(store.__class__,'retain',replaced)
    with pytest.raises(ValueError):subject.capture(**a)
    assert not store.path.exists()
    r=subject.status(**status_options(a))
    assert r['retained_pacing_sessions']==1 and not r['local_capture_history_agreement']


def test_quote_commit_lost_ack_keeps_binding_for_offline_status(vault,tmp_path,monkeypatch):
    save();store,a=args(tmp_path);original=store.__class__.retain
    def lost(self,*values,**kw):
        original(self,*values,**kw)
        raise ValueError('INJECTED_LOST_ACK')
    monkeypatch.setattr(store.__class__,'retain',lost)
    with pytest.raises(ValueError):subject.capture(**a)
    before=snapshot(tmp_path);reads=vault.reads;r=subject.status(**status_options(a))
    assert r['retained_quotes']==1 and r['local_capture_history_agreement']
    assert vault.reads==reads and snapshot(tmp_path)==before


def test_quote_commit_then_key_removal_loses_ack_but_preserves_binding(vault,tmp_path,monkeypatch):
    save();store,a=args(tmp_path);original=store.__class__.retain
    def revoked(self,*values,**kw):
        result=original(self,*values,**kw);credentials.remove(confirm_remove=True);return result
    monkeypatch.setattr(store.__class__,'retain',revoked)
    with pytest.raises(ValueError):subject.capture(**a)
    assert subject.status(**status_options(a))['local_capture_history_agreement']
    assert store.inspect(expected_request_head=a['expected_request_head'])['retained_records']==1


def test_legacy_supplied_records_are_not_invented_capture_receipts(vault,tmp_path):
    save();store,a=args(tmp_path)
    from nexus.paper_quote_observation import inspect_quote
    value=inspect_quote(KEY,SECRET,**options(tmp_path/'supplied'))
    r=store.retain(value,client_order_id=a['client_order_id'],expected_request_head=a['expected_request_head'],expected_quote_head=quotes.ZERO)
    a['expected_quote_head']=r['head_sha256'];before=snapshot(tmp_path);reads=vault.reads
    review=subject.status(**status_options(a));assert 'SUPPLIED_QUOTES_WITHOUT_CAPTURE_BINDING' in review['review_reasons']
    with pytest.raises(ValueError,match='UNBOUND_HISTORY'):subject.capture(**a)
    assert vault.reads==reads and snapshot(tmp_path)==before


def test_unrelated_pacing_session_is_not_accepted_quote(vault,tmp_path):
    save();_,a=args(tmp_path)
    from nexus.paper_read_pacing import PaperReadPacing
    gate=PaperReadPacing(a['pacing_path'],ACCOUNT);token=gate.begin(admission=lambda:EPOCH)
    gate.finish(token,admission=lambda:EPOCH,status_code=200)
    before=snapshot(tmp_path);reads=vault.reads
    with pytest.raises(ValueError,match='UNBOUND_HISTORY'):subject.capture(**a)
    assert not subject.status(**status_options(a))['local_capture_history_agreement']
    assert vault.reads==reads and snapshot(tmp_path)==before


def test_pacing_scope_path_cannot_be_swapped_to_copied_history(vault,tmp_path):
    save();_,a=args(tmp_path);subject.capture(**a)
    copied=tmp_path/'copied.sqlite3';copied.write_bytes(Path(a['pacing_path']).read_bytes())
    a['pacing_path']=copied
    assert 'PACING_SCOPE_DISAGREEMENT' in subject.status(**status_options(a))['review_reasons']


def test_status_only_fresh_process_has_no_network_vault_or_writes(vault,tmp_path):
    save();_,a=args(tmp_path);subject.capture(**a);before=snapshot(tmp_path)
    script='''import json,socket,ssl,sys
socket.socket=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK'))
from nexus import paper_credentials
paper_credentials._capture=lambda:(_ for _ in ()).throw(AssertionError('VAULT'))
from nexus.paper_quote_capture import status
r=status(**json.loads(sys.argv[1]));print(json.dumps(dict(agreement=r['local_capture_history_agreement'],count=r['retained_quotes'])))'''
    result=subprocess.run([sys.executable,'-c',script,json.dumps({k:str(v) for k,v in status_options(a).items()})],capture_output=True,text=True,timeout=30)
    assert result.returncode==0 and json.loads(result.stdout)==dict(agreement=True,count=1)
    assert snapshot(tmp_path)==before


def test_two_captures_keep_one_receipt_per_pacing_session(vault,tmp_path):
    save();store,a=args(tmp_path);first=subject.capture(**a)
    a.update(expected_quote_head=first['quote_head_sha256'],expected_pacing_head=first['pacing_after_head_sha256'],
        wall_clock=lambda:EPOCH+61,opener=Opener([account(),quote(t='2026-10-17T00:01:00.123456789Z'),account()]))
    second=subject.capture(**a);r=subject.status(**status_options(a))
    records=store.inspect(expected_request_head=a['expected_request_head'])['records']
    assert r['local_capture_history_agreement'] and r['retained_quotes']==r['retained_pacing_sessions']==2
    assert records[1]['pacing_before_head_sha256']==first['pacing_after_head_sha256']
    assert r['quote_head_sha256']==second['quote_head_sha256']


def test_changed_pacing_head_before_read_ownership_blocks_gets(vault,tmp_path,monkeypatch):
    save();store,a=args(tmp_path);original=credentials._capture;calls=[]
    def changed():
        result=original();calls.append(1)
        if len(calls)==1:
            from nexus.paper_read_pacing import PaperReadPacing
            gate=PaperReadPacing(a['pacing_path'],ACCOUNT);token=gate.begin(admission=lambda:EPOCH)
            gate.finish(token,admission=lambda:EPOCH,status_code=200)
        return result
    monkeypatch.setattr(credentials,'_capture',changed)
    with pytest.raises(ValueError,match='PACING_HEAD_CHANGED'):subject.capture(**a)
    assert not a['opener'].requests and not store.path.exists()
    assert not subject.status(**status_options(a))['local_capture_history_agreement']


@pytest.mark.parametrize('boundary',('before_quote_commit','after_quote_commit'))
def test_process_death_between_completed_read_and_acknowledgment(tmp_path,boundary):
    child_root=tmp_path/'child'
    script='''import os,sys,warnings
warnings.filterwarnings('ignore',message='Using .*httpx.* with .*starlette.testclient.* is deprecated.*')
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_paper_quote_capture import args
from test_paper_credentials import Vault,save
from nexus import paper_credentials as credentials,paper_quote_store as quotes,paper_quote_capture as subject
credentials._vault=lambda:fake
fake=Vault();save();store,a=args(Path(sys.argv[1]))
original=quotes.PaperQuoteStore.retain
def killed(self,*values,**kw):
 if sys.argv[2]=='before_quote_commit':os._exit(73)
 original(self,*values,**kw);os._exit(73)
quotes.PaperQuoteStore.retain=killed
subject.capture(**a)
'''
    child_root.mkdir()
    result=subprocess.run([sys.executable,'-c',script,str(child_root),boundary],capture_output=True,timeout=30)
    assert result.returncode==73 and not result.stdout and not result.stderr
    from nexus.broker_request_journal import BrokerRequestJournal
    head=BrokerRequestJournal(child_root/'requests.sqlite3',ACCOUNT).recovery()['head_sha256'];before=snapshot(child_root)
    r=subject.status(expected_account_id=ACCOUNT,request_journal_path=child_root/'requests.sqlite3',quote_store_path=child_root/'quotes.sqlite3',
        pacing_path=child_root/'capture-pacing.sqlite3',expected_request_head=head)
    assert r['retained_pacing_sessions']==1 and not r['pending_read']
    if boundary=='before_quote_commit':assert r['retained_quotes']==0 and not r['local_capture_history_agreement']
    else:assert r['retained_quotes']==1 and r['local_capture_history_agreement']
    assert snapshot(child_root)==before


@pytest.mark.parametrize('field,value,code',[
    ('one_local_transition_bound',False,'BTC_QUOTE_CAPTURE'),
    ('capture_source_attested',True,'BTC_QUOTE_CAPTURE_AUTHORITY'),
    ('ready_for_routing',True,'BTC_QUOTE_CAPTURE_AUTHORITY')])
def test_mandatory_release_capture_guards(field,value,code):
    from nexus import release_trials,paper_quote_observation
    r=dict(passed=True,btc_quote_observation=paper_quote_observation.fixture_report(),btc_quote_retention=quotes.fixture_report(),btc_quote_capture=subject.fixture_report())
    r['btc_quote_capture'][field]=value
    with pytest.raises(ValueError,match='RELEASE_TRIAL_'+code):release_trials._validate('inspection_inventory',r)


def test_capture_fixture_is_aggregate_only():
    r=subject.fixture_report()
    assert r['passed'] is True and all(r[k] is True for k in subject.CHECKS)
    assert all(type(v) is bool or k=='source' for k,v in r.items())


@pytest.mark.parametrize('arguments',[
    ['--expected-account','PRIVATE_BAD_ARG'],
    ['--expected-account',ACCOUNT,'--request-journal','PRIVATE_PATH','--quote-store','PRIVATE_PATH','--pacing','PRIVATE_PATH','--request-head','f'*64],
])
def test_cli_errors_never_echo_private_values(arguments):
    result=subprocess.run([sys.executable,'-m','nexus.paper_quote_capture',*arguments],capture_output=True,text=True,timeout=30)
    assert result.returncode==2
    assert result.stdout+result.stderr in ('PAPER_QUOTE_CAPTURE_USAGE_ERROR\n','PAPER_QUOTE_CAPTURE_FAILED\n')
