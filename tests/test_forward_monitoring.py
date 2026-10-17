import json
import sqlite3
import pytest
from nexus.paper_forward import PaperForwardRunner
from nexus.paper_policy import admit,catalogue
from test_paper_forward import setup,warmup


def test_session_counters_survive_restart_without_authority(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    warmup(r,c)
    session=r.sessions()['sessions'][0]
    assert session['counts']['BUY']==1
    assert session['counts']['START']==1
    assert session['policy']['policy_sha256']==r.status()['policy']['policy_sha256']
    new=PaperForwardRunner(g,c.quote,lambda:c.now)
    assert not new.status()['enabled']
    assert new.sessions()['sessions'][0]['effective_state']=='INTERRUPTED_OR_PRIOR_SESSION'
    r.stop('TEST_STOP')
    final=new.sessions()['sessions'][0]
    assert final['stored_state']=='STOPPED'
    assert final['counts']['STOP']==1
    assert not final['requires_review']


def test_heartbeat_staleness_and_no_automatic_rearm(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    c.now+=21
    assert r.sessions()['sessions'][0]['effective_state']=='HEARTBEAT_STALE'
    assert r.sessions()['sessions'][0]['requires_review']
    # Reading evidence cannot change enabled state or submit orders.
    assert r.status()['enabled']
    assert not g.recovery_report()['orders']
    r.stop()


def test_outage_resets_entry_window(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    for i in range(5):c.now=1000+i*5;c.price=100+i*.1;r.single_cycle()
    original=r.fetcher
    r.fetcher=lambda product:None
    c.now+=5
    assert r.single_cycle()['decision']=='VETO'
    r.fetcher=original;c.now+=5;c.price=101
    result=r.single_cycle()
    assert result['decision']=='HOLD'
    assert not g.recovery_report()['orders']
    assert r.status()['consecutive_feed_failures']==0


def test_long_gap_does_not_use_old_entry_samples(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    for i in range(5):c.now=1000+i*5;c.price=100+i*.1;r.single_cycle()
    c.now+=60;c.price=101
    assert r.single_cycle()['decision']=='HOLD'
    assert not g.recovery_report()['orders']


def test_research_ids_and_mutated_copies_have_no_authority(tmp_path,monkeypatch):
    for policy in ('mean_reversion','spot_momentum','ai_generated','https://example.com'):
        with pytest.raises(ValueError,match='NOT_ADMITTED'):admit(policy)
    data=catalogue();data['admitted_experiments'][0]['entry_threshold_bps']=0
    assert admit('btc_momentum_experiment_v1')['entry_threshold_bps']==30
    r,g,c=setup(tmp_path,monkeypatch);r.stop()
    with pytest.raises(ValueError,match='NOT_ADMITTED'):r.prepare('terrestrial','mean_reversion')
    assert not r.status()['enabled']


def test_session_write_failure_is_atomic_with_cycle_log(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    before=r.sessions()['sessions'][0]['counts']
    history=len(r.history())
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        db.execute("CREATE TRIGGER fail_cycle BEFORE INSERT ON cycles BEGIN SELECT RAISE(ABORT,'failure'); END")
    c.now+=5
    assert r.single_cycle()['decision']=='PAUSED'
    assert not r.status()['enabled']
    assert r.sessions()['sessions'][0]['counts']==before
    assert len(r.history())==history


def test_outage_while_holding_requires_explicit_resume_then_allows_exit(tmp_path,monkeypatch):
    from nexus.tier_permissions import configure
    r,g,c=setup(tmp_path,monkeypatch)
    assert warmup(r,c)['decision']=='BUY'
    old_commands=g.recovery_report()['commands']
    r.fetcher=lambda product:None
    for _ in range(3):c.now+=5;result=r.single_cycle()
    assert result['decision']=='PAUSED'
    assert g.recovery_report()['commands']==old_commands
    assert g.recovery_report()['execution_account']['account']['positions']
    stored=r.sessions()['sessions'][0]
    assert stored['stored_state']=='PAUSED'
    assert stored['counts']['VETO']==2 and stored['counts']['PAUSED']==1
    configure('terrestrial',False)
    restarted=PaperForwardRunner(g,c.quote,lambda:c.now)
    assert not restarted.status()['enabled']
    restarted.prepare('terrestrial')
    c.now+=5;c.price=102
    assert restarted.single_cycle()['decision']=='SELL'
    assert not g.recovery_report()['execution_account']['account']['positions']
    restarted.stop()


def test_session_limit_blocks_start_without_auto_deletion(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.stop()
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        base=db.execute('SELECT started_at,updated_at,state,policy_json,counts_json,last_json FROM sessions LIMIT 1').fetchone()
        db.executemany('INSERT INTO sessions VALUES (?,?,?,?,?,?,?)',[(f'old_{i}',*base) for i in range(999)])
    with pytest.raises(ValueError,match='SESSION_HISTORY_CAP'):r.prepare('terrestrial')
    assert not r.status()['enabled']
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==1000


def test_monitoring_and_policy_apis_are_read_only(tmp_path,monkeypatch):
    import nexus.app as backend
    from fastapi.testclient import TestClient
    r,g,c=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(backend,'paper_forward_runner',r)
    client=TestClient(backend.app)
    assert client.get('/api/paper/forward/sessions').json()['read_only']
    assert client.get('/api/paper/forward/policies').json()['research_promotes_automatically'] is False
    assert client.post('/api/paper/forward/policies',json={}).status_code==405
    assert client.get('/api/paper/forward/sessions?limit=101').status_code==422
    assert client.post('/api/paper/forward/start',json=dict(tier='terrestrial',confirm_experimental_paper=True,entry_threshold_bps=0)).status_code==422
    assert not g.recovery_report()['orders']
    r.stop()
