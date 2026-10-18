"""Original IOC terms versus explicit retained REST fills; all inputs injected."""
import json
import subprocess
import sys
from decimal import Decimal
import pytest
from nexus import broker_request_activity_review as module
from nexus.broker_request_journal import BrokerRequestJournal, ZERO
from nexus.broker_activity_store import BrokerActivityStore
from test_broker_request_journal import ACCOUNT
from test_alpaca_paper_contract import plan,response

START='2026-10-17T00:00:00Z';END='2026-10-18T00:00:00Z'


def stores(tmp_path,side='BUY',prices=('98','100'),leaves=('1','0')):
    j=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT);s=BrokerActivityStore(tmp_path/'activities.sqlite3',ACCOUNT)
    p=plan(side,limit_price='100' if side=='BUY' else '98');j.prepare(p);j.mark_submission_uncertain('nx_fixture')
    avg=str((Decimal(prices[0])+Decimal(prices[1]))/2)
    raw=response(2,side=side.lower(),limit_price=p['payload']['limit_price'],status='filled',filled_qty='2',filled_avg_price=avg)
    j.observe('nx_fixture',raw);order=j.recovery()['requests'][0]['snapshot']
    rows=[dict(id='20261017180000000::'+f'01234567-89ab-cdef-0123-{i:012d}',activity_type='FILL',order_id=raw['id'],symbol='BTCUSD',
        side=side.lower(),type='partial_fill' if i==1 else 'fill',qty='1',price=prices[i-1],cum_qty=str(i),leaves_qty=leaves[i-1],
        transaction_time=f'2026-10-17T18:00:0{i}Z',description='PRIVATE_DESCRIPTION') for i in (1,2)]
    s.retain(rows,[order],after=START,until=END);return j,s,rows,order


def review(j,s,**overrides):
    r=module.review(j,s,expected_request_head=overrides.get('request',j.recovery()['head_sha256']),expected_activity_head=overrides.get('activity',s.inspect()['head_sha256']))
    assert r['read_only'] and r['status']=='REVIEW_REQUIRED'
    assert all(r[k] is False for k in module.FLAGS)
    return r


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_explicit_fills_agree_without_writes_or_coverage_authority(tmp_path,side):
    j,s,_,_=stores(tmp_path,side);before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    r=review(j,s);assert r['local_request_activity_agreement'] and r['agreement_blockers']==[]
    assert r['request_reviews'][0]['explicit_fill_count']==2 and r['request_reviews'][0]['explicit_fill_quantity']=='2'
    assert 'FEE_LINK_AND_TIMING_UNVERIFIED' in r['review_reasons'] and 'PRIVATE' not in json.dumps(r)
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_acceptable_average_cannot_hide_an_individual_limit_violation(tmp_path,side):
    j,s,_,_=stores(tmp_path,side,prices=('85','113'))
    assert s.review()['review_reasons']==['ACTIVITY_COVERAGE_UNVERIFIED','FEE_LINK_AND_TIMING_UNVERIFIED']
    r=review(j,s);assert not r['local_request_activity_agreement']
    assert r['agreement_blockers']==['INDIVIDUAL_FILL_LIMIT_DISAGREEMENT']


def test_consistent_activity_leaves_must_still_match_original_quantity(tmp_path):
    j,s,_,_=stores(tmp_path,leaves=('2','1'))
    assert 'REMAINING_QUANTITY_DISAGREEMENT' not in s.review()['review_reasons']
    assert 'FILL_REQUEST_QUANTITY_DISAGREEMENT' in review(j,s)['agreement_blockers']


def test_missing_histories_do_not_create_directories_or_imply_agreement(tmp_path):
    root=tmp_path/'absent';j=BrokerRequestJournal(root/'r.sqlite3',ACCOUNT);s=BrokerActivityStore(root/'a.sqlite3',ACCOUNT)
    r=review(j,s);assert not root.exists() and not r['local_request_activity_agreement']
    assert 'NO_RETAINED_REQUESTS' in r['agreement_blockers'] and 'NO_RETAINED_ACTIVITY_BATCHES' in r['agreement_blockers']


@pytest.mark.parametrize('which',['request','activity'])
def test_stale_head_refuses_before_result_and_preserves_history(tmp_path,which):
    j,s,_,_=stores(tmp_path);before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(ValueError,match='HEAD_DISAGREEMENT'):review(j,s,**{which:ZERO})
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}


def test_foreign_account_refuses_without_initializing_paths(tmp_path):
    j=BrokerRequestJournal(tmp_path/'r.sqlite3',ACCOUNT);s=BrokerActivityStore(tmp_path/'a.sqlite3','bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
    with pytest.raises(ValueError,match='ACCOUNT_DISAGREEMENT'):module.review(j,s,expected_request_head=ZERO,expected_activity_head=ZERO)
    assert list(tmp_path.iterdir())==[]


def test_missing_request_observation_does_not_link_only_by_client_id(tmp_path):
    j,s,_,_=stores(tmp_path);other=BrokerRequestJournal(tmp_path/'unobserved.sqlite3',ACCOUNT)
    other.prepare(plan());other.mark_submission_uncertain('nx_fixture')
    r=review(other,s);assert 'REQUEST_OBSERVATION_MISSING' in r['agreement_blockers']
    assert 'UNTRACKED_ACTIVITY_ORDERS_REQUIRE_REVIEW' in r['agreement_blockers']


def test_changed_snapshot_status_is_not_accepted_as_matching_fill_history(tmp_path):
    j,s,rows,order=stores(tmp_path);other=BrokerActivityStore(tmp_path/'other.sqlite3',ACCOUNT)
    other.retain(rows,[dict(order,status='canceled')],after=START,until=END)
    assert 'REQUEST_ACTIVITY_SNAPSHOT_DISAGREEMENT' in review(j,other)['agreement_blockers']


def test_incomplete_explicit_fills_are_not_inferred_from_request_average(tmp_path):
    j,s,rows,order=stores(tmp_path);other=BrokerActivityStore(tmp_path/'other.sqlite3',ACCOUNT)
    other.retain(rows[:1],[order],after=START,until=END)
    r=review(j,other);assert 'REQUEST_FILLED_QUANTITY_DISAGREEMENT' in r['agreement_blockers']
    assert r['request_reviews'][0]['explicit_fill_quantity']=='1'


def test_changed_head_during_review_is_detected_without_undoing_external_append(tmp_path,monkeypatch):
    j,s,_,_=stores(tmp_path);inspect=s.inspect;calls=0;head=inspect()['head_sha256']
    def race():
        nonlocal calls
        calls+=1
        if calls==2:s.retain([],[],after='2026-10-18T00:00:00Z',until='2026-10-19T00:00:00Z')
        return inspect()
    monkeypatch.setattr(s,'inspect',race)
    with pytest.raises(ValueError,match='JOURNALS_CHANGED'):module.review(j,s,expected_request_head=j.recovery()['head_sha256'],expected_activity_head=head)
    assert inspect()['retained_batches']==2


def test_fresh_process_review_matches_without_mutation(tmp_path):
    j,s,_,_=stores(tmp_path);before={p.name:p.read_bytes() for p in tmp_path.iterdir()};expected=review(j,s)
    code='import json,sys;from nexus.broker_request_journal import BrokerRequestJournal;from nexus.broker_activity_store import BrokerActivityStore;from nexus.broker_request_activity_review import review;j=BrokerRequestJournal(sys.argv[1],sys.argv[3]);s=BrokerActivityStore(sys.argv[2],sys.argv[3]);print(json.dumps(review(j,s,expected_request_head=j.recovery()["head_sha256"],expected_activity_head=s.inspect()["head_sha256"])))'
    actual=json.loads(subprocess.check_output([sys.executable,'-c',code,str(j.path),str(s.path),ACCOUNT],text=True))
    assert actual==expected and before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}


def test_disposable_release_fixture():
    r=module.fixture_report();assert r['passed'] and r['offsetting_bad_price_detected'] and r['journals_unchanged']
    assert all(r[k] is False for k in module.FLAGS)


def test_untracked_activity_order_requires_review_instead_of_silent_omission(tmp_path):
    j,s,_,order=stores(tmp_path)
    other=dict(order,broker_order_id='11234567-89ab-cdef-0123-456789abcdef',client_order_id='manual',status='new',filled_quantity='0',average_fill_price=None)
    s.retain([],[other],after=START,until=END)
    r=review(j,s);assert r['untracked_activity_order_ids']==[other['broker_order_id']]
    assert 'UNTRACKED_ACTIVITY_ORDERS_REQUIRE_REVIEW' in r['agreement_blockers']


def test_equal_time_activity_conflict_never_becomes_overall_agreement(tmp_path):
    j,s,_,order=stores(tmp_path)
    s.retain([],[dict(order,status='canceled')],after=START,until=END)
    r=review(j,s)
    assert not r['local_request_activity_agreement'] and 'EQUAL_TIME_ORDER_SNAPSHOT_CONFLICT' in r['agreement_blockers']
    assert 'MERGED_ACTIVITY_NORMALIZATION_UNAVAILABLE' in r['agreement_blockers']


def test_request_append_during_review_is_detected_and_preserved(tmp_path,monkeypatch):
    j,s,_,_=stores(tmp_path);read=module._requests;calls=0
    def race(journal):
        nonlocal calls
        calls+=1
        if calls==2:journal.prepare(plan(client_order_id='newer'))
        return read(journal)
    monkeypatch.setattr(module,'_requests',race)
    with pytest.raises(ValueError,match='JOURNALS_CHANGED'):review(j,s)
    assert len(j.recovery()['requests'])==2
