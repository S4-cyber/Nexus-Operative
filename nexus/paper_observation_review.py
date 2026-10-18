"""Head-bound offline comparison of portable observations and retained activity.

Sequential read windows are uncertainty, never exact broker balance instants.
No provider/vault access, default storage, accounting write or order authority.
"""
import argparse
import getpass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import json
import re
import sqlite3

from nexus import alpaca_activity_normalizer as adapter
from nexus import broker_activity_review, retained_balance_review
from nexus.broker_activity_store import BrokerActivityStore
from nexus.alpaca_paper_contract import canonical, sha, text
from nexus import portable_paper_observation as portable

POLICY = 'nexus.portable-observation-retained-review.v1'
MAX_SAMPLES = 20


def _fail(code): raise ValueError('PAPER_OBSERVATION_REVIEW_' + code)


def _instant(token):
    """Convert exact epoch spelling to UTC nanoseconds; never round finer time."""
    with localcontext() as context:
        context.prec = 80
        nanos = Decimal(token) * 1000000000
        if nanos != nanos.to_integral_value(): _fail('TIME_PRECISION')
        seconds, fraction = divmod(int(nanos), 1000000000)
    try:
        base = datetime(1970,1,1,tzinfo=timezone.utc) + timedelta(seconds=seconds)
        return adapter._instant(base.isoformat(timespec='seconds')[:-6] + f'.{fraction:09d}+00:00')
    except (ValueError, OverflowError): _fail('TIME_RANGE')


def _changes(first, last):
    result = {}
    with localcontext() as context:
        context.prec = 80
        for field in portable.AMOUNTS:
            a, b = first[field], last[field]
            result[field] = None if a is None or b is None else text(Decimal(b)-Decimal(a))
    return result


def review(store, *, expected_account_id, expected_head, opening_observation, closing_observation):
    """Read-only diagnostic of two supplied manifests against one retained head.

    Absence of a retained boundary fill is not proof of a quiet account. Gross
    arithmetic remains conditional on unverified supplied observations/coverage.
    """
    first, last = portable.validate(opening_observation), portable.validate(closing_observation)
    if type(store) is not BrokerActivityStore: _fail('STORE_REQUIRED')
    account = adapter._uuid(expected_account_id)
    if store.account != account or first['account_id'] != account or last['account_id'] != account: _fail('ACCOUNT_DISAGREEMENT')
    if not isinstance(expected_head,str) or re.fullmatch(r'[0-9a-f]{64}',expected_head) is None: _fail('HEAD_REFERENCE')
    retained = store.inspect()
    if retained['retained_batches'] == 0: _fail('HISTORY_REQUIRED')
    if retained['head_sha256'] != expected_head: _fail('HEAD_DISAGREEMENT')
    reasons = {'PORTABLE_SOURCES_AND_BALANCES_UNATTESTED', 'SEQUENTIAL_READ_WINDOWS_NOT_BALANCE_INSTANTS',
               'ABSENCE_OF_RETAINED_BOUNDARY_FILLS_IS_NOT_COVERAGE'}
    blockers = set(); windows = None; changes = None
    if first['manifest_sha256'] == last['manifest_sha256']: blockers.add('SAME_OBSERVATION_REUSED')
    if first['source'] != last['source']: blockers.add('MIXED_DECLARED_SOURCES')
    for label, observation in (('OPENING',first),('CLOSING',last)):
        if observation['checks'] != 'OBSERVED_FIELDS_COVERED': reasons.add(label+'_OBSERVATION_REQUIRES_REVIEW')
        if observation['open_order_count']: reasons.add(label+'_OPEN_ORDER_HOLDS_UNRECONCILED')
        inv = observation['observed_inventory']
        if inv['cash'] is None or inv['btc_total'] is None or not inv['scope_supported']:
            blockers.add(label+'_TOTAL_BALANCES_UNKNOWN_OR_UNSUPPORTED')
    try:
        windows = {label:{key:_instant(observation['read_timing'][key]) for key in ('started_at','completed_at')}
            for label,observation in (('opening',first),('closing',last))}
    except ValueError as error:
        if str(error) not in ('PAPER_OBSERVATION_REVIEW_TIME_PRECISION','PAPER_OBSERVATION_REVIEW_TIME_RANGE'): raise
        blockers.add(str(error).removeprefix('PAPER_OBSERVATION_REVIEW_'))
    samples = []; boundary_events = 0; counts = dict(opening=0,closing=0)
    if windows is not None:
        if windows['opening']['completed_at'] >= windows['closing']['started_at']:
            blockers.add('OBSERVATION_WINDOWS_OVERLAP_OR_NOT_ORDERED')
        else:
            changes = _changes(first['observed_inventory'],last['observed_inventory'])
        for row in sorted(retained['normalized_activities'],key=lambda r:(r.get('occurred_at',''),r['activity_id'])):
            if row['kind'] != 'FILL': continue
            hit = [label for label,window in windows.items() if window['started_at'] <= row['occurred_at'] <= window['completed_at']]
            if not hit: continue
            boundary_events += 1
            for label in hit: counts[label] += 1
            if len(samples) < MAX_SAMPLES: samples.append(dict(activity_id=row['activity_id'],occurred_at=row['occurred_at'],windows=hit))
        if boundary_events: blockers.add('RETAINED_FILL_DURING_SEQUENTIAL_OBSERVATION')
    calculated = None
    if not blockers:
        start, end = windows['opening']['completed_at'], windows['closing']['started_at']
        # No balance is inferred from available quantity, buying power or holds.
        opening = {asset:Decimal(first['observed_inventory'][field]) for asset,field in (('USD','cash'),('BTC','btc_total'))}
        observed = {asset:Decimal(last['observed_inventory'][field]) for asset,field in (('USD','cash'),('BTC','btc_total'))}
        calculated = retained_balance_review._review_snapshot(retained, account=account, expected_head=expected_head,
            opening=opening, observed=observed, start=start, end=end)
        merged = calculated['retained_activity_review']
        reasons.update(calculated['review_reasons'])
    else:
        merged = broker_activity_review._review_snapshot(retained, account=account)
        reasons.update(merged['review_reasons'])
        reasons.add('OBSERVATION_BALANCE_ARITHMETIC_NOT_EVALUATED')
    reasons.update(blockers)
    if calculated is not None:
        # Share the validated merged review once in serialized output rather
        # than duplicate up to 2,000 normalized activity records in two places.
        calculated = {k:v for k,v in calculated.items() if k != 'retained_activity_review'}
        calculated['retained_activity_review_location'] = 'TOP_LEVEL_SHARED_REVIEW'
    # Full final replay, even when arithmetic is blocked. A sampled head is not
    # an admission lock, durable attestation or protection against later writes.
    if store.inspect()['head_sha256'] != expected_head: _fail('HEAD_CHANGED_DURING_REVIEW')
    if calculated is not None: calculated['head_rechecked'] = True
    scope = dict(policy=POLICY, account_sha256=retained['account_sha256'], head_sha256=expected_head,
        opening_manifest_sha256=first['manifest_sha256'], closing_manifest_sha256=last['manifest_sha256'])
    return dict(mode='PORTABLE_OBSERVATION_RETAINED_ACTIVITY_REVIEW', status='REVIEW_REQUIRED', read_only=True,
        **scope, input_sha256=sha(scope), head_rechecked=True, admission_reserved=False,
        observation_windows=windows, sampled_balance_changes=changes,
        change_scope='CLOSING_MINUS_OPENING_REPORTED_FIELDS_NOT_REALIZED_PNL_OR_SPENDABLE_FUNDS',
        boundary_fill_events=boundary_events, boundary_fills_by_window=counts, boundary_fill_samples=samples,
        boundary_sample_limit=MAX_SAMPLES, boundary_samples_truncated=boundary_events>len(samples),
        arithmetic_evaluated=calculated is not None and calculated['arithmetic_evaluated'],
        retained_balance_review=calculated, retained_activity_review=merged, review_reasons=sorted(reasons),
        source='PORTABLE_SUPPLIED_UNATTESTED', source_attested=False, source_coverage_verified=False,
        balance_instants_verified=False, observation_windows_verified_quiet=False, atomic_snapshot=False,
        fee_coverage_complete=False, fee_execution_links_verified=False, holds_reconciled=False,
        broker_account_reconciled=False, spendable_funds_verified=False, ledger_applied=False,
        ready_for_routing=False, broker_connected=False, live_orders_enabled=False,
        automatic_retry=False, automatic_continuation=False,
        limitations='Read-only supplied manifests plus one replay-validated local history head. No retained fill in read windows does not prove no actual activity. Boundary choices are diagnostic assumptions, not exact balance measurement times. Gross fills omit fees; differences are not profit, cash flows, verified holds or spending permission. No inferred fee links/posting instants, source freshness/coverage, account mutation or resume. Final head recheck is a sample only; hashes cannot attest source or prevent valid history rewrites/tail deletion.')


def _fixture_observation(account_id, cash, btc, start, end, *, available=None):
    """Supplied fixture fields only; no actual read, credential or source claim."""
    from nexus.alpaca_paper_inspection import _report
    account = dict(id=account_id,currency='USD',status='ACTIVE',crypto_status='ACTIVE',cash=cash,
        non_marginable_buying_power=cash,pending_transfer_in='0',pending_transfer_out='0',accrued_fees='0',
        account_blocked=False,trading_blocked=False,trade_suspended_by_user=False)
    positions = [dict(symbol='BTCUSD',asset_class='crypto',side='long',qty=btc,qty_available=btc if available is None else available)]
    report = _report(account,positions,[],dict(account),account_id,'INJECTED_UNATTESTED')
    report['read_timing'] = dict(started_at=start,completed_at=end,elapsed_seconds=end-start)
    report['observation_digest_scope'] = portable.DIGEST_SCOPE
    report['observation_sha256'] = sha(dict(profile='nexus.paper-account-observation.v1',**{k:report[k] for k in
        ('account_fields_sha256','reported_inventory_sha256','position_count','open_order_count','source','read_timing')}))
    return portable.create(report)


def fixture_report():
    import tempfile
    from pathlib import Path
    from nexus.broker_activity_store import _raw
    from nexus.broker_fee_projection import fixture_inputs
    data = fixture_inputs(); epoch = datetime(2026,10,17,18,tzinfo=timezone.utc).timestamp()
    with tempfile.TemporaryDirectory(prefix='nexus-observation-review-') as directory:
        store = BrokerActivityStore(Path(directory)/'activities.db','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        rows = [_raw(r) for r in adapter.fixture_report()['normalized_activities']]
        store.retain(rows,data['orders'],after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z')
        first = _fixture_observation(store.account,'1000','0',epoch-1,epoch-0.5)
        last = _fixture_observation(store.account,'1009.78011','0',epoch+3,epoch+3.5)
        scope = dict(expected_account_id=store.account,expected_head=store.inspect()['head_sha256'],opening_observation=first)
        result = review(store,closing_observation=last,**scope)
        from nexus import encrypted_observation as envelope
        import os
        phrase = os.urandom(32).hex()
        decoded = [portable.loads(envelope.unseal(envelope.seal(
            canonical(value).encode(),phrase),phrase)) for value in (first,last)]
        encrypted_result = review(store, expected_account_id=store.account,
            expected_head=scope['expected_head'], opening_observation=decoded[0], closing_observation=decoded[1])
        boundary = review(store,closing_observation=_fixture_observation(store.account,'1009.78011','0',epoch+2,epoch+3),**scope)
        stale = False
        try: review(store,closing_observation=last,**dict(scope,expected_head='0'*64))
        except ValueError as error:
            if str(error) != 'PAPER_OBSERVATION_REVIEW_HEAD_DISAGREEMENT': raise
            stale = True
        return dict(passed=result['arithmetic_evaluated'] and not boundary['arithmetic_evaluated'] and stale,
            review=result,boundary_review=boundary,stale_head_rejected=stale,
            encrypted_pair_review_matches=encrypted_result==result,decrypted_file_writes=False,
            source='FIXED_SYNTHETIC_PORTABLE_RETAINED_FIXTURE',broker_connected=False,live_orders_enabled=False)


def _load_file(path, *, encrypted=False, label='Observation'):
    # Explicit mode, never infer encryption from a suffix or retry as plaintext.
    # Reuse bounded regular-file reads; no decrypted intermediate file is made.
    from nexus import encrypted_observation as envelope
    raw = envelope._read(path, envelope.MAX_BYTES if encrypted else portable.MAX_BYTES)
    if encrypted:
        raw = envelope.unseal(raw, envelope.prompt_passphrase(label+' passphrase (not stored): '))
    return portable.loads(raw)


class _Parser(argparse.ArgumentParser):
    def error(self, message): self.exit(2, 'PAPER_OBSERVATION_REVIEW_USAGE_ERROR\n')


def main():
    parser = _Parser(description='Offline review of two private paper observations against retained activities. Prints private plaintext diagnostic JSON; encrypted inputs are decrypted in memory only. Never grants trading permission.')
    for name in ('activities','account','head','opening','closing'): parser.add_argument('--'+name,required=True)
    for name in ('opening','closing'):
        parser.add_argument('--'+name+'-encrypted',action='store_true',help='Read this input as an encrypted observation; prompt privately for its passphrase. No decrypted file is written.')
    args=parser.parse_args()
    try:
        result = review(BrokerActivityStore(args.activities,args.account), expected_account_id=args.account, expected_head=args.head,
            opening_observation=_load_file(args.opening, encrypted=args.opening_encrypted, label='Opening'),
            closing_observation=_load_file(args.closing, encrypted=args.closing_encrypted, label='Closing'))
    except (OSError,ValueError,TypeError,sqlite3.Error,EOFError,KeyboardInterrupt,MemoryError,getpass.GetPassWarning):
        print('PAPER_OBSERVATION_REVIEW_FAILED');return 2
    print(json.dumps(result,sort_keys=True,indent=2));return 0


if __name__ == '__main__': raise SystemExit(main())
