"""POLO R65 backend-only performance patch, reversible; stdlib only.

Dry-run: python scripts/apply_backend_perf_r65.py
Apply:   python scripts/apply_backend_perf_r65.py --apply

No network access, no database changes, no service restart.  Assumes this file
is in C:\\Ticketing\\scripts next to the live C:\\Ticketing\\app.py.
"""
import argparse
import ast
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import datetime

MARKER = "# POLO-R65-BACKEND-PERFORMANCE"

# Each replacement is bounded by a unique nearby method or statement to avoid
# changing unrelated authentication/API functionality.
CHANGES = [
    (
        "static auth bypass (only Flask static endpoint)",
        '    g.user = None\n    if session.get("user") == "natan":',
        '    g.user = None\n'
        f'    {MARKER}: files in Flask static/ are public, skip user DB lookup\n'
        '    if request.endpoint == "static":\n'
        '        return\n'
        '    if session.get("user") == "natan":',
    ),
    (
        "bounded static caching",
        '    if request.path.startswith("/static/") and request.path.lower().endswith((".css", ".js")):\n'
        '        resp.headers["Cache-Control"] = "public, max-age=0, must-revalidate"\n'
        '    else:\n'
        '        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"',
        f'    {MARKER}: reduce repeat 304 requests on same-origin static assets\n'
        '    if request.endpoint == "static" and request.path.lower().endswith((".woff2", ".woff", ".ttf", ".otf")):\n'
        '        resp.headers["Cache-Control"] = "public, max-age=86400"\n'
        '    elif request.endpoint == "static" and request.path.lower().endswith((".css", ".js")):\n'
        '        resp.headers["Cache-Control"] = "public, max-age=180, must-revalidate"\n'
        '    else:\n'
        '        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"',
    ),
    (
        "dashboard query reuse for default filters",
        '    chart_start_date,chart_end_date=get_chart_range(); bars,chart_total=build_chart_data(decorate_rows(query(None,None,q)),chart_start_date,chart_end_date)',
        f'    {MARKER}: reuse all_rows when no search query is applied\n'
        '    chart_start_date,chart_end_date=get_chart_range()\n'
        '    chart_rows=(decorate_rows(query(None,None,q)) if q else all_rows)\n'
        '    bars,chart_total=build_chart_data(chart_rows,chart_start_date,chart_end_date)',
    ),
    (
        "dashboard today count from already loaded rows",
        '    today_date=local_now().date(); today_total=len(query(today_date,today_date,"")); resolved_today=sum(1 for r in all_rows if r["status"] in COMPLETED_STATUSES and r["completed_at"] and r["completed_at"][:10]==today_date.strftime("%Y-%m-%d"))',
        f'    {MARKER}: identical count using the same all_rows snapshot\n'
        '    today_text=local_now().strftime("%Y-%m-%d")\n'
        '    today_total=sum(1 for r in all_rows if r["tanggal"]==today_text)\n'
        '    resolved_today=sum(1 for r in all_rows if r["status"] in COMPLETED_STATUSES and r["completed_at"] and r["completed_at"][:10]==today_text)',
    ),
]


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def patch(source):
    if MARKER in source:
        raise RuntimeError("R65 marker already found: file appears patched. No changes made.")
    result = source
    for label, old, new in CHANGES:
        count = result.count(old)
        if count != 1:
            raise RuntimeError(f"Pattern mismatch [{label}], matches={count}; existing app.py was NOT changed."
                               " Send this message for targeted revision rather than overwriting app.py.")
        result = result.replace(old, new, 1)
    ast.parse(result, filename="app.py")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Verified backup and atomic app.py patch")
    parser.add_argument("--path", type=Path, help=argparse.SUPPRESS)  # for local test only
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    src = args.path if args.path else root / "app.py"
    if not src.is_file():
        parser.error(f"app.py not found: {src}")
    original = src.read_bytes()
    try:
        text = original.decode("utf-8-sig")
        changed = patch(text)
    except (UnicodeError, SyntaxError, RuntimeError) as exc:
        print(f"STOP: {exc}")
        return 1

    print("READY: 4 backend performance adjustments validated (syntax OK).")
    print("- Flask public static resources skip per-file user SQLite lookup.")
    print("- Static CSS/JS cached 180 seconds; fonts 24 hours.")
    print("- Dashboard avoids two redundant queries for standard view.")
    print("- API and HTML remain no-store; authentication/CSRF unchanged.")
    if not args.apply:
        print("Read-only check complete. Run with --apply to make changes.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = src.with_name(f"app.py.pre-r65-{stamp}.bak")
    if bak.exists():
        print(f"STOP: backup already exists: {bak}")
        return 1
    shutil.copy2(src, bak)
    if sha256(bak) != sha256(src):
        print("STOP: backup checksum mismatch. Original app.py unchanged.")
        return 1
    fd, tempname = tempfile.mkstemp(prefix=".app-r65-", suffix=".py", dir=src.parent)
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(changed.encode("utf-8"))
            fp.flush()
            os.fsync(fp.fileno())
        # Do not overwrite if another process or user edited app.py during this operation.
        if sha256(src) != sha256(bak):
            print("STOP: app.py changed since backup; refusing to overwrite.")
            return 1
        os.replace(tempname, src)
        tempname = None
    finally:
        if tempname and os.path.exists(tempname):
            os.unlink(tempname)
    print(f"APPLIED. Verified backup: {bak}")
    print("Run: python -m py_compile app.py")
    print("Then restart the Ticketing service during a short maintenance window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
