import csv
import gzip
import io
import json
import math
import ctypes
from ctypes import wintypes
import mimetypes
import ntpath
import os
import re
import secrets
import shutil
import sqlite3
import stat
import time
import threading
import tempfile
import logging
import sys
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except Exception:
    FileSystemEventHandler = None
    Observer = None
from urllib.parse import quote_plus, unquote, quote

from flask import (Flask, Response, abort, flash, g, get_flashed_messages, jsonify, redirect,
                   request, send_file, send_from_directory,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import ClientDisconnected, RequestEntityTooLarge
from markupsafe import escape
from html import escape as html_escape
from auth_session_policy import (ABSOLUTE_TIMEOUT_SECONDS, IDLE_TIMEOUT_SECONDS,
                                 remaining_seconds as auth_remaining_seconds,
                                 expiry_payload as auth_expiry_payload)
try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC_PAGE_DIR = os.path.join(BASE, "static", "pages")
if load_dotenv:
    load_dotenv(os.path.join(BASE, ".env"), override=False)
DB = os.path.join(BASE, "tickets.db")
UPLOAD_DIR = os.path.join(BASE, "uploads")  # legacy read-only sources until verified migration
# Keep File Center unchanged; dashboard uploads use their own private QNAP subfolder.
from dashboard_upload_storage import DashboardUploadStorage, PRIVATE_FOLDER as DASHBOARD_PRIVATE_FOLDER
# Verified storage: same QNAP share accessible through both interfaces.
# Unlike File Center, dashboard uploads always go under POLO\root_dashboard.
DASHBOARD_QNAP_SHARE = os.environ.get("DASHBOARD_QNAP_SHARE", r"\\172.16.0.74\IT").strip()
DASHBOARD_QNAP_SECONDARY_SHARE = os.environ.get("DASHBOARD_QNAP_SECONDARY_SHARE", r"\\172.16.0.99\IT").strip()
DASHBOARD_QNAP_RELATIVE_ROOT = r"POLO\root_dashboard"
DASHBOARD_UPLOAD_STORAGE = DashboardUploadStorage(
    DASHBOARD_QNAP_SHARE, UPLOAD_DIR,
    secondary_share=DASHBOARD_QNAP_SECONDARY_SHARE,
    private_relative=DASHBOARD_QNAP_RELATIVE_ROOT,
    username=os.environ.get("FILE_CENTER_QNAP_USERNAME", "").strip(),
    password=os.environ.get("FILE_CENTER_QNAP_PASSWORD", ""),
    domain=os.environ.get("FILE_CENTER_QNAP_DOMAIN", "").strip(),
)
# Streaming uploads already finish on File Center QNAP. Only their temporary
# chunks are kept outside the application tree and cleaned after each request.
TEMP_UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "Ticketing-upload-temp")

# File Center storage is on QNAP. Use direct UNC access so the Flask service
# never depends on a mapped drive letter. The service account should have
# SMB read/write permissions on this share. Optional credentials are loaded
# only from environment variables and are used to establish the SMB session
# when needed.
QNAP_SHARE = os.environ.get("FILE_CENTER_QNAP_SHARE", r"\\172.16.0.74\IT").strip()
# Secondary is used only for authenticated read-only download failover. Never
# use it for uploads, metadata writes, deletions, or faster "striped" reads.
QNAP_READ_SECONDARY_SHARE = os.environ.get(
    "FILE_CENTER_QNAP_SECONDARY_SHARE", DASHBOARD_QNAP_SECONDARY_SHARE
).strip()
FILE_STORAGE_DIR = QNAP_SHARE
QNAP_USERNAME = os.environ.get("FILE_CENTER_QNAP_USERNAME", "").strip()
QNAP_PASSWORD = os.environ.get("FILE_CENTER_QNAP_PASSWORD", "")
QNAP_DOMAIN = os.environ.get("FILE_CENTER_QNAP_DOMAIN", "").strip()
QNAP_POLL_SECONDS = max(10, min(30, int(os.environ.get("FILE_CENTER_POLL_SECONDS", "30") or 30)))
# Ticket progress evidence is private; File Center remains on the public IT share.
# Existing tickets under IT\TICKETS remain readable through legacy paths.
TICKET_EVIDENCE_RELATIVE_ROOT = "POLO/root_dashboard/TICKETS"
TICKET_LEGACY_RELATIVE_ROOT = "TICKETS"

QNAP_SYSTEM_NAMES = {
    DASHBOARD_PRIVATE_FOLDER.casefold(),  # private app uploads; never indexed in File Center
    "@recycle", ".@__thumb", ".@__thumbnail", "@recently-snapshot",
    "@sharebin", "@eaDir", ".snapshot", "@synologydrive", ".qsync",
}
QNAP_SYSTEM_PREFIXES = ("@recycle", ".@__thumb", ".@__thumbnail", "@recently-snapshot", "@sharebin")
QNAP_SYNC_LOCK = threading.RLock()
QNAP_LAST_SYNC = 0.0
QNAP_SYNC_DIRTY = True
QNAP_WATCHER = None
QNAP_LIST_CACHE_TTL = 15.0
QNAP_LIST_CACHE = {}
QNAP_LIST_CACHE_LOCK = threading.RLock()
# REVISI-1: serialize only the QNAP upload finalization step to avoid filename/DB races.
QNAP_UPLOAD_FINALIZE_LOCK = threading.RLock()
QUERY_CACHE_TTL = 20.0
QUERY_CACHE = {}
QUERY_CACHE_LOCK = threading.RLock()

os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)
# Remove only abandoned Ticketing temporary parts older than 48 hours;
# never scan or clean permanent QNAP content.
try:
    stale_before = time.time() - (48 * 60 * 60)
    for temp_entry in os.scandir(TEMP_UPLOAD_DIR):
        if (temp_entry.is_file(follow_symlinks=False) and temp_entry.name.endswith(".part")
                and temp_entry.name.startswith((".ticket-upload-", ".ticket-async-", ".file-center-"))
                and temp_entry.stat(follow_symlinks=False).st_mtime < stale_before):
            try:
                os.remove(temp_entry.path)
            except OSError:
                pass
except OSError:
    pass
MAX_FOTO = 10 * 1024 * 1024  # 10 MB
FILE_MAX_SIZE = 8 * 1024 * 1024 * 1024  # 8 GB per library file
COMMENT_MAX_FILES = 4
COMMENT_MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1 GB per ticket progress attachment
TICKET_STATUSES = ["Open", "In Progress", "Waiting for User", "Resolved", "Closed"]
COMPLETED_STATUSES = {"Resolved", "Closed", "Done"}
CLOSED_STATUSES = {"Closed", "Done"}
LOCAL_TZ = ZoneInfo("Asia/Jakarta")
TIMEZONE_LABEL = "WIB"

SUPPORT_USERS = ["Nathan", "Naufal", "Viky", "Sabrina"]
UNASSIGNED = "Unassigned"
SUPPORT_STATUS_FILTERS = {
    "all": None,
    "Open": {"Open"},
    "In Progress": {"In Progress", "Waiting for User"},
    "Resolved": {"Resolved", "Closed", "Done"},
}
PRIORITIES = ["Critical", "High", "Medium", "Low"]
CATEGORIES = ["Hardware", "Software", "Network", "Printer", "Account", "Email", "Other"]
PRIORITY_WEIGHT = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
SUPPORT_COLORS = {
    "Nathan": "#6366f1",
    "Naufal": "#10b981",
    "Viky": "#f59e0b",
    "Sabrina": "#ec4899",
    UNASSIGNED: "#94a3b8",
}
DASHBOARD_QUEUE_PREVIEW_LIMIT = None  # Unlimited; dashboard queue uses a bounded scroll area.

SEED_USERS = [
    ("admin", "Admin", "admin",
     "pbkdf2:sha256:1000000$hiluOhnq0n849yY3$3bbdba1e5f1335ee1c97d88ee7297ef93ab864da4bcb7377f7b87e4e062c2189"),
    ("nathan", "Nathan", "support",
     "pbkdf2:sha256:1000000$7c55e63c0305a186$6dd3a1b0673ada46ea1fa70dc4a8e6233808e00b73e3d68fc635d66e5b093e94"),
    ("naufal", "Naufal", "support",
     "pbkdf2:sha256:1000000$telzoG4AloAw3cqQ$65fc374eb4402787008da08b5518f8d04d0abea95cff90414ae72b9eeb447113"),
    ("viky", "Viky", "support",
     "pbkdf2:sha256:1000000$zKxcZ7zEcd2ZwiX5$94f43b080854bf03e219153fe11d415d5fa5b29ec0cd802547441039826200de"),
    ("sabrina", "Sabrina", "support",
     "pbkdf2:sha256:1000000$Sabrina2026$83d64a07a2db68c9e503eddff25441cae06eb59d06432aed95f23c3de887d13d"),
]

app = Flask(__name__)

# ---------- logging ----------
LOG_DIR = os.path.join(BASE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "app.log")

_log_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Configure only once so Flask/Waitress reloads do not duplicate log lines.
if not any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == os.path.abspath(LOG_FILE)
           for h in app.logger.handlers):
    _file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    _file_handler.setFormatter(_log_formatter)
    app.logger.addHandler(_file_handler)

if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
           for h in app.logger.handlers):
    _console_handler = logging.StreamHandler(sys.stdout)
    _console_handler.setFormatter(_log_formatter)
    app.logger.addHandler(_console_handler)

app.logger.setLevel(logging.INFO)
app.logger.propagate = False

# Capture fatal process/thread exceptions so an NSSM/Waitress restart does not
# leave the cause invisible. The traceback is written to logs/app.log.
def _log_fatal_exception(exc_type, exc_value, exc_traceback):
    if exc_type is KeyboardInterrupt:
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    app.logger.critical('FATAL PROCESS EXCEPTION', exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = _log_fatal_exception
if hasattr(threading, 'excepthook'):
    def _log_fatal_thread_exception(args):
        app.logger.critical(
            'FATAL THREAD EXCEPTION thread=%s',
            getattr(args.thread, 'name', 'unknown'),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
    threading.excepthook = _log_fatal_thread_exception

app.logger.info(
    'Application bootstrap: pid=%s python=%s cwd=%s base=%s qnap=%s db=%s',
    os.getpid(), sys.executable, os.getcwd(), BASE, QNAP_SHARE, DB,
)

# Log unexpected request exceptions without changing Flask's normal error response.
@app.teardown_request
def _log_unhandled_request_exception(exc):
    if exc is not None:
        app.logger.error(
            "Unhandled request exception method=%s path=%s remote=%s",
            request.method, request.path, request.remote_addr,
            exc_info=(type(exc), exc, exc.__traceback__),
        )

# Log HTTP 4xx/5xx responses while keeping normal Flask behavior intact.

@app.after_request
def _log_failed_http_response(response):
    if response.status_code >= 400:
        app.logger.warning(
            "HTTP %s method=%s path=%s remote=%s",
            response.status_code, request.method, request.path, request.remote_addr,
        )
    return response

app.logger.info("Logging initialized. File: %s", LOG_FILE)

app.config.update(
    MAX_CONTENT_LENGTH=FILE_MAX_SIZE + (32 * 1024 * 1024),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,  # Login via https://tiket.pmj.my.id; never transmit over plain HTTP.
    SESSION_REFRESH_EACH_REQUEST=False,  # Background polls must not refresh cookies.
    PERMANENT_SESSION_LIFETIME=timedelta(seconds=ABSOLUTE_TIMEOUT_SECONDS),
)


def load_secret():
    p = os.path.join(BASE, "secret.key")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
    with open(p) as f:
        return f.read().strip()


app.secret_key = load_secret()


# ---------- database ----------
def db():
    c = sqlite3.connect(
        DB,
        timeout=30,
        isolation_level="DEFERRED",
        check_same_thread=False,
    )
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute(
            """CREATE TABLE IF NOT EXISTS tickets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tanggal TEXT, jam TEXT, pelapor TEXT, masalah TEXT,
                tindakan TEXT, support_id TEXT, status TEXT DEFAULT 'Open')"""
        )
        cols = {r["name"] for r in c.execute("PRAGMA table_info(tickets)")}
        # Safe schema migration: preserve all existing ticket values while
        # renaming the legacy owner column to the canonical Support field.
        if "petugas" in cols and "support_id" not in cols:
            c.execute("ALTER TABLE tickets RENAME COLUMN petugas TO support_id")
            cols = {r["name"] for r in c.execute("PRAGMA table_info(tickets)")}
        elif "support_id" not in cols:
            c.execute("ALTER TABLE tickets ADD COLUMN support_id TEXT")
            cols = {r["name"] for r in c.execute("PRAGMA table_info(tickets)")}
        for col, ddl in [("no_tiket", "TEXT"), ("foto", "TEXT"),
                         ("dibuat_oleh", "TEXT"), ("tanpa_foto", "INTEGER DEFAULT 0"),
                         ("reassigned_from", "TEXT"), ("whatsapp", "TEXT"),
                         ("priority", "TEXT DEFAULT 'Medium'"),
                         ("division", "TEXT"),
                         ("category", "TEXT DEFAULT 'Other'"),
                         ("case_tanggal", "TEXT"),
                         ("case_jam", "TEXT"),
                         ("completion_foto", "TEXT"),
                         ("completed_at", "TEXT"),
                         ("public_token", "TEXT"),
                         ("public_token_enabled", "INTEGER DEFAULT 1")]:
            if col not in cols:
                c.execute(f"ALTER TABLE tickets ADD COLUMN {col} {ddl}")
        c.execute("UPDATE tickets SET case_tanggal=tanggal WHERE case_tanggal IS NULL OR case_tanggal=''")
        c.execute("UPDATE tickets SET case_jam=jam WHERE case_jam IS NULL OR case_jam=''")
        c.execute(
            "UPDATE tickets SET no_tiket='TKT-'||replace(tanggal,'-','')||'-'||printf('%04d',id) "
            "WHERE no_tiket IS NULL"
        )
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_no_tiket ON tickets(no_tiket)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_public_token ON tickets(public_token)")
        # Division is independent of Support. Deactivation never removes historical tickets.
        c.execute("""CREATE TABLE IF NOT EXISTS divisions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )""")
        if not c.execute("SELECT 1 FROM divisions LIMIT 1").fetchone():
            now_text = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
            c.executemany("INSERT INTO divisions(name,is_active,created_at) VALUES(?,1,?)",
                          [(name,now_text) for name in ("IT", "Finance", "Accounting", "Purchasing", "Marketing", "Warehouse")])
        c.execute("CREATE INDEX IF NOT EXISTS idx_tickets_division ON tickets(division)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tickets_tanggal ON tickets(tanggal, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tickets_case_tanggal ON tickets(case_tanggal, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status_date ON tickets(status, tanggal, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tickets_support_date ON tickets(support_id, tanggal, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tickets_support_status_date ON tickets(support_id, status, tanggal, id)")
        c.execute(
            """CREATE TABLE IF NOT EXISTS users(
                username TEXT PRIMARY KEY, display TEXT, role TEXT, password_hash TEXT)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS chat_messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT, display TEXT, message TEXT, created_at TEXT
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS notifications(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
                ticket_id INTEGER, created_at TEXT NOT NULL
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS notification_reads(
                username TEXT NOT NULL, notification_id INTEGER NOT NULL,
                read_at TEXT NOT NULL, PRIMARY KEY(username, notification_id)
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS ticket_activity(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS audit_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                username TEXT NOT NULL,
                ticket_id INTEGER,
                ticket_no TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_ticket ON audit_log(ticket_id, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at, id)")
        c.execute(
            """CREATE TABLE IF NOT EXISTS ticket_comments(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                display TEXT NOT NULL,
                comment TEXT NOT NULL,
                created_at TEXT NOT NULL,
                file_folder_id INTEGER
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS comment_attachments(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comment_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                original_name TEXT,
                created_at TEXT NOT NULL,
                qnap_path TEXT,
                mime_type TEXT,
                size_bytes INTEGER DEFAULT 0
            )"""
        )
        ticket_comment_cols = {r["name"] for r in c.execute("PRAGMA table_info(ticket_comments)")}
        if "file_folder_id" not in ticket_comment_cols:
            c.execute("ALTER TABLE ticket_comments ADD COLUMN file_folder_id INTEGER")
        comment_attachment_cols = {r["name"] for r in c.execute("PRAGMA table_info(comment_attachments)")}
        if "qnap_path" not in comment_attachment_cols:
            c.execute("ALTER TABLE comment_attachments ADD COLUMN qnap_path TEXT")
        if "mime_type" not in comment_attachment_cols:
            c.execute("ALTER TABLE comment_attachments ADD COLUMN mime_type TEXT")
        if "size_bytes" not in comment_attachment_cols:
            c.execute("ALTER TABLE comment_attachments ADD COLUMN size_bytes INTEGER DEFAULT 0")
        if "deleted_at" not in ticket_comment_cols:
            c.execute("ALTER TABLE ticket_comments ADD COLUMN deleted_at TEXT")
        if "deleted_by" not in ticket_comment_cols:
            c.execute("ALTER TABLE ticket_comments ADD COLUMN deleted_by TEXT")
        if "delete_error" not in ticket_comment_cols:
            c.execute("ALTER TABLE ticket_comments ADD COLUMN delete_error TEXT")
        if "deleted_at" not in comment_attachment_cols:
            c.execute("ALTER TABLE comment_attachments ADD COLUMN deleted_at TEXT")
        if "delete_error" not in comment_attachment_cols:
            c.execute("ALTER TABLE comment_attachments ADD COLUMN delete_error TEXT")
        c.execute(
            """CREATE TABLE IF NOT EXISTS qnap_delete_retry(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER,
                comment_id INTEGER,
                attachment_id INTEGER,
                qnap_path TEXT NOT NULL,
                physical_path TEXT,
                error TEXT,
                attempts INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                last_attempt_at TEXT
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_qnap_delete_retry_ticket ON qnap_delete_retry(ticket_id, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ticket_comments_ticket ON ticket_comments(ticket_id, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_comment_attachments_comment ON comment_attachments(comment_id, id)")
        c.execute(
            """CREATE TABLE IF NOT EXISTS support_settings(
                support_id TEXT PRIMARY KEY,
                require_evidence INTEGER NOT NULL DEFAULT 0,
                updated_by TEXT,
                updated_at TEXT
            )"""
        )
        now_text = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        for support_name in SUPPORT_USERS:
            c.execute(
                "INSERT OR IGNORE INTO support_settings(support_id,require_evidence,updated_by,updated_at) VALUES(?,?,?,?)",
                (support_name, 1 if support_name == "Viky" else 0, "System", now_text),
            )
        c.execute(
            """CREATE TABLE IF NOT EXISTS file_folders(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parent_id INTEGER,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(parent_id, name),
                FOREIGN KEY(parent_id) REFERENCES file_folders(id) ON DELETE CASCADE
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS file_items(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id INTEGER,
                stored_name TEXT NOT NULL UNIQUE,
                disk_name TEXT,
                original_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                uploaded_by TEXT NOT NULL,
                uploaded_display TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(folder_id) REFERENCES file_folders(id) ON DELETE CASCADE
            )"""
        )
        file_item_cols = {r["name"] for r in c.execute("PRAGMA table_info(file_items)")}
        if "disk_name" not in file_item_cols:
            c.execute("ALTER TABLE file_items ADD COLUMN disk_name TEXT")
            c.execute("UPDATE file_items SET disk_name=stored_name WHERE disk_name IS NULL OR TRIM(disk_name)=''")
        else:
            c.execute("UPDATE file_items SET disk_name=stored_name WHERE disk_name IS NULL OR TRIM(disk_name)=''")
        if "qnap_path" not in file_item_cols:
            c.execute("ALTER TABLE file_items ADD COLUMN qnap_path TEXT")

        folder_cols = {r["name"] for r in c.execute("PRAGMA table_info(file_folders)")}
        if "qnap_path" not in folder_cols:
            c.execute("ALTER TABLE file_folders ADD COLUMN qnap_path TEXT")

        c.execute("CREATE INDEX IF NOT EXISTS idx_file_folders_parent ON file_folders(parent_id, name)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_file_folders_qnap_path ON file_folders(qnap_path)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_file_items_qnap_path ON file_items(qnap_path)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_file_folders_unique_name_nocase ON file_folders(COALESCE(parent_id, 0), name COLLATE NOCASE)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_file_items_folder ON file_items(folder_id, original_name)")
        c.execute("UPDATE tickets SET status='Closed' WHERE status='Done'")
        c.execute("UPDATE tickets SET support_id=? WHERE COALESCE(TRIM(support_id), '')=''", (UNASSIGNED,))

        c.execute(
            """INSERT INTO ticket_activity(ticket_id, action, actor, details, created_at)
               SELECT t.id, 'Ticket created', 'System', 'Legacy ticket history initialized',
                      t.tanggal || ' ' || t.jam || ':00'
               FROM tickets t
               WHERE NOT EXISTS (SELECT 1 FROM ticket_activity a WHERE a.ticket_id=t.id)"""
        )

        # Robustly migrate the legacy Natan account/data to Nathan on every startup.
        # This fixes older databases even when the previous migration only ran partially.
        nathan_hash = generate_password_hash("nathan", method="pbkdf2:sha256")
        c.execute("UPDATE tickets SET support_id='Nathan' WHERE support_id='Natan'")
        c.execute("UPDATE tickets SET dibuat_oleh='nathan' WHERE dibuat_oleh='natan'")
        c.execute("UPDATE tickets SET reassigned_from=REPLACE(reassigned_from, 'Natan', 'Nathan') WHERE reassigned_from LIKE '%Natan%'")
        c.execute("UPDATE chat_messages SET username='nathan', display='Nathan' WHERE username='natan'")
        c.execute("UPDATE notifications SET username='nathan', title=REPLACE(title, 'Natan', 'Nathan'), body=REPLACE(body, 'Natan', 'Nathan') WHERE username='natan' OR title LIKE '%Natan%' OR body LIKE '%Natan%'")
        c.execute("""INSERT OR IGNORE INTO notification_reads(username,notification_id,read_at)
                     SELECT 'nathan', notification_id, read_at FROM notification_reads WHERE username='natan'""")
        c.execute("DELETE FROM notification_reads WHERE username='natan'")
        c.execute("DELETE FROM users WHERE username='natan'")

        # Always force the canonical Nathan account and password.
        c.execute("INSERT OR IGNORE INTO users(username,display,role,password_hash) VALUES(?,?,?,?)",
                  ("nathan", "Nathan", "support", nathan_hash))
        c.execute("UPDATE users SET display='Nathan', role='support', password_hash=? WHERE username='nathan'",
                  (nathan_hash,))

        # Seed remaining users individually so existing databases get any missing accounts.
        c.executemany(
            "INSERT OR IGNORE INTO users(username,display,role,password_hash) VALUES(?,?,?,?)",
            [u for u in SEED_USERS if u[0] != "nathan"],
        )


try:
    app.logger.info('Database initialization started: %s', DB)
    init_db()
    # Restore support accounts created through Admin Tools after restart.
    with db() as _support_db:
        _saved_support = _support_db.execute("SELECT display FROM users WHERE role='support' ORDER BY display").fetchall()
    SUPPORT_USERS[:] = [row["display"] for row in _saved_support]
    for _support_name in SUPPORT_USERS:
        SUPPORT_COLORS.setdefault(_support_name, "#8193aa")
    app.logger.info('Database initialization completed')
except Exception:
    app.logger.exception('FATAL: database initialization failed')
    raise

# ---------- throttling ----------
FAILS = {}


def locked(key):
    now = time.time()
    FAILS[key] = [t for t in FAILS.get(key, []) if now - t < 300]
    return len(FAILS[key]) >= 5


def register_fail(key):
    FAILS.setdefault(key, []).append(time.time())


# ---------- File Center / QNAP storage ----------
_QNAP_LAST_STATUS = {"available": None, "message": "Not checked."}


def _qnap_error_text(code):
    try:
        return ctypes.FormatError(code).strip() or f"Windows error {code}"
    except Exception:
        return f"Windows error {code}"


def _ensure_qnap_connection(share=None):
    """Ensure the Windows process can reach the configured SMB share.

    Direct UNC access is preferred. If credentials are present, Windows'
    WNetAddConnection2 API is used rather than shelling out to `net use`, so
    the password does not appear in the process command line.
    """
    share = share if share is not None else QNAP_SHARE
    if not share:
        return False, "QNAP share path is empty. Set FILE_CENTER_QNAP_SHARE."
    if os.path.isdir(share):
        return True, f"QNAP share reachable: {share}"

    if os.name != "nt":
        return False, f"QNAP share is not reachable from this host: {share}"

    if not QNAP_USERNAME:
        return False, f"QNAP share is not reachable: {share}. Run the service with an SMB-enabled Windows account or configure FILE_CENTER_QNAP_USERNAME / FILE_CENTER_QNAP_PASSWORD."

    class NETRESOURCEW(ctypes.Structure):
        _fields_ = [
            ("dwScope", wintypes.DWORD),
            ("dwType", wintypes.DWORD),
            ("dwDisplayType", wintypes.DWORD),
            ("dwUsage", wintypes.DWORD),
            ("lpLocalName", wintypes.LPWSTR),
            ("lpRemoteName", wintypes.LPWSTR),
            ("lpComment", wintypes.LPWSTR),
            ("lpProvider", wintypes.LPWSTR),
        ]

    resource = NETRESOURCEW(
        0, 1, 0, 0, None, share, None, None
    )
    username = f"{QNAP_DOMAIN}\\{QNAP_USERNAME}" if QNAP_DOMAIN else QNAP_USERNAME
    try:
        mpr = ctypes.windll.mpr
        result = mpr.WNetAddConnection2W(
            ctypes.byref(resource),
            QNAP_PASSWORD or None,
            username,
            0,
        )
        # 0 = success; 1219 = existing connection under different credentials.
        if result not in (0, 1219):
            message = _qnap_error_text(result)
            return False, f"SMB connection to {share} failed: {message} (code {result})."
        if os.path.isdir(share):
            return True, f"QNAP share reachable: {share}"
        return False, f"SMB session created but share is still unreachable: {share}"
    except Exception as exc:
        return False, f"Could not initialize SMB connection to {share}: {type(exc).__name__}: {exc}"


def qnap_storage_status():
    started = time.perf_counter()
    available, message = _ensure_qnap_connection()
    elapsed_ms = (time.perf_counter() - started) * 1000
    if elapsed_ms >= 750:
        app.logger.warning("QNAP primary SMB check slow duration_ms=%.1f available=%s",
                           elapsed_ms, available)
    _QNAP_LAST_STATUS.update(available=available, message=message)
    return {"available": available, "message": message, "path": QNAP_SHARE}


def require_qnap_storage():
    status = qnap_storage_status()
    if not status["available"]:
        app.logger.error("File Center QNAP unavailable: %s", status["message"])
        return False, status
    return True, status


@app.get("/api/dashboard-uploads/status")
def dashboard_uploads_status():
    if not g.user or g.user["role"] != "admin":
        abort(403)
    return jsonify(ok=True, **DASHBOARD_UPLOAD_STORAGE.status())


# ---------- file center ----------
def format_file_size(num):
    try:
        size = float(num)
    except (TypeError, ValueError):
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    return f"{size:.1f} {units[idx]}" if idx else f"{int(size)} B"

def parse_folder_id(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        folder_id = int(value)
    except ValueError:
        abort(404)
    if folder_id < 1:
        abort(404)
    return folder_id

def get_file_folder(folder_id):
    if folder_id is None:
        return None
    with db() as c:
        row = c.execute("SELECT * FROM file_folders WHERE id=?", (folder_id,)).fetchone()
    if not row:
        abort(404)
    return row

def file_breadcrumbs(folder):
    crumbs = []
    current = dict(folder) if folder else None
    while current:
        crumbs.append(current)
        parent_id = current.get("parent_id")
        if parent_id is None:
            break
        with db() as c:
            parent = c.execute("SELECT * FROM file_folders WHERE id=?", (parent_id,)).fetchone()
        current = dict(parent) if parent else None
    crumbs.reverse()
    return crumbs


def _normalize_qnap_relative_path(value):
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or raw in {".", "/"}:
        return []
    if raw.startswith("/") or raw.startswith("\\") or ":" in raw:
        raise ValueError("Invalid QNAP parent path.")
    parts = [part.strip() for part in raw.split("/") if part.strip()]
    if parts and parts[0].casefold() == DASHBOARD_PRIVATE_FOLDER.casefold():
        raise ValueError("Dashboard uploads are private and excluded from File Center.")
    if any(part in {".", ".."} or "\x00" in part for part in parts):
        raise ValueError("Invalid QNAP parent path.")
    if len(parts) >= 2 and [p.casefold() for p in parts[:2]] == ["polo", "root_dashboard"]:
        raise ValueError("Dashboard attachment storage is private and unavailable in File Center.")
    return parts


def qnap_relative_path(folder):
    return "/".join(str(item["name"]) for item in file_breadcrumbs(folder)) if folder else ""


def _path_is_within_root(root, candidate):
    r"""Windows-safe containment check that also supports UNC QNAP paths.

    Python 3.14's ntpath.commonpath() can raise ``Can't mix absolute and
    relative paths`` for a UNC share root such as ``\\server\share`` and a
    child path. File Center uses a UNC share, so compare normalized paths
    using a directory-boundary prefix instead.
    """
    root_norm = ntpath.normcase(ntpath.normpath(root))
    candidate_norm = ntpath.normcase(ntpath.normpath(candidate))
    if candidate_norm == root_norm:
        return True
    prefix = root_norm.rstrip("\\/") + "\\"
    # File Center must not serve private dashboard files, even via stale DB metadata.
    private_norm = ntpath.normcase(ntpath.normpath(ntpath.join(root, "POLO", "root_dashboard")))
    if candidate_norm == private_norm or candidate_norm.startswith(private_norm.rstrip("\\/") + "\\"):
        return False
    if candidate_norm.startswith(prefix):
        relative = candidate_norm[len(prefix):]
        if relative.split("\\", 1)[0] == DASHBOARD_PRIVATE_FOLDER.casefold():
            return False
    return candidate_norm.startswith(prefix)


def qnap_path_from_relative(relative_path):
    root = os.path.normpath(QNAP_SHARE)
    parts = _normalize_qnap_relative_path(relative_path)
    path = os.path.normpath(os.path.join(root, *parts))
    if not _path_is_within_root(root, path):
        raise ValueError("QNAP parent path escapes the configured share.")
    return path, parts

def folder_row_by_relative_path(parts):
    if not parts:
        return None
    parent_id = None
    row = None
    with db() as c:
        for part in parts:
            row = c.execute(
                "SELECT * FROM file_folders WHERE parent_id IS ? AND name COLLATE NOCASE=?",
                (parent_id, part),
            ).fetchone()
            if not row:
                return None
            parent_id = int(row["id"])
    return row


def folder_storage_path(folder_id):
    """Resolve a File Center folder to the configured QNAP UNC path."""
    root = os.path.normpath(QNAP_SHARE)
    parts = []
    current_id = folder_id
    while current_id is not None:
        with db() as c:
            row = c.execute("SELECT id, name, parent_id FROM file_folders WHERE id=?", (current_id,)).fetchone()
        if not row:
            abort(404)
        parts.append(row["name"])
        current_id = row["parent_id"]
    path = os.path.normpath(os.path.join(root, *reversed(parts)))
    if not _path_is_within_root(root, path):
        abort(404)
    return path

def file_storage_path(row):
    """Resolve a File Center file against the QNAP path stored in metadata."""
    rel = row["qnap_path"] if "qnap_path" in row.keys() and row["qnap_path"] else ""
    if rel:
        path,_ = qnap_path_from_relative(rel)
        return path
    disk_name = row["disk_name"] if "disk_name" in row.keys() and row["disk_name"] else row["stored_name"]
    return os.path.normpath(os.path.join(folder_storage_path(row["folder_id"]), disk_name))


def _qnap_name_hidden(name):
    low = (name or "").strip().lower()
    return not low or low in QNAP_SYSTEM_NAMES or any(low.startswith(p) for p in QNAP_SYSTEM_PREFIXES)


def _qnap_dir_name_valid(name):
    return bool(name) and name not in {".", ".."} and not _qnap_name_hidden(name)


def _qnap_file_name_valid(name):
    return bool(name) and name not in {".", ".."} and not _qnap_name_hidden(name) and not name.endswith(".part")


def _looks_like_legacy_stored_name(name):
    return bool(re.fullmatch(r"[0-9a-fA-F]{48}", name or ""))


def _qnap_rel_join(parts):
    return "/".join(str(x) for x in parts if str(x))


def _qnap_rel_to_physical(relative_path):
    path, parts = qnap_path_from_relative(relative_path)
    return path, parts


def _folder_qnap_relative(folder_id):
    if folder_id is None:
        return ""
    with db() as c:
        row = c.execute("SELECT qnap_path, name, parent_id FROM file_folders WHERE id=?", (folder_id,)).fetchone()
        if row and row["qnap_path"]:
            return row["qnap_path"].strip("/")
    parts=[]
    current=folder_id
    while current is not None:
        with db() as c:
            row=c.execute("SELECT id,name,parent_id FROM file_folders WHERE id=?",(current,)).fetchone()
        if not row:
            return ""
        parts.append(row["name"])
        current=row["parent_id"]
    return _qnap_rel_join(reversed(parts))


def _folder_path_id_from_parts(parts):
    if not parts:
        return None
    with db() as c:
        row=None
        parent_id=None
        for part in parts:
            row=c.execute("SELECT * FROM file_folders WHERE parent_id IS ? AND name COLLATE NOCASE=?",(parent_id,part)).fetchone()
            if not row:
                return None
            parent_id=int(row["id"])
        return int(row["id"]) if row else None


def _ensure_folder_metadata(c, name, parent_id, rel_path):
    row = c.execute("SELECT id FROM file_folders WHERE qnap_path=?", (rel_path,)).fetchone() if rel_path else None
    if row:
        fid=int(row["id"])
        c.execute("UPDATE file_folders SET name=?, parent_id=? WHERE id=?", (name,parent_id,fid))
        return fid
    row = c.execute("SELECT id FROM file_folders WHERE parent_id IS ? AND name COLLATE NOCASE=?", (parent_id,name)).fetchone()
    if row:
        fid=int(row["id"])
        c.execute("UPDATE file_folders SET qnap_path=? WHERE id=?", (rel_path,fid))
        return fid
    cur=c.execute("INSERT INTO file_folders(name,parent_id,created_by,created_at,qnap_path) VALUES(?,?,?,?,?)",
                  (name,parent_id,"QNAP",local_now().strftime("%Y-%m-%d %H:%M:%S"),rel_path))
    return int(cur.lastrowid)


def _sync_qnap_tree(force=False):
    """Index the QNAP tree for metadata only; QNAP itself remains the source of truth."""
    global QNAP_LAST_SYNC, QNAP_SYNC_DIRTY
    now=time.monotonic()
    with QNAP_SYNC_LOCK:
        if not force and not QNAP_SYNC_DIRTY and (now-QNAP_LAST_SYNC) < QNAP_POLL_SECONDS:
            return True
        ok,status=require_qnap_storage()
        if not ok:
            return False
        seen_folders=set(); seen_files=set(); scan_complete=True
        try:
            with db() as c:
                def ensure_file(parent_id, entry_path, display_name, stat_obj, rel_path):
                    nonlocal c
                    mime=mimetypes.guess_type(display_name)[0] or "application/octet-stream"
                    row=c.execute("SELECT * FROM file_items WHERE qnap_path=?",(rel_path,)).fetchone()
                    if row is None:
                        row=c.execute("SELECT * FROM file_items WHERE folder_id IS ? AND original_name COLLATE NOCASE=?",(parent_id,display_name)).fetchone()
                    if row is None:
                        # Preserve metadata across a QNAP move when there is one unambiguous match.
                        candidates=c.execute("SELECT * FROM file_items WHERE original_name COLLATE NOCASE=? AND size_bytes=?",(display_name,int(stat_obj.st_size))).fetchall()
                        unseen=[r for r in candidates if int(r["id"]) not in seen_files]
                        row=unseen[0] if len(unseen)==1 else None
                    if row:
                        fid=int(row["id"])
                        c.execute("UPDATE file_items SET folder_id=?, stored_name=?, disk_name=?, original_name=?, mime_type=?, size_bytes=?, qnap_path=? WHERE id=?",
                                  (parent_id,rel_path,display_name,display_name,mime,int(stat_obj.st_size),rel_path,fid))
                    else:
                        mtime=datetime.fromtimestamp(stat_obj.st_mtime, timezone.utc).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
                        c.execute("""INSERT INTO file_items
                            (folder_id,stored_name,disk_name,original_name,mime_type,size_bytes,uploaded_by,uploaded_display,created_at,qnap_path)
                            VALUES(?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(stored_name) DO UPDATE SET
                              folder_id=excluded.folder_id, disk_name=excluded.disk_name,
                              original_name=excluded.original_name, mime_type=excluded.mime_type,
                              size_bytes=excluded.size_bytes, qnap_path=excluded.qnap_path""",
                            (parent_id,rel_path,display_name,display_name,mime,int(stat_obj.st_size),"-","-",mtime,rel_path))
                        fid=int(c.execute("SELECT id FROM file_items WHERE stored_name=?", (rel_path,)).fetchone()["id"])
                    seen_files.add(fid)
                    return fid

                def walk(path,parent_id,rel_parts):
                    nonlocal scan_complete
                    try:
                        entries=list(os.scandir(path))
                    except OSError as exc:
                        scan_complete=False; app.logger.warning("QNAP scan failed at %s: %s",path,exc); return
                    for entry in sorted(entries,key=lambda e:e.name.lower()):
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if not _qnap_dir_name_valid(entry.name): continue
                                rel=_qnap_rel_join([*rel_parts,entry.name])
                                fid=_ensure_folder_metadata(c,entry.name,parent_id,rel)
                                seen_folders.add(fid)
                                walk(entry.path,fid,[*rel_parts,entry.name])
                            elif entry.is_file(follow_symlinks=False) and _qnap_file_name_valid(entry.name):
                                display_name=entry.name
                                row=c.execute("SELECT * FROM file_items WHERE folder_id IS ? AND disk_name=?",(parent_id,entry.name)).fetchone()
                                if _looks_like_legacy_stored_name(entry.name) and row and row["original_name"] and row["original_name"] != entry.name:
                                    legacy_target=os.path.basename(re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", str(row["original_name"]))).strip(" .")[:180]
                                    candidate=os.path.join(path,legacy_target)
                                    if os.path.exists(candidate):
                                        stem,ext=os.path.splitext(legacy_target)
                                        suffix=1
                                        while os.path.exists(candidate):
                                            legacy_target=f"{stem} ({suffix}){ext}"
                                            candidate=os.path.join(path,legacy_target)
                                            suffix+=1
                                    try:
                                        os.rename(entry.path,candidate)
                                        display_name=legacy_target
                                    except OSError:
                                        app.logger.warning("Could not restore legacy QNAP filename %s -> %s",entry.name,legacy_target,exc_info=True)
                                if not _qnap_file_name_valid(display_name): continue
                                stat_obj=os.stat(os.path.join(path,display_name),follow_symlinks=False)
                                rel=_qnap_rel_join([*rel_parts,display_name])
                                ensure_file(parent_id,os.path.join(path,display_name),display_name,stat_obj,rel)
                        except OSError as exc:
                            scan_complete=False; app.logger.warning("QNAP scan entry failed: %s (%s)",entry.path,exc)
                walk(FILE_STORAGE_DIR,None,[])
                if scan_complete:
                    if seen_files:
                        placeholders=','.join('?' for _ in seen_files)
                        c.execute(f"DELETE FROM file_items WHERE id NOT IN ({placeholders}) AND COALESCE(qnap_path,'') NOT LIKE 'POLO/root_dashboard/%'",tuple(seen_files))
                    else:
                        c.execute("DELETE FROM file_items WHERE COALESCE(qnap_path,'') NOT LIKE 'POLO/root_dashboard/%'")
                    if seen_folders:
                        placeholders=','.join('?' for _ in seen_folders)
                        c.execute(f"DELETE FROM file_folders WHERE id NOT IN ({placeholders}) AND COALESCE(qnap_path,'') NOT LIKE 'POLO/root_dashboard/%' AND COALESCE(qnap_path,'') != 'POLO/root_dashboard'",tuple(seen_folders))
                    else:
                        c.execute("DELETE FROM file_folders WHERE COALESCE(qnap_path,'') NOT LIKE 'POLO/root_dashboard/%' AND COALESCE(qnap_path,'') != 'POLO/root_dashboard'")
                c.commit()
            QNAP_LAST_SYNC=now; QNAP_SYNC_DIRTY=not scan_complete
            return scan_complete
        except Exception:
            app.logger.exception("QNAP metadata sync failed")
            return False


def _qnap_listing(relative_path="", force_refresh=False):
    """Read only the requested QNAP folder; a manual refresh bypasses the listing cache."""
    cache_key=str(relative_path or "").strip("/")
    now=time.monotonic()
    with QNAP_LIST_CACHE_LOCK:
        cached=QNAP_LIST_CACHE.get(cache_key)
        if not force_refresh and cached and (now-cached[0]) < QNAP_LIST_CACHE_TTL:
            payload=cached[1]
            return payload[0],list(payload[1]),[dict(x) for x in payload[2]],[dict(x) for x in payload[3]]
    storage_ok, storage_status=require_qnap_storage()
    if not storage_ok:
        raise OSError(storage_status["message"])
    current_path, parts=qnap_path_from_relative(relative_path)
    if not os.path.isdir(current_path):
        raise FileNotFoundError(f"QNAP folder does not exist: {current_path}")
    folder_id=_folder_path_id_from_parts(parts)
    folders=[]; files=[]
    with db() as c:
        physical_entries=sorted(os.scandir(current_path),key=lambda e:e.name.lower())
        for entry in physical_entries:
            if entry.is_dir(follow_symlinks=False) and _qnap_dir_name_valid(entry.name):
                rel=_qnap_rel_join([*parts,entry.name])
                row=c.execute("SELECT * FROM file_folders WHERE qnap_path=?",(rel,)).fetchone()
                if not row:
                    row=c.execute("SELECT * FROM file_folders WHERE parent_id IS ? AND name COLLATE NOCASE=?",(folder_id,entry.name)).fetchone()
                if not row:
                    fid=_ensure_folder_metadata(c,entry.name,folder_id,rel)
                    row=c.execute("SELECT * FROM file_folders WHERE id=?",(fid,)).fetchone()
                if row:
                    folders.append(dict(row))
            elif entry.is_file(follow_symlinks=False) and _qnap_file_name_valid(entry.name):
                rel=_qnap_rel_join([*parts,entry.name])
                row=c.execute("SELECT * FROM file_items WHERE qnap_path=?",(rel,)).fetchone()
                if not row:
                    row=c.execute("SELECT * FROM file_items WHERE folder_id IS ? AND original_name COLLATE NOCASE=?",(folder_id,entry.name)).fetchone()
                stat_obj=entry.stat()
                item=dict(row) if row else {
                    "id": None,"folder_id":folder_id,"stored_name":rel,"disk_name":entry.name,
                    "original_name":entry.name,"mime_type":mimetypes.guess_type(entry.name)[0] or "application/octet-stream",
                    "size_bytes":int(stat_obj.st_size),"uploaded_by":"-","uploaded_display":"-",
                    "created_at":datetime.fromtimestamp(stat_obj.st_mtime,timezone.utc).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),"qnap_path":rel,
                }
                item["size_bytes"]=int(stat_obj.st_size); item["original_name"]=entry.name; item["disk_name"]=entry.name
                item["mime_type"]=mimetypes.guess_type(entry.name)[0] or item.get("mime_type") or "application/octet-stream"
                if not row:
                    mtime=datetime.fromtimestamp(stat_obj.st_mtime,timezone.utc).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
                    # A concurrent upload may insert metadata between this scan and INSERT.
                    # Upsert by the unique QNAP-relative stored_name, preserving audit fields.
                    c.execute("""INSERT INTO file_items
                        (folder_id,stored_name,disk_name,original_name,mime_type,size_bytes,uploaded_by,uploaded_display,created_at,qnap_path)
                        VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(stored_name) DO UPDATE SET
                          folder_id=excluded.folder_id, disk_name=excluded.disk_name,
                          original_name=excluded.original_name, mime_type=excluded.mime_type,
                          size_bytes=excluded.size_bytes, qnap_path=excluded.qnap_path""",
                        (folder_id,rel,entry.name,entry.name,item["mime_type"],int(stat_obj.st_size),"-","-",mtime,rel))
                    saved=c.execute("SELECT id,uploaded_display,created_at FROM file_items WHERE stored_name=?", (rel,)).fetchone()
                    item["id"]=int(saved["id"])
                    item["uploaded_display"]=saved["uploaded_display"]
                    item["created_at"]=saved["created_at"]
                files.append(item)
        c.commit()
    payload=(folder_id,list(parts),[dict(x) for x in folders],[dict(x) for x in files])
    with QNAP_LIST_CACHE_LOCK:
        QNAP_LIST_CACHE[cache_key]=(time.monotonic(),payload)
    return payload[0],list(payload[1]),[dict(x) for x in payload[2]],[dict(x) for x in payload[3]]


class _QNAPChangeHandler(FileSystemEventHandler if FileSystemEventHandler else object):
    def on_any_event(self, event):
        global QNAP_SYNC_DIRTY
        QNAP_SYNC_DIRTY = True


def _start_qnap_watcher():
    global QNAP_WATCHER
    if Observer is None or FileSystemEventHandler is None or QNAP_WATCHER is not None:
        return
    if os.name != "nt":
        return
    try:
        handler = _QNAPChangeHandler()
        observer = Observer()
        observer.schedule(handler, FILE_STORAGE_DIR, recursive=True)
        observer.daemon = True
        observer.start()
        QNAP_WATCHER = observer
        app.logger.info("QNAP File Center watcher started for %s", FILE_STORAGE_DIR)
    except Exception:
        app.logger.warning("QNAP watcher unavailable; polling fallback remains active", exc_info=True)


def sync_top_level_storage_folders():
    """Backward-compatible name; now performs a complete QNAP tree sync."""
    _sync_qnap_tree(force=True)


# ---------- photos ----------
def sniff(b):
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    return None


def save_photo(fs):
    data = fs.read(MAX_FOTO + 1)
    if len(data) > MAX_FOTO:
        raise ValueError("Maximum photo size is 10 MB.")
    ext = sniff(data)
    if not ext:
        raise ValueError("File must be an image (JPG, PNG, GIF, or WEBP).")
    name = secrets.token_hex(16) + "." + ext
    DASHBOARD_UPLOAD_STORAGE.write("ticket", name, data)
    return name


def _ticket_evidence_path(relative_path):
    """Resolve ticket-only private evidence; legacy IT/TICKETS links still work.

    Never use this resolver for general File Center requests.
    """
    raw = str(relative_path or "").replace("\\", "/")
    if ":" in raw or "\x00" in raw:
        raise ValueError("Invalid ticket evidence path")
    parts = _safe_upload_relative_path(raw)
    private_prefix = TICKET_EVIDENCE_RELATIVE_ROOT.split("/")
    if len(parts) >= 3 and [x.casefold() for x in parts[:3]] == [x.casefold() for x in private_prefix]:
        root = DASHBOARD_UPLOAD_STORAGE.active_root()
        physical = os.path.join(str(root), *parts[2:])
        root_norm = ntpath.normcase(ntpath.normpath(str(root)))
        physical_norm = ntpath.normcase(ntpath.normpath(physical))
        if not physical_norm.startswith(root_norm.rstrip("\\/") + "\\"):
            raise ValueError("Invalid private ticket evidence path")
        return physical, parts
    if parts and parts[0].casefold() == TICKET_LEGACY_RELATIVE_ROOT.casefold():
        storage_ok, status = require_qnap_storage()
        if not storage_ok:
            raise OSError(status["message"])
        return qnap_path_from_relative("/".join(parts))
    raise ValueError("Path is outside the allowed ticket evidence roots")


def _ticket_folder_prefix(no_tiket):
    return _qnap_rel_join([TICKET_EVIDENCE_RELATIVE_ROOT, _safe_upload_name(no_tiket)])


def _is_ticket_attachment_path(no_tiket, relative_path):
    rel = str(relative_path or "").replace("\\", "/")
    prefixes = (_ticket_folder_prefix(no_tiket),
                _qnap_rel_join([TICKET_LEGACY_RELATIVE_ROOT, _safe_upload_name(no_tiket)]))
    return any(rel.startswith(p + "/") for p in prefixes)


def _ticket_attachment_storage_state(relative_path):
    """Return missing only after storage is reachable; outages are not deletions."""
    if not relative_path:
        return "available"
    try:
        physical, _ = _ticket_evidence_path(relative_path)
        return "available" if os.path.isfile(physical) else "missing"
    except (OSError, ValueError):
        return "unavailable"


def _ensure_ticket_storage_tree(tid, no_tiket, conn=None):
    """Create the private ticket directory and QNAP metadata, including on creation.

    Supply the existing SQLite connection during ticket INSERT to keep the ticket
    row and its folder metadata in *one* database transaction. If SMB storage is
    unavailable, the INSERT rolls back instead of creating a ticket without its
    directory. A standalone read/progress operation can omit ``conn``.

    The two IPs are alternate paths to the SAME QNAP dataset, not two replicas.
    """
    ticket_name = _safe_upload_name(no_tiket)
    if ticket_name != str(no_tiket):
        raise ValueError("Invalid ticket number for QNAP folder")
    root_parts = TICKET_EVIDENCE_RELATIVE_ROOT.split("/")
    ticket_parts = [*root_parts, ticket_name]

    def register(c):
        parent_id = None
        parts = []
        for part in ticket_parts:
            parts.append(part)
            parent_id = _ensure_folder_metadata(c, part, parent_id, _qnap_rel_join(parts))
        return {
            "ticket_folder_id": int(parent_id),
            "ticket_path": _qnap_rel_join(ticket_parts),
        }

    with QNAP_UPLOAD_FINALIZE_LOCK:
        # Do NOT use require_qnap_storage(): it only verifies the primary File
        # Center share and would incorrectly reject a valid .99 failover.
        active_root = DASHBOARD_UPLOAD_STORAGE.active_root()
        ticket_dir = os.path.join(str(active_root), "TICKETS", ticket_name)
        os.makedirs(ticket_dir, exist_ok=True)
        if not os.path.isdir(ticket_dir):
            raise OSError(f"QNAP ticket folder is not accessible: {ticket_name}")
        if conn is not None:
            return register(conn)
        with db() as c:
            return register(c)


def _ticket_folder_rows(tid, no_tiket):
    storage = _ensure_ticket_storage_tree(tid, no_tiket)
    ticket_dir, _ = _ticket_evidence_path(storage["ticket_path"])
    result = []
    # Physical contents are authoritative. Stale DB rows remain for audit, but
    # are not selectable for new uploads after a folder is removed from QNAP.
    with QNAP_UPLOAD_FINALIZE_LOCK:
        with db() as c:
            for entry in sorted(os.scandir(ticket_dir), key=lambda e: e.name.lower()):
                if not entry.is_dir(follow_symlinks=False) or not _qnap_dir_name_valid(entry.name):
                    continue
                rel = _qnap_rel_join([storage["ticket_path"], entry.name])
                fid = _ensure_folder_metadata(c, entry.name, storage["ticket_folder_id"], rel)
                row = c.execute("SELECT id,name,qnap_path,created_by,created_at FROM file_folders WHERE id=?", (fid,)).fetchone()
                result.append({"id": int(row["id"]), "name": row["name"], "path": row["qnap_path"],
                               "created_by": row["created_by"] or "QNAP", "created_at": row["created_at"] or "-"})
            c.commit()
    return storage, result


def _next_ticket_progress_folder(c, parent_id, parent_rel):
    rows = c.execute(
        "SELECT name FROM file_folders WHERE parent_id=? AND name LIKE 'UPDATE-%'",
        (parent_id,),
    ).fetchall()
    used = set()
    for row in rows:
        m = re.fullmatch(r"UPDATE-(\d+)", row["name"] or "")
        if m:
            used.add(int(m.group(1)))
    index = 1
    while index in used:
        index += 1
    name = f"UPDATE-{index:03d}"
    rel = _qnap_rel_join([parent_rel, name])
    physical, _ = _ticket_evidence_path(rel)
    os.makedirs(physical, exist_ok=True)
    folder_id = _ensure_folder_metadata(c, name, parent_id, rel)
    return folder_id, name, rel, physical


def _save_ticket_attachment_to_qnap(fs, target_dir, target_rel):
    """Stream one ticket attachment through a local temporary file, then publish to QNAP."""
    original_name = _safe_upload_name(unquote(fs.filename or "attachment"))
    if not original_name:
        raise ValueError("Invalid attachment filename.")

    local_temp = None
    qnap_temp = None
    final_path = None
    try:
        fd, local_temp = tempfile.mkstemp(prefix=".ticket-upload-", suffix=".part", dir=TEMP_UPLOAD_DIR)
        os.close(fd)
        total = 0
        with open(local_temp, "wb", buffering=4 * 1024 * 1024) as out:
            while True:
                chunk = fs.stream.read(4 * 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > COMMENT_MAX_FILE_SIZE:
                    raise ValueError(
                        f"Each ticket attachment must be <= {format_file_size(COMMENT_MAX_FILE_SIZE)}."
                    )
                out.write(chunk)
            out.flush()
            try:
                os.fsync(out.fileno())
            except OSError:
                pass

        final_name = _unique_qnap_filename(target_dir, original_name)
        final_rel = _qnap_rel_join([target_rel, final_name])
        qnap_temp = os.path.join(target_dir, f".{secrets.token_hex(12)}.part")
        with open(local_temp, "rb", buffering=4 * 1024 * 1024) as src, open(
            qnap_temp, "wb", buffering=4 * 1024 * 1024
        ) as dst:
            shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
            dst.flush()
            try:
                os.fsync(dst.fileno())
            except OSError:
                pass
        if os.path.getsize(qnap_temp) != total:
            raise IOError("QNAP attachment size verification failed.")
        final_path = os.path.join(target_dir, final_name)
        os.replace(qnap_temp, final_path)
        qnap_temp = None
        mime_type = mimetypes.guess_type(final_name)[0] or "application/octet-stream"
        return final_name, original_name, final_rel, mime_type, total
    finally:
        for candidate in (local_temp, qnap_temp):
            if candidate:
                try:
                    if os.path.exists(candidate):
                        os.remove(candidate)
                except OSError:
                    app.logger.warning("Could not remove temporary ticket upload: %r", candidate, exc_info=True)


def _remove_qnap_paths(paths):
    for rel_path in paths:
        if not rel_path:
            continue
        try:
            physical, _ = _ticket_evidence_path(rel_path)
            if os.path.isfile(physical):
                os.remove(physical)
        except (OSError, ValueError):
            app.logger.warning("Could not clean up ticket attachment %r", rel_path, exc_info=True)


def support_evidence_settings(conn=None):
    close_conn = conn is None
    c = conn or db()
    try:
        rows = c.execute("SELECT support_id, require_evidence FROM support_settings").fetchall()
        settings = {r["support_id"]: bool(r["require_evidence"]) for r in rows}
        return {name: bool(settings.get(name, name == "Viky")) for name in SUPPORT_USERS}
    finally:
        if close_conn:
            c.close()


def support_requires_evidence(support_id, conn=None):
    if support_id not in SUPPORT_USERS:
        return False
    return bool(support_evidence_settings(conn).get(support_id, False))


def ticket_has_evidence(conn, tid):
    row = conn.execute(
        "SELECT foto, completion_foto FROM tickets WHERE id=?",
        (tid,),
    ).fetchone()
    if not row:
        return False
    if (row["foto"] or "").strip() or (row["completion_foto"] or "").strip():
        return True
    attachment = conn.execute(
        """SELECT 1
           FROM comment_attachments ca
           JOIN ticket_comments tc ON tc.id=ca.comment_id
           WHERE tc.ticket_id=? AND tc.deleted_at IS NULL AND ca.deleted_at IS NULL
           LIMIT 1""",
        (tid,),
    ).fetchone()
    return bool(attachment)


def _save_initial_evidence_to_qnap(tid, no_tiket, fs, actor_username, actor_display, comment="Initial evidence attached"):
    if not fs or not fs.filename:
        return None
    storage = _ensure_ticket_storage_tree(tid, no_tiket)
    now_text = local_now().strftime("%Y-%m-%d %H:%M:%S")
    with db() as c:
        folder_name = "INITIAL-EVIDENCE"
        target_rel = _qnap_rel_join([storage["ticket_path"], folder_name])
        target_dir, _ = _ticket_evidence_path(target_rel)
        os.makedirs(target_dir, exist_ok=True)
        target_folder_id = _ensure_folder_metadata(c, folder_name, storage["ticket_folder_id"], target_rel)

    saved = _save_ticket_attachment_to_qnap(fs, target_dir, target_rel)
    final_name, original_name, qnap_path, mime_type, size_bytes = saved
    with db() as c:
        cur = c.execute(
            "INSERT INTO ticket_comments(ticket_id,username,display,comment,created_at,file_folder_id) VALUES(?,?,?,?,?,?)",
            (tid, actor_username, actor_display, comment, now_text, target_folder_id),
        )
        comment_id = cur.lastrowid
        c.execute(
            "INSERT INTO comment_attachments(comment_id,filename,original_name,created_at,qnap_path,mime_type,size_bytes) VALUES(?,?,?,?,?,?,?)",
            (comment_id, final_name, original_name, now_text, qnap_path, mime_type, size_bytes),
        )
    return saved


# ---------- Modern UI CSS ----------

_SUN = '<svg class="sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>'
_MOON = '<svg class="moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>'

THEME_HEAD = ("<script>(function(){try{var t=localStorage.getItem('theme');"
              "if(!t){t='light'}"
              "document.documentElement.setAttribute('data-theme',t)}catch(e){}})();</script>"
              '<style id="polo-theme-vars">'
              ":root{--page-bg:#eef2f6;--card-bg:#ffffff;--dark-card:#0b1220;--dark-bg:#080d16;--dark-panel:#111827;}"
              ":root[data-theme=dark]{--page-bg:var(--dark-bg);--card-bg:var(--dark-card);--panel-bg:var(--dark-panel);--bg:var(--page-bg)!important;--bg-accent:var(--page-bg)!important;--surface:var(--panel-bg)!important;--surface-2:var(--card-bg)!important;--surface-3:#162033!important;--header:var(--card-bg)!important;--header-2:var(--card-bg)!important;--dark-header:var(--card-bg);--text:#f8fafc!important;--text-soft:#e2e8f0!important;--muted:#cbd5e1!important;--border:rgba(255,255,255,.09)!important;--border-strong:rgba(255,255,255,.14)!important;--shadow-md:0 10px 28px rgba(0,0,0,.22);--shadow-lg:0 16px 40px rgba(0,0,0,.26);}"
              ":root[data-theme=dark] body{background:var(--page-bg)!important;color:var(--text)!important;}"
              ":root[data-theme=dark] header{background:var(--card-bg)!important;border-color:var(--border)!important;box-shadow:0 8px 24px rgba(0,0,0,.20)!important;}"
              ":root[data-theme=dark] .hero-card,:root[data-theme=dark] .panel,:root[data-theme=dark] .staff-card,:root[data-theme=dark] .stat,:root[data-theme=dark] .queue-card,:root[data-theme=dark] .ticket-item,:root[data-theme=dark] .ticket-body,:root[data-theme=dark] .filter-toolbar,:root[data-theme=dark] .open-queue-item,:root[data-theme=dark] .comment-card,:root[data-theme=dark] .chat-msg,:root[data-theme=dark] .notif-panel,:root[data-theme=dark] .admin-edit,:root[data-theme=dark] .files-card,:root[data-theme=dark] .files-toolbar-card,.dark-theme-card{background:var(--panel-bg)!important;border-color:var(--border)!important;box-shadow:0 10px 28px rgba(0,0,0,.20)!important;}"
              ":root[data-theme=dark] .staff-card>summary,:root[data-theme=dark] .ticket-item>summary,:root[data-theme=dark] .open-queue-item,:root[data-theme=dark] .filter-toolbar input,:root[data-theme=dark] .filter-toolbar select,:root[data-theme=dark] .panel input,:root[data-theme=dark] .panel select,:root[data-theme=dark] .panel textarea{background:var(--card-bg)!important;border-color:var(--border)!important;}"
              ":root[data-theme=dark] header::after{background:var(--page-bg)!important;}"
              ":root[data-theme=dark] .brand-mark{background:rgba(255,255,255,.05)!important;}"
              "</style>")
THEME_BTN = ('<button type="button" id="themeBtn" class="themebtn" title="Toggle theme" '
             'aria-label="Toggle theme">' + _SUN + _MOON + '</button>')
THEME_JS = ("<script>"
            "(function(){var b=document.getElementById('themeBtn');if(!b)return;"
            "function syncTheme(){var d=document.documentElement;var dark=d.getAttribute('data-theme')==='dark';"
            "b.setAttribute('title',dark?'Use light mode':'Use dark mode');"
            "b.setAttribute('aria-label',dark?'Use light mode':'Use dark mode');}"
            "syncTheme();b.addEventListener('click',function(){var d=document.documentElement,t=d.getAttribute('data-theme')==='dark'?'light':'dark';"
            "d.setAttribute('data-theme',t);try{localStorage.setItem('theme',t)}catch(e){}syncTheme();});})();"
            "</script>")






# ---------- helpers ----------
def local_now():
    return datetime.now(timezone.utc).astimezone(LOCAL_TZ)


try:
    app.logger.info('Initial QNAP metadata sync started: %s', FILE_STORAGE_DIR)
    _sync_started = time.monotonic()
    _sync_ok = sync_top_level_storage_folders()
    app.logger.info(
        'Initial QNAP metadata sync finished ok=%s duration=%.1fs',
        _sync_ok, time.monotonic() - _sync_started,
    )
except Exception:
    app.logger.exception('Initial QNAP metadata sync failed; continuing startup')

def today():
    return local_now().strftime("%Y-%m-%d")


def parse_date_arg(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def get_ticket_range():
    if request.args.get("ticket_all") == "1":
        return None, None, True
    today_date = local_now().date()
    start = parse_date_arg(request.args.get("ticket_start")) or today_date
    end = parse_date_arg(request.args.get("ticket_end")) or today_date
    if start > end:
        start, end = end, start
    return start, end, False


def format_filter_label(start_date, end_date, is_all):
    if is_all:
        return "All ticket dates"
    start_text = start_date.strftime("%Y-%m-%d")
    end_text = end_date.strftime("%Y-%m-%d")
    return start_text if start_text == end_text else f"{start_text} to {end_text}"


def _query_cache_get(key):
    now = time.monotonic()
    with QUERY_CACHE_LOCK:
        item = QUERY_CACHE.get(key)
        if not item:
            return None
        created, value = item
        if now - created > QUERY_CACHE_TTL:
            QUERY_CACHE.pop(key, None)
            return None
        return value


def _query_cache_set(key, value):
    with QUERY_CACHE_LOCK:
        QUERY_CACHE[key] = (time.monotonic(), value)
        if len(QUERY_CACHE) > 128:
            oldest = sorted(QUERY_CACHE.items(), key=lambda kv: kv[1][0])[:32]
            for key_to_drop, _ in oldest:
                QUERY_CACHE.pop(key_to_drop, None)


def _invalidate_query_cache():
    with QUERY_CACHE_LOCK:
        QUERY_CACHE.clear()


def _invalidate_qnap_listing_cache():
    with QNAP_LIST_CACHE_LOCK:
        QNAP_LIST_CACHE.clear()


def _timed_db_fetch(sql, args, label):
    started = time.perf_counter()
    with db() as c:
        rows = c.execute(sql, args).fetchall()
    elapsed = (time.perf_counter() - started) * 1000
    if elapsed >= 100:
        app.logger.warning("Slow DB query %s: %.1f ms (%s rows)", label, elapsed, len(rows))
    return rows


def query(start_date=None, end_date=None, q=""):
    key = ("query", start_date.isoformat() if start_date else None, end_date.isoformat() if end_date else None, q)
    cached = _query_cache_get(key)
    if cached is not None:
        return cached
    sql, args = "SELECT * FROM tickets WHERE 1=1", []
    if start_date is not None and end_date is not None:
        sql += " AND tanggal BETWEEN ? AND ?"
        args += [start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")]
    if q:
        sql += (" AND (no_tiket LIKE ? OR pelapor LIKE ? OR masalah LIKE ?"
                " OR tindakan LIKE ? OR support_id LIKE ?)")
        args += [f"%{q}%"] * 5
    sql += " ORDER BY tanggal DESC, jam DESC, id DESC"
    rows = [dict(r) for r in _timed_db_fetch(sql, args, "ticket query")]
    _query_cache_set(key, rows)
    return rows


def month_bounds(month_text=None):
    """Return first/last date for a YYYY-MM month; defaults to current month."""
    today_date = local_now().date()
    try:
        start = datetime.strptime((month_text or "").strip(), "%Y-%m").date()
    except (TypeError, ValueError):
        start = today_date.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1, day=1)
    else:
        next_month = start.replace(month=start.month + 1, day=1)
    end = next_month - timedelta(days=1)
    return start, end

def query_month_page(month_text=None, q="", page=1, per_page=50):
    start_date, end_date = month_bounds(month_text)
    page = max(1, int(page or 1))
    per_page = max(25, min(50, int(per_page or 50)))
    where = "tanggal BETWEEN ? AND ?"
    args = [start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")]
    if q:
        where += (" AND (no_tiket LIKE ? OR pelapor LIKE ? OR masalah LIKE ?"
                  " OR tindakan LIKE ? OR support_id LIKE ? OR status LIKE ? OR category LIKE ?)")
        args += [f"%{q}%"] * 7
    cache_key=("archive",start_date.isoformat(),end_date.isoformat(),q,page,per_page)
    cached=_query_cache_get(cache_key)
    if cached is not None:
        return cached
    started=time.perf_counter()
    with db() as c:
        summary=c.execute(
            f"SELECT COUNT(*) AS total, "
            f"SUM(CASE WHEN status NOT IN ('Resolved','Closed','Done') THEN 1 ELSE 0 END) AS open_count, "
            f"SUM(CASE WHEN status IN ('Resolved','Closed','Done') THEN 1 ELSE 0 END) AS resolved_count "
            f"FROM tickets WHERE {where}", args).fetchone()
        rows=c.execute(
            f"SELECT * FROM tickets WHERE {where} ORDER BY tanggal DESC, jam DESC, id DESC LIMIT ? OFFSET ?",
            args + [per_page, (page-1)*per_page],
        ).fetchall()
    elapsed=(time.perf_counter()-started)*1000
    if elapsed>=100:
        app.logger.warning("Slow archive query: %.1f ms page=%s q=%r",elapsed,page,q)
    payload=([dict(r) for r in rows], start_date, end_date, int(summary["total"] or 0), int(summary["open_count"] or 0), int(summary["resolved_count"] or 0))
    _query_cache_set(cache_key,payload)
    return payload


def query_month(month_text=None, q=""):
    rows,start_date,end_date,total,_,_=query_month_page(month_text,q,1,50)
    return rows,start_date,end_date


# Admin users may edit any ticket; support users may edit only their assigned tickets.
def check_edit(tid):
    with db() as c:
        r = c.execute("SELECT support_id FROM tickets WHERE id=?", (tid,)).fetchone()
    if not r:
        abort(404)
    if g.user["role"] != "admin" and r["support_id"] != g.user["display"]:
        abort(403)


def normalize_whatsapp(value):
    raw = re.sub(r"\D", "", value or "")
    if not raw:
        return ""
    if raw.startswith("0"):
        raw = "62" + raw[1:]
    elif raw.startswith("8"):
        raw = "62" + raw
    if not (10 <= len(raw) <= 15) or not raw.startswith("62"):
        raise ValueError("WhatsApp number must be a valid Indonesian number, e.g. 081234567890.")
    return raw


def ticket_sla(row, now=None):
    d = dict(row) if not isinstance(row, dict) else dict(row)
    now = now or local_now()
    try:
        case_dt = ticket_case_datetime(d)
        age_seconds = max(0, int((now - case_dt).total_seconds()))
    except (KeyError, TypeError, ValueError):
        age_seconds = 0
    days = age_seconds // 86400
    hours = (age_seconds % 86400) // 3600
    d["age_days"] = days
    if d.get("status") in COMPLETED_STATUSES:
        d["sla_level"] = "ok"
        d["sla_label"] = "Resolved"
    elif age_seconds >= 4 * 86400:
        d["sla_level"] = "danger"
        d["sla_label"] = f"SLA breach · {days}d {hours}h"
    elif age_seconds >= 3 * 86400:
        d["sla_level"] = "warn"
        d["sla_label"] = f"Due soon · {days}d {hours}h"
    else:
        d["sla_level"] = "ok"
        d["sla_label"] = f"Open · {days}d {hours}h"
    return d


def decorate_rows(rows):
    now = local_now()
    return [ticket_sla(r, now) for r in rows]


def add_notification(title, body, ticket_id=None, actor_username=None, conn=None):
    now_text = local_now().strftime("%Y-%m-%d %H:%M")

    def _insert(c):
        users = c.execute("SELECT username FROM users").fetchall()
        rows = [
            (u["username"], title, body, ticket_id, now_text)
            for u in users
            if u["username"] != actor_username
        ]
        if rows:
            c.executemany(
                "INSERT INTO notifications(username,title,body,ticket_id,created_at) VALUES(?,?,?,?,?)",
                rows,
            )

    if conn is not None:
        _insert(conn)
    else:
        with db() as c:
            _insert(c)


def notification_feed(username, limit=12):
    with db() as c:
        unread = c.execute(
            "SELECT COUNT(*) FROM notifications n LEFT JOIN notification_reads r "
            "ON r.notification_id=n.id AND r.username=n.username "
            "WHERE n.username=? AND r.notification_id IS NULL",
            (username,),
        ).fetchone()[0]
        rows = c.execute(
            "SELECT n.id,n.title,n.body,n.ticket_id,n.created_at, "
            "CASE WHEN r.notification_id IS NULL THEN 0 ELSE 1 END AS is_read "
            "FROM notifications n LEFT JOIN notification_reads r "
            "ON r.notification_id=n.id AND r.username=n.username "
            "WHERE n.username=? ORDER BY n.id DESC LIMIT ?",
            (username, limit),
        ).fetchall()
    return int(unread), [dict(r) for r in rows]


def latest_chat_messages(limit=30):
    with db() as c:
        rows = c.execute(
            "SELECT username, display, message, created_at FROM chat_messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def record_activity(ticket_id, action, actor, details="", conn=None):
    now_text = local_now().strftime("%Y-%m-%d %H:%M:%S")

    def _insert(c):
        c.execute(
            "INSERT INTO ticket_activity(ticket_id,action,actor,details,created_at) VALUES(?,?,?,?,?)",
            (ticket_id, action, actor, details, now_text),
        )

    if conn is not None:
        _insert(conn)
    else:
        with db() as c:
            _insert(c)


def ticket_comment_data(ticket_ids, check_storage=False):
    if not ticket_ids:
        return {}, {}

    placeholders = ",".join("?" for _ in ticket_ids)
    with db() as c:
        comment_rows = c.execute(
            f"SELECT id, ticket_id, username, display, comment, created_at, file_folder_id "
            f"FROM ticket_comments WHERE ticket_id IN ({placeholders}) AND deleted_at IS NULL ORDER BY id ASC",
            tuple(ticket_ids),
        ).fetchall()

        attachment_rows = c.execute(
            "SELECT id, comment_id, filename, original_name, created_at, qnap_path, mime_type, size_bytes "
            "FROM comment_attachments "
            f"WHERE deleted_at IS NULL AND comment_id IN (SELECT id FROM ticket_comments WHERE ticket_id IN ({placeholders}) AND deleted_at IS NULL) "
            "ORDER BY id ASC",
            tuple(ticket_ids),
        ).fetchall()

    attachments_by_comment = {}
    for a in attachment_rows:
        item = dict(a)
        if check_storage:
            item["storage_state"] = _ticket_attachment_storage_state(a["qnap_path"]) if a["qnap_path"] else (
                "available" if DASHBOARD_UPLOAD_STORAGE.resolve("ticket", a["filename"]) else "missing")
        attachments_by_comment.setdefault(a["comment_id"], []).append(item)

    comments_by_ticket = {tid: [] for tid in ticket_ids}
    for row in comment_rows:
        item = dict(row)
        item["attachments"] = attachments_by_comment.get(row["id"], [])
        comments_by_ticket.setdefault(row["ticket_id"], []).append(item)

    meta = {}
    for tid in ticket_ids:
        items = comments_by_ticket.get(tid, [])
        meta[tid] = {
            "count": len(items),
            "latest": items[-1] if items else None,
        }

    return comments_by_ticket, meta


def ensure_public_token(conn, tid):
    row = conn.execute("SELECT public_token FROM tickets WHERE id=?", (tid,)).fetchone()
    if row and row["public_token"]:
        return row["public_token"]
    for _ in range(8):
        token = secrets.token_urlsafe(24)
        try:
            conn.execute("UPDATE tickets SET public_token=?, public_token_enabled=1 WHERE id=?", (token, tid))
            return token
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("Unable to create a unique public token.")


def user_can_delete_ticket_comment(ticket_row, comment_row=None):
    if not g.user:
        return False
    if g.user["role"] == "admin":
        return True
    display = str(g.user["display"] or "").strip().casefold()
    username = str(g.user["username"] or "").strip().casefold()
    reporter = str(ticket_row["pelapor"] or "").strip().casefold()
    support = str(ticket_row["support_id"] or "").strip().casefold()
    author_display = str(comment_row["display"] or "").strip().casefold() if comment_row else ""
    author_username = str(comment_row["username"] or "").strip().casefold() if comment_row else ""
    identities = {display, username}
    return (
        (bool(reporter) and reporter in identities)
        or (bool(support) and support in identities)
        or (bool(author_display) and author_display in identities)
        or (bool(author_username) and author_username in identities)
    )


def queue_qnap_delete_retry(conn, ticket_id, comment_id, attachment_id, qnap_path, physical_path, error, now_text):
    if not qnap_path and not physical_path:
        return
    conn.execute(
        "INSERT INTO qnap_delete_retry(ticket_id,comment_id,attachment_id,qnap_path,physical_path,error,attempts,created_at,last_attempt_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (ticket_id, comment_id, attachment_id, qnap_path or physical_path, physical_path or "", str(error)[:1000], 1, now_text, now_text),
    )


def public_ticket_payload(token):
    with db() as c:
        row = c.execute(
            "SELECT * FROM tickets WHERE public_token=? AND COALESCE(public_token_enabled,1)=1",
            (token,),
        ).fetchone()
        if not row:
            return None
        tid = int(row["id"])
        comments_by_ticket, comment_meta = ticket_comment_data([tid], check_storage=True)
        for comment in comments_by_ticket.get(tid, []):
            for attachment in comment.get("attachments", []):
                attachment["download_url"] = f"/t/{quote(token)}/comment-file/{int(attachment['id'])}"
        storage = None
        try:
            storage = _ensure_ticket_storage_tree(tid, row["no_tiket"])
        except Exception:
            app.logger.warning("Public ticket folder unavailable for %s", row["no_tiket"], exc_info=True)
        files = []
        if storage:
            prefix = storage["ticket_path"].rstrip("/") + "/"
            file_rows = c.execute(
                "SELECT id, original_name, mime_type, size_bytes, qnap_path, created_at "
                "FROM file_items WHERE qnap_path LIKE ? ORDER BY created_at DESC, id DESC",
                (prefix + "%",),
            ).fetchall()
            for item in file_rows:
                qnap_path = item["qnap_path"] or ""
                if not qnap_path:
                    continue
                files.append({
                    "id": int(item["id"]),
                    "name": item["original_name"],
                    "mime_type": item["mime_type"],
                    "size_bytes": int(item["size_bytes"] or 0),
                    "created_at": item["created_at"],
                    "download_url": f"/t/{quote(token)}/file/{int(item['id'])}",
                })
    ticket = ticket_sla(row)
    return {
        "ok": True,
        "ticket": {
            "no_tiket": ticket["no_tiket"],
            "status": ticket["status"],
            "priority": ticket["priority"],
            "category": ticket["category"],
            "masalah": ticket["masalah"],
            "support_id": ticket["support_id"],
            "sla_label": ticket.get("sla_label", ""),
            "created": f"{ticket['tanggal']} {ticket['jam']}",
        },
        "comments": comments_by_ticket.get(int(row["id"]), []),
        "comment_meta": comment_meta.get(int(row["id"]), {"count": 0, "latest": None}),
        "files": files,
    }


def parse_local_datetime(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
    except (TypeError, ValueError):
        return None


def parse_form_datetime(value, fallback=None):
    """Parse HTML datetime-local input as WIB. Accept both with and without seconds."""
    value = (value or "").strip()
    if not value:
        return fallback
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=LOCAL_TZ)
        except ValueError:
            continue
    return None


def ticket_case_datetime(row):
    """Return the incident/case datetime used for ticket age and SLA."""
    d = dict(row) if not isinstance(row, dict) else row
    date_text = d.get("case_tanggal") or d.get("tanggal")
    time_text = d.get("case_jam") or d.get("jam")
    try:
        return datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
    except (TypeError, ValueError):
        return local_now()


def format_duration(seconds):
    if seconds is None:
        return "â€”"
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def operational_metrics(rows):
    now = local_now()
    total_seconds = 0
    completed_count = 0
    aging = {"0–1 day": 0, "2–3 days": 0, "4–7 days": 0, "8+ days": 0}
    for row in rows:
        d = ticket_sla(row, now)
        # Match Open Ticket Queue: every ticket that has not been completed
        # must be included, including In Progress and other active statuses.
        if d.get("status") not in COMPLETED_STATUSES:
            days = d.get("age_days", 0)
            if days <= 1:
                aging["0–1 day"] += 1
            elif days <= 3:
                aging["2–3 days"] += 1
            elif days <= 7:
                aging["4–7 days"] += 1
            else:
                aging["8+ days"] += 1
        completed_at = row["completed_at"] if "completed_at" in row.keys() else None
        if row["status"] in COMPLETED_STATUSES and completed_at:
            created = ticket_case_datetime(row)
            completed = parse_local_datetime(completed_at)
            if completed:
                total_seconds += max(0, int((completed - created).total_seconds()))
                completed_count += 1
    avg_seconds = round(total_seconds / completed_count) if completed_count else None
    return {
        "avg_resolution": format_duration(avg_seconds),
        "avg_resolution_count": completed_count,
        "aging": aging,
        "aging_total": sum(aging.values()),
    }


def csv_safe(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


# ---------- hooks ----------
@app.before_request
def load_user():
    g._request_started = time.perf_counter()
    g.user = None
    # POLO-R65-BACKEND-PERFORMANCE: files in Flask static/ are public, skip user DB lookup
    if request.endpoint == "static":
        return
    # Enforce both server-side clocks before fetching user data. Pre-R85
    # sessions lack timestamps and must sign in again (fail closed).
    if session.get("user"):
        idle_left, absolute_left = auth_remaining_seconds(session)
        if idle_left <= 0 or absolute_left <= 0:
            session.clear()
            g.auth_expired = True
    if session.get("user") == "natan":
        session["user"] = "nathan"
    if session.get("user"):
        with db() as c:
            g.user = c.execute("SELECT * FROM users WHERE username=?",
                               (session["user"],)).fetchone()
        if not g.user:
            session.clear()
    if request.endpoint in (None, "login", "static", "api_session", "api_open_config", "open_ticket", "public_ticket_page", "public_ticket_api", "public_comment_file", "public_ticket_comment_file", "guides.public_guide_page", "guides.public_guide_data", "guides.public_guide_media", "guides.public_guide_document", "guides.public_guide_video_stream"):
        return
    if not g.user:
        if request.path.startswith("/api/") or "application/json" in (request.headers.get("Accept") or ""):
            return jsonify(ok=False, error="Authentication required.", authenticated=False), 401
        return redirect(url_for("login", next=request.full_path.rstrip("?")))
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        csrf_token = request.form.get("csrf", "") or request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(csrf_token, session.get("csrf", "")):
            abort(400)


@app.context_processor
def inject():
    messages = get_flashed_messages(with_categories=True)
    toast_html = ''.join(
        f'<div class="toast toast-{escape(c)}" role="status" aria-live="polite">'
        f'<span class="toast-message">{escape(m)}</span>'
        f'<button type="button" class="toast-close" aria-label="Close notification">Ã—</button>'
        f'</div>'
        for c, m in messages
    )
    return {"csrf": session.get("csrf", ""), "theme_head": THEME_HEAD,
            "theme_btn": THEME_BTN, "theme_js": THEME_JS,
            "completed_statuses": COMPLETED_STATUSES,
            "alerts": f'<div class="toast-stack" id="toast-stack">{toast_html}</div>' if toast_html else ''}


@app.after_request
def headers(resp):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and resp.status_code < 400:
        _invalidate_query_cache()
    started = getattr(g, "_request_started", None)
    if started is not None:
        elapsed_ms = (time.perf_counter() - started) * 1000
        resp.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
        if elapsed_ms >= 500:
            # Public Guide URLs contain bearer tokens. Never log those tokens.
            logged_path = re.sub(r'^(/(?:api/public/)?g/)[A-Za-z0-9_-]+', r'\1[redacted]', request.path)
            app.logger.warning("Slow HTTP %s %s -> %.1f ms", request.method, logged_path, elapsed_ms)
    # Header role is known from the authenticated request: avoid navigation flicker.
    if (g.user and g.user["role"] == "admin" and
            (request.path in {"/", "/files", "/ticket", "/admin/tools", "/admin/tickets", "/our-project", "/guide", "/guide/new"}
             or re.fullmatch(r"/(?:our-project|guide)/[0-9]+", request.path))
            and resp.mimetype == "text/html"):
        resp.direct_passthrough = False
        markup = resp.get_data(as_text=True)
        markup = markup.replace('class="admin-only admin-nav-dropdown" hidden',
                                'class="admin-only admin-nav-dropdown"')
        markup = markup.replace('class="admin-only admin-nav-dropdown active" hidden',
                                'class="admin-only admin-nav-dropdown active"')
        resp.set_data(markup)
    # Every authenticated HTML page gets the same lightweight activity
    # tracker, including dashboard, ticket details, File Center and Workspace.
    # HTML is already served with no-store; API polling never sends heartbeat.
    if g.user and resp.status_code == 200 and resp.mimetype == "text/html" and request.endpoint != "static":
        resp.direct_passthrough = False
        html = resp.get_data(as_text=True)
        if "</body>" in html:
            remain = auth_expiry_payload(session)
            csrf_attr = html_escape(str(session.get("csrf", "")), quote=True)
            tracker = (
                '<script src="/static/js/session-activity-v85.js?v=r85" defer '
                f'data-csrf="{csrf_attr}" '
                f'data-idle-seconds="{remain["idle_seconds_remaining"]}" '
                f'data-absolute-seconds="{remain["absolute_seconds_remaining"]}"></script>'
            )
            resp.set_data(html.replace("</body>", tracker + "</body>", 1))
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    # POLO-R65-BACKEND-PERFORMANCE: reduce repeat 304 requests on same-origin static assets
    if request.endpoint == "static" and request.path.lower().endswith((".woff2", ".woff", ".ttf", ".otf")):
        resp.headers["Cache-Control"] = "public, max-age=86400"
    elif request.endpoint == "static" and request.path.lower().endswith((".css", ".js")):
        resp.headers["Cache-Control"] = "public, max-age=180, must-revalidate"
    else:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    if ("gzip" in (request.headers.get("Accept-Encoding") or "").lower()
            and "Content-Encoding" not in resp.headers
            and resp.status_code not in (204, 304)
            and resp.direct_passthrough is False
            and (resp.mimetype or "").lower() in {"text/html", "text/css", "application/javascript", "text/javascript", "application/json"}):
        body=resp.get_data()
        if len(body) >= 1024:
            resp.set_data(gzip.compress(body, compresslevel=5, mtime=0))
            resp.headers["Content-Encoding"]="gzip"
            resp.headers["Vary"]="Accept-Encoding"
            resp.headers["Content-Length"]=str(len(resp.get_data()))
    return resp


@app.errorhandler(413)
def too_big(e):
    flash("File is too large. The maximum library file size is 8 GB.", "err")
    return redirect(request.referrer or "/files")


# ---------- static frontend session/api ----------
def division_list(active_only=False):
    with db() as c:
        if active_only:
            rows = c.execute("SELECT id,name,is_active FROM divisions WHERE is_active=1 ORDER BY name COLLATE NOCASE").fetchall()
        else:
            rows = c.execute("SELECT id,name,is_active FROM divisions ORDER BY is_active DESC,name COLLATE NOCASE").fetchall()
    return [dict(row) for row in rows]


def valid_division(value):
    name = str(value or "").strip()
    if not name:
        return None
    with db() as c:
        row = c.execute("SELECT name FROM divisions WHERE name=? COLLATE NOCASE AND is_active=1", (name,)).fetchone()
    return row["name"] if row else None


def check_division(payload):
    value = str(payload.get("division") or "").strip()
    division = valid_division(value)
    if not division:
        raise ValueError("Select an active Division.")
    return division


@app.get("/api/open/config")
def api_open_config():
    if not session.get("csrf"):
        session["csrf"] = secrets.token_hex(16)
    return jsonify(ok=True, csrf=session["csrf"], support_users=SUPPORT_USERS, support_require_evidence=support_evidence_settings(), categories=CATEGORIES, priorities=PRIORITIES, divisions=[x["name"] for x in division_list(active_only=True)])

@app.get("/api/session")
def api_session():
    if not g.user:
        return jsonify(ok=False, authenticated=False, csrf=session.get("csrf", "")), 401
    unread, notifications = notification_feed(g.user["username"])
    return jsonify(ok=True, authenticated=True, csrf=session.get("csrf", ""), user={
        "username":g.user["username"], "display":g.user["display"], "role":g.user["role"]
    }, unread_notifications=unread, notifications=notifications, messages=get_flashed_messages(with_categories=True))

@app.get("/api/session/security")
def api_session_security():
    """Lightweight non-renewing session verification for restored/multi-tab pages."""
    return jsonify(ok=True, session=auth_expiry_payload(session))


@app.post("/api/session/heartbeat")
def api_session_heartbeat():
    """Refresh idle activity only after a signed-in CSRF-verified human event.

    before_request validates idle/absolute expiration and the CSRF token.
    Automated dashboard GET polling cannot extend either expiry clock.
    """
    session["auth_last_active_at"] = time.time()
    return jsonify(ok=True, session=auth_expiry_payload(session))


@app.get("/api/dashboard")
def api_dashboard():
    q=request.args.get("q","").strip()
    support_status=(request.args.get("support_status") or "all").strip()
    support_owner=(request.args.get("support_owner") or "all").strip()
    if support_status not in SUPPORT_STATUS_FILTERS: support_status="all"
    if support_owner != "all" and support_owner not in SUPPORT_USERS: support_owner="all"
    ticket_start_date,ticket_end_date,ticket_all=get_ticket_range()
    rows=decorate_rows(query(ticket_start_date,ticket_end_date,q))
    if support_owner != "all": rows=[r for r in rows if (r["support_id"] or UNASSIGNED).strip()==support_owner]

    # Top-level dashboard statistics stay independent from the Support Team
    # status filter. The filter is applied only to the Support Team board,
    # matching the original dashboard behavior.
    support_rows = rows
    allowed_support_statuses = SUPPORT_STATUS_FILTERS.get(support_status)
    if allowed_support_statuses is not None:
        support_rows = [r for r in support_rows if r["status"] in allowed_support_statuses]

    ticket_ids=[r["id"] for r in rows]
    comments_by_ticket,comment_meta=ticket_comment_data(ticket_ids)
    display_owners=SUPPORT_USERS+[UNASSIGNED]
    per={p:[dict(r) for r in support_rows if (r["support_id"] or UNASSIGNED).strip()==p] for p in display_owners}
    stat={}
    for p in display_owners:
        done=sum(1 for r in per[p] if r["status"] in COMPLETED_STATUSES); n=len(per[p])
        stat[p]={"total":n,"done":done,"open":n-done,"pct":round(done*100/n) if n else 0}
    n_done=sum(1 for r in rows if r["status"] in COMPLETED_STATUSES); total=len(rows)
    all_rows=decorate_rows(query(None,None,"")); open_queue_rows=[r for r in all_rows if r["status"] not in COMPLETED_STATUSES]
    open_queue=sorted(open_queue_rows,key=lambda r:(0 if r["sla_level"]=="danger" else 1,r["tanggal"],r["jam"],r["id"]))
    # Show every active ticket in the scrollable queue, not just the first eight.
    # Otherwise older SLA tickets can push new user-created tickets out of sight.
    open_queue_preview=(open_queue if DASHBOARD_QUEUE_PREVIEW_LIMIT is None
                        else open_queue[:DASHBOARD_QUEUE_PREVIEW_LIMIT])
    operational=operational_metrics(all_rows)
    # POLO-R65-BACKEND-PERFORMANCE: reuse all_rows when no search query is applied
    chart_start_date,chart_end_date=get_chart_range()
    chart_rows=(decorate_rows(query(None,None,q)) if q else all_rows)
    bars,chart_total=build_chart_data(chart_rows,chart_start_date,chart_end_date)
    # POLO-R65-BACKEND-PERFORMANCE: identical count using the same all_rows snapshot
    today_text=local_now().strftime("%Y-%m-%d")
    today_total=sum(1 for r in all_rows if r["tanggal"]==today_text)
    resolved_today=sum(1 for r in all_rows if r["status"] in COMPLETED_STATUSES and r["completed_at"] and r["completed_at"][:10]==today_text)
    unread_notifications,notifications=notification_feed(g.user["username"])
    return jsonify(ok=True, support_users=SUPPORT_USERS, support_require_evidence=support_evidence_settings(), support_colors=SUPPORT_COLORS, priorities=PRIORITIES, categories=CATEGORIES, divisions=[x["name"] for x in division_list(active_only=True)], statuses=TICKET_STATUSES,
        allowed=([UNASSIGNED]+SUPPORT_USERS) if g.user["role"]=="admin" else [UNASSIGNED,g.user["display"]], unassigned=UNASSIGNED, completed_statuses=list(COMPLETED_STATUSES),
        bars=bars,total=total,n_done=n_done,n_open=total-n_done,all_open_count=len(open_queue),unassigned_open_count=sum(1 for r in open_queue if (r["support_id"] or UNASSIGNED).strip()==UNASSIGNED),sla_breach_count=sum(1 for r in open_queue if r["sla_level"]=="danger"),today_total=today_total,resolved_today=resolved_today,
        unread_notifications=unread_notifications,notifications=notifications,chat_messages=latest_chat_messages(),open_queue=[dict(r) for r in open_queue_preview],open_queue_all=[dict(r) for r in open_queue],open_queue_preview_limit=DASHBOARD_QUEUE_PREVIEW_LIMIT,
        stat=stat,per=per,comment_meta=comment_meta,aging=operational["aging"],aging_total=operational["aging_total"],chart_start=chart_start_date.strftime("%Y-%m-%d"),chart_end=chart_end_date.strftime("%Y-%m-%d"),chart_total=chart_total,
        ticket_start=ticket_start_date.strftime("%Y-%m-%d") if ticket_start_date else "",ticket_end=ticket_end_date.strftime("%Y-%m-%d") if ticket_end_date else "",ticket_all=ticket_all,
        ticket_filter_label=format_filter_label(ticket_start_date,ticket_end_date,ticket_all),is_admin=(g.user["role"]=="admin"))


@app.route("/admin/tools")
def admin_tools():
    if g.user["role"] != "admin":
        abort(403)
    return send_from_directory(STATIC_PAGE_DIR, "admin_tools.html")



@app.route("/admin/support-users")
def admin_support_users_page():
    if g.user["role"] != "admin": abort(403)
    return send_from_directory(STATIC_PAGE_DIR, "support_users.html")

@app.get("/api/admin/support-users")
def admin_support_users_list():
    if g.user["role"] != "admin": abort(403)
    with db() as c:
        rows = c.execute("SELECT username,display FROM users WHERE role='support' ORDER BY display").fetchall()
    return jsonify(ok=True, users=[dict(row) for row in rows])

@app.post("/api/admin/support-users")
def admin_support_users_create():
    if g.user["role"] != "admin": abort(403)
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip().lower()
    display = str(payload.get("display") or "").strip()
    password = str(payload.get("password") or "")
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,31}", username):
        return jsonify(ok=False,error="Username must be 3-32 letters/numbers/._-."),400
    if not (2 <= len(display) <= 48) or not re.fullmatch(r"[\w .'-]+",display,re.UNICODE):
        return jsonify(ok=False,error="Invalid display name."),400
    if len(password) < 8:
        return jsonify(ok=False,error="Password must be at least 8 characters."),400
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE username=? OR lower(display)=lower(?)",(username,display)).fetchone():
            return jsonify(ok=False,error="Username or display name already exists."),409
        c.execute("INSERT INTO users(username,display,role,password_hash) VALUES(?,?,?,?)",
                  (username,display,"support",generate_password_hash(password,method="pbkdf2:sha256")))
        c.execute("INSERT OR IGNORE INTO support_settings(support_id,require_evidence,updated_by,updated_at) VALUES(?,?,?,?)",(display,0,g.user["username"],local_now().strftime("%Y-%m-%d %H:%M:%S")))
    if display not in SUPPORT_USERS: SUPPORT_USERS.append(display)
    SUPPORT_COLORS.setdefault(display,"#8193aa")
    return jsonify(ok=True,message="Support user created."),201

@app.delete("/api/admin/support-users/<username>")
def admin_support_users_delete(username):
    if g.user["role"] != "admin": abort(403)
    with db() as c:
        row=c.execute("SELECT display FROM users WHERE username=? AND role='support'",(username,)).fetchone()
        if not row:return jsonify(ok=False,error="Support user not found."),404
        display=row["display"]
        if c.execute("SELECT 1 FROM tickets WHERE support_id=? LIMIT 1",(display,)).fetchone():
            return jsonify(ok=False,error="Support has ticket history. Reassign/archive tickets before deletion."),409
        c.execute("DELETE FROM users WHERE username=? AND role='support'",(username,))
        c.execute("DELETE FROM support_settings WHERE support_id=?",(display,))
    if display in SUPPORT_USERS:SUPPORT_USERS.remove(display)
    SUPPORT_COLORS.pop(display,None)
    return jsonify(ok=True,message="Support user deleted.")

@app.get("/api/admin/divisions")
def api_admin_divisions():
    if g.user["role"] != "admin": abort(403)
    return jsonify(ok=True, divisions=division_list())


@app.post("/api/admin/divisions")
def api_admin_divisions_create():
    if g.user["role"] != "admin": abort(403)
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not (2 <= len(name) <= 60) or not re.fullmatch(r"[\w &().,'/-]+", name, re.UNICODE):
        return jsonify(ok=False, error="Division must be 2–60 characters (letters, numbers and common punctuation)."), 400
    try:
        with db() as c:
            c.execute("INSERT INTO divisions(name,is_active,created_at) VALUES(?,1,?)",
                      (name,local_now().strftime("%Y-%m-%d %H:%M:%S")))
    except sqlite3.IntegrityError:
        return jsonify(ok=False, error="Division already exists. Reactivate it instead."), 409
    return jsonify(ok=True, divisions=division_list(), message="Division added."), 201


@app.patch("/api/admin/divisions/<int:division_id>")
def api_admin_divisions_modify(division_id):
    if g.user["role"] != "admin": abort(403)
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    has_name = name is not None
    has_status = isinstance(payload.get("is_active"), bool)
    if not (has_name or has_status):
        return jsonify(ok=False,error="Provide name or is_active."), 400
    if has_name:
        name = str(name).strip()
        if not (2 <= len(name) <= 60) or not re.fullmatch(r"[\w &().,'/-]+", name, re.UNICODE):
            return jsonify(ok=False,error="Invalid division name."), 400
    try:
        with db() as c:
            old = c.execute("SELECT name,is_active FROM divisions WHERE id=?", (division_id,)).fetchone()
            if not old:
                return jsonify(ok=False,error="Division not found."), 404
            new_name = name if has_name else old["name"]
            active = int(payload["is_active"]) if has_status else old["is_active"]
            c.execute("UPDATE divisions SET name=?,is_active=?,updated_at=? WHERE id=?",
                      (new_name,active,local_now().strftime("%Y-%m-%d %H:%M:%S"),division_id))
            # Preserve the association of existing tickets with the renamed division.
            if new_name != old["name"]:
                c.execute("UPDATE tickets SET division=? WHERE division=? COLLATE NOCASE", (new_name,old["name"]))
    except sqlite3.IntegrityError:
        return jsonify(ok=False,error="A division with that name already exists."), 409
    _invalidate_query_cache()
    return jsonify(ok=True,divisions=division_list(),message="Division updated.")


@app.get("/api/admin/tools/evidence")
def api_admin_tools_evidence():
    if g.user["role"] != "admin":
        return jsonify(ok=False, error="Admin access required."), 403
    return jsonify(ok=True, support_users=SUPPORT_USERS, settings=support_evidence_settings())


@app.post("/api/admin/tools/evidence")
def api_admin_tools_evidence_update():
    if g.user["role"] != "admin":
        return jsonify(ok=False, error="Admin access required."), 403
    payload = request.get_json(silent=True) or {}
    support_id = str(payload.get("support_id") or "").strip()
    if support_id not in SUPPORT_USERS:
        return jsonify(ok=False, error="Invalid Support."), 400
    require_evidence = 1 if bool(payload.get("require_evidence")) else 0
    now_text = local_now().strftime("%Y-%m-%d %H:%M:%S")
    with db() as c:
        c.execute(
            """INSERT INTO support_settings(support_id,require_evidence,updated_by,updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(support_id) DO UPDATE SET
               require_evidence=excluded.require_evidence,
               updated_by=excluded.updated_by,
               updated_at=excluded.updated_at""",
            (support_id, require_evidence, g.user["display"], now_text),
        )
    return jsonify(ok=True, support_id=support_id, require_evidence=bool(require_evidence), settings=support_evidence_settings())

@app.get("/api/admin/tickets")
def api_admin_tickets():
    if g.user["role"]!="admin": return jsonify(ok=False,error="Admin access required."),403
    month=(request.args.get("month") or "").strip(); q=(request.args.get("q") or "").strip()[:100]
    try: page=max(1,int(request.args.get("page") or 1))
    except ValueError: page=1
    requested_page=page
    page_size=50
    rows,start_date,end_date,total,open_count,resolved_count=query_month_page(month,q,page,page_size)
    total_pages=max(1,(total+page_size-1)//page_size); page=min(page,total_pages)
    if page!=requested_page: rows,start_date,end_date,total,open_count,resolved_count=query_month_page(month,q,page,page_size)
    decorated=[dict(x) for x in decorate_rows(rows)]
    return jsonify(ok=True,rows=decorated,month=start_date.strftime("%Y-%m"),month_label=start_date.strftime("%B %Y"),q=q,total=total,open_count=open_count,resolved_count=resolved_count,page=page,page_size=page_size,total_pages=total_pages,start_date=start_date.strftime("%Y-%m-%d"),end_date=end_date.strftime("%Y-%m-%d"),support_users=SUPPORT_USERS,unassigned=UNASSIGNED,categories=CATEGORIES,priorities=PRIORITIES,statuses=TICKET_STATUSES,divisions=[x["name"] for x in division_list(active_only=True)])

# ---------- auth ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect("/")
    login_error = None
    if request.method == "POST":
        key = "login:" + (request.remote_addr or "?")
        u = request.form.get("username", "").strip().lower()
        pw = request.form.get("password", "")
        if locked(key):
            login_error = "Too many attempts. Please try again in a few minutes."
        else:
            with db() as c:
                row = c.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
            if row and check_password_hash(row["password_hash"], pw):
                session.clear()
                session["user"] = row["username"]
                session["csrf"] = secrets.token_hex(16)
                auth_now = time.time()
                session["auth_started_at"] = auth_now
                session["auth_last_active_at"] = auth_now
                session.permanent = True
                nxt = request.args.get("next", "/open" if row["role"] == "staff" else "/")
                if not nxt.startswith("/") or nxt.startswith("//"):
                    nxt = "/"
                return redirect(nxt)
            register_fail(key)
            login_error = "Invalid username or password."
    if login_error:
        return redirect("/login?error=" + quote(login_error) + (("&next=" + quote(request.args["next"], safe="")) if "next" in request.args else ""))
    return send_from_directory(STATIC_PAGE_DIR, "login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/login")


@app.route("/password", methods=["GET", "POST"])
def password():
    if request.method == "POST":
        old = request.form.get("old", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        key = "pw:" + g.user["username"]
        if locked(key):
            flash("Too many attempts. Try again later.", "err")
        elif not check_password_hash(g.user["password_hash"], old):
            register_fail(key)
            flash("Current password is incorrect.", "err")
        elif len(new) < 6:
            flash("New password must be at least 6 characters.", "err")
        elif new != confirm:
            flash("New password and confirmation do not match.", "err")
        else:
            with db() as c:
                c.execute("UPDATE users SET password_hash=? WHERE username=?",
                          (generate_password_hash(new, method="pbkdf2:sha256"),
                           g.user["username"]))
            return redirect("/?password=changed")
        messages = get_flashed_messages(with_categories=True)
        error = next((m for cat, m in messages if cat == "err"), "")
        return redirect("/password?error=" + quote(error)) if error else redirect("/password")
    return send_from_directory(STATIC_PAGE_DIR, "password.html")


# ---------- dashboard ----------
def get_chart_range():
    now = local_now().date()
    default_start = now
    default_end = now

    raw_start = (request.args.get("chart_start") or "").strip()
    raw_end = (request.args.get("chart_end") or "").strip()

    def parse_date(value, fallback):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return fallback

    start = parse_date(raw_start, default_start)
    end = parse_date(raw_end, default_end)
    if start > end:
        start, end = end, start
    return start, end


def build_chart_data(rows, start_date, end_date):
    """Build current ownership/performance for the selected range.

    Open tickets remain visible after their creation date until they are completed.
    Completed tickets are counted when they were created in the selected range.
    This keeps an unresolved ticket from disappearing from the next day's chart.
    """
    start_text = start_date.strftime("%Y-%m-%d")
    end_text = end_date.strftime("%Y-%m-%d")

    def completed_by_end(row):
        if row["status"] not in COMPLETED_STATUSES:
            return False
        completed_at = row.get("completed_at") if isinstance(row, dict) else row["completed_at"]
        # Legacy completed tickets may have no completed_at timestamp.
        # Their current completed status must not be displayed as Open.
        return not completed_at or str(completed_at)[:10] <= end_text

    # Completed tickets follow the selected creation-date range. Open tickets
    # are included from any earlier date as long as they existed by the end of
    # the range and were still unresolved at that point.
    filtered = []
    for r in rows:
        created = str(r.get("tanggal") or "")
        if not created or created > end_text:
            continue
        if r["status"] not in COMPLETED_STATUSES:
            filtered.append(r)
        elif start_text <= created <= end_text:
            filtered.append(r)

    chart_per = {p: [r for r in filtered if (r["support_id"] or UNASSIGNED).strip() == p] for p in SUPPORT_USERS}
    stat = {}
    for p in SUPPORT_USERS:
        done = sum(1 for r in chart_per[p] if completed_by_end(r))
        breach = sum(1 for r in chart_per[p] if not completed_by_end(r) and r.get("sla_level") == "danger")
        n = len(chart_per[p])
        open_count = n - done
        open_normal = max(0, open_count - breach)
        stat[p] = {"total": n, "done": done, "open": open_count, "open_normal": open_normal, "breach": breach,
                   "pct": round(done * 100 / n) if n else 0}
    max_val = max([s["total"] for s in stat.values()] + [1])
    bars = [{"name": p, "color": SUPPORT_COLORS[p], **stat[p],
             "done_bar_pct": round(stat[p]["done"] * 100 / max_val, 1),
             "open_bar_pct": round(stat[p]["open_normal"] * 100 / max_val, 1),
             "breach_bar_pct": round(stat[p]["breach"] * 100 / max_val, 1)} for p in SUPPORT_USERS]
    return bars, sum(s["total"] for s in stat.values())


def _support_cards_data(start_date=None, end_date=None, ticket_all=False, q="", limit_active=40, limit_closed=20):
    key=("support-cards", start_date.isoformat() if start_date else None, end_date.isoformat() if end_date else None, bool(ticket_all), q, limit_active, limit_closed)
    cached=_query_cache_get(key)
    if cached is not None:
        return cached

    # Closed history follows the selected filter, while unresolved tickets are
    # always carried forward from earlier dates so an open ticket created today
    # remains visible in the Support card tomorrow and on later days.
    closed_where=["status IN ('Resolved','Closed','Done')"]
    closed_args=[]
    if start_date is not None and end_date is not None:
        closed_where.append("tanggal BETWEEN ? AND ?")
        closed_args += [start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")]
    if q:
        closed_where.append("(no_tiket LIKE ? OR pelapor LIKE ? OR masalah LIKE ? OR tindakan LIKE ? OR support_id LIKE ?)")
        closed_args += [f"%{q}%"]*5
    closed_sql=" AND ".join(closed_where)

    active_where=["status NOT IN ('Resolved','Closed','Done')"]
    active_args=[]
    if q:
        active_where.append("(no_tiket LIKE ? OR pelapor LIKE ? OR masalah LIKE ? OR tindakan LIKE ? OR support_id LIKE ?)")
        active_args += [f"%{q}%"]*5
    active_sql=" AND ".join(active_where)

    stat={p:{"total":0,"done":0,"open":0,"pct":0} for p in SUPPORT_USERS}
    per={p:[] for p in SUPPORT_USERS}
    ticket_ids=[]
    started=time.perf_counter()
    with db() as c:
        active_summary=c.execute(
            f"SELECT COALESCE(NULLIF(TRIM(support_id),''), ?) AS owner, COUNT(*) AS total "
            f"FROM tickets WHERE {active_sql} GROUP BY COALESCE(NULLIF(TRIM(support_id),''), ?)",
            active_args+[UNASSIGNED,UNASSIGNED],
        ).fetchall()
        closed_summary=c.execute(
            f"SELECT COALESCE(NULLIF(TRIM(support_id),''), ?) AS owner, COUNT(*) AS total "
            f"FROM tickets WHERE {closed_sql} GROUP BY COALESCE(NULLIF(TRIM(support_id),''), ?)",
            closed_args+[UNASSIGNED,UNASSIGNED],
        ).fetchall()
        active_map={r["owner"]:int(r["total"] or 0) for r in active_summary}
        closed_map={r["owner"]:int(r["total"] or 0) for r in closed_summary}
        for p in SUPPORT_USERS:
            open_count=active_map.get(p,0)
            done_count=closed_map.get(p,0)
            total=open_count+done_count
            stat[p]={"total":total,"done":done_count,"open":open_count,"pct":round(done_count*100/total) if total else 0}

        for p in SUPPORT_USERS:
            active_rows=c.execute(
                f"SELECT * FROM tickets WHERE {active_sql} AND COALESCE(NULLIF(TRIM(support_id),''), ?) = ? "
                f"ORDER BY tanggal ASC,jam ASC,id ASC LIMIT ?",
                active_args+[UNASSIGNED,p,limit_active],
            ).fetchall()
            closed_rows=c.execute(
                f"SELECT * FROM tickets WHERE {closed_sql} AND COALESCE(NULLIF(TRIM(support_id),''), ?) = ? "
                f"ORDER BY completed_at DESC,tanggal DESC,jam DESC,id DESC LIMIT ?",
                closed_args+[UNASSIGNED,p,limit_closed],
            ).fetchall()
            rows=[dict(r) for r in active_rows]+[dict(r) for r in reversed(closed_rows)]
            per[p]=rows
            ticket_ids.extend(int(r["id"]) for r in rows)
    comments_by_ticket, comment_meta = ticket_comment_data(ticket_ids)
    payload=(per,stat,comments_by_ticket,comment_meta)
    _query_cache_set(key,payload)
    elapsed=(time.perf_counter()-started)*1000
    if elapsed>=100:
        app.logger.warning("Slow support cards query: %.1f ms",elapsed)
    return payload


@app.get("/api/support-cards")
def api_support_cards():
    support_status=(request.args.get("support_status") or "all").strip()
    if support_status not in SUPPORT_STATUS_FILTERS:
        support_status="all"
    ticket_all=request.args.get("ticket_all") == "1"
    start_date=parse_date_arg(request.args.get("ticket_start"))
    end_date=parse_date_arg(request.args.get("ticket_end"))
    if not ticket_all and not (start_date and end_date):
        start_date=end_date=local_now().date()
    if start_date and end_date and start_date>end_date:
        start_date,end_date=end_date,start_date
    q=(request.args.get("q") or "").strip()[:100]
    per,stat,comments_by_ticket,comment_meta=_support_cards_data(start_date,end_date,ticket_all,q)
    return jsonify(ok=True, support_users=SUPPORT_USERS, support_colors=SUPPORT_COLORS, stat=stat, per={k:[dict(x) for x in v] for k,v in per.items()}, comment_meta=comment_meta, completed_statuses=list(COMPLETED_STATUSES))


@app.route("/")
def index():
    return send_from_directory(STATIC_PAGE_DIR, "dashboard.html")


@app.route("/open", methods=["GET", "POST"])
def open_ticket():
    if request.method == "GET":
        return send_from_directory(STATIC_PAGE_DIR, "open.html")

    f = request.form
    support_id = (f.get("support_id") or "").strip()
    support_id = support_id if support_id in SUPPORT_USERS else "Naufal"
    pelapor = (f.get("staff_name") or f.get("pelapor") or "").strip()[:100]
    masalah = (f.get("masalah") or "").strip()[:2000]
    priority = f.get("priority") if f.get("priority") in PRIORITIES else "Medium"
    category = f.get("category") if f.get("category") in CATEGORIES else "Other"
    try:
        division = check_division(f)
    except ValueError as exc:
        return redirect("/open?error=" + quote_plus(str(exc)))
    foto_file = request.files.get("foto")
    has_photo = bool(foto_file and foto_file.filename)

    if support_requires_evidence(support_id) and not has_photo:
        return redirect("/open?error=" + quote_plus(f"Evidence is required for {support_id}."))
    if not pelapor or not masalah:
        return redirect("/open?error=" + quote_plus("Your name and issue are required."))
    try:
        whatsapp = normalize_whatsapp(f.get("whatsapp", ""))
    except ValueError as e:
        return redirect("/open?error=" + quote_plus(str(e)))

    now = local_now()
    foto_name = None
    if has_photo:
        try:
            foto_name = save_photo(foto_file)
            try:
                foto_file.stream.seek(0)
            except (OSError, ValueError):
                pass
        except (ValueError, OSError) as e:
            return redirect("/open?error=" + quote_plus(str(e)))

    tid = None
    no = None
    try:
        with db() as c:
            cur = c.execute(
                "INSERT INTO tickets(tanggal,jam,case_tanggal,case_jam,pelapor,masalah,tindakan,support_id,status,foto,dibuat_oleh,tanpa_foto,whatsapp,priority,category,division) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), now.strftime("%Y-%m-%d"), now.strftime("%H:%M"),
                 pelapor, masalah, "", support_id, "Open", foto_name, "staff", 0 if has_photo else 1, whatsapp, priority, category, division),
            )
            tid = cur.lastrowid
            no = f"TKT-{now:%Y%m%d}-{tid:04d}"
            c.execute("UPDATE tickets SET no_tiket=? WHERE id=?", (no, tid))
            _ensure_ticket_storage_tree(tid, no, conn=c)

        if has_photo:
            _save_initial_evidence_to_qnap(tid, no, foto_file, "staff", pelapor, "Initial evidence attached")

        _invalidate_query_cache()
        record_activity(tid, "Ticket created", pelapor, f"Assigned to {support_id} · {division} · {category} · {priority}")
        add_notification("New ticket created", f"{no} · {category} · {priority} assigned to {support_id}.", tid, "staff")
        return redirect(f"/static/pages/open_success.html?ticket={quote_plus(no)}&support={quote_plus(support_id)}")
    except Exception as exc:
        app.logger.exception("Open ticket creation failed")
        if tid:
            with db() as c:
                c.execute("DELETE FROM comment_attachments WHERE comment_id IN (SELECT id FROM ticket_comments WHERE ticket_id=?)", (tid,))
                c.execute("DELETE FROM ticket_comments WHERE ticket_id=?", (tid,))
                c.execute("DELETE FROM tickets WHERE id=?", (tid,))
        if foto_name:
            try:
                DASHBOARD_UPLOAD_STORAGE.remove("ticket", foto_name)
            except OSError:
                pass
        return redirect("/open?error=" + quote_plus(f"Could not save evidence: {exc}"))



@app.route("/admin/tickets")
def admin_tickets():
    if g.user["role"] != "admin":
        abort(403)
    return send_from_directory(STATIC_PAGE_DIR, "admin_tickets.html")



@app.route("/admin/tickets/edit/<int:tid>", methods=["POST"])
def admin_archive_edit(tid):
    if g.user["role"] != "admin":
        abort(403)

    support_id = (request.form.get("support_id") or "").strip()
    category = (request.form.get("category") or "").strip()
    priority = (request.form.get("priority") or "").strip()
    status = (request.form.get("status") or "").strip()
    requested_division = (request.form.get("division") or "").strip()

    allowed_support = [UNASSIGNED] + SUPPORT_USERS
    if support_id not in allowed_support:
        abort(400)
    if category not in CATEGORIES or priority not in PRIORITIES or status not in TICKET_STATUSES:
        abort(400)

    with db() as c:
        row = c.execute(
            "SELECT no_tiket, support_id, category, priority, status, completed_at, division FROM tickets WHERE id=?",
            (tid,),
        ).fetchone()
    if not row:
        abort(404)

    division = row["division"] or ""
    if requested_division and requested_division not in {division, "Unspecified"}:
        division = valid_division(requested_division)
        if not division:
            return "Select an active Division.", 400
    changes = []
    if division != (row["division"] or ""):
        changes.append(f"division {row['division'] or 'Unspecified'} → {division}")
    if support_id != (row["support_id"] or UNASSIGNED):
        changes.append(f"Support {row['support_id'] or UNASSIGNED} → {support_id}")
        if support_requires_evidence(support_id):
            with db() as c:
                if not ticket_has_evidence(c, tid):
                    return f"Evidence is required before assigning this ticket to {support_id}.", 400
    if category != (row["category"] or "Other"):
        changes.append(f"category {row['category'] or 'Other'} → {category}")
    if priority != (row["priority"] or "Medium"):
        changes.append(f"priority {row['priority'] or 'Medium'} → {priority}")
    if status != (row["status"] or "Open"):
        changes.append(f"status {row['status'] or 'Open'} → {status}")

    completed_at = row["completed_at"]
    if status in COMPLETED_STATUSES and row["status"] not in COMPLETED_STATUSES:
        completed_at = local_now().strftime("%Y-%m-%d %H:%M:%S")
    elif status not in COMPLETED_STATUSES:
        completed_at = None

    with db() as c:
        c.execute(
            "UPDATE tickets SET support_id=?, category=?, priority=?, status=?, completed_at=?, division=? WHERE id=?",
            (support_id, category, priority, status, completed_at, division, tid),
        )

    if changes:
        record_activity(tid, "Archive edit", g.user["display"], "; ".join(changes))
        add_notification(
            "Ticket archive updated",
            f"{row['no_tiket']} was updated by {g.user['display']}: {', '.join(changes)}.",
            tid,
            g.user["username"],
        )
        flash(f"{row['no_tiket']} updated successfully.", "ok")
    else:
        flash(f"{row['no_tiket']} has no changes.", "ok")

    month = (request.form.get("month") or "").strip()
    q = (request.form.get("q") or "").strip()
    params = []
    if month:
        params.append(("month", month))
    if q:
        params.append(("q", q))
    suffix = "" if not params else "?" + "&".join(f"{k}={quote_plus(v)}" for k, v in params)
    _invalidate_query_cache()
    return redirect(url_for("admin_tickets") + suffix)


@app.route("/add", methods=["POST"])
def add():
    f = request.form
    admin = g.user["role"] == "admin"
    requested_support = (f.get("support_id") or "").strip()
    if admin:
        support_id = requested_support if requested_support in SUPPORT_USERS or requested_support == UNASSIGNED else UNASSIGNED
    else:
        support_id = requested_support if requested_support in {UNASSIGNED, g.user["display"]} else UNASSIGNED

    pelapor = f.get("pelapor", "").strip()[:100]
    masalah = f.get("masalah", "").strip()[:2000]
    tindakan = f.get("tindakan", "").strip()[:2000]
    priority = f.get("priority") if f.get("priority") in PRIORITIES else "Medium"
    category = f.get("category") if f.get("category") in CATEGORIES else "Other"
    try:
        division = check_division(f)
    except ValueError as exc:
        flash(str(exc), "err")
        return redirect("/")

    try:
        whatsapp = normalize_whatsapp(f.get("whatsapp", ""))
    except ValueError as e:
        flash(str(e), "err")
        return redirect("/")

    if not pelapor or not masalah:
        flash("Reporter and issue are required.", "err")
        return redirect("/")
    evidence_file = request.files.get("evidence")
    if support_requires_evidence(support_id) and not (evidence_file and evidence_file.filename):
        flash(f"Evidence is required for {support_id}.", "err")
        return redirect("/")

    now = local_now()
    case_dt = parse_form_datetime(f.get("case_at"), now)
    if case_dt is None:
        flash("Invalid case date/time. Please use the provided field.", "err")
        return redirect("/")
    if case_dt > now + timedelta(minutes=1):
        flash("Case date/time cannot be in the future.", "err")
        return redirect("/")

    try:
        with db() as c:
            cur = c.execute(
                "INSERT INTO tickets(tanggal,jam,case_tanggal,case_jam,pelapor,masalah,tindakan,support_id,status,foto,dibuat_oleh,tanpa_foto,whatsapp,priority,category,division) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), case_dt.strftime("%Y-%m-%d"), case_dt.strftime("%H:%M"),
                 pelapor, masalah, "", support_id, "Open", None, g.user["username"], 0, whatsapp, priority, category, division),
            )
            tid = cur.lastrowid
            no = f"TKT-{now:%Y%m%d}-{tid:04d}"
            c.execute("UPDATE tickets SET no_tiket=? WHERE id=?", (no, tid))
            _ensure_ticket_storage_tree(tid, no, conn=c)
    except Exception as exc:
        app.logger.exception("Ticket workspace provisioning failed for dashboard Create Ticket")
        flash(f"Ticket was not created: QNAP ticket folder could not be prepared ({exc}).", "err")
        return redirect("/")
    # A new ticket must immediately invalidate dashboard/archive query caches.
    # Otherwise the redirect to / can return the old cached Open Ticket Queue.
    _invalidate_query_cache()

    record_activity(tid, "Ticket created", g.user["display"], f"Assigned to {support_id} · {division} · {category} · {priority} · Case occurred {case_dt:%Y-%m-%d %H:%M}")

    if evidence_file and evidence_file.filename:
        try:
            _save_initial_evidence_to_qnap(tid, no, evidence_file, g.user["username"], g.user["display"], "Initial evidence attached")
        except Exception as exc:
            with db() as c:
                c.execute("DELETE FROM comment_attachments WHERE comment_id IN (SELECT id FROM ticket_comments WHERE ticket_id=?)", (tid,))
                c.execute("DELETE FROM ticket_comments WHERE ticket_id=?", (tid,))
                c.execute("DELETE FROM tickets WHERE id=?", (tid,))
            flash(f"Could not save evidence: {exc}", "err")
            return redirect("/")

    if tindakan:
        with db() as c:
            cur = c.execute(
                "INSERT INTO ticket_comments(ticket_id,username,display,comment,created_at) VALUES(?,?,?,?,?)",
                (tid, g.user["username"], g.user["display"], tindakan, now.strftime("%Y-%m-%d %H:%M:%S")),
            )
        record_activity(tid, "Initial progress added", g.user["display"], "Initial action saved as a progress comment")

    if support_id == UNASSIGNED:
        add_notification("New unassigned ticket", f"{no} · {category} · {priority} is waiting for an IT support owner.", tid, g.user["username"])
    else:
        add_notification("New ticket created", f"{no} · {category} · {priority} assigned to {support_id}.", tid, g.user["username"])
    flash(f"Ticket {no} created successfully.", "ok")
    return redirect("/")


@app.post("/api/tickets/create")
def api_create_ticket():
    multipart = request.mimetype == "multipart/form-data"
    payload = request.form if multipart else (request.get_json(silent=True) or {})
    evidence_file = request.files.get("evidence") if multipart else None
    admin = g.user["role"] == "admin"
    requested_support = str(payload.get("support_id") or "").strip()
    if admin:
        support_id = requested_support if requested_support in SUPPORT_USERS or requested_support == UNASSIGNED else UNASSIGNED
    else:
        support_id = requested_support if requested_support in {UNASSIGNED, g.user["display"]} else UNASSIGNED

    pelapor = str(payload.get("pelapor") or "").strip()[:100]
    masalah = str(payload.get("masalah") or "").strip()[:2000]
    tindakan = str(payload.get("tindakan") or "").strip()[:2000]
    priority = payload.get("priority") if payload.get("priority") in PRIORITIES else "Medium"
    category = payload.get("category") if payload.get("category") in CATEGORIES else "Other"
    try:
        division = check_division(payload)
    except ValueError as exc:
        return jsonify(ok=False,error=str(exc)), 400

    try:
        whatsapp = normalize_whatsapp(str(payload.get("whatsapp") or ""))
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400

    if not pelapor or not masalah:
        return jsonify(ok=False, error="Reporter and issue are required."), 400
    if support_requires_evidence(support_id) and not (evidence_file and evidence_file.filename):
        return jsonify(ok=False, error=f"Evidence is required for {support_id}."), 400

    now = local_now()
    case_dt = parse_form_datetime(str(payload.get("case_at") or ""), now)
    if case_dt is None:
        return jsonify(ok=False, error="Invalid case date/time."), 400
    if case_dt > now + timedelta(minutes=1):
        return jsonify(ok=False, error="Case date/time cannot be in the future."), 400

    try:
        with db() as c:
            cur = c.execute(
                "INSERT INTO tickets(tanggal,jam,case_tanggal,case_jam,pelapor,masalah,tindakan,support_id,status,foto,dibuat_oleh,tanpa_foto,whatsapp,priority,category,division) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), case_dt.strftime("%Y-%m-%d"), case_dt.strftime("%H:%M"),
                 pelapor, masalah, "", support_id, "Open", None, g.user["username"], 0, whatsapp, priority, category, division),
            )
            tid = cur.lastrowid
            no = f"TKT-{now:%Y%m%d}-{tid:04d}"
            c.execute("UPDATE tickets SET no_tiket=? WHERE id=?", (no, tid))
            _ensure_ticket_storage_tree(tid, no, conn=c)
    except Exception as exc:
        app.logger.exception("Ticket workspace provisioning failed for API Create Ticket")
        return jsonify(ok=False, error=f"Ticket not created: QNAP ticket folder could not be prepared ({exc})"), (503 if isinstance(exc, OSError) else 500)
    _invalidate_query_cache()
    record_activity(tid, "Ticket created", g.user["display"], f"Assigned to {support_id} · {division} · {category} · {priority} · Case occurred {case_dt:%Y-%m-%d %H:%M}")

    if evidence_file and evidence_file.filename:
        try:
            _save_initial_evidence_to_qnap(tid, no, evidence_file, g.user["username"], g.user["display"], "Initial evidence attached")
        except Exception as exc:
            app.logger.exception("Initial evidence upload failed for ticket %s", tid)
            with db() as c:
                c.execute("DELETE FROM comment_attachments WHERE comment_id IN (SELECT id FROM ticket_comments WHERE ticket_id=?)", (tid,))
                c.execute("DELETE FROM ticket_comments WHERE ticket_id=?", (tid,))
                c.execute("DELETE FROM tickets WHERE id=?", (tid,))
            return jsonify(ok=False, error=f"Could not save evidence: {exc}"), 500

    if tindakan:
        with db() as c:
            c.execute(
                "INSERT INTO ticket_comments(ticket_id,username,display,comment,created_at) VALUES(?,?,?,?,?)",
                (tid, g.user["username"], g.user["display"], tindakan, now.strftime("%Y-%m-%d %H:%M:%S")),
            )
        record_activity(tid, "Initial progress added", g.user["display"], "Initial action saved as a progress comment")

    if support_id == UNASSIGNED:
        add_notification("New unassigned ticket", f"{no} · {category} · {priority} is waiting for an IT support owner.", tid, g.user["username"])
    else:
        add_notification("New ticket created", f"{no} · {category} · {priority} assigned to {support_id}.", tid, g.user["username"])

    return jsonify(ok=True, ticket_id=tid, no_tiket=no, message=f"Ticket {no} created successfully.")


@app.route("/update/<int:tid>", methods=["POST"])
def update(tid):
    check_edit(tid)
    action_text = request.form.get("tindakan", "").strip()[:2000]
    if not action_text:
        flash("Write a progress update first.", "err")
        return redirect(request.referrer or "/")

    with db() as c:
        c.execute(
            "INSERT INTO ticket_comments(ticket_id,username,display,comment,created_at) VALUES(?,?,?,?,?)",
            (tid, g.user["username"], g.user["display"], action_text, local_now().strftime("%Y-%m-%d %H:%M:%S")),
        )

    record_activity(tid, "Progress comment added", g.user["display"], action_text[:300])
    return redirect(request.referrer or "/")


@app.route("/admin/edit/<int:tid>", methods=["POST"])
def admin_edit(tid):
    if g.user["role"] != "admin":
        abort(403)
    with db() as c:
        row = c.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not row:
        abort(404)

    pelapor = request.form.get("pelapor", "").strip()[:100]
    masalah = request.form.get("masalah", "").strip()[:2000]
    tindakan = request.form.get("tindakan", "").strip()[:2000]
    priority = request.form.get("priority") if request.form.get("priority") in PRIORITIES else "Medium"
    category = request.form.get("category") if request.form.get("category") in CATEGORIES else "Other"
    try:
        whatsapp = normalize_whatsapp(request.form.get("whatsapp", ""))
    except ValueError as e:
        flash(str(e), "err")
        return redirect(request.referrer or "/")
    if not pelapor or not masalah:
        flash("Reporter and issue are required.", "err")
        return redirect(request.referrer or "/")

    # Admin may correct the ticket creation date/time so SLA aging reflects the
    # actual pending duration. The ticket number itself remains unchanged.
    created_input = (request.form.get("created_at") or "").strip()
    case_input = (request.form.get("case_at") or "").strip()
    new_tanggal = row["tanggal"]
    new_jam = row["jam"]
    new_case_tanggal = row["case_tanggal"] or row["tanggal"]
    new_case_jam = row["case_jam"] or row["jam"]
    if created_input:
        created_dt = parse_form_datetime(created_input)
        if created_dt is None:
            flash("Invalid created date/time. Please use the provided date/time field.", "err")
            return redirect(request.referrer or "/")
        new_tanggal = created_dt.strftime("%Y-%m-%d")
        new_jam = created_dt.strftime("%H:%M")
    if case_input:
        case_dt = parse_form_datetime(case_input)
        if case_dt is None:
            flash("Invalid case date/time. Please use the provided case date/time field.", "err")
            return redirect(request.referrer or "/")
        if case_dt > local_now() + timedelta(minutes=1):
            flash("Case date/time cannot be in the future.", "err")
            return redirect(request.referrer or "/")
        new_case_tanggal = case_dt.strftime("%Y-%m-%d")
        new_case_jam = case_dt.strftime("%H:%M")

    changes = []
    if pelapor != row["pelapor"]:
        changes.append("reporter updated")
    if masalah != row["masalah"]:
        changes.append("issue updated")
    if tindakan != (row["tindakan"] or ""):
        changes.append("action/comment updated")
    if whatsapp != (row["whatsapp"] or ""):
        changes.append("WhatsApp updated")
    if priority != (row["priority"] or "Medium"):
        changes.append(f"priority {row['priority'] or 'Medium'} → {priority}")
    if category != (row["category"] or "Other"):
        changes.append(f"category {row['category'] or 'Other'} → {category}")
    if new_tanggal != (row["tanggal"] or "") or new_jam != (row["jam"] or ""):
        changes.append(
            f"record created time {row['tanggal']} {row['jam']} → {new_tanggal} {new_jam}"
        )
    old_case_tanggal = row["case_tanggal"] or row["tanggal"]
    old_case_jam = row["case_jam"] or row["jam"]
    if new_case_tanggal != old_case_tanggal or new_case_jam != old_case_jam:
        changes.append(
            f"case occurred {old_case_tanggal} {old_case_jam} → {new_case_tanggal} {new_case_jam}"
        )

    with db() as c:
        c.execute(
            "UPDATE tickets SET tanggal=?, jam=?, case_tanggal=?, case_jam=?, pelapor=?, masalah=?, tindakan=?, whatsapp=?, priority=?, category=? WHERE id=?",
            (new_tanggal, new_jam, new_case_tanggal, new_case_jam, pelapor, masalah, tindakan, whatsapp, priority, category, tid),
        )
    if changes:
        record_activity(tid, "Ticket edited", g.user["display"], "; ".join(changes))
        add_notification("Ticket updated", f"{row['no_tiket']} was edited by {g.user['display']}.", tid, g.user["username"])
        flash(f"Ticket {row['no_tiket']} updated successfully.", "ok")
    return redirect(request.referrer or "/")


@app.route("/toggle/<int:tid>", methods=["POST"])
def toggle(tid):
    check_edit(tid)
    with db() as c:
        r = c.execute("SELECT no_tiket,status,support_id FROM tickets WHERE id=?", (tid,)).fetchone()
    if not r:
        abort(404)

    if r["status"] not in CLOSED_STATUSES:
        fs = request.files.get("completion_evidence")
        completion_foto = None
        if fs and fs.filename:
            try:
                completion_foto = save_photo(fs)
            except (ValueError, OSError) as e:
                flash(str(e), "err")
                return redirect(request.referrer or "/")

        completed_at = local_now().strftime("%Y-%m-%d %H:%M:%S")
        with db() as c:
            c.execute(
                "UPDATE tickets SET status='Resolved', completion_foto=?, completed_at=? WHERE id=?",
                (completion_foto, completed_at, tid),
            )

        record_activity(
            tid,
            "Ticket resolved",
            g.user["display"],
            "Resolution evidence attached" if completion_foto else "Resolved without attachment",
        )
        new_status = "Resolved"
    else:
        with db() as c:
            c.execute(
                "UPDATE tickets SET status='In Progress', completed_at=NULL WHERE id=?",
                (tid,),
            )

        record_activity(
            tid,
            "Ticket reopened",
            g.user["display"],
            "Ticket returned to In Progress status",
        )
        new_status = "In Progress"

    add_notification(
        f"Ticket {new_status.lower()}",
        f"{r['no_tiket']} was moved to {new_status} by {g.user['display']}.",
        tid,
        g.user["username"],
    )
    return redirect(request.referrer or "/")


@app.route("/reassign/<int:tid>", methods=["POST"])
def reassign(tid):
    if g.user["role"] != "admin":
        abort(403)

    new = (request.form.get("support_id") or "").strip()
    if new not in SUPPORT_USERS:
        abort(400)

    with db() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute(
                "SELECT support_id, reassigned_from, no_tiket FROM tickets WHERE id=?",
                (tid,),
            ).fetchone()
            if not r:
                c.rollback()
                abort(404)

            old_support = r["support_id"]
            no_tiket = r["no_tiket"]

            if old_support == new:
                c.rollback()
                flash(f"Ticket {no_tiket} is already assigned to {new}.", "ok")
                return redirect(request.referrer or "/")
            if support_requires_evidence(new, c) and not ticket_has_evidence(c, tid):
                c.rollback()
                flash(f"Evidence is required before assigning {no_tiket} to {new}.", "err")
                return redirect(request.referrer or "/")

            previous = r["reassigned_from"] or ""
            history = f"{previous} > {old_support}" if previous else old_support
            c.execute(
                "UPDATE tickets SET support_id=?, reassigned_from=? WHERE id=?",
                (new, history, tid),
            )

            record_activity(
                tid, "Ticket reassigned", g.user["display"],
                f"Moved from {old_support} to {new}", conn=c,
            )
            add_notification(
                "Ticket reassigned",
                f"{no_tiket} moved from {old_support} to {new}.",
                tid, g.user["username"], conn=c,
            )
            c.commit()
            _invalidate_query_cache()
        except Exception:
            try:
                c.rollback()
            except sqlite3.Error:
                pass
            raise

    flash(f"Ticket {no_tiket} reassigned from {old_support} to {new}.", "ok")
    return redirect(request.referrer or "/")



@app.route("/api/tickets/bulk", methods=["POST"])
@app.route("/api/tickets/bulk-action", methods=["POST"])
@app.route("/api/tickets/bulk-assign", methods=["POST"])
def bulk_ticket_action():
    """Bulk Support assignment/close in one transaction.

    Frontend payload: {ticket_ids: [1, 2], action: "assign"|"close"|"assign_close", support_id: "Name"}.
    The legacy bulk-assign URL is an alias to this same handler, preventing stale
    clients from hitting a different implementation. Invalid, missing, and already
    completed tickets are returned individually without rolling back valid work.
    """
    if g.user["role"] != "admin":
        return jsonify(ok=False, error="Administrator permission is required for bulk actions."), 403

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(ok=False, error="Request body must be a JSON object."), 400

    raw_ids = payload.get("ticket_ids")
    if raw_ids is None and "ticket_id" in payload:
        raw_ids = payload.get("ticket_id")
    if isinstance(raw_ids, (str, int)):
        raw_ids = [raw_ids]
    if not isinstance(raw_ids, list):
        return jsonify(ok=False, error="ticket_ids must be an array of ticket IDs."), 400

    action = str(payload.get("action") or "assign").strip().lower()
    support_id = str(payload.get("support_id") or "").strip()
    if action not in {"assign", "close", "assign_close"}:
        return jsonify(ok=False, error="Invalid bulk action. Use assign, close, or assign_close."), 400
    if action in {"assign", "assign_close"} and support_id not in SUPPORT_USERS:
        return jsonify(ok=False, error=f"Invalid Support '{support_id}'. Choose one of: {', '.join(SUPPORT_USERS)}."), 400

    ids=[]
    failed_items=[]
    for value in raw_ids:
        try:
            tid=int(value)
        except (TypeError, ValueError):
            failed_items.append({"ticket_id": value, "reason": f"Invalid ticket ID: {value!r}."})
            continue
        if tid <= 0:
            failed_items.append({"ticket_id": tid, "reason": "Ticket ID must be greater than zero."})
            continue
        if tid not in ids:
            ids.append(tid)

    if not ids and not failed_items:
        return jsonify(ok=False, error="No tickets selected."), 400

    succeeded_items=[]
    now_text=local_now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            rows=[]
            if ids:
                placeholders=','.join('?' for _ in ids)
                rows=c.execute(
                    f"SELECT id,no_tiket,support_id,status,reassigned_from FROM tickets WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()
            by_id={int(r["id"]): r for r in rows}

            for tid in ids:
                row=by_id.get(tid)
                if row is None:
                    failed_items.append({"ticket_id": tid, "reason": "Ticket not found."})
                    continue
                if row["status"] in COMPLETED_STATUSES:
                    failed_items.append({
                        "ticket_id": tid,
                        "no_tiket": row["no_tiket"],
                        "reason": f"Ticket already closed/resolved (status: {row['status']}).",
                    })
                    continue

                changes=[]
                old_support=row["support_id"] or UNASSIGNED
                old_status=row["status"]

                if action in {"assign", "assign_close"}:
                    if old_support != support_id:
                        if support_requires_evidence(support_id, c) and not ticket_has_evidence(c, tid):
                            failed_items.append({
                                "ticket_id": tid,
                                "no_tiket": row["no_tiket"],
                                "reason": f"Evidence is required before assigning this ticket to {support_id}.",
                            })
                            continue
                        previous=row["reassigned_from"] or ""
                        history=f"{previous} > {old_support}" if previous else old_support
                        c.execute(
                            "UPDATE tickets SET support_id=?, reassigned_from=? WHERE id=?",
                            (support_id, history, tid),
                        )
                        changes.append(f"Support {old_support} → {support_id}")
                        record_activity(
                            tid, "Bulk Support assignment", g.user["display"],
                            f"Moved from {old_support} to {support_id}", conn=c,
                        )
                        add_notification(
                            "Ticket assigned", f"{row['no_tiket']} was assigned to {support_id}.",
                            tid, support_id.lower(), conn=c,
                        )
                    else:
                        changes.append(f"Already assigned to {support_id}")

                if action in {"close", "assign_close"}:
                    c.execute(
                        "UPDATE tickets SET status='Closed', completed_at=? WHERE id=?",
                        (now_text, tid),
                    )
                    changes.append(f"Status {old_status} → Closed")
                    record_activity(
                        tid, "Bulk ticket close", g.user["display"],
                        f"{old_status} → Closed", conn=c,
                    )
                    notify_user=(support_id.lower() if action == "assign_close" else (row["support_id"] or g.user["display"]).lower())
                    add_notification(
                        "Ticket closed", f"{row['no_tiket']} was closed by {g.user['display']}.",
                        tid, notify_user, conn=c,
                    )

                succeeded_items.append({
                    "ticket_id": tid,
                    "no_tiket": row["no_tiket"],
                    "support_id": support_id if action in {"assign", "assign_close"} else (row["support_id"] or UNASSIGNED),
                    "status": "Closed" if action in {"close", "assign_close"} else old_status,
                    "changes": changes,
                })

            c.commit()
            _invalidate_query_cache()
    except Exception as exc:
        app.logger.exception(
            "Bulk Support action failed. method=%s path=%s action=%r support_id=%r ticket_ids=%r payload=%r",
            request.method, request.path, action, support_id, ids, payload,
        )
        error_text=f"Transaction rolled back: {type(exc).__name__}: {exc}"
        failed_all=[]
        seen=set()
        for item in failed_items:
            tid=item.get("ticket_id")
            if tid not in seen:
                failed_all.append(item)
                seen.add(tid)
        for tid in ids:
            if tid not in seen:
                failed_all.append({"ticket_id": tid, "reason": error_text})
                seen.add(tid)
        return jsonify(
            ok=False,
            error=f"Bulk action failed: {type(exc).__name__}: {exc}",
            action=action,
            succeeded=0,
            failed=len(failed_all),
            successful_items=[],
            failed_items=failed_all,
        ), 500

    success_count=len(succeeded_items)
    fail_count=len(failed_items)
    return jsonify(
        ok=True,
        action=action,
        succeeded=success_count,
        failed=fail_count,
        selected=len(ids) + len([x for x in failed_items if x.get("ticket_id") not in ids]),
        successful_items=succeeded_items,
        failed_items=failed_items,
        message=f"{success_count} berhasil, {fail_count} gagal",
    )


@app.route("/claim/<int:tid>", methods=["POST"])
def claim_ticket(tid):
    if g.user["role"] != "support":
        abort(403)

    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT no_tiket, status, support_id, reassigned_from FROM tickets WHERE id=?",
            (tid,),
        ).fetchone()
        if not row:
            c.rollback()
            abort(404)
        if row["status"] in COMPLETED_STATUSES:
            c.rollback()
            flash("This ticket is already resolved/closed.", "err")
            return redirect(request.referrer or "/")
        if row["support_id"] != UNASSIGNED:
            c.rollback()
            flash(f"{row['no_tiket']} is already assigned to {row['support_id']}.", "err")
            return redirect(request.referrer or f"/ticket/{tid}")

        new_owner = g.user["display"]
        if support_requires_evidence(new_owner, c) and not ticket_has_evidence(c, tid):
            c.rollback()
            flash(f"Evidence is required before assigning this ticket to {new_owner}.", "err")
            return redirect(request.referrer or f"/ticket/{tid}")
        previous = row["reassigned_from"] or ""
        history = f"{previous} > {UNASSIGNED}" if previous else UNASSIGNED
        c.execute(
            "UPDATE tickets SET support_id=?, reassigned_from=? WHERE id=?",
            (new_owner, history, tid),
        )
        record_activity(
            tid,
            "Ticket claimed",
            g.user["display"],
            f"{row['no_tiket']} was claimed from {UNASSIGNED} by {new_owner}",
            conn=c,
        )
        add_notification(
            "Ticket claimed",
            f"{row['no_tiket']} is now assigned to {new_owner}.",
            tid,
            g.user["username"],
            conn=c,
        )
        c.commit()

    flash(f"{row['no_tiket']} is now assigned to you.", "ok")
    return redirect(request.referrer or f"/ticket/{tid}")


@app.route("/admin/edit/<int:tid>/created-time", methods=["POST"])
def admin_edit_created_time(tid):
    if g.user["role"] != "admin":
        abort(403)

    created_input = (request.form.get("created_at") or "").strip()
    if not created_input:
        flash("Created date/time is required.", "err")
        return redirect(request.referrer or f"/ticket/{tid}")

    try:
        created_dt = datetime.fromisoformat(created_input)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=LOCAL_TZ)
        else:
            created_dt = created_dt.astimezone(LOCAL_TZ)
        new_tanggal = created_dt.strftime("%Y-%m-%d")
        new_jam = created_dt.strftime("%H:%M")
    except ValueError:
        flash("Invalid created date/time. Please use the date/time picker.", "err")
        return redirect(request.referrer or f"/ticket/{tid}")

    with db() as c:
        row = c.execute("SELECT no_tiket, tanggal, jam FROM tickets WHERE id=?", (tid,)).fetchone()
        if not row:
            abort(404)
        old_text = f"{row['tanggal']} {row['jam']}"
        new_text = f"{new_tanggal} {new_jam}"
        if old_text == new_text:
            flash(f"{row['no_tiket']} already uses {new_text}.", "ok")
            return redirect(request.referrer or f"/ticket/{tid}")
        c.execute("UPDATE tickets SET tanggal=?, jam=? WHERE id=?", (new_tanggal, new_jam, tid))

    record_activity(
        tid,
        "Record created time adjusted",
        g.user["display"],
        f"{old_text} → {new_text}; case age/SLA remains based on case occurred time",
    )
    add_notification(
        "Ticket record time updated",
        f"{row['no_tiket']} record created time changed to {new_text} by {g.user['display']}.",
        tid,
        g.user["username"],
    )
    flash(f"{row['no_tiket']} record created time updated to {new_text}.", "ok")
    return redirect(request.referrer or f"/ticket/{tid}")


@app.route("/admin/edit/<int:tid>/case-time", methods=["POST"])
def admin_edit_case_time(tid):
    if g.user["role"] != "admin":
        abort(403)

    case_input = (request.form.get("case_at") or "").strip()
    case_dt = parse_form_datetime(case_input)
    if case_dt is None:
        flash("Case date/time is required and must be valid.", "err")
        return redirect(request.referrer or f"/ticket/{tid}")
    if case_dt > local_now() + timedelta(minutes=1):
        flash("Case date/time cannot be in the future.", "err")
        return redirect(request.referrer or f"/ticket/{tid}")

    new_case_tanggal = case_dt.strftime("%Y-%m-%d")
    new_case_jam = case_dt.strftime("%H:%M")
    with db() as c:
        row = c.execute("SELECT no_tiket, tanggal, jam, case_tanggal, case_jam FROM tickets WHERE id=?", (tid,)).fetchone()
        if not row:
            abort(404)
        old_case = f"{row['case_tanggal'] or row['tanggal']} {row['case_jam'] or row['jam']}"
        new_case = f"{new_case_tanggal} {new_case_jam}"
        if old_case == new_case:
            flash(f"{row['no_tiket']} already uses case time {new_case}.", "ok")
            return redirect(request.referrer or f"/ticket/{tid}")
        c.execute("UPDATE tickets SET case_tanggal=?, case_jam=? WHERE id=?", (new_case_tanggal, new_case_jam, tid))

    record_activity(
        tid,
        "Case occurred time adjusted",
        g.user["display"],
        f"{old_case} → {new_case}; ticket age/SLA recalculated from case time",
    )
    add_notification(
        "Ticket case time updated",
        f"{row['no_tiket']} case occurred time changed to {new_case} by {g.user['display']}.",
        tid,
        g.user["username"],
    )
    flash(f"{row['no_tiket']} case occurred time updated to {new_case}.", "ok")
    return redirect(request.referrer or f"/ticket/{tid}")


@app.route("/api/tickets/<int:tid>")
def ticket_api_detail(tid):
    with db() as c:
        row=c.execute("SELECT * FROM tickets WHERE id=?",(tid,)).fetchone()
        if row:
            ensure_public_token(c, tid)
            c.commit()
            row=c.execute("SELECT * FROM tickets WHERE id=?",(tid,)).fetchone()
    if not row: return jsonify(ok=False,error="Ticket not found."),404
    t=ticket_sla(row); comments_by_ticket,comment_meta=ticket_comment_data([tid], check_storage=True)
    ticket_file_folder_id=None; ticket_folders=[]; ticket_storage_available=True
    try:
        ticket_storage,ticket_folders=_ticket_folder_rows(tid,t["no_tiket"]); ticket_file_folder_id=ticket_storage["ticket_folder_id"]
    except Exception:
        ticket_storage_available=False
        app.logger.warning("Ticket private evidence workspace unavailable for %s",t["no_tiket"],exc_info=True)
    active=t["status"] not in COMPLETED_STATUSES
    reporter=str(t["pelapor"] or "").strip().casefold(); display=str(g.user["display"] or "").strip().casefold(); username=str(g.user["username"] or "").strip().casefold()
    can_delete=user_can_delete_ticket_comment(t)
    comments=comments_by_ticket.get(tid,[])
    return jsonify(ok=True,ticket=dict(t),comments=comments,comment_meta=comment_meta.get(tid,{"count":0,"latest":None}),ticket_folders=ticket_folders,ticket_file_folder_id=ticket_file_folder_id,
        ticket_storage_available=ticket_storage_available,
        public_link=(url_for("public_ticket_page", token=t["public_token"], _external=True) if t.get("public_token") else ""),
        public_token_enabled=bool(t.get("public_token_enabled", 1)),
        permissions={"can_comment":active and g.user["role"] in {"admin","support"},"can_change_status":active and (g.user["role"]=="admin" or t["support_id"]==g.user["display"]),"can_claim":active and t["support_id"]==UNASSIGNED and g.user["role"]=="support","is_admin":g.user["role"]=="admin","can_delete_ticket_attachments":can_delete},statuses=TICKET_STATUSES,unassigned=UNASSIGNED)


@app.route("/ticket/<int:tid>")
def ticket_detail(tid):
    with db() as c:
        exists = c.execute("SELECT 1 FROM tickets WHERE id=?", (tid,)).fetchone()
    if not exists:
        abort(404)
    return send_from_directory(STATIC_PAGE_DIR, "ticket.html")


@app.post("/ticket/<int:tid>/public-token")
def ticket_public_token_update(tid):
    if g.user["role"] != "admin":
        abort(403)
    action = (request.form.get("action") or "enable").strip().lower()
    with db() as c:
        row = c.execute("SELECT id FROM tickets WHERE id=?", (tid,)).fetchone()
        if not row:
            abort(404)
        token = ensure_public_token(c, tid)
        if action == "disable":
            c.execute("UPDATE tickets SET public_token_enabled=0 WHERE id=?", (tid,))
        elif action == "regenerate":
            for _ in range(8):
                token = secrets.token_urlsafe(24)
                try:
                    c.execute("UPDATE tickets SET public_token=?, public_token_enabled=1 WHERE id=?", (token, tid))
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                return jsonify(ok=False, error="Unable to regenerate a unique token."), 500
        else:
            c.execute("UPDATE tickets SET public_token_enabled=1 WHERE id=?", (tid,))
        c.commit()
        row = c.execute("SELECT public_token, public_token_enabled FROM tickets WHERE id=?", (tid,)).fetchone()
    return jsonify(
        ok=True,
        public_link=url_for("public_ticket_page", token=row["public_token"], _external=True),
        enabled=bool(row["public_token_enabled"]),
    )


@app.get("/t/<token>")
def public_ticket_page(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", token or ""):
        abort(404)
    return send_from_directory(STATIC_PAGE_DIR, "public_ticket.html")


@app.get("/api/public/t/<token>")
def public_ticket_api(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", token or ""):
        abort(404)
    payload = public_ticket_payload(token)
    if not payload:
        abort(404)
    return jsonify(payload)


@app.get("/t/<token>/file/<int:file_id>")
def public_comment_file(token, file_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", token or ""):
        abort(404)
    with db() as c:
        ticket = c.execute(
            "SELECT id, no_tiket FROM tickets WHERE public_token=? AND COALESCE(public_token_enabled,1)=1",
            (token,),
        ).fetchone()
        if not ticket:
            abort(404)
        item = c.execute(
            "SELECT * FROM file_items WHERE id=?",
            (file_id,),
        ).fetchone()
    if not item or not item["qnap_path"]:
        abort(404)
    qnap_path = (item["qnap_path"] or "").replace("\\", "/")
    if not _is_ticket_attachment_path(ticket["no_tiket"], qnap_path):
        abort(404)
    try:
        path, _ = _ticket_evidence_path(qnap_path)
    except ValueError:
        abort(404)
    except OSError:
        abort(503)
    if not os.path.isfile(path):
        abort(404)
    mime = item["mime_type"] or mimetypes.guess_type(item["original_name"] or item["stored_name"])[0] or "application/octet-stream"
    inline = mime.startswith("image/") or mime == "application/pdf"
    return send_file(
        path,
        as_attachment=not inline,
        download_name=item["original_name"] or item["stored_name"],
        mimetype=mime,
        conditional=True,
        etag=True,
        last_modified=os.path.getmtime(path),
        max_age=300,
    )


@app.get("/t/<token>/comment-file/<int:attachment_id>")
def public_ticket_comment_file(token, attachment_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", token or ""):
        abort(404)
    with db() as c:
        row = c.execute(
            "SELECT ca.*, t.no_tiket FROM comment_attachments ca "
            "JOIN ticket_comments tc ON tc.id=ca.comment_id "
            "JOIN tickets t ON t.id=tc.ticket_id "
            "WHERE ca.id=? AND ca.deleted_at IS NULL AND tc.deleted_at IS NULL "
            "AND t.public_token=? AND COALESCE(t.public_token_enabled,1)=1",
            (attachment_id, token),
        ).fetchone()
    if not row or not row["qnap_path"]:
        abort(404)
    qnap_path = (row["qnap_path"] or "").replace("\\", "/")
    if not _is_ticket_attachment_path(row["no_tiket"], qnap_path):
        abort(404)
    try:
        path, _ = _ticket_evidence_path(qnap_path)
    except ValueError:
        abort(404)
    except OSError:
        abort(503)
    if not os.path.isfile(path):
        abort(404)
    mime = row["mime_type"] or mimetypes.guess_type(row["original_name"] or row["filename"])[0] or "application/octet-stream"
    inline = mime.startswith("image/") or mime == "application/pdf"
    return send_file(
        path,
        as_attachment=not inline,
        download_name=row["original_name"] or row["filename"],
        mimetype=mime,
        conditional=True,
        etag=True,
        last_modified=os.path.getmtime(path),
        max_age=300,
    )



@app.route("/ticket/<int:tid>/status", methods=["POST"])
def ticket_status(tid):
    with db() as c:
        row = c.execute(
            "SELECT no_tiket, status, support_id FROM tickets WHERE id=?",
            (tid,),
        ).fetchone()

    if not row:
        abort(404)
    if g.user["role"] != "admin" and row["support_id"] != g.user["display"]:
        abort(403)

    status = (request.form.get("status") or "").strip()
    if status not in TICKET_STATUSES:
        abort(400)

    old = row["status"]
    if old == status:
        flash(f"{row['no_tiket']} is already {status}.", "ok")
        return redirect(request.referrer or "/")

    completed_at = None
    if status in {"Resolved", "Closed"}:
        completed_at = local_now().strftime("%Y-%m-%d %H:%M:%S")

    with db() as c:
        c.execute(
            "UPDATE tickets SET status=?, completed_at=? WHERE id=?",
            (status, completed_at, tid),
        )

    record_activity(
        tid,
        "Status changed",
        g.user["display"],
        f"{old} → {status}",
    )
    add_notification(
        "Ticket status updated",
        f"{row['no_tiket']} changed from {old} to {status} by {g.user['display']}.",
        tid,
        g.user["username"],
    )
    flash(f"{row['no_tiket']} moved to {status}.", "ok")
    return redirect(request.referrer or "/")


@app.route("/ticket/<int:tid>/attachment-upload", methods=["POST"])
def ticket_attachment_upload(tid):
    """Stream one ticket attachment with browser-visible XHR progress, then publish it to QNAP."""
    global QNAP_SYNC_DIRTY

    with db() as c:
        row = c.execute(
            "SELECT no_tiket, status, support_id FROM tickets WHERE id=?",
            (tid,),
        ).fetchone()
    if not row:
        abort(404)
    if g.user["role"] not in {"admin", "support"}:
        abort(403)
    if row["status"] in COMPLETED_STATUSES:
        return jsonify(ok=False, error="This ticket is already resolved/closed."), 409

    try:
        original_name = _safe_upload_name(unquote(request.headers.get("X-File-Name", "")))
        expected_size = int((request.headers.get("X-File-Size") or request.content_length or 0))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Invalid upload size."), 400
    if expected_size <= 0:
        return jsonify(ok=False, error="The selected file is empty or its size is missing."), 400
    if expected_size > COMMENT_MAX_FILE_SIZE:
        return jsonify(ok=False, error=f"Each ticket attachment must be <= {format_file_size(COMMENT_MAX_FILE_SIZE)}."), 413
    if request.content_length is not None and request.content_length != expected_size:
        return jsonify(ok=False, error="Upload size metadata does not match the request body."), 400

    storage_section = (request.headers.get("X-Storage-Section") or "02_PROGRESS").strip()
    if storage_section not in {"01_INITIAL_EVIDENCE", "02_PROGRESS", "03_COMPLETION"}:
        storage_section = "02_PROGRESS"
    requested_target = unquote((request.headers.get("X-Target-Path") or "").strip())

    local_temp = None
    qnap_temp = None
    final_rel = None
    final_name = None
    published = False
    total = 0

    try:
        storage = _ensure_ticket_storage_tree(tid, row["no_tiket"])

        with QNAP_UPLOAD_FINALIZE_LOCK:
            with db() as c:
                target_rel = _qnap_rel_join(_safe_upload_relative_path(requested_target)) if requested_target else ""
                ticket_prefix = storage["ticket_path"] + "/"
                if not target_rel or not target_rel.startswith(ticket_prefix):
                    raise ValueError("Please select a folder inside this ticket before uploading.")
                folder_row = c.execute(
                    "SELECT id, qnap_path FROM file_folders WHERE qnap_path=?",
                    (target_rel,),
                ).fetchone()
                if not folder_row:
                    raise ValueError("The selected ticket upload folder no longer exists.")
                target_folder_id = int(folder_row["id"])
                target_dir, _ = _ticket_evidence_path(target_rel)
                if not os.path.isdir(target_dir):
                    raise ValueError("The ticket upload folder is unavailable on QNAP.")

            fd, local_temp = tempfile.mkstemp(prefix=".ticket-async-", suffix=".part", dir=TEMP_UPLOAD_DIR)
            os.close(fd)
            with open(local_temp, "wb", buffering=4 * 1024 * 1024) as out:
                while True:
                    chunk = request.stream.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > COMMENT_MAX_FILE_SIZE:
                        raise ValueError(f"Each ticket attachment must be <= {format_file_size(COMMENT_MAX_FILE_SIZE)}.")
                    out.write(chunk)
                out.flush()
                try:
                    os.fsync(out.fileno())
                except OSError:
                    pass

            if total != expected_size:
                raise IOError(f"Upload stopped before completion: received {total} of {expected_size} bytes.")

            final_name = _unique_qnap_filename(target_dir, original_name)
            final_rel = _qnap_rel_join([target_rel, final_name])
            qnap_temp = os.path.join(target_dir, f".{secrets.token_hex(12)}.part")
            with open(local_temp, "rb", buffering=4 * 1024 * 1024) as src, open(
                qnap_temp, "wb", buffering=4 * 1024 * 1024
            ) as dst:
                shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
                dst.flush()
                try:
                    os.fsync(dst.fileno())
                except OSError:
                    pass
            if os.path.getsize(qnap_temp) != total:
                raise IOError("QNAP attachment size verification failed.")
            os.replace(qnap_temp, os.path.join(target_dir, final_name))
            qnap_temp = None
            published = True

            mime_type = mimetypes.guess_type(final_name)[0] or "application/octet-stream"
            now_text = local_now().strftime("%Y-%m-%d %H:%M:%S")
            with db() as c:
                cur = c.execute(
                    "INSERT INTO file_items(folder_id,stored_name,disk_name,original_name,mime_type,size_bytes,uploaded_by,uploaded_display,created_at,qnap_path) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        target_folder_id, final_rel, final_name, original_name, mime_type,
                        total, g.user["username"], g.user["display"], now_text, final_rel,
                    ),
                )
                file_id = cur.lastrowid
                c.commit()

        QNAP_SYNC_DIRTY = True
        _invalidate_qnap_listing_cache()
        return jsonify(
            ok=True,
            file={
                "id": int(file_id),
                "name": final_name,
                "original_name": original_name,
                "size_bytes": total,
                "size_label": format_file_size(total),
                "mime_type": mime_type,
                "qnap_path": final_rel,
                "folder_id": int(target_folder_id),
                "folder_path": target_rel,
            },
        ), 200
    except ClientDisconnected as exc:
        app.logger.warning("Ticket async upload disconnected ticket=%s file=%r received=%d", tid, original_name, total)
        return jsonify(ok=False, error="Upload connection closed before completion.", received_bytes=total, expected_bytes=expected_size), 400
    except (ConnectionResetError, BrokenPipeError, TimeoutError) as exc:
        app.logger.warning("Ticket async upload interrupted ticket=%s file=%r received=%d", tid, original_name, total, exc_info=True)
        return jsonify(ok=False, error=f"Upload connection was interrupted: {exc}", received_bytes=total, expected_bytes=expected_size), 400
    except sqlite3.IntegrityError:
        QNAP_SYNC_DIRTY = True
        _invalidate_qnap_listing_cache()
        app.logger.exception("Ticket async upload metadata conflict ticket=%s file=%r", tid, final_name or original_name)
        return jsonify(ok=False, error="File reached QNAP but metadata could not be recorded; it will be re-synced."), 500
    except (ValueError, OSError) as exc:
        if published and final_rel:
            _remove_qnap_paths([final_rel])
        app.logger.warning("Ticket async upload failed ticket=%s file=%r: %s", tid, original_name, exc, exc_info=True)
        return jsonify(ok=False, error=str(exc)), 400
    except Exception:
        if published and final_rel:
            _remove_qnap_paths([final_rel])
        app.logger.exception("Ticket async upload failed unexpectedly ticket=%s file=%r", tid, original_name)
        return jsonify(ok=False, error="Ticket attachment upload failed. Check the application log."), 500
    finally:
        for candidate in (local_temp, qnap_temp):
            if candidate:
                try:
                    if os.path.exists(candidate):
                        os.remove(candidate)
                except OSError:
                    app.logger.warning("Could not remove async ticket temp file: %r", candidate, exc_info=True)


@app.route("/ticket/<int:tid>/comment", methods=["POST"])
def ticket_comment(tid):
    with db() as c:
        row = c.execute(
            "SELECT no_tiket, status, support_id FROM tickets WHERE id=?",
            (tid,),
        ).fetchone()

    if not row:
        abort(404)
    if g.user["role"] not in {"admin", "support"}:
        abort(403)
    if row["status"] in COMPLETED_STATUSES:
        flash("This ticket is already resolved/closed and no longer accepts progress updates.", "err")
        return redirect(request.referrer or url_for("ticket_detail", tid=tid))

    comment = (request.form.get("comment") or "").strip()[:4000]
    requested_status = (request.form.get("status") or row["status"]).strip()
    storage_folder_path = (request.form.get("storage_folder_path") or "").strip()

    can_change_status = g.user["role"] == "admin" or row["support_id"] == g.user["display"]
    if not can_change_status:
        requested_status = row["status"]
    if requested_status not in TICKET_STATUSES:
        requested_status = row["status"]

    files = [fs for fs in request.files.getlist("attachments") if fs and fs.filename]
    uploaded_attachment_ids = []
    uploaded_raw = (request.form.get("uploaded_attachment_ids") or "").strip()
    if uploaded_raw:
        try:
            parsed_ids = json.loads(uploaded_raw)
            if not isinstance(parsed_ids, list):
                raise ValueError
            uploaded_attachment_ids = [int(v) for v in parsed_ids]
        except (TypeError, ValueError, json.JSONDecodeError):
            flash("Uploaded attachment data is invalid. Please upload the files again.", "err")
            return redirect(request.referrer or url_for("ticket_detail", tid=tid))

    if files and uploaded_attachment_ids:
        flash("Do not mix pending browser uploads with direct attachments. Please wait for the upload queue to finish.", "err")
        return redirect(request.referrer or url_for("ticket_detail", tid=tid))
    if len(files) + len(uploaded_attachment_ids) > COMMENT_MAX_FILES:
        flash(f"You can attach up to {COMMENT_MAX_FILES} files per update.", "err")
        return redirect(request.referrer or "/")
    if not comment and not files and not uploaded_attachment_ids:
        flash("Write a comment or attach a file before posting an update.", "err")
        return redirect(request.referrer or "/")

    published_paths = []
    saved_files = []
    now_text = local_now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        storage = _ensure_ticket_storage_tree(tid, row["no_tiket"]) if files or uploaded_attachment_ids else None
        target_folder_id = None
        target_rel = ""
        target_dir = None

        with QNAP_UPLOAD_FINALIZE_LOCK:
            with db() as c:
                if uploaded_attachment_ids:
                    placeholders = ",".join("?" for _ in uploaded_attachment_ids)
                    uploaded_rows = c.execute(
                        f"SELECT id, disk_name AS filename, original_name, qnap_path, mime_type, size_bytes, folder_id, stored_name "
                        f"FROM file_items WHERE id IN ({placeholders})",
                        uploaded_attachment_ids,
                    ).fetchall()
                    uploaded_by_id = {int(r["id"]): r for r in uploaded_rows}
                    if len(uploaded_by_id) != len(set(uploaded_attachment_ids)):
                        raise ValueError("One or more uploaded files are no longer available in File Center.")
                    first_folder_id = int(uploaded_by_id[uploaded_attachment_ids[0]]["folder_id"] or 0)
                    folder_row = c.execute(
                        "SELECT id, qnap_path FROM file_folders WHERE id=?",
                        (first_folder_id,),
                    ).fetchone()
                    if not folder_row or not folder_row["qnap_path"]:
                        raise ValueError("The ticket upload folder is no longer available.")
                    target_folder_id = first_folder_id
                    target_rel = folder_row["qnap_path"]
                    ticket_prefix = storage["ticket_path"] + "/"
                    if not target_rel.startswith(ticket_prefix):
                        raise ValueError("The uploaded file is outside this ticket's File Center workspace.")
                    target_dir, _ = _ticket_evidence_path(target_rel)
                    for file_id in uploaded_attachment_ids:
                        r = uploaded_by_id[file_id]
                        qnap_path = (r["qnap_path"] or r["stored_name"] or "").replace("\\", "/")
                        if int(r["folder_id"] or 0) != target_folder_id or not qnap_path.startswith(target_rel.rstrip("/") + "/"):
                            raise ValueError("An uploaded file does not belong to this ticket update folder.")
                        physical_path, _ = _ticket_evidence_path(qnap_path)
                        if not os.path.isfile(physical_path):
                            raise ValueError(f"Uploaded file is missing from QNAP: {r['original_name'] or r['filename']}")
                        saved_files.append((
                            r["filename"],
                            r["original_name"] or r["filename"],
                            qnap_path,
                            r["mime_type"] or (mimetypes.guess_type(r["filename"])[0] or "application/octet-stream"),
                            int(r["size_bytes"] or 0),
                        ))
                elif files:
                    target_rel = _qnap_rel_join(_safe_upload_relative_path(storage_folder_path)) if storage_folder_path else ""
                    ticket_prefix = storage["ticket_path"] + "/"
                    if not target_rel or not target_rel.startswith(ticket_prefix):
                        raise ValueError("Please select a folder inside this ticket before attaching files.")
                    folder_row = c.execute(
                        "SELECT id, qnap_path FROM file_folders WHERE qnap_path=?",
                        (target_rel,),
                    ).fetchone()
                    if not folder_row:
                        raise ValueError("The selected ticket upload folder no longer exists.")
                    target_folder_id = int(folder_row["id"])
                    target_dir, _ = _ticket_evidence_path(target_rel)
                    if not os.path.isdir(target_dir):
                        raise ValueError("The selected ticket upload folder is unavailable on QNAP.")

            for fs in files:
                saved = _save_ticket_attachment_to_qnap(fs, target_dir, target_rel)
                saved_files.append(saved)
                published_paths.append(saved[2])

            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                cur = c.execute(
                    "INSERT INTO ticket_comments(ticket_id,username,display,comment,created_at,file_folder_id) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        tid,
                        g.user["username"],
                        g.user["display"],
                        comment or "Files attached",
                        now_text,
                        target_folder_id,
                    ),
                )
                comment_id = cur.lastrowid

                for final_name, original_name, qnap_path, mime_type, size_bytes in saved_files:
                    c.execute(
                        "INSERT INTO comment_attachments(comment_id,filename,original_name,created_at,qnap_path,mime_type,size_bytes) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (
                            comment_id,
                            final_name,
                            original_name,
                            now_text,
                            qnap_path,
                            mime_type,
                            size_bytes,
                        ),
                    )

                old_status = row["status"]
                if old_status != requested_status:
                    completed_at = now_text if requested_status in {"Resolved", "Closed"} else None
                    c.execute(
                        "UPDATE tickets SET status=?, completed_at=? WHERE id=?",
                        (requested_status, completed_at, tid),
                    )
                    record_activity(
                        tid,
                        "Status changed",
                        g.user["display"],
                        f"{old_status} → {requested_status}",
                        conn=c,
                    )

                details = []
                if comment:
                    details.append("Progress comment added")
                if saved_files:
                    details.append(f"{len(saved_files)} file attachment(s)")
                if target_rel:
                    details.append(f"File Center: {target_rel}")

                record_activity(
                    tid,
                    "Progress update added",
                    g.user["display"],
                    " · ".join(details),
                    conn=c,
                )
                add_notification(
                    "Ticket progress updated",
                    f"{row['no_tiket']} updated by {g.user['display']}"
                    + (f" · {requested_status}" if old_status != requested_status else ""),
                    tid,
                    g.user["username"],
                    conn=c,
                )
                c.commit()
    except ValueError as exc:
        _remove_qnap_paths(published_paths)
        flash(str(exc), "err")
        return redirect(request.referrer or "/")
    except Exception:
        _remove_qnap_paths(published_paths)
        app.logger.exception("Ticket progress/File Center upload failed for ticket %s", tid)
        flash("Progress update gagal disimpan. File yang gagal diproses sudah dibersihkan jika memungkinkan.", "err")
        return redirect(request.referrer or f"/ticket/{tid}")

    _invalidate_qnap_listing_cache()
    if requested_status != row["status"]:
        flash(f"Update added and ticket moved to {requested_status}.", "ok")
    else:
        flash("Progress update added and files saved to File Center." if saved_files else "Progress update added.", "ok")
    return redirect(request.referrer or f"/ticket/{tid}")


@app.route("/ticket/<int:tid>/attachment-folders", methods=["GET", "POST"])
def ticket_attachment_folders(tid):
    with db() as c:
        row = c.execute("SELECT no_tiket, status FROM tickets WHERE id=?", (tid,)).fetchone()
    if not row:
        abort(404)
    if g.user["role"] not in {"admin", "support"}:
        abort(403)
    if row["status"] in COMPLETED_STATUSES:
        return jsonify(ok=False, error="This ticket is already resolved/closed."), 409

    try:
        storage, folders = _ticket_folder_rows(tid, row["no_tiket"])
        if request.method == "GET":
            return jsonify(ok=True, ticket_folder_id=storage["ticket_folder_id"], ticket_path=storage["ticket_path"], folders=folders)

        payload = request.get_json(silent=True) or request.form
        name = ((payload.get("name") if payload else "") or "").strip()
        if not name or len(name) > 80 or "/" in name or "\\" in name or name in {".", ".."}:
            return jsonify(ok=False, error="Folder name is invalid. Use a simple name without / or \\ ."), 400
        if not _qnap_dir_name_valid(name):
            return jsonify(ok=False, error="Folder name is not allowed."), 400

        rel = _qnap_rel_join([storage["ticket_path"], name])
        physical, _ = _ticket_evidence_path(rel)
        with QNAP_UPLOAD_FINALIZE_LOCK:
            if os.path.exists(physical):
                return jsonify(ok=False, error="A folder with that name already exists."), 409
            os.makedirs(physical, exist_ok=False)
            with db() as c:
                folder_id = _ensure_folder_metadata(c, name, storage["ticket_folder_id"], rel)
        return jsonify(ok=True, folder={"id": int(folder_id), "name": name, "path": rel})
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except FileExistsError:
        return jsonify(ok=False, error="A folder with that name already exists."), 409
    except Exception:
        app.logger.exception("Ticket attachment folder operation failed for ticket %s", tid)
        return jsonify(ok=False, error="Unable to manage the ticket folder."), 500


@app.route("/ticket/<int:tid>/attachment-prepare", methods=["POST"])
def ticket_attachment_prepare(tid):
    with db() as c:
        row = c.execute("SELECT no_tiket, status FROM tickets WHERE id=?", (tid,)).fetchone()
    if not row:
        abort(404)
    if g.user["role"] not in {"admin", "support"}:
        abort(403)
    if row["status"] in COMPLETED_STATUSES:
        return jsonify(ok=False, error="This ticket is already resolved/closed."), 409

    requested_target = (request.form.get("folder_path") or "").strip()
    try:
        storage = _ensure_ticket_storage_tree(tid, row["no_tiket"])
        target_rel = _qnap_rel_join(_safe_upload_relative_path(requested_target)) if requested_target else ""
        ticket_prefix = storage["ticket_path"] + "/"
        if not target_rel or not target_rel.startswith(ticket_prefix):
            raise ValueError("Please select a folder inside this ticket before uploading.")
        with db() as c:
            folder_row = c.execute("SELECT id, name, qnap_path FROM file_folders WHERE qnap_path=?", (target_rel,)).fetchone()
        if not folder_row:
            raise ValueError("The selected ticket upload folder no longer exists.")
        target_dir, _ = _ticket_evidence_path(target_rel)
        if not os.path.isdir(target_dir):
            raise ValueError("The selected ticket upload folder is unavailable on QNAP.")
        return jsonify(ok=True, folder_id=int(folder_row["id"]), folder_path=target_rel, folder_name=folder_row["name"])
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except Exception:
        app.logger.exception("Ticket attachment folder preparation failed for ticket %s", tid)
        return jsonify(ok=False, error="Unable to prepare the ticket upload folder."), 500


# ==================== PPG EDIT ZONE: TICKET ATTACHMENT DELETE ====================
@app.route("/ticket/<int:tid>/attachment-delete/<int:attachment_id>", methods=["POST"])
def ticket_attachment_delete(tid, attachment_id):
    """Delete a ticket progress attachment. Only admin or the ticket reporter may delete it."""
    with db() as c:
        row = c.execute(
            "SELECT ca.id, ca.comment_id, ca.filename, ca.qnap_path, ca.original_name, tc.ticket_id, "
            "t.no_tiket, t.pelapor, t.support_id FROM comment_attachments ca "
            "JOIN ticket_comments tc ON tc.id=ca.comment_id "
            "JOIN tickets t ON t.id=tc.ticket_id "
            "WHERE ca.id=? AND tc.ticket_id=? AND ca.deleted_at IS NULL",
            (attachment_id, tid),
        ).fetchone()
    if not row:
        abort(404)

    if not user_can_delete_ticket_comment(row):
        abort(403)

    physical_path = None
    try:
        if row["qnap_path"]:
            physical_path, _ = _ticket_evidence_path(row["qnap_path"])
        elif row["filename"]:
            physical_path = DASHBOARD_UPLOAD_STORAGE.resolve("ticket", row["filename"])
    except ValueError:
        return jsonify(ok=False, error="Invalid attachment path."), 400

    try:
        with QNAP_UPLOAD_FINALIZE_LOCK:
            now_text = local_now().strftime("%Y-%m-%d %H:%M:%S")
            delete_error = ""
            file_deleted = True
            if physical_path:
                try:
                    os.remove(physical_path)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    file_deleted = False
                    delete_error = str(exc)
                    app.logger.warning("Ticket attachment metadata deleted but physical file cleanup failed: %r", exc)

            with db() as c:
                delete_cursor = c.execute(
                    "UPDATE comment_attachments SET deleted_at=?, delete_error=? WHERE id=? AND comment_id IN (SELECT id FROM ticket_comments WHERE ticket_id=?) AND deleted_at IS NULL",
                    (now_text, delete_error, attachment_id, tid),
                )
                if delete_cursor.rowcount != 1:
                    return jsonify(ok=False, error="Attachment was already deleted."), 404
                if row["qnap_path"] and file_deleted:
                    c.execute("DELETE FROM file_items WHERE qnap_path=?", (row["qnap_path"],))
                if row["qnap_path"] and not file_deleted:
                    queue_qnap_delete_retry(c, tid, row["comment_id"], attachment_id, row["qnap_path"], physical_path, delete_error, now_text)
                c.commit()

        _invalidate_qnap_listing_cache()
        record_activity(tid, "Attachment deleted", g.user["display"], row["original_name"] or row["filename"] or "attachment")
        return jsonify(ok=True, message="Attachment deleted.", retry=not file_deleted)
    except Exception:
        app.logger.exception("Ticket attachment delete failed ticket=%s attachment=%s", tid, attachment_id)
        return jsonify(ok=False, error="Unable to delete the attachment."), 500
# ==================== PPG EDIT ZONE: TICKET COMMENT DELETE ====================
@app.route("/ticket/<int:tid>/comment-delete/<int:comment_id>", methods=["POST"])
def ticket_comment_delete(tid, comment_id):
    """Delete a progress comment and every attachment belonging to it."""
    with db() as c:
        comment_row = c.execute(
            "SELECT tc.id, tc.ticket_id, tc.display, tc.username, t.no_tiket, t.pelapor, t.support_id "
            "FROM ticket_comments tc "
            "JOIN tickets t ON t.id=tc.ticket_id "
            "WHERE tc.id=? AND tc.ticket_id=? AND tc.deleted_at IS NULL",
            (comment_id, tid),
        ).fetchone()
        attachment_rows = c.execute(
            "SELECT id, filename, original_name, qnap_path "
            "FROM comment_attachments WHERE comment_id=? AND deleted_at IS NULL ORDER BY id ASC",
            (comment_id,),
        ).fetchall() if comment_row else []

    if not comment_row:
        return jsonify(ok=False, error="Comment tidak ditemukan."), 404

    if not user_can_delete_ticket_comment(comment_row, comment_row):
        abort(403)

    physical_items = []
    try:
        for row in attachment_rows:
            if row["qnap_path"]:
                path, _ = _ticket_evidence_path(row["qnap_path"])
                physical_items.append((row, path))
            elif row["filename"]:
                physical_items.append((row, DASHBOARD_UPLOAD_STORAGE.resolve("ticket", row["filename"])))
    except ValueError:
        return jsonify(ok=False, error="Invalid attachment path."), 400

    try:
        with QNAP_UPLOAD_FINALIZE_LOCK:
            now_text = local_now().strftime("%Y-%m-%d %H:%M:%S")
            delete_errors = {}
            deleted_qnap_paths = []
            for row, physical_path in physical_items:
                try:
                    if physical_path is None:
                        # Attachment bytes already absent; metadata can still be removed.
                        continue
                    os.remove(physical_path)
                    if row["qnap_path"]:
                        deleted_qnap_paths.append(row["qnap_path"])
                except FileNotFoundError:
                    if row["qnap_path"]:
                        deleted_qnap_paths.append(row["qnap_path"])
                except OSError as exc:
                    delete_errors[int(row["id"])] = str(exc)
                    app.logger.warning("Comment metadata deleted but physical file cleanup failed: %r", exc)

            with db() as c:
                c.execute(
                    "UPDATE comment_attachments SET deleted_at=?, delete_error='' WHERE comment_id=? AND deleted_at IS NULL",
                    (now_text, comment_id),
                )
                for attachment_id, error in delete_errors.items():
                    c.execute("UPDATE comment_attachments SET delete_error=? WHERE id=?", (error[:1000], attachment_id))
                c.execute(
                    "UPDATE ticket_comments SET deleted_at=?, deleted_by=?, delete_error=? WHERE id=? AND ticket_id=? AND deleted_at IS NULL",
                    (now_text, g.user["display"], "; ".join(delete_errors.values())[:1000], comment_id, tid),
                )
                if deleted_qnap_paths:
                    placeholders = ",".join("?" for _ in deleted_qnap_paths)
                    c.execute(
                        f"DELETE FROM file_items WHERE qnap_path IN ({placeholders})",
                        tuple(deleted_qnap_paths),
                    )
                for row, physical_path in physical_items:
                    if int(row["id"]) in delete_errors:
                        queue_qnap_delete_retry(c, tid, comment_id, int(row["id"]), row["qnap_path"] or "", physical_path, delete_errors[int(row["id"])], now_text)
                c.commit()

        _invalidate_qnap_listing_cache()
        record_activity(
            tid,
            "Comment deleted",
            g.user["display"],
            f"Comment #{comment_id} and {len(attachment_rows)} attachment(s) deleted",
        )
        return jsonify(
            ok=True,
            message="Comment deleted. File cleanup queued for retry." if delete_errors else "Comment and related files deleted.",
            deleted_files=len(attachment_rows),
            retry_count=len(delete_errors),
        )
    except Exception:
        app.logger.exception("Ticket comment delete failed ticket=%s comment=%s", tid, comment_id)
        return jsonify(ok=False, error="Unable to delete the comment and its files."), 500
# ================== END PPG EDIT ZONE: TICKET COMMENT DELETE ==================


@app.route("/comment-file/<name>")
def comment_file(name):
    if not re.fullmatch(r"[a-f0-9]{32}\.(jpg|png|gif|webp)", name):
        abort(404)
    path = DASHBOARD_UPLOAD_STORAGE.resolve("ticket", name)
    if path is None:
        abort(404)
    return send_file(path, conditional=True)


@app.route("/comment-file-id/<int:attachment_id>")
def comment_file_id(attachment_id):
    with db() as c:
        row = c.execute(
            "SELECT ca.*, tc.ticket_id FROM comment_attachments ca "
            "JOIN ticket_comments tc ON tc.id=ca.comment_id WHERE ca.id=? AND ca.deleted_at IS NULL AND tc.deleted_at IS NULL",
            (attachment_id,),
        ).fetchone()
    if not row or not row["qnap_path"]:
        abort(404)
    try:
        path, _ = _ticket_evidence_path(row["qnap_path"])
    except ValueError:
        abort(404)
    except OSError:
        abort(503)
    if not os.path.isfile(path):
        abort(404)
    mime = row["mime_type"] or mimetypes.guess_type(row["original_name"] or row["filename"])[0] or "application/octet-stream"
    inline = mime.startswith("image/") or mime == "application/pdf"
    return send_file(
        path,
        as_attachment=not inline,
        download_name=row["original_name"] or row["filename"],
        mimetype=mime,
        conditional=True,
        etag=True,
        last_modified=os.path.getmtime(path),
        max_age=300,
    )


@app.route("/ticket/<int:tid>/files")
def ticket_files(tid):
    with db() as c:
        row = c.execute("SELECT id, no_tiket FROM tickets WHERE id=?", (tid,)).fetchone()
    if not row:
        abort(404)
    try:
        storage = _ensure_ticket_storage_tree(tid, row["no_tiket"])
    except RuntimeError as exc:
        flash(str(exc), "err")
        return redirect(request.referrer or f"/ticket/{tid}")
    flash("Private evidence is available in this ticket's Progress attachments. The general File Center excludes root_dashboard.", "ok")
    return redirect(url_for("ticket_detail", tid=tid))
@app.post("/api/admin/tickets/delete")
def api_admin_delete_tickets():
    if g.user["role"] != "admin":
        return jsonify(ok=False, error="Administrator permission is required."), 403
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("ticket_ids")
    password = str(payload.get("password") or "")
    if isinstance(raw_ids, (int, str)):
        raw_ids = [raw_ids]
    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify(ok=False, error="ticket_ids must be a non-empty array."), 400
    if not password:
        return jsonify(ok=False, error="Administrator password is required."), 400
    if not check_password_hash(g.user["password_hash"], password):
        app.logger.warning("Archive delete denied: invalid admin password user=%s", g.user["username"])
        return jsonify(ok=False, error="Administrator password is incorrect."), 403

    ids=[]
    failed=[]
    for value in raw_ids:
        try:
            tid=int(value)
            if tid<=0: raise ValueError
        except Exception:
            failed.append({"ticket_id": value, "reason": "Invalid ticket ID."})
            continue
        if tid not in ids: ids.append(tid)
    if not ids:
        return jsonify(ok=False, error="No valid ticket IDs were supplied.", failed_items=failed), 400

    now_text=local_now().strftime("%Y-%m-%d %H:%M:%S")
    cleanup_files=[]
    success=[]
    try:
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            placeholders=','.join('?' for _ in ids)
            rows=c.execute(f"SELECT id,no_tiket,foto,completion_foto FROM tickets WHERE id IN ({placeholders})", ids).fetchall()
            by_id={int(r["id"]):r for r in rows}
            for tid in ids:
                row=by_id.get(tid)
                if not row:
                    failed.append({"ticket_id":tid,"reason":"Ticket not found."})
                    continue
                attachment_rows=c.execute("SELECT filename FROM comment_attachments WHERE comment_id IN (SELECT id FROM ticket_comments WHERE ticket_id=?)", (tid,)).fetchall()
                files=[x for x in [row["foto"],row["completion_foto"]] if x]
                files.extend(r["filename"] for r in attachment_rows if r["filename"])
                c.execute("INSERT INTO audit_log(action,actor,username,ticket_id,ticket_no,details,created_at) VALUES(?,?,?,?,?,?,?)",
                          ("Ticket deleted",g.user["display"],g.user["username"],tid,row["no_tiket"],"Deleted from Admin Archive",now_text))
                c.execute("DELETE FROM comment_attachments WHERE comment_id IN (SELECT id FROM ticket_comments WHERE ticket_id=?)", (tid,))
                c.execute("DELETE FROM ticket_comments WHERE ticket_id=?", (tid,))
                c.execute("DELETE FROM ticket_activity WHERE ticket_id=?", (tid,))
                c.execute("DELETE FROM notifications WHERE ticket_id=?", (tid,))
                c.execute("DELETE FROM tickets WHERE id=?", (tid,))
                cleanup_files.extend(files)
                success.append({"ticket_id":tid,"no_tiket":row["no_tiket"]})
            c.commit()
        _invalidate_query_cache()
    except Exception as exc:
        app.logger.exception("Admin archive delete transaction failed ids=%r", ids)
        return jsonify(ok=False,error=f"Delete transaction failed: {type(exc).__name__}: {exc}",succeeded=0,failed=len(ids),successful_items=[],failed_items=[{"ticket_id":x,"reason":f"Transaction rolled back: {type(exc).__name__}: {exc}"} for x in ids]),500

    cleanup_errors=[]
    for name in cleanup_files:
        if not name:
            continue
        try:
            DASHBOARD_UPLOAD_STORAGE.remove("ticket", name)
        except (OSError, ValueError) as exc:
            cleanup_errors.append({"filename":name,"reason":str(exc)})
    if cleanup_errors:
        app.logger.warning("Archive delete file cleanup incomplete: %r", cleanup_errors)
    return jsonify(ok=True,succeeded=len(success),failed=len(failed),successful_items=success,failed_items=failed,cleanup_errors=cleanup_errors,
                    message=f"{len(success)} berhasil, {len(failed)} gagal")


@app.route("/delete/<int:tid>", methods=["POST"])
def delete(tid):
    if g.user["role"] != "admin":
        abort(403)
    password = request.form.get("admin_password", "")
    if not password or not check_password_hash(g.user["password_hash"], password):
        flash("Administrator password is required and must be correct.", "err")
        return redirect(request.referrer or url_for("admin_tickets"))

    attachment_files = []

    with db() as c:
        row = c.execute(
            "SELECT foto, completion_foto FROM tickets WHERE id=?",
            (tid,),
        ).fetchone()
        if not row:
            abort(404)

        attachment_rows = c.execute(
            "SELECT filename FROM comment_attachments "
            "WHERE comment_id IN (SELECT id FROM ticket_comments WHERE ticket_id=?)",
            (tid,),
        ).fetchall()
        attachment_files = [r["filename"] for r in attachment_rows]

        c.execute("DELETE FROM comment_attachments WHERE comment_id IN (SELECT id FROM ticket_comments WHERE ticket_id=?)", (tid,))
        c.execute("DELETE FROM ticket_comments WHERE ticket_id=?", (tid,))
        c.execute("DELETE FROM ticket_activity WHERE ticket_id=?", (tid,))
        c.execute("DELETE FROM notifications WHERE ticket_id=?", (tid,))
        c.execute("INSERT INTO audit_log(action,actor,username,ticket_id,ticket_no,details,created_at) VALUES(?,?,?,?,?,?,?)",
                  ("Ticket deleted", g.user["display"], g.user["username"], tid, row["no_tiket"], "Deleted from Admin Archive", local_now().strftime("%Y-%m-%d %H:%M:%S")))
        c.execute("DELETE FROM tickets WHERE id=?", (tid,))

    photo_names = [row["foto"], row["completion_foto"]] + attachment_files
    for photo_name in photo_names:
        if photo_name:
            try:
                DASHBOARD_UPLOAD_STORAGE.remove("ticket", photo_name)
            except (OSError, ValueError):
                pass

    flash("Ticket deleted successfully.", "ok")
    return redirect(request.referrer or "/")


@app.route("/foto/<name>")
def foto(name):
    if not re.fullmatch(r"[a-f0-9]{32}\.(jpg|png|gif|webp)", name):
        abort(404)
    path = DASHBOARD_UPLOAD_STORAGE.resolve("ticket", name)
    if path is None:
        abort(404)
    return send_file(path, conditional=True)


@app.route("/files", strict_slashes=False)
def file_center():
    return send_from_directory(STATIC_PAGE_DIR, "file_center.html")


@app.get("/api/files/list")
def api_file_listing():
    started = time.perf_counter()
    relative_path=request.args.get("path","")
    refresh = request.args.get("refresh") == "1"
    # _qnap_listing() already verifies SMB availability. Do not repeat an
    # expensive os.path.isdir(UNC) call before the actual directory lookup.
    app.logger.info("File Center list start depth=%d refresh=%s",
                    relative_path.count("/") + bool(relative_path), refresh)
    try:
        folder_id,parts,folders,files=_qnap_listing(
            relative_path, force_refresh=refresh
        )
    except (OSError,ValueError,FileNotFoundError) as exc:
        app.logger.warning("File Center list failed after %.1fms: %s",
                           (time.perf_counter()-started)*1000, type(exc).__name__)
        return jsonify(ok=False,error=str(exc),code="qnap_list_failed"),503
    elapsed_ms = (time.perf_counter()-started)*1000
    if elapsed_ms >= 750:
        app.logger.warning("File Center slow list took %.1fms depth=%d count=%d",
                           elapsed_ms, len(parts), len(folders)+len(files))
    for item in folders:
        item.setdefault("created_by","QNAP"); item.setdefault("created_at",local_now().strftime("%Y-%m-%d %H:%M:%S"))
    payload_files=[]
    for item in files:
        payload_files.append({
            "id":item.get("id"),"original_name":item["original_name"],"mime_type":item["mime_type"],
            "size_bytes":int(item["size_bytes"]),"size_label":format_file_size(item["size_bytes"]),
            "uploaded_display":item.get("uploaded_display") or "-","created_at":item.get("created_at") or "-",
            "kind":(os.path.splitext(item["original_name"])[1].lstrip(".") or "FILE").upper(),
        })
    return jsonify(ok=True,path=relative_path,folder_id=folder_id,folder_name=(parts[-1] if parts else "Root Files"),folders=[{
        "id":int(x["id"]),"name":x["name"],"created_by":x.get("created_by") or "QNAP","created_at":x.get("created_at") or "-"
    } for x in folders],files=payload_files,generated_at=local_now().isoformat())


@app.route("/files/folder", methods=["POST"], strict_slashes=False)
def file_create_folder():
    """Create directly on QNAP using the current parent path as the source of truth."""
    wants_json = request.is_json or "application/json" in (request.headers.get("Accept") or "")
    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}

    storage_ok, storage_status = require_qnap_storage()
    if not storage_ok:
        message = f"File Center storage unavailable: {storage_status['message']}"
        if wants_json:
            return jsonify(ok=False, error=message, code="qnap_unreachable"), 503
        flash(message, "err")
        return redirect(url_for("file_center"))

    name = ((payload.get("name") if payload else "") or "").strip()
    if not name or len(name) > 80 or "/" in name or "\\" in name or name in {".", ".."}:
        message = "Folder name is invalid. Use a simple name without / or \\."
        if wants_json:
            return jsonify(ok=False, error=message, code="invalid_name"), 400
        flash(message, "err")
        return redirect(url_for("file_center"))

    parent_path_raw = (payload.get("parent_path") if payload else "") or ""
    try:
        parent_storage_path, parent_parts = qnap_path_from_relative(parent_path_raw)
    except ValueError as exc:
        message = f"Invalid QNAP parent path: {exc}"
        if wants_json:
            return jsonify(ok=False, error=message, code="invalid_parent_path"), 400
        flash(message, "err")
        return redirect(url_for("file_center"))

    if not os.path.isdir(parent_storage_path):
        message = f"QNAP parent folder does not exist or is not reachable: {parent_storage_path}"
        app.logger.error("File Center create folder parent missing: %s", parent_storage_path)
        if wants_json:
            return jsonify(ok=False, error=message, code="parent_unreachable", path=parent_storage_path), 404
        flash(message, "err")
        return redirect(url_for("file_center"))

    qnap_root = os.path.normpath(QNAP_SHARE)
    target_path = os.path.normpath(os.path.join(parent_storage_path, name))
    if not _path_is_within_root(qnap_root, target_path):
        message = "Invalid target path: target is outside the configured QNAP share."
        app.logger.error(
            "File Center create folder rejected target outside QNAP root: root=%r parent=%r target=%r",
            qnap_root, parent_storage_path, target_path,
        )
        if wants_json:
            return jsonify(ok=False, error=message, code="invalid_target_path",
                           root=qnap_root, parent_path=parent_storage_path, path=target_path), 400
        flash(message, "err")
        return redirect(url_for("file_center"))

    if os.path.exists(target_path):
        message = "A folder with that name already exists here on QNAP."
        if wants_json:
            return jsonify(ok=False, error=message, code="duplicate", path=target_path), 409
        flash(message, "err")
        return redirect(url_for("file_center"))

    try:
        os.makedirs(target_path, exist_ok=False)
    except FileExistsError:
        message = "A folder with that name already exists here on QNAP."
        if wants_json:
            return jsonify(ok=False, error=message, code="duplicate", path=target_path), 409
        flash(message, "err")
        return redirect(url_for("file_center"))
    except PermissionError as exc:
        message = f"Access denied while creating the QNAP folder: {exc}"
        app.logger.exception("File Center folder permission error: parent=%s name=%r", parent_storage_path, name)
        if wants_json:
            return jsonify(ok=False, error=message, code="access_denied", path=target_path), 403
        flash(message, "err")
        return redirect(url_for("file_center"))
    except OSError as exc:
        message = f"Could not create folder on QNAP: {exc}"
        app.logger.exception("File Center QNAP mkdir failed: parent=%s name=%r", parent_storage_path, name)
        if wants_json:
            return jsonify(ok=False, error=message, code="qnap_mkdir_failed", path=target_path), 500
        flash(message, "err")
        return redirect(url_for("file_center"))

    # QNAP is the storage source of truth. Re-scan immediately so the new
    # folder appears without a page reload and receives its DB metadata.
    sync_ok = _sync_qnap_tree(force=True)
    _invalidate_qnap_listing_cache()
    created_rel_parts = [*parent_parts, name]
    created_row = folder_row_by_relative_path(created_rel_parts)
    if not created_row:
        message = "Folder was created on QNAP, but metadata sync could not find it yet."
        app.logger.error("File Center metadata sync missed newly-created folder: %s", target_path)
        if wants_json:
            return jsonify(ok=False, error=message, code="qnap_sync_incomplete", path=target_path, sync_ok=bool(sync_ok)), 500
        flash(message, "err")
        return redirect(url_for("file_center"))

    folder_data = {
        "id": int(created_row["id"]),
        "name": created_row["name"],
        "parent_id": created_row["parent_id"],
        "created_by": created_row["created_by"],
        "created_at": created_row["created_at"],
    }
    if wants_json:
        return jsonify(ok=True, folder=folder_data, path=target_path), 201
    flash(f"Folder '{name}' created.", "ok")
    return redirect(url_for("file_center", folder=created_row["parent_id"]) if created_row["parent_id"] else url_for("file_center"))


def _safe_upload_name(value):
    raw=os.path.basename((value or "file").replace("\\","/")).strip()
    raw=re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", raw).strip(" .")[:180]
    if not raw or raw in {".",".."}:
        raise ValueError("The selected file name is invalid.")
    stem,ext=os.path.splitext(raw)
    if stem.upper() in {"CON","PRN","AUX","NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]",stem.upper()):
        raw="_"+raw
    return raw


def _unique_qnap_filename(storage_dir,name):
    candidate=os.path.join(storage_dir,name)
    if not os.path.exists(candidate):
        return name
    stem,ext=os.path.splitext(name)
    index=1
    while True:
        candidate_name=f"{stem} ({index}){ext}"
        if not os.path.exists(os.path.join(storage_dir,candidate_name)):
            return candidate_name
        index+=1


def _safe_upload_relative_path(value):
    raw = unquote(str(value or "")).strip()
    if not raw:
        return []
    raw = raw.replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or ntpath.isabs(raw):
        raise ValueError("The upload path must be relative to the current File Center folder.")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("The upload path contains an invalid or unsafe segment.")
    try:
        return [_safe_upload_name(part) for part in parts]
    except ValueError:
        raise ValueError("The upload path contains an invalid file or folder name.")


@app.route("/files/upload-fast", methods=["POST"], strict_slashes=False)
def file_upload_fast():
    global QNAP_SYNC_DIRTY
    # REVISI-1: stream directly to QNAP, verify the received byte count, and never
    # delete a fully-published QNAP file merely because SQLite metadata fails.
    request_started = time.perf_counter()
    storage_ok, storage_status = require_qnap_storage()
    if not storage_ok:
        return jsonify(ok=False, error=f"QNAP storage unavailable: {storage_status['message']}", code="qnap_unreachable"), 503

    try:
        original_name = _safe_upload_name(unquote(request.headers.get("X-File-Name", "")))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc), code="invalid_filename"), 400

    expected_header = request.headers.get("X-File-Size", "").strip()
    try:
        expected_size = int(expected_header) if expected_header else int(request.content_length or 0)
    except (TypeError, ValueError):
        expected_size = int(request.content_length or 0)
    if expected_size < 0 or expected_size > FILE_MAX_SIZE:
        return jsonify(ok=False, error="Maximum library file size is 8 GB.", code="file_too_large"), 413
    if request.content_length is not None and request.content_length != expected_size:
        return jsonify(ok=False, error="Upload size metadata does not match the request body.", code="size_mismatch"), 400

    relative_path = request.headers.get("X-Folder-Path", "")
    folder_relative_file = request.headers.get("X-Relative-Path", "")
    try:
        current_storage_dir, current_parts = qnap_path_from_relative(unquote(relative_path))
        if not os.path.isdir(current_storage_dir):
            return jsonify(ok=False, error=f"QNAP target folder does not exist: {current_storage_dir}", code="parent_unreachable"), 404

        relative_parts = _safe_upload_relative_path(folder_relative_file) if folder_relative_file else []
        if relative_parts:
            original_name = _safe_upload_name(relative_parts[-1])
            target_parts = list(current_parts)
            storage_dir = current_storage_dir
            for folder_name in relative_parts[:-1]:
                candidate = os.path.join(storage_dir, folder_name)
                actual_folder_name = folder_name
                if os.path.exists(candidate) and not os.path.isdir(candidate):
                    stem, ext = os.path.splitext(folder_name)
                    index = 1
                    while True:
                        actual_folder_name = f"{stem} ({index}){ext}"
                        candidate = os.path.join(storage_dir, actual_folder_name)
                        if not os.path.exists(candidate):
                            break
                        index += 1
                os.makedirs(candidate, exist_ok=True)
                target_parts.append(actual_folder_name)
                storage_dir = candidate
            _qnap_rel_to_physical(_qnap_rel_join(target_parts))
            parent_parts = target_parts
        else:
            storage_dir = current_storage_dir
            parent_parts = current_parts
    except ValueError as exc:
        return jsonify(ok=False, error=f"Invalid target path: {exc}", code="invalid_target_path"), 400
    except PermissionError as exc:
        return jsonify(ok=False, error=f"Access denied while creating the QNAP upload folder: {exc}", code="access_denied"), 403
    except OSError as exc:
        return jsonify(ok=False, error=f"Could not prepare the QNAP upload folder: {exc}", code="qnap_mkdir_failed"), 500

    # REVISI-UPLOAD: first spool the HTTP body locally, then copy to QNAP.
    # The previous implementation wrote request.stream directly to the UNC share.
    # A temporary SMB stall could back-pressure the HTTP request and make the browser
    # look frozen halfway through the upload. Separating the two phases keeps the
    # client upload independent from QNAP write latency and lets us verify both copies.
    local_temp_path = None
    qnap_temp_path = None
    final_path = None
    final_name = None
    rel_path = None
    total = 0
    buffer_size = 2 * 1024 * 1024
    receive_seconds = 0.0
    qnap_write_seconds = 0.0
    qnap_wait_seconds = 0.0
    published = False

    def _cleanup_upload_temps():
        for candidate in (local_temp_path, qnap_temp_path):
            if candidate:
                try:
                    if os.path.exists(candidate):
                        os.remove(candidate)
                except OSError:
                    app.logger.warning("File Center could not remove temporary upload file: %r", candidate, exc_info=True)

    try:
        fd, local_temp_path = tempfile.mkstemp(prefix=".file-center-", suffix=".part", dir=TEMP_UPLOAD_DIR)
        os.close(fd)
        with open(local_temp_path, "wb", buffering=4 * 1024 * 1024) as out:
            while True:
                read_started = time.perf_counter()
                chunk = request.stream.read(buffer_size)
                receive_seconds += time.perf_counter() - read_started
                if not chunk:
                    break
                total += len(chunk)
                if total > FILE_MAX_SIZE:
                    raise ValueError("Maximum library file size is 8 GB.")
                out.write(chunk)
            out.flush()
            try:
                os.fsync(out.fileno())
            except OSError:
                pass

        actual_local_size = os.path.getsize(local_temp_path)
        if total != actual_local_size:
            raise IOError(f"Temporary upload size mismatch: received {total} bytes, stored {actual_local_size} bytes.")
        if expected_size and total != expected_size:
            raise IOError(f"Upload stopped before completion: received {total} of {expected_size} bytes.")

        # REVISI-UPLOAD: serialize only the QNAP publish/metadata step.
        # The browser has already finished sending the request body before this phase.
        qnap_lock_started = time.perf_counter()
        with QNAP_UPLOAD_FINALIZE_LOCK:
            qnap_wait_seconds = time.perf_counter() - qnap_lock_started
            # Consider BOTH the physical QNAP directory and historical SQLite
            # metadata. The previous loop could choose the same name forever
            # if SQLite contained a stale row but the QNAP file was missing.
            stem, ext = os.path.splitext(original_name)
            suffix = 0
            while True:
                final_name = original_name if suffix == 0 else f"{stem} ({suffix}){ext}"
                if len(final_name) > 180:
                    final_name = f"{stem[:max(1, 180-len(ext)-len(str(suffix))-3)]} ({suffix}){ext}"
                rel_path = _qnap_rel_join([*parent_parts, final_name])
                if not os.path.exists(os.path.join(storage_dir, final_name)):
                    with db() as probe:
                        taken = probe.execute("SELECT 1 FROM file_items WHERE stored_name=?", (rel_path,)).fetchone()
                    if not taken:
                        break
                suffix += 1

            qnap_temp_path = os.path.join(storage_dir, f".{secrets.token_hex(12)}.part")
            copy_started = time.perf_counter()
            with open(local_temp_path, "rb", buffering=4 * 1024 * 1024) as src, open(qnap_temp_path, "wb", buffering=4 * 1024 * 1024) as dst:
                shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
                dst.flush()
                try:
                    os.fsync(dst.fileno())
                except OSError:
                    pass
            qnap_write_seconds += time.perf_counter() - copy_started

            actual_qnap_size = os.path.getsize(qnap_temp_path)
            if actual_qnap_size != total:
                raise IOError(f"QNAP temporary upload size mismatch: expected {total} bytes, stored {actual_qnap_size} bytes.")

            final_path = os.path.join(storage_dir, final_name)
            os.replace(qnap_temp_path, final_path)
            qnap_temp_path = None
            published = True
            mime_type = mimetypes.guess_type(final_name)[0] or "application/octet-stream"
            now_text = local_now().strftime("%Y-%m-%d %H:%M:%S")
            folder_id = _folder_path_id_from_parts(parent_parts)
            with db() as c:
                if parent_parts:
                    metadata_parent_id = None
                    metadata_parts = []
                    for folder_name in parent_parts:
                        metadata_parts.append(folder_name)
                        metadata_parent_id = _ensure_folder_metadata(
                            c, folder_name, metadata_parent_id, _qnap_rel_join(metadata_parts)
                        )
                    folder_id = metadata_parent_id
                # The listing endpoint can see the published QNAP file before
                # this transaction starts. Atomic UPSERT prevents UNIQUE errors.
                c.execute(
                    """INSERT INTO file_items
                    (folder_id,stored_name,disk_name,original_name,mime_type,size_bytes,uploaded_by,uploaded_display,created_at,qnap_path)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(stored_name) DO UPDATE SET
                      folder_id=excluded.folder_id, disk_name=excluded.disk_name,
                      original_name=excluded.original_name, mime_type=excluded.mime_type,
                      size_bytes=excluded.size_bytes, uploaded_by=excluded.uploaded_by,
                      uploaded_display=excluded.uploaded_display, created_at=excluded.created_at,
                      qnap_path=excluded.qnap_path""",
                    (folder_id, rel_path, final_name, final_name, mime_type, total, g.user["username"], g.user["display"], now_text, rel_path),
                )
                file_id = int(c.execute("SELECT id FROM file_items WHERE stored_name=?", (rel_path,)).fetchone()["id"])

    except RequestEntityTooLarge:
        message="Upload rejected because the request exceeds the 8 GB limit."
        app.logger.warning("File Center upload exceeded request limit: file=%r", original_name)
        _cleanup_upload_temps()
        return jsonify(ok=False, error=message, code="file_too_large"), 413
    except ClientDisconnected as exc:
        message=f"Upload connection closed before completion: {exc or 'client disconnected'}."
        app.logger.warning("File Center client disconnected during upload: file=%r received=%d expected=%d", original_name, total, expected_size)
        _cleanup_upload_temps()
        return jsonify(ok=False, error=message, code="client_disconnected", received_bytes=total, expected_bytes=expected_size), 400
    except (ConnectionResetError, BrokenPipeError, TimeoutError) as exc:
        message=f"Upload connection was interrupted: {exc}."
        app.logger.warning("File Center transport interrupted: file=%r received=%d expected=%d", original_name, total, expected_size, exc_info=True)
        _cleanup_upload_temps()
        return jsonify(ok=False, error=message, code="connection_interrupted", received_bytes=total, expected_bytes=expected_size), 400
    except ValueError as exc:
        _cleanup_upload_temps()
        return jsonify(ok=False, error=str(exc), code="upload_failed", received_bytes=total, expected_bytes=expected_size), 413
    except PermissionError as exc:
        app.logger.exception("File Center upload permission failure: folder=%r file=%r", storage_dir, original_name)
        _cleanup_upload_temps()
        return jsonify(ok=False, error=f"Access denied while writing to QNAP: {exc}", code="access_denied"), 403
    except sqlite3.IntegrityError as exc:
        # Keep the already-published file on QNAP. The next metadata sync can
        # index it instead of silently deleting a successful upload.
        QNAP_SYNC_DIRTY = True
        _invalidate_qnap_listing_cache()
        app.logger.exception("File Center upload metadata conflict after QNAP publish: %s", exc)
        _cleanup_upload_temps()
        return jsonify(ok=False, error="The file reached QNAP but metadata could not be recorded. It was kept on QNAP and will be re-synced.", code="metadata_conflict", qnap_path=rel_path if 'rel_path' in locals() else None), 500
    except sqlite3.Error as exc:
        QNAP_SYNC_DIRTY = True
        _invalidate_qnap_listing_cache()
        app.logger.exception("File Center upload database error after QNAP publish: %s", exc)
        _cleanup_upload_temps()
        return jsonify(ok=False, error="The file reached QNAP but the metadata database failed. It was kept on QNAP and will be re-synced.", code="metadata_db_error", qnap_path=rel_path if 'rel_path' in locals() else None), 500
    except OSError as exc:
        app.logger.exception("File Center upload I/O failure: folder=%r file=%r", storage_dir, final_name or original_name)
        _cleanup_upload_temps()
        return jsonify(ok=False, error=f"QNAP write failed: {exc}", code="qnap_write_failed", received_bytes=total, expected_bytes=expected_size), 500

    total_seconds = max(time.perf_counter() - request_started, 0.0001)
    receive_ms = receive_seconds * 1000.0
    qnap_write_ms = qnap_write_seconds * 1000.0
    qnap_wait_ms = qnap_wait_seconds * 1000.0
    throughput = (total / 1024 / 1024) / total_seconds if total else 0
    _cleanup_upload_temps()
    app.logger.info(
        "File Center upload complete file=%r size=%d bytes total_ms=%.1f receive_ms=%.1f qnap_wait_ms=%.1f qnap_write_ms=%.1f throughput=%.2f MB/s",
        final_name, total, total_seconds * 1000.0, receive_ms, qnap_wait_ms, qnap_write_ms, throughput
    )

    QNAP_SYNC_DIRTY = True
    _invalidate_qnap_listing_cache()
    return jsonify(
        ok=True,
        file={
            "id": file_id,
            "name": final_name,
            "original_name": final_name,
            "size_bytes": total,
            "size_label": format_file_size(total),
            "uploaded_display": g.user["display"],
            "created_at": now_text,
            "qnap_path": rel_path,
        },
        receive_ms=round(receive_ms, 1),
        qnap_wait_ms=round(qnap_wait_ms, 1),
        qnap_write_ms=round(qnap_write_ms, 1),
        total_ms=round(total_seconds * 1000.0, 1),
        throughput_mbps=round(throughput, 2),
    ), 200


@app.route("/files/upload", methods=["POST"], strict_slashes=False)
def file_upload():
    """Reject legacy multipart upload so large files always use the QNAP streaming endpoint."""
    storage_ok, storage_status = require_qnap_storage()
    if not storage_ok:
        return jsonify(ok=False, error=f"QNAP storage unavailable: {storage_status['message']}"), 503
    return jsonify(
        ok=False,
        error="Legacy multipart upload is disabled. Use the File Center streaming upload endpoint."
    ), 410



def _qnap_read_fallback_path(primary_path):
    """Map a File Center file to the equivalent secondary UNC path, read-only.

    Preserve the shared folder-relative path and existing private directory
    exclusion. This is not a general SMB load balancer.
    """
    secondary = QNAP_READ_SECONDARY_SHARE
    if not secondary or ntpath.normcase(ntpath.normpath(secondary)) == ntpath.normcase(ntpath.normpath(QNAP_SHARE)):
        return None
    # Reject traversal *before* normpath collapses dot-dot components.
    if ".." in ntpath.normcase(primary_path).split("\\"):
        return None
    root = ntpath.normpath(QNAP_SHARE).rstrip("\\/")
    full = ntpath.normpath(primary_path)
    prefix = ntpath.normcase(root) + "\\"
    full_casefold = ntpath.normcase(full)
    if not full_casefold.startswith(prefix):
        return None
    relative = full[len(root):].lstrip("\\/")
    if not relative or ".." in relative.split("\\"):
        return None
    candidate = ntpath.normpath(ntpath.join(secondary, relative))
    if not _path_is_within_root(secondary, candidate):
        return None
    return candidate


def _serve_file_download(file_id):
    # Never run a full QNAP tree scan before a download: large shares could
    # block every request and even rename legacy files during that scan.
    started = time.perf_counter()
    with db() as c:
        row = c.execute("SELECT * FROM file_items WHERE id=?", (file_id,)).fetchone()
    if not row:
        abort(404)
    path = file_storage_path(row)
    if not _path_is_within_root(QNAP_SHARE, path):
        abort(404)
    source = "primary"
    try:
        file_stat = os.stat(path)
    except OSError as primary_error:
        secondary_path = _qnap_read_fallback_path(path)
        if not secondary_path:
            app.logger.warning("File Center download source unavailable id=%d primary=%s",
                               file_id, type(primary_error).__name__)
            abort(404 if isinstance(primary_error, FileNotFoundError) else 503)
        try:
            available, _ = _ensure_qnap_connection(QNAP_READ_SECONDARY_SHARE)
            if not available:
                raise OSError("Secondary QNAP SMB unavailable")
            secondary_stat = os.stat(secondary_path)
            # A secondary share pointing at an older/different dataset must
            # never silently return a file with a different byte count.
            if int(secondary_stat.st_size) != int(row["size_bytes"]):
                raise OSError("Secondary QNAP file size differs from indexed file")
            path, file_stat, source = secondary_path, secondary_stat, "secondary"
            app.logger.warning("File Center download read failover id=%d to secondary", file_id)
        except OSError as secondary_error:
            app.logger.warning("File Center download failed id=%d primary=%s secondary=%s",
                               file_id, type(primary_error).__name__, type(secondary_error).__name__)
            if isinstance(primary_error, FileNotFoundError) and isinstance(secondary_error, FileNotFoundError):
                abort(404)
            abort(503)
    if not stat.S_ISREG(file_stat.st_mode):
        abort(404)

    # The ETag does not depend on which of the QNAP NIC addresses is used.
    # This preserves correct HTTP Range resume across same-file read failover.
    import hashlib
    stable_etag = hashlib.sha256(
        f"{file_id}:{file_stat.st_size}:{file_stat.st_mtime_ns}".encode("ascii")
    ).hexdigest()
    resp = send_file(
        path,
        as_attachment=True,
        download_name=row["original_name"],
        mimetype=row["mime_type"],
        conditional=True,
        etag=stable_etag,
        last_modified=file_stat.st_mtime,
        max_age=300,
    )
    setup_ms = (time.perf_counter()-started)*1000
    if source == "secondary" or setup_ms >= 750:
        app.logger.warning("File Center download setup id=%d source=%s time_ms=%.1f size=%d",
                           file_id, source, setup_ms, file_stat.st_size)
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Cache-Control"] = "private, max-age=300"
    # Header role is known from the authenticated request: avoid navigation flicker.
    if (g.user and g.user["role"] == "admin" and
            (request.path in {"/", "/files", "/ticket", "/admin/tools", "/admin/tickets", "/our-project", "/guide", "/guide/new"}
             or re.fullmatch(r"/(?:our-project|guide)/[0-9]+", request.path))
            and resp.mimetype == "text/html"):
        resp.direct_passthrough = False
        markup = resp.get_data(as_text=True)
        markup = markup.replace('class="admin-only admin-nav-dropdown" hidden',
                                'class="admin-only admin-nav-dropdown"')
        markup = markup.replace('class="admin-only admin-nav-dropdown active" hidden',
                                'class="admin-only admin-nav-dropdown active"')
        resp.set_data(markup)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.route("/files/download/<int:file_id>", strict_slashes=False)
def file_download(file_id):
    return _serve_file_download(file_id)


@app.route("/files/delete-folder/<int:folder_id>", methods=["POST"], strict_slashes=False)
def file_delete_folder(folder_id):
    """Admin-only recursive File Center folder delete on QNAP storage."""
    _sync_qnap_tree()
    if g.user["role"] != "admin":
        abort(403)
    storage_ok, storage_status = require_qnap_storage()
    if not storage_ok:
        flash(f"File Center storage unavailable: {storage_status['message']}", "err")
        return redirect(request.referrer or url_for("file_center"))
    if not folder_id:
        abort(400)
    get_file_folder(folder_id)

    # Build the complete descendant tree from the DB before touching storage.
    with db() as c:
        all_folders = [dict(r) for r in c.execute(
            "SELECT id, name, parent_id FROM file_folders ORDER BY id"
        ).fetchall()]
        all_files = [dict(r) for r in c.execute(
            "SELECT id, folder_id, original_name, stored_name FROM file_items WHERE folder_id IS NOT NULL"
        ).fetchall()]

    children = {}
    for row in all_folders:
        children.setdefault(row["parent_id"], []).append(row["id"])
    folder_ids = []
    stack = [folder_id]
    while stack:
        fid = stack.pop()
        if fid in folder_ids:
            continue
        folder_ids.append(fid)
        stack.extend(children.get(fid, []))

    file_rows = [r for r in all_files if r["folder_id"] in set(folder_ids)]
    physical_root = folder_storage_path(folder_id)
    try:
        if os.path.isdir(physical_root):
            shutil.rmtree(physical_root)
        elif os.path.exists(physical_root):
            os.remove(physical_root)
    except OSError as e:
        flash(f"Folder could not be removed from storage: {e}", "err")
        return redirect(request.referrer or url_for("file_center"))

    with db() as c:
        if file_rows:
            c.executemany("DELETE FROM file_items WHERE id=?", [(r["id"],) for r in file_rows])
        c.executemany("DELETE FROM file_folders WHERE id=?", [(fid,) for fid in reversed(folder_ids)])

    _invalidate_qnap_listing_cache()
    _invalidate_query_cache()
    flash(f"Folder and {len(file_rows)} file(s) deleted by admin.", "ok")
    return redirect(request.referrer or url_for("file_center"))


@app.route("/api/files/transfer", methods=["POST"], strict_slashes=False)
def api_file_transfer():
    """Admin-only File Center copy/cut for selected QNAP items."""
    if g.user["role"] != "admin":
        return jsonify(ok=False, error="Admin access required.", code="admin_required"), 403

    storage_ok, storage_status = require_qnap_storage()
    if not storage_ok:
        return jsonify(ok=False, error=storage_status["message"], code="qnap_unreachable"), 503

    payload = request.get_json(silent=True) or {}
    operation = str(payload.get("operation") or "").strip().lower()
    items = payload.get("items")
    destination_path = str(payload.get("destination_path") or "").strip()
    if operation not in {"copy", "cut"}:
        return jsonify(ok=False, error="Invalid transfer operation.", code="invalid_operation"), 400
    if not isinstance(items, list) or not items or len(items) > 100:
        return jsonify(ok=False, error="Invalid item list.", code="invalid_items"), 400

    try:
        destination_root, _ = qnap_path_from_relative(destination_path)
    except ValueError as exc:
        return jsonify(ok=False, error=f"Invalid destination path: {exc}", code="invalid_destination"), 400
    if not os.path.isdir(destination_root):
        return jsonify(ok=False, error="Destination folder does not exist on QNAP.", code="destination_missing"), 404

    root = os.path.abspath(QNAP_SHARE)

    def clean_rel(value):
        raw = str(value or "").replace("\\", "/").strip().strip("/")
        if not raw or "\x00" in raw:
            raise ValueError("Invalid source path.")
        parts = [part for part in raw.split("/") if part]
        if any(part in {".", ".."} or "\x00" in part for part in parts):
            raise ValueError("Invalid source path.")
        return "/".join(parts)

    def safe_abs(rel):
        candidate = os.path.abspath(os.path.join(root, *rel.split("/")))
        if not _path_is_within_root(root, candidate) or os.path.normcase(candidate) == os.path.normcase(root):
            raise ValueError("Source path is outside the QNAP share.")
        return candidate

    def unique_target(name):
        stem, ext = os.path.splitext(name)
        candidate = os.path.join(destination_root, name)
        index = 1
        while os.path.exists(candidate):
            candidate = os.path.join(destination_root, f"{stem} ({index}){ext}")
            index += 1
        return candidate

    results = []
    for item in items:
        try:
            rel = clean_rel(item.get("path") if isinstance(item, dict) else item)
            source = safe_abs(rel)
            if not os.path.exists(source):
                raise FileNotFoundError("Item not found on QNAP.")
            if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(destination_root)):
                raise ValueError("Cannot transfer a folder into itself.")

            name = os.path.basename(source.rstrip("\\/"))
            target = unique_target(name)
            if operation == "copy":
                if os.path.isdir(source):
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
            else:
                shutil.move(source, target)

            results.append({
                "ok": True,
                "source": rel,
                "destination": os.path.relpath(target, root).replace(os.sep, "/"),
                "kind": "folder" if os.path.isdir(target) else "file",
            })
        except (ValueError, OSError, shutil.Error) as exc:
            results.append({"ok": False, "source": str(item.get("path") if isinstance(item, dict) else item), "reason": str(exc)})
        except Exception as exc:
            app.logger.exception("File Center transfer failed")
            results.append({"ok": False, "source": str(item.get("path") if isinstance(item, dict) else item), "reason": str(exc)})

    _sync_qnap_tree(force=True)
    _invalidate_qnap_listing_cache()
    _invalidate_query_cache()
    failed = [result for result in results if not result.get("ok")]
    return jsonify(
        ok=not failed,
        operation=operation,
        successful=len(results) - len(failed),
        failed=len(failed),
        results=results,
    ), 200 if not failed else 207


@app.route("/api/files/mass-delete", methods=["POST"], strict_slashes=False)
def api_file_mass_delete():
    """Admin-only batch delete from QNAP using relative QNAP paths.

    Each requested path is validated against the configured QNAP share root.
    The endpoint first attempts to move the item into the QNAP @Recycle
    directory. If that is unavailable, it falls back to permanent deletion.
    Database metadata is removed only after the physical operation succeeds.
    """
    if g.user["role"] != "admin":
        return jsonify(ok=False, error="Admin access required.", code="admin_required"), 403

    storage_ok, storage_status = require_qnap_storage()
    if not storage_ok:
        return jsonify(ok=False, error=storage_status["message"], code="qnap_unreachable"), 503

    payload = request.get_json(silent=True) or {}
    paths = payload.get("paths")
    if not isinstance(paths, list) or not paths:
        return jsonify(ok=False, error="No paths were supplied.", code="invalid_paths"), 400
    if len(paths) > 500:
        return jsonify(ok=False, error="Too many items in one request.", code="too_many_paths"), 400

    def clean_rel(value):
        raw = str(value or "").replace("\\", "/").strip().strip("/")
        if not raw or "\x00" in raw:
            raise ValueError("Invalid path.")
        parts = [p for p in raw.split("/") if p]
        if any(p in {".", ".."} or "\x00" in p for p in parts):
            raise ValueError("Invalid path.")
        return "/".join(parts)

    def safe_abs(rel):
        root = os.path.abspath(QNAP_SHARE)
        candidate = os.path.abspath(os.path.join(root, *rel.split("/")))
        if not _path_is_within_root(root, candidate) or os.path.normcase(candidate) == os.path.normcase(root):
            raise ValueError("Path is outside the QNAP share.")
        return candidate

    def try_recycle(source):
        recycle_root = os.path.join(os.path.abspath(QNAP_SHARE), "@Recycle")
        try:
            os.makedirs(recycle_root, exist_ok=True)
            if not os.path.isdir(recycle_root) or not os.access(recycle_root, os.W_OK):
                return False
            base_name = os.path.basename(source.rstrip("\\/")) or "item"
            target = os.path.join(recycle_root, f"FileCenter_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(5)}_{base_name}")
            shutil.move(source, target)
            return True
        except (OSError, shutil.Error) as exc:
            app.logger.warning("QNAP recycle move failed for %s: %s", source, exc)
            return False

    def db_remove_for_path(rel):
        prefix = rel.rstrip("/")
        with db() as c:
            folder_rows = c.execute(
                "SELECT id, qnap_path FROM file_folders WHERE qnap_path=? OR qnap_path LIKE ?",
                (prefix, prefix + "/%"),
            ).fetchall()
            file_rows = c.execute(
                "SELECT id, qnap_path FROM file_items WHERE qnap_path=? OR qnap_path LIKE ?",
                (prefix, prefix + "/%"),
            ).fetchall()
            if file_rows:
                c.executemany("DELETE FROM file_items WHERE id=?", [(int(r["id"]),) for r in file_rows])
            if folder_rows:
                ids = [int(r["id"]) for r in folder_rows]
                c.executemany("DELETE FROM file_folders WHERE id=?", [(fid,) for fid in reversed(ids)])
            return len(file_rows), len(folder_rows)

    results = []
    for raw in paths:
        try:
            rel = clean_rel(raw)
            physical = safe_abs(rel)
            if not os.path.exists(physical):
                results.append({"path": rel, "ok": False, "reason": "Item not found on QNAP."})
                continue

            is_folder = os.path.isdir(physical)
            contains_files = False
            if is_folder:
                for _root, _dirs, names in os.walk(physical):
                    if names:
                        contains_files = True
                        break

            recycled = try_recycle(physical)
            if not recycled:
                try:
                    if is_folder:
                        shutil.rmtree(physical)
                    else:
                        os.remove(physical)
                except OSError as exc:
                    results.append({"path": rel, "ok": False, "reason": f"Delete failed: {exc}"})
                    continue

            file_count, folder_count = db_remove_for_path(rel)
            results.append({
                "path": rel,
                "ok": True,
                "kind": "folder" if is_folder else "file",
                "contains_files": contains_files,
                "recycled": recycled,
                "files_removed": file_count,
                "folders_removed": folder_count,
            })
        except (ValueError, OSError) as exc:
            results.append({"path": str(raw), "ok": False, "reason": str(exc)})
        except Exception as exc:
            app.logger.exception("File Center mass delete failed for %r", raw)
            results.append({"path": str(raw), "ok": False, "reason": f"Unexpected error: {exc}"})

    _invalidate_qnap_listing_cache()
    _invalidate_query_cache()
    success_count = sum(1 for item in results if item.get("ok"))
    failed_count = len(results) - success_count
    return jsonify(
        ok=(failed_count == 0),
        successful=success_count,
        failed=failed_count,
        results=results,
        message=f"{success_count} berhasil, {failed_count} gagal",
    ), 200


@app.route("/files/delete/<int:file_id>", methods=["POST"], strict_slashes=False)
def file_delete(file_id):
    _sync_qnap_tree()
    if g.user["role"] != "admin":
        abort(403)
    storage_ok, storage_status = require_qnap_storage()
    if not storage_ok:
        flash(f"File Center storage unavailable: {storage_status['message']}", "err")
        return redirect(request.referrer or url_for("file_center"))
    with db() as c:
        row = c.execute("SELECT * FROM file_items WHERE id=?", (file_id,)).fetchone()
    if not row:
        abort(404)

    try:
        os.remove(file_storage_path(row))
    except FileNotFoundError:
        pass
    except OSError:
        flash("The file could not be removed from storage, so its record was kept.", "err")
        return redirect(request.referrer or url_for("file_center"))

    with db() as c:
        c.execute("DELETE FROM file_items WHERE id=?", (file_id,))
    _invalidate_qnap_listing_cache()
    _invalidate_query_cache()
    flash(f"'{row['original_name']}' deleted by admin.", "ok")
    return redirect(request.referrer or url_for("file_center"))


def _json_rename_payload():
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload
    return request.form


def _validate_folder_rename(value):
    name = (value or "").strip()
    invalid = '<>:"/\\|?*'
    if not name or len(name) > 80 or any(ch in name for ch in invalid) or name in {".", ".."}:
        raise ValueError("Folder name is invalid.")
    if name[-1] in {".", " "}:
        raise ValueError("Folder name cannot end with a dot or space.")
    if name.upper() in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", name.upper()):
        raise ValueError("That folder name is reserved by Windows.")
    return name


def _validate_file_rename(value):
    name = (value or "").strip()
    invalid = '<>:"/\\|?*'
    if not name or len(name) > 180 or any(ch in name for ch in invalid) or name in {".", ".."}:
        raise ValueError("File name is invalid.")
    if name[-1] in {".", " "}:
        raise ValueError("File name cannot end with a dot or space.")
    stem = os.path.splitext(name)[0]
    if stem.upper() in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", stem.upper()):
        raise ValueError("That file name is reserved by Windows.")
    return name


def _safe_os_rename(old_path, new_path):
    """Rename on Windows safely, including case-only renames."""
    old_abs = os.path.abspath(old_path)
    new_abs = os.path.abspath(new_path)
    if os.path.normcase(old_abs) == os.path.normcase(new_abs) and old_abs != new_abs:
        temp_path = new_abs + f".__rename_{secrets.token_hex(8)}.tmp"
        os.rename(old_abs, temp_path)
        try:
            os.rename(temp_path, new_abs)
        except Exception:
            try:
                os.rename(temp_path, old_abs)
            except OSError:
                app.logger.exception("Failed to restore case-only rename: %s", old_abs)
            raise
        return
    os.rename(old_abs, new_abs)


def _rename_error(message, status=400):
    app.logger.warning("File Center rename rejected: %s", message)
    return jsonify(ok=False, error=message), status


@app.route("/api/files/rename-folder/<int:folder_id>", methods=["PUT", "POST", "PATCH"], strict_slashes=False)
def api_file_rename_folder(folder_id):
    _sync_qnap_tree(force=True)
    if g.user["role"] != "admin":
        return jsonify(ok=False, error="Admin access required."), 403
    storage_ok, storage_status = require_qnap_storage()
    if not storage_ok:
        return jsonify(ok=False, error=f"QNAP storage unavailable: {storage_status['message']}"), 503
    folder = get_file_folder(folder_id)
    if not folder:
        return jsonify(ok=False, error="Folder not found."), 404

    payload = _json_rename_payload()
    try:
        name = _validate_folder_rename(payload.get("name"))
    except ValueError as e:
        return _rename_error(str(e), 400)

    old_name = folder["name"]
    if name == old_name:
        return jsonify(ok=True, name=name, unchanged=True)

    parent_id = folder["parent_id"]
    with db() as c:
        conflict = c.execute(
            "SELECT id FROM file_folders WHERE parent_id IS ? AND name COLLATE NOCASE = ? AND id<>?",
            (parent_id, name, folder_id),
        ).fetchone()
    if conflict:
        return _rename_error("A folder with that name already exists here.", 409)

    old_path = os.path.abspath(folder_storage_path(folder_id))
    if not os.path.isdir(old_path):
        return _rename_error("The physical folder is missing from storage.", 404)
    parent_path = os.path.dirname(old_path)
    new_path = os.path.abspath(os.path.join(parent_path, name))
    if os.path.exists(new_path) and os.path.normcase(new_path) != os.path.normcase(old_path):
        return _rename_error("A storage folder with that name already exists.", 409)

    moved = False
    try:
        _safe_os_rename(old_path, new_path)
        moved = True
        parent_rel = _folder_qnap_relative(parent_id) if parent_id else ""
        new_folder_rel = _qnap_rel_join([*_normalize_qnap_relative_path(parent_rel), name])
        old_folder_rel = str(folder["qnap_path"] or _qnap_rel_join([*_normalize_qnap_relative_path(parent_rel), old_name])).strip("/")
        with db() as c:
            cur = c.execute("UPDATE file_folders SET name=?, qnap_path=? WHERE id=?", (name, new_folder_rel, folder_id))
            if cur.rowcount != 1:
                raise RuntimeError("Folder database record was not updated.")
            descendant_folders = c.execute("SELECT id,qnap_path FROM file_folders WHERE qnap_path LIKE ?", (old_folder_rel + "/%",)).fetchall()
            for child in descendant_folders:
                old_child = str(child["qnap_path"] or "")
                new_child = new_folder_rel + old_child[len(old_folder_rel):]
                c.execute("UPDATE file_folders SET qnap_path=? WHERE id=?", (new_child, child["id"]))
            descendant_files = c.execute("SELECT id,qnap_path,stored_name FROM file_items WHERE qnap_path LIKE ?", (old_folder_rel + "/%",)).fetchall()
            for child in descendant_files:
                old_file = str(child["qnap_path"] or child["stored_name"] or "")
                new_file = new_folder_rel + old_file[len(old_folder_rel):]
                c.execute("UPDATE file_items SET qnap_path=?, stored_name=? WHERE id=?", (new_file, new_file, child["id"]))
    except sqlite3.IntegrityError:
        if moved:
            try:
                _safe_os_rename(new_path, old_path)
            except OSError:
                app.logger.exception("Failed to rollback folder rename id=%s", folder_id)
        return _rename_error("A folder with that name already exists here.", 409)
    except OSError as e:
        if moved:
            try:
                _safe_os_rename(new_path, old_path)
            except OSError:
                app.logger.exception("Failed to rollback folder rename id=%s", folder_id)
        app.logger.exception("Folder rename storage error id=%s", folder_id)
        return _rename_error(f"Folder could not be renamed: {e}", 500)
    except Exception:
        if moved:
            try:
                _safe_os_rename(new_path, old_path)
            except OSError:
                app.logger.exception("Failed to rollback folder rename id=%s", folder_id)
        app.logger.exception("Unexpected folder rename failure id=%s", folder_id)
        return _rename_error("Folder rename failed.", 500)

    app.logger.info("File Center folder renamed: id=%s %r -> %r", folder_id, old_name, name)
    _invalidate_qnap_listing_cache()
    return jsonify(ok=True, id=folder_id, name=name, unchanged=False)


@app.route("/api/files/rename/<int:file_id>", methods=["PUT", "POST", "PATCH"], strict_slashes=False)
def api_file_rename(file_id):
    _sync_qnap_tree(force=True)
    if g.user["role"] != "admin":
        return jsonify(ok=False, error="Admin access required."), 403
    storage_ok, storage_status = require_qnap_storage()
    if not storage_ok:
        return jsonify(ok=False, error=f"QNAP storage unavailable: {storage_status['message']}"), 503

    with db() as c:
        row = c.execute("SELECT * FROM file_items WHERE id=?", (file_id,)).fetchone()
    if not row:
        return jsonify(ok=False, error="File not found."), 404

    payload = _json_rename_payload()
    try:
        name = _validate_file_rename(payload.get("name"))
    except ValueError as e:
        return _rename_error(str(e), 400)

    old_name = row["original_name"]
    if name == old_name:
        return jsonify(ok=True, id=file_id, name=name, unchanged=True)

    with db() as c:
        conflict = c.execute(
            "SELECT id FROM file_items WHERE folder_id IS ? AND original_name COLLATE NOCASE = ? AND id<>?",
            (row["folder_id"], name, file_id),
        ).fetchone()
    if conflict:
        return _rename_error("A file with that name already exists here.", 409)

    old_path = os.path.abspath(file_storage_path(row))
    if not os.path.isfile(old_path):
        return _rename_error("The physical file is missing from storage.", 404)

    new_path = os.path.abspath(os.path.join(os.path.dirname(old_path), name))
    if os.path.exists(new_path) and os.path.normcase(new_path) != os.path.normcase(old_path):
        return _rename_error("A storage file with that name already exists.", 409)

    moved = False
    new_disk_name = os.path.basename(new_path)
    mime_type = mimetypes.guess_type(name)[0] or row["mime_type"] or "application/octet-stream"

    try:
        _safe_os_rename(old_path, new_path)
        moved = True
        with db() as c:
            cur = c.execute(
                "UPDATE file_items SET disk_name=?, original_name=?, mime_type=?, stored_name=?, qnap_path=? WHERE id=?",
                (new_disk_name, name, mime_type, _qnap_rel_join([*(_normalize_qnap_relative_path(_folder_qnap_relative(row["folder_id"])) if row["folder_id"] else []), name]), _qnap_rel_join([*(_normalize_qnap_relative_path(_folder_qnap_relative(row["folder_id"])) if row["folder_id"] else []), name]), file_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("File database record was not updated.")
    except sqlite3.IntegrityError:
        if moved:
            try:
                _safe_os_rename(new_path, old_path)
            except OSError:
                app.logger.exception("Failed to rollback file rename id=%s", file_id)
        return _rename_error("The file name could not be changed because of a database conflict.", 409)
    except OSError as e:
        if moved:
            try:
                _safe_os_rename(new_path, old_path)
            except OSError:
                app.logger.exception("Failed to rollback file rename id=%s", file_id)
        app.logger.exception("File rename storage error id=%s", file_id)
        return _rename_error(f"File could not be renamed: {e}", 500)
    except Exception:
        if moved:
            try:
                _safe_os_rename(new_path, old_path)
            except OSError:
                app.logger.exception("Failed to rollback file rename id=%s", file_id)
        app.logger.exception("Unexpected file rename failure id=%s", file_id)
        return _rename_error("File rename failed.", 500)

    app.logger.info("File Center file renamed: id=%s %r -> %r", file_id, old_name, name)
    _invalidate_qnap_listing_cache()
    _invalidate_query_cache()
    return jsonify(ok=True, id=file_id, name=name, mime_type=mime_type,
                   disk_name=new_disk_name, unchanged=False)


# Report capacity exposed by the configured QNAP SMB share, not the total NAS pool.
# Cache remote disk queries to prevent repeated SMB traffic from dashboard refreshes.
_QNAP_CAPACITY_CACHE = {"expires": 0.0, "result": None}
_QNAP_CAPACITY_LOCK = threading.Lock()


@app.get("/api/files/capacity")
def api_file_capacity():
    now = time.monotonic()
    cached = _QNAP_CAPACITY_CACHE["result"]
    refresh = request.args.get("refresh") == "1"
    if cached is not None and not refresh and now < _QNAP_CAPACITY_CACHE["expires"]:
        return jsonify(cached)

    # A slow SMB disk_usage() must not queue every new capacity request on
    # this mutex (and unnecessarily consume more Waitress worker threads).
    if not _QNAP_CAPACITY_LOCK.acquire(blocking=False):
        if cached is not None and cached.get("ok"):
            return jsonify({**cached, "stale": True})
        response = jsonify(ok=False, error="QNAP capacity check in progress.", scope="smb_share")
        response.status_code = 503
        response.headers["Retry-After"] = "3"
        return response

    started = time.perf_counter()
    try:
        # Re-check cache after acquiring the lock, in case another request
        # finished just before this caller acquired it.
        cached = _QNAP_CAPACITY_CACHE["result"]
        if cached is not None and not refresh and time.monotonic() < _QNAP_CAPACITY_CACHE["expires"]:
            return jsonify(cached)
        try:
            status = qnap_storage_status()
            if not status["available"]:
                raise OSError("QNAP share is unavailable")
            capacity_path = QNAP_SHARE.rstrip("\\/") + "\\" if os.name == "nt" else QNAP_SHARE
            capacity = shutil.disk_usage(capacity_path)
            total = int(capacity.total)
            free = int(capacity.free)
            if total <= 0 or free < 0 or free > total:
                raise ValueError("Invalid storage capacity returned by SMB server")
            used = total - free
            result = {"ok": True, "total_bytes": total, "used_bytes": used,
                      "free_bytes": free, "used_percent": round(100 * used / total, 1),
                      "scope": "smb_share"}
            ttl = 90
        except (OSError, ValueError) as exc:
            app.logger.warning("QNAP share capacity query failed: %s", type(exc).__name__)
            result = {"ok": False, "error": "Storage capacity unavailable from QNAP SMB share.",
                      "scope": "smb_share"}
            ttl = 20
        _QNAP_CAPACITY_CACHE.update(expires=time.monotonic() + ttl, result=result)
        return jsonify(result)
    finally:
        duration_ms = (time.perf_counter()-started)*1000
        if duration_ms >= 750:
            app.logger.warning("QNAP capacity query slow duration_ms=%.1f", duration_ms)
        _QNAP_CAPACITY_LOCK.release()


@app.get("/api/files/storage-status")
def api_file_storage_status():
    status = qnap_storage_status()
    return jsonify(ok=bool(status["available"]), **status), (200 if status["available"] else 503)


@app.route("/chat", methods=["POST"])
def chat_post():
    message = request.form.get("message", "").strip()[:500]
    if message:
        with db() as c:
            c.execute(
                "INSERT INTO chat_messages(username, display, message, created_at) VALUES(?,?,?,?)",
                (g.user["username"], g.user["display"], message, local_now().strftime("%Y-%m-%d %H:%M")),
            )
    return redirect((request.referrer or "/") + "#team-chat")


@app.route("/chat/messages")
def chat_messages_api():
    return jsonify(latest_chat_messages(30))


@app.route("/notifications/read", methods=["POST"])
def notifications_read():
    with db() as c:
        ids = c.execute("SELECT id FROM notifications WHERE username=?", (g.user["username"],)).fetchall()
        now_text = local_now().strftime("%Y-%m-%d %H:%M")
        c.executemany(
            "INSERT OR IGNORE INTO notification_reads(username,notification_id,read_at) VALUES(?,?,?)",
            [(g.user["username"], r["id"], now_text) for r in ids],
        )
    return redirect(request.referrer or "/")


@app.route("/export")
def export():
    q = request.args.get("q", "").strip()
    support_status = (request.args.get("support_status") or "all").strip()
    support_owner = (request.args.get("support_owner") or "all").strip()
    if support_status not in SUPPORT_STATUS_FILTERS:
        support_status = "all"
    if support_owner != "all" and support_owner not in SUPPORT_USERS:
        support_owner = "all"
    start_date, end_date, ticket_all = get_ticket_range()
    rows = query(start_date, end_date, q)
    # The Support Team toolbar exports exactly the Support columns shown
    # on the board; unassigned tickets remain available from Open Ticket Queue.
    rows = [r for r in rows if (r["support_id"] or UNASSIGNED).strip() in SUPPORT_USERS]
    if support_owner != "all":
        rows = [r for r in rows if (r["support_id"] or UNASSIGNED).strip() == support_owner]
    allowed_statuses = SUPPORT_STATUS_FILTERS[support_status]
    if allowed_statuses is not None:
        rows = [r for r in rows if r["status"] in allowed_statuses]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["No Tiket", "Created Tanggal", "Created Jam", "Case Tanggal", "Case Jam", "Pelapor", "WhatsApp", "Category", "Priority", "Masalah", "Tindakan",
                "Support", "Status", "Foto Bukti"])
    for r in rows:
        w.writerow([csv_safe(x) for x in (
            r["no_tiket"], r["tanggal"], r["jam"], r["case_tanggal"], r["case_jam"], r["pelapor"], r["whatsapp"], r["category"], r["priority"], r["masalah"],
            r["tindakan"], r["support_id"], r["status"],
            "Ada" if r["foto"] else ("No photo (admin approval)" if r["tanpa_foto"] else "No photo"))])
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=tickets.csv"},
    )


def _log_registered_routes():
    lines = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda item: item.rule):
        methods = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        lines.append(f"{','.join(methods) or '-':<18} {rule.rule:<42} -> {rule.endpoint}")
    app.logger.info("Registered Flask routes:\n%s", "\n".join(lines))
    if not any(rule.rule == "/files/folder" and "POST" in rule.methods for rule in app.url_map.iter_rules()):
        app.logger.error("CRITICAL: POST /files/folder is NOT registered in app.url_map")


# Revisi 21: durable File Center chunk upload, keeping /files/upload-fast intact.
def _resumable_mark_sync_dirty():
    global QNAP_SYNC_DIRTY
    QNAP_SYNC_DIRTY = True
    _invalidate_qnap_listing_cache()

from file_center_resumable import register_resumable_upload
register_resumable_upload(
    app, db=db, temp_upload_dir=TEMP_UPLOAD_DIR,
    qnap_lock=QNAP_UPLOAD_FINALIZE_LOCK,
    qnap_path_from_relative=qnap_path_from_relative,
    safe_name=_safe_upload_name, safe_relative_path=_safe_upload_relative_path,
    qnap_rel_join=_qnap_rel_join, ensure_folder_metadata=_ensure_folder_metadata,
    require_qnap_storage=require_qnap_storage,
    invalidate_listing=_invalidate_qnap_listing_cache,
    mark_dirty=_resumable_mark_sync_dirty,
    local_now=local_now, format_file_size=format_file_size,
    file_max_size=FILE_MAX_SIZE,
)

from our_projects import register_our_projects
register_our_projects(app, db, STATIC_PAGE_DIR, upload_storage=DASHBOARD_UPLOAD_STORAGE)

from guides import register_guides
app.config['TEMP_UPLOAD_DIR'] = TEMP_UPLOAD_DIR
register_guides(app, db, STATIC_PAGE_DIR, upload_storage=DASHBOARD_UPLOAD_STORAGE)

# Revisi 61: register cPanel Email Accounts routes (backend).
from cpanel_mail import register_email_tools
register_email_tools(app, DB, STATIC_PAGE_DIR)

_log_registered_routes()

try:
    _start_qnap_watcher()
except Exception:
    app.logger.warning("Unable to initialize QNAP watcher", exc_info=True)

try:
    from open_portal import register_open_portal
    register_open_portal(app)
    app.logger.info('Open Portal registration completed')
except Exception:
    app.logger.exception('FATAL: Open Portal registration failed')
    raise

app.logger.info('Application bootstrap completed successfully; routes=%d', len(list(app.url_map.iter_rules())))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10, threaded=True)
