"""Pure supplied evidence only, no provider/store/credential/ledger access."""
import copy
import json
from decimal import localcontext

import pytest
from nexus import broker_balance_review as review
from nexus import broker_fee_projection as projection
from test_alpaca_paper_inspection import ACCOUNT, OTHER, KEY, SECRET, account


def data():
    value=projection.fixture_inputs(); observed=value.pop('observed_balances')
    a=account();a.update(cash=observed['USD'],non_marginable_buying_power=observed['USD'])
    value.update(expected_account_id=ACCOUNT,account_before=a,account_after=copy.deepcopy(a),positions=[],open_orders=[])
    return value


def run(value=None): return review.review(**(data() if value is None else value))


def account_fields(value,**changes):
    value['account_before'].update(changes); value['account_after'].update(changes)


def active_order(**changes):
    row=dict(id='11234567-89ab-cdef-0123-456789abcdef',client_order_id='open_fixture',symbol='BTC/USD',asset_class='crypto',
        side='sell',type='limit',time_in_force='ioc',qty='0.4',limit_price='110',status='pending_cancel',
        filled_qty='0',filled_avg_price=None,updated_at='2026-10-17T18:00:03Z')
    row.update(changes);return row


def nonflat():
    value=data()
    for field in ('orders','executions','fees'): value[field]=value[field][:1]
    account_fields(value,cash='900',non_marginable_buying_power='800')
    value['positions']=[dict(symbol='BTCUSD',asset_class='crypto',side='long',qty='0.999',qty_available='0.599')]
    row=active_order();value['open_orders']=[row]
    value['orders'].append(dict(broker_order_id=row['id'],client_order_id=row['client_order_id'],symbol='BTC/USD',
        side='sell',status='pending_cancel',filled_quantity='0',average_fill_price=None,updated_at=row['updated_at']))
    return value


def test_matching_explicit_fee_history_never_approves_account_or_spending():
    value=data();original=copy.deepcopy(value);result=run(value)
    assert value==original and result['balance_projection_evaluated']
    assert result['linked_history_review']['projection_matches_declared_balances']
    assert result['linked_history_review']['cash_path']['declared_path_consistency_ok']
    assert result['observed_inventory']['reported_cash']=='1009.78011'
    assert result['status']=='REVIEW_REQUIRED' and result['source']=='SUPPLIED_UNATTESTED'
    for flag in ('atomic_snapshot','source_coverage_verified','fee_coverage_complete','holds_reconciled',
                 'broker_account_reconciled','spendable_funds_verified','ready_for_routing','broker_connected',
                 'ledger_applied','live_orders_enabled','automatic_retry'): assert result[flag] is False
    encoded=json.dumps(result)
    assert KEY not in encoded and SECRET not in encoded and 'PRIVATE_DO_NOT_RETURN' not in encoded


def test_pending_cancel_remains_open_and_totals_use_full_inventory_not_availability():
    result=run(nonflat())
    inventory=result['observed_inventory'];terms=result['open_order_terms']
    assert inventory['reported_total_btc']=='0.999' and inventory['reported_available_btc']=='0.599'
    assert inventory['observed_unavailable_btc']=='0.4' and terms['remaining_sell_quantity_btc']=='0.4'
    assert result['linked_history_review']['projection_matches_declared_balances']
    assert 'OBSERVED_BTC_UNAVAILABLE_DIFFERS_FROM_SELL_REMAINDERS' not in result['review_reasons']
    assert 'NONTERMINAL_ORDER_RESERVATIONS_REQUIRE_REVIEW' in result['review_reasons']
    assert not result['holds_reconciled']


def test_partial_pending_cancel_only_counts_unfilled_remainder():
    value=nonflat();row=value['open_orders'][0]
    row.update(qty='0.5',filled_qty='0.1',filled_avg_price='110')
    value['orders'][-1].update(filled_quantity='0.1',average_fill_price='110')
    value['executions'].append(dict(execution_id='partial',broker_order_id=row['id'],client_order_id=row['client_order_id'],
        product='BTC-USD',side='SELL',quantity='0.1',price='110',occurred_at=row['updated_at']))
    value['fees'].append(dict(activity_id='partial_fee',execution_id='partial',asset='USD',amount='0.01',occurred_at=row['updated_at']))
    account_fields(value,cash='910.99')
    value['positions'][0].update(qty='0.899',qty_available='0.499')
    result=run(value)
    assert result['open_order_terms']['remaining_sell_quantity_btc']=='0.4'
    assert result['linked_history_review']['projection_matches_declared_balances']
    assert result['observed_inventory']['observed_unavailable_btc']=='0.4'


def test_available_power_is_not_subtracted_again_or_used_as_cash():
    value=nonflat();account_fields(value,non_marginable_buying_power='10')
    row=value['open_orders'][0];row.update(side='buy',qty='2',limit_price='50',status='new')
    value['orders'][-1].update(side='buy',status='new')
    value['positions'][0]['qty_available']='0.999'
    result=run(value)
    assert result['open_order_terms']['remaining_buy_limit_principal_cap_usd']=='100'
    assert result['observed_inventory']['reported_cash']=='900'
    assert result['observed_inventory']['reported_non_marginable_buying_power']=='10'
    assert result['linked_history_review']['projected_balances']['USD']=='900'
    assert result['linked_history_review']['projection_matches_declared_balances']


def test_buy_limit_cap_is_diagnostic_without_crediting_incoming_quantity():
    value=nonflat();value['open_orders'][0].update(side='buy',qty='20',limit_price='50')
    value['orders'][-1]['side']='buy';value['positions'][0]['qty_available']='0.999'
    result=run(value)
    assert result['open_order_terms']['remaining_buy_limit_principal_cap_usd']=='1000'
    assert result['observed_inventory']['reported_total_btc']=='0.999'
    assert 'BUY_LIMIT_PRINCIPAL_CAP_EXCEEDS_REPORTED_CASH' in result['review_reasons']
    assert not result['spendable_funds_verified']


@pytest.mark.parametrize('available,reason',[('0.7','OBSERVED_BTC_UNAVAILABLE_DIFFERS_FROM_SELL_REMAINDERS'),
                                           (None,'POSITION_AVAILABILITY_MISSING')])
def test_missing_or_different_availability_requires_review_without_inference(available,reason):
    value=nonflat();value['positions'][0]['qty_available']=available;result=run(value)
    assert reason in result['review_reasons']
    if available is None:
        assert result['observed_inventory']['reported_available_btc'] is None
        assert result['observed_inventory']['observed_unavailable_btc'] is None
    assert result['linked_history_review']['projection_matches_declared_balances']


def test_sell_remainder_exceeding_total_is_visible():
    value=nonflat();value['open_orders'][0]['qty']='2';result=run(value)
    assert 'SELL_REMAINDERS_EXCEED_REPORTED_TOTAL_BTC' in result['review_reasons']
    assert result['open_order_terms']['remaining_sell_quantity_btc']=='2'


@pytest.mark.parametrize('missing',['cash','unsupported_position'])
def test_unsupported_or_incomplete_inventory_prevents_projection(missing):
    value=data()
    if missing=='cash': account_fields(value,cash=None)
    else: value['positions']=[dict(symbol='ETHUSD',asset_class='crypto',side='long',qty='1',qty_available='0.9')]
    result=run(value)
    assert not result['balance_projection_evaluated'] and result['linked_history_review'] is None
    assert 'BALANCE_PROJECTION_NOT_EVALUATED' in result['review_reasons']
    if missing=='unsupported_position': assert result['observed_inventory']['reported_total_btc'] is None


@pytest.mark.parametrize('field,value,reason',[('pending_transfer_in','1','PENDING_TRANSFERS_OR_FEES_REVIEW'),
    ('accrued_fees','0.01','PENDING_TRANSFERS_OR_FEES_REVIEW'),('trading_blocked',None,'ACCOUNT_PERMISSION_FLAGS_REVIEW'),
    ('currency','EUR','ACCOUNT_CURRENCY_OR_STATUS_REVIEW')])
def test_eligibility_pending_transfers_and_fees_cannot_disappear(field,value,reason):
    args=data();account_fields(args,**{field:value})
    assert reason in run(args)['review_reasons']


def test_missing_fee_not_assumed_zero_despite_matching_total_arithmetic():
    value=data();value['fees']=[];account_fields(value,cash='1009.89')
    value['positions']=[dict(symbol='BTCUSD',asset_class='crypto',side='long',qty='0.001',qty_available='0.001')]
    result=run(value)
    assert result['linked_history_review']['projection_matches_declared_balances']
    assert 'EXECUTION_FEE_ACTIVITY_MISSING' in result['review_reasons']
    assert not result['fee_coverage_complete']


def test_balance_mismatch_and_earlier_insufficient_funding_are_not_masked():
    value=data();account_fields(value,cash='10')
    assert 'DECLARED_BALANCE_DISAGREEMENT' in run(value)['review_reasons']
    value=data();value['opening_balances']['USD']='50';account_fields(value,cash='59.78011')
    result=run(value)
    assert result['linked_history_review']['projection_matches_declared_balances']
    assert 'INSUFFICIENT_USD_BEFORE_EXECUTION' in result['review_reasons']


def test_open_list_history_disagreement_and_missing_nonterminal_are_flagged():
    value=nonflat();value['orders'][-1]['status']='new'
    assert 'OPEN_ORDER_HISTORY_SNAPSHOT_DISAGREEMENT' in run(value)['review_reasons']
    value=nonflat();value['open_orders']=[]
    assert 'NONTERMINAL_HISTORY_ORDER_NOT_IN_OPEN_SNAPSHOT' in run(value)['review_reasons']
    value=nonflat();value['orders'].pop()
    assert 'OPEN_ORDER_OUTSIDE_SUPPLIED_EXECUTION_HISTORY' in run(value)['review_reasons']


def test_zero_remainder_does_not_make_pending_cancel_terminal():
    value=nonflat();value['open_orders'][0].update(qty='0.4',filled_qty='0.4',filled_avg_price='110')
    result=run(value)
    assert 'NONTERMINAL_ORDER_WITHOUT_REMAINDER' in result['review_reasons']
    assert result['open_order_terms']['unique_orders']==1


@pytest.mark.parametrize('change,code',[
    ({'status':'canceled'},'TERMINAL_ORDER_IN_OPEN_SNAPSHOT'),({'type':'market'},'OPEN_ORDER_SCOPE'),
    ({'time_in_force':'gtc'},'OPEN_ORDER_SCOPE'),({'notional':'10'},'COMPLEX_OR_REPLACED_ORDER'),
    ({'replaces':'11234567-89ab-cdef-0123-456789abcdef'},'COMPLEX_OR_REPLACED_ORDER'),
    ({'qty':'0.3','filled_qty':'0.4','filled_avg_price':'110'},'CUMULATIVE_OVERFILL'),
    ({'updated_at':'2026-10-17T18:00:04Z'},'OPEN_SNAPSHOT_AFTER_OBSERVATION')])
def test_unsupported_open_obligations_fail_whole_review(change,code):
    value=nonflat();value['open_orders'][0].update(change)
    with pytest.raises(ValueError,match=code): run(value)


def test_duplicate_terms_semantic_time_equivalence_and_private_extras_do_not_change_fingerprint():
    value=nonflat();first=run(value)
    duplicate=dict(value['open_orders'][0],symbol='BTCUSD',qty='0.400',limit_price='110.00',updated_at='2026-10-17T19:00:03+01:00',private=SECRET)
    value['open_orders'].append(duplicate);value['orders'].append(dict(value['orders'][0]))
    result=run(value)
    assert result['input_sha256']==first['input_sha256']
    assert result['duplicate_records_ignored']['open_orders']==result['duplicate_records_ignored']['orders']==1
    assert SECRET not in json.dumps(result)
    value['open_orders'][-1]['qty']='0.5'
    with pytest.raises(ValueError,match='OPEN_ORDER_DUPLICATE_CONFLICT'): run(value)


def test_open_client_id_cannot_rebind_another_broker_identity():
    value=nonflat();value['open_orders'].append(dict(value['open_orders'][0],id='21234567-89ab-cdef-0123-456789abcdef'))
    with pytest.raises(ValueError,match='CLIENT_ID_CONFLICT'): run(value)


@pytest.mark.parametrize('group',['orders','executions','fees','positions','open_orders'])
def test_explicit_foreign_account_on_any_row_is_rejected(group):
    value=nonflat();value[group][0]['account_id']=OTHER
    with pytest.raises(ValueError,match='ACCOUNT_DISAGREEMENT'): run(value)


def test_changing_account_or_duplicate_position_is_rejected():
    value=data();value['account_after']['id']=OTHER
    with pytest.raises(ValueError,match='ACCOUNT_ID_CHANGED'): run(value)
    value=data();value['account_after']['cash']='1009'
    with pytest.raises(ValueError,match='ACCOUNT_CHANGED_DURING_READ'): run(value)
    value=nonflat();value['positions'].append(dict(value['positions'][0]))
    with pytest.raises(ValueError,match='DUPLICATE_BTC_POSITION'): run(value)
    value=nonflat();value['positions'][0]['qty_available']='1'
    with pytest.raises(ValueError,match='POSITION_AVAILABILITY_INVALID'): run(value)


@pytest.mark.parametrize('field',['opening_at','observed_at','execution','order','fee','open_order'])
def test_submicrosecond_evidence_never_silently_loses_precision(field):
    value=nonflat();stamp='2026-10-17T18:00:01.000000001Z'
    if field in ('opening_at','observed_at'): value[field]=stamp
    elif field=='open_order': value['open_orders'][0]['updated_at']=stamp
    else:
        group={'execution':'executions','order':'orders','fee':'fees'}[field]
        value[group][0]['updated_at' if field=='order' else 'occurred_at']=stamp
    with pytest.raises(ValueError,match='TIMESTAMP_PROFILE'): run(value)


@pytest.mark.parametrize('group,limit',[('orders',1000),('executions',2000),('fees',2000),('positions',128),('open_orders',128)])
def test_each_collection_cap_is_checked_before_financial_work(group,limit):
    value=data();value[group]=[{}]*(limit+1)
    with pytest.raises(ValueError,match='INPUT_CAP'): run(value)


def test_combined_byte_budget_and_nested_source_shapes_are_rejected():
    value=data();value['open_orders']=[dict(private='x'*4096,**{f'p{i}':'x'*4096 for i in range(10)}) for _ in range(60)]
    with pytest.raises(ValueError,match='BYTE_CAP'): run(value)
    value=data();value['account_before']['private']={'nested':SECRET}
    with pytest.raises(ValueError,match='ROW_INVALID'): run(value)


@pytest.mark.parametrize('field',['correction','is_correction','reversal','previous_execution_id'])
def test_correction_flags_never_become_ordinary_execution_history(field):
    value=data();value['executions'][0][field]=True if field!='previous_execution_id' else 'old'
    with pytest.raises(ValueError,match='CORRECTION_REQUIRES_REVIEW'): run(value)


def test_global_decimal_context_cannot_round_btc_availability_or_caps():
    value=nonflat()
    expected=run(value)
    with localcontext() as context:
        context.prec=4
        result=run(value)
    assert result==expected


def test_invalid_history_still_fails_when_projection_is_unavailable():
    value=data();account_fields(value,cash=None);value['executions'][0]['price']='NaN'
    with pytest.raises(ValueError): run(value)


def test_supported_scope_fingerprint_changes_with_unmodeled_position_identity():
    value=data();value['positions']=[dict(symbol='ETHUSD',asset_class='crypto',side='long',qty='1')]
    first=run(value)['input_sha256'];value['positions'][0]['symbol']='SOLUSD'
    assert run(value)['input_sha256']!=first


def test_fixture_report_is_disposable_and_keeps_all_authority_false():
    report=review.fixture_report()
    assert report['passed'] and not report['broker_connected'] and not report['live_orders_enabled']
    assert not report['review']['ready_for_routing']
