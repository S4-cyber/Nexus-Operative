"""Private exact-head paper history -> supplied-benchmark execution diagnostic.

Read-only local replay, never credentials, provider calls or routing. Benchmark
quotes and clocks remain supplied/unverified; retained fee records stay unlinked.
"""
import argparse
from datetime import datetime,timezone
import json
import re
import sqlite3
import tempfile
from pathlib import Path

from nexus import execution_quality as quality
from nexus import paper_request_activity_acceptance as accepted
from nexus import broker_request_activity_review as original
from nexus import alpaca_activity_normalizer as adapter
from nexus.alpaca_paper_contract import sha
from nexus.broker_activity_store import BrokerActivityStore
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_activity_acceptance_probe import _private_path

POLICY='nexus.paper-execution-quality-review.v1'
BENCHMARK_KEYS=('decision_mid','decision_at_ns','arrival_bid','arrival_ask','arrival_at_ns','end_at_ns','unfilled_mark')
FLAGS=tuple(dict.fromkeys((*quality.FLAGS,*accepted.FLAGS,'benchmark_source_verified',
    'benchmark_request_event_time_verified','atomic_snapshot','fee_debits_allocated_to_order')))
CHECKS=('all_original_fills_derived','exact_heads_replayed','missing_fees_unlinked',
    'repeat_review_matches','journals_unchanged','benchmark_not_attested','integer_fill_times_preserved')


def _fail(code):raise ValueError('PAPER_EXECUTION_QUALITY_'+code)


def _benchmark(value):
    if type(value) is not dict or set(value)!=set(BENCHMARK_KEYS):_fail('BENCHMARK_SHAPE')
    copied={k:value[k] for k in BENCHMARK_KEYS}
    # Validate quote/time/mark fields before opening any journal. No fill is
    # invented by this empty-input validation and no result is acknowledged.
    quality.review(product='BTC-USD',side='BUY',requested_quantity='1',fills=[],**copied)
    return copied


def _instant_ns(value):
    normalized=adapter._instant(value);base,_,fraction=normalized[:-6].partition('.')
    delta=datetime.fromisoformat(base).replace(tzinfo=timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
    return quality._ns((delta.days*86400+delta.seconds)*1_000_000_000+int(fraction.ljust(9,'0')))


def review(*,expected_account_id,request_journal_path,pacing_path,activity_store_path,acceptance_store_path,
           expected_request_head,expected_activity_head,client_order_id,benchmark):
    """Derive all retained priced fills for one original request; never sample a subset."""
    paths=[_private_path(p)[1] for p in (request_journal_path,pacing_path,activity_store_path,acceptance_store_path)]
    if len(set(paths))!=4:_fail('DISTINCT_JOURNALS_REQUIRED')
    from nexus.sqlite_journal_guard import check_path_set
    check_path_set(paths,'PAPER_EXECUTION_QUALITY_')
    for head in (expected_request_head,expected_activity_head):
        if type(head) is not str or re.fullmatch('[0-9a-f]{64}',head) is None:_fail('HEAD_REFERENCE')
    if type(client_order_id) is not str or re.fullmatch('[A-Za-z0-9_-]{1,64}',client_order_id) is None:_fail('CLIENT_ID')
    benchmark=_benchmark(benchmark)
    common=dict(expected_account_id=expected_account_id,request_journal_path=request_journal_path,pacing_path=pacing_path,
        activity_store_path=activity_store_path,acceptance_store_path=acceptance_store_path,
        expected_request_head=expected_request_head,expected_activity_head=expected_activity_head)
    before=accepted.review(**common)
    if (before.get('read_only') is not True or before.get('complete_accepted_history_replayed') is not True or
        before.get('local_request_activity_agreement') is not True or
        any(before.get(k) is not False for k in accepted.FLAGS)):_fail('ACCEPTED_REQUEST_AGREEMENT_REQUIRED')
    journal=BrokerRequestJournal(request_journal_path,expected_account_id)
    store=BrokerActivityStore(activity_store_path,expected_account_id)
    requests=original._requests(journal);retained=store.inspect()
    if (requests['head_sha256']!=expected_request_head or retained['head_sha256']!=expected_activity_head or
        before['account_sha256']!=store.account_digest):_fail('HEAD_DISAGREEMENT')
    request=requests['requests'].get(client_order_id)
    if request is None or request['marked'] is not True or request['snapshot'] is None:_fail('OBSERVED_ORIGINAL_REQUEST_REQUIRED')
    plan=request['plan'];payload=plan['payload'];snapshot=request['snapshot'];broker_id=snapshot['broker_order_id']
    selected=[r for r in before['request_activity_review']['request_reviews'] if r['client_order_id']==client_order_id]
    if (len(selected)!=1 or selected[0]['broker_order_id']!=broker_id or selected[0]['local_terms_agree'] is not True or
        selected[0]['payload_sha256']!=plan['payload_sha256']):_fail('REQUEST_BINDING')
    records=[r for r in retained['normalized_activities'] if r['kind']=='FILL' and r['broker_order_id']==broker_id]
    if any(r['client_order_id']!=client_order_id or r['side']!=payload['side'].upper() or r['product']!='BTC-USD' for r in records):_fail('FILL_BINDING')
    if len(records)!=selected[0]['explicit_fill_count']:_fail('FILL_COUNT_DISAGREEMENT')
    # REST activity IDs are local surrogate identities, not claimed streaming
    # execution IDs. No fee is guessed from references, dates or residuals.
    fills=[dict(execution_id='rest:'+r['activity_id'],quantity=r['quantity'],price=r['price'],
        filled_at_ns=_instant_ns(r['occurred_at']),fee_usd=None) for r in records]
    result=quality.review(product='BTC-USD',side=payload['side'].upper(),requested_quantity=payload['qty'],
        fills=fills,**benchmark)
    if (result.get('mode')!='SUPPLIED_EXECUTION_QUALITY_REVIEW' or result.get('policy')!=quality.POLICY or
        result.get('status')!='RESEARCH_REVIEW_REQUIRED' or any(result.get(k) is not False for k in quality.FLAGS) or
        result.get('unique_fills')!=len(records) or result.get('filled_quantity')!=selected[0]['explicit_fill_quantity'] or
        result.get('all_supplied_fees_explicit') is not False or
        any(result.get(k) is not None for k in ('supplied_fee_total_usd','signed_shortfall_with_supplied_fees_usd',
            'executed_shortfall_with_supplied_fees_bps','signed_whole_order_shortfall_usd','whole_order_shortfall_bps'))):
        _fail('QUALITY_COMPONENT_DISAGREEMENT')
    after=accepted.review(**common)
    if (after!=before or original._requests(journal)!=requests or store.inspect()!=retained):_fail('JOURNALS_CHANGED_DURING_REVIEW')
    scope=dict(policy=POLICY,account_sha256=before['account_sha256'],request_head_sha256=expected_request_head,
        activity_head_sha256=expected_activity_head,pacing_head_sha256=before['pacing_head_sha256'],
        acceptance_head_sha256=before['acceptance_head_sha256'],client_order_id=client_order_id,
        broker_order_id=broker_id,payload_sha256=plan['payload_sha256'],benchmark_sha256=sha(benchmark),
        accepted_review_sha256=sha(before),quality_input_sha256=result['input_sha256'])
    return dict(mode='PAPER_EXECUTION_QUALITY_REVIEW',status='REVIEW_REQUIRED',read_only=True,
        **scope,input_sha256=sha(scope),complete_accepted_history_replayed=True,local_original_terms_agree=True,
        all_retained_original_fills_included=True,journals_rechecked_unchanged=True,
        retained_unallocated_fee_records=sum(r['kind']!='FILL' for r in retained['normalized_activities']),
        identity_scope='REST_ACTIVITY_IDS_AS_LOCAL_SURROGATES_NOT_VERIFIED_STREAM_EXECUTION_IDS',
        benchmark_scope='SUPPLIED_UNVERIFIED_QUOTES_AND_CLOCK_DOMAIN_NOT_ORDER_EVENT_ATTESTATION',
        execution_quality=result,**{k:False for k in FLAGS},
        limitations='Exact local account/request/activity/pacing/acceptance replay binds all retained priced fills and original immutable terms to a supplied quote benchmark. It does not prove complete received activity, actual order arrival/decision timing, source, synchronized clocks, fee links, account balances, spendable funds, profit or routing permission. All fee-inclusive results remain unknown. Separate databases are sampled, not atomic; valid-tail deletion/ABA replacement and other hosts are not excluded. No network/vault/write/retry/repair. Actual input/output is private financial information, never public CI evidence.')


def _fixture(root):
    options,_=accepted._fixture(root);epoch=_instant_ns('2026-10-17T18:00:00Z')
    return dict(**options,client_order_id='nx_accept_fixture',benchmark=dict(decision_mid='100',decision_at_ns=epoch-1,
        arrival_bid='99',arrival_ask='101',arrival_at_ns=epoch,end_at_ns=epoch+3_000_000_000,unfilled_mark=None))


def fixture_report():
    with tempfile.TemporaryDirectory(prefix='nexus-paper-quality-') as directory:
        root=Path(directory);args=_fixture(root)
        before=tuple((p.name,p.read_bytes()) for p in sorted(root.iterdir()))
        result=review(**args);repeated=review(**args);q=result['execution_quality']
        checks=dict(all_original_fills_derived=q['unique_fills']==2 and q['filled_quantity']=='2' and q['fill_vwap']=='99',
            exact_heads_replayed=result['complete_accepted_history_replayed'] and result['journals_rechecked_unchanged'],
            missing_fees_unlinked=q['supplied_fee_total_usd'] is None and q['signed_shortfall_with_supplied_fees_usd'] is None,
            repeat_review_matches=result==repeated,journals_unchanged=before==tuple((p.name,p.read_bytes()) for p in sorted(root.iterdir())),
            benchmark_not_attested=result['benchmark_source_verified'] is False and result['benchmark_request_event_time_verified'] is False,
            integer_fill_times_preserved=q['decision_to_arrival_ns']==1 and q['arrival_to_last_supplied_fill_ns']==2_000_000_000)
        return dict(source='SYNTHETIC_ACCEPTED_PAPER_EXECUTION_QUALITY_FIXTURE',passed=all(checks.values()),**checks,**{k:False for k in FLAGS})


def _load_benchmark(path):
    from nexus.encrypted_observation import _read
    raw=_read(path,4096)
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:_fail('DUPLICATE_BENCHMARK_KEY')
            result[key]=value
        return result
    value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=lambda _: _fail('BENCHMARK_NUMBER'))
    return raw,_benchmark(value)


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PAPER_EXECUTION_QUALITY_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__)
    descriptions={
        'expected-account':'Exact paper account UUID that all selected journals must match; never an API key.',
        'request-journal':'Private original-request journal; read-only replay, no submission or retry.',
        'pacing':'Private cooperating paper-read pacing journal; no new reservation or provider read.',
        'activity-store':'Private retained explicit fill/activity journal; all fills of the selected original order are included.',
        'acceptance-store':'Private accepted collection history; complete cooperating local transitions must replay.',
        'request-head':'Exact current request-history SHA256 to bind; a changed history fails without retry.',
        'activity-head':'Exact current activity-history SHA256 to bind; a changed history fails without retry.',
        'client-order-id':'Original retained client order ID to review; never creates an order.',
        'benchmark':'Private bounded JSON with decision_mid, decision_at_ns, arrival_bid, arrival_ask, arrival_at_ns, end_at_ns and unfilled_mark. Prices are decimal strings, times integer nanoseconds; quotes/clocks remain unverified.'}
    for name,description in descriptions.items():parser.add_argument('--'+name,required=True,help=description)
    args=parser.parse_args()
    try:
        names=[args.request_journal,args.pacing,args.activity_store,args.acceptance_store,args.benchmark]
        if len({_private_path(p)[1] for p in names})!=5:_fail('DISTINCT_INPUT_PATHS_REQUIRED')
        raw,benchmark=_load_benchmark(args.benchmark)
        result=review(expected_account_id=args.expected_account,request_journal_path=args.request_journal,pacing_path=args.pacing,
            activity_store_path=args.activity_store,acceptance_store_path=args.acceptance_store,
            expected_request_head=args.request_head,expected_activity_head=args.activity_head,
            client_order_id=args.client_order_id,benchmark=benchmark)
        after,_=_load_benchmark(args.benchmark)
        if raw!=after:_fail('BENCHMARK_FILE_CHANGED')
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,UnicodeError,OverflowError,MemoryError,RecursionError,KeyboardInterrupt):
        print('PAPER_EXECUTION_QUALITY_FAILED');return 2
    print(json.dumps(result,sort_keys=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
