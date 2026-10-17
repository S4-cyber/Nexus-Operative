"""Retrospective supplied funding paths; no account/broker changes or trade permission."""
import copy
import json
import os
import subprocess
import sys
import pytest
from nexus import broker_cash_path as path
from nexus import broker_fee_projection as projection


def data():return projection.fixture_inputs()


def audit(value):return path.analyze(**value)


def times(value,buy,sell):
    for row,stamp in zip(value['executions'],(buy,sell)):row['occurred_at']=stamp
    for row,stamp in zip(value['fees'],(buy,sell)):row['occurred_at']=stamp
    for row in value['orders']:row['updated_at']='2026-10-17T18:00:03+00:00'


def test_fee_reserved_roundtrip_covers_each_debit_but_never_verifies_spendable_funds():
    value=data();original=copy.deepcopy(value);report=audit(value)
    assert value==original and report['declared_path_consistency_ok'] and report['declared_debits_covered']
    assert report['fee_reserved_ending_balances']=={'USD':'1009.78011','BTC':'0'}
    assert report['minimum_fee_reserved_balances']=={'USD':'900','BTC':'0'}
    assert report['execution_groups']==report['executions_checked']==2
    for flag in ('broker_account_reconciled','broker_connected','ledger_applied','ready_for_routing','spendable_funds_verified','live_orders_enabled'):assert report[flag] is False


def test_positive_final_balance_cannot_fund_an_earlier_buy():
    value=data();value['opening_balances']['USD']='50';value['observed_balances']['USD']='59.78011'
    assert projection.reconcile(**value)['input_consistency_ok']
    report=audit(value)
    assert not report['declared_path_consistency_ok'] and not report['declared_debits_covered']
    assert 'INSUFFICIENT_USD_BEFORE_EXECUTION' in report['reasons']
    assert report['review_samples'][0]['shortfall']=='50'
    assert report['fee_reserved_ending_balances']['USD']=='59.78011' and report['ending_balances_match_aggregate']


def test_sell_before_btc_acquisition_is_rejected_despite_matching_final_balances():
    value=data();times(value,'2026-10-17T18:00:02+00:00','2026-10-17T18:00:01+00:00')
    assert projection.reconcile(**value)['input_consistency_ok']
    report=audit(value)
    assert 'INSUFFICIENT_BTC_BEFORE_EXECUTION' in report['reasons']
    assert report['minimum_fee_reserved_balances']['BTC']=='-0.999'
    assert not report['declared_debits_covered'] and report['ending_balances_match_aggregate']


def test_late_fee_postings_are_reserved_from_execution_receipts():
    value=data();baseline=audit(value)
    for fee in value['fees']:fee['occurred_at']='2026-10-17T18:00:03+00:00'
    result=audit(value)
    assert result['declared_path_consistency_ok']
    assert result['fee_reserved_ending_balances']==baseline['fee_reserved_ending_balances']
    assert result['minimum_fee_reserved_balances']==baseline['minimum_fee_reserved_balances']
    assert result['input_sha256']!=baseline['input_sha256']  # Retain posting evidence identity.


def test_overselling_before_delayed_fee_cannot_be_repaired_by_a_later_buy():
    value=data();value['orders'][1]['filled_quantity']='1';value['executions'][1]['quantity']='1'
    value['fees'][1]['amount']='0'
    for fee in value['fees']:fee['occurred_at']='2026-10-17T18:00:03+00:00'
    for order in value['orders']:order['updated_at']='2026-10-17T18:00:03+00:00'
    order=copy.deepcopy(value['orders'][0]);order.update(broker_order_id='01234567-89ab-cdef-0123-456789abcde3',client_order_id='fixture_3',filled_quantity='0.001')
    execution=copy.deepcopy(value['executions'][0]);execution.update(execution_id='execution_3',broker_order_id=order['broker_order_id'],client_order_id='fixture_3',quantity='0.001',occurred_at='2026-10-17T18:00:03+00:00')
    fee=copy.deepcopy(value['fees'][0]);fee.update(activity_id='fee_3',execution_id='execution_3',amount='0')
    value['orders'].append(order);value['executions'].append(execution);value['fees'].append(fee)
    value['observed_balances']={'USD':'1009.9','BTC':'0'}
    assert projection.reconcile(**value)['input_consistency_ok']
    result=audit(value)
    assert not result['declared_debits_covered'] and 'INSUFFICIENT_BTC_BEFORE_EXECUTION' in result['reasons']


@pytest.mark.parametrize('asset',['USD','BTC'])
def test_simultaneous_credits_cannot_be_assumed_to_precede_other_debits(asset):
    value=data();times(value,'2026-10-17T18:00:01+00:00','2026-10-17T18:00:01+00:00')
    if asset=='USD':
        value['opening_balances']={'USD':'50','BTC':'0.999'}
        value['observed_balances']={'USD':'59.78011','BTC':'0.999'}
    assert projection.reconcile(**value)['input_consistency_ok']
    result=audit(value)
    assert result['execution_groups']==1 and 'AMBIGUOUS_SAME_TIME_FUNDING' in result['reasons']
    assert any(row['asset']==asset for row in result['review_samples'])
    assert not result['declared_debits_covered']


def test_equal_time_execs_with_all_debits_prefunded_are_order_independent():
    value=data();times(value,'2026-10-17T18:00:01+00:00','2026-10-17T18:00:01+00:00')
    value['opening_balances']['BTC']='0.999';value['observed_balances']['BTC']='0.999'
    assert audit(value)['declared_path_consistency_ok']
    first=audit(value)
    for key in ('orders','executions','fees'):value[key].reverse()
    assert audit(value)==first


def test_timezone_equivalent_group_report_does_not_depend_on_input_order():
    value=data();times(value,'2026-10-17T18:00:01+00:00','2026-10-17T11:00:01-07:00')
    first=audit(value)
    for key in ('orders','executions','fees'):value[key].reverse()
    assert audit(value)==first
    assert first['execution_groups']==1 and first['review_samples'][0]['at']=='2026-10-17T18:00:01+00:00'


def test_submicrosecond_times_do_not_invent_a_verified_execution_order():
    value=data();times(value,'2026-10-17T18:00:01.000000001+00:00','2026-10-17T18:00:01.000000002+00:00')
    report=audit(value)
    assert 'AMBIGUOUS_SAME_TIME_FUNDING' in report['reasons'] and not report['declared_debits_covered']


def test_open_or_pending_cancel_orders_require_reservation_review_even_when_path_is_funded():
    value=data();value['orders'][0]['status']='pending_cancel'
    report=audit(value)
    assert report['declared_debits_covered'] and not report['declared_path_consistency_ok']
    assert report['reasons']==['NONTERMINAL_ORDER_RESERVATIONS_REQUIRE_REVIEW']


@pytest.mark.parametrize('change',['coverage','missing_fee','missing_execution','balances'])
def test_incomplete_aggregate_evidence_does_not_produce_a_partially_invented_path(change):
    value=data()
    if change=='coverage':value['fee_coverage_complete']=False
    if change=='missing_fee':value['fees'].pop()
    if change=='missing_execution':value['executions'].pop()
    if change=='balances':value['observed_balances']['USD']='999'
    result=audit(value)
    assert not result['evaluated'] and result['declared_debits_covered'] is None
    assert not result['declared_path_consistency_ok'] and result['fee_reserved_ending_balances'] is None
    assert result['executions_checked']==0 and 'AGGREGATE_EVIDENCE_REQUIRES_REVIEW' in result['reasons']


def test_fee_exceeding_its_own_received_asset_requires_review_even_with_other_holdings():
    value=data();value['opening_balances']['BTC']='2';value['fees'][0]['amount']='1.001';value['observed_balances']['BTC']='1'
    assert projection.reconcile(**value)['input_consistency_ok']
    result=audit(value)
    assert 'EXECUTION_FEES_EXCEED_RECEIVED_ASSET' in result['reasons'] and not result['declared_path_consistency_ok']


def test_duplicate_records_are_not_counted_as_spendable_funds_or_fee_debits_twice():
    value=data();baseline=audit(value)
    for key in ('orders','executions','fees'):value[key]=list(reversed(value[key]))+copy.deepcopy(value[key])
    assert audit(value)==baseline


def test_conflicts_and_caps_fail_without_mutating_supplied_data():
    value=data();conflict=copy.deepcopy(value['executions'][0]);conflict['price']='99';value['executions'].append(conflict)
    original=copy.deepcopy(value)
    with pytest.raises(ValueError,match='DUPLICATE_CONFLICT'):audit(value)
    assert value==original
    value=data();value['fees']=[value['fees'][0]]*(projection.MAX_FEES+1)
    with pytest.raises(ValueError,match='INPUT_CAP'):audit(value)


def test_extra_fields_do_not_enter_report_digest_or_review_samples():
    value=data();value['opening_balances']['USD']='50';value['observed_balances']['USD']='59.78011';baseline=audit(value)
    for key in ('orders','executions','fees'):
        for row in value[key]:row['private']='PRIVATE_KEY'
    assert audit(value)==baseline and 'PRIVATE' not in json.dumps(audit(value))


def test_review_sample_cap_retains_failure_count_and_never_turns_review_into_a_pass(monkeypatch):
    value=data();times(value,'2026-10-17T18:00:01+00:00','2026-10-17T18:00:01+00:00')
    value['opening_balances']={'USD':'50','BTC':'0'};value['observed_balances']={'USD':'59.78011','BTC':'0'}
    monkeypatch.setattr(path,'MAX_REVIEW_SAMPLES',1);result=audit(value)
    assert result['review_events']==2 and len(result['review_samples'])==1 and result['review_samples_truncated']
    assert not result['declared_path_consistency_ok']


def test_no_executions_is_an_empty_unverified_path_not_broker_or_funding_acceptance():
    value=data();value.update(orders=[],executions=[],fees=[],observed_balances=value['opening_balances'])
    result=audit(value)
    assert result['executions_checked']==0 and result['declared_debits_covered']
    assert not result['spendable_funds_verified'] and not result['ready_for_routing']


def test_fixture_cli_and_extended_api_remain_disposable(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from nexus import app
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));before=dict(os.environ)
    report=path.fixture_report()
    reply=TestClient(app.app).get('/api/execution/alpaca-paper/fee-projection-fixture')
    assert reply.json()['cash_path']==report
    child=subprocess.run([sys.executable,'-m','nexus.broker_cash_path'],capture_output=True,text=True,timeout=20)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout)==report
    assert not list(tmp_path.iterdir()) and dict(os.environ)==before


def test_aggregate_and_path_share_one_normalization_pass(monkeypatch):
    original=projection.unique;calls=[]
    def observed(rows,limit,key,normalize):calls.append(key);return original(rows,limit,key,normalize)
    monkeypatch.setattr(projection,'unique',observed)
    combined=projection.fixture_report()
    assert calls==['broker_order_id','execution_id','activity_id']
    assert combined['cash_path']['declared_path_consistency_ok']


def test_fixture_api_is_no_store_and_storage_failure_does_not_echo_private_errors(monkeypatch):
    from fastapi.testclient import TestClient
    from nexus import app
    client=TestClient(app.app)
    assert client.get('/api/execution/alpaca-paper/fee-projection-fixture').headers['cache-control']=='no-store'
    def failure():raise OSError('PRIVATE_STORAGE_PATH')
    monkeypatch.setattr(projection,'fixture_report',failure)
    reply=client.get('/api/execution/alpaca-paper/fee-projection-fixture')
    assert reply.status_code==503 and 'PRIVATE' not in reply.text
