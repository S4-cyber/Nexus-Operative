"""Offline exact-byte review of one account/activity response bundle.

The bundle composes the existing account and activity normalization reviews at
one exact current pacing head.  It requires an ACCOUNT_INVENTORY session to be
followed immediately by the current ACTIVITY_COLLECTION session, binds every
private response body and the portable account observation, and rechecks the
pacing and activity journals unchanged.  It never opens credentials, contacts
a provider, writes evidence or grants account, source, funds, routing or
release authority.
"""
import argparse
import json
import re
import sqlite3

from nexus.alpaca_paper_contract import sha
from nexus import paper_account_response_review as account_review
from nexus import paper_activity_response_review as activity_review
from nexus.paper_activity_acceptance_probe import _private_path
from nexus.paper_read_pacing import PaperReadPacing
from nexus.broker_activity_store import BrokerActivityStore

MODE='PAPER_EXACT_RESPONSE_BUNDLE_REVIEW'
FLAGS=tuple(dict.fromkeys((*account_review.FLAGS,*activity_review.FLAGS)))


def _fail(code):
    raise ValueError('PAPER_RESPONSE_BUNDLE_REVIEW_'+code) from None


def _digest(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}',value) is None:
        _fail('HEAD_INVALID')
    return value


def review(*,pacing_path,activity_store_path,expected_account_id,
        expected_pacing_head,expected_activity_head,account_session_head,
        account_before_path,positions_path,orders_path,account_after_path,
        account_observation_path,activity_session_head,
        activity_account_before_path,activity_page_paths,
        activity_account_after_path):
    """Bind adjacent retained account/activity sessions to exact private bytes."""
    expected_pacing_head=_digest(expected_pacing_head)
    expected_activity_head=_digest(expected_activity_head)
    account_session_head=_digest(account_session_head)
    activity_session_head=_digest(activity_session_head)
    if type(activity_page_paths) not in (tuple,list) or not 1<=len(activity_page_paths)<=2:
        _fail('PAGE_COUNT_INVALID')
    paths=[pacing_path,activity_store_path,account_before_path,positions_path,
        orders_path,account_after_path,account_observation_path,
        activity_account_before_path,*activity_page_paths,activity_account_after_path]
    checked=[_private_path(value)[1] for value in paths]
    if len(set(checked))!=len(checked):_fail('DISTINCT_PATHS_REQUIRED')

    gate=PaperReadPacing(pacing_path,expected_account_id)
    store=BrokerActivityStore(activity_store_path,expected_account_id)
    pacing_before=gate.inspect();activity_before=store.inspect()
    if pacing_before['head_sha256']!=expected_pacing_head:_fail('PACING_HEAD_CHANGED')
    if activity_before['head_sha256']!=expected_activity_head:_fail('ACTIVITY_HEAD_CHANGED')
    history=gate.operation_history()
    indices={row['after_head_sha256']:index for index,row in enumerate(history)}
    if account_session_head not in indices or activity_session_head not in indices:
        _fail('SESSION_NOT_FOUND')
    account_index=indices[account_session_head];activity_index=indices[activity_session_head]
    if (activity_index!=account_index+1 or activity_index!=len(history)-1 or
            activity_session_head!=expected_pacing_head):
        _fail('SESSION_SEQUENCE_INVALID')
    if (history[account_index]['operation']!='ACCOUNT_INVENTORY' or
            history[activity_index]['operation']!='ACTIVITY_COLLECTION'):
        _fail('SESSION_SEQUENCE_INVALID')

    account=account_review.review(pacing_path=pacing_path,
        expected_account_id=expected_account_id,expected_head=expected_pacing_head,
        session_head=account_session_head,account_before_path=account_before_path,
        positions_path=positions_path,orders_path=orders_path,
        account_after_path=account_after_path,observation_path=account_observation_path)
    activity=activity_review.review(pacing_path=pacing_path,
        activity_store_path=activity_store_path,expected_account_id=expected_account_id,
        expected_pacing_head=expected_pacing_head,
        expected_activity_head=expected_activity_head,session_head=activity_session_head,
        account_before_path=activity_account_before_path,
        activity_page_paths=activity_page_paths,
        account_after_path=activity_account_after_path)
    if (account['account_sha256']!=activity['account_sha256'] or
            account['pacing_head_sha256']!=activity['pacing_head_sha256'] or
            any(account[flag] is not False or activity[flag] is not False for flag in FLAGS)):
        _fail('COMPONENT_DISAGREEMENT')
    if account['account_fields_sha256']!=activity['account_fields_sha256']:
        _fail('ADJACENT_ACCOUNT_FIELDS_CHANGED')
    if gate.inspect()!=pacing_before:_fail('PACING_HEAD_CHANGED')
    if store.inspect()!=activity_before:_fail('ACTIVITY_HEAD_CHANGED')

    scope=dict(mode=MODE,status='REVIEW_REQUIRED',read_only=True,
        account_sha256=account['account_sha256'],
        pacing_head_sha256=expected_pacing_head,
        activity_head_sha256=expected_activity_head,
        account_session_head_sha256=account_session_head,
        activity_session_head_sha256=activity_session_head,
        account_response_manifest_sha256=account['response_manifest_sha256'],
        account_observation_manifest_sha256=account['portable_manifest_sha256'],
        adjacent_account_fields_sha256=account['account_fields_sha256'],
        activity_response_manifest_sha256=activity['response_manifest_sha256'],
        activity_receipt_sha256=activity['retained_receipt_sha256'],
        account_review_sha256=account['review_sha256'],
        activity_review_sha256=activity['review_sha256'],
        response_bodies=account['response_bodies']+activity['response_bodies'],
        activity_pages=activity['activity_pages'],
        exact_response_bytes_match=True,account_normalization_replayed=True,
        account_observation_replayed=True,activity_normalization_replayed=True,
        activity_observation_replayed=True,activity_receipt_replayed=True,
        sessions_adjacent=True,activity_session_is_current_tail=True,
        adjacent_account_fields_match=True,
        journals_rechecked_unchanged=True,admission_reserved=False,
        **{flag:False for flag in FLAGS})
    scope['review_sha256']=sha(scope)
    scope['limitations']=('Exact private bytes locally replay one adjacent ACCOUNT_INVENTORY and current-tail '
        'ACTIVITY_COLLECTION pair at shared journal heads and requires their normalized account fields to match. '
        'This detects an adjacent discontinuity but does not make the reads atomic. Hash agreement is not provider identity, TLS capture, '
        'account ownership, source or received-activity coverage, creation/execution/fee coverage, verified '
        'clocks, fee links/posting, holds, settled or spendable funds, broker-wide pacing, routing permission or '
        'release acceptance. Keep all response bodies, observations and output outside Git.')
    return scope


def _fixture(root,two_pages=False,activity_account_cash='1000'):
    """Create disposable injected responses for one exact adjacent session pair."""
    from pathlib import Path
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    account=account_review._fixture(root/'account')
    activity=activity_review._fixture(root/'activity',two_pages=two_pages,
        pacing_path=account['pacing_path'],account_cash=activity_account_cash)
    return dict(pacing_path=account['pacing_path'],
        activity_store_path=activity['activity_store_path'],
        expected_account_id=account['expected_account_id'],
        expected_pacing_head=activity['expected_pacing_head'],
        expected_activity_head=activity['expected_activity_head'],
        account_session_head=account['session_head'],
        account_before_path=account['account_before_path'],
        positions_path=account['positions_path'],orders_path=account['orders_path'],
        account_after_path=account['account_after_path'],
        account_observation_path=account['observation_path'],
        activity_session_head=activity['session_head'],
        activity_account_before_path=activity['account_before_path'],
        activity_page_paths=activity['activity_page_paths'],
        activity_account_after_path=activity['account_after_path'])


def fixture_report():
    """Bounded injected bundle evidence for release trials; no external IO."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix='nexus-response-bundle-') as directory:
        root=Path(directory);options=_fixture(root/'base',two_pages=True)
        before=sorted((str(path.relative_to(root/'base')),path.read_bytes())
            for path in (root/'base').rglob('*') if path.is_file())
        result=review(**options);repeated=review(**options)
        after=sorted((str(path.relative_to(root/'base')),path.read_bytes())
            for path in (root/'base').rglob('*') if path.is_file())
        changed=_fixture(root/'changed')
        changed['activity_page_paths'][0].write_bytes(
            changed['activity_page_paths'][0].read_bytes()+b' ')
        changed_rejected=False
        try:review(**changed)
        except ValueError as error:
            changed_rejected='RESPONSE_DIGEST_DISAGREEMENT' in str(error)
        discontinuous=_fixture(root/'discontinuous',activity_account_cash='999')
        discontinuity_rejected=False
        try:review(**discontinuous)
        except ValueError as error:
            discontinuity_rejected='ADJACENT_ACCOUNT_FIELDS_CHANGED' in str(error)
        tail=_fixture(root/'tail');gate=PaperReadPacing(tail['pacing_path'],tail['expected_account_id'])
        with gate.ownership() as owner:
            token=gate.begin(admission=lambda:1792454401,ownership=owner,
                operation='ACCOUNT_INVENTORY')
            gate.finish(token,admission=lambda:1792454402,status_code=401,ownership=owner,
                receipt=dict(operation='ACCOUNT_INVENTORY',request_kinds=['ACCOUNT'],
                    request_sha256=['a'*64],response_sha256=[]))
        tail_rejected=False
        try:review(**dict(tail,expected_pacing_head=gate.inspect()['head_sha256']))
        except ValueError as error:
            tail_rejected='SESSION_SEQUENCE_INVALID' in str(error)
        checks=dict(exact_adjacent_sessions_bound=result['sessions_adjacent'] and
                result['activity_session_is_current_tail'],
            exact_account_response_replayed=result['account_normalization_replayed'] and
                result['account_observation_replayed'],
            exact_activity_response_replayed=result['activity_normalization_replayed'] and
                result['activity_observation_replayed'] and result['activity_receipt_replayed'],
            adjacent_account_fields_match=result['adjacent_account_fields_match'],
            repeat_review_matches=result==repeated,changed_activity_response_rejected=changed_rejected,
            adjacent_account_discontinuity_rejected=discontinuity_rejected,
            session_tail_required=tail_rejected,journals_unchanged=before==after)
        return dict(source='SYNTHETIC_EXACT_RESPONSE_BUNDLE_FIXTURE',passed=all(checks.values()),
            **checks,actual_provider_access=False,actual_vault_access=False,
            **{flag:False for flag in FLAGS})


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PAPER_RESPONSE_BUNDLE_REVIEW_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__)
    for name in ('pacing','activity-store','expected-account','expected-pacing-head',
            'expected-activity-head','account-session-head','account-before','positions',
            'orders','account-after','account-observation','activity-session-head',
            'activity-account-before','activity-account-after'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--activity-page',required=True,action='append')
    args=parser.parse_args()
    try:
        result=review(pacing_path=args.pacing,activity_store_path=args.activity_store,
            expected_account_id=args.expected_account,
            expected_pacing_head=args.expected_pacing_head,
            expected_activity_head=args.expected_activity_head,
            account_session_head=args.account_session_head,
            account_before_path=args.account_before,positions_path=args.positions,
            orders_path=args.orders,account_after_path=args.account_after,
            account_observation_path=args.account_observation,
            activity_session_head=args.activity_session_head,
            activity_account_before_path=args.activity_account_before,
            activity_page_paths=args.activity_page,
            activity_account_after_path=args.activity_account_after)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,UnicodeError,
            OverflowError,MemoryError,RecursionError,RuntimeError,KeyboardInterrupt):
        print('PAPER_RESPONSE_BUNDLE_REVIEW_FAILED');return 2
    print(json.dumps(result,sort_keys=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
