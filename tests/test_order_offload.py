import json
from pathlib import Path
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


def test_cli_create_and_verify_emit_content_free_status(tmp_path):
    gateway=archived(tmp_path);destination=tmp_path/'archive'
    create=subprocess.run([sys.executable,'-m','nexus.order_offload','create',str(gateway.path),str(destination),
                           '--confirm-application-closed'],capture_output=True,text=True,timeout=30)
    assert create.returncode==0 and str(gateway.path) not in create.stdout
    created=json.loads(create.stdout);assert created['verified'] and not created['capacity_reclaimed']
    check=subprocess.run([sys.executable,'-m','nexus.order_offload','verify',str(destination)],
                          capture_output=True,text=True,timeout=30)
    assert check.returncode==0 and json.loads(check.stdout)['verified']


@pytest.mark.parametrize('arguments',[
    ['create','PRIVATE_PATH_TOKEN/order_lifecycle.sqlite3','PRIVATE_DEST_TOKEN','--confirm-application-closed'],
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
    assert all(report[key] is False for key in order_offload.AUTHORITY_FLAGS)
