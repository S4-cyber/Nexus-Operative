import copy
import json
import os
import subprocess
import sys
import pytest
from nexus import broker_fee_projection as projection


def inputs():return projection.fixture_inputs()


def test_asset_fee_roundtrip_matches_declared_balances_without_claiming_broker_reconciliation():
    data=inputs();original=copy.deepcopy(data);result=projection.reconcile(**data)
    assert data==original
    assert result['input_consistency_ok'] and result['projection_matches_declared_balances']
    assert result['projected_balances']=={'USD':'1009.78011','BTC':'0'}
    assert result['fees_by_asset']=={'BTC':'0.001','USD':'0.10989'}
    assert result['gross_buy_notional']=='100' and result['gross_sell_notional']=='109.89'
    assert not result['broker_account_reconciled'] and not result['ready_for_routing']
    assert not result['broker_connected'] and not result['ledger_applied']


def test_replay_and_input_order_do_not_double_count_execution_or_fee():
    data=inputs();baseline=projection.reconcile(**data)
    for key in ('orders','executions','fees'):data[key]=list(reversed(data[key]))+copy.deepcopy(data[key])
    replay=projection.reconcile(**data)
    assert replay['input_consistency_ok']
    assert replay['input_sha256']==baseline['input_sha256']
    assert replay['projected_balances']==baseline['projected_balances']
    assert replay['duplicate_records_ignored']==dict(orders=2,executions=2,fees=2)


@pytest.mark.parametrize('kind',['orders','executions','fees'])
def test_conflicting_id_cannot_replace_a_prior_record(kind):
    data=inputs();changed=copy.deepcopy(data[kind][0])
    field={'orders':'filled_quantity','executions':'quantity','fees':'amount'}[kind]
    changed[field]='0.5';data[kind].append(changed)
    original=copy.deepcopy(data)
    with pytest.raises(ValueError,match='DUPLICATE_CONFLICT'):projection.reconcile(**data)
    assert data==original


@pytest.mark.parametrize('kind,reason',[
    ('missing_fee','EXECUTION_FEE_ACTIVITY_MISSING'),('unknown_fee','FEE_EXECUTION_LINK_UNKNOWN'),
    ('wrong_fee_asset','FEE_RECEIVED_ASSET_DISAGREEMENT'),('oversized_fee','NEGATIVE_PROJECTED_BALANCE'),
    ('unknown_execution_order','EXECUTION_ORDER_LINK_DISAGREEMENT'),
    ('missing_execution','ORDER_EXECUTION_QUANTITY_DISAGREEMENT'),
    ('average_disagreement','ORDER_EXECUTION_AVERAGE_DISAGREEMENT'),
    ('late_execution','EXECUTION_TIME_OUTSIDE_RECONCILIATION_WINDOW'),
    ('early_fee','FEE_TIME_OUTSIDE_RECONCILIATION_WINDOW'),
    ('old_snapshot','ORDER_SNAPSHOT_OUTSIDE_RECONCILIATION_WINDOW')])
def test_incomplete_or_inconsistent_evidence_blocks_local_consistency(kind,reason):
    data=inputs()
    if kind=='missing_fee':data['fees'].pop(0)
    if kind=='unknown_fee':data['fees'][0]['execution_id']='missing'
    if kind=='wrong_fee_asset':data['fees'][0]['asset']='USD'
    if kind=='oversized_fee':data['fees'][0]['amount']='0.01'
    if kind=='unknown_execution_order':data['executions'][0]['client_order_id']='other'
    if kind=='missing_execution':data['executions'].pop(0)
    if kind=='average_disagreement':data['orders'][0]['average_fill_price']='99'
    if kind=='late_execution':data['executions'][0]['occurred_at']='2026-10-17T18:00:04+00:00'
    if kind=='early_fee':data['fees'][0]['occurred_at']='2026-10-17T17:59:59+00:00'
    if kind=='old_snapshot':data['orders'][0]['updated_at']='2026-10-17T17:59:59+00:00'
    result=projection.reconcile(**data)
    assert not result['input_consistency_ok'] and reason in result['reasons']
    assert not result['broker_account_reconciled'] and not result['ledger_applied']


def test_unknown_fee_coverage_is_distinct_from_matching_arithmetic():
    data=inputs();data['fee_coverage_complete']=False
    result=projection.reconcile(**data)
    assert result['projection_matches_declared_balances'] and not result['input_consistency_ok']
    assert result['reasons']==['FEE_ACTIVITY_COVERAGE_UNCONFIRMED']


def test_missing_execution_is_never_inferred_from_cumulative_order_average():
    data=inputs();data['executions']=[];result=projection.reconcile(**data)
    assert result['executions']==0 and result['gross_buy_notional']==result['gross_sell_notional']=='0'
    assert 'ORDER_EXECUTION_QUANTITY_DISAGREEMENT' in result['reasons']
    assert not result['input_consistency_ok']


def test_explicit_zero_fee_activity_can_be_consistent_but_never_attested():
    data=inputs();data['orders'][1]['filled_quantity']='1';data['executions'][1]['quantity']='1'
    for fee in data['fees']:fee['amount']='0'
    data['observed_balances']={'USD':'1010','BTC':'0'}
    result=projection.reconcile(**data)
    assert result['input_consistency_ok'] and result['fees_by_asset']=={'USD':'0','BTC':'0'}
    assert not result['broker_account_reconciled']


@pytest.mark.parametrize('asset',['USD','BTC'])
def test_balance_difference_is_not_hidden_by_rounding(asset):
    from decimal import Decimal
    data=inputs();data['observed_balances'][asset]=str(Decimal(data['observed_balances'][asset])+Decimal('0.000000000000000001'))
    result=projection.reconcile(**data)
    assert 'DECLARED_BALANCE_DISAGREEMENT' in result['reasons']


@pytest.mark.parametrize('kind',['unsupported_asset','negative_fee','bool_fee','naive_time','extra_balance_asset','regressing_window','nonbool_coverage'])
def test_malformed_inputs_fail_closed(kind):
    data=inputs()
    if kind=='unsupported_asset':data['fees'][0]['asset']='ETH'
    if kind=='negative_fee':data['fees'][0]['amount']='-1'
    if kind=='bool_fee':data['fees'][0]['amount']=True
    if kind=='naive_time':data['executions'][0]['occurred_at']='2026-10-17T18:00:01'
    if kind=='extra_balance_asset':data['observed_balances']['ETH']='0'
    if kind=='regressing_window':data['observed_at']='2026-10-17T17:59:00+00:00'
    if kind=='nonbool_coverage':data['fee_coverage_complete']='true'
    with pytest.raises(ValueError):projection.reconcile(**data)


def test_input_cap_is_applied_before_duplicate_expansion():
    data=inputs();data['fees']=[data['fees'][0]]*(projection.MAX_FEES+1)
    with pytest.raises(ValueError,match='INPUT_CAP'):projection.reconcile(**data)


def test_extra_private_fields_are_excluded_from_report_and_digest():
    data=inputs();baseline=projection.reconcile(**data)
    for key in ('orders','executions','fees'):
        for row in data[key]:row['private_key']='DO-NOT-RETAIN'
    result=projection.reconcile(**data)
    assert baseline==result and 'DO-NOT-RETAIN' not in json.dumps(result)


def test_reused_client_id_across_broker_ids_requires_review():
    data=inputs();data['orders'][1]['client_order_id']=data['orders'][0]['client_order_id']
    data['executions'][1]['client_order_id']=data['orders'][0]['client_order_id']
    assert 'CLIENT_ORDER_ID_MULTIPLE_BROKER_IDS' in projection.reconcile(**data)['reasons']


def test_fixture_cli_and_api_do_not_create_account_files_or_offer_writes(tmp_path,monkeypatch):
    import nexus.app as backend
    from fastapi.testclient import TestClient
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));before=dict(os.environ)
    report=projection.fixture_report();client=TestClient(backend.app)
    assert client.get('/api/execution/alpaca-paper/fee-projection-fixture').json()==report
    assert client.post('/api/execution/alpaca-paper/fee-projection-fixture',json={}).status_code==405
    child=subprocess.run([sys.executable,'-m','nexus.broker_fee_projection'],capture_output=True,text=True,timeout=20)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout)==report
    assert not list(tmp_path.iterdir()) and dict(os.environ)==before
