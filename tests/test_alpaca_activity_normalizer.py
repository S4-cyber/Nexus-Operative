"""Supplied REST activities only: no broker, credential, file or account IO."""
import copy
import json
from decimal import Decimal, localcontext

import pytest
from nexus import alpaca_activity_normalizer as adapter
from nexus.broker_fee_projection import fixture_inputs, reconcile

ACCOUNT = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
OTHER = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'


def identity(n=1):
    return '20261017180000000::' + f'01234567-89ab-cdef-0123-{n:012d}'


def inputs():
    data = fixture_inputs(); orders = data['orders']
    rows = []
    for i, execution in enumerate(data['executions'], 1):
        rows.append(dict(id=identity(i), activity_type='FILL', order_id=execution['broker_order_id'],
            symbol='BTCUSD', side=execution['side'].lower(), type='fill', qty=execution['quantity'],
            cum_qty=execution['quantity'], leaves_qty='0', price=execution['price'],
            transaction_time=execution['occurred_at'], description='PRIVATE_DESCRIPTION'))
    rows += [dict(id=identity(3), activity_type='CFEE', date='2026-10-18', status='executed',
                  symbol='BTCUSD', qty='-0.001', net_amount='0', description='PRIVATE_FEE'),
             dict(id=identity(4), activity_type='FEE', date='2026-10-18', status='executed',
                  symbol='BTC/USD', net_amount='-0.10989')]
    return rows, orders


def normalize(rows=None, orders=None):
    original_rows, original_orders = inputs()
    return adapter.normalize(original_rows if rows is None else rows,
                             original_orders if orders is None else orders, expected_account_id=ACCOUNT)


def test_explicit_fills_and_received_asset_debits_are_separate_unverified_evidence():
    rows, orders = inputs(); before = copy.deepcopy((rows, orders))
    result = normalize(rows, orders)
    assert (rows, orders) == before
    assert len(result['executions']) == 2 and len(result['fee_debits']) == 2
    assert result['recognized_fee_debits'] == {'BTC': '0.001', 'USD': '0.10989'}
    assert result['executions'][0]['execution_id'] == identity(1)
    assert result['execution_identity_scope'] == 'REST_ACTIVITY_ID_NOT_STREAM_EXECUTION_ID'
    assert result['status'] == 'REVIEW_REQUIRED'
    assert result['review_reasons'] == ['ACTIVITY_COVERAGE_UNVERIFIED', 'FEE_LINK_AND_TIMING_UNVERIFIED']
    for flag in ('fee_coverage_complete', 'broker_account_reconciled', 'spendable_funds_verified',
                 'ledger_applied', 'broker_connected', 'ready_for_routing', 'live_orders_enabled'):
        assert result[flag] is False
    assert 'PRIVATE' not in json.dumps(result)
    assert all('execution_id' not in fee and 'occurred_at' not in fee for fee in result['fee_debits'])


def test_fill_records_feed_existing_projection_without_fabricating_missing_fees():
    result = normalize(); data = fixture_inputs()
    report = reconcile(data['orders'], result['executions'], [], opening_balances=data['opening_balances'],
        opening_at=data['opening_at'], observed_balances=data['observed_balances'], observed_at=data['observed_at'])
    assert report['input_consistency_ok'] is False
    assert 'EXECUTION_FEE_ACTIVITY_MISSING' in report['reasons']
    assert 'FEE_ACTIVITY_COVERAGE_UNCONFIRMED' in report['reasons']
    assert report['ready_for_routing'] is False


def test_equivalent_duplicates_order_and_timezone_changes_keep_digest():
    rows, orders = inputs(); original = normalize(rows, orders)
    duplicate = copy.deepcopy(rows[0]); duplicate.update(symbol='BTC/USD', qty='1.000', price='100.00',
        transaction_time='2026-10-17T11:00:01-07:00', description='OTHER_PRIVATE')
    result = normalize(list(reversed(rows)) + [duplicate], list(reversed(orders)))
    assert result['duplicate_activities_ignored'] == 1
    assert result['input_sha256'] == original['input_sha256']
    assert result['executions'] == original['executions']


@pytest.mark.parametrize('changes', [{'price': '101'}, {'qty': '.5'}, {'transaction_time': '2026-10-17T18:00:01.000000001Z'},
                                    {'type': 'partial_fill'}, {'cum_qty': '0.5'}, {'leaves_qty': '1'}])
def test_conflicting_activity_ids_never_overwrite_or_double_count(changes):
    rows, orders = inputs(); duplicate = {**rows[0], **changes}
    with pytest.raises(ValueError): normalize(rows + [duplicate], orders)


def test_same_identity_cannot_be_both_fill_and_fee():
    rows, orders = inputs(); rows[2]['id'] = rows[0]['id']
    with pytest.raises(ValueError, match='DUPLICATE_CONFLICT'): normalize(rows, orders)


@pytest.mark.parametrize('changes', [{'activity_type': 'CSD'}, {'activity_type': 'CSW'}, {'activity_type': 'TRD'},
    {'type': 'trade_bust'}, {'execution_type': 'trade_correct'}, {'previous_id': OTHER},
    {'previous_execution_id': OTHER}, {'is_correction': True}, {'reversal': True},
    {'entry_type': 'FILL'}, {'ref_id': OTHER}, {'details': None}, {'event_ulid': 'fake'},
    {'symbol': 'ETHUSD'}, {'side': 'short'}, {'currency': 'EUR'}, {'asset_class': 'us_equity'},
    {'status': 'pending'}, {'account_id': OTHER}, {'order_id': OTHER}, {'client_order_id': 'wrong'}])
def test_unsupported_corrections_schema_assets_and_linkage_fail_closed(changes):
    rows, orders = inputs(); rows[0].update(changes)
    with pytest.raises(ValueError): normalize(rows, orders)


@pytest.mark.parametrize('field,bad', [('qty', None), ('qty', '0'), ('qty', '-1'), ('price', None), ('price', 'NaN'),
    ('price', True), ('price', '1e13'), ('cum_qty', None), ('cum_qty', '.5'), ('leaves_qty', None),
    ('leaves_qty', '-1'), ('qty', '0.0000000000000000001')])
def test_fill_quantity_price_and_cumulative_fields_are_explicit(field, bad):
    rows, orders = inputs(); rows[0][field] = bad
    with pytest.raises(ValueError): normalize(rows, orders)


@pytest.mark.parametrize('changes', [{'qty': '0.001'}, {'net_amount': '1'}, {'net_amount': '-1'}, {'qty': 'NaN'},
    {'status': 'pending'}, {'status': None}, {'symbol': 'ETHUSD'}, {'date': None}, {'date': '2026-02-30'},
    {'date': '2026-10-18T00:00:00'}, {'qty': '-1e-19'}])
def test_crypto_fee_debit_requires_its_own_asset_sign_status_and_date(changes):
    rows, orders = inputs(); rows[2].update(changes)
    with pytest.raises(ValueError): normalize(rows, orders)


@pytest.mark.parametrize('changes', [{'net_amount': '1'}, {'net_amount': None}, {'net_amount': 'Infinity'},
    {'qty': '1'}, {'qty': '-1'}, {'symbol': 'AAPL'}, {'status': 'pending'}])
def test_usd_fee_does_not_swallow_credits_other_assets_or_pending_activity(changes):
    rows, orders = inputs(); rows[3].update(changes)
    with pytest.raises(ValueError): normalize(rows, orders)


def test_missing_fee_records_never_imply_zero_coverage():
    rows, orders = inputs(); result = normalize(rows[:2], orders)
    assert result['fee_coverage_complete'] is False and result['status'] == 'REVIEW_REQUIRED'
    assert result['recognized_fee_debits'] == {'BTC': '0', 'USD': '0'}
    assert 'FEE_LINK_AND_TIMING_UNVERIFIED' in result['review_reasons']


def test_explicit_zero_fee_debits_still_do_not_complete_fee_coverage():
    rows, orders = inputs(); rows[2]['qty'] = '-0.000'; rows[3]['net_amount'] = '0'
    result = normalize(rows, orders)
    assert result['recognized_fee_debits'] == {'BTC': '0', 'USD': '0'}
    assert result['fee_coverage_complete'] is False


def test_fee_reference_hints_never_become_execution_links_or_posting_instants():
    rows, orders = inputs(); rows[2].update(order_id=orders[0]['broker_order_id'], execution_id=identity(1))
    result = normalize(rows, orders); fee = result['fee_debits'][0]
    assert fee['order_reference'] == orders[0]['broker_order_id']
    assert fee['unverified_execution_reference'] == identity(1)
    assert 'execution_id' not in fee and 'occurred_at' not in fee
    assert result['fee_coverage_complete'] is False


@pytest.mark.parametrize('time', ['2026-10-17 18:00:01Z', '2026-10-17T18:00:01', '2026-02-30T00:00:00Z',
    '2026-10-17T18:00:60Z', '2026-10-17T18:00:01-00:00', '2026-10-17T18:00:01.1234567890Z'])
def test_invalid_unknown_or_excess_precision_times_are_not_repaired(time):
    rows, orders = inputs(); rows[0]['transaction_time'] = time
    with pytest.raises(ValueError): normalize(rows, orders)


def test_nanoseconds_are_preserved_and_canonical_timezone_duplicates_deduplicate():
    rows, orders = inputs(); rows[0]['transaction_time'] = '2026-10-17T18:00:00.123456789Z'
    dupe = {**rows[0], 'transaction_time': '2026-10-17T11:00:00.123456789-07:00'}
    result = normalize(rows + [dupe], orders)
    assert result['executions'][0]['occurred_at'] == '2026-10-17T18:00:00.123456789+00:00'
    assert result['duplicate_activities_ignored'] == 1


def test_fee_decimal_sign_conversion_is_exact_under_low_ambient_precision():
    rows, orders = inputs(); rows[2]['qty'] = '-123456789.123456789123456789'
    with localcontext() as ctx:
        ctx.prec = 8
        result = normalize(rows, orders)
    assert result['recognized_fee_debits']['BTC'] == '123456789.123456789123456789'


def split_fills():
    rows, orders = inputs(); first = rows[0]
    second = {**first, 'id': identity(5), 'qty': '.6', 'cum_qty': '1', 'price': '110', 'transaction_time': '2026-10-17T18:00:01Z'}
    first.update(qty='.4', cum_qty='.4', leaves_qty='.6', type='partial_fill', price='85', transaction_time='2026-10-17T18:00:00Z')
    return rows + [second], orders


def test_multiple_fill_quantities_and_prices_use_explicit_activities_not_average_deltas():
    rows, orders = split_fills(); result = normalize(rows, orders)
    assert len(result['executions']) == 3
    assert result['review_reasons'] == ['ACTIVITY_COVERAGE_UNVERIFIED', 'FEE_LINK_AND_TIMING_UNVERIFIED']
    assert {e['price'] for e in result['executions'] if e['broker_order_id'] == orders[0]['broker_order_id']} == {'85', '110'}


@pytest.mark.parametrize('case,reason', [('missing', 'SUPPLIED_FILL_QUANTITY_DISAGREEMENT'),
    ('average', 'SUPPLIED_FILL_AVERAGE_DISAGREEMENT'), ('chain', 'CUMULATIVE_FILL_CHAIN_DISAGREEMENT'),
    ('leaves', 'REMAINING_QUANTITY_DISAGREEMENT'), ('time', 'CUMULATIVE_FILL_TIME_REGRESSION'),
    ('after', 'FILL_AFTER_ORDER_SNAPSHOT')])
def test_fill_chain_disagreements_remain_review_blockers(case, reason):
    rows, orders = split_fills()
    if case == 'missing': rows.pop()
    elif case == 'average': rows[-1]['price'] = '111'
    elif case == 'chain': rows[0]['cum_qty'] = '1'
    elif case == 'leaves': rows[-1]['leaves_qty'] = '1'
    elif case == 'time': rows[0]['transaction_time'] = '2026-10-17T18:00:01.000000001Z'
    else: rows[-1]['transaction_time'] = '2026-10-17T18:00:01.000000001Z'
    assert reason in normalize(rows, orders)['review_reasons']


def test_large_or_malformed_inputs_fail_without_partial_result_or_private_echo():
    rows, orders = inputs()
    cases = [None, rows * 501, [{**rows[0], 'private': 'x' * 4097}],
             [{**rows[0], 'private': ['nested']}], [{**rows[0], 'private': 10 ** 10000}],
             [{**rows[0], 'private': '\ud800'}], [{**rows[0], 'private': 'é' * 3000}],
             [{**rows[0], **{f'x{i}': i for i in range(65)}}]]
    for data in cases:
        with pytest.raises(ValueError) as caught: adapter.normalize(data, orders, expected_account_id=ACCOUNT)
        assert 'PRIVATE_DESCRIPTION' not in str(caught.value)
    with pytest.raises(ValueError): normalize(rows, orders * 501)


def test_total_byte_budget_counts_discarded_fields_before_copy_or_digest(monkeypatch):
    rows, orders = inputs(); monkeypatch.setattr(adapter, 'MAX_INPUT_BYTES', 30)
    with pytest.raises(ValueError, match='INPUT_BYTE_CAP'): normalize(rows, orders)


def test_empty_input_and_duplicate_orders_are_not_account_acceptance():
    result = normalize([], [])
    assert result['status'] == 'REVIEW_REQUIRED' and result['fee_coverage_complete'] is False
    rows, orders = inputs(); result = normalize(rows, orders + [copy.deepcopy(orders[0])])
    assert result['duplicate_orders_ignored'] == 1 and not result['broker_account_reconciled']


@pytest.mark.parametrize('identity_value', [None, '01234567-89ab-cdef-0123-000000000001',
    '20261017::01234567-89ab-cdef-0123-000000000001', 'x' * 128,
    '20261017180000000::01234567-89ab-cdef-0123-00000000000Z'])
def test_rest_identity_namespace_is_strict_and_never_bridged_to_stream_ids(identity_value):
    rows, orders = inputs(); rows[0]['id'] = identity_value
    with pytest.raises(ValueError, match='REST_ID_INVALID'): normalize(rows, orders)


@pytest.mark.parametrize('changes', [{'commission': '1'}, {'net_amount': '-100'}, {'swap_rate': '1'},
                                     {'swap_fee_bps': '1'}, {'correction': 0}])
def test_unmodeled_financial_or_nonboolean_correction_fields_require_review(changes):
    rows, orders = inputs(); rows[0].update(changes)
    with pytest.raises(ValueError): normalize(rows, orders)


def test_fee_timestamp_like_date_stays_a_reported_date_not_execution_time():
    rows, orders = inputs(); rows[2]['date'] = '2026-10-17T17:00:00.123456789-07:00'
    result = normalize(rows, orders)
    assert result['fee_debits'][0]['reported_date'] == '2026-10-18T00:00:00.123456789+00:00'
    assert 'occurred_at' not in result['fee_debits'][0]


def test_order_timestamp_nanoseconds_are_preserved_in_review_and_digest():
    rows, orders = inputs(); rows[0]['transaction_time'] = '2026-10-17T18:00:01.000000001Z'
    orders[0]['updated_at'] = '2026-10-17T18:00:01.000000001Z'
    first = normalize(rows, orders)
    assert 'FILL_AFTER_ORDER_SNAPSHOT' not in first['review_reasons']
    orders[0]['updated_at'] = '2026-10-17T18:00:01Z'
    second = normalize(rows, orders)
    assert first['input_sha256'] != second['input_sha256']
    assert 'FILL_AFTER_ORDER_SNAPSHOT' in second['review_reasons']


def test_order_identity_conflicts_and_oversized_discarded_order_fields_are_rejected():
    rows, orders = inputs()
    with pytest.raises(ValueError): normalize(rows, orders + [{**orders[0], 'filled_quantity': '0'}])
    with pytest.raises(ValueError): normalize(rows, [{**order, 'client_order_id': 'same'} for order in orders])
    with pytest.raises(ValueError): normalize(rows, [{**orders[0], 'private': 'x' * 4097}])


def test_actual_input_byte_cap_is_enforced_even_with_equivalent_duplicates():
    rows, orders = inputs(); expanded = [{**rows[0], 'private': 'x' * 2000}] * 1500
    with pytest.raises(ValueError, match='INPUT_BYTE_CAP'): normalize(expanded, orders)


def test_pure_normalization_uses_no_network_files_credentials_or_environment_mutation(monkeypatch):
    import builtins
    import os
    import socket
    before = dict(os.environ)
    def forbidden(*args, **kwargs): pytest.fail('Pure activity adapter attempted IO')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(builtins, 'open', forbidden)
    result = normalize()
    assert result['status'] == 'REVIEW_REQUIRED' and dict(os.environ) == before


def test_fixture_cli_and_existing_api_keep_raw_fee_links_unverified_without_account_writes(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    from fastapi.testclient import TestClient
    from nexus.app import app
    from nexus import broker_fee_projection as projection
    monkeypatch.setenv('NEXUS_PORTABLE_DATA', str(tmp_path))
    report = adapter.fixture_report()
    assert report['source'] == 'FIXED_SYNTHETIC_REST_EXAMPLE'
    assert report['fee_coverage_complete'] is False and len(report['fee_debits']) == 2
    child = subprocess.run([sys.executable, '-m', 'nexus.alpaca_activity_normalizer'],
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == report
    response = TestClient(app).get('/api/execution/alpaca-paper/fee-projection-fixture')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    combined = response.json()
    assert combined['activity_normalization'] == report
    assert combined['input_consistency_ok'] is True
    assert combined['activity_normalization']['status'] == 'REVIEW_REQUIRED'
    assert not list(tmp_path.iterdir())


def test_new_fixture_failure_stays_private_and_no_store(monkeypatch):
    from fastapi.testclient import TestClient
    from nexus.app import app
    def broken(): raise ValueError('PRIVATE_ACTIVITY_DETAILS')
    monkeypatch.setattr(adapter, 'fixture_report', broken)
    response = TestClient(app).get('/api/execution/alpaca-paper/fee-projection-fixture')
    assert response.status_code == 503 and 'PRIVATE_ACTIVITY_DETAILS' not in response.text
    assert response.headers['cache-control'] == 'no-store'
