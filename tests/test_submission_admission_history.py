"""Retained local admission samples are not send/arrival timestamps."""
import json
import sqlite3
import subprocess
import sys

import pytest
from nexus import broker_request_journal as module
from nexus.alpaca_paper_contract import canonical,sha
from test_broker_request_journal import ACCOUNT
from test_alpaca_paper_contract import plan


@pytest.fixture
def journal(tmp_path):
    j=module.BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    j.prepare(plan());return j


def begin(j,identity='nx_fixture',at=1000):
    current=j.lookup_request(identity)
    return j.begin_submission_checked(identity,expected_head=current['head_sha256'],
        expected_payload_sha256=current['plan']['payload_sha256'],admission=at if callable(at) else lambda:at)


def rewrite(path,change):
    """Rehash the whole chain: semantic guards must also reject consistent hashes."""
    with sqlite3.connect(path) as db:
        rows=db.execute('SELECT seq,kind,body FROM events ORDER BY seq').fetchall();previous=module.ZERO
        for seq,kind,encoded in rows:
            body=json.loads(encoded)
            if kind=='SUBMISSION_UNCERTAIN':change(body)
            digest=sha(dict(seq=seq,kind=kind,body=body,previous=previous))
            db.execute('UPDATE events SET body=?,previous=?,digest=? WHERE seq=?',(canonical(body),previous,digest,seq))
            previous=digest


def test_sample_is_under_write_lock_and_first_sample_is_retained(journal,monkeypatch):
    # A second writer cannot enter during admission. The later precommit check
    # must not overwrite the sample that admitted this marker.
    head=journal.recovery()['head_sha256'];samples=iter([1000.125,1000.5]);calls=[]
    def admission():
        with sqlite3.connect(journal.path,timeout=0) as other:
            with pytest.raises(sqlite3.OperationalError,match='locked'):other.execute('BEGIN IMMEDIATE')
        calls.append(1);return next(samples)
    result=begin(journal,at=admission);marker=result['submission_admission']
    assert len(calls)==2 and marker==dict(profile=module.ADMISSION_PROFILE,clock_scope=module.ADMISSION_SCOPE,
        evaluated_at=1000.125,expected_head_sha256=head,payload_sha256=plan()['payload_sha256'])
    assert result['attempt_marker_commit_observed'] and result['transport_invoked'] is result['routing_authorized'] is False
    marker['evaluated_at']=0
    assert journal.recovery()['requests'][0]['submission_admission']['evaluated_at']==1000.125


def test_legacy_marker_remains_untimed_and_duplicate_does_not_upgrade_it(journal):
    journal.mark_submission_uncertain('nx_fixture');before=journal.path.read_bytes()
    assert journal.recovery()['requests'][0]['submission_admission'] is None
    assert journal.mark_submission_uncertain('nx_fixture')['submission_admission'] is None
    with pytest.raises(ValueError,match='ALREADY_MARKED'):begin(journal)
    assert journal.path.read_bytes()==before


def test_mixed_legacy_and_timed_markers_survive_later_prefixes_and_restart(journal):
    journal.mark_submission_uncertain('nx_fixture');journal.prepare(plan(client_order_id='second'))
    marker=begin(journal,'second',1000.125)['submission_admission'];journal.prepare(plan(client_order_id='third'))
    before=journal.path.read_bytes();cold=module.BrokerRequestJournal(journal.path,ACCOUNT)
    requests=cold.recovery()['requests']
    assert [r['submission_admission'] for r in requests]==[None,marker,None]
    assert cold.mark_submission_uncertain('second')['submission_admission']==marker
    assert journal.path.read_bytes()==before


@pytest.mark.parametrize('operation',['marker','lookup','review','selection'])
def test_retained_admission_is_cross_request_clock_floor_after_restart(journal,operation):
    begin(journal,at=1000.125);journal.prepare(plan(client_order_id='second'))
    cold=module.BrokerRequestJournal(journal.path,ACCOUNT);before=journal.path.read_bytes()
    if operation=='marker':
        with pytest.raises(ValueError,match='CLOCK_REGRESSION'):begin(cold,'second',1000)
    elif operation=='lookup':
        with pytest.raises(ValueError,match='CLOCK_REGRESSION'):cold.begin_lookup('nx_fixture',evaluated_at=1000)
    elif operation=='review':assert 'CLOCK_REGRESSION' in cold.submission_review('second',evaluated_at=1000)['reasons']
    else:assert 'LOOKUP_CLOCK_REGRESSION' in cold.lookup_review(evaluated_at=1000)['reasons']
    assert journal.path.read_bytes()==before


@pytest.mark.parametrize('bad',[
    {'profile':'UNKNOWN'},{'clock_scope':'VERIFIED_BROKER_ARRIVAL'},
    {'expected_head_sha256':'0'*64},{'payload_sha256':'0'*64},
    {'evaluated_at':True},{'evaluated_at':-1},{'evaluated_at':'1000'},
    {'evaluated_at':None},{'evaluated_at':1e13},{'extra':'not allowed'},
],ids=['profile','scope','parent','terms','bool-time','negative-time','string-time','null-time','large-time','extra'])
def test_semantically_invalid_rehashed_admission_refuses(journal,bad):
    begin(journal);rewrite(journal.path,lambda body:body['admission'].update(bad))
    before=journal.path.read_bytes()
    with pytest.raises(ValueError):module.BrokerRequestJournal(journal.path,ACCOUNT).recovery()
    assert journal.path.read_bytes()==before


@pytest.mark.parametrize('bad',[None,[],{},'invalid'])
def test_invalid_marker_shapes_refuse_without_repair(journal,bad):
    begin(journal);rewrite(journal.path,lambda body:body.update(admission=bad));before=journal.path.read_bytes()
    with pytest.raises(ValueError):journal.recovery()
    assert journal.path.read_bytes()==before


def test_rehashed_later_marker_cannot_regress_behind_another_marker(journal):
    begin(journal);journal.prepare(plan(client_order_id='second'));begin(journal,'second',1001)
    def change(body):
        if body['client_order_id']=='second':body['admission']['evaluated_at']=999
    rewrite(journal.path,change);before=journal.path.read_bytes()
    with pytest.raises(ValueError,match='CLOCK_REGRESSION'):journal.recovery()
    assert journal.path.read_bytes()==before


def test_post_insert_revocation_keeps_original_history_untimed_and_unmarked(journal,monkeypatch):
    inserted=False;original=journal._insert;before=journal.path.read_bytes()
    def insert(*args):
        nonlocal inserted
        original(*args);inserted=True
    monkeypatch.setattr(journal,'_insert',insert)
    with pytest.raises(ValueError):begin(journal,at=lambda:False if inserted else 1000)
    assert inserted and journal.path.read_bytes()==before
    result=journal.recovery()['requests'][0]
    assert result['submission_admission'] is None and not result['submission_attempt_marked']


def test_committed_marker_with_lost_return_replays_offline_in_fresh_process(journal,monkeypatch):
    # Simulate loss after commit acknowledgment but before a caller receives it.
    summary=journal._summary
    def lost(*args):summary(*args);raise OSError('lost acknowledgment')
    monkeypatch.setattr(journal,'_summary',lost)
    with pytest.raises(OSError):begin(journal,at=1000.125)
    before=journal.path.read_bytes()
    script='''
import json,socket,sys
from nexus.broker_request_journal import BrokerRequestJournal
def denied(*args,**kwargs):raise AssertionError('network forbidden')
socket.socket=socket.create_connection=denied
r=BrokerRequestJournal(sys.argv[1],sys.argv[2]).recovery()['requests'][0]
assert r['submission_admission']['evaluated_at']==1000.125
assert r['lookup_by_original_client_id_required'] and not r['sendable']
print(json.dumps(r['submission_admission']))
'''
    p=subprocess.run([sys.executable,'-c',script,str(journal.path),ACCOUNT],capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)['clock_scope']==module.ADMISSION_SCOPE and journal.path.read_bytes()==before


def test_release_fixture_requires_retained_marker_checks():
    result=module.submission_admission_fixture()
    for key in ('admission_marker_replayed','admission_clock_floor_retained','legacy_marker_untimed'):assert result[key] is True


def test_historical_prefix_retains_absent_and_timed_marker_separately(journal):
    prepared=journal.recovery()['head_sha256'];marker=begin(journal)['submission_admission']
    marked=journal.recovery()['head_sha256'];journal.prepare(plan(client_order_id='later'))
    before=journal.path.read_bytes();prefix=journal.observations_at_heads([prepared,marked])
    assert prefix['observations_by_head'][prepared][0]['submission_admission'] is None
    assert prefix['observations_by_head'][marked][0]['submission_admission']==marker
    assert journal.path.read_bytes()==before


def test_encrypted_quote_copy_replays_new_request_marker_and_old_quote_prefix(tmp_path):
    from nexus import paper_quote_bundle as bundle
    options=bundle._fixture(tmp_path/'private')
    j=module.BrokerRequestJournal(options['request_journal_path'],options['expected_account_id'])
    j.prepare(plan(client_order_id='nx_admission_copy'))
    marker=begin(j,'nx_admission_copy',1792195202.125)['submission_admission']
    options['expected_request_head']=j.recovery()['head_sha256']
    before={p:p.read_bytes() for p in (tmp_path/'private').iterdir()}
    raw=bundle.snapshot(**options);value=bundle.loads(raw)
    retained=[json.loads(r[2]) for r in value['request_events'] if r[1]=='SUBMISSION_UNCERTAIN']
    assert retained==[dict(client_order_id='nx_quote_fixture'),dict(client_order_id='nx_admission_copy',admission=marker)]
    phrase='temporary fixture words only'
    assert bundle.unseal(bundle.seal(raw,phrase),phrase)==raw
    assert before=={p:p.read_bytes() for p in (tmp_path/'private').iterdir()}


@pytest.mark.parametrize('field',['admission_marker_replayed','admission_clock_floor_retained','legacy_marker_untimed'])
def test_release_guard_cannot_accept_missing_admission_history_checks(field):
    from pathlib import Path
    from nexus import release_trials,release_roadmap
    result=json.loads((Path(__file__).resolve().parents[1]/'validation'/
        ('release_trials_'+release_roadmap.REVISION.replace('.','_')+'.json')).read_text())['profiles']['pacing']
    result['submission_admission'][field]=False
    with pytest.raises(ValueError,match='SUBMISSION_ADMISSION_HISTORY'):release_trials._validate('pacing',result)
