"""Real local pacing and injected vault/HTTPS; no actual account/key enrollment."""
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from email.message import Message

import pytest
from nexus import paper_acceptance_probe as probe, paper_credentials as credentials
from nexus.paper_read_pacing import PaperReadPacing
from test_paper_credentials import vault, save
from test_alpaca_paper_inspection import ACCOUNT, OTHER, Opener, KEY, SECRET


def run(path,opener=None,at=1000,**changes):
    args=dict(expected_account_id=ACCOUNT,pacing_path=path,confirm_read_only_paper=True,
        opener=opener or Opener(),wall_clock=lambda:at,elapsed_clock=lambda:50)
    args.update(changes);return probe.probe(**args)


def test_real_pacing_saved_pair_and_inventory_are_bound_without_secrets(vault,tmp_path):
    save();path=tmp_path/'private'/'pacing.sqlite3';opener=Opener();result=run(path,opener)
    assert len(opener.requests)==4 and result['status']=='REVIEW_REQUIRED' and result['read_completed']
    assert result['source']=='INJECTED_UNATTESTED' and result['portable_observation']['account_id']==ACCOUNT
    assert result['local_pacing']['sessions']==1 and result['local_pacing']['events']==2
    assert result['local_pacing']['next_allowed_at']==1060
    assert all(result[k] is False for k in probe.FLAGS)
    assert not result['inventory_only'] and result['open_order_terms_included']
    terms=result['reported_open_ioc_limit_terms']
    assert terms['reported_rows']==result['portable_observation']['open_order_count']==0
    assert terms['terms_complete'] and terms['remaining_buy_limit_principal_cap_usd']=='0'
    assert terms['remaining_sell_quantity_btc']=='0'
    scope=result['acceptance_record_scope']
    assert scope['observation_manifest_sha256']==result['portable_observation']['manifest_sha256']
    assert scope['open_order_terms_sha256']==terms['summary_sha256']
    from nexus.alpaca_paper_contract import sha
    assert result['acceptance_record_sha256']==sha(scope)
    encoded=json.dumps(result)
    assert all(value not in encoded for value in (KEY,SECRET,str(path)))
    assert len(list(path.parent.glob('*.sqlite3')))==1


def test_probe_reports_allowlisted_open_terms_without_raw_order_identity(vault,tmp_path):
    from test_paper_open_order_terms import order
    save();path=tmp_path/'private'/'pacing.sqlite3'
    buy=order(id='01234567-89ab-cdef-0123-456789abcde1',client_order_id='private_client_buy',
        side='buy',qty='3',limit_price='100',filled_qty='0.5',filled_avg_price='99',status='pending_cancel')
    sell=order(id='01234567-89ab-cdef-0123-456789abcde2',client_order_id='private_client_sell',
        side='sell',qty='1',limit_price='110',filled_qty='0.5',filled_avg_price='111',status='partially_filled')
    opener=Opener();opener.values[2]=[buy,sell]
    result=run(path,opener)
    terms=result['reported_open_ioc_limit_terms']
    assert terms['reported_rows']==2 and terms['supported_unique_orders']==2 and terms['terms_complete']
    assert terms['remaining_buy_limit_principal_cap_usd']=='250'
    assert terms['remaining_sell_quantity_btc']=='0.5'
    encoded=json.dumps(result)
    assert all(value not in encoded for value in ('private_client_buy','private_client_sell',buy['id'],sell['id']))


def test_probe_fails_closed_if_inspector_terms_are_not_count_bound(vault,tmp_path,monkeypatch):
    save();path=tmp_path/'private'/'pacing.sqlite3';original=credentials.inspect_saved
    def changed(**kwargs):
        result=original(**kwargs)
        result['open_order_terms']=dict(result['open_order_terms'],reported_rows=1)
        return result
    monkeypatch.setattr(credentials,'inspect_saved',changed)
    with pytest.raises(ValueError,match='PAPER_ACCEPTANCE_PROBE_FAILED'):run(path)


@pytest.mark.parametrize('value',[False,None,1,'true'])
def test_confirmation_precedes_vault_and_local_creation(vault,tmp_path,value):
    path=tmp_path/'absent'/'pacing.sqlite3'
    with pytest.raises(ValueError):run(path,confirm_read_only_paper=value)
    assert vault.reads==vault.writes==0 and not path.parent.exists()


def test_invalid_account_or_public_source_destination_never_reads_vault(vault,tmp_path):
    with pytest.raises(ValueError):run(tmp_path/'state.sqlite3',expected_account_id='private-invalid')
    with pytest.raises(ValueError):run(probe.ROOT/'storage'/'state.sqlite3')
    assert vault.reads==0


def test_cooldown_survives_fresh_probe_and_does_not_retry(vault,tmp_path):
    save();path=tmp_path/'pacing.sqlite3';run(path)
    denied=Opener();before=path.read_bytes()
    with pytest.raises(ValueError):run(path,denied,at=1059)
    assert not denied.requests and path.read_bytes()==before
    result=run(path,at=1060)
    assert result['local_pacing']['sessions']==2 and result['local_pacing']['next_allowed_at']==1120


def test_foreign_pacing_journal_blocks_before_vault_or_provider(vault,tmp_path):
    path=tmp_path/'pacing.sqlite3';gate=PaperReadPacing(path,OTHER);gate.begin(admission=lambda:1000)
    before=path.read_bytes();opener=Opener()
    with pytest.raises(ValueError):run(path,opener)
    assert vault.reads==0 and not opener.requests and path.read_bytes()==before


def test_missing_saved_pair_creates_no_journal_or_provider_request(vault,tmp_path):
    path=tmp_path/'absent'/'pacing.sqlite3';opener=Opener()
    with pytest.raises(ValueError):run(path,opener)
    assert not path.parent.exists() and not opener.requests


def test_account_mismatch_stops_after_first_get_and_keeps_pending_review(vault,tmp_path):
    save();path=tmp_path/'pacing.sqlite3';opener=Opener()
    with pytest.raises(ValueError):run(path,opener,expected_account_id=OTHER)
    assert len(opener.requests)==1
    state=PaperReadPacing(path,OTHER).inspect()
    assert state['pending_token'] is not None


def test_rate_limit_persists_cooldown_and_no_automatic_retry(vault,tmp_path):
    save();path=tmp_path/'pacing.sqlite3';headers=Message();headers['Retry-After']='120'
    opener=Opener();calls=[]
    def limited(request,timeout):
        calls.append(request);raise HTTPError(request.full_url,429,'private failure',headers,None)
    opener.open=limited
    with pytest.raises(ValueError):run(path,opener)
    state=PaperReadPacing(path,ACCOUNT).inspect()
    assert len(calls)==1 and state['next_allowed_at']==1120 and state['pending_token'] is None
    denied=Opener()
    with pytest.raises(ValueError):run(path,denied,at=1119)
    assert not denied.requests


def test_lost_credential_confirmation_does_not_emit_report_or_erase_successful_reads(vault,tmp_path,monkeypatch):
    save();path=tmp_path/'pacing.sqlite3';opener=Opener();original=credentials._capture;captures=[]
    def changed():
        value=original();captures.append(True)
        if len(captures)==2:raise credentials.PaperVaultError('private readback failure')
        return value
    monkeypatch.setattr(credentials,'_capture',changed)
    with pytest.raises(ValueError):run(path,opener)
    assert len(opener.requests)==4
    assert PaperReadPacing(path,ACCOUNT).inspect()['last_outcome']=='SUCCESS'


def test_cli_missing_confirmation_and_usage_are_private(vault,tmp_path,monkeypatch,capsys):
    path=tmp_path/'absent'/'pacing.sqlite3'
    monkeypatch.setattr(sys,'argv',['tool','--expected-account',ACCOUNT,'--pacing',str(path)])
    assert probe.main()==2
    captured=capsys.readouterr();assert captured.out.strip()=='PAPER_ACCEPTANCE_PROBE_FAILED' and not captured.err
    assert vault.reads==0 and not path.parent.exists()
    monkeypatch.setattr(sys,'argv',['tool','--api-secret','private-mistaken-secret'])
    with pytest.raises(SystemExit) as stopped:probe.main()
    assert stopped.value.code==2 and capsys.readouterr().err.strip()=='PAPER_ACCEPTANCE_PROBE_USAGE_ERROR'


def test_pacing_status_is_vault_silent_and_preserves_missing_or_pending_state(vault,tmp_path):
    path=tmp_path/'absent'/'pacing.sqlite3'
    result=probe.pacing_status(expected_account_id=ACCOUNT,pacing_path=path)
    from tools.portal_browser_smoke import paper_pacing_status_fixture
    assert set(paper_pacing_status_fixture())==set(result)
    assert set(paper_pacing_status_fixture()['operation_counts'])==set(result['operation_counts'])
    assert result['journal_present'] is False and not path.parent.exists() and vault.reads==0
    assert result['pending_operation'] is None and result['legacy_unspecified_sessions']==0
    assert sum(result['operation_counts'].values())==result['sessions']==0
    assert result['operation_identity_attested'] is result['mixed_operation_acceptance_verified'] is False
    gate=PaperReadPacing(path,ACCOUNT);gate.begin(admission=lambda:1000)
    before=path.read_bytes();result=probe.pacing_status(expected_account_id=ACCOUNT,pacing_path=path)
    assert result['pending_read'] and result['suggested_action']=='REVIEW_RETAINED_PENDING_READ_NO_AUTOMATIC_RETRY'
    assert result['pending_operation']=='LEGACY_UNSPECIFIED' and result['legacy_unspecified_sessions']==1
    assert sum(result['operation_counts'].values())==result['sessions']==1
    assert vault.reads==0 and path.read_bytes()==before and 'pending_token' not in result


@pytest.mark.parametrize('kind',['directory','fifo','loop'])
def test_nonregular_pacing_path_fails_before_vault_or_network(vault,tmp_path,kind):
    import os
    if kind in ('fifo','loop') and os.name=='nt':pytest.skip('No Windows FIFO or symlink privilege assumed')
    path=tmp_path/'pacing.sqlite3'
    if kind=='directory':path.mkdir()
    elif kind=='fifo':os.mkfifo(path)
    else:path.symlink_to(path)
    opener=Opener()
    with pytest.raises(ValueError):run(path,opener)
    assert vault.reads==0 and not opener.requests


def test_cli_interrupt_retains_pending_read_and_returns_no_private_trace(vault,tmp_path,monkeypatch,capsys):
    save();path=tmp_path/'pacing.sqlite3';opener=Opener()
    def interrupted(*_args,**_kwargs):raise KeyboardInterrupt('private interrupted request')
    opener.open=interrupted;original=probe.probe
    monkeypatch.setattr(probe,'probe',lambda **kwargs:original(**kwargs,opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50))
    monkeypatch.setattr(sys,'argv',['tool','--expected-account',ACCOUNT,'--pacing',str(path),'--confirm-read-only-paper'])
    assert probe.main()==2
    captured=capsys.readouterr();assert captured.out.strip()=='PAPER_ACCEPTANCE_PROBE_FAILED' and not captured.err
    assert PaperReadPacing(path,ACCOUNT).inspect()['pending_token'] is not None


@pytest.mark.parametrize('profile',['missing','pending','interrupted','legacy','unreceipted','v1','v2','v3','failure'])
def test_pacing_status_receipt_summary_is_read_only_and_not_authority(vault,tmp_path,profile):
    import sqlite3
    from nexus import paper_read_pacing as pacing
    from nexus.alpaca_paper_contract import canonical,sha
    from test_typed_paper_read_pacing import receipt
    path=tmp_path/'private'/'pacing.sqlite3';gate=PaperReadPacing(path,ACCOUNT)
    if profile=='legacy':
        token=gate.begin(admission=lambda:1000);gate.finish(token,admission=lambda:1001,status_code=200)
    elif profile!='missing':
        with gate.ownership() as owner:
            token=gate.begin(admission=lambda:1000,ownership=owner,operation='ACCOUNT_INVENTORY')
            if profile not in ('pending','interrupted'):
                retained=receipt('ACCOUNT_INVENTORY','RATE_LIMITED' if profile=='failure' else 'SUCCESS')
                if profile=='v3':retained['result_projection_sha256']=sha(dict(normalized_result=True))
                gate.finish(token,admission=lambda:1001,status_code=429 if profile=='failure' else 200,
                    retry_after='120' if profile=='failure' else None,ownership=owner,receipt=retained)
        if profile=='interrupted':
            gate.interrupt(token,expected_head=gate.inspect()['head_sha256'],expected_account_id=ACCOUNT,
                confirm_interrupted=True,admission=lambda:1001)
        if profile in ('unreceipted','v1'):
            original=gate.operation_history()[0]
            with sqlite3.connect(path) as db:
                encoded,parent=db.execute('SELECT body,previous FROM events WHERE seq=2').fetchone();body=json.loads(encoded)
                if profile=='unreceipted':
                    for key in ('receipt_profile','request_kinds','request_sha256','response_sha256','receipt_sha256'):body.pop(key)
                else:
                    body['receipt_profile']=pacing.RECEIPT_PROFILE_V1;body.pop('request_sha256')
                    body['receipt_sha256']=sha(dict(profile=pacing.RECEIPT_PROFILE_V1,operation='ACCOUNT_INVENTORY',
                        before_head_sha256=original['before_head_sha256'],begin_head_sha256=original['begin_head_sha256'],
                        evaluated_at=body['evaluated_at'],outcome=body['outcome'],request_kinds=body['request_kinds'],
                        response_sha256=body['response_sha256']))
                db.execute('UPDATE events SET body=?,digest=? WHERE seq=2',(canonical(body),sha(dict(seq=2,body=body,previous=parent))))
    before=path.read_bytes() if path.exists() else None
    status=probe.pacing_status(expected_account_id=ACCOUNT,pacing_path=path)
    summary=status['receipt_summary']
    completed=profile in ('unreceipted','v1','v2','v3','failure')
    successful=profile in ('unreceipted','v1','v2','v3')
    assert summary==dict(typed_result_receipts=int(profile in ('v1','v2','v3','failure')),
        unreceipted_typed_results=int(profile=='unreceipted'),all_typed_results_receipted=profile in ('v1','v2','v3','failure'),
        target_bound_typed_results=int(profile in ('v2','v3','failure')),untargeted_typed_results=int(profile in ('unreceipted','v1')),
        all_typed_request_targets_bound=profile in ('v2','v3','failure'),successful_typed_results=int(successful),
        normalized_bound_successful_results=int(profile=='v3'),normalized_unbound_successful_results=int(successful and profile!='v3'),
        all_successful_normalized_results_bound=profile=='v3')
    assert sum(summary[k] for k in ('typed_result_receipts','unreceipted_typed_results'))==int(completed)
    assert all(status[flag] is False for flag in probe.FLAGS)
    assert status['operation_identity_attested'] is status['mixed_operation_acceptance_verified'] is False
    assert vault.reads==vault.writes==0
    assert (path.read_bytes() if path.exists() else None)==before
    if profile=='missing':assert not path.parent.exists()
    encoded=json.dumps(status)
    assert all(value not in encoded for value in (KEY,SECRET,ACCOUNT,str(path)))
    # Cold status uses only the retained journal, not the process's lease memory.
    assert probe.pacing_status(expected_account_id=ACCOUNT,pacing_path=path)==status
