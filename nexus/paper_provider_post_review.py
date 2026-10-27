"""Read-only composition of provider POST and request-journal history.

This review joins cooperating local journals selected by the caller.  It does
not contact a provider, discover other clients, attest a broker quota, repair
history, admit IO, or authorize routing.
"""
import hashlib
import re

from nexus.broker_request_journal import (
    ADMISSION_PROFILE,
    ADMISSION_SCOPE,
    BrokerRequestJournal,
    MAX_DATABASE_BYTES as REQUEST_DATABASE_BYTES,
    ZERO,
)
from nexus.paper_provider_post_budget import (
    MAX_DATABASE_BYTES as POST_DATABASE_BYTES,
    PaperProviderPostBudget,
)
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints, capture_file_identities, check_path_set,
    require_file_fingerprints, require_file_identities)

MAX_REQUEST_JOURNALS = 7
FLAGS = (
    'actual_provider_access', 'actual_vault_access', 'source_attested',
    'broker_wide_quota_verified', 'distributed_ownership_verified',
    'broker_account_reconciled', 'spendable_funds_verified',
    'ready_for_routing', 'live_orders_enabled', 'money_movement_enabled',
    'release_approved',
)


def _fail(code):
    raise ValueError('PAPER_PROVIDER_POST_REVIEW_' + code) from None


def _digest(value, code):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        _fail(code)
    return value


def _request_by_digest(rows, client_digest):
    matches = [row for row in rows
        if hashlib.sha256(row['client_order_id'].encode()).hexdigest() == client_digest]
    return matches[0] if len(matches) == 1 else None


def _store_binding(post_budget, bindings):
    """Capture every mutable store identity used by this composition."""
    return (post_budget.path, post_budget.provider_scope,
        post_budget.provider_digest,
        tuple((journal.path, journal.account_digest)
            for journal, _ in bindings))


def _require_store_binding(post_budget, bindings, expected):
    if (type(post_budget) is not PaperProviderPostBudget or
            any(type(journal) is not BrokerRequestJournal
                for journal, _ in bindings) or
            _store_binding(post_budget, bindings) != expected):
        _fail('STORE_BINDING_CHANGED')


def _require_bindings(post_budget, bindings, store_binding, paths,
                      file_binding, file_limits, file_fingerprints):
    _require_store_binding(post_budget, bindings, store_binding)
    require_file_identities(paths, file_binding,
        'PAPER_PROVIDER_POST_REVIEW_')
    require_file_fingerprints(paths, file_limits, file_fingerprints,
        'PAPER_PROVIDER_POST_REVIEW_')


def review(post_budget, *, expected_post_head, request_bindings):
    """Replay and compare every retained checked POST attempt.

    ``request_bindings`` is a bounded list of exact dictionaries containing a
    ``BrokerRequestJournal`` and its expected current ``head_sha256``.  Results
    contain counts and fixed reason codes only, never account or order IDs.
    """
    if type(post_budget) is not PaperProviderPostBudget:
        _fail('POST_BUDGET_REQUIRED')
    expected_post_head = _digest(expected_post_head, 'POST_HEAD_INVALID')
    if (type(request_bindings) not in (list, tuple) or
            not 1 <= len(request_bindings) <= MAX_REQUEST_JOURNALS):
        _fail('REQUEST_BINDINGS_INVALID')
    bindings = []
    for item in request_bindings:
        if type(item) is not dict or set(item) != {'journal', 'expected_head'}:
            _fail('REQUEST_BINDING_INVALID')
        journal = item['journal']
        if type(journal) is not BrokerRequestJournal:
            _fail('REQUEST_JOURNAL_REQUIRED')
        bindings.append((journal, _digest(item['expected_head'], 'REQUEST_HEAD_INVALID')))
    store_binding = _store_binding(post_budget, bindings)
    accounts = [journal.account_digest for journal, _ in bindings]
    if len(set(accounts)) != len(accounts):
        _fail('DUPLICATE_ACCOUNT_BINDING')
    paths = check_path_set(
        (post_budget.path, *(journal.path for journal, _ in bindings)),
        'PAPER_PROVIDER_POST_REVIEW_')
    file_binding = capture_file_identities(paths,
        'PAPER_PROVIDER_POST_REVIEW_')
    file_limits = (POST_DATABASE_BYTES,
        *((REQUEST_DATABASE_BYTES,) * len(bindings)))
    file_fingerprints = capture_file_fingerprints(paths, file_limits,
        'PAPER_PROVIDER_POST_REVIEW_')
    _require_bindings(post_budget, bindings, store_binding, paths,
        file_binding, file_limits, file_fingerprints)

    first_post = post_budget.inspect()
    _require_bindings(post_budget, bindings, store_binding, paths,
        file_binding, file_limits, file_fingerprints)
    if (first_post['events'] == 0 or first_post['head_sha256'] != expected_post_head):
        _fail('POST_HEAD_CHANGED')
    attempts = post_budget.history()
    _require_bindings(post_budget, bindings, store_binding, paths,
        file_binding, file_limits, file_fingerprints)
    if len(attempts) != first_post['completed_attempts']:
        _fail('POST_HISTORY_CHANGED')
    recoveries = post_budget.recovery_history()
    _require_bindings(post_budget, bindings, store_binding, paths,
        file_binding, file_limits, file_fingerprints)
    if len(recoveries) != first_post['recoveries']:
        _fail('POST_HISTORY_CHANGED')

    by_account = {account: binding
        for account, binding in zip(accounts, bindings)}
    needed = {account: set() for account in by_account}
    missing_accounts = 0
    for attempt in attempts:
        account = attempt['account_sha256']
        if account not in needed:
            missing_accounts += 1
            continue
        needed[account].add(attempt['request_before_head_sha256'])
        if attempt['request_after_head_sha256'] is not None:
            needed[account].add(attempt['request_after_head_sha256'])
    for recovery in recoveries:
        account = recovery['account_sha256']
        if account in needed:
            needed[account].add(recovery['request_resolution_head_sha256'])

    prefixes = {}
    current_requests = {}
    for account, (journal, expected) in by_account.items():
        initial = journal.recovery()
        _require_bindings(post_budget, bindings, store_binding, paths,
            file_binding, file_limits, file_fingerprints)
        if initial['head_sha256'] != expected or expected == ZERO:
            _fail('REQUEST_HEAD_CHANGED')
        sample = journal.observations_at_heads(tuple(sorted(needed[account])))
        _require_bindings(post_budget, bindings, store_binding, paths,
            file_binding, file_limits, file_fingerprints)
        if sample['current_head_sha256'] != expected:
            _fail('REQUEST_HEAD_CHANGED')
        prefixes[account] = sample['observations_by_head']
        current_requests[account] = initial['requests']
        final = journal.recovery()
        _require_bindings(post_budget, bindings, store_binding, paths,
            file_binding, file_limits, file_fingerprints)
        if final['head_sha256'] != expected or final['requests'] != initial['requests']:
            _fail('REQUEST_HISTORY_CHANGED')

    exact = 0
    outcome_bound = 0
    outcome_disagreements = 0
    binding_disagreements = 0
    interrupted = 0
    unbound = missing_accounts
    duplicate_attempt_bindings = 0
    bound_marker_keys = []
    for attempt in attempts:
        account = attempt['account_sha256']
        if account not in prefixes:
            continue
        if attempt['request_after_head_sha256'] is None:
            interrupted += 1
            continue
        before_rows = prefixes[account].get(attempt['request_before_head_sha256'])
        after_rows = prefixes[account].get(attempt['request_after_head_sha256'])
        before = None if before_rows is None else _request_by_digest(
            before_rows, attempt['client_order_sha256'])
        after = None if after_rows is None else _request_by_digest(
            after_rows, attempt['client_order_sha256'])
        marker = None if after is None else after['submission_admission']
        expected_marker = None if before is None else dict(
            profile=ADMISSION_PROFILE, clock_scope=ADMISSION_SCOPE,
            evaluated_at=marker.get('evaluated_at') if type(marker) is dict else None,
            expected_head_sha256=attempt['request_before_head_sha256'],
            payload_sha256=attempt['payload_sha256'])
        binding_valid = (before is not None and after is not None and
            before['payload_sha256'] == after['payload_sha256'] == attempt['payload_sha256'] and
            before['submission_attempt_marked'] is False and
            before['submission_admission'] is None and
            after['submission_attempt_marked'] is True and
            marker == expected_marker)
        if binding_valid:
            key = (account, attempt['client_order_sha256'],
                attempt['payload_sha256'], attempt['request_before_head_sha256'])
            bound_marker_keys.append(key)
            exact += 1
            snapshot = after['snapshot']
            outcome_valid = (attempt['outcome'] == 'OUTCOME_UNKNOWN' or
                attempt['outcome'] == 'NOT_SENT' and snapshot is None or
                attempt['outcome'] == 'RESPONSE_OBSERVED' and type(snapshot) is dict)
            if outcome_valid:
                outcome_bound += 1
            else:
                outcome_disagreements += 1
                unbound += 1
        else:
            binding_disagreements += 1
            unbound += 1
    duplicate_attempt_bindings = len(bound_marker_keys) - len(set(bound_marker_keys))

    attempt_by_head = {attempt['after_head_sha256']: attempt for attempt in attempts}
    exact_recoveries = 0
    recovery_disagreements = 0
    for recovery in recoveries:
        account = recovery['account_sha256']
        rows = None if account not in prefixes else prefixes[account].get(
            recovery['request_resolution_head_sha256'])
        request = None if rows is None else _request_by_digest(
            rows, recovery['client_order_sha256'])
        attempt = attempt_by_head.get(recovery['blocked_attempt_head_sha256'])
        snapshot = None if request is None else request['snapshot']
        valid = (attempt is not None and request is not None and
            attempt['outcome'] == 'OUTCOME_UNKNOWN' and
            attempt['account_sha256'] == account and
            attempt['payload_sha256'] == recovery['payload_sha256'] and
            attempt['client_order_sha256'] == recovery['client_order_sha256'] and
            request['payload_sha256'] == recovery['payload_sha256'] and
            request['submission_attempt_marked'] is True and
            request['recovery_state'] == 'OBSERVED_TERMINAL_RECONCILIATION_REQUIRED' and
            request['lookup_by_original_client_id_required'] is False and
            type(snapshot) is dict and snapshot.get('terminal') is True)
        if valid:
            exact_recoveries += 1
        else:
            recovery_disagreements += 1

    retained_markers = []
    legacy_markers = 0
    for account, rows in current_requests.items():
        for row in rows:
            if not row['submission_attempt_marked']:
                continue
            marker = row['submission_admission']
            if marker is None:
                legacy_markers += 1
                continue
            retained_markers.append((account,
                hashlib.sha256(row['client_order_id'].encode()).hexdigest(),
                row['payload_sha256'], marker['expected_head_sha256']))
    unbound_markers = len(set(retained_markers) - set(bound_marker_keys))
    duplicate_markers = len(retained_markers) - len(set(retained_markers))

    final_post = post_budget.inspect()
    _require_bindings(post_budget, bindings, store_binding, paths,
        file_binding, file_limits, file_fingerprints)
    if final_post['head_sha256'] != expected_post_head or final_post != first_post:
        _fail('POST_HISTORY_CHANGED')
    reasons = []
    if first_post['pending_token'] is not None:
        reasons.append('POST_ATTEMPT_PENDING')
    if missing_accounts: reasons.append('POST_ACCOUNT_JOURNAL_MISSING')
    if binding_disagreements: reasons.append('POST_ATTEMPT_BINDING_DISAGREEMENT')
    if outcome_disagreements: reasons.append('POST_OUTCOME_REQUEST_HISTORY_DISAGREEMENT')
    if recovery_disagreements:
        reasons.append('POST_RECOVERY_REQUEST_HISTORY_DISAGREEMENT')
    if interrupted: reasons.append('INTERRUPTED_POST_ATTEMPT_UNBOUND')
    if unbound_markers: reasons.append('CHECKED_REQUEST_MARKER_UNBOUND')
    if legacy_markers: reasons.append('LEGACY_REQUEST_MARKER_UNBOUND')
    if duplicate_attempt_bindings or duplicate_markers:
        reasons.append('DUPLICATE_ATTEMPT_BINDING')
    complete = (not reasons and exact == outcome_bound == len(attempts) ==
        len(retained_markers) and exact_recoveries == len(recoveries))
    return dict(mode='READ_ONLY_PAPER_PROVIDER_POST_HISTORY_REVIEW', read_only=True,
        status='COMPLETE_LOCAL_HISTORY_CONSISTENT' if complete else 'REVIEW_REQUIRED',
        complete_local_history_consistent=complete,
        post_attempts=len(attempts), exact_attempt_bindings=exact,
        outcome_bound_attempts=outcome_bound,
        outcome_request_history_disagreements=outcome_disagreements,
        post_recoveries=len(recoveries), exact_recovery_bindings=exact_recoveries,
        recovery_request_history_disagreements=recovery_disagreements,
        interrupted_unknown_attempts=interrupted, unbound_post_attempts=unbound,
        retained_checked_request_markers=len(retained_markers),
        unbound_checked_request_markers=unbound_markers,
        legacy_unbound_request_markers=legacy_markers,
        duplicate_attempt_bindings=duplicate_attempt_bindings + duplicate_markers,
        request_journals=len(bindings), reasons=reasons,
        admission_reserved=False, automatic_retry=False, automatic_recovery=False,
        uncertainty_preserved=True, **{flag: False for flag in FLAGS},
        limitations='Exact-head local replay for explicitly selected cooperating journals only. Recovery binding confirms a retained terminal local request prefix, not provider identity or complete reconciliation. It does not bind raw provider bytes, resolve interrupted attempts, discover another path, credential, process, host or machine, attest broker quotas or source, reconcile fills, fees, holds or funds, or grant routing or release authority.')


def fixture_report():
    """Disposable multi-account composition with reciprocal fail-closed cases."""
    import tempfile
    from pathlib import Path
    from nexus import alpaca_paper_contract as contract

    accounts = ('01234567-89ab-cdef-0123-456789abcdef',
        '11234567-89ab-cdef-0123-456789abcdef')
    asset = dict(symbol='BTC/USD', **{'class':'crypto'}, status='active',
        tradable=True, fractionable=True, min_order_size='.5',
        min_trade_increment='.5', price_increment='.5')
    with tempfile.TemporaryDirectory(prefix='nexus-post-review-') as directory:
        root = Path(directory); budget = PaperProviderPostBudget(root/'post.sqlite3')
        journals = []
        head = ZERO
        for index, account in enumerate(accounts):
            identity = f'nx_post_review_{index}'
            plan = contract.preview(identity, 'BTC-USD', 'BUY', '2', '100', asset,
                metadata_at=1000 + index, evaluated_at=1001 + index)
            journal = BrokerRequestJournal(root/f'request-{index}.sqlite3', account)
            journal.prepare(plan); selected = journal.lookup_request(identity)
            with budget.ownership() as owner:
                token = budget.begin(account_id=account,
                    request_before_head_sha256=selected['head_sha256'],
                    payload_sha256=plan['payload_sha256'], client_order_id=identity,
                    admission=lambda value=1000 + index * 61: value,
                    ownership=owner, expected_head=head)
                journal.begin_submission_checked(identity,
                    expected_head=selected['head_sha256'],
                    expected_payload_sha256=plan['payload_sha256'],
                    admission=lambda value=1000 + index * 61: value)
                outcome = 'NOT_SENT'
                if index:
                    outcome = 'RESPONSE_OBSERVED'
                    journal.observe(identity, dict(id=account, client_order_id=identity,
                        symbol='BTC/USD', side='buy', type='limit', time_in_force='ioc',
                        asset_class='crypto', qty='2', limit_price='100', status='new',
                        filled_qty='0', filled_avg_price=None,
                        updated_at='2026-10-23T17:00:00Z'))
                head = budget.finish(token, outcome=outcome,
                    request_after_head_sha256=journal.recovery()['head_sha256'],
                    actual_posts=index, admission=lambda value=1001 + index * 61: value,
                    ownership=owner)
            journals.append(journal)
        bindings = [dict(journal=journal,
            expected_head=journal.recovery()['head_sha256']) for journal in journals]
        result = review(budget, expected_post_head=head, request_bindings=bindings)
        changed_head_denied = False
        try:
            review(budget, expected_post_head='f'*64, request_bindings=bindings)
        except ValueError:
            changed_head_denied = True
        omitted_account_requires_review = not review(budget, expected_post_head=head,
            request_bindings=bindings[:1])['complete_local_history_consistent']
        mismatch_journal = BrokerRequestJournal(root/'mismatch.sqlite3',
            '21234567-89ab-cdef-0123-456789abcdef')
        mismatch_plan = contract.preview('nx_post_review_mismatch', 'BTC-USD', 'BUY',
            '2', '100', asset, metadata_at=2000, evaluated_at=2001)
        mismatch_journal.prepare(mismatch_plan)
        mismatch_selected = mismatch_journal.lookup_request('nx_post_review_mismatch')
        mismatch_budget = PaperProviderPostBudget(root/'mismatch-post.sqlite3')
        with mismatch_budget.ownership() as owner:
            mismatch_token = mismatch_budget.begin(
                account_id='21234567-89ab-cdef-0123-456789abcdef',
                request_before_head_sha256=mismatch_selected['head_sha256'],
                payload_sha256=mismatch_plan['payload_sha256'],
                client_order_id='nx_post_review_mismatch', admission=lambda: 2000,
                ownership=owner, expected_head=ZERO)
            mismatch_journal.begin_submission_checked('nx_post_review_mismatch',
                expected_head=mismatch_selected['head_sha256'],
                expected_payload_sha256=mismatch_plan['payload_sha256'],
                admission=lambda: 2000)
            mismatch_head = mismatch_budget.finish(mismatch_token,
                outcome='RESPONSE_OBSERVED',
                request_after_head_sha256=mismatch_journal.recovery()['head_sha256'],
                actual_posts=1, admission=lambda: 2001, ownership=owner)
        mismatch = review(mismatch_budget, expected_post_head=mismatch_head,
            request_bindings=[dict(journal=mismatch_journal,
                expected_head=mismatch_journal.recovery()['head_sha256'])])

        recovered_account = '31234567-89ab-cdef-0123-456789abcdef'
        recovered_identity = 'nx_post_review_recovered'
        recovered_plan = contract.preview(recovered_identity, 'BTC-USD', 'BUY',
            '2', '100', asset, metadata_at=3000, evaluated_at=3001)
        recovered_journal = BrokerRequestJournal(root/'recovered.sqlite3',
            recovered_account)
        recovered_budget = PaperProviderPostBudget(root/'recovered-post.sqlite3')
        recovered_journal.prepare(recovered_plan)
        recovered_selected = recovered_journal.lookup_request(recovered_identity)
        with recovered_budget.ownership() as owner:
            recovered_token = recovered_budget.begin(account_id=recovered_account,
                request_before_head_sha256=recovered_selected['head_sha256'],
                payload_sha256=recovered_plan['payload_sha256'],
                client_order_id=recovered_identity, admission=lambda: 3000,
                ownership=owner, expected_head=ZERO)
            recovered_journal.begin_submission_checked(recovered_identity,
                expected_head=recovered_selected['head_sha256'],
                expected_payload_sha256=recovered_plan['payload_sha256'],
                admission=lambda: 3000)
            recovered_blocked_head = recovered_budget.finish(recovered_token,
                outcome='OUTCOME_UNKNOWN',
                request_after_head_sha256=recovered_journal.recovery()['head_sha256'],
                actual_posts=1, admission=lambda: 3001, ownership=owner)
        recovered_journal.observe(recovered_identity,
            dict(id=recovered_account, client_order_id=recovered_identity,
                symbol='BTC/USD', side='buy', type='limit', time_in_force='ioc',
                asset_class='crypto', qty='2', limit_price='100', status='filled',
                filled_qty='2', filled_avg_price='99',
                updated_at='2026-10-23T19:00:00Z'))
        resolution_head = recovered_journal.recovery()['head_sha256']
        proof = dict(account_sha256=recovered_journal.account_digest,
            payload_sha256=recovered_plan['payload_sha256'],
            client_order_sha256=hashlib.sha256(recovered_identity.encode()).hexdigest(),
            request_resolution_head_sha256=resolution_head,
            recovery_state='OBSERVED_TERMINAL_RECONCILIATION_REQUIRED',
            lookup_by_original_client_id_required=False)
        with recovered_budget.ownership() as owner:
            recovered = recovered_budget.recover_resolved_unknown(
                expected_head=recovered_blocked_head,
                expected_account_id=recovered_account,
                expected_payload_sha256=recovered_plan['payload_sha256'],
                expected_client_order_id=recovered_identity,
                expected_request_resolution_head=resolution_head,
                resolution_check=lambda: dict(proof),
                confirm_original_id_resolved=True, confirm_no_resubmission=True,
                admission=lambda: 3002, ownership=owner)
        recovered_review = review(recovered_budget,
            expected_post_head=recovered['head_sha256'],
            request_bindings=[dict(journal=recovered_journal,
                expected_head=resolution_head)])
        checks = dict(exact_multi_account_history=result['complete_local_history_consistent'],
            every_attempt_bound=result['exact_attempt_bindings'] == 2,
            every_outcome_bound=result['outcome_bound_attempts'] == 2,
            exact_terminal_recovery_history=(
                recovered_review['complete_local_history_consistent'] and
                recovered_review['post_recoveries'] == 1 and
                recovered_review['exact_recovery_bindings'] == 1 and
                recovered_review['recovery_request_history_disagreements'] == 0),
            outcome_disagreement_requires_review=(
                'POST_OUTCOME_REQUEST_HISTORY_DISAGREEMENT' in mismatch['reasons']),
            changed_head_denied=changed_head_denied,
            omitted_account_requires_review=omitted_account_requires_review,
            output_never_authoritative=all(result[flag] is False for flag in FLAGS))
        return dict(source='TEMPORARY_LOCAL_PROVIDER_POST_HISTORY_REVIEW_FIXTURE',
            passed=all(checks.values()), **checks, **{flag: False for flag in FLAGS})
