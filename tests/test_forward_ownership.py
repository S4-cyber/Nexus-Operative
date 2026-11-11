import subprocess
import sys
import threading
import os
import pytest
from nexus import forward_ownership as ownership
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


def test_unsafe_owner_directory_symlink_is_rejected_without_touching_target(tmp_path):
    target=tmp_path/'private-target';target.mkdir();root=tmp_path/'owner-root'
    try:root.symlink_to(target,target_is_directory=True)
    except OSError:pytest.skip('directory symlink unavailable')
    candidate=ForwardOwnership(root)
    with pytest.raises(ValueError,match='PAPER_FORWARD_OWNER_STORE_UNAVAILABLE'):
        candidate.available()
    with pytest.raises(ValueError,match='PAPER_FORWARD_OWNER_STORE_UNAVAILABLE'):
        candidate.acquire()
    assert not list(target.iterdir()) and not candidate.held


def test_release_rejects_late_owner_directory_replacement(tmp_path,monkeypatch):
    root=tmp_path/'owner-root';candidate=ForwardOwnership(root);candidate.acquire()
    if os.name=='nt':
        # Windows may deny moving a directory containing a mandatory-share
        # locked file. Inject the equivalent acquired-directory disagreement.
        candidate._directory_identity=(-1,-1)
    else:
        retired=tmp_path/'retired-owner-root';os.replace(root,retired);root.mkdir()
        # Preserve the same regular single-link lock inode and public lock path.
        # Only the containing storage-directory identity now disagrees.
        os.replace(retired/candidate.path.name,candidate.path)
        assert os.path.samestat(os.fstat(candidate._fd),candidate.path.lstat())
    with pytest.raises(ValueError,match='PAPER_FORWARD_OWNER_STORE_CHANGED'):
        candidate.validate()
    with pytest.raises(ValueError,match='PAPER_FORWARD_OWNER_STORE_CHANGED'):
        candidate.release()
    assert not candidate.held


def test_validate_rechecks_directory_after_lock_path_sample(tmp_path,monkeypatch):
    root=tmp_path/'owner-root';candidate=ForwardOwnership(root);candidate.acquire()
    original=ownership._directory_identity;calls=[0]
    if os.name=='nt':
        def replace_after_first_sample(path,before=None,*,changed=False):
            result=original(path,before,changed=changed);calls[0]+=int(changed)
            if calls[0]==2:raise ValueError('PAPER_FORWARD_OWNER_STORE_CHANGED')
            return result
    else:
        retired=tmp_path/'retired-owner-root'
        def replace_after_first_sample(path,before=None,*,changed=False):
            result=original(path,before,changed=changed)
            if changed and calls[0]==0:
                calls[0]+=1;os.replace(root,retired);root.mkdir()
                os.replace(retired/candidate.path.name,candidate.path)
                assert os.path.samestat(os.fstat(candidate._fd),candidate.path.lstat())
            return result
    monkeypatch.setattr(ownership,'_directory_identity',replace_after_first_sample)
    with pytest.raises(ValueError,match='PAPER_FORWARD_OWNER_STORE_CHANGED'):
        candidate.validate()
    try:candidate.release()
    except ValueError as error:assert str(error)=='PAPER_FORWARD_OWNER_STORE_CHANGED'
    assert not candidate.held


def test_release_rejects_late_owner_path_replacement(tmp_path,monkeypatch):
    candidate=ForwardOwnership(tmp_path);candidate.acquire();replacement=None
    if os.name=='nt':
        # Windows correctly denies renaming the mandatory-share locked file;
        # inject the equivalent late identity disagreement for portable exit
        # error-priority coverage.
        monkeypatch.setattr(candidate,'validate',
            lambda: (_ for _ in ()).throw(ValueError('PAPER_FORWARD_OWNER_STORE_CHANGED')))
    else:
        retired=tmp_path/'retired-owner.lock';os.replace(candidate.path,retired)
        candidate.path.write_bytes(b'0')
        replacement=ForwardOwnership(tmp_path);replacement.acquire()
        with pytest.raises(ValueError,match='PAPER_FORWARD_OWNER_STORE_CHANGED'):
            candidate.validate()
    with pytest.raises(ValueError,match='PAPER_FORWARD_OWNER_STORE_CHANGED'):
        candidate.release()
    assert not candidate.held
    if replacement is not None:replacement.release()


def test_runtime_owner_replacement_fails_before_quote_or_order_mutation(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);before_history=r.history();before_orders=g.recovery_report()
    replacement=[None]
    def replace_during_fetch(product):
        if os.name=='nt':
            monkeypatch.setattr(r._ownership,'validate',
                lambda: (_ for _ in ()).throw(ValueError('PAPER_FORWARD_OWNER_STORE_CHANGED')))
        else:
            retired=tmp_path/'retired-forward-owner.lock'
            os.replace(r._ownership.path,retired);r._ownership.path.write_bytes(b'0')
            replacement[0]=ForwardOwnership(tmp_path);replacement[0].acquire()
        return c.quote(product)
    r.fetcher=replace_during_fetch
    with pytest.raises(ValueError,match='PAPER_FORWARD_OWNER_STORE_CHANGED'):
        r.single_cycle()
    assert not r.status()['enabled'] and not r.status()['ownership_held']
    assert r.history()==before_history and g.recovery_report()==before_orders
    # The process-independent cut signal never depends on the owner lock.
    r.request_cut();assert r.status()['cut_requested']
    if replacement[0] is not None:replacement[0].release()


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
