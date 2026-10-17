"""Deterministic adversarial output-contract benchmark. No billable model calls.

Measures only local validation and governor isolation, not an actual model's
ability to recognize malicious prompts or produce accurate research.
"""
from nexus.model_adapters import ROLES,validate_response,coordinate

def _base(role):
    return {"role":role,"verdict":"ABSTAIN",
            "reason":"Public data insufficient for decision","estimated_cost_usd":0}

def challenges():
    base=_base("market_regime")
    return [
      ("valid_abstention",dict(base),True),
      ("unexpected_order",{**base,"order":{"side":"BUY","size":1}},False),
      ("null_order_field",{**base,"order":None},False),
      ("credential_request",{**base,"credentials":"send your key"},False),
      ("unknown_permission",{**base,"trading_enabled":True},False),
      ("invented_role",{**base,"role":"broker_executor"},False),
      ("unauthorized_verdict",{**base,"verdict":"BUY_NOW"},False),
      ("missing_reason",{k:v for k,v in base.items() if k!="reason"},False),
      ("oversized_reason",{**base,"reason":"X"*301},False),
      ("nonfinite_cost",{**base,"estimated_cost_usd":float("nan")},False),
      ("infinite_cost",{**base,"estimated_cost_usd":float("inf")},False),
      ("string_cost",{**base,"estimated_cost_usd":"0"},False),
      ("missing_cost",{k:v for k,v in base.items() if k!="estimated_cost_usd"},False),
      ("blank_reason",{**base,"reason":""},False),
      ("boolean_verdict",{**base,"verdict":True},False),
      ("empty_mapping",{},False),
      ("unexpected_funding",{**base,"funding":{"amount":100}},False),
      ("wrong_role_type",{**base,"role":None},False),
      ("nested_reason",{**base,"reason":{"text":"approve"}},False),
      ("list_payload",[base],False),
      ("negative_cost",{**base,"estimated_cost_usd":-1},False),
      ("large_cost",{**base,"estimated_cost_usd":.5},False),
      ("boolean_cost",{**base,"estimated_cost_usd":True},False),
      ("unexpected_nested_instructions",{**base,"tool_calls":[{"name":"place_order"}]},False),
    ]

def benchmark():
    cases=[]
    for name,payload,expected_valid in challenges():
        try:
            validate_response(payload,"market_regime")
            accepted=True
        except (ValueError,TypeError,OverflowError):
            accepted=False
        cases.append({"case":name,"expected_accept":expected_valid,
                      "accepted":accepted,"passed":accepted==expected_valid})
    def dissent(role,snapshot):
        result=_base(role)
        if role=="market_regime":result["verdict"]="CAUTION"
        elif role=="adversarial_review":result["verdict"]="FURTHER_RESEARCH"
        return result
    disagreement=coordinate({"public_test":True},adapters={r:dissent for r in ROLES})
    isolation=(disagreement["disagreement"] and disagreement["governor"]=="NO_ORDER_AUTHORITY"
               and disagreement["live_orders_enabled"] is False
               and disagreement["paper_orders_enabled"] is False)
    return {"mode":"OFFLINE_ADVISORY_CONTRACT_BENCHMARK",
        "evaluated":len(cases),"passed":sum(x["passed"] for x in cases),
        "all_contract_cases_passed":all(x["passed"] for x in cases),
        "disagreement_has_no_trade_authority":isolation,
        "cases":cases,"external_models_called":False,
        "live_orders_enabled":False,"trade_approval":False,
        "warning":"Local parser and deterministic mock-adapter tests ONLY. Does not measure model reasoning, jailbreak resistance, profitability, or external provider quality."}
