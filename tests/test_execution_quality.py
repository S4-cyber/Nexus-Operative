"""Execution benchmark arithmetic, precision, replay and conservative unknowns."""
import copy
import json
import subprocess
import sys

import pytest
from nexus import execution_quality as subject


def test_partial_buy_decomposes_and_distinguishes_opportunity_from_execution():
    r=subject.review(**subject._fixture())
    assert r['filled_quantity']=='2' and r['unfilled_quantity']=='1' and r['fill_vwap']=='102.5'
    assert r['delay_component_usd']=='2' and r['arrival_spread_component_usd']=='2'
    assert r['post_arrival_residual_usd']=='1' and r['signed_gross_shortfall_usd']=='5'
    assert r['signed_shortfall_with_supplied_fees_usd']=='5.2'
    assert r['executed_shortfall_with_supplied_fees_bps']=='260'
    assert r['signed_unfilled_opportunity_usd']=='4' and r['signed_whole_order_shortfall_usd']=='9.2'
    assert r['whole_order_decision_notional_usd']=='300'
    assert all(r[k] is False for k in subject.FLAGS)


def test_sell_better_price_has_negative_cost_but_fees_still_increase_it():
    r=subject.review(**subject._fixture(side='SELL'))
    assert r['signed_gross_shortfall_usd']=='-5'
    assert r['delay_component_usd']=='-2' and r['arrival_spread_component_usd']=='2'
    assert r['post_arrival_residual_usd']=='-5'
    assert r['signed_shortfall_with_supplied_fees_usd']=='-4.8'
    assert r['signed_unfilled_opportunity_usd']=='-4'
    assert not r['profitability_verified'] and not r['market_impact_identified']


def test_unknown_fee_is_not_zero_and_cannot_produce_an_all_cost_total():
    args=subject._fixture();args['fills'][0]['fee_usd']=None
    r=subject.review(**args)
    assert r['signed_gross_shortfall_usd']=='5' and not r['all_supplied_fees_explicit']
    for key in ('supplied_fee_total_usd','signed_shortfall_with_supplied_fees_usd',
                'executed_shortfall_with_supplied_fees_bps','signed_whole_order_shortfall_usd','whole_order_shortfall_bps'):
        assert r[key] is None


def test_missing_end_mark_keeps_only_unfilled_opportunity_unknown():
    partial=subject.review(**subject._fixture(unfilled_mark=None))
    assert partial['signed_shortfall_with_supplied_fees_usd']=='5.2'
    assert partial['signed_unfilled_opportunity_usd'] is None and partial['signed_whole_order_shortfall_usd'] is None
    full=subject.review(**subject._fixture(requested_quantity='2',unfilled_mark=None))
    assert full['signed_unfilled_opportunity_usd']=='0' and full['signed_whole_order_shortfall_usd']=='5.2'


def test_nanoseconds_do_not_round_through_float_and_order_does_not_change_result():
    args=subject._fixture();before=copy.deepcopy(args)
    r=subject.review(**args)
    assert args==before and r['decision_to_arrival_ns']==1
    assert r['arrival_to_first_supplied_fill_ns']==1 and r['arrival_to_last_supplied_fill_ns']==2
    args['fills'].reverse();assert subject.review(**args)==r
    assert not r['clock_domains_verified'] and not r['broker_latency_measured']


def test_matching_duplicate_is_canonical_and_conflicting_duplicate_is_not_rewritten():
    args=subject._fixture();r=subject.review(**args)
    args['fills'].append(dict(args['fills'][0],quantity='1.0',price='102.0'))
    repeated=subject.review(**args)
    assert repeated['input_sha256']==r['input_sha256'] and repeated['duplicate_rows_ignored']==1
    assert repeated['filled_quantity']=='2'
    args['fills'][-1]['fee_usd']=None
    with pytest.raises(ValueError,match='^EXECUTION_QUALITY_DUPLICATE_CONFLICT$'):subject.review(**args)


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_no_fill_is_not_zero_cost_or_successful_execution(side):
    r=subject.review(**subject._fixture(side=side,fills=[],unfilled_mark=None))
    assert r['unique_fills']==0 and r['fill_fraction']=='0' and r['fill_vwap'] is None
    assert r['signed_gross_shortfall_usd']=='0'
    assert r['signed_shortfall_with_supplied_fees_usd'] is None and r['signed_unfilled_opportunity_usd'] is None
    assert r['arrival_to_first_supplied_fill_ns'] is None and not r['all_supplied_fees_explicit']


@pytest.mark.parametrize('value',['NaN','Infinity','-1','1e3','1.0000000000000000001','10000000000000',1,True,None])
def test_noncanonical_or_unbounded_numbers_refuse(value):
    with pytest.raises(ValueError,match='^EXECUTION_QUALITY_AMOUNT$'):
        subject.review(**subject._fixture(decision_mid=value))


@pytest.mark.parametrize('value',[1.0,True,-1,2**63,None])
def test_timestamp_requires_bounded_integer_nanoseconds(value):
    with pytest.raises(ValueError,match='^EXECUTION_QUALITY_TIMESTAMP$'):
        subject.review(**subject._fixture(decision_at_ns=value))


def test_future_arrival_and_fills_outside_interval_are_rejected_at_one_ns_boundary():
    args=subject._fixture()
    with pytest.raises(ValueError,match='TIME_ORDER'):
        subject.review(**dict(args,decision_at_ns=args['arrival_at_ns']+1))
    for stamp in (args['arrival_at_ns']-1,args['end_at_ns']+1):
        changed=copy.deepcopy(args);changed['fills'][0]['filled_at_ns']=stamp
        with pytest.raises(ValueError,match='FILL_TIME_ORDER'):subject.review(**changed)


def test_shape_cap_overfill_crossed_and_unsupported_scope_reject():
    for changes,code in (({'fills':subject._fixture()['fills']*(subject.MAX_FILLS+1)},'FILL_CAP'),
                         ({'requested_quantity':'1'},'OVERFILL'),({'arrival_bid':'103'},'CROSSED_QUOTE'),
                         ({'product':'ETH-USD'},'SCOPE'),({'side':'SHORT'},'SCOPE')):
        with pytest.raises(ValueError,match='EXECUTION_QUALITY_'+code):subject.review(**subject._fixture(**changes))
    args=subject._fixture();args['fills'][0]['private_extra']='FIXTURE'
    with pytest.raises(ValueError,match='FILL_SHAPE'):subject.review(**args)
    args=subject._fixture();args['fills'][0]['fee_usd']='-0.1'
    with pytest.raises(ValueError,match='AMOUNT'):subject.review(**args)


def test_exact_explicit_zero_fees_do_not_attest_actual_fee_coverage():
    args=subject._fixture()
    for row in args['fills']:row['fee_usd']='0'
    r=subject.review(**args)
    assert r['all_supplied_fees_explicit'] and r['supplied_fee_total_usd']=='0'
    assert not r['actual_fee_coverage_verified'] and not r['ready_for_routing']


def test_same_prices_but_changed_time_changes_content_digest_without_latency_claim():
    args=subject._fixture();r=subject.review(**args)
    args['fills'][0]['filled_at_ns']+=1
    changed=subject.review(**args)
    assert changed['input_sha256']!=r['input_sha256']
    assert changed['signed_gross_shortfall_usd']==r['signed_gross_shortfall_usd']
    assert not changed['broker_latency_measured']


def test_extreme_bounded_decimal_products_and_caller_rounding_do_not_change_report():
    from decimal import localcontext,ROUND_DOWN,ROUND_UP
    args=subject._fixture(requested_quantity='9999999999999.999999999999999999',
        decision_mid='9999999999999.999999999999999999',arrival_bid='9999999999999.999999999999999998',
        arrival_ask='9999999999999.999999999999999999',unfilled_mark=None)
    args['fills'][0].update(quantity='9999999999998.999999999999999999',price='9999999999999.999999999999999997')
    args['fills'][1].update(price='9999999999999.999999999999999999')
    with localcontext() as ctx:
        ctx.rounding=ROUND_DOWN;first=subject.review(**args)
        ctx.rounding=ROUND_UP;second=subject.review(**args)
    assert first==second and first['unfilled_quantity']=='0' and first['decomposition_matches']
    assert first['signed_gross_shortfall_usd'].startswith('-0.000019999999999997')


def test_synthetic_child_fixture_has_no_storage_network_or_vault(tmp_path):
    script='''
import json,sys,os
from nexus.execution_quality import fixture_report
def audit(event,args):
 if event in ('socket.connect','socket.bind'):raise RuntimeError('NETWORK_DENIED')
sys.addaudithook(audit);os.chdir(sys.argv[1])
print(json.dumps(fixture_report()))
'''
    child=subprocess.run([sys.executable,'-c',script,str(tmp_path)],capture_output=True,text=True,timeout=10)
    assert child.returncode==0 and not child.stderr and not list(tmp_path.iterdir())
    r=json.loads(child.stdout)
    assert r['passed'] and all(r[k] is True for k in subject.CHECKS)
    assert all(r[k] is False for k in subject.FLAGS)
    assert all(token not in child.stdout for token in ('fixture_a','102.5','1792195200','input_sha256'))


@pytest.mark.parametrize('field,value,code',[
    ('decomposition_checked',False,'EXECUTION_QUALITY'),
    ('missing_fees_remain_unknown',False,'EXECUTION_QUALITY'),
    ('nanosecond_ordering_checked',False,'EXECUTION_QUALITY'),
    ('broker_latency_measured',True,'EXECUTION_QUALITY_AUTHORITY'),
    ('ready_for_routing',True,'EXECUTION_QUALITY_AUTHORITY')])
def test_release_validator_requires_semantics_and_rejects_promoted_authority(field,value,code):
    from nexus import release_trials
    result=subject.fixture_report();result[field]=value
    with pytest.raises(ValueError,match='RELEASE_TRIAL_'+code):
        release_trials._validate('capacity',{'execution_quality':result})
