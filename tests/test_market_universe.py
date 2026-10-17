from fastapi.testclient import TestClient
from nexus.app import app
from nexus.market_universe import parse,page

def product(base,status="online",**flags):
    return {"id":base+"-USD","base_currency":base,"quote_currency":"USD",
            "status":status,"quote_increment":"0.01",**flags}

def test_catalog_can_hold_thousands_without_trade_authority():
    rows=[product("T"+str(i).zfill(4)) for i in range(2500)]
    result=parse(rows)
    assert result["discovered_count"]==2500
    assert result["ready_for_simulated_orders"] is False
    assert result["live_orders_enabled"] is False
    assert all(x["execution_allowed"] is False for x in result["products"])
    assert all(x["research_status"]=="DISCOVERED_UNVERIFIED" for x in result["products"])

def test_filter_disabled_invalid_and_malformed_rows():
    rows=[product("BTC"),product("ETH",status="offline"),
          product("SOL",trading_disabled=True),product("AVAX",cancel_only=True),
          product("BTC"),{"id":"../../evil","status":"online"},
          product("XRP",quote_increment="-0.01"),
          product("DOT",post_only=True),product("UNI",limit_only=True)]
    assert [x["product"] for x in parse(rows)["products"]]==["BTC-USD"]

def test_pagination_search_and_bounds():
    rows=[product("T"+str(i).zfill(4)) for i in range(350)]
    first=page(100,0,"",fetch=lambda _:rows)
    second=page(100,100,"",fetch=lambda _:rows)
    assert len(first["products"])==len(second["products"])==100
    assert set(x["product"] for x in first["products"]).isdisjoint(x["product"] for x in second["products"])
    assert page(100,0,"T003",fetch=lambda _:rows)["filtered_count"]==10
    for args in ((999,0,""),(100,-1,""),(100,0,"!")):
        try:page(*args,fetch=lambda _:rows)
        except ValueError:pass
        else:assert False,"Unsafe catalog argument accepted"

def test_read_only_api_status_and_ui():
    client=TestClient(app)
    assert client.get("/api/markets/discover?limit=9999").status_code==422
    html=client.get("/portal").text
    for key in ("universe-load","universe-filter","universe-search","universe-next"):
        assert 'id="'+key+'"' in html
    assert "research universe" in html.lower()
    assert client.get("/api/status").json()["live_orders_enabled"] is False
