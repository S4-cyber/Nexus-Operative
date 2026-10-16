"""Conservative paper-account recovery diagnostics and local SQLite snapshots.

Never fabricates transaction history, upgrades old ledgers, resets balances,
touches brokers, or alters the original account. SQLite's online backup API
preserves a consistent snapshot while normal journal locks are respected.
"""
import hashlib
import sqlite3
import time
from pathlib import Path
from nexus.paper_portfolio import LOCK, _connect
from nexus.portfolio_governor import audit_portfolio

def recovery_report():
    audit=audit_portfolio()
    with LOCK,_connect() as db:
        meta=dict(db.execute("SELECT key,value FROM meta").fetchall())
        ledger_count=db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        event_count=db.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
        seal_count=db.execute("SELECT COUNT(*) FROM chain_seals").fetchone()[0]
    reasons=list(audit["reasons"])
    is_legacy=meta.get("position_journal_version")!="2" or meta.get("hash_chain_version")!="1"
    if not meta.get("initial"):
        status="UNINITIALIZED"
    elif is_legacy:
        status="LEGACY_REQUIRES_MANUAL_REVIEW"
    elif not audit["integrity_ok"]:
        status="INTEGRITY_FAILED"
    else:
        status="VERIFIED_LOCAL_STRUCTURE"
    return {"mode":"READ_ONLY_PAPER_RECOVERY","status":status,
      "integrity_ok":audit["integrity_ok"],"reasons":reasons,
      "ledger_rows":ledger_count,"position_event_rows":event_count,"seal_rows":seal_count,
      "migration_performed":False,"balance_changed":False,"live_orders_enabled":False,
      "recommended_action":("Export a local snapshot and review missing historical transaction evidence before any migration."
                            if status!="VERIFIED_LOCAL_STRUCTURE" else
                            "Local structural checks passed; this is not proof of actual market fills."),
      "limitations":"Historical quantity and price details missing from legacy records cannot safely be inferred from cash deltas."}

def backup_snapshot():
    with LOCK,_connect() as source:
        directory=Path(source.execute("PRAGMA database_list").fetchone()[2]).parent/"recovery_snapshots"
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        # A UTC timestamp plus exclusive create avoids replacing previous exports.
        filename="paper_snapshot_"+str(time.time_ns())+".sqlite3"
        destination=directory/filename
        try:
            with destination.open("xb"):
                pass
            with sqlite3.connect(destination) as target:
                source.backup(target)
            digest=hashlib.sha256()
            with destination.open("rb") as fd:
                for chunk in iter(lambda: fd.read(1048576),b""):
                    digest.update(chunk)
            size=destination.stat().st_size
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    return {"mode":"LOCAL_SQLITE_BACKUP","filename":filename,"bytes":size,
            "sha256":digest.hexdigest(),"stored_in":"Local NEXUS_PORTABLE_DATA/recovery_snapshots",
            "source_modified":False,"migration_performed":False,
            "orders_enabled":False,
            "note":"File remains on this computer. Backups may include sensitive simulated account history; protect access."}
