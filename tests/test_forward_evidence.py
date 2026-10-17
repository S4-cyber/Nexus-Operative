import json
import sqlite3
import pytest
from test_paper_forward import setup,warmup
from nexus import forward_evidence


def test_quote_fill_review_separates_injected_source(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    assert warmup(r,c)['decision']=='BUY'
    c.now+=5;c.price=102
    assert r.single_cycle()['decision']=='SELL'
    r.stop()
    report=r.evidence_report(r.status()['session'])
    assert report['local_consistency_ok']
    assert report['quote_evidence']['observations']==8
    assert report['quote_evidence']['source_modes']==['INJECTED_TRANSPORT_UNATTESTED']
    assert report['execution_crosscheck']['modeled_buy_orders']==1
    assert report['execution_crosscheck']['modeled_sell_orders']==1
    assert not report['release_approved'] and not report['strategy_validated']
    assert not report['provisional_active_session']


def test_quote_payload_edit_and_tail_deletion_detected(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);warmup(r,c);r.stop()
    session=r.status()['session']
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        raw=db.execute('SELECT payload FROM forward_quotes WHERE cycle=1').fetchone()[0]
        data=json.loads(raw);data['quote']['bid']=1
        db.execute('UPDATE forward_quotes SET payload=? WHERE cycle=1',(json.dumps(data),))
        db.execute('DELETE FROM forward_quotes WHERE cycle=7')
    report=r.evidence_report(session)
    assert not report['local_consistency_ok']
    assert 'QUOTE_CHAIN_DISAGREEMENT' in report['reasons']
    assert 'QUOTE_HEAD_DISAGREEMENT' in report['reasons']


def test_failed_evidence_write_blocks_entry(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        db.execute("CREATE TRIGGER fail_quote BEFORE INSERT ON forward_quotes BEGIN SELECT RAISE(ABORT,'failure'); END")
    assert r.single_cycle()['decision']=='PAUSED'
    assert not r.status()['enabled']
    assert not g.recovery_report()['orders']


def test_evidence_limit_pauses_without_deleting_rows(tmp_path,monkeypatch):
    monkeypatch.setattr(forward_evidence,'LIMIT',1)
    r,g,c=setup(tmp_path,monkeypatch)
    r.single_cycle();c.now+=5
    assert r.single_cycle()['decision']=='PAUSED'
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM forward_quotes').fetchone()[0]==1


def test_sensitive_extra_quote_fields_are_not_retained(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch)
    def fetch(product):return {**c.quote(product),'api_key':'never-store-this','account':'private'}
    r.fetcher=fetch;r.single_cycle()
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        raw=db.execute('SELECT payload FROM forward_quotes').fetchone()[0]
    assert 'never-store-this' not in raw and 'private' not in raw


def test_counter_disagreement_is_reported_not_repaired(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);warmup(r,c);r.stop()
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        raw=db.execute('SELECT counts_json FROM sessions').fetchone()[0]
        counts=json.loads(raw);counts['BUY']=0
        db.execute('UPDATE sessions SET counts_json=?',(json.dumps(counts),))
    report=r.evidence_report(r.status()['session'])
    assert 'SESSION_COUNTER_FILL_DISAGREEMENT' in report['reasons']
    assert g.recovery_report()['execution_account']['account']['positions']


def test_quote_head_corruption_blocks_new_observations(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle()
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        db.execute("UPDATE forward_quote_heads SET head='bad'")
    c.now+=5
    assert r.single_cycle()['decision']=='PAUSED'
    assert not r.status()['enabled']
    assert not g.recovery_report()['orders']


def test_api_evidence_read_and_unknown_session(tmp_path,monkeypatch):
    import nexus.app as backend
    from fastapi.testclient import TestClient
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle();r.stop()
    monkeypatch.setattr(backend,'paper_forward_runner',r)
    client=TestClient(backend.app);sid=r.status()['session']
    assert client.get('/api/paper/forward/sessions/'+sid+'/evidence').json()['read_only']
    assert client.get('/api/paper/forward/sessions/not_valid/evidence').status_code==422
    assert client.get('/api/paper/forward/sessions/'+'f'*16+'/evidence').status_code==404


def test_changed_session_policy_is_flagged(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle();r.stop()
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        policy=json.loads(db.execute('SELECT policy_json FROM sessions').fetchone()[0])
        policy['entry_threshold_bps']=0
        db.execute('UPDATE sessions SET policy_json=?',(json.dumps(policy),))
    report=r.evidence_report(r.status()['session'])
    assert 'SESSION_POLICY_FINGERPRINT_DISAGREEMENT' in report['reasons']
    assert not report['local_consistency_ok']
