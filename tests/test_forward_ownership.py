import subprocess
import sys
import threading
import os
import pytest
from nexus.forward_ownership import ForwardOwnership
from nexus.paper_forward import PaperForwardRunner
from test_paper_forward import setup


def test_second_runner_rejected_before_account_or_session_change(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    second=PaperForwardRunner(g,c.quote,lambda:c.now)
    before=g.recovery_report();sessions=r.sessions()
    with pytest.raises(ValueError,match='OWNED_BY_ANOTHER_RUNNER'):second.prepare('terrestrial')
    assert g.recovery_report()==before and r.sessions()==sessions
    assert not second.status()['enabled']
    r.stop()
    assert not r.status()['ownership_held']
    assert second.prepare('terrestrial')['ownership_held']
    second.stop()


def test_process_death_releases_os_ownership(tmp_path):
    expression='from nexus.forward_ownership import ForwardOwnership; import sys; lock=ForwardOwnership(sys.argv[1]); lock.acquire(); print("ready",flush=True); sys.stdin.read()'
    process=subprocess.Popen([sys.executable,'-c',expression,str(tmp_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip()=='ready'
        candidate=ForwardOwnership(tmp_path)
        with pytest.raises(ValueError,match='OWNED_BY_ANOTHER_RUNNER'):candidate.acquire()
        process.kill();process.wait(timeout=10)
        candidate.acquire();assert candidate.held;candidate.release()
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=10)
        process.stdin.close();process.stdout.close()


def test_read_only_availability_probe_never_creates_or_reserves_lock(tmp_path):
    candidate=ForwardOwnership(tmp_path)
    assert candidate.available() and not candidate.path.exists() and not list(tmp_path.iterdir())
    candidate.acquire();candidate.release()
    before=candidate.path.read_bytes()
    assert candidate.available() and candidate.path.read_bytes()==before
    candidate.acquire();candidate.release()


def test_availability_probe_detects_external_process_without_mutation(tmp_path):
    expression='from nexus.forward_ownership import ForwardOwnership; import sys; lock=ForwardOwnership(sys.argv[1]); lock.acquire(); print("ready",flush=True); sys.stdin.read()'
    process=subprocess.Popen([sys.executable,'-c',expression,str(tmp_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip()=='ready';candidate=ForwardOwnership(tmp_path)
        before=candidate.path.read_bytes();assert not candidate.available()
        assert candidate.path.read_bytes()==before
        process.kill();process.wait(timeout=10)
        assert candidate.available() and candidate.path.read_bytes()==before
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=10)
        process.stdin.close();process.stdout.close()


@pytest.mark.parametrize('kind',['symlink','hardlink','oversized'])
def test_unsafe_owner_store_is_rejected_without_touching_target(tmp_path,kind):
    candidate=ForwardOwnership(tmp_path);target=tmp_path/'private-target'
    target.write_bytes(b'PRIVATE')
    if kind=='symlink':
        try:candidate.path.symlink_to(target)
        except OSError:pytest.skip('file symlink unavailable')
    elif kind=='hardlink':
        try:os.link(target,candidate.path)
        except OSError:pytest.skip('hardlink unavailable')
    else:candidate.path.write_bytes(b'oversized')
    before=target.read_bytes()
    with pytest.raises(ValueError,match='PAPER_FORWARD_OWNER_STORE_UNAVAILABLE'):candidate.available()
    with pytest.raises(ValueError,match='PAPER_FORWARD_OWNER_STORE_UNAVAILABLE'):candidate.acquire()
    assert target.read_bytes()==before and not candidate.held


def test_failed_preparation_releases_ownership(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.stop()
    with pytest.raises(ValueError,match='POLICY_NOT_ADMITTED'):r.prepare('terrestrial','invalid')
    candidate=ForwardOwnership(tmp_path);candidate.acquire();candidate.release()


def test_stop_during_fetch_keeps_ownership_until_cycle_finishes(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);entered=threading.Event();release=threading.Event()
    def fetch(product):
        entered.set();release.wait(timeout=10);return c.quote(product)
    r.fetcher=fetch
    cycle=threading.Thread(target=r.single_cycle);cycle.start()
    try:
        assert entered.wait(timeout=5)
        r.stop();assert r.status()['ownership_held']
        candidate=ForwardOwnership(tmp_path)
        with pytest.raises(ValueError,match='OWNED_BY_ANOTHER_RUNNER'):candidate.acquire()
    finally:
        release.set();cycle.join(timeout=10)
    assert not cycle.is_alive() and not r.status()['ownership_held']
    assert not g.recovery_report()['orders']
    candidate.acquire();candidate.release()


def test_failure_pause_releases_ownership(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.fetcher=lambda product:None
    for _ in range(3):r.single_cycle()
    assert not r.status()['enabled'] and not r.status()['ownership_held']
    candidate=ForwardOwnership(tmp_path);candidate.acquire();candidate.release()


def test_worker_start_failure_releases_ownership(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.stop()
    def fail_start(self):raise RuntimeError('cannot start worker')
    monkeypatch.setattr(threading.Thread,'start',fail_start)
    with pytest.raises(RuntimeError,match='cannot start worker'):r.start('terrestrial')
    assert not r.status()['ownership_held'] and not r.status()['enabled']
    candidate=ForwardOwnership(tmp_path);candidate.acquire();candidate.release()
