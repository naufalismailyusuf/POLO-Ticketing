"""POLO Email Accounts: server-side cPanel UAPI proxy.

Supported authentication: token (previous revision) or HTTPS Basic with a
Windows DPAPI-encrypted cPanel password (Revisi 58). Never disclose credentials
or store mailbox passwords, account credentials or Webmail sessions in SQLite.
"""
import base64
import html
import json
import os
import re
import sqlite3
import ssl
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen, HTTPSHandler, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError

from flask import Blueprint, Response, abort, g, jsonify, request, send_from_directory

from cpanel_credentials import load_encrypted_password, credential_file_path, CredentialError

_ALLOWED_LOCAL = re.compile(r"^[a-z0-9](?:[a-z0-9._+-]{0,62}[a-z0-9])?$", re.I)
_CPS_TOKEN = re.compile(r"^/?cpsess[0-9]+/?$")
_CPANEL_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
_PENDING = {}
_PENDING_LOCK = threading.Lock()


class MailApiError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    """Never follow redirect with Authorization headers or credential payloads."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _config():
    base = os.getenv('CPANEL_API_BASE', 'https://srv180.niagahoster.com:2083').strip().rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.port != 2083
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise MailApiError('Invalid CPANEL_API_BASE. HTTPS port 2083 is required.', 503)
    method = os.getenv('CPANEL_AUTH_METHOD', 'token').strip().lower()
    if method not in ('token', 'basic'):
        raise MailApiError('Unsupported CPANEL_AUTH_METHOD configuration.', 503)
    user = os.getenv('CPANEL_ACCOUNT_USERNAME', '').strip()
    if user and not _CPANEL_USERNAME.fullmatch(user):
        raise MailApiError('Invalid cPanel account username configuration.', 503)
    return {
        'base': base, 'host': parsed.hostname, 'auth_method': method,
        'user': user, 'token': os.getenv('CPANEL_API_TOKEN', '').strip() if method == 'token' else '',
        'password_file': credential_file_path(),
        'domain': os.getenv('CPANEL_EMAIL_DOMAIN', 'ppg.co.id').strip().lower(),
        'webmail': os.getenv('CPANEL_ALLOW_SUPPORT_WEBMAIL', '0') == '1',
        'webmail_host': os.getenv('CPANEL_WEBMAIL_HOST', '').strip().lower(),
    }


def _is_ready(conf):
    if not conf['user']:
        return False
    if conf['auth_method'] == 'token':
        return bool(conf['token'])
    # The actual DPAPI decryption is performed in _uapi under the service identity.
    return os.path.isfile(conf['password_file'])


def _auth_header(conf):
    if not _is_ready(conf):
        raise MailApiError('cPanel credentials are not configured on the server.', 503)
    if conf['auth_method'] == 'token':
        return 'cpanel ' + conf['user'] + ':' + conf['token']
    try:
        password = load_encrypted_password(conf['password_file'])
    except CredentialError:
        # Do not return OS DPAPI failure details, secret path, or password.
        raise MailApiError('Unable to unlock cPanel credentials. Check the Ticketing service identity.', 503) from None
    if '\r' in password or '\n' in password or '\x00' in password:
        raise MailApiError('Invalid cPanel credentials.', 503)
    return 'Basic ' + base64.b64encode((conf['user'] + ':' + password).encode('utf-8')).decode('ascii')


def _uapi(module, operation, fields=None):
    conf = _config()
    if module not in ('Email', 'Session') or operation not in (
        'list_pops_with_disk', 'list_pops', 'add_pop', 'passwd_pop',
        'edit_pop_quota', 'create_webmail_session_for_mail_user'):
        raise MailApiError('Unsupported operation.', 400)
    encoded = urlencode(fields or {}).encode('utf-8')
    req = Request(
        conf['base'] + '/execute/' + module + '/' + operation,
        data=encoded,
        headers={
            'Authorization': _auth_header(conf),
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'POLO-Ticketing/EmailTools',
        }, method='POST')
    try:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # Explicit NoRedirect prevents cross-origin or HTTPS->HTTP Authorization leakage.
        opener = build_opener(HTTPSHandler(context=context), _NoRedirect())
        with opener.open(req, timeout=15) as resp:
            raw = resp.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise MailApiError('cPanel response exceeded the size limit.', 502)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise MailApiError('cPanel authentication failed or the account lacks permission.', 502) from None
        if 300 <= exc.code < 400:
            raise MailApiError('cPanel redirected the API request. Redirects are blocked for security.', 502) from None
        raise MailApiError('cPanel returned HTTP ' + str(exc.code) + '.', 502) from None
    except (URLError, TimeoutError, OSError, ssl.SSLError):
        raise MailApiError('Cannot connect to cPanel securely.', 503) from None
    try:
        doc = json.loads(raw)
        obj = doc.get('result', doc)
        if not isinstance(obj, dict):
            raise ValueError('bad result')
        if obj.get('status') not in (1, True, '1'):
            raise MailApiError('cPanel rejected this operation. Check account permissions and inputs.', 422)
        return obj.get('data')
    except MailApiError:
        raise
    except (ValueError, TypeError, AttributeError):
        raise MailApiError('Unexpected cPanel API response.', 502) from None


def _local_address(value, domain):
    value = str(value or '').strip().lower()
    local = value[:-len(domain)-1] if value.endswith('@' + domain) else value
    if '@' in local or len(local) > 64 or not _ALLOWED_LOCAL.fullmatch(local):
        raise MailApiError('Invalid email username.', 400)
    if '..' in local or local.startswith('.') or local.endswith('.'):
        raise MailApiError('Invalid email username.', 400)
    return local, local + '@' + domain


def _quota(value):
    try:
        num = int(value)
    except (TypeError, ValueError):
        raise MailApiError('Quota must be a number of MB.', 400) from None
    if num < 0 or num > 102400:
        raise MailApiError('Quota must be between 0 and 102400 MB.', 400)
    return num


def _password(value):
    if not isinstance(value, str) or not (12 <= len(value) <= 128):
        raise MailApiError('Password must be 12–128 characters.', 400)
    if '\x00' in value or '\n' in value or '\r' in value:
        raise MailApiError('Invalid password characters.', 400)
    return value


def _access(admin_only=False):
    user = getattr(g, 'user', None)
    if user is None:
        abort(401)
    role = user['role']
    if admin_only and role != 'admin':
        abort(403)
    if role not in ('admin', 'support'):
        abort(403)
    return user


def _limit_operation(user, action):
    # Avoid accidental repeated account creations or API password resets.
    now = time.monotonic()
    key = (user['username'], action)
    with _PENDING_LOCK:
        stamps = [t for t in _PENDING.get(key, ()) if now - t < 3600]
        max_calls = {'create': 10, 'password': 15, 'quota': 30, 'check_mail': 30}.get(action, 60)
        if len(stamps) >= max_calls:
            raise MailApiError('Hourly safety limit reached. Contact administrator.', 429)
        stamps.append(now)
        _PENDING[key] = stamps
        if len(_PENDING) > 500:
            for k in list(_PENDING):
                _PENDING[k] = [t for t in _PENDING[k] if now - t < 3600]
                if not _PENDING[k]:
                    del _PENDING[k]


def register_email_tools(app, db_path, static_pages_dir):
    bp = Blueprint('email_tools', __name__)
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS email_activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            actor_username TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            action TEXT NOT NULL,
            mailbox TEXT NOT NULL,
            success INTEGER NOT NULL,
            result_code TEXT NOT NULL,
            client_ip TEXT
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_email_activity_when ON email_activity_log(id DESC)')

    def audit(user, action, mailbox, success, result_code):
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute('''INSERT INTO email_activity_log
              (created_at,actor_username,actor_role,action,mailbox,success,result_code,client_ip)
              VALUES (?,?,?,?,?,?,?,?)''',
                (datetime.now(timezone.utc).isoformat(timespec='seconds'),
                 user['username'], user['role'], action, mailbox, int(success),
                 result_code, (request.remote_addr or '')[:45]))

    def run_action(action, username, fn):
        user = _access()
        conf = _config()
        local, mailbox = _local_address(username, conf['domain'])
        try:
            _limit_operation(user, action)
            data = fn(local, conf)
            audit(user, action, mailbox, True, 'success')
            return data
        except MailApiError as exc:
            audit(user, action, mailbox, False, 'denied' if exc.status in (400, 403, 429) else 'remote_error')
            raise

    @bp.route('/tools/email')
    def page():
        _access()
        return send_from_directory(static_pages_dir, 'email_tools.html')

    @bp.get('/api/tools/email/config')
    def config():
        _access()
        try:
            conf = _config()
            return jsonify(ok=True, domain=conf['domain'], ready=_is_ready(conf),
                           check_mail_enabled=bool(conf['webmail'] or g.user['role'] == 'admin'))
        except (MailApiError, CredentialError) as exc:
            return jsonify(ok=False, error=str(exc)), getattr(exc, 'status', 503)

    @bp.get('/api/tools/email/accounts')
    def accounts():
        _access()
        try:
            conf = _config()
            data = _uapi('Email', 'list_pops_with_disk', {'domain': conf['domain'], 'skip_main': '1'})
            if not isinstance(data, list):
                raise MailApiError('Account list has an unexpected format.', 502)
            records = []
            for row in data:
                if not isinstance(row, dict):
                    continue
                address = str(row.get('email') or row.get('login') or '').strip().lower()
                if not address.endswith('@' + conf['domain']):
                    continue
                records.append({
                    'email': address,
                    'used': str(row.get('humandiskused') or '—'),
                    'quota': str(row.get('humandiskquota') or 'Unlimited'),
                    'percent': min(100, max(0, float(row.get('diskusedpercent20') or 0))),
                    'suspended': bool(row.get('suspended_login') or False),
                })
            return jsonify(ok=True, accounts=records)
        except MailApiError as exc:
            return jsonify(ok=False, error=str(exc)), exc.status

    @bp.post('/api/tools/email/accounts')
    def create():
        payload = request.get_json(silent=True) or {}
        try:
            def perform(local, conf):
                return _uapi('Email', 'add_pop', {
                    'email': local, 'domain': conf['domain'],
                    'password': _password(payload.get('password')),
                    'quota': _quota(payload.get('quota', 1024)),
                })
            run_action('create', payload.get('email'), perform)
            return jsonify(ok=True, message='Email account created.'), 201
        except MailApiError as exc:
            return jsonify(ok=False, error=str(exc)), exc.status

    @bp.post('/api/tools/email/accounts/password')
    def change_password():
        payload = request.get_json(silent=True) or {}
        try:
            def perform(local, conf):
                return _uapi('Email', 'passwd_pop', {
                    'email': local, 'domain': conf['domain'],
                    'password': _password(payload.get('password')),
                })
            run_action('password', payload.get('email'), perform)
            return jsonify(ok=True, message='Email password updated.')
        except MailApiError as exc:
            return jsonify(ok=False, error=str(exc)), exc.status

    @bp.post('/api/tools/email/accounts/quota')
    def update_quota():
        payload = request.get_json(silent=True) or {}
        try:
            def perform(local, conf):
                return _uapi('Email', 'edit_pop_quota', {
                    'email': local, 'domain': conf['domain'],
                    'quota': _quota(payload.get('quota')),
                })
            run_action('quota', payload.get('email'), perform)
            return jsonify(ok=True, message='Email quota updated.')
        except MailApiError as exc:
            return jsonify(ok=False, error=str(exc)), exc.status

    @bp.post('/tools/email/check-mail')
    def check_mail():
        user = _access()
        conf = _config()
        if user['role'] != 'admin' and not conf['webmail']:
            abort(403)
        try:
            def perform(local, conf):
                data = _uapi('Session', 'create_webmail_session_for_mail_user',
                             {'domain': conf['domain'], 'login': local})
                if not isinstance(data, dict):
                    raise MailApiError('Unable to create Webmail session.', 502)
                token = str(data.get('token') or '').strip('/')
                one_time = str(data.get('session') or '')
                cp_host = conf['host']
                hostname = str(data.get('hostname') or cp_host).lower()
                if hostname not in {cp_host, conf['webmail_host']} or not _CPS_TOKEN.fullmatch(token) or not one_time or len(one_time) > 2000:
                    raise MailApiError('Invalid Webmail session response.', 502)
                return (f'https://{hostname}:2096/{token}/login', one_time, hostname)
            target, one_time, hostname = run_action('check_mail', request.form.get('email'), perform)
            # cPanel requires POST of a temporary session token; never return API token.
            body = ("<!doctype html><html><head><meta charset='utf-8'>"
                    "<title>Opening Webmail</title></head><body><p>Opening Webmail…</p>"
                    f"<form id='s' action='{html.escape(target, quote=True)}' method='post'>"
                    f"<input type='hidden' name='session' value='{html.escape(one_time, quote=True)}'>"
                    "<noscript><button type='submit'>Continue to Webmail</button></noscript>"
                    "</form><script>document.getElementById('s').submit()</script></body></html>")
            resp = Response(body, content_type='text/html; charset=utf-8')
            resp.headers['Cache-Control'] = 'no-store, no-cache, private'
            resp.headers['Referrer-Policy'] = 'no-referrer'
            resp.headers['X-Frame-Options'] = 'DENY'
            resp.headers['Content-Security-Policy'] = f"default-src 'none'; script-src 'unsafe-inline'; form-action https://{hostname}:2096; base-uri 'none'"
            return resp
        except MailApiError as exc:
            return Response('Webmail is unavailable: ' + html.escape(str(exc)), status=exc.status, content_type='text/plain; charset=utf-8')

    @bp.get('/admin/email-activity')
    def audit_page():
        _access(admin_only=True)
        return send_from_directory(static_pages_dir, 'email_activity.html')

    @bp.get('/api/admin/email-activity')
    def audit_list():
        _access(admin_only=True)
        try:
            page = max(1, min(10000, int(request.args.get('page', '1'))))
        except ValueError:
            page = 1
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute('SELECT count(*) FROM email_activity_log').fetchone()[0]
            rows = conn.execute('''SELECT id,created_at,actor_username,actor_role,
                                         action,mailbox,success,result_code,client_ip
                                  FROM email_activity_log ORDER BY id DESC LIMIT 50 OFFSET ?''', ((page-1)*50,)).fetchall()
        return jsonify(ok=True, total=total, page=page, rows=[dict(r) for r in rows])

    app.register_blueprint(bp)
