from decimal import Decimal
import math
import time

import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.risk_gate import evaluate


class EqualText(str):
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False


class StringQuantity:
    def __str__(self):
        return "50"


class RequestMapping(dict):
    pass

def viable():
    return dict(data_verified=True,instrument_authorized=True,market_open=True,
        account_reconciled=True,emergency_halt=False,cooldown_active=False,
        quote_timestamp=time.time(),tier="terrestrial",equity=10000,notional=50,
        current_exposure=0,realized_daily_loss=0,roundtrip_fees_bps=2,
        spread_bps=3,roundtrip_slippage_bps=5,
        conservative_expected_edge_bps=50,modeled_worst_loss_bps=100)

def test_default_denial():
    r=evaluate({})
    assert r["decision"]=="REJECT"
    assert r["live_execution_authorized"] is False

def test_good_candidate_is_still_not_live():
    r=evaluate(viable())
    assert r["decision"]=="PAPER_RESEARCH_CANDIDATE"
    assert r["live_execution_authorized"] is False

def test_weak_edge_and_late_quote_rejected():
    x=viable();x["conservative_expected_edge_bps"]=10
    assert "INSUFFICIENT_NET_EDGE_AFTER_COSTS" in evaluate(x)["reasons"]
    x=viable();x["quote_timestamp"]=time.time()-50
    assert "STALE_OR_MISSING_QUOTE" in evaluate(x)["reasons"]

def test_spending_and_halt_rejected():
    x=viable();x["realized_daily_loss"]=101
    assert "DAILY_LOSS_BUDGET_EXHAUSTED" in evaluate(x)["reasons"]
    x=viable();x["emergency_halt"]=True
    assert "EMERGENCY_HALT_OR_UNKNOWN" in evaluate(x)["reasons"]

def test_invalid_numeric_fails_closed():
    x=viable();x["equity"]="NaN"
    assert evaluate(x)["decision"]=="REJECT"


@pytest.mark.parametrize(("field", "value"), [
    ("equity", "10000"),
    ("notional", StringQuantity()),
    ("current_exposure", True),
    ("roundtrip_fees_bps", Decimal("1e100000")),
    ("conservative_expected_edge_bps", float("inf")),
])
def test_numeric_evidence_requires_exact_finite_bounded_types(field, value):
    request = viable()
    request[field] = value
    result = evaluate(request)
    assert result["decision"] == "REJECT"
    assert "MISSING_OR_INVALID_NUMERIC_EVIDENCE" in result["reasons"]
    assert result["cost_bps"] is None


def test_type_confused_request_and_tier_fail_closed_without_mapping_hooks():
    request = viable()
    request["tier"] = EqualText("foreign")
    result = evaluate(request)
    assert result["decision"] == "REJECT"
    assert "UNSUPPORTED_TIER" in result["reasons"]

    mapped = evaluate(RequestMapping(viable()))
    assert mapped["decision"] == "REJECT"
    assert "INVALID_REQUEST_SCHEMA" in mapped["reasons"]
    assert mapped["cost_bps"] is None


def test_missing_extra_and_non_string_keys_are_schema_rejections():
    missing = viable(); missing.pop("market_open")
    extra = {**viable(), "future_authority": True}
    non_string = {**viable(), 1: "private"}
    for request in (missing, extra, non_string):
        result = evaluate(request)
        assert result["decision"] == "REJECT"
        assert "INVALID_REQUEST_SCHEMA" in result["reasons"]


def test_bounded_cost_output_is_always_json_safe():
    request = viable()
    request["roundtrip_fees_bps"] = 1_000_000
    request["spread_bps"] = 1_000_000
    request["roundtrip_slippage_bps"] = 1_000_000
    request["conservative_expected_edge_bps"] = 1_000_000
    result = evaluate(request)
    assert result["decision"] == "REJECT"
    assert result["cost_bps"] == 3_000_000
    assert math.isfinite(result["cost_bps"])

def test_endpoint_does_not_authorize_broker():
    c=TestClient(app)
    r=c.post("/api/paper/risk-screen",json=viable())
    assert r.status_code==200
    assert r.json()["live_execution_authorized"] is False
    assert c.get("/api/status").json()["money_movement_enabled"] is False


def test_endpoint_rejects_unbounded_string_evidence_without_serialization_failure():
    request = viable()
    request["roundtrip_fees_bps"] = "1e999999"
    request["conservative_expected_edge_bps"] = "2e999999"
    response = TestClient(app).post("/api/paper/risk-screen", json=request)
    assert response.status_code == 200
    result = response.json()
    assert result["decision"] == "REJECT"
    assert "MISSING_OR_INVALID_NUMERIC_EVIDENCE" in result["reasons"]
    assert result["cost_bps"] is None
    assert result["live_execution_authorized"] is False
