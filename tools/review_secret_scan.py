"""Review redacted Gitleaks results without excluding any scanned history.

Only two exact findings in one immutable synthetic acceptance fixture have
been reviewed. New commits, paths, locations, rules or source bytes remain
blocking. This policy never prints secret, match, author or message fields.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_FINDINGS = 10000
FIXTURE_COMMIT = '884da4467cf0aaabb85f2963ca7b2db04357bed8'
FIXTURE_PATH = 'nexus/paper_activity_sse_acceptance_evidence.py'
FIXTURE_SHA256 = 'e7f156518badf1a1334a58599043ab61c228297105e39c380a854b6cb61be05b'
# Both lines assign explicitly fixture-labelled constants, subsequently
# replaced by repeated-character markers. The complete original blob is pinned.
REVIEWED_LOCATIONS = {(24, 24, 2, 34), (25, 25, 2, 40)}
MODE = 'REDACTED_FULL_HISTORY_SECRET_REVIEW'


class SecretReviewError(ValueError):
    pass


def _fail():
    raise SecretReviewError('SECRET_SCAN_REVIEW_INCOMPLETE') from None


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            _fail()
        value[key] = item
    return value


def load_report(path):
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 1 <= before.st_size <= MAX_REPORT_BYTES:
        _fail()
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or not os.path.samestat(before, opened) or opened.st_size != before.st_size:
            _fail()
        raw = stream.read(MAX_REPORT_BYTES + 1)
        after = os.fstat(stream.fileno())
    final = path.lstat()
    if len(raw) != before.st_size or not os.path.samestat(opened, final) or any(
            getattr(opened, key) != getattr(after, key) or getattr(before, key) != getattr(final, key)
            for key in ('st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')):
        _fail()
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=lambda _: _fail())


def _fixture_proof(repository):
    reference = FIXTURE_COMMIT + ':' + FIXTURE_PATH
    command = ['git', '-C', str(repository), 'cat-file']
    size = subprocess.run([*command, '-s', reference], capture_output=True, timeout=10, check=True)
    if not 1 <= int(size.stdout) <= 1024 * 1024:
        _fail()
    blob = subprocess.run([*command, 'blob', reference], capture_output=True, timeout=10, check=True).stdout
    if len(blob) != int(size.stdout) or hashlib.sha256(blob).hexdigest() != FIXTURE_SHA256:
        _fail()


def review(rows, *, scan_exit_code, repository):
    if type(scan_exit_code) is not int or scan_exit_code not in (0, 1):
        _fail()
    if type(rows) is not list or len(rows) > MAX_FINDINGS or bool(rows) != (scan_exit_code == 1):
        _fail()
    blocking = []
    reviewed = 0
    seen = set()
    proof_checked = False
    for row in rows:
        if type(row) is not dict:
            _fail()
        rule, commit, path = (row.get(key) for key in ('RuleID', 'Commit', 'File'))
        if type(rule) is not str or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', rule) is None:
            _fail()
        if type(commit) is not str or re.fullmatch(r'[a-f0-9]{40}', commit) is None:
            _fail()
        if type(path) is not str or not 1 <= len(path) <= 4096 or any(ord(char) < 32 for char in path):
            _fail()
        location = tuple(row.get(key) for key in ('StartLine', 'EndLine', 'StartColumn', 'EndColumn'))
        if any(type(value) is not int or not 1 <= value <= 2**31 - 1 for value in location):
            _fail()
        if location[1] < location[0] or (location[0] == location[1] and location[3] < location[2]):
            _fail()
        fingerprint = f'{commit}:{path}:{rule}:{location[0]}'
        if row.get('Fingerprint') != fingerprint or fingerprint in seen or row.get('Secret') != 'REDACTED':
            _fail()
        seen.add(fingerprint)
        known = (commit == FIXTURE_COMMIT and path == FIXTURE_PATH and
                 rule == 'generic-api-key' and location in REVIEWED_LOCATIONS)
        if known:
            if not proof_checked:
                _fixture_proof(repository)
                proof_checked = True
            reviewed += 1
        else:
            blocking.append(dict(rule=rule, commit=commit, line=location[0],
                file_sha256=hashlib.sha256(path.encode()).hexdigest()))
    return dict(mode=MODE, scan_complete=True, passed=not blocking,
        scanner_exit_code=scan_exit_code, raw_findings=len(rows),
        reviewed_synthetic_findings=reviewed, blocking_findings=len(blocking), locations=blocking,
        history_rewritten=False, scan_exclusions_added=False,
        limitations='Two exact historical synthetic findings only; all other findings block. '
            'A completed scan/review is not a guarantee that no secret exists.')


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'SECRET_SCAN_REVIEW_USAGE_ERROR\n')


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('--report', required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--scan-exit-code', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        result = review(load_report(args.report), scan_exit_code=args.scan_exit_code, repository=args.repository)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, subprocess.SubprocessError,
            MemoryError, RecursionError):
        print(json.dumps(dict(mode=MODE, passed=False, scan_complete=False,
            reason='SECRET_SCAN_REVIEW_INCOMPLETE'), sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
