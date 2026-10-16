"""Evidence Auditor: conservative validation independent of Council opinions.

User-supplied fields, including 'data_verified':true, are NOT proof that an
external quote or performance record is authenticated. Audits deliberately
cannot grant an execution permission or validate model-predicted returns.
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation

def safe_number(value):
    if isinstance(value,bool):
        raise ValueError("Boolean is not numeric evidence")
    try:
        v=Decimal(str(value))
    except (TypeError,ValueError,InvalidOperation) as exc:
        raise ValueError("Unparseable numeric evidence") from exc
    if not v.is_finite():raise ValueError("Nonfinite numeric evidence")
    return v

def audit_evidence(evidence, quote=None):
    if not isinstance(evidence,dict):
        raise ValueError("Evidence mapping required")
    reasons=[]
    required=("symbol","data_source","history_observations","out_of_sample_trades",
              "conservative_edge_bps","equity","proposed_notional")
    if any(k not in evidence for k in required):
        reasons.append("MISSING_EVIDENCE_FIELDS")
    vals={}
    for name in required[2:]:
        if name not in evidence:continue
        try:vals[name]=safe_number(evidence[name])
        except ValueError:reasons.append("INVALID_"+name.upper())
    for name in ("history_observations","out_of_sample_trades","equity","proposed_notional"):
        if name in vals and vals[name]<=0:reasons.append("NONPOSITIVE_"+name.upper())
    if all(k in vals for k in ("history_observations","out_of_sample_trades")):
        if vals["out_of_sample_trades"]>=vals["history_observations"]:
            reasons.append("HOLDOUT_SAMPLE_INCONSISTENT")
    if evidence.get("data_verified") is True:
        reasons.append("SELF_ATTESTED_VERIFICATION_NOT_ACCEPTED")
    if quote is None:
        reasons.append("NO_INDEPENDENT_QUOTE_ATTESTATION")
    else:
        if not isinstance(quote,dict) or quote.get("product")!=evidence.get("symbol") or quote.get("read_only") is not True or quote.get("orders_allowed") is not False:
            reasons.append("QUOTE_PROVENANCE_MISMATCH")
        else:
            # A caller-supplied quote cannot be treated as server-attested.
            reasons.append("QUOTE_SUPPLIED_BY_CALLER_NOT_ATTESTED")
    reasons.append("NO_INDEPENDENT_HOLDOUT_PROVENANCE")
    payload=json.dumps(evidence,sort_keys=True,default=str,allow_nan=False)
    return {"component":"EVIDENCE_AUDITOR_V1","decision":"UNVERIFIED",
            "reasons":sorted(set(reasons)),
            "evidence_sha256":hashlib.sha256(payload.encode()).hexdigest(),
            "source_verified":False,"performance_verified":False,
            "simulation_only":True,"live_orders_enabled":False,
            "note":"Caller assertions are never independent evidence. Future server-side signed market data and immutable replay provenance are required."}
