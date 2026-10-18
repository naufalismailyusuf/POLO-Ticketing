"""R62 cPanel Email Accounts diagnostic. Read-only; never displays passwords/tokens."""
import getpass
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

def check():
    print("=== POLO Email Tools diagnostics (read-only) ===")
    env_file = BASE / ".env"
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("python-dotenv: MISSING (app.py cannot load .env automatically)")
    else:
        print("python-dotenv: available")
        if env_file.exists():
            load_dotenv(env_file, override=False)
    print(".env:", "present" if env_file.is_file() else "MISSING")
    print("Windows identity:", getpass.getuser())
    for rel in ("cpanel_mail.py", "cpanel_credentials.py", "static/pages/email_tools.html", "static/css/email-tools-v57.css", "static/js/email-tools-v57.js"):
        print(rel + ":", "OK" if (BASE / rel).is_file() else "MISSING")
    cfg = os.environ
    method = cfg.get("CPANEL_AUTH_METHOD", "token").strip().lower()
    user = cfg.get("CPANEL_ACCOUNT_USERNAME", "").strip()
    url = cfg.get("CPANEL_API_BASE", "https://srv180.niagahoster.com:2083").strip()
    print("CPANEL_AUTH_METHOD:", method, "(expected: basic)")
    print("CPANEL_ACCOUNT_USERNAME:", "present" if user else "MISSING")
    try:
        u = urlsplit(url)
        correct = u.scheme == "https" and u.hostname and u.port == 2083
    except ValueError:
        correct = False
    print("CPANEL_API_BASE HTTPS:2083:", "OK" if correct else "INVALID")
    if method == "basic":
        try:
            from cpanel_credentials import credential_file_path, load_encrypted_password
            path = Path(credential_file_path())
            print("DPAPI file:", "present" if path.is_file() else "MISSING", "at", path)
            if path.is_file():
                try:
                    value = load_encrypted_password(str(path))
                    del value
                    print("DPAPI decrypt (CURRENT account): OK")
                except Exception:
                    print("DPAPI decrypt (CURRENT account): FAILED. Verify Windows service identity.")
        except Exception:
            print("DPAPI module: ERROR (check cpanel_credentials.py)")
    elif method == "token":
        print("CPANEL_API_TOKEN:", "present" if cfg.get('CPANEL_API_TOKEN') else "MISSING")
    print("No network requests made, and no credentials printed.")
    print("Reminder: the Windows service identity may differ from this console identity.")

if __name__ == "__main__":
    check()
