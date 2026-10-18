"""Explicit Portal adapter for paced account reads; no startup or order path."""
import hashlib
import os
from pathlib import Path
import time
from nexus.alpaca_paper_inspection import _uuid
from nexus.paper_acceptance_probe import _inspect_paced, pacing_status


def _managed_path(account):
    base=(Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'NexusOperative' if os.name=='nt'
          else Path.home()/'.nexus-operative')
    return base/'paper-read-pacing'/(hashlib.sha256(account.encode()).hexdigest()+'.sqlite3')


def inspect_saved(*, expected_account_id, confirm_read_only_paper=False,
                  opener=None,wall_clock=time.time,elapsed_clock=time.monotonic):
    if confirm_read_only_paper is not True:raise ValueError('PAPER_PORTAL_READ_CONFIRMATION_REQUIRED')
    account=_uuid(expected_account_id)
    result,observation,pacing=_inspect_paced(expected_account_id=account,pacing_path=_managed_path(account),
        confirm_read_only_paper=True,opener=opener,wall_clock=wall_clock,elapsed_clock=elapsed_clock)
    return dict(result,durable_local_pacing=True,local_pacing=pacing)


def status(*,expected_account_id):
    account=_uuid(expected_account_id)
    return pacing_status(expected_account_id=account,pacing_path=_managed_path(account))
