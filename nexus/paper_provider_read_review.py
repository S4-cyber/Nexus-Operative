"""Read-only consistency review for provider and account paper-read histories.

This composes already-retained local journals.  It never opens a credential
vault, calls a provider, repairs history, reserves admission or grants broker,
routing, funding, live-trading or release authority.
"""
from collections import Counter
import re

from nexus.alpaca_paper_contract import sha
from nexus.paper_provider_read_budget import PaperProviderReadBudget
from nexus.paper_read_pacing import LEGACY_OPERATION,OPERATIONS,PaperReadPacing
from nexus.sqlite_journal_guard import check_path_set

MAX_ACCOUNTS=6
ZERO='0'*64
PROFILE='nexus.paper-provider-read-review-receipt.v1'
FLAGS=('provider_quota_attested','distributed_ownership_verified','source_attested',
       'source_coverage_verified','clock_domains_verified','broker_account_reconciled',
       'spendable_funds_verified','ready_for_routing','ledger_applied',
       'live_orders_enabled','money_movement_enabled','automatic_retry',
       'automatic_continuation','release_approved')


def _fail(code):raise ValueError('PAPER_PROVIDER_READ_REVIEW_'+code) from None


def _digest(value,code='DIGEST_INVALID'):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}',value) is None:_fail(code)
    return value


def _inputs(provider_budget,gates,expected_account_heads):
    if type(provider_budget) is not PaperProviderReadBudget:_fail('PROVIDER_BUDGET_REQUIRED')
    if type(gates) not in (list,tuple) or not 1<=len(gates)<=MAX_ACCOUNTS:_fail('ACCOUNTS_INVALID')
    if type(expected_account_heads) is not dict:_fail('ACCOUNT_HEADS_INVALID')
    seen=set();checked=[]
    for gate in gates:
        if type(gate) is not PaperReadPacing or gate.account_digest in seen:_fail('ACCOUNTS_INVALID')
        seen.add(gate.account_digest);checked.append(gate)
    if set(expected_account_heads)!=seen:_fail('ACCOUNT_HEADS_INVALID')
    heads={account:_digest(value,'ACCOUNT_HEADS_INVALID') for account,value in expected_account_heads.items()}
    check_path_set((provider_budget.path,*(gate.path for gate in checked)),'PAPER_PROVIDER_READ_REVIEW_')
    return tuple(checked),heads


def _account_rows(gates,reasons):
    rows=[]
    for gate in gates:
        for record in gate.operation_history():
            operation=record['operation'];outcome=record['outcome']
            if operation==LEGACY_OPERATION:
                reasons.add('LEGACY_ACCOUNT_HISTORY');continue
            if outcome in ('PENDING_UNKNOWN','INTERRUPTED_UNKNOWN') or record['after_head_sha256'] is None:
                reasons.add('INCOMPLETE_ACCOUNT_HISTORY');continue
            if not record['result_receipt_bound']:
                reasons.add('UNRECEIPTED_ACCOUNT_HISTORY')
            rows.append((gate.account_digest,record['before_head_sha256'],record['after_head_sha256'],
                operation,outcome,len(record['request_kinds'])))
    return rows


def _provider_rows(history,reasons):
    rows=[]
    for record in history:
        if record['outcome']=='INTERRUPTED_UNKNOWN' or record['account_after_head_sha256'] is None:
            reasons.add('INCOMPLETE_PROVIDER_HISTORY');continue
        rows.append((record['account_sha256'],record['account_before_head_sha256'],
            record['account_after_head_sha256'],record['operation'],record['outcome'],record['actual_gets']))
    return rows


def review(provider_budget,gates,*,expected_provider_head,expected_account_heads):
    """Compare every completed provider reservation with exact account history."""
    expected_provider_head=_digest(expected_provider_head,'PROVIDER_HEAD_INVALID')
    gates,expected_account_heads=_inputs(provider_budget,gates,expected_account_heads)
    provider_before=provider_budget.inspect()
    if provider_before['head_sha256']!=expected_provider_head:_fail('PROVIDER_HEAD_CHANGED')
    account_before={gate.account_digest:gate.inspect() for gate in gates}
    if any(account_before[key]['head_sha256']!=expected_account_heads[key] for key in account_before):
        _fail('ACCOUNT_HEAD_CHANGED')
    reasons=set()
    if provider_before['pending_token'] is not None:reasons.add('PENDING_PROVIDER_HISTORY')
    if provider_before['review_required']:reasons.add('PROVIDER_REVIEW_BLOCK')
    provider_history=provider_budget.history()
    account_rows=_account_rows(gates,reasons);provider_rows=_provider_rows(provider_history,reasons)
    if Counter(account_rows)!=Counter(provider_rows):reasons.add('PROVIDER_ACCOUNT_SESSION_MISMATCH')
    purposes={row[3] for row in provider_rows}
    if not set(OPERATIONS)<=purposes:reasons.add('FIVE_PURPOSE_HISTORY_INCOMPLETE')
    provider_after=provider_budget.inspect()
    account_after={gate.account_digest:gate.inspect() for gate in gates}
    if provider_after!=provider_before:_fail('PROVIDER_HEAD_CHANGED')
    if account_after!=account_before:_fail('ACCOUNT_HEAD_CHANGED')
    manifest=[dict(account_sha256=key,head_sha256=expected_account_heads[key]) for key in sorted(expected_account_heads)]
    consistent=not reasons and len(provider_rows)==len(account_rows)>0
    return dict(mode='READ_ONLY_LOCAL_PROVIDER_PAPER_READ_REVIEW',status='REVIEW_REQUIRED',read_only=True,
        provider_sha256=provider_budget.provider_digest,provider_head_sha256=expected_provider_head,
        account_manifest_sha256=sha(manifest),account_journals=len(gates),provider_sessions_replayed=len(provider_history),
        account_sessions_replayed=len(account_rows),matched_sessions=len(provider_rows) if consistent else 0,
        operation_counts={operation:sum(row[3]==operation for row in provider_rows) for operation in OPERATIONS},
        all_five_purposes_observed=set(OPERATIONS)<=purposes,local_provider_account_history_consistent=consistent,
        review_reasons=sorted(reasons),admission_reserved=False,**{flag:False for flag in FLAGS},
        limitations='Exact-head replay of explicitly supplied local journals only. Matching digests do not attest provider identity, quotas, another path/credential/host, source coverage, clocks, fees, holds, funds, routing or release. No reservation, retry, repair or authority is granted.')


def retain_review(store_path,provider_budget,gates,*,expected_provider_head,expected_account_heads,
                  expected_store_head=ZERO,precommit_check=lambda:True):
    """Retain a content-free digest receipt for one complete local review."""
    from nexus.paper_provider_read_review_store import PaperProviderReadReviewStore
    gates,expected_account_heads=_inputs(provider_budget,gates,expected_account_heads)
    def scope():
        result=review(provider_budget,gates,expected_provider_head=expected_provider_head,
            expected_account_heads=expected_account_heads)
        if not result['local_provider_account_history_consistent']:_fail('INCOMPLETE')
        return dict(profile=PROFILE,provider_sha256=result['provider_sha256'],
            provider_head_sha256=result['provider_head_sha256'],account_manifest_sha256=result['account_manifest_sha256'],
            review_sha256=sha(result),account_journals=result['account_journals'],
            sessions_replayed=result['provider_sessions_replayed'],matched_sessions=result['matched_sessions'],
            operation_counts=result['operation_counts'],all_five_purposes_observed=result['all_five_purposes_observed'],
            local_provider_account_history_consistent=result['local_provider_account_history_consistent'])
    value=scope();receipt=sha(value)
    store=PaperProviderReadReviewStore(store_path,provider_budget,gates)
    return store.append(value,expected_head=expected_store_head,admission=lambda:sha(scope()),
        precommit_check=precommit_check)
