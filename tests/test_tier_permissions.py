import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from nexus.app import app
from nexus import app as backend
from nexus.tier_permissions import current,configure,require_entry
from nexus.tier_performance import tier_performance


class EqualText(str):
    def __eq__(self,other):return True
    def __ne__(self,other):return False


@pytest.mark.parametrize('notional',(
    0,-1,True,None,'10',float('nan'),float('inf'),
    Decimal('NaN'),Decimal('Infinity')))
def test_invalid_entry_value_fails_before_settings_or_permission_read(
        tmp_path,monkeypatch,notional):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    monkeypatch.setattr('nexus.tier_permissions.current',
        lambda:pytest.fail('tier permission state read'))
    with pytest.raises(ValueError,match='^RISK_TIER_INTENT_INVALID$'):
        require_entry('terrestrial',notional)
    assert not tuple(tmp_path.iterdir())


def test_type_confused_tier_fails_before_notional_or_settings_read(
        tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    monkeypatch.setattr('nexus.tier_permissions.current',
        lambda:pytest.fail('tier permission state read'))
    with pytest.raises(ValueError,match='^UNKNOWN_RISK_TIER$'):
        require_entry(EqualText('terrestrial'),10)
    assert not tuple(tmp_path.iterdir())

def test_new_install_terrestrial_preset_and_caps_are_independent(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    policies=current()["tiers"]
    assert len(policies)==3
    assert {x["tier"] for x in policies if x["enabled"]}=={"terrestrial"}
    require_entry("terrestrial",10)
    configure("terrestrial",False)
    with pytest.raises(ValueError,match="DISENGAGED"):
        require_entry("terrestrial",10)
    configure("solar",True)
    assert {x["tier"] for x in current()["tiers"] if x["enabled"]}=={"solar"}
    require_entry("solar",100)
    with pytest.raises(ValueError,match="CAP"):
        require_entry("solar",201)
    with pytest.raises(ValueError,match="DISENGAGED"):
        require_entry("universal",10)


def test_entry_admission_ignores_stale_permission_read_after_disable(
        tmp_path, monkeypatch):
    import sqlite3
    from nexus import tier_permissions
    monkeypatch.setenv('NEXUS_PORTABLE_DATA', str(tmp_path))
    configure('terrestrial', True)
    stale = current()
    with sqlite3.connect(tmp_path / 'portal_settings.sqlite3') as db, db:
        db.execute("UPDATE tier_permissions SET enabled=0 WHERE tier='terrestrial'")

    # Reproduce the former split-read race: a stale enabled result is still
    # available after the durable OFF commit. Admission must use one fresh
    # settings-and-permission snapshot, never stale current() plus later caps.
    monkeypatch.setattr(tier_permissions, 'current', lambda: stale)
    original_snapshot = tier_permissions._policy_snapshot
    calls = 0

    def counted_snapshot():
        nonlocal calls
        calls += 1
        return original_snapshot()

    monkeypatch.setattr(tier_permissions, '_policy_snapshot', counted_snapshot)
    with pytest.raises(ValueError, match='^RISK_TIER_DISENGAGED$'):
        tier_permissions.require_entry('terrestrial', 10)
    assert calls == 1

def test_tier_buy_is_blocked_unless_engaged_and_portal_active(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend.observer,"start",lambda:None)
    monkeypatch.setattr(backend.refresh_worker,"start",lambda *a,**k:None)
    monkeypatch.setattr(backend.observer,"stop",lambda:None)
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend,"_paused",True)
    c=TestClient(app)
    body={"client_order_id":"policy_1","product":"BTC-USD","side":"BUY","notional":10,
          "provider":"nexus_local_paper","tier":"terrestrial"}
    assert c.post("/api/execution/intent/simulate",json=body).status_code==409
    assert c.put("/api/tiers/terrestrial/permission",json={"enabled":True}).status_code==200
    assert c.post("/api/execution/intent/simulate",json=body).status_code==409
    assert c.post("/api/portal",json={"enabled":True}).status_code==200
    result=c.post("/api/execution/intent/simulate",json=body)
    assert result.status_code==200
    assert result.json()["tier"]=="terrestrial"
    assert result.json()["sent_to_broker"] is False
    assert c.put("/api/tiers/terrestrial/permission",json={"enabled":False}).status_code==200
    assert c.post("/api/execution/intent/simulate",json={**body,"client_order_id":"policy_2"}).status_code==422
    assert c.post("/api/execution/intent/simulate",json={**body,"client_order_id":"policy_exit","side":"SELL"}).status_code==200
    c.post("/api/simulate/halt")
    assert c.put("/api/tiers/solar/permission",json={"enabled":True}).status_code==409
    c.post("/api/simulate/halt/reset",json={"confirm_reset":True})
    c.post("/api/portal",json={"enabled":False})


@pytest.mark.parametrize('notional',[True,'1',None,[],{}])
def test_simulated_order_notional_rejects_coercion_without_journal_write(tmp_path,monkeypatch,notional):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));client=TestClient(app)
    before=tuple(tmp_path.iterdir())
    response=client.post('/api/execution/intent/simulate',json={
        'client_order_id':'strict_notional','product':'BTC-USD','side':'BUY',
        'notional':notional,'provider':'nexus_local_paper','tier':'terrestrial'})
    assert response.status_code==422 and tuple(tmp_path.iterdir())==before


@pytest.mark.parametrize('notional',[1,1.5])
def test_simulated_order_notional_accepts_json_numbers(tmp_path,monkeypatch,notional):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    monkeypatch.setattr(backend,'_halted',False);monkeypatch.setattr(backend,'_paused',True)
    monkeypatch.setattr(backend.observer,'start',lambda:None)
    monkeypatch.setattr(backend.refresh_worker,'start',lambda *a,**k:None)
    monkeypatch.setattr(backend.observer,'stop',lambda:None)
    client=TestClient(app);client.post('/api/portal',json={'enabled':True})
    response=client.post('/api/execution/intent/simulate',json={
        'client_order_id':'numeric_notional','product':'BTC-USD','side':'SELL',
        'notional':notional,'provider':'nexus_local_paper','tier':'terrestrial'})
    assert response.status_code==200

def test_performance_readout_fails_closed_without_valid_history(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    r=tier_performance("BTC-USD")
    assert r["predicted_success_rate_pct"] is None
    assert r["projected_gain_per_minute_pct"] is None
    assert r["projected_gain_per_day_pct"] is None
    assert r["available"] is False
    assert r["live_orders_enabled"] is False
    c=TestClient(app)
    assert c.get("/api/tiers/performance?product=BTC-USD").status_code==200
    assert c.get("/api/tiers/performance?product=NOT-REAL").status_code==422

def test_controls_and_historical_labels_in_portal():
    html=TestClient(app).get("/portal").text
    assert html.count("<script>")==html.count("</script>")==1
    assert html.count("</html>")==1
    assert 'id="tier-engagement"' in html
    assert "Engage Research Risk Tiers" not in html or "tier-engagement" in html
    assert "HISTORICAL WINS" in html
    assert "NO RELIABLE ESTIMATE" in html
    assert "Not a prediction" in html

def test_paper_ledger_buy_requires_selected_engaged_tier(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend,"_paused",True)
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend.observer,"start",lambda:None)
    monkeypatch.setattr(backend.refresh_worker,"start",lambda *a,**k:None)
    monkeypatch.setattr(backend.observer,"stop",lambda:None)
    quote={"product":"BTC-USD","read_only":True,"orders_allowed":False,
           "provider":"coinbase_exchange_public","received_at":__import__("time").time(),
           "bid":100.0,"ask":100.05}
    monkeypatch.setattr(backend,"read_ticker",lambda product:dict(quote))
    c=TestClient(app)
    assert c.post("/api/paper/portfolio/init",json={"paper_capital":10000}).status_code==200
    req={"product":"BTC-USD","notional":25,"tier":"terrestrial"}
    assert c.post("/api/paper/portfolio/buy",json=req).status_code==409
    c.post("/api/portal",json={"enabled":True})
    assert c.post("/api/paper/portfolio/buy",json=req).status_code==422
    assert c.post("/api/paper/portfolio/buy",json={"product":"BTC-USD","notional":25}).status_code==422
    assert c.put("/api/tiers/solar/permission",json={"enabled":True}).status_code==200
    assert c.post("/api/paper/portfolio/buy",json=req).status_code==422
    assert c.put("/api/tiers/terrestrial/permission",json={"enabled":True}).status_code==200
    approved=c.post("/api/paper/portfolio/buy",json=req)
    assert approved.status_code==200
    assert len(approved.json()["positions"])==1
    assert approved.json()["live_orders_enabled"] is False
    c.put("/api/tiers/terrestrial/permission",json={"enabled":False})
    assert c.post("/api/paper/portfolio/sell",json={"product":"BTC-USD"}).status_code==200
    c.post("/api/portal",json={"enabled":False})


def test_tier_off_committed_during_direct_buy_vetoes_ledger_commit(
        tmp_path,monkeypatch):
    """An OFF commit between route admission and ledger mutation must win."""
    import threading
    from nexus import portfolio_governor

    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend,"_paused",False)
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend,"_cut_confirmed",False)
    quote={"product":"BTC-USD","read_only":True,"orders_allowed":False,
           "provider":"coinbase_exchange_public","received_at":__import__("time").time(),
           "bid":100.0,"ask":100.05}
    monkeypatch.setattr(backend,"read_ticker",lambda product:dict(quote))
    client=TestClient(app)
    assert client.post("/api/paper/portfolio/init",
                       json={"paper_capital":10000}).status_code==200
    configure("terrestrial",True)

    admitted=threading.Event();release=threading.Event()
    original=portfolio_governor.require_safe_ledger

    def hold_after_ledger_writer_reservation(db):
        admitted.set()
        assert release.wait(2)
        return original(db)

    monkeypatch.setattr(portfolio_governor,"require_safe_ledger",
                        hold_after_ledger_writer_reservation)
    result={}
    worker=threading.Thread(target=lambda:result.update(response=client.post(
        "/api/paper/portfolio/buy",json={"product":"BTC-USD","notional":10,
                                         "tier":"terrestrial"})))
    worker.start()
    try:
        assert admitted.wait(2)
        configure("terrestrial",False)
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert result["response"].status_code==422
    assert result["response"].json()=={"detail":"RISK_TIER_DISENGAGED"}
    snapshot=client.get("/api/paper/portfolio").json()
    assert snapshot["positions"]==[] and snapshot["event_count"]==1


def test_direct_buy_writer_reservation_holds_tier_permission_until_commit(
        tmp_path,monkeypatch):
    """Once BUY owns both writers, OFF waits for its durable commit."""
    import threading
    from nexus import paper_portfolio

    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend,"_paused",False)
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend,"_cut_confirmed",False)
    quote={"product":"BTC-USD","read_only":True,"orders_allowed":False,
           "provider":"coinbase_exchange_public","received_at":__import__("time").time(),
           "bid":100.0,"ask":100.05}
    monkeypatch.setattr(backend,"read_ticker",lambda product:dict(quote))
    client=TestClient(app)
    assert client.post("/api/paper/portfolio/init",
                       json={"paper_capital":10000}).status_code==200
    configure("terrestrial",True)

    writers_held=threading.Event();release=threading.Event()
    disable_started=threading.Event();disable_finished=threading.Event()
    original=paper_portfolio.verify_ledger

    def hold_with_both_writer_reservations(db):
        writers_held.set()
        assert release.wait(2)
        return original(db)

    monkeypatch.setattr(paper_portfolio,"verify_ledger",
                        hold_with_both_writer_reservations)
    result={}
    buyer=threading.Thread(target=lambda:result.update(response=client.post(
        "/api/paper/portfolio/buy",json={"product":"BTC-USD","notional":10,
                                         "tier":"terrestrial"})))

    def disable():
        disable_started.set()
        configure("terrestrial",False)
        disable_finished.set()

    buyer.start()
    disabler=None
    try:
        assert writers_held.wait(2)
        disabler=threading.Thread(target=disable);disabler.start()
        assert disable_started.wait(2)
        assert not disable_finished.wait(.1)
    finally:
        release.set()
        buyer.join(2)
        if disabler is not None:disabler.join(2)
    assert not buyer.is_alive() and not disabler.is_alive()
    assert result["response"].status_code==200
    assert disable_finished.is_set()
    assert not next(row for row in current()["tiers"]
                    if row["tier"]=="terrestrial")["enabled"]
    snapshot=client.get("/api/paper/portfolio").json()
    assert len(snapshot["positions"])==1 and snapshot["event_count"]==2


def test_direct_buy_quote_expiring_during_writer_wait_rolls_back(
        tmp_path,monkeypatch):
    """A quote that ages out while both BUY writers are held cannot commit."""
    from nexus import paper_portfolio

    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend,"_paused",False)
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend,"_cut_confirmed",False)
    clock={"now":100.0}
    monkeypatch.setattr(paper_portfolio.time,"time",lambda:clock["now"])
    quote={"product":"BTC-USD","read_only":True,"orders_allowed":False,
           "provider":"coinbase_exchange_public","received_at":100.0,
           "bid":100.0,"ask":100.05}
    monkeypatch.setattr(backend,"read_ticker",lambda product:dict(quote))
    client=TestClient(app)
    assert client.post("/api/paper/portfolio/init",
                       json={"paper_capital":10000}).status_code==200
    configure("terrestrial",True)

    original=paper_portfolio.verify_ledger

    def age_quote_after_protected_ledger_audit(db):
        cash=original(db)
        clock["now"]=111.0
        return cash

    monkeypatch.setattr(paper_portfolio,"verify_ledger",
                        age_quote_after_protected_ledger_audit)
    response=client.post("/api/paper/portfolio/buy",json={
        "product":"BTC-USD","notional":10,"tier":"terrestrial"})

    assert response.status_code==422
    assert response.json()=={"detail":"Quote too old"}
    snapshot=client.get("/api/paper/portfolio").json()
    assert snapshot["positions"]==[] and snapshot["event_count"]==1


def test_direct_buy_cut_during_quote_revalidation_rolls_back(
        tmp_path,monkeypatch):
    """A cut published inside the protected quote check must veto the BUY."""
    from nexus import emergency_intent,paper_portfolio

    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend,"_paused",False)
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend,"_cut_confirmed",False)
    quote={"product":"BTC-USD","read_only":True,"orders_allowed":False,
           "provider":"coinbase_exchange_public",
           "received_at":__import__("time").time(),
           "bid":100.0,"ask":100.05}
    monkeypatch.setattr(backend,"read_ticker",lambda product:dict(quote))
    client=TestClient(app)
    assert client.post("/api/paper/portfolio/init",
                       json={"paper_capital":10000}).status_code==200
    configure("terrestrial",True)

    original=paper_portfolio.check_quote
    calls={"count":0}

    def publish_cut_after_protected_quote_validation(product,value):
        result=original(product,value)
        calls["count"]+=1
        if calls["count"]==2:
            emergency_intent.path(
                backend.research_order_gateway._path()).write_bytes(
                    emergency_intent.RECORD)
        return result

    monkeypatch.setattr(paper_portfolio,"check_quote",
                        publish_cut_after_protected_quote_validation)
    response=client.post("/api/paper/portfolio/buy",json={
        "product":"BTC-USD","notional":10,"tier":"terrestrial"})

    assert calls["count"]==2
    assert response.status_code==409
    assert response.json()=={"detail":"EMERGENCY_CUT_INTERRUPTED_TRADE"}
    snapshot=client.get("/api/paper/portfolio").json()
    assert snapshot["positions"]==[] and snapshot["event_count"]==1
    assert emergency_intent.requested(backend.research_order_gateway._path())


def test_standalone_cut_during_direct_buy_vetoes_ledger_commit(
        tmp_path,monkeypatch):
    """A durable cut asserted after both BUY writer reservations must win."""
    import threading
    from nexus import emergency_intent,paper_portfolio

    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend,"_paused",False)
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend,"_cut_confirmed",False)
    quote={"product":"BTC-USD","read_only":True,"orders_allowed":False,
           "provider":"coinbase_exchange_public","received_at":__import__("time").time(),
           "bid":100.0,"ask":100.05}
    monkeypatch.setattr(backend,"read_ticker",lambda product:dict(quote))
    client=TestClient(app)
    assert client.post("/api/paper/portfolio/init",
                       json={"paper_capital":10000}).status_code==200
    configure("terrestrial",True)

    writers_held=threading.Event();release=threading.Event()
    original=paper_portfolio.verify_ledger

    def hold_with_both_writer_reservations(db):
        writers_held.set()
        assert release.wait(2)
        return original(db)

    monkeypatch.setattr(paper_portfolio,"verify_ledger",
                        hold_with_both_writer_reservations)
    result={}
    buyer=threading.Thread(target=lambda:result.update(response=client.post(
        "/api/paper/portfolio/buy",json={"product":"BTC-USD","notional":10,
                                         "tier":"terrestrial"})))
    buyer.start()
    try:
        assert writers_held.wait(2)
        intent_path=emergency_intent.path(backend.research_order_gateway._path())
        intent_path.write_bytes(emergency_intent.RECORD)
    finally:
        release.set()
        buyer.join(2)
    assert not buyer.is_alive()
    assert result["response"].status_code==409
    assert result["response"].json()=={
        "detail":"EMERGENCY_CUT_INTERRUPTED_TRADE"}
    snapshot=client.get("/api/paper/portfolio").json()
    assert snapshot["positions"]==[] and snapshot["event_count"]==1
    assert emergency_intent.requested(backend.research_order_gateway._path())

def test_emergency_cut_keeps_reducing_paper_exit_available(tmp_path,monkeypatch):
    from nexus import emergency_intent
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend,"_paused",False)
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend,"_cut_confirmed",False)
    monkeypatch.setattr(backend.observer,"start",lambda:None)
    monkeypatch.setattr(backend.refresh_worker,"start",lambda *a,**k:None)
    monkeypatch.setattr(backend.observer,"stop",lambda:None)
    quote={"product":"BTC-USD","read_only":True,"orders_allowed":False,
           "provider":"coinbase_exchange_public","received_at":__import__("time").time(),
           "bid":100.0,"ask":100.05}
    monkeypatch.setattr(backend,"read_ticker",lambda product:dict(quote))
    client=TestClient(app)
    assert client.post("/api/paper/portfolio/init",json={"paper_capital":10000}).status_code==200
    configure("terrestrial",True)
    buy={"product":"BTC-USD","notional":10,"tier":"terrestrial"}
    assert client.post("/api/paper/portfolio/buy",json=buy).status_code==200
    emergency_intent.path(backend.research_order_gateway._path()).write_bytes(emergency_intent.RECORD)
    assert client.post("/api/paper/portfolio/buy",json=buy).status_code==409
    closed=client.post("/api/paper/portfolio/sell",json={"product":"BTC-USD"})
    assert closed.status_code==200 and closed.json()["positions"]==[]
    assert backend._execution_halted() is True

def test_emergency_cut_exit_still_fails_closed_on_invalid_quote(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    monkeypatch.setattr(backend,"_halted",False)
    monkeypatch.setattr(backend,"_paused",False)
    good={"product":"BTC-USD","read_only":True,"orders_allowed":False,
          "provider":"coinbase_exchange_public","received_at":__import__("time").time(),
          "bid":100.0,"ask":100.05}
    monkeypatch.setattr(backend,"read_ticker",lambda product:dict(good))
    client=TestClient(app)
    client.post("/api/paper/portfolio/init",json={"paper_capital":10000})
    configure("terrestrial",True)
    assert client.post("/api/paper/portfolio/buy",json={"product":"BTC-USD","notional":10,"tier":"terrestrial"}).status_code==200
    monkeypatch.setattr(backend,"_halted",True)
    monkeypatch.setattr(backend,"read_ticker",lambda product:(_ for _ in ()).throw(ValueError("QUOTE_STALE")))
    assert client.post("/api/paper/portfolio/sell",json={"product":"BTC-USD"}).status_code==422
    assert len(client.get("/api/paper/portfolio").json()["positions"])==1

def test_ledger_ui_has_explicit_tier_selector():
    html=TestClient(app).get("/portal").text
    assert 'id="papertier"' in html
    assert "tier:byId('papertier').value" in html


def test_tier_writer_contention_never_enables_and_recovers(tmp_path,monkeypatch):
    import sqlite3
    from nexus import state,tier_permissions
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));current()
    path=tmp_path/'portal_settings.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(state,'DB_BUSY_TIMEOUT_MS',25)
    monkeypatch.setattr(tier_permissions,'DB_BUSY_TIMEOUT_MS',25)
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError,match='^PAPER_SETTINGS_DATABASE_BUSY$'):configure('solar',True)
        assert path.read_bytes()==before
    assert not next(row for row in current()['tiers'] if row['tier']=='solar')['enabled']
    assert next(row for row in configure('solar',True)['tiers'] if row['tier']=='solar')['enabled']


def test_tier_reader_blocked_commit_rolls_back_and_recovers(tmp_path,monkeypatch):
    import sqlite3
    from nexus import state,tier_permissions
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));current()
    path=tmp_path/'portal_settings.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(state,'DB_BUSY_TIMEOUT_MS',25)
    monkeypatch.setattr(tier_permissions,'DB_BUSY_TIMEOUT_MS',25)
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN');blocker.execute('SELECT COUNT(*) FROM tier_permissions').fetchone()
        with pytest.raises(ValueError,match='^PAPER_SETTINGS_DATABASE_BUSY$'):configure('solar',True)
        assert path.read_bytes()==before
    assert next(row for row in configure('solar',True)['tiers'] if row['tier']=='solar')['enabled']


def test_tier_exclusive_contention_is_stable_at_api_and_recovers(tmp_path,monkeypatch):
    import sqlite3
    from nexus import state,tier_permissions
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));expected=current()
    path=tmp_path/'portal_settings.sqlite3';before=path.read_bytes()
    monkeypatch.setattr(state,'DB_BUSY_TIMEOUT_MS',25)
    monkeypatch.setattr(tier_permissions,'DB_BUSY_TIMEOUT_MS',25)
    with sqlite3.connect(path) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match='^PAPER_SETTINGS_DATABASE_BUSY$'):current()
        reply=TestClient(app).get('/api/tiers/permissions')
        assert reply.status_code==503 and reply.json()=={'detail':'PAPER_SETTINGS_DATABASE_BUSY'}
        assert path.read_bytes()==before
    assert current()==expected

