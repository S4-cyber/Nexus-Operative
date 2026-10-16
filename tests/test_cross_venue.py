import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.cross_venue import corroborate, parse_kraken, crosscheck, fetch_kraken

def coin(base=100,now=1000):
    return {"product":"BTC-USD","provider":"coinbase_exchange_public",
        "bid":base-0.1,"ask":base+0.1,"received_at":now,
        "read_only":True,"orders_allowed":False}
def kraken(base=100,now=1001):
    return {"product":"BTC-USD","provider":"kraken_spot_public",
        "bid":str(base-0.1),"ask":str(base+0.1),"received_at":now,
        "read_only":True,"orders_allowed":False,"has_exchange_timestamp":False}

def test_corrob_reports_provisional_not_verified():
    result=corroborate("BTC-USD",coin(),kraken(),now=1002)
    assert result["decision"]=="PROVISIONAL_AGREEMENT"
    assert result["trade_approval"] is False
    assert result["exchange_timestamps_verified"] is False

def test_divergence_stale_and_capture_time_skew_veto():
    assert "CROSS_VENUE_PRICE_DIVERGENCE" in corroborate("BTC-USD",coin(),kraken(104),now=1002)["issues"]
    assert "coinbase_exchange_public:STALE_RECEIPT" in corroborate("BTC-USD",coin(now=960),kraken(),now=1002)["issues"]
    assert "CAPTURE_TIME_SKEW" in corroborate("BTC-USD",coin(now=990),kraken(),now=1002)["issues"]

def test_invalid_provider_and_crossed_quote_fail_closed():
    forged=kraken();forged["provider"]="another"
    assert corroborate("BTC-USD",coin(),forged,now=1002)["decision"]=="VETO"
    assert corroborate("BTC-USD",coin(),None,now=1002)["decision"]=="VETO"
    crossed=kraken();crossed["bid"]="120"
    assert corroborate("BTC-USD",coin(),crossed,now=1002)["decision"]=="VETO"

def test_kraken_parser_allowlist_and_fixed_transport():
    payload={"error":[],"result":{"XXBTZUSD":{"a":["100.1","1"],"b":["99.9","1"]}}}
    result=parse_kraken("BTC-USD",payload,received_at=1000)
    assert result["bid"]=="99.9"
    assert result["has_exchange_timestamp"] is False
    quote=fetch_kraken("BTC-USD",transport=lambda url: payload,now=1000)
    assert quote["provider"]=="kraken_spot_public"
    with pytest.raises(ValueError):fetch_kraken("SHIB-USD",transport=lambda _: payload)
    with pytest.raises(ValueError):parse_kraken("BTC-USD",{"error":["fail"],"result":{}})

def test_read_only_crosscheck_no_provider_fails_closed():
    report=crosscheck("BTC-USD",coinbase_reader=lambda _:coin(),kraken_reader=lambda _:(_ for _ in ()).throw(ValueError("offline")))
    assert report["decision"]=="VETO"
    assert report["trade_approval"] is False
    assert TestClient(app).get("/api/public-quote/INVALID/cross-venue").status_code==404
    assert TestClient(app).get("/api/status").json()["live_orders_enabled"] is False
