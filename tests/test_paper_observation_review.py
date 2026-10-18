"""Real temporary retained replay + supplied portable files, no provider/vault."""
import copy
from datetime import datetime, timezone
from decimal import localcontext
import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from nexus import paper_observation_review as review
from nexus import portable_paper_observation as portable
from nexus import broker_activity_review as merged
from nexus.alpaca_paper_contract import canonical, sha
from test_retained_balance_review import target, ACCOUNT, OTHER

T=datetime(2026,10,17,18,tzinfo=timezone.utc).timestamp()


def observations():
    return review._fixture_observation(ACCOUNT,'1000','0',T-1,T-0.5),review._fixture_observation(ACCOUNT,'1009.78011','0',T+3,T+3.5)


def run(store,**changes):
    first,last=observations()
    scope=dict(expected_account_id=ACCOUNT,expected_head=store.inspect()['head_sha256'],opening_observation=first,closing_observation=last)
    scope.update(changes);return review.review(store,**scope)


def rehash(value):
    value['reported_inventory_sha256']=sha(value['observed_inventory'])
    timing={k:float(v) if '.' in v or 'e' in v else int(v) for k,v in value['read_timing'].items()}
    value['observation_sha256']=sha(dict(profile='nexus.paper-account-observation.v1',read_timing=timing,**{k:value[k] for k in
        ('account_fields_sha256','reported_inventory_sha256','position_count','open_order_count','source')}))
    value['manifest_sha256']=sha({k:v for k,v in value.items() if k!='manifest_sha256'})
    return portable.validate(value)


def test_pair_uses_total_balances_and_one_replayed_head_without_writes(tmp_path):
    store=target(tmp_path);before=store.path.read_bytes();first,last=observations();result=run(store)
    assert result['arithmetic_evaluated'] and result['head_rechecked'] and result['status']=='REVIEW_REQUIRED'
    assert result['sampled_balance_changes']['cash']=='9.78011'
    assert result['boundary_fill_events']==0 and result['observation_windows']['closing']['started_at']=='2026-10-17T18:00:03+00:00'
    component=result['retained_balance_review'];assert component['head_rechecked']
    assert 'retained_activity_review' not in component
    assert component['retained_activity_review_location']=='TOP_LEVEL_SHARED_REVIEW'
    assert component['gross_fill_review']['gross_minus_supplied_observed']=={'USD':'0.10989','BTC':'0.001'}
    assert result['opening_manifest_sha256']==first['manifest_sha256'] and result['closing_manifest_sha256']==last['manifest_sha256']
    assert store.path.read_bytes()==before
    for flag in ('admission_reserved','source_attested','source_coverage_verified','balance_instants_verified',
        'observation_windows_verified_quiet','atomic_snapshot','fee_coverage_complete','fee_execution_links_verified',
        'holds_reconciled','broker_account_reconciled','spendable_funds_verified','ledger_applied',
        'ready_for_routing','broker_connected','live_orders_enabled','automatic_retry','automatic_continuation'):
        assert result[flag] is False


@pytest.mark.parametrize('start,end',[(T+2,T+3),(T+1.5,T+2),(T,T+1),(T+1,T+1)])
def test_fill_at_either_inclusive_read_boundary_or_inside_blocks_arithmetic(tmp_path,start,end):
    last=review._fixture_observation(ACCOUNT,'1009.78011','0',start,end)
    result=run(target(tmp_path),closing_observation=last)
    assert not result['arithmetic_evaluated'] and result['retained_balance_review'] is None
    assert result['boundary_fill_events']>0 and result['boundary_fills_by_window']['closing']>0
    assert 'RETAINED_FILL_DURING_SEQUENTIAL_OBSERVATION' in result['review_reasons']


def test_fill_at_opening_completion_is_not_silently_counted_as_already_in_opening(tmp_path):
    first=review._fixture_observation(ACCOUNT,'1000','0',T-1,T+1)
    result=run(target(tmp_path),opening_observation=first)
    assert not result['arithmetic_evaluated'] and result['boundary_fills_by_window']['opening']==1


@pytest.mark.parametrize('first,last',[(T+3,T+3.5),(T-1,T-0.5),(T-0.5,T-0.25)])
def test_overlapping_reversed_or_touching_windows_do_not_get_reordered(tmp_path,first,last):
    opening,closing=observations();closing=review._fixture_observation(ACCOUNT,'1009.78011','0',first,last)
    if first==T+3:opening=copy.deepcopy(closing)
    result=run(target(tmp_path),opening_observation=opening,closing_observation=closing)
    assert not result['arithmetic_evaluated'] and result['sampled_balance_changes'] is None
    assert 'OBSERVATION_WINDOWS_OVERLAP_OR_NOT_ORDERED' in result['review_reasons']


def test_same_export_is_reported_not_reused_as_a_trading_interval(tmp_path):
    first,_=observations();result=run(target(tmp_path),closing_observation=first)
    assert 'SAME_OBSERVATION_REUSED' in result['review_reasons'] and not result['arithmetic_evaluated']


@pytest.mark.parametrize('changes,code',[
    ({'expected_account_id':OTHER},'ACCOUNT_DISAGREEMENT'),({'expected_head':'0'*64},'HEAD_DISAGREEMENT'),
    ({'expected_head':'private-bad'},'HEAD_REFERENCE'),({'expected_head':None},'HEAD_REFERENCE'),
])
def test_explicit_account_and_history_head_are_mandatory(tmp_path,changes,code):
    store=target(tmp_path);before=store.path.read_bytes()
    with pytest.raises(ValueError,match=code):run(store,**changes)
    assert store.path.read_bytes()==before


def test_rehashed_foreign_account_or_promoted_manifest_cannot_be_used(tmp_path):
    first,last=observations();first['account_id']=OTHER;rehash(first)
    with pytest.raises(ValueError,match='ACCOUNT_DISAGREEMENT'):run(target(tmp_path),opening_observation=first)
    last['authority']['source_attested']=True
    with pytest.raises(ValueError,match='PAPER_OBSERVATION_INVALID'):run(target(tmp_path),closing_observation=last)


@pytest.mark.parametrize('field',['cash','btc_total'])
def test_missing_totals_block_instead_of_using_buying_power_or_available(tmp_path,field):
    first,_=observations();inv=first['observed_inventory'];inv[field]=None;first['checks']='REVIEW_REQUIRED'
    if field=='cash':first['observed_usd_ceiling']=None
    else:inv.update(btc_available=None,btc_unavailable_difference=None,scope_supported=False)
    result=run(target(tmp_path),opening_observation=rehash(first))
    assert not result['arithmetic_evaluated'] and result['retained_balance_review'] is None
    assert 'OPENING_TOTAL_BALANCES_UNKNOWN_OR_UNSUPPORTED' in result['review_reasons']


def test_missing_availability_stays_unknown_and_never_replaces_total_for_arithmetic(tmp_path):
    first,_=observations();first['checks']='REVIEW_REQUIRED'
    first['observed_inventory'].update(btc_available=None,btc_unavailable_difference=None)
    result=run(target(tmp_path),opening_observation=rehash(first))
    assert result['arithmetic_evaluated'] and result['sampled_balance_changes']['btc_available'] is None
    assert result['retained_balance_review']['opening_balances']['BTC']=='0'


def test_mixed_declared_sources_are_blocked_without_trusting_https_label(tmp_path):
    first,_=observations();first['source']='PAPER_HTTPS_READS'
    result=run(target(tmp_path),opening_observation=rehash(first))
    assert not result['arithmetic_evaluated'] and 'MIXED_DECLARED_SOURCES' in result['review_reasons']
    assert not result['source_attested']


@pytest.mark.parametrize('stamp,reason',[(0.00010000000000000002,'TIME_PRECISION'),(1e12,'TIME_RANGE')])
def test_finer_than_nanosecond_or_out_of_calendar_range_is_not_rounded(tmp_path,stamp,reason):
    first=review._fixture_observation(ACCOUNT,'1000','0',stamp,stamp)
    result=run(target(tmp_path),opening_observation=first)
    assert not result['arithmetic_evaluated'] and result['observation_windows'] is None
    assert reason in result['review_reasons'] and result['head_rechecked']


def test_future_order_snapshot_and_fill_outside_gap_remain_blockers(tmp_path):
    # No retained fill at 18:00:01.5, but the last retained order/fill is later.
    last=review._fixture_observation(ACCOUNT,'1000','0',T+1.5,T+1.75)
    result=run(target(tmp_path),closing_observation=last)
    assert not result['arithmetic_evaluated'] and result['retained_balance_review'] is not None
    assert 'RETAINED_FILL_OUTSIDE_SELECTED_INTERVAL' in result['review_reasons']
    assert 'RETAINED_ORDER_SNAPSHOT_AFTER_OBSERVATION' in result['review_reasons']


def test_pair_inputs_are_frozen_and_precision_does_not_change_result(tmp_path):
    store=target(tmp_path);first,last=observations();original=copy.deepcopy((first,last));expected=run(store)
    with localcontext() as context:
        context.prec=4
        actual=run(store,opening_observation=first,closing_observation=last)
    assert actual==expected and (first,last)==original


def test_missing_retained_history_does_not_create_directories(tmp_path):
    from nexus.broker_activity_store import BrokerActivityStore
    store=BrokerActivityStore(tmp_path/'missing'/'history.db',ACCOUNT)
    with pytest.raises(ValueError,match='HISTORY_REQUIRED'):run(store)
    assert not store.path.parent.exists()


@pytest.mark.parametrize('blocked',[False,True])
def test_concurrent_real_append_is_rejected_even_if_balance_arithmetic_is_blocked(tmp_path,monkeypatch,blocked):
    store=target(tmp_path);head=store.inspect()['head_sha256'];entered=threading.Event();release=threading.Event()
    original=merged._review_snapshot
    def wait(*args,**kwargs):entered.set();assert release.wait(timeout=10);return original(*args,**kwargs)
    monkeypatch.setattr(merged,'_review_snapshot',wait)
    options=dict(expected_head=head)
    if blocked:options['closing_observation']=review._fixture_observation(ACCOUNT,'1000','0',T+2,T+3)
    with ThreadPoolExecutor() as pool:
        result=pool.submit(run,store,**options)
        try:
            assert entered.wait(timeout=5)
            from nexus.broker_activity_store import BrokerActivityStore
            BrokerActivityStore(store.path,ACCOUNT).retain([],[],after='2026-10-16T00:00:00Z',until='2026-10-17T00:00:00Z')
        finally:release.set()
        with pytest.raises(ValueError,match='HEAD_CHANGED_DURING_REVIEW'):result.result(timeout=10)


def test_final_replay_detects_corruption_not_just_a_matching_cached_head(tmp_path,monkeypatch):
    store=target(tmp_path);original=merged._review_snapshot
    def corrupt(*args,**kwargs):
        value=original(*args,**kwargs)
        with sqlite3.connect(store.path) as db:db.execute("UPDATE receipts SET digest='bad' WHERE seq=0")
        return value
    monkeypatch.setattr(merged,'_review_snapshot',corrupt)
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):run(store)


def test_cli_private_report_is_explicit_read_only_and_failures_do_not_echo_input(tmp_path):
    store=target(tmp_path);first,last=observations();opening=tmp_path/'first.json';closing=tmp_path/'last.json'
    opening.write_text(canonical(first));closing.write_text(canonical(last));before=store.path.read_bytes()
    command=[sys.executable,'-m','nexus.paper_observation_review','--activities',str(store.path),'--account',ACCOUNT,
        '--head',store.inspect()['head_sha256'],'--opening',str(opening),'--closing',str(closing)]
    env={**os.environ,'NEXUS_PORTABLE_DATA':str(tmp_path/'unused-app')}
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['arithmetic_evaluated'] and store.path.read_bytes()==before
    assert not (tmp_path/'unused-app').exists()
    closing.write_bytes(b'private-invalid'*2000)
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and result.stdout.strip()=='PAPER_OBSERVATION_REVIEW_FAILED' and not result.stderr
    closing.write_text(canonical(last));store.path.write_bytes(b'private-corrupt-database')
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and result.stdout.strip()=='PAPER_OBSERVATION_REVIEW_FAILED' and not result.stderr


def test_boundary_samples_are_capped_without_truncating_full_counts(tmp_path):
    from nexus.broker_activity_store import BrokerActivityStore
    from test_retained_balance_review import source
    rows,orders=source();many=[];snapshots=[]
    for i in range(23):
        row=copy.deepcopy(rows[0]);order=copy.deepcopy(orders[0]);oid=f'01234567-89ab-cdef-0123-{i+100:012d}'
        row.update(id=f'20261017180000000::11234567-89ab-cdef-0123-{i+100:012d}',order_id=oid,client_order_id=f'boundary_{i}')
        order.update(broker_order_id=oid,client_order_id=f'boundary_{i}')
        many.append(row);snapshots.append(order)
    store=BrokerActivityStore(tmp_path/'many.db',ACCOUNT)
    store.retain(many,snapshots,after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z')
    last=review._fixture_observation(ACCOUNT,'1000','0',T+1,T+1)
    result=run(store,closing_observation=last)
    assert result['boundary_fill_events']==23 and result['boundary_fills_by_window']['closing']==23
    assert len(result['boundary_fill_samples'])==20 and result['boundary_samples_truncated']
    assert not result['arithmetic_evaluated']


@pytest.mark.parametrize('instant,expected',[
    ('2026-10-17T18:00:01.999999999Z',0),('2026-10-17T18:00:02.000000001Z',1)])
def test_nanosecond_fills_are_not_rounded_into_or_out_of_read_window(tmp_path,instant,expected):
    from nexus.broker_activity_store import BrokerActivityStore
    from test_retained_balance_review import source
    rows,orders=source();rows[1]['transaction_time']=instant
    store=BrokerActivityStore(tmp_path/'nanos.db',ACCOUNT)
    store.retain(rows,orders,after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z')
    last=review._fixture_observation(ACCOUNT,'1009.78011','0',T+2,T+2.5)
    result=run(store,closing_observation=last)
    assert result['boundary_fill_events']==expected
    if expected:assert not result['arithmetic_evaluated']
    else:assert result['arithmetic_evaluated']


def test_mutating_original_export_during_calculation_cannot_change_bound_inputs(tmp_path,monkeypatch):
    store=target(tmp_path);first,last=observations();expected=run(store);original=merged._review_snapshot
    def mutate(*args,**kwargs):
        first['observed_inventory']['cash']='0';last['observed_inventory']['btc_total']='99'
        return original(*args,**kwargs)
    monkeypatch.setattr(merged,'_review_snapshot',mutate)
    actual=run(store,opening_observation=first,closing_observation=last)
    assert actual==expected


def test_overlapping_boundary_windows_count_a_shared_fill_only_once(tmp_path):
    first=review._fixture_observation(ACCOUNT,'1000','0',T,T+1.5)
    last=review._fixture_observation(ACCOUNT,'1009.78011','0',T+0.5,T+2.5)
    result=run(target(tmp_path),opening_observation=first,closing_observation=last)
    assert result['boundary_fill_events']==2 and result['boundary_fills_by_window']=={'opening':1,'closing':2}
    assert sorted(len(row['windows']) for row in result['boundary_fill_samples'])==[1,2]
    assert not result['arithmetic_evaluated']
