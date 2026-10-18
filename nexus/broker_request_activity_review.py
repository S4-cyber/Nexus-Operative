"""Read-only original-request/retained-activity agreement; never order authority."""
from contextlib import closing
from decimal import localcontext
import re

from nexus import alpaca_activity_normalizer as adapter
from nexus.alpaca_paper_contract import number, text, sha
from nexus.broker_activity_store import BrokerActivityStore
from nexus.broker_activity_review import _review_snapshot
from nexus.broker_request_journal import BrokerRequestJournal, ZERO

FLAGS=('source_attested','activity_coverage_verified','fee_coverage_complete',
       'fee_execution_links_verified','holds_reconciled','broker_account_reconciled',
       'spendable_funds_verified','ledger_applied','ready_for_routing','broker_connected',
       'live_orders_enabled','money_movement_enabled','automatic_retry','release_approved')


def _fail(code):raise ValueError('BROKER_REQUEST_ACTIVITY_'+code)


def _requests(journal):
    journal._check_path()
    requests={};head=ZERO
    if journal.path.exists():
        with closing(journal._connect()) as db:requests,_,_,head=journal._load(db)
    return dict(head_sha256=head,requests={key:dict(plan=r['plan'],
        marked=r['uncertain'],snapshot=r['tracker'].status()['latest']) for key,r in requests.items()})


def review(journal,store,*,expected_request_head,expected_activity_head):
    """Bind two replayed local heads; cross-database reads are not atomic/attested."""
    if type(journal) is not BrokerRequestJournal or type(store) is not BrokerActivityStore:_fail('STORES_REQUIRED')
    for head in (expected_request_head,expected_activity_head):
        if type(head) is not str or re.fullmatch('[0-9a-f]{64}',head) is None:_fail('HEAD_INVALID')
    if journal.account_digest!=store.account_digest:_fail('ACCOUNT_DISAGREEMENT')
    requests=_requests(journal);retained=store.inspect()
    if requests['head_sha256']!=expected_request_head or retained['head_sha256']!=expected_activity_head:_fail('HEAD_DISAGREEMENT')
    activity=_review_snapshot(retained,account=store.account)
    blockers=set(activity['review_reasons'])-{'ACTIVITY_COVERAGE_UNVERIFIED','FEE_LINK_AND_TIMING_UNVERIFIED','COLLECTION_OBSERVATIONS_NOT_SOURCE_ATTESTATIONS'}
    if not requests['requests']:blockers.add('NO_RETAINED_REQUESTS')
    if not retained['retained_batches']:blockers.add('NO_RETAINED_ACTIVITY_BATCHES')
    if not activity['normalization_evaluated']:blockers.add('MERGED_ACTIVITY_NORMALIZATION_UNAVAILABLE')
    orders={}
    for receipt in retained['receipts'].values():
        for order in receipt['orders']:
            prior=orders.get(order['broker_order_id'])
            if prior is None or order['updated_at']>prior['updated_at']:orders[order['broker_order_id']]=order
    bound={};rows=[]
    for identity,request in requests['requests'].items():
        reasons=set();snapshot=request['snapshot'];payload=request['plan']['payload']
        if not request['marked']:reasons.add('REQUEST_ATTEMPT_NOT_MARKED')
        if snapshot is None:reasons.add('REQUEST_OBSERVATION_MISSING')
        else:
            oid=snapshot['broker_order_id'];bound[oid]=identity;order=orders.get(oid)
            if order is None:reasons.add('ACTIVITY_ORDER_SNAPSHOT_MISSING')
            elif any(order[key]!=(adapter._instant(snapshot[key]) if key=='updated_at' else snapshot[key])
                for key in ('broker_order_id','client_order_id','side','status','filled_quantity','average_fill_price','updated_at')):
                reasons.add('REQUEST_ACTIVITY_SNAPSHOT_DISAGREEMENT')
        fills=[] if snapshot is None else [r for r in retained['normalized_activities'] if r['kind']=='FILL' and r['broker_order_id']==snapshot['broker_order_id']]
        with localcontext() as context:
            context.prec=80;quantity=number(payload['qty']);limit=number(payload['limit_price']);total=number('0',zero=True)
            for fill in fills:
                total+=number(fill['quantity'])
                if fill['client_order_id']!=identity or fill['side']!=payload['side'].upper() or fill['product']!='BTC-USD':reasons.add('FILL_REQUEST_IDENTITY_DISAGREEMENT')
                if number(fill['cumulative_quantity'])+number(fill['leaves_quantity'],zero=True)!=quantity:reasons.add('FILL_REQUEST_QUANTITY_DISAGREEMENT')
                price=number(fill['price'])
                if payload['side']=='buy' and price>limit or payload['side']=='sell' and price<limit:reasons.add('INDIVIDUAL_FILL_LIMIT_DISAGREEMENT')
            if total>quantity:reasons.add('REQUEST_OVERFILL')
            if snapshot is not None and total!=number(snapshot['filled_quantity'],zero=True):reasons.add('REQUEST_FILLED_QUANTITY_DISAGREEMENT')
        blockers.update(reasons)
        rows.append(dict(client_order_id=identity,payload_sha256=request['plan']['payload_sha256'],
            broker_order_id=None if snapshot is None else snapshot['broker_order_id'],
            explicit_fill_count=len(fills),explicit_fill_quantity=text(total),
            local_terms_agree=not reasons,reasons=sorted(reasons)))
    untracked=sorted(set(orders)-set(bound))
    if untracked:blockers.add('UNTRACKED_ACTIVITY_ORDERS_REQUIRE_REVIEW')
    if _requests(journal)!=requests or store.inspect()!=retained:_fail('JOURNALS_CHANGED_DURING_REVIEW')
    scope=dict(policy='nexus.request-activity-review.v1',account_sha256=journal.account_digest,
        request_head_sha256=expected_request_head,activity_head_sha256=expected_activity_head)
    reasons=sorted(blockers|{'ACTIVITY_COVERAGE_UNVERIFIED','FEE_LINK_AND_TIMING_UNVERIFIED','HOLD_AND_ACCOUNT_RECONCILIATION_REQUIRED'})
    return dict(mode='BROKER_REQUEST_ACTIVITY_REVIEW',status='REVIEW_REQUIRED',read_only=True,
        **scope,input_sha256=sha(scope),journals_rechecked_unchanged=True,
        local_request_activity_agreement=not blockers,agreement_blockers=sorted(blockers),
        request_reviews=rows,untracked_activity_order_ids=untracked,review_reasons=reasons,
        **{flag:False for flag in FLAGS},
        limitations='Supplied local histories only. Exact replayed heads and explicit fill/limit checks do not attest source or complete activity/fees, verify funds/holds, make two databases atomic, detect deleted tails or authorize routing. No price is inferred from cumulative averages; no ledger or journal is written.')


def fixture_report():
    """One original IOC request, two explicit partial fills, no provider/account IO."""
    import tempfile
    from pathlib import Path
    from nexus.alpaca_paper_contract import preview
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,
        min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    plan=preview('nx_link_fixture','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
    oid='01234567-89ab-cdef-0123-456789abcdef';account='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    raw=dict(**plan['payload'],id=oid,asset_class='crypto',status='filled',filled_qty='2',filled_avg_price='99',updated_at='2026-10-17T18:00:02+00:00')
    order=dict(broker_order_id=oid,client_order_id='nx_link_fixture',symbol='BTC/USD',side='buy',status='filled',filled_quantity='2',average_fill_price='99',updated_at=raw['updated_at'])
    fills=[dict(id='20261017180000000::'+f'01234567-89ab-cdef-0123-{i:012d}',activity_type='FILL',order_id=oid,
        symbol='BTCUSD',side='buy',type='partial_fill' if i==1 else 'fill',qty='1',price=price,cum_qty=str(i),leaves_qty=str(2-i),transaction_time=f'2026-10-17T18:00:0{i}Z') for i,price in ((1,'98'),(2,'100'))]
    with tempfile.TemporaryDirectory(prefix='nexus-request-activity-') as directory:
        root=Path(directory);j=BrokerRequestJournal(root/'requests.sqlite3',account);s=BrokerActivityStore(root/'activities.sqlite3',account)
        j.prepare(plan);j.mark_submission_uncertain('nx_link_fixture');j.observe('nx_link_fixture',raw)
        s.retain(fills,[order],after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z')
        before={p.name:p.read_bytes() for p in root.iterdir()}
        r=review(j,s,expected_request_head=j.recovery()['head_sha256'],expected_activity_head=s.inspect()['head_sha256'])
        unchanged=before=={p.name:p.read_bytes() for p in root.iterdir()}
        bad=BrokerActivityStore(root/'bad.sqlite3',account)
        bad.retain([dict(fills[0],price='85'),dict(fills[1],price='113')],[order],after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z')
        rejected=review(j,bad,expected_request_head=j.recovery()['head_sha256'],expected_activity_head=bad.inspect()['head_sha256'])
        return dict(source='TEMPORARY_REQUEST_ACTIVITY_LINK_FIXTURE',passed=r['local_request_activity_agreement'] and unchanged and 'INDIVIDUAL_FILL_LIMIT_DISAGREEMENT' in rejected['agreement_blockers'],
            original_request_linked=r['local_request_activity_agreement'],explicit_fills_checked=r['request_reviews'][0]['explicit_fill_count']==2,
            offsetting_bad_price_detected='INDIVIDUAL_FILL_LIMIT_DISAGREEMENT' in rejected['agreement_blockers'],journals_unchanged=unchanged,
            **{flag:False for flag in FLAGS})
