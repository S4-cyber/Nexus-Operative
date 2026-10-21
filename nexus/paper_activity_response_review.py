"""Offline exact-byte review for one retained paper activity-read session.

Private account/page response bodies are caller-supplied.  The review matches
their exact bytes to one successful paced ACTIVITY_COLLECTION session, replays
the production activity normalizer and compares the result with one retained
activity receipt.  It never opens credentials, contacts a provider, writes a
receipt or grants account, source, funds, routing or release authority.
"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from nexus import alpaca_activity_normalizer as adapter
from nexus import alpaca_paper_inspection as inspection
from nexus import activity_collection_evidence as evidence
from nexus.alpaca_paper_contract import sha
from nexus.broker_activity_store import BrokerActivityStore, _inputs
from nexus.paper_activity_acceptance_probe import _private_path
from nexus.paper_provider_result_tool import _read
from nexus import paper_read_projection as projection
from nexus.paper_read_pacing import PaperReadPacing, RECEIPT_PROFILE_V3

MODE='PAPER_ACTIVITY_RESPONSE_NORMALIZATION_REVIEW'
FLAGS=('source_attested','source_coverage_verified','activity_coverage_verified',
    'fee_coverage_complete','fee_execution_links_verified','holds_reconciled',
    'broker_account_reconciled','spendable_funds_verified','ready_for_routing',
    'broker_connected','ledger_applied','live_orders_enabled','money_movement_enabled',
    'automatic_retry','automatic_continuation','release_approved')


def _fail(code):
    raise ValueError('PAPER_ACTIVITY_RESPONSE_REVIEW_'+code) from None


def _digest(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}',value) is None:_fail('HEAD_INVALID')
    return value


def _raw(path):
    path,_=_private_path(path)
    raw=_read(path)
    if len(raw)>inspection.MAX_BODY:_fail('BODY_CAP')
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=inspection._pairs,
            parse_constant=inspection._nonfinite,parse_float=Decimal)
    except (UnicodeError,ValueError,TypeError,RecursionError,MemoryError):
        _fail('BODY_INVALID')
    return raw,value


def review(*,pacing_path,activity_store_path,expected_account_id,
        expected_pacing_head,expected_activity_head,session_head,
        account_before_path,activity_page_paths,account_after_path):
    """Recompute one retained activity receipt from exact private response bytes."""
    expected_pacing_head=_digest(expected_pacing_head)
    expected_activity_head=_digest(expected_activity_head)
    session_head=_digest(session_head)
    if type(activity_page_paths) not in (tuple,list) or not 1<=len(activity_page_paths)<=2:
        _fail('PAGE_COUNT_INVALID')
    paths=[pacing_path,activity_store_path,account_before_path,*activity_page_paths,account_after_path]
    checked=[_private_path(value)[1] for value in paths]
    if len(set(checked))!=len(checked):_fail('DISTINCT_PATHS_REQUIRED')
    gate=PaperReadPacing(pacing_path,expected_account_id)
    store=BrokerActivityStore(activity_store_path,expected_account_id)
    pacing_before=gate.inspect();activity_before=store.inspect()
    if pacing_before['head_sha256']!=expected_pacing_head:_fail('PACING_HEAD_CHANGED')
    if activity_before['head_sha256']!=expected_activity_head:_fail('ACTIVITY_HEAD_CHANGED')
    matches=[row for row in gate.operation_history() if row['after_head_sha256']==session_head]
    if len(matches)!=1:_fail('SESSION_NOT_FOUND')
    operation=matches[0]
    expected_kinds=('ACCOUNT',*('ACTIVITY_PAGE' for _ in activity_page_paths),'ACCOUNT')
    if (operation['operation']!='ACTIVITY_COLLECTION' or operation['outcome']!='SUCCESS' or
            operation['receipt_profile']!=RECEIPT_PROFILE_V3 or
            operation['normalized_result_bound'] is not True or
            operation['request_kinds']!=expected_kinds):
        _fail('SESSION_SCOPE_INVALID')

    raw_values=[_raw(account_before_path),*(_raw(path) for path in activity_page_paths),_raw(account_after_path)]
    bodies=[item[0] for item in raw_values];values=[item[1] for item in raw_values]
    body_hashes=tuple(hashlib.sha256(raw).hexdigest() for raw in bodies)
    if body_hashes!=operation['response_sha256']:_fail('RESPONSE_DIGEST_DISAGREEMENT')
    first=inspection._account(values[0]);last=inspection._account(values[-1])
    if first!=last or first['id']!=gate.account:_fail('ACCOUNT_DISAGREEMENT')
    pages=values[1:-1]
    if any(type(page) is not list or len(page)>evidence.PAGE_SIZE for page in pages):
        _fail('PAGE_SHAPE_INVALID')

    candidates=[]
    for receipt_id,receipt in activity_before['receipts'].items():
        if (projection.matches(operation,'ACTIVITY_COLLECTION',receipt_id) and
                evidence.request_targets_match(operation,receipt)):
            candidates.append((receipt_id,receipt))
    if len(candidates)!=1:_fail('RETAINED_RECEIPT_NOT_FOUND')
    receipt_id,receipt=candidates[0]
    rows=[row for page in pages for row in page]
    try:
        normalized=_inputs(rows,[dict(order,symbol='BTC/USD') for order in receipt['orders']],gate.account)
        retained={row['activity_id']:row for row in activity_before['normalized_activities']}
        expected=dict(orders=receipt['orders'],activities=[retained[key] for key in receipt['activity_ids']])
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError,MemoryError):
        _fail('NORMALIZATION_INVALID')
    if normalized!=expected:_fail('NORMALIZED_ACTIVITY_DISAGREEMENT')

    observed=receipt.get('collection_observation')
    if type(observed) is not dict or len(observed.get('pages',()))!=len(pages):
        _fail('OBSERVATION_DISAGREEMENT')
    try:
        replayed_pages=[]
        for page,recorded in zip(pages,observed['pages']):
            replayed_pages.append(evidence.page_observation(page,normalized['activities'],
                after=receipt['after'],until=receipt['until'],request_cursor=recorded['request_cursor'],
                requested_at=recorded['requested_at'],received_at=recorded['received_at']))
        replayed=evidence.validate(dict(schema=evidence.SCHEMA,
            declared_transport=observed['declared_transport'],
            read_started_at=observed['read_started_at'],read_completed_at=observed['read_completed_at'],
            account_fields_sha256=sha(first),pages=replayed_pages),normalized,
            after=receipt['after'],until=receipt['until'])
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError,MemoryError):
        _fail('OBSERVATION_INVALID')
    if replayed!=observed:_fail('OBSERVATION_DISAGREEMENT')
    if not (operation['started_at']<=replayed['read_started_at']<=
            replayed['read_completed_at']<=operation['completed_at']):
        _fail('SESSION_TIMING_DISAGREEMENT')
    normalized_receipt=sha(dict(account_sha256=store.account_digest,after=receipt['after'],
        until=receipt['until'],**normalized,collection_observation=replayed))
    if normalized_receipt!=receipt_id:_fail('RETAINED_RECEIPT_DISAGREEMENT')
    normalized_digest=projection.digest('ACTIVITY_COLLECTION',normalized_receipt)
    if normalized_digest!=operation['result_projection_sha256']:_fail('NORMALIZED_RESULT_DISAGREEMENT')
    if gate.inspect()!=pacing_before:_fail('PACING_HEAD_CHANGED')
    if store.inspect()!=activity_before:_fail('ACTIVITY_HEAD_CHANGED')
    scope=dict(mode=MODE,status='REVIEW_REQUIRED',read_only=True,
        account_sha256=gate.account_digest,pacing_head_sha256=expected_pacing_head,
        activity_head_sha256=expected_activity_head,session_head_sha256=session_head,
        account_fields_sha256=sha(first),
        response_manifest_sha256=sha(list(body_hashes)),retained_receipt_sha256=receipt_id,
        collection_observation_sha256=sha(replayed),normalized_result_sha256=normalized_digest,
        response_bodies=len(bodies),activity_pages=len(pages),rows_received=len(rows),
        normalized_activities=len(normalized['activities']),exact_response_bytes_match=True,
        account_fields_replayed=True,activity_normalization_replayed=True,
        collection_observation_replayed=True,retained_receipt_replayed=True,
        session_timing_envelope_match=True,
        journals_rechecked_unchanged=True,admission_reserved=False,
        **{flag:False for flag in FLAGS})
    scope['review_sha256']=sha(scope)
    scope['limitations']=('Exact private response bytes match one replayed local ACTIVITY_COLLECTION session and '
        'one retained normalization receipt. Hash equality and local replay do not attest provider identity, TLS '
        'capture, creation/execution/fee coverage, clocks, account ownership, fee links/posting, holds, settled or '
        'spendable funds, another host, broker-wide pacing, routing permission or release acceptance. Keep all '
        'response bodies and output outside Git.')
    return scope


def _fixture(root,two_pages=False,pacing_path=None,account_cash='1000',wall_clock_value=1792368000):
    """Create disposable injected response bytes plus paced retained activity."""
    import io
    from nexus.alpaca_activity_collection import collect_activities
    from nexus.broker_fee_projection import fixture_inputs
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    account='01234567-89ab-cdef-0123-456789abcdef'
    raw_account=dict(id=account,currency='USD',status='ACTIVE',crypto_status='ACTIVE',
        account_blocked=False,trading_blocked=False,trade_suspended_by_user=False,
        cash=account_cash,non_marginable_buying_power='900',pending_transfer_in='0',
        pending_transfer_out='0',accrued_fees='0')
    if two_pages:
        orders=[fixture_inputs()['orders'][0]]
        rows=[dict(id='20261017180000000::11234567-89ab-cdef-0123-'+f'{index:012d}',
            activity_type='FEE',status='executed',net_amount='-0.01',date='2026-10-17')
            for index in range(1,102)]
        pages=[rows[:100],rows[100:]]
    else:
        order=fixture_inputs()['orders'][0];orders=[order]
        row=dict(id='20261017180000000::11234567-89ab-cdef-0123-000000000001',
            activity_type='FILL',order_id=order['broker_order_id'],client_order_id=order['client_order_id'],
            symbol='BTCUSD',side='buy',type='fill',qty='1',cum_qty='1',leaves_qty='0',
            price='100',transaction_time='2026-10-17T18:00:01Z')
        pages=[[row]]
    values=[raw_account,*pages,raw_account]
    bodies=[json.dumps(value,separators=(',',':')).encode() for value in values]
    class Response:
        status=200;headers={'Content-Type':'application/json'}
        def __init__(self,url,raw):self.url=url;self.body=io.BytesIO(raw)
        def geturl(self):return self.url
        def read(self,size):return self.body.read(size)
        def __enter__(self):return self
        def __exit__(self,*args):self.body.close()
    class Opener:
        def __init__(self):self.items=iter(bodies)
        def open(self,request,timeout):return Response(request.full_url,next(self.items))
    gate=PaperReadPacing(pacing_path or root/'pacing.sqlite3',account)
    store=BrokerActivityStore(root/'activity.sqlite3',account)
    collect_activities('fixture_key_only_123','fixture_secret_only_123',orders,
        expected_account_id=account,after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z',
        confirm_read_only_paper=True,opener=Opener(),wall_clock=lambda:wall_clock_value,
        elapsed_clock=lambda:50,activity_store=store,pacing=gate)
    paths=[];names=['account-before',*(f'activity-page-{index}' for index in range(len(pages))),'account-after']
    for name,raw in zip(names,bodies):
        path=root/(name+'.json');path.write_bytes(raw);paths.append(path)
    state=gate.inspect();activity=store.inspect();session=gate.operation_history()[-1]
    return dict(pacing_path=gate.path,activity_store_path=store.path,expected_account_id=account,
        expected_pacing_head=state['head_sha256'],expected_activity_head=activity['head_sha256'],
        session_head=session['after_head_sha256'],account_before_path=paths[0],
        activity_page_paths=paths[1:-1],account_after_path=paths[-1])


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PAPER_ACTIVITY_RESPONSE_REVIEW_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__)
    for name in ('pacing','activity-store','expected-account','expected-pacing-head',
                 'expected-activity-head','session-head','account-before','account-after'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--activity-page',required=True,action='append')
    args=parser.parse_args()
    try:
        result=review(pacing_path=args.pacing,activity_store_path=args.activity_store,
            expected_account_id=args.expected_account,expected_pacing_head=args.expected_pacing_head,
            expected_activity_head=args.expected_activity_head,session_head=args.session_head,
            account_before_path=args.account_before,activity_page_paths=args.activity_page,
            account_after_path=args.account_after)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,UnicodeError,
            OverflowError,MemoryError,RecursionError,RuntimeError,KeyboardInterrupt):
        print('PAPER_ACTIVITY_RESPONSE_REVIEW_FAILED');return 2
    print(json.dumps(result,sort_keys=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
