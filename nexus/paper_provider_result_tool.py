"""Explicit offline review of private provider/account evidence; no broker reads.

This command never writes receipts, starts the desktop, reads keys or grants
trading permission. Keep manifests outside the source/installation folder.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat

from nexus import paper_provider_result_review as review_module
from nexus import paper_read_review
from nexus.paper_activity_acceptance_probe import _private_path
from nexus.paper_provider_read_budget import PaperProviderReadBudget
from nexus.paper_read_pacing import PaperReadPacing
from nexus.sqlite_journal_guard import (
    capture_file_identities,check_path_set,require_file_identities)

PROFILE='nexus.paper-provider-result-input.v1'
MAX_BYTES=3*1024*1024
CHECKS=('review_checked','manifest_identity_rechecked',
    'semantic_reader_failures_rechecked','source_files_preserved')


def _fail():raise ValueError('PAPER_PROVIDER_RESULT_INPUT_INVALID') from None


def _path(value,base):
    if type(value) is not str or not 1<=len(value)<=4096 or '\0' in value:_fail()
    path=Path(value)
    if not path.is_absolute():path=base/path
    return _private_path(path)[0].absolute()


def _read(path):
    observed=path.lstat()
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink!=1 or not 1<=observed.st_size<=MAX_BYTES:_fail()
    flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)
    def same_file(left,right):return os.path.samestat(left,right)
    def stable(left,right):return (same_file(left,right) and
        (left.st_size,left.st_mtime_ns)==(right.st_size,right.st_mtime_ns))
    with os.fdopen(os.open(path,flags),'rb') as stream:
        before=os.fstat(stream.fileno())
        # Windows path stat and descriptor stat can expose different ctime
        # values immediately after a rewrite.  ctime is metadata, not file
        # identity.  samestat uses the platform's device/file identity; size
        # still binds the pre-open path sample to the opened descriptor.
        if not same_file(before,observed) or before.st_size!=observed.st_size or not stat.S_ISREG(before.st_mode) or before.st_nlink!=1:_fail()
        raw=stream.read(MAX_BYTES+1);after=os.fstat(stream.fileno())
    if len(raw)>MAX_BYTES or len(raw)!=before.st_size or not stable(after,before):_fail()
    final=path.lstat()
    if not same_file(final,before) or final.st_size!=before.st_size:_fail()
    return raw


def _decode(raw):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:_fail()
            value[key]=item
        return value
    value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=lambda _: _fail())
    paper_read_review._structure(value)
    if type(value) is not dict or set(value)!={'profile','provider_budget','expected_provider_head','accounts'} or value['profile']!=PROFILE:_fail()
    if type(value['accounts']) is not list or not 1<=len(value['accounts'])<=6:_fail()
    for account in value['accounts']:
        if type(account) is not dict or set(account)!={'account_id','pacing','expected_head','bindings'}:_fail()
    return value


def _manifest_binding(path,raw):
    try:
        observed=path.lstat()
        identities=capture_file_identities((path,),'PAPER_PROVIDER_RESULT_INPUT_')
        if _read(path)!=raw:_fail()
        return identities,(observed.st_size,observed.st_mtime_ns),raw
    except (OSError,ValueError,RuntimeError,TypeError):_fail()


def _require_manifest(path,binding):
    try:
        require_file_identities((path,),binding[0],'PAPER_PROVIDER_RESULT_INPUT_')
        observed=path.lstat();raw=_read(path)
        require_file_identities((path,),binding[0],'PAPER_PROVIDER_RESULT_INPUT_')
        if (observed.st_size,observed.st_mtime_ns)!=binding[1] or raw!=binding[2]:_fail()
    except (OSError,ValueError,RuntimeError,TypeError):_fail()


def _stable_manifest_reader(path,binding,reader):
    try:value=reader()
    except BaseException:
        _require_manifest(path,binding)
        raise
    _require_manifest(path,binding)
    return value


def review_file(input_path):
    """Hold one sampled manifest binding across the pinned, read-only review.

    Relative journal paths resolve beside the manifest, never against defaults.
    This is not persistent descriptor ownership, an ACL audit or external anchor.
    """
    path=_path(str(input_path),Path.cwd());raw=_read(path)
    manifest_binding=_manifest_binding(path,raw)
    def read():
        value=_decode(raw)
        provider=PaperProviderReadBudget(_path(value['provider_budget'],path.parent))
        gates=[];heads={};bindings={}
        for account in value['accounts']:
            gate=PaperReadPacing(_path(account['pacing'],path.parent),account['account_id'])
            if gate.account_digest in heads:_fail()
            gates.append(gate);heads[gate.account_digest]=account['expected_head'];bindings[gate.account_digest]=account['bindings']
        check_path_set((path,provider.path,*(gate.path for gate in gates)),'PAPER_PROVIDER_RESULT_INPUT_')
        result=review_module.review(provider,gates,bindings,expected_provider_head=value['expected_provider_head'],expected_account_heads=heads)
        if _read(path)!=raw:_fail()
        return result
    return _stable_manifest_reader(path,manifest_binding,read)


def _fixture(root):
    """Explicit disposable injected five-reader input for source/EXE acceptance."""
    from nexus.alpaca_paper_contract import canonical
    budget,gates,bindings=review_module._fixture(root)
    path=root/'input.json'
    value=dict(profile=PROFILE,provider_budget=str(budget.path.relative_to(root)),
        expected_provider_head=budget.inspect()['head_sha256'],accounts=[dict(account_id=gate.account,
            pacing=str(gate.path.relative_to(root)),expected_head=gate.inspect()['head_sha256'],
            bindings=bindings[gate.account_digest]) for gate in gates])
    path.write_text(canonical(value),encoding='utf-8')
    return path


def fixture_report():
    """Disposable manifest-bound review only; no provider, vault or authority."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix='nexus-provider-result-tool-') as directory:
        root=Path(directory);path=_fixture(root)
        files=lambda:{str(item.relative_to(root)):item.read_bytes()
            for item in root.rglob('*') if item.is_file()}
        before=files();result=review_file(path);preserved=files()==before
        def replacement_refused():
            probe=root/'identity-probe.json';probe.write_bytes(path.read_bytes())
            raw=_read(probe);binding=_manifest_binding(probe,raw)
            def replace():
                candidate=root/'identity-replacement.json';candidate.write_bytes(raw)
                candidate.replace(probe)
            try:_stable_manifest_reader(probe,binding,replace)
            except ValueError as error:return str(error)=='PAPER_PROVIDER_RESULT_INPUT_INVALID'
            return False
        def failure_refused():
            probe=root/'failure-probe.json';probe.write_bytes(path.read_bytes())
            raw=_read(probe);binding=_manifest_binding(probe,raw)
            def mutate_then_fail():
                probe.write_bytes(raw+b' ')
                raise RuntimeError('injected lower-reader failure')
            try:_stable_manifest_reader(probe,binding,mutate_then_fail)
            except ValueError as error:return str(error)=='PAPER_PROVIDER_RESULT_INPUT_INVALID'
            except RuntimeError:return False
            return False
        checks=dict(review_checked=result['local_provider_results_consistent'] is True,
            manifest_identity_rechecked=replacement_refused(),
            semantic_reader_failures_rechecked=failure_refused(),source_files_preserved=preserved)
        return dict(source='TEMPORARY_INJECTED_PROVIDER_RESULT_TOOL',passed=all(checks.values()),
            **checks,**{flag:False for flag in review_module.FLAGS})


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PAPER_PROVIDER_RESULT_TOOL_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__)
    parser.add_argument('--input',required=True,help='Private bounded JSON evidence manifest with exact provider/account heads, journal paths and normalized result bindings. Relative paths resolve beside this file. Read-only local consistency only; no connection, credential, receipt write or trade.')
    args=parser.parse_args()
    try:result=review_file(args.input)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,UnicodeError,OverflowError,MemoryError,RecursionError,RuntimeError,KeyboardInterrupt):
        print('PAPER_PROVIDER_RESULT_TOOL_FAILED');return 2
    print(json.dumps(result,sort_keys=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
