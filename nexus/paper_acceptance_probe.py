"""Explicit, durably paced Windows paper-account read for operator acceptance.

Uses only an already saved credential pair. No secret arguments, order/funding
methods, automatic retries, cloud enrollment or acceptance/release authority.
Successful stdout is a PRIVATE inventory/open-term observation and pacing record.
"""
import argparse
from pathlib import Path
import json
import sqlite3
import time

from nexus import paper_credentials
from nexus.alpaca_paper_contract import sha
from nexus.alpaca_paper_inspection import _uuid
from nexus import paper_open_order_terms as order_terms
from nexus.paper_read_pacing import PaperReadPacing, MAX_SESSIONS, fixed_targets_match
from nexus import portable_paper_observation as portable

ROOT=Path(__file__).resolve().parents[1]
FLAGS=('broker_connected','broker_paper_accepted','source_attested','source_coverage_verified',
       'broker_wide_quota_verified','account_reconciled','holds_reconciled','spendable_funds_verified',
       'ledger_applied','ready_for_routing','live_orders_enabled','money_movement_enabled',
       'automatic_retry','automatic_continuation','release_approved','cloud_deployed')
FLAGS+=('provider_key_permissions_verified','windows_profile_accepted')
TERMS_FIELDS=('mode','status','scope','reported_rows','supported_unique_orders','duplicate_rows_ignored',
    'unsupported_rows','terms_complete','remaining_buy_limit_principal_cap_usd','remaining_sell_quantity_btc',
    'normalized_terms_sha256','review_reasons',*order_terms.FLAGS,'summary_sha256')


def _fail():raise ValueError('PAPER_ACCEPTANCE_PROBE_FAILED') from None


def _gate(expected_account_id,pacing_path):
    account=_uuid(expected_account_id);path=Path(pacing_path)
    try:resolved=path.resolve()
    except (OSError,RuntimeError):_fail()
    if resolved.is_relative_to(ROOT):_fail()
    return PaperReadPacing(path,account)


def pacing_status(*, expected_account_id, pacing_path):
    gate=_gate(expected_account_id,pacing_path);state=gate.inspect()
    pending=state['pending_token'] is not None
    remaining=max(0,MAX_SESSIONS-state['sessions'])
    action=('REVIEW_RETAINED_PENDING_READ_NO_AUTOMATIC_RETRY' if pending else
        'REVIEW_RETAINED_BLOCK_NO_AUTOMATIC_RESET' if state['review_required'] else
        'SESSION_HISTORY_CAP_REVIEW_NO_DELETION' if not remaining else
        'RESPECT_RETAINED_COOLDOWN_AND_SEPARATELY_CONFIRM_ONE_READ')
    return dict(mode='PAPER_ACCEPTANCE_PACING_STATUS',status='REVIEW_REQUIRED',read_only=True,
        journal_present=gate.path.is_file(),account_sha256=state['account_sha256'],head_sha256=state['head_sha256'],
        sessions=state['sessions'],session_capacity_remaining=remaining,next_allowed_at=state['next_allowed_at'],
        pending_read=pending,retained_review_block=state['review_required'],suggested_action=action,
        operation_counts=dict(state['operation_counts']),pending_operation=state['pending_operation'],
        legacy_unspecified_sessions=state['operation_counts']['LEGACY_UNSPECIFIED'],
        receipt_summary={key:state[key] for key in ('typed_result_receipts','unreceipted_typed_results',
            'all_typed_results_receipted','target_bound_typed_results','untargeted_typed_results',
            'all_typed_request_targets_bound')},
        operation_identity_attested=False,mixed_operation_acceptance_verified=False,
        **{flag:False for flag in FLAGS},
        limitations='Local replay only, no vault/provider access or admission permission. Missing history does not prove no earlier read. Other/no journals and hosts are not coordinated. Never erase uncertain reads or cooldowns to bypass review.')


def _inspect_paced(*, expected_account_id, pacing_path, confirm_read_only_paper=False,
          opener=None, wall_clock=time.time, elapsed_clock=time.monotonic):
    if confirm_read_only_paper is not True:_fail()
    # Real acceptance data/journals belong outside the public source checkout.
    gate=_gate(expected_account_id,pacing_path);account=gate.account;before=gate.inspect()
    result=paper_credentials.inspect_saved(confirm_read_only_paper=True,expected_account_id=account,
        pacing=gate,opener=opener,wall_clock=wall_clock,elapsed_clock=elapsed_clock)
    observation=portable.validate(result['portable_observation'])
    if observation['account_id']!=account:_fail()
    after=gate.inspect()
    operations=gate.operation_history()
    # Another valid append/change is not silently attributed to this probe. This
    # sampled binding is not a cross-process reservation or broker attestation.
    if (after['sessions']!=before['sessions']+1 or after['events']!=before['events']+2 or
        after['pending_token'] is not None or after['review_required'] is not False or
        after['last_outcome']!='SUCCESS' or not operations or
        operations[-1]['before_head_sha256']!=before['head_sha256'] or
        operations[-1]['after_head_sha256']!=after['head_sha256'] or
        not fixed_targets_match(operations[-1],'ACCOUNT_INVENTORY')):_fail()
    pacing=dict(account_sha256=after['account_sha256'],before_head_sha256=before['head_sha256'],
        after_head_sha256=after['head_sha256'],events=after['events'],sessions=after['sessions'],
        next_allowed_at=after['next_allowed_at'],min_post_result_cooldown_seconds=after['min_post_result_cooldown_seconds'],
        coordination_scope='EXACT_LOCAL_JOURNAL_ONLY_NOT_BROKER_WIDE_OR_DISTRIBUTED')
    return result,observation,pacing


def probe(*, expected_account_id, pacing_path, confirm_read_only_paper=False,
          opener=None, wall_clock=time.time, elapsed_clock=time.monotonic):
    result,observation,pacing=_inspect_paced(expected_account_id=expected_account_id,pacing_path=pacing_path,
        confirm_read_only_paper=confirm_read_only_paper,opener=opener,wall_clock=wall_clock,elapsed_clock=elapsed_clock)
    try:
        raw_terms=result['open_order_terms']
        terms={key:raw_terms[key] for key in TERMS_FIELDS}
        if (set(raw_terms)!=set(TERMS_FIELDS) or terms['reported_rows']!=observation['open_order_count'] or
            terms['scope']!=order_terms.SCOPE or any(terms[flag] is not False for flag in order_terms.FLAGS) or
            terms['summary_sha256']!=sha({key:value for key,value in terms.items() if key!='summary_sha256'})):_fail()
    except (KeyError,TypeError,ValueError,OverflowError,AttributeError):_fail()
    scope=dict(profile='nexus.paper-account-acceptance-record.v1',
        observation_manifest_sha256=observation['manifest_sha256'],
        open_order_terms_sha256=terms['summary_sha256'],account_sha256=pacing['account_sha256'],
        pacing_before_head_sha256=pacing['before_head_sha256'],pacing_after_head_sha256=pacing['after_head_sha256'])
    return dict(mode='PAPER_ACCOUNT_ACCEPTANCE_PROBE',status='REVIEW_REQUIRED',read_completed=True,
        source=observation['source'],portable_observation=observation,local_pacing=pacing,
        reported_open_ioc_limit_terms=terms,acceptance_record_scope=scope,
        acceptance_record_sha256=sha(scope),credential_pair_rechecked=True,
        inventory_only=False,open_order_terms_included=True,
        **{flag:False for flag in FLAGS},
        limitations='One explicitly confirmed fixed-host paper GET session with an already saved Windows pair, account-ID check and local durable pacing. The aggregate open IOC terms come from that same sequential read and are not verified provider holds, execution prices or complete order coverage. This is not provider/source coverage, fee/hold/account reconciliation, broker-wide quota, execution-owner, live/funding/cloud or release acceptance. Only this exact pacing journal coordinates cooperating callers; Portal reads using other/no journals do not share it. Failed calls may already have read remotely or retained a reservation/outcome; inspect retained state and never auto-retry/reset/delete it. No secrets/passwords/tokens/raw paths are included; keep this private report and journal outside Git.')


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PAPER_ACCEPTANCE_PROBE_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__);parser.add_argument('--expected-account',required=True)
    parser.add_argument('--pacing',required=True);parser.add_argument('--confirm-read-only-paper',action='store_true')
    parser.add_argument('--pacing-status-only',action='store_true',help='Replay local pacing only; never read credentials or contact the provider.')
    args=parser.parse_args()
    try:
        if args.pacing_status_only:
            if args.confirm_read_only_paper:_fail()
            result=pacing_status(expected_account_id=args.expected_account,pacing_path=args.pacing)
        else:result=probe(expected_account_id=args.expected_account,pacing_path=args.pacing,confirm_read_only_paper=args.confirm_read_only_paper)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,MemoryError,RecursionError,KeyboardInterrupt):
        print('PAPER_ACCEPTANCE_PROBE_FAILED');return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
