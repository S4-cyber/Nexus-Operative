"""Durable per-tier research permission switches. No trade authority.

All tiers default OFF. These limits only narrow the existing local
simulation gateway; they never override portfolio or global risk vetoes.
"""
import sqlite3,threading,time
from contextlib import closing
from decimal import Decimal
from nexus.state import db_path,load_settings

TIERS={"terrestrial":Decimal("0.01"),"solar":Decimal("0.02"),"universal":Decimal("0.03")}
_lock=threading.RLock()

def _db():
    db=sqlite3.connect(db_path(),timeout=5)
    db.execute("CREATE TABLE IF NOT EXISTS tier_permissions (tier TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)))")
    return db

def current():
    with _lock,closing(_db()) as db,db:
        saved=dict(db.execute("SELECT tier,enabled FROM tier_permissions"))
    config=load_settings()
    return {"mode":"LOCAL_SIMULATED_RISK_PERMISSIONS",
        "tiers":[{"tier":tier,"enabled":saved.get(tier,0)==1,
                  "maximum_intent_fraction":float(frac),
                  "maximum_intent_usd":round(min(200.,float(Decimal(str(config["paper_budget"]))*frac),
                         float(Decimal(str(config["paper_budget"]))*Decimal(str(config[tier+"_allocation"]))/100)),2),
                  "allocation_pct":config[tier+"_allocation"]}
                 for tier,frac in TIERS.items()],
        "live_orders_enabled":False,"broker_connected":False,
        "note":"All switches start OFF. Limits apply only to new simulated entry intents. Existing risk vetoes remain stricter where relevant; no broker orders are created."}

def configure(tier,enabled):
    if tier not in TIERS or type(enabled) is not bool:
        raise ValueError("INVALID_TIER_PERMISSION")
    with _lock,closing(_db()) as db,db:
        db.execute("INSERT INTO tier_permissions(tier,enabled) VALUES(?,?) ON CONFLICT(tier) DO UPDATE SET enabled=excluded.enabled",(tier,int(enabled)))
    return current()

def require_entry(tier,notional):
    if tier not in TIERS:raise ValueError("UNKNOWN_RISK_TIER")
    policy=next(x for x in current()["tiers"] if x["tier"]==tier)
    if not policy["enabled"]:raise ValueError("RISK_TIER_DISENGAGED")
    amount=Decimal(str(notional))
    # Compare against unrounded limit (avoid cent rounding permitting excess).
    config=load_settings()
    budget=Decimal(str(config["paper_budget"]))
    cap=min(Decimal("200"),budget*TIERS[tier],budget*Decimal(str(config[tier+"_allocation"]))/100)
    if amount>cap:raise ValueError("RISK_TIER_INTENT_CAP_EXCEEDED")
    return {"tier":tier,"tier_cap_usd":float(cap),"paper_only":True}

