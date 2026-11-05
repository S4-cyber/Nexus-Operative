"""Operator wrapper for one saved-pair Activity SSE acceptance attempt."""
import copy
import io
import json
import os

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
    private=tmp_path/'private';clock_calls=0

    def clock():
        nonlocal clock_calls
        clock_calls+=1
        return 1000

    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))
    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3',clock=clock)
    assert clock_calls==2
    assert result['status_evaluated_at']==1000
    assert result['status_clock_consistent']
    assert result['storage_snapshot_consistent']
    assert result['snapshot_consistent'] and result['one_attempt_eligible']
    assert not result['actual_vault_access']
    assert not result['actual_provider_access'] and not private.exists()


@pytest.mark.parametrize('observed',(None,True,'1000',float('nan'),
    float('inf'),-1,1e12+1))
def test_status_invalid_clock_is_explicitly_ineligible_without_raw_error(
        tmp_path,observed):
    private=tmp_path/'private'
    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3',
        clock=lambda:observed)
    assert result['status_evaluated_at'] is None
    assert not result['status_clock_consistent']
    assert not result['snapshot_consistent']
    assert not result['one_attempt_eligible']
    assert not result['actual_vault_access']
    assert not result['actual_provider_access'] and not private.exists()


def test_status_clock_failure_is_explicitly_ineligible_without_raw_error(
        tmp_path):
    private=tmp_path/'private'

    def unavailable():
        raise RuntimeError('PRIVATE_CLOCK_DETAIL')

    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3',clock=unavailable)
    assert result['status_evaluated_at'] is None
    assert not result['status_clock_consistent']
    assert not result['snapshot_consistent']
    assert not result['one_attempt_eligible']


def test_status_regressing_clock_is_explicitly_ineligible(tmp_path):
    private=tmp_path/'private';samples=iter((1000,999))
    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3',
        clock=lambda:next(samples))
    assert result['status_evaluated_at']==999
    assert not result['status_clock_consistent']
    assert not result['snapshot_consistent']
    assert not result['one_attempt_eligible']


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


@pytest.mark.parametrize('store_name',('cursor','budget'))
def test_status_denies_eligibility_after_byte_identical_path_replacement(
        tmp_path,saved_pair,monkeypatch,store_name):
    options,raw=paths(tmp_path);opener=Opener(raw)
    subject.attempt(**options,confirm_reconnect_attempt=True,
        confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
        opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50,
        outcome_clock=lambda:1000)
    store = (subject.ActivitySSECursorStore if store_name == 'cursor' else
        subject.ActivitySSEReconnectBudget)
    original=store.inspect;calls=0

    def replace_before_second_read(self):
        nonlocal calls
        calls+=1
        if calls == 2:
            replacement=self.path.with_name(self.path.name+'.replacement')
            replacement.write_bytes(self.path.read_bytes())
            replacement.replace(self.path)
        return original(self)

    monkeypatch.setattr(store,'inspect',replace_before_second_read)
    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=options['cursor_path'],budget_path=options['budget_path'],
        order_journal_path=options['order_journal_path'],clock=lambda:1001)
    assert calls==2
    assert result['cursor_head_sha256'] != ZERO
    assert result['reconnect_budget_head_sha256'] != ZERO
    assert not result['storage_snapshot_consistent']
    assert not result['snapshot_consistent']
    assert not result['one_attempt_eligible']
    assert not result['actual_vault_access']
    assert not result['actual_provider_access']


@pytest.mark.parametrize('journal_name',('cursor_path','budget_path'))
def test_status_binds_validated_journal_identity_before_first_read(
        tmp_path,saved_pair,journal_name):
    options,raw=paths(tmp_path);opener=Opener(raw)
    subject.attempt(**options,confirm_reconnect_attempt=True,
        confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
        opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50,
        outcome_clock=lambda:1000)
    selected=options[journal_name];calls=0

    def replace_on_first_clock():
        nonlocal calls
        calls+=1
        if calls == 1:
            replacement=selected.with_name(selected.name+'.replacement')
            replacement.write_bytes(selected.read_bytes())
            replacement.replace(selected)
        return 1001

    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=options['cursor_path'],budget_path=options['budget_path'],
        order_journal_path=options['order_journal_path'],
        clock=replace_on_first_clock)
    assert calls==2
    assert not result['journal_scope_consistent']
    assert not result['storage_snapshot_consistent']
    assert not result['snapshot_consistent']
    assert not result['one_attempt_eligible']
    assert not result['actual_vault_access']
    assert not result['actual_provider_access']


def test_status_late_emergency_latch_denies_eligibility(tmp_path,monkeypatch):
    private=tmp_path/'private';order=private/'orders.sqlite3';calls=0
    original=subject._stop_sample

    def cut_between_samples(path):
        nonlocal calls
        calls+=1
        if calls == 2:
            subject.emergency_intent.persist(path)
        return original(path)

    monkeypatch.setattr(subject,'_stop_sample',cut_between_samples)
    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',order_journal_path=order,
        clock=lambda:1000)
    assert calls==2
    assert result['emergency_cut_latch_active']
    assert not result['emergency_cut_scope_consistent']
    assert not result['snapshot_consistent']
    assert not result['one_attempt_eligible']
    assert not result['actual_vault_access']
    assert not result['actual_provider_access']


def test_status_detects_byte_identical_emergency_latch_replacement(
        tmp_path,monkeypatch):
    private=tmp_path/'private';order=private/'orders.sqlite3'
    subject.emergency_intent.persist(order)
    latch=subject.emergency_intent.path(order);original=subject._stop_sample
    calls=0

    def replace_between_samples(path):
        nonlocal calls
        calls+=1
        if calls == 2:
            replacement=latch.with_name(latch.name+'.replacement')
            replacement.write_bytes(latch.read_bytes())
            replacement.replace(latch)
        return original(path)

    monkeypatch.setattr(subject,'_stop_sample',replace_between_samples)
    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',order_journal_path=order,
        clock=lambda:1000)
    assert calls==2
    assert result['emergency_cut_latch_active']
    assert not result['emergency_cut_scope_consistent']
    assert not result['snapshot_consistent']
    assert not result['one_attempt_eligible']
    assert latch.read_bytes()==subject.emergency_intent.RECORD
    assert not result['actual_vault_access']
    assert not result['actual_provider_access']


def test_status_pins_resolved_emergency_latch_across_parent_alias_swap(
        tmp_path):
    first=tmp_path/'first';second=tmp_path/'second'
    first.mkdir();second.mkdir()
    alias=tmp_path/'active'
    try:
        alias.symlink_to(first,target_is_directory=True)
    except OSError:
        pytest.skip('directory symlinks unavailable')
    subject.emergency_intent.persist(first/'orders.sqlite3')
    calls=0

    def swap_alias_on_first_clock():
        nonlocal calls
        calls+=1
        if calls == 1:
            alias.unlink()
            alias.symlink_to(second,target_is_directory=True)
        return 1000

    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=alias/'cursor.sqlite3',
        budget_path=alias/'budget.sqlite3',
        order_journal_path=alias/'orders.sqlite3',
        clock=swap_alias_on_first_clock)
    assert calls==2
    assert result['emergency_cut_latch_active']
    assert result['snapshot_consistent']
    assert not result['one_attempt_eligible']
    assert not result['actual_vault_access']
    assert not result['actual_provider_access']
    assert not (second/'cursor.sqlite3').exists()
    assert not (second/'budget.sqlite3').exists()


def test_status_detects_resolved_journal_directory_replacement(tmp_path):
    private=tmp_path/'private';private.mkdir();retired=tmp_path/'retired'
    calls=0

    def replace_on_first_clock():
        nonlocal calls
        calls+=1
        if calls == 1:
            private.rename(retired)
            private.mkdir()
            subject.emergency_intent.persist(retired/'orders.sqlite3')
        return 1000

    result=subject.status(expected_account_id=ACCOUNT,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3',
        clock=replace_on_first_clock)
    assert calls==2
    assert not result['journal_scope_consistent']
    assert not result['storage_snapshot_consistent']
    assert not result['snapshot_consistent']
    assert not result['one_attempt_eligible']
    assert subject.emergency_intent.requested(retired/'orders.sqlite3')
    assert not result['actual_vault_access']
    assert not result['actual_provider_access']
    assert not (private/'cursor.sqlite3').exists()
    assert not (private/'budget.sqlite3').exists()


@pytest.mark.parametrize('journal_name',
    ('cursor_path','budget_path','order_journal_path'))
def test_status_rejects_journal_leaf_symlink_before_storage_access(
        tmp_path,monkeypatch,journal_name):
    private=tmp_path/'private';private.mkdir()
    target=private/(journal_name+'.target')
    target.write_bytes(b'PRIVATE_UNRELATED_JOURNAL')
    alias=private/(journal_name+'.sqlite3')
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip('file symlinks unavailable')
    options=dict(expected_account_id=ACCOUNT,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3')
    options[journal_name]=alias
    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.status(**options,clock=lambda:1000)
    assert target.read_bytes()==b'PRIVATE_UNRELATED_JOURNAL'
    assert not (private/'cursor.sqlite3').exists()
    assert not (private/'budget.sqlite3').exists()
    assert not (private/'orders.sqlite3').exists()


@pytest.mark.parametrize('journal_name',
    ('cursor_path','budget_path','order_journal_path'))
@pytest.mark.parametrize('leaf_kind',('directory','hardlink'))
def test_status_rejects_unsafe_existing_journal_leaf_before_storage_access(
        tmp_path,monkeypatch,journal_name,leaf_kind):
    private=tmp_path/'private';private.mkdir()
    options=dict(expected_account_id=ACCOUNT,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3')
    selected=options[journal_name];target=None
    if leaf_kind == 'directory':
        selected.mkdir()
    else:
        target=private/(journal_name+'.target')
        target.write_bytes(b'PRIVATE_UNRELATED_JOURNAL')
        try:
            os.link(target,selected)
        except OSError:
            pytest.skip('hard links unavailable')
    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.status(**options,clock=lambda:1000)
    if leaf_kind == 'directory':
        assert selected.is_dir() and not tuple(selected.iterdir())
    else:
        assert target.read_bytes()==b'PRIVATE_UNRELATED_JOURNAL'
        assert selected.read_bytes()==b'PRIVATE_UNRELATED_JOURNAL'
    for name in ('cursor_path','budget_path','order_journal_path'):
        if name != journal_name:
            assert not options[name].exists()


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


def test_attempt_pins_resolved_journals_before_parent_alias_swap(
        tmp_path,saved_pair,monkeypatch):
    first=tmp_path/'first';second=tmp_path/'second';retained=tmp_path/'retained'
    first.mkdir();second.mkdir();retained.mkdir()
    alias=tmp_path/'active'
    try:
        alias.symlink_to(first,target_is_directory=True)
    except OSError:
        pytest.skip('directory symlinks unavailable')
    initial,raw=segments();source=retained/'initial.json'
    source.write_text(canonical(initial),encoding='utf-8')
    options=dict(expected_account_id=ACCOUNT,initial_events_path=source,
        cursor_path=alias/'cursor.sqlite3',
        budget_path=alias/'budget.sqlite3',
        order_journal_path=alias/'orders.sqlite3',
        expected_cursor_head=ZERO,expected_budget_head=ZERO,
        reconnect_reason='CONNECTION_DROPPED')
    opener=Opener(raw);vault_reads=0
    original_read=saved_pair.get_password
    original_preflight=subject._attempt_head_preflight

    def count_vault_reads(*args):
        nonlocal vault_reads
        vault_reads+=1
        return original_read(*args)

    def swap_after_preflight(*args):
        original_preflight(*args)
        alias.unlink()
        alias.symlink_to(second,target_is_directory=True)
        subject.emergency_intent.persist(first/'orders.sqlite3')

    monkeypatch.setattr(saved_pair,'get_password',count_vault_reads)
    monkeypatch.setattr(subject,'_attempt_head_preflight',
        swap_after_preflight)
    with pytest.raises(ValueError,match='^PAPER_VAULT_READ_REVOKED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert vault_reads==0 and not opener.requests
    assert not (first/'cursor.sqlite3').exists()
    assert not (first/'budget.sqlite3').exists()
    assert not (second/'cursor.sqlite3').exists()
    assert not (second/'budget.sqlite3').exists()


def test_attempt_rejects_resolved_journal_directory_replacement_before_vault(
        tmp_path,saved_pair,monkeypatch):
    private=tmp_path/'private';private.mkdir();retired=tmp_path/'retired'
    retained=tmp_path/'retained';retained.mkdir()
    initial,raw=segments();source=retained/'initial.json'
    source.write_text(canonical(initial),encoding='utf-8')
    options=dict(expected_account_id=ACCOUNT,initial_events_path=source,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3',
        expected_cursor_head=ZERO,expected_budget_head=ZERO,
        reconnect_reason='CONNECTION_DROPPED')
    original=subject._attempt_head_preflight;vault_reads=0
    original_read=saved_pair.get_password

    def count_vault_reads(*args):
        nonlocal vault_reads
        vault_reads+=1
        return original_read(*args)

    def replace_after_preflight(*args):
        original(*args)
        private.rename(retired)
        private.mkdir()
        subject.emergency_intent.persist(retired/'orders.sqlite3')

    monkeypatch.setattr(saved_pair,'get_password',count_vault_reads)
    monkeypatch.setattr(subject,'_attempt_head_preflight',
        replace_after_preflight)
    opener=Opener(raw)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert vault_reads==0 and not opener.requests
    assert subject.emergency_intent.requested(retired/'orders.sqlite3')
    assert not (private/'cursor.sqlite3').exists()
    assert not (private/'budget.sqlite3').exists()


@pytest.mark.parametrize('journal_name',
    ('cursor_path','budget_path','order_journal_path'))
def test_attempt_rejects_journal_leaf_symlink_before_vault_or_storage_access(
        tmp_path,saved_pair,monkeypatch,journal_name):
    options,raw=paths(tmp_path);private=options['cursor_path'].parent
    target=private/(journal_name+'.target')
    target.write_bytes(b'PRIVATE_UNRELATED_JOURNAL')
    alias=private/(journal_name+'.alias')
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip('file symlinks unavailable')
    options[journal_name]=alias
    vault_reads=0;original_read=saved_pair.get_password

    def count_vault_reads(*args):
        nonlocal vault_reads
        vault_reads+=1
        return original_read(*args)

    monkeypatch.setattr(saved_pair,'get_password',count_vault_reads)
    opener=Opener(raw)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert vault_reads==0 and not opener.requests
    assert target.read_bytes()==b'PRIVATE_UNRELATED_JOURNAL'
    assert not (private/'cursor.sqlite3').exists()
    assert not (private/'budget.sqlite3').exists()
    assert not (private/'orders.sqlite3').exists()


@pytest.mark.parametrize('journal_name',
    ('cursor_path','budget_path','order_journal_path'))
@pytest.mark.parametrize('leaf_kind',('directory','hardlink'))
def test_attempt_rejects_unsafe_existing_journal_leaf_before_vault_or_storage(
        tmp_path,saved_pair,monkeypatch,journal_name,leaf_kind):
    options,raw=paths(tmp_path);private=options['cursor_path'].parent
    selected=options[journal_name];target=None
    if leaf_kind == 'directory':
        selected.mkdir()
    else:
        target=private/(journal_name+'.target')
        target.write_bytes(b'PRIVATE_UNRELATED_JOURNAL')
        try:
            os.link(target,selected)
        except OSError:
            pytest.skip('hard links unavailable')
    vault_reads=0;original_read=saved_pair.get_password

    def count_vault_reads(*args):
        nonlocal vault_reads
        vault_reads+=1
        return original_read(*args)

    monkeypatch.setattr(saved_pair,'get_password',count_vault_reads)
    opener=Opener(raw)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert vault_reads==0 and not opener.requests
    if leaf_kind == 'directory':
        assert selected.is_dir() and not tuple(selected.iterdir())
    else:
        assert target.read_bytes()==b'PRIVATE_UNRELATED_JOURNAL'
        assert selected.read_bytes()==b'PRIVATE_UNRELATED_JOURNAL'
    for name in ('cursor_path','budget_path','order_journal_path'):
        if name != journal_name:
            assert not options[name].exists()


@pytest.mark.parametrize('journal_name',
    ('cursor_path','budget_path','order_journal_path'))
def test_attempt_rejects_missing_journal_substitution_after_head_preflight(
        tmp_path,saved_pair,monkeypatch,journal_name):
    options,raw=paths(tmp_path);selected=options[journal_name]
    original=subject._attempt_head_preflight
    vault_reads=0;original_read=saved_pair.get_password

    def count_vault_reads(*args):
        nonlocal vault_reads
        vault_reads+=1
        return original_read(*args)

    def substitute_after_preflight(*args):
        original(*args)
        selected.write_bytes(b'PRIVATE_UNRELATED_JOURNAL')

    monkeypatch.setattr(saved_pair,'get_password',count_vault_reads)
    monkeypatch.setattr(subject,'_attempt_head_preflight',
        substitute_after_preflight)
    opener=Opener(raw)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert vault_reads==0 and not opener.requests
    assert selected.read_bytes()==b'PRIVATE_UNRELATED_JOURNAL'
    for name in ('cursor_path','budget_path','order_journal_path'):
        if name != journal_name:
            assert not options[name].exists()


def test_attempt_rejects_existing_order_journal_identity_replacement(
        tmp_path,saved_pair,monkeypatch):
    options,raw=paths(tmp_path);selected=options['order_journal_path']
    selected.write_bytes(b'PRIVATE_UNRELATED_JOURNAL')
    original=subject._attempt_head_preflight
    vault_reads=0;original_read=saved_pair.get_password

    def count_vault_reads(*args):
        nonlocal vault_reads
        vault_reads+=1
        return original_read(*args)

    def replace_after_preflight(*args):
        original(*args)
        replacement=selected.with_name(selected.name+'.replacement')
        replacement.write_bytes(selected.read_bytes())
        replacement.replace(selected)

    monkeypatch.setattr(saved_pair,'get_password',count_vault_reads)
    monkeypatch.setattr(subject,'_attempt_head_preflight',
        replace_after_preflight)
    opener=Opener(raw)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert vault_reads==0 and not opener.requests
    assert selected.read_bytes()==b'PRIVATE_UNRELATED_JOURNAL'
    assert not options['cursor_path'].exists()
    assert not options['budget_path'].exists()


@pytest.mark.parametrize('journal_name',
    ('cursor_path','budget_path','order_journal_path'))
def test_attempt_binds_created_journals_before_final_acknowledgement(
        tmp_path,saved_pair,monkeypatch,journal_name):
    options,raw=paths(tmp_path);selected=options[journal_name]
    original=subject._final_journal_scope;substituted={}

    def replace_after_final_scope(paths,identities,parent_identities):
        scope=original(paths,identities,parent_identities)
        if selected.exists():
            payload=selected.read_bytes()
            replacement=selected.with_name(selected.name+'.replacement')
            replacement.write_bytes(payload)
            replacement.replace(selected)
        else:
            payload=b'PRIVATE_LATE_EMERGENCY_JOURNAL'
            selected.write_bytes(payload)
        substituted['bytes']=payload
        return scope

    monkeypatch.setattr(subject,'_final_journal_scope',
        replace_after_final_scope)
    opener=Opener(raw)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert len(opener.requests)==1
    assert selected.read_bytes()==substituted['bytes']
    assert options['cursor_path'].exists()
    assert options['budget_path'].exists()


@pytest.mark.parametrize('journal_name',('cursor_path','budget_path'))
def test_attempt_binds_created_journal_content_before_final_acknowledgement(
        tmp_path,saved_pair,monkeypatch,journal_name):
    options,raw=paths(tmp_path);selected=options[journal_name]
    original=subject._final_storage_scope;mutation={}

    def mutate_after_final_storage_scope(cursor,budget):
        scope=original(cursor,budget)
        before=selected.stat()
        with selected.open('ab') as stream:
            stream.write(b'PRIVATE_SAME_IDENTITY_MUTATION')
        after=selected.stat()
        assert (before.st_dev,before.st_ino)==(after.st_dev,after.st_ino)
        mutation['size']=after.st_size
        return scope

    monkeypatch.setattr(subject,'_final_storage_scope',
        mutate_after_final_storage_scope)
    opener=Opener(raw)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert len(opener.requests)==1
    assert selected.stat().st_size==mutation['size']
    assert selected.read_bytes().endswith(b'PRIVATE_SAME_IDENTITY_MUTATION')
    assert options['cursor_path'].exists()
    assert options['budget_path'].exists()


def test_attempt_pins_resolved_private_input_across_parent_alias_swap(
        tmp_path,saved_pair,monkeypatch):
    first=tmp_path/'first';second=tmp_path/'second';journals=tmp_path/'journals'
    first.mkdir();second.mkdir();journals.mkdir()
    alias=tmp_path/'input'
    try:
        alias.symlink_to(first,target_is_directory=True)
    except OSError:
        pytest.skip('directory symlinks unavailable')
    initial,raw=segments()
    (first/'initial.json').write_text(canonical(initial),encoding='utf-8')
    (second/'initial.json').write_text('[]',encoding='utf-8')
    options=dict(expected_account_id=ACCOUNT,
        initial_events_path=alias/'initial.json',
        cursor_path=journals/'cursor.sqlite3',
        budget_path=journals/'budget.sqlite3',
        order_journal_path=journals/'orders.sqlite3',
        expected_cursor_head=ZERO,expected_budget_head=ZERO,
        reconnect_reason='CONNECTION_DROPPED')
    original=subject.check_path_set;calls=0

    def swap_after_combined_scope(paths,prefix):
        nonlocal calls
        original(paths,prefix);calls+=1
        if calls == 2:
            alias.unlink()
            alias.symlink_to(second,target_is_directory=True)

    monkeypatch.setattr(subject,'check_path_set',swap_after_combined_scope)
    opener=Opener(raw)
    result=subject.attempt(**options,confirm_reconnect_attempt=True,
        confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
        opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50,
        outcome_clock=lambda:1000)
    assert calls >= 2 and len(opener.requests)==1
    assert result['initial_events_sha256']==subject.sha(initial)
    assert result['local_cursor_checkpoint_committed']


def test_attempt_refuses_private_input_leaf_replacement_after_scope(
        tmp_path,saved_pair,monkeypatch):
    private=tmp_path/'private';private.mkdir()
    initial,raw=segments();source=private/'initial.json'
    source.write_text(canonical(initial),encoding='utf-8')
    options=dict(expected_account_id=ACCOUNT,initial_events_path=source,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3',
        expected_cursor_head=ZERO,expected_budget_head=ZERO,
        reconnect_reason='CONNECTION_DROPPED')
    original=subject.check_path_set;calls=0;vault_reads=0
    original_read=saved_pair.get_password

    def replace_after_combined_scope(paths,prefix):
        nonlocal calls
        original(paths,prefix);calls+=1
        if calls == 2:
            replacement=source.with_name('replacement.json')
            replacement.write_bytes(source.read_bytes())
            replacement.replace(source)

    def count_vault_reads(*args):
        nonlocal vault_reads
        vault_reads+=1
        return original_read(*args)

    monkeypatch.setattr(subject,'check_path_set',replace_after_combined_scope)
    monkeypatch.setattr(saved_pair,'get_password',count_vault_reads)
    opener=Opener(raw)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert calls==2 and vault_reads==0 and not opener.requests
    assert not (private/'cursor.sqlite3').exists()
    assert not (private/'budget.sqlite3').exists()


def test_attempt_keeps_rejecting_private_input_leaf_symlink(
        tmp_path,saved_pair):
    private=tmp_path/'private';private.mkdir()
    initial,raw=segments();target=private/'target.json'
    target.write_text(canonical(initial),encoding='utf-8')
    source=private/'initial.json'
    try:
        source.symlink_to(target)
    except OSError:
        pytest.skip('file symlinks unavailable')
    options=dict(expected_account_id=ACCOUNT,initial_events_path=source,
        cursor_path=private/'cursor.sqlite3',
        budget_path=private/'budget.sqlite3',
        order_journal_path=private/'orders.sqlite3',
        expected_cursor_head=ZERO,expected_budget_head=ZERO,
        reconnect_reason='CONNECTION_DROPPED')
    opener=Opener(raw)
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert not opener.requests
    assert not (private/'cursor.sqlite3').exists()
    assert not (private/'budget.sqlite3').exists()


@pytest.mark.parametrize('observed',(None,True,'1000',float('nan'),
    float('inf'),-1,1e12+1))
def test_attempt_invalid_admission_clock_fails_fixed_before_vault_network_or_write(
        tmp_path,monkeypatch,observed):
    options,raw=paths(tmp_path);opener=Opener(raw)
    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:observed,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert not opener.requests
    assert not options['cursor_path'].exists()
    assert not options['budget_path'].exists()


def test_attempt_admission_clock_failure_hides_private_detail_before_io(
        tmp_path,monkeypatch):
    options,raw=paths(tmp_path);opener=Opener(raw)
    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))

    def unavailable():
        raise RuntimeError('PRIVATE_CLOCK_DETAIL')

    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=unavailable,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert not opener.requests
    assert not options['cursor_path'].exists()
    assert not options['budget_path'].exists()


@pytest.mark.parametrize('signal',(KeyboardInterrupt,SystemExit))
def test_attempt_admission_clock_preserves_process_control_before_io(
        tmp_path,monkeypatch,signal):
    options,raw=paths(tmp_path);opener=Opener(raw)
    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))

    def interrupt():
        raise signal()

    with pytest.raises(signal):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=interrupt,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert not opener.requests
    assert not options['cursor_path'].exists()
    assert not options['budget_path'].exists()


@pytest.mark.parametrize('head_name',('expected_cursor_head',
    'expected_budget_head'))
@pytest.mark.parametrize('observed',(None,True,'bad','g'*64))
def test_attempt_invalid_expected_head_fails_before_vault_network_or_write(
        tmp_path,monkeypatch,head_name,observed):
    options,raw=paths(tmp_path);opener=Opener(raw)
    options[head_name]=observed
    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert not opener.requests
    assert not options['cursor_path'].exists()
    assert not options['budget_path'].exists()


@pytest.mark.parametrize('head_name',('expected_cursor_head',
    'expected_budget_head'))
def test_attempt_stale_expected_head_fails_before_vault_network_or_write(
        tmp_path,monkeypatch,head_name):
    options,raw=paths(tmp_path);opener=Opener(raw)
    options[head_name]='f'*64
    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert not opener.requests
    assert not options['cursor_path'].exists()
    assert not options['budget_path'].exists()


@pytest.mark.parametrize('store_name',('cursor','budget'))
def test_attempt_incoherent_head_preflight_fails_before_vault(
        tmp_path,monkeypatch,store_name):
    options,raw=paths(tmp_path);opener=Opener(raw);calls=0
    store = (subject.ActivitySSECursorStore if store_name == 'cursor' else
        subject.ActivitySSEReconnectBudget)
    original=store.inspect

    def changed(self):
        nonlocal calls
        result=original(self);calls+=1
        if calls == 2:
            result=dict(result);result['head_sha256']='f'*64
        return result

    monkeypatch.setattr(store,'inspect',changed)
    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))
    with pytest.raises(ValueError,
            match='^PAPER_ACTIVITY_SSE_ACCEPTANCE_PROBE_FAILED$'):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert calls==2 and not opener.requests
    assert not options['cursor_path'].exists()
    assert not options['budget_path'].exists()


@pytest.mark.parametrize('store_name',('cursor','budget'))
@pytest.mark.parametrize('signal',(KeyboardInterrupt,SystemExit))
def test_attempt_head_preflight_preserves_process_control_before_vault(
        tmp_path,monkeypatch,store_name,signal):
    options,raw=paths(tmp_path);opener=Opener(raw)
    store = (subject.ActivitySSECursorStore if store_name == 'cursor' else
        subject.ActivitySSEReconnectBudget)

    def interrupted(self):
        raise signal()

    monkeypatch.setattr(store,'inspect',interrupted)
    monkeypatch.setattr(paper_credentials,'_vault',
        lambda:pytest.fail('vault accessed'))
    with pytest.raises(signal):
        subject.attempt(**options,confirm_reconnect_attempt=True,
            confirm_read_only_paper=True,confirm_cursor_checkpoint=True,
            opener=opener,wall_clock=lambda:1000,
            elapsed_clock=lambda:50,outcome_clock=lambda:1000)
    assert not opener.requests
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
