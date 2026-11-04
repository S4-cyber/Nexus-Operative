"""Read-only consistency review for provider and account paper-read histories.

This composes already-retained local journals.  It never opens a credential
vault, calls a provider, repairs history, reserves admission or grants broker,
routing, funding, live-trading or release authority.
"""
from collections import Counter
import os
from pathlib import Path
import re
import sqlite3
import stat

from nexus.alpaca_paper_contract import sha
from nexus.paper_provider_read_budget import (
    MAX_DATABASE_BYTES as PROVIDER_DATABASE_BYTES, PaperProviderReadBudget)
from nexus.paper_read_pacing import (
    LEGACY_OPERATION, MAX_DATABASE_BYTES as ACCOUNT_DATABASE_BYTES,
    OPERATIONS, OPERATION_PATTERNS, PaperReadPacing)
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints, capture_file_identities, check_path_set,
    guard_file_set_finalization,require_file_fingerprints,
    require_file_identities)

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
        except BaseException as error:
            if not isinstance(error,Exception):raise
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
    from nexus.paper_provider_read_review_store import (
        MAX_DATABASE_BYTES as STORE_DATABASE_BYTES,
        SCHEMA as STORE_SCHEMA,PaperProviderReadReviewStore)
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
        except BaseException as error:
            if not isinstance(error,Exception):raise
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
        except BaseException as error:
            if not isinstance(error,Exception):raise
            require_sources();raise
        require_sources()
        return allowed
    value=scope();receipt=sha(value)
    store=PaperProviderReadReviewStore(store_path,provider_budget,gates)
    write_error=None
    try:
        store.append(value,expected_head=expected_store_head,
            admission=lambda:sha(scope()),precommit_check=checked_precommit)
    except BaseException as error:
        if not isinstance(error,Exception):raise
        write_error=error
    require_sources()
    if type(write_error) is ValueError:raise write_error
    if not store.path.exists():_fail('DURABLE_STATE_CHANGED')
    try:
        store_identity=capture_file_identities((store.path,),
            'PAPER_PROVIDER_READ_REVIEW_STORE_')
        store_fingerprint=capture_file_fingerprints((store.path,),
            (STORE_DATABASE_BYTES,),'PAPER_PROVIDER_READ_REVIEW_STORE_')
        with guard_file_set_finalization((store.path,),
                (STORE_DATABASE_BYTES,),store_identity,store_fingerprint,
                'PAPER_PROVIDER_READ_REVIEW_STORE_'):
            first=store.inspect();require_sources()
            require_file_identities((store.path,),store_identity,
                'PAPER_PROVIDER_READ_REVIEW_STORE_')
            require_file_fingerprints((store.path,),(STORE_DATABASE_BYTES,),
                store_fingerprint,'PAPER_PROVIDER_READ_REVIEW_STORE_')
            second=store.inspect();require_sources()
            if first!=second or not first['initialized'] or not first['receipts']:
                _fail('DURABLE_STATE_CHANGED')
            latest=first['receipts'][-1]
            schema_head=sha(dict(seq=0,body=dict(schema=STORE_SCHEMA,
                provider_sha256=provider_budget.provider_digest),previous=ZERO))
            previous=(schema_head if expected_store_head==ZERO else
                expected_store_head)
            expected_after=sha(dict(seq=first['retained_receipts'],
                body=dict(scope=value,receipt_sha256=receipt),
                previous=previous))
            duplicate=first['head_sha256']==expected_store_head
            if (latest['scope']!=value or latest['receipt_sha256']!=receipt or
                    latest['after_head_sha256']!=first['head_sha256'] or
                    not (duplicate or first['head_sha256']==expected_after)):
                _fail('DURABLE_STATE_CHANGED')
            return dict(mode='LOCAL_PROVIDER_READ_REVIEW_RECEIPT',
                head_sha256=first['head_sha256'],receipt_sha256=receipt,
                duplicate=duplicate,read_only=True,ready_for_routing=False,
                release_approved=False)
    except ValueError:
        raise
    except BaseException as error:
        if not isinstance(error,Exception):raise
        require_sources();_fail('DURABLE_STATE_CHANGED')


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
        from nexus.paper_provider_read_review_store import \
            PaperProviderReadReviewStore
        ack_gate,ack_budget=build_pair(root/'retained-ack-replay')
        ack_store=root/'retained-ack-replay'/'receipt.sqlite3'
        original_append=PaperProviderReadReviewStore.append
        lost_acknowledgements=0
        def lose_acknowledgement(store,*args,**kwargs):
            nonlocal lost_acknowledgements
            original_append(store,*args,**kwargs)
            lost_acknowledgements+=1
            raise RuntimeError('fixture private lost receipt acknowledgement')
        PaperProviderReadReviewStore.append=lose_acknowledgement
        try:
            ack_receipt=retain_review(ack_store,ack_budget,[ack_gate],
                expected_provider_head=ack_budget.inspect()['head_sha256'],
                expected_account_heads={ack_gate.account_digest:
                    ack_gate.inspect()['head_sha256']})
        finally:
            PaperProviderReadReviewStore.append=original_append
        ack_replay=PaperProviderReadReviewStore(
            ack_store,ack_budget,[ack_gate]).inspect()
        recovery_gate,recovery_budget=build_pair(
            root/'retained-recovery-process-control')
        recovery_store=(root/'retained-recovery-process-control'/
            'receipt.sqlite3')
        original_inspect=PaperProviderReadReviewStore.inspect
        recovery_committed=False;recovery_interrupted=False
        def lose_recovery_acknowledgement(store,*args,**kwargs):
            nonlocal recovery_committed
            original_append(store,*args,**kwargs)
            recovery_committed=True
            raise RuntimeError('fixture private lost recovery acknowledgement')
        def interrupt_recovery_inspection(store):
            if recovery_committed:raise KeyboardInterrupt
            return original_inspect(store)
        PaperProviderReadReviewStore.append=lose_recovery_acknowledgement
        PaperProviderReadReviewStore.inspect=interrupt_recovery_inspection
        try:
            try:
                retain_review(recovery_store,recovery_budget,[recovery_gate],
                    expected_provider_head=
                        recovery_budget.inspect()['head_sha256'],
                    expected_account_heads={recovery_gate.account_digest:
                        recovery_gate.inspect()['head_sha256']})
            except KeyboardInterrupt:recovery_interrupted=True
        finally:
            PaperProviderReadReviewStore.append=original_append
            PaperProviderReadReviewStore.inspect=original_inspect
        recovery_replay=PaperProviderReadReviewStore(
            recovery_store,recovery_budget,[recovery_gate]).inspect()
        recovery_receipt=retain_review(recovery_store,recovery_budget,
            [recovery_gate],expected_provider_head=
                recovery_budget.inspect()['head_sha256'],
            expected_account_heads={recovery_gate.account_digest:
                recovery_gate.inspect()['head_sha256']},
            expected_store_head=recovery_replay['head_sha256'])
        recovery_final=PaperProviderReadReviewStore(
            recovery_store,recovery_budget,[recovery_gate]).inspect()
        finalization_gate,finalization_budget=build_pair(
            root/'retained-read-finalization-process-control')
        finalization_store=(root/'retained-read-finalization-process-control'/
            'receipt.sqlite3')
        finalization_created=retain_review(finalization_store,
            finalization_budget,[finalization_gate],expected_provider_head=
                finalization_budget.inspect()['head_sha256'],
            expected_account_heads={finalization_gate.account_digest:
                finalization_gate.inspect()['head_sha256']})
        finalization_reader=PaperProviderReadReviewStore(finalization_store,
            finalization_budget,[finalization_gate])
        finalization_bytes=finalization_store.read_bytes()
        finalization_connect=finalization_reader._connect
        finalization_interrupted=False
        def interrupt_read_finalization(write=False):
            observed=finalization_store.stat()
            os.utime(finalization_store,ns=(observed.st_atime_ns,
                observed.st_mtime_ns+1_000_000_000))
            raise KeyboardInterrupt
        finalization_reader._connect=interrupt_read_finalization
        try:
            try:finalization_reader.inspect()
            except KeyboardInterrupt:finalization_interrupted=True
        finally:finalization_reader._connect=finalization_connect
        finalization_replay=PaperProviderReadReviewStore(finalization_store,
            finalization_budget,[finalization_gate]).inspect()
        write_signal_gate,write_signal_budget=build_pair(
            root/'retained-write-finalization-process-control')
        write_signal_store=(root/'retained-write-finalization-process-control'/
            'receipt.sqlite3')
        write_signal_first=retain_review(write_signal_store,
            write_signal_budget,[write_signal_gate],expected_provider_head=
                write_signal_budget.inspect()['head_sha256'],
            expected_account_heads={write_signal_gate.account_digest:
                write_signal_gate.inspect()['head_sha256']})
        write_signal_writer=PaperProviderReadReviewStore(write_signal_store,
            write_signal_budget,[write_signal_gate])
        write_signal_scope=dict(
            write_signal_writer.inspect()['receipts'][0]['scope'],
            review_sha256=sha(dict(
                fixture='provider-write-finalization-process-control')))
        write_signal_receipt=sha(write_signal_scope)
        write_signal_calls=0;write_signal_raised=False
        def interrupt_write_finalization():
            nonlocal write_signal_calls
            write_signal_calls+=1
            if write_signal_calls==3:
                observed=write_signal_store.stat()
                os.utime(write_signal_store,ns=(observed.st_atime_ns,
                    observed.st_mtime_ns+1_000_000_000))
                raise KeyboardInterrupt
            return write_signal_receipt
        try:write_signal_writer.append(write_signal_scope,
            expected_head=write_signal_first['head_sha256'],
            admission=interrupt_write_finalization)
        except KeyboardInterrupt:write_signal_raised=True
        write_signal_replay=write_signal_writer.inspect()
        write_signal_retry=write_signal_writer.append(write_signal_scope,
            expected_head=write_signal_replay['head_sha256'],
            admission=lambda:write_signal_receipt)
        write_signal_final=write_signal_writer.inspect()
        fabricated_gate,fabricated_budget=build_pair(
            root/'fabricated-retained-ack')
        fabricated_store=root/'fabricated-retained-ack'/'receipt.sqlite3'
        fabricated_denied=False
        PaperProviderReadReviewStore.append=lambda *args,**kwargs:dict(
            head_sha256='f'*64,receipt_sha256='e'*64)
        try:
            retain_review(fabricated_store,fabricated_budget,[fabricated_gate],
                expected_provider_head=
                    fabricated_budget.inspect()['head_sha256'],
                expected_account_heads={fabricated_gate.account_digest:
                    fabricated_gate.inspect()['head_sha256']})
        except ValueError as error:
            fabricated_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_DURABLE_STATE_CHANGED')
        finally:
            PaperProviderReadReviewStore.append=original_append
        fresh_gate,fresh_budget=build_pair(root/'fresh-retained-retry')
        fresh_store=root/'fresh-retained-retry'/'receipt.sqlite3'
        fresh_calls=0
        def revoke_fresh_transaction():
            nonlocal fresh_calls
            fresh_calls+=1
            return fresh_calls!=2
        fresh_failure_denied=False
        try:
            retain_review(fresh_store,fresh_budget,[fresh_gate],
                expected_provider_head=fresh_budget.inspect()['head_sha256'],
                expected_account_heads={fresh_gate.account_digest:
                    fresh_gate.inspect()['head_sha256']},
                precommit_check=revoke_fresh_transaction)
        except ValueError as error:
            fresh_failure_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_INPUT_REVOKED')
        fresh_absence_restored=(not fresh_store.exists() and all(
            not Path(str(fresh_store)+suffix).exists() for suffix in
            ('-journal','-wal','-shm')))
        fresh_receipt=retain_review(fresh_store,fresh_budget,[fresh_gate],
            expected_provider_head=fresh_budget.inspect()['head_sha256'],
            expected_account_heads={fresh_gate.account_digest:
                fresh_gate.inspect()['head_sha256']})
        fresh_replay=PaperProviderReadReviewStore(
            fresh_store,fresh_budget,[fresh_gate]).inspect()
        initialization_gate,initialization_budget=build_pair(
            root/'fresh-retained-initialization')
        initialization_store=(root/'fresh-retained-initialization'/
            'receipt.sqlite3')
        from nexus import paper_provider_read_review_store as store_module
        original_connect=store_module.sqlite3.connect
        initialization_target=initialization_store.resolve().as_uri()
        initialization_failures=0
        def fail_initialization_connect(*args,**kwargs):
            nonlocal initialization_failures
            if (str(args[0]).startswith(initialization_target) and
                    initialization_failures==0):
                initialization_failures+=1
                raise RuntimeError('fixture private initialization failure')
            return original_connect(*args,**kwargs)
        initialization_failure_denied=False
        store_module.sqlite3.connect=fail_initialization_connect
        try:
            retain_review(initialization_store,initialization_budget,
                [initialization_gate],expected_provider_head=
                    initialization_budget.inspect()['head_sha256'],
                expected_account_heads={initialization_gate.account_digest:
                    initialization_gate.inspect()['head_sha256']})
        except ValueError as error:
            initialization_failure_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_DURABLE_STATE_CHANGED')
        finally:
            store_module.sqlite3.connect=original_connect
        initialization_absence_restored=(not initialization_store.exists() and
            all(not Path(str(initialization_store)+suffix).exists() for suffix
                in ('-journal','-wal','-shm')))
        initialization_receipt=retain_review(initialization_store,
            initialization_budget,[initialization_gate],
            expected_provider_head=
                initialization_budget.inspect()['head_sha256'],
            expected_account_heads={initialization_gate.account_digest:
                initialization_gate.inspect()['head_sha256']})
        initialization_replay=PaperProviderReadReviewStore(
            initialization_store,initialization_budget,
            [initialization_gate]).inspect()
        continuity_gate,continuity_budget=build_pair(
            root/'fresh-retained-identity-continuity')
        continuity_store=(root/'fresh-retained-identity-continuity'/
            'receipt.sqlite3')
        continuity_target=continuity_store.resolve().as_uri()
        continuity_sql=None;continuity_failures=0
        original_require=store_module.require_file_identities
        class ContinuityConnection:
            def __init__(self,db):self.db=db
            def execute(self,sql,*args,**kwargs):
                nonlocal continuity_sql
                result=self.db.execute(sql,*args,**kwargs)
                continuity_sql=sql
                return result
            def close(self):return self.db.close()
            def __getattr__(self,name):return getattr(self.db,name)
        def continuity_connect(*args,**kwargs):
            db=original_connect(*args,**kwargs)
            if str(args[0]).startswith(continuity_target):
                return ContinuityConnection(db)
            return db
        def continuity_require(paths,expected,prefix):
            nonlocal continuity_failures
            if (continuity_failures==0 and type(continuity_sql) is str and
                    continuity_sql.startswith('PRAGMA synchronous')):
                continuity_failures+=1
                raise ValueError(prefix+'FILE_IDENTITY_CHANGED')
            return original_require(paths,expected,prefix)
        continuity_denied=False
        store_module.sqlite3.connect=continuity_connect
        store_module.require_file_identities=continuity_require
        try:
            PaperProviderReadReviewStore(continuity_store,continuity_budget,
                [continuity_gate])._connect(write=True)
        except ValueError as error:
            continuity_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_FILE_IDENTITY_CHANGED')
        finally:
            store_module.sqlite3.connect=original_connect
            store_module.require_file_identities=original_require
        continuity_absence=(not continuity_store.exists() and all(
            not Path(str(continuity_store)+suffix).exists() for suffix in
            ('-journal','-wal','-shm')))
        continuity_receipt=retain_review(continuity_store,continuity_budget,
            [continuity_gate],expected_provider_head=
                continuity_budget.inspect()['head_sha256'],
            expected_account_heads={continuity_gate.account_digest:
                continuity_gate.inspect()['head_sha256']})
        continuity_replay=PaperProviderReadReviewStore(continuity_store,
            continuity_budget,[continuity_gate]).inspect()
        initialization_identity_continuity_pinned=(
            continuity_failures==1 and continuity_denied and
            continuity_absence and continuity_replay['retained_receipts']==1 and
            continuity_receipt['head_sha256']==continuity_replay['head_sha256'])
        durability_gate,durability_budget=build_pair(
            root/'retained-commit-durability')
        durability_store=(root/'retained-commit-durability'/'receipt.sqlite3')
        original_fsync=store_module.os.fsync;durability_failures=0
        def fail_first_commit_flush(descriptor):
            nonlocal durability_failures
            if durability_failures==0:
                durability_failures+=1
                raise OSError('fixture private commit flush failure')
            return original_fsync(descriptor)
        durability_failure_denied=False
        store_module.os.fsync=fail_first_commit_flush
        try:
            retain_review(durability_store,durability_budget,
                [durability_gate],expected_provider_head=
                    durability_budget.inspect()['head_sha256'],
                expected_account_heads={durability_gate.account_digest:
                    durability_gate.inspect()['head_sha256']})
        except ValueError as error:
            durability_failure_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN')
        finally:
            store_module.os.fsync=original_fsync
        durability_replay=PaperProviderReadReviewStore(
            durability_store,durability_budget,[durability_gate]).inspect()
        durability_receipt=retain_review(durability_store,durability_budget,
            [durability_gate],expected_provider_head=
                durability_budget.inspect()['head_sha256'],
            expected_account_heads={durability_gate.account_digest:
                durability_gate.inspect()['head_sha256']},
            expected_store_head=durability_replay['head_sha256'])
        durability_final=PaperProviderReadReviewStore(
            durability_store,durability_budget,[durability_gate]).inspect()
        signal_gate,signal_budget=build_pair(
            root/'retained-process-control')
        signal_store=(root/'retained-process-control'/'receipt.sqlite3')
        signal_raised=False
        def interrupt_commit_sync(_descriptor):raise KeyboardInterrupt
        store_module.os.fsync=interrupt_commit_sync
        try:
            try:
                retain_review(signal_store,signal_budget,[signal_gate],
                    expected_provider_head=signal_budget.inspect()['head_sha256'],
                    expected_account_heads={signal_gate.account_digest:
                        signal_gate.inspect()['head_sha256']})
            except KeyboardInterrupt:signal_raised=True
        finally:store_module.os.fsync=original_fsync
        signal_replay=PaperProviderReadReviewStore(
            signal_store,signal_budget,[signal_gate]).inspect()
        signal_receipt=retain_review(signal_store,signal_budget,[signal_gate],
            expected_provider_head=signal_budget.inspect()['head_sha256'],
            expected_account_heads={signal_gate.account_digest:
                signal_gate.inspect()['head_sha256']},
            expected_store_head=signal_replay['head_sha256'])
        signal_final=PaperProviderReadReviewStore(
            signal_store,signal_budget,[signal_gate]).inspect()
        fingerprint_gate,fingerprint_budget=build_pair(
            root/'retained-commit-fingerprint')
        fingerprint_store=(root/'retained-commit-fingerprint'/'receipt.sqlite3')
        original_require=store_module.require_file_identities
        fingerprint_fsynced=False;fingerprint_changes=0
        def observe_fingerprint_flush(descriptor):
            nonlocal fingerprint_fsynced
            result=original_fsync(descriptor)
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                fingerprint_fsynced=True
            return result
        def change_after_identity(*args,**kwargs):
            nonlocal fingerprint_changes
            result=original_require(*args,**kwargs)
            if fingerprint_fsynced and fingerprint_changes==0:
                fingerprint_changes+=1;state=fingerprint_store.stat()
                os.utime(fingerprint_store,ns=(state.st_atime_ns,
                    state.st_mtime_ns+1_000_000_000))
            return result
        fingerprint_failure_denied=False
        store_module.os.fsync=observe_fingerprint_flush
        store_module.require_file_identities=change_after_identity
        try:
            retain_review(fingerprint_store,fingerprint_budget,
                [fingerprint_gate],expected_provider_head=
                    fingerprint_budget.inspect()['head_sha256'],
                expected_account_heads={fingerprint_gate.account_digest:
                    fingerprint_gate.inspect()['head_sha256']})
        except ValueError as error:
            fingerprint_failure_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN')
        finally:
            store_module.os.fsync=original_fsync
            store_module.require_file_identities=original_require
        fingerprint_replay=PaperProviderReadReviewStore(
            fingerprint_store,fingerprint_budget,[fingerprint_gate]).inspect()
        fingerprint_receipt=retain_review(fingerprint_store,
            fingerprint_budget,[fingerprint_gate],expected_provider_head=
                fingerprint_budget.inspect()['head_sha256'],
            expected_account_heads={fingerprint_gate.account_digest:
                fingerprint_gate.inspect()['head_sha256']},
            expected_store_head=fingerprint_replay['head_sha256'])
        fingerprint_final=PaperProviderReadReviewStore(
            fingerprint_store,fingerprint_budget,[fingerprint_gate]).inspect()
        descriptor_gate,descriptor_budget=build_pair(
            root/'retained-commit-descriptor')
        descriptor_store=(root/'retained-commit-descriptor'/'receipt.sqlite3')
        original_open=store_module.os.open;descriptor_saved=None
        descriptor_tampered=False;descriptor_syncs=0
        def tamper_committed_descriptor(value,flags,*args,**kwargs):
            nonlocal descriptor_saved,descriptor_tampered
            descriptor=original_open(value,flags,*args,**kwargs)
            if (not descriptor_tampered and flags&os.O_RDWR and
                    Path(value).resolve()==descriptor_store.resolve()):
                size=os.fstat(descriptor).st_size
                os.lseek(descriptor,0,os.SEEK_SET)
                descriptor_saved=os.read(descriptor,size)
                offset=min(100,size-1);os.lseek(descriptor,offset,os.SEEK_SET)
                os.write(descriptor,bytes((descriptor_saved[offset]^1,)))
                descriptor_tampered=True;os.lseek(descriptor,0,os.SEEK_SET)
            return descriptor
        def count_descriptor_sync(descriptor):
            nonlocal descriptor_syncs
            descriptor_syncs+=1
            return original_fsync(descriptor)
        descriptor_failure_denied=False
        store_module.os.open=tamper_committed_descriptor
        store_module.os.fsync=count_descriptor_sync
        try:
            retain_review(descriptor_store,descriptor_budget,
                [descriptor_gate],expected_provider_head=
                    descriptor_budget.inspect()['head_sha256'],
                expected_account_heads={descriptor_gate.account_digest:
                    descriptor_gate.inspect()['head_sha256']})
        except ValueError as error:
            descriptor_failure_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN')
        finally:
            store_module.os.open=original_open
            store_module.os.fsync=original_fsync
        if descriptor_saved is not None:
            descriptor_store.write_bytes(descriptor_saved)
        descriptor_replay=PaperProviderReadReviewStore(
            descriptor_store,descriptor_budget,[descriptor_gate]).inspect()
        descriptor_receipt=retain_review(descriptor_store,
            descriptor_budget,[descriptor_gate],expected_provider_head=
                descriptor_budget.inspect()['head_sha256'],
            expected_account_heads={descriptor_gate.account_digest:
                descriptor_gate.inspect()['head_sha256']},
            expected_store_head=descriptor_replay['head_sha256'])
        descriptor_final=PaperProviderReadReviewStore(
            descriptor_store,descriptor_budget,[descriptor_gate]).inspect()
        inspection_gate,inspection_budget=build_pair(
            root/'retained-inspection-binding')
        inspection_store=(root/'retained-inspection-binding'/
            'receipt.sqlite3')
        retain_review(inspection_store,inspection_budget,[inspection_gate],
            expected_provider_head=
                inspection_budget.inspect()['head_sha256'],
            expected_account_heads={inspection_gate.account_digest:
                inspection_gate.inspect()['head_sha256']})
        foreign_inspection_store=(root/'retained-inspection-binding'/
            'foreign.sqlite3')
        foreign_inspection_store.write_bytes(inspection_store.read_bytes())
        foreign_db=sqlite3.connect(foreign_inspection_store)
        try:
            foreign_db.execute('PRAGMA user_version=1')
            foreign_db.commit()
        finally:foreign_db.close()
        inspection_reader=PaperProviderReadReviewStore(
            inspection_store,inspection_budget,[inspection_gate])
        inspection_bytes={path:path.read_bytes() for path in
            (inspection_store,foreign_inspection_store)}
        inspection_connect=inspection_reader._connect
        foreign_inspection_reader=PaperProviderReadReviewStore(
            foreign_inspection_store,inspection_budget,[inspection_gate])
        inspection_reader._connect=foreign_inspection_reader._connect
        inspection_connection_denied=False
        try:inspection_reader.inspect()
        except ValueError as error:
            inspection_connection_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_CONNECTION_BINDING_CHANGED')
        finally:inspection_reader._connect=inspection_connect
        class SpoofedInspectionConnection(sqlite3.Connection):
            def execute(self,statement,*args,**kwargs):
                if statement=='PRAGMA database_list':
                    class StaticRows:
                        def fetchall(self):return [
                            (0,'main',str(inspection_store.resolve()))]
                    return StaticRows()
                return super().execute(statement,*args,**kwargs)
        def substitute_inspection_content(*args,**kwargs):
            db=sqlite3.connect(
                foreign_inspection_store.resolve().as_uri()+'?mode=ro',
                uri=True,timeout=10,factory=SpoofedInspectionConnection)
            return db,False
        inspection_content_denied=False
        inspection_reader._connect=substitute_inspection_content
        try:inspection_reader.inspect()
        except ValueError as error:
            inspection_content_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_CONNECTION_CONTENT_CHANGED')
        finally:inspection_reader._connect=inspection_connect
        inspection_continuity_before=inspection_store.read_bytes()
        inspection_require=store_module.require_file_fingerprints
        inspection_load_calls=0;inspection_serialized=False
        class SerializationRaceConnection(sqlite3.Connection):
            def serialize(self,*args,**kwargs):
                nonlocal inspection_serialized
                result=super().serialize(*args,**kwargs)
                inspection_serialized=True
                return result
        def serialization_race_connection(*args,**kwargs):
            if args or kwargs:raise AssertionError('unexpected connection arguments')
            return (sqlite3.connect(
                inspection_store.resolve().as_uri()+'?mode=ro',uri=True,
                timeout=10,factory=SerializationRaceConnection),False)
        def serialization_race_require(*args,**kwargs):
            if inspection_serialized:store_module._fail('FILE_SET_CHANGED')
            return inspection_require(*args,**kwargs)
        inspection_load=inspection_reader._load
        def count_inspection_load(db):
            nonlocal inspection_load_calls
            inspection_load_calls+=1
            return inspection_load(db)
        inspection_continuity_denied=False
        inspection_reader._connect=serialization_race_connection
        inspection_reader._load=count_inspection_load
        store_module.require_file_fingerprints=serialization_race_require
        try:inspection_reader.inspect()
        except ValueError as error:
            inspection_continuity_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_FILE_SET_CHANGED')
        finally:
            inspection_reader._connect=inspection_connect
            inspection_reader._load=inspection_load
            store_module.require_file_fingerprints=inspection_require
        inspection_continuity_denied=(inspection_continuity_denied and
            inspection_load_calls==0 and
            inspection_store.read_bytes()==inspection_continuity_before and
            inspection_reader.inspect()['retained_receipts']==1)
        inspection_state=inspection_reader.inspect()
        inspection_scope=inspection_state['receipts'][-1]['scope']
        inspection_receipt=inspection_state['receipts'][-1][
            'receipt_sha256']
        def substitute_write_connection(write=False):
            if not write:return inspection_connect(write=write)
            db=sqlite3.connect(
                foreign_inspection_store.resolve().as_uri()+'?mode=rw',
                uri=True,timeout=10)
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            return db,False
        inspection_write_connection_denied=False
        inspection_reader._connect=substitute_write_connection
        try:
            inspection_reader.append(inspection_scope,
                expected_head=inspection_state['head_sha256'],
                admission=lambda:inspection_receipt)
        except ValueError as error:
            inspection_write_connection_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_CONNECTION_BINDING_CHANGED')
        finally:inspection_reader._connect=inspection_connect
        def substitute_write_content(write=False):
            if not write:return inspection_connect(write=write)
            db=sqlite3.connect(
                foreign_inspection_store.resolve().as_uri()+'?mode=rw',
                uri=True,timeout=10,factory=SpoofedInspectionConnection)
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            return db,False
        inspection_write_content_denied=False
        inspection_reader._connect=substitute_write_content
        try:
            inspection_reader.append(inspection_scope,
                expected_head=inspection_state['head_sha256'],
                admission=lambda:inspection_receipt)
        except ValueError as error:
            inspection_write_content_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_CONNECTION_CONTENT_CHANGED')
        finally:inspection_reader._connect=inspection_connect
        inspection_write_serialized=False
        inspection_main_fingerprint=store_module._main_fingerprint
        class ObservedWriteConnection(sqlite3.Connection):
            def serialize(self,*args,**kwargs):
                nonlocal inspection_write_serialized
                result=super().serialize(*args,**kwargs)
                inspection_write_serialized=True
                return result
        def observe_write_content(write=False):
            if not write:return inspection_connect(write=write)
            db=sqlite3.connect(
                inspection_store.resolve().as_uri()+'?mode=rw',uri=True,
                timeout=10,factory=ObservedWriteConnection)
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            return db,False
        def change_serialized_fingerprint(path):
            result=inspection_main_fingerprint(path)
            if inspection_write_serialized:
                replacement='f'*64 if result[-1]!='f'*64 else 'e'*64
                return (*result[:-1],replacement)
            return result
        inspection_write_continuity_denied=False
        inspection_reader._connect=observe_write_content
        store_module._main_fingerprint=change_serialized_fingerprint
        try:
            inspection_reader.append(inspection_scope,
                expected_head=inspection_state['head_sha256'],
                admission=lambda:inspection_receipt)
        except ValueError as error:
            inspection_write_continuity_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_CONNECTION_CONTENT_CHANGED')
        finally:
            inspection_reader._connect=inspection_connect
            store_module._main_fingerprint=inspection_main_fingerprint
        inspection_write_boundary_bound=(
            inspection_write_connection_denied and
            inspection_write_content_denied and
            inspection_write_continuity_denied and
            inspection_write_serialized and inspection_bytes==
                {path:path.read_bytes() for path in inspection_bytes} and
            inspection_reader.inspect()['retained_receipts']==1)
        setup_gate,setup_budget=build_pair(
            root/'retained-connection-setup')
        setup_store=(root/'retained-connection-setup'/'receipt.sqlite3')
        setup_target=setup_store.resolve().as_uri()+'?mode=rw'
        original_sqlite_connect=store_module.sqlite3.connect
        setup_foreign_before=foreign_inspection_store.read_bytes()
        def substitute_initialization(*args,**kwargs):
            if args and args[0]==setup_target:
                return original_sqlite_connect(
                    foreign_inspection_store.resolve().as_uri()+'?mode=rw',
                    uri=True,timeout=10)
            return original_sqlite_connect(*args,**kwargs)
        setup_connection_denied=False
        store_module.sqlite3.connect=substitute_initialization
        try:
            retain_review(setup_store,setup_budget,[setup_gate],
                expected_provider_head=
                    setup_budget.inspect()['head_sha256'],
                expected_account_heads={setup_gate.account_digest:
                    setup_gate.inspect()['head_sha256']})
        except ValueError as error:
            setup_connection_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_CONNECTION_BINDING_CHANGED')
        finally:store_module.sqlite3.connect=original_sqlite_connect
        setup_connection_denied=(setup_connection_denied and
            not setup_store.exists() and
            foreign_inspection_store.read_bytes()==setup_foreign_before)
        setup_receipt=retain_review(setup_store,setup_budget,[setup_gate],
            expected_provider_head=setup_budget.inspect()['head_sha256'],
            expected_account_heads={setup_gate.account_digest:
                setup_gate.inspect()['head_sha256']})
        setup_replay=PaperProviderReadReviewStore(setup_store,setup_budget,
            [setup_gate]).inspect()
        setup_connection_denied=(setup_connection_denied and
            setup_replay['retained_receipts']==1 and
            setup_receipt['head_sha256']==setup_replay['head_sha256'])
        inspection_target=inspection_store.resolve().as_uri()+'?mode=rw'
        def substitute_setup_content(*args,**kwargs):
            if args and args[0]==inspection_target:
                return original_sqlite_connect(
                    foreign_inspection_store.resolve().as_uri()+'?mode=rw',
                    uri=True,timeout=10,factory=SpoofedInspectionConnection)
            return original_sqlite_connect(*args,**kwargs)
        setup_content_denied=False
        store_module.sqlite3.connect=substitute_setup_content
        try:
            inspection_reader.append(inspection_scope,
                expected_head=inspection_state['head_sha256'],
                admission=lambda:inspection_receipt)
        except ValueError as error:
            setup_content_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_CONNECTION_CONTENT_CHANGED')
        finally:store_module.sqlite3.connect=original_sqlite_connect
        setup_content_denied=(setup_content_denied and inspection_bytes==
            {path:path.read_bytes() for path in inspection_bytes} and
            inspection_reader.inspect()['retained_receipts']==1)
        close_mutated=False
        class CloseMutationConnection(sqlite3.Connection):
            def close(self):
                nonlocal close_mutated
                super().close()
                if not close_mutated:
                    current=inspection_store.stat()
                    os.utime(inspection_store,ns=(current.st_atime_ns,
                        current.st_mtime_ns+1_000_000_000))
                    close_mutated=True
        def mutate_on_write_close(write=False):
            if not write:return inspection_connect(write=write)
            db=sqlite3.connect(
                inspection_store.resolve().as_uri()+'?mode=rw',uri=True,
                timeout=10,factory=CloseMutationConnection)
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            return db,False
        close_change_denied=False
        inspection_reader._connect=mutate_on_write_close
        try:
            inspection_reader.append(inspection_scope,
                expected_head=inspection_state['head_sha256'],
                admission=lambda:inspection_receipt)
        except ValueError as error:
            close_change_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_FILE_SET_CHANGED')
        finally:inspection_reader._connect=inspection_connect
        close_replay=inspection_reader.inspect()
        close_retry=inspection_reader.append(inspection_scope,
            expected_head=close_replay['head_sha256'],
            admission=lambda:inspection_receipt)
        close_final=inspection_reader.inspect()
        close_boundary_bound=(close_mutated and close_change_denied and
            close_replay['retained_receipts']==1 and
            close_retry['duplicate'] is True and
            close_final['retained_receipts']==1 and
            close_retry['head_sha256']==close_replay['head_sha256']==
                close_final['head_sha256'])
        close_error_gate,close_error_budget=build_pair(
            root/'retained-close-error')
        close_error_store=root/'retained-close-error'/'receipt.sqlite3'
        close_error_first=retain_review(close_error_store,close_error_budget,
            [close_error_gate],expected_provider_head=
                close_error_budget.inspect()['head_sha256'],
            expected_account_heads={close_error_gate.account_digest:
                close_error_gate.inspect()['head_sha256']})
        close_error_reader=PaperProviderReadReviewStore(close_error_store,
            close_error_budget,[close_error_gate])
        close_error_state=close_error_reader.inspect()
        close_error_scope=dict(close_error_state['receipts'][-1]['scope'])
        close_error_scope['review_sha256']='b'*64
        close_error_receipt=sha(close_error_scope)
        close_error_connect=close_error_reader._connect
        class CloseFailureConnection(sqlite3.Connection):
            def close(self):
                super().close()
                raise RuntimeError('private close failure')
        def fail_write_close(write=False):
            if not write:return close_error_connect(write=write)
            db=sqlite3.connect(close_error_store.resolve().as_uri()+
                '?mode=rw',uri=True,timeout=10,
                factory=CloseFailureConnection)
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            return db,False
        close_error_denied=False
        close_error_reader._connect=fail_write_close
        try:
            close_error_reader.append(close_error_scope,
                expected_head=close_error_first['head_sha256'],
                admission=lambda:close_error_receipt)
        except ValueError as error:
            close_error_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN')
        finally:close_error_reader._connect=close_error_connect
        close_error_replay=close_error_reader.inspect()
        close_error_retry=close_error_reader.append(close_error_scope,
            expected_head=close_error_replay['head_sha256'],
            admission=lambda:close_error_receipt)
        close_error_final=close_error_reader.inspect()
        close_error_recovery_bound=(close_error_denied and
            close_error_replay['retained_receipts']==2 and
            close_error_retry['duplicate'] is True and
            close_error_final['retained_receipts']==2 and
            close_error_retry['head_sha256']==
                close_error_replay['head_sha256']==
                close_error_final['head_sha256'])
        read_close_connect=inspection_reader._connect
        class ReadCloseFailureConnection(sqlite3.Connection):
            def close(self):
                super().close()
                raise RuntimeError('private read close failure')
        def fail_read_close(write=False):
            if write:return read_close_connect(write=write)
            db=sqlite3.connect(inspection_store.resolve().as_uri()+
                '?mode=ro',uri=True,timeout=10,
                factory=ReadCloseFailureConnection)
            db.execute('PRAGMA query_only=ON')
            return db,False
        read_close_denied=False
        inspection_reader._connect=fail_read_close
        try:inspection_reader.inspect()
        except ValueError as error:
            read_close_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_READ_ACKNOWLEDGEMENT_UNCERTAIN')
        finally:inspection_reader._connect=read_close_connect
        read_close_retry=inspection_reader.inspect()
        read_close_retry_bound=(read_close_denied and
            read_close_retry['retained_receipts']==1 and
            read_close_retry['head_sha256']==close_final['head_sha256'])
        def retarget_inspection(*args,**kwargs):
            inspection_reader.path=foreign_inspection_store
            return inspection_connect(*args,**kwargs)
        inspection_binding_denied=False
        inspection_reader._connect=retarget_inspection
        try:inspection_reader.inspect()
        except ValueError as error:
            inspection_binding_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_STORE_BINDING_CHANGED')
        finally:
            inspection_reader.path=inspection_store
            inspection_reader._connect=inspection_connect
        inspection_load=inspection_reader._load
        def mutate_inspection_fingerprint(db):
            result=inspection_load(db);state=inspection_store.stat()
            os.utime(inspection_store,ns=(state.st_atime_ns,
                state.st_mtime_ns+1_000_000_000))
            return result
        inspection_fingerprint_denied=False
        inspection_reader._load=mutate_inspection_fingerprint
        try:inspection_reader.inspect()
        except ValueError as error:
            inspection_fingerprint_denied=(str(error)==
                'PAPER_PROVIDER_READ_REVIEW_STORE_FILE_SET_CHANGED')
        finally:inspection_reader._load=inspection_load
        inspection_replay=inspection_reader.inspect()
        inspection_boundary_bound=(inspection_connection_denied and
            inspection_content_denied and
            inspection_binding_denied and
            inspection_fingerprint_denied and inspection_bytes==
                {path:path.read_bytes() for path in inspection_bytes} and
            inspection_replay['retained_receipts']==1)
        parent_descriptor_bound=True
        if os.name!='nt':
            parent_gate,parent_budget=build_pair(
                root/'retained-parent-descriptor')
            parent_store=(root/'retained-parent-descriptor'/'receipt.sqlite3')
            foreign_parent=root/'retained-parent-descriptor-foreign'
            foreign_parent.mkdir()
            parent_substituted=False;parent_directory_syncs=0
            def substitute_parent_descriptor(value,flags,*args,**kwargs):
                nonlocal parent_substituted
                if (not parent_substituted and
                        flags&getattr(os,'O_DIRECTORY',0) and
                        Path(value).resolve()==parent_store.resolve().parent):
                    parent_substituted=True
                    return original_open(foreign_parent,flags,*args,**kwargs)
                return original_open(value,flags,*args,**kwargs)
            def count_parent_directory_sync(descriptor):
                nonlocal parent_directory_syncs
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    parent_directory_syncs+=1
                return original_fsync(descriptor)
            parent_failure_denied=False
            store_module.os.open=substitute_parent_descriptor
            store_module.os.fsync=count_parent_directory_sync
            try:
                retain_review(parent_store,parent_budget,[parent_gate],
                    expected_provider_head=
                        parent_budget.inspect()['head_sha256'],
                    expected_account_heads={parent_gate.account_digest:
                        parent_gate.inspect()['head_sha256']})
            except ValueError as error:
                parent_failure_denied=(str(error)==
                    'PAPER_PROVIDER_READ_REVIEW_STORE_COMMIT_DURABILITY_UNCERTAIN')
            finally:
                store_module.os.open=original_open
                store_module.os.fsync=original_fsync
            parent_replay=PaperProviderReadReviewStore(
                parent_store,parent_budget,[parent_gate]).inspect()
            parent_receipt=retain_review(parent_store,parent_budget,
                [parent_gate],expected_provider_head=
                    parent_budget.inspect()['head_sha256'],
                expected_account_heads={parent_gate.account_digest:
                    parent_gate.inspect()['head_sha256']},
                expected_store_head=parent_replay['head_sha256'])
            parent_final=PaperProviderReadReviewStore(
                parent_store,parent_budget,[parent_gate]).inspect()
            parent_descriptor_bound=(parent_substituted and
                parent_directory_syncs==0 and parent_failure_denied and
                parent_replay['retained_receipts']==1 and
                parent_receipt['duplicate'] is True and
                parent_final['retained_receipts']==1 and
                parent_receipt['head_sha256']==
                    parent_replay['head_sha256']==
                    parent_final['head_sha256'])
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
            retained_receipt_acknowledgement_replayed=(
                lost_acknowledgements==1 and
                ack_replay['retained_receipts']==1 and
                ack_receipt['head_sha256']==ack_replay['head_sha256'] and
                ack_receipt['receipt_sha256']==
                    ack_replay['receipts'][0]['receipt_sha256'] and
                fabricated_denied and not fabricated_store.exists()),
            retained_receipt_recovery_inspection_preserves_process_control=(
                recovery_interrupted and
                recovery_replay['retained_receipts']==1 and
                recovery_receipt['duplicate'] is True and
                recovery_final['retained_receipts']==1 and
                recovery_receipt['head_sha256']==
                    recovery_replay['head_sha256']==
                    recovery_final['head_sha256']),
            retained_receipt_read_finalization_preserves_process_control=(
                finalization_interrupted and
                finalization_store.read_bytes()==finalization_bytes and
                finalization_replay['retained_receipts']==1 and
                finalization_replay['head_sha256']==
                    finalization_created['head_sha256']),
            retained_receipt_write_finalization_preserves_process_control=(
                write_signal_raised and write_signal_calls==3 and
                write_signal_replay['retained_receipts']==2 and
                write_signal_retry['duplicate'] is True and
                write_signal_final['retained_receipts']==2 and
                write_signal_retry['head_sha256']==
                    write_signal_replay['head_sha256']==
                    write_signal_final['head_sha256']),
            retained_receipt_fresh_failure_recoverable=(
                fresh_calls==2 and fresh_failure_denied and
                fresh_absence_restored and
                fresh_replay['retained_receipts']==1 and
                fresh_receipt['head_sha256']==fresh_replay['head_sha256'] and
                fresh_receipt['receipt_sha256']==
                    fresh_replay['receipts'][0]['receipt_sha256']),
            retained_receipt_initialization_failure_recoverable=(
                initialization_failures==1 and
                initialization_failure_denied and
                initialization_absence_restored and
                initialization_replay['retained_receipts']==1 and
                initialization_receipt['head_sha256']==
                    initialization_replay['head_sha256'] and
                initialization_receipt['receipt_sha256']==
                    initialization_replay['receipts'][0]['receipt_sha256']),
            retained_receipt_initialization_identity_continuity_pinned=(
                initialization_identity_continuity_pinned),
            retained_receipt_commit_flush_failure_recoverable=(
                durability_failures==1 and durability_failure_denied and
                durability_replay['retained_receipts']==1 and
                durability_receipt['duplicate'] is True and
                durability_final['retained_receipts']==1 and
                durability_receipt['head_sha256']==
                    durability_replay['head_sha256']==
                    durability_final['head_sha256']),
            retained_receipt_commit_sync_preserves_process_control=(
                signal_raised and signal_replay['retained_receipts']==1 and
                signal_receipt['duplicate'] is True and
                signal_final['retained_receipts']==1 and
                signal_receipt['head_sha256']==signal_replay['head_sha256']==
                    signal_final['head_sha256']),
            retained_receipt_commit_fingerprint_change_recoverable=(
                fingerprint_changes==1 and fingerprint_failure_denied and
                fingerprint_replay['retained_receipts']==1 and
                fingerprint_receipt['duplicate'] is True and
                fingerprint_final['retained_receipts']==1 and
                fingerprint_receipt['head_sha256']==
                    fingerprint_replay['head_sha256']==
                    fingerprint_final['head_sha256']),
            retained_receipt_commit_descriptor_tamper_recoverable=(
                descriptor_tampered and descriptor_syncs==0 and
                descriptor_failure_denied and
                descriptor_replay['retained_receipts']==1 and
                descriptor_receipt['duplicate'] is True and
                descriptor_final['retained_receipts']==1 and
                descriptor_receipt['head_sha256']==
                    descriptor_replay['head_sha256']==
                    descriptor_final['head_sha256']),
            retained_receipt_inspection_binding_pinned=(
                inspection_boundary_bound),
            retained_receipt_connection_binding_pinned=(
                inspection_connection_denied),
            retained_receipt_connection_content_pinned=(
                inspection_content_denied),
            retained_receipt_connection_content_continuity_pinned=(
                inspection_continuity_denied),
            retained_receipt_write_connection_pinned=(
                inspection_write_boundary_bound and
                inspection_write_connection_denied),
            retained_receipt_write_connection_content_pinned=(
                inspection_write_boundary_bound and
                inspection_write_content_denied),
            retained_receipt_write_connection_content_continuity_pinned=(
                inspection_write_boundary_bound and
                inspection_write_continuity_denied),
            retained_receipt_initialization_connection_pinned=(
                setup_connection_denied),
            retained_receipt_write_setup_content_pinned=(
                setup_content_denied),
            retained_receipt_connection_close_content_pinned=(
                close_boundary_bound),
            retained_receipt_close_error_requires_explicit_recovery=(
                close_error_recovery_bound),
            retained_receipt_read_close_error_requires_explicit_retry=(
                read_close_retry_bound),
            retained_receipt_parent_descriptor_boundary_respected=(
                parent_descriptor_bound),
            journals_unchanged=source_same,
            journal_file_fingerprints_rechecked=result['journal_files_rechecked_unchanged'],
            semantic_reader_failures_rechecked=result['semantic_reader_failures_rechecked'],
            output_never_authoritative=all(result[flag] is False for flag in FLAGS))
        return dict(source='TEMPORARY_LOCAL_PROVIDER_READ_HISTORY_FIXTURE',
            passed=all(checks.values()),**checks,**{flag:False for flag in FLAGS})
