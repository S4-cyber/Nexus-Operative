import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import pytest
from nexus.durable_orders import DurableOrderGateway
from nexus.order_accounting import order_id_fingerprint, retired_id_set_sha256


def test_restart_preserves_partial_fees_cancel_and_idempotency(tmp_path):
    path = tmp_path/'orders.sqlite3'
    g = DurableOrderGateway(path)
    g.submit('order', 'BTC-USD', 'BUY', 25)
    g.simulate_event('order', 'fill', 1, 'FILL', '10', '.01')
    g = DurableOrderGateway(path)
    assert g.simulate_event('order', 'fill', 1, 'FILL', '10', '.01')['idempotent_replay']
    assert g.submit('order', 'BTC-USD', 'BUY', 25)['idempotent_replay']
    g.cancel('order')
    record = DurableOrderGateway(path).recovery_report()['orders'][0]
    assert record['status'] == 'SIMULATED_CANCELLED'
    assert record['simulated_filled_notional'] == '10'
    assert record['simulated_fee_total'] == '0.01'


def test_halt_survives_new_instance_and_reset(tmp_path):
    path = tmp_path/'orders.sqlite3'
    g = DurableOrderGateway(path)
    g.submit('order', 'BTC-USD', 'BUY', 25)
    g.emergency_halt()
    g = DurableOrderGateway(path)
    with pytest.raises(ValueError, match='EMERGENCY_HALT_ACTIVE'):
        g.submit('other', 'BTC-USD', 'BUY', 25)
    assert g.recovery_report()['halted']
    assert g.reset()['paused']
    assert DurableOrderGateway(path).recovery_report()['orders'][0]['status'] == 'SIMULATED_CANCELLED'


def test_independent_writers_apply_duplicate_once(tmp_path):
    path = tmp_path/'orders.sqlite3'
    DurableOrderGateway(path).submit('order', 'BTC-USD', 'BUY', 25)
    def fill(_):
        return DurableOrderGateway(path).simulate_event('order', 'fill', 1, 'FILL', 10)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(fill, range(18)))
    assert sum(not r['idempotent_replay'] for r in results) == 1
    assert DurableOrderGateway(path).recovery_report()['commands'] == 2


def test_invalid_transition_not_committed(tmp_path):
    g = DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.submit('order', 'BTC-USD', 'BUY', 25)
    with pytest.raises(ValueError, match='SIMULATED_OVERFILL'):
        g.simulate_event('order', 'fill', 1, 'FILL', 26)
    assert g.recovery_report()['commands'] == 1


def test_replacement_epoch_lineage_requires_fresh_halted_flat_state_and_replays(tmp_path):
    path=tmp_path/'orders.sqlite3';g=DurableOrderGateway(path)
    with pytest.raises(ValueError,match='FRESH_HALTED_FLAT'):
        g.bind_replacement_epoch(1,'a'*64,'0'*64)
    g.initialize_account(1000);g.link_portfolio();g.emergency_halt()
    bound=g.bind_replacement_epoch(1,'a'*64,'0'*64)
    assert bound['generation']==1 and len(bound['epoch_sha256'])==64
    assert g.bind_replacement_epoch(1,'a'*64,'0'*64)['idempotent_replay']
    with pytest.raises(ValueError,match='LINEAGE_CONFLICT'):
        g.bind_replacement_epoch(1,'b'*64,'0'*64)
    restored=DurableOrderGateway(path).recovery_report()
    assert restored['lineage']=={key:value for key,value in bound.items() if key!='idempotent_replay'}
    assert restored['commands']==4 and restored['halted']


def test_replacement_epoch_replays_bounded_retired_id_fence_without_retaining_plain_ids(tmp_path):
    path=tmp_path/'orders.sqlite3';g=DurableOrderGateway(path)
    g.initialize_account(1000);g.link_portfolio();g.emergency_halt()
    fingerprints=sorted(order_id_fingerprint(value) for value in ('prior-a','prior-b'))
    bound=g.bind_replacement_epoch(1,'a'*64,'0'*64,fingerprints)
    assert bound['retired_order_id_fingerprint_count']==2
    assert bound['retired_order_id_fingerprints_sha256']==retired_id_set_sha256(fingerprints)
    restored=DurableOrderGateway(path)
    report=restored.recovery_report()
    assert report['retired_order_ids']==dict(policy='nexus.retired-order-id-fingerprint.v1',
        fingerprint_count=2,fingerprint_set_sha256=retired_id_set_sha256(fingerprints),
        fingerprint_limit=100000,cross_epoch_reuse_fenced=True)
    assert 'prior-a' not in path.read_bytes().decode('utf-8',errors='ignore')
    restored.reset();before=restored.recovery_report()['commands']
    with pytest.raises(ValueError,match='RETIRED_ORDER_ID_REUSE'):
        restored.submit('prior-a','BTC-USD','BUY',25)
    assert restored.recovery_report()['commands']==before
    assert restored.submit('new-id','BTC-USD','BUY',25)['id']=='new-id'


def test_reviewed_legacy_baseline_provenance_is_durable_and_replay_bound(tmp_path):
    path=tmp_path/'orders.sqlite3';g=DurableOrderGateway(path)
    g.initialize_account(1000);g.link_portfolio();g.emergency_halt()
    fingerprints=sorted(order_id_fingerprint(value) for value in ('prior-a','prior-b'))
    bound=g.bind_replacement_epoch(2,'a'*64,'b'*64,fingerprints,'c'*64)
    assert bound['retired_order_id_baseline_kind']=='OPERATOR_REVIEWED_UNATTESTED'
    assert bound['retired_order_id_baseline_review_sha256']=='c'*64
    assert DurableOrderGateway(path).recovery_report()['lineage']=={
        key:value for key,value in bound.items() if key!='idempotent_replay'}


def test_reviewed_legacy_baseline_hash_requires_fingerprint_set(tmp_path):
    path=tmp_path/'orders.sqlite3';g=DurableOrderGateway(path)
    g.initialize_account(1000);g.link_portfolio();g.emergency_halt()
    with pytest.raises(ValueError,match='INVALID_RETIRED_ORDER_ID_BASELINE_REVIEW'):
        g.bind_replacement_epoch(1,'a'*64,'0'*64,None,'c'*64)


@pytest.mark.parametrize('fingerprints',[
    ['f'*32,'a'*32],['a'*32,'a'*32],['A'*32],['a'*31],[1],
])
def test_invalid_retired_id_fingerprint_sets_are_rejected(tmp_path,fingerprints):
    g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.initialize_account(1000);g.link_portfolio();g.emergency_halt()
    with pytest.raises(ValueError,match='INVALID_RETIRED_ORDER_ID_FINGERPRINTS'):
        g.bind_replacement_epoch(1,'a'*64,'0'*64,fingerprints)


@pytest.mark.parametrize('generation,archive,predecessor',[
    (0,'a'*64,'0'*64),(1,'A'*64,'0'*64),(1,'a'*63,'0'*64),
    (1,'a'*64,'1'*64),(2,'a'*64,'0'*64)])
def test_invalid_replacement_epoch_lineage_is_rejected(tmp_path,generation,archive,predecessor):
    g=DurableOrderGateway(tmp_path/'orders.sqlite3')
    g.initialize_account(1000);g.link_portfolio();g.emergency_halt()
    with pytest.raises(ValueError,match='INVALID_REPLACEMENT_EPOCH_LINEAGE'):
        g.bind_replacement_epoch(generation,archive,predecessor)


def test_corrupt_log_fails_closed(tmp_path):
    path = tmp_path/'orders.sqlite3'
    g = DurableOrderGateway(path)
    g.submit('order', 'BTC-USD', 'BUY', 25)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE commands SET payload='{}'")
    with pytest.raises(ValueError, match='ORDER_JOURNAL_RECOVERY_FAILED'):
        g.reset()


def test_sqlite_write_failure_does_not_acknowledge(tmp_path):
    path = tmp_path/'orders.sqlite3'
    g = DurableOrderGateway(path)
    # Explicit disposable schema fixture; observation no longer creates it.
    with closing(g._connect()):pass
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER fail_write BEFORE INSERT ON commands BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    with pytest.raises(sqlite3.Error):
        g.submit('order', 'BTC-USD', 'BUY', 25)
    assert g.recovery_report()['orders'] == []


def test_recovery_api_and_failed_reset_keep_halt(tmp_path, monkeypatch):
    import nexus.app as backend
    from fastapi.testclient import TestClient
    g = DurableOrderGateway(tmp_path/'orders.sqlite3')
    monkeypatch.setattr(backend, 'research_order_gateway', g)
    monkeypatch.setattr(backend, '_halted', True)
    monkeypatch.setattr(backend, '_paused', True)
    monkeypatch.setattr(backend.observer, 'stop', lambda: None)
    client = TestClient(backend.app)
    assert client.get('/api/execution/recovery').json()['portfolio_reconciled'] is False
    def failure():
        raise sqlite3.OperationalError('unavailable')
    monkeypatch.setattr(g, 'reset', failure)
    assert client.post('/api/simulate/halt/reset', json={'confirm_reset':True}).status_code == 503
    assert backend._halted is True
