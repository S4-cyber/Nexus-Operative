"""Counterfactual forward intent provenance and durable unsendability."""
import copy
from contextlib import contextmanager
import json
import os
import shutil
import sqlite3

import pytest

from nexus import alpaca_paper_submission
from nexus import broker_request_journal as journal_module
from nexus import emergency_intent
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
    assert reconciled['review_input_detached']
    assert reconciled['order_gateway_exact']
    assert reconciled['order_gateway_policy_bound']
    assert reconciled['order_replay_implementation_bound']
    assert reconciled['order_journal_identity_rechecked']
    assert reconciled['order_file_set_bound']
    assert reconciled['order_file_fingerprints_rechecked']
    assert reconciled['emergency_cut_latch_clear_rechecked']
    assert reconciled['request_journal_exact']
    assert reconciled['request_guard_implementation_bound']
    assert reconciled['request_journal_identity_rechecked_from_entry']
    assert reconciled['request_snapshot_replayed']
    assert reconciled['journal_file_fingerprint_rechecked']
    assert reconciled['request_guard_spans_second_order_replay']
    assert reconciled['final_request_snapshot_replayed']
    assert reconciled['post_guard_order_snapshot_replayed']
    assert reconciled['post_guard_request_source_rechecked']
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


def test_request_guard_spans_second_order_replay_and_rechecks_after_it(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    original=intent._replayed_order_snapshot;calls=[]
    mtime=store.path.stat().st_mtime_ns
    def mutate_during_final_order_replay(*args,**kwargs):
        value=original(*args,**kwargs);calls.append(value['snapshot_sha256'])
        if len(calls)==2:
            os.utime(store.path,ns=(store.path.stat().st_atime_ns,
                mtime+1_000_000_000))
        return value
    monkeypatch.setattr(intent,'_replayed_order_snapshot',
        mutate_during_final_order_replay)
    with pytest.raises(ValueError,match='LOCKED_PLAN_FILE_SET_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert len(calls)==2


def test_persisted_reconciliation_detaches_review_before_journal_access(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    baseline=intent.reconcile_persisted(copy.deepcopy(review),store,
        'nx_forward_fixture',expected_head=head,order_gateway=orders)
    original=intent._replayed_order_snapshot;calls=[]
    def mutate_caller_after_first_replay(frozen_review,order_gateway):
        value=original(frozen_review,order_gateway);calls.append(value)
        if len(calls)==1:
            review['review_receipt']['receipt_sha256']='0'*64
            review['stored_state']='RUNNING'
        return value
    monkeypatch.setattr(intent,'_replayed_order_snapshot',
        mutate_caller_after_first_replay)
    assert intent.reconcile_persisted(review,store,'nx_forward_fixture',
        expected_head=head,order_gateway=orders)==baseline
    assert len(calls)==3


def test_persisted_reconciliation_requires_real_durable_order_gateway(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    class MatchingFake:
        calls=0
        def session_orders_snapshot(self,session):
            self.calls+=1
            return copy.deepcopy(review['order_journal_snapshot'])
    fake=MatchingFake()
    with pytest.raises(ValueError,match='ORDER_GATEWAY_INVALID'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=fake)
    assert fake.calls==0


def test_persisted_reconciliation_rejects_recovery_only_order_gateway(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3',
        rotation_recovery=True)
    (orders.path.parent/'nexus-order-rotation.intent').write_text('{}')
    with pytest.raises(ValueError,match='ORDER_GATEWAY_POLICY_INVALID'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)


def test_persisted_reconciliation_rejects_order_gateway_policy_change(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    original=intent._replayed_order_snapshot;calls=[]
    def disable_checkpoints_after_first_replay(frozen_review,order_gateway):
        value=original(frozen_review,order_gateway);calls.append(value)
        if len(calls)==1:order_gateway.checkpoints_enabled=False
        return value
    monkeypatch.setattr(intent,'_replayed_order_snapshot',
        disable_checkpoints_after_first_replay)
    with pytest.raises(ValueError,match='ORDER_GATEWAY_POLICY_INVALID'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert len(calls)==1


def test_persisted_reconciliation_rejects_order_replay_class_substitution(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3');calls=[]
    def matching_fake(self,session):
        calls.append(session);return copy.deepcopy(review['order_journal_snapshot'])
    monkeypatch.setattr(DurableOrderGateway,'session_orders_snapshot',matching_fake)
    with pytest.raises(ValueError,match='ORDER_IMPLEMENTATION_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert calls==[]


def test_persisted_reconciliation_rechecks_order_replay_implementation(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    original=intent._replayed_order_snapshot;calls=[]
    def replace_after_first(frozen_review,order_gateway):
        value=original(frozen_review,order_gateway);calls.append(value)
        if len(calls)==1:
            monkeypatch.setattr(DurableOrderGateway,'session_orders_snapshot',
                lambda self,session:copy.deepcopy(
                    frozen_review['order_journal_snapshot']))
        return value
    monkeypatch.setattr(intent,'_replayed_order_snapshot',replace_after_first)
    with pytest.raises(ValueError,match='ORDER_IMPLEMENTATION_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert len(calls)==1


def test_persisted_reconciliation_rejects_emergency_reader_substitution(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    before=store.path.read_bytes();emergency_intent.persist(orders.path);calls=[]
    def forged_clear(path):
        calls.append(path)
        return dict(status='CLEAR',requested=False,canonical=False,
            record_sha256=None),None
    monkeypatch.setattr(emergency_intent,'review_sample',forged_clear)
    with pytest.raises(ValueError,match='EMERGENCY_IMPLEMENTATION_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert calls==[] and emergency_intent.requested(orders.path)
    assert store.path.read_bytes()==before
    assert not DurableOrderGateway(orders.path).recovery_report()['halted']


def test_persisted_reconciliation_rechecks_emergency_reader_implementation(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    before=store.path.read_bytes();original=intent._replayed_order_snapshot
    replays=[];fake_calls=[]
    def forged_clear(path):
        fake_calls.append(path)
        return dict(status='CLEAR',requested=False,canonical=False,
            record_sha256=None),None
    def replace_after_first(frozen_review,order_gateway):
        value=original(frozen_review,order_gateway);replays.append(value)
        if len(replays)==1:
            monkeypatch.setattr(emergency_intent,'review_sample',forged_clear)
        return value
    monkeypatch.setattr(intent,'_replayed_order_snapshot',replace_after_first)
    with pytest.raises(ValueError,match='EMERGENCY_IMPLEMENTATION_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert len(replays)==1 and fake_calls==[]
    assert store.path.read_bytes()==before


def test_emergency_reader_binding_survives_consumer_import_after_substitution(
        monkeypatch):
    import importlib
    calls=[]
    def forged_clear(path):
        calls.append(path)
        return dict(status='CLEAR',requested=False,canonical=False,
            record_sha256=None),None
    monkeypatch.setattr(emergency_intent,'review_sample',forged_clear)
    importlib.reload(intent)
    with pytest.raises(ValueError,match='EMERGENCY_IMPLEMENTATION_CHANGED'):
        intent._require_emergency_cut_clear((('/unused',0,0),))
    assert calls==[]


def test_missing_emergency_reader_has_fixed_implementation_error(monkeypatch):
    monkeypatch.delattr(emergency_intent,'review_sample')
    with pytest.raises(ValueError,match='EMERGENCY_IMPLEMENTATION_CHANGED'):
        intent._require_emergency_cut_clear((('/unused',0,0),))


def test_persisted_reconciliation_rejects_byte_identical_order_path_swap(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    clone=tmp_path/'orders-clone.sqlite3';shutil.copy2(orders.path,clone)
    original=intent._replayed_order_snapshot;calls=[]
    def swap_after_first_replay(frozen_review,order_gateway):
        value=original(frozen_review,order_gateway);calls.append(value)
        if len(calls)==1:order_gateway.path=clone
        return value
    monkeypatch.setattr(intent,'_replayed_order_snapshot',swap_after_first_replay)
    with pytest.raises(ValueError,match='ORDER_SOURCE_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert len(calls)==1


@pytest.mark.parametrize('target',('orders','portfolio'))
def test_persisted_reconciliation_rejects_order_file_set_change(
        tmp_path,monkeypatch,target):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    path=(orders.path if target=='orders' else
        orders.path.parent/'paper_portfolio.sqlite3')
    original=intent._replayed_order_snapshot;calls=[]
    def mutate_after_first_replay(frozen_review,order_gateway):
        value=original(frozen_review,order_gateway);calls.append(value)
        if len(calls)==1:
            with sqlite3.connect(path) as db:
                db.execute('PRAGMA user_version=1')
        return value
    monkeypatch.setattr(intent,'_replayed_order_snapshot',
        mutate_after_first_replay)
    with pytest.raises(ValueError,match='ORDER_SOURCE_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert len(calls)==1


def test_persisted_reconciliation_requires_real_request_journal(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    with store.locked_request_plan_snapshot('nx_forward_fixture',
            expected_head=head) as read:
        snapshot=read()
    class MatchingFake:
        account_digest=store.account_digest
        calls=0
        def locked_request_plan_snapshot(self,*args,**kwargs):
            self.calls+=1
            class Guard:
                def __enter__(inner):return lambda:copy.deepcopy(snapshot)
                def __exit__(inner,*exc):return False
            return Guard()
    fake=MatchingFake()
    with pytest.raises(ValueError,match='REQUEST_JOURNAL_INVALID'):
        intent.reconcile_persisted(review,fake,'nx_forward_fixture',
            expected_head=head,order_gateway=DurableOrderGateway(
                tmp_path/'forward'/'orders.sqlite3'))
    assert fake.calls==0


def test_persisted_reconciliation_rejects_request_guard_class_substitution(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256'];calls=[]
    def matching_fake(self,*args,**kwargs):
        calls.append(args);return object()
    monkeypatch.setattr(BrokerRequestJournal,'locked_request_plan_snapshot',
        matching_fake)
    with pytest.raises(ValueError,match='REQUEST_IMPLEMENTATION_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=DurableOrderGateway(
                tmp_path/'forward'/'orders.sqlite3'))
    assert calls==[]


def test_persisted_reconciliation_rechecks_request_guard_implementation(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    original=intent.reconcile_current;calls=[]
    def replace_after_compare(*args,**kwargs):
        value=original(*args,**kwargs);calls.append(value)
        monkeypatch.setattr(BrokerRequestJournal,'locked_request_plan_snapshot',
            lambda self,*a,**k:object())
        return value
    monkeypatch.setattr(intent,'reconcile_current',replace_after_compare)
    with pytest.raises(ValueError,match='REQUEST_IMPLEMENTATION_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=DurableOrderGateway(
                tmp_path/'forward'/'orders.sqlite3'))
    assert len(calls)==1


def test_persisted_reconciliation_rejects_byte_identical_request_path_swap(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    clone=tmp_path/'requests-clone.sqlite3';shutil.copy2(store.path,clone)
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    original=intent._replayed_order_snapshot;calls=[]
    def swap_request_after_first_replay(frozen_review,order_gateway):
        value=original(frozen_review,order_gateway);calls.append(value)
        if len(calls)==1:store.path=clone
        return value
    monkeypatch.setattr(intent,'_replayed_order_snapshot',
        swap_request_after_first_replay)
    with pytest.raises(ValueError,match='REQUEST_SOURCE_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert len(calls)==1


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


def test_pending_emergency_cut_latch_rejects_nonhalted_reconciliation(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    before=store.path.read_bytes();emergency_intent.persist(orders.path)
    assert emergency_intent.requested(orders.path)
    assert not DurableOrderGateway(orders.path).recovery_report()['halted']
    with pytest.raises(ValueError,match='EMERGENCY_CUT_ACTIVE'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert store.path.read_bytes()==before


def test_emergency_cut_latch_remains_independent_during_request_reconciliation(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    before=store.path.read_bytes();original=intent.reconcile_current;requested=[]
    def request_cut_after_compare(*args,**kwargs):
        value=original(*args,**kwargs);emergency_intent.persist(orders.path)
        requested.append(emergency_intent.requested(orders.path));return value
    monkeypatch.setattr(intent,'reconcile_current',request_cut_after_compare)
    with pytest.raises(ValueError,match='EMERGENCY_CUT_ACTIVE'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert requested==[True] and store.path.read_bytes()==before
    assert not DurableOrderGateway(orders.path).recovery_report()['halted']


def test_emergency_cut_after_final_request_source_check_invalidates_reconciliation(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    before=store.path.read_bytes();original=intent._require_request_source
    calls=[]
    def request_cut_after_final_source_check(*args,**kwargs):
        value=original(*args,**kwargs);calls.append(True)
        if len(calls)==6:emergency_intent.persist(orders.path)
        return value
    monkeypatch.setattr(intent,'_require_request_source',
        request_cut_after_final_source_check)
    with pytest.raises(ValueError,match='EMERGENCY_CUT_ACTIVE'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert len(calls)==6 and emergency_intent.requested(orders.path)
    assert store.path.read_bytes()==before
    assert not DurableOrderGateway(orders.path).recovery_report()['halted']


def test_halt_during_request_guard_exit_invalidates_reconciliation(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    before=store.path.read_bytes()
    original=BrokerRequestJournal._write_connection;halted=[]
    @contextmanager
    def halt_after_guard_body(self):
        with original(self) as db:
            yield db
        halted.append(orders.emergency_halt()['halted'])
    monkeypatch.setattr(BrokerRequestJournal,'_write_connection',
        halt_after_guard_body)
    with pytest.raises(ValueError,match='ORDER_SNAPSHOT_STALE'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert halted==[True] and store.path.read_bytes()==before
    assert DurableOrderGateway(orders.path).recovery_report()['halted']
    assert not emergency_intent.requested(orders.path)


@pytest.mark.parametrize('target',('orders','portfolio'))
def test_order_file_change_during_request_guard_exit_is_rejected(
        tmp_path,monkeypatch,target):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    path=(orders.path if target=='orders' else
        orders.path.parent/'paper_portfolio.sqlite3')
    original=BrokerRequestJournal._write_connection;mutated=[]
    @contextmanager
    def mutate_after_guard_body(self):
        with original(self) as db:
            yield db
        with sqlite3.connect(path) as changed:
            changed.execute('PRAGMA user_version=2')
        mutated.append(target)
    monkeypatch.setattr(BrokerRequestJournal,'_write_connection',
        mutate_after_guard_body)
    with pytest.raises(ValueError,match='ORDER_SOURCE_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert mutated==[target]


def test_request_change_during_post_guard_order_replay_is_rejected(
        tmp_path,monkeypatch):
    result,review=candidate(tmp_path/'forward',monkeypatch)
    store=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    store.prepare_forward_intent(result)
    head=store.lookup_request('nx_forward_fixture')['head_sha256']
    orders=DurableOrderGateway(tmp_path/'forward'/'orders.sqlite3')
    original=intent._replayed_order_snapshot;calls=[]
    mtime=store.path.stat().st_mtime_ns
    def mutate_request_during_post_guard_replay(*args,**kwargs):
        value=original(*args,**kwargs);calls.append(value['snapshot_sha256'])
        if len(calls)==3:
            os.utime(store.path,ns=(store.path.stat().st_atime_ns,
                mtime+1_000_000_000))
        return value
    monkeypatch.setattr(intent,'_replayed_order_snapshot',
        mutate_request_during_post_guard_replay)
    with pytest.raises(ValueError,match='REQUEST_SOURCE_CHANGED'):
        intent.reconcile_persisted(review,store,'nx_forward_fixture',
            expected_head=head,order_gateway=orders)
    assert len(calls)==3


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
