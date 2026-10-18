"""Portable, bounded paper observations. Hash consistency is not authenticity.

Offline validation never opens a vault, connects to a provider or changes state.
"""
import argparse
import json
import math
import re
from decimal import Decimal, localcontext
from uuid import UUID

from nexus.alpaca_paper_contract import canonical, number, text, sha

PROFILE = 'nexus.portable-paper-observation.v1'
MAX_BYTES = 16384
DIGEST_SCOPE = 'NORMALIZED_ACCOUNT_INVENTORY_COUNTS_SOURCE_LABEL_AND_SAMPLED_TIMING_ONLY'
LIMITATIONS = ('Unverified sequential paper observation. Hashes check content consistency, not source authenticity, '
    'complete coverage, freshness, verified holds, spendable funds or trading permission. Account identity and balances '
    'are private financial information; share only with people you trust. No credentials or raw provider bodies included.')
FLAGS = ('ready_for_routing', 'spendable_funds_verified', 'broker_account_reconciled', 'broker_connected',
         'ledger_applied', 'live_orders_enabled', 'atomic_snapshot', 'automatic_retry')
FIELDS = ('account_id', 'source', 'checks', 'observed_inventory', 'observed_usd_ceiling', 'position_count',
          'open_order_count', 'account_fields_sha256', 'reported_inventory_sha256', 'read_timing', 'observation_sha256')
AMOUNTS = ('cash', 'non_marginable_buying_power', 'btc_total', 'btc_available', 'btc_unavailable_difference')
KEYS = set(FIELDS) | {'profile', 'observation_digest_scope', 'authority', 'limitations', 'manifest_sha256'}


def _fail():
    raise ValueError('PAPER_OBSERVATION_INVALID')


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected): _fail()


def _amount(value):
    if value is None: return None
    if not isinstance(value, str) or re.fullmatch(r'(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,18})?', value) is None: _fail()
    parsed = number(value, zero=True)
    if text(parsed) != value: _fail()
    return parsed


def validate(value):
    """Return a detached allowlisted object; never attest source or freshness."""
    try:
        _keys(value, KEYS)
        if value['profile'] != PROFILE or value['limitations'] != LIMITATIONS or value['observation_digest_scope'] != DIGEST_SCOPE: _fail()
        if not isinstance(value['account_id'], str) or str(UUID(value['account_id'])) != value['account_id']: _fail()
        if value['source'] not in ('PAPER_HTTPS_READS', 'INJECTED_UNATTESTED'): _fail()
        if value['checks'] not in ('OBSERVED_FIELDS_COVERED', 'REVIEW_REQUIRED'): _fail()
        _keys(value['authority'], (*FLAGS, 'source_attested'))
        if any(flag is not False for flag in value['authority'].values()): _fail()
        for field in ('position_count', 'open_order_count'):
            if type(value[field]) is not int or not 0 <= value[field] <= 128: _fail()
        inv = value['observed_inventory']; _keys(inv, (*AMOUNTS, 'scope_supported'))
        amounts = {field:_amount(inv[field]) for field in AMOUNTS}
        if type(inv['scope_supported']) is not bool: _fail()
        total, available, difference = (amounts[k] for k in AMOUNTS[2:])
        if not inv['scope_supported']:
            if any(v is not None for v in (total, available, difference)) or value['position_count'] == 0: _fail()
        else:
            if total is None or value['position_count'] > 1 or (available is None) != (difference is None): _fail()
            if value['position_count'] == 0 and (total, available, difference) != (Decimal(0),)*3: _fail()
            with localcontext() as context:
                context.prec = 80
                if available is not None and (available > total or total-available != difference): _fail()
        if (any(v is None for v in amounts.values()) or not inv['scope_supported'] or value['open_order_count']) and value['checks'] != 'REVIEW_REQUIRED': _fail()
        ceiling = _amount(value['observed_usd_ceiling'])
        cash, buying = (amounts[k] for k in AMOUNTS[:2])
        if ceiling != (None if cash is None or buying is None else min(cash, buying)): _fail()
        timing = value['read_timing']; _keys(timing, ('started_at', 'completed_at', 'elapsed_seconds'))
        # Strings preserve Python numeric spelling through JavaScript downloads,
        # including integral floats and small scientific-notation elapsed times.
        sampled = {}
        for key, token in timing.items():
            if type(token) is not str or len(token)>32 or re.fullmatch(r'-?[0-9]{1,13}(?:\.[0-9]{1,24})?(?:e[+-][0-9]{1,3})?', token) is None: _fail()
            parsed = float(token) if '.' in token or 'e' in token else int(token)
            if str(parsed) != token or not math.isfinite(parsed) or not 0 <= parsed <= 1e12: _fail()
            sampled[key] = parsed
        duration = sampled['completed_at'] - sampled['started_at']
        if duration < 0 or sampled['elapsed_seconds'] >= 20 or abs(duration-sampled['elapsed_seconds']) > 2: _fail()
        for key in ('account_fields_sha256', 'reported_inventory_sha256', 'observation_sha256', 'manifest_sha256'):
            if not isinstance(value[key], str) or re.fullmatch(r'[0-9a-f]{64}', value[key]) is None: _fail()
        if value['reported_inventory_sha256'] != sha(inv): _fail()
        scope = dict(profile='nexus.paper-account-observation.v1', account_fields_sha256=value['account_fields_sha256'],
            reported_inventory_sha256=value['reported_inventory_sha256'], position_count=value['position_count'],
            open_order_count=value['open_order_count'], source=value['source'], read_timing=sampled)
        if value['observation_sha256'] != sha(scope): _fail()
        if value['manifest_sha256'] != sha({k:v for k,v in value.items() if k != 'manifest_sha256'}): _fail()
        encoded = canonical(value).encode('utf-8')
        if len(encoded) > MAX_BYTES: _fail()
        return json.loads(encoded)
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError, RecursionError):
        _fail()


def create(report):
    """Select only known observation fields; private extra response data is omitted."""
    try:
        if report.get('mode') != 'READ_ONLY_PAPER_INSPECTION' or any(report.get(k) is not False for k in FLAGS): _fail()
        if report.get('observation_digest_scope') != DIGEST_SCOPE: _fail()
        result = {key:report[key] for key in FIELDS}
        result['read_timing'] = {key:str(v) for key,v in result['read_timing'].items()}
        result.update(profile=PROFILE, observation_digest_scope=DIGEST_SCOPE, limitations=LIMITATIONS,
                      authority=dict.fromkeys((*FLAGS, 'source_attested'), False))
        result['manifest_sha256'] = sha(result)
        return validate(result)
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError, RecursionError):
        _fail()


def loads(raw):
    """Bounded UTF-8 JSON with duplicate keys and nonfinite tokens rejected."""
    if type(raw) is not bytes or len(raw) > MAX_BYTES: _fail()
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: _fail()
            result[key] = value
        return result
    try:
        return validate(json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=lambda _: _fail()))
    except (ValueError, UnicodeError, RecursionError):
        _fail()


def main():
    parser = argparse.ArgumentParser(description='Validate one exported paper observation offline. Hash consistency is not source authenticity or trading permission.')
    parser.add_argument('file'); args = parser.parse_args()
    try:
        with open(args.file, 'rb') as stream: value = loads(stream.read(MAX_BYTES+1))
    except (OSError, ValueError):
        print('PAPER_OBSERVATION_INVALID'); return 2
    print(json.dumps(dict(status='CONTENT_CONSISTENCY_CHECKED_SOURCE_UNVERIFIED', profile=PROFILE,
        manifest_sha256=value['manifest_sha256'], source_attested=False, spendable_funds_verified=False,
        ready_for_routing=False, live_orders_enabled=False), sort_keys=True))
    return 0


if __name__ == '__main__': raise SystemExit(main())
