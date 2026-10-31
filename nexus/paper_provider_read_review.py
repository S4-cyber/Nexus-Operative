"""Read-only consistency review for provider and account paper-read histories.

This composes already-retained local journals.  It never opens a credential
vault, calls a provider, repairs history, reserves admission or grants broker,
routing, funding, live-trading or release authority.
"""
from collections import Counter
import os
from pathlib import Path
import re

from nexus.alpaca_paper_contract import sha
from nexus.paper_provider_read_budget import (
    MAX_DATABASE_BYTES as PROVIDER_DATABASE_BYTES, PaperProviderReadBudget)
from nexus.paper_read_pacing import (
    LEGACY_OPERATION, MAX_DATABASE_BYTES as ACCOUNT_DATABASE_BYTES,
    OPERATIONS, OPERATION_PATTERNS, PaperReadPacing)
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints, capture_file_identities, check_path_set,
    require_file_fingerprints, require_file_identities)

MAX_ACCOUNTS=6
ZERO='0'*64
PROFILE='nexus.paper-provider-read-review-receipt.v1'
FLAGS=('provider_quota_attested','distributed_ownership_verified','source_attested',
       'source_coverage_verified','clock_domains_verified','broker_account_reconciled',
       'spendable_funds_verified','ready_for_routing','ledger_applied',
       'live_orders_enabled','money_movement_enabled','automatic_retry',
       'automatic_continuation','release_approved')


def _fail(code):raise ValueError('PAPER_PROVIDER_READ_REVIEW_'+code) from None


def _digest(value,code='DIGEST_INVALID'):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}',value) is None:_fail(code)
    return value


def _inputs(provider_budget,gates,expected_account_heads):
    if type(provider_budget) is not PaperProviderReadBudget:_fail('PROVIDER_BUDGET_REQUIRED')
    if type(gates) not in (list,tuple) or not 1<=len(gates)<=MAX_ACCOUNTS:_fail('ACCOUNTS_INVALID')
    if type(expected_account_heads) is not dict:_fail('ACCOUNT_HEADS_INVALID')
    seen=set();checked=[]
    for gate in gates:
        if type(gate) is not PaperReadPacing or gate.account_digest in seen:_fail('ACCOUNTS_INVALID')
        seen.add(gate.account_digest);checked.append(gate)
    if set(expected_account_heads)!=seen:_fail('ACCOUNT_HEADS_INVALID')
    heads={account:_digest(value,'ACCOUNT_HEADS_INVALID') for account,value in expected_account_heads.items()}
    check_path_set((provider_budget.path,*(gate.path for gate in checked)),'PAPER_PROVIDER_READ_REVIEW_')
    return tuple(checked),heads


def _store_binding(provider_budget,gates):
    return (provider_budget.path,provider_budget.provider_scope,
        provider_budget.provider_digest,
        tuple((gate.path,gate.account_digest) for gate in gates))


def _file_scope(paths,file_limits):
    present=[];present_limits=[];absent=[]
    for path,limit in zip(paths,file_limits):
        try:Path(path).lstat()
        except FileNotFoundError:absent.append(path)
        except (OSError,RuntimeError,TypeError):_fail('FILE_IDENTITY_UNAVAILABLE')
        else:present.append(path);present_limits.append(limit)
    return tuple(present),tuple(present_limits),tuple(absent)


def _require_absent(paths):
    for path in paths:
        try:Path(path).lstat()
        except FileNotFoundError:continue
        except (OSError,RuntimeError,TypeError):_fail('FILE_IDENTITY_CHANGED')
        _fail('FILE_IDENTITY_CHANGED')


def _require_bindings(provider_budget,gates,store_binding,paths,absent_paths,
                      file_binding,file_limits,file_fingerprints):
    if (type(provider_budget) is not PaperProviderReadBudget or
            any(type(gate) is not PaperReadPacing for gate in gates) or
            _store_binding(provider_budget,gates)!=store_binding):
        _fail('STORE_BINDING_CHANGED')
    _require_absent(absent_paths)
    require_file_identities(paths,file_binding,'PAPER_PROVIDER_READ_REVIEW_')
    require_file_fingerprints(paths,file_limits,file_fingerprints,
        'PAPER_PROVIDER_READ_REVIEW_')


def _stable_reader(provider_budget,gates,store_binding,paths,absent_paths,file_binding,
                   file_limits,file_fingerprints):
    def read(reader):
        try:value=reader()
        except BaseException:
            _require_bindings(provider_budget,gates,store_binding,paths,absent_paths,
                file_binding,file_limits,file_fingerprints)
            raise
        _require_bindings(provider_budget,gates,store_binding,paths,absent_paths,
            file_binding,file_limits,file_fingerprints)
        return value
    return read


def _account_rows(gates,reasons,stable_read):
    rows=[]
    for gate in gates:
        for record in stable_read(gate.operation_history):
            operation=record['operation'];outcome=record['outcome']
            if operation==LEGACY_OPERATION:
                reasons.add('LEGACY_ACCOUNT_HISTORY');continue
            if outcome in ('PENDING_UNKNOWN','INTERRUPTED_UNKNOWN') or record['after_head_sha256'] is None:
                reasons.add('INCOMPLETE_ACCOUNT_HISTORY');continue
            if not record['result_receipt_bound']:
                reasons.add('UNRECEIPTED_ACCOUNT_HISTORY')
            rows.append((gate.account_digest,record['before_head_sha256'],record['after_head_sha256'],
                operation,outcome,len(record['request_kinds'])))
    return rows


def _provider_rows(history,reasons):
    rows=[]
    for record in history:
        if record['outcome']=='INTERRUPTED_UNKNOWN' or record['account_after_head_sha256'] is None:
            reasons.add('INCOMPLETE_PROVIDER_HISTORY');continue
        rows.append((record['account_sha256'],record['account_before_head_sha256'],
            record['account_after_head_sha256'],record['operation'],record['outcome'],record['actual_gets']))
    return rows


def review(provider_budget,gates,*,expected_provider_head,expected_account_heads):
    """Compare every completed provider reservation with exact account history."""
    expected_provider_head=_digest(expected_provider_head,'PROVIDER_HEAD_INVALID')
    gates,expected_account_heads=_inputs(provider_budget,gates,expected_account_heads)
    paths=(provider_budget.path,*(gate.path for gate in gates))
    store_binding=_store_binding(provider_budget,gates)
    file_limits=(PROVIDER_DATABASE_BYTES,
        *((ACCOUNT_DATABASE_BYTES,)*len(gates)))
    paths,file_limits,absent_paths=_file_scope(paths,file_limits)
    file_binding=capture_file_identities(paths,'PAPER_PROVIDER_READ_REVIEW_')
    file_fingerprints=capture_file_fingerprints(paths,file_limits,
        'PAPER_PROVIDER_READ_REVIEW_')
    _require_bindings(provider_budget,gates,store_binding,paths,absent_paths,file_binding,
        file_limits,file_fingerprints)
    stable_read=_stable_reader(provider_budget,gates,store_binding,paths,absent_paths,
        file_binding,file_limits,file_fingerprints)
    provider_before=stable_read(provider_budget.inspect)
    if provider_before['head_sha256']!=expected_provider_head:_fail('PROVIDER_HEAD_CHANGED')
    account_before={gate.account_digest:stable_read(gate.inspect) for gate in gates}
    if any(account_before[key]['head_sha256']!=expected_account_heads[key] for key in account_before):
        _fail('ACCOUNT_HEAD_CHANGED')
    reasons=set()
    if provider_before['pending_token'] is not None:reasons.add('PENDING_PROVIDER_HISTORY')
    if provider_before['review_required']:reasons.add('PROVIDER_REVIEW_BLOCK')
    if any(state['pending_token'] is not None for state in account_before.values()):
        reasons.add('PENDING_ACCOUNT_HISTORY')
    if any(state['review_required'] for state in account_before.values()):
        reasons.add('ACCOUNT_REVIEW_BLOCK')
    provider_history=stable_read(provider_budget.history)
    account_rows=_account_rows(gates,reasons,stable_read);provider_rows=_provider_rows(provider_history,reasons)
    if Counter(account_rows)!=Counter(provider_rows):reasons.add('PROVIDER_ACCOUNT_SESSION_MISMATCH')
    purposes={account:set() for account in expected_account_heads}
    for account,_,_,operation,_,_ in provider_rows:
        if account in purposes:purposes[account].add(operation)
    every_account_complete=all(set(OPERATIONS)<=account_purposes
        for account_purposes in purposes.values())
    if not every_account_complete:reasons.add('FIVE_PURPOSE_HISTORY_INCOMPLETE')
    provider_after=stable_read(provider_budget.inspect)
    account_after={gate.account_digest:stable_read(gate.inspect) for gate in gates}
    if provider_after!=provider_before:_fail('PROVIDER_HEAD_CHANGED')
    if account_after!=account_before:_fail('ACCOUNT_HEAD_CHANGED')
    manifest=[dict(account_sha256=key,head_sha256=expected_account_heads[key]) for key in sorted(expected_account_heads)]
    consistent=not reasons and len(provider_rows)==len(account_rows)>0
    return dict(mode='READ_ONLY_LOCAL_PROVIDER_PAPER_READ_REVIEW',status='REVIEW_REQUIRED',read_only=True,
        provider_sha256=provider_budget.provider_digest,provider_head_sha256=expected_provider_head,
        account_manifest_sha256=sha(manifest),account_journals=len(gates),provider_sessions_replayed=len(provider_history),
        account_sessions_replayed=len(account_rows),matched_sessions=len(provider_rows) if consistent else 0,
        operation_counts={operation:sum(row[3]==operation for row in provider_rows) for operation in OPERATIONS},
        all_five_purposes_observed=every_account_complete,local_provider_account_history_consistent=consistent,
        review_reasons=sorted(reasons),admission_reserved=False,
        journal_files_rechecked_unchanged=True,
        semantic_reader_failures_rechecked=True,**{flag:False for flag in FLAGS},
        limitations='Exact-head replay of explicitly supplied local journals only. Matching digests do not attest provider identity, quotas, another path/credential/host, source coverage, clocks, fees, holds, funds, routing or release. No reservation, retry, repair or authority is granted.')


def retain_review(store_path,provider_budget,gates,*,expected_provider_head,expected_account_heads,
                  expected_store_head=ZERO,precommit_check=lambda:True):
    """Retain a content-free digest receipt for one complete local review."""
    from nexus.paper_provider_read_review_store import PaperProviderReadReviewStore
    gates,expected_account_heads=_inputs(provider_budget,gates,expected_account_heads)
    paths=(provider_budget.path,*(gate.path for gate in gates))
    store_binding=_store_binding(provider_budget,gates)
    file_limits=(PROVIDER_DATABASE_BYTES,
        *((ACCOUNT_DATABASE_BYTES,)*len(gates)))
    paths,file_limits,absent_paths=_file_scope(paths,file_limits)
    file_binding=capture_file_identities(paths,'PAPER_PROVIDER_READ_REVIEW_')
    file_fingerprints=capture_file_fingerprints(paths,file_limits,
        'PAPER_PROVIDER_READ_REVIEW_')
    def require_sources():
        _require_bindings(provider_budget,gates,store_binding,paths,absent_paths,
            file_binding,file_limits,file_fingerprints)
    def scope():
        require_sources()
        try:
            result=review(provider_budget,gates,
                expected_provider_head=expected_provider_head,
                expected_account_heads=expected_account_heads)
        except BaseException:
            require_sources();raise
        require_sources()
        if not result['local_provider_account_history_consistent']:_fail('INCOMPLETE')
        return dict(profile=PROFILE,provider_sha256=result['provider_sha256'],
            provider_head_sha256=result['provider_head_sha256'],account_manifest_sha256=result['account_manifest_sha256'],
            review_sha256=sha(result),account_journals=result['account_journals'],
            sessions_replayed=result['provider_sessions_replayed'],matched_sessions=result['matched_sessions'],
            operation_counts=result['operation_counts'],all_five_purposes_observed=result['all_five_purposes_observed'],
            local_provider_account_history_consistent=result['local_provider_account_history_consistent'])
    def checked_precommit():
        require_sources()
        try:allowed=precommit_check()
        except BaseException:
            require_sources();raise
        require_sources()
        return allowed
    value=scope()
    store=PaperProviderReadReviewStore(store_path,provider_budget,gates)
    try:
        retained=store.append(value,expected_head=expected_store_head,
            admission=lambda:sha(scope()),precommit_check=checked_precommit)
    except BaseException:
        require_sources();raise
    require_sources()
    return retained


def fixture_report():
    """Disposable exact provider/account history composition; no provider I/O."""
    import tempfile
    from nexus import paper_read_review
    with tempfile.TemporaryDirectory(prefix='nexus-provider-read-review-') as directory:
        root=Path(directory)
        def build_pair(path):
            gate,_=paper_read_review._fixture(path/'account')
            budget=PaperProviderReadBudget(path/'provider.sqlite3')
            for index,record in enumerate(gate.operation_history()):
                with budget.ownership() as owner:
                    token=budget.begin(account_id=gate.account,
                        account_before_head_sha256=record['before_head_sha256'],
                        operation=record['operation'],admission=lambda index=index:1000+index*61,
                        ownership=owner)
                    budget.finish(token,
                        account_after_head_sha256=record['after_head_sha256'],
                        actual_gets=len(record['request_kinds']),
                        admission=lambda index=index:1001+index*61,
                        ownership=owner,status_code=200)
            return gate,budget
        def block_pair(gate,budget):
            operation='ACCOUNT_INVENTORY';pattern=list(OPERATION_PATTERNS[operation][0])
            blocked_at=max(gate.inspect()['next_allowed_at'],budget.inspect()['next_allowed_at'])
            with budget.ownership() as provider_owner:
                provider_token=budget.begin(account_id=gate.account,
                    account_before_head_sha256=gate.inspect()['head_sha256'],operation=operation,
                    admission=lambda:blocked_at,ownership=provider_owner)
                with gate.ownership() as account_owner:
                    account_token=gate.begin(admission=lambda:blocked_at,
                        ownership=account_owner,operation=operation)
                    gate.finish(account_token,admission=lambda:blocked_at+1,status_code=401,
                        ownership=account_owner,receipt=dict(operation=operation,
                            request_kinds=pattern[:1],request_sha256=[sha(dict(request=0))],
                            response_sha256=[]))
                budget.finish(provider_token,
                    account_after_head_sha256=gate.inspect()['head_sha256'],actual_gets=1,
                    admission=lambda:blocked_at+1,ownership=provider_owner,status_code=401)
            return blocked_at
        def current(gate,budget):
            return review(budget,[gate],expected_provider_head=budget.inspect()['head_sha256'],
                expected_account_heads={gate.account_digest:gate.inspect()['head_sha256']})
        def current_many(gates,budget):
            return review(budget,gates,
                expected_provider_head=budget.inspect()['head_sha256'],
                expected_account_heads={gate.account_digest:
                    gate.inspect()['head_sha256'] for gate in gates})
        def add_inventory_account(path,budget,account):
            gate=PaperReadPacing(path/'pacing.sqlite3',account)
            operation='ACCOUNT_INVENTORY'
            request_kinds=list(OPERATION_PATTERNS[operation][0])
            completed_at=max(2000,budget.inspect()['next_allowed_at'])
            receipt=dict(operation=operation,request_kinds=request_kinds,
                request_sha256=[sha(dict(request=index))
                    for index in range(len(request_kinds))],
                response_sha256=[sha(dict(response=index))
                    for index in range(len(request_kinds))])
            with gate.ownership() as owner:
                token=gate.begin(admission=lambda:completed_at,ownership=owner,
                    operation=operation)
                gate.finish(token,admission=lambda:completed_at+1,status_code=200,
                    ownership=owner,receipt=receipt)
            record=gate.operation_history()[0]
            with budget.ownership() as owner:
                token=budget.begin(account_id=account,
                    account_before_head_sha256=record['before_head_sha256'],
                    operation=operation,admission=lambda:completed_at,
                    ownership=owner)
                budget.finish(token,
                    account_after_head_sha256=record['after_head_sha256'],
                    actual_gets=len(request_kinds),admission=lambda:completed_at+1,
                    ownership=owner,status_code=200)
            return gate
        def add_complete_account(path,budget,account,epoch):
            gate,_=paper_read_review._fixture(path,provider_budget=budget,
                account_id=account,epoch=epoch)
            return gate
        def leave_pending(gate,budget,mode):
            pending_at=max(gate.inspect()['next_allowed_at'],
                budget.inspect()['next_allowed_at'])
            if mode in ('provider','both'):
                with budget.ownership() as owner:
                    budget.begin(account_id=gate.account,
                        account_before_head_sha256=gate.inspect()['head_sha256'],
                        operation='ACCOUNT_INVENTORY',admission=lambda:pending_at,
                        ownership=owner)
            if mode in ('account','both'):
                with gate.ownership() as owner:
                    gate.begin(admission=lambda:pending_at,ownership=owner,
                        operation='ACCOUNT_INVENTORY')
        def recover_provider(gate,budget,blocked_at):
            state=budget.inspect()
            with budget.ownership() as owner:
                budget.recover_review_block(expected_head=state['head_sha256'],
                    expected_account_id=gate.account,confirm_block_reviewed=True,
                    confirm_uncertainty_preserved=True,admission=lambda:blocked_at+2,
                    ownership=owner)
        def recover_account(gate,blocked_at):
            state=gate.inspect()
            with gate.ownership() as owner:
                gate.recover_review_block(expected_head=state['head_sha256'],
                    expected_account_id=gate.account,confirm_block_reviewed=True,
                    confirm_uncertainty_preserved=True,admission=lambda:blocked_at+3,
                    ownership=owner)
        gate,budget=build_pair(root/'provider-first')
        options=dict(expected_provider_head=budget.inspect()['head_sha256'],
            expected_account_heads={gate.account_digest:gate.inspect()['head_sha256']})
        files=lambda:{str(path.relative_to(root)):path.read_bytes()
            for path in root.rglob('*') if path.is_file()}
        before=files();result=review(budget,[gate],**options)
        changed_head_denied=False
        try:review(budget,[gate],expected_provider_head='f'*64,
            expected_account_heads=options['expected_account_heads'])
        except ValueError as error:
            changed_head_denied=str(error)=='PAPER_PROVIDER_READ_REVIEW_PROVIDER_HEAD_CHANGED'
        source_same=files()==before
        blocked_at=block_pair(gate,budget);blocked=current(gate,budget)
        recover_provider(gate,budget,blocked_at);split=current(gate,budget)
        recover_account(gate,blocked_at);recovered=current(gate,budget)
        inverse_gate,inverse_budget=build_pair(root/'account-first')
        inverse_blocked_at=block_pair(inverse_gate,inverse_budget)
        inverse_blocked=current(inverse_gate,inverse_budget)
        recover_account(inverse_gate,inverse_blocked_at)
        inverse_split=current(inverse_gate,inverse_budget)
        recover_provider(inverse_gate,inverse_budget,inverse_blocked_at)
        inverse_recovered=current(inverse_gate,inverse_budget)
        pending_expectations={
            'provider':['PENDING_PROVIDER_HISTORY'],
            'account':['INCOMPLETE_ACCOUNT_HISTORY','PENDING_ACCOUNT_HISTORY'],
            'both':['INCOMPLETE_ACCOUNT_HISTORY','PENDING_ACCOUNT_HISTORY',
                'PENDING_PROVIDER_HISTORY']}
        pending_results={}
        pending_unchanged={}
        for mode in pending_expectations:
            pending_gate,pending_budget=build_pair(root/f'{mode}-pending')
            leave_pending(pending_gate,pending_budget,mode)
            pending_before=files()
            pending_results[mode]=current(pending_gate,pending_budget)
            pending_unchanged[mode]=files()==pending_before
        multi_gate,multi_budget=build_pair(root/'multi-account-pending')
        other_gate=add_complete_account(root/'multi-account-pending'/'other',
            multi_budget,'21234567-89ab-cdef-0123-456789abcdef',1792370000)
        leave_pending(multi_gate,multi_budget,'account')
        multi_before=files()
        multi_result=current_many([multi_gate,other_gate],multi_budget)
        multi_unchanged=files()==multi_before
        incomplete_gate,incomplete_budget=build_pair(
            root/'multi-account-incomplete')
        incomplete_peer=add_inventory_account(
            root/'multi-account-incomplete'/'other',incomplete_budget,
            '41234567-89ab-cdef-0123-456789abcdef')
        incomplete_before=files()
        incomplete_result=current_many(
            [incomplete_gate,incomplete_peer],incomplete_budget)
        incomplete_unchanged=files()==incomplete_before
        guard_gate,guard_budget=build_pair(root/'multi-account-reader-guard')
        guard_peer=add_complete_account(root/'multi-account-reader-guard'/'other',
            guard_budget,'31234567-89ab-cdef-0123-456789abcdef',1792380000)
        guard_paths=(guard_budget.path,guard_gate.path,guard_peer.path)
        guard_bytes={path:path.read_bytes() for path in guard_paths}
        guard_reader=guard_gate.operation_history
        def mutate_peer_then_fail():
            guard_reader()
            state=os.stat(guard_peer.path)
            os.utime(guard_peer.path,
                ns=(state.st_atime_ns,state.st_mtime_ns+1_000_000_000))
            raise RuntimeError('injected cross-account reader failure')
        guard_gate.operation_history=mutate_peer_then_fail
        peer_mutation_denied=False
        try:current_many([guard_gate,guard_peer],guard_budget)
        except ValueError as error:
            peer_mutation_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_FILE_SET_CHANGED')
        guard_bytes_unchanged=(guard_bytes==
            {path:path.read_bytes() for path in guard_paths})
        retained_gate,retained_budget=build_pair(root/'retained-source-guard')
        retained_store=root/'retained-source-guard'/'receipt.sqlite3'
        retained_bytes={path:path.read_bytes() for path in
            (retained_budget.path,retained_gate.path)}
        def mutate_retained_source():
            state=os.stat(retained_gate.path)
            os.utime(retained_gate.path,
                ns=(state.st_atime_ns,state.st_mtime_ns+1_000_000_000))
            return True
        retained_source_mutation_denied=False
        try:retain_review(retained_store,retained_budget,[retained_gate],
            expected_provider_head=retained_budget.inspect()['head_sha256'],
            expected_account_heads={retained_gate.account_digest:
                retained_gate.inspect()['head_sha256']},
            precommit_check=mutate_retained_source)
        except ValueError as error:
            retained_source_mutation_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_FILE_SET_CHANGED')
        retained_source_bytes_unchanged=(retained_bytes==
            {path:path.read_bytes() for path in retained_bytes})
        checks=dict(
            exact_provider_account_history=result['local_provider_account_history_consistent'],
            every_session_matched=result['matched_sessions']==5,
            all_five_purposes_observed=result['all_five_purposes_observed'],
            changed_head_denied=changed_head_denied,
            split_recovery_requires_both_sides=(
                blocked['local_provider_account_history_consistent'] is False and
                blocked['review_reasons']==[
                    'ACCOUNT_REVIEW_BLOCK','PROVIDER_REVIEW_BLOCK'] and
                split['local_provider_account_history_consistent'] is False and
                split['review_reasons']==['ACCOUNT_REVIEW_BLOCK'] and
                recovered['local_provider_account_history_consistent'] is True and
                recovered['review_reasons']==[] and recovered['matched_sessions']==6),
            split_recovery_order_independent=(
                inverse_blocked['local_provider_account_history_consistent'] is False and
                inverse_blocked['review_reasons']==[
                    'ACCOUNT_REVIEW_BLOCK','PROVIDER_REVIEW_BLOCK'] and
                inverse_split['local_provider_account_history_consistent'] is False and
                inverse_split['review_reasons']==['PROVIDER_REVIEW_BLOCK'] and
                inverse_recovered['local_provider_account_history_consistent'] is True and
                inverse_recovered['review_reasons']==[] and
                inverse_recovered['matched_sessions']==6),
            pending_histories_never_promote=all(
                pending_results[mode]['local_provider_account_history_consistent'] is False and
                pending_results[mode]['matched_sessions']==0 and
                pending_results[mode]['review_reasons']==expected and
                pending_unchanged[mode]
                for mode,expected in pending_expectations.items()),
            pending_account_cannot_hide_behind_complete_peer=(
                multi_result['local_provider_account_history_consistent'] is False and
                multi_result['review_reasons']==[
                    'INCOMPLETE_ACCOUNT_HISTORY','PENDING_ACCOUNT_HISTORY'] and
                multi_result['account_journals']==2 and
                multi_result['provider_sessions_replayed']==10 and
                multi_result['account_sessions_replayed']==10 and
                multi_result['operation_counts']['ACCOUNT_INVENTORY']==2 and
                multi_result['matched_sessions']==0 and
                other_gate.inspect()['pending_token'] is None and
                multi_unchanged),
            incomplete_account_cannot_hide_behind_complete_peer=(
                incomplete_result['local_provider_account_history_consistent']
                    is False and
                incomplete_result['review_reasons']==[
                    'FIVE_PURPOSE_HISTORY_INCOMPLETE'] and
                incomplete_result['account_journals']==2 and
                incomplete_result['provider_sessions_replayed']==6 and
                incomplete_result['account_sessions_replayed']==6 and
                incomplete_result['all_five_purposes_observed'] is False and
                incomplete_result['matched_sessions']==0 and
                incomplete_unchanged),
            multi_account_reader_failure_rechecks_peer=(
                peer_mutation_denied and guard_bytes_unchanged),
            retained_receipt_source_mutation_denied=(
                retained_source_mutation_denied and
                retained_source_bytes_unchanged and
                not retained_store.exists()),
            journals_unchanged=source_same,
            journal_file_fingerprints_rechecked=result['journal_files_rechecked_unchanged'],
            semantic_reader_failures_rechecked=result['semantic_reader_failures_rechecked'],
            output_never_authoritative=all(result[flag] is False for flag in FLAGS))
        return dict(source='TEMPORARY_LOCAL_PROVIDER_READ_HISTORY_FIXTURE',
            passed=all(checks.values()),**checks,**{flag:False for flag in FLAGS})
