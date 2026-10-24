"""Bind exact provider POST reconciliation to retained fee references.

This private offline composition reads caller-selected journals and supplied
observations only. It requires every retained fee record to carry an explicit
order/execution pair resolving to an accepted original fill. Those REST fields
remain unverified reference hints: this grants no fee-coverage, posting-time,
funds, order, ledger, routing or release authority.
"""
from nexus.alpaca_paper_contract import sha, timestamp_order_ns
from nexus.broker_activity_store import BrokerActivityStore
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore
from nexus import paper_provider_post_reconciliation_review as reconciliation
from nexus import paper_request_activity_acceptance as accepted


POLICY = 'nexus.paper-provider-post-fee-reference-review.v1'
FLAGS = tuple(dict.fromkeys((*reconciliation.FLAGS, *accepted.FLAGS,
    'retained_fee_references_verified', 'fee_execution_links_verified',
    'fee_coverage_complete', 'fee_posting_interval_verified',
    'fee_debits_allocated_to_interval', 'fee_reserved_funding_verified')))


def _fail(code):
    raise ValueError('PAPER_PROVIDER_POST_FEE_REFERENCE_' + code) from None


def _component_valid(result):
    return (result.get('mode') == 'PAPER_PROVIDER_POST_RECONCILIATION_REVIEW'
        and result.get('status') == 'REVIEW_REQUIRED'
        and result.get('read_only') is True
        and result.get('post_activity_component_bound') is True
        and result.get('account_evidence_component_bound') is True
        and result.get('complete_accepted_history_replayed') is True
        and result.get('guarded_balance_evidence_bound') is True
        and result.get('journals_rechecked_unchanged') is True
        and any(result.get(flag) is not False for flag in reconciliation.FLAGS)
            is False)


def review(post_budget, *, expected_post_head, account_id, journal,
           expected_request_head, pacing_path, activity_store_path,
           acceptance_store_path, expected_activity_head,
           pre_opening_observation, opening_observation, closing_observation,
           post_closing_observation):
    """Require complete local fee-reference partition after exact reconciliation."""
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
    base = reconciliation.review(**options)
    if not _component_valid(base):
        _fail('RECONCILIATION_INVALID')

    common = dict(expected_account_id=account_id,
        request_journal_path=journal.path, pacing_path=pacing_path,
        activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path,
        expected_request_head=expected_request_head,
        expected_activity_head=expected_activity_head)
    activity = accepted.review(**common)
    if (activity.get('read_only') is not True or
            activity.get('complete_accepted_history_replayed') is not True or
            any(activity.get(flag) is not False for flag in accepted.FLAGS)):
        _fail('ACCEPTED_ACTIVITY_INVALID')

    store = BrokerActivityStore(activity_store_path, account_id)
    retained = store.inspect()
    acceptance = PaperActivityAcceptanceStore(acceptance_store_path, account_id)
    scopes = acceptance.replay_scopes()
    accepted_receipts = {scope['retained_receipt_sha256'] for scope in scopes}
    if accepted_receipts != set(retained['receipts']):
        _fail('ACCEPTED_RECEIPT_PARTITION')
    records = {row['activity_id']: row
        for row in retained['normalized_activities']}
    if len(records) != len(retained['normalized_activities']):
        _fail('ACTIVITY_IDENTITY_CONFLICT')
    fees = [row for row in retained['normalized_activities']
        if row['kind'] != 'FILL']
    if not fees:
        _fail('FEES_REQUIRED')
    requests = {row['broker_order_id']: row
        for row in activity['request_activity_review']['request_reviews']
        if row['broker_order_id'] is not None and row['local_terms_agree'] is True}
    links = []
    timestamped = 0
    date_only = 0
    for fee in fees:
        order_id = fee.get('order_reference')
        execution_id = fee.get('unverified_execution_reference')
        if order_id is None or execution_id is None:
            _fail('REFERENCE_REQUIRED')
        fill = records.get(execution_id)
        if fill is None or fill.get('kind') != 'FILL':
            _fail('REFERENCE_EXECUTION_UNKNOWN')
        if fill.get('broker_order_id') != order_id:
            _fail('REFERENCE_PAIR_DISAGREEMENT')
        request = requests.get(order_id)
        if (request is None or request['client_order_id'] !=
                fill.get('client_order_id')):
            _fail('REFERENCE_NOT_ACCEPTED_ORIGINAL_FILL')
        reported = fee.get('reported_date')
        if type(reported) is not str:
            _fail('REPORTED_DATE_INVALID')
        if 'T' in reported:
            try:
                if timestamp_order_ns(reported) < timestamp_order_ns(
                        fill['occurred_at']):
                    _fail('REFERENCE_TIME_PRECEDES_FILL')
            except (ValueError, TypeError, KeyError, OverflowError,
                    AttributeError):
                _fail('REPORTED_DATE_INVALID')
            timestamped += 1
        else:
            date_only += 1
        links.append(dict(fee_activity_id=fee['activity_id'],
            fee_kind=fee['kind'], fee_asset=fee['asset'],
            fee_amount=fee['amount'], reported_date=reported,
            fill_activity_id=execution_id, broker_order_id=order_id,
            client_order_id=fill['client_order_id']))

    repeated = reconciliation.review(**options)
    if repeated != base or store.inspect() != retained or \
            acceptance.replay_scopes() != scopes:
        _fail('HISTORY_CHANGED_DURING_REVIEW')
    scope = dict(policy=POLICY, account_sha256=journal.account_digest,
        post_head_sha256=expected_post_head,
        request_head_sha256=expected_request_head,
        activity_head_sha256=expected_activity_head,
        pacing_head_sha256=base['pacing_head_sha256'],
        acceptance_head_sha256=base['acceptance_head_sha256'],
        post_reconciliation_sha256=sha(base),
        accepted_activity_sha256=sha(activity),
        retained_fee_reference_sha256=sha(sorted(links,
            key=lambda row: row['fee_activity_id'])))
    return dict(mode='PAPER_PROVIDER_POST_FEE_REFERENCE_REVIEW',
        status='REVIEW_REQUIRED', read_only=True, **scope,
        input_sha256=sha(scope), post_reconciliation_bound=True,
        complete_accepted_history_replayed=True,
        accepted_receipt_partition_complete=True,
        retained_fee_records=len(fees),
        explicitly_referenced_fee_records=len(links),
        timestamped_fee_references=timestamped,
        date_only_fee_references=date_only,
        all_retained_fees_explicitly_referenced=True,
        all_references_resolve_to_accepted_original_fills=True,
        journals_rechecked_unchanged=True,
        reference_semantics_unverified=True,
        **{flag: False for flag in FLAGS},
        limitations='One private exact-head local composition binds every retained fee reference pair to an accepted original fill after provider POST, request/activity and guarded balance reconciliation. REST order/execution reference fields remain unverified hints; date-only fees have no posting instant. This does not prove provider source, fee completeness, fee allocation to the observed balance interval, holds, settled/spendable funds, profitability or broker-account acceptance. Separate journals are not atomic or distributed. No provider/vault IO, write, retry, continuation, ledger, routing, funding, live trading or release approval.')


def _fixture(root, *, fee_mode='linked'):
    return reconciliation._fixture(root, fee_mode=fee_mode)


def fixture_report():
    """Synthetic linked, missing and unreferenced controls; no external IO."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(
            prefix='nexus-post-fee-reference-') as directory:
        root = Path(directory)
        options = _fixture(root / 'linked')
        before = {path.relative_to(root): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}
        result = review(**options)
        repeated = review(**options)
        unchanged = before == {path.relative_to(root): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}
        negative = {}
        for name, mode, code in (
                ('missing', None, 'FEES_REQUIRED'),
                ('unreferenced', 'unreferenced', 'REFERENCE_REQUIRED'),
                ('unknown', 'unknown', 'REFERENCE_EXECUTION_UNKNOWN')):
            args = _fixture(root / name, fee_mode=mode)
            try:
                review(**args)
                negative[name] = False
            except ValueError as error:
                negative[name] = str(error) == \
                    'PAPER_PROVIDER_POST_FEE_REFERENCE_' + code
        checks = dict(
            post_reconciliation_bound=result['post_reconciliation_bound'],
            complete_accepted_history_replayed=
                result['complete_accepted_history_replayed'],
            accepted_receipt_partition_complete=
                result['accepted_receipt_partition_complete'],
            all_retained_fees_explicitly_referenced=
                result['all_retained_fees_explicitly_referenced'] and
                result['retained_fee_records'] == 1 and
                result['explicitly_referenced_fee_records'] == 1,
            references_resolve_to_accepted_original_fills=
                result['all_references_resolve_to_accepted_original_fills'],
            date_only_scope_preserved=result['date_only_fee_references'] == 1 and
                result['timestamped_fee_references'] == 0 and
                result['reference_semantics_unverified'] is True,
            missing_fee_history_rejected=negative['missing'],
            unreferenced_fee_rejected=negative['unreferenced'],
            unknown_execution_reference_rejected=negative['unknown'],
            repeat_review_matches=result == repeated,
            journals_unchanged=unchanged,
            output_never_authoritative=
                all(result[flag] is False for flag in FLAGS))
        return dict(source='SYNTHETIC_LOCAL_PROVIDER_POST_FEE_REFERENCE_FIXTURE',
            passed=all(checks.values()), **checks,
            **{flag: False for flag in FLAGS})
