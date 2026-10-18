"""Actual temporary retained history; no credentials/provider/account writes."""
import copy
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import localcontext

import pytest
from nexus import retained_balance_review as review
from nexus import alpaca_activity_normalizer as adapter
from nexus import broker_activity_review as merged
from nexus.broker_activity_store import BrokerActivityStore, _raw
from nexus.broker_fee_projection import fixture_inputs

ACCOUNT='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
OTHER='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
START='2026-10-17T00:00:00Z'; END='2026-10-18T00:00:00Z'


def source(): return [_raw(r) for r in adapter.fixture_report()['normalized_activities']],fixture_inputs()['orders']
def target(tmp_path):
    store=BrokerActivityStore(tmp_path/'activities.db',ACCOUNT)
    rows,orders=source();store.retain(rows,orders,after=START,until=END)
    return store

def run(store,**changes):
    data=fixture_inputs()
    scope=dict(expected_account_id=ACCOUNT,expected_head=store.inspect()['head_sha256'],
        opening_balances=data['opening_balances'],opening_at=data['opening_at'],
        observed_balances=data['observed_balances'],observed_at=data['observed_at'])
    scope.update(changes);return store.review_balances(**scope)


def test_date_only_fee_residual_is_not_promoted_to_interval_evidence(tmp_path):
    store=target(tmp_path);before=store.path.read_bytes();result=run(store);gross=result['gross_fill_review']
    assert result['arithmetic_evaluated'] and result['head_rechecked']
    assert gross['gross_execution_ending_balances']=={'USD':'1009.89','BTC':'0.001'}
    assert gross['gross_minus_supplied_observed']=={'USD':'0.10989','BTC':'0.001'}
    assert gross['residual_equals_all_retained_fee_debits']=={'USD':True,'BTC':True}
    assert gross['all_retained_unallocated_fee_debits']=={'USD':'0.10989','BTC':'0.001'}
    assert gross['residual_equals_exact_interval_fee_debits']=={'USD':None,'BTC':None}
    assert gross['exact_interval_fee_debits']=={'USD':'0','BTC':'0'}
    assert gross['date_only_fee_records_by_asset']=={'USD':1,'BTC':1}
    assert gross['exact_interval_fee_records_by_asset']=={'USD':0,'BTC':0}
    assert gross['exact_fee_records_outside_interval_by_asset']=={'USD':0,'BTC':0}
    assert gross['fee_temporal_scope_complete_by_asset']=={'USD':False,'BTC':False}
    assert gross['fee_posting_interval_verified'] is gross['fee_debits_allocated_to_interval'] is gross['fee_execution_links_verified'] is gross['fee_reserved_funding_verified'] is False
    assert result['status']=='REVIEW_REQUIRED' and 'FEE_DEBITS_NOT_ALLOCATED_TO_EXECUTIONS_OR_INTERVAL' in result['review_reasons']
    for flag in ('admission_reserved','atomic_snapshot','source_coverage_verified','fee_coverage_complete',
                 'fee_execution_links_verified','holds_reconciled','broker_account_reconciled','spendable_funds_verified',
                 'ledger_applied','ready_for_routing','broker_connected','live_orders_enabled','automatic_retry','automatic_continuation'):
        assert result[flag] is False
    assert store.path.read_bytes()==before
    # The fixture's fee dates are the next day; numerical agreement must never
    # allocate them to this execution interval or fabricate posting instants.
    assert all(r['reported_date']=='2026-10-18' for r in store.inspect()['normalized_activities'] if r['kind']!='FILL')


def test_absent_fee_records_stay_unknown_instead_of_zero_coverage(tmp_path):
    rows,orders=source();store=BrokerActivityStore(tmp_path/'activities.db',ACCOUNT)
    store.retain(rows[:2],orders,after=START,until=END)
    result=run(store,observed_balances={'USD':'1009.89','BTC':'0.001'})
    assert result['gross_fill_review']['residual_equals_all_retained_fee_debits']=={'USD':None,'BTC':None}
    assert result['gross_fill_review']['residual_equals_exact_interval_fee_debits']=={'USD':None,'BTC':None}
    assert result['gross_fill_review']['retained_fee_records_by_asset']=={'USD':0,'BTC':0}
    assert not result['fee_coverage_complete']


def test_single_asset_fee_does_not_assert_other_asset_zero_fee(tmp_path):
    rows,orders=source();store=BrokerActivityStore(tmp_path/'activities.db',ACCOUNT)
    store.retain(rows[:3],orders,after=START,until=END)
    result=run(store,observed_balances={'USD':'1009.89','BTC':'0'})
    assert result['gross_fill_review']['residual_equals_all_retained_fee_debits']=={'USD':None,'BTC':True}
    assert result['gross_fill_review']['residual_equals_exact_interval_fee_debits']=={'USD':None,'BTC':None}


def test_different_observed_balance_reports_signed_residual_without_forcing_fee_match(tmp_path):
    result=run(target(tmp_path),observed_balances={'USD':'1010','BTC':'1'})
    assert result['gross_fill_review']['gross_minus_supplied_observed']=={'USD':'-0.11','BTC':'-0.999'}
    assert result['gross_fill_review']['residual_equals_all_retained_fee_debits']=={'USD':False,'BTC':False}
    assert result['gross_fill_review']['residual_equals_exact_interval_fee_debits']=={'USD':None,'BTC':None}


def test_exact_timestamped_fees_can_be_filtered_without_claiming_provider_posting(tmp_path):
    rows,orders=source();rows[2]['date']='2026-10-17T18:00:01.000000001Z';rows[3]['date']='2026-10-17T18:00:02.000000001Z'
    store=BrokerActivityStore(tmp_path/'activities.db',ACCOUNT);store.retain(rows,orders,after=START,until=END)
    gross=run(store)['gross_fill_review']
    assert gross['residual_equals_exact_interval_fee_debits']=={'USD':True,'BTC':True}
    assert gross['exact_interval_fee_debits']=={'USD':'0.10989','BTC':'0.001'}
    assert gross['exact_interval_fee_records_by_asset']=={'USD':1,'BTC':1}
    assert gross['date_only_fee_records_by_asset']=={'USD':0,'BTC':0}
    assert gross['exact_fee_records_outside_interval_by_asset']=={'USD':0,'BTC':0}
    assert gross['fee_temporal_scope_complete_by_asset']=={'USD':True,'BTC':True}
    assert not gross['fee_posting_interval_verified'] and not gross['fee_execution_links_verified']


def test_out_of_interval_exact_fee_timestamp_stays_unallocated(tmp_path):
    rows,orders=source();rows[2]['date']='2026-10-17T18:00:01Z';rows[3]['date']='2026-10-17T18:00:04Z'
    store=BrokerActivityStore(tmp_path/'activities.db',ACCOUNT);store.retain(rows,orders,after=START,until=END)
    gross=run(store)['gross_fill_review']
    assert gross['exact_interval_fee_records_by_asset']=={'USD':0,'BTC':1}
    assert gross['exact_fee_records_outside_interval_by_asset']=={'USD':1,'BTC':0}
    assert gross['fee_temporal_scope_complete_by_asset']=={'USD':False,'BTC':True}
    assert gross['residual_equals_exact_interval_fee_debits']=={'USD':None,'BTC':True}


def test_missing_history_inspection_never_initializes_state(tmp_path):
    store=BrokerActivityStore(tmp_path/'missing'/'activities.db',ACCOUNT)
    with pytest.raises(ValueError,match='HISTORY_REQUIRED'): run(store)
    assert not store.path.parent.exists()


@pytest.mark.parametrize('changes,code',[
    ({'expected_account_id':OTHER},'ACCOUNT_DISAGREEMENT'),({'expected_head':'0'*64},'HEAD_DISAGREEMENT'),
    ({'expected_head':'bad'},'HEAD_REFERENCE'),({'expected_head':None},'HEAD_REFERENCE'),
    ({'opening_at':'2026-10-17T18:01:00Z'},'INTERVAL_REGRESSION'),
])
def test_account_head_and_interval_fences_preserve_history(tmp_path,changes,code):
    store=target(tmp_path);before=store.path.read_bytes()
    with pytest.raises(ValueError,match=code): run(store,**changes)
    assert store.path.read_bytes()==before


def test_stale_review_head_rejected_after_an_append(tmp_path):
    store=target(tmp_path);old=store.inspect()['head_sha256']
    store.retain([],[],after='2026-10-16T00:00:00Z',until=START)
    with pytest.raises(ValueError,match='HEAD_DISAGREEMENT'): run(store,expected_head=old)


def test_concurrent_real_sqlite_append_cannot_return_stale_review(tmp_path,monkeypatch):
    store=target(tmp_path);head=store.inspect()['head_sha256'];entered=threading.Event();release=threading.Event()
    original=merged._review_snapshot
    def blocked(*args,**kwargs):
        entered.set();assert release.wait(timeout=10);return original(*args,**kwargs)
    monkeypatch.setattr(merged,'_review_snapshot',blocked)
    with ThreadPoolExecutor() as pool:
        future=pool.submit(run,store,expected_head=head)
        try:
            assert entered.wait(timeout=5)
            writer=BrokerActivityStore(store.path,ACCOUNT)
            writer.retain([],[],after='2026-10-16T00:00:00Z',until=START)
        finally: release.set()
        with pytest.raises(ValueError,match='HEAD_CHANGED_DURING_REVIEW'): future.result(timeout=10)
    assert store.inspect()['retained_batches']==2


def test_post_evaluation_corruption_is_replayed_instead_of_trusting_head_only(tmp_path,monkeypatch):
    store=target(tmp_path);original=merged._review_snapshot
    def corrupt(*args,**kwargs):
        result=original(*args,**kwargs)
        with sqlite3.connect(store.path) as db: db.execute("UPDATE receipts SET digest='bad' WHERE seq=0")
        return result
    monkeypatch.setattr(merged,'_review_snapshot',corrupt)
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'): run(store)


@pytest.mark.parametrize('field,value,reason',[
    ('opening_at','2026-10-17T18:00:01Z','RETAINED_FILL_OUTSIDE_SELECTED_INTERVAL'),
    ('observed_at','2026-10-17T18:00:01.999999999Z','RETAINED_FILL_OUTSIDE_SELECTED_INTERVAL'),
])
def test_all_retained_fills_must_be_inside_explicit_opening_exclusive_interval(tmp_path,field,value,reason):
    result=run(target(tmp_path),**{field:value})
    assert not result['arithmetic_evaluated'] and result['gross_fill_review'] is None
    assert reason in result['review_reasons']


def test_future_order_snapshot_blocks_current_balance_arithmetic(tmp_path):
    rows,orders=source();orders[0]['updated_at']='2026-10-17T18:01:00Z'
    store=BrokerActivityStore(tmp_path/'activities.db',ACCOUNT);store.retain(rows,orders,after=START,until=END)
    result=run(store)
    assert not result['arithmetic_evaluated']
    assert 'RETAINED_ORDER_SNAPSHOT_AFTER_OBSERVATION' in result['review_reasons']


def test_snapshot_conflict_blocks_arithmetic_instead_of_selecting_an_arrival(tmp_path):
    store=target(tmp_path);rows,orders=source();orders[0]['average_fill_price']='101'
    store.retain([],orders,after='2026-10-16T00:00:00Z',until=START)
    result=run(store)
    assert not result['arithmetic_evaluated'] and result['gross_fill_review'] is None
    assert 'EQUAL_TIME_ORDER_SNAPSHOT_CONFLICT' in result['review_reasons']


def test_missing_fill_in_merged_history_blocks_arithmetic(tmp_path):
    rows,orders=source();store=BrokerActivityStore(tmp_path/'activities.db',ACCOUNT)
    store.retain(rows[1:],orders,after=START,until=END)
    result=run(store)
    assert not result['arithmetic_evaluated']
    assert 'SUPPLIED_FILL_QUANTITY_DISAGREEMENT' in result['review_reasons']


def timed_store(tmp_path,buy_at,sell_at,**changes):
    rows,orders=source()
    for row,at in zip(rows[:2],(buy_at,sell_at)): row['transaction_time']=at
    for order in orders: order['updated_at']='2026-10-17T18:00:03Z'
    store=BrokerActivityStore(tmp_path/'activities.db',ACCOUNT);store.retain(rows,orders,after=START,until=END)
    return store


def test_distinct_nanoseconds_can_fund_later_fill_without_microsecond_collapse(tmp_path):
    store=timed_store(tmp_path,'2026-10-17T18:00:01.000000001Z','2026-10-17T18:00:01.000000002Z')
    result=run(store);gross=result['gross_fill_review']
    assert result['arithmetic_evaluated'] and gross['execution_groups']==2 and gross['equal_nanosecond_groups']==0
    assert gross['gross_only_debits_covered']
    assert result['opening_at']=='2026-10-17T18:00:00+00:00'


def test_equal_nanosecond_fills_cannot_fund_one_another(tmp_path):
    store=timed_store(tmp_path,'2026-10-17T18:00:01.000000001Z','2026-10-17T18:00:01.000000001Z')
    gross=run(store)['gross_fill_review']
    assert gross['execution_groups']==gross['equal_nanosecond_groups']==1
    assert not gross['gross_only_debits_covered']
    assert gross['gross_only_shortfalls'][0]['reason']=='AMBIGUOUS_EQUAL_NANOSECOND_FUNDING'
    assert gross['gross_only_shortfalls'][0]['at']=='2026-10-17T18:00:01.000000001+00:00'


def test_earlier_sell_is_unfunded_despite_positive_ending_balance(tmp_path):
    store=timed_store(tmp_path,'2026-10-17T18:00:01.000000002Z','2026-10-17T18:00:01.000000001Z')
    result=run(store);gross=result['gross_fill_review']
    assert gross['gross_execution_ending_balances']=={'USD':'1009.89','BTC':'0.001'}
    assert not gross['gross_only_debits_covered'] and 'GROSS_ONLY_FUNDING_SHORTFALL' in result['review_reasons']
    assert gross['gross_only_shortfalls'][0]['reason']=='GROSS_ONLY_INSUFFICIENT_BTC'


def test_positive_gross_path_is_not_fee_reserved_funding(tmp_path):
    rows,orders=source();rows[1].update(qty='1',cum_qty='1');orders[1].update(filled_quantity='1')
    store=BrokerActivityStore(tmp_path/'activities.db',ACCOUNT);store.retain(rows,orders,after=START,until=END)
    result=run(store,observed_balances={'USD':'1010','BTC':'0'})
    assert result['gross_fill_review']['gross_only_debits_covered']
    assert not result['gross_fill_review']['fee_reserved_funding_verified']
    assert not result['spendable_funds_verified']


def test_precision_and_equivalent_timezones_preserve_scope_fingerprint(tmp_path):
    store=target(tmp_path);expected=run(store)
    with localcontext() as context:
        context.prec=4
        actual=run(store,opening_at='2026-10-17T19:00:00+01:00',observed_at='2026-10-17T19:00:03+01:00')
    assert actual==expected
    changed=run(store,observed_balances={'USD':'1009.78012','BTC':'0'})
    assert changed['input_sha256']!=expected['input_sha256']


def test_empty_supplied_receipt_keeps_fee_absence_unknown(tmp_path):
    store=BrokerActivityStore(tmp_path/'activities.db',ACCOUNT);store.retain([],[],after=START,until=END)
    result=run(store,observed_balances={'USD':'1000','BTC':'0'})
    assert result['arithmetic_evaluated'] and result['gross_fill_review']['executions']==0
    assert result['gross_fill_review']['residual_equals_all_retained_fee_debits']=={'USD':None,'BTC':None}
    assert result['gross_fill_review']['residual_equals_exact_interval_fee_debits']=={'USD':None,'BTC':None}
    assert not result['source_coverage_verified']


def test_fresh_object_replays_review_without_mutating_database(tmp_path):
    store=target(tmp_path);expected=run(store);before=store.path.read_bytes()
    assert run(BrokerActivityStore(store.path,ACCOUNT))==expected
    assert store.path.read_bytes()==before


def test_fixed_fixture_numeric_fee_match_cannot_promote_source_or_funds():
    result=review.fixture_report()
    assert result['passed'] and not result['broker_connected'] and not result['live_orders_enabled']
    assert not result['review']['source_coverage_verified'] and not result['review']['ready_for_routing']


def test_many_gross_shortfalls_retain_counts_but_bound_samples(tmp_path):
    rows,orders=source();template=rows[1];order=orders[1];sells=[];snapshots=[]
    for i in range(1,25):
        oid=f'01234567-89ab-cdef-0123-{i:012d}';at=f'2026-10-17T18:00:{i:02d}Z'
        sells.append(dict(template,id=f'20261017180000000::11234567-89ab-cdef-0123-{i:012d}',
            order_id=oid,client_order_id=f'sell_{i}',qty='1',cum_qty='1',transaction_time=at))
        snapshots.append(dict(order,broker_order_id=oid,client_order_id=f'sell_{i}',filled_quantity='1',updated_at=at))
    store=BrokerActivityStore(tmp_path/'activities.db',ACCOUNT);store.retain(sells,snapshots,after=START,until=END)
    result=run(store,opening_balances={'USD':'0','BTC':'0'},observed_balances={'USD':'0','BTC':'0'},observed_at='2026-10-17T18:00:30Z')
    gross=result['gross_fill_review']
    assert gross['gross_only_shortfall_events']==24 and len(gross['gross_only_shortfalls'])==20
    assert gross['shortfall_samples_truncated'] and gross['gross_execution_ending_balances']['BTC']=='-24'
    assert 'NEGATIVE_GROSS_EXECUTION_ENDING_BALANCE' in result['review_reasons']
