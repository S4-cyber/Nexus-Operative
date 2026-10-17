"""Portable review summaries. Passing local checks never grants trading authority."""
import hashlib
import json

SCHEMA = 'nexus.forward-review.v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def build(report):
    reasons = list(report['reasons'])
    if report['provisional_active_session']:
        reasons.append('STOP_SESSION_FOR_STABLE_REVIEW')
    if report['stored_state'] not in ('STOPPED', 'PAUSED'):
        reasons.append('SESSION_INTERRUPTION_REQUIRES_REVIEW')
    modes = report['quote_evidence']['source_modes']
    if modes != ['PUBLIC_READER_SINGLE_SOURCE_UNATTESTED']:
        reasons.append('PUBLIC_READER_PROVENANCE_NOT_ESTABLISHED')
    payload = dict(schema=SCHEMA, report=report,
                   review_blockers=sorted(set(reasons)),
                   independent_review_required=True, release_approved=False,
                   strategy_validated=False,
                   scope='Summary only; retain original databases for quote/order replay. SHA256 detects accidental file changes, not coordinated rewriting or source authenticity.')
    return dict(payload=payload, sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())


def verify(bundle):
    """Check export integrity, not the truth of the underlying evidence."""
    if not isinstance(bundle, dict) or not isinstance(bundle.get('payload'), dict) or set(bundle) != {'payload', 'sha256'} or bundle['payload'].get('schema') != SCHEMA:
        raise ValueError('UNSUPPORTED_FORWARD_REVIEW')
    expected = hashlib.sha256(canonical(bundle['payload']).encode()).hexdigest()
    if bundle['sha256'] != expected:
        raise ValueError('FORWARD_REVIEW_DIGEST_DISAGREEMENT')
    return dict(summary_digest_ok=True, independently_attested=False, release_approved=False)


def main():
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description='Verify an exported forward review summary (no network or order operations).')
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    try:
        if args.report.stat().st_size > 1_000_000:
            raise ValueError('FORWARD_REVIEW_TOO_LARGE')
        print(canonical(verify(json.loads(args.report.read_text(encoding='utf-8')))))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(1, f'Review verification failed: {exc}\n')


if __name__ == '__main__':
    main()
