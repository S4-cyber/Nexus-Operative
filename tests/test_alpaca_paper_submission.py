"""Injected single-attempt paper submission boundary; never provider IO."""
from contextlib import contextmanager
import io
import json
import os
import threading
from urllib.error import URLError

import pytest

from nexus import alpaca_paper_submission as module, emergency_intent
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_provider_post_budget import PaperProviderPostBudget
from test_alpaca_paper_contract import plan, response
from test_broker_request_journal import ACCOUNT

KEY = 'fixture_key_only_123'
SECRET = 'fixture_secret_only_123'


def _capture(errors,action):
    try:
        return action()
    except BaseException as error:
        errors.append(error)


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


def open_order(**changes):
    value=dict(id='01234567-89ab-cdef-0123-456789abcde1',
        client_order_id='existing_buy',symbol='BTC/USD',asset_class='crypto',
        side='buy',type='limit',time_in_force='ioc',order_class='simple',
        extended_hours=False,qty='1',limit_price='100',status='new',
        filled_qty='0',filled_avg_price=None,
        updated_at='2026-10-17T18:00:00Z')
    value.update(changes);return value


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
    def __init__(self, values=None, open_orders=None):
        positions=[dict(symbol='BTC/USD',asset_class='crypto',side='long',qty='3',qty_available='3')]
        self.values=list(values or [account(),positions,asset(),account(),positions,
                                    account(),positions,asset(),response()])
        self.open_orders=list(open_orders or ([],[],[]))
        self.value_index=0;self.open_order_index=0
        self.requests=[];self.responses=[]
    def open(self, request, timeout):
        self.requests.append((request,timeout))
        if request.full_url==getattr(module,'OPEN_ORDERS_URL',None):
            value=self.open_orders[self.open_order_index]
            self.open_order_index+=1
        else:
            value=self.values[self.value_index]
            self.value_index+=1
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
    api_key=options.pop('api_key',KEY);api_secret=options.pop('api_secret',SECRET)
    if 'transport_authorization' not in options:
        authorization=module.PaperTransportAuthorizationLease()
        authorization.arm_once(api_key,api_secret,
            confirm_single_attempt=True)
        options['transport_authorization']=authorization
    options.setdefault('emergency_stop_order_path',str(journal.path.resolve()))
    options.setdefault('confirm_emergency_stop_binding',True)
    return module.submit_once(journal,'nx_fixture',api_key,api_secret,**options)


def test_transport_authorization_is_off_by_default_and_one_shot():
    authorization=module.PaperTransportAuthorizationLease()
    with pytest.raises(module.PaperSubmissionError,
                       match='TRANSPORT_AUTHORIZATION_NOT_ARMED'):
        with authorization.claim(KEY,SECRET):
            raise AssertionError('unarmed authorization entered')
    authorization.arm_once(KEY,SECRET,confirm_single_attempt=True)
    with authorization.claim(KEY,SECRET):
        pass
    with pytest.raises(module.PaperSubmissionError,
                       match='TRANSPORT_AUTHORIZATION_NOT_ARMED'):
        with authorization.claim(KEY,SECRET):
            raise AssertionError('consumed authorization re-entered')
    revoked=module.PaperTransportAuthorizationLease()
    revoked.arm_once(KEY,SECRET,confirm_single_attempt=True);revoked.revoke()
    with pytest.raises(module.PaperSubmissionError,
                       match='TRANSPORT_AUTHORIZATION_ALREADY_USED'):
        revoked.arm_once(KEY,SECRET,confirm_single_attempt=True)


def test_equality_forging_credential_is_rejected_before_store_or_transport(prepared):
    class ForgedCredential(str):
        def encode(self,*_args,**_kwargs):return b'FORGED_WIRE_VALUE'
    journal,_,budget=prepared;opener=Opener()
    request_before=journal.path.read_bytes();budget_before=budget.inspect()
    with pytest.raises(module.PaperSubmissionError,match='CREDENTIAL_FORMAT_INVALID'):
        submit(prepared,opener,api_key=ForgedCredential(KEY))
    assert not opener.requests and journal.path.read_bytes()==request_before
    assert budget.inspect()==budget_before


def test_polymorphic_account_fields_never_pass_submission_preflight():
    class ForgedText(str):
        def __str__(self):return '1000'
    raw=account(cash=ForgedText('0'))
    with pytest.raises(module.PaperSubmissionError,match='ACCOUNT_INVALID'):
        module._account_and_funds(raw,[],ACCOUNT,plan()['payload'])


def test_single_attempt_commits_unknown_before_post_and_retains_response(prepared):
    opener=Opener();result=submit(prepared,opener)
    assert result['paper_only'] and result['single_attempt_consumed']
    assert result['durable_unknown_outcome_marker_committed']
    assert result['account_and_position_capacity_resampled']
    assert result['sequential_preflight_stable']
    assert result['post_reservation_risk_resampled']
    assert result['post_reservation_asset_resampled']
    assert result['open_order_reservations_absent_at_three_samples']
    assert result['post_transport_credentials_rechecked']
    assert result['post_transport_controls_rechecked']
    assert result['typed_local_transport_authorization_consumed']
    assert result['pre_post_journal_writer_exclusion']
    assert result['explicit_emergency_stop_binding']
    assert result['trusted_transport_deadline_enforced']
    assert result['terminal_store_bindings_rechecked']
    assert result['request_and_post_budget_store_bindings_pinned']
    assert result['request_and_post_budget_file_identities_pinned']
    assert result['post_reservation_durable_heads_rechecked']
    assert result['response_observation_head_bound_to_post_budget']
    assert result['post_budget_reservation_cold_replayed']
    assert result['request_marker_cold_replayed']
    assert result['reservation_and_marker_admission_windows_bound']
    assert result['response_observation_cold_replayed']
    assert result['response_budget_completion_cold_replayed']
    assert result['post_response_persistence_stop_independent']
    assert result['post_response_local_completion_stop_independent']
    assert result['response_observation_completion_bracketed']
    assert result['response_and_safety_completion_windows_bound']
    assert result['alpaca_post_budget_scope_required']
    assert result['paper_order_response_observed']
    assert result['source']=='INJECTED_UNATTESTED'
    assert result['journal']['recovery_state']=='OBSERVED_NONTERMINAL_REVIEW_REQUIRED'
    assert all(result[key] is False for key in ('automatic_retry','automatic_continuation',
        'sendable','ready_for_routing','broker_account_reconciled','spendable_funds_verified',
        'ledger_applied','live_orders_enabled','money_movement_enabled','release_approved'))
    assert [request.full_url for request,_ in opener.requests]==[
        module.ACCOUNT_URL,module.POSITIONS_URL,module.OPEN_ORDERS_URL,module.ASSET_URL,
        module.ACCOUNT_URL,module.POSITIONS_URL,module.OPEN_ORDERS_URL,
        module.ACCOUNT_URL,module.POSITIONS_URL,module.ASSET_URL,
        module.OPEN_ORDERS_URL,module.ORDER_URL]
    assert [request.get_method() for request,_ in opener.requests]==[
        'GET','GET','GET','GET','GET','GET','GET','GET','GET','GET','GET','POST']
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


def test_existing_open_order_requires_review_before_reservation(prepared):
    opener=Opener(open_orders=[[open_order()],[],[]])
    before=prepared[0].path.read_bytes()
    with pytest.raises(module.PaperSubmissionError,
            match='OPEN_ORDER_RESERVATIONS_REVIEW_REQUIRED'):
        submit(prepared,opener)
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)
    assert prepared[0].path.read_bytes()==before
    assert prepared[2].inspect()['attempts']==0


def test_malformed_open_order_terms_fail_before_reservation(prepared):
    opener=Opener(open_orders=[[{'private':SECRET,
        'account_id':'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'}],[],[]])
    before=prepared[0].path.read_bytes()
    with pytest.raises(module.PaperSubmissionError,
            match='OPEN_ORDERS_INVALID') as error:
        submit(prepared,opener)
    assert SECRET not in str(error.value)
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)
    assert prepared[0].path.read_bytes()==before
    assert prepared[2].inspect()['attempts']==0


@pytest.mark.parametrize('orders',[
    (),
    type('OpenOrderList',(list,),{})([]),
    [type('OpenOrderRow',(dict,),{})()],
    [{} for _ in range(129)],
])
def test_open_order_collection_shape_is_exact_and_bounded(prepared,orders):
    with pytest.raises(module.PaperSubmissionError,match='OPEN_ORDERS_INVALID'):
        module._require_empty_open_orders(orders,ACCOUNT,
            nonempty_code='OPEN_ORDER_RESERVATIONS_REVIEW_REQUIRED')


def test_open_order_collection_cap_blocks_before_reservation(prepared):
    opener=Opener(open_orders=[[{} for _ in range(129)],[],[]])
    before=prepared[0].path.read_bytes()
    with pytest.raises(module.PaperSubmissionError,match='OPEN_ORDERS_INVALID'):
        submit(prepared,opener)
    assert prepared[0].path.read_bytes()==before
    assert prepared[2].inspect()['attempts']==0
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)


def test_open_orders_change_before_reservation_never_marks_or_posts(prepared):
    opener=Opener(open_orders=[[],[open_order()],[]])
    before=prepared[0].path.read_bytes()
    with pytest.raises(module.PaperSubmissionError,match='PREFLIGHT_OPEN_ORDERS_CHANGED'):
        submit(prepared,opener)
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)
    assert prepared[0].path.read_bytes()==before
    assert prepared[2].inspect()['attempts']==0


def test_open_orders_change_after_reservation_never_posts_or_rearms(prepared):
    opener=Opener(open_orders=[[],[],[open_order()]])
    with pytest.raises(module.PaperSubmissionError,
            match='POST_RESERVATION_OPEN_ORDERS_CHANGED'):
        submit(prepared,opener)
    retained=prepared[0].recovery()['requests'][0]
    assert retained['lookup_by_original_client_id_required']
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)
    history=prepared[2].history()
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0
    state=prepared[2].inspect()
    assert state['pending_token'] is None and state['completed_attempts']==1
    repeat=Opener()
    with pytest.raises(module.PaperSubmissionError,match='LOCAL_REVIEW_BLOCKED'):
        submit(prepared,repeat)
    assert not repeat.requests and prepared[2].history()==history


def test_open_orders_endpoint_and_session_boundary_are_exact():
    assert module.OPEN_ORDERS_URL==(
        'https://paper-api.alpaca.markets/v2/orders?status=open&limit=129&direction=asc')
    opener=Opener()
    def session():
        return module._Session(KEY,SECRET,opener=opener,
            wall_clock=lambda:1000,elapsed_clock=lambda:50,
            permission_check=lambda:True,emergency_stop_check=lambda:False,
            credential_check=lambda:True,store_binding_check=lambda:None)
    with pytest.raises(module.PaperSubmissionError,match='PATH_DENIED'):
        session().request(module.OPEN_ORDERS_URL+'&extra=true')
    with pytest.raises(module.PaperSubmissionError,match='METHOD_DENIED'):
        session().request(module.OPEN_ORDERS_URL,payload=plan()['payload'])
    capped=session();capped.requests=module.MAX_REQUESTS
    with pytest.raises(module.PaperSubmissionError,match='REQUEST_CAP'):
        capped.request(module.ACCOUNT_URL)
    assert module.MAX_REQUESTS==12 and not opener.requests


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


@pytest.mark.parametrize('ack_mode',['lost','malformed'])
def test_committed_post_budget_reservation_ack_is_cold_replayed_before_marker(
        prepared,monkeypatch,ack_mode):
    original=PaperProviderPostBudget.begin
    def begin(self,*args,**kwargs):
        original(self,*args,**kwargs)
        if ack_mode=='lost':
            raise RuntimeError('private lost reservation acknowledgement')
        return 'f'*64
    monkeypatch.setattr(PaperProviderPostBudget,'begin',begin)
    opener=Opener();result=submit(prepared,opener)
    assert result['post_budget_reservation_cold_replayed'] is True
    assert prepared[0].recovery()['requests'][0]['snapshot']['status']=='new'
    assert prepared[2].history()[0]['outcome']=='RESPONSE_OBSERVED'
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1


def test_out_of_window_post_budget_admission_never_marks_or_posts(
        prepared,monkeypatch):
    original=PaperProviderPostBudget.begin
    def wrong_admission(self,*args,**kwargs):
        return original(self,*args,**dict(kwargs,admission=lambda:999))
    monkeypatch.setattr(PaperProviderPostBudget,'begin',wrong_admission)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_BUDGET_RESERVATION_UNCERTAIN'):
        submit(prepared,opener)
    retained=prepared[0].recovery()['requests'][0]
    state=prepared[2].inspect()
    assert retained['submission_attempt_marked'] is False
    assert state['pending_token'] is not None
    assert state['last_evaluated_at']==999
    assert prepared[2].history()==()
    assert all(request.get_method()!='POST' for request,_ in opener.requests)


@pytest.mark.parametrize('begin_result',[RuntimeError('private begin detail'),'f'*64])
def test_uncommitted_post_budget_reservation_stops_before_marker_or_post(
        prepared,monkeypatch,begin_result):
    request_before=prepared[0].path.read_bytes()
    def uncommitted(*_args,**_kwargs):
        if isinstance(begin_result,BaseException):
            raise begin_result
        return begin_result
    monkeypatch.setattr(PaperProviderPostBudget,'begin',uncommitted)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
            match='POST_BUDGET_RESERVATION_UNCERTAIN') as error:
        submit(prepared,opener)
    assert 'private begin detail' not in str(error.value)
    assert prepared[0].path.read_bytes()==request_before
    assert prepared[0].recovery()['requests'][0]['submission_attempt_marked'] is False
    assert prepared[2].inspect()['attempts']==0
    assert all(request.get_method()!='POST' for request,_ in opener.requests)


def test_unstable_post_budget_reservation_replay_stops_before_marker_or_post(
        prepared,monkeypatch):
    request_before=prepared[0].path.read_bytes()
    original=PaperProviderPostBudget.pending_receipt
    reads=0
    def unstable(self):
        nonlocal reads
        reads+=1;result=original(self)
        return dict(result,head_sha256='f'*64) if reads==2 else result
    monkeypatch.setattr(PaperProviderPostBudget,'pending_receipt',unstable)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_BUDGET_RESERVATION_UNCERTAIN'):
        submit(prepared,opener)
    assert reads==2 and prepared[0].path.read_bytes()==request_before
    assert prepared[2].inspect()['pending_token'] is not None
    assert all(request.get_method()!='POST' for request,_ in opener.requests)


def test_post_budget_reservation_requires_original_active_lease(
        prepared,monkeypatch):
    request_before=prepared[0].path.read_bytes()
    original=PaperProviderPostBudget.begin
    def lose_active_lease(self,*args,**kwargs):
        result=original(self,*args,**kwargs)
        self._active_completion=None
        return result
    monkeypatch.setattr(PaperProviderPostBudget,'begin',lose_active_lease)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_BUDGET_RESERVATION_UNCERTAIN'):
        submit(prepared,opener)
    assert prepared[0].path.read_bytes()==request_before
    assert prepared[2].inspect()['pending_token'] is not None
    assert all(request.get_method()!='POST' for request,_ in opener.requests)


@pytest.mark.parametrize('ack_mode',['lost','malformed'])
def test_committed_request_marker_ack_is_cold_replayed_before_post(
        prepared,monkeypatch,ack_mode):
    original=BrokerRequestJournal.begin_submission_checked
    def begin(self,*args,**kwargs):
        original(self,*args,**kwargs)
        if ack_mode=='lost':
            raise RuntimeError('private lost marker acknowledgement')
        return {'attempt_marker_commit_observed':False,
                'submission_admission':{'private':'forged'}}
    monkeypatch.setattr(BrokerRequestJournal,'begin_submission_checked',begin)
    opener=Opener();result=submit(prepared,opener)
    retained=prepared[0].recovery()['requests'][0]
    assert result['request_marker_cold_replayed'] is True
    assert retained['submission_attempt_marked'] is True
    assert retained['snapshot']['status']=='new'
    assert prepared[2].history()[0]['outcome']=='RESPONSE_OBSERVED'
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1


def test_out_of_window_request_marker_admission_never_posts(
        prepared,monkeypatch):
    original=BrokerRequestJournal.begin_submission_checked
    def wrong_admission(self,*args,**kwargs):
        return original(self,*args,**dict(kwargs,admission=lambda:1001))
    monkeypatch.setattr(BrokerRequestJournal,'begin_submission_checked',
                        wrong_admission)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='REQUEST_MARKER_DURABLE_STATE_CHANGED'):
        submit(prepared,opener)
    retained=prepared[0].recovery()['requests'][0]
    history=prepared[2].history()
    assert retained['submission_attempt_marked'] is True
    assert retained['submission_admission']['evaluated_at']==1001
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0
    assert all(request.get_method()!='POST' for request,_ in opener.requests)


@pytest.mark.parametrize('begin_result',[
    RuntimeError('private marker detail'),
    {'attempt_marker_commit_observed':True,
     'submission_admission':{'private':'forged'}},
])
def test_uncommitted_request_marker_ack_stops_before_post(
        prepared,monkeypatch,begin_result):
    def uncommitted(*_args,**_kwargs):
        if isinstance(begin_result,BaseException):
            raise begin_result
        return begin_result
    monkeypatch.setattr(BrokerRequestJournal,'begin_submission_checked',
                        uncommitted)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
            match='REQUEST_MARKER_DURABLE_STATE_CHANGED') as error:
        submit(prepared,opener)
    retained=prepared[0].recovery()['requests'][0]
    history=prepared[2].history()
    assert 'private marker detail' not in str(error.value)
    assert retained['submission_attempt_marked'] is False
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0
    assert all(request.get_method()!='POST' for request,_ in opener.requests)


def test_unstable_request_marker_cold_replay_stops_before_post(
        prepared,monkeypatch):
    original=BrokerRequestJournal.recovery
    marker_reads=0
    def unstable(self):
        nonlocal marker_reads
        result=original(self)
        if (result['requests'] and
                result['requests'][0]['submission_attempt_marked'] and
                result['requests'][0]['snapshot'] is None):
            marker_reads+=1
            if marker_reads==2:
                return dict(result,head_sha256='f'*64)
        return result
    monkeypatch.setattr(BrokerRequestJournal,'recovery',unstable)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='REQUEST_MARKER_DURABLE_STATE_CHANGED'):
        submit(prepared,opener)
    retained=original(prepared[0])['requests'][0]
    history=prepared[2].history()
    assert marker_reads>=2 and retained['submission_attempt_marked'] is True
    assert retained['snapshot'] is None
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0
    assert all(request.get_method()!='POST' for request,_ in opener.requests)


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
                       match='POST_BUDGET_SAFETY_CLOSURE_UNCERTAIN'):
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


def test_lost_response_observation_ack_cold_replays_without_second_post(
        prepared,monkeypatch):
    original=BrokerRequestJournal.observe_checked
    def lose_ack(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise RuntimeError('private lost response acknowledgement')
    monkeypatch.setattr(BrokerRequestJournal,'observe_checked',lose_ack)
    opener=Opener();result=submit(prepared,opener)
    assert result['response_observation_cold_replayed'] is True
    assert result['journal']['snapshot']['status']=='new'
    assert prepared[2].history()[0]['outcome']=='RESPONSE_OBSERVED'
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1


def test_uncommitted_response_observation_is_fixed_unknown_review(
        prepared,monkeypatch):
    def fail_before_commit(*_args,**_kwargs):
        raise RuntimeError('private observation storage detail')
    monkeypatch.setattr(BrokerRequestJournal,'observe_checked',fail_before_commit)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
            match='POST_RESPONSE_DURABLE_STATE_CHANGED') as error:
        submit(prepared,opener)
    assert 'private observation storage detail' not in str(error.value)
    retained=prepared[0].recovery()['requests'][0]
    history=prepared[2].history()
    assert retained['snapshot'] is None
    assert len(history)==1 and history[0]['outcome']=='OUTCOME_UNKNOWN'
    assert history[0]['actual_posts']==1
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1


def test_uncommitted_malformed_response_observation_ack_is_not_trusted(
        prepared,monkeypatch):
    monkeypatch.setattr(BrokerRequestJournal,'observe_checked',
        lambda *_args,**_kwargs:{'head_sha256':'f'*64})
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_RESPONSE_DURABLE_STATE_CHANGED'):
        submit(prepared,opener)
    assert prepared[0].recovery()['requests'][0]['snapshot'] is None
    assert prepared[2].history()[0]['outcome']=='OUTCOME_UNKNOWN'
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1


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


def test_response_observation_is_replayed_after_budget_completion(
        prepared,monkeypatch):
    original_finish=PaperProviderPostBudget.finish
    original_receipt=BrokerRequestJournal.observation_receipt
    completion_written=False
    def finish_then_close_reader(self,*args,**kwargs):
        nonlocal completion_written
        result=original_finish(self,*args,**kwargs)
        if kwargs.get('outcome')=='RESPONSE_OBSERVED':
            completion_written=True
        return result
    def fail_after_completion(self,*args,**kwargs):
        if completion_written:
            raise RuntimeError('private post-completion request reader detail')
        return original_receipt(self,*args,**kwargs)
    monkeypatch.setattr(PaperProviderPostBudget,'finish',finish_then_close_reader)
    monkeypatch.setattr(BrokerRequestJournal,'observation_receipt',
                        fail_after_completion)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
            match='POST_BUDGET_COMPLETION_UNCERTAIN') as error:
        submit(prepared,opener)
    assert 'private post-completion request reader detail' not in str(error.value)
    history=prepared[2].history()
    assert len(history)==1 and history[0]['outcome']=='RESPONSE_OBSERVED'
    assert history[0]['actual_posts']==1
    assert prepared[0].recovery()['requests'][0]['snapshot']['status']=='new'
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1


def test_valid_lookup_during_response_budget_completion_keeps_exact_observation(
        prepared,monkeypatch):
    journal,_,budget=prepared
    original=PaperProviderPostBudget.finish
    advanced=False
    def finish_then_advance(self,*args,**kwargs):
        nonlocal advanced
        result=original(self,*args,**kwargs)
        if kwargs.get('outcome')=='RESPONSE_OBSERVED' and not advanced:
            BrokerRequestJournal(journal.path,ACCOUNT).begin_lookup(
                'nx_fixture',evaluated_at=1001)
            advanced=True
        return result
    monkeypatch.setattr(PaperProviderPostBudget,'finish',finish_then_advance)
    opener=Opener();result=submit(prepared,opener)
    history=budget.history()
    assert advanced and result['response_observation_completion_bracketed'] is True
    assert len(history)==1 and history[0]['outcome']=='RESPONSE_OBSERVED'
    assert history[0]['request_after_head_sha256']==result['journal']['head_sha256']
    assert journal.recovery()['head_sha256']!=result['journal']['head_sha256']
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1


def test_response_completion_outside_witnessed_window_is_not_trusted(
        prepared,monkeypatch):
    original=PaperProviderPostBudget.finish
    def wrong_admission(self,*args,**kwargs):
        if kwargs.get('outcome')=='RESPONSE_OBSERVED':
            kwargs=dict(kwargs,admission=lambda:1001)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(PaperProviderPostBudget,'finish',wrong_admission)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_BUDGET_COMPLETION_UNCERTAIN'):
        submit(prepared,opener)
    history=prepared[2].history()
    assert len(history)==1 and history[0]['outcome']=='RESPONSE_OBSERVED'
    assert history[0]['actual_posts']==1 and history[0]['completed_at']==1001
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


def test_lost_not_sent_safety_closure_ack_preserves_original_failure(
        prepared,monkeypatch):
    original=PaperProviderPostBudget.finish
    def lose_ack(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise RuntimeError('private lost safety closure acknowledgement')
    monkeypatch.setattr(PaperProviderPostBudget,'finish',lose_ack)
    def stop_after_marker():
        retained=prepared[0].recovery()['requests'][0]
        return retained['submission_attempt_marked']
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='EMERGENCY_STOP_ACTIVE') as error:
        submit(prepared,opener,emergency_stop_check=stop_after_marker)
    assert 'private lost safety closure acknowledgement' not in str(error.value)
    history=prepared[2].history()
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0
    assert all(request.get_method()!='POST' for request,_ in opener.requests)


def test_lost_unknown_safety_closure_ack_preserves_original_failure(
        prepared,monkeypatch):
    original=PaperProviderPostBudget.finish
    def lose_ack(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise RuntimeError('private lost safety closure acknowledgement')
    monkeypatch.setattr(PaperProviderPostBudget,'finish',lose_ack)
    opener=Opener([account(),[],asset(),account(),[],account(),[],asset(),
                   URLError('private transport detail')])
    with pytest.raises(module.PaperSubmissionError,
                       match='OUTCOME_UNKNOWN_LOOKUP_REQUIRED') as error:
        submit(prepared,opener)
    assert 'private lost safety closure acknowledgement' not in str(error.value)
    history=prepared[2].history()
    assert len(history)==1 and history[0]['outcome']=='OUTCOME_UNKNOWN'
    assert history[0]['actual_posts']==1
    assert prepared[2].inspect()['review_required'] is True
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1


@pytest.mark.parametrize('outcome', ['NOT_SENT','OUTCOME_UNKNOWN'])
def test_safety_closure_outside_last_trusted_window_is_not_trusted(
        prepared,monkeypatch,outcome):
    original=PaperProviderPostBudget.finish
    def wrong_admission(self,*args,**kwargs):
        if kwargs.get('outcome')==outcome:
            kwargs=dict(kwargs,admission=lambda:1001)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(PaperProviderPostBudget,'finish',wrong_admission)
    if outcome=='NOT_SENT':
        def stop_after_marker():
            return prepared[0].recovery()['requests'][0][
                'submission_attempt_marked']
        opener=Opener(); changes=dict(emergency_stop_check=stop_after_marker)
    else:
        opener=Opener([account(),[],asset(),account(),[],account(),[],asset(),
                       URLError('private transport detail')])
        changes={}
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_BUDGET_SAFETY_CLOSURE_UNCERTAIN'):
        submit(prepared,opener,**changes)
    history=prepared[2].history()
    assert len(history)==1 and history[0]['outcome']==outcome
    assert history[0]['actual_posts']==(outcome=='OUTCOME_UNKNOWN')
    assert history[0]['completed_at']==1001
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==(
        outcome=='OUTCOME_UNKNOWN')


@pytest.mark.parametrize('outcome', ['NOT_SENT','OUTCOME_UNKNOWN'])
def test_safety_closure_request_head_advance_during_budget_finish_is_uncertain(
        prepared,monkeypatch,outcome):
    journal,_,budget=prepared
    original=PaperProviderPostBudget.finish
    advanced=False
    def finish_then_advance(self,*args,**kwargs):
        nonlocal advanced
        result=original(self,*args,**kwargs)
        if kwargs.get('outcome')==outcome and not advanced:
            BrokerRequestJournal(journal.path,ACCOUNT).begin_lookup(
                'nx_fixture',evaluated_at=1001)
            advanced=True
        return result
    monkeypatch.setattr(PaperProviderPostBudget,'finish',finish_then_advance)
    if outcome=='NOT_SENT':
        def stop_after_marker():
            return journal.recovery()['requests'][0][
                'submission_attempt_marked']
        opener=Opener(); changes=dict(emergency_stop_check=stop_after_marker)
    else:
        opener=Opener([account(),[],asset(),account(),[],account(),[],asset(),
                       URLError('private transport detail')])
        changes={}
    with pytest.raises(module.PaperSubmissionError,
                       match='POST_BUDGET_SAFETY_CLOSURE_UNCERTAIN'):
        submit(prepared,opener,**changes)
    retained=journal.recovery()['requests'][0]
    history=budget.history()
    assert advanced and retained['lookup']['pending'] is True
    assert len(history)==1 and history[0]['outcome']==outcome
    assert history[0]['actual_posts']==(outcome=='OUTCOME_UNKNOWN')
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==(
        outcome=='OUTCOME_UNKNOWN')


@pytest.mark.parametrize('finish_result',[RuntimeError('private closure detail'),'f'*64])
def test_uncommitted_safety_closure_is_fixed_pending_review(
        prepared,monkeypatch,finish_result):
    def uncommitted(*_args,**_kwargs):
        if isinstance(finish_result,BaseException):
            raise finish_result
        return finish_result
    monkeypatch.setattr(PaperProviderPostBudget,'finish',uncommitted)
    def stop_after_marker():
        retained=prepared[0].recovery()['requests'][0]
        return retained['submission_attempt_marked']
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
            match='POST_BUDGET_SAFETY_CLOSURE_UNCERTAIN') as error:
        submit(prepared,opener,emergency_stop_check=stop_after_marker)
    assert 'private closure detail' not in str(error.value)
    state=prepared[2].inspect()
    assert state['pending_token'] is not None and state['completed_attempts']==0
    assert prepared[2].history()==()
    assert all(request.get_method()!='POST' for request,_ in opener.requests)


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
                   final_account,final_positions,asset()])
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


@pytest.mark.parametrize('final_asset,code',[
    (asset(tradable=False),'ASSET_NOT_ELIGIBLE'),
    (asset(status='inactive'),'ASSET_NOT_ELIGIBLE'),
    (asset(fractionable=False),'ASSET_NOT_ELIGIBLE'),
    (asset(min_order_size='1'),'POST_RESERVATION_ASSET_CHANGED'),
    (asset(min_trade_increment='.25'),'POST_RESERVATION_ASSET_CHANGED'),
    (asset(price_increment='.25'),'POST_RESERVATION_ASSET_CHANGED'),
    ({'malformed':'asset'},'ASSET_NOT_ELIGIBLE'),
])
def test_post_reservation_asset_change_never_posts_and_never_rearms(
        prepared,final_asset,code):
    opener=Opener([account(),[],asset(),account(),[],account(),[],final_asset])
    with pytest.raises(module.PaperSubmissionError,match=code):
        submit(prepared,opener)
    retained=prepared[0].recovery()['requests'][0]
    assert retained['lookup_by_original_client_id_required']
    assert all(request.get_method()!='POST' for request,_ in opener.requests)
    assert prepared[2].history()[0]['outcome']=='NOT_SENT'
    assert prepared[2].history()[0]['actual_posts']==0
    with pytest.raises(module.PaperSubmissionError,match='LOCAL_REVIEW_BLOCKED'):
        submit(prepared,Opener())


def test_credential_mutation_during_final_transport_checkpoint_never_posts(
        prepared):
    opener=Opener();credential_current=True;credential_samples=0
    def credential_check():
        nonlocal credential_samples
        credential_samples += 1
        return credential_current
    def permission_check():
        nonlocal credential_current
        # The second explicit credential sample occurs after the final risk
        # reads.  Mutate during the following POST-adjacent permission check,
        # reproducing the interval that used to follow the last pair check.
        if credential_samples >= 2 and len(opener.requests)==11:
            credential_current=False
        return True
    with pytest.raises(module.PaperSubmissionError,
                       match='CREDENTIALS_CHANGED_AT_TRANSPORT'):
        submit(prepared,opener,permission_check=permission_check,
               credential_check=credential_check)
    retained=prepared[0].recovery()['requests'][0]
    history=prepared[2].history()
    assert credential_samples==3
    assert retained['lookup_by_original_client_id_required']
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0
    with pytest.raises(module.PaperSubmissionError,match='LOCAL_REVIEW_BLOCKED'):
        submit(prepared,Opener())


def test_durable_emergency_stop_during_final_credential_sample_never_posts(
        prepared):
    journal,_,budget=prepared
    opener=Opener();credential_samples=0
    publish=threading.Event();published=threading.Event()
    def persist_stop():
        assert publish.wait(5)
        emergency_intent.persist(journal.path)
        published.set()
    worker=threading.Thread(target=persist_stop)
    worker.start()
    def credential_check():
        nonlocal credential_samples
        credential_samples += 1
        if credential_samples==3:
            publish.set()
            assert published.wait(5)
        return True
    try:
        with pytest.raises(module.PaperSubmissionError,
                           match='PAPER_SUBMISSION_EMERGENCY_STOP_ACTIVE'):
            submit(prepared,opener,credential_check=credential_check,
                   emergency_stop_check=lambda:emergency_intent.requested(
                       journal.path))
    finally:
        publish.set();worker.join(5)
    retained=journal.recovery()['requests'][0]
    history=budget.history()
    assert not worker.is_alive() and published.is_set()
    assert credential_samples==3 and emergency_intent.requested(journal.path)
    assert retained['lookup_by_original_client_id_required']
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0
    with pytest.raises(module.PaperSubmissionError,match='LOCAL_REVIEW_BLOCKED'):
        submit(prepared,Opener())


def test_durable_stop_during_final_locked_replay_never_posts(
        prepared,monkeypatch):
    journal,_,budget=prepared
    entered=threading.Event();published=threading.Event()
    original=BrokerRequestJournal.locked_request_snapshot
    @contextmanager
    def blocked_snapshot(self,*args,**kwargs):
        with original(self,*args,**kwargs) as read:
            def blocked_read():
                entered.set();assert published.wait(5);return read()
            yield blocked_read
    monkeypatch.setattr(BrokerRequestJournal,'locked_request_snapshot',
                        blocked_snapshot)
    def publish_stop():
        assert entered.wait(5)
        emergency_intent.persist(journal.path)
        published.set()
    worker=threading.Thread(target=publish_stop);worker.start();opener=Opener()
    try:
        with pytest.raises(module.PaperSubmissionError,
                           match='PAPER_SUBMISSION_EMERGENCY_STOP_ACTIVE'):
            submit(prepared,opener)
    finally:
        published.set();worker.join(5)
    retained=journal.recovery()['requests'][0];history=budget.history()
    assert not worker.is_alive() and entered.is_set()
    assert retained['lookup_by_original_client_id_required']
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0


def test_explicit_owner_stop_root_is_not_inferred_from_broker_journal(
        prepared,tmp_path):
    journal,_,budget=prepared
    runtime=tmp_path/'runtime';runtime.mkdir()
    owner_order_path=runtime/'orders.sqlite3'
    credential_samples=0
    def credential_check():
        nonlocal credential_samples
        credential_samples+=1
        if credential_samples==3:
            emergency_intent.persist(owner_order_path)
        return True
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='PAPER_SUBMISSION_EMERGENCY_STOP_ACTIVE'):
        submit(prepared,opener,credential_check=credential_check,
            emergency_stop_check=lambda:emergency_intent.requested(
                owner_order_path),
            emergency_stop_order_path=str(owner_order_path.resolve()))
    history=budget.history();retained=journal.recovery()['requests'][0]
    assert credential_samples==3 and retained['lookup_by_original_client_id_required']
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0


def test_trusted_transport_deadline_expires_during_final_guard(
        prepared,monkeypatch):
    samples=iter((0,5))
    monkeypatch.setattr(module,'_TRUSTED_MONOTONIC',lambda:next(samples))
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,match='TIME_BUDGET'):
        submit(prepared,opener)
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)
    history=prepared[2].history()
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0


def test_trusted_transport_deadline_includes_final_checkpoint_work(
        prepared,monkeypatch):
    trusted_now=0
    monkeypatch.setattr(module,'_TRUSTED_MONOTONIC',lambda:trusted_now)
    original=module._Session.checkpoint
    def delayed_checkpoint(self):
        nonlocal trusted_now
        result=original(self)
        if self.request_head_guard is not None and self.requests==11:
            trusted_now=17
        return result
    monkeypatch.setattr(module._Session,'checkpoint',delayed_checkpoint)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,match='TIME_BUDGET'):
        submit(prepared,opener)
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)
    history=prepared[2].history()
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0


@pytest.mark.parametrize('store_index',['request','budget'])
def test_final_credential_store_replacement_never_posts(
        prepared,tmp_path,store_index):
    journal,_,budget=prepared
    target=journal.path if store_index=='request' else budget.path
    samples=0;replaced=False
    def credential_check():
        nonlocal samples,replaced
        samples+=1
        if samples==3:
            replacement=tmp_path/f'late-{store_index}.sqlite3'
            replacement.write_bytes(target.read_bytes())
            os.replace(replacement,target);replaced=True
        return True
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,match='STORE_BINDING_CHANGED'):
        submit(prepared,opener,credential_check=credential_check)
    assert samples==3 and replaced
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)
    history=PaperProviderPostBudget(budget.path).history()
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0


def test_typed_transport_revocation_linearizes_before_or_after_opener_entry(
        prepared):
    entered=threading.Event();release=threading.Event()
    revocation_started=threading.Event();revoked=threading.Event()
    class BlockingOpener(Opener):
        def open(self,request,timeout):
            if request.get_method()=='POST':
                entered.set();assert release.wait(5)
            return super().open(request,timeout)
    authorization=module.PaperTransportAuthorizationLease()
    authorization.arm_once(KEY,SECRET,confirm_single_attempt=True)
    opener=BlockingOpener();errors=[]
    worker=threading.Thread(target=lambda:_capture(
        errors,lambda:submit(prepared,opener,
            transport_authorization=authorization)))
    def revoke():
        revocation_started.set();authorization.revoke();revoked.set()
    revoker=threading.Thread(target=revoke)
    worker.start()
    try:
        assert entered.wait(5)
        revoker.start()
        assert revocation_started.wait(5)
        assert not revoked.wait(.05)
        assert not any(request.get_method()=='POST'
                       for request,_ in opener.requests)
    finally:
        release.set();worker.join(5);revoker.join(5)
    assert not worker.is_alive() and not revoker.is_alive()
    assert not errors and revoked.is_set()
    assert sum(request.get_method()=='POST'
               for request,_ in opener.requests)==1


def test_request_head_writer_cannot_commit_before_opener_entry_returns(
        prepared):
    journal,_,_=prepared
    entered=threading.Event();release=threading.Event()
    writer_started=threading.Event();committed=threading.Event()
    class BlockingOpener(Opener):
        def open(self,request,timeout):
            if request.get_method()=='POST':
                entered.set();assert release.wait(5)
            return super().open(request,timeout)
    opener=BlockingOpener();submission_errors=[];writer_errors=[]
    worker=threading.Thread(target=lambda:_capture(
        submission_errors,lambda:submit(prepared,opener)))
    def advance_head():
        try:
            writer_started.set()
            BrokerRequestJournal(journal.path,ACCOUNT).begin_lookup(
                'nx_fixture',evaluated_at=1001)
            committed.set()
        except BaseException as error:
            writer_errors.append(error)
    writer=threading.Thread(target=advance_head)
    worker.start()
    try:
        assert entered.wait(5)
        writer.start()
        assert writer_started.wait(5)
        assert not committed.wait(.05)
        assert not any(request.get_method()=='POST'
                       for request,_ in opener.requests)
    finally:
        release.set();worker.join(5);writer.join(5)
    assert not worker.is_alive() and not writer.is_alive()
    assert committed.is_set() and not writer_errors
    assert sum(request.get_method()=='POST'
               for request,_ in opener.requests)==1
    assert (not submission_errors or
            all(isinstance(error,module.PaperSubmissionError) for
                error in submission_errors))
    history=prepared[2].history()
    assert len(history)==1 and history[0]['outcome'] in (
        'RESPONSE_OBSERVED','OUTCOME_UNKNOWN')
    assert history[0]['actual_posts']==1


def test_preopen_request_guard_failure_is_fixed_not_sent(prepared,monkeypatch):
    @contextmanager
    def failed_guard(*_args,**_kwargs):
        raise ValueError('private guard detail')
        yield
    monkeypatch.setattr(BrokerRequestJournal,'locked_request_snapshot',
                        failed_guard)
    opener=Opener()
    with pytest.raises(module.PaperSubmissionError,
                       match='PRE_POST_TRANSPORT_GUARD_FAILED') as error:
        submit(prepared,opener)
    assert 'private guard detail' not in str(error.value)
    assert not any(request.get_method()=='POST' for request,_ in opener.requests)
    history=prepared[2].history()
    assert len(history)==1 and history[0]['outcome']=='NOT_SENT'
    assert history[0]['actual_posts']==0


def test_process_signal_on_guard_exit_closes_unentered_response(
        prepared,monkeypatch):
    original=BrokerRequestJournal.locked_request_snapshot
    @contextmanager
    def signaled_guard(self,*args,**kwargs):
        with original(self,*args,**kwargs) as read:
            yield read
            raise KeyboardInterrupt
    monkeypatch.setattr(BrokerRequestJournal,'locked_request_snapshot',
                        signaled_guard)
    opener=Opener()
    with pytest.raises(KeyboardInterrupt):
        submit(prepared,opener)
    assert sum(request.get_method()=='POST'
               for request,_ in opener.requests)==1
    assert opener.responses[-1].closed is True
    history=prepared[2].history()
    assert len(history)==1 and history[0]['outcome']=='OUTCOME_UNKNOWN'
    assert history[0]['actual_posts']==1


def test_credential_check_exception_is_fixed_and_post_silent(prepared):
    opener=Opener()
    def failed_check():
        raise RuntimeError('private vault detail')
    with pytest.raises(module.PaperSubmissionError,
                       match='PAPER_SUBMISSION_CREDENTIAL_CHECK_FAILED') as error:
        submit(prepared,opener,credential_check=failed_check)
    assert 'private vault detail' not in str(error.value)
    assert all(request.get_method()!='POST' for request,_ in opener.requests)
    assert prepared[0].recovery()['requests'][0]['submission_attempt_marked'] is False
    assert prepared[2].inspect()['attempts']==0


def test_post_transport_loss_preserves_unknown_and_cannot_retry(prepared):
    opener=Opener([account(),[],asset(),account(),[],account(),[],asset(),
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


@pytest.mark.parametrize('control', ['permission', 'emergency_stop'])
def test_parsed_response_persistence_does_not_require_control_reauthorization(
        prepared, monkeypatch, control):
    opener=Opener();response_returned=False;observed_during_control=[]
    original=module._Session.request
    def request(self,url,**kwargs):
        nonlocal response_returned
        result=original(self,url,**kwargs)
        if url==module.ORDER_URL:response_returned=True
        return result
    monkeypatch.setattr(module._Session,'request',request)
    def permission_check():
        observed_during_control.append(response_returned)
        return not response_returned if control=='permission' else True
    def emergency_stop_check():
        observed_during_control.append(response_returned)
        return response_returned if control=='emergency_stop' else False
    result=submit(prepared,opener,permission_check=permission_check,
                  emergency_stop_check=emergency_stop_check)
    retained=prepared[0].recovery()['requests'][0]
    history=prepared[2].history()
    assert result['post_response_persistence_stop_independent'] is True
    assert response_returned and observed_during_control
    assert not any(observed_during_control)
    assert retained['snapshot']['status']=='new'
    assert len(history)==1 and history[0]['outcome']=='RESPONSE_OBSERVED'
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1


@pytest.mark.parametrize('control', ['permission', 'emergency_stop'])
def test_durable_response_completion_does_not_require_control_reauthorization(
        prepared, control):
    journal=prepared[0];opener=Opener();observed_during_control=[]
    def response_is_retained():
        return journal.recovery()['requests'][0]['snapshot'] is not None
    def permission_check():
        observed=response_is_retained();observed_during_control.append(observed)
        return not observed if control=='permission' else True
    def emergency_stop_check():
        observed=response_is_retained();observed_during_control.append(observed)
        return observed if control=='emergency_stop' else False
    result=submit(prepared,opener,permission_check=permission_check,
                  emergency_stop_check=emergency_stop_check)
    history=prepared[2].history()
    assert result['post_response_local_completion_stop_independent'] is True
    assert observed_during_control and not any(observed_during_control)
    assert len(history)==1 and history[0]['outcome']=='RESPONSE_OBSERVED'
    assert history[0]['actual_posts']==1
    assert sum(request.get_method()=='POST' for request,_ in opener.requests)==1


def test_invalid_post_response_preserves_unknown_without_secret_or_body_echo(prepared):
    opener=Opener([account(),[],asset(),account(),[],account(),[],asset(),
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
    authorization=module.PaperTransportAuthorizationLease()
    authorization.arm_once(KEY,SECRET,confirm_single_attempt=True)
    options=dict(expected_account_id=ACCOUNT,expected_head=selected['head_sha256'],
        expected_payload_sha256=selected['plan']['payload_sha256'],
        post_budget=PaperProviderPostBudget(journal.path.with_name('sell-post-budget.sqlite3')),
        expected_post_budget_head='0'*64,confirm_paper_order=True,
        confirm_single_attempt=True,opener=Opener([account(),
            [dict(symbol='BTC/USD',asset_class='crypto',side='long',qty='2',qty_available='1')],asset()]),
        wall_clock=lambda:1000,elapsed_clock=lambda:50,permission_check=lambda:True,
        emergency_stop_check=lambda:False,credential_check=lambda:True,
        transport_authorization=authorization,
        emergency_stop_order_path=str(journal.path.resolve()),
        confirm_emergency_stop_binding=True)
    before=journal.path.read_bytes()
    with pytest.raises(module.PaperSubmissionError,match='BTC_AVAILABLE_INSUFFICIENT'):
        module.submit_once(journal,'sell_fixture',KEY,SECRET,**options)
    assert journal.path.read_bytes()==before
