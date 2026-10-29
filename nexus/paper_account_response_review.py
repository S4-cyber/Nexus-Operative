"""Offline exact-byte review for one retained paper account-read session.

Private response bodies and the portable observation are caller-supplied.  The
review replays the existing normalizer and compares both the four response-byte
digests and normalized-result digest with one exact successful pacing session.
It never opens credentials, contacts a provider, writes a receipt or grants
account, funds, hold, routing or release authority.
"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from nexus import alpaca_paper_inspection as inspection
from nexus.alpaca_paper_contract import sha
from nexus.paper_activity_acceptance_probe import _private_path
from nexus.paper_provider_result_tool import _read
from nexus import paper_read_projection as projection
from nexus.paper_read_pacing import (
    MAX_DATABASE_BYTES as PACING_DATABASE_BYTES,PaperReadPacing,
    RECEIPT_PROFILE_V3,fixed_targets_match)
from nexus import portable_paper_observation as portable
from nexus.sqlite_journal_guard import (
    capture_file_fingerprints,capture_file_identities,
    require_file_fingerprints,require_file_identities)

MODE='PAPER_ACCOUNT_RESPONSE_NORMALIZATION_REVIEW'
MAX_OBSERVATION_BYTES=portable.MAX_BYTES
FLAGS=('source_attested','source_coverage_verified','activity_coverage_verified',
    'fee_coverage_complete','fee_execution_links_verified','holds_reconciled',
    'broker_account_reconciled','spendable_funds_verified','ready_for_routing',
    'broker_connected','ledger_applied','live_orders_enabled','money_movement_enabled',
    'automatic_retry','automatic_continuation','release_approved')
CHECKS=('review_checked','private_file_set_rechecked',
    'semantic_reader_failures_rechecked','source_files_preserved')


def _fail(code):
    raise ValueError('PAPER_ACCOUNT_RESPONSE_REVIEW_'+code) from None


def _digest(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}',value) is None:_fail('HEAD_INVALID')
    return value


def _raw(path,raw=None):
    path,_=_private_path(path)
    if raw is None:raw=_read(path)
    if len(raw)>inspection.MAX_BODY:_fail('BODY_CAP')
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=inspection._pairs,
            parse_constant=inspection._nonfinite,parse_float=Decimal)
    except (UnicodeError,ValueError,TypeError,RecursionError,MemoryError):
        _fail('BODY_INVALID')
    return raw,value


def _store_binding(gate):
    return gate.path,gate.account_digest


def _require_files(paths,limits,identities,fingerprints,gate=None,store_binding=None):
    if gate is not None and (type(gate) is not PaperReadPacing or
            _store_binding(gate)!=store_binding):
        _fail('STORE_BINDING_CHANGED')
    try:
        require_file_identities(paths,identities,'PAPER_ACCOUNT_RESPONSE_REVIEW_')
        require_file_fingerprints(paths,limits,fingerprints,
            'PAPER_ACCOUNT_RESPONSE_REVIEW_')
    except ValueError:
        _fail('FILE_SET_CHANGED')


def _stable_reader(paths,limits,identities,fingerprints,gate=None,store_binding=None):
    def read(reader):
        try:value=reader()
        except BaseException:
            _require_files(paths,limits,identities,fingerprints,gate,store_binding)
            raise
        _require_files(paths,limits,identities,fingerprints,gate,store_binding)
        return value
    return read


def _timing(value):
    result={}
    for key,token in value.items():
        result[key]=float(token) if '.' in token or 'e' in token else int(token)
    return result


def review(*,pacing_path,expected_account_id,expected_head,session_head,
        account_before_path,positions_path,orders_path,account_after_path,
        observation_path):
    """Recompute one account result from exact private bytes without any IO side effect."""
    expected_head=_digest(expected_head);session_head=_digest(session_head)
    supplied=(pacing_path,account_before_path,positions_path,orders_path,
        account_after_path,observation_path)
    paths=tuple(_private_path(value)[1] for value in supplied)
    if len(set(paths))!=len(paths):_fail('DISTINCT_PATHS_REQUIRED')
    limits=(PACING_DATABASE_BYTES,*((inspection.MAX_BODY,)*4),MAX_OBSERVATION_BYTES)
    identities=capture_file_identities(paths,'PAPER_ACCOUNT_RESPONSE_REVIEW_')
    fingerprints=capture_file_fingerprints(paths,limits,
        'PAPER_ACCOUNT_RESPONSE_REVIEW_')
    stable_files=_stable_reader(paths,limits,identities,fingerprints)
    gate=stable_files(lambda:PaperReadPacing(paths[0],expected_account_id))
    store_binding=_store_binding(gate)
    stable_read=_stable_reader(paths,limits,identities,fingerprints,gate,store_binding)
    before=stable_read(gate.inspect)
    if before['head_sha256']!=expected_head:_fail('HEAD_CHANGED')
    matches=[row for row in stable_read(gate.operation_history)
        if row['after_head_sha256']==session_head]
    if len(matches)!=1:_fail('SESSION_NOT_FOUND')
    record=matches[0]
    if (record['operation']!='ACCOUNT_INVENTORY' or record['outcome']!='SUCCESS' or
            record['receipt_profile']!=RECEIPT_PROFILE_V3 or
            record['normalized_result_bound'] is not True or not fixed_targets_match(record,'ACCOUNT_INVENTORY')):
        _fail('SESSION_SCOPE_INVALID')
    bodies=[];values=[]
    for path in paths[1:5]:
        raw=stable_read(lambda path=path:_read(path))
        raw,value=stable_read(lambda path=path,raw=raw:_raw(path,raw))
        bodies.append(raw);values.append(value)
    body_hashes=tuple(hashlib.sha256(raw).hexdigest() for raw in bodies)
    if body_hashes!=record['response_sha256']:_fail('RESPONSE_DIGEST_DISAGREEMENT')
    observation_path=paths[5]
    observation_raw=stable_read(lambda:_read(observation_path))
    if len(observation_raw)>MAX_OBSERVATION_BYTES:_fail('OBSERVATION_CAP')
    def load_observation():
        try:return portable.loads(observation_raw)
        except ValueError:_fail('OBSERVATION_INVALID')
    observation=stable_read(load_observation)
    if observation['account_id']!=gate.account:_fail('ACCOUNT_DISAGREEMENT')
    def normalize():
        try:
            result=inspection._report(*values,gate.account,observation['source'])
            result['read_timing']=_timing(observation['read_timing'])
            result['observation_sha256']=sha(dict(profile='nexus.paper-account-observation.v1',
                account_fields_sha256=result['account_fields_sha256'],
                reported_inventory_sha256=result['reported_inventory_sha256'],
                position_count=result['position_count'],open_order_count=result['open_order_count'],
                source=result['source'],read_timing=result['read_timing']))
            result['observation_digest_scope']=portable.DIGEST_SCOPE
            result['portable_observation']=portable.create(result)
            return result
        except (ValueError,TypeError,KeyError,OverflowError,RecursionError,MemoryError):
            _fail('NORMALIZATION_INVALID')
    result=stable_read(normalize)
    if result['portable_observation']!=observation:_fail('OBSERVATION_DISAGREEMENT')
    timing=result['read_timing']
    if not (record['started_at']<=timing['started_at']<=timing['completed_at']<=record['completed_at']):
        _fail('SESSION_TIMING_DISAGREEMENT')
    normalized_digest=stable_read(lambda:projection.digest('ACCOUNT_INVENTORY',result))
    if normalized_digest!=record['result_projection_sha256']:_fail('NORMALIZED_RESULT_DISAGREEMENT')
    if stable_read(gate.inspect)!=before:_fail('HEAD_CHANGED')
    scope=dict(mode=MODE,status='REVIEW_REQUIRED',read_only=True,
        account_sha256=gate.account_digest,pacing_head_sha256=expected_head,
        session_head_sha256=session_head,response_manifest_sha256=sha(list(body_hashes)),
        portable_manifest_sha256=observation['manifest_sha256'],
        portable_observation_file_sha256=hashlib.sha256(observation_raw).hexdigest(),
        account_fields_sha256=result['account_fields_sha256'],
        normalized_result_sha256=normalized_digest,response_bodies=4,
        exact_response_bytes_match=True,normalization_replayed=True,
        portable_observation_matches=True,session_timing_envelope_match=True,
        journal_rechecked_unchanged=True,private_file_set_rechecked_unchanged=True,
        semantic_reader_failures_rechecked=True,
        admission_reserved=False,**{flag:False for flag in FLAGS})
    scope['review_sha256']=sha(scope)
    scope['limitations']=('Exact private response bytes and one caller-supplied portable observation match a '
        'single replayed local ACCOUNT_INVENTORY session. Hash equality and local normalization do not attest '
        'provider identity, TLS capture, completeness, clocks, account ownership, fee posting/linkage, holds, '
        'settled or spendable funds, another host, routing permission or release acceptance. Keep all response '
        'bodies, observations and output outside Git.')
    return scope


def fixture_report():
    """Bounded private-file-set evidence for release trials; no external IO."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix='nexus-account-response-review-') as directory:
        root=Path(directory);options=_fixture(root/'base')
        files=lambda:{str(path.relative_to(root)):path.read_bytes()
            for path in root.rglob('*') if path.is_file()}
        before=files();result=review(**options);preserved=files()==before
        changed=_fixture(root/'changed')
        original=inspection._report
        def mutate_then_fail(*args,**kwargs):
            path=changed['observation_path']
            path.write_bytes(path.read_bytes()+b' ')
            raise RuntimeError('injected lower-reader failure')
        inspection._report=mutate_then_fail
        try:
            try:review(**changed)
            except ValueError as error:
                failure_rechecked=str(error)==(
                    'PAPER_ACCOUNT_RESPONSE_REVIEW_FILE_SET_CHANGED')
            except RuntimeError:failure_rechecked=False
            else:failure_rechecked=False
        finally:inspection._report=original
        checks=dict(review_checked=result['normalization_replayed'] is True,
            private_file_set_rechecked=result['private_file_set_rechecked_unchanged'] is True,
            semantic_reader_failures_rechecked=failure_rechecked,
            source_files_preserved=preserved)
        return dict(source='TEMPORARY_INJECTED_ACCOUNT_RESPONSE_REVIEW',
            passed=all(checks.values()),**checks,**{flag:False for flag in FLAGS})


def _fixture(root,wall_clock_value=1000,open_orders=None):
    """Create disposable injected bodies plus a real reader/pacing receipt."""
    import io
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    account='01234567-89ab-cdef-0123-456789abcdef'
    raw_account=dict(id=account,currency='USD',status='ACTIVE',crypto_status='ACTIVE',
        account_blocked=False,trading_blocked=False,trade_suspended_by_user=False,
        cash='1000',non_marginable_buying_power='900',pending_transfer_in='0',
        pending_transfer_out='0',accrued_fees='0')
    rows=[raw_account,[dict(symbol='BTCUSD',asset_class='crypto',side='long',qty='2',qty_available='1.9')],
        [] if open_orders is None else open_orders,raw_account]
    bodies=[json.dumps(value,separators=(',',':')).encode() for value in rows]
    class Response:
        status=200;headers={'Content-Type':'application/json'}
        def __init__(self,url,raw):self.url=url;self.body=io.BytesIO(raw)
        def geturl(self):return self.url
        def read(self,size):return self.body.read(size)
        def __enter__(self):return self
        def __exit__(self,*args):self.body.close()
    class Opener:
        def __init__(self):self.items=iter(bodies)
        def open(self,request,timeout):return Response(request.full_url,next(self.items))
    gate=PaperReadPacing(root/'pacing.sqlite3',account)
    report=inspection.inspect_account('fixture_key_only_123','fixture_secret_only_123',
        confirm_read_only_paper=True,expected_account_id=account,pacing=gate,opener=Opener(),
        wall_clock=lambda:wall_clock_value,elapsed_clock=lambda:50)
    body_paths=[]
    for index,raw in enumerate(bodies):
        path=root/f'response-{index}.json';path.write_bytes(raw);body_paths.append(path)
    observation=root/'observation.json';observation.write_text(
        json.dumps(report['portable_observation'],sort_keys=True,separators=(',',':')),encoding='utf-8')
    state=gate.inspect();session=gate.operation_history()[-1]
    return dict(pacing_path=gate.path,expected_account_id=account,
        expected_head=state['head_sha256'],session_head=session['after_head_sha256'],
        account_before_path=body_paths[0],positions_path=body_paths[1],orders_path=body_paths[2],
        account_after_path=body_paths[3],observation_path=observation)


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PAPER_ACCOUNT_RESPONSE_REVIEW_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__)
    for name in ('pacing','expected-account','expected-head','session-head','account-before',
                 'positions','orders','account-after','observation'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    try:
        result=review(pacing_path=args.pacing,expected_account_id=args.expected_account,
            expected_head=args.expected_head,session_head=args.session_head,
            account_before_path=args.account_before,positions_path=args.positions,
            orders_path=args.orders,account_after_path=args.account_after,
            observation_path=args.observation)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,UnicodeError,
            OverflowError,MemoryError,RecursionError,RuntimeError,KeyboardInterrupt):
        print('PAPER_ACCOUNT_RESPONSE_REVIEW_FAILED');return 2
    print(json.dumps(result,sort_keys=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
