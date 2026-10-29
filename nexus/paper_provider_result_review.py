"""Read-only composition of local provider budgets and normalized read evidence.

Hashes and successful composition grant no provider, source, funds or routing
acceptance. Observations remain supplied private inputs and are never retained.
"""
import json
from pathlib import Path
from nexus.alpaca_paper_contract import canonical,sha
from nexus import paper_read_review as account_review
from nexus import paper_provider_read_review as provider_review
from nexus.paper_provider_read_budget import (
    MAX_DATABASE_BYTES as PROVIDER_DATABASE_BYTES,PaperProviderReadBudget)
from nexus.paper_read_pacing import (
    MAX_DATABASE_BYTES as ACCOUNT_DATABASE_BYTES,PaperReadPacing)
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints,capture_file_identities,
    require_file_fingerprints,require_file_identities)

FLAGS=tuple(dict.fromkeys((*provider_review.FLAGS,*account_review.FLAGS)))
CHECKS=('real_provider_and_results_match','changed_result_refused','missing_result_refused',
        'receipt_replayed','private_content_not_retained','source_files_preserved',
        'journal_file_fingerprints_rechecked','semantic_reader_failures_rechecked')


def _fail(code):raise ValueError('PAPER_PROVIDER_RESULT_REVIEW_'+code) from None


def _freeze(gates,bindings):
    keys={gate.account_digest for gate in gates}
    if type(bindings) is not dict or set(bindings)!=keys:_fail('ACCOUNT_BINDINGS_INVALID')
    try:
        account_review._structure(bindings)
        encoded=canonical(bindings)
        if len(encoded.encode())>account_review.MAX_BINDING_BYTES:_fail('ACCOUNT_BINDINGS_INVALID')
        frozen=json.loads(encoded)
        for key in sorted(keys):account_review._bindings(frozen[key])
        return frozen
    except (TypeError,ValueError,OverflowError,RecursionError):_fail('ACCOUNT_BINDINGS_INVALID')


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
    require_file_identities(paths,file_binding,'PAPER_PROVIDER_RESULT_REVIEW_')
    require_file_fingerprints(paths,file_limits,file_fingerprints,
        'PAPER_PROVIDER_RESULT_REVIEW_')


def _stable_reader(provider_budget,gates,store_binding,paths,absent_paths,
                   file_binding,file_limits,file_fingerprints):
    def read(reader):
        try:value=reader()
        except BaseException:
            _require_bindings(provider_budget,gates,store_binding,paths,
                absent_paths,file_binding,file_limits,file_fingerprints)
            raise
        _require_bindings(provider_budget,gates,store_binding,paths,
            absent_paths,file_binding,file_limits,file_fingerprints)
        return value
    return read


def review(provider_budget,gates,bindings_by_account,*,expected_provider_head,expected_account_heads):
    """Freeze all supplied results and compare pinned full histories twice.

    Global five-purpose coverage is required; individual accounts may contribute
    subsets, but every retained account session still needs its own evidence.
    """
    gates,heads=provider_review._inputs(provider_budget,gates,expected_account_heads)
    expected_provider_head=provider_review._digest(expected_provider_head,'PROVIDER_HEAD_INVALID')
    frozen=_freeze(gates,bindings_by_account)
    paths=(provider_budget.path,*(gate.path for gate in gates))
    store_binding=_store_binding(provider_budget,gates)
    file_limits=(PROVIDER_DATABASE_BYTES,*((ACCOUNT_DATABASE_BYTES,)*len(gates)))
    paths,file_limits,absent_paths=_file_scope(paths,file_limits)
    file_binding=capture_file_identities(paths,'PAPER_PROVIDER_RESULT_REVIEW_')
    file_fingerprints=capture_file_fingerprints(paths,file_limits,
        'PAPER_PROVIDER_RESULT_REVIEW_')
    _require_bindings(provider_budget,gates,store_binding,paths,absent_paths,
        file_binding,file_limits,file_fingerprints)
    stable_read=_stable_reader(provider_budget,gates,store_binding,paths,
        absent_paths,file_binding,file_limits,file_fingerprints)
    before=stable_read(lambda:provider_review.review(provider_budget,gates,
        expected_provider_head=expected_provider_head,expected_account_heads=heads))
    reasons=set(before['review_reasons']);manifest=[];matched=0;supplied=0;accounts_ok=True
    for gate in sorted(gates,key=lambda g:g.account_digest):
        key=gate.account_digest;values=frozen[key]
        result=stable_read(lambda gate=gate,values=values,key=key:
            account_review.review(gate,values,expected_head=heads[key]))
        if result['account_sha256']!=key:_fail('ACCOUNT_SCOPE_CHANGED')
        valid=result['receipt_bindings_consistent'] is True
        accounts_ok=accounts_ok and valid
        if not valid:reasons.add('ACCOUNT_NORMALIZED_EVIDENCE_UNVERIFIED')
        matched+=result['matched_sessions'];supplied+=result['supplied_bindings']
        bindings=account_review._bindings(values)
        scope=[dict(after_head_sha256=head,normalized_result_sha256=sha(value['normalized_result']),
            request_scope_sha256=sha(value['request_scope'])) for head,value in sorted(bindings.items())]
        manifest.append(dict(account_sha256=key,pacing_head_sha256=heads[key],
            binding_manifest_sha256=sha(scope),review_sha256=sha(result)))
    after=stable_read(lambda:provider_review.review(provider_budget,gates,
        expected_provider_head=expected_provider_head,expected_account_heads=heads))
    if after!=before:_fail('HISTORY_CHANGED')
    consistent=(before['local_provider_account_history_consistent'] is True and accounts_ok and
        matched==supplied==before['account_sessions_replayed']==before['provider_sessions_replayed'])
    return dict(mode='READ_ONLY_LOCAL_PROVIDER_NORMALIZED_RESULT_REVIEW',status='REVIEW_REQUIRED',read_only=True,
        provider_sha256=before['provider_sha256'],provider_head_sha256=expected_provider_head,
        account_manifest_sha256=before['account_manifest_sha256'],result_manifest_sha256=sha(manifest),
        account_journals=len(gates),provider_sessions_replayed=before['provider_sessions_replayed'],
        account_sessions_replayed=before['account_sessions_replayed'],normalized_sessions_matched=matched,
        supplied_bindings=supplied,operation_counts=before['operation_counts'],
        all_five_purposes_observed=before['all_five_purposes_observed'],
        local_provider_account_history_consistent=before['local_provider_account_history_consistent'],
        all_account_result_bindings_consistent=accounts_ok,
        local_provider_results_consistent=consistent,review_reasons=sorted(reasons),admission_reserved=False,
        journal_files_rechecked_unchanged=True,semantic_reader_failures_rechecked=True,
        **{flag:False for flag in FLAGS},
        limitations='Pinned, supplied, co-reported local histories and normalized observations only. No proof of raw-byte normalization, provider identity/quota, another path/credential/host, source coverage, clocks, fees, holds, funds, broker acceptance or routing. No reservation, retry, repair, migration or release authority. Output and optional receipt contain only aggregate counts and digests.')


def retain_review(store_path,provider_budget,gates,bindings_by_account,*,expected_provider_head,
                  expected_account_heads,expected_store_head='0'*64,precommit_check=lambda:True):
    from nexus.paper_provider_read_review_store import PaperProviderReadReviewStore,RESULT_PROFILE
    gates,heads=provider_review._inputs(provider_budget,gates,expected_account_heads)
    frozen=_freeze(gates,bindings_by_account)
    # Validate all source/store path families before any source review or write.
    store=PaperProviderReadReviewStore(store_path,provider_budget,gates)
    def scope():
        result=review(provider_budget,gates,frozen,expected_provider_head=expected_provider_head,expected_account_heads=heads)
        if result['local_provider_results_consistent'] is not True:_fail('INCOMPLETE')
        return dict(profile=RESULT_PROFILE,provider_sha256=result['provider_sha256'],
            provider_head_sha256=expected_provider_head,account_manifest_sha256=result['account_manifest_sha256'],
            result_manifest_sha256=result['result_manifest_sha256'],review_sha256=sha(result),
            account_journals=result['account_journals'],sessions_replayed=result['provider_sessions_replayed'],
            matched_sessions=result['normalized_sessions_matched'],normalized_sessions_replayed=result['normalized_sessions_matched'],
            operation_counts=result['operation_counts'],all_five_purposes_observed=result['all_five_purposes_observed'],
            local_provider_account_history_consistent=result['local_provider_account_history_consistent'])
    value=scope()
    return store.append(value,expected_head=expected_store_head,admission=lambda:sha(scope()),precommit_check=precommit_check)


def _fixture(root):
    from nexus.paper_provider_read_budget import PaperProviderReadBudget
    budget=PaperProviderReadBudget(root/'provider.sqlite3')
    gate,bindings=account_review._fixture(root/'account',full_activity=True,provider_budget=budget)
    return budget,[gate],{gate.account_digest:bindings}


def fixture_report():
    import tempfile
    from pathlib import Path
    from copy import deepcopy
    from nexus.paper_provider_read_review_store import PaperProviderReadReviewStore,RESULT_PROFILE
    with tempfile.TemporaryDirectory(prefix='nexus-provider-result-review-') as directory:
        root=Path(directory);budget,gates,bindings=_fixture(root)
        options=dict(expected_provider_head=budget.inspect()['head_sha256'],
            expected_account_heads={g.account_digest:g.inspect()['head_sha256'] for g in gates})
        files=lambda:{str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        before=files();result=review(budget,gates,bindings,**options)
        changed=deepcopy(bindings);changed[gates[0].account_digest][0]['normalized_result']['cash']='999'
        wrong=review(budget,gates,changed,**options)
        missing=deepcopy(bindings);missing[gates[0].account_digest].pop()
        omitted=review(budget,gates,missing,**options)
        source_same=files()==before
        path=root/'receipt.sqlite3';receipt=retain_review(path,budget,gates,bindings,**options)
        retained=PaperProviderReadReviewStore(path,budget,gates).inspect();blob=path.read_bytes()
        checks=dict(real_provider_and_results_match=result['local_provider_results_consistent'],
            changed_result_refused=not wrong['local_provider_results_consistent'],missing_result_refused=not omitted['local_provider_results_consistent'],
            receipt_replayed=retained['head_sha256']==receipt['head_sha256'] and retained['receipts'][0]['scope']['profile']==RESULT_PROFILE,
            private_content_not_retained=all(value not in blob for value in
                (gates[0].account.encode(),b'BTC/USD',b'nx_review_fixture',b'fixture_key_only_123',b'fixture_secret_only_123')),
            source_files_preserved=source_same and all(p.read_bytes()==body for name,body in before.items() for p in (root/name,)),
            journal_file_fingerprints_rechecked=result['journal_files_rechecked_unchanged'],
            semantic_reader_failures_rechecked=result['semantic_reader_failures_rechecked'])
        return dict(source='TEMPORARY_INJECTED_PROVIDER_RESULT_REVIEW',passed=all(checks.values()),**checks,**{key:False for key in FLAGS})
