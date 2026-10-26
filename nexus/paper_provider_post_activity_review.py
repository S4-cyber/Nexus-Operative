"""Read-only exact-head composition of provider POST and accepted activity.

The caller selects one shared-local provider POST budget plus one to seven
account-scoped request/activity histories.  This module writes nothing, reads
no credentials, performs no provider IO and grants no routing authority.
"""
import hashlib
from pathlib import Path
import re

from nexus import paper_provider_post_review as posts
from nexus import paper_request_activity_acceptance as activity
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_provider_post_budget import PaperProviderPostBudget
from nexus.sqlite_journal_guard import check_path_set


MAX_ACCOUNT_BINDINGS = posts.MAX_REQUEST_JOURNALS
BINDING_KEYS = {
    'account_id', 'journal', 'expected_request_head', 'pacing_path',
    'activity_store_path', 'acceptance_store_path', 'expected_activity_head',
}
FLAGS = tuple(dict.fromkeys((*posts.FLAGS, *activity.FLAGS,
    'activity_coverage_verified', 'fee_execution_links_verified',
    'holds_reconciled', 'ledger_applied', 'broker_connected',
    'automatic_recovery')))


def _fail(code):
    raise ValueError('PAPER_PROVIDER_POST_ACTIVITY_' + code) from None


def _digest(value, code):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        _fail(code)
    return value


def _client_digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _frozen_path(value, code):
    try:
        return Path(value)
    except (TypeError, ValueError, OSError, AttributeError):
        _fail(code)


def _check_related_paths(paths):
    """Apply the existing bounded relationship guard to every selected pair."""
    if type(paths) not in (list, tuple) or not 5 <= len(paths) <= 29:
        _fail('PATH_SET_INVALID')
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            check_path_set((path, other), 'PAPER_PROVIDER_POST_ACTIVITY_')


def _store_binding(post_budget, bindings):
    return (post_budget.path, post_budget.provider_scope,
        post_budget.provider_digest,
        tuple((item['journal'].path, item['journal'].account_digest)
            for item in bindings))


def _require_store_binding(post_budget, bindings, expected):
    if (type(post_budget) is not PaperProviderPostBudget or
            any(type(item['journal']) is not BrokerRequestJournal
                for item in bindings) or
            _store_binding(post_budget, bindings) != expected):
        _fail('STORE_BINDING_CHANGED')


def review(post_budget, *, expected_post_head, account_bindings):
    """Join exact POST outcomes to fully replayed accepted activity histories.

    Results expose aggregate counts and fixed reasons only.  Account, client
    order and broker order identifiers remain inside caller-selected private
    journals and are not copied into the returned diagnostic.
    """
    if type(post_budget) is not PaperProviderPostBudget:
        _fail('POST_BUDGET_REQUIRED')
    expected_post_head = _digest(expected_post_head, 'POST_HEAD_INVALID')
    if (type(account_bindings) not in (list, tuple) or
            not 1 <= len(account_bindings) <= MAX_ACCOUNT_BINDINGS):
        _fail('ACCOUNT_BINDINGS_INVALID')

    bindings = []
    paths = [post_budget.path]
    accounts = set()
    for item in account_bindings:
        if type(item) is not dict or set(item) != BINDING_KEYS:
            _fail('ACCOUNT_BINDING_INVALID')
        journal = item['journal']
        if type(journal) is not BrokerRequestJournal:
            _fail('REQUEST_JOURNAL_REQUIRED')
        account_id = item['account_id']
        try:
            account_check = BrokerRequestJournal(journal.path, account_id)
        except (TypeError, ValueError, AttributeError):
            _fail('ACCOUNT_SCOPE_INVALID')
        if account_check.account_digest != journal.account_digest:
            _fail('ACCOUNT_SCOPE_DISAGREEMENT')
        if journal.account_digest in accounts:
            _fail('DUPLICATE_ACCOUNT_BINDING')
        accounts.add(journal.account_digest)
        current = dict(item)
        current['expected_request_head'] = _digest(
            current['expected_request_head'], 'REQUEST_HEAD_INVALID')
        current['expected_activity_head'] = _digest(
            current['expected_activity_head'], 'ACTIVITY_HEAD_INVALID')
        current['pacing_path'] = _frozen_path(
            current['pacing_path'], 'PACING_PATH_INVALID')
        current['activity_store_path'] = _frozen_path(
            current['activity_store_path'], 'ACTIVITY_STORE_PATH_INVALID')
        current['acceptance_store_path'] = _frozen_path(
            current['acceptance_store_path'], 'ACCEPTANCE_STORE_PATH_INVALID')
        current['_account_digest'] = journal.account_digest
        bindings.append(current)
        paths.extend((journal.path, current['pacing_path'],
            current['activity_store_path'], current['acceptance_store_path']))
    store_binding = _store_binding(post_budget, bindings)
    _check_related_paths(paths)
    _require_store_binding(post_budget, bindings, store_binding)

    first_post = post_budget.inspect()
    _require_store_binding(post_budget, bindings, store_binding)
    if first_post['events'] == 0 or first_post['head_sha256'] != expected_post_head:
        _fail('POST_HEAD_CHANGED')
    attempts = post_budget.history()
    _require_store_binding(post_budget, bindings, store_binding)
    recoveries = post_budget.recovery_history()
    _require_store_binding(post_budget, bindings, store_binding)
    if (len(attempts) != first_post['completed_attempts'] or
            len(recoveries) != first_post['recoveries']):
        _fail('POST_HISTORY_CHANGED')

    post_result = posts.review(post_budget, expected_post_head=expected_post_head,
        request_bindings=[dict(journal=item['journal'],
            expected_head=item['expected_request_head']) for item in bindings])
    _require_store_binding(post_budget, bindings, store_binding)
    reasons = []
    if not post_result['complete_local_history_consistent']:
        reasons.append('POST_HISTORY_INCOMPLETE')

    rows_by_account = {}
    accepted_heads = {}
    activity_components_valid = True
    untracked_activity_orders = 0
    unexpected_activity_blockers = 0
    for item in bindings:
        component = activity.review(expected_account_id=item['account_id'],
            request_journal_path=item['journal'].path,
            pacing_path=item['pacing_path'],
            activity_store_path=item['activity_store_path'],
            acceptance_store_path=item['acceptance_store_path'],
            expected_request_head=item['expected_request_head'],
            expected_activity_head=item['expected_activity_head'])
        _require_store_binding(post_budget, bindings, store_binding)
        nested = component.get('request_activity_review')
        valid = (component.get('read_only') is True and
            component.get('status') == 'REVIEW_REQUIRED' and
            component.get('complete_accepted_history_replayed') is True and
            component.get('journals_rechecked_unchanged') is True and
            component.get('account_sha256') == item['_account_digest'] and
            component.get('request_head_sha256') == item['expected_request_head'] and
            component.get('activity_head_sha256') == item['expected_activity_head'] and
            type(nested) is dict and nested.get('read_only') is True and
            all(component.get(flag) is False for flag in activity.FLAGS))
        if not valid:
            activity_components_valid = False
            continue
        rows = nested.get('request_reviews')
        if type(rows) is not list:
            activity_components_valid = False
            continue
        mapped = {}
        for row in rows:
            if type(row) is not dict or type(row.get('client_order_id')) is not str:
                activity_components_valid = False
                continue
            digest = _client_digest(row['client_order_id'])
            if digest in mapped:
                activity_components_valid = False
            mapped[digest] = row
        rows_by_account[item['_account_digest']] = mapped
        accepted_heads[item['_account_digest']] = (
            component.get('pacing_head_sha256'),
            component.get('acceptance_head_sha256'))
        untracked = nested.get('untracked_activity_order_ids')
        if type(untracked) is not list:
            activity_components_valid = False
        else:
            untracked_activity_orders += len(untracked)
        blockers = nested.get('agreement_blockers')
        if type(blockers) is not list:
            activity_components_valid = False
        else:
            unexpected_activity_blockers += len(
                set(blockers) - {'REQUEST_OBSERVATION_MISSING'})
    if not activity_components_valid:
        reasons.append('ACTIVITY_COMPONENT_INVALID')
    if untracked_activity_orders:
        reasons.append('UNTRACKED_ACTIVITY_ORDER')
    if unexpected_activity_blockers:
        reasons.append('ACTIVITY_HISTORY_DISAGREEMENT')

    recovered_attempts = {row['blocked_attempt_head_sha256'] for row in recoveries}
    matched = set()
    exact_activity_bindings = 0
    exact_not_sent_bindings = 0
    unresolved_unknown_attempts = 0
    attempt_activity_disagreements = 0
    for attempt in attempts:
        account = attempt['account_sha256']
        client = attempt['client_order_sha256']
        row = rows_by_account.get(account, {}).get(client)
        key = (account, client)
        if row is not None:
            matched.add(key)
        same_terms = (row is not None and
            row.get('payload_sha256') == attempt['payload_sha256'])
        if attempt['outcome'] == 'NOT_SENT':
            valid = (same_terms and row.get('broker_order_id') is None and
                row.get('explicit_fill_count') == 0 and
                row.get('explicit_fill_quantity') == '0' and
                row.get('local_terms_agree') is False and
                row.get('reasons') == ['REQUEST_OBSERVATION_MISSING'])
            if valid:
                exact_not_sent_bindings += 1
            else:
                attempt_activity_disagreements += 1
        elif attempt['outcome'] == 'RESPONSE_OBSERVED':
            valid = (same_terms and row.get('broker_order_id') is not None and
                row.get('local_terms_agree') is True and row.get('reasons') == [])
            if valid:
                exact_activity_bindings += 1
            else:
                attempt_activity_disagreements += 1
        elif attempt['outcome'] == 'OUTCOME_UNKNOWN':
            if attempt['after_head_sha256'] not in recovered_attempts:
                unresolved_unknown_attempts += 1
            else:
                valid = (same_terms and row.get('broker_order_id') is not None and
                    row.get('local_terms_agree') is True and row.get('reasons') == [])
                if valid:
                    exact_activity_bindings += 1
                else:
                    attempt_activity_disagreements += 1
        else:
            attempt_activity_disagreements += 1
    all_rows = {(account, client) for account, rows in rows_by_account.items()
        for client in rows}
    unbound_request_activity_rows = len(all_rows - matched)
    if unresolved_unknown_attempts:
        reasons.append('UNRESOLVED_POST_OUTCOME')
    if attempt_activity_disagreements:
        reasons.append('POST_ACTIVITY_BINDING_DISAGREEMENT')
    if unbound_request_activity_rows:
        reasons.append('UNBOUND_REQUEST_ACTIVITY_ROW')

    for item in bindings:
        current_request = item['journal'].recovery()
        _require_store_binding(post_budget, bindings, store_binding)
        current_activity = activity.acceptance_status(
            expected_account_id=item['account_id'],
            pacing_path=item['pacing_path'],
            activity_store_path=item['activity_store_path'],
            acceptance_store_path=item['acceptance_store_path'])
        _require_store_binding(post_budget, bindings, store_binding)
        expected_heads = accepted_heads.get(item['_account_digest'])
        if (current_request['head_sha256'] != item['expected_request_head'] or
                not activity._complete(current_activity) or
                current_activity['retention_head_sha256'] !=
                    item['expected_activity_head'] or
                expected_heads is None or
                (current_activity['pacing_head_sha256'],
                 current_activity['acceptance_head_sha256']) != expected_heads):
            _fail('JOURNALS_CHANGED_DURING_REVIEW')

    final_post = post_budget.inspect()
    _require_store_binding(post_budget, bindings, store_binding)
    final_attempts = post_budget.history()
    _require_store_binding(post_budget, bindings, store_binding)
    final_recoveries = post_budget.recovery_history()
    _require_store_binding(post_budget, bindings, store_binding)
    if (final_post != first_post or final_attempts != attempts or
            final_recoveries != recoveries):
        _fail('POST_HISTORY_CHANGED')
    complete = (not reasons and
        exact_activity_bindings + exact_not_sent_bindings == len(attempts))
    return dict(mode='READ_ONLY_PAPER_PROVIDER_POST_ACTIVITY_REVIEW',
        status='COMPLETE_LOCAL_POST_ACTIVITY_CONSISTENT' if complete else 'REVIEW_REQUIRED',
        read_only=True, complete_local_post_activity_consistent=complete,
        post_attempts=len(attempts), account_histories=len(bindings),
        exact_activity_bindings=exact_activity_bindings,
        exact_not_sent_bindings=exact_not_sent_bindings,
        unresolved_unknown_attempts=unresolved_unknown_attempts,
        attempt_activity_disagreements=attempt_activity_disagreements,
        unbound_request_activity_rows=unbound_request_activity_rows,
        untracked_activity_orders=untracked_activity_orders,
        unexpected_activity_blockers=unexpected_activity_blockers,
        reasons=list(dict.fromkeys(reasons)), uncertainty_preserved=True,
        **{flag: False for flag in FLAGS},
        limitations='Exact-head local composition over explicitly selected cooperating journals only. Observed/recovered POST attempts must agree with complete accepted local activity history; NOT_SENT attempts must retain no observation or fills. This does not attest provider source or activity/fee completeness, make journals atomic, verify fee links, holds, balances or spendable funds, coordinate another credential/process/host, authorize transport, apply a ledger or approve release.')


def _case(root, *, resolve_unknown, fee_mode=None):
    """Disposable accepted activity plus reconstructed exact POST prefixes."""
    import sqlite3
    from nexus.alpaca_paper_contract import preview
    from nexus.paper_provider_post_recovery import recover

    options, _ = activity._fixture(root, submission_admission=1000,
        fee_mode=fee_mode)
    journal = BrokerRequestJournal(options['request_journal_path'],
        options['expected_account_id'])
    db = sqlite3.connect(journal.path)
    try:
        events = db.execute(
            'SELECT kind,digest FROM events ORDER BY seq').fetchall()
    finally:
        db.close()
    before = next(digest for kind, digest in events if kind == 'PREPARE')
    marker = next(digest for kind, digest in events
        if kind == 'SUBMISSION_UNCERTAIN')
    observed = next(digest for kind, digest in events if kind == 'OBSERVE')
    selected = journal.lookup_request('nx_accept_fixture')
    budget = PaperProviderPostBudget(root / 'post.sqlite3')
    with budget.ownership() as owner:
        token = budget.begin(account_id=options['expected_account_id'],
            request_before_head_sha256=before,
            payload_sha256=selected['plan']['payload_sha256'],
            client_order_id='nx_accept_fixture', admission=lambda: 1000,
            ownership=owner, expected_head='0' * 64)
        head = budget.finish(token, outcome='OUTCOME_UNKNOWN',
            request_after_head_sha256=marker, actual_posts=1,
            admission=lambda: 1001, ownership=owner)
    if resolve_unknown:
        head = recover(budget.path, journal.path,
            expected_account_id=options['expected_account_id'],
            client_order_id='nx_accept_fixture',
            expected_post_budget_head=head, expected_request_head=observed,
            confirm_original_id_resolved=True, confirm_no_resubmission=True,
            admission=lambda: 1002)['head_sha256']
        asset = dict(symbol='BTC/USD', **{'class': 'crypto'}, status='active',
            tradable=True, fractionable=True, min_order_size='.5',
            min_trade_increment='.5', price_increment='.5')
        plan = preview('nx_not_sent', 'BTC-USD', 'BUY', '2', '100', asset,
            metadata_at=2000, evaluated_at=2001)
        journal.prepare(plan)
        pending = journal.lookup_request('nx_not_sent')
        with budget.ownership() as owner:
            token = budget.begin(account_id=options['expected_account_id'],
                request_before_head_sha256=pending['head_sha256'],
                payload_sha256=plan['payload_sha256'],
                client_order_id='nx_not_sent', admission=lambda: 87402,
                ownership=owner, expected_head=head)
            journal.begin_submission_checked('nx_not_sent',
                expected_head=pending['head_sha256'],
                expected_payload_sha256=plan['payload_sha256'],
                admission=lambda: 87402)
            head = budget.finish(token, outcome='NOT_SENT',
                request_after_head_sha256=journal.recovery()['head_sha256'],
                actual_posts=0, admission=lambda: 87403, ownership=owner)
    options['expected_request_head'] = journal.recovery()['head_sha256']
    binding = dict(account_id=options['expected_account_id'], journal=journal,
        expected_request_head=options['expected_request_head'],
        pacing_path=options['pacing_path'],
        activity_store_path=options['activity_store_path'],
        acceptance_store_path=options['acceptance_store_path'],
        expected_activity_head=options['expected_activity_head'])
    return budget, head, binding


def fixture_report():
    """Synthetic positive/negative controls; never provider or vault access."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix='nexus-post-activity-') as directory:
        root = Path(directory)
        budget, head, binding = _case(root / 'resolved', resolve_unknown=True)
        before = tuple((path.name, path.read_bytes())
            for path in sorted((root / 'resolved').iterdir()))
        result = review(budget, expected_post_head=head,
            account_bindings=[binding])
        unchanged = before == tuple((path.name, path.read_bytes())
            for path in sorted((root / 'resolved').iterdir()))
        unresolved_budget, unresolved_head, unresolved_binding = _case(
            root / 'unresolved', resolve_unknown=False)
        unresolved = review(unresolved_budget,
            expected_post_head=unresolved_head,
            account_bindings=[unresolved_binding])
        return dict(source='SYNTHETIC_LOCAL_PROVIDER_POST_ACTIVITY_FIXTURE',
            passed=(result['complete_local_post_activity_consistent'] and
                result['exact_activity_bindings'] == 1 and
                result['exact_not_sent_bindings'] == 1 and unchanged and
                unresolved['reasons'] == ['UNRESOLVED_POST_OUTCOME']),
            recovered_unknown_bound_to_accepted_activity=
                result['exact_activity_bindings'] == 1,
            not_sent_bound_without_activity=result['exact_not_sent_bindings'] == 1,
            unresolved_unknown_requires_review=
                unresolved['reasons'] == ['UNRESOLVED_POST_OUTCOME'],
            journals_unchanged=unchanged, output_never_authoritative=
                all(result[flag] is False for flag in FLAGS),
            **{flag: False for flag in FLAGS})
