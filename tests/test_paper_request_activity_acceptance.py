"""Accepted-history composition for original requests and explicit fills."""
import copy
import json
import subprocess
import sys

import pytest

from nexus import alpaca_activity_normalizer as adapter
from nexus import paper_request_activity_acceptance as subject
from nexus.activity_collection_evidence import SCHEMA, page_observation, validate
from nexus.alpaca_activity_collection import PAGE_SIZE
from nexus.alpaca_paper_contract import preview, sha
from nexus.broker_activity_store import BrokerActivityStore
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore, PROFILE
from nexus.paper_read_pacing import PaperReadPacing

ACCOUNT='01234567-89ab-cdef-0123-456789abcdef'
AFTER='2026-10-17T00:00:00Z';UNTIL='2026-10-18T00:00:00Z'


def prepared(root, prices=('98','100')):
    root.mkdir(exist_ok=True)
    pacing=PaperReadPacing(root/'pacing.sqlite3',ACCOUNT)
    activity=BrokerActivityStore(root/'activity.sqlite3',ACCOUNT)
    acceptance=PaperActivityAcceptanceStore(root/'acceptance.sqlite3',ACCOUNT)
    requests=BrokerRequestJournal(root/'requests.sqlite3',ACCOUNT)
    pace_before=pacing.inspect();activity_before=activity.inspect()
    acceptance.bind(pacing_head=pace_before['head_sha256'],
        retention_head=activity_before['head_sha256'],pacing_sessions=0,retained_batches=0)
    token=pacing.begin(admission=lambda:1000);pacing.finish(token,admission=lambda:1000,status_code=200)

    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,
        fractionable=True,min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    plan=preview('nx_accept_fixture','BTC-USD','BUY','2','100',asset,
        metadata_at=1000,evaluated_at=1001)
    broker_id='11234567-89ab-cdef-0123-456789abcdef'
    average=str((int(prices[0])+int(prices[1]))/2).rstrip('0').rstrip('.')
    raw=dict(**plan['payload'],id=broker_id,asset_class='crypto',status='filled',
        filled_qty='2',filled_avg_price=average,updated_at='2026-10-17T18:00:02+00:00')
    requests.prepare(plan);requests.mark_submission_uncertain('nx_accept_fixture')
    requests.observe('nx_accept_fixture',raw)
    order=dict(broker_order_id=broker_id,client_order_id='nx_accept_fixture',symbol='BTC/USD',
        side='buy',status='filled',filled_quantity='2',average_fill_price=average,
        updated_at=raw['updated_at'])
    fills=[dict(id='20261017180000000::'+f'01234567-89ab-cdef-0123-{i:012d}',
        activity_type='FILL',order_id=broker_id,symbol='BTCUSD',side='buy',
        type='partial_fill' if i==1 else 'fill',qty='1',price=price,cum_qty=str(i),
        leaves_qty=str(2-i),transaction_time=f'2026-10-17T18:00:0{i}Z')
        for i,price in ((1,prices[0]),(2,prices[1]))]
    normalized=adapter.normalize(fills,[order],expected_account_id=ACCOUNT)
    observed_at=1792281600
    observation=validate(dict(schema=SCHEMA,declared_transport='INJECTED_UNATTESTED',
        read_started_at=observed_at,read_completed_at=observed_at,
        account_fields_sha256=sha(dict(fixture='accepted-request-fill')),
        pages=[page_observation(fills,normalized['normalized_activities'],after=AFTER,until=UNTIL,
            request_cursor=None,requested_at=observed_at,received_at=observed_at)]),
        dict(activities=normalized['normalized_activities']),after=AFTER,until=UNTIL)
    receipt=activity.retain(fills,[order],after=AFTER,until=UNTIL,
        collection_observation=observation)
    pace_after=pacing.inspect();activity_after=activity.inspect()
    scope=dict(profile=PROFILE,account_sha256=activity_after['account_sha256'],
        collection_scope_sha256=sha(dict(account_sha256=activity_after['account_sha256'],
            after=AFTER,until=UNTIL,direction='asc',page_size=PAGE_SIZE)),
        account_fields_sha256=observation['account_fields_sha256'],
        collection_observation_sha256=sha(observation),
        activity_input_sha256=normalized['input_sha256'],
        retained_receipt_sha256=receipt['receipt_id'],
        pacing_before_head_sha256=pace_before['head_sha256'],
        pacing_after_head_sha256=pace_after['head_sha256'],
        retention_before_head_sha256=activity_before['head_sha256'],
        retention_after_head_sha256=activity_after['head_sha256'])
    acceptance.append(scope,admission=lambda:(pacing.inspect()['head_sha256'],activity.inspect()['head_sha256']))
    options=dict(expected_account_id=ACCOUNT,request_journal_path=requests.path,
        pacing_path=pacing.path,activity_store_path=activity.path,
        acceptance_store_path=acceptance.path,
        expected_request_head=requests.recovery()['head_sha256'],
        expected_activity_head=activity.inspect()['head_sha256'])
    return options,requests,activity


def snapshot(root):
    return tuple((p.name,p.read_bytes()) for p in sorted(root.iterdir()) if p.is_file())


def test_complete_accepted_history_binds_original_terms_and_explicit_fills(tmp_path):
    options,_,_=prepared(tmp_path);before=snapshot(tmp_path);result=subject.review(**options)
    assert result['mode']=='PAPER_REQUEST_ACTIVITY_ACCEPTANCE_REVIEW'
    assert result['complete_accepted_history_replayed'] is True
    assert result['local_request_activity_agreement'] is True
    assert result['request_activity_review']['request_reviews'][0]['explicit_fill_count']==2
    assert result['journals_rechecked_unchanged'] is True and snapshot(tmp_path)==before
    assert all(result[name] is False for name in subject.FLAGS)


def test_unaccepted_direct_activity_append_blocks_before_component_review(tmp_path,monkeypatch):
    options,_,activity=prepared(tmp_path)
    activity.retain([],[],after='2026-10-18T00:00:00Z',until='2026-10-19T00:00:00Z')
    called=False
    def forbidden(*args,**kwargs):
        nonlocal called;called=True;raise AssertionError
    monkeypatch.setattr(subject.request_review,'review',forbidden)
    with pytest.raises(ValueError,match='ACCEPTED_HISTORY_REQUIRED'):subject.review(**options)
    assert called is False


def test_individual_bad_fill_price_remains_a_component_blocker(tmp_path):
    options,_,_=prepared(tmp_path,prices=('85','113'))
    result=subject.review(**options)
    assert not result['local_request_activity_agreement']
    assert 'INDIVIDUAL_FILL_LIMIT_DISAGREEMENT' in result['request_activity_review']['agreement_blockers']


@pytest.mark.parametrize('field,value',[('cross_journal_recovery_state','UNBOUND_CROSS_JOURNAL_TRANSITION_REVIEW_REQUIRED'),
    ('preexisting_history_unverified',True),('all_collection_cursor_sequences_complete',False),
    ('all_accepted_transitions_replayed',False)])
def test_every_complete_history_requirement_fails_closed(tmp_path,monkeypatch,field,value):
    options,_,_=prepared(tmp_path);actual=subject.acceptance_status(
        expected_account_id=ACCOUNT,pacing_path=options['pacing_path'],
        activity_store_path=options['activity_store_path'],
        acceptance_store_path=options['acceptance_store_path'])
    actual[field]=value;monkeypatch.setattr(subject,'acceptance_status',lambda **kwargs:actual)
    with pytest.raises(ValueError,match='ACCEPTED_HISTORY_REQUIRED'):subject.review(**options)


def test_stale_explicit_heads_refuse(tmp_path):
    options,_,_=prepared(tmp_path)
    with pytest.raises(ValueError,match='ACTIVITY_HEAD_DISAGREEMENT'):
        subject.review(**dict(options,expected_activity_head='0'*64))
    with pytest.raises(ValueError,match='HEAD_DISAGREEMENT'):
        subject.review(**dict(options,expected_request_head='0'*64))


def test_request_change_during_review_is_detected_and_preserved(tmp_path,monkeypatch):
    options,requests,_=prepared(tmp_path);original=requests.recovery
    calls=0
    def changed():
        nonlocal calls;calls+=1
        result=original()
        if calls==1:result=copy.deepcopy(result);result['head_sha256']='f'*64
        return result
    monkeypatch.setattr(subject.BrokerRequestJournal,'recovery',lambda self:changed())
    with pytest.raises(ValueError,match='JOURNALS_CHANGED_DURING_REVIEW'):subject.review(**options)


def test_cli_is_read_only_and_returns_private_review(tmp_path):
    options,_,_=prepared(tmp_path);before=snapshot(tmp_path)
    command=[sys.executable,'-m','nexus.paper_request_activity_acceptance',
        '--expected-account',ACCOUNT,'--request-journal',str(options['request_journal_path']),
        '--pacing',str(options['pacing_path']),'--activity-store',str(options['activity_store_path']),
        '--acceptance-store',str(options['acceptance_store_path']),
        '--request-head',options['expected_request_head'],'--activity-head',options['expected_activity_head']]
    child=subprocess.run(command,capture_output=True,text=True,timeout=20)
    assert child.returncode==0 and json.loads(child.stdout)['local_request_activity_agreement'] is True
    assert snapshot(tmp_path)==before
