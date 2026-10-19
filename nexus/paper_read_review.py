"""Bounded read-only review of supplied results against one local read history.

Co-reported consistency only: no provider attestation, credential/network access,
reservation, journal writes, import, repair, retry, acceptance or order authority.
"""
import json
import re

from nexus.alpaca_paper_contract import canonical,sha
from nexus import paper_read_pacing as pacing
from nexus import paper_read_projection as projection

MAX_BINDING_BYTES=2*1024*1024
MAX_SCOPE_BYTES=256*1024
MAX_DEPTH=32
MAX_NODES=131072
FLAGS=('source_attested','source_coverage_verified','clock_domains_verified',
       'mixed_operation_acceptance_verified','broker_wide_quota_verified',
       'broker_account_reconciled','spendable_funds_verified','ready_for_routing',
       'ledger_applied','live_orders_enabled','money_movement_enabled',
       'automatic_retry','automatic_continuation','release_approved')
CHECKS=('five_actual_results_match','omission_refused','changed_result_refused',
        'legacy_tail_unverified','review_preserved_files','durable_receipt_replays',
        'private_results_not_retained')


def _fail(code):raise ValueError('PAPER_READ_REVIEW_'+code) from None


def _hash(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def _structure(value):
    stack=[(value,0)];nodes=0;text_units=0
    while stack:
        item,depth=stack.pop();nodes+=1
        if nodes>MAX_NODES or depth>MAX_DEPTH:_fail('BINDINGS_INVALID')
        if type(item) in (dict,list):
            if nodes+len(stack)+len(item)>MAX_NODES:_fail('BINDINGS_INVALID')
            if type(item) is dict:
                if any(type(key) is not str for key in item):_fail('BINDINGS_INVALID')
                text_units+=sum(len(key) for key in item)
            stack.extend((child,depth+1) for child in (item.values() if type(item) is dict else item))
        elif type(item) not in (str,int,float,bool,type(None)):_fail('BINDINGS_INVALID')
        elif type(item) is str:text_units+=len(item)
        elif type(item) is int:text_units+=item.bit_length()//4
        if text_units>MAX_BINDING_BYTES:_fail('BINDINGS_INVALID')


def _bindings(values):
    if type(values) is not list or len(values)>pacing.MAX_SESSIONS:_fail('BINDINGS_INVALID')
    _structure(values)
    try:
        encoded=canonical(values)
        if len(encoded.encode())>MAX_BINDING_BYTES:_fail('BINDINGS_INVALID')
        frozen=json.loads(encoded);heads=set()
        for value in frozen:
            if (type(value) is not dict or set(value)!={'after_head_sha256','normalized_result','request_scope'} or
                not _hash(value['after_head_sha256']) or value['after_head_sha256'] in heads or
                len(canonical(value).encode())>MAX_SCOPE_BYTES):_fail('BINDINGS_INVALID')
            heads.add(value['after_head_sha256'])
        return {value['after_head_sha256']:value for value in frozen}
    except (TypeError,KeyError,OverflowError,RecursionError,ValueError):_fail('BINDINGS_INVALID')


def _scope_matches(record,binding,gate):
    """Reconstruct dynamic GET scope; never accept caller-selected arbitrary URLs."""
    operation=record['operation'];value=binding['normalized_result'];scope=binding['request_scope']
    try:
        if operation in ('ACCOUNT_INVENTORY','BTC_ASSET_RULES','BTC_QUOTE_OBSERVATION'):
            account_ok=(value.get('account_sha256')==gate.account_digest if operation=='BTC_QUOTE_OBSERVATION'
                else value.get('account_id')==gate.account)
            return scope is None and account_ok and pacing.fixed_targets_match(record,operation)
        if operation=='ORIGINAL_ID_RECOVERY':
            from nexus.broker_request_journal import BrokerRequestJournal
            from nexus.broker_lookup_policy import LOOKUP_URL
            from nexus.alpaca_paper_inspection import URLS
            if scope is not None or type(value) is not dict or set(value)!={'client_order_id','journal'}:return False
            identity=value['client_order_id'];BrokerRequestJournal._identity(identity)
            targets=(URLS[0],LOOKUP_URL+'?client_order_id='+identity,URLS[0])
            return (value['journal']['client_order_id']==identity and
                record['request_kinds']==('ACCOUNT','ORIGINAL_LOOKUP','ACCOUNT') and
                record['request_sha256']==tuple(sha(dict(method='GET',target=target)) for target in targets))
        if operation=='ACTIVITY_COLLECTION':
            from nexus.broker_activity_store import _inputs,_raw
            from nexus.activity_collection_evidence import validate,request_targets_match
            if (type(scope) is not dict or set(scope)!={'account_sha256','after','until','orders','activities','collection_observation'} or
                scope['account_sha256']!=gate.account_digest or sha(scope)!=value):return False
            data=_inputs([_raw(row) for row in scope['activities']],
                [dict(order,symbol='BTC/USD') for order in scope['orders']],gate.account)
            if canonical(data)!=canonical(dict(orders=scope['orders'],activities=scope['activities'])):return False
            checked=validate(scope['collection_observation'],data,after=scope['after'],until=scope['until'])
            return canonical(checked)==canonical(scope['collection_observation']) and request_targets_match(record,scope)
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError):return False
    return False


def review(gate,bindings,*,expected_head):
    """Replay every retained session and freeze supplied bindings; no missing-file creation.

    This sampled review is not an atomic reservation across processes. Hashes
    cannot authenticate a provider or detect a valid removed/replaced tail.
    """
    if type(gate) is not pacing.PaperReadPacing:_fail('PACING_REQUIRED')
    if not _hash(expected_head):_fail('HEAD_INVALID')
    values=_bindings(bindings)
    before=gate.inspect()
    if before['head_sha256']!=expected_head:_fail('HEAD_CHANGED')
    history=gate.operation_history();reasons=set();matched=0;purposes=set();successful=set()
    if not history:reasons.add('NO_RETAINED_HISTORY')
    for record in history:
        operation=record['operation']
        if operation==pacing.LEGACY_OPERATION:reasons.add('LEGACY_PURPOSE_UNVERIFIED')
        if record['outcome']!='SUCCESS':
            reasons.add('UNSUCCESSFUL_OR_UNKNOWN_HISTORY');continue
        head=record['after_head_sha256'];successful.add(head)
        binding=values.get(head)
        if binding is None:reasons.add('SUCCESSFUL_RESULT_BINDING_MISSING');continue
        if not projection.matches(record,operation,binding['normalized_result']):
            reasons.add('NORMALIZED_RESULT_UNVERIFIED');continue
        if not _scope_matches(record,binding,gate):reasons.add('REQUEST_SCOPE_UNVERIFIED');continue
        matched+=1;purposes.add(operation)
    if set(values)-successful:reasons.add('BINDING_HEAD_NOT_A_SUCCESSFUL_SESSION')
    if before['review_required']:reasons.add('RETAINED_REVIEW_BLOCK')
    if gate.inspect()!=before:_fail('HEAD_CHANGED')
    complete=bool(history) and not reasons and matched==len(history)
    return dict(mode='READ_ONLY_LOCAL_MIXED_PAPER_READ_REVIEW',status='REVIEW_REQUIRED',read_only=True,
        account_sha256=gate.account_digest,head_sha256=expected_head,sessions_replayed=len(history),
        supplied_bindings=len(values),matched_sessions=matched,
        operation_counts=dict(before['operation_counts']),receipt_bindings_consistent=complete,
        all_five_purposes_observed=set(pacing.OPERATIONS)<=purposes,
        complete_mixed_history_consistent=complete and set(pacing.OPERATIONS)<=purposes,
        review_reasons=sorted(reasons),admission_reserved=False,**{flag:False for flag in FLAGS},
        limitations='Supplied normalized results and one fully replayed local journal only. Co-reported hashes do not prove normalization from response bytes, provider identity, coverage, clock domains, fees, holds, funds, provider-wide quotas or valid-tail preservation. No history import/repair, reservation, retry, routing, account or release acceptance.')


def retain_review(store_path,gate,bindings,*,expected_head,expected_store_head='0'*64,precommit_check=lambda:True):
    """Retain only content digests for one complete review; never raw observations."""
    from nexus.paper_read_review_store import PaperReadReviewStore,PROFILE
    frozen=_bindings(bindings)
    frozen_values=[value for _,value in sorted(frozen.items())]
    manifest=[dict(after_head_sha256=value['after_head_sha256'],normalized_result_sha256=sha(value['normalized_result']),
        request_scope_sha256=sha(value['request_scope'])) for value in frozen_values]
    manifest_sha=sha(manifest)
    def scope():
        result=review(gate,frozen_values,expected_head=expected_head)
        if not result['complete_mixed_history_consistent']:_fail('INCOMPLETE')
        return dict(profile=PROFILE,account_sha256=result['account_sha256'],pacing_head_sha256=expected_head,
            review_sha256=sha(result),binding_manifest_sha256=manifest_sha,sessions_replayed=result['sessions_replayed'],
            supplied_bindings=result['supplied_bindings'],matched_sessions=result['matched_sessions'],
            operation_counts=result['operation_counts'],all_five_purposes_observed=result['all_five_purposes_observed'],
            complete_mixed_history_consistent=result['complete_mixed_history_consistent'])
    value=scope();receipt=sha(value)
    store=PaperReadReviewStore(store_path,gate)
    return store.append(value,expected_head=expected_store_head,admission=lambda:sha(scope()),precommit_check=precommit_check)


def _fixture(root,*,full_activity=False):
    """Five actual bounded readers, disposable history and injected response bytes."""
    import io
    from nexus import alpaca_paper_inspection as reader
    from nexus.paper_asset_rules import inspect_rules
    from nexus.alpaca_activity_collection import collect_activities
    from nexus.broker_paper_recovery import recover_original
    from nexus.paper_quote_observation import inspect_quote
    from nexus.broker_request_journal import BrokerRequestJournal
    from nexus.alpaca_paper_contract import preview
    account='01234567-89ab-cdef-0123-456789abcdef';epoch=1792368000
    raw_account=dict(id=account,currency='USD',status='ACTIVE',crypto_status='ACTIVE',account_blocked=False,
        trading_blocked=False,trade_suspended_by_user=False,cash='1000',non_marginable_buying_power='900',
        pending_transfer_in='0',pending_transfer_out='0',accrued_fees='0')
    asset=dict(id='11234567-89ab-cdef-0123-456789abcdef',symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,
        fractionable=True,min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    quote={'quotes':{'BTC/USD':dict(bp='100',ap='101',bs='2',**{'as':'3'},t='2026-10-19T00:04:00.123456789Z')}}
    class Response:
        status=200;headers={'Content-Type':'application/json'}
        def __init__(self,url,value):self.url=url;self.body=io.BytesIO(json.dumps(value).encode())
        def geturl(self):return self.url
        def read(self,size):return self.body.read(size)
        def __enter__(self):return self
        def __exit__(self,*args):self.body.close()
    class Opener:
        def __init__(self,values):self.values=iter(values)
        def open(self,request,timeout):return Response(request.full_url,next(self.values))
    gate=pacing.PaperReadPacing(root/'pacing.sqlite3',account);bindings=[]
    key='fixture_key_only_123';secret='fixture_secret_only_123'
    def args(values,at):return dict(expected_account_id=account,confirm_read_only_paper=True,pacing=gate,
        opener=Opener(values),wall_clock=lambda:at,elapsed_clock=lambda:50)
    def retain(value,scope=None):bindings.append(dict(after_head_sha256=gate.inspect()['head_sha256'],normalized_result=value,request_scope=scope))
    retain(reader.inspect_account(key,secret,**args([raw_account,[],[],raw_account],epoch)))
    retain(inspect_rules(key,secret,**args([raw_account,asset,raw_account],epoch+60)))
    after='2026-10-17T00:00:00Z';until='2026-10-18T00:00:00Z'
    rows=([dict(id='20261017000000000::11234567-89ab-cdef-0123-'+f'{i:012d}',activity_type='FEE',status='executed',net_amount='-0.01',date='2026-10-16') for i in range(1,101)] if full_activity else [])
    pages=[rows,[]] if full_activity else [[]]
    collected=collect_activities(key,secret,[],after=after,until=until,**args([raw_account,*pages,raw_account],epoch+120))
    from nexus.broker_activity_store import _inputs
    payload=dict(account_sha256=gate.account_digest,after=after,until=until,**_inputs(rows,[],account),collection_observation=collected['collection_observation'])
    retain(sha(payload),payload)
    journal=BrokerRequestJournal(root/'requests.sqlite3',account)
    plan=preview('nx_review_fixture','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
    journal.prepare(plan);journal.mark_submission_uncertain('nx_review_fixture')
    raw_order=dict(plan['payload'],id=asset['id'],asset_class='crypto',status='canceled',filled_qty='0',filled_avg_price=None,updated_at='2026-10-19T00:03:00Z')
    recovered=recover_original(journal,'nx_review_fixture',key,secret,**args([raw_account,raw_order,raw_account],epoch+180))
    retain(dict(client_order_id='nx_review_fixture',journal=recovered['journal']))
    retain(inspect_quote(key,secret,**args([raw_account,quote,raw_account],epoch+241)))
    return gate,bindings


def fixture_report():
    import tempfile
    from pathlib import Path
    from copy import deepcopy
    with tempfile.TemporaryDirectory(prefix='nexus-mixed-read-review-') as directory:
        root=Path(directory);gate,bindings=_fixture(root,full_activity=True);head=gate.inspect()['head_sha256']
        files=lambda:{p.name:p.read_bytes() for p in root.iterdir()}
        before=files();result=review(gate,bindings,expected_head=head)
        omitted=review(gate,bindings[1:],expected_head=head)
        changed=deepcopy(bindings);changed[0]['normalized_result']['cash']='999'
        different=review(gate,changed,expected_head=head)
        unchanged=files()==before
        receipt=retain_review(root/'review.sqlite3',gate,bindings,expected_head=head)
        from nexus.paper_read_review_store import PaperReadReviewStore
        retained=PaperReadReviewStore(root/'review.sqlite3',gate).inspect()
        store_bytes=(root/'review.sqlite3').read_bytes()
        token=gate.begin(admission=lambda:1792368400);gate.finish(token,admission=lambda:1792368400,status_code=200)
        before=files();legacy=review(gate,bindings,expected_head=gate.inspect()['head_sha256'])
        checks=dict(five_actual_results_match=result['complete_mixed_history_consistent'],
            omission_refused=not omitted['receipt_bindings_consistent'],changed_result_refused=not different['receipt_bindings_consistent'],
            legacy_tail_unverified=not legacy['receipt_bindings_consistent'] and 'LEGACY_PURPOSE_UNVERIFIED' in legacy['review_reasons'],
            review_preserved_files=unchanged,
            durable_receipt_replays=retained['retained_receipts']==1 and retained['head_sha256']==receipt['head_sha256'],
            private_results_not_retained=all(token not in store_bytes for token in
                (b'fixture_key_only_123',b'fixture_secret_only_123',b'nx_review_fixture',b'BTC/USD',b'1000')))
        return dict(source='TEMPORARY_INJECTED_MIXED_READ_REVIEW',passed=all(checks.values()),**checks,**{flag:False for flag in FLAGS})
