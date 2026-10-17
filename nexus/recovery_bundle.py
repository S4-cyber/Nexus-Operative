"""Offline data-directory snapshots. Close Nexus before using this tool."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from contextlib import closing
from nexus.artifact_manifest import file_entry

MANIFEST = 'nexus-backup-manifest.json'
SCHEMA = 'nexus.offline-backup.v1'


def inventory(root):
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError('BACKUP_SOURCE_MUST_BE_DIRECTORY')
    entries=[]
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise ValueError('BACKUP_SYMLINK_NOT_ALLOWED')
        if path.is_dir():continue
        if path.name.endswith(('-wal','-shm','-journal')):
            raise ValueError('PENDING_SQLITE_SIDECARS_REQUIRE_OFFLINE_REVIEW')
        if not path.is_file():raise ValueError('BACKUP_SPECIAL_FILE_NOT_ALLOWED')
        if path.relative_to(root).as_posix()==MANIFEST:continue
        entry=file_entry(path);entry['name']=path.relative_to(root).as_posix();entries.append(entry)
    if not entries:raise ValueError('BACKUP_SOURCE_EMPTY')
    return entries


def check_sqlite(root,entries):
    for entry in entries:
        path=Path(root)/entry['name']
        if path.suffix.lower() not in ('.sqlite3','.sqlite','.db'):continue
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:
                raise ValueError('BACKUP_SQLITE_INTEGRITY_FAILED')


def verify(root):
    root=Path(root)
    if root.is_symlink() or (root/MANIFEST).is_symlink():raise ValueError('BACKUP_SYMLINK_NOT_ALLOWED')
    document=json.loads((root/MANIFEST).read_text(encoding='utf-8'))
    if not isinstance(document,dict) or document.get('schema')!=SCHEMA:
        raise ValueError('INVALID_BACKUP_MANIFEST')
    # Inventory comparison avoids using untrusted manifest paths to open files.
    actual=inventory(root)
    if actual!=document.get('files'):raise ValueError('BACKUP_CONTENT_DISAGREEMENT')
    check_sqlite(root,actual)
    if inventory(root)!=actual:raise ValueError('BACKUP_CHANGED_DURING_VERIFICATION')
    return document


def _publish(source,destination,copy_manifest):
    if Path(source).is_symlink():raise ValueError('BACKUP_SYMLINK_NOT_ALLOWED')
    source=Path(source).resolve();destination=Path(destination).resolve()
    if destination.exists():raise ValueError('BACKUP_DESTINATION_MUST_BE_NEW')
    if source==destination or source in destination.parents:
        raise ValueError('BACKUP_DESTINATION_INSIDE_SOURCE')
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='nexus-recovery-',dir=destination.parent))
    try:
        before=inventory(source)
        if not copy_manifest and (source/MANIFEST).exists():
            raise ValueError('SOURCE_ALREADY_CONTAINS_BACKUP_MANIFEST')
        for entry in before:
            target=stage/entry['name'];target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source/entry['name'],target)
        if inventory(source)!=before:raise ValueError('SOURCE_CHANGED_DURING_BACKUP')
        if copy_manifest:
            shutil.copy2(source/MANIFEST,stage/MANIFEST)
        else:
            document=dict(schema=SCHEMA,application_closed_confirmation=True,
                          files=before,scope='Offline file copy; no independent attestation, encryption or release approval.')
            (stage/MANIFEST).write_text(json.dumps(document,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        verify(stage)
        # A restore has ordinary application data, not the backup manifest.
        if copy_manifest:(stage/MANIFEST).unlink()
        if destination.exists():raise ValueError('BACKUP_DESTINATION_MUST_BE_NEW')
        os.rename(stage,destination)
        return dict(files=len(before),verified=True,automatic_resume=False)
    finally:
        if stage.exists():shutil.rmtree(stage)


def backup(source,destination,*,application_closed=False):
    if application_closed is not True:raise ValueError('CLOSE_APPLICATION_BEFORE_BACKUP')
    return _publish(source,destination,False)


def restore(source,destination,*,application_closed=False):
    if application_closed is not True:raise ValueError('CLOSE_APPLICATION_BEFORE_RESTORE')
    verify(source)
    return _publish(source,destination,True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    for action in ('backup','restore'):
        command=sub.add_parser(action)
        command.add_argument('source',type=Path);command.add_argument('destination',type=Path)
        command.add_argument('--confirm-application-closed',action='store_true')
    command=sub.add_parser('verify');command.add_argument('source',type=Path)
    args=parser.parse_args()
    try:
        if args.action=='verify':
            document=verify(args.source);result=dict(verified=True,files=len(document['files']))
        else:
            result=globals()[args.action](args.source,args.destination,application_closed=args.confirm_application_closed)
        print(json.dumps(result))
    except (ValueError,OSError,sqlite3.Error,TypeError,KeyError) as exc:
        parser.exit(1,f'Offline recovery failed: {exc}\n')


if __name__=='__main__':main()
