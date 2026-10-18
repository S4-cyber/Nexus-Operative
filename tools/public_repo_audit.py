"""Fail closed on private artifacts in tracked paths and all reachable Git history.

Companion to Gitleaks, not a replacement. Never prints file contents/secrets.
Run only trusted policy against an untrusted checkout, with no provider secrets.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

MAX_BYTES = 512 * 1024 * 1024
MAX_OBJECTS = 100000
PRIVATE_MAGIC = (b'SQLite format 3\x00', b'-----'+b'BEGIN PRIVATE KEY-----', b'-----'+b'BEGIN RSA PRIVATE KEY-----',
                 b'-----'+b'BEGIN EC PRIVATE KEY-----', b'-----'+b'BEGIN OPENSSH PRIVATE KEY-----',
                 b'-----'+b'BEGIN ENCRYPTED PRIVATE KEY-----', b'NEXUS-OBS-ENC-1\n')


def path_rule(name):
    path = PurePosixPath(name.lower()); parts = path.parts; base = path.name
    if (base.startswith('.env') or base.endswith('.env')) and base != '.env.example': return 'ENVIRONMENT_FILE'
    if base in ('.netrc','.npmrc','.pypirc','.envrc'): return 'CREDENTIAL_CONFIGURATION'
    if re.search(r'\.(?:db|sqlite|sqlite3)(?:-(?:wal|shm|journal))?$',base): return 'RUNTIME_DATABASE'
    if re.search(r'\.(?:pem|key|p8|p12|pfx|jks|keystore|kdbx|nexusenc|age|enc|bak|backup|dump|sql|zip|tar|tgz|7z)$',base) or base.endswith('.tar.gz'): return 'PRIVATE_KEY_OR_ARCHIVE'
    if any(part in ('secrets','.nexus-operative','private-data','backups') for part in parts): return 'PRIVATE_DATA_DIRECTORY'
    if 'storage' in parts and base not in ('readme.txt','readme.md','.gitkeep'): return 'RUNTIME_STORAGE'
    if base.startswith('nexus-paper-observation') and base.endswith('.json') or base == 'nexus-backup-manifest.json': return 'PRIVATE_OBSERVATION_OR_BACKUP'
    return None


def content_rule(prefix):
    if any(prefix.startswith(magic) for magic in PRIVATE_MAGIC): return 'PRIVATE_BINARY_OR_KEY_CONTENT'
    if prefix.startswith((b'PK\x03\x04',b'7z\xbc\xaf\x27\x1c',b'\x1f\x8b')): return 'ARCHIVE_CONTENT'
    return None


def json_rule(raw):
    if not raw.lstrip().startswith(b'{'):return None
    if len(raw)>8*1024*1024:return 'OVERSIZED_JSON_REQUIRES_REVIEW'
    try:value=json.loads(raw)
    except (ValueError,UnicodeError,RecursionError):return None
    if not isinstance(value,dict):return None
    if value.get('profile')=='nexus.portable-paper-observation.v1' or value.get('mode') in (
        'PORTABLE_OBSERVATION_RETAINED_ACTIVITY_REVIEW','HEAD_BOUND_RETAINED_GROSS_BALANCE_REVIEW','READ_ONLY_PAPER_INSPECTION',
        'RETAINED_FORWARD_OPERATIONAL_COVERAGE_REVIEW','PAPER_ACCOUNT_ACCEPTANCE_PROBE','PAPER_ACCEPTANCE_PACING_STATUS'):
        return 'PRIVATE_FINANCIAL_REPORT'
    return None


def _git(repo,*args):
    result = subprocess.run(['git','-C',str(repo),*args],capture_output=True,check=False,timeout=120)
    if result.returncode or len(result.stdout)>64*1024*1024: raise ValueError('GIT_AUDIT_INCOMPLETE')
    return result.stdout


def audit(repo, *, history=False, required_refs=()):
    repo = Path(repo); findings = []; total = 0; blobs = 0
    if history and _git(repo,'rev-parse','--is-shallow-repository').strip() != b'false': raise ValueError('FULL_HISTORY_REQUIRED')
    for ref in required_refs:
        if not re.fullmatch(r'refs/[A-Za-z0-9_./-]+',ref): raise ValueError('INVALID_REQUIRED_REF')
        _git(repo,'show-ref','--verify',ref)
    paths = set(_git(repo,'ls-files','-z').decode('utf-8').split('\0')) - {''}
    if history: paths |= set(_git(repo,'log','--all','--format=','--name-only','--no-renames','-z').decode('utf-8').split('\0')) - {''}
    for name in sorted(paths):
        rule = path_rule(name)
        if rule: findings.append(dict(rule=rule,path_sha256=hashlib.sha256(name.encode()).hexdigest()))
    objects = _git(repo,'rev-list','--objects','--all') if history else _git(repo,'ls-files','-s')
    ids = set()
    for line in objects.splitlines():
        oid = line.split(b' ',1)[0] if history else line.split()[1]
        if re.fullmatch(rb'[0-9a-f]{40,64}',oid) is None: raise ValueError('INVALID_GIT_OBJECT')
        ids.add(oid)
    if len(ids)>MAX_OBJECTS: raise ValueError('OBJECT_AUDIT_CAP')
    child = subprocess.Popen(['git','-C',str(repo),'cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    try:
        for oid in sorted(ids):
            child.stdin.write(oid+b'\n');child.stdin.flush()
            header=child.stdout.readline().split()
            if len(header)!=3 or header[0]!=oid: raise ValueError('OBJECT_AUDIT_INCOMPLETE')
            size=int(header[2]);total+=size
            if size<0:raise ValueError('INVALID_OBJECT_SIZE')
            if total>MAX_BYTES: raise ValueError('HISTORY_BYTE_CAP')
            remaining=size;prefix=b'';json_blocks=[];is_json=None
            while remaining:
                block=child.stdout.read(min(65536,remaining))
                if not block: raise ValueError('OBJECT_AUDIT_INCOMPLETE')
                if is_json is None:is_json=block.lstrip().startswith(b'{')
                if is_json and size<=8*1024*1024:json_blocks.append(block)
                prefix=(prefix+block[:128])[:128];remaining-=len(block)
            if child.stdout.read(1)!=b'\n': raise ValueError('OBJECT_AUDIT_INCOMPLETE')
            if header[1]!=b'blob':continue
            blobs+=1;rule=content_rule(prefix)
            if not rule and is_json:rule=json_rule(b''.join(json_blocks)) if size<=8*1024*1024 else 'OVERSIZED_JSON_REQUIRES_REVIEW'
            if rule:findings.append(dict(rule=rule,blob_sha=oid.decode()))
        child.stdin.close()
        if child.wait(timeout=15)!=0:raise ValueError('OBJECT_AUDIT_INCOMPLETE')
    finally:
        if child.poll() is None:child.kill();child.wait()
    return dict(mode='PUBLIC_REPOSITORY_ARTIFACT_AUDIT',passed=not findings,history_scanned=history,
        paths_checked=len(paths),blobs_checked=blobs,bytes_checked=total,findings=findings,
        commits_checked=int(_git(repo,'rev-list','--count','--all').strip()) if history else None,
        limitations='Reachable fetched refs only. Not a secret detector, source attestation, privacy guarantee or review of deleted refs, Actions logs/artifacts, issues or external copies.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',default='.');parser.add_argument('--history',action='store_true')
    parser.add_argument('--require-ref',action='append',default=[]);args=parser.parse_args()
    try:report=audit(args.repository,history=args.history,required_refs=args.require_ref)
    except (OSError,ValueError,UnicodeError,subprocess.SubprocessError):
        print('PUBLIC_REPOSITORY_AUDIT_INCOMPLETE');return 2
    print(json.dumps(report,sort_keys=True));return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
