"""Revalidate saved offline trial evidence against the current source checkout.

Explicit local source tool only. No account/key/network access or release authority.
A self-consistent report/hash is not a signature, CI provenance or proof of execution.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

from nexus.release_roadmap import REVISION
from nexus.release_trials import PROFILES, _validate, source_digest

MAX_BYTES=8*1024*1024
ROOT=Path(__file__).resolve().parents[1]
FLAGS=('production_release_ready','release_approved','real_market_realism_authenticated',
       'actual_broker_acceptance','sustained_forward_acceptance','native_windows_acceptance',
       'live_orders_enabled','money_movement_enabled')
FIELDS={'mode','version','source_fingerprint','passed','environment','profiles','elapsed_seconds','limitations',*FLAGS}


def _fail(code):raise ValueError('RELEASE_EVIDENCE_'+code) from None


def _pairs(items):
    value={}
    for key,item in items:
        if key in value:_fail('JSON')
        value[key]=item
    return value


def _float(token):
    value=float(token)
    if not math.isfinite(value):_fail('JSON')
    return value


def verify(raw):
    """Validate bounded bytes; return only an allowlisted evidence-scope summary."""
    if type(raw) is not bytes or not 1<=len(raw)<=MAX_BYTES:_fail('SIZE')
    try:
        report=json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs,parse_float=_float,
            parse_constant=lambda _: _fail('JSON'))
    except (ValueError,UnicodeError,RecursionError):_fail('JSON')
    if type(report) is not dict or set(report)!=FIELDS:_fail('SHAPE')
    if report['mode']!='BOUNDED_OFFLINE_RELEASE_TRIALS' or report['passed'] is not True:_fail('RESULT')
    if report['version']!=REVISION:_fail('VERSION')
    if any(report[field] is not False for field in FLAGS):_fail('AUTHORITY')
    fingerprint=report['source_fingerprint']
    if (type(fingerprint) is not dict or set(fingerprint)!={'files','sha256'} or
        type(fingerprint['files']) is not int or fingerprint['files']<=0 or
        type(fingerprint['sha256']) is not str or re.fullmatch('[0-9a-f]{64}',fingerprint['sha256']) is None):_fail('FINGERPRINT')
    before=source_digest(ROOT)
    if fingerprint!=before:_fail('SOURCE_MISMATCH')
    profiles=report['profiles'];timings=report['elapsed_seconds'];environment=report['environment']
    if type(profiles) is not dict or set(profiles)!=set(PROFILES):_fail('PROFILES')
    if (type(timings) is not dict or set(timings)!=set(PROFILES) or
        any(type(v) not in (int,float) or not 0<=v<=86400 or not math.isfinite(v) for v in timings.values())):_fail('TIMINGS')
    if (type(environment) is not dict or set(environment)!={'python','system','sqlite'} or
        any(type(v) is not str or not 1<=len(v)<=128 for v in environment.values()) or
        type(report['limitations']) is not str or not 1<=len(report['limitations'])<=4096):_fail('METADATA')
    try:
        for name in PROFILES:_validate(name,profiles[name])
    except (ValueError,KeyError,TypeError,IndexError,AttributeError,OverflowError,RecursionError):_fail('PROFILE_FAILED')
    if source_digest(ROOT)!=before:_fail('SOURCE_CHANGED')
    return dict(mode='VERIFIED_OFFLINE_EVIDENCE_ONLY',version=REVISION,passed=True,
        source_fingerprint=before,profiles_verified=len(PROFILES),report_sha256=hashlib.sha256(raw).hexdigest(),
        **{flag:False for flag in FLAGS},
        limitations='Supplied report consistency and current-source match only. Hashes do not attest execution, CI identity or authenticity. No tests rerun, provider/Windows/market acceptance, funds, deployment or release approval. Independent evidence and owner approval remain required.')


def _read(path):
    path=Path(path)
    if path.is_symlink():_fail('FILE')
    flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)
    with os.fdopen(os.open(path,flags),'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_BYTES:_fail('FILE')
        raw=stream.read(MAX_BYTES+1);after=os.fstat(stream.fileno())
        fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
        if any(getattr(before,k)!=getattr(after,k) for k in fields):_fail('FILE_CHANGED')
    return raw


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'RELEASE_EVIDENCE_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__);parser.add_argument('report');args=parser.parse_args()
    try:result=verify(_read(args.report))
    except (OSError,ValueError,TypeError,MemoryError,RecursionError):
        print('RELEASE_EVIDENCE_FAILED');return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
