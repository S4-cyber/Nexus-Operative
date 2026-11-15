"""Explicit post-commit durability boundaries for provider budgets."""
import os
from pathlib import Path
import stat

import pytest

from nexus import paper_provider_post_budget as post_module
from nexus import paper_provider_read_budget as read_module
from nexus.paper_provider_post_budget import PaperProviderPostBudget
from nexus.paper_provider_read_budget import PaperProviderReadBudget

ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
CASES = (
    ('post', post_module, PaperProviderPostBudget,
        'PAPER_PROVIDER_POST_BUDGET_'),
    ('read', read_module, PaperProviderReadBudget,
        'PAPER_PROVIDER_READ_BUDGET_'),
)


def _begin(kind, budget, owner):
    if kind == 'post':
        return budget.begin(account_id=ACCOUNT,
            request_before_head_sha256='a' * 64,
            payload_sha256='b' * 64,
            client_order_id='nx_commit_sync', admission=lambda: 1000,
            ownership=owner, expected_head='0' * 64)
    return budget.begin(account_id=ACCOUNT,
        account_before_head_sha256='0' * 64,
        operation='BTC_ASSET_RULES', admission=lambda: 1000,
        ownership=owner)


def _finish(kind, budget, owner, token):
    if kind == 'post':
        return budget.finish(token, outcome='RESPONSE_OBSERVED',
            request_after_head_sha256='c' * 64, actual_posts=1,
            admission=lambda: 1001, ownership=owner)
    return budget.finish(token, account_after_head_sha256='c' * 64,
        actual_gets=3, admission=lambda: 1001, ownership=owner,
        status_code=200)


def _attempt(kind, budget):
    with budget.ownership() as owner:
        return _begin(kind, budget, owner)


@pytest.mark.parametrize('kind,module,budget_type,prefix', CASES)
def test_commit_flushes_exact_main_and_only_new_posix_parent(
        tmp_path, monkeypatch, kind, module, budget_type, prefix):
    budget = budget_type(tmp_path / f'{kind}.sqlite3')
    original = module.os.fsync; calls = []

    def fsync(descriptor):
        mode = os.fstat(descriptor).st_mode
        calls.append('directory' if stat.S_ISDIR(mode) else 'file')
        return original(descriptor)

    owner = budget.ownership(); owner.__enter__()
    try:
        monkeypatch.setattr(module.os, 'fsync', fsync)
        token = _begin(kind, budget, owner)
        assert calls == (['file'] if os.name == 'nt' else
            ['file', 'directory'])
        calls.clear()
        _finish(kind, budget, owner, token)
        assert calls == ['file']
        monkeypatch.setattr(module.os, 'fsync', original)
    finally:
        owner.__exit__(None, None, None)


@pytest.mark.parametrize('kind,module,budget_type,prefix', CASES)
def test_main_flush_uncertainty_retains_pending_and_blocks_retry(
        tmp_path, monkeypatch, kind, module, budget_type, prefix):
    budget = budget_type(tmp_path / f'{kind}.sqlite3')
    original = module.os.fsync; failed = False

    def fsync(descriptor):
        nonlocal failed
        if not failed and stat.S_ISREG(os.fstat(descriptor).st_mode):
            failed = True
            raise OSError('injected private main-file flush failure')
        return original(descriptor)

    with budget.ownership() as owner:
        monkeypatch.setattr(module.os, 'fsync', fsync)
        with pytest.raises(ValueError,
                match=f'^{prefix}COMMIT_DURABILITY_UNCERTAIN$'):
            _begin(kind, budget, owner)
    monkeypatch.setattr(module.os, 'fsync', original)
    state = budget.inspect()
    assert failed and state['pending_token'] is not None
    assert state['ready_for_routing'] is False
    with pytest.raises(ValueError, match=f'^{prefix}PENDING_REVIEW$'):
        _attempt(kind, budget)


@pytest.mark.parametrize('kind,module,budget_type,prefix', CASES)
def test_commit_hashes_writable_descriptor_before_fsync(
        tmp_path, monkeypatch, kind, module, budget_type, prefix):
    budget = budget_type(tmp_path / f'{kind}.sqlite3')
    original_open = module.os.open; original_fsync = module.os.fsync
    saved = None; tampered = False; sync_calls = 0

    def open_file(value, flags, *args, **kwargs):
        nonlocal saved, tampered
        descriptor = original_open(value, flags, *args, **kwargs)
        if (not tampered and flags & os.O_RDWR and
                Path(value).resolve() == budget.path.resolve()):
            size = os.fstat(descriptor).st_size
            os.lseek(descriptor, 0, os.SEEK_SET)
            saved = os.read(descriptor, size)
            offset = min(100, size - 1)
            os.lseek(descriptor, offset, os.SEEK_SET)
            os.write(descriptor, bytes((saved[offset] ^ 1,)))
            os.lseek(descriptor, 0, os.SEEK_SET); tampered = True
        return descriptor

    def fsync(descriptor):
        nonlocal sync_calls
        sync_calls += 1
        return original_fsync(descriptor)

    with budget.ownership() as owner:
        monkeypatch.setattr(module.os, 'open', open_file)
        monkeypatch.setattr(module.os, 'fsync', fsync)
        with pytest.raises(ValueError,
                match=f'^{prefix}COMMIT_DURABILITY_UNCERTAIN$'):
            _begin(kind, budget, owner)
        assert tampered and saved and sync_calls == 0
        monkeypatch.setattr(module.os, 'open', original_open)
        monkeypatch.setattr(module.os, 'fsync', original_fsync)
    budget.path.write_bytes(saved)
    state = budget.inspect()
    assert state['pending_token'] is not None
    assert state['ready_for_routing'] is False


@pytest.mark.skipif(os.name == 'nt',
    reason='Python exposes no portable Windows directory fsync')
@pytest.mark.parametrize('kind,module,budget_type,prefix', CASES)
def test_new_parent_flush_binds_exact_directory_descriptor(
        tmp_path, monkeypatch, kind, module, budget_type, prefix):
    budget = budget_type(tmp_path / f'{kind}.sqlite3')
    foreign = tmp_path / 'foreign'; foreign.mkdir()
    original_open = module.os.open; original_fsync = module.os.fsync
    substituted = False; directory_syncs = 0

    def open_file(value, flags, *args, **kwargs):
        nonlocal substituted
        if (not substituted and flags & getattr(os, 'O_DIRECTORY', 0) and
                Path(value).resolve() == budget.path.resolve().parent):
            substituted = True
            return original_open(foreign, flags, *args, **kwargs)
        return original_open(value, flags, *args, **kwargs)

    def fsync(descriptor):
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_syncs += 1
        return original_fsync(descriptor)

    with budget.ownership() as owner:
        monkeypatch.setattr(module.os, 'open', open_file)
        monkeypatch.setattr(module.os, 'fsync', fsync)
        with pytest.raises(ValueError,
                match=f'^{prefix}COMMIT_DURABILITY_UNCERTAIN$'):
            _begin(kind, budget, owner)
        assert substituted and directory_syncs == 0
        monkeypatch.setattr(module.os, 'open', original_open)
        monkeypatch.setattr(module.os, 'fsync', original_fsync)
    state = budget.inspect()
    assert state['pending_token'] is not None
    assert state['ready_for_routing'] is False


@pytest.mark.parametrize('kind,module,budget_type,prefix', CASES)
@pytest.mark.parametrize('signal', (KeyboardInterrupt, SystemExit))
def test_commit_sync_preserves_process_control_signals(
        tmp_path, monkeypatch, kind, module, budget_type, prefix, signal):
    budget = budget_type(tmp_path / f'{kind}.sqlite3')
    original = module.os.fsync

    def interrupt(_descriptor):
        raise signal

    with budget.ownership() as owner:
        monkeypatch.setattr(module.os, 'fsync', interrupt)
        with pytest.raises(signal):
            _begin(kind, budget, owner)
        monkeypatch.setattr(module.os, 'fsync', original)
    state = budget.inspect()
    assert state['pending_token'] is not None
    assert state['ready_for_routing'] is False
