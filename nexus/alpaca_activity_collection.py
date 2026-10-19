"""Explicit bounded paper activity reads with optional caller-selected retention.

Creation-date pagination is not execution/fee coverage. At most two pages and
four total GETs; no order methods, arbitrary URLs, default window or account.
"""
from datetime import datetime
import hashlib
import re
import time
from urllib.parse import parse_qsl, urlencode

from nexus import alpaca_activity_normalizer as adapter
from nexus import alpaca_paper_inspection as transport
from nexus.alpaca_paper_contract import sha

ACTIVITY_URL = 'https://paper-api.alpaca.markets/v2/account/activities'
PAGE_SIZE = 100
PAGE_LIMIT = 2


def _fail(code):
    transport._fail('PAPER_ACTIVITY_' + code)


def _window(after, until):
    values = []
    for value in (after, until):
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', value):
            _fail('WINDOW_INVALID')
        try:
            stamp = datetime.fromisoformat(value[:-1] + '+00:00').timestamp()
        except (ValueError, OverflowError, OSError): _fail('WINDOW_INVALID')
        if stamp < 0: _fail('WINDOW_INVALID')
        values.append(stamp)
    if not 0 < values[1] - values[0] <= 86400: _fail('WINDOW_INVALID')
    return values


def activity_url(after, until, page_token=None):
    """Build only this closed-shape local profile; no arbitrary query options."""
    _window(after, until)
    pairs = [('after', after), ('until', until), ('direction', 'asc'), ('page_size', str(PAGE_SIZE))]
    if page_token is not None:
        adapter._id(page_token)
        pairs.append(('page_token', page_token))  # Opaque source spelling is preserved.
    return ACTIVITY_URL + '?' + urlencode(pairs)


def is_activity_url(url):
    """Shared reader admission rejects alternate hosts, query forms and filters."""
    if not isinstance(url, str) or len(url) > 512 or not url.startswith(ACTIVITY_URL + '?'):
        return False
    try:
        pairs = parse_qsl(url[len(ACTIVITY_URL) + 1:], keep_blank_values=True, strict_parsing=True, max_num_fields=5)
        fields = dict(pairs)
        required = {'after', 'until', 'direction', 'page_size'}
        if len(fields) != len(pairs) or set(fields) not in (required, required | {'page_token'}): return False
        return url == activity_url(fields['after'], fields['until'], fields.get('page_token'))
    except (ValueError, TypeError):
        return False


def _storage_paths(activity_store,pacing):
    if activity_store is None or pacing is None:return
    from nexus.broker_activity_store import BrokerActivityStore
    from nexus.paper_read_pacing import PaperReadPacing
    from nexus.sqlite_journal_guard import check_path_set
    if type(activity_store) is not BrokerActivityStore or type(pacing) is not PaperReadPacing:
        _fail('RETENTION_OR_PACING_TYPE')
    check_path_set((activity_store.path,pacing.path),'PAPER_ACTIVITY_COLLECTION_')


def collect_activities(api_key, api_secret, orders, *, expected_account_id, after, until,
                       confirm_read_only_paper=False, opener=None, wall_clock=time.time,
                       elapsed_clock=time.monotonic, permission_check=lambda: True,
                       credential_check=lambda: True, activity_store=None, pacing=None):
    if confirm_read_only_paper is not True:transport._fail('PAPER_INSPECTION_EXPLICIT_AUTHORIZATION_REQUIRED')
    _storage_paths(activity_store,pacing)
    session = transport._ReadSession(api_key, api_secret, confirm_read_only_paper=confirm_read_only_paper,
        opener=opener, wall_clock=wall_clock, elapsed_clock=elapsed_clock, permission_check=permission_check,
        pacing=pacing, expected_account_id=expected_account_id,operation='ACTIVITY_COLLECTION')
    account = adapter._uuid(expected_account_id)
    if activity_store is not None:
        from nexus.broker_activity_store import BrokerActivityStore, MAX_BATCHES
        if type(activity_store) is not BrokerActivityStore or activity_store.account != account:
            _fail('RETENTION_ACCOUNT_OR_TYPE')
        retained = activity_store.inspect()  # Missing stays missing; corrupt history blocks IO.
        if retained['retained_batches'] >= MAX_BATCHES:
            _fail('RETENTION_BATCH_CAP_REVIEW')
    _, end = _window(after, until)
    # Snapshot caller terms once, before network work. Scalars are immutable;
    # validate the bounded copy again rather than borrowing a caller's live list.
    adapter._budget(orders)
    frozen_orders = [dict(order) for order in orders]
    adapter.normalize([], frozen_orders, expected_account_id=account)
    scope = dict(account_sha256=hashlib.sha256(account.encode()).hexdigest(),
                 after=after, until=until, direction='asc', page_size=PAGE_SIZE)
    with session:
        started = session.evaluated_at()
        if end > started: _fail('FUTURE_CREATION_WINDOW')
        before = transport._account(session.get(transport.URLS[0]))
        if before['id'] != account: _fail('ACCOUNT_ID_DISAGREEMENT')
        rows = []; page_counts = []; page_observations = []; seen = set(); cursor = None; end_observed = False
        report = None
        for _ in range(PAGE_LIMIT):
            requested = session.evaluated_at()
            page = session.get(activity_url(after, until, cursor))
            received = session.evaluated_at()
            if not isinstance(page, list) or len(page) > PAGE_SIZE: _fail('PAGE_SHAPE_OR_CAP')
            # Validate a page and the full accumulated set before admitting any
            # next request. This also rejects overlapping-ID conflicts.
            report = adapter.normalize(rows + page, frozen_orders, expected_account_id=account)
            from nexus.activity_collection_evidence import page_observation
            page_observations.append(page_observation(page, report['normalized_activities'], after=after, until=until,
                request_cursor=cursor, requested_at=requested, received_at=received))
            ids = [adapter._id(row.get('id')) for row in page]
            if ids and ids[-1] in seen: _fail('CURSOR_NO_PROGRESS')
            rows.extend(page); page_counts.append(len(page)); seen.update(ids)
            cursor = page[-1]['id'] if ids else cursor
            session.checkpoint()
            if len(page) < PAGE_SIZE:
                end_observed = True
                break
        after_account = transport._account(session.get(transport.URLS[0]))
        if before != after_account: _fail('ACCOUNT_CHANGED_DURING_READ')
        if credential_check() is not True: _fail('CREDENTIALS_CHANGED')
        completed = session.evaluated_at()
        from nexus.activity_collection_evidence import SCHEMA, validate
        observation = dict(schema=SCHEMA, declared_transport='INJECTED_UNATTESTED' if session.injected else 'PAPER_HTTPS_READS',
            read_started_at=started, read_completed_at=completed, account_fields_sha256=sha(before), pages=page_observations)
        observation = validate(observation, dict(activities=report['normalized_activities']), after=after, until=until)
        retention = None
        if activity_store is not None:
            def admission():
                session.checkpoint()
                return credential_check() is True
            retention = activity_store.retain(rows, frozen_orders, after=after, until=until, admission=admission,
                                              collection_observation=observation)
            # A post-commit fault may lose the return, never the retained receipt.
            if admission() is not True: _fail('CREDENTIALS_CHANGED')
            completed = session.evaluated_at()
        return dict(mode='EXPLICIT_BOUNDED_PAPER_ACTIVITY_COLLECTION',
                    source='INJECTED_UNATTESTED' if session.injected else 'PAPER_HTTPS_READS',
                    requested_creation_window=scope, scope_sha256=sha(scope),
                    invocation_started_at=started, invocation_completed_at=completed,
                    page_counts=page_counts, page_limit=PAGE_LIMIT, rows_received=len(rows),
                    end_of_page_sequence_observed=end_observed, collection_limit_reached=not end_observed,
                    continuation_hint=None if end_observed else dict(scope_sha256=sha(scope), page_token=cursor),
                    activity_normalization=report, account_fields_double_checked=True, atomic_snapshot=False,
                    local_retention=retention, collection_observation=observation, collection_source_attested=False,
                    automatic_continuation=False, automatic_retry=False, activity_coverage_verified=False,
                    fee_coverage_complete=False, broker_account_reconciled=False, spendable_funds_verified=False,
                    ready_for_routing=False, broker_connected=False, live_orders_enabled=False, ledger_applied=False,
                    limitations='Bounded sequential reads, not an atomic snapshot. Optional local retention preserves normalized unverified inputs only. A short page is only a pagination observation. Creation filters do not prove execution/fee coverage; delayed, revised or corrected source records may be missing. Cursor hints grant no automatic resume, ownership or trading permission.')
