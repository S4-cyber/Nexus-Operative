"""Exact commit durability for retained forward-session evidence."""
import os
import stat

import pytest

from nexus import sqlite_journal_guard as durability
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import PaperForwardRunner
from nexus.tier_permissions import configure


def _candidate(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    configure('terrestrial',True)
    return PaperForwardRunner(DurableOrderGateway(tmp_path/'orders.sqlite3'),
        fetcher=lambda product:None,clock=lambda:1000)


def _target(descriptor,path):
    if not path.exists():return False
    try:opened=os.fstat(descriptor);observed=path.stat()
    except OSError:return False
    return (opened.st_dev,opened.st_ino)==(observed.st_dev,observed.st_ino)


def _target_directory(descriptor,path):
    try:opened=os.fstat(descriptor);observed=path.stat()
    except OSError:return False
    return (stat.S_ISDIR(opened.st_mode) and
        (opened.st_dev,opened.st_ino)==(observed.st_dev,observed.st_ino))


def _retained_review(tmp_path):
    cold=PaperForwardRunner(DurableOrderGateway(tmp_path/'orders.sqlite3'),
        fetcher=lambda product:None,clock=lambda:1000)
    sessions=cold.sessions()['sessions']
    assert len(sessions)==1
    assert sessions[0]['stored_state']=='RUNNING'
    assert sessions[0]['effective_state']=='INTERRUPTED_OR_PRIOR_SESSION'
    assert sessions[0]['requires_review'] is True


def test_commit_flushes_exact_main_and_only_new_posix_parent(
        tmp_path,monkeypatch):
    runner=_candidate(tmp_path,monkeypatch);path=tmp_path/'paper_forward.sqlite3'
    original=durability.os.fsync;calls=[]
    def fsync(descriptor):
        if _target(descriptor,path):calls.append('file')
        elif _target_directory(descriptor,path.parent):calls.append('directory')
        return original(descriptor)
    monkeypatch.setattr(durability.os,'fsync',fsync)
    runner.prepare('terrestrial')
    assert calls[-1:]==['file'] if os.name=='nt' else (
        calls[-2:]==['file','directory'])
    calls.clear();runner._record('HOLD','COMMIT_FIXTURE')
    assert calls==['file']
    runner.stop()


def test_main_flush_failure_retains_unacknowledged_session_for_review(
        tmp_path,monkeypatch):
    runner=_candidate(tmp_path,monkeypatch);path=tmp_path/'paper_forward.sqlite3'
    original=durability.os.fsync;failed=False
    def fsync(descriptor):
        nonlocal failed
        if not failed and _target(descriptor,path):
            failed=True;raise OSError('injected private forward flush failure')
        return original(descriptor)
    monkeypatch.setattr(durability.os,'fsync',fsync)
    with pytest.raises(ValueError,
            match='^PAPER_FORWARD_COMMIT_DURABILITY_UNCERTAIN$'):
        runner.prepare('terrestrial')
    monkeypatch.setattr(durability.os,'fsync',original)
    assert failed and not runner.status()['enabled']
    assert not runner.status()['ownership_held']
    _retained_review(tmp_path)


@pytest.mark.skipif(os.name=='nt',
    reason='Python exposes no portable Windows directory fsync')
def test_new_parent_flush_binds_exact_directory_descriptor(
        tmp_path,monkeypatch):
    runner=_candidate(tmp_path,monkeypatch);path=tmp_path/'paper_forward.sqlite3'
    foreign=tmp_path/'foreign';foreign.mkdir()
    original_open=durability.os.open;original_fsync=durability.os.fsync
    main_flushed=False;substituted=False
    def fsync(descriptor):
        nonlocal main_flushed
        if _target(descriptor,path):main_flushed=True
        return original_fsync(descriptor)
    def open_file(value,flags,*args,**kwargs):
        nonlocal substituted
        if (main_flushed and not substituted and
                flags & getattr(os,'O_DIRECTORY',0) and
                os.path.samefile(value,path.parent)):
            substituted=True
            return original_open(foreign,flags,*args,**kwargs)
        return original_open(value,flags,*args,**kwargs)
    monkeypatch.setattr(durability.os,'fsync',fsync)
    monkeypatch.setattr(durability.os,'open',open_file)
    with pytest.raises(ValueError,
            match='^PAPER_FORWARD_COMMIT_DURABILITY_UNCERTAIN$'):
        runner.prepare('terrestrial')
    monkeypatch.setattr(durability.os,'open',original_open)
    assert substituted and not runner.status()['ownership_held']
    _retained_review(tmp_path)


@pytest.mark.parametrize('signal',(KeyboardInterrupt,SystemExit))
def test_commit_sync_preserves_process_control(tmp_path,monkeypatch,signal):
    runner=_candidate(tmp_path,monkeypatch);path=tmp_path/'paper_forward.sqlite3'
    original=durability.os.fsync
    def interrupt(descriptor):
        if _target(descriptor,path):raise signal
        return original(descriptor)
    monkeypatch.setattr(durability.os,'fsync',interrupt)
    with pytest.raises(signal):runner.prepare('terrestrial')
    monkeypatch.setattr(durability.os,'fsync',original)
    assert not runner.status()['enabled']
    assert not runner.status()['ownership_held']
    _retained_review(tmp_path)


@pytest.mark.parametrize('kind',('symlink','hardlink'))
def test_unsafe_existing_forward_store_is_not_followed_or_repaired(
        tmp_path,monkeypatch,kind):
    if kind=='symlink' and os.name=='nt':
        pytest.skip('Windows CI does not assume symlink privilege')
    runner=_candidate(tmp_path,monkeypatch);path=tmp_path/'paper_forward.sqlite3'
    target=tmp_path/'foreign.sqlite3';target.write_bytes(b'private fixture')
    if kind=='symlink':path.symlink_to(target)
    else:os.link(target,path)
    before=target.read_bytes()
    with pytest.raises(ValueError,
            match='^(FORWARD_HISTORY|PAPER_FORWARD)_(SYMLINK|HARDLINK)$'):
        runner.prepare('terrestrial')
    assert target.read_bytes()==before
    assert not runner.status()['enabled']
    assert not runner.status()['ownership_held']
