"""SHA-256 continuity checks for local paper transactions.

This is not an externally anchored signature: an attacker with full database
write access can rehash all rows. It detects ordinary edits and missing rows.
"""
import hashlib
import json

GENESIS="0"*64

def row_payload(db,ledger_id):
    record=db.execute("SELECT id,at,action,product,cash_delta FROM ledger WHERE id=?",(ledger_id,)).fetchone()
    if record is None:raise ValueError("LEDGER_RECORD_MISSING")
    event=db.execute("SELECT action,product,quantity,entry FROM position_events WHERE ledger_id=?",(ledger_id,)).fetchone()
    payload={"ledger":list(record),"position":list(event) if event else None}
    if record[2] in ("PAPER_FILL_BUY","PAPER_FILL_SELL"):
        execution=db.execute("SELECT order_id,event_id,quantity,price,fee FROM execution_fills WHERE ledger_id=?",(ledger_id,)).fetchone()
        payload["execution"]=list(execution) if execution else None
    return json.dumps(payload,
                      sort_keys=True,separators=(",",":"),ensure_ascii=True)

def checksum(previous,payload):
    return hashlib.sha256((previous+"\n"+payload).encode("utf-8")).hexdigest()

def append_seal(db,ledger_id):
    prev=db.execute("SELECT digest FROM chain_seals ORDER BY ledger_id DESC LIMIT 1").fetchone()
    previous=prev[0] if prev else GENESIS
    db.execute("INSERT INTO chain_seals(ledger_id,previous,digest) VALUES(?,?,?)",
               (ledger_id,previous,checksum(previous,row_payload(db,ledger_id))))

def verify_seals(db):
    ledger_ids=[r[0] for r in db.execute("SELECT id FROM ledger ORDER BY id")]
    seals=db.execute("SELECT ledger_id,previous,digest FROM chain_seals ORDER BY ledger_id").fetchall()
    if len(ledger_ids)!=len(seals):return False,"HASH_CHAIN_LENGTH_MISMATCH"
    previous=GENESIS
    for ledger_id,(sealed_id,stored_prev,stored_digest) in zip(ledger_ids,seals):
        if sealed_id!=ledger_id or stored_prev!=previous:
            return False,"HASH_CHAIN_SEQUENCE_MISMATCH"
        if checksum(previous,row_payload(db,ledger_id))!=stored_digest:
            return False,"HASH_CHAIN_DIGEST_MISMATCH"
        previous=stored_digest
    return True,previous

