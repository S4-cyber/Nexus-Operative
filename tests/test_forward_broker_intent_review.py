"""Counterfactual forward intent provenance and durable unsendability."""
import copy
import json
import sqlite3

import pytest

from nexus import alpaca_paper_submission
from nexus import broker_request_journal as journal_module
from nexus import forward_broker_intent_review as intent
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.tier_permissions import configure
from test_paper_forward import setup,warmup


ACCOUNT='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
ASSET=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,
    fractionable=True,min_order_size='0.000000000001',
    min_trade_increment='0.000000000001',price_increment='0.000000000001')


def candidate(tmp_path,monkeypatch,*,sell=False):
    runner,gateway,clock=setup(tmp_path,monkeypatch)
    assert warmup(runner,clock)['decision']=='BUY'
    if sell:
        configure('terrestrial',False);clock.now+=5;clock.price=102
        assert runner.single_cycle()['decision']=='SELL'
    runner.stop('COUNTERFACTUAL_INTENT_FIXTURE')
    review=runner.evidence_report(runner.status()['session'])
    result=intent.build(review,'nx_forward_fixture','100',ASSET,
        metadata_at=1000,evaluated_at=1001)
    return result,review


@pytest.mark.parametrize(('sell','expected'),((False,'BUY'),(True,'SELL')))
def test_stopped_reconciled_fill_builds_deterministic_unsendable_intent(
        tmp_path,monkeypatch,sell,expected):
    result,review=candidate(tmp_path,monkeypatch,sell=sell)
    assert result['mode']==intent.MODE and result['profile']==intent.PROFILE
    assert result['binding']['side']==expected
    assert result['binding']['session']==review['session']
    assert result['binding']['counterfactual_only']
    assert all(result[key] is False for key in intent.FALSE_FLAGS)
    assert all(result['binding'][key] is False for key in intent.FALSE_FLAGS)
    assert intent.normalize_plan(result['plan'])==result['plan']
    assert json.dumps(result,sort_keys=True)==json.dumps(
        intent.build(review,'nx_forward_fixture','100',ASSET,
            metadata_at=1000,evaluated_at=1001),sort_keys=True)


def test_review_source_and_preview_mutations_fail_closed(tmp_path,monkeypatch):
    result,review=candidate(tmp_path,monkeypatch)
    mutations=[]
    changed=copy.deepcopy(review);changed['stored_state']='RUNNING';mutations.append(changed)
    changed=copy.deepcopy(review);changed['provisional_active_session']=True;mutations.append(changed)
    changed=copy.deepcopy(review);changed['reasons']=['INJECTED'];changed['local_consistency_ok']=False;mutations.append(changed)
    changed=copy.deepcopy(review)
    changed['durable_event_evidence']['latest_modeled_outcome']['modeled_fill']['quantity']='1'
    mutations.append(changed)
    changed=copy.deepcopy(review)
    changed['review_receipt']['receipt_sha256']='0'*64;mutations.append(changed)
    for changed in mutations:
        with pytest.raises(ValueError,match='FORWARD_BROKER_INTENT_(REVIEW|SOURCE)_INVALID'):
            intent.build(changed,'nx_forward_fixture','100',ASSET,
                metadata_at=1000,evaluated_at=1001)
    with pytest.raises(ValueError):
        intent.build(review,'nx_forward_fixture','1000',ASSET,
            metadata_at=1000,evaluated_at=1001)
    with pytest.raises(ValueError):
        intent.build(review,'nx_forward_fixture','100',ASSET,
            metadata_at=900,evaluated_at=1001)
    changed=copy.deepcopy(result['plan'])
    changed['forward_intent_binding']['quantity']='1'
    with pytest.raises(ValueError,match='INVALID_FORWARD_BROKER_INTENT_PLAN'):
        intent.normalize_plan(changed)


def test_bound_prepare_replays_exactly_and_changed_binding_conflicts(
        tmp_path,monkeypatch):
    result,_=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    first=store.prepare_forward_intent(result)
    assert first['forward_intent_bound'] and not first['duplicate']
    assert store.prepare_forward_intent(result)['duplicate']
    cold=BrokerRequestJournal(store.path,ACCOUNT);recovered=cold.recovery()['requests'][0]
    assert recovered['forward_intent_bound']
    assert recovered['forward_intent_sha256']==result['binding']['intent_sha256']
    changed=copy.deepcopy(result)
    changed['binding']['limit_price']='99'
    changed['plan']['forward_intent_binding']=changed['binding']
    with pytest.raises(ValueError):cold.prepare_forward_intent(changed)
    assert cold.recovery()['requests'][0]==recovered
    with sqlite3.connect(store.path) as db:
        retained=''.join(row[0] for row in db.execute('SELECT body FROM events'))
    assert ACCOUNT not in retained and 'PRIVATE' not in retained


def test_lost_bound_prepare_ack_replays_once_without_resend(
        tmp_path,monkeypatch):
    result,_=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    original=journal_module._sync_committed_store
    def lost(*args,**kwargs):
        original(*args,**kwargs)
        raise OSError('injected lost acknowledgement')
    monkeypatch.setattr(journal_module,'_sync_committed_store',lost)
    with pytest.raises(OSError,match='lost acknowledgement'):
        store.prepare_forward_intent(result)
    monkeypatch.setattr(journal_module,'_sync_committed_store',original)
    cold=BrokerRequestJournal(store.path,ACCOUNT)
    before=cold.recovery()
    assert before['retained_events']==2
    assert before['requests'][0]['forward_intent_bound']
    assert cold.prepare_forward_intent(result)['duplicate']
    assert cold.recovery()==before


def test_legacy_prepare_remains_readable_and_unbound(tmp_path):
    from test_alpaca_paper_contract import plan
    store=BrokerRequestJournal(tmp_path/'legacy.sqlite3',ACCOUNT);store.prepare(plan())
    recovered=BrokerRequestJournal(store.path,ACCOUNT).recovery()['requests'][0]
    assert recovered['forward_intent_bound'] is False
    assert recovered['forward_intent_sha256'] is None


def test_bound_intent_is_rejected_before_callbacks_credentials_or_transport(
        tmp_path,monkeypatch):
    result,_=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    stored=store.prepare_forward_intent(result)
    calls=[]
    review=store.submission_review('nx_forward_fixture',evaluated_at=1002)
    assert review['reasons']==['COUNTERFACTUAL_FORWARD_INTENT_NOT_SENDABLE']
    before=store.path.read_bytes()
    with pytest.raises(ValueError,match='COUNTERFACTUAL_FORWARD_INTENT_NOT_SENDABLE'):
        store.begin_submission_checked('nx_forward_fixture',
            expected_head=store.lookup_request('nx_forward_fixture')['head_sha256'],
            expected_payload_sha256=stored['payload_sha256'],
            admission=lambda:calls.append('admission'))
    with pytest.raises(alpaca_paper_submission.PaperSubmissionError,
            match='COUNTERFACTUAL_FORWARD_INTENT_NOT_SENDABLE'):
        alpaca_paper_submission.submit_once(store,'nx_forward_fixture','bad','bad',
            expected_account_id=ACCOUNT,expected_head='0'*64,
            expected_payload_sha256=stored['payload_sha256'],post_budget=object(),
            expected_post_budget_head='0'*64,confirm_paper_order=True,
            confirm_single_attempt=True,opener=lambda:calls.append('opener'),
            wall_clock=lambda:calls.append('clock'),
            permission_check=lambda:calls.append('permission'),
            emergency_stop_check=lambda:calls.append('stop'),
            credential_check=lambda:calls.append('credential'))
    assert calls==[] and store.path.read_bytes()==before


def test_committed_forward_outcome_without_prepare_stays_explicitly_missing(
        tmp_path,monkeypatch):
    candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'missing.sqlite3',ACCOUNT)
    assert store.recovery()['requests']==[]
    assert not store.path.exists()
