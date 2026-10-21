"""One private offline review of requests, accepted fills and inventory evidence.

No vault, provider, journal/ledger writes, retry or trading permission. Hashes
bind supplied local content; they are not source signatures or encryption.
"""
import argparse
import getpass
import json
import re
import sqlite3
from decimal import Decimal

from nexus.alpaca_paper_contract import sha
from nexus.broker_request_journal import BrokerRequestJournal
from nexus import paper_request_activity_acceptance as requests
from nexus import paper_reconciliation_acceptance as inventory
from nexus import paper_observation_review as observations
from nexus import portable_paper_observation as portable
from nexus.paper_activity_acceptance_probe import status as acceptance_status, _private_path

POLICY='nexus.paper-account-evidence-review.v1'
STABILITY_POLICY='nexus.paper-account-evidence-stability-guard.v2'
FLAGS=tuple(dict.fromkeys((*requests.FLAGS,'source_coverage_verified','balance_instants_verified',
    'observation_windows_verified_quiet','atomic_snapshot','automatic_submission',
    'fee_posting_interval_verified','fee_debits_allocated_to_interval','fee_reserved_funding_verified')))
HEADS=('account_sha256','request_head_sha256','activity_head_sha256','pacing_head_sha256','acceptance_head_sha256')


def _fail(code):raise ValueError('PAPER_ACCOUNT_EVIDENCE_'+code)


def _review_paths(request_journal_path,pacing_path,activity_store_path,acceptance_store_path):
    paths=[_private_path(p)[1] for p in (request_journal_path,pacing_path,activity_store_path,acceptance_store_path)]
    if len(set(paths))!=4:_fail('DISTINCT_JOURNALS_REQUIRED')
    from nexus.sqlite_journal_guard import check_path_set
    check_path_set(paths,'PAPER_ACCOUNT_EVIDENCE_')


def review_current(*,expected_account_id,request_journal_path,pacing_path,activity_store_path,
                   acceptance_store_path,opening_observation,closing_observation):
    """Explicitly sample current local heads, then use the strict pinned review.

    No lock/reservation or atomic cross-database snapshot is implied. A changed
    sampled head fails the ordinary review; there is no automatic retry.
    """
    _review_paths(request_journal_path,pacing_path,activity_store_path,acceptance_store_path)
    current=acceptance_status(expected_account_id=expected_account_id,pacing_path=pacing_path,
        activity_store_path=activity_store_path,acceptance_store_path=acceptance_store_path)
    if not requests._complete(current):_fail('COMPLETE_ACCEPTED_HISTORY_REQUIRED')
    request=BrokerRequestJournal(request_journal_path,expected_account_id).recovery()
    result=review(expected_account_id=expected_account_id,request_journal_path=request_journal_path,
        pacing_path=pacing_path,activity_store_path=activity_store_path,acceptance_store_path=acceptance_store_path,
        expected_request_head=request['head_sha256'],expected_activity_head=current['retention_head_sha256'],
        opening_observation=opening_observation,closing_observation=closing_observation)
    # Also fence the pacing/acceptance heads sampled before request replay.
    if any(result[key]!=current[source] for key,source in
           (('pacing_head_sha256','pacing_head_sha256'),('acceptance_head_sha256','acceptance_head_sha256'))):
        _fail('JOURNALS_CHANGED_DURING_REVIEW')
    return dict(result,head_selection='EXPLICIT_CURRENT_LOCAL_HEADS_NO_AUTOMATIC_RETRY')


def review_current_with_stability_guards(*,expected_account_id,request_journal_path,pacing_path,
        activity_store_path,acceptance_store_path,pre_opening_observation,
        opening_observation,closing_observation,post_closing_observation):
    """Sample current heads once, then run the guarded pinned composition."""
    _review_paths(request_journal_path,pacing_path,activity_store_path,acceptance_store_path)
    current=acceptance_status(expected_account_id=expected_account_id,pacing_path=pacing_path,
        activity_store_path=activity_store_path,acceptance_store_path=acceptance_store_path)
    if not requests._complete(current):_fail('COMPLETE_ACCEPTED_HISTORY_REQUIRED')
    request=BrokerRequestJournal(request_journal_path,expected_account_id).recovery()
    result=review_with_stability_guards(expected_account_id=expected_account_id,
        request_journal_path=request_journal_path,pacing_path=pacing_path,
        activity_store_path=activity_store_path,acceptance_store_path=acceptance_store_path,
        expected_request_head=request['head_sha256'],
        expected_activity_head=current['retention_head_sha256'],
        pre_opening_observation=pre_opening_observation,opening_observation=opening_observation,
        closing_observation=closing_observation,post_closing_observation=post_closing_observation)
    if any(result[key]!=current[source] for key,source in
           (('pacing_head_sha256','pacing_head_sha256'),('acceptance_head_sha256','acceptance_head_sha256'))):
        _fail('JOURNALS_CHANGED_DURING_REVIEW')
    return dict(result,head_selection='EXPLICIT_CURRENT_LOCAL_HEADS_NO_AUTOMATIC_RETRY')


def public_summary(result):
    """Allowlist diagnostic counts/booleans only, never copy private text/maps.

    This is an unverified local diagnostic, not a signature or acceptance proof.
    Activity counts and agreement outcomes remain visible by explicit choice.
    """
    modes={'PAPER_ACCOUNT_EVIDENCE_BUNDLE_REVIEW':POLICY,
        'PAPER_ACCOUNT_EVIDENCE_STABILITY_GUARD_REVIEW':STABILITY_POLICY}
    if (type(result) is not dict or modes.get(result.get('mode'))!=result.get('policy') or
        result.get('status')!='REVIEW_REQUIRED' or
        result.get('read_only') is not True or any(result.get(k) is not False for k in FLAGS)):
        _fail('PUBLIC_SUMMARY_INVALID')
    checks=('journals_rechecked_unchanged','complete_accepted_history_replayed','component_heads_agree',
        'local_request_terms_agree','all_retained_request_origins_bound','inventory_arithmetic_evaluated')
    counts={'original_requests_reviewed':128,'explicit_fills_reviewed':2000,
        'request_origin_bound_transitions':128,'legacy_unbound_request_transitions':128}
    if (any(type(result.get(k)) is not bool for k in checks) or
        any(result[k] is not True for k in checks[:3]) or
        any(type(result.get(k)) is not int or not 0<=result[k]<=cap for k,cap in counts.items())):
        _fail('PUBLIC_SUMMARY_INVALID')
    gross=result.get('gross_only_minus_observed')
    if result['inventory_arithmetic_evaluated']:
        if (type(gross) is not dict or set(gross)!= {'USD','BTC'} or
            any(type(v) is not str or re.fullmatch(r'-?(?:0|[1-9][0-9]{0,63})(?:\.[0-9]{1,36})?',v) is None for v in gross.values())):
            _fail('PUBLIC_SUMMARY_INVALID')
        difference='NO_GROSS_DIFFERENCE' if all(Decimal(v)==0 for v in gross.values()) else 'DIFFERENCES_PRESENT'
    else:
        if gross is not None:_fail('PUBLIC_SUMMARY_INVALID')
        difference='NOT_EVALUATED'
    guarded=result['policy']==STABILITY_POLICY
    guard_checks=('stability_guard_bound','guard_manifests_distinct','guard_windows_ordered',
        'adjacent_guard_balances_stable','guard_retained_fill_events_absent',
        'guard_retained_fee_date_overlaps_absent',
        'accepted_declared_windows_cover_guard_envelope')
    if guarded and any(result.get(k) is not True for k in guard_checks):
        _fail('PUBLIC_SUMMARY_INVALID')
    return dict(mode='PAPER_ACCOUNT_EVIDENCE_PUBLIC_SUMMARY',policy=result['policy'],status='REVIEW_REQUIRED',read_only=True,
        **{k:result[k] for k in (*checks,*counts)},gross_difference_status=difference,
        **({k:True for k in guard_checks} if guarded else {}),
        **{k:False for k in FLAGS},
        scope='Explicit local diagnostic counts and agreement outcomes only. Account/order IDs, balances, amounts, dates, paths, hashes, observations and free-text reasons are omitted. This unsigned summary cannot prove its source or authenticate an account. No gross difference never means zero fees, profitability, complete activity, settled/spendable funds or trading permission.')


def review(*,expected_account_id,request_journal_path,pacing_path,activity_store_path,
           acceptance_store_path,expected_request_head,expected_activity_head,
           opening_observation,closing_observation):
    """Compose current heads once; disagreement remains diagnostic, never authority."""
    for head in (expected_request_head,expected_activity_head):
        if type(head) is not str or re.fullmatch('[0-9a-f]{64}',head) is None:_fail('HEAD_REFERENCE')
    _review_paths(request_journal_path,pacing_path,activity_store_path,acceptance_store_path)
    first,last=portable.validate(opening_observation),portable.validate(closing_observation)
    common=dict(expected_account_id=expected_account_id,pacing_path=pacing_path,
        activity_store_path=activity_store_path,acceptance_store_path=acceptance_store_path)
    before=acceptance_status(**common)
    if not requests._complete(before):_fail('COMPLETE_ACCEPTED_HISTORY_REQUIRED')
    context=dict(account_sha256=before['account_sha256'],request_head_sha256=expected_request_head,
        activity_head_sha256=expected_activity_head,pacing_head_sha256=before['pacing_head_sha256'],
        acceptance_head_sha256=before['acceptance_head_sha256'])
    if context['activity_head_sha256']!=before['retention_head_sha256']:_fail('ACTIVITY_HEAD_DISAGREEMENT')
    request_result=requests.review(**common,request_journal_path=request_journal_path,
        expected_request_head=expected_request_head,expected_activity_head=expected_activity_head)
    inventory_result=inventory.review(**common,expected_activity_head=expected_activity_head,
        opening_observation=first,closing_observation=last)
    for component,heads in ((request_result,HEADS),(inventory_result,tuple(k for k in HEADS if k!='request_head_sha256'))):
        if any(component.get(k)!=context[k] for k in heads):_fail('COMPONENT_HEAD_DISAGREEMENT')
        if (component.get('read_only') is not True or component.get('status')!='REVIEW_REQUIRED' or
            any(component.get(k) is not False for k in requests.FLAGS) or
            any(component[k] is not False for k in FLAGS if k in component)):_fail('COMPONENT_AUTHORITY_DISAGREEMENT')
    after=acceptance_status(**common)
    request_after=BrokerRequestJournal(request_journal_path,expected_account_id).recovery()
    if before!=after or request_after['head_sha256']!=expected_request_head:_fail('JOURNALS_CHANGED_DURING_REVIEW')
    pair=inventory_result['observation_review'];balance=pair['retained_balance_review']
    gross=None if balance is None else balance['gross_fill_review']
    rows=request_result['request_activity_review']['request_reviews']
    scope=dict(policy=POLICY,**context,accepted_transitions=before['accepted_transitions'],
        opening_manifest_sha256=first['manifest_sha256'],closing_manifest_sha256=last['manifest_sha256'],
        request_review_sha256=sha(request_result),inventory_review_sha256=sha(inventory_result))
    return dict(mode='PAPER_ACCOUNT_EVIDENCE_BUNDLE_REVIEW',status='REVIEW_REQUIRED',read_only=True,
        **scope,input_sha256=sha(scope),journals_rechecked_unchanged=True,
        complete_accepted_history_replayed=True,component_heads_agree=True,
        local_request_terms_agree=request_result['local_request_activity_agreement'],
        **{k:request_result[k] for k in ('request_origin_bound_transitions','legacy_unbound_request_transitions',
            'retained_request_origin_prefixes_replayed','all_retained_request_origins_bound')},
        original_requests_reviewed=len(rows),explicit_fills_reviewed=sum(row['explicit_fill_count'] for row in rows),
        request_agreement_blockers=request_result['request_activity_review']['agreement_blockers'],
        inventory_arithmetic_evaluated=pair['arithmetic_evaluated'],
        gross_only_ending_balances=None if gross is None else gross['gross_execution_ending_balances'],
        gross_only_minus_observed=None if gross is None else gross['gross_minus_supplied_observed'],
        gross_only_debits_covered=None if gross is None else gross['gross_only_debits_covered'],
        retained_unallocated_fee_debits=None if gross is None else gross['all_retained_unallocated_fee_debits'],
        retained_fee_record_counts=None if gross is None else gross['retained_fee_records_by_asset'],
        observation_review_reasons=pair['review_reasons'],
        review_reasons=sorted(set(pair['review_reasons'])|set(request_result['request_activity_review']['review_reasons'])),
        comparison_scope='EXPLICIT_GROSS_FILLS_OMIT_ALL_FEE_OBLIGATIONS_DIFFERENCES_ARE_NOT_PNL_OR_SPENDABLE_FUNDS',
        **{flag:False for flag in FLAGS},
        limitations='One private local consistency summary, not broker/account acceptance. Components share exact replayed heads and declared observation manifests, then all journals are sampled again. Separate databases are not atomic; valid-tail deletion/ABA replacement and other hosts are not excluded. Zero gross differences or zero retained fee records never mean zero actual fees, complete activity, settled funds, verified holds or profitability. No source signature, provider permission, ledger write, automatic retry, order or funding authority. Keep actual journals, observations and output outside Git.')


def review_with_stability_guards(*,expected_account_id,request_journal_path,pacing_path,
        activity_store_path,acceptance_store_path,expected_request_head,expected_activity_head,
        pre_opening_observation,opening_observation,closing_observation,post_closing_observation):
    """Compose exact request/activity heads with the four-observation guard."""
    base=review(expected_account_id=expected_account_id,request_journal_path=request_journal_path,
        pacing_path=pacing_path,activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path,expected_request_head=expected_request_head,
        expected_activity_head=expected_activity_head,opening_observation=opening_observation,
        closing_observation=closing_observation)
    guarded=inventory.review_with_stability_guards(expected_account_id=expected_account_id,
        pacing_path=pacing_path,activity_store_path=activity_store_path,
        acceptance_store_path=acceptance_store_path,expected_activity_head=expected_activity_head,
        pre_opening_observation=pre_opening_observation,opening_observation=opening_observation,
        closing_observation=closing_observation,post_closing_observation=post_closing_observation)
    inner=guarded.get('accepted_reconciliation_review')
    if (type(inner) is not dict or sha(inner)!=base['inventory_review_sha256'] or
        guarded.get('account_sha256')!=base['account_sha256'] or
        guarded.get('activity_head_sha256')!=base['activity_head_sha256']):
        _fail('COMPONENT_HEAD_DISAGREEMENT')
    if (guarded.get('read_only') is not True or guarded.get('status')!='REVIEW_REQUIRED' or
        any(guarded.get(k) is not False for k in FLAGS if k in guarded)):
        _fail('COMPONENT_AUTHORITY_DISAGREEMENT')
    current=acceptance_status(expected_account_id=expected_account_id,pacing_path=pacing_path,
        activity_store_path=activity_store_path,acceptance_store_path=acceptance_store_path)
    request=BrokerRequestJournal(request_journal_path,expected_account_id).recovery()
    if (request['head_sha256']!=expected_request_head or
        any(current[source]!=base[key] for key,source in
            (('account_sha256','account_sha256'),('activity_head_sha256','retention_head_sha256'),
             ('pacing_head_sha256','pacing_head_sha256'),('acceptance_head_sha256','acceptance_head_sha256')))):
        _fail('JOURNALS_CHANGED_DURING_REVIEW')
    scope=dict(policy=STABILITY_POLICY,**{k:base[k] for k in HEADS},
        account_evidence_sha256=base['input_sha256'],stability_guard_sha256=guarded['input_sha256'],
        pre_opening_manifest_sha256=guarded['pre_opening_manifest_sha256'],
        opening_manifest_sha256=guarded['opening_manifest_sha256'],
        closing_manifest_sha256=guarded['closing_manifest_sha256'],
        post_closing_manifest_sha256=guarded['post_closing_manifest_sha256'])
    copied=('accepted_transitions','journals_rechecked_unchanged','complete_accepted_history_replayed',
        'component_heads_agree','local_request_terms_agree','request_origin_bound_transitions',
        'legacy_unbound_request_transitions','retained_request_origin_prefixes_replayed',
        'all_retained_request_origins_bound','original_requests_reviewed','explicit_fills_reviewed',
        'request_agreement_blockers','inventory_arithmetic_evaluated','gross_only_ending_balances',
        'gross_only_minus_observed','gross_only_debits_covered','retained_unallocated_fee_debits',
        'retained_fee_record_counts','observation_review_reasons','review_reasons','comparison_scope')
    return dict(mode='PAPER_ACCOUNT_EVIDENCE_STABILITY_GUARD_REVIEW',status='REVIEW_REQUIRED',
        read_only=True,**scope,input_sha256=sha(scope),**{k:base[k] for k in copied},
        account_evidence_review=base,stability_guard_review=guarded,
        stability_guard_bound=True,guard_manifests_distinct=guarded['guard_manifests_distinct'],
        guard_windows_ordered=guarded['guard_windows_ordered'],
        adjacent_guard_balances_stable=guarded['adjacent_guard_balances_stable'],
        guard_retained_fill_events_absent=guarded['guard_retained_fill_events']==0,
        guard_retained_fee_date_overlaps_absent=guarded['guard_retained_fee_date_overlaps']==0,
        accepted_declared_windows_cover_guard_envelope=guarded['accepted_declared_windows_cover_guard_envelope'],
        **{flag:False for flag in FLAGS},
        limitations='One private exact-head account-evidence composition plus four supplied ordered stability observations. The guard detects retained fill overlap, adjacent local balance changes and retained fee reported-date overlap with either guard-band calendar date. Fee dates are not posting/execution instants; overlap fails closed, while absence proves neither quiet nor complete fee history. It does not make sequential reads quiet or atomic, attest provider source, prove received/late/corrected or fee-only coverage, link fees, verify holds or spendable funds, coordinate other writers, authorize routing or approve release. Keep all journals, observations and output outside Git.')


def _fixture(root):
    from nexus.paper_request_activity_acceptance import _fixture as prepare
    epoch=1792195200+18*3600;options,_=prepare(root,submission_admission=epoch)
    return dict(**options,
        opening_observation=observations._fixture_observation(options['expected_account_id'],'1000','0',epoch-2,epoch-1),
        closing_observation=observations._fixture_observation(options['expected_account_id'],'802','2',epoch+3,epoch+4))


def fixture_report():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix='nexus-account-evidence-') as directory:
        root=Path(directory);options=_fixture(root)
        epoch=1792195200+18*3600
        before=tuple((p.name,p.read_bytes()) for p in sorted(root.iterdir()))
        result=review(**options)
        repeated=review(**options)
        sampled=review_current(**{k:v for k,v in options.items() if k not in ('expected_request_head','expected_activity_head')})
        sampled.pop('head_selection')
        public=public_summary(result)
        guarded_options=dict(options,
            pre_opening_observation=observations._fixture_observation(
                options['expected_account_id'],'1000','0',epoch-4,epoch-3),
            post_closing_observation=observations._fixture_observation(
                options['expected_account_id'],'802','2',epoch+6,epoch+7))
        guarded=review_with_stability_guards(**guarded_options)
        guarded_repeated=review_with_stability_guards(**guarded_options)
        guarded_current=review_current_with_stability_guards(**{
            k:v for k,v in guarded_options.items() if k not in ('expected_request_head','expected_activity_head')})
        guarded_current.pop('head_selection')
        guarded_public=public_summary(guarded)
        unchanged=before==tuple((p.name,p.read_bytes()) for p in sorted(root.iterdir()))
        discrepant=review(**dict(options,closing_observation=observations._fixture_observation(
            options['expected_account_id'],'803','2',epoch+3,epoch+4)))
        zero=result['gross_only_minus_observed']=={'USD':'0','BTC':'0'}
        mismatch=discrepant['gross_only_minus_observed']=={'USD':'-1','BTC':'0'}
        checks=dict(component_heads_bound=result['component_heads_agree'],
            original_terms_and_two_fills_checked=result['local_request_terms_agree'] and result['original_requests_reviewed']==1 and result['explicit_fills_reviewed']==2,
            observation_arithmetic_evaluated=result['inventory_arithmetic_evaluated'],
            gross_only_zero_difference_observed=zero,changed_balance_difference_preserved=mismatch,
            zero_fee_records_not_promoted=result['retained_fee_record_counts']=={'USD':0,'BTC':0} and result['fee_coverage_complete'] is False,
            repeat_review_matches=result==repeated,current_head_review_matches=result==sampled,
            public_summary_checked=public['gross_difference_status']=='NO_GROSS_DIFFERENCE' and
                public['explicit_fills_reviewed']==2 and all(public[k] is False for k in FLAGS) and
                not any(k in public for k in (*HEADS,'gross_only_minus_observed','input_sha256','review_reasons')),
            stability_guard_bound=guarded['stability_guard_bound'],
            guarded_repeat_review_matches=guarded==guarded_repeated,
            guarded_current_head_review_matches=guarded==guarded_current,
            guarded_public_summary_checked=guarded_public['stability_guard_bound'] is True and
                guarded_public['gross_difference_status']=='NO_GROSS_DIFFERENCE' and
                all(guarded_public[k] is False for k in FLAGS),
            journals_unchanged=unchanged)
        return dict(source='SYNTHETIC_LOCAL_ACCOUNT_EVIDENCE_BUNDLE_FIXTURE',passed=all(checks.values()),
            **checks,actual_provider_access=False,actual_vault_access=False,**{flag:False for flag in FLAGS})


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PAPER_ACCOUNT_EVIDENCE_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__)
    for name in ('expected-account','request-journal','pacing','activity-store','acceptance-store',
                 'opening','closing'):parser.add_argument('--'+name,required=True)
    for name in ('pre-opening','post-closing'):parser.add_argument('--'+name)
    for name in ('request-head','activity-head'):parser.add_argument('--'+name)
    parser.add_argument('--use-current-heads',action='store_true',help='Explicitly sample current local fingerprints, then perform the same pinned review. A change fails; no retry, reservation or atomic snapshot.')
    parser.add_argument('--public-summary',action='store_true',help='Print only allowlisted local diagnostic counts and agreement outcomes. Omit account/order IDs, balances, dates, paths, hashes and private reasons. This is not acceptance proof.')
    for name in ('opening','closing','pre-opening','post-closing'):
        parser.add_argument('--'+name+'-encrypted',action='store_true',help='Decrypt this input in memory after a private prompt; no plaintext file is written. Output remains private plaintext.')
    args=parser.parse_args()
    if args.use_current_heads:
        if args.request_head is not None or args.activity_head is not None:parser.error('head selection')
    elif args.request_head is None or args.activity_head is None:parser.error('head selection')
    try:
        # Reject source-checkout/private-path aliases before prompting or reading.
        if (bool(args.pre_opening)!=bool(args.post_closing) or
            (args.pre_opening_encrypted or args.post_closing_encrypted) and not args.pre_opening):
            _fail('BOTH_STABILITY_GUARDS_REQUIRED')
        named=[args.request_journal,args.pacing,args.activity_store,args.acceptance_store,args.opening,args.closing]
        if args.pre_opening:named.extend((args.pre_opening,args.post_closing))
        resolved=[_private_path(p)[1] for p in named]
        if len(set(resolved))!=len(named):_fail('DISTINCT_INPUT_PATHS_REQUIRED')
        guarded=bool(args.pre_opening)
        operation=(review_current_with_stability_guards if args.use_current_heads else review_with_stability_guards) if guarded else (review_current if args.use_current_heads else review)
        heads={} if args.use_current_heads else dict(expected_request_head=args.request_head,expected_activity_head=args.activity_head)
        observations_input=dict(
            opening_observation=observations._load_file(args.opening,encrypted=args.opening_encrypted,label='Opening'),
            closing_observation=observations._load_file(args.closing,encrypted=args.closing_encrypted,label='Closing'))
        if guarded:
            observations_input.update(
                pre_opening_observation=observations._load_file(args.pre_opening,encrypted=args.pre_opening_encrypted,label='Pre-opening guard'),
                post_closing_observation=observations._load_file(args.post_closing,encrypted=args.post_closing_encrypted,label='Post-closing guard'))
        result=operation(expected_account_id=args.expected_account,request_journal_path=args.request_journal,
            pacing_path=args.pacing,activity_store_path=args.activity_store,acceptance_store_path=args.acceptance_store,
            **heads,**observations_input)
        if args.public_summary:result=public_summary(result)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,EOFError,KeyboardInterrupt,
            MemoryError,RecursionError,getpass.GetPassWarning):
        print('PAPER_ACCOUNT_EVIDENCE_FAILED');return 2
    print(json.dumps(result,sort_keys=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
