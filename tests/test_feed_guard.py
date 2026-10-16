import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.feed_guard import FeedGuard

def q(last=100,bid=99.9,ask=100.1,received=1000,product="BTC-USD"):
    return {"provider":"coinbase_exchange_public","product":product,"read_only":True,
            "orders_allowed":False,"bid":bid,"ask":ask,"last":last,"received_at":received}

def test_good_readonly_quote_not_executable():
    d=FeedGuard().evaluate(q(),now=1001)
    assert d["decision"]=="READ_ONLY_OBSERVATION"
    assert d["live_orders_enabled"] is False
    assert d["independent_price_verification"] is False

def test_stale_spread_crossed_and_source_veto():
    g=FeedGuard()
    assert "QUOTE_TOO_OLD" in g.evaluate(q(),now=1040)["reasons"]
    assert "EXCESSIVE_SPREAD" in g.evaluate(q(bid=95,ask=105),now=1001)["reasons"]
    assert "CROSSED_MARKET" in g.evaluate(q(bid=101,ask=100),now=1001)["reasons"]
    altered=q();altered["read_only"]=False
    assert "UNVERIFIED_READ_ONLY_PROVENANCE" in g.evaluate(altered,now=1001)["reasons"]

def test_large_jump_veto_but_does_not_poison_baseline():
    g=FeedGuard()
    assert g.evaluate(q(),now=1001)["decision"]=="READ_ONLY_OBSERVATION"
    d=g.evaluate(q(last=150,bid=149.9,ask=150.1,received=1002),now=1002)
    assert "UNCONFIRMED_PRICE_JUMP" in d["reasons"]
    assert g.evaluate(q(last=101,bid=100.9,ask=101.1,received=1003),now=1003)["decision"]=="READ_ONLY_OBSERVATION"

def test_feed_guard_endpoint_is_present_without_broker():
    c=TestClient(app)
    assert c.get("/api/public-quote/NONEXISTENT/quality").status_code==404
    assert c.get("/api/status").json()["live_orders_enabled"] is False
