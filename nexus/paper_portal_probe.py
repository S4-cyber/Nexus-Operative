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


def inspect_rules_saved(*,expected_account_id,confirm_read_only_paper=False,
                        opener=None,wall_clock=time.time,elapsed_clock=time.monotonic):
    if confirm_read_only_paper is not True:raise ValueError('PAPER_PORTAL_READ_CONFIRMATION_REQUIRED')
    from nexus import paper_credentials
    from nexus.paper_acceptance_probe import _gate
    from nexus.paper_read_pacing import fixed_targets_match
    from nexus.paper_read_projection import matches as projection_matches
    account=_uuid(expected_account_id);gate=_gate(account,_managed_path(account));before=gate.inspect()
    result=paper_credentials.inspect_rules_saved(expected_account_id=account,confirm_read_only_paper=True,
        opener=opener,wall_clock=wall_clock,elapsed_clock=elapsed_clock,pacing=gate)
    after=gate.inspect();operations=gate.operation_history()
    if (result['account_id']!=account or after['sessions']!=before['sessions']+1 or
        after['events']!=before['events']+2 or after['pending_token'] is not None or
        after['review_required'] is not False or after['last_outcome']!='SUCCESS' or not operations or
        operations[-1]['before_head_sha256']!=before['head_sha256'] or
        operations[-1]['after_head_sha256']!=after['head_sha256'] or
        not fixed_targets_match(operations[-1],'BTC_ASSET_RULES') or
        not projection_matches(operations[-1],'BTC_ASSET_RULES',result)):
        raise ValueError('PAPER_PORTAL_ASSET_RULES_REVIEW_REQUIRED')
    pacing=dict(account_sha256=after['account_sha256'],before_head_sha256=before['head_sha256'],
        after_head_sha256=after['head_sha256'],events=after['events'],sessions=after['sessions'],
        next_allowed_at=after['next_allowed_at'],min_post_result_cooldown_seconds=after['min_post_result_cooldown_seconds'],
        coordination_scope='EXACT_LOCAL_JOURNAL_ONLY_NOT_BROKER_WIDE_OR_DISTRIBUTED')
    return dict(result,durable_local_pacing=True,local_pacing=pacing)
