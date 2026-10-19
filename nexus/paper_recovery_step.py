"""One explicitly confirmed, head-fenced original-ID recovery step; never a loop."""
import argparse
import hashlib
import json
import sqlite3
import time

from nexus import alpaca_paper_inspection as transport
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.broker_paper_recovery import recover_original,_storage_paths

FLAGS=('automatic_continuation','automatic_retry','automatic_submission','ready_for_routing',
       'broker_connected','broker_account_reconciled','spendable_funds_verified','ledger_applied',
       'live_orders_enabled','money_movement_enabled','release_approved')


def review_next(journal,*,expected_account_id,evaluated_at,pacing=None):
    """Sample local request/pacing blocks without vault, provider or admission."""
    if type(journal) is not BrokerRequestJournal:raise ValueError('PAPER_RECOVERY_STEP_JOURNAL_REQUIRED')
    account=transport._uuid(expected_account_id)
    if journal.account_digest!=hashlib.sha256(account.encode()).hexdigest():raise ValueError('PAPER_RECOVERY_STEP_ACCOUNT_DISAGREEMENT')
    _storage_paths(journal,pacing)
    chosen=journal.lookup_review(evaluated_at=evaluated_at)
    reasons=list(chosen['reasons']);state=None
    if pacing is not None:
        from nexus.paper_read_pacing import PaperReadPacing,MAX_SESSIONS
        if type(pacing) is not PaperReadPacing or pacing.account!=account:raise ValueError('PAPER_RECOVERY_STEP_PACING_SCOPE')
        state=pacing.inspect()
        if state['pending_token'] is not None:reasons.append('PACING_PENDING_READ_REVIEW_REQUIRED')
        if state['review_required']:reasons.append('PACING_AUTH_OR_REVIEW_REQUIRED')
        if evaluated_at<state['last_evaluated_at']:reasons.append('PACING_CLOCK_REGRESSION')
        if evaluated_at<state['next_allowed_at']:reasons.append('PACING_COOLDOWN_ACTIVE')
        if state['sessions']>=MAX_SESSIONS:reasons.append('PACING_SESSION_CAP_REVIEW_REQUIRED')
        # A sampled review never reserves either journal. Detect an append during
        # composition, while actual admissions still recheck under write locks.
        if (journal.lookup_review(evaluated_at=evaluated_at)!=chosen or pacing.inspect()!=state):
            raise ValueError('PAPER_RECOVERY_STEP_REVIEW_CHANGED')
    return dict(mode='PAPER_RECOVERY_STEP_REVIEW',read_only=True,selection=chosen,pacing_state=state,
        local_checks_clear=not reasons,review_reasons=reasons,admission_reserved=False,
        **{flag:False for flag in FLAGS},
        limitations='Sampled local replay only; a candidate is not permission or a cross-journal reservation. Explicit reads still recheck clocks, keys, ownership, pacing and the exact request head. Pending reads never expire or reset automatically.')


def _selection(journal,expected_account_id,wall_clock,pacing=None):
    reviewed=review_next(journal,expected_account_id=expected_account_id,evaluated_at=wall_clock(),pacing=pacing)
    if not reviewed['local_checks_clear']:raise ValueError('PAPER_RECOVERY_STEP_LOCAL_REVIEW_REQUIRED')
    return reviewed['selection']


def _receipt_before(chosen,journal,pacing):
    if pacing is None:return None
    before=pacing.inspect();requests=journal.recovery()
    if (requests['head_sha256']!=chosen['head_sha256'] or
        pacing.inspect()!=before or journal.recovery()!=requests):
        raise ValueError('PAPER_RECOVERY_STEP_RECEIPT_DISAGREEMENT')
    return before,requests


def _check_receipt(chosen,result,journal,pacing,before):
    """Sample both full replays under local pacing ownership; never accept a tail.

    Successful normalization is co-reported, not provider attestation. Failed
    lookups bind only their attempted targets/outcome and replayed local summary.
    There is no cross-journal transaction or historical recovery acceptance.
    """
    if pacing is None:return
    from nexus.alpaca_paper_contract import sha
    from nexus.broker_lookup_policy import LOOKUP_URL
    from nexus.paper_read_pacing import RECEIPT_PROFILE,RECEIPT_PROFILE_V3
    from nexus.paper_read_projection import matches
    def fail():raise ValueError('PAPER_RECOVERY_STEP_RECEIPT_DISAGREEMENT')
    try:
        with pacing.ownership():
            old,old_requests=before
            after=pacing.inspect();history=pacing.operation_history();current=journal.recovery()
            identity=chosen['selected_client_order_id'];value=result['journal']
            if (after['sessions']!=old['sessions']+1 or after['events']!=old['events']+2 or
                after['pending_token'] is not None or not history or
                current['retained_events']!=old_requests['retained_events']+2):fail()
            record=history[-1];outcome=value['lookup']['last_outcome']
            found=outcome=='FOUND'
            kinds=('ACCOUNT','ORIGINAL_LOOKUP','ACCOUNT') if found else ('ACCOUNT','ORIGINAL_LOOKUP')
            targets=(transport.URLS[0],LOOKUP_URL+'?client_order_id='+identity)
            if found:targets=(*targets,transport.URLS[0])
            if (record['operation']!='ORIGINAL_ID_RECOVERY' or
                record['before_head_sha256']!=old['head_sha256'] or
                record['after_head_sha256']!=after['head_sha256'] or
                record['outcome']!=('SUCCESS' if found else outcome) or
                record['receipt_profile'] not in (RECEIPT_PROFILE,RECEIPT_PROFILE_V3) or
                record['result_receipt_bound'] is not True or record['request_targets_bound'] is not True or
                record['request_kinds']!=kinds or
                record['request_sha256']!=tuple(sha(dict(method='GET',target=target)) for target in targets) or
                value['client_order_id']!=identity):fail()
            if found and not matches(record,'ORIGINAL_ID_RECOVERY',dict(client_order_id=identity,journal=value)):fail()
            selected=[row for row in current['requests'] if row['client_order_id']==identity]
            previous=[row for row in old_requests['requests'] if row['client_order_id']==identity]
            if (len(selected)!=1 or len(previous)!=1 or
                sha({key:value[key] for key in selected[0]})!=sha(selected[0]) or
                selected[0]['payload_sha256']!=previous[0]['payload_sha256'] or
                selected[0]['lookup']['attempts']!=previous[0]['lookup']['attempts']+1 or
                selected[0]['lookup']['pending'] is not False or
                [row for row in current['requests'] if row['client_order_id']!=identity]!=
                [row for row in old_requests['requests'] if row['client_order_id']!=identity] or
                pacing.inspect()!=after or journal.recovery()!=current):fail()
    except (KeyError,TypeError,AttributeError,IndexError):fail()


def _result(chosen,result,journal=None,pacing=None,before=None):
    _check_receipt(chosen,result,journal,pacing,before)
    return dict(mode='EXPLICIT_SINGLE_PAPER_RECOVERY_STEP',source=result['source'],
        selected_client_order_id=chosen['selected_client_order_id'],selection_head_sha256=chosen['head_sha256'],
        selection_policy=chosen['selection_policy'],lookup_attempts_this_step=1,recovery=result,
        **{flag:False for flag in FLAGS},
        limitations='One confirmed read of an original uncertain request, not a background retry loop or resubmission. The exact selection head is rechecked before IO and under journal write ownership. Terminal/capped requests remain for separate review, and no fill/fee/funds or trading authority is granted. Preserve pending outcomes; never automatically retry after failure.')


def recover_next(journal,api_key,api_secret,*,expected_account_id,confirm_read_only_paper=False,
                 opener=None,wall_clock=time.time,elapsed_clock=time.monotonic,
                 permission_check=lambda:True,credential_check=lambda:True,pacing=None,
                 provider_budget=None,expected_provider_budget_head=None):
    if confirm_read_only_paper is not True:raise ValueError('PAPER_RECOVERY_STEP_CONFIRMATION_REQUIRED')
    chosen=_selection(journal,expected_account_id,wall_clock,pacing)
    before=_receipt_before(chosen,journal,pacing)
    result=recover_original(journal,chosen['selected_client_order_id'],api_key,api_secret,
        expected_account_id=expected_account_id,confirm_read_only_paper=True,opener=opener,
        wall_clock=wall_clock,elapsed_clock=elapsed_clock,permission_check=permission_check,
        credential_check=credential_check,pacing=pacing,expected_request_head=chosen['head_sha256'],
        expected_pacing_head=None if before is None else before[0]['head_sha256'],
        provider_budget=provider_budget,expected_provider_budget_head=expected_provider_budget_head)
    return _result(chosen,result,journal,pacing,before)


def recover_next_saved(journal,*,expected_account_id,confirm_read_only_paper=False,
                       opener=None,wall_clock=time.time,elapsed_clock=time.monotonic,pacing=None,
                       provider_budget=None,expected_provider_budget_head=None):
    if confirm_read_only_paper is not True:raise ValueError('PAPER_RECOVERY_STEP_CONFIRMATION_REQUIRED')
    chosen=_selection(journal,expected_account_id,wall_clock,pacing)
    before=_receipt_before(chosen,journal,pacing)
    from nexus.paper_credentials import recover_saved
    result=recover_saved(journal,chosen['selected_client_order_id'],expected_account_id=expected_account_id,
        confirm_read_only_paper=True,opener=opener,wall_clock=wall_clock,elapsed_clock=elapsed_clock,
        pacing=pacing,expected_request_head=chosen['head_sha256'],expected_pacing_head=None if before is None else before[0]['head_sha256'],
        provider_budget=provider_budget,expected_provider_budget_head=expected_provider_budget_head)
    return _result(chosen,result,journal,pacing,before)


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PAPER_RECOVERY_STEP_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__)
    for field in ('expected-account','journal','pacing'):parser.add_argument('--'+field,required=True)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--review-only',action='store_true',help='Read local selection and pacing only; no vault/provider access or missing-file creation.')
    action.add_argument('--confirm-read-only-paper',action='store_true',help='Use saved Windows paper keys for at most one original-ID recovery; no orders, retry or automatic continuation.')
    args=parser.parse_args()
    try:
        from nexus.paper_activity_acceptance_probe import _private_path
        from nexus.paper_acceptance_probe import pacing_status
        from nexus.paper_read_pacing import PaperReadPacing
        account=transport._uuid(args.expected_account)
        request_path,request_resolved=_private_path(args.journal);pacing_path,pacing_resolved=_private_path(args.pacing)
        if request_resolved==pacing_resolved:raise ValueError('PAPER_RECOVERY_STEP_DISTINCT_PATHS_REQUIRED')
        journal=BrokerRequestJournal(request_path,account)
        if args.review_only:
            result=review_next(journal,expected_account_id=account,evaluated_at=time.time(),pacing=PaperReadPacing(pacing_path,account))
            result['pacing']=pacing_status(expected_account_id=account,pacing_path=pacing_path)
        else:result=recover_next_saved(journal,expected_account_id=account,confirm_read_only_paper=True,pacing=PaperReadPacing(pacing_path,account))
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,KeyboardInterrupt,EOFError,MemoryError,RecursionError):
        print('PAPER_RECOVERY_STEP_FAILED');return 2
    print(json.dumps(result,sort_keys=True,indent=2));return 0


def fixture_report():
    """Two separately invoked steps, temporary journals and injected responses."""
    import io
    import tempfile
    from pathlib import Path
    from nexus.alpaca_paper_contract import preview
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    account='01234567-89ab-cdef-0123-456789abcdef'
    raw_account=dict(id=account,currency='USD',status='ACTIVE',crypto_status='ACTIVE',account_blocked=False,trading_blocked=False,trade_suspended_by_user=False,cash='1000',non_marginable_buying_power='900',pending_transfer_in='0',pending_transfer_out='0',accrued_fees='0')
    class Response:
        headers={'Content-Type':'application/json'}
        def __init__(self,url):
            self.url=url;self.status=200 if url==transport.URLS[0] else 404
            self.body=io.BytesIO(json.dumps(raw_account if self.status==200 else {}).encode())
        def geturl(self):return self.url
        def read(self,size):return self.body.read(size)
        def __enter__(self):return self
        def __exit__(self,*args):self.body.close()
    class Reader:
        def __init__(self):self.urls=[]
        def open(self,request,timeout):self.urls.append(request.full_url);return Response(request.full_url)
    with tempfile.TemporaryDirectory(prefix='nexus-recovery-step-') as directory:
        j=BrokerRequestJournal(Path(directory)/'requests.sqlite3',account)
        def make(identity):return preview(identity,'BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
        for identity in ('a_recovery','b_recovery'):j.prepare(make(identity));j.mark_submission_uncertain(identity)
        first_reader=Reader();first=recover_next(j,'fixture_key_only_123','fixture_secret_only_123',expected_account_id=account,confirm_read_only_paper=True,opener=first_reader,wall_clock=lambda:1000,elapsed_clock=lambda:50)
        before=j.path.read_bytes();wait=j.lookup_review(evaluated_at=1001)
        passive=j.path.read_bytes()==before and wait['selected_client_order_id'] is None and 'LOOKUP_COOLDOWN_ACTIVE' in wait['reasons']
        second_reader=Reader();second=recover_next(j,'fixture_key_only_123','fixture_secret_only_123',expected_account_id=account,confirm_read_only_paper=True,opener=second_reader,wall_clock=lambda:1005,elapsed_clock=lambda:50)
        stale_reader=Reader();changed=False;stale=False
        def race():
            nonlocal changed
            if not changed:j.prepare(make('c_prepared'));changed=True
            return True
        try:recover_next(j,'fixture_key_only_123','fixture_secret_only_123',expected_account_id=account,confirm_read_only_paper=True,opener=stale_reader,wall_clock=lambda:1010,elapsed_clock=lambda:50,permission_check=race)
        except ValueError:stale=changed and not stale_reader.urls and all(r['lookup']['attempts']==1 for r in j.recovery()['requests'] if r['submission_attempt_marked'])
        a=first['selected_client_order_id']=='a_recovery';b=second['selected_client_order_id']=='b_recovery'
        return dict(source='TEMPORARY_BOUNDED_RECOVERY_STEP_FIXTURE',passed=a and b and passive and stale,
            first_request_selected=a,next_request_selected=b,cooldown_review_unchanged=passive,
            stale_head_prevented_io=stale,steps_performed=2,get_requests=len(first_reader.urls)+len(second_reader.urls),
            actual_provider_access=False,actual_vault_access=False,**{flag:False for flag in FLAGS})


if __name__=='__main__':raise SystemExit(main())
