"""Independent local intent durability and conservative startup; no external IO."""
import pytest
from nexus import app as api, emergency_intent as intent
from test_emergency_storage import control, rig


@pytest.mark.parametrize('contents',[b'',intent.RECORD,b'broken\x00private'])
def test_incomplete_or_malformed_intent_blocks_cold_admission_without_ledger_repair(control,contents):
    c,g,r,*_=control
    target=intent.path(g._path());target.write_bytes(contents)
    before=g.recovery_report()
    halted,verified=api._recover_emergency_cut()
    assert halted and not verified and r.status()['cut_requested']
    with pytest.raises(ValueError,match='EMERGENCY_CUT_REQUESTED'):r.prepare('terrestrial')
    assert g.recovery_report()==before and target.read_bytes()==contents
    # Recovery alone never enables research or execution.
    assert not api.status()['portal_enabled'] and not r.status()['enabled']


def test_intent_failure_still_durably_halts_and_fences_but_cannot_ack_success(control,monkeypatch):
    c,g,r,*_=control
    def fail(*_):raise OSError('PRIVATE_DISK')
    monkeypatch.setattr(intent,'persist',fail)
    reply=c.post('/api/simulate/halt')
    assert reply.status_code==503 and 'PRIVATE' not in reply.text
    assert g.recovery_report()['halted'] and r.status()['cut_requested']
    assert api.status()['emergency_cut_state']=='UNCONFIRMED'


def test_intent_unreadable_recovery_fails_closed_even_with_clear_database(control,monkeypatch):
    c,g,r,*_=control
    def fail(*_):raise PermissionError('PRIVATE')
    monkeypatch.setattr(intent,'requested',fail)
    assert api._recover_emergency_cut()==(True,False)
    assert r.status()['cut_requested'] and not g.recovery_report()['halted']


def test_failed_intent_reset_retains_cut_and_explicit_retry_stays_disengaged(control,monkeypatch):
    c,g,r,*_=control
    assert c.post('/api/simulate/halt').status_code==200
    target=intent.path(g._path());original=intent.clear
    def fail(*_):raise OSError('PRIVATE_CLEAR')
    monkeypatch.setattr(intent,'clear',fail)
    reply=c.post('/api/simulate/halt/reset',json={'confirm_reset':True})
    assert reply.status_code==503 and 'PRIVATE' not in reply.text
    assert target.exists() and api._halted and r.status()['cut_requested']
    assert api.status()['emergency_cut_state']=='UNCONFIRMED'
    assert api._recover_emergency_cut()==(True,False)
    monkeypatch.setattr(intent,'clear',original)
    assert c.post('/api/simulate/halt/reset',json={'confirm_reset':True}).status_code==200
    assert not target.exists() and not r.status()['cut_requested'] and not r.status()['enabled']


def test_intent_bounded_idempotent_and_existing_directory_is_not_silently_repaired(tmp_path):
    order=tmp_path/'orders.sqlite3';target=intent.path(order)
    assert not intent.requested(order) and not target.exists()
    for _ in range(20):intent.persist(order)
    assert target.read_bytes()==intent.RECORD
    intent.clear(order);target.mkdir()
    assert intent.requested(order)
    intent.persist(order)
    with pytest.raises(OSError):intent.clear(order)
    assert target.is_dir()


def test_cut_at_intent_clear_boundary_wins_reset_and_repersists(control,monkeypatch):
    c,g,r,*_=control
    c.post('/api/simulate/halt');original=intent.clear
    def interrupted(order):
        original(order)
        # Same boundary as an async cut arriving while reset owns trading control.
        with api._cut_lock:
            api._cut_epoch+=1
            r.request_cut()
    monkeypatch.setattr(intent,'clear',interrupted)
    reply=c.post('/api/simulate/halt/reset',json={'confirm_reset':True})
    assert reply.status_code==409 and api._halted and r.status()['cut_requested']
    assert g.recovery_report()['halted']
    c.post('/api/simulate/halt')
    assert intent.requested(g._path()) and api._recover_emergency_cut()==(True,True)


@pytest.mark.parametrize('present',[True,False])
def test_windows_create_permission_error_only_accepts_existing_latch(tmp_path,monkeypatch,present):
    order=tmp_path/'orders.sqlite3';target=intent.path(order)
    if present:target.mkdir()
    original=intent.os.open
    def windows_open(path,*args,**kwargs):
        if path==target:raise PermissionError('simulated Windows EACCES')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(intent.os,'open',windows_open)
    if present:
        intent.persist(order)
        assert target.is_dir() and intent.requested(order)
        with pytest.raises(OSError):intent.clear(order)
    else:
        with pytest.raises(PermissionError):intent.persist(order)
        assert not target.exists()
