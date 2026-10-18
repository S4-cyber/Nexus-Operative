"""Read-only merged review of bounded, unverified local paper activity history.

Reported snapshot times select diagnostic observations, never trading authority.
Requested creation windows are not observed execution/fee coverage.
"""
from nexus import alpaca_activity_normalizer as adapter
from nexus.alpaca_paper_contract import TERMINAL, number, sha
from nexus.broker_activity_store import BrokerActivityStore, _raw

MAX_REVIEW_ORDERS = 1000


def review(store):
    if type(store) is not BrokerActivityStore:
        raise ValueError('BROKER_ACTIVITY_REVIEW_STORE_REQUIRED')
    retained = store.inspect()  # One replay-validated snapshot, no missing-state initialization.
    return _review_snapshot(retained, account=store.account)


def _review_snapshot(retained, *, account):
    """Internal reuse of one already replay-validated, account-bound snapshot."""
    reasons = {'ACTIVITY_COVERAGE_UNVERIFIED', 'FEE_LINK_AND_TIMING_UNVERIFIED'}
    snapshots = {}; windows = set(); versions = 0
    for receipt in retained['receipts'].values():
        windows.add((receipt['after'], receipt['until']))
        for order in receipt['orders']:
            oid = order['broker_order_id']
            if oid not in snapshots:
                if len(snapshots) >= MAX_REVIEW_ORDERS:
                    raise ValueError('BROKER_ACTIVITY_REVIEW_ORDER_CAP')
                snapshots[oid] = {}
            at = order['updated_at']
            values = snapshots[oid].setdefault(at, {})
            digest = sha(order)
            if digest not in values:
                values[digest] = order; versions += 1
    selected = []; ambiguous = []; regressed = []
    for oid in sorted(snapshots):
        timeline = snapshots[oid]
        if any(len(values) != 1 for values in timeline.values()):
            ambiguous.append(oid)
            continue  # Never break equal-time ties by receipt order, ID or arrival.
        sequence = [next(iter(timeline[at].values())) for at in sorted(timeline)]
        previous = None
        for order in sequence:
            if previous is not None:
                if number(order['filled_quantity'], zero=True) < number(previous['filled_quantity'], zero=True):
                    reasons.add('SNAPSHOT_FILLED_QUANTITY_REGRESSION'); regressed.append(oid)
                if previous['status'] in TERMINAL and order['status'] != previous['status']:
                    reasons.add('TERMINAL_SNAPSHOT_STATUS_CHANGED'); regressed.append(oid)
                financial_change = any(order[key] != previous[key] for key in ('filled_quantity', 'average_fill_price'))
                if previous['status'] in TERMINAL and financial_change:
                    reasons.add('TERMINAL_SNAPSHOT_FINANCIAL_FIELDS_CHANGED'); regressed.append(oid)
                if order['filled_quantity'] == previous['filled_quantity'] and order['average_fill_price'] != previous['average_fill_price']:
                    reasons.add('UNCHANGED_QUANTITY_AVERAGE_CHANGED'); regressed.append(oid)
            previous = order
        selected.append(sequence[-1])
    if ambiguous: reasons.add('EQUAL_TIME_ORDER_SNAPSHOT_CONFLICT')
    if any(order['status'] not in TERMINAL for order in selected):
        reasons.add('OPEN_ORDER_RESERVATIONS_UNVERIFIED')
    normalization = None
    if not ambiguous and not regressed:
        try:
            normalization = adapter.normalize([_raw(record) for record in retained['normalized_activities']],
                [dict(order, symbol='BTC/USD') for order in selected], expected_account_id=account)
        except ValueError:
            # Individually valid batches may disagree when merged, or exceed the
            # existing combined parser budget. No truncated/partial review is returned.
            reasons.add('MERGED_ACTIVITY_INPUT_LIMIT_OR_DISAGREEMENT')
        else:
            reasons.update(normalization['review_reasons'])
    observations = [r['collection_observation'] for r in retained['receipts'].values() if 'collection_observation' in r]
    if observations: reasons.add('COLLECTION_OBSERVATIONS_NOT_SOURCE_ATTESTATIONS')
    observation_review = dict(retained_observations=len(observations),
        batches_without_observations=len(retained['receipts'])-len(observations),
        declared_transports=sorted({r['declared_transport'] for r in observations}),
        retained_pages=sum(len(r['pages']) for r in observations),
        short_last_page_observed=sum(len(r['pages'][-1]['activity_ids']) < 100 for r in observations),
        source_attested=False, activity_coverage_verified=False, automatic_continuation=False)
    components = []; gaps = []
    for start, end in sorted(windows):
        if not components or start > components[-1]['until']:
            if components:
                gaps.append(dict(after=components[-1]['until'], until=start))
            components.append(dict(after=start, until=end))
        elif end > components[-1]['until']:
            components[-1]['until'] = end
    if not windows: reasons.add('NO_RETAINED_REQUEST_WINDOWS')
    if gaps: reasons.add('GAPS_BETWEEN_DECLARED_REQUEST_WINDOWS')
    return dict(mode='UNATTESTED_RETAINED_ACTIVITY_REVIEW', status='REVIEW_REQUIRED', read_only=True,
        account_sha256=retained['account_sha256'], head_sha256=retained['head_sha256'],
        retained_batches=retained['retained_batches'], retained_activities=retained['retained_activities'],
        distinct_orders=len(snapshots), distinct_order_snapshots=versions,
        ambiguous_order_ids=ambiguous, snapshot_revision_order_ids=sorted(set(regressed)),
        snapshot_selection='REPORTED_UPDATED_AT_DIAGNOSTIC_ONLY',
        normalization_evaluated=normalization is not None, activity_normalization=normalization,
        collection_observation_review=observation_review,
        requested_window_review=dict(scope='CALLER_DECLARED_CREATION_WINDOWS_ONLY',
            unique_windows=len(windows), components=components, gaps_between_requested_windows=gaps,
            boundary_semantics_verified=False, activity_coverage_verified=False),
        review_reasons=sorted(reasons), source='SUPPLIED_UNATTESTED',
        activity_coverage_verified=False, fee_coverage_complete=False, broker_account_reconciled=False,
        spendable_funds_verified=False, ledger_applied=False, broker_connected=False,
        ready_for_routing=False, live_orders_enabled=False, automatic_continuation=False,
        limitations='Retained supplied observations only. Latest reported order time is not an atomic broker snapshot or verified event order. Equal-time conflicts and quantity/terminal revisions require review. Requested-window unions/gaps describe collection intent, not received-page completeness, boundary inclusion, execution/fee coverage or source attestation. No guessed fee links, posting times, fills, holdings, spending or resume permission.')


def fixture_report():
    """Two explicitly synthetic batches in temporary storage, no actual account IO."""
    import tempfile
    from pathlib import Path
    from nexus.broker_fee_projection import fixture_inputs
    data = fixture_inputs(); orders = data['orders']
    rows = [_raw(record) for record in adapter.fixture_report()['normalized_activities']]
    first = dict(rows[0], qty='.4', cum_qty='.4', leaves_qty='.6', type='partial_fill', price='85',
                 transaction_time='2026-10-17T18:00:00Z')
    second = dict(rows[0], id='20261017180000000::11234567-89ab-cdef-0123-000000000005',
                  qty='.6', cum_qty='1', leaves_qty='0', price='110')
    initial_order = dict(orders[0], status='partially_filled', filled_quantity='.4', average_fill_price='85',
                         updated_at='2026-10-17T18:00:00Z')
    with tempfile.TemporaryDirectory(prefix='nexus-activity-review-') as temporary:
        store = BrokerActivityStore(Path(temporary)/'activities.db', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        scope = dict(after='2026-10-17T00:00:00Z', until='2026-10-18T00:00:00Z')
        store.retain([first], [initial_order], **scope)
        store.retain([second] + rows[1:], orders, **scope)
        report = store.review()
        if report['review_reasons'] != ['ACTIVITY_COVERAGE_UNVERIFIED', 'FEE_LINK_AND_TIMING_UNVERIFIED']:
            raise ValueError('BROKER_ACTIVITY_REVIEW_FIXTURE_DISAGREEMENT')
    return dict(mode='DISPOSABLE_MERGED_ACTIVITY_REVIEW', passed=True, source='FIXED_SYNTHETIC_REST_EXAMPLE',
                review=report, isolated_temporary_storage=True, application_accounts_changed=False,
                broker_connected=False, live_orders_enabled=False, release_approved=False)
