"""Declared read purposes, exact transport paths and legacy uncertainty."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from nexus import paper_read_pacing as pacing,alpaca_paper_inspection as reader
from nexus import paper_quote_capture as capture,paper_quote_bundle as bundle
from nexus.alpaca_paper_contract import canonical,sha
from test_alpaca_paper_inspection import ACCOUNT,KEY,SECRET,account,Opener


def test_missing_history_and_legacy_sessions_stay_unknown_without_writes(tmp_path):
    gate=pacing.PaperReadPacing(tmp_path/'absent'/'pacing.sqlite3',ACCOUNT)
    assert gate.operation_history()==() and not gate.path.parent.exists()
    token=gate.begin(admission=lambda:1000);gate.finish(token,admission=lambda:1000,status_code=200)
    before=gate.path.read_bytes();history=gate.operation_history();state=gate.inspect()
    assert history[0]['operation']=='LEGACY_UNSPECIFIED' and history[0]['outcome']=='SUCCESS'
    assert state['operation_counts']['LEGACY_UNSPECIFIED']==1
    assert all(state['operation_counts'][key]==0 for key in pacing.OPERATIONS)
    assert state['operation_identity_attested'] is state['mixed_operation_acceptance_verified'] is False
    assert gate.path.read_bytes()==before


@pytest.mark.parametrize('operation',pacing.OPERATIONS)
def test_typed_begin_requires_owned_account_scope_and_retains_outcome(tmp_path,operation):
    gate=pacing.PaperReadPacing(tmp_path/'p.sqlite3',ACCOUNT)
    with pytest.raises(ValueError,match='OPERATION_OWNER_REQUIRED'):gate.begin(admission=lambda:1000,operation=operation)
    assert not gate.path.exists()
    with gate.ownership() as owner:
        token=gate.begin(admission=lambda:1000,ownership=owner,operation=operation)
        assert gate.inspect()['pending_operation']==operation
        pending=gate.operation_history()[0];assert pending['outcome']=='PENDING_UNKNOWN' and pending['completed_at'] is None
        gate.finish(token,admission=lambda:1001,status_code=429,retry_after='120')
    record=pacing.PaperReadPacing(gate.path,ACCOUNT).operation_history()[0]
    assert record['operation']==operation and record['outcome']=='RATE_LIMITED'
    assert record['started_at']==1000 and record['completed_at']==1001
    assert gate.inspect()['next_allowed_at']==1121 and gate.inspect()['pending_operation'] is None
    assert gate.completed_sessions()==(dict(before_head_sha256=record['before_head_sha256'],after_head_sha256=record['after_head_sha256'],outcome='RATE_LIMITED'),)


@pytest.mark.parametrize('operation',(True,1,'UNKNOWN','',{},[]))
def test_invalid_purpose_refuses_before_file_creation(tmp_path,operation):
    gate=pacing.PaperReadPacing(tmp_path/'missing'/'p.sqlite3',ACCOUNT)
    with pytest.raises(ValueError,match='OPERATION_INVALID'):gate.begin(admission=lambda:1000,operation=operation)
    assert not gate.path.parent.exists()
    with pytest.raises(ValueError,match='OPERATION_INVALID'):
        reader._ReadSession(KEY,SECRET,confirm_read_only_paper=True,operation=operation)


@pytest.mark.parametrize('operation',pacing.OPERATIONS)
def test_purpose_is_immutable_in_hash_chain(tmp_path,operation):
    gate=pacing.PaperReadPacing(tmp_path/'p.sqlite3',ACCOUNT)
    with gate.ownership() as owner:gate.begin(admission=lambda:1000,ownership=owner,operation=operation)
    with sqlite3.connect(gate.path) as db:
        body=json.loads(db.execute('SELECT body FROM events WHERE seq=1').fetchone()[0]);body['operation']='UNKNOWN'
        db.execute('UPDATE events SET body=? WHERE seq=1',(canonical(body),))
    before=gate.path.read_bytes()
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):gate.operation_history()
    assert gate.path.read_bytes()==before


@pytest.mark.parametrize('operation',pacing.OPERATIONS)
def test_wrong_operation_path_is_denied_before_get_and_leaves_pending(tmp_path,operation):
    gate=pacing.PaperReadPacing(tmp_path/'p.sqlite3',ACCOUNT);opener=Opener()
    with pytest.raises(ValueError,match='OPERATION_PATH_SEQUENCE'):
        with reader._ReadSession(KEY,SECRET,confirm_read_only_paper=True,expected_account_id=ACCOUNT,pacing=gate,
            operation=operation,opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50) as session:
            session.get(reader.URLS[1])  # Every actual operation requires account first.
    assert not opener.requests and gate.inspect()['pending_operation']==operation
    assert gate.operation_history()[0]['outcome']=='PENDING_UNKNOWN'


@pytest.mark.parametrize('operation',pacing.OPERATIONS)
def test_incomplete_operation_cannot_commit_success(tmp_path,operation):
    gate=pacing.PaperReadPacing(tmp_path/'p.sqlite3',ACCOUNT);opener=Opener([account()])
    with pytest.raises(ValueError,match='OPERATION_INCOMPLETE'):
        with reader._ReadSession(KEY,SECRET,confirm_read_only_paper=True,expected_account_id=ACCOUNT,pacing=gate,
            operation=operation,opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50) as session:session.get(reader.URLS[0])
    assert len(opener.requests)==1 and gate.inspect()['pending_token'] is not None
    assert not gate.completed_sessions()


def test_inventory_cannot_expand_to_quote_host_mid_session(tmp_path):
    gate=pacing.PaperReadPacing(tmp_path/'p.sqlite3',ACCOUNT);opener=Opener([account()])
    with pytest.raises(ValueError,match='OPERATION_PATH_SEQUENCE'):
        with reader._ReadSession(KEY,SECRET,confirm_read_only_paper=True,expected_account_id=ACCOUNT,pacing=gate,
            operation='ACCOUNT_INVENTORY',opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50) as session:
            session.get(reader.URLS[0]);session.get_btc_quote()
    assert len(opener.requests)==1 and gate.inspect()['last_outcome'] is None


def test_actual_five_readers_record_fixed_purposes(tmp_path):
    from test_paper_asset_rules import read as asset_read
    from test_alpaca_activity_collection import collect
    from test_broker_paper_recovery import recover
    from test_alpaca_paper_contract import plan
    from nexus.broker_request_journal import BrokerRequestJournal
    from test_paper_quote_observation import options
    from nexus.paper_quote_observation import inspect_quote
    def gate(name):return pacing.PaperReadPacing(tmp_path/(name+'.sqlite3'),ACCOUNT)
    inventory=gate('inventory');reader.inspect_account(KEY,SECRET,confirm_read_only_paper=True,expected_account_id=ACCOUNT,
        opener=Opener(),wall_clock=lambda:1000,elapsed_clock=lambda:50,pacing=inventory)
    asset=gate('asset');asset_read(pacing=asset)
    activity=gate('activity');collect(Opener([account(),[],account()]),pacing=activity)
    j=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT);j.prepare(plan());j.mark_submission_uncertain('nx_fixture')
    recovery=gate('recovery');recover(j,pacing=recovery)
    quote=gate('quote');inspect_quote(KEY,SECRET,**dict(options(tmp_path),pacing=quote))
    for journal,operation in zip((inventory,asset,activity,recovery,quote),pacing.OPERATIONS):
        assert journal.operation_history()[0]['operation']==operation
        assert journal.inspect()['operation_counts'][operation]==1 and journal.inspect()['operation_counts'][pacing.LEGACY_OPERATION]==0


def test_same_budget_cooldown_survives_changed_purpose(tmp_path):
    from test_paper_asset_rules import read as asset_read
    gate=pacing.PaperReadPacing(tmp_path/'shared.sqlite3',ACCOUNT);asset_read(pacing=gate)
    before=gate.path.read_bytes();opener=Opener()
    with pytest.raises(ValueError,match='RESPONSE_INVALID'):
        reader.inspect_account(KEY,SECRET,confirm_read_only_paper=True,expected_account_id=ACCOUNT,
            opener=opener,wall_clock=lambda:1001,elapsed_clock=lambda:50,pacing=gate)
    assert not opener.requests and gate.path.read_bytes()==before
    assert gate.inspect()['operation_counts']['BTC_ASSET_RULES']==1 and gate.inspect()['sessions']==1


def test_real_process_death_retains_typed_unknown_and_interruption(tmp_path):
    path=tmp_path/'p.sqlite3'
    script='''import os,sys
from nexus.paper_read_pacing import PaperReadPacing
p=PaperReadPacing(sys.argv[1],sys.argv[2])
with p.ownership() as owner:
 p.begin(admission=lambda:1000,ownership=owner,operation='BTC_QUOTE_OBSERVATION')
 os._exit(73)
'''
    child=subprocess.run([sys.executable,'-c',script,str(path),ACCOUNT],capture_output=True,timeout=30)
    assert child.returncode==73 and not child.stdout and not child.stderr
    gate=pacing.PaperReadPacing(path,ACCOUNT);state=gate.inspect();before=path.read_bytes()
    assert state['pending_operation']=='BTC_QUOTE_OBSERVATION'
    assert gate.operation_history()[0]['outcome']=='PENDING_UNKNOWN' and path.read_bytes()==before
    gate.interrupt(state['pending_token'],expected_head=state['head_sha256'],expected_account_id=ACCOUNT,
        confirm_interrupted=True,admission=lambda:1001)
    record=gate.operation_history()[0]
    assert record['operation']=='BTC_QUOTE_OBSERVATION' and record['outcome']=='INTERRUPTED_UNKNOWN'
    assert gate.inspect()['next_allowed_at']==87401 and gate.inspect()['sessions']==1


def recast_bundle_operation(value,operation):
    rows=value['pacing_events'];previous='0'*64;token=None
    for row in rows:
        body=json.loads(row[1])
        if body.get('kind')=='BEGIN':
            if operation is None:body.pop('operation',None)
            else:body['operation']=operation
        if body.get('kind')=='RESULT':body['token']=token
        row[1]=canonical(body);row[2]=previous;row[3]=sha(dict(seq=row[0],body=body,previous=previous))
        if body.get('kind')=='BEGIN':token=row[3]
        previous=row[3]
    value['heads']['pacing']=previous;previous='0'*64
    for row in value['quote_records']:
        body=json.loads(row[1])
        if row[0]:body['pacing_after_head_sha256']=value['heads']['pacing']
        row[1]=canonical(body);row[2]=previous;row[3]=sha(dict(seq=row[0],body=body,previous=previous));previous=row[3]
    value['heads']['quote']=previous
    value['bundle_sha256']=sha({k:v for k,v in value.items() if k!='bundle_sha256'})


def test_legacy_quote_bundle_replays_without_inventing_purpose(tmp_path):
    options=bundle._fixture(tmp_path/'private');value=bundle.loads(bundle.snapshot(**options));recast_bundle_operation(value,None)
    assert bundle.validate(value)['local_capture_history_agreement']
    gate=pacing.PaperReadPacing('unused',value['account_id'])
    with closing(bundle._memory('events','seq INTEGER PRIMARY KEY,body TEXT,previous TEXT,digest TEXT',value['pacing_events'])) as db:
        state=gate._load(db)
    assert state['operation_sessions'][0]['operation']=='LEGACY_UNSPECIFIED'


def test_self_consistent_wrong_typed_quote_receipt_is_rejected(tmp_path):
    options=bundle._fixture(tmp_path/'private');value=bundle.loads(bundle.snapshot(**options));recast_bundle_operation(value,'ACCOUNT_INVENTORY')
    with pytest.raises(ValueError,match='UNBOUND_HISTORY'):bundle.validate(value)


def test_pacing_status_exposes_only_fixed_counts_and_unknown_legacy(tmp_path):
    from nexus.paper_acceptance_probe import pacing_status
    gate=pacing.PaperReadPacing(tmp_path/'p.sqlite3',ACCOUNT);token=gate.begin(admission=lambda:1000)
    gate.finish(token,admission=lambda:1000,status_code=200);before=gate.path.read_bytes()
    report=pacing_status(expected_account_id=ACCOUNT,pacing_path=gate.path)
    assert report['legacy_unspecified_sessions']==1 and report['pending_operation'] is None
    assert set(report['operation_counts'])=={*pacing.OPERATIONS,pacing.LEGACY_OPERATION}
    assert report['operation_identity_attested'] is report['mixed_operation_acceptance_verified'] is False
    assert gate.path.read_bytes()==before


def test_invalid_typed_owner_refuses_before_database_creation(tmp_path):
    gate=pacing.PaperReadPacing(tmp_path/'missing'/'p.sqlite3',ACCOUNT)
    for owner in (False,object()):
        with pytest.raises(ValueError,match='OWNER_REQUIRED'):gate.begin(admission=lambda:1000,operation='BTC_ASSET_RULES',ownership=owner)
    assert not gate.path.parent.exists()


def test_premature_internal_success_cannot_release_the_reservation(tmp_path):
    gate=pacing.PaperReadPacing(tmp_path/'p.sqlite3',ACCOUNT)
    with pytest.raises(ValueError,match='OPERATION_INCOMPLETE'):
        with reader._ReadSession(KEY,SECRET,confirm_read_only_paper=True,expected_account_id=ACCOUNT,pacing=gate,
            operation='ACCOUNT_INVENTORY',opener=Opener([account()]),wall_clock=lambda:1000,elapsed_clock=lambda:50) as session:
            session.get(reader.URLS[0]);session._pacing_result(status_code=200)
    assert gate.inspect()['pending_token'] is not None and not gate.completed_sessions()


def test_aggregate_fixture_covers_every_reader_without_provider_or_vault():
    r=pacing.typed_fixture_report()
    assert r['passed'] and all(r[k] is True for k in pacing.OPERATION_CHECKS)
    assert all(type(v) is bool or k=='source' for k,v in r.items())
    assert all(r[k] is False for k in pacing.OPERATION_FLAGS)


@pytest.mark.parametrize('field,value,code',[
    ('five_actual_readers_typed',False,'TYPED_READ_OPERATIONS'),
    ('operation_identity_attested',True,'TYPED_READ_AUTHORITY'),
    ('mixed_operation_acceptance_verified',True,'TYPED_READ_AUTHORITY')])
def test_release_evidence_requires_typed_checks_without_authority(field,value,code):
    from nexus import release_trials
    r=pacing.typed_fixture_report();r[field]=value
    with pytest.raises(ValueError,match='RELEASE_TRIAL_'+code):release_trials._validate('pacing',dict(passed=True,typed_operations=r))


def test_activity_receipt_cannot_accept_a_different_declared_operation(tmp_path,monkeypatch):
    from nexus import paper_request_activity_acceptance,paper_activity_acceptance_probe
    options,_=paper_request_activity_acceptance._fixture(tmp_path/'private')
    common={k:options[k] for k in ('expected_account_id','request_journal_path','pacing_path','activity_store_path','acceptance_store_path')}
    before=paper_activity_acceptance_probe.status(**common)
    assert before['latest_pacing_transition_replayed']
    original=pacing.PaperReadPacing.operation_history
    def changed(self):return tuple(dict(row,operation='BTC_QUOTE_OBSERVATION') for row in original(self))
    monkeypatch.setattr(pacing.PaperReadPacing,'operation_history',changed)
    after=paper_activity_acceptance_probe.status(**common)
    assert after['latest_pacing_transition_replayed'] is False
    assert after['latest_acceptance_record_replayed'] is False
