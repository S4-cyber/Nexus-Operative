import builtins
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from nexus.durable_orders import DurableOrderGateway
from nexus import order_offload
from nexus.tier_permissions import configure


def archived(tmp_path):
    live=tmp_path/'live';live.mkdir();configure('terrestrial',True)
    gateway=DurableOrderGateway(live/order_offload.ORDER_FILE)
    gateway.initialize_account(10000);gateway.link_portfolio()
    for side in ('BUY','SELL'):
        oid=side.lower();gateway.submit(oid,'BTC-USD',side,50,tier='terrestrial')
        gateway.set_execution_terms(oid,1000,10)
        gateway.simulate_quote_fill(oid,'fill',1,1,25,25,2,1001,1001)
        gateway.cancel(oid)
    gateway.emergency_halt();gateway.archive_terminal('epoch_0')
    return gateway


def test_verified_copy_preserves_source_and_archived_replay(tmp_path):
    gateway=archived(tmp_path);source=gateway.path;portfolio=source.parent/order_offload.PORTFOLIO_FILE
    before=(source.read_bytes(),portfolio.read_bytes());destination=tmp_path/'off-volume'/'epoch_0'
    result=order_offload.create(source,destination,application_closed=True)
    assert result['verified'] and result['source_unchanged'] and result['new_destination']
    assert not result['capacity_reclaimed'] and not result['source_history_deleted']
    assert before==(source.read_bytes(),portfolio.read_bytes())
    assert set(path.name for path in destination.iterdir())=={
        order_offload.ORDER_FILE,order_offload.PORTFOLIO_FILE,order_offload.MANIFEST}
    verified=order_offload.verify(destination)
    assert verified['archived_orders']==2 and verified['archive_batches']==1
    copied=DurableOrderGateway(destination/order_offload.ORDER_FILE)
    assert copied.order_report('buy')['archived'] and copied.order_report('sell')['archived']
    assert copied.recovery_report()['portfolio_reconciled']


def test_rotation_plan_exactly_binds_copy_and_live_source_without_writes(tmp_path):
    gateway=archived(tmp_path);source=gateway.path;portfolio=source.parent/order_offload.PORTFOLIO_FILE
    destination=tmp_path/'off-volume'/'epoch_0'
    order_offload.create(source,destination,application_closed=True)
    before=(source.read_bytes(),portfolio.read_bytes(),
            (destination/order_offload.ORDER_FILE).read_bytes(),
            (destination/order_offload.PORTFOLIO_FILE).read_bytes(),
            (destination/order_offload.MANIFEST).read_bytes())
    result=order_offload.rotation_plan(source,destination,application_closed=True)
    assert result['mode']=='OFFLINE_ORDER_ROTATION_PLAN'
    assert result['verified_copy'] and result['exact_source_bound']
    assert result['source_unchanged'] and result['archive_unchanged']
    assert result['rotation_preflight_only'] and result['dry_run_only']
    assert not result['compaction_performed'] and not result['capacity_reclaimed']
    assert not result['source_history_deleted'] and not result['automatic_resume']
    assert before==(source.read_bytes(),portfolio.read_bytes(),
                    (destination/order_offload.ORDER_FILE).read_bytes(),
                    (destination/order_offload.PORTFOLIO_FILE).read_bytes(),
                    (destination/order_offload.MANIFEST).read_bytes())


def test_rotation_plan_rejects_stale_copy_and_requires_closed_assertion(tmp_path):
    gateway=archived(tmp_path);source=gateway.path;destination=tmp_path/'archive'
    order_offload.create(source,destination,application_closed=True)
    with pytest.raises(ValueError,match='APPLICATION_MUST_BE_CLOSED'):
        order_offload.rotation_plan(source,destination)
    gateway.reset();gateway.emergency_halt()
    with pytest.raises(ValueError,match='ROTATION_SOURCE_DISAGREEMENT'):
        order_offload.rotation_plan(source,destination,application_closed=True)


def test_rotation_plan_rechecks_source_and_archive_after_comparison(tmp_path,monkeypatch):
    gateway=archived(tmp_path);source=gateway.path;destination=tmp_path/'archive'
    order_offload.create(source,destination,application_closed=True)
    original_verify=order_offload.verify;calls=[0]
    def change_archive(path):
        result=original_verify(path);calls[0]+=1
        if calls[0]==1:
            manifest=destination/order_offload.MANIFEST
            manifest.write_bytes(manifest.read_bytes()+b' ')
        return result
    monkeypatch.setattr(order_offload,'verify',change_archive)
    with pytest.raises(ValueError,match='MANIFEST_INVALID|ARCHIVE_CHANGED'):
        order_offload.rotation_plan(source,destination,application_closed=True)


def test_rotation_plan_rejects_source_change_after_initial_replay(tmp_path,monkeypatch):
    gateway=archived(tmp_path);source=gateway.path;destination=tmp_path/'archive'
    order_offload.create(source,destination,application_closed=True)
    original_snapshot=order_offload._snapshot;changed=[False]
    def change_source(path):
        result=original_snapshot(path)
        if Path(path)==source and not changed[0]:
            changed[0]=True;gateway.reset();gateway.emergency_halt()
        return result
    monkeypatch.setattr(order_offload,'_snapshot',change_source)
    with pytest.raises(ValueError,match='SOURCE_CHANGED_DURING_ROTATION_PLAN'):
        order_offload.rotation_plan(source,destination,application_closed=True)


def test_prepare_replacement_publishes_halted_candidate_and_preserves_inputs(tmp_path):
    gateway=archived(tmp_path);source=gateway.path;archive=tmp_path/'archive';candidate=tmp_path/'candidate'
    order_offload.create(source,archive,application_closed=True)
    before=(source.read_bytes(),(source.parent/order_offload.PORTFOLIO_FILE).read_bytes(),
            (archive/order_offload.ORDER_FILE).read_bytes(),
            (archive/order_offload.PORTFOLIO_FILE).read_bytes(),
            (archive/order_offload.MANIFEST).read_bytes())
    result=order_offload.prepare_replacement(source,archive,candidate,application_closed=True)
    assert result['verified'] and result['replacement_candidate_only'] and result['new_destination']
    assert result['halted'] and result['flat'] and result['portfolio_reconciled']
    assert result['commands']==4 and result['source_archived_orders']==2
    assert result['generation']==1 and len(result['epoch_sha256'])==64
    assert result['retired_order_id_fingerprint_count']==2 and result['cross_epoch_id_reuse_fenced']
    assert result['source_preserved'] and result['archive_preserved']
    assert not result['capacity_reclaimed'] and not result['source_history_deleted']
    assert not result['automatic_install'] and not result['automatic_resume']
    assert set(path.name for path in candidate.iterdir())=={
        order_offload.ORDER_FILE,order_offload.PORTFOLIO_FILE,order_offload.REPLACEMENT_MANIFEST}
    assert order_offload.verify_replacement(archive,candidate)=={key:value for key,value in result.items()
        if key!='new_destination'}
    replacement=DurableOrderGateway(candidate/order_offload.ORDER_FILE).recovery_report()
    assert replacement['halted'] and replacement['orders']==[]
    source_cash=gateway.recovery_report()['execution_account']['account']['cash']
    assert replacement['execution_account']['account']=={'initial':source_cash,'cash':source_cash,'positions':{}}
    assert replacement['archival']['archived_order_count']==0
    assert before==(source.read_bytes(),(source.parent/order_offload.PORTFOLIO_FILE).read_bytes(),
                    (archive/order_offload.ORDER_FILE).read_bytes(),
                    (archive/order_offload.PORTFOLIO_FILE).read_bytes(),
                    (archive/order_offload.MANIFEST).read_bytes())


def test_replacement_rejects_archive_or_candidate_tamper(tmp_path):
    gateway=archived(tmp_path);source=gateway.path;archive=tmp_path/'archive';candidate=tmp_path/'candidate'
    order_offload.create(source,archive,application_closed=True)
    order_offload.prepare_replacement(source,archive,candidate,application_closed=True)
    manifest=candidate/order_offload.REPLACEMENT_MANIFEST
    document=json.loads(manifest.read_text());document['commands']=5
    manifest.write_text(json.dumps(document))
    with pytest.raises(ValueError,match='REPLACEMENT_REPLAY_DISAGREEMENT'):
        order_offload.verify_replacement(archive,candidate)
    document['commands']=4;manifest.write_text(json.dumps(document))
    with sqlite3.connect(candidate/order_offload.ORDER_FILE) as db:
        db.execute("UPDATE commands SET payload='{}' WHERE sequence=1")
    with pytest.raises(ValueError,match='REPLACEMENT_CONTENT_DISAGREEMENT|RECOVERY_FAILED'):
        order_offload.verify_replacement(archive,candidate)


def test_v1_replacement_candidate_remains_verifiable(tmp_path):
    gateway=archived(tmp_path);source=gateway.path;archive=tmp_path/'archive';candidate=tmp_path/'candidate'
    order_offload.create(source,archive,application_closed=True)
    order_offload.prepare_replacement(source,archive,candidate,application_closed=True)
    with sqlite3.connect(candidate/order_offload.ORDER_FILE) as db:
        db.execute('DELETE FROM commands WHERE sequence=4')
    manifest=candidate/order_offload.REPLACEMENT_MANIFEST;document=json.loads(manifest.read_text())
    document['schema']=order_offload.REPLACEMENT_SCHEMA_V1
    for key in ('source_epoch_sha256','candidate_epoch_sha256','candidate_generation',
                'retired_order_id_policy','retired_order_id_fingerprint_count',
                'retired_order_id_fingerprints_sha256'):document.pop(key)
    document['commands']=3
    document['candidate_recovery_sha256']=order_offload._snapshot(candidate/order_offload.ORDER_FILE)[1]
    document['files']=order_offload._files(candidate)
    manifest.write_text(json.dumps(document,sort_keys=True,indent=2)+'\n')
    verified=order_offload.verify_replacement(archive,candidate)
    assert verified['generation']==0 and verified['commands']==3


def test_v2_replacement_candidate_remains_verifiable(tmp_path):
    gateway=archived(tmp_path);source=gateway.path;archive=tmp_path/'archive';candidate=tmp_path/'candidate'
    order_offload.create(source,archive,application_closed=True)
    order_offload.prepare_replacement(source,archive,candidate,application_closed=True)
    source_report=DurableOrderGateway(archive/order_offload.ORDER_FILE).recovery_report()
    archive_sha=order_offload._manifest_entry(archive)['sha256']
    legacy_lineage=order_offload._next_lineage(source_report,archive_sha)
    with sqlite3.connect(candidate/order_offload.ORDER_FILE) as db:
        payload=json.loads(db.execute('SELECT payload FROM commands WHERE sequence=4').fetchone()[0])
        payload['args']=payload['args'][:3]
        db.execute('UPDATE commands SET payload=? WHERE sequence=4',(json.dumps(payload,allow_nan=False),))
    manifest=candidate/order_offload.REPLACEMENT_MANIFEST;document=json.loads(manifest.read_text())
    document['schema']=order_offload.REPLACEMENT_SCHEMA_V2
    for key in ('retired_order_id_policy','retired_order_id_fingerprint_count',
                'retired_order_id_fingerprints_sha256'):document.pop(key)
    document['candidate_epoch_sha256']=legacy_lineage['epoch_sha256']
    document['candidate_recovery_sha256']=order_offload._snapshot(candidate/order_offload.ORDER_FILE)[1]
    document['files']=order_offload._files(candidate)
    manifest.write_text(json.dumps(document,sort_keys=True,indent=2)+'\n')
    verified=order_offload.verify_replacement(archive,candidate)
    assert verified['generation']==1 and verified['commands']==4
    assert verified['cross_epoch_id_reuse_fenced'] is False


def test_prepare_replacement_requires_fresh_external_destination_and_closed_assertion(tmp_path):
    gateway=archived(tmp_path);source=gateway.path;archive=tmp_path/'archive'
    order_offload.create(source,archive,application_closed=True)
    with pytest.raises(ValueError,match='APPLICATION_MUST_BE_CLOSED'):
        order_offload.prepare_replacement(source,archive,tmp_path/'candidate')
    with pytest.raises(ValueError,match='OUTSIDE_SOURCE_AND_ARCHIVE'):
        order_offload.prepare_replacement(source,archive,archive/'candidate',application_closed=True)
    existing=tmp_path/'existing';existing.mkdir()
    with pytest.raises(ValueError,match='DESTINATION_MUST_BE_NEW'):
        order_offload.prepare_replacement(source,archive,existing,application_closed=True)


def test_legacy_rotated_source_requires_explicit_retired_id_baseline_review(tmp_path):
    live=tmp_path/'live';live.mkdir();source=live/order_offload.ORDER_FILE
    gateway=DurableOrderGateway(source);gateway.initialize_account(1000);gateway.link_portfolio();gateway.emergency_halt()
    gateway.bind_replacement_epoch(1,'a'*64,'0'*64)  # v2 lineage had no retired-ID membership set.
    gateway.reset();gateway.submit('legacy-id','BTC-USD','BUY',25,tier='terrestrial');gateway.cancel('legacy-id')
    gateway.emergency_halt();gateway.archive_terminal('legacy_epoch')
    archive=tmp_path/'archive';order_offload.create(source,archive,application_closed=True)
    with pytest.raises(ValueError,match='RETIRED_ORDER_ID_BASELINE_REVIEW_REQUIRED'):
        order_offload.prepare_replacement(source,archive,tmp_path/'candidate',application_closed=True)


def test_failed_replacement_verification_never_publishes_destination(tmp_path,monkeypatch):
    gateway=archived(tmp_path);source=gateway.path;archive=tmp_path/'archive';destination=tmp_path/'candidate'
    order_offload.create(source,archive,application_closed=True)
    original=order_offload.verify_replacement
    def fail_stage(archive_path,candidate_path):
        if Path(candidate_path)!=destination:raise ValueError('INJECTED_REPLACEMENT_VERIFY_FAILURE')
        return original(archive_path,candidate_path)
    monkeypatch.setattr(order_offload,'verify_replacement',fail_stage)
    with pytest.raises(ValueError,match='INJECTED_REPLACEMENT_VERIFY_FAILURE'):
        order_offload.prepare_replacement(source,archive,destination,application_closed=True)
    assert not destination.exists()


def prepared_install(tmp_path):
    gateway=archived(tmp_path);source=gateway.path;archive=tmp_path/'archive';candidate=tmp_path/'candidate'
    order_offload.create(source,archive,application_closed=True)
    order_offload.prepare_replacement(source,archive,candidate,application_closed=True)
    return gateway,source,archive,candidate


def test_file_sync_uses_windows_compatible_writable_descriptor_without_mutation(tmp_path,monkeypatch):
    path=tmp_path/'private.bin';path.write_bytes(b'unchanged')
    modes=[]
    def opened(target,mode,**kwargs):
        modes.append(mode)
        return builtins.open(target,mode,**kwargs)
    monkeypatch.setattr(order_offload,'open',opened,raising=False)
    order_offload._sync_file(path)
    assert modes==['r+b'] and path.read_bytes()==b'unchanged'


def test_install_replacement_reclaims_live_capacity_and_explicit_rollback_restores_source(tmp_path):
    gateway,source,archive,candidate=prepared_install(tmp_path);recovery=tmp_path/'install-recovery'
    portfolio=source.parent/order_offload.PORTFOLIO_FILE
    before=(source.read_bytes(),portfolio.read_bytes());source_commands=gateway.recovery_report()['commands']
    installed=order_offload.install_replacement(source,archive,candidate,recovery,application_closed=True)
    assert installed['installed'] and installed['capacity_reclaimed'] and installed['rollback_available']
    assert installed['source_preserved'] and not installed['source_history_deleted']
    assert not installed['automatic_resume'] and not installed['live_orders_enabled']
    report=DurableOrderGateway(source).recovery_report()
    assert report['commands']==4 and report['halted'] and report['orders']==[]
    assert report['lineage']['generation']==1
    assert not (source.parent/order_offload.ROTATION_MARKER).exists()
    recovery_report=order_offload.verify_install_recovery(archive,recovery)
    assert recovery_report['source_commands']==source_commands and recovery_report['rollback_available']
    rolled_back=order_offload.rollback_install(source,archive,recovery,application_closed=True)
    assert rolled_back['restored'] and not rolled_back['capacity_reclaimed']
    assert before==(source.read_bytes(),portfolio.read_bytes())
    assert DurableOrderGateway(source).recovery_report()['commands']==source_commands


def test_successive_replacements_form_replay_verified_epoch_lineage(tmp_path):
    _,source,archive_0,candidate_1=prepared_install(tmp_path)
    order_offload.install_replacement(source,archive_0,candidate_1,tmp_path/'recovery-0',application_closed=True)
    first=DurableOrderGateway(source).recovery_report()['lineage']
    assert first['generation']==1

    gateway=DurableOrderGateway(source);gateway.reset()
    for side in ('BUY','SELL'):
        oid=side.lower()+'-epoch-1';gateway.submit(oid,'BTC-USD',side,50,tier='terrestrial')
        gateway.set_execution_terms(oid,2000,10)
        gateway.simulate_quote_fill(oid,'fill',1,1,25,25,2,2001,2001)
        gateway.cancel(oid)
    gateway.emergency_halt();gateway.archive_terminal('epoch_1')
    archive_1=tmp_path/'archive-1';candidate_2=tmp_path/'candidate-2'
    order_offload.create(source,archive_1,application_closed=True)
    prepared=order_offload.prepare_replacement(source,archive_1,candidate_2,application_closed=True)
    second=DurableOrderGateway(candidate_2/order_offload.ORDER_FILE).recovery_report()['lineage']
    assert prepared['generation']==2 and second['generation']==2
    assert second['predecessor_epoch_sha256']==first['epoch_sha256']
    assert second['predecessor_archive_sha256']==order_offload._manifest_entry(archive_1)['sha256']
    installed=order_offload.install_replacement(source,archive_1,candidate_2,tmp_path/'recovery-1',application_closed=True)
    assert installed['generation']==2 and DurableOrderGateway(source).recovery_report()['lineage']==second
    rotated=DurableOrderGateway(source);rotated.reset();before=rotated.recovery_report()['commands']
    with pytest.raises(ValueError,match='RETIRED_ORDER_ID_REUSE'):
        rotated.submit('buy','BTC-USD','BUY',50,tier='terrestrial')
    assert rotated.recovery_report()['commands']==before


def test_interrupted_pair_install_blocks_gateway_until_explicit_recovery(tmp_path,monkeypatch):
    _,source,archive,candidate=prepared_install(tmp_path);recovery=tmp_path/'install-recovery'
    portfolio=source.parent/order_offload.PORTFOLIO_FILE
    before=(source.read_bytes(),portfolio.read_bytes());original_replace=order_offload.os.replace
    def interrupt(source_path,target_path):
        if Path(target_path)==portfolio:raise OSError('INJECTED_SECOND_REPLACE_FAILURE')
        return original_replace(source_path,target_path)
    monkeypatch.setattr(order_offload.os,'replace',interrupt)
    with pytest.raises(OSError,match='INJECTED_SECOND_REPLACE_FAILURE'):
        order_offload.install_replacement(source,archive,candidate,recovery,application_closed=True)
    marker=source.parent/order_offload.ROTATION_MARKER
    assert marker.is_file() and source.read_bytes()!=before[0] and portfolio.read_bytes()==before[1]
    with pytest.raises(ValueError,match='ORDER_ROTATION_RECOVERY_REQUIRED'):
        DurableOrderGateway(source).recovery_report()
    monkeypatch.setattr(order_offload.os,'replace',original_replace)
    recovered=order_offload.recover_install(source,archive,recovery,application_closed=True)
    assert recovered['restored'] and not marker.exists()
    assert before==(source.read_bytes(),portfolio.read_bytes())
    assert DurableOrderGateway(source).recovery_report()['halted']


def test_install_requires_closed_assertion_fresh_external_recovery_and_intact_manifest(tmp_path):
    _,source,archive,candidate=prepared_install(tmp_path)
    with pytest.raises(ValueError,match='APPLICATION_MUST_BE_CLOSED'):
        order_offload.install_replacement(source,archive,candidate,tmp_path/'recovery')
    nested_candidate=source.parent/'nested-candidate'
    shutil.copytree(candidate,nested_candidate)
    with pytest.raises(ValueError,match='CANDIDATE_OUTSIDE_SOURCE_AND_ARCHIVE'):
        order_offload.install_replacement(source,archive,nested_candidate,tmp_path/'nested-recovery',application_closed=True)
    with pytest.raises(ValueError,match='OUTSIDE_SOURCE_ARCHIVE_AND_CANDIDATE'):
        order_offload.install_replacement(source,archive,candidate,candidate/'recovery',application_closed=True)
    recovery=tmp_path/'recovery'
    order_offload.install_replacement(source,archive,candidate,recovery,application_closed=True)
    manifest=recovery/order_offload.INSTALL_MANIFEST;document=json.loads(manifest.read_text())
    document['source_commands']+=1;manifest.write_text(json.dumps(document))
    with pytest.raises(ValueError,match='INSTALL_RECOVERY_REPLAY_DISAGREEMENT'):
        order_offload.verify_install_recovery(archive,recovery)


def test_v1_install_recovery_manifest_remains_verifiable(tmp_path):
    _,source,archive,candidate=prepared_install(tmp_path);recovery=tmp_path/'recovery'
    order_offload.install_replacement(source,archive,candidate,recovery,application_closed=True)
    manifest=recovery/order_offload.INSTALL_MANIFEST;document=json.loads(manifest.read_text())
    document['schema']=order_offload.INSTALL_SCHEMA_V1
    document.pop('candidate_generation');document.pop('candidate_epoch_sha256')
    manifest.write_text(json.dumps(document,sort_keys=True,indent=2)+'\n')
    verified=order_offload.verify_install_recovery(archive,recovery)
    assert verified['candidate_generation']==0 and verified['candidate_epoch_sha256']=='0'*64


def test_v1_interrupted_install_marker_remains_recoverable(tmp_path):
    _,source,archive,candidate=prepared_install(tmp_path);recovery=tmp_path/'recovery'
    order_offload.install_replacement(source,archive,candidate,recovery,application_closed=True)
    transaction_sha=order_offload._manifest_entry(recovery,order_offload.INSTALL_MANIFEST)['sha256']
    marker=order_offload._write_rotation_marker(source.parent,transaction_sha)
    document=json.loads(marker.read_text());document['schema']=order_offload.INSTALL_SCHEMA_V1
    marker.write_text(json.dumps(document,sort_keys=True)+'\n')
    recovered=order_offload.recover_install(source,archive,recovery,application_closed=True)
    assert recovered['restored'] and recovered['halted'] and not marker.exists()


def test_uncertain_marker_clear_restores_gateway_fence(tmp_path,monkeypatch):
    _,source,archive,candidate=prepared_install(tmp_path);recovery=tmp_path/'install-recovery'
    order_offload.install_replacement(source,archive,candidate,recovery,application_closed=True)
    transaction_sha=order_offload._manifest_entry(recovery,order_offload.INSTALL_MANIFEST)['sha256']
    marker=order_offload._write_rotation_marker(source.parent,transaction_sha)
    original_sync=order_offload._sync_directory
    def fail_live_sync(path):
        if Path(path)==source.parent:raise OSError('INJECTED_DIRECTORY_SYNC_FAILURE')
        return original_sync(path)
    monkeypatch.setattr(order_offload,'_sync_directory',fail_live_sync)
    with pytest.raises(OSError,match='INJECTED_DIRECTORY_SYNC_FAILURE'):
        order_offload._clear_rotation_marker(source.parent,transaction_sha)
    assert marker.is_file()
    with pytest.raises(ValueError,match='ORDER_ROTATION_RECOVERY_REQUIRED'):
        DurableOrderGateway(source).recovery_report()


def test_creation_requires_closed_halted_flat_logical_archive(tmp_path):
    live=tmp_path/'live';live.mkdir();source=live/order_offload.ORDER_FILE
    gateway=DurableOrderGateway(source);gateway.initialize_account(1000);gateway.link_portfolio()
    with pytest.raises(ValueError,match='APPLICATION_MUST_BE_CLOSED'):
        order_offload.create(source,tmp_path/'a')
    with pytest.raises(ValueError,match='HALTED_FLAT_LOGICAL_ARCHIVE_REQUIRED'):
        order_offload.create(source,tmp_path/'a',application_closed=True)
    gateway.submit('open','BTC-USD','BUY',25,tier='terrestrial');gateway.emergency_halt()
    with pytest.raises(ValueError,match='HALTED_FLAT_LOGICAL_ARCHIVE_REQUIRED'):
        order_offload.create(source,tmp_path/'a',application_closed=True)


def test_destination_must_be_new_and_outside_live_directory(tmp_path):
    gateway=archived(tmp_path);existing=tmp_path/'existing';existing.mkdir()
    with pytest.raises(ValueError,match='DESTINATION_MUST_BE_NEW'):
        order_offload.create(gateway.path,existing,application_closed=True)
    with pytest.raises(ValueError,match='OUTSIDE_LIVE_DATA_REQUIRED'):
        order_offload.create(gateway.path,gateway.path.parent/'archive',application_closed=True)


def test_sidecars_and_link_aliases_fail_closed(tmp_path):
    gateway=archived(tmp_path);sidecar=gateway.path.with_name(gateway.path.name+'-wal');sidecar.write_bytes(b'review')
    with pytest.raises(ValueError,match='SQLITE_SIDECAR_REVIEW_REQUIRED'):
        order_offload.create(gateway.path,tmp_path/'archive',application_closed=True)
    sidecar.unlink();alias=tmp_path/'alias.sqlite3';alias.hardlink_to(gateway.path)
    with pytest.raises(ValueError,match='HARDLINK_NOT_ALLOWED'):
        order_offload.create(gateway.path,tmp_path/'archive',application_closed=True)


def test_copy_or_manifest_tamper_is_rejected(tmp_path):
    gateway=archived(tmp_path);destination=tmp_path/'archive'
    order_offload.create(gateway.path,destination,application_closed=True)
    manifest=destination/order_offload.MANIFEST;document=json.loads(manifest.read_text())
    document['commands']+=1;manifest.write_text(json.dumps(document))
    with pytest.raises(ValueError,match='REPLAY_DISAGREEMENT'):
        order_offload.verify(destination)
    document['commands']-=1;manifest.write_text(json.dumps(document))
    with sqlite3.connect(destination/order_offload.ORDER_FILE) as db:
        db.execute("UPDATE commands SET payload='{}' WHERE sequence=1")
    with pytest.raises(ValueError,match='CONTENT_DISAGREEMENT|RECOVERY_FAILED'):
        order_offload.verify(destination)


def test_verification_requires_both_databases_and_unlinked_manifest(tmp_path):
    gateway=archived(tmp_path);destination=tmp_path/'archive'
    order_offload.create(gateway.path,destination,application_closed=True)
    portfolio=destination/order_offload.PORTFOLIO_FILE;portfolio.unlink()
    with pytest.raises(ValueError,match='CONTENT_DISAGREEMENT'):
        order_offload.verify(destination)
    order_offload.create(gateway.path,tmp_path/'complete',application_closed=True)
    complete=tmp_path/'complete';manifest=complete/order_offload.MANIFEST
    contents=manifest.read_bytes();manifest.unlink();backing=tmp_path/'linked-manifest.json'
    backing.write_bytes(contents);manifest.hardlink_to(backing)
    with pytest.raises(ValueError,match='MANIFEST_INVALID'):
        order_offload.verify(complete)


def test_failed_verification_never_publishes_destination(tmp_path,monkeypatch):
    gateway=archived(tmp_path);destination=tmp_path/'archive';original=order_offload.verify
    calls=[0]
    def fail_once(path):
        calls[0]+=1
        if calls[0]==1:raise ValueError('INJECTED_VERIFY_FAILURE')
        return original(path)
    monkeypatch.setattr(order_offload,'verify',fail_once)
    with pytest.raises(ValueError,match='INJECTED_VERIFY_FAILURE'):
        order_offload.create(gateway.path,destination,application_closed=True)
    assert not destination.exists()


def test_integrity_connections_close_before_staged_directory_publish(tmp_path,monkeypatch):
    gateway=archived(tmp_path);destination=tmp_path/'archive';opened=[]
    original_connect=order_offload.sqlite3.connect
    original_rename=order_offload.os.rename
    class Tracked:
        def __init__(self,connection):self.connection=connection;self.closed=False
        def __getattr__(self,name):return getattr(self.connection,name)
        def close(self):self.connection.close();self.closed=True
    def connect(*args,**kwargs):
        connection=original_connect(*args,**kwargs)
        if kwargs.get('uri') is True and 'immutable=1' in str(args[0]):
            connection=Tracked(connection);opened.append(connection)
        return connection
    def rename(source,target):
        assert opened and all(connection.closed for connection in opened)
        return original_rename(source,target)
    monkeypatch.setattr(order_offload.sqlite3,'connect',connect)
    monkeypatch.setattr(order_offload.os,'rename',rename)
    assert order_offload.create(gateway.path,destination,application_closed=True)['verified']
    assert all(connection.closed for connection in opened)


def test_cli_create_and_verify_emit_content_free_status(tmp_path):
    gateway=archived(tmp_path);destination=tmp_path/'archive'
    create=subprocess.run([sys.executable,'-m','nexus.order_offload','create',str(gateway.path),str(destination),
                           '--confirm-application-closed'],capture_output=True,text=True,timeout=30)
    assert create.returncode==0 and str(gateway.path) not in create.stdout
    created=json.loads(create.stdout);assert created['verified'] and not created['capacity_reclaimed']
    check=subprocess.run([sys.executable,'-m','nexus.order_offload','verify',str(destination)],
                          capture_output=True,text=True,timeout=30)
    assert check.returncode==0 and json.loads(check.stdout)['verified']
    plan=subprocess.run([sys.executable,'-m','nexus.order_offload','rotation-plan',str(gateway.path),str(destination),
                         '--confirm-application-closed'],capture_output=True,text=True,timeout=30)
    assert plan.returncode==0 and json.loads(plan.stdout)['exact_source_bound']
    candidate=tmp_path/'candidate'
    prepared=subprocess.run([sys.executable,'-m','nexus.order_offload','prepare-replacement',str(gateway.path),
        str(destination),str(candidate),'--confirm-application-closed'],capture_output=True,text=True,timeout=30)
    assert prepared.returncode==0 and str(gateway.path) not in prepared.stdout
    assert json.loads(prepared.stdout)['replacement_candidate_only']
    replacement=subprocess.run([sys.executable,'-m','nexus.order_offload','verify-replacement',str(destination),
        str(candidate)],capture_output=True,text=True,timeout=30)
    assert replacement.returncode==0 and json.loads(replacement.stdout)['verified']


@pytest.mark.parametrize('arguments',[
    ['create','PRIVATE_PATH_TOKEN/order_lifecycle.sqlite3','PRIVATE_DEST_TOKEN','--confirm-application-closed'],
    ['rotation-plan','PRIVATE_PATH_TOKEN/order_lifecycle.sqlite3','PRIVATE_DEST_TOKEN'],
    ['prepare-replacement','PRIVATE_PATH_TOKEN/order_lifecycle.sqlite3','PRIVATE_ARCHIVE_TOKEN','PRIVATE_DEST_TOKEN'],
    ['--unknown','PRIVATE_ARGUMENT_TOKEN'],
])
def test_cli_failures_never_echo_private_arguments(arguments):
    result=subprocess.run([sys.executable,'-m','nexus.order_offload',*arguments],capture_output=True,text=True,timeout=30)
    assert result.returncode==2
    assert result.stdout+result.stderr in ('ORDER_OFFLOAD_FAILED\n','ORDER_OFFLOAD_USAGE_ERROR\n')
    assert 'PRIVATE_' not in result.stdout+result.stderr


def test_fixture_is_bounded_and_grants_no_authority():
    report=order_offload.fixture_report()
    assert report['passed'] and all(report[key] for key in order_offload.CHECKS)
    assert report['multi_epoch_generations']==4 and report['multi_epoch_orders']==48
    assert report['multi_epoch_retired_id_fingerprint_count']==48
    assert report['multi_epoch_max_live_commands']==4
    assert report['multi_epoch_max_source_commands']<100
    assert report['multi_epoch_retired_id_reuse_fenced'] is True
    assert all(report[key] is False for key in order_offload.AUTHORITY_FLAGS)
