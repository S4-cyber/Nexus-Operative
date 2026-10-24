"""Reserve retained referenced fees at accepted fill groups, read-only.

This private composition evaluates only the fee obligations present in the
caller-selected retained history.  It does not declare that history complete,
turn a reported fee date into a posting instant, or establish spendable funds.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext

from nexus.alpaca_paper_contract import number, sha, text
from nexus.broker_activity_store import BrokerActivityStore
from nexus import paper_provider_post_fee_reference_review as references
from nexus import portable_paper_observation as portable


POLICY = 'nexus.paper-provider-post-known-fee-reserve-review.v1'
FLAGS = tuple(dict.fromkeys((*references.FLAGS,
    'known_fee_coverage_complete', 'known_fee_posting_verified',
    'retained_fee_reserve_path_verified', 'spendable_funds_verified',
    'broker_account_reconciled', 'ready_for_routing', 'release_approved')))


def _fail(code):
    raise ValueError('PAPER_PROVIDER_POST_KNOWN_FEE_RESERVE_' + code) from None


def _component_valid(result):
    return (result.get('mode') == 'PAPER_PROVIDER_POST_FEE_REFERENCE_REVIEW'
        and result.get('status') == 'REVIEW_REQUIRED'
        and result.get('read_only') is True
        and result.get('post_reconciliation_bound') is True
        and result.get('complete_accepted_history_replayed') is True
        and result.get('accepted_receipt_partition_complete') is True
        and result.get('all_retained_fees_explicitly_referenced') is True
        and result.get('all_references_resolve_to_accepted_original_fills') is True
        and result.get('journals_rechecked_unchanged') is True
        and any(result.get(flag) is not False for flag in references.FLAGS)
            is False)


def _totals(observation):
    value = portable.validate(observation)
    inventory = value['observed_inventory']
    if (value['checks'] != 'OBSERVED_FIELDS_COVERED' or
            inventory['scope_supported'] is not True or
            inventory['cash'] is None or inventory['btc_total'] is None):
        _fail('TOTAL_BALANCES_REQUIRED')
    try:
        return value, {'USD': number(inventory['cash'], zero=True),
            'BTC': number(inventory['btc_total'], zero=True)}
    except (ValueError, TypeError, KeyError, InvalidOperation):
        _fail('TOTAL_BALANCES_REQUIRED')


def _reserve_path(records, opening):
    fills = {row['activity_id']: row for row in records
        if row['kind'] == 'FILL'}
    if not fills:
        _fail('FILLS_REQUIRED')
    fees_by_fill = defaultdict(list)
    fee_orders = set()
    for fee in records:
        if fee['kind'] == 'FILL':
            continue
        fill_id = fee.get('unverified_execution_reference')
        fill = fills.get(fill_id)
        if fill is None or fee.get('order_reference') != \
                fill.get('broker_order_id'):
            _fail('REFERENCE_COMPONENT_DISAGREEMENT')
        fees_by_fill[fill_id].append(fee)
        fee_orders.add(fill['broker_order_id'])
    if not fees_by_fill:
        _fail('FEES_REQUIRED')
    fill_orders = {row['broker_order_id'] for row in fills.values()}
    if fee_orders != fill_orders:
        _fail('ACCEPTED_ORDER_WITHOUT_REFERENCED_FEE')

    groups = defaultdict(list)
    for fill in fills.values():
        groups[fill['occurred_at']].append(fill)
    current = dict(opening)
    minimum = dict(opening)
    fee_totals = {'USD': Decimal(0), 'BTC': Decimal(0)}
    fee_counts = {'USD': 0, 'BTC': 0}
    with localcontext() as context:
        context.prec = 80
        for _, rows in sorted(groups.items()):
            debit = {'USD': Decimal(0), 'BTC': Decimal(0)}
            credit = {'USD': Decimal(0), 'BTC': Decimal(0)}
            for fill in rows:
                quantity = number(fill['quantity'])
                gross = quantity * number(fill['price'])
                buy = fill['side'] == 'BUY'
                spent = 'USD' if buy else 'BTC'
                received = 'BTC' if buy else 'USD'
                debit[spent] += gross if buy else quantity
                incoming = quantity if buy else gross
                retained_received_asset_fees = Decimal(0)
                for fee in fees_by_fill.get(fill['activity_id'], ()):
                    asset = fee['asset']
                    amount = number(fee['amount'], zero=True)
                    if amount == 0:
                        _fail('POSITIVE_FEE_OBLIGATION_REQUIRED')
                    fee_totals[asset] += amount
                    fee_counts[asset] += 1
                    if asset == received:
                        retained_received_asset_fees += amount
                    else:
                        debit[asset] += amount
                if retained_received_asset_fees > incoming:
                    _fail('FEE_EXCEEDS_RECEIVED_ASSET')
                credit[received] += incoming - retained_received_asset_fees
            for asset in ('USD', 'BTC'):
                if current[asset] < debit[asset]:
                    _fail('KNOWN_FEE_DEBITS_NOT_COVERED')
                current[asset] += credit[asset] - debit[asset]
                minimum[asset] = min(minimum[asset], current[asset])
    return dict(ending=current, minimum=minimum, groups=len(groups),
        fills=len(fills), fees=sum(fee_counts.values()), fee_totals=fee_totals,
        fee_counts=fee_counts, orders=len(fill_orders))


def review(post_budget, *, expected_post_head, account_id, journal,
           expected_request_head, pacing_path, activity_store_path,
           acceptance_store_path, expected_activity_head,
           pre_opening_observation, opening_observation, closing_observation,
           post_closing_observation):
    """Compose reference binding with a conservative retained-fee cash path."""
    options = dict(post_budget=post_budget,
        expected_post_head=expected_post_head, account_id=account_id,
        journal=journal, expected_request_head=expected_request_head,
        pacing_path=pacing_path, activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path,
        expected_activity_head=expected_activity_head,
        pre_opening_observation=pre_opening_observation,
        opening_observation=opening_observation,
        closing_observation=closing_observation,
        post_closing_observation=post_closing_observation)
    base = references.review(**options)
    if not _component_valid(base):
        _fail('REFERENCE_COMPONENT_INVALID')
    opening_manifest, opening = _totals(opening_observation)
    closing_manifest, closing = _totals(closing_observation)
    if (opening_manifest['account_id'] != account_id or
            closing_manifest['account_id'] != account_id):
        _fail('ACCOUNT_DISAGREEMENT')

    store = BrokerActivityStore(activity_store_path, account_id)
    retained = store.inspect()
    path = _reserve_path(retained['normalized_activities'], opening)
    if path['ending'] != closing:
        _fail('KNOWN_FEE_ADJUSTED_BALANCE_DISAGREEMENT')

    repeated = references.review(**options)
    if repeated != base or store.inspect() != retained:
        _fail('HISTORY_CHANGED_DURING_REVIEW')
    scope = dict(policy=POLICY, account_sha256=journal.account_digest,
        post_head_sha256=expected_post_head,
        request_head_sha256=expected_request_head,
        activity_head_sha256=expected_activity_head,
        pacing_head_sha256=base['pacing_head_sha256'],
        acceptance_head_sha256=base['acceptance_head_sha256'],
        opening_manifest_sha256=opening_manifest['manifest_sha256'],
        closing_manifest_sha256=closing_manifest['manifest_sha256'],
        fee_reference_review_sha256=sha(base),
        retained_known_fee_path_sha256=sha(dict(
            ending={k: text(v) for k, v in path['ending'].items()},
            minimum={k: text(v) for k, v in path['minimum'].items()},
            fee_totals={k: text(v) for k, v in path['fee_totals'].items()},
            fee_counts=path['fee_counts'], groups=path['groups'],
            fills=path['fills'], fees=path['fees'], orders=path['orders'])))
    return dict(mode='PAPER_PROVIDER_POST_KNOWN_FEE_RESERVE_REVIEW',
        status='REVIEW_REQUIRED', read_only=True, **scope,
        input_sha256=sha(scope), fee_reference_component_bound=True,
        retained_known_fee_reservations_applied=True,
        retained_known_fee_debits_covered=True,
        known_fee_adjusted_closing_balances_match=True,
        accepted_orders_with_referenced_fees=path['orders'],
        execution_groups_reviewed=path['groups'],
        accepted_fills_reviewed=path['fills'],
        retained_fees_reviewed=path['fees'],
        retained_fee_records_by_asset=path['fee_counts'],
        retained_known_fee_debits={k: text(v)
            for k, v in path['fee_totals'].items()},
        minimum_known_fee_reserved_balances={k: text(v)
            for k, v in path['minimum'].items()},
        journals_rechecked_unchanged=True,
        known_fee_reservation_is_retrospective=True,
        **{flag: False for flag in FLAGS},
        limitations='Retrospective private local consistency over only retained, explicitly referenced fee obligations. Known fees are conservatively reserved at their accepted fill group, independent of their unverified reported posting date. This neither proves complete fee/activity coverage nor provider source/reference semantics, fee posting, exact balance instants, holds, settlement or spendable funds. Separate journals are not atomic or distributed. No provider/vault IO, write, retry, continuation, ledger, routing, funding, live trading or release approval.')


def _fixture(root, *, closing_cash='801', opening_cash='1000',
             fee_mode='linked'):
    from nexus.paper_observation_review import _fixture_observation

    options = references._fixture(root, fee_mode=fee_mode)
    epoch = 1792195200 + 18 * 3600
    options.update(
        pre_opening_observation=_fixture_observation(
            options['account_id'], opening_cash, '0', epoch - 4, epoch - 3),
        opening_observation=_fixture_observation(
            options['account_id'], opening_cash, '0', epoch - 2, epoch - 1),
        closing_observation=_fixture_observation(
            options['account_id'], closing_cash, '2', epoch + 3, epoch + 4),
        post_closing_observation=_fixture_observation(
            options['account_id'], closing_cash, '2', epoch + 6, epoch + 7))
    return options


def fixture_report():
    """Synthetic aggregate-only positive and negative controls."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix='nexus-known-fee-reserve-') as directory:
        root = Path(directory)
        options = _fixture(root / 'consistent')
        before = {path.relative_to(root): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}
        result = review(**options)
        repeated = review(**options)
        unchanged = before == {path.relative_to(root): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}
        failures = {}
        for name, changes, code in (
                ('balance', {'closing_cash': '802'},
                    'KNOWN_FEE_ADJUSTED_BALANCE_DISAGREEMENT'),
                ('funding', {'opening_cash': '50', 'closing_cash': '1'},
                    'KNOWN_FEE_DEBITS_NOT_COVERED'),
                ('missing', {'fee_mode': None},
                    'PAPER_PROVIDER_POST_FEE_REFERENCE_FEES_REQUIRED')):
            args = _fixture(root / name, **changes)
            try:
                review(**args)
                failures[name] = False
            except ValueError as error:
                failures[name] = code in str(error)
        checks = dict(
            fee_reference_component_bound=
                result['fee_reference_component_bound'],
            retained_known_fee_reservations_applied=
                result['retained_known_fee_reservations_applied'],
            retained_known_fee_debits_covered=
                result['retained_known_fee_debits_covered'],
            known_fee_adjusted_closing_balances_match=
                result['known_fee_adjusted_closing_balances_match'],
            balance_disagreement_rejected=failures['balance'],
            insufficient_known_fee_funding_rejected=failures['funding'],
            missing_fee_history_rejected=failures['missing'],
            repeat_review_matches=result == repeated,
            journals_unchanged=unchanged,
            output_never_authoritative=
                all(result[flag] is False for flag in FLAGS))
        return dict(source='SYNTHETIC_LOCAL_PROVIDER_POST_KNOWN_FEE_RESERVE_FIXTURE',
            passed=all(checks.values()), **checks,
            **{flag: False for flag in FLAGS})
