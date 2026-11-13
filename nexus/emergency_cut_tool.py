"""Process-independent local paper emergency cut; no network or reset path.

The installed launcher exposes this only through an exact ``--emergency-stop``
command.  The intent file is published before SQLite work so a running paper
loop can veto intake even when its Portal/event loop is unavailable.  Durable
order cancellation remains the authoritative cleanup acknowledgment.
"""
import json
import os
import stat
from pathlib import Path

from nexus import emergency_intent
from nexus import emergency_priority
from nexus.durable_orders import DurableOrderGateway

PUBLIC_KEYS = frozenset(('mode','passed','halted','emergency_cut_state',
    'intent_preceded_durable_halt','emergency_intent_present',
    'durable_order_halt_confirmed','emergency_priority_attempted',
    'emergency_priority_confirmed','emergency_priority_class',
    'data_root_path_pinned','desktop_started','network_requested',
    'credential_access_requested','broker_connected','live_orders_enabled',
    'money_movement_enabled','reset_available','automatic_restart','scope'))


def _resolved_data_root(data_root):
    """Resolve one non-symlink data-root before cut storage access."""
    candidate = Path(data_root or os.environ.get('NEXUS_PORTABLE_DATA',
        str(Path.home()/'.nexus-operative'))).expanduser()
    if candidate.is_symlink():raise ValueError('DATA_ROOT_SYMLINK')
    try:before = candidate.lstat()
    except FileNotFoundError:
        return candidate.resolve(strict=False),None
    if not stat.S_ISDIR(before.st_mode):raise ValueError('DATA_ROOT_DIRECTORY_REQUIRED')
    resolved = candidate.resolve(strict=True)
    after = candidate.lstat()
    if candidate.is_symlink() or not os.path.samestat(before,after):
        raise ValueError('DATA_ROOT_CHANGED')
    return resolved,before


def _initialize_data_root(root):
    """Exclusively create a root observed absent before priority elevation."""
    root.parent.mkdir(parents=True,exist_ok=True)
    try:root.mkdir(mode=0o700)
    except FileExistsError:raise ValueError('DATA_ROOT_CHANGED')
    identity=root.lstat()
    if root.is_symlink() or not stat.S_ISDIR(identity.st_mode):
        raise ValueError('DATA_ROOT_DIRECTORY_REQUIRED')
    return identity


def _same_data_root(root,identity):
    """Conservatively sample the public root identity throughout the cut."""
    if identity is None:return False
    try:current=root.lstat()
    except OSError:return False
    return (not root.is_symlink() and stat.S_ISDIR(current.st_mode)
        and os.path.samestat(identity,current))


def _request_priority():
    """Contain all best-effort priority faults before authoritative cut work."""
    fallback = dict(attempted=os.name == 'nt',confirmed=False,
        priority_class='STANDARD')
    try:result=emergency_priority.request()
    except Exception:return fallback
    if type(result) is not dict:return fallback
    attempted=result.get('attempted');confirmed=result.get('confirmed')
    priority_class=result.get('priority_class')
    if (type(attempted) is not bool or type(confirmed) is not bool
            or (attempted,confirmed,priority_class) not in (
                (False,False,'STANDARD'),(True,False,'STANDARD'),
                (True,True,'HIGH'))):
        return fallback
    return dict(attempted=attempted,confirmed=confirmed,
        priority_class=priority_class)


def _halt_confirmed(result):
    """Accept only the gateway's ordinary, explicit halt acknowledgement."""
    return type(result) is dict and result.get('halted') is True


def _intent_present(result):
    """Accept only the intent reader's exact boolean presence result."""
    return result is True


def activate(data_root=None):
    root,root_identity = _resolved_data_root(data_root)
    priority = _request_priority()
    if root_identity is None:root_identity=_initialize_data_root(root)
    root_stable = _same_data_root(root,root_identity)
    order_path = root/'order_lifecycle.sqlite3'
    intent_preceded = durable_halt = final_intent = False
    try:
        if not root_stable:raise ValueError('DATA_ROOT_CHANGED')
        if order_path.is_symlink():raise ValueError('ORDER_HISTORY_SYMLINK')
        try:
            emergency_intent.persist(order_path)
            if not _same_data_root(root,root_identity):
                raise ValueError('DATA_ROOT_CHANGED')
            # ``persist`` deliberately has no success return value.  Observe
            # the latch itself before claiming that intake was fenced ahead
            # of the durable cancellation phase.  A silent/malformed helper
            # can never manufacture the ordering evidence required by
            # ``VERIFIED``.
            intent_preceded = _intent_present(
                emergency_intent.requested(order_path))
        finally:
            try:
                if _same_data_root(root,root_identity):
                    result = DurableOrderGateway(order_path).emergency_halt()
                    durable_halt = _halt_confirmed(result)
                else:raise ValueError('DATA_ROOT_CHANGED')
            finally:
                # Reassert after SQLite so a concurrently finishing explicit
                # reset cannot leave the standalone request without a latch.
                if _same_data_root(root,root_identity):
                    emergency_intent.persist(order_path)
                    if not _same_data_root(root,root_identity):
                        raise ValueError('DATA_ROOT_CHANGED')
                    final_intent = _intent_present(
                        emergency_intent.requested(order_path))
    except Exception:
        # An emergency command must still return a conservative structured
        # result after an unforeseen ordinary storage/helper fault.  System
        # exits and interrupts are intentionally not swallowed here.  The
        # nested finally blocks above already attempted the independent latch,
        # durable halt and final latch before control reaches this boundary.
        if _same_data_root(root,root_identity):
            try:final_intent = _intent_present(
                emergency_intent.requested(order_path))
            except Exception:final_intent = False
        else:final_intent = False
    root_stable = _same_data_root(root,root_identity)
    verified = root_stable and intent_preceded and durable_halt and final_intent
    return dict(mode='NEXUS_LOCAL_PAPER_EMERGENCY_STOP',passed=verified,
        halted=final_intent or durable_halt,
        emergency_cut_state='VERIFIED' if verified else 'UNCONFIRMED',
        intent_preceded_durable_halt=intent_preceded,
        emergency_intent_present=final_intent,
        durable_order_halt_confirmed=durable_halt,
        emergency_priority_attempted=priority['attempted'],
        emergency_priority_confirmed=priority['confirmed'],
        emergency_priority_class=priority['priority_class'],
        data_root_path_pinned=root_stable,
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
