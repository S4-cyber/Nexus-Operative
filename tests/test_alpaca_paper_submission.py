"""Injected single-attempt paper submission boundary; never provider IO."""
import io
import json
import os
from urllib.error import URLError

import pytest

from nexus import alpaca_paper_submission as module
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_provider_post_budget import PaperProviderPostBudget
from test_alpaca_paper_contract import plan, response
from test_broker_request_journal import ACCOUNT

KEY = 'fixture_key_only_123'
SECRET = 'fixture_secret_only_123'


def account(**changes):
    value = dict(id=ACCOUNT, currency='USD', status='ACTIVE', crypto_status='ACTIVE',
        account_blocked=False, trading_blocked=False, trade_suspended_by_user=False,
        cash='1000', non_marginable_buying_power='900', pending_transfer_in='0',
        pending_transfer_out='0', accrued_fees='0')
    value.update(changes); return value


def asset(**changes):
    value = dict(symbol='BTC/USD', **{'class':'crypto'}, status='active', tradable=True,
        fractionable=True, min_order_size='.5', min_trade_increment='.5', price_increment='.5')
    value.update(changes); return value


class Reply:
    def __init__(self, value, *, url=None, status=200, headers=None, raw=None):
        self.url=url; self.status=status
        self.headers={'Content-Type':'application/json'} if headers is None else headers
        self.stream=io.BytesIO(json.dumps(value).encode() if raw is None else raw)
        self.closed=False
    def __enter__(self): return self
    def __exit__(self,*_): self.closed=True
    def geturl(self): return self.url
    def read(self,size): return self.stream.read(size)


class Opener:
    def __init__(self, values=None):
        positions=[dict(symbol='BTC/USD',asset_class='crypto',side='long',qty='3',qty_available='3')]
        self.values=list(values or [account(),positions,asset(),account(),positions,
                                    account(),positions,response()])
        self.requests=[];self.responses=[]
    def open(self, request, timeout):
        self.requests.append((request,timeout)); value=self.values[len(self.requests)-1]
        if isinstance(value,BaseException): raise value
        reply=value if isinstance(value,Reply) else Reply(value)
        if reply.url is None: reply.url=request.full_url
        self.responses.append(reply);return reply


@pytest.fixture
def prepared(tmp_path):
    journal=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    budget=PaperProviderPostBudget(tmp_path/'post-budget.sqlite3')
    journal.prepare(plan())
    selected=journal.lookup_request('nx_fixture')
    return journal,selected,budget


def submit(prepared, opener=None, **changes):
    journal,selected,budget=prepared
    options=dict(expected_account_id=ACCOUNT,expected_head=selected['head_sha256'],
        expected_payload_sha256=selected['plan']['payload_sha256'],
        post_budget=budget,expected_post_budget_head=budget.inspect()['head_sha256'],
        confirm_paper_order=True,confirm_single_attempt=True,
        opener=opener or Opener(),wall_clock=lambda:1000,elapsed_clock=lambda:50,
        permission_check=lambda:True,emergency_stop_check=lambda:False,
        credential_check=lambda:True)
    options.update(changes)
    return module.submit_once(journal,'nx_fixture',KEY,SECRET,**options)


def test_single_attempt_commits_unknown_before_post_and_retains_response(prepared):
    opener=Opener();result=submit(prepared,opener)
    assert result['paper_only'] and result['single_attempt_consumed']
    assert result['durable_unknown_outcome_marker_committed']
    assert result['account_and_position_capacity_resampled']
    assert result['sequential_preflight_stable']
    assert result['post_reservation_risk_resampled']
    assert result['request_and_post_budget_store_bindings_pinned']
    assert result['request_and_post_budget_file_identities_pinned']
    assert result['post_reservation_durable_heads_rechecked']
    assert result['response_observation_head_bound_to_post_budget']
    assert result['response_budget_completion_cold_replayed']
    assert result['alpaca_post_budget_scope_required']
    assert result['paper_order_response_observed']
    assert result['source']=='INJECTED_UNATTESTED'
    assert result['journal']['recovery_state']=='OBSERVED_NONTERMINAL_REVIEW_REQUIRED'
    assert all(result[key] is False for key in ('automatic_retry','automatic_continuation',
        'sendable','ready_for_routing','broker_account_reconciled','spendable_funds_verified',
        'ledger_applied','live_orders_enabled','money_movement_enabled','release_approved'))
    assert [request.full_url for request,_ in opener.requests]==[
        module.ACCOUNT_URL,module.POSITIONS_URL,module.ASSET_URL,
        module.ACCOUNT_URL,module.POSITIONS_URL,module.ACCOUNT_URL,
        module.POSITIONS_URL,module.ORDER_URL]
    assert [request.get_method() for request,_ in opener.requests]==[
        'GET','GET','GET','GET','GET','GET','GET','POST']
    posted=json.loads(opener.requests[-1][0].data)
    assert posted==plan()['payload']
    assert all(request.get_header('Apca-api-key-id')==KEY and
        request.get_header('Apca-api-secret-key')==SECRET for request,_ in opener.requests)
    assert all(0<timeout<=module.REQUEST_TIMEOUT for _,timeout in opener.requests)
    assert all(reply.closed for reply in opener.responses)
    retained=prepared[0].recovery()['requests'][0]
    assert retained['submission_attempt_marked'] and retained['snapshot']['status']=='new'
    assert (prepared[2].history()[0]['request_after_head_sha256']==
            result['journal']['head_sha256'])
    assert KEY not in json.dumps(result) and SECRET not in json.dumps(result)


@pytest.mark.parametrize('field,value',[('confirm_paper_order',False),('confirm_single_attempt',False)])
def test_strict_confirmations_are_network_and_journal_silent(prepared,field,value):
    opener=Opener();before=prepared[0].path.read_bytes()
    with pytest.raises(module.PaperSubmissionError,match='CONFIRMATION_REQUIRED'):
        submit(prepared,opener,**{field:value})
    assert not opener.requests and prepared[0].path.read_bytes()==before


@pytest.mark.parametrize('changes,code',[
    ({'permission_check':lambda:False},'PERMISSION_REVOKED'),
    ({'emergency_stop_check':lambda:True},'EMERGENCY_STOP_ACTIVE'),
    ({'credential_check':lambda:False},'CREDENTIALS_CHANGED'),
])
def test_off_or_revoked_preflight_never_marks_or_posts(prepared,changes,code):
    opener=Opener();before=prepared[0].path.read_bytes()
    with pytest.raises(module.PaperSubmissionError,match=code):submit(prepared,opener,**changes)
    assert all(request.get_method()!='POST' for request,_ in opener.requests)
    assert prepared[0].path.read_bytes()==before


@pytest.mark.parametrize('values,code',[
    ([account(id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')],'ACCOUNT_ID_DISAGREEMENT'),
    ([account(cash='100',non_marginable_buying_power='100'),[],asset()],'BUY_FUNDS_INSUFFICIENT'),
    ([account(),[],asset(tradable=False)],'ASSET_NOT_ELIGIBLE'),
])
def test_account_asset_or_funds_failure_precedes_attempt_marker(prepared,values,code):
    opener=Opener(values);before=prepared[0].path.read_bytes()
    with pytest.raises(module.PaperSubmissionError,match=code):submit(prepared,opener)
    assert all(request.get_method()!='POST' for request,_ in opener.requests)
    assert prepared[0].path.read_bytes()==before


@pytest.mark.parametrize('late_account,late_positions,code',[
    (account(trading_blocked=True), [], 'ACCOUNT_PERMISSION_REVIEW_REQUIRED'),
    (account(cash='100',non_marginable_buying_power='100'), [], 'BUY_FUNDS_INSUFFICIENT'),
    (account(cash='999'), [], 'PREFLIGHT_STATE_CHANGED'),
    (account(), [dict(symbol='BTC/USD',asset_class='crypto',side='long',qty='3',qty_available='2')],
     'PREFLIGHT_STATE_CHANGED'),
])
def test_second_account_and_position_sample_fails_before_reservation_or_post(
        prepared, late_account, late_positions, code):
    initial_positions=[dict(symbol='BTC/USD',asset_class='crypto',side='long',qty='3',qty_available='3')]
    opener=Opener([account(),initial_positions,asset(),late_account,late_positions])
    request_before=prepared[0].path.read_bytes()
    with pytest.raises(module.PaperSubmissionError,match=code):
        submit(prepared,opener)
    assert all(request.get_method()!='POST' for request,_ in opener.requests)
    assert prepared[0].path.read_bytes()==request_before
    assert prepared[2].inspect()['attempts']==0


def test_stop_after_marker_never_posts_and_never_rearms(prepared,monkeypatch):
    journal=prepared[0];opener=Opener();marked=False
    original=BrokerRequestJournal.begin_submission_checked
    def begin(self,*args,**kwargs):
        nonlocal marked
        result=original(self,*args,**kwargs);marked=True;return result
    monkeypatch.setattr(BrokerRequestJournal,'begin_submission_checked',begin)
    with pytest.raises(module.PaperSubmissionError,match='EMERGENCY_STOP_ACTIVE'):
        submit(prepared,opener,emergency_stop_check=lambda:marked)
    assert marked and all(request.get_method()!='POST' for request,_ in opener.requests)
    assert journal.recovery()['requests'][0]['lookup_by_original_client_id_required']
    assert prepared[2].history()[0]['outcome']=='NOT_SENT'
    assert prepared[2].history()[0]['actual_posts']==0
    with pytest.raises(module.PaperSubmissionError,match='LOCAL_REVIEW_BLOCKED'):
        submit(prepared,Opener())


def test_wrong_provider_post_scope_is_network_and_journal_silent(prepared,tmp_path):
    journal,selected,_=prepared
    wrong=PaperProviderPostBudget(tmp_path/'wrong-scope.sqlite3','OTHER_PROVIDER')
    opener=Opener();before=journal.path.read_bytes()
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_BUDGET_SCOPE_DISAGREEMENT'):
        submit((journal,selected,wrong),opener)
    assert not opener.requests and journal.path.read_bytes()==before
    assert not wrong.path.exists()


def test_post_marker_caller_store_retarget_is_fail_closed_and_uses_pinned_stores(
        prepared,tmp_path):
    journal,selected,budget=prepared
    request_path=journal.path;budget_path=budget.path
    decoy_request=tmp_path/'decoy-request.sqlite3'
    decoy_budget=tmp_path/'decoy-budget.sqlite3'
    opener=Opener();retargeted=False
    def stop():
        nonlocal retargeted
        state=BrokerRequestJournal(request_path,ACCOUNT).recovery()['requests'][0]
        if state['submission_attempt_marked'] and not retargeted:
            journal.path=decoy_request;budget.path=decoy_budget;retargeted=True
        return False
    with pytest.raises(module.PaperSubmissionError,match='STORE_BINDING_CHANGED'):
        submit((journal,selected,budget),opener,emergency_stop_check=stop)
    retained=BrokerRequestJournal(request_path,ACCOUNT).recovery()['requests'][0]
    history=PaperProviderPostBudget(budget_path).history()
    assert retargeted and retained['lookup_by_original_client_id_required']
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0
    assert all(request.get_method()!='POST' for request,_ in opener.requests)
    assert not decoy_request.exists() and not decoy_budget.exists()


@pytest.mark.parametrize('store_index',['request','budget'])
def test_post_marker_same_byte_store_replacement_is_fail_closed(
        prepared,tmp_path,store_index):
    journal,_,budget=prepared
    target=journal.path if store_index=='request' else budget.path
    opener=Opener();replaced=False
    def stop():
        nonlocal replaced
        state=BrokerRequestJournal(journal.path,ACCOUNT).recovery()['requests'][0]
        if state['submission_attempt_marked'] and not replaced:
            replacement=tmp_path/f'{store_index}-replacement.sqlite3'
            replacement.write_bytes(target.read_bytes())
            os.replace(replacement,target);replaced=True
        return False
    with pytest.raises(module.PaperSubmissionError,match='STORE_BINDING_CHANGED'):
        submit(prepared,opener,emergency_stop_check=stop)
    retained=BrokerRequestJournal(journal.path,ACCOUNT).recovery()['requests'][0]
    history=PaperProviderPostBudget(budget.path).history()
    assert replaced and retained['lookup_by_original_client_id_required']
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0
    assert all(request.get_method()!='POST' for request,_ in opener.requests)


def test_post_marker_same_file_request_head_advance_is_fail_closed(prepared):
    journal,_,budget=prepared
    opener=Opener();advanced=False
    def stop():
        nonlocal advanced
        state=BrokerRequestJournal(journal.path,ACCOUNT).recovery()['requests'][0]
        if state['submission_attempt_marked'] and not advanced:
            BrokerRequestJournal(journal.path,ACCOUNT).begin_lookup(
                'nx_fixture',evaluated_at=1001)
            advanced=True
        return False
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_RESERVATION_DURABLE_STATE_CHANGED'):
        submit(prepared,opener,emergency_stop_check=stop)
    retained=journal.recovery()['requests'][0]
    history=budget.history()
    assert advanced and retained['lookup_by_original_client_id_required']
    assert retained['lookup']['pending'] is True
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0
    assert all(request.get_method()!='POST' for request,_ in opener.requests)


def test_post_marker_budget_head_disagreement_is_fail_closed(
        prepared,monkeypatch):
    journal,_,budget=prepared
    original=PaperProviderPostBudget.inspect
    def inspect(self):
        state=original(self)
        retained=BrokerRequestJournal(journal.path,ACCOUNT).recovery()['requests'][0]
        if self.path==budget.path and retained['submission_attempt_marked']:
            return dict(state,head_sha256='f'*64)
        return state
    monkeypatch.setattr(PaperProviderPostBudget,'inspect',inspect)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_RESERVATION_DURABLE_STATE_CHANGED'):
        submit(prepared,opener)
    retained=journal.recovery()['requests'][0]
    history=budget.history()
    assert retained['lookup_by_original_client_id_required']
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0
    assert all(request.get_method()!='POST' for request,_ in opener.requests)


def test_post_response_pre_observation_head_advance_is_unknown_and_never_reposts(
        prepared,monkeypatch):
    journal,_,budget=prepared
    original=BrokerRequestJournal.observe_checked
    def advance_then_observe(self,*args,**kwargs):
        BrokerRequestJournal(self.path,ACCOUNT).begin_lookup(
            'nx_fixture',evaluated_at=1001)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(BrokerRequestJournal,'observe_checked',advance_then_observe)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_RESPONSE_DURABLE_STATE_CHANGED'):
        submit(prepared,opener)
    retained=journal.recovery()['requests'][0]
    history=budget.history()
    assert retained['lookup']['pending'] is True and retained['snapshot'] is None
    assert len(history)==1 and history[0]['outcome']=='OUTCOME_UNKNOWN'
    assert history[0]['actual_posts']==1
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1
    with pytest.raises(module.PaperSubmissionError,match='LOCAL_REVIEW_BLOCKED'):
        submit(prepared,Opener())


def test_post_budget_binds_exact_observation_head_not_later_lookup_head(
        prepared,monkeypatch):
    journal,_,budget=prepared
    original=BrokerRequestJournal.observe_checked
    def observe_then_advance(self,*args,**kwargs):
        result=original(self,*args,**kwargs)
        BrokerRequestJournal(self.path,ACCOUNT).begin_lookup(
            'nx_fixture',evaluated_at=1001)
        return result
    monkeypatch.setattr(BrokerRequestJournal,'observe_checked',observe_then_advance)
    result=submit(prepared,Opener())
    history=budget.history()
    current=journal.recovery()['head_sha256']
    assert len(history)==1 and history[0]['outcome']=='RESPONSE_OBSERVED'
    assert history[0]['request_after_head_sha256']==result['journal']['head_sha256']
    assert history[0]['request_after_head_sha256']!=current


def test_lost_post_budget_completion_ack_cold_replays_without_second_post(
        prepared,monkeypatch):
    original=PaperProviderPostBudget.finish
    def lose_ack(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise RuntimeError('private lost completion acknowledgement')
    monkeypatch.setattr(PaperProviderPostBudget,'finish',lose_ack)
    opener=Opener();result=submit(prepared,opener)
    history=prepared[2].history()
    assert result['response_budget_completion_cold_replayed'] is True
    assert len(history)==1 and history[0]['outcome']=='RESPONSE_OBSERVED'
    assert result['post_budget_head_sha256']==history[0]['after_head_sha256']
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1


def test_uncommitted_post_budget_completion_is_fixed_review_block(
        prepared,monkeypatch):
    def fail_before_commit(*_args,**_kwargs):
        raise RuntimeError('private completion storage detail')
    monkeypatch.setattr(PaperProviderPostBudget,'finish',fail_before_commit)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
            match='POST_BUDGET_COMPLETION_UNCERTAIN') as error:
        submit(prepared,opener)
    assert 'private completion storage detail' not in str(error.value)
    retained=prepared[0].recovery()['requests'][0]
    state=prepared[2].inspect()
    assert retained['snapshot']['status']=='new'
    assert state['pending_token'] is not None and state['completed_attempts']==0
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1
    with pytest.raises(module.PaperSubmissionError,match='LOCAL_REVIEW_BLOCKED'):
        submit(prepared,Opener())


def test_uncommitted_malformed_post_budget_ack_is_not_trusted(
        prepared,monkeypatch):
    monkeypatch.setattr(PaperProviderPostBudget,'finish',
        lambda *_args,**_kwargs:'f'*64)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_BUDGET_COMPLETION_UNCERTAIN'):
        submit(prepared,opener)
    assert prepared[2].inspect()['pending_token'] is not None
    assert prepared[2].history()==()
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1


@pytest.mark.parametrize('final_account,final_positions',[
    (account(cash='999'), []),
    (account(), [dict(symbol='BTC/USD',asset_class='crypto',side='long',
                      qty='3',qty_available='2')]),
])
def test_post_reservation_risk_change_never_posts_and_never_rearms(
        prepared, final_account, final_positions):
    initial_positions=[dict(symbol='BTC/USD',asset_class='crypto',side='long',
                            qty='3',qty_available='3')]
    opener=Opener([account(),initial_positions,asset(),account(),initial_positions,
                   final_account,final_positions])
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_RESERVATION_STATE_CHANGED'):
        submit(prepared,opener)
    retained=prepared[0].recovery()['requests'][0]
    assert retained['lookup_by_original_client_id_required']
    assert all(request.get_method()!='POST' for request,_ in opener.requests)
    assert prepared[2].history()[0]['outcome']=='NOT_SENT'
    assert prepared[2].history()[0]['actual_posts']==0
    with pytest.raises(module.PaperSubmissionError,match='LOCAL_REVIEW_BLOCKED'):
        submit(prepared,Opener())


def test_post_transport_loss_preserves_unknown_and_cannot_retry(prepared):
    opener=Opener([account(),[],asset(),account(),[],account(),[],
                   URLError('private transport detail')])
    with pytest.raises(module.PaperSubmissionError,match='OUTCOME_UNKNOWN_LOOKUP_REQUIRED') as error:
        submit(prepared,opener)
    assert 'private transport detail' not in str(error.value)
    retained=prepared[0].recovery()['requests'][0]
    assert retained['lookup_by_original_client_id_required'] and retained['snapshot'] is None
    assert prepared[2].inspect()['review_required'] is True
    assert prepared[2].history()[0]['outcome']=='OUTCOME_UNKNOWN'
    second=Opener()
    with pytest.raises(module.PaperSubmissionError,match='LOCAL_REVIEW_BLOCKED'):
        submit(prepared,second)
    assert not second.requests


def test_invalid_post_response_preserves_unknown_without_secret_or_body_echo(prepared):
    opener=Opener([account(),[],asset(),account(),[],account(),[],
                   Reply({},raw=b'{"secret":"PRIVATE_BODY"}')])
    with pytest.raises(ValueError) as error:submit(prepared,opener)
    assert 'PRIVATE_BODY' not in str(error.value) and KEY not in str(error.value)
    retained=prepared[0].recovery()['requests'][0]
    assert retained['lookup_by_original_client_id_required'] and retained['snapshot'] is None


def test_sell_requires_reported_available_btc(prepared):
    journal,_,_=prepared
    # Use a separate durable request so the fixture's buy remains untouched.
    journal.prepare(plan(client_order_id='sell_fixture',side='SELL'))
    selected=journal.lookup_request('sell_fixture')
    options=dict(expected_account_id=ACCOUNT,expected_head=selected['head_sha256'],
        expected_payload_sha256=selected['plan']['payload_sha256'],
        post_budget=PaperProviderPostBudget(journal.path.with_name('sell-post-budget.sqlite3')),
        expected_post_budget_head='0'*64,confirm_paper_order=True,
        confirm_single_attempt=True,opener=Opener([account(),
            [dict(symbol='BTC/USD',asset_class='crypto',side='long',qty='2',qty_available='1')],asset()]),
        wall_clock=lambda:1000,elapsed_clock=lambda:50,permission_check=lambda:True,
        emergency_stop_check=lambda:False,credential_check=lambda:True)
    before=journal.path.read_bytes()
    with pytest.raises(module.PaperSubmissionError,match='BTC_AVAILABLE_INSUFFICIENT'):
        module.submit_once(journal,'sell_fixture',KEY,SECRET,**options)
    assert journal.path.read_bytes()==before
