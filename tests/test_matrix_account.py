import json
import sqlite3
from datetime import datetime,timezone
import pytest
from nexus import matrix_account as m
from nexus.paper_portfolio import create_schema

@pytest.fixture
def account(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));clock=[1000.]
    monkeypatch.setattr(m.time,'time',lambda:clock[0])
    with sqlite3.connect(tmp_path/'paper_portfolio.sqlite3') as db:
        create_schema(db)
        db.executemany('INSERT INTO meta VALUES(?,?)',[('initial','1000'),('cash','800')])
        db.executemany('INSERT INTO positions VALUES(?,?,?)',[('BTC-USD','1','100'),('ETH-USD','2','50')])
        db.execute("INSERT INTO ledger VALUES(1,990,'INIT','','1000')")
        db.execute("INSERT INTO ledger VALUES(2,995,'PAPER_BUY','BTC-USD','-100')")
        db.execute("INSERT INTO position_events VALUES(1,2,'PAPER_BUY','BTC-USD','1','100')")
    return tmp_path,clock


def quote(product):
    at=m.time.time()
    return dict(product=product,bid=110 if product=='BTC-USD' else 55,ask=110 if product=='BTC-USD' else 55,
        provider='coinbase_exchange_public',read_only=True,orders_allowed=False,received_at=at,
        exchange_time=datetime.fromtimestamp(at,timezone.utc).isoformat())


def test_global_value_and_multiple_position_marks_are_actual_observations(account):
    path,clock=account
    before=(path/'paper_portfolio.sqlite3').read_bytes()
    sample=m.capture(quote)['point']
    assert sample['complete'] and sample['equity']=='1019.780' and sample['pnl']=='19.780'
    assert [p['unrealized_pnl'] for p in sample['positions']]==['9.890','9.890']
    assert not sample['accounting_verified']
    assert (path/'paper_portfolio.sqlite3').read_bytes()==before
    h=m.history('1m',now=1001)
    assert len(h['points'])==1 and h['observations_in_range']==1
    assert h['adjustments'][0]['quantity']=='1' and h['adjustments'][0]['cash_delta']=='-100'
    assert not h['broker_connected'] and not h['live_orders_enabled']


def test_uninitialized_reads_create_no_files(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    assert not m.history()['account_initialized']
    assert m.capture(lambda p:pytest.fail('unexpected quote'))['reason']=='PAPER_ACCOUNT_UNINITIALIZED'
    assert list(tmp_path.iterdir())==[]

@pytest.mark.parametrize('fault',['missing','stale_exchange','stale_receipt','identity','crossed'])
def test_incomplete_prices_make_an_equity_gap_not_a_cash_only_value(account,fault):
    def reader(p):
        q=quote(p)
        if p=='ETH-USD':
            if fault=='missing':raise ValueError('Unavailable')
            if fault=='stale_exchange':q['exchange_time']='1970-01-01T00:00:00+00:00'
            if fault=='stale_receipt':q['received_at']=0
            if fault=='identity':q['product']='SOL-USD'
            if fault=='crossed':q['ask']=1
        return q
    p=m.capture(reader)['point']
    assert not p['complete'] and p['equity'] is None and p['pnl'] is None
    assert p['missing_products']==['ETH-USD']
    assert len(p['positions'])==1


def test_sampling_is_throttled_and_clock_regression_does_not_append(account):
    path,clock=account
    assert m.capture(quote)['recorded']
    assert m.capture(lambda p:pytest.fail('throttle must precede transport'))['reason']=='OBSERVATION_INTERVAL_OR_CLOCK'
    clock[0]-=100
    assert not m.capture(quote)['recorded']
    assert m.history('all',now=1001)['observations_in_range']==1


def test_changed_account_during_quote_fetch_is_not_recorded(account):
    path,clock=account
    def changing(p):
        with sqlite3.connect(path/'paper_portfolio.sqlite3') as db:db.execute("UPDATE meta SET value='799' WHERE key='cash'")
        return quote(p)
    assert m.capture(changing)['reason']=='ACCOUNT_CHANGED_DURING_OBSERVATION'
    assert m.history('all')['observations_in_range']==0


def test_cap_stops_without_deleting_or_fetching(account,monkeypatch):
    m.capture(quote);monkeypatch.setattr(m,'MAX_SAMPLES',1);account[1][0]+=6
    assert m.capture(lambda p:pytest.fail('cap before transport'))['reason']=='OBSERVATION_CAP_REACHED'
    assert len(m.history('all')['points'])==1


def test_cash_only_portfolio_needs_no_external_prices(account):
    path,clock=account
    with sqlite3.connect(path/'paper_portfolio.sqlite3') as db:db.execute('DELETE FROM positions')
    p=m.capture(lambda p:pytest.fail('no holdings'))['point']
    assert p['equity']=='800' and p['positions']==[] and p['complete']


def test_retained_range_and_bounded_downsampling_do_not_backfill(account):
    path,clock=account;m.capture(quote)
    with sqlite3.connect(path/'matrix_observations.sqlite3') as db:
        point=json.loads(db.execute('SELECT payload FROM observations').fetchone()[0])
        for i in range(1,2500):
            p={**point,'at':1000+i*6}
            db.execute('INSERT INTO observations(at,payload) VALUES(?,?)',(p['at'],json.dumps(p)))
    now=1000+2499*6
    short=m.history('1m',now=now);all_=m.history('all',now=now)
    assert len(short['points'])==11 and not short['downsampled']
    assert all_['downsampled'] and len(all_['points'])<=1200
    assert all_['points'][0]['at']==1000 and all_['points'][-1]['at']==now
    assert all_['observations_in_range']==2500
    for span in m.RANGES:assert m.history(span,now=now)['range']==span
    with pytest.raises(ValueError,match='UNSUPPORTED_RANGE'):m.history('arbitrary')


def test_ytd_boundary_is_utc_and_future_samples_are_not_included(account):
    m.capture(quote)
    now=datetime(2026,10,17,tzinfo=timezone.utc).timestamp()
    h=m.history('ytd',now=now)
    assert h['range_start']==datetime(2026,1,1,tzinfo=timezone.utc).timestamp()
    assert h['points']==[]
    assert m.history('all',now=999)['points']==[]


def test_api_history_is_read_only_and_observation_requires_active_portal(account,monkeypatch):
    import nexus.app as backend
    from fastapi.testclient import TestClient
    client=TestClient(backend.app);monkeypatch.setattr(backend,'_paused',True)
    assert client.get('/api/matrix/account?span=1mo').status_code==200
    assert client.get('/api/matrix/account?span=invalid').status_code==422
    assert client.post('/api/matrix/account/observe').status_code==409
    monkeypatch.setattr(backend,'_paused',False);monkeypatch.setattr(backend,'_halted',False)
    monkeypatch.setattr(m,'capture',lambda:dict(recorded=False,reason='FIXTURE'))
    assert client.post('/api/matrix/account/observe').json()['reason']=='FIXTURE'


def test_actual_matrix_renderer_and_read_only_controls():
    import shutil
    import subprocess
    from pathlib import Path
    node=shutil.which('node')
    if not node:pytest.skip('Node required for actual Matrix controller check')
    result=subprocess.run([node,str(Path(__file__).resolve().parents[1]/'tools/matrix_account_smoke.cjs')],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
