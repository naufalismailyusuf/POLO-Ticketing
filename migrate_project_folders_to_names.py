"""Optional migration of legacy numeric Our Project folders to readable QNAP names.

Run with Ticketing service STOPPED.  --check never writes; --apply copies files,
verifies SHA-256, then updates the project's immutable storage_folder field.
It does not delete old NAS/local data, and never overwrites different content.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import uuid

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None
from dashboard_upload_storage import DashboardUploadStorage, QnapUploadsUnavailable

HERE = Path(__file__).resolve().parent


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true', help='Dry run; read only')
    group.add_argument('--apply', action='store_true', help='Copy, verify, then update SQLite')
    args = parser.parse_args()
    if load_dotenv:
        load_dotenv(HERE / '.env')
    storage = DashboardUploadStorage(
        os.environ.get('DASHBOARD_QNAP_SHARE', r'\\172.16.0.74\IT'),
        HERE / 'uploads',
        secondary_share=os.environ.get('DASHBOARD_QNAP_SECONDARY_SHARE', r'\\172.16.0.99\IT'),
        private_relative=r'POLO\root_dashboard',
        username=os.environ.get('FILE_CENTER_QNAP_USERNAME', '').strip(),
        password=os.environ.get('FILE_CENTER_QNAP_PASSWORD', ''),
        domain=os.environ.get('FILE_CENTER_QNAP_DOMAIN', '').strip())
    root = storage.active_root()
    db_file = HERE / 'tickets.db'
    if not db_file.is_file():
        parser.error('tickets.db not found. Run from a valid Ticketing installation.')
    conn = sqlite3.connect(db_file.as_uri() + '?mode=ro', uri=True, timeout=30) if args.check else sqlite3.connect(db_file, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(team_projects)')}
        if 'storage_folder' not in columns:
            parser.error('Install Revisi 13 and start Ticketing once to initialize the schema.')
        projects = conn.execute('SELECT id,title,storage_folder FROM team_projects ORDER BY id').fetchall()
        total = done = 0
        for p in projects:
            if p['storage_folder']:
                print(f"SKIP P{p['id']}: already uses {p['storage_folder']}")
                continue
            pid = p['id']
            name = storage.project_folder_name(p['title'], pid)
            target = root / 'Project_Discussion' / name
            attachments = conn.execute(
                'SELECT stored_name FROM team_project_comment_attachments WHERE project_id=? ORDER BY id',
                (pid,)).fetchall()
            source_files = []
            missing = []
            for a in attachments:
                filename = storage._name(a['stored_name'])
                source = storage.resolve('project', filename, pid=pid)
                if source is None:
                    missing.append(filename)
                else:
                    source_files.append((source, target / filename))
            print(f"P{pid}: {p['title']!r} => {target} ({len(source_files)} files, {len(missing)} missing)")
            if missing:
                print('  BLOCKED: missing source attachments:', ', '.join(missing[:8]))
                if args.apply:
                    raise RuntimeError(f'Missing attachments for project {pid}; no project path was switched')
                continue
            if args.check:
                total += 1
                continue
            # Fail closed on unexpected preexisting directory contents.
            if target.exists():
                names = {src.name for src, _ in source_files}
                unexpected = [x.name for x in target.iterdir() if not x.is_file() or x.name not in names]
                if unexpected:
                    raise RuntimeError(f'Unrecognized files in destination {target}: {unexpected[:5]}')
            else:
                target.mkdir(parents=True, exist_ok=False)
            for source, destination in source_files:
                source_hash = digest(source)
                if destination.exists():
                    if digest(destination) != source_hash:
                        raise RuntimeError(f'Destination collision (different bytes): {destination}')
                    continue
                tmp = destination.parent / ('.migrate-' + uuid.uuid4().hex + '.part')
                try:
                    shutil.copyfile(source, tmp)
                    if digest(tmp) != source_hash:
                        raise OSError('SHA-256 verification failed while copying ' + str(source))
                    tmp.replace(destination)
                finally:
                    tmp.unlink(missing_ok=True)
                if digest(destination) != source_hash:
                    raise OSError('SHA-256 verification failed for ' + str(destination))
            # DB flip only after every attachment was copied and verified.
            with conn:
                conn.execute('UPDATE team_projects SET storage_folder=? '
                             'WHERE id=? AND storage_folder IS NULL', (name, pid))
                if conn.execute('SELECT changes()').fetchone()[0] != 1:
                    raise RuntimeError(f'Project {pid} was changed concurrently; stop Ticketing before retry')
            done += 1
        print(('Checked' if args.check else 'Migrated'), (total if args.check else done), 'projects')
        if args.check:
            print('Read-only check complete. Stop Ticketing and backup tickets.db before --apply.')
        else:
            print('Copies verified. Original numeric QNAP folders and local files were NOT deleted.')
    finally:
        conn.close()


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print('MIGRATION FAILED:', exc, file=sys.stderr)
        sys.exit(1)
