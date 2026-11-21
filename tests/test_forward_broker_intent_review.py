"""Counterfactual forward intent provenance and durable unsendability."""
import copy
import json
import os
import sqlite3

import pytest

from nexus import alpaca_paper_submission
from nexus import broker_request_journal as journal_module
from nexus import forward_broker_intent_review as intent
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.durable_orders import DurableOrderGateway
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


def test_cold_bound_intent_reconciles_to_fresh_stopped_review_without_write(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    cold=BrokerRequestJournal(store.path,ACCOUNT)
    head=cold.lookup_request('nx_forward_fixture')['head_sha256']
    before=cold.path.read_bytes()
    reconciled=intent.reconcile_persisted(review,cold,'nx_forward_fixture',
        expected_head=head,order_gateway=DurableOrderGateway(
            tmp_path/'forward'/'orders.sqlite3'))
    assert reconciled['mode']==intent.RECONCILIATION_MODE
    assert reconciled['profile']==intent.RECONCILIATION_PROFILE
    assert reconciled['matched_local_snapshot']
    assert reconciled['request_snapshot_replayed']
    assert reconciled['journal_file_fingerprint_rechecked']
    assert reconciled['order_snapshot_replayed']
    assert reconciled['order_snapshot_stable_during_reconciliation']
    assert reconciled['durable_halt_clear']
    assert reconciled['intent_sha256']==result['binding']['intent_sha256']
    assert all(reconciled[key] is False for key in intent.FALSE_FLAGS)
    assert cold.path.read_bytes()==before
    assert intent.reconcile_persisted(review,cold,'nx_forward_fixture',
        expected_head=head,order_gateway=DurableOrderGateway(
            tmp_path/'forward'/'orders.sqlite3'))==reconciled


def test_reconciliation_rejects_stale_head_legacy_and_changed_source(
        tmp_path,monkeypatch):
    from test_alpaca_paper_contract import plan
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    changed=copy.deepcopy(review)
    changed['review_receipt']['receipt_sha256']='0'*64
    with pytest.raises(ValueError,match='FORWARD_BROKER_INTENT_REVIEW_INVALID'):
        intent.reconcile_persisted(changed,store,'nx_forward_fixture',
            expected_head=head,order_gateway=DurableOrderGateway(
                tmp_path/'forward'/'orders.sqlite3'))
    store.prepare(plan(client_order_id='nx_unrelated'))
    before=store.path.read_bytes()
    with pytest.raises(ValueError,match='LOCKED_PLAN_HEAD_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=DurableOrderGateway(
                tmp_path/'forward'/'orders.sqlite3'))
    assert store.path.read_bytes()==before
    legacy=BrokerRequestJournal(tmp_path/'legacy.sqlite3',ACCOUNT)
    legacy.prepare(plan())
    legacy_head=legacy.lookup_request('nx_fixture')['head_sha256']
    with pytest.raises(ValueError,match='RECONCILIATION_INVALID'):
        intent.reconcile_persisted(review,legacy,'nx_fixture',
            expected_head=legacy_head,order_gateway=DurableOrderGateway(
                tmp_path/'forward'/'orders.sqlite3'))


def test_pure_reconciliation_rejects_cross_source_and_summary_mutations(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    with store.locked_request_plan_snapshot('nx_forward_fixture',
            expected_head=head) as read:
        snapshot=read()
    mutations=[]
    changed=copy.deepcopy(snapshot);changed['account_sha256']='0'*64;mutations.append(changed)
    changed=copy.deepcopy(snapshot);changed['request']['recovery_state']='SUBMISSION_UNKNOWN_LOOKUP_REQUIRED';mutations.append(changed)
    changed=copy.deepcopy(snapshot);changed['request']['lookup']['attempts']=True;mutations.append(changed)
    changed=copy.deepcopy(snapshot);changed['request']['forward_intent_bound']=False;mutations.append(changed)
    changed=copy.deepcopy(snapshot);changed['plan']['forward_intent_binding']['session']='other';mutations.append(changed)
    for changed in mutations:
        with pytest.raises(ValueError,match='RECONCILIATION_INVALID'):
            intent.reconcile_current(review,changed,
                expected_account_sha256=snapshot['account_sha256'],
                expected_head=snapshot['head_sha256'])
    other_review=copy.deepcopy(review)
    other_review['session']='other'
    with pytest.raises(ValueError):intent.reconcile_current(other_review,snapshot,
        expected_account_sha256=snapshot['account_sha256'],
        expected_head=snapshot['head_sha256'])


def test_guard_rechecks_request_file_after_final_reconciliation(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    original=intent.reconcile_current;mtime=store.path.stat().st_mtime_ns
    def mutate_after_compare(*args,**kwargs):
        value=original(*args,**kwargs)
        os.utime(store.path,ns=(store.path.stat().st_atime_ns,
            mtime+1_000_000_000))
        return value
    monkeypatch.setattr(intent,'reconcile_current',mutate_after_compare)
    with pytest.raises(ValueError,match='LOCKED_PLAN_FILE_SET_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=DurableOrderGateway(
                tmp_path/'forward'/'orders.sqlite3'))


def test_lost_bound_prepare_ack_replays_once_without_resend(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
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
    head=cold.lookup_request('nx_forward_fixture')['head_sha256']
    assert intent.reconcile_persisted(review,cold,'nx_forward_fixture',
        expected_head=head,order_gateway=DurableOrderGateway(
            tmp_path/'forward'/'orders.sqlite3'))['matched_local_snapshot']
    assert cold.recovery()==before


def test_later_emergency_halt_invalidates_retained_review_without_request_write(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    before=store.path.read_bytes()
    assert orders.emergency_halt()['halted']
    assert DurableOrderGateway(orders.path).recovery_report()['halted']
    with pytest.raises(ValueError,match='ORDER_SNAPSHOT_STALE'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert store.path.read_bytes()==before


def test_emergency_halt_remains_independent_during_request_reconciliation(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    original=intent.reconcile_current;halted=[]
    def halt_after_compare(*args,**kwargs):
        value=original(*args,**kwargs)
        halted.append(orders.emergency_halt()['halted'])
        return value
    monkeypatch.setattr(intent,'reconcile_current',halt_after_compare)
    with pytest.raises(ValueError,match='ORDER_SNAPSHOT_STALE'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert halted==[True]
    assert DurableOrderGateway(orders.path).recovery_report()['halted']


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
