from datetime import datetime,timezone,timedelta
import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.public_data import parse_ticker,read_ticker

NOW=datetime(2026,10,16,22,0,0,tzinfo=timezone.utc).timestamp()
def payload(seconds=0, bid="100",ask="100.1"):
    return {"time":datetime.fromtimestamp(NOW+seconds,timezone.utc).isoformat(),
        "bid":bid,"ask":ask,"price":"100.05"}

def test_valid_public_quote():
    x=parse_ticker("BTC-USD",payload(),received=NOW+2)
    assert x["read_only"] and not x["orders_allowed"]
    assert x["spread_bps"]>0

def test_stale_crossed_and_future_quotes_rejected():
    with pytest.raises(ValueError):parse_ticker("BTC-USD",payload(),received=NOW+80)
    with pytest.raises(ValueError):parse_ticker("BTC-USD",payload(bid="110",ask="100"),received=NOW)
    with pytest.raises(ValueError):parse_ticker("BTC-USD",payload(seconds=10),received=NOW)

def test_product_allowlist_prevents_ssrf():
    called=[]
    def fetch(url):
        called.append(url)
        return payload()
    with pytest.raises(ValueError):read_ticker("https://example.invalid/",fetch=fetch,now=NOW)
    assert not called
    q=read_ticker("ETH-USD",fetch=fetch,now=NOW)
    assert q["product"]=="ETH-USD"
    assert called==["https://api.exchange.coinbase.com/products/ETH-USD/ticker"]

def test_transport_failure_does_not_return_fake_quote():
    def down(_):raise TimeoutError()
    with pytest.raises(ValueError):read_ticker("BTC-USD",fetch=down,now=NOW)

def test_api_rejects_unknown_product_without_network():
    assert TestClient(app).get("/api/public-quote/INVALID").status_code==404
    assert TestClient(app).get("/api/status").json()["live_orders_enabled"] is False



def test_default_transport_uses_actual_receipt_time(monkeypatch):
    import json
    import nexus.public_data as public
    ticks=iter([NOW,NOW+4])
    monkeypatch.setattr(public.time,'time',lambda:next(ticks))
    monkeypatch.setattr(public,'_cache',{})
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return json.dumps(payload(seconds=3)).encode()
    monkeypatch.setattr(public,'urlopen',lambda *args,**kwargs:Response())
    result=public.read_ticker('BTC-USD')
    assert result['received_at']==NOW+4
    assert result['age_seconds']==1
