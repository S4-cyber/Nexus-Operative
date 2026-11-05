"""Operator wrapper for one saved-pair Activity SSE acceptance attempt."""
import copy
import io
import json

import pytest

from nexus import paper_activity_sse_acceptance_probe as subject
from nexus import paper_credentials
from nexus.alpaca_activity_sse_cursor_store import ZERO
from nexus.alpaca_activity_sse_review import _event
from nexus.alpaca_paper_contract import canonical


ACCOUNT='01234567-89ab-cdef-0123-456789abcdef'
KEY='fixture_key_only_123'
SECRET='fixture_secret_only_123'


class Vault:
    def __init__(self):self.raw=None
    def set_password(self,service,account,raw):self.raw=raw
    def get_password(self,service,account):return self.raw


class Response:
    status=200
    headers={'Content-Type':'text/event-stream; charset=utf-8'}
    def __init__(self,raw,target):self.body=io.BytesIO(raw);self.target=target
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def geturl(self):return self.target
    def read(self,size):return self.body.read(size)


class Opener:
    def __init__(self,raw):self.raw=raw;self.requests=[]
    def open(self,request,timeout):
        self.requests.append((request,timeout))
        return Response(self.raw,request.full_url)


def segments():
    first_trade=_event('01K00000000000000000000001',
        '11111111-1111-1111-1111-111111111111','TRD')
    first_fee=_event('01K00000000000000000000002',
        '22222222-2222-2222-2222-222222222222','FEE')
    later_trade=_event('01K00000000000000000000003',
        '33333333-3333-3333-3333-333333333333','TRD',
        details={**first_trade['details'],
            'order_id':'cccccccc-cccc-cccc-cccc-cccccccccccc',
            'client_order_id':'fixture_client_2'},
        at='2026-10-18T18:02:00.100000000Z',
        executed_at='2026-10-18T18:02:00.000000000Z')
    later_fee=_event('01K00000000000000000000004',
        '44444444-4444-4444-4444-444444444444','FEE',
        details={'parent_id':later_trade['ref_id'],
            'system_date':'2026-10-18'},
        at='2026-10-18T18:03:00.100000000Z',
        executed_at='2026-10-18T18:03:00.000000000Z')
    initial=[first_trade,first_fee,later_trade]
    replay=[copy.deepcopy(later_trade),later_fee]
    raw=''.join('data: '+canonical(row)+'\n\n' for row in replay).encode()
    return initial,raw


@pytest.fixture
def saved_pair(monkeypatch):
    vault=Vault()
    monkeypatch.setattr(paper_credentials,'_vault',lambda:vault)
    monkeypatch.setattr(paper_credentials,'_epoch',0)
    monkeypatch.setattr(paper_credentials,'_blocked',False)
    paper_credentials.install(KEY,SECRET,confirm_store=True)
    return vault


def paths(tmp_path):
    private=tmp_path/'private';private.mkdir()
    initial,raw=segments();source=private/'initial.json'
    source.write_text(canonical(initial),encoding='utf-8')
    return dict(expected_account_id=ACCOUNT,initial_events_path=source,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3',
        expected_cursor_head=ZERO,expected_budget_head=ZERO,
        reconnect_reason='CONNECTION_DROPPED'),raw


def test_status_is_vault_network_silent_and_missing_paths_stay_missing(
        tmp_path,monkeypatch):
    private=tmp_path/'private'
    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))
    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3',clock=lambda:1000)
    assert result['snapshot_consistent'] and result['one_attempt_eligible']
    assert not result['actual_vault_access']
    assert not result['actual_provider_access'] and not private.exists()


@pytest.mark.parametrize('store_name',('cursor','budget'))
def test_status_denies_eligibility_when_repeated_journal_reads_disagree(
        tmp_path,monkeypatch,store_name):
    private=tmp_path/'private';calls=0
    store = (subject.ActivitySSECursorStore if store_name == 'cursor' else
        subject.ActivitySSEReconnectBudget)
    original=store.inspect

    def changed(self):
        nonlocal calls
        result=original(self);calls+=1
        if calls == 2:
            result=dict(result)
            result['head_sha256']='f'*64
        return result

    monkeypatch.setattr(store,'inspect',changed)
    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3',clock=lambda:1000)
    assert calls==2
    assert not result['snapshot_consistent']
    assert not result['one_attempt_eligible']
    assert not result['actual_vault_access']
    assert not result['actual_provider_access'] and not private.exists()


def test_status_late_emergency_latch_denies_eligibility(tmp_path,monkeypatch):
    private=tmp_path/'private';order=private/'orders.sqlite3';calls=0
    original=subject.emergency_intent.requested

    def cut_between_samples(path):
        nonlocal calls
        calls+=1
        if calls == 2:
            subject.emergency_intent.persist(path)
        return original(path)

    monkeypatch.setattr(subject.emergency_intent,'requested',
        cut_between_samples)
    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',order_journal_path=order,
        clock=lambda:1000)
    assert calls==2
    assert result['emergency_cut_latch_active']
    assert not result['snapshot_consistent']
    assert not result['one_attempt_eligible']
    assert not result['actual_vault_access']
    assert not result['actual_provider_access']


def test_emergency_latch_blocks_before_vault_or_journal_write(
        tmp_path,monkeypatch):
    options,raw=paths(tmp_path)
    from nexus import emergency_intent
    emergency_intent.persist(options['order_journal_path'])
    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))
    with pytest.raises(ValueError,match='PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=Opener(raw),wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert not options['cursor_path'].exists()
    assert not options['budget_path'].exists()


def test_one_saved_pair_attempt_commits_aggregate_checkpoint(
        tmp_path,saved_pair):
    options,raw=paths(tmp_path);opener=Opener(raw)
    result=subject.attempt(**options,confirm_reconnect_attempt=True,
        confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
        opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50,
        outcome_clock=lambda:1000)
    assert len(opener.requests)==1
    assert result['read_completed'] and result['actual_vault_access']
    assert not result['actual_provider_access']
    assert result['local_cursor_checkpoint_committed']
    assert result['complete_review_counts_bound']
    assert result['redelivered_events_deduplicated']==1
    assert result['new_events_processed']==1
    assert not result['automatic_retry'] and not result['live_orders_enabled']
    encoded=json.dumps(result)
    assert KEY not in encoded and SECRET not in encoded
    assert 'fixture_client_2' not in encoded
    replay=subject.status(expected_account_id=ACCOUNT,
        cursor_path=options['cursor_path'],budget_path=options['budget_path'],
        order_journal_path=options['order_journal_path'],clock=lambda:1001)
    assert replay['reconnect_completed'] and not replay['one_attempt_eligible']


@pytest.mark.parametrize(('field','replacement'),(
    ('mode','WRONG'),('status','READY'),('source','PRIVATE_SECRET'),
    ('account_sha256','0'*64),('checkpoint_head_sha256','bad'),
    ('reconnect_budget_head_sha256','bad'),
    ('initial_events_reviewed',True),('replay_events_reviewed',0),
    ('redelivered_events_deduplicated',2001),
    ('new_events_processed','1'),('new_events_processed',2),
    ('reconnect_attempt_number',True),
    ('reconnect_attempt_cap',0),
    ('local_cursor_checkpoint_committed',False),
    ('process_restart_recoverable',False),
    ('complete_review_counts_bound',False),
    ('reconnect_budget_completed',False),('actual_vault_access',False),
    ('automatic_retry',True),('actual_provider_access','false'),
    ('actual_stream_connection','false')))
def test_malformed_saved_pair_acknowledgement_fails_fixed_after_one_commit(
        tmp_path,saved_pair,monkeypatch,field,replacement):
    options,raw=paths(tmp_path);opener=Opener(raw)
    original=paper_credentials.collect_activity_sse_saved

    def malformed(**kwargs):
        result=original(**kwargs)
        result[field]=replacement
        return result

    monkeypatch.setattr(paper_credentials,'collect_activity_sse_saved',
        malformed)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50,
            outcome_clock=lambda:1000)
    assert len(opener.requests)==1
    replay=subject.status(expected_account_id=ACCOUNT,
        cursor_path=options['cursor_path'],budget_path=options['budget_path'],
        order_journal_path=options['order_journal_path'],clock=lambda:1001)
    assert replay['reconnect_completed']
    assert replay['retained_checkpoints']==1
    assert not replay['one_attempt_eligible']


def test_stream_connection_cannot_claim_no_provider_access():
    result=dict(mode='EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_ATTEMPT',
        status='REVIEW_REQUIRED',source='INJECTED_UNATTESTED',
        account_sha256='a'*64,
        checkpoint_head_sha256='b'*64,reconnect_budget_head_sha256='c'*64,
        initial_events_reviewed=1,replay_events_reviewed=1,
        redelivered_events_deduplicated=1,new_events_processed=1,
        reconnect_attempt_number=1,reconnect_attempt_cap=3,
        local_cursor_checkpoint_committed=True,
        process_restart_recoverable=True,complete_review_counts_bound=True,
        reconnect_budget_completed=True,actual_vault_access=True,
        automatic_retry=False,actual_provider_access=False,
        actual_stream_connection=True)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject._attempt_result(result,'a'*64)


def test_post_commit_emergency_cut_withholds_success_without_second_get(
        tmp_path,saved_pair,monkeypatch):
    options,raw=paths(tmp_path);opener=Opener(raw)
    original=paper_credentials.collect_activity_sse_saved

    def cut_after_commit(**kwargs):
        result=original(**kwargs)
        from nexus import emergency_intent
        emergency_intent.persist(options['order_journal_path'])
        return result

    monkeypatch.setattr(paper_credentials,'collect_activity_sse_saved',
        cut_after_commit)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50,
            outcome_clock=lambda:1000)
    assert len(opener.requests)==1
    replay=subject.status(expected_account_id=ACCOUNT,
        cursor_path=options['cursor_path'],budget_path=options['budget_path'],
        order_journal_path=options['order_journal_path'],clock=lambda:1001)
    assert replay['emergency_cut_latch_active']
    assert replay['reconnect_completed']
    assert replay['retained_checkpoints']==1
    assert not replay['one_attempt_eligible']


def test_post_commit_private_input_change_withholds_success_without_second_get(
        tmp_path,saved_pair,monkeypatch):
    options,raw=paths(tmp_path);opener=Opener(raw)
    original=paper_credentials.collect_activity_sse_saved

    def change_input_after_commit(**kwargs):
        result=original(**kwargs)
        options['initial_events_path'].write_bytes(b'changed')
        return result

    monkeypatch.setattr(paper_credentials,'collect_activity_sse_saved',
        change_input_after_commit)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50,
            outcome_clock=lambda:1000)
    assert len(opener.requests)==1
    replay=subject.status(expected_account_id=ACCOUNT,
        cursor_path=options['cursor_path'],budget_path=options['budget_path'],
        order_journal_path=options['order_journal_path'],clock=lambda:1001)
    assert replay['reconnect_completed']
    assert replay['retained_checkpoints']==1
    assert not replay['one_attempt_eligible']


@pytest.mark.parametrize('missing',('confirm_reconnect_attempt',
    'confirm_read_only_paper','confirm_cursor_checkpoint'))
def test_every_confirmation_is_required_before_vault(tmp_path,monkeypatch,missing):
    options,raw=paths(tmp_path)
    confirmations=dict(confirm_reconnect_attempt=True,
        confirm_read_only_paper=True,confirm_cursor_checkpoint=True)
    confirmations[missing]=False
    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))
    with pytest.raises(ValueError,match='PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED'):
        subject.attempt(**options,**confirmations,opener=Opener(raw),
            wall_clock=lambda:1000,elapsed_clock=lambda:50,
            outcome_clock=lambda:1000)
