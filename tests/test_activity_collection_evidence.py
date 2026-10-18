"""Injected pages/temp journals only; never actual credentials or broker IO."""
import copy
import json
import sqlite3
from pathlib import Path
import subprocess
import sys

import pytest
from nexus import activity_collection_evidence as evidence
from nexus import broker_activity_store as storage
from nexus.alpaca_paper_contract import sha, canonical
from test_alpaca_activity_collection import collect, page, fee, START, END, NOW
from test_alpaca_paper_inspection import Opener, account, ACCOUNT, KEY, SECRET


def target(tmp_path): return storage.BrokerActivityStore(tmp_path/'activities.db',ACCOUNT)
def one(): return collect()['collection_observation']
def retain(store, observation=None, rows=None, **changes):
    return store.retain([fee()] if rows is None else rows, [],after=START,until=END,
        collection_observation=one() if observation is None else observation,**changes)


def test_collection_retains_page_identity_timing_and_fingerprints_across_restart(tmp_path):
    store=target(tmp_path);result=collect(activity_store=store)
    fresh=storage.BrokerActivityStore(store.path,ACCOUNT);state=fresh.inspect()
    receipt=state['receipts'][result['local_retention']['receipt_id']]
    observation=receipt['collection_observation']
    assert observation==result['collection_observation']
    assert observation['declared_transport']=='INJECTED_UNATTESTED'
    assert observation['read_started_at']==observation['read_completed_at']==NOW
    assert observation['pages'][0]['activity_ids']==[fee()['id']]
    assert state['retained_collection_observations']==1 and not state['collection_source_attested']
    review=fresh.review()['collection_observation_review']
    assert review['retained_pages']==review['short_last_page_observed']==1
    assert not review['source_attested'] and not review['activity_coverage_verified']
    text=json.dumps(state)
    assert KEY not in text and SECRET not in text and 'description' not in text
    assert fresh.path.read_bytes()==store.path.read_bytes()


def test_two_full_pages_keep_opaque_case_sensitive_cursor_without_coverage_claim(tmp_path):
    store=target(tmp_path);first=page();first[-1]['id']=first[-1]['id'].upper();second=page(101)
    result=collect(Opener([account(),first,second,account()]),activity_store=store)
    observation=result['collection_observation'];pages=observation['pages']
    assert pages[0]['response_cursor']==pages[1]['request_cursor']==first[-1]['id']
    assert pages[0]['activity_ids'][-1]==first[-1]['id'].lower()
    assert pages[1]['response_cursor']==second[-1]['id']
    assert result['collection_limit_reached'] and not result['activity_coverage_verified']
    reviewed=store.review()
    assert reviewed['collection_observation_review']['retained_pages']==2
    assert reviewed['collection_observation_review']['short_last_page_observed']==0
    assert 'COLLECTION_OBSERVATIONS_NOT_SOURCE_ATTESTATIONS' in reviewed['review_reasons']
    assert not reviewed['ready_for_routing']


def test_overlapping_semantic_duplicates_preserve_received_ids_but_store_content_once(tmp_path):
    store=target(tmp_path);first=page();second=[dict(first[-1]),fee(101)]
    result=collect(Opener([account(),first,second,account()]),activity_store=store)
    assert result['rows_received']==102 and store.inspect()['retained_activities']==101
    assert result['collection_observation']['pages'][1]['activity_ids'][0]==first[-1]['id']


def test_empty_page_retains_an_observation_instead_of_claiming_no_activities(tmp_path):
    store=target(tmp_path);result=collect(Opener([account(),[],account()]),activity_store=store)
    state=store.inspect()
    assert state['retained_batches']==state['retained_collection_observations']==1
    assert state['retained_activities']==0 and not state['activity_coverage_verified']
    assert result['collection_observation']['pages'][0]['response_cursor'] is None
    assert result['collection_observation']['pages'][0]['activity_ids']==[]


def test_identical_observation_replays_but_new_read_time_appends_without_data_copies(tmp_path):
    store=target(tmp_path);first=collect(activity_store=store)
    assert collect(activity_store=store)['local_retention']['duplicate']
    second=collect(activity_store=store,wall_clock=lambda:NOW+60)
    assert not second['local_retention']['duplicate']
    assert first['local_retention']['receipt_id']!=second['local_retention']['receipt_id']
    state=store.inspect();assert state['retained_activities']==1 and state['retained_batches']==2
    with sqlite3.connect(store.path) as db:
        body=json.loads(db.execute('SELECT body FROM receipts WHERE seq=2').fetchone()[0])
    assert body['new_activities']==[]


def test_supplied_legacy_and_collection_receipts_stay_distinct(tmp_path):
    store=target(tmp_path);store.retain([fee()],[],after=START,until=END)
    collect(activity_store=store)
    state=store.inspect();assert state['retained_batches']==2 and state['retained_collection_observations']==1
    review=store.review()['collection_observation_review']
    assert review['batches_without_observations']==1
    assert not review['source_attested']


@pytest.mark.parametrize('change,code',[
    ('extra','SHAPE'),('transport','TRANSPORT'),('account_digest','ACCOUNT_DIGEST'),('time_regression','TIME_SCOPE'),
    ('time_budget','TIME_SCOPE'),('future_window','TIME_SCOPE'),('zero_pages','PAGE_CAP'),('extra_pages','PAGE_CAP'),
    ('page_extra','PAGE_SHAPE'),('oversized_page','ROW_CAP'),('cursor_chain','CURSOR_CHAIN'),
    ('response_cursor','RESPONSE_CURSOR'),('query_digest','DIGEST_DISAGREEMENT'),('content_digest','DIGEST_DISAGREEMENT'),
    ('unknown_id','UNKNOWN_ACTIVITY'),('page_time','PAGE_TIME'),('missing_set','ACTIVITY_SET_DISAGREEMENT'),
])
def test_malformed_metadata_rejected_before_store_creation(tmp_path,change,code):
    store=target(tmp_path);value=one();p=value['pages'][0]
    if change=='extra': value['private']=SECRET
    elif change=='transport': value['declared_transport']='AUTHENTICATED'
    elif change=='account_digest': value['account_fields_sha256']='bad'
    elif change=='time_regression': value['read_completed_at']=NOW-1
    elif change=='time_budget': value['read_completed_at']=NOW+20
    elif change=='future_window': value['read_started_at']=value['read_completed_at']=1000;p['requested_at']=p['received_at']=1000
    elif change=='zero_pages': value['pages']=[]
    elif change=='extra_pages': value['pages']*=3
    elif change=='page_extra': p['private']=SECRET
    elif change=='oversized_page': p['activity_ids']*=101
    elif change=='cursor_chain': p['request_cursor']=fee(2)['id']
    elif change=='response_cursor': p['response_cursor']=None
    elif change=='query_digest': p['query_sha256']='0'*64
    elif change=='content_digest': p['normalized_records_sha256']='0'*64
    elif change=='unknown_id': p['activity_ids']=[fee(2)['id']];p['response_cursor']=fee(2)['id']
    elif change=='page_time': p['received_at']=NOW+1
    elif change=='missing_set': p.update(activity_ids=[],response_cursor=None,normalized_records_sha256=sha([]))
    with pytest.raises(ValueError,match=code): retain(store,value)
    assert not store.path.exists()


def test_second_page_after_short_end_and_cursor_loop_are_rejected(tmp_path):
    store=target(tmp_path);value=one();value['pages'].append(copy.deepcopy(value['pages'][0]))
    value['pages'][1]['request_cursor']=value['pages'][0]['response_cursor']
    with pytest.raises(ValueError,match='PAGE_AFTER_SHORT_END'): retain(store,value)
    first=page();result=collect(Opener([account(),first,[fee(101)],account()]))
    value=result['collection_observation'];value['pages'][1]['activity_ids']=[first[-1]['id']]
    value['pages'][1]['response_cursor']=first[-1]['id']
    with pytest.raises(ValueError,match='CURSOR_NO_PROGRESS'): retain(store,value,rows=first+[fee(101)])


def test_metadata_frozen_before_admission_callback(tmp_path):
    store=target(tmp_path);original=one();expected=copy.deepcopy(original)
    def admission(): original['pages'][0]['activity_ids'].clear();return True
    receipt=retain(store,original,admission=admission)
    assert store.inspect()['receipts'][receipt['receipt_id']]['collection_observation']==expected


def test_rehashed_internally_inconsistent_metadata_fails_full_replay(tmp_path):
    store=target(tmp_path);retain(store)
    with sqlite3.connect(store.path) as db:
        encoded,parent=db.execute('SELECT body,previous FROM receipts WHERE seq=1').fetchone()
        body=json.loads(encoded);body['collection_observation']['pages'][0]['normalized_records_sha256']='0'*64
        db.execute('UPDATE receipts SET body=?,digest=? WHERE seq=1',
            (canonical(body),sha(dict(seq=1,body=body,previous=parent))))
    with pytest.raises(ValueError,match='DIGEST_DISAGREEMENT'): store.inspect()


def test_lost_collection_ack_keeps_reviewable_metadata(tmp_path,monkeypatch):
    store=target(tmp_path);original=store.retain
    def lose(*args,**kwargs): original(*args,**kwargs);raise OSError('lost')
    monkeypatch.setattr(store,'retain',lose)
    with pytest.raises(ValueError): collect(activity_store=store)
    fresh=storage.BrokerActivityStore(store.path,ACCOUNT)
    assert fresh.inspect()['retained_collection_observations']==1
    assert fresh.review()['collection_observation_review']['retained_pages']==1


def test_admission_revocation_rolls_back_new_metadata_and_content(tmp_path):
    store=target(tmp_path);retain(store);before=store.inspect();value=one()
    value['read_completed_at']=NOW+1;calls=[]
    def admission(): calls.append(1);return len(calls)==1
    with pytest.raises(ValueError,match='ADMISSION_REVOKED'): retain(store,value,admission=admission)
    assert store.inspect()==before


def test_legacy_read_only_inspection_of_missing_store_creates_nothing(tmp_path):
    store=target(tmp_path);result=store.inspect()
    assert result['retained_collection_observations']==0 and not store.path.exists()


def test_caller_declared_https_label_is_never_attested(tmp_path):
    store=target(tmp_path);value=one();value['declared_transport']='PAPER_HTTPS_READS'
    retain(store,value);result=store.review()
    assert result['collection_observation_review']['declared_transports']==['PAPER_HTTPS_READS']
    assert not result['collection_observation_review']['source_attested']
    assert not result['activity_coverage_verified'] and not result['ready_for_routing']


@pytest.mark.parametrize('phase,observations',[('before',0),('after',1)])
def test_real_process_death_preserves_atomic_metadata_and_global_content(tmp_path,phase,observations):
    store=target(tmp_path);store.retain([fee()],[],after=START,until=END)
    script='''from nexus.broker_activity_store import BrokerActivityStore
import sys,json,os
g=BrokerActivityStore(sys.argv[1],sys.argv[2]); original=g._insert
phase=sys.argv[4]
def die(db,seq,body,previous):
    result=original(db,seq,body,previous)
    if phase=='before' and 'collection_observation' in body: os._exit(51)
    return result
g._insert=die
row=dict(id='20261017000000000::11234567-89ab-cdef-0123-000000000001',activity_type='FEE',net_amount='-0.01',status='executed',date='2026-10-16')
g.retain([row],[],after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z',collection_observation=json.loads(sys.argv[3]))
os._exit(52)
'''
    result=subprocess.run([sys.executable,'-c',script,str(store.path),ACCOUNT,json.dumps(one()),phase],capture_output=True,timeout=20)
    assert result.returncode==(51 if phase=='before' else 52)
    fresh=storage.BrokerActivityStore(store.path,ACCOUNT).inspect()
    assert fresh['retained_collection_observations']==observations
    assert fresh['retained_batches']==1+observations and fresh['retained_activities']==1


def test_final_account_failure_cannot_leave_partial_page_metadata(tmp_path):
    store=target(tmp_path);retain(store);before=store.inspect()
    changed=account();changed['cash']='5'
    with pytest.raises(ValueError): collect(Opener([account(),[fee(2)],changed]),activity_store=store)
    assert store.inspect()==before


def test_full_receipt_cap_prevents_network_but_offline_exact_replay_still_works(tmp_path,monkeypatch):
    store=target(tmp_path);retain(store)
    monkeypatch.setattr(storage,'MAX_BATCHES',1)
    opener=Opener()
    with pytest.raises(ValueError,match='RETENTION_BATCH_CAP_REVIEW'): collect(opener,activity_store=store)
    assert not opener.requests and store.inspect()['retained_batches']==1
    assert retain(store)['duplicate']
