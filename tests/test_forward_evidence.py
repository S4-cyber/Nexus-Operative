import json
import sqlite3
import pytest
from test_paper_forward import setup,warmup
from nexus import emergency_intent,forward_evidence,forward_event_evidence


def event_record(**changes):
    row=dict(at=1,clock_sample_valid=True,session='1'*16,cycle=0,
        quote_evidence_sha256=None,decision='START',reason='TEST',product='BTC-USD',
        tier='terrestrial',simulation_only=True,policy_id='fixture',policy_sha256='a'*64)
    row.update(changes);return row


def test_event_chain_preserves_two_records_on_one_cycle():
    with sqlite3.connect(':memory:') as db:
        forward_event_evidence.schema(db)
        forward_event_evidence.append(db,event_record())
        forward_event_evidence.append(db,event_record(decision='HOLD',reason='SCHEDULER_OVERRUN'))
        report=forward_event_evidence.review(db,'1'*16)
    assert report['integrity_ok'] and report['events']==2
    assert [point['sequence'] for point in report['points']]==[1,2]
    assert [point['ordinal'] for point in report['points']]==[1,2]
    assert report['points'][1]['kind']=='SCHEDULER'


def test_event_head_corruption_blocks_append():
    with sqlite3.connect(':memory:') as db:
        forward_event_evidence.schema(db);forward_event_evidence.append(db,event_record())
        db.execute("UPDATE forward_event_heads SET head='bad'")
        with pytest.raises(ValueError,match='FORWARD_EVENT_HEAD_CORRUPT'):
            forward_event_evidence.append(db,event_record(decision='HOLD'))


def test_event_payload_size_is_bounded():
    with sqlite3.connect(':memory:') as db:
        forward_event_evidence.schema(db)
        with pytest.raises(ValueError,match='FORWARD_EVENT_(SCHEMA|PAYLOAD_TOO_LARGE)'):
            forward_event_evidence.append(db,event_record(reason='x'*5000))


def test_unknown_event_decision_is_rejected():
    with sqlite3.connect(':memory:') as db:
        forward_event_evidence.schema(db)
        with pytest.raises(ValueError,match='FORWARD_EVENT_SCHEMA'):
            forward_event_evidence.append(db,event_record(decision='UNKNOWN'))


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
    assert report['order_journal_snapshot']['stable_during_review']
    assert report['order_journal_snapshot']['commands']>0
    assert report['order_journal_snapshot']['portfolio_linked']
    assert report['order_journal_snapshot']['portfolio_rows']>0
    assert report['order_journal_snapshot']['journal_stable_during_review']
    assert report['order_journal_snapshot']['portfolio_stable_during_review']
    assert len(report['order_journal_snapshot']['snapshot_sha256'])==64
    events=report['durable_event_evidence']
    assert events['integrity_ok'] and events['event_trace_complete']
    assert events['quote_outcome_bijection'] and events['durable_order_outcomes_reconciled']
    assert events['quote_outcomes']==8 and events['modeled_order_outcomes']==2
    assert events['events']==10 and events['lifecycle_events']==2
    assert len(events['head_sha256'])==len(events['reconciliation_sha256'])==64
    receipt=report['review_receipt']
    assert receipt['profile']=='nexus.forward-session-review-receipt.v1'
    assert receipt['local_consistency_ok'] and receipt['reason_count']==0
    assert receipt['order_snapshot_stable_during_review']
    assert receipt['initial_order_snapshot_sha256']==receipt['final_order_snapshot_sha256']
    assert receipt['quote_head_sha256']==report['quote_evidence']['head_sha256']
    assert receipt['event_head_sha256']==events['head_sha256']
    assert receipt['event_reconciliation_sha256']==events['reconciliation_sha256']
    assert len(receipt['session_summary_sha256'])==64
    assert len(receipt['reason_codes_sha256'])==len(receipt['receipt_sha256'])==64
    assert not receipt['live_orders_enabled'] and not receipt['release_approved']


def _rewrite_event_chain(db,mutate):
    rows=db.execute('SELECT session,sequence,cycle,ordinal,kind,payload FROM forward_events ORDER BY session,sequence').fetchall()
    previous_by_session={};last={}
    for session,sequence,cycle,ordinal,kind,payload in rows:
        data=json.loads(payload);mutate(data)
        payload=forward_event_evidence.canonical(data)
        previous=previous_by_session.get(session,forward_event_evidence.GENESIS)
        digest=forward_event_evidence.digest(previous,payload)
        db.execute('UPDATE forward_events SET payload=?,previous=?,digest=? WHERE session=? AND sequence=?',
            (payload,previous,digest,session,sequence))
        previous_by_session[session]=digest;last[session]=(sequence,digest)
    for session,(events,head) in last.items():
        db.execute('UPDATE forward_event_heads SET events=?,head=? WHERE session=?',(events,head,session))


def test_self_consistent_wrong_event_order_id_fails_three_way_reconciliation(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);warmup(r,c);r.stop();changed=False
    def mutate(data):
        nonlocal changed
        if not changed and data['record']['decision']=='BUY':
            data['record']['order_id']='pf_'+r.status()['session']+'_999_BUY';changed=True
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:_rewrite_event_chain(db,mutate)
    assert changed
    report=r.evidence_report(r.status()['session'])
    assert report['durable_event_evidence']['event_trace_complete']
    assert not report['durable_event_evidence']['durable_order_outcomes_reconciled']
    assert 'DURABLE_ORDER_OUTCOME_DISAGREEMENT' in report['reasons']
    assert not report['local_consistency_ok']


def test_missing_durable_event_tables_leave_legacy_session_readable_but_incomplete(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle();r.stop()
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        db.execute('DROP TABLE forward_events');db.execute('DROP TABLE forward_event_heads')
    report=r.evidence_report(r.status()['session'])
    assert report['quote_evidence']['observations']==1
    assert not report['durable_event_evidence']['event_trace_complete']
    assert not report['local_consistency_ok']


def test_generic_durable_halt_is_not_promoted_to_independent_cut(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle();g.emergency_halt()
    report=r.evidence_report(r.status()['session'])
    assert not report['durable_event_evidence']['independent_cut_observed']
    assert not report['durable_event_evidence']['cut_binding_verified']
    assert report['order_journal_snapshot']['halted']
    assert 'DURABLE_HALT_WITHOUT_INDEPENDENT_CUT_EVIDENCE' in report['reasons']
    assert not report['local_consistency_ok']


def test_canonical_independent_cut_without_terminal_event_is_bound(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle()
    emergency_intent.persist(g._path());g.emergency_halt()
    report=r.evidence_report(r.status()['session']);events=report['durable_event_evidence']
    assert events['independent_cut_observed'] and events['cut_binding_verified']
    assert events['cut_binding_source']=='CANONICAL_LATCH'
    assert events['cut_state_before']==events['cut_state_after']=='CANONICAL_REQUESTED'
    assert 'INDEPENDENT_CUT_REQUIRES_SESSION_REVIEW' in report['reasons']
    assert not report['local_consistency_ok']


def test_signal_only_cut_is_review_required_before_durable_halt(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);warmup(r,c);r.request_cut()
    report=r.evidence_report(r.status()['session']);events=report['durable_event_evidence']
    assert events['cut_binding_verified'] and events['cut_binding_source']=='PROCESS_SIGNAL'
    assert not events['independent_cut_observed']
    assert 'INDEPENDENT_CUT_WITHOUT_DURABLE_HALT' in report['reasons']
    assert not report['local_consistency_ok']


def test_malformed_latch_is_fail_closed_but_not_verified(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle()
    emergency_intent.path(g._path()).write_bytes(b'broken')
    report=r.evidence_report(r.status()['session']);events=report['durable_event_evidence']
    assert events['cut_state_before']==events['cut_state_after']=='PRESENT_UNVERIFIED'
    assert not events['cut_evidence_stable'] and not events['cut_binding_verified']
    assert 'INDEPENDENT_CUT_EVIDENCE_UNSTABLE' in report['reasons']
    assert not report['local_consistency_ok']


def test_process_signal_cannot_sanitize_malformed_latch(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);warmup(r,c);r.request_cut()
    emergency_intent.path(g._path()).write_bytes(b'broken')
    report=r.evidence_report(r.status()['session']);events=report['durable_event_evidence']
    assert events['cut_state_before']==events['cut_state_after']=='PRESENT_UNVERIFIED'
    assert not events['cut_evidence_stable'] and not events['cut_binding_verified']
    assert events['cut_binding_source']=='NONE'
    assert 'INDEPENDENT_CUT_EVIDENCE_UNSTABLE' in report['reasons']
    assert not report['local_consistency_ok']


def test_later_generic_halt_does_not_taint_stopped_history(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);warmup(r,c);r.stop();g.emergency_halt()
    report=r.evidence_report(r.status()['session'])
    assert report['local_consistency_ok']
    assert not report['durable_event_evidence']['independent_cut_observed']


def test_cut_transition_during_review_is_unstable(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);warmup(r,c)
    original=g.session_orders_snapshot;calls=0
    def signal_after_first(value):
        nonlocal calls
        result=original(value);calls+=1
        if calls==1:r.request_cut()
        return result
    monkeypatch.setattr(g,'session_orders_snapshot',signal_after_first)
    report=r.evidence_report(r.status()['session'])
    assert calls==2 and not report['durable_event_evidence']['cut_evidence_stable']
    assert 'INDEPENDENT_CUT_EVIDENCE_UNSTABLE' in report['reasons']
    assert not report['local_consistency_ok']


def test_order_journal_change_during_review_fails_closed(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);warmup(r,c);r.stop()
    session=r.status()['session'];original=g.session_orders_snapshot;calls=[]
    def raced_snapshot(value):
        snapshot=original(value);calls.append(snapshot['snapshot_sha256'])
        if len(calls)==1:g.emergency_halt()
        return snapshot
    monkeypatch.setattr(g,'session_orders_snapshot',raced_snapshot)
    report=r.evidence_report(session)
    assert len(calls)==2 and calls[0]!=calls[1]
    assert not report['local_consistency_ok']
    assert 'ORDER_JOURNAL_CHANGED_DURING_REVIEW' in report['reasons']
    assert 'LINKED_PORTFOLIO_CHANGED_DURING_REVIEW' not in report['reasons']
    assert not report['order_journal_snapshot']['stable_during_review']
    receipt=report['review_receipt']
    assert not receipt['local_consistency_ok'] and receipt['reason_count']>=1
    assert not receipt['order_snapshot_stable_during_review']
    assert receipt['initial_order_snapshot_sha256']!=receipt['final_order_snapshot_sha256']
    assert receipt['halted_before'] is False and receipt['halted_after'] is True


def test_linked_portfolio_change_during_review_fails_closed(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);warmup(r,c);r.stop()
    session=r.status()['session'];original=g.session_orders_snapshot;calls=[]
    def raced_snapshot(value):
        snapshot=original(value);calls.append(snapshot['snapshot_sha256'])
        if len(calls)==1:
            with sqlite3.connect(tmp_path/'paper_portfolio.sqlite3') as db:
                db.execute("INSERT INTO meta VALUES ('review_fixture','changed')")
        return snapshot
    monkeypatch.setattr(g,'session_orders_snapshot',raced_snapshot)
    report=r.evidence_report(session)
    assert len(calls)==2 and calls[0]!=calls[1]
    assert not report['local_consistency_ok']
    assert 'ORDER_JOURNAL_CHANGED_DURING_REVIEW' not in report['reasons']
    assert 'LINKED_PORTFOLIO_CHANGED_DURING_REVIEW' in report['reasons']
    assert report['order_journal_snapshot']['journal_stable_during_review']
    assert not report['order_journal_snapshot']['portfolio_stable_during_review']
    assert not report['order_journal_snapshot']['stable_during_review']
    receipt=report['review_receipt']
    assert not receipt['order_snapshot_stable_during_review']
    assert receipt['initial_order_snapshot_sha256']!=receipt['final_order_snapshot_sha256']


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


@pytest.mark.parametrize('field,value,reason',[
    ('session','f'*16,'TERMINAL_EVENT_SESSION_DISAGREEMENT'),
    ('policy_sha256','0'*64,'TERMINAL_EVENT_POLICY_DISAGREEMENT'),
    ('simulation_only',False,'TERMINAL_EVENT_SCOPE_DISAGREEMENT'),
    ('clock_sample_valid',False,'TERMINAL_EVENT_CLOCK_UNTRUSTED'),
    ('quote_evidence_sha256','0'*64,'TERMINAL_EVENT_QUOTE_HEAD_DISAGREEMENT'),
    ('cycle',0,'TERMINAL_EVENT_CYCLE_DISAGREEMENT')])
def test_terminal_event_binding_disagreement_is_reported(tmp_path,monkeypatch,field,value,reason):
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle();r.stop()
    with sqlite3.connect(tmp_path/'paper_forward.sqlite3') as db:
        raw=db.execute('SELECT last_json FROM sessions').fetchone()[0]
        last=json.loads(raw);last[field]=value
        db.execute('UPDATE sessions SET last_json=?',(json.dumps(last),))
    report=r.evidence_report(r.status()['session'])
    assert not report['local_consistency_ok'] and reason in report['reasons']
