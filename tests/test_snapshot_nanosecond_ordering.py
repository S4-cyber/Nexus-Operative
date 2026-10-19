"""Exact supplied event ordering is not a synchronized clock or market latency."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from nexus import alpaca_paper_contract as contract
from nexus.broker_request_journal import BrokerRequestJournal
from test_alpaca_paper_contract import plan,response
from test_broker_request_journal import ACCOUNT


@pytest.mark.parametrize('digits',range(1,10))
def test_fractional_precision_has_exact_integer_ordering(digits):
    before='2026-10-17T18:00:00.'+'1'*digits+'Z'
    after='2026-10-17T18:00:00.'+'1'*(digits-1)+'2Z'
    assert contract.timestamp_order_ns(after)-contract.timestamp_order_ns(before)==10**(9-digits)


@pytest.mark.parametrize('other',[
    '2026-10-17T11:00:00.123456789-07:00','2026-10-18T04:00:00.123456789+10:00',
    '2026-10-17T18:00:00.123456789+00:00'])
def test_timezone_equivalent_nine_digit_instants_are_exact(other):
    assert contract.timestamp_order_ns(other)==contract.timestamp_order_ns('2026-10-17T18:00:00.123456789Z')


@pytest.mark.parametrize('other',['2026-10-17 18:00:00.123456+00:00',
    '2026-10-17T18:00:00,123456+00:00','2026-10-17T18:00:00.123456+0000'])
def test_legacy_microsecond_iso_spellings_remain_orderable_without_rewriting(other):
    assert contract.timestamp_order_ns(other)==contract.timestamp_order_ns('2026-10-17T18:00:00.123456Z')
    r=contract.normalize(response(updated_at=other),plan()['payload'])
    assert r['updated_at']==other


@pytest.mark.parametrize('stamp',['2026-10-17T18:00:00.1234567891Z',
    '2026-10-17 18:00:00.123456789Z','2026-10-17T18:00:00,123456789Z'])
def test_unrepresentable_finer_profiles_refuse_before_first_snapshot(stamp):
    tracker=contract.SnapshotTracker(plan())
    with pytest.raises(ValueError,match='PRECISION_UNSUPPORTED'):tracker.observe(response(updated_at=stamp))
    assert tracker.status()['latest'] is None


def test_nanosecond_updates_do_not_collapse_to_false_same_time_conflicts():
    tracker=contract.SnapshotTracker(plan())
    for suffix,status in [('001','new'),('002','partially_filled'),('003','pending_cancel'),('004','canceled')]:
        raw=response(status=status,filled_qty='0' if status=='new' else '1',filled_avg_price=None if status=='new' else '99',
            updated_at='2026-10-17T18:00:00.123456'+suffix+'Z')
        assert not tracker.observe(raw)['duplicate']
    assert tracker.status()['latest']['updated_at'].endswith('.123456004Z')
    assert tracker.observe(raw)['duplicate']


@pytest.mark.parametrize('change',['regression','same_instant'])
def test_older_nanosecond_and_equal_instant_conflicts_preserve_latest(change):
    tracker=contract.SnapshotTracker(plan());tracker.observe(response(updated_at='2026-10-17T18:00:00.123456789Z'))
    before=tracker.status()
    at='2026-10-17T18:00:00.123456788Z' if change=='regression' else '2026-10-17T11:00:00.123456789-07:00'
    with pytest.raises(ValueError,match='TIME_REGRESSION' if change=='regression' else 'TIME_CONFLICT'):
        tracker.observe(response(updated_at=at,status='pending_cancel'))
    assert tracker.status()==before


def test_request_journal_replays_exact_nanosecond_updates_in_fresh_offline_process(tmp_path):
    j=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT);j.prepare(plan());j.mark_submission_uncertain('nx_fixture')
    first=response(updated_at='2026-10-17T18:00:00.123456788Z')
    second=response(updated_at='2026-10-17T18:00:00.123456789Z',status='partially_filled',filled_qty='1',filled_avg_price='99')
    j.observe('nx_fixture',first);j.observe('nx_fixture',second);before=j.path.read_bytes()
    with pytest.raises(ValueError,match='TIME_REGRESSION'):j.observe('nx_fixture',first)
    assert j.path.read_bytes()==before
    code='''
import sys,socket
from nexus.broker_request_journal import BrokerRequestJournal
def denied(*a,**k):raise AssertionError('network forbidden')
socket.socket=socket.create_connection=denied
r=BrokerRequestJournal(sys.argv[1],sys.argv[2]).recovery()['requests'][0]
assert r['snapshot']['updated_at']=='2026-10-17T18:00:00.123456789Z'
assert r['snapshot']['filled_quantity']=='1' and not r['sendable']
'''
    r=subprocess.run([sys.executable,'-c',code,str(j.path),ACCOUNT],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    assert j.path.read_bytes()==before


def test_encrypted_quote_bundle_replays_new_nanosecond_original_history(tmp_path):
    from nexus import paper_quote_bundle as bundle
    options=bundle._fixture(tmp_path/'private');j=BrokerRequestJournal(options['request_journal_path'],options['expected_account_id'])
    for suffix,status in [('788','new'),('789','partially_filled')]:
        j.observe('nx_quote_fixture',response(client_order_id='nx_quote_fixture',updated_at='2026-10-17T18:00:00.123456'+suffix+'Z',
            status=status,filled_qty='0' if status=='new' else '1',filled_avg_price=None if status=='new' else '99'))
    options['expected_request_head']=j.recovery()['head_sha256'];before={p:p.read_bytes() for p in (tmp_path/'private').iterdir()}
    raw=bundle.snapshot(**options);assert bundle.unseal(bundle.seal(raw,'temporary fixture words only'),'temporary fixture words only')==raw
    assert before=={p:p.read_bytes() for p in (tmp_path/'private').iterdir()}


def test_fixture_is_aggregate_and_never_claims_synchronized_clocks():
    r=contract.timestamp_fixture_report()
    assert set(r)=={'source','passed',*contract.TIME_CHECKS,*contract.TIME_FLAGS}
    assert r['passed'] and all(r[k] is True for k in contract.TIME_CHECKS)
    assert all(r[k] is False for k in contract.TIME_FLAGS)


@pytest.mark.parametrize('field',contract.TIME_CHECKS)
def test_release_requires_all_snapshot_time_checks(field):
    from nexus import release_trials,release_roadmap
    r=json.loads((Path(__file__).resolve().parents[1]/'validation'/('release_trials_'+release_roadmap.REVISION.replace('.','_')+'.json')).read_text())['profiles']['pacing']
    r['snapshot_time'][field]=False
    with pytest.raises(ValueError,match='SNAPSHOT_TIME_ORDERING'):release_trials._validate('pacing',r)


@pytest.mark.parametrize('field',contract.TIME_FLAGS)
def test_event_ordering_fixture_cannot_claim_execution_or_release_authority(field):
    from nexus import release_trials,release_roadmap
    r=json.loads((Path(__file__).resolve().parents[1]/'validation'/('release_trials_'+release_roadmap.REVISION.replace('.','_')+'.json')).read_text())['profiles']['pacing']
    r['snapshot_time'][field]=True
    with pytest.raises(ValueError,match='SNAPSHOT_TIME_AUTHORITY'):release_trials._validate('pacing',r)
