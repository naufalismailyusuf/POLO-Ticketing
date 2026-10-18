"""File Center resumable uploads (Revisi 21).

A bounded 16 MiB HTTP chunk is durably appended to a local spool. The completed
spool is copied to a hidden .part on QNAP, verified, atomically published, and
indexed. Upload state is owner-bound and durable across browser/server restarts.
Existing /files/upload-fast remains unchanged as a legacy fallback.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from flask import g, jsonify, request

CHUNK_BYTES = 16 * 1024 * 1024
MAX_ACTIVE_BYTES = 8 * 1024 * 1024 * 1024
KEEP_SECONDS = 7 * 24 * 3600
FAILED_KEEP_SECONDS = 30 * 24 * 3600
FREE_RESERVE = 512 * 1024 * 1024
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
FINALIZER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="file-center-finalize")
STATE_LOCK = threading.RLock()  # Single Waitress process; protects spool and metadata transitions.
PROGRESS_LOCK = threading.Lock()  # Independent: status reads must not wait on SMB writes.
LIVE_PROGRESS = {}  # In-process, transient copy-byte counters; safely reset on worker restart.
IN_FLIGHT = set()


def register_resumable_upload(app, *, db, temp_upload_dir, qnap_lock, qnap_path_from_relative,
                              safe_name, safe_relative_path, qnap_rel_join,
                              ensure_folder_metadata, require_qnap_storage,
                              invalidate_listing, mark_dirty, local_now, format_file_size,
                              file_max_size):
    root = Path(temp_upload_dir) / "resumable-v21"
    root.mkdir(parents=True, exist_ok=True)
    last_cleanup = [0.0]

    def response_error(message, code, status=400):
        return jsonify(ok=False, error=message, code=code), status

    def paths(token):
        if not TOKEN_RE.fullmatch(token or ""):
            raise ValueError("Invalid upload session.")
        folder = root / token
        return folder, folder / "meta.json", folder / "data.part"

    def read_meta(token):
        folder, state_path, spool = paths(token)
        try:
            with state_path.open("r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (FileNotFoundError, OSError, ValueError):
            return None
        if meta.get("id") != token:
            return None
        return meta

    def persist(meta):
        folder, state_path, _ = paths(meta["id"])
        folder.mkdir(parents=True, exist_ok=True)
        candidate = state_path.with_suffix(".json.tmp")
        meta["updated_at"] = time.time()
        with candidate.open("w", encoding="utf-8") as out:
            json.dump(meta, out, ensure_ascii=False, separators=(",", ":"))
            out.flush()
            os.fsync(out.fileno())
        os.replace(candidate, state_path)

    def owner_meta(token):
        try:
            meta = read_meta(token)
        except ValueError:
            return None
        if not meta or meta.get("owner") != g.user["username"]:
            return None
        return meta

    def reconcile(meta):
        """If a worker crashed after append but before commit, discard the uncommitted tail."""
        _, _, spool = paths(meta["id"])
        committed = int(meta.get("offset", 0))
        if meta["state"] == "uploading":
            if not spool.exists():
                if committed:
                    raise IOError("Upload temporary data is missing. Start a new upload.")
                return
            size = spool.stat().st_size
            if size < committed:
                raise IOError("Upload temporary data is incomplete. Start a new upload.")
            if size > committed:
                with spool.open("r+b") as fh:
                    fh.truncate(committed)
                    fh.flush()
                    os.fsync(fh.fileno())

    def set_qnap_stage(token, phase, bytes_written=0):
        # Do not fsync the upload's metadata for every SMB block. Status reads
        # use this independent lock and remain responsive during a slow QNAP copy.
        with PROGRESS_LOCK:
            LIVE_PROGRESS[token] = {"phase": phase, "bytes": max(0, int(bytes_written))}

    def visible(meta):
        state = meta["state"]
        with PROGRESS_LOCK:
            live = LIVE_PROGRESS.get(meta["id"])
            live = dict(live) if live else None
        if state == "completed":
            phase, copied = "completed", int(meta["size"])
        elif state == "failed":
            phase, copied = "failed", (live or {}).get("bytes", 0)
        elif state == "uploading":
            phase, copied = "uploading", 0
        elif live:
            phase, copied = live["phase"], live["bytes"]
        else:
            # After service restart the job is rescheduled. Never expose stale
            # progress from the interrupted process as a completed SMB write.
            phase, copied = "queued", 0
        return {
            "ok": True, "upload_id": meta["id"], "state": state,
            "offset": int(meta["offset"]), "size": int(meta["size"]),
            "chunk_size": CHUNK_BYTES, "name": meta["name"],
            "error": meta.get("error"), "file": meta.get("file"),
            "qnap_phase": phase, "qnap_bytes": min(copied, int(meta["size"])),
            "qnap_total": int(meta["size"]),
        }

    def cleanup_old():
        cutoff = time.time() - KEEP_SECONDS
        for child in root.iterdir():
            if not child.is_dir() or not TOKEN_RE.fullmatch(child.name):
                continue
            try:
                state = read_meta(child.name)
                if state and (state.get("updated_at", 0) >= cutoff or
                              state.get("state") in ("queued", "finalizing") or
                              (state.get("reserved_name") and state.get("updated_at", 0) >= time.time() - FAILED_KEEP_SECONDS)):
                    # An uncertain publish must not be discarded automatically:
                    # the spool and reservation are needed for safe recovery.
                    continue
                if child.name in IN_FLIGHT:
                    continue
                # Only delete in this tool's private temporary session directory.
                shutil.rmtree(child)
            except OSError:
                app.logger.warning("Resumable cleanup failed: %s", child.name, exc_info=True)

    def json_input():
        payload = request.get_json(silent=True)
        return payload if isinstance(payload, dict) else {}

    @app.post("/api/files/resumable/init")
    def file_resumable_init():
        payload = json_input()
        try:
            name = safe_name(str(payload.get("name", "")))
            size = int(payload.get("size", -1))
            if size < 0 or size > file_max_size:
                return response_error("Maximum File Center file size is 8 GB.", "file_too_large", 413)
            base_path = str(payload.get("path") or "")
            _, base_parts = qnap_path_from_relative(base_path)
            relative_parts = safe_relative_path(payload.get("relative_path") or "")
            if relative_parts:
                name = relative_parts[-1]
            target_parts = [*base_parts, *relative_parts[:-1]]
            # This is a validation only. No QNAP directory is created until finalize.
            qnap_path_from_relative(qnap_rel_join(target_parts))
            fingerprint = str(payload.get("fingerprint") or "")
            if not re.fullmatch(r"[a-f0-9]{16,64}", fingerprint):
                return response_error("Invalid file fingerprint.", "bad_fingerprint")
        except (ValueError, TypeError) as exc:
            return response_error(f"Invalid upload information: {exc}", "invalid_upload")

        old_id = str(payload.get("upload_id") or "")
        with STATE_LOCK:
            if old_id:
                meta = owner_meta(old_id)
                if meta and all((meta.get(k) == v) for k, v in {
                    "name": name, "size": size, "base_path": qnap_rel_join(base_parts),
                    "relative_path": qnap_rel_join(relative_parts), "fingerprint": fingerprint,
                }.items()):
                    try:
                        reconcile(meta)
                    except OSError as exc:
                        return response_error(str(exc), "temp_missing", 409)
                    if meta["state"] in ("queued", "finalizing"):
                        schedule(meta["id"])
                    return jsonify(**visible(meta))
                return response_error("Previous upload session expired or does not match this file. Check QNAP before restarting.", "resume_not_found", 409)
            if time.time() - last_cleanup[0] > 3600:
                cleanup_old()
                last_cleanup[0] = time.time()
            active_sessions = 0
            for item in root.iterdir():
                if item.is_dir() and TOKEN_RE.fullmatch(item.name):
                    previous = read_meta(item.name)
                    if previous and previous.get("owner") == g.user["username"] and previous.get("state") in ("uploading", "queued", "finalizing"):
                        active_sessions += 1
            if active_sessions >= 8:
                return response_error("Too many unfinished uploads. Complete or resume an existing upload before starting another.", "too_many_uploads", 429)
            free = shutil.disk_usage(root).free
            if size + FREE_RESERVE > free:
                return response_error(
                    "Not enough temporary space on the Windows server for this upload. "
                    f"Free: {format_file_size(free)}; required: {format_file_size(size + FREE_RESERVE)}.",
                    "temp_disk_full", 507)
            token = secrets.token_hex(16)
            meta = dict(id=token, owner=g.user["username"], display=g.user["display"],
                        name=name, size=size, offset=0,
                        base_path=qnap_rel_join(base_parts),
                        relative_path=qnap_rel_join(relative_parts), fingerprint=fingerprint,
                        state="uploading", error=None, file=None, created_at=time.time())
            folder, _, spool = paths(token)
            folder.mkdir(mode=0o700)
            spool.touch()
            persist(meta)
            return jsonify(**visible(meta)), 201

    @app.get("/api/files/resumable/<token>/status")
    def file_resumable_status(token):
        with STATE_LOCK:
            meta = owner_meta(token)
            if not meta:
                return response_error("Upload session not found.", "not_found", 404)
            try:
                reconcile(meta)
            except OSError as exc:
                return response_error(str(exc), "temp_missing", 409)
            if meta["state"] in ("queued", "finalizing"):
                schedule(token)
            return jsonify(**visible(meta))

    @app.put("/api/files/resumable/<token>/chunk")
    def file_resumable_chunk(token):
        with STATE_LOCK:
            meta = owner_meta(token)
            if not meta:
                return response_error("Upload session not found.", "not_found", 404)
            if meta["state"] != "uploading":
                return response_error("Upload is no longer accepting chunks.", "invalid_state", 409)
            try:
                offset = int(request.headers.get("X-Chunk-Offset", "-1"))
            except ValueError:
                return response_error("Invalid chunk offset.", "bad_offset")
            size = int(meta["size"])
            expected_length = min(CHUNK_BYTES, size - offset) if 0 <= offset < size else 0
            if not expected_length or offset % CHUNK_BYTES:
                return response_error("Invalid chunk offset or empty file.", "bad_offset")
            if request.content_length != expected_length:
                return response_error("Unexpected chunk body length.", "bad_chunk_length", 400)
            # Flask/Waitress request bodies are bounded here to 16 MiB.
            try:
                data = request.get_data(cache=False)
            except OSError:
                return response_error("Upload chunk was interrupted.", "interrupted", 400)
            if len(data) != expected_length:
                return response_error("Upload chunk was incomplete.", "short_chunk", 400)
            checksum = request.headers.get("X-Chunk-SHA256", "").lower()
            if checksum and (not re.fullmatch(r"[a-f0-9]{64}", checksum) or
                             hashlib.sha256(data).hexdigest() != checksum):
                return response_error("Upload chunk checksum mismatch.", "checksum_mismatch", 422)
            try:
                reconcile(meta)
                _, _, spool = paths(token)
                current = int(meta["offset"])
                if offset < current:
                    # A retried chunk must match the bytes already committed.
                    if offset + expected_length > current:
                        return response_error("Chunk overlaps uncommitted data.", "offset_conflict", 409)
                    with spool.open("rb") as src:
                        src.seek(offset)
                        if src.read(expected_length) != data:
                            return response_error("Repeated chunk differs from stored data.", "duplicate_mismatch", 409)
                    return jsonify(**visible(meta))
                if offset != current:
                    return response_error(f"Resume from byte {current}.", "offset_conflict", 409)
                with spool.open("ab") as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                meta["offset"] = current + expected_length
                persist(meta)
            except OSError as exc:
                app.logger.exception("Resumable chunk I/O error token=%s", token)
                return response_error(f"Temporary file write failed: {exc}", "temp_write_failed", 507)
            return jsonify(**visible(meta))

    @app.post("/api/files/resumable/<token>/complete")
    def file_resumable_complete(token):
        with STATE_LOCK:
            meta = owner_meta(token)
            if not meta:
                return response_error("Upload session not found.", "not_found", 404)
            if meta["state"] == "uploading":
                try:
                    reconcile(meta)
                except OSError as exc:
                    return response_error(str(exc), "temp_missing", 409)
                if int(meta["offset"]) != int(meta["size"]):
                    return response_error("Upload is incomplete. Resume remaining chunks.", "not_complete", 409)
                meta["state"] = "queued"
                persist(meta)
                set_qnap_stage(token, "queued")
            if meta["state"] in ("queued", "finalizing"):
                schedule(token)
            return jsonify(**visible(meta)), 202 if meta["state"] in ("queued", "finalizing") else 200

    @app.post("/api/files/resumable/<token>/retry")
    def file_resumable_retry(token):
        with STATE_LOCK:
            meta = owner_meta(token)
            if not meta:
                return response_error("Upload session not found.", "not_found", 404)
            if meta["state"] == "failed":
                _, _, spool = paths(token)
                if not spool.is_file() or spool.stat().st_size != meta["size"]:
                    return response_error("Upload temporary file is no longer available.", "temp_missing", 409)
                meta["state"] = "queued"
                meta["error"] = None
                persist(meta)
                set_qnap_stage(token, "queued")
            if meta["state"] in ("queued", "finalizing"):
                schedule(token)
            return jsonify(**visible(meta))

    def schedule(token):
        if token not in IN_FLIGHT:
            IN_FLIGHT.add(token)
            FINALIZER.submit(run_finalize, token)

    def run_finalize(token):
        try:
            finalize(token)
        except Exception:
            app.logger.exception("Unexpected resumable finalize failure token=%s", token)
            with STATE_LOCK:
                meta = read_meta(token)
                if meta and meta.get("state") != "completed":
                    meta["state"] = "failed"
                    meta["error"] = "Unexpected server error while saving to QNAP. Check File Center before retrying."
                    persist(meta)
                    set_qnap_stage(token, "failed")
        finally:
            with STATE_LOCK:
                IN_FLIGHT.discard(token)
            # Persisted meta carries terminal state. Do not retain a live
            # progress entry per historical upload for the life of Waitress.
            with PROGRESS_LOCK:
                LIVE_PROGRESS.pop(token, None)

    def sha256_file(file_path):
        digest = hashlib.sha256()
        with open(file_path, "rb", buffering=4 * 1024 * 1024) as src:
            for block in iter(lambda: src.read(4 * 1024 * 1024), b""):
                digest.update(block)
        return digest.digest()

    def finalize(token):
        with STATE_LOCK:
            meta = read_meta(token)
            if not meta or meta["state"] not in ("queued", "finalizing"):
                return
            meta["state"] = "finalizing"
            persist(meta)
            set_qnap_stage(token, "preparing")
            _, _, spool = paths(token)
            if not spool.is_file() or spool.stat().st_size != meta["size"]:
                meta["state"] = "failed"
                meta["error"] = "Temporary upload file is missing or incomplete."
                persist(meta)
                set_qnap_stage(token, "failed")
                return

        start = time.perf_counter()
        qnap_part = None
        published = False
        rel_path = None
        metrics = {}
        try:
            set_qnap_stage(token, "checking_storage")
            ok, status = require_qnap_storage()
            if not ok:
                raise OSError("QNAP storage unavailable: " + status["message"])
            set_qnap_stage(token, "waiting_qnap")
            lock_started = time.perf_counter()
            with qnap_lock:
                metrics["qnap_wait_ms"] = (time.perf_counter() - lock_started) * 1000
                set_qnap_stage(token, "preparing")
                base_dir, base_parts = qnap_path_from_relative(meta["base_path"])
                if not os.path.isdir(base_dir):
                    raise OSError("Target QNAP folder no longer exists.")
                relative_parts = safe_relative_path(meta["relative_path"])
                target_parts = list(base_parts)
                storage_dir = base_dir
                for folder_name in relative_parts[:-1]:
                    candidate = os.path.join(storage_dir, folder_name)
                    if os.path.exists(candidate) and not os.path.isdir(candidate):
                        # Never replace an existing file with a folder.
                        raise OSError(f"A file blocks the destination subfolder: {folder_name}")
                    os.makedirs(candidate, exist_ok=True)
                    target_parts.append(folder_name)
                    storage_dir = candidate
                _, verified_parts = qnap_path_from_relative(qnap_rel_join(target_parts))
                target_parts = verified_parts

                # Persist the *reserved* name before publication so a server restart
                # does not create a duplicate. The file is never overwritten.
                if meta.get("reserved_name"):
                    final_name = meta["reserved_name"]
                    rel_path = qnap_rel_join([*target_parts, final_name])
                else:
                    original_name = safe_name(meta["name"])
                    stem, ext = os.path.splitext(original_name)
                    suffix = 0
                    while True:
                        final_name = original_name if suffix == 0 else f"{stem[:max(1, 176-len(ext)-len(str(suffix)))]} ({suffix}){ext}"
                        rel_path = qnap_rel_join([*target_parts, final_name])
                        if not os.path.exists(os.path.join(storage_dir, final_name)):
                            with db() as check:
                                occupied = check.execute("SELECT 1 FROM file_items WHERE stored_name=?", (rel_path,)).fetchone()
                            if not occupied:
                                break
                        suffix += 1
                    with STATE_LOCK:
                        meta = read_meta(token)
                        meta["reserved_name"] = final_name
                        meta["reserved_parent"] = qnap_rel_join(target_parts)
                        persist(meta)

                if meta.get("reserved_parent") != qnap_rel_join(target_parts):
                    raise OSError("The upload destination changed during finalization.")
                final_path = os.path.join(storage_dir, final_name)
                qnap_part = os.path.join(storage_dir, f".file-center-{token}.part")

                if os.path.isfile(final_path):
                    if os.path.getsize(final_path) != meta["size"]:
                        raise OSError("A different file now occupies the reserved QNAP destination.")
                    # This is the recovery-only path: verify contents before
                    # adopting an existing name in case another writer used it.
                    set_qnap_stage(token, "verifying_existing")
                    recovery_started = time.perf_counter()
                    if sha256_file(final_path) != sha256_file(spool):
                        raise OSError("Reserved QNAP file content differs from the upload spool.")
                    metrics["recovery_sha256_ms"] = (time.perf_counter() - recovery_started) * 1000
                    set_qnap_stage(token, "verifying", meta["size"])
                    # After restart, recover metadata, not a second QNAP copy.
                else:
                    copy_start = time.perf_counter()
                    bytes_written = 0
                    set_qnap_stage(token, "copying", 0)
                    with spool.open("rb", buffering=4 * 1024 * 1024) as source, open(qnap_part, "wb", buffering=4 * 1024 * 1024) as target:
                        while True:
                            block = source.read(4 * 1024 * 1024)
                            if not block:
                                break
                            target.write(block)
                            bytes_written += len(block)
                            # Accurate application-level SMB write progress. A
                            # successful write may still be buffered by the NAS;
                            # fsync and publish remain separate states.
                            set_qnap_stage(token, "copying", bytes_written)
                        metrics["qnap_copy_ms"] = (time.perf_counter() - copy_start) * 1000
                        set_qnap_stage(token, "flushing", bytes_written)
                        flush_started = time.perf_counter()
                        target.flush()
                        os.fsync(target.fileno())
                        metrics["qnap_flush_ms"] = (time.perf_counter() - flush_started) * 1000
                    set_qnap_stage(token, "verifying", bytes_written)
                    verification_started = time.perf_counter()
                    if os.path.getsize(qnap_part) != meta["size"]:
                        raise OSError("QNAP temporary file size does not match source.")
                    metrics["qnap_verify_ms"] = (time.perf_counter() - verification_started) * 1000
                    # Never silently replace a file that appeared while copying.
                    if os.path.exists(final_path):
                        raise OSError("Destination filename became occupied while uploading.")
                    set_qnap_stage(token, "publishing", bytes_written)
                    rename_started = time.perf_counter()
                    os.rename(qnap_part, final_path)
                    metrics["qnap_publish_ms"] = (time.perf_counter() - rename_started) * 1000
                    published = True

                set_qnap_stage(token, "indexing", meta["size"])
                index_started = time.perf_counter()
                now_text = local_now().strftime("%Y-%m-%d %H:%M:%S")
                with db() as c:
                    folder_id = None
                    path_parts = []
                    for component in target_parts:
                        path_parts.append(component)
                        folder_id = ensure_folder_metadata(c, component, folder_id, qnap_rel_join(path_parts))
                    mime = mimetypes.guess_type(final_name)[0] or "application/octet-stream"
                    # If indexing discovers the published file before this statement,
                    # the UPSERT adopts the same row instead of failing UNIQUE.
                    c.execute("""INSERT INTO file_items
                        (folder_id,stored_name,disk_name,original_name,mime_type,size_bytes,uploaded_by,uploaded_display,created_at,qnap_path)
                        VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(stored_name) DO UPDATE SET
                            folder_id=excluded.folder_id,disk_name=excluded.disk_name,
                            original_name=excluded.original_name,mime_type=excluded.mime_type,
                            size_bytes=excluded.size_bytes,uploaded_by=excluded.uploaded_by,
                            uploaded_display=excluded.uploaded_display,created_at=excluded.created_at,
                            qnap_path=excluded.qnap_path""",
                        (folder_id, rel_path, final_name, final_name, mime, meta["size"],
                         meta["owner"], meta["display"], now_text, rel_path))
                    row = c.execute("SELECT id FROM file_items WHERE stored_name=?", (rel_path,)).fetchone()
                    file_id = int(row["id"])
                    c.commit()
                metrics["metadata_ms"] = (time.perf_counter() - index_started) * 1000
                with STATE_LOCK:
                    meta = read_meta(token)
                    meta["state"] = "completed"
                    meta["error"] = None
                    meta["file"] = {"id": file_id, "name": final_name, "qnap_path": rel_path,
                                    "size_bytes": meta["size"], "size_label": format_file_size(meta["size"])}
                    persist(meta)
                    set_qnap_stage(token, "completed", meta["size"])
                mark_dirty()
                invalidate_listing()
                try:
                    spool.unlink(missing_ok=True)
                except OSError:
                    app.logger.warning("Unable to clean completed spool %s", token)
                app.logger.info(
                    "Resumable finalized token=%s name=%r size=%d total_ms=%.1f qnap_wait_ms=%.1f "
                    "qnap_copy_ms=%.1f qnap_flush_ms=%.1f qnap_verify_ms=%.1f "
                    "qnap_publish_ms=%.1f metadata_ms=%.1f recovery_sha256_ms=%.1f",
                    token, final_name, meta["size"], (time.perf_counter()-start)*1000,
                    metrics.get("qnap_wait_ms", 0), metrics.get("qnap_copy_ms", 0),
                    metrics.get("qnap_flush_ms", 0), metrics.get("qnap_verify_ms", 0),
                    metrics.get("qnap_publish_ms", 0), metrics.get("metadata_ms", 0),
                    metrics.get("recovery_sha256_ms", 0),
                )
        except (OSError, sqlite3.Error, ValueError) as exc:
            app.logger.exception("Resumable finalize failed token=%s published=%s rel=%s", token, published, rel_path)
            with STATE_LOCK:
                current = read_meta(token)
                if current and current.get("state") != "completed":
                    current["state"] = "failed"
                    current["error"] = "QNAP finalization failed: " + str(exc) + ". Check File Center before retrying."
                    persist(current)
                    set_qnap_stage(token, "failed")
        finally:
            if qnap_part and os.path.isfile(qnap_part):
                try:
                    os.remove(qnap_part)
                except OSError:
                    app.logger.warning("Could not remove QNAP staging file %s", qnap_part)

    # This only removes stale sessions from Ticketing-upload-temp/resumable-v21.
    # It never cleans QNAP files or modifies existing File Center metadata.
    try:
        cleanup_old()
    except OSError:
        app.logger.warning("Resumable temporary cleanup deferred", exc_info=True)
