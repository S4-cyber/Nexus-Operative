"""Head-bound retained REST fill arithmetic against supplied total balances.

No IO except read-only retained-history replay. Fees remain unallocated: dates,
references and numerical residual matches never supply posting/link authority.
"""
from collections import defaultdict
from decimal import Decimal, localcontext
import re

from nexus import alpaca_activity_normalizer as adapter
from nexus import broker_activity_review
from nexus.broker_activity_store import BrokerActivityStore
from nexus.broker_fee_projection import balances
from nexus.alpaca_paper_contract import number, text, sha

POLICY = 'nexus.retained-gross-fill-residual-review.v2'
MAX_SAMPLES = 20


def _fail(code):
    raise ValueError('RETAINED_BALANCE_REVIEW_' + code)


def _time_key(value):
    """Order a canonical UTC instant without rounding its nanoseconds."""
    base, separator, fraction = value[:-6].partition('.')
    return base, (fraction if separator else '').ljust(9, '0')


def _gross_review(records, opening, observed, start, end):
    current = dict(opening); minimum = dict(opening); groups = defaultdict(list)
    fees = {'USD':Decimal(0), 'BTC':Decimal(0)}; fee_counts = dict(USD=0, BTC=0)
    exact_fees = {'USD':Decimal(0), 'BTC':Decimal(0)}
    exact_counts = dict(USD=0, BTC=0); date_counts = dict(USD=0, BTC=0)
    outside_counts = dict(USD=0, BTC=0)
    start_key, end_key = _time_key(start), _time_key(end)
    for row in records:
        if row['kind'] == 'FILL': groups[row['occurred_at']].append(row)
        else:
            asset = row['asset']; amount = number(row['amount'], zero=True)
            fees[asset] += amount; fee_counts[asset] += 1
            reported = row['reported_date']
            if 'T' not in reported: date_counts[asset] += 1
            elif start_key < _time_key(reported) <= end_key:
                exact_fees[asset] += amount; exact_counts[asset] += 1
            else: outside_counts[asset] += 1
    covered = True; shortfalls = []; events = 0
    for at, rows in sorted(groups.items()):
        debit = {'USD':Decimal(0), 'BTC':Decimal(0)}; credit = dict(debit)
        for row in rows:
            qty = number(row['quantity']); gross = qty * number(row['price'])
            buy = row['side'] == 'BUY'
            debit['USD' if buy else 'BTC'] += gross if buy else qty
            credit['BTC' if buy else 'USD'] += qty if buy else gross
        for asset in ('USD','BTC'):
            if current[asset] < debit[asset]:
                covered = False; events += 1
                reason = 'AMBIGUOUS_EQUAL_NANOSECOND_FUNDING' if len(rows)>1 and current[asset]-debit[asset]+credit[asset]>=0 else 'GROSS_ONLY_INSUFFICIENT_' + asset
                if len(shortfalls) < MAX_SAMPLES:
                    shortfalls.append(dict(at=at, asset=asset, reason=reason, available=text(current[asset]),
                        required=text(debit[asset]), shortfall=text(debit[asset]-current[asset])))
            current[asset] += credit[asset] - debit[asset]
            minimum[asset] = min(minimum[asset], current[asset])
    residual = {k:current[k]-observed[k] for k in ('USD','BTC')}
    complete = {k:fee_counts[k] > 0 and exact_counts[k] == fee_counts[k] for k in fees}
    return dict(gross_execution_ending_balances={k:text(v) for k,v in current.items()},
        gross_minus_supplied_observed={k:text(v) for k,v in residual.items()},
        gross_only_debits_covered=covered, gross_only_minimum_group_end_balances={k:text(v) for k,v in minimum.items()},
        execution_groups=len(groups), executions=sum(len(rows) for rows in groups.values()),
        equal_nanosecond_groups=sum(len(rows)>1 for rows in groups.values()),
        gross_only_shortfalls=shortfalls, gross_only_shortfall_events=events, shortfall_sample_limit=MAX_SAMPLES,
        shortfall_samples_truncated=events>len(shortfalls),
        all_retained_unallocated_fee_debits={k:text(v) for k,v in fees.items()}, retained_fee_records_by_asset=fee_counts,
        residual_equals_all_retained_fee_debits={k:None if fee_counts[k]==0 else residual[k]==fees[k] for k in fees},
        exact_interval_fee_debits={k:text(v) for k,v in exact_fees.items()},
        exact_interval_fee_records_by_asset=exact_counts, date_only_fee_records_by_asset=date_counts,
        exact_fee_records_outside_interval_by_asset=outside_counts, fee_temporal_scope_complete_by_asset=complete,
        residual_equals_exact_interval_fee_debits={k:residual[k]==exact_fees[k] if complete[k] else None for k in fees},
        fee_scope='EXACT_TIMESTAMP_FILTERED_OR_ALL_RETAINED_UNALLOCATED_DEBITS_NEITHER_VERIFIES_PROVIDER_POSTING',
        arithmetic_scope='EXPLICIT_GROSS_FILLS_ONLY_FEES_NEVER_SPENT_OR_ALLOCATED',
        time_scope='CANONICAL_UTC_UP_TO_NANOSECONDS_EQUAL_INSTANT_DEBITS_REQUIRE_PRE_GROUP_FUNDS',
        fee_posting_interval_verified=False, fee_debits_allocated_to_interval=False,
        fee_execution_links_verified=False, fee_reserved_funding_verified=False)


def _review_snapshot(retained, *, account, expected_head, opening, observed, start, end):
    """Pure calculation on one caller-validated snapshot; caller must recheck head."""
    merged = broker_activity_review._review_snapshot(retained, account=account)
    reasons = set(merged['review_reasons']) | {'OPENING_AND_OBSERVED_BALANCES_UNATTESTED',
        'FEE_DEBITS_NOT_ALLOCATED_TO_EXECUTIONS_OR_INTERVAL', 'GROSS_FUNDING_EXCLUDES_FEE_OBLIGATIONS'}
    normalization = merged['activity_normalization']; arithmetic = None
    allowed = {'ACTIVITY_COVERAGE_UNVERIFIED','FEE_LINK_AND_TIMING_UNVERIFIED'}
    if normalization is None or set(normalization['review_reasons']) - allowed:
        reasons.add('RETAINED_FILL_ARITHMETIC_NOT_EVALUATED')
    else:
        records = normalization['normalized_activities']
        # Never drop inconvenient fills or use an opening balance that already
        # includes the same fill. Selection is explicit (opening, observed].
        outside = [r['activity_id'] for r in records if r['kind']=='FILL' and not start < r['occurred_at'] <= end]
        latest_times = [r['updated_at'] for receipt in retained['receipts'].values() for r in receipt['orders']]
        if outside:
            reasons.add('RETAINED_FILL_OUTSIDE_SELECTED_INTERVAL')
        if any(at > end for at in latest_times): reasons.add('RETAINED_ORDER_SNAPSHOT_AFTER_OBSERVATION')
        if not outside and not any(at > end for at in latest_times):
            with localcontext() as context:
                context.prec = 80
                arithmetic = _gross_review(records, opening, observed, start, end)
            if not arithmetic['gross_only_debits_covered']: reasons.add('GROSS_ONLY_FUNDING_SHORTFALL')
            if any(Decimal(v)<0 for v in arithmetic['gross_execution_ending_balances'].values()):
                reasons.add('NEGATIVE_GROSS_EXECUTION_ENDING_BALANCE')
    scope = dict(policy=POLICY, account_sha256=retained['account_sha256'], head_sha256=expected_head,
        opening_balances={k:text(v) for k,v in opening.items()}, opening_at=start,
        observed_balances={k:text(v) for k,v in observed.items()}, observed_at=end)
    return dict(mode='HEAD_BOUND_RETAINED_GROSS_BALANCE_REVIEW', status='REVIEW_REQUIRED', read_only=True,
        **scope, input_sha256=sha(scope), head_rechecked=False, admission_reserved=False, atomic_snapshot=False,
        arithmetic_evaluated=arithmetic is not None, gross_fill_review=arithmetic,
        retained_activity_review=merged, review_reasons=sorted(reasons), source='SUPPLIED_UNATTESTED',
        source_coverage_verified=False, fee_coverage_complete=False, fee_execution_links_verified=False,
        holds_reconciled=False, broker_account_reconciled=False, spendable_funds_verified=False,
        ledger_applied=False, ready_for_routing=False, broker_connected=False, live_orders_enabled=False,
        automatic_retry=False, automatic_continuation=False,
        limitations='Read-only diagnostic of one retained head plus supplied TOTAL balances. Gross fills omit all fee obligations; a passing gross path is not spendable funding. All-retained residual equality is explicitly unallocated. Exact fee timestamps permit filtering only when every retained fee for an asset is inside the selected interval; neither form verifies provider posting, source completeness, associations or settled balances. No guessed fee links, date-to-instant conversion, cancellation, account write, reservation or resume permission. Final head recheck is a sample only, not protection from later changes, rewritten hashes or valid-tail deletion.')


def review(store, *, expected_account_id, expected_head, opening_balances, opening_at,
           observed_balances, observed_at):
    """Inspect exact retained history, then recheck its head before returning.

    A recheck is no lock/reservation and does not attest supplied opening/observed
    balances, source completeness or a broker snapshot. No automatic continuation.
    """
    if type(store) is not BrokerActivityStore: _fail('STORE_REQUIRED')
    account = adapter._uuid(expected_account_id)
    if store.account != account: _fail('ACCOUNT_DISAGREEMENT')
    if not isinstance(expected_head, str) or re.fullmatch(r'[0-9a-f]{64}', expected_head) is None: _fail('HEAD_REFERENCE')
    opening = balances(opening_balances); observed = balances(observed_balances)
    start, end = adapter._instant(opening_at), adapter._instant(observed_at)
    if end < start: _fail('INTERVAL_REGRESSION')
    retained = store.inspect()
    if retained['retained_batches'] == 0: _fail('HISTORY_REQUIRED')
    if retained['head_sha256'] != expected_head: _fail('HEAD_DISAGREEMENT')
    result = _review_snapshot(retained, account=account, expected_head=expected_head,
        opening=opening, observed=observed, start=start, end=end)
    # Complete replay again: concurrent append/corruption cannot return a result
    # bearing a no-longer-current head. Changes after this sample remain possible.
    current = store.inspect()
    if current['head_sha256'] != expected_head: _fail('HEAD_CHANGED_DURING_REVIEW')
    result['head_rechecked'] = True
    return result


def fixture_report():
    """Synthetic raw REST records, real temporary retention/review, no provider."""
    import tempfile
    from pathlib import Path
    from nexus.broker_activity_store import _raw
    from nexus.broker_fee_projection import fixture_inputs
    data = fixture_inputs(); records = adapter.fixture_report()['normalized_activities']
    with tempfile.TemporaryDirectory(prefix='nexus-retained-balance-') as directory:
        store = BrokerActivityStore(Path(directory)/'activities.db','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        store.retain([_raw(r) for r in records], data['orders'], after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z')
        result = store.review_balances(expected_account_id=store.account, expected_head=store.inspect()['head_sha256'],
            opening_balances=data['opening_balances'],opening_at=data['opening_at'],
            observed_balances=data['observed_balances'],observed_at=data['observed_at'])
        arithmetic = result['gross_fill_review']
        stale_rejected = False
        try:
            store.review_balances(expected_account_id=store.account, expected_head='0'*64,
                opening_balances=data['opening_balances'],opening_at=data['opening_at'],
                observed_balances=data['observed_balances'],observed_at=data['observed_at'])
        except ValueError as error:
            if str(error) != 'RETAINED_BALANCE_REVIEW_HEAD_DISAGREEMENT': raise
            stale_rejected = True
        date_only_not_promoted = (arithmetic['residual_equals_all_retained_fee_debits'] == {'USD':True,'BTC':True}
            and arithmetic['residual_equals_exact_interval_fee_debits'] == {'USD':None,'BTC':None})
        exact_records = []
        for record in records:
            copy = dict(record)
            if copy['kind'] == 'CFEE': copy['reported_date'] = '2026-10-17T18:00:01Z'
            if copy['kind'] == 'FEE': copy['reported_date'] = '2026-10-17T18:00:02Z'
            exact_records.append(copy)
        exact_store = BrokerActivityStore(Path(directory)/'exact-activities.db',store.account)
        exact_store.retain([_raw(r) for r in exact_records], data['orders'], after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z')
        exact = exact_store.review_balances(expected_account_id=exact_store.account,
            expected_head=exact_store.inspect()['head_sha256'], opening_balances=data['opening_balances'],
            opening_at=data['opening_at'], observed_balances=data['observed_balances'], observed_at=data['observed_at'])
        exact_timestamped_compared = exact['gross_fill_review']['residual_equals_exact_interval_fee_debits'] == {'USD':True,'BTC':True}
        passed = result['arithmetic_evaluated'] and arithmetic['gross_only_debits_covered'] and date_only_not_promoted and exact_timestamped_compared
        return dict(passed=passed and stale_rejected,stale_head_rejected=stale_rejected,
                    date_only_fee_residual_not_promoted=date_only_not_promoted,
                    exact_timestamped_fee_residual_compared=exact_timestamped_compared,review=result,source='FIXED_SYNTHETIC_RETAINED_REST_FIXTURE',
                    broker_connected=False,live_orders_enabled=False)
