import json
import sqlite3
import pytest
from nexus import recovery_bundle as bundle
from nexus.durable_orders import DurableOrderGateway


def source(tmp_path):
    root=tmp_path/'data';root.mkdir()
    g=DurableOrderGateway(root/'orders.sqlite3')
    g.submit('preserved','BTC-USD','BUY',25)
    g.simulate_event('preserved','fill',1,'FILL','10','.01')
    g.emergency_halt()
    (root/'nested').mkdir();(root/'nested'/'settings.txt').write_text('retain')
    return root,g


def test_snapshot_restore_replays_halt_and_orders(tmp_path):
    root,g=source(tmp_path);before=bundle.inventory(root)
    snapshot=tmp_path/'backup';restored=tmp_path/'restored'
    assert bundle.backup(root,snapshot,application_closed=True)['verified']
    assert bundle.verify(snapshot)['files']==before
    assert bundle.restore(snapshot,restored,application_closed=True)['verified']
    report=DurableOrderGateway(restored/'orders.sqlite3').recovery_report()
    assert report['halted']
    assert report['orders']==g.recovery_report()['orders']
    assert bundle.inventory(root)==before
    assert (restored/'nested'/'settings.txt').read_text()=='retain'
    assert not (restored/bundle.MANIFEST).exists()


def test_no_overwrite_or_unconfirmed_copy(tmp_path):
    root,g=source(tmp_path)
    with pytest.raises(ValueError,match='CLOSE_APPLICATION'):bundle.backup(root,tmp_path/'b')
    with pytest.raises(ValueError,match='MUST_BE_NEW'):bundle.backup(root,root,application_closed=True)
    with pytest.raises(ValueError,match='INSIDE_SOURCE'):bundle.backup(root,root/'sub',application_closed=True)
    assert not (root/'sub').exists()


def test_tampered_backup_does_not_create_restore(tmp_path):
    root,g=source(tmp_path);snapshot=tmp_path/'backup'
    bundle.backup(root,snapshot,application_closed=True)
    (snapshot/'nested'/'settings.txt').write_text('changed')
    with pytest.raises(ValueError,match='CONTENT_DISAGREEMENT'):
        bundle.restore(snapshot,tmp_path/'restored',application_closed=True)
    assert not (tmp_path/'restored').exists()


def test_changed_source_aborts_and_removes_stage(tmp_path,monkeypatch):
    root,g=source(tmp_path);original=bundle.shutil.copy2
    def changing(src,dst):
        result=original(src,dst)
        (root/'nested'/'settings.txt').write_text('changed during backup')
        return result
    monkeypatch.setattr(bundle.shutil,'copy2',changing)
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):
        bundle.backup(root,tmp_path/'backup',application_closed=True)
    assert not (tmp_path/'backup').exists()
    assert not list(tmp_path.glob('nexus-recovery-*'))


def test_corrupt_database_aborts(tmp_path):
    root=tmp_path/'data';root.mkdir();(root/'bad.sqlite3').write_bytes(b'not a sqlite database')
    with pytest.raises(sqlite3.DatabaseError):bundle.backup(root,tmp_path/'backup',application_closed=True)
    assert not (tmp_path/'backup').exists()


@pytest.mark.parametrize('sidecar',['orders.sqlite3-wal','orders.sqlite3-shm','orders.sqlite3-journal'])
def test_pending_sidecars_require_review(tmp_path,sidecar):
    root,g=source(tmp_path);(root/sidecar).write_bytes(b'pending')
    with pytest.raises(ValueError,match='SIDECARS_REQUIRE_OFFLINE_REVIEW'):
        bundle.backup(root,tmp_path/'backup',application_closed=True)


def test_manifest_path_injection_is_never_opened(tmp_path):
    root,g=source(tmp_path);snapshot=tmp_path/'backup'
    bundle.backup(root,snapshot,application_closed=True)
    path=snapshot/bundle.MANIFEST;doc=json.loads(path.read_text())
    doc['files'][0]['name']='../../private'
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='CONTENT_DISAGREEMENT'):bundle.verify(snapshot)


def test_offline_cli_roundtrip_and_confirmation(tmp_path):
    import subprocess
    import sys
    root,g=source(tmp_path);snapshot=tmp_path/'backup';restored=tmp_path/'restored'
    command=[sys.executable,'-m','nexus.recovery_bundle']
    result=subprocess.run(command+['backup',str(root),str(snapshot)],capture_output=True,text=True)
    assert result.returncode==1 and 'CLOSE_APPLICATION' in result.stderr
    for args in [['backup',str(root),str(snapshot),'--confirm-application-closed'],
                 ['verify',str(snapshot)],
                 ['restore',str(snapshot),str(restored),'--confirm-application-closed']]:
        result=subprocess.run(command+args,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)['verified']
