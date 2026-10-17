from decimal import Decimal
import json
import sqlite3
import pytest
from nexus import order_checkpoints as cp,order_gateway,durable_orders
from nexus.durable_orders import DurableOrderGateway
from nexus.tier_permissions import configure
from nexus.paper_portfolio import state
from nexus.portfolio_governor import audit_portfolio


def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    configure('terrestrial',True)
    g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.initialize_account(10000);g.link_portfolio()
    return g


def cycle(g,index):
    now=1000+index*30
    for side in ('BUY','SELL'):
        oid=side+'_'+str(index)
        g.submit(oid,'BTC-USD',side,50,tier='terrestrial')
        g.set_execution_terms(oid,now,10)
        g.simulate_quote_fill(oid,'fill',1,1,25,25.1,2,now+1,now+1)
        g.cancel(oid)


def test_archive_releases_active_capacity_and_preserves_replays(tmp_path,monkeypatch):
    monkeypatch.setattr(order_gateway,'MAX_INTENTS',2)
    monkeypatch.setattr(cp,'INTERVAL',4)
    g=setup(tmp_path,monkeypatch)
    cycle(g,0)
    before=state()
    g.emergency_halt()
    manifest=g.archive_terminal('batch_0')
    assert manifest['order_count']==2
    assert len(manifest['evidence_sha256'])==64
    report=g.recovery_report()
    assert report['orders']==[]
    assert report['archival']['archived_order_count']==2
    assert report['capacity']['intents_remaining']==2
    cp.clear_cache();g=DurableOrderGateway(g.path)
    assert g.submit('BUY_0','BTC-USD','BUY',50,tier='terrestrial')['idempotent_replay']
    assert g.simulate_quote_fill('BUY_0','fill',1,1,25,25.1,2,1001,1001)['idempotent_replay']
    with pytest.raises(ValueError,match='DUPLICATE_ID_CONFLICT'):
        g.submit('BUY_0','ETH-USD','BUY',50,tier='terrestrial')
    assert state()==before
    assert g.archive_terminal('batch_0')['idempotent_replay']
    g.reset();cycle(g,1)
    assert g.recovery_report()['portfolio_reconciled']
    assert audit_portfolio()['integrity_ok']


def test_archive_requires_halt_flatness_and_valid_identity(tmp_path,monkeypatch):
    g=setup(tmp_path,monkeypatch)
    g.submit('buy','BTC-USD','BUY',25,tier='terrestrial')
    with pytest.raises(ValueError,match='REQUIRES_HALT'):g.archive_terminal('batch')
    g.simulate_priced_fill('buy','a',1,1,25)
    g.emergency_halt()
    with pytest.raises(ValueError,match='FLAT_ACCOUNT'):g.archive_terminal('batch')
    with pytest.raises(ValueError,match='INVALID_ARCHIVE_BATCH_ID'):g.archive_terminal('../bad')
    assert g.recovery_report()['archival']['archived_order_count']==0


def test_failed_archive_commit_preserves_old_epoch(tmp_path,monkeypatch):
    g=setup(tmp_path,monkeypatch);cycle(g,0);g.emergency_halt()
    before=g.recovery_report()
    with sqlite3.connect(g.path) as db:
        db.execute("CREATE TRIGGER fail BEFORE INSERT ON commands BEGIN SELECT RAISE(ABORT,'failure'); END")
    with pytest.raises(sqlite3.Error):g.archive_terminal('batch')
    after=g.recovery_report()
    assert after['orders']==before['orders']
    assert after['archival']==before['archival']
    assert after['commands']==before['commands']
    assert after['halted']


def test_retained_history_limit_cannot_be_bypassed_by_rotation(tmp_path,monkeypatch):
    monkeypatch.setattr(durable_orders,'MAX_RETAINED_COMMANDS',1020)
    g=setup(tmp_path,monkeypatch);cycle(g,0);g.emergency_halt();g.archive_terminal('batch');g.reset()
    with pytest.raises(ValueError,match='CAP_REACHED'):
        # Lower total-history bound preserves its independent stop even after rotation.
        monkeypatch.setattr(durable_orders,'MAX_RETAINED_COMMANDS',1014)
        g.submit('new','BTC-USD','BUY',25,tier='terrestrial')
    assert g.recovery_report()['archival']['archived_order_count']==2


def test_repeated_linked_operation_across_24_epochs(tmp_path,monkeypatch):
    monkeypatch.setattr(order_gateway,'MAX_INTENTS',4)
    monkeypatch.setattr(durable_orders,'MAX_COMMANDS',1013)
    monkeypatch.setattr(cp,'INTERVAL',8)
    g=setup(tmp_path,monkeypatch)
    for index in range(24):
        cycle(g,index)
        g.emergency_halt()
        g.archive_terminal('batch_'+str(index))
        assert g.recovery_report()['capacity']['ordinary_commands_remaining']==12
        g.reset()
        g=DurableOrderGateway(g.path)
    cp.clear_cache()
    report=g.recovery_report()
    assert report['archival']['archived_order_count']==48
    assert len(report['archival']['batches'])==24
    assert report['portfolio_reconciled']
    assert state()['positions']==[]
    assert audit_portfolio()['integrity_ok']
    assert report['commands']>durable_orders.MAX_COMMANDS-1001
    assert not report['archival']['command_history_deleted']


def test_pre_archival_checkpoint_schema_remains_readable(tmp_path,monkeypatch):
    monkeypatch.setattr(cp,'INTERVAL',2)
    g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.initialize_account(1000);g.submit('a','BTC-USD','BUY',25)
    with sqlite3.connect(g.path) as db:
        data=json.loads(db.execute('SELECT state_json FROM order_checkpoints').fetchone()[0])
        for key in ('archived_intents','archive_batches','archival_boundary'):data.pop(key)
        db.execute('UPDATE order_checkpoints SET state_json=?',(json.dumps(data,sort_keys=True,separators=(',',':')),))
    cp.clear_cache()
    assert g.recovery_report()['commands']==2


def test_archived_order_history_read_only_api(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import nexus.app as backend
    g=setup(tmp_path,monkeypatch);cycle(g,0);g.emergency_halt();g.archive_terminal('batch')
    monkeypatch.setattr(backend,'research_order_gateway',g)
    c=TestClient(backend.app)
    data=c.get('/api/execution/intent/BUY_0').json()
    assert data['read_only'] and data['archived']
    assert data['order']['status']=='SIMULATED_CANCELLED'
    assert len(data['executions'])==1
    assert len(data['quote_events'])==1
    assert c.get('/api/execution/intent/missing').status_code==404


def test_archive_history_edit_fails_closed(tmp_path,monkeypatch):
    g=setup(tmp_path,monkeypatch);cycle(g,0);g.emergency_halt();g.archive_terminal('batch')
    with sqlite3.connect(g.path) as db:
        row=db.execute('SELECT sequence,payload FROM commands ORDER BY sequence DESC LIMIT 1').fetchone()
        payload=json.loads(row[1]);payload['args'][1]+=10
        db.execute('UPDATE commands SET payload=? WHERE sequence=?',(json.dumps(payload),row[0]))
    cp.clear_cache()
    with pytest.raises(ValueError):g.recovery_report()


def test_archive_api_requires_confirmation_and_stays_halted(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import nexus.app as backend
    g=setup(tmp_path,monkeypatch);cycle(g,0)
    monkeypatch.setattr(backend,'research_order_gateway',g)
    monkeypatch.setattr(backend,'_halted',False)
    monkeypatch.setattr(backend,'_paused',True)
    c=TestClient(backend.app)
    request=dict(batch_id='maintenance',confirm_archive=True)
    assert c.post('/api/execution/archive-terminal',json=request).status_code==409
    g.emergency_halt();monkeypatch.setattr(backend,'_halted',True)
    assert c.post('/api/execution/archive-terminal',json={**request,'confirm_archive':False}).status_code==422
    result=c.post('/api/execution/archive-terminal',json=request)
    assert result.status_code==200
    assert result.json()['halted'] and result.json()['paused']
    assert not result.json()['history_deleted']
    assert c.post('/api/execution/archive-terminal',json=request).json()['idempotent_replay']
    assert backend._halted and backend._paused


def test_process_death_during_archive_checkpoint_preserves_old_epoch(tmp_path,monkeypatch):
    import subprocess,sys
    g=setup(tmp_path,monkeypatch);cycle(g,0);g.emergency_halt()
    before=g.recovery_report()
    with sqlite3.connect(g.path) as db:
        db.execute('CREATE TRIGGER die BEFORE INSERT ON order_checkpoints BEGIN SELECT kill_process(); END')
    code='''
import os,sys
from nexus import order_checkpoints as cp
from nexus.durable_orders import DurableOrderGateway
cp.INTERVAL=4
original=DurableOrderGateway._connect
def connect(self):
    db=original(self)
    db.create_function('kill_process',0,lambda:os._exit(73))
    return db
DurableOrderGateway._connect=connect
DurableOrderGateway(sys.argv[1]).archive_terminal('crash_batch')
'''
    result=subprocess.run([sys.executable,'-c',code,str(g.path)],capture_output=True,timeout=20)
    assert result.returncode==73,result.stderr.decode()
    cp.clear_cache()
    after=g.recovery_report()
    assert after['commands']==before['commands']
    assert after['orders']==before['orders']
    assert after['archival']['archived_order_count']==0
    assert after['halted'] and after['portfolio_reconciled']
