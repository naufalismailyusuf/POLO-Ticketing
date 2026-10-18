"""Revisi 61: register existing cPanel Email Management blueprint safely.

Usage:
  python scripts/fix_email_route.py           # read-only inspection
  python scripts/fix_email_route.py --apply   # timestamped backup then minimal edit

Works on installed project, never replaces app.py with an older revision.
"""
import argparse
import datetime
import hashlib
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / 'app.py'
MODULE = ROOT / 'cpanel_mail.py'
PAGE = ROOT / 'static' / 'pages' / 'email_tools.html'
ANCHOR = 'register_guides(app, db, STATIC_PAGE_DIR, upload_storage=DASHBOARD_UPLOAD_STORAGE)'
REGISTRATION = (
    '\n# Revisi 61: register cPanel Email Accounts routes (backend).\n'
    'from cpanel_mail import register_email_tools\n'
    'register_email_tools(app, DB, STATIC_PAGE_DIR)\n'
)


def main():
    parser=argparse.ArgumentParser(description='Safe backend route repair for Tools > Email Accounts')
    parser.add_argument('--apply', action='store_true', help='Backup app.py then add missing blueprint registration')
    args=parser.parse_args()

    required = (APP, MODULE, PAGE)
    missing = [str(p.relative_to(ROOT)) for p in required if not p.is_file()]
    if missing:
        print('STOP: File yang dibutuhkan tidak ditemukan: ' + ', '.join(missing))
        print('Pasang file modul Email Management dari Revisi 57/58 terlebih dahulu.')
        return 2

    original = APP.read_bytes()
    try:
        text = original.decode('utf-8-sig')
        # Verify that input is a valid Python file.
        compile(text, str(APP), 'exec')
    except (UnicodeError, SyntaxError) as exc:
        print('STOP: app.py tidak dapat dibaca/validasi sintaks gagal:', exc)
        return 2

    has_import = 'from cpanel_mail import register_email_tools' in text
    has_registration = 'register_email_tools(app, DB, STATIC_PAGE_DIR)' in text
    if has_import and has_registration:
        print('OK: Registrasi Email Tools sudah ada pada app.py.')
        print('Jika masih 404, periksa proses Flask aktif, lokasi deployment, dan routing proxy. Jangan jalankan patch ulang.')
        return 0
    if has_import != has_registration:
        print('STOP: Ditemukan registrasi cPanel parsial; diperlukan pemeriksaan manual agar tidak menduplikasi route.')
        return 2

    if text.count(ANCHOR) != 1:
        print('STOP: Anchor register_guides tidak unik/berbeda, app.py tidak diubah.')
        return 2
    pos = text.index(ANCHOR) + len(ANCHOR)
    line_end = text.find('\n', pos)
    if line_end < 0:
        print('STOP: Struktur app.py tidak sesuai; app.py tidak diubah.')
        return 2
    suffix = text[line_end+1:]
    if '_log_registered_routes()' not in suffix:
        print('STOP: Tidak ditemukan route logger setelah register_guides; app.py tidak diubah.')
        return 2
    if 'register_email_tools(' in text:
        print('STOP: Ditemukan pemanggilan register_email_tools lain; periksa secara manual.')
        return 2

    changed = text[:line_end+1] + REGISTRATION + text[line_end+1:]
    try:
        compile(changed, str(APP), 'exec')
    except SyntaxError as exc:
        print('STOP: Pemeriksaan sintaks hasil perubahan gagal:', exc)
        return 2

    if not args.apply:
        print('READY: app.py belum mendaftarkan /tools/email. Tidak ada file yang diubah.')
        print('Jalankan dengan --apply untuk membuat backup dan menambahkan registrasi route.')
        return 0

    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = APP.with_name('app.py.bak-r61-' + stamp)
    if backup.exists():
        print('STOP: Backup dengan timestamp yang sama sudah ada:', backup)
        return 2
    backup.write_bytes(original)
    if hashlib.sha256(backup.read_bytes()).digest() != hashlib.sha256(original).digest():
        print('STOP: Verifikasi backup gagal. app.py tidak diubah.')
        return 2
    tmp = APP.with_name('app.py.r61.tmp')
    if tmp.exists():
        print('STOP: File sementara sudah ada, app.py tidak diubah:', tmp)
        return 2
    try:
        # Encode identically if file originally contained a UTF-8 BOM.
        candidate = changed.encode('utf-8-sig' if original.startswith(b'\xef\xbb\xbf') else 'utf-8')
        with tmp.open('xb') as handle:
            handle.write(candidate)
        os.chmod(tmp, APP.stat().st_mode)
        if tmp.read_bytes() != candidate:
            raise RuntimeError('Verifikasi write temp gagal')
        os.replace(tmp, APP)
    except Exception as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        print('GAGAL: app.py tidak diubah (atau penggantian tidak selesai):', exc)
        print('Backup aman di:', backup)
        return 3
    print('SUCCESS: registrasi blueprint /tools/email ditambahkan.')
    print('BACKUP:', backup)
    print('NEXT: restart aplikasi Flask/Ticketing, kemudian akses Tools > Email Accounts.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
