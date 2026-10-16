"""Independent local paper-portfolio integrity governor.

Audits local SQLite journal and position invariants without network access.
It cannot prove that historical prices or fills were real. Failed integrity
checks prohibit additional PAPER purchases; live trading remains impossible.
"""
from decimal import Decimal, InvalidOperation
from nexus.paper_portfolio import LOCK, _connect, PRODUCTS, D, FEE
from nexus.transaction_seals import verify_seals

def checked(raw, *, positive=False):
    try:
        value=Decimal(str(raw))
    except (InvalidOperation,TypeError,ValueError) as exc:
        raise ValueError("INVALID_NUMERIC_LEDGER_FIELD") from exc
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError("INVALID_NUMERIC_LEDGER_FIELD")
    return value

def audit_portfolio():
    reasons=[]
    with LOCK,_connect() as db:
        meta=dict(db.execute("SELECT key,value FROM meta"))
        entries=db.execute("SELECT id,action,product,cash_delta FROM ledger ORDER BY id").fetchall()
        positions=db.execute("SELECT product,quantity,entry FROM positions ORDER BY product").fetchall()
        journal=db.execute("SELECT ledger_id,action,product,quantity,entry FROM position_events ORDER BY ledger_id").fetchall()
        chain_valid,chain_reason=verify_seals(db)
    if "initial" not in meta or "cash" not in meta:
        reasons.append("UNINITIALIZED_PORTFOLIO")
        return {"mode":"PAPER_INTEGRITY_AUDIT","integrity_ok":False,
                "reasons":reasons,"simulation_only":True,"orders_enabled":False}
    if meta.get("hash_chain_version")!="1":reasons.append("LEGACY_UNSEALED_JOURNAL")
    if not chain_valid:reasons.append(chain_reason)
    try:
        capital=checked(meta["initial"],positive=True)
        cash=checked(meta["cash"])
        deltas=[Decimal(str(event[3])) for event in entries]
        if not deltas or any(not d.is_finite() for d in deltas) or sum(deltas,D(0))!=cash:
            reasons.append("CASH_JOURNAL_DISAGREEMENT")
        if not entries or entries[0][1]!="INIT" or D(str(entries[0][3]))!=capital:
            reasons.append("INVALID_INITIAL_ENTRY")
        if any(action not in ("INIT","PAPER_BUY","PAPER_SELL") for _,action,_,_ in entries):
            reasons.append("UNKNOWN_LEDGER_ACTION")
        if any(action=="INIT" for _,action,_,_ in entries[1:]):
            reasons.append("MULTIPLE_INITIAL_ENTRIES")
        if meta.get("position_journal_version")!="2":
            reasons.append("LEGACY_POSITION_HISTORY_UNVERIFIABLE")
        else:
            expected={}
            trades={row[0]:(row[1],row[2]) for row in entries if row[1] in ("PAPER_BUY","PAPER_SELL")}
            if len(journal)!=len(trades):reasons.append("MISSING_POSITION_EVENTS")
            seen_ids=set()
            for event_id,action,product,quantity,entry in journal:
                if event_id in seen_ids or trades.get(event_id)!=(action,product):
                    reasons.append("POSITION_EVENT_LEDGER_DISAGREEMENT")
                seen_ids.add(event_id)
                qty=checked(quantity,positive=True)
                price=checked(entry,positive=True)
                if action=="PAPER_BUY":
                    if product in expected:reasons.append("DUPLICATE_POSITION_OPEN")
                    expected[product]=(qty,price)
                elif action=="PAPER_SELL":
                    if expected.pop(product,None)!=(qty,price):
                        reasons.append("POSITION_CLOSE_MISMATCH")
                else:reasons.append("UNKNOWN_POSITION_EVENT")
            stored={p:(checked(q,positive=True),checked(e,positive=True)) for p,q,e in positions}
            if expected!=stored:reasons.append("POSITION_RECONSTRUCTION_MISMATCH")
        exposure=D(0)
        seen=set()
        for product,quantity,entry in positions:
            if product not in PRODUCTS or product in seen:
                reasons.append("UNKNOWN_OR_DUPLICATE_POSITION")
            seen.add(product)
            size=checked(quantity,positive=True)*checked(entry,positive=True)
            exposure+=size
            if size>capital*D(".02"):
                reasons.append("POSITION_EXCEEDS_LIMIT")
        if exposure>capital*D(".10"):
            reasons.append("AGGREGATE_EXPOSURE_LIMIT")
        if cash>capital*D("100"):
            reasons.append("IMPLAUSIBLE_CASH_BALANCE")
    except (InvalidOperation,ValueError,OverflowError):
        reasons.append("INVALID_NUMERIC_LEDGER_FIELD")
        exposure=None
    return {"mode":"PAPER_INTEGRITY_AUDIT","integrity_ok":not reasons,
        "reasons":sorted(set(reasons)),"position_count":len(positions),
        "journal_entries":len(entries),
        "paper_cost_basis_exposure":float(exposure) if exposure is not None else None,
        "simulation_only":True,"orders_enabled":False,
        "limitations":"Version 2 journals reconstruct virtual quantities. SHA-256 seals detect isolated edits but are not externally anchored and cannot prevent coordinated rewrites. Legacy accounts require review."}

def require_safe_ledger():
    report=audit_portfolio()
    if not report["integrity_ok"]:
        raise ValueError("Paper governor veto: "+", ".join(report["reasons"]))
    return report
