"""Supplied open terms and actual injected inspection, never provider orders."""
import copy
import json
from decimal import localcontext

import pytest
from nexus import paper_open_order_terms as terms
from test_alpaca_paper_inspection import inspect, Opener, account, ACCOUNT, OTHER, SECRET


def order(**changes):
    raw=dict(id='01234567-89ab-cdef-0123-456789abcde1',client_order_id='fixture_buy',symbol='BTCUSD',asset_class='crypto',
        side='buy',type='limit',time_in_force='ioc',qty='3',limit_price='100',filled_qty='0.5',filled_avg_price='99',
        status='pending_cancel',updated_at='2026-10-17T18:00:01.000000001Z',private_description=SECRET)
    raw.update(changes);return raw


def summarize(rows):return terms.summarize(rows,expected_account_id=ACCOUNT)


def test_pending_cancel_retains_remaining_quantity_and_never_becomes_verified_hold():
    buy=order();sell=order(id=OTHER,client_order_id='fixture_sell',side='sell',qty='1',limit_price='110',filled_avg_price='111',status='partially_filled')
    original=copy.deepcopy([buy,sell,buy]);report=summarize([buy,sell,buy])
    assert report['terms_complete'] and report['remaining_buy_limit_principal_cap_usd']=='250'
    assert report['remaining_sell_quantity_btc']=='0.5' and report['supported_unique_orders']==2
    assert report['duplicate_rows_ignored']==1 and report['reported_rows']==3
    assert report['status']=='TERMS_OBSERVED_SOURCE_UNVERIFIED'
    assert all(report[k] is False for k in terms.FLAGS)
    assert SECRET not in json.dumps(report) and 'fixture_buy' not in json.dumps(report)
    assert [buy,sell,buy]==original


@pytest.mark.parametrize('changes',[
    dict(symbol='ETHUSD'),dict(type='market'),dict(time_in_force='day'),dict(order_class='bracket'),
    dict(extended_hours=0),dict(extended_hours=True),dict(qty=None),dict(limit_price='-1'),
    dict(filled_qty='4'),dict(filled_avg_price=None),dict(status='canceled'),dict(status='future'),
    dict(updated_at='2026-10-17T18:00:01.0000000001Z'),dict(updated_at='2026-10-17T18:00:01'),
    dict(correction=True),dict(legs=[]),dict(notional='0'),dict(replaces=OTHER),dict(account_id=OTHER),
    dict(client_order_id=SECRET*100),dict(private_description='x'*4097),dict(private_nested={'secret':SECRET}),
],ids=['asset','market','day','bracket','bool-zero','extended','quantity','price','overfill','missing-average','terminal','unknown-status','precision','naive-time','correction','legs','notional','replace','account','client-cap','private-cap','nested'])
def test_unsupported_or_invalid_row_suppresses_both_totals_without_partial_claim(changes):
    report=summarize([order(),order(id=OTHER,client_order_id='other',**changes)]) if 'id' not in changes and 'client_order_id' not in changes else summarize([order(),order(**changes)])
    assert not report['terms_complete']
    assert report['remaining_buy_limit_principal_cap_usd'] is report['remaining_sell_quantity_btc'] is report['normalized_terms_sha256'] is None
    assert report['unsupported_rows']==1 and report['status']=='REVIEW_REQUIRED'
    assert SECRET not in json.dumps(report)


@pytest.mark.parametrize('changes',[dict(limit_price='101'),dict(filled_avg_price='98'),dict(status='partially_filled'),dict(updated_at='2026-10-17T18:00:01.000000002Z')])
def test_conflicting_duplicate_identity_or_unchanged_quantity_average_revision_is_unknown(changes):
    report=summarize([order(),order(**changes)])
    assert not report['terms_complete'] and 'CONFLICTING_OPEN_ORDER_IDENTITY_OR_TERMS' in report['review_reasons']
    assert report['remaining_buy_limit_principal_cap_usd'] is None


def test_client_identity_cannot_bind_two_broker_ids():
    report=summarize([order(),order(id=OTHER)])
    assert not report['terms_complete'] and report['remaining_sell_quantity_btc'] is None


def test_semantic_duplicate_normalizes_decimal_symbol_and_timezone_without_dropping_nanoseconds():
    a=order();b=order(qty='3.00',filled_qty='0.50',limit_price='100.00',filled_avg_price='99.0',symbol='BTC/USD',updated_at='2026-10-17T11:00:01.000000001-07:00',private_description='different')
    report=summarize([a,b]);single=summarize([a])
    assert report['duplicate_rows_ignored']==1 and report['normalized_terms_sha256']==single['normalized_terms_sha256']
    assert summarize([order(updated_at='2026-10-17T18:00:01.000000002Z')])['normalized_terms_sha256']!=single['normalized_terms_sha256']


def test_nonterminal_zero_remainder_is_not_cancellation_confirmation():
    report=summarize([order(filled_qty='3',status='pending_cancel')])
    assert report['terms_complete'] and report['remaining_buy_limit_principal_cap_usd']=='0'
    assert report['status']=='REVIEW_REQUIRED' and report['review_reasons']==['NONTERMINAL_ORDER_WITHOUT_REMAINDER']
    assert not report['holds_reconciled']


def test_empty_supplied_orders_report_zero_without_coverage_or_spending_permission():
    report=summarize([])
    assert report['remaining_buy_limit_principal_cap_usd']==report['remaining_sell_quantity_btc']=='0'
    assert all(report[k] is False for k in terms.FLAGS)


@pytest.mark.parametrize('rows',[None,[{}]*129,[None]])
def test_collection_type_and_caps_fail_before_normalization(rows):
    with pytest.raises(ValueError,match='PAPER_OPEN_ORDER_TERMS_'):summarize(rows)


def test_full_bounded_totals_and_tiny_products_are_exact_independent_of_decimal_context():
    rows=[order(id=f'01234567-89ab-cdef-0123-{i:012d}',client_order_id=f'large_{i}',qty='1000000000000',limit_price='1000000000000',filled_qty='0',filled_avg_price=None,status='new') for i in range(128)]
    expected=summarize(rows)
    assert expected['remaining_buy_limit_principal_cap_usd']=='128000000000000000000000000'
    with localcontext() as context:
        context.prec=4
        assert summarize(rows)==expected
        tiny=summarize([order(qty='0.000000000000000001',limit_price='0.000000000000000001',filled_qty='0',filled_avg_price=None,status='new')])
    assert tiny['remaining_buy_limit_principal_cap_usd']=='0.'+'0'*35+'1'


def test_discarded_input_byte_budget_does_not_produce_partial_aggregate():
    rows=[order(id=f'01234567-89ab-cdef-0123-{i:012d}',client_order_id=f'wide_{i}',**{f'private_{j}':'x'*4096 for j in range(25)}) for i in range(25)]
    result=summarize(rows)
    assert not result['terms_complete'] and result['unsupported_rows']>0
    assert result['remaining_buy_limit_principal_cap_usd'] is None and 'xxxx' not in json.dumps(result)


def test_actual_confirmed_reader_includes_terms_without_extra_gets_or_export_scope_change():
    opener=Opener([account(),[],[order()],account()]);report=inspect(opener)
    assert len(opener.requests)==4 and report['open_order_terms']['remaining_buy_limit_principal_cap_usd']=='250'
    assert report['open_order_count']==1 and report['checks']=='REVIEW_REQUIRED'
    assert 'open_order_terms' not in report['portable_observation']
    assert not report['spendable_funds_verified'] and not report['ready_for_routing']


def test_inventory_export_does_not_claim_to_fingerprint_detailed_order_terms():
    first=inspect(Opener([account(),[],[order()],account()]))
    second=inspect(Opener([account(),[],[order(limit_price='101')],account()]))
    assert first['portable_observation']==second['portable_observation']
    assert first['open_order_terms']['normalized_terms_sha256']!=second['open_order_terms']['normalized_terms_sha256']
