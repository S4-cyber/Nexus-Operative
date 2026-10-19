"""Process-independent local paper emergency cut; no network or reset path.

The installed launcher exposes this only through an exact ``--emergency-stop``
command.  The intent file is published before SQLite work so a running paper
loop can veto intake even when its Portal/event loop is unavailable.  Durable
order cancellation remains the authoritative cleanup acknowledgment.
"""
import json
import os
import sqlite3
from pathlib import Path

from nexus import emergency_intent
from nexus import emergency_priority
from nexus.durable_orders import DurableOrderGateway


def activate(data_root=None):
    priority = emergency_priority.request()
    root = Path(data_root or os.environ.get('NEXUS_PORTABLE_DATA',
        str(Path.home()/'.nexus-operative'))).expanduser()
    order_path = root/'order_lifecycle.sqlite3'
    if order_path.is_symlink():raise ValueError('ORDER_HISTORY_SYMLINK')
    intent_preceded = durable_halt = final_intent = False
    try:
        try:
            emergency_intent.persist(order_path)
            intent_preceded = True
        finally:
            try:
                result = DurableOrderGateway(order_path).emergency_halt()
                durable_halt = result.get('halted') is True
            finally:
                # Reassert after SQLite so a concurrently finishing explicit
                # reset cannot leave the standalone request without a latch.
                emergency_intent.persist(order_path)
                final_intent = emergency_intent.requested(order_path)
    except (ValueError, OSError, sqlite3.Error, RuntimeError):
        try:final_intent = emergency_intent.requested(order_path)
        except (ValueError, OSError):final_intent = False
    verified = intent_preceded and durable_halt and final_intent
    return dict(mode='NEXUS_LOCAL_PAPER_EMERGENCY_STOP',passed=verified,
        halted=final_intent or durable_halt,
        emergency_cut_state='VERIFIED' if verified else 'UNCONFIRMED',
        intent_preceded_durable_halt=intent_preceded,
        emergency_intent_present=final_intent,
        durable_order_halt_confirmed=durable_halt,
        emergency_priority_attempted=priority['attempted'],
        emergency_priority_confirmed=priority['confirmed'],
        emergency_priority_class=priority['priority_class'],
        desktop_started=False,network_requested=False,credential_access_requested=False,
        broker_connected=False,live_orders_enabled=False,money_movement_enabled=False,
        reset_available=False,automatic_restart=False,
        scope='Local paper execution cut only. No broker, funding, cloud, live-order or reset authority.')


def main():
    try:
        result=activate();print(json.dumps(result,sort_keys=True,indent=2))
        return 0 if result['passed'] else 2
    except (Exception,KeyboardInterrupt):
        print('NEXUS_EMERGENCY_STOP_FAILED')
        return 2


if __name__=='__main__':raise SystemExit(main())
