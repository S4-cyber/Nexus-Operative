"""Durable cross-journal acceptance history stays private and fail closed."""
import os
import sqlite3
import hashlib

import pytest

from nexus.alpaca_paper_contract import sha
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore, PROFILE, ZERO
from test_alpaca_paper_inspection import ACCOUNT, OTHER


def heads(seed):
    return [sha(dict(seed=seed,index=index)) for index in range(4)]


def scope(seed='one'):
    pace_before,pace_after,store_before,store_after=heads(seed)
    return dict(profile=PROFILE,account_sha256=hashlib.sha256(ACCOUNT.encode()).hexdigest(),
        collection_scope_sha256=sha(dict(seed=seed,kind='scope')),
        account_fields_sha256=sha(dict(seed=seed,kind='account')),
        collection_observation_sha256=sha(dict(seed=seed,kind='observation')),
        activity_input_sha256=sha(dict(seed=seed,kind='input')),
        retained_receipt_sha256=sha(dict(seed=seed,kind='receipt')),
        pacing_before_head_sha256=pace_before,pacing_after_head_sha256=pace_after,
        retention_before_head_sha256=store_before,retention_after_head_sha256=store_after)


def bound(tmp_path,value=None,*,sessions=0,batches=0):
    value=value or scope();store=PaperActivityAcceptanceStore(tmp_path/'private'/'acceptance.sqlite3',ACCOUNT)
    store.bind(pacing_head=value['pacing_before_head_sha256'],retention_head=value['retention_before_head_sha256'],
        pacing_sessions=sessions,retained_batches=batches)
    return store


def test_missing_inspection_is_read_only_and_baseline_retains_legacy_uncertainty(tmp_path):
    path=tmp_path/'missing'/'acceptance.sqlite3';store=PaperActivityAcceptanceStore(path,ACCOUNT)
    report=store.inspect();assert not report['initialized'] and not path.parent.exists()
    store=bound(tmp_path,sessions=2,batches=1);report=store.inspect()
    assert report['initialized'] and report['records']==0 and report['preexisting_history_unverified']
    assert report['baseline_pacing_sessions']==2 and report['baseline_retained_batches']==1
    heads_only=bound(tmp_path/'heads-only',sessions=0,batches=0).inspect()
    assert heads_only['preexisting_history_unverified']


def test_record_replay_binds_both_head_transitions_and_duplicate_is_idempotent(tmp_path):
    value=scope();store=bound(tmp_path,value)
    admitted=(value['pacing_after_head_sha256'],value['retention_after_head_sha256'])
    first=store.append(value,admission=lambda:admitted);before=store.path.read_bytes()
    duplicate=store.append(value,admission=lambda:admitted)
    report=PaperActivityAcceptanceStore(store.path,ACCOUNT).inspect()
    assert not first['duplicate'] and duplicate['duplicate'] and before==store.path.read_bytes()
    assert report['records']==1 and report['last_record_sha256']==sha(value)
    assert report['last_record_scope']==value
    assert report['pacing_bound_head_sha256']==admitted[0] and report['retention_bound_head_sha256']==admitted[1]


def test_continuity_account_and_sampled_admission_fail_closed(tmp_path):
    value=scope();store=bound(tmp_path,value);admitted=(value['pacing_after_head_sha256'],value['retention_after_head_sha256'])
    bad=dict(value,pacing_before_head_sha256=sha(dict(wrong='pace')))
    with pytest.raises(ValueError,match='CONTINUITY_DISAGREEMENT'):store.append(bad,admission=lambda:admitted)
    with pytest.raises(ValueError,match='ADMISSION_CHANGED'):store.append(value,admission=lambda:(ZERO,ZERO))
    foreign=dict(value,account_sha256=sha(OTHER))
    with pytest.raises(ValueError,match='SCOPE_INVALID'):store.append(foreign,admission=lambda:admitted)
    assert store.inspect()['records']==0


def test_lost_postcommit_sample_retains_record_for_replay(tmp_path):
    value=scope();store=bound(tmp_path,value);admitted=(value['pacing_after_head_sha256'],value['retention_after_head_sha256']);calls=[]
    def changed():
        calls.append(1);return admitted if len(calls)==1 else (ZERO,ZERO)
    with pytest.raises(ValueError,match='ADMISSION_CHANGED'):store.append(value,admission=changed)
    assert store.inspect()['records']==1 and store.inspect()['last_record_sha256']==sha(value)


def test_chain_tamper_foreign_account_and_unsafe_paths_are_rejected(tmp_path):
    value=scope();store=bound(tmp_path,value)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE records SET previous=? WHERE seq=0",('f'*64,))
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):store.inspect()
    with pytest.raises(ValueError,match='ACCOUNT_OR_SCHEMA'):PaperActivityAcceptanceStore(bound(tmp_path/'foreign',value).path,OTHER).inspect()
    if os.name!='nt':
        target=tmp_path/'target.sqlite3';target.write_text('x');link=tmp_path/'link.sqlite3';link.symlink_to(target)
        with pytest.raises(ValueError,match='SYMLINK'):PaperActivityAcceptanceStore(link,ACCOUNT).inspect()


def test_distinct_second_transition_requires_exact_previous_heads(tmp_path):
    first=scope();store=bound(tmp_path,first)
    store.append(first,admission=lambda:(first['pacing_after_head_sha256'],first['retention_after_head_sha256']))
    second=scope('two');second['pacing_before_head_sha256']=first['pacing_after_head_sha256']
    second['retention_before_head_sha256']=first['retention_after_head_sha256']
    admitted=(second['pacing_after_head_sha256'],second['retention_after_head_sha256'])
    store.append(second,admission=lambda:admitted)
    report=store.inspect();assert report['records']==2 and report['last_record_sha256']==sha(second)
    assert report['last_record_scope']==second
    assert store.replay_scopes()==(first,second)
    assert 'record_scopes' not in report


@pytest.mark.parametrize('verdict',[False,1])
def test_optional_input_guard_rolls_back_new_record_before_commit(tmp_path,verdict):
    value=scope();store=bound(tmp_path,value);before=store.path.read_bytes()
    admitted=(value['pacing_after_head_sha256'],value['retention_after_head_sha256'])
    with pytest.raises(ValueError,match='INPUT_REVOKED'):
        store.append(value,admission=lambda:admitted,precommit_check=lambda:verdict)
    assert store.inspect()['records']==0 and store.path.read_bytes()==before
