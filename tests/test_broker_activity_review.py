"""Read-only cross-batch supplied observations; never an actual broker account."""
import copy
import json
import subprocess
import sys

import pytest
from nexus import broker_activity_review as review
from nexus.broker_activity_store import BrokerActivityStore
from test_alpaca_activity_normalizer import inputs, split_fills, ACCOUNT

START='2026-10-17T00:00:00Z'; END='2026-10-18T00:00:00Z'


def target(tmp_path): return BrokerActivityStore(tmp_path/'activity.db', ACCOUNT)


def retain(store, rows, orders, start=START, end=END):
    return store.retain(rows, orders, after=start, until=end)


def split_batches():
    rows, orders = split_fills()
    first = copy.deepcopy(orders[:1])
    first[0].update(status='partially_filled',filled_quantity='.4',average_fill_price='85',updated_at='2026-10-17T18:00:00Z')
    return ([rows[0]], first), (rows[1:], orders)


def test_missing_review_no_initialization_and_authority_false(tmp_path):
    store=target(tmp_path); result=store.review()
    assert not store.path.exists() and result['retained_batches']==result['retained_activities']==0
    assert result['status']=='REVIEW_REQUIRED' and result['read_only']
    assert result['requested_window_review']['components']==[]
    assert 'NO_RETAINED_REQUEST_WINDOWS' in result['review_reasons']
    for name in ('activity_coverage_verified','fee_coverage_complete','broker_account_reconciled','spendable_funds_verified',
                 'ledger_applied','broker_connected','ready_for_routing','live_orders_enabled','automatic_continuation'):
        assert result[name] is False


@pytest.mark.parametrize('reverse', [False, True])
def test_distinct_partial_batches_merge_by_reported_snapshot_time_not_arrival(tmp_path, reverse):
    store=target(tmp_path); batches=split_batches()
    for rows, orders in reversed(batches) if reverse else batches: retain(store, rows, orders)
    before=store.path.read_bytes(); result=store.review()
    assert store.path.read_bytes()==before
    assert result['retained_activities']==5 and result['distinct_orders']==2 and result['distinct_order_snapshots']==3
    assert result['normalization_evaluated'] and result['ambiguous_order_ids']==result['snapshot_revision_order_ids']==[]
    assert result['review_reasons']==['ACTIVITY_COVERAGE_UNVERIFIED','FEE_LINK_AND_TIMING_UNVERIFIED']
    normalized=result['activity_normalization']
    assert len(normalized['executions'])==3
    assert normalized['recognized_fee_debits']=={'BTC':'0.001','USD':'0.10989'}
    assert 'PRIVATE' not in json.dumps(result)
    assert all('execution_id' not in fee and 'occurred_at' not in fee for fee in normalized['fee_debits'])


def test_fresh_process_review_agrees_and_does_not_modify_history(tmp_path):
    store=target(tmp_path)
    for rows,orders in split_batches(): retain(store,rows,orders)
    before=store.path.read_bytes()
    code='import json,sys; from nexus.broker_activity_store import BrokerActivityStore; print(json.dumps(BrokerActivityStore(sys.argv[1],sys.argv[2]).review()))'
    result=json.loads(subprocess.check_output([sys.executable,'-c',code,str(store.path),ACCOUNT],text=True))
    assert result==store.review() and store.path.read_bytes()==before


@pytest.mark.parametrize('reverse', [False, True])
def test_equal_time_conflict_never_chooses_a_snapshot(tmp_path, reverse):
    rows,orders=inputs(); changed=copy.deepcopy(orders); changed[0]['status']='canceled'
    store=target(tmp_path); batches=[(rows,orders),([],changed)]
    for r,o in reversed(batches) if reverse else batches: retain(store,r,o)
    result=store.review()
    assert result['ambiguous_order_ids']==[orders[0]['broker_order_id']]
    assert 'EQUAL_TIME_ORDER_SNAPSHOT_CONFLICT' in result['review_reasons']
    assert not result['normalization_evaluated'] and result['activity_normalization'] is None


@pytest.mark.parametrize('change,reason', [
    ({'filled_quantity':'.4','average_fill_price':'85'},'SNAPSHOT_FILLED_QUANTITY_REGRESSION'),
    ({'status':'canceled'},'TERMINAL_SNAPSHOT_STATUS_CHANGED')])
def test_later_revisions_require_review_instead_of_repair(tmp_path,change,reason):
    rows,orders=inputs(); changed=copy.deepcopy(orders)
    changed[0].update(updated_at='2026-10-17T18:00:02Z',**change)
    store=target(tmp_path); retain(store,rows,orders); retain(store,[],changed)
    before=store.path.read_bytes(); result=store.review()
    assert reason in result['review_reasons'] and result['snapshot_revision_order_ids']==[orders[0]['broker_order_id']]
    assert result['activity_normalization'] is None and store.path.read_bytes()==before


def test_later_average_disagreement_does_not_invent_new_execution_prices(tmp_path):
    rows,orders=inputs(); changed=copy.deepcopy(orders)
    changed[0].update(updated_at='2026-10-17T18:00:02Z',average_fill_price='101')
    store=target(tmp_path); retain(store,rows,orders); retain(store,[],changed)
    result=store.review()
    assert not result['normalization_evaluated']
    assert 'TERMINAL_SNAPSHOT_FINANCIAL_FIELDS_CHANGED' in result['review_reasons']
    assert 'UNCHANGED_QUANTITY_AVERAGE_CHANGED' in result['review_reasons']
    assert store.inspect()['normalized_activities'][0]['price']=='100'


def test_missing_retained_fill_remains_a_blocker(tmp_path):
    rows,orders=inputs(); store=target(tmp_path); retain(store,rows[1:],orders)
    result=store.review()
    assert 'SUPPLIED_FILL_QUANTITY_DISAGREEMENT' in result['review_reasons']
    assert result['activity_coverage_verified'] is False


def test_nonterminal_latest_snapshot_requires_hold_review(tmp_path):
    store=target(tmp_path); first,_=split_batches(); retain(store,*first)
    result=store.review()
    assert 'OPEN_ORDER_RESERVATIONS_UNVERIFIED' in result['review_reasons']


@pytest.mark.parametrize('windows,components,gaps', [
    ([(START,END)],1,0),
    ([(START,END),(START,END)],1,0),
    ([(START,END),('2026-10-17T12:00:00Z','2026-10-18T12:00:00Z')],1,0),
    ([(START,END),(END,'2026-10-19T00:00:00Z')],1,0),
    ([(START,END),('2026-10-19T00:00:00Z','2026-10-20T00:00:00Z')],2,1),
    ([(START,END),('2026-10-19T00:00:00Z','2026-10-20T00:00:00Z'),(END,'2026-10-19T00:00:00Z')],1,0)])
def test_declared_window_union_is_never_received_coverage(tmp_path,windows,components,gaps):
    store=target(tmp_path)
    for start,end in reversed(windows): retain(store,[],[],start,end)
    result=store.review(); scope=result['requested_window_review']
    assert len(scope['components'])==components and len(scope['gaps_between_requested_windows'])==gaps
    assert scope['unique_windows']==len(set(windows))
    assert scope['boundary_semantics_verified'] is scope['activity_coverage_verified'] is False
    assert result['activity_coverage_verified'] is False and result['fee_coverage_complete'] is False
    assert ('GAPS_BETWEEN_DECLARED_REQUEST_WINDOWS' in result['review_reasons']) is bool(gaps)


def test_nanosecond_order_snapshots_are_not_collapsed_into_equal_times(tmp_path):
    rows,orders=inputs(); older=copy.deepcopy(orders); newer=copy.deepcopy(orders)
    older[0].update(status='partially_filled',updated_at='2026-10-17T18:00:01.000000001Z')
    newer[0].update(updated_at='2026-10-17T18:00:01.000000002Z')
    store=target(tmp_path); retain(store,[],newer); retain(store,rows,older)
    result=store.review()
    assert result['distinct_order_snapshots']==3 and result['ambiguous_order_ids']==[]
    assert result['normalization_evaluated']


def test_duplicate_snapshots_do_not_increase_versions(tmp_path):
    rows,orders=inputs(); store=target(tmp_path); retain(store,rows,orders)
    retain(store,[],orders,'2026-10-16T00:00:00Z',START)
    assert store.review()['distinct_order_snapshots']==2


def test_global_review_cap_never_returns_a_partial_account(tmp_path,monkeypatch):
    rows,orders=inputs(); store=target(tmp_path); retain(store,rows,orders); before=store.path.read_bytes()
    monkeypatch.setattr(review,'MAX_REVIEW_ORDERS',1)
    with pytest.raises(ValueError,match='REVIEW_ORDER_CAP'): store.review()
    assert store.path.read_bytes()==before and store.inspect()['retained_activities']==4


def test_normalizer_failure_is_explicit_and_no_partial_normalization_escapes(tmp_path,monkeypatch):
    rows,orders=inputs(); store=target(tmp_path); retain(store,rows,orders)
    def fail(*args,**kwargs): raise ValueError('PRIVATE_INTERNAL_DETAIL')
    monkeypatch.setattr(review.adapter,'normalize',fail)
    result=store.review()
    assert 'MERGED_ACTIVITY_INPUT_LIMIT_OR_DISAGREEMENT' in result['review_reasons']
    assert result['activity_normalization'] is None and 'PRIVATE' not in json.dumps(result)


def test_corruption_is_not_converted_into_successful_empty_review(tmp_path):
    store=target(tmp_path); retain(store,[],[])
    import sqlite3
    with sqlite3.connect(store.path) as db: db.execute("UPDATE receipts SET digest='broken' WHERE seq=0")
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'): store.review()


def test_terminal_quantity_increase_is_not_a_new_fill_authorization(tmp_path):
    rows,orders=inputs(); changed=copy.deepcopy(orders)
    changed[0].update(filled_quantity='2',updated_at='2026-10-17T18:00:02Z')
    store=target(tmp_path); retain(store,rows,orders); retain(store,[],changed)
    result=store.review()
    assert 'TERMINAL_SNAPSHOT_FINANCIAL_FIELDS_CHANGED' in result['review_reasons']
    assert result['activity_normalization'] is None and not result['ready_for_routing']


def test_partial_snapshot_average_change_without_more_quantity_requires_review(tmp_path):
    first,_=split_batches(); rows,orders=first; changed=copy.deepcopy(orders)
    changed[0].update(average_fill_price='86',updated_at='2026-10-17T18:00:00.000000001Z')
    store=target(tmp_path); retain(store,rows,orders); retain(store,[],changed)
    result=store.review()
    assert 'UNCHANGED_QUANTITY_AVERAGE_CHANGED' in result['review_reasons']
    assert result['activity_normalization'] is None


def test_fixed_disposable_fixture_runs_actual_retention_and_review():
    result=review.fixture_report()
    assert result['passed'] and result['source']=='FIXED_SYNTHETIC_REST_EXAMPLE'
    assert result['review']['retained_batches']==2 and result['review']['retained_activities']==5
    assert not result['application_accounts_changed'] and not result['broker_connected']


def test_review_requires_store_not_forged_supplied_report():
    with pytest.raises(ValueError,match='STORE_REQUIRED'): review.review({'activity_coverage_verified':True})
