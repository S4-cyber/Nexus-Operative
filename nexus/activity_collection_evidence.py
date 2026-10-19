"""Bounded declared page observations, never source attestation or coverage.

Canonical normalized content only: no raw response bodies, headers or credentials.
"""
import re
from nexus import alpaca_activity_normalizer as adapter
from nexus.alpaca_activity_collection import _window, activity_url, PAGE_LIMIT, PAGE_SIZE
from nexus.alpaca_paper_contract import sha
from nexus.broker_lookup_policy import clock

SCHEMA = 'nexus.paper-activity-collection-observation.v1'


def _fail(code):
    raise ValueError('ACTIVITY_COLLECTION_EVIDENCE_' + code)


def page_observation(page, records, *, after, until, request_cursor, requested_at, received_at):
    mapping = {r['activity_id']:r for r in records}
    ids = [adapter._id(row['id']) for row in page]
    return dict(request_cursor=request_cursor, response_cursor=None if not page else page[-1]['id'],
        activity_ids=ids, requested_at=requested_at, received_at=received_at,
        query_sha256=sha(activity_url(after, until, request_cursor)),
        normalized_records_sha256=sha([mapping[key] for key in ids]))


def validate(evidence, data, *, after, until):
    """Freeze/revalidate metadata against the exact normalized receipt contents."""
    fields = {'schema', 'declared_transport', 'read_started_at', 'read_completed_at', 'account_fields_sha256', 'pages'}
    if not isinstance(evidence, dict) or set(evidence) != fields or evidence['schema'] != SCHEMA:
        _fail('SHAPE')
    if evidence['declared_transport'] not in ('INJECTED_UNATTESTED', 'PAPER_HTTPS_READS'): _fail('TRANSPORT')
    digest = evidence['account_fields_sha256']
    if not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None: _fail('ACCOUNT_DIGEST')
    started, completed = clock(evidence['read_started_at']), clock(evidence['read_completed_at'])
    if not 0 <= completed - started < 20 or _window(after, until)[1] > started: _fail('TIME_SCOPE')
    pages = evidence['pages']
    if not isinstance(pages, list) or not 1 <= len(pages) <= PAGE_LIMIT: _fail('PAGE_CAP')
    records = {r['activity_id']:r for r in data['activities']}
    seen = set(); previous_cursor = None; previous_time = started; result = []
    page_fields = {'request_cursor','response_cursor','activity_ids','requested_at','received_at','query_sha256','normalized_records_sha256'}
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or set(page) != page_fields: _fail('PAGE_SHAPE')
        ids = page['activity_ids']
        if not isinstance(ids, list) or len(ids) > PAGE_SIZE: _fail('ROW_CAP')
        if any(adapter._id(key) != key for key in ids): _fail('IDENTITY_CANONICAL')
        if page['request_cursor'] != previous_cursor: _fail('CURSOR_CHAIN')
        if page['response_cursor'] is None:
            if ids: _fail('RESPONSE_CURSOR')
        elif not ids or adapter._id(page['response_cursor']) != ids[-1]: _fail('RESPONSE_CURSOR')
        if index and len(result[-1]['activity_ids']) != PAGE_SIZE: _fail('PAGE_AFTER_SHORT_END')
        if ids and ids[-1] in seen: _fail('CURSOR_NO_PROGRESS')
        requested, received = clock(page['requested_at']), clock(page['received_at'])
        if not previous_time <= requested <= received <= completed: _fail('PAGE_TIME')
        try: content = [records[key] for key in ids]
        except KeyError: _fail('UNKNOWN_ACTIVITY')
        query_digest = sha(activity_url(after, until, previous_cursor)); content_digest = sha(content)
        if page['query_sha256'] != query_digest or page['normalized_records_sha256'] != content_digest: _fail('DIGEST_DISAGREEMENT')
        result.append(dict(request_cursor=previous_cursor, response_cursor=page['response_cursor'], activity_ids=list(ids),
            requested_at=requested, received_at=received, query_sha256=query_digest, normalized_records_sha256=content_digest))
        seen.update(ids); previous_cursor = page['response_cursor']; previous_time = received
    if seen != set(records): _fail('ACTIVITY_SET_DISAGREEMENT')
    return dict(schema=SCHEMA, declared_transport=evidence['declared_transport'], read_started_at=started,
        read_completed_at=completed, account_fields_sha256=digest, pages=result)



def request_targets_match(operation, receipt):
    """Compare a replayed successful pacing receipt with retained page scope.

    Callers supply a fully replay-validated activity receipt. This is local
    target consistency only, not a check of raw response content or source.
    Reconstruct at most two approved page targets; retain no URLs or cursors.
    """
    if not isinstance(operation, dict) or not isinstance(receipt, dict):
        return False
    if (operation.get('operation') != 'ACTIVITY_COLLECTION' or
        operation.get('outcome') != 'SUCCESS' or operation.get('result_receipt_bound') is not True or
        operation.get('request_targets_bound') is not True):
        return False
    observation = receipt.get('collection_observation')
    if not isinstance(observation, dict):
        return False
    pages = observation.get('pages')
    if not isinstance(pages, list) or not 1 <= len(pages) <= PAGE_LIMIT:
        return False
    from nexus.alpaca_paper_inspection import URLS
    try:
        targets = [URLS[0], *(activity_url(receipt['after'], receipt['until'], page['request_cursor'])
            for page in pages), URLS[0]]
    except (KeyError, TypeError, ValueError):
        return False
    kinds = ('ACCOUNT', *('ACTIVITY_PAGE' for _ in pages), 'ACCOUNT')
    digests = tuple(sha(dict(method='GET', target=target)) for target in targets)
    return operation.get('request_kinds') == kinds and operation.get('request_sha256') == digests

def fixture_report():
    """Actual bounded collector + temporary retention, injected response objects."""
    import io
    import json
    from pathlib import Path
    import tempfile
    from nexus.alpaca_activity_collection import collect_activities
    from nexus.broker_activity_store import BrokerActivityStore
    account_id = '01234567-89ab-cdef-0123-456789abcdef'
    account = dict(id=account_id, currency='USD', status='ACTIVE', crypto_status='ACTIVE',
        account_blocked=False, trading_blocked=False, trade_suspended_by_user=False,
        cash='1000', non_marginable_buying_power='1000', pending_transfer_in='0', pending_transfer_out='0', accrued_fees='0')
    rows = [dict(id='20261017000000000::11234567-89ab-cdef-0123-' + f'{i:012d}',
        activity_type='FEE', status='executed', net_amount='-0.01', date='2026-10-16') for i in range(1,201)]
    class Response:
        status = 200
        headers = {'Content-Type':'application/json'}
        def __init__(self, url, value): self.url=url; self.body=io.BytesIO(json.dumps(value).encode())
        def __enter__(self): return self
        def __exit__(self,*args): self.body.close()
        def geturl(self): return self.url
        def read(self,size): return self.body.read(size)
    class Opener:
        def __init__(self): self.values=iter([account,rows[:100],rows[100:],account]);self.requests=0
        def open(self, request, timeout):
            self.requests+=1
            return Response(request.full_url,next(self.values))
    opener=Opener()
    with tempfile.TemporaryDirectory(prefix='nexus-collection-evidence-') as directory:
        store=BrokerActivityStore(Path(directory)/'activities.db',account_id)
        result=collect_activities('fixture_key_only_123','fixture_secret_only_123',[],
            expected_account_id=account_id,after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z',
            confirm_read_only_paper=True,opener=opener,wall_clock=lambda:1792368000,elapsed_clock=lambda:50,
            activity_store=store)
        fresh=BrokerActivityStore(store.path,account_id);state=fresh.inspect();review=fresh.review()
        pages=state['receipts'][result['local_retention']['receipt_id']]['collection_observation']['pages']
        passed=(state['retained_activities']==200 and state['retained_collection_observations']==1
                and len(pages)==2 and result['collection_limit_reached'] is True
                and review['collection_observation_review']['short_last_page_observed']==0)
        return dict(passed=passed,source='INJECTED_COLLECTOR_AND_TEMPORARY_RETENTION',
            get_requests=opener.requests,retained_activities=state['retained_activities'],
            retained_observations=state['retained_collection_observations'],retained_pages=len(pages),
            page_cap_reported=result['collection_limit_reached'],
            cursor_chain_preserved=pages[1]['request_cursor']==pages[0]['response_cursor'],
            source_attested=state['collection_source_attested'],activity_coverage_verified=state['activity_coverage_verified'],
            ready_for_routing=state['ready_for_routing'],automatic_continuation=state['automatic_continuation'],
            broker_connected=False,live_orders_enabled=False,
            limitations='Injected page objects only; no actual provider account, authenticated coverage, fee links or order authority.')
