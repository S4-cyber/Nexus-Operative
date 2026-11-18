import copy
import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.order_gateway import OrderGateway


class EqualText(str):
    def __eq__(self,other):return True
    def __ne__(self,other):return False


@pytest.mark.parametrize(('field','code'),[
    ('client_order_id','INVALID_ORDER_ID'),
    ('product','PRODUCT_NOT_ELIGIBLE_FOR_SIMULATION'),
    ('side','INVALID_SIDE'),
    ('provider','UNKNOWN_PROVIDER'),
    ('tier','UNKNOWN_RISK_TIER'),
])
def test_submit_rejects_equality_forging_protocol_text(field,code):
    gateway=OrderGateway()
    values=dict(client_order_id='order',product='BTC-USD',side='BUY',
                notional=10,provider='nexus_local_paper',tier='terrestrial')
    values[field]=EqualText(values[field])
    with pytest.raises(ValueError,match='^'+code+'$'):
        gateway.submit(**values)
    assert gateway.intents=={}


@pytest.mark.parametrize(('field','code'),[
    ('client_order_id','INVALID_ORDER_ID'),
    ('event_id','INVALID_EVENT_ID'),
    ('kind','INVALID_EVENT_KIND'),
])
def test_lifecycle_rejects_equality_forging_text_without_mutation(field,code):
    gateway=OrderGateway();gateway.submit('order','BTC-USD','BUY',10)
    before=copy.deepcopy(gateway.intents)
    values=dict(client_order_id='order',event_id='event',sequence=1,
                kind='FILL',fill_notional=10)
    values[field]=EqualText(values[field])
    with pytest.raises(ValueError,match='^'+code+'$'):
        gateway.simulate_event(**values)
    assert gateway.intents==before


def test_cancel_rejects_equality_forging_order_id_without_mutation():
    gateway=OrderGateway();gateway.submit('order','BTC-USD','BUY',10)
    before=copy.deepcopy(gateway.intents)
    with pytest.raises(ValueError,match='^INVALID_ORDER_ID$'):
        gateway.cancel(EqualText('order'))
    assert gateway.intents==before

def test_idempotent_order_intent_and_no_broker_calls():
    gateway=OrderGateway()
    a=gateway.submit("demo_1","BTC-USD","BUY",25)
    assert a["status"]=="SIMULATED_ACK_ONLY"
    assert a["sent_to_broker"] is False
    assert a["simulated_filled_notional"]=="0"
    b=gateway.submit("demo_1","BTC-USD","BUY",25)
    assert b["idempotent_replay"] is True
    assert len(gateway.intents)==1
    with pytest.raises(ValueError,match="DUPLICATE_ID_CONFLICT"):
        gateway.submit("demo_1","ETH-USD","BUY",25)

def test_external_venues_remain_unconnected_and_orderless():
    gateway=OrderGateway()
    assert all(not p["live_submission_supported"] for p in gateway.connections()["providers"])
    for provider in ("alpaca_paper","ibkr_paper","coinbase_advanced"):
        with pytest.raises(ValueError,match="EXTERNAL_BROKER_NOT_AUTHORIZED"):
            gateway.submit("test_"+provider,"BTC-USD","BUY",10,provider)
    assert gateway.intents=={}

def test_halt_cancels_unfilled_simulation_and_reset_stays_paused():
    gateway=OrderGateway()
    gateway.submit("demo_2","ETH-USD","SELL",10)
    assert gateway.emergency_halt()["halted"] is True
    assert gateway.intents["demo_2"]["status"]=="SIMULATED_CANCELLED"
    with pytest.raises(ValueError,match="EMERGENCY_HALT_ACTIVE"):
        gateway.submit("demo_3","BTC-USD","BUY",15)
    assert gateway.reset()["paused"] is True
    assert gateway.submit("demo_3","BTC-USD","BUY",15)["live_orders_enabled"] is False

def test_invalid_notional_and_ids():
    gateway=OrderGateway()
    for amount in (float("nan"),float("inf"),-5,0,201,True):
        with pytest.raises(ValueError):
            gateway.submit("valid_1","BTC-USD","BUY",amount)
    with pytest.raises(ValueError):
        gateway.submit("../unsafe","BTC-USD","BUY",10)
    with pytest.raises(ValueError):
        gateway.submit("valid_2","SOME-USD","BUY",10)

def test_connector_status_is_fully_read_only():
    c=TestClient(app)
    r=c.get("/api/execution/connectors")
    assert r.status_code==200
    assert r.json()["broker_connected"] is False
    assert r.json()["live_orders_enabled"] is False
    assert c.post("/api/execution/intent/simulate",json={"client_order_id":"bad","product":"BTC-USD","side":"BUY","notional":10,"provider":"alpaca_paper"}).status_code==422
