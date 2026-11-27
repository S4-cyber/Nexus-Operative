"""Durable per-tier research permission switches. No trade authority.

Unused new installations select Terrestrial; Solar and Universal stay OFF.
Existing settings and explicitly saved switches are preserved. These limits only narrow the existing local
simulation gateway; they never override portfolio or global risk vetoes.
"""
import sqlite3,threading,time
from contextlib import closing,contextmanager
from decimal import Decimal
from nexus.state import (DB_BUSY_TIMEOUT_MS,db_path,load_settings,
    read_effective_settings,settings_busy,settings_database_busy,settings_transaction)

TIERS={"terrestrial":Decimal("0.01"),"solar":Decimal("0.02"),"universal":Decimal("0.03")}
_lock=threading.RLock()

def _db():
    db=None
    try:
        db=sqlite3.connect(db_path(),timeout=DB_BUSY_TIMEOUT_MS/1000)
        db.execute(f'PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}')
        db.execute('PRAGMA synchronous=FULL')
        db.execute("CREATE TABLE IF NOT EXISTS tier_permissions (tier TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)))")
        return db
    except sqlite3.OperationalError as error:
        if db is not None:db.close()
        if settings_database_busy(error):settings_busy()
        raise
    except BaseException:
        if db is not None:db.close()
        raise

def _policy_snapshot():
    """Read settings and tier switches from one SQLite snapshot."""
    with _lock:
        # Bootstrap a genuinely new installation through state._connect before
        # _db creates any schema. This read is not used for admission; the
        # authoritative settings and switch values are both read below.
        load_settings()
        with closing(_db()) as db:
            with settings_transaction(db):
                db.execute('BEGIN')
                config=read_effective_settings(db)
                saved=dict(db.execute("SELECT tier,enabled FROM tier_permissions"))
    return config,saved

def current():
    config,saved=_policy_snapshot()
    return {"mode":"LOCAL_SIMULATED_RISK_PERMISSIONS",
        "tiers":[{"tier":tier,"enabled":saved.get(tier,0)==1,
                  "maximum_intent_fraction":float(frac),
                  "maximum_intent_usd":round(min(200.,float(Decimal(str(config["paper_budget"]))*frac),
                         float(Decimal(str(config["paper_budget"]))*Decimal(str(config[tier+"_allocation"]))/100)),2),
                  "allocation_pct":config[tier+"_allocation"]}
                 for tier,frac in TIERS.items()],
        "live_orders_enabled":False,"broker_connected":False,
        "note":"New unused installations select Terrestrial. Saved switches are preserved; missing switches in existing installations stay OFF. Limits apply only to new simulated entry intents. Existing risk vetoes remain stricter where relevant; no broker orders are created."}

def configure(tier,enabled):
    if tier not in TIERS or type(enabled) is not bool:
        raise ValueError("INVALID_TIER_PERMISSION")
    with _lock,closing(_db()) as db:
        with settings_transaction(db):
            db.execute("BEGIN IMMEDIATE")
            if enabled:read_effective_settings(db)
            db.execute("INSERT INTO tier_permissions(tier,enabled) VALUES(?,?) ON CONFLICT(tier) DO UPDATE SET enabled=excluded.enabled",(tier,int(enabled)))
    try:return current()
    except ValueError as exc:
        if enabled or str(exc)!='PAPER_SETTINGS_REVIEW_REQUIRED':raise
        return dict(mode='LOCAL_SIMULATED_RISK_PERMISSIONS_SETTINGS_REVIEW',tier=tier,enabled=False,
            settings_review_required=True,live_orders_enabled=False,broker_connected=False,
            note='This tier is OFF. Saved paper settings need review; no other tier limits are inferred.')

def _entry_request(tier,notional):
    if type(tier) is not str or tier not in TIERS:
        raise ValueError("UNKNOWN_RISK_TIER")
    if type(notional) not in (int,float,Decimal):
        raise ValueError("RISK_TIER_INTENT_INVALID")
    amount=Decimal(str(notional))
    if not amount.is_finite() or amount<=0:
        raise ValueError("RISK_TIER_INTENT_INVALID")
    return amount

def _require_entry_snapshot(tier,amount,config,saved):
    if saved.get(tier,0)!=1:raise ValueError("RISK_TIER_DISENGAGED")
    # Compare against the same snapshot's unrounded limit (avoid cent rounding
    # permitting excess).
    budget=Decimal(str(config["paper_budget"]))
    cap=min(Decimal("200"),budget*TIERS[tier],budget*Decimal(str(config[tier+"_allocation"]))/100)
    if amount>cap:raise ValueError("RISK_TIER_INTENT_CAP_EXCEEDED")
    return {"tier":tier,"tier_cap_usd":float(cap),"paper_only":True}

def require_entry(tier,notional):
    amount=_entry_request(tier,notional)
    # Permission and cap inputs must come from one durable SQLite snapshot.
    # A disable or settings change can commit before or after this snapshot,
    # but cannot be spliced between two independent reads.
    config,saved=_policy_snapshot()
    return _require_entry_snapshot(tier,amount,config,saved)

@contextmanager
def entry_commit_guard(tier,notional):
    """Hold tier/settings mutation exclusion through one durable BUY commit.

    A cooperating OFF or cap change either commits before this writer
    reservation (and is observed here) or waits until the caller releases the
    reservation after its order/portfolio transaction. Reducing exits and the
    independent emergency latch never acquire this guard.
    """
    amount=_entry_request(tier,notional)
    # Serialize only the bootstrap. The durable exclusion below is SQLite's
    # writer reservation, so it also covers cooperating callers in another
    # process rather than relying on this process-local lock.
    with _lock:load_settings()
    with closing(_db()) as db:
        with settings_transaction(db):
            db.execute('BEGIN IMMEDIATE')
            config=read_effective_settings(db)
            saved=dict(db.execute("SELECT tier,enabled FROM tier_permissions"))
            try:yield _require_entry_snapshot(tier,amount,config,saved)
            finally:
                # This transaction never mutates settings. Roll back its
                # writer reservation explicitly so a concurrent read cannot
                # turn an already committed BUY into a false commit failure.
                db.rollback()

