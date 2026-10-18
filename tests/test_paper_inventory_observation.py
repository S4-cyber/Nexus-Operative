"""Injected bounded paper reads only; no actual key/vault/provider access."""
import copy
import json
from decimal import localcontext
import pytest
from nexus import alpaca_paper_inspection as inspection
from nexus.alpaca_paper_contract import sha
from test_alpaca_paper_inspection import inspect, account, position, Opener, ACCOUNT, OTHER, KEY, SECRET


def test_observation_preserves_separate_inventory_without_fee_or_hold_claims():
    opener=Opener();report=inspect(opener)
    inventory=report['observed_inventory']
    assert inventory==dict(cash='1000',non_marginable_buying_power='900',btc_total='2',btc_available='1.9',btc_unavailable_difference='0.1',scope_supported=True)
    assert report['observed_usd_ceiling']=='900' and report['observed_btc_available']=='1.9'
    assert len(opener.requests)==4
    assert report['reported_inventory_sha256']==sha(inventory)
    assert report['account_fields_sha256']==sha(inspection._account(account()))
    assert report['read_timing']==dict(started_at=1000,completed_at=1000,elapsed_seconds=0)
    assert report['observation_sha256']==sha(dict(profile='nexus.paper-account-observation.v1',
        account_fields_sha256=report['account_fields_sha256'],reported_inventory_sha256=report['reported_inventory_sha256'],
        position_count=report['position_count'],open_order_count=report['open_order_count'],source=report['source'],read_timing=report['read_timing']))
    assert not report['spendable_funds_verified'] and not report['ready_for_routing'] and not report['atomic_snapshot']
    assert KEY not in json.dumps(report) and SECRET not in json.dumps(report)


@pytest.mark.parametrize('field',['cash','non_marginable_buying_power'])
def test_missing_cash_field_stays_unknown(field):
    first=account();first[field]=None
    report=inspect(Opener([first,[position()],[],copy.deepcopy(first)]))
    assert report['observed_inventory'][field] is None and report['observed_usd_ceiling'] is None
    assert 'ACCOUNT_FUNDS_FIELDS_INCOMPLETE' in report['review_reasons']


def test_missing_available_never_replaced_with_total_or_inferred_difference():
    row=position();row.pop('qty_available')
    inventory=inspect(Opener([account(),[row],[],account()]))['observed_inventory']
    assert inventory['btc_total']=='2' and inventory['btc_available'] is None
    assert inventory['btc_unavailable_difference'] is None


@pytest.mark.parametrize('rows',[[position(symbol='ETHUSD')],[position(side='short')],
                               [position(),position(symbol='ETHUSD')]])
def test_unsupported_position_cannot_be_reported_as_zero_or_complete_btc(rows):
    report=inspect(Opener([account(),rows,[],account()]))
    assert report['observed_inventory']['scope_supported'] is False
    assert report['observed_inventory']['btc_total'] is report['observed_inventory']['btc_available'] is report['observed_inventory']['btc_unavailable_difference'] is None
    assert report['observed_btc_available'] is None and report['checks']=='REVIEW_REQUIRED'


def test_empty_supplied_positions_report_zero_but_still_do_not_reconcile_account():
    report=inspect(Opener([account(),[],[],account()]))
    assert report['observed_inventory']['btc_total']==report['observed_inventory']['btc_available']==report['observed_inventory']['btc_unavailable_difference']=='0'
    assert not report['broker_account_reconciled'] and not report['spendable_funds_verified']


@pytest.mark.parametrize('kind,count',[('positions',2),('orders',3)])
def test_optional_foreign_account_rejected_without_later_gets(kind,count):
    positions=[position(account_id=OTHER)] if kind=='positions' else []
    orders=[dict(account_id=OTHER)] if kind=='orders' else []
    opener=Opener([account(),positions,orders,account()])
    with pytest.raises(inspection.PaperInspectionError,match='COLLECTION_ACCOUNT_DISAGREEMENT'): inspect(opener)
    assert len(opener.requests)==count and all(r.closed for r in opener.responses)


@pytest.mark.parametrize('rows,code',[(None,'COLLECTION_INVALID'),([{}]*129,'COLLECTION_CAP_REVIEW'),
    ([position(),position()],'DUPLICATE_BTC_POSITION'),([position(qty_available='3')],'POSITION_AVAILABILITY_INVALID')])
def test_invalid_position_collection_stops_before_open_orders(rows,code):
    opener=Opener([account(),rows,[],account()])
    with pytest.raises(ValueError,match=code): inspect(opener)
    assert len(opener.requests)==2


def test_fixed_precision_and_decimal_equivalence_preserve_financial_fingerprints():
    row=position(qty='1.234567890123456789',qty_available='1.234567890123456788')
    expected=inspect(Opener([account(),[row],[],account()]))
    with localcontext() as context:
        context.prec=4
        report=inspect(Opener([account(),[row],[],account()]))
    assert report==expected and report['observed_inventory']['btc_unavailable_difference']=='0.000000000000000001'
    first=account();first['cash']='1000.00'
    assert inspect(Opener([first,[row],[],account()]))['account_fields_sha256']==report['account_fields_sha256']


def test_private_extras_do_not_change_normalized_observation_digest():
    first=inspect();a=account();a['private_extra']='DO_NOT_RETAIN'
    report=inspect(Opener([a,[dict(position(),private_extra=SECRET)],[],copy.deepcopy(a)]))
    assert report['observation_sha256']==first['observation_sha256']
    assert SECRET not in json.dumps(report) and 'DO_NOT_RETAIN' not in json.dumps(report)


def test_field_or_timing_change_alters_fingerprint_without_becoming_provenance():
    first=inspect();a=account();a.update(cash='999',non_marginable_buying_power='899')
    second=inspect(Opener([a,[position()],[],copy.deepcopy(a)]))
    assert first['observation_sha256']!=second['observation_sha256']
    assert 'NOT_RAW_SOURCE_ATTESTATION' in second['fingerprint_scope']


@pytest.mark.parametrize('currency',['EUR',None])
def test_unknown_or_non_usd_cash_never_displayed_as_usd(currency):
    a=account();a['currency']=currency
    report=inspect(Opener([a,[position()],[],copy.deepcopy(a)]))
    assert report['observed_inventory']['cash'] is report['observed_inventory']['non_marginable_buying_power'] is None
    assert report['observed_usd_ceiling'] is None
    assert 'ACCOUNT_CURRENCY_OR_STATUS_REVIEW' in report['review_reasons']
