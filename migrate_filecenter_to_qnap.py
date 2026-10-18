import argparse
import ctypes
from ctypes import wintypes
import os
import shutil
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "tickets.db"
SOURCE_ROOT = Path(os.environ.get("FILE_CENTER_SOURCE_ROOT", r"C:\IT_FILES"))
SOURCE_LEGACY = Path(os.environ.get("FILE_CENTER_SOURCE_LEGACY", str(BASE / "file_store")))
TARGET_ROOT = Path(os.environ.get("FILE_CENTER_QNAP_SHARE", r"\\172.16.0.73\IT"))
QNAP_USERNAME = os.environ.get("FILE_CENTER_QNAP_USERNAME", "").strip()
QNAP_PASSWORD = os.environ.get("FILE_CENTER_QNAP_PASSWORD", "")
QNAP_DOMAIN = os.environ.get("FILE_CENTER_QNAP_DOMAIN", "").strip()
CHUNK = 16 * 1024 * 1024


def ensure_qnap_access():
    if TARGET_ROOT.exists():
        return
    if os.name != "nt" or not QNAP_USERNAME:
        raise SystemExit(f"QNAP share not reachable: {TARGET_ROOT}")

    class NETRESOURCEW(ctypes.Structure):
        _fields_ = [("dwScope", wintypes.DWORD), ("dwType", wintypes.DWORD),
                    ("dwDisplayType", wintypes.DWORD), ("dwUsage", wintypes.DWORD),
                    ("lpLocalName", wintypes.LPWSTR), ("lpRemoteName", wintypes.LPWSTR),
                    ("lpComment", wintypes.LPWSTR), ("lpProvider", wintypes.LPWSTR)]

    resource=NETRESOURCEW(0,1,0,0,None,str(TARGET_ROOT),None,None)
    username=f"{QNAP_DOMAIN}\\{QNAP_USERNAME}" if QNAP_DOMAIN else QNAP_USERNAME
    code=ctypes.windll.mpr.WNetAddConnection2W(ctypes.byref(resource),QNAP_PASSWORD or None,username,0)
    if code not in (0,1219) or not TARGET_ROOT.exists():
        raise SystemExit(f"QNAP SMB connection failed: Windows error {code}; share={TARGET_ROOT}")


def open_db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def folder_path_builder(conn):
    rows = conn.execute("SELECT id,name,parent_id FROM file_folders ORDER BY id").fetchall()
    by_id = {int(row["id"]): row for row in rows}
    cache = {}
    def rel(folder_id):
        if folder_id is None:
            return Path()
        folder_id = int(folder_id)
        if folder_id in cache:
            return cache[folder_id]
        row = by_id.get(folder_id)
        if not row:
            raise RuntimeError(f"Folder {folder_id} does not exist in file_folders")
        value = rel(row["parent_id"]) / row["name"]
        cache[folder_id] = value
        return value
    return rel


def copy_large(src, dst):
    with src.open("rb", buffering=1024 * 1024) as rf, dst.open("wb", buffering=1024 * 1024) as wf:
        while True:
            block = rf.read(CHUNK)
            if not block:
                break
            wf.write(block)
    try:
        shutil.copystat(src, dst, follow_symlinks=True)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not DB.exists():
        raise SystemExit(f"Database not found: {DB}")
    ensure_qnap_access()
    copied=skipped=missing=failed=0

    with open_db() as conn:
        rel=folder_path_builder(conn)
        folders=conn.execute("SELECT id,name,parent_id FROM file_folders ORDER BY id").fetchall()
        files=conn.execute("SELECT * FROM file_items ORDER BY id").fetchall()

        for row in folders:
            target=TARGET_ROOT/rel(row["id"])
            if args.dry_run:
                print(f"[FOLDER] {target}")
            else:
                target.mkdir(parents=True,exist_ok=True)

        for row in files:
            disk_name=row["disk_name"] or row["stored_name"]
            relative=rel(row["folder_id"])/disk_name
            target=TARGET_ROOT/relative
            source=SOURCE_ROOT/relative
            if not source.is_file():
                legacy=SOURCE_LEGACY/(row["stored_name"] or disk_name)
                if legacy.is_file(): source=legacy
            if not source.is_file():
                missing+=1; print(f"[MISSING] id={row['id']} source={source}"); continue
            if target.is_file() and target.stat().st_size==source.stat().st_size:
                skipped+=1; print(f"[SKIP] {target}"); continue
            print(f"[COPY] {source} -> {target}")
            if args.dry_run: continue
            temp=target.with_name(target.name+'.migration.part')
            try:
                target.parent.mkdir(parents=True,exist_ok=True)
                copy_large(source,temp)
                os.replace(temp,target)
                copied+=1
            except Exception as exc:
                failed+=1
                try: temp.unlink(missing_ok=True)
                except Exception: pass
                print(f"[ERROR] id={row['id']} {type(exc).__name__}: {exc}")

    print(f"Done: copied={copied}, skipped={skipped}, missing={missing}, failed={failed}, dry_run={args.dry_run}")
    raise SystemExit(2 if (missing or failed) else 0)


if __name__ == '__main__':
    main()
