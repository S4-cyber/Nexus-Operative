"""Checksums for reviewed distribution files; no signing or release authority."""
import argparse
import hashlib
import json
from pathlib import Path


def file_entry(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return dict(name=path.name, bytes=path.stat().st_size, sha256=digest.hexdigest())


def manifest(paths, version, commit):
    entries = sorted((file_entry(p) for p in paths), key=lambda x: x['name'])
    if not entries or len({e['name'] for e in entries}) != len(entries):
        raise ValueError('ARTIFACT_NAMES_MUST_BE_NONEMPTY_AND_UNIQUE')
    return dict(schema='nexus.artifacts.v1', version=version, source_commit=commit,
                signed=False, release_approved=False, artifacts=entries)


def verify(document, directory):
    if document.get('schema') != 'nexus.artifacts.v1' or not document.get('artifacts'):
        raise ValueError('INVALID_ARTIFACT_MANIFEST')
    seen = set()
    for entry in document['artifacts']:
        name = entry['name']
        if not isinstance(name,str) or name in ('.','..') or '/' in name or '\\' in name or ':' in name or name in seen:
            raise ValueError('INVALID_ARTIFACT_NAME')
        seen.add(name)
        if file_entry(Path(directory) / name) != entry:
            raise ValueError('ARTIFACT_DIGEST_DISAGREEMENT')
    return dict(artifact_integrity_ok=True, signed=False, release_approved=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('create')
    create.add_argument('--version', required=True)
    create.add_argument('--commit', required=True)
    create.add_argument('--output', type=Path, required=True)
    create.add_argument('files', type=Path, nargs='+')
    check = sub.add_parser('verify')
    check.add_argument('manifest', type=Path)
    check.add_argument('directory', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'create':
            result = manifest(args.files,args.version,args.commit)
            args.output.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        else:
            print(json.dumps(verify(json.loads(args.manifest.read_text(encoding='utf-8')),args.directory)))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        parser.exit(1,f'Artifact check failed: {exc}\n')


if __name__ == '__main__':
    main()
