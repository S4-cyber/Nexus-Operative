"""Bind a private observation-pair review to accepted activity history.

This offline composition replays the complete cooperating pacing, activity and
acceptance journals before and after balance arithmetic.  It never reads a
credential, contacts a provider, writes evidence or grants order authority.
"""
import argparse
import getpass
import json
import sqlite3

from nexus.alpaca_paper_contract import sha
from nexus.broker_activity_store import BrokerActivityStore
from nexus import paper_observation_review
from nexus import portable_paper_observation as portable
from nexus import alpaca_activity_normalizer as adapter
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore
from nexus.paper_activity_acceptance_probe import status as acceptance_status

POLICY = 'nexus.paper-reconciliation-acceptance.v2'
STABILITY_POLICY = 'nexus.paper-reconciliation-stability-guard.v1'


def _fail(code):
    raise ValueError('PAPER_RECONCILIATION_ACCEPTANCE_' + code)


def _accepted(status):
    """Require every retained cooperating transition, not merely accepted tail replay."""
    return (
        status['cross_journal_recovery_state'] == 'BOUND_TRANSITIONS_REPLAYED_REVIEW_REQUIRED'
        and status['cross_journal_heads_match'] is True
        and status['accepted_transitions'] > 0
        and status['preexisting_history_unverified'] is False
        and status['accepted_transitions'] == status['sessions']
        and status['accepted_transitions'] == status['retained_batches']
        and status['accepted_transitions'] == status['retained_collection_observations']
        and status['all_collection_cursor_sequences_complete'] is True
        and status['all_pacing_operations_typed'] is True
        and status['all_pacing_result_receipts_bound'] is True
        and status['all_pacing_request_targets_bound'] is True
        and status['all_pacing_normalized_results_bound'] is True
        and status['replayed_accepted_transitions'] == status['accepted_transitions']
        and status['latest_acceptance_record_replayed'] is True
        and status['all_accepted_transitions_replayed'] is True
    )


def _declared_window_coverage(receipts, scopes, first, last):
    """Union requested windows exactly; never infer received/provider coverage."""
    opening = {k:paper_observation_review._instant(first['read_timing'][k])
        for k in ('started_at','completed_at')}
    closing = {k:paper_observation_review._instant(last['read_timing'][k])
        for k in ('started_at','completed_at')}
    if opening['completed_at'] >= closing['started_at']:
        _fail('OBSERVATION_WINDOWS_NOT_ORDERED')
    windows = []
    for scope in scopes:
        receipt = receipts.get(scope['retained_receipt_sha256'])
        if receipt is None: _fail('ACCEPTED_RECEIPT_CHANGED')
        start, end = adapter._instant(receipt['after']), adapter._instant(receipt['until'])
        if start >= end: _fail('DECLARED_WINDOW_INVALID')
        windows.append((start, end))
    merged = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else: merged.append([start, end])
    if not any(start <= opening['started_at'] and end >= closing['completed_at']
        for start, end in merged):
        _fail('DECLARED_WINDOWS_DO_NOT_COVER_OBSERVATION_ENVELOPE')
    return [dict(after=start, until=end) for start, end in merged]


def review(*, expected_account_id, pacing_path, activity_store_path,
           acceptance_store_path, expected_activity_head,
           opening_observation, closing_observation):
    """Read-only composition of accepted collection history and balance review."""
    before = acceptance_status(expected_account_id=expected_account_id,
        pacing_path=pacing_path, activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path)
    if not _accepted(before):
        _fail('ACCEPTED_HISTORY_REQUIRED')
    if before['retention_head_sha256'] != expected_activity_head:
        _fail('ACTIVITY_HEAD_DISAGREEMENT')

    first, last = portable.validate(opening_observation), portable.validate(closing_observation)
    store = BrokerActivityStore(activity_store_path, expected_account_id)
    retained = store.inspect()
    scopes = PaperActivityAcceptanceStore(acceptance_store_path, expected_account_id).replay_scopes()
    if retained['head_sha256'] != expected_activity_head or len(scopes) != before['accepted_transitions']:
        _fail('JOURNALS_CHANGED_DURING_REVIEW')
    windows = _declared_window_coverage(retained['receipts'], scopes, first, last)
    window_digest = sha(dict(account_sha256=before['account_sha256'], windows=windows))
    component = paper_observation_review.review(
        store,
        expected_account_id=expected_account_id, expected_head=expected_activity_head,
        opening_observation=first, closing_observation=last)

    after = acceptance_status(expected_account_id=expected_account_id,
        pacing_path=pacing_path, activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path)
    if before != after:
        _fail('JOURNALS_CHANGED_DURING_REVIEW')
    if component['head_sha256'] != after['retention_head_sha256']:
        _fail('COMPONENT_HEAD_DISAGREEMENT')

    scope = dict(policy=POLICY, account_sha256=after['account_sha256'],
        pacing_head_sha256=after['pacing_head_sha256'],
        activity_head_sha256=after['retention_head_sha256'],
        acceptance_head_sha256=after['acceptance_head_sha256'],
        declared_collection_window_union_sha256=window_digest,
        accepted_transitions=after['accepted_transitions'],
        opening_manifest_sha256=component['opening_manifest_sha256'],
        closing_manifest_sha256=component['closing_manifest_sha256'],
        observation_review_sha256=component['input_sha256'])
    return dict(mode='PAPER_RECONCILIATION_ACCEPTANCE_REVIEW',
        status='REVIEW_REQUIRED', read_only=True, **scope, input_sha256=sha(scope),
        journals_rechecked_unchanged=True, complete_accepted_history_replayed=True,
        legacy_baseline_absent=True, complete_pacing_sessions_accepted=True,
        accepted_pacing_operations_typed=True,
        accepted_pacing_result_receipts_bound=True,
        accepted_pacing_request_targets_bound=True,
        complete_retained_batches_accepted=True,
        complete_collection_observations_accepted=True,
        all_collection_cursor_sequences_complete=True,
        declared_collection_windows_cover_observation_envelope=True,
        declared_window_coverage_scope='REQUESTED_CREATION_WINDOWS_ONLY_NOT_RECEIVED_ACTIVITY_COVERAGE',
        requested_window_boundaries_verified=False,
        acceptance_recovery_state=after['cross_journal_recovery_state'],
        observation_review=component,
        admission_reserved=False, source_attested=False,
        source_coverage_verified=False, activity_coverage_verified=False,
        fee_coverage_complete=False, fee_execution_links_verified=False,
        balance_instants_verified=False, observation_windows_verified_quiet=False,
        holds_reconciled=False, broker_account_reconciled=False,
        spendable_funds_verified=False, ledger_applied=False,
        ready_for_routing=False, broker_connected=False, live_orders_enabled=False,
        money_movement_enabled=False, automatic_retry=False,
        automatic_continuation=False, release_approved=False,
        limitations='Complete local accepted-history replay binds this supplied observation pair to one exact retained activity head. The union of declared creation windows covers the full observation envelope geometrically only; it does not prove provider boundary semantics or received/late/corrected activity coverage. It does not attest the provider, prove complete activity/fee coverage, turn sequential reads into atomic balances, verify holds or spendable funds, coordinate other hosts/writers, authorize orders or approve release. Keep all journals, observations and output outside Git; never erase uncertainty to retry.')


def _guard_windows(pre_opening, opening, closing, post_closing):
    try:
        windows = {label:{key:paper_observation_review._instant(observation['read_timing'][key])
            for key in ('started_at','completed_at')}
            for label,observation in (('pre_opening',pre_opening),('opening',opening),
                                      ('closing',closing),('post_closing',post_closing))}
    except (KeyError, TypeError, ValueError):
        _fail('GUARD_TIME_INVALID')
    ordered = ('pre_opening','opening','closing','post_closing')
    if any(windows[label]['started_at'] > windows[label]['completed_at'] for label in ordered):
        _fail('GUARD_TIME_INVALID')
    if any(windows[first]['completed_at'] >= windows[second]['started_at']
        for first,second in zip(ordered,ordered[1:])):
        _fail('GUARD_WINDOWS_NOT_ORDERED')
    return windows


def _guard_fill_events(retained, windows):
    """Return explicit retained fills in either stability band, never infer coverage."""
    bands=((windows['pre_opening']['started_at'],windows['opening']['completed_at']),
           (windows['closing']['started_at'],windows['post_closing']['completed_at']))
    events=[]
    for row in retained['normalized_activities']:
        if row['kind']=='FILL' and any(start <= row['occurred_at'] <= end for start,end in bands):
            events.append(row['activity_id'])
    return sorted(set(events))


def review_with_stability_guards(*, expected_account_id, pacing_path, activity_store_path,
        acceptance_store_path, expected_activity_head, pre_opening_observation,
        opening_observation, closing_observation, post_closing_observation):
    """Bind four ordered supplied observations without promoting them to quiet instants."""
    status_options=dict(expected_account_id=expected_account_id,pacing_path=pacing_path,
        activity_store_path=activity_store_path,acceptance_store_path=acceptance_store_path)
    before=acceptance_status(**status_options)
    if not _accepted(before): _fail('ACCEPTED_HISTORY_REQUIRED')
    observations=[portable.validate(value) for value in (pre_opening_observation,
        opening_observation,closing_observation,post_closing_observation)]
    pre_opening,opening,closing,post_closing=observations
    if any(value['account_id']!=expected_account_id for value in observations):
        _fail('GUARD_ACCOUNT_DISAGREEMENT')
    if len({value['manifest_sha256'] for value in observations})!=4:
        _fail('GUARD_MANIFEST_REUSE')
    if len({value['source'] for value in observations})!=1:
        _fail('GUARD_SOURCE_DISAGREEMENT')
    if any(value['checks']!='OBSERVED_FIELDS_COVERED' for value in observations):
        _fail('GUARD_OBSERVATION_REQUIRES_REVIEW')
    if any(value['open_order_count']!=0 for value in observations):
        _fail('GUARD_OPEN_ORDERS_UNRECONCILED')
    windows=_guard_windows(pre_opening,opening,closing,post_closing)
    for guard,inner in ((pre_opening,opening),(closing,post_closing)):
        if (guard['account_fields_sha256']!=inner['account_fields_sha256'] or
            guard['position_count']!=inner['position_count'] or
            guard['observed_inventory']!=inner['observed_inventory']):
            _fail('GUARD_BALANCE_CHANGED')

    accepted=review(expected_account_id=expected_account_id,pacing_path=pacing_path,
        activity_store_path=activity_store_path,acceptance_store_path=acceptance_store_path,
        expected_activity_head=expected_activity_head,opening_observation=opening,
        closing_observation=closing)
    if accepted['observation_review']['arithmetic_evaluated'] is not True:
        _fail('GUARD_INNER_REVIEW_BLOCKED')
    store=BrokerActivityStore(activity_store_path,expected_account_id)
    retained=store.inspect()
    scopes=PaperActivityAcceptanceStore(acceptance_store_path,expected_account_id).replay_scopes()
    if retained['head_sha256']!=expected_activity_head or len(scopes)!=before['accepted_transitions']:
        _fail('JOURNALS_CHANGED_DURING_REVIEW')
    declared_windows=_declared_window_coverage(retained['receipts'],scopes,pre_opening,post_closing)
    fill_events=_guard_fill_events(retained,windows)
    if fill_events: _fail('GUARD_RETAINED_FILL_PRESENT')
    after=acceptance_status(**status_options)
    if before!=after or store.inspect()['head_sha256']!=expected_activity_head:
        _fail('JOURNALS_CHANGED_DURING_REVIEW')
    scope=dict(policy=STABILITY_POLICY,account_sha256=after['account_sha256'],
        activity_head_sha256=expected_activity_head,
        accepted_reconciliation_sha256=accepted['input_sha256'],
        pre_opening_manifest_sha256=pre_opening['manifest_sha256'],
        opening_manifest_sha256=opening['manifest_sha256'],
        closing_manifest_sha256=closing['manifest_sha256'],
        post_closing_manifest_sha256=post_closing['manifest_sha256'],
        declared_guard_window_union_sha256=sha(dict(account_sha256=after['account_sha256'],
            windows=declared_windows)))
    return dict(mode='PAPER_RECONCILIATION_STABILITY_GUARD_REVIEW',status='REVIEW_REQUIRED',
        read_only=True,**scope,input_sha256=sha(scope),accepted_reconciliation_review=accepted,
        guard_windows=windows,guard_manifests_distinct=True,guard_windows_ordered=True,
        adjacent_guard_balances_stable=True,guard_retained_fill_events=0,
        accepted_declared_windows_cover_guard_envelope=True,journals_rechecked_unchanged=True,
        admission_reserved=False,source_attested=False,source_coverage_verified=False,
        activity_coverage_verified=False,fee_coverage_complete=False,
        fee_execution_links_verified=False,balance_instants_verified=False,
        observation_windows_verified_quiet=False,holds_reconciled=False,
        broker_account_reconciled=False,spendable_funds_verified=False,ledger_applied=False,
        ready_for_routing=False,broker_connected=False,live_orders_enabled=False,
        money_movement_enabled=False,automatic_retry=False,automatic_continuation=False,
        release_approved=False,
        limitations='Four supplied manifests are distinct, ordered and locally balance-stable around the accepted inner comparison. Accepted declared creation windows cover the outer envelope and no retained fill appears in either guard band. This is not provider/source attestation, received-activity coverage, a quiet or atomic balance instant, fee/hold reconciliation, spendable-funds proof, routing permission or release approval. Missing, late, corrected, fee-only or activity outside retained history remains possible.')


def fixture_report():
    """Synthetic disposable acceptance/reconciliation evidence; no external IO."""
    import tempfile
    from pathlib import Path
    from nexus.paper_activity_acceptance_evidence import ACCOUNT, _scope
    from nexus.paper_observation_review import _fixture_observation

    with tempfile.TemporaryDirectory(prefix='nexus-reconciliation-acceptance-') as directory:
        root = Path(directory)
        pacing, activity, acceptance, scope = _scope(root)
        acceptance.append(scope, admission=lambda:(pacing.inspect()['head_sha256'],
            activity.inspect()['head_sha256']))
        head = activity.inspect()['head_sha256']
        epoch = 1792195200  # 2026-10-17T00:00:00Z: inside the retained requested window.
        pre_opening = _fixture_observation(ACCOUNT, '1000', '0', epoch+98, epoch+99)
        opening = _fixture_observation(ACCOUNT, '1000', '0', epoch+100, epoch+101)
        closing = _fixture_observation(ACCOUNT, '1000', '0', epoch+102, epoch+103)
        post_closing = _fixture_observation(ACCOUNT, '1000', '0', epoch+104, epoch+105)
        paths = dict(expected_account_id=ACCOUNT, pacing_path=root/'pacing.sqlite3',
            activity_store_path=root/'activity.sqlite3',
            acceptance_store_path=root/'acceptance.sqlite3',
            expected_activity_head=head, opening_observation=opening,
            closing_observation=closing)
        result = review(**paths)
        before = tuple((path.name, path.read_bytes()) for path in sorted(root.iterdir()))
        repeated = review(**paths)
        guarded_paths=dict(paths,pre_opening_observation=pre_opening,
            post_closing_observation=post_closing)
        guarded=review_with_stability_guards(**guarded_paths)
        guarded_repeated=review_with_stability_guards(**guarded_paths)
        after = tuple((path.name, path.read_bytes()) for path in sorted(root.iterdir()))
        unbound_root = root/'unbound'; unbound_root.mkdir()
        _, unbound_activity, _, _ = _scope(unbound_root)
        unbound_rejected = False
        try:
            review(expected_account_id=ACCOUNT,
                pacing_path=unbound_root/'pacing.sqlite3',
                activity_store_path=unbound_root/'activity.sqlite3',
                acceptance_store_path=unbound_root/'acceptance.sqlite3',
                expected_activity_head=unbound_activity.inspect()['head_sha256'],
                opening_observation=opening, closing_observation=closing)
        except ValueError as error:
            unbound_rejected = str(error) == 'PAPER_RECONCILIATION_ACCEPTANCE_ACCEPTED_HISTORY_REQUIRED'
        continuation_root = root/'continuation'; continuation_root.mkdir()
        partial_pacing, partial_activity, partial_acceptance, partial_scope = _scope(
            continuation_root, full_page=True)
        partial_acceptance.append(partial_scope, admission=lambda:(
            partial_pacing.inspect()['head_sha256'],partial_activity.inspect()['head_sha256']))
        continuation_rejected = False
        try:
            review(expected_account_id=ACCOUNT,
                pacing_path=continuation_root/'pacing.sqlite3',
                activity_store_path=continuation_root/'activity.sqlite3',
                acceptance_store_path=continuation_root/'acceptance.sqlite3',
                expected_activity_head=partial_activity.inspect()['head_sha256'],
                opening_observation=opening, closing_observation=closing)
        except ValueError as error:
            continuation_rejected = str(error) == 'PAPER_RECONCILIATION_ACCEPTANCE_ACCEPTED_HISTORY_REQUIRED'
        outside_rejected = False
        try:
            review(**dict(paths,opening_observation=_fixture_observation(ACCOUNT,'1000','0',1800000000,1800000001),
                closing_observation=_fixture_observation(ACCOUNT,'1000','0',1800000002,1800000003)))
        except ValueError as error:
            outside_rejected = str(error) == 'PAPER_RECONCILIATION_ACCEPTANCE_DECLARED_WINDOWS_DO_NOT_COVER_OBSERVATION_ENVELOPE'
        changed_guard_rejected = False
        try:
            review_with_stability_guards(**dict(guarded_paths,
                pre_opening_observation=_fixture_observation(ACCOUNT,'999','0',epoch+98,epoch+99)))
        except ValueError as error:
            changed_guard_rejected = str(error) == 'PAPER_RECONCILIATION_ACCEPTANCE_GUARD_BALANCE_CHANGED'
        unordered_guard_rejected = False
        try:
            review_with_stability_guards(**dict(guarded_paths,
                pre_opening_observation=_fixture_observation(ACCOUNT,'1000','0',epoch+99,epoch+100)))
        except ValueError as error:
            unordered_guard_rejected = str(error) == 'PAPER_RECONCILIATION_ACCEPTANCE_GUARD_WINDOWS_NOT_ORDERED'
        outer_guard_coverage_rejected = False
        try:
            review_with_stability_guards(**dict(guarded_paths,
                pre_opening_observation=_fixture_observation(ACCOUNT,'1000','0',epoch-2,epoch-1)))
        except ValueError as error:
            outer_guard_coverage_rejected = str(error) == 'PAPER_RECONCILIATION_ACCEPTANCE_DECLARED_WINDOWS_DO_NOT_COVER_OBSERVATION_ENVELOPE'
        exact_bound=(result['activity_head_sha256']==head and
            result['opening_manifest_sha256']==opening['manifest_sha256'] and
            result['closing_manifest_sha256']==closing['manifest_sha256'])
        replayed=(result['complete_accepted_history_replayed'] is True and
            result['journals_rechecked_unchanged'] is True and result['accepted_transitions']==1)
        complete=(result['legacy_baseline_absent'] is True and
            result['complete_pacing_sessions_accepted'] is True and
            result['accepted_pacing_operations_typed'] is True and
            result['accepted_pacing_result_receipts_bound'] is True and
            result['accepted_pacing_request_targets_bound'] is True and
            result['complete_retained_batches_accepted'] is True and
            result['complete_collection_observations_accepted'] is True)
        cursor_complete=result['all_collection_cursor_sequences_complete'] is True
        arithmetic=result['observation_review']['arithmetic_evaluated'] is True
        covered=result['declared_collection_windows_cover_observation_envelope'] is True
        guard_bound=(guarded['guard_manifests_distinct'] is True and
            guarded['guard_windows_ordered'] is True and
            guarded['adjacent_guard_balances_stable'] is True and
            guarded['guard_retained_fill_events']==0 and
            guarded['accepted_declared_windows_cover_guard_envelope'] is True and
            guarded['journals_rechecked_unchanged'] is True and
            guarded['balance_instants_verified'] is False and
            guarded['observation_windows_verified_quiet'] is False)
        passed=(result == repeated and guarded == guarded_repeated and before == after and
            unbound_rejected and continuation_rejected and outside_rejected and covered and
            changed_guard_rejected and unordered_guard_rejected and outer_guard_coverage_rejected and
            exact_bound and replayed and complete and cursor_complete and arithmetic and guard_bound)
        return dict(passed=passed, repeat_review_matches=result == repeated,
            stability_guard_repeat_matches=guarded == guarded_repeated,
            journals_unchanged=before == after, unbound_history_rejected=unbound_rejected,
            continuation_required_history_rejected=continuation_rejected,
            out_of_window_observations_rejected=outside_rejected,
            declared_windows_cover_observation_envelope=covered,
            stability_guard_bound=guard_bound,
            changed_guard_balance_rejected=changed_guard_rejected,
            unordered_guard_windows_rejected=unordered_guard_rejected,
            outer_guard_window_coverage_rejected=outer_guard_coverage_rejected,
            guard_retained_fill_events_absent=guarded['guard_retained_fill_events']==0,
            guard_balance_instants_verified=False,guard_observation_windows_verified_quiet=False,
            requested_window_boundaries_verified=False,
            exact_activity_head_bound=exact_bound, complete_accepted_history_replayed=replayed,
            legacy_baseline_absent=result['legacy_baseline_absent'],
            complete_pacing_sessions_accepted=result['complete_pacing_sessions_accepted'],
            accepted_pacing_operations_typed=result['accepted_pacing_operations_typed'],
            accepted_pacing_result_receipts_bound=result['accepted_pacing_result_receipts_bound'],
            accepted_pacing_request_targets_bound=result['accepted_pacing_request_targets_bound'],
            complete_retained_batches_accepted=result['complete_retained_batches_accepted'],
            complete_collection_observations_accepted=result['complete_collection_observations_accepted'],
            all_collection_cursor_sequences_complete=cursor_complete,
            observation_arithmetic_evaluated=arithmetic, private_scope_omitted=True,
            source='SYNTHETIC_LOCAL_ACCEPTED_RECONCILIATION_FIXTURE',
            actual_provider_access=False, actual_vault_access=False,
            broker_account_reconciled=False, spendable_funds_verified=False,
            ready_for_routing=False, broker_connected=False, live_orders_enabled=False,
            money_movement_enabled=False, release_approved=False)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'PAPER_RECONCILIATION_ACCEPTANCE_USAGE_ERROR\n')


def main():
    parser = _Parser(description=__doc__)
    for name in ('expected-account','pacing','activity-store','acceptance-store',
                 'activity-head','opening','closing'):
        parser.add_argument('--'+name, required=True)
    for name in ('pre-opening','post-closing'):
        parser.add_argument('--'+name)
    for name in ('opening','closing','pre-opening','post-closing'):
        parser.add_argument('--'+name+'-encrypted', action='store_true',
            help='Decrypt this bounded input in memory after a private prompt; write no plaintext file.')
    args = parser.parse_args()
    try:
        if (bool(args.pre_opening) != bool(args.post_closing) or
            (args.pre_opening_encrypted or args.post_closing_encrypted) and not args.pre_opening):
            _fail('BOTH_STABILITY_GUARDS_REQUIRED')
        inputs=dict(expected_account_id=args.expected_account,
            pacing_path=args.pacing, activity_store_path=args.activity_store,
            acceptance_store_path=args.acceptance_store,
            expected_activity_head=args.activity_head,
            opening_observation=paper_observation_review._load_file(args.opening,
                encrypted=args.opening_encrypted, label='Opening'),
            closing_observation=paper_observation_review._load_file(args.closing,
                encrypted=args.closing_encrypted, label='Closing'))
        if args.pre_opening:
            result=review_with_stability_guards(**inputs,
                pre_opening_observation=paper_observation_review._load_file(args.pre_opening,
                    encrypted=args.pre_opening_encrypted,label='Pre-opening guard'),
                post_closing_observation=paper_observation_review._load_file(args.post_closing,
                    encrypted=args.post_closing_encrypted,label='Post-closing guard'))
        else:
            result=review(**inputs)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, EOFError,
            KeyboardInterrupt, MemoryError, RecursionError, getpass.GetPassWarning):
        print('PAPER_RECONCILIATION_ACCEPTANCE_FAILED')
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
