"""Non-destructive migration of legacy Ticketing/uploads files to QNAP.

Default: --check / dry run. --copy requires --confirm-copy.
No action deletes source files, changes SQLite, or overwrites different data.
Run this script from the Windows server account that runs Ticketing.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
from secrets import token_hex

BASE = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / '.env', override=False)
except ImportError:
    pass

from dashboard_upload_storage import (DashboardUploadStorage, PHOTO_FOLDER,
                                      DISCUSSION_FOLDER, PRIVATE_FOLDER)

PHOTO_PATTERN = re.compile(r'^[0-9a-f]{32}\.(?:jpg|png|gif|webp)$', re.I)
FILE_PATTERN = re.compile(r'^[0-9a-f]{32}\.[A-Za-z0-9]{1,8}$')


def digest(path):
    hash_obj = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            hash_obj.update(block)
    return hash_obj.hexdigest()


def candidates(legacy):
    if legacy.is_dir():
        for source in sorted(legacy.iterdir()):
            if source.is_file() and PHOTO_PATTERN.fullmatch(source.name):
                yield source, Path(PHOTO_FOLDER) / source.name
    projects = legacy / 'project_comments'
    if projects.is_dir():
        for directory in sorted(projects.iterdir()):
            if not directory.is_dir() or not directory.name.isdigit() or int(directory.name) <= 0:
                continue
            for source in sorted(directory.iterdir()):
                if source.is_file() and FILE_PATTERN.fullmatch(source.name):
                    yield source, Path(DISCUSSION_FOLDER) / directory.name / source.name


def transfer(src, dst):
    tmp = dst.with_name('.' + token_hex(12) + '.part')
    try:
        with src.open('rb') as reader, tmp.open('xb') as writer:
            shutil.copyfileobj(reader, writer, length=4 * 1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if src.stat().st_size != tmp.stat().st_size or digest(src) != digest(tmp):
            raise OSError(f'Copy verification failed: {src.name}')
        if dst.exists():
            raise FileExistsError(f'Target already exists: {dst}')
        os.replace(tmp, dst)
        if digest(src) != digest(dst):
            raise OSError(f'Published copy checksum mismatch: {dst}')
    finally:
        tmp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Report expected destination, source counts and conflicts (default)')
    parser.add_argument('--copy', action='store_true', help='Copy verified files to QNAP (never delete local)')
    parser.add_argument('--confirm-copy', action='store_true', help='Explicitly acknowledge non-destructive QNAP file copy')
    parser.add_argument('--verify', action='store_true', help='Verify all known local source checksums against QNAP copies')
    args = parser.parse_args()
    if args.copy and not args.confirm_copy:
        parser.error('--copy requires --confirm-copy')

    share = os.environ.get('DASHBOARD_QNAP_SHARE', r'\\172.16.0.74\IT').strip()
    secondary = os.environ.get('DASHBOARD_QNAP_SECONDARY_SHARE', r'\\172.16.0.99\IT').strip()
    legacy = BASE / 'uploads'
    store = DashboardUploadStorage(share, legacy, secondary_share=secondary,
        private_relative=r'POLO\root_dashboard',
        username=os.environ.get('FILE_CENTER_QNAP_USERNAME', '').strip(),
        password=os.environ.get('FILE_CENTER_QNAP_PASSWORD', ''),
        domain=os.environ.get('FILE_CENTER_QNAP_DOMAIN', '').strip())
    print('Local source:', legacy)
    print('QNAP primary root:', store.root)
    print('QNAP secondary root:', store.roots[1] if len(store.roots)>1 else 'None')
    print('Action:', 'COPY + SHA256 verification' if args.copy else 'VERIFY ONLY' if args.verify else 'DRY RUN')
    try:
        active_root = store.active_root()
    except OSError as exc:
        raise SystemExit('STOP: ' + str(exc))
    print('Access route:', 'primary' if active_root == store.root else 'secondary')
    rows = list(candidates(legacy))
    summary = {'copy': 0, 'present': 0, 'conflict': 0, 'missing': 0}
    for src, relative in rows:
        dst = active_root / relative
        try:
            if dst.is_file():
                if src.stat().st_size == dst.stat().st_size and digest(src) == digest(dst):
                    summary['present'] += 1
                    continue
                summary['conflict'] += 1
                print('CONFLICT (not overwritten):', relative)
                continue
            if args.verify:
                summary['missing'] += 1
                print('MISSING:', relative)
                continue
            if args.copy:
                dst.parent.mkdir(parents=True, exist_ok=True)
                transfer(src, dst)
                summary['copy'] += 1
                print('COPIED + VERIFIED:', relative)
            else:
                summary['missing'] += 1
                print('WOULD COPY:', relative)
        except OSError as exc:
            summary['conflict'] += 1
            print('ERROR:', relative, exc)
    print('Total local files:', len(rows), '|', ', '.join(f'{k}={v}' for k, v in summary.items()))
    print('Local files kept untouched. Database was not modified.')
    if summary['conflict'] or (args.verify and summary['missing']):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
