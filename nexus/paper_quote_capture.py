"""Explicit saved-pair BTC quote capture, private retention and offline review.

No default paths, startup work, order methods or automatic retry. This diagnostic
binds local read/retention history, never source, decision/arrival timing or funds.
Do not mix these sessions into an activity-acceptance pacing journal.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import time

from nexus import paper_quote_store as quotes,paper_quote_observation as observation,paper_credentials
from nexus.alpaca_paper_contract import sha
from nexus.broker_lookup_policy import clock
from nexus.paper_activity_acceptance_probe import _private_path
from nexus.paper_read_pacing import PaperReadPacing, fixed_targets_match
from nexus.sqlite_journal_guard import check_path_set
from nexus.paper_read_projection import matches as projection_matches

FLAGS=(*observation.FLAGS,'capture_source_attested','pacing_causality_attested','broker_wide_quota_verified','windows_profile_accepted')
CHECKS=('three_fixed_gets','one_local_transition_bound','quote_retained','offline_status_matches',
    'cooldown_blocks_without_vault','original_journal_unchanged','authority_withheld')


def _fail(code):raise ValueError('PAPER_QUOTE_CAPTURE_'+code)


def _stores(expected_account_id,request_journal_path,quote_store_path,pacing_path):
    resolved=[_private_path(p)[1] for p in (request_journal_path,quote_store_path,pacing_path)]
    if len(set(resolved))!=3:_fail('DISTINCT_PATHS_REQUIRED')
    # Reject paths that occupy another selected SQLite recovery file or the
    # pacing ownership file before a provider read can be attempted.
    check_path_set(resolved,'PAPER_QUOTE_CAPTURE_')
    store=quotes.PaperQuoteStore(quote_store_path,expected_account_id,request_journal_path)
    gate=PaperReadPacing(pacing_path,expected_account_id)
    # Bind the selected local scope, not a portable path, file attestation,
    # central provider quota or identity on another host.
    scope=sha(dict(account_sha256=gate.account_digest,local_pacing_path=str(resolved[2])))
    return store,gate,scope


def _review(store,gate,scope,expected_request_head):
    before=gate.inspect();history=store.inspect(expected_request_head=expected_request_head)
    sessions=gate.completed_sessions();operations=gate.operation_history();records=history['records'];reasons=set()
    if any(r['operation'] not in ('BTC_QUOTE_OBSERVATION','LEGACY_UNSPECIFIED') for r in operations):reasons.add('PACING_OPERATION_DISAGREEMENT')
    if any(r['operation']!='LEGACY_UNSPECIFIED' and not r['result_receipt_bound'] for r in operations):
        reasons.add('PACING_RESULT_RECEIPT_UNVERIFIED')
    if any(r['operation']!='LEGACY_UNSPECIFIED' and not fixed_targets_match(r,'BTC_QUOTE_OBSERVATION') for r in operations):
        reasons.add('PACING_REQUEST_TARGET_UNVERIFIED')
    if any(r['operation']=='LEGACY_UNSPECIFIED' for r in operations):reasons.add('PACING_OPERATION_UNVERIFIED')
    if before['pending_token'] is not None:reasons.add('PENDING_READ_REQUIRES_REVIEW')
    if before['review_required'] or before['interruptions']:reasons.add('RETAINED_PACING_REVIEW_REQUIRED')
    if any(r['profile']!=quotes.CAPTURE_PROFILE for r in records):reasons.add('SUPPLIED_QUOTES_WITHOUT_CAPTURE_BINDING')
    captured=[r for r in records if r['profile']==quotes.CAPTURE_PROFILE]
    if any(r['pacing_scope_sha256']!=scope for r in captured):reasons.add('PACING_SCOPE_DISAGREEMENT')
    completed_operations={(r['before_head_sha256'],r['after_head_sha256']):r for r in operations}
    if any(not projection_matches(completed_operations.get((r['pacing_before_head_sha256'],r['pacing_after_head_sha256'])),
        'BTC_QUOTE_OBSERVATION',r['observation']) for r in captured):
        reasons.add('PACING_NORMALIZED_RESULT_UNVERIFIED')
    bindings=[dict(before_head_sha256=r['pacing_before_head_sha256'],after_head_sha256=r['pacing_after_head_sha256'],outcome='SUCCESS') for r in captured]
    if bindings!=list(sessions) or before['sessions']!=len(captured):reasons.add('UNBOUND_OR_DISAGREEING_PACING_SESSIONS')
    if not records and before['head_sha256']!=quotes.ZERO:reasons.add('PREEXISTING_UNBOUND_PACING_HISTORY')
    if (gate.inspect()!=before or store.inspect(expected_request_head=expected_request_head)!=history or
        gate.completed_sessions()!=sessions or gate.operation_history()!=operations):_fail('INPUTS_CHANGED_DURING_REVIEW')
    result=dict(mode='PAPER_QUOTE_CAPTURE_STATUS',status='REVIEW_REQUIRED',read_only=True,
        account_sha256=store.account,pacing_scope_sha256=scope,request_head_sha256=expected_request_head,
        quote_head_sha256=history['head_sha256'],pacing_head_sha256=before['head_sha256'],
        retained_quotes=len(records),retained_pacing_sessions=before['sessions'],
        pending_read=before['pending_token'] is not None,next_allowed_at=before['next_allowed_at'],
        local_capture_history_agreement=not reasons,review_reasons=sorted(reasons),
        **{k:False for k in FLAGS},
        limitations='Full retained local request/quote/pacing replay only. One matching captured quote per successful local pacing transition is not provider causality, authenticated source, complete data, synchronized clocks or an order-event benchmark. Missing history does not exclude deleted history or another host. Separate files are sampled, not atomic. No vault/network/write/retry/reset or routing. Keep this report and all journals private.')
    return result,history,before


def status(*,expected_account_id,request_journal_path,quote_store_path,pacing_path,expected_request_head):
    store,gate,scope=_stores(expected_account_id,request_journal_path,quote_store_path,pacing_path)
    return _review(store,gate,scope,expected_request_head)[0]


def capture(*,expected_account_id,request_journal_path,quote_store_path,pacing_path,expected_request_head,
            expected_quote_head,expected_pacing_head,client_order_id,confirm_read_only_paper=False,
            opener=None,wall_clock=time.time,elapsed_clock=time.monotonic):
    return _capture(expected_account_id=expected_account_id,request_journal_path=request_journal_path,
        quote_store_path=quote_store_path,pacing_path=pacing_path,expected_request_head=expected_request_head,
        expected_quote_head=expected_quote_head,expected_pacing_head=expected_pacing_head,client_order_id=client_order_id,
        confirm_read_only_paper=confirm_read_only_paper,opener=opener,wall_clock=wall_clock,elapsed_clock=elapsed_clock,
        saved_reader=paper_credentials._consume_quote_saved)


def _capture(*,expected_account_id,request_journal_path,quote_store_path,pacing_path,expected_request_head,
             expected_quote_head,expected_pacing_head,client_order_id,confirm_read_only_paper=False,
             opener=None,wall_clock=time.time,elapsed_clock=time.monotonic,saved_reader):
    if confirm_read_only_paper is not True:_fail('EXPLICIT_CONFIRMATION_REQUIRED')
    quotes._head(expected_quote_head);quotes._head(expected_pacing_head)
    store,gate,scope=_stores(expected_account_id,request_journal_path,quote_store_path,pacing_path)
    before,history,pacing_before=_review(store,gate,scope,expected_request_head)
    if not before['local_capture_history_agreement']:_fail('UNBOUND_HISTORY_REVIEW_REQUIRED')
    if before['quote_head_sha256']!=expected_quote_head or before['pacing_head_sha256']!=expected_pacing_head:_fail('HEAD_CHANGED')
    requests=store._requests(expected_request_head)
    if type(client_order_id) is not str or client_order_id not in requests['plans']:_fail('ORIGINAL_REQUEST_REQUIRED')
    if len(history['records'])>=quotes.MAX_RECORDS:_fail('QUOTE_CAPACITY')
    gate._admit(pacing_before,clock(wall_clock()))  # Refuse before vault/IO; begin checks again under ownership.
    retaining=False
    def stable():
        try:
            if store._requests(expected_request_head)!=requests:return False
            if not retaining:
                current=store.inspect(expected_request_head=expected_request_head)
                if current!=history:return False
            return True
        except (OSError,ValueError,sqlite3.Error):return False
    def consume(value,credential_check):
        nonlocal retaining
        # The reader has durably finished and released its read lease. Reacquire
        # before retention, verify the exact one new session and hold through
        # commit/cleanup. Any intervening change refuses without retry/repair.
        with gate.ownership() as owner:
            after=gate.inspect();completed=gate.completed_sessions()
            transition=dict(before_head_sha256=expected_pacing_head,after_head_sha256=after['head_sha256'],outcome='SUCCESS')
            if (after['sessions']!=pacing_before['sessions']+1 or after['events']!=pacing_before['events']+2 or
                after['pending_token'] is not None or after['review_required'] or after['last_outcome']!='SUCCESS' or
                len(completed)!=after['sessions'] or not completed or completed[-1]!=transition or
                list(completed[:-1])!=[dict(before_head_sha256=r['pacing_before_head_sha256'],after_head_sha256=r['pacing_after_head_sha256'],outcome='SUCCESS') for r in history['records']]):
                _fail('PACING_TRANSITION_DISAGREEMENT')
            operations=gate.operation_history()
            if (not operations or operations[-1]['before_head_sha256']!=expected_pacing_head or
                operations[-1]['after_head_sha256']!=after['head_sha256'] or
                not fixed_targets_match(operations[-1],'BTC_QUOTE_OBSERVATION')):
                _fail('PACING_REQUEST_TARGET_DISAGREEMENT')
            quotes._observation(value,store.account)
            if not projection_matches(operations[-1],'BTC_QUOTE_OBSERVATION',value):
                _fail('PACING_NORMALIZED_RESULT_DISAGREEMENT')
            if not stable():_fail('INPUTS_CHANGED_AFTER_READ')
            retaining=True
            def admit():
                owner.validate()
                if gate.inspect()!=after or not stable():return False
                return credential_check() is True
            binding=dict(pacing_scope_sha256=scope,pacing_before_head_sha256=expected_pacing_head,pacing_after_head_sha256=after['head_sha256'])
            receipt=store.retain(value,client_order_id=client_order_id,expected_request_head=expected_request_head,
                expected_quote_head=expected_quote_head,capture_binding=binding,precommit_check=admit)
            reviewed=_review(store,gate,scope,expected_request_head)[0]
            if (not reviewed['local_capture_history_agreement'] or reviewed['quote_head_sha256']!=receipt['head_sha256'] or
                reviewed['pacing_head_sha256']!=after['head_sha256'] or admit() is not True):_fail('POST_COMMIT_INPUTS_CHANGED')
            record_scope=dict(profile='nexus.paper-quote-capture-receipt.v1',account_sha256=store.account,
                request_head_sha256=expected_request_head,quote_head_sha256=receipt['head_sha256'],
                quote_record_sha256=receipt['record_sha256'],observation_sha256=value['observation_sha256'],**binding)
            return dict(mode='EXPLICIT_PAPER_QUOTE_CAPTURE',status='REVIEW_REQUIRED',**record_scope,
                receipt_sha256=sha(record_scope),fixed_get_requests=3,local_capture_history_agreement=True,
                **{k:False for k in FLAGS},
                limitations='One explicitly confirmed saved-pair fixed-host paper quote read and bounded private retention. Exact local original-request and pacing transitions are content bindings, not provider/source/clock/event attestation, a verified decision/arrival benchmark, funds or order permission. Capture does not submit, retry or automatically continue. Failures may retain pending/completed reads or committed quotes: inspect without deleting/rebaselining history. Keep output private.')
    result=saved_reader(expected_account_id=gate.account,confirm_read_only_paper=True,pacing=gate,opener=opener,
        wall_clock=wall_clock,elapsed_clock=elapsed_clock,consumer=consume,permission_check=stable,expected_pacing_head=expected_pacing_head)
    final=status(expected_account_id=gate.account,request_journal_path=request_journal_path,quote_store_path=quote_store_path,
        pacing_path=pacing_path,expected_request_head=expected_request_head)
    if (final['local_capture_history_agreement'] is not True or final['quote_head_sha256']!=result['quote_head_sha256'] or
        final['pacing_head_sha256']!=result['pacing_after_head_sha256']):_fail('FINAL_HISTORY_CHANGED')
    return result


def _fixture(root):
    """Disposable real reader/retention with an injected saved-reader seam."""
    import io
    from nexus.alpaca_paper_contract import preview
    account='01234567-89ab-cdef-0123-456789abcdef';root=Path(root)
    store=quotes.PaperQuoteStore(root/'quotes.sqlite3',account,root/'requests.sqlite3')
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,
        min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    store.journal.prepare(preview('nx_quote_fixture','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001))
    head=store.journal.recovery()['head_sha256'];original=store.request_path.read_bytes()
    raw_account=dict(id=account,currency='USD',status='ACTIVE',crypto_status='ACTIVE',account_blocked=False,
        trading_blocked=False,trade_suspended_by_user=False,cash='1000',non_marginable_buying_power='900',
        pending_transfer_in='0',pending_transfer_out='0',accrued_fees='0')
    raw_quote={'quotes':{'BTC/USD':dict(bp='100',ap='101',bs='2',**{'as':'3'},t='2026-10-17T00:00:00.123456789Z')}}
    class Response:
        status=200;headers={'Content-Type':'application/json'}
        def __init__(self,url,value):self.url=url;self.body=io.BytesIO(json.dumps(value).encode())
        def geturl(self):return self.url
        def read(self,size):return self.body.read(size)
        def __enter__(self):return self
        def __exit__(self,*args):self.body.close()
    class Opener:
        def __init__(self):self.values=iter([raw_account,raw_quote,raw_account]);self.requests=[]
        def open(self,req,timeout):self.requests.append(req);return Response(req.full_url,next(self.values))
    opener=Opener();calls=[]
    def saved_reader(*,consumer,permission_check,**args):
        calls.append(1)
        value=observation.inspect_quote('fixture_key_only_123','fixture_secret_only_123',permission_check=permission_check,**args)
        if permission_check() is not True:_fail('FIXTURE_CHANGED')
        return consumer(value,permission_check)
    options=dict(expected_account_id=account,request_journal_path=store.request_path,quote_store_path=store.path,
        pacing_path=root/'pacing.sqlite3',expected_request_head=head)
    args=dict(**options,client_order_id='nx_quote_fixture',expected_quote_head=quotes.ZERO,expected_pacing_head=quotes.ZERO,
        confirm_read_only_paper=True,opener=opener,wall_clock=lambda:1792195201,elapsed_clock=lambda:50,saved_reader=saved_reader)
    receipt=_capture(**args);reviewed=status(**options);before={p.name:p.read_bytes() for p in root.iterdir()}
    args.update(expected_quote_head=receipt['quote_head_sha256'],expected_pacing_head=receipt['pacing_after_head_sha256'])
    blocked=False
    try:_capture(**args)
    except ValueError:blocked=len(calls)==1 and before=={p.name:p.read_bytes() for p in root.iterdir()}
    retained=store.inspect(expected_request_head=head)
    checks=dict(three_fixed_gets=len(opener.requests)==3 and all(r.method=='GET' and r.data is None for r in opener.requests),
        one_local_transition_bound=reviewed['local_capture_history_agreement'] and reviewed['retained_pacing_sessions']==1,
        quote_retained=retained['retained_records']==1 and retained['records'][0]['profile']==quotes.CAPTURE_PROFILE,
        offline_status_matches=reviewed['quote_head_sha256']==receipt['quote_head_sha256'],
        cooldown_blocks_without_vault=blocked,original_journal_unchanged=store.request_path.read_bytes()==original,
        authority_withheld=all(receipt[k] is False and reviewed[k] is False for k in FLAGS))
    return options,dict(source='INJECTED_PAPER_QUOTE_CAPTURE_FIXTURE',passed=all(checks.values()),**checks,
        actual_provider_access=False,actual_vault_access=False,**{k:False for k in FLAGS})


def fixture_report():
    import tempfile
    with tempfile.TemporaryDirectory(prefix='nexus-quote-capture-') as directory:return _fixture(Path(directory))[1]


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PAPER_QUOTE_CAPTURE_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__)
    parser.add_argument('--expected-account',required=True,help='Exact paper account UUID; never a credential.')
    parser.add_argument('--request-journal',required=True,help='Private retained original requests; never creates/submits an order.')
    parser.add_argument('--quote-store',required=True,help='Private bounded quote journal outside the app/source directory; no pruning or automatic repair.')
    parser.add_argument('--pacing',required=True,help='Private cooperating quote-capture pacing history; do not mix with accepted activity history or bypass provider-wide limits.')
    parser.add_argument('--request-head',required=True,help='Exact current original-request history SHA256; changed history refuses.')
    parser.add_argument('--status-only',action='store_true',help='Replay private local history without credentials, provider calls or writes.')
    parser.add_argument('--quote-head',help='Exact quote head from status; use64 zeros only for absent history.')
    parser.add_argument('--pacing-head',help='Exact pacing head from status; use64 zeros only for absent history.')
    parser.add_argument('--client-order-id',help='Existing original request to associate; this read does not mark an order decision or arrival.')
    parser.add_argument('--confirm-read-only-paper',action='store_true',help='Separately authorize exactly one three-GET saved-pair quote read and private retention; no orders.')
    args=parser.parse_args()
    try:
        common=dict(expected_account_id=args.expected_account,request_journal_path=args.request_journal,quote_store_path=args.quote_store,
            pacing_path=args.pacing,expected_request_head=args.request_head)
        if args.status_only:
            if args.confirm_read_only_paper or any(v is not None for v in (args.quote_head,args.pacing_head,args.client_order_id)):_fail('STATUS_ARGUMENTS')
            result=status(**common)
        else:
            result=capture(**common,expected_quote_head=args.quote_head,expected_pacing_head=args.pacing_head,
                client_order_id=args.client_order_id,confirm_read_only_paper=args.confirm_read_only_paper)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,UnicodeError,OverflowError,MemoryError,RecursionError,KeyboardInterrupt):
        print('PAPER_QUOTE_CAPTURE_FAILED');return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
