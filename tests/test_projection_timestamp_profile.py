"""No silent precision loss or timezone-dependent identity in legacy projection."""
from copy import deepcopy
from datetime import timedelta,timezone
import json
from pathlib import Path

import pytest
from nexus import broker_fee_projection as projection,broker_cash_path


@pytest.mark.parametrize('field',['opening_at','observed_at','orders','executions','fees'])
@pytest.mark.parametrize('fraction',['123456789','0000000'])
def test_finer_timestamp_profile_refuses_without_rounding_or_mutation(field,fraction):
    data=projection.fixture_inputs();stamp='2026-10-17T18:00:01.'+fraction+'Z'
    if field in ('opening_at','observed_at'):data[field]=stamp
    else:data[field][0]['updated_at' if field=='orders' else 'occurred_at']=stamp
    before=deepcopy(data)
    with pytest.raises(ValueError,match='TIMESTAMP_PROFILE'):projection.reconcile(**data)
    with pytest.raises(ValueError,match='TIMESTAMP_PROFILE'):broker_cash_path.analyze(**data)
    assert data==before


@pytest.mark.parametrize('stamp',[
    '2026-10-17T18:00:01-00:00','2026-10-17T18:00:01+00:99','2026-10-17T18:00:01+24:00',
    '2026-10-17 18:00:01Z','2026-10-17T18:00:01','2026-02-30T18:00:01Z',True,None,
],ids=['unknown-zone','offset-minutes','offset-hours','space','naive','calendar','bool','null'])
def test_ambiguous_or_unsupported_timestamp_profile_is_not_canonicalized_into_acceptance(stamp):
    data=projection.fixture_inputs();data['fees'][0]['occurred_at']=stamp
    with pytest.raises(ValueError,match='TIMESTAMP_PROFILE'):projection.reconcile(**data)


@pytest.mark.parametrize('group,field',[('orders','updated_at'),('executions','occurred_at'),('fees','occurred_at')])
@pytest.mark.parametrize('offset',[-7,10])
def test_timezone_equivalent_same_identity_is_deduplicated_before_accounting(group,field,offset):
    data=projection.fixture_inputs();baseline=projection.reconcile(**data)
    duplicates=deepcopy(data[group])
    for row in duplicates:row[field]=projection._timestamp(row[field]).astimezone(timezone(timedelta(hours=offset))).isoformat()
    data[group]+=duplicates;result=projection.reconcile(**data)
    assert result['input_consistency_ok'] and result['input_sha256']==baseline['input_sha256']
    assert result['duplicate_records_ignored'][group]==2 and result['projected_balances']==baseline['projected_balances']
    assert result['timestamp_scope']==projection.TIMESTAMP_SCOPE


def test_shifted_whole_window_and_all_rows_have_same_utc_digest():
    data=projection.fixture_inputs();baseline=broker_cash_path.analyze_and_project(**data)
    for field in ('opening_at','observed_at'):data[field]=projection._timestamp(data[field]).astimezone(timezone(timedelta(hours=10))).isoformat()
    for group,field in [('orders','updated_at'),('executions','occurred_at'),('fees','occurred_at')]:
        for row in data[group]:row[field]=projection._timestamp(row[field]).astimezone(timezone(timedelta(hours=10))).isoformat()
    assert broker_cash_path.analyze_and_project(**data)==baseline


def test_microsecond_difference_remains_a_conflicting_duplicate():
    data=projection.fixture_inputs();duplicate=deepcopy(data['executions'][0]);duplicate['occurred_at']='2026-10-17T18:00:01.000001Z'
    data['executions'].append(duplicate)
    with pytest.raises(ValueError,match='DUPLICATE_CONFLICT'):projection.reconcile(**data)


@pytest.mark.parametrize('group,field,reason',[
    ('fees','occurred_at','FEE_TIME_OUTSIDE_RECONCILIATION_WINDOW'),
    ('orders','updated_at','EXECUTION_TIME_OUTSIDE_RECONCILIATION_WINDOW'),
])
def test_supported_microsecond_before_execution_is_not_equal_time(group,field,reason):
    data=projection.fixture_inputs();data['executions'][0]['occurred_at']='2026-10-17T18:00:01.000002Z'
    data['orders'][0]['updated_at']='2026-10-17T18:00:01.000002Z';data['fees'][0]['occurred_at']='2026-10-17T18:00:01.000002Z'
    data[group][0][field]='2026-10-17T18:00:01.000001Z'
    assert reason in projection.reconcile(**data)['reasons']


@pytest.mark.parametrize('group,maximum',[('orders',projection.MAX_ORDERS),('executions',projection.MAX_EXECUTIONS),('fees',projection.MAX_FEES)])
def test_row_caps_precede_timestamp_normalization(group,maximum):
    data=projection.fixture_inputs();data[group]=[{}]*(maximum+1)
    with pytest.raises(ValueError,match='INPUT_CAP'):projection.reconcile(**data)


def test_rest_adapter_still_retains_nanoseconds_and_timestamp_fixture_is_aggregate_only():
    from test_alpaca_activity_normalizer import inputs,ACCOUNT
    from nexus import alpaca_activity_normalizer as adapter
    rows,orders=inputs()
    for row in rows:
        if row['activity_type']=='FILL':row['transaction_time']=row['transaction_time'][:19]+'.123456789Z'
    for row in orders:row['updated_at']=row['updated_at'][:19]+'.123456789Z'
    result=adapter.normalize(rows,orders,expected_account_id=ACCOUNT)
    assert all(r['occurred_at'].endswith('.123456789+00:00') for r in result['executions'])
    report=projection.timestamp_fixture_report()
    assert report['passed'] and all(report[k] is True for k in projection.TIMESTAMP_CHECKS)
    assert all(report[k] is False for k in projection.TIMESTAMP_FLAGS)
    assert set(report)=={'source','passed',*projection.TIMESTAMP_CHECKS,*projection.TIMESTAMP_FLAGS}


@pytest.mark.parametrize('suffix',['-00:00','+00:99','+24:00'])
def test_combined_balance_review_uses_same_timezone_profile_without_repair(suffix):
    from test_broker_balance_review import data,run
    value=data();value['opening_at']='2026-10-17T18:00:00'+suffix;before=deepcopy(value)
    with pytest.raises(ValueError,match='TIMESTAMP_PROFILE'):run(value)
    assert value==before


@pytest.mark.parametrize('field',projection.TIMESTAMP_CHECKS)
def test_release_guard_requires_all_timestamp_checks(field):
    from nexus import release_trials,release_roadmap
    result=json.loads((Path(__file__).resolve().parents[1]/'validation'/
        ('release_trials_'+release_roadmap.REVISION.replace('.','_')+'.json')).read_text())['profiles']['balance']
    result['timestamp_profile'][field]=False
    with pytest.raises(ValueError,match='BALANCE_TIMESTAMP_PROFILE'):release_trials._validate('balance',result)


@pytest.mark.parametrize('field',projection.TIMESTAMP_FLAGS)
def test_precision_fixture_cannot_claim_provider_funds_or_release_authority(field):
    from nexus import release_trials,release_roadmap
    result=json.loads((Path(__file__).resolve().parents[1]/'validation'/
        ('release_trials_'+release_roadmap.REVISION.replace('.','_')+'.json')).read_text())['profiles']['balance']
    result['timestamp_profile'][field]=True
    with pytest.raises(ValueError,match='BALANCE_TIMESTAMP_AUTHORITY'):release_trials._validate('balance',result)
