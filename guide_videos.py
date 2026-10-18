"""Revisi 29: private QNAP Guide video storage and resumable MP4 uploads.

Video files never live in the web static tree. Public playback requires a live
published Guide share token, checked for every Range request.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from pathlib import Path

from flask import Response, g, jsonify, request, stream_with_context
from werkzeug.utils import secure_filename

CHUNK = 16 * 1024 * 1024
MAX_VIDEO = 4 * 1024 ** 3
MAX_VIDEOS = 8
MAX_SESSIONS = 4
KEEP = 7 * 86400
TOKEN = re.compile(r'^[a-f0-9]{32}$')
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='guide-video-qnap')
GUARD = threading.RLock()
RUNNING = set()
# Each upload ID has its own append lock. Network body reads and spool fsync
# no longer block other uploads or their status requests.
_CHUNK_LOCKS = {}


def _chunk_lock(token):
    with GUARD:
        return _CHUNK_LOCKS.setdefault(token, threading.Lock())


def _has_faststart(path):
    """Find top-level moov preceding mdat, without scanning video payload."""
    try:
        size = path.stat().st_size
        with path.open('rb') as file:
            offset = 0
            for _ in range(96):
                if offset + 8 > size:
                    break
                file.seek(offset)
                atom = file.read(8)
                length = int.from_bytes(atom[:4], 'big')
                header_length = 8
                if length == 1:
                    extended = file.read(8)
                    if len(extended) != 8:
                        break
                    length = int.from_bytes(extended, 'big')
                    header_length = 16
                elif length == 0:
                    length = size - offset
                if length < header_length or offset + length > size:
                    break
                if atom[4:8] == b'moov':
                    return True
                if atom[4:8] == b'mdat':
                    return False
                offset += length
    except OSError:
        pass
    return False


def _faststart_copy(spool, size, logger):
    """Remux into separate local file if FFmpeg and spare disk are available.

    Fallback is always the original upload; no destructive in-place rewriting.
    """
    if _has_faststart(spool):
        return spool, 'already-faststart'
    if os.environ.get('GUIDE_VIDEO_FASTSTART', '1').strip().lower() in ('0', 'false', 'off'):
        return spool, 'disabled'
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return spool, 'ffmpeg-not-found'
    # Remux temporarily needs a second local file of approximately source size.
    if shutil.disk_usage(spool.parent).free < size + 512 * 1024 * 1024:
        return spool, 'insufficient-temporary-space'
    remuxed = spool.with_name('faststart.mp4')
    try:
        remuxed.unlink(missing_ok=True)
        t0 = time.perf_counter()
        args = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
                '-y', '-i', str(spool), '-map', '0', '-c', 'copy',
                '-movflags', '+faststart', str(remuxed)]
        result = subprocess.run(args, capture_output=True, timeout=1800, check=False)
        if result.returncode or not remuxed.exists() or remuxed.stat().st_size < 12 or not _has_faststart(remuxed):
            raise RuntimeError('FFmpeg remux failed or output was not faststart')
        logger.info('Guide video Fast Start remux: bytes=%s elapsed_ms=%.1f',
                    remuxed.stat().st_size, (time.perf_counter()-t0)*1000)
        return remuxed, 'optimized'
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        logger.warning('Guide video Fast Start skipped: %s', str(exc)[:180])
        try: remuxed.unlink(missing_ok=True)
        except OSError: pass
        return spool, 'failed-fallback-original'
    finally:
        # Unsuccessful/incomplete output must never be used for QNAP publishing.
        if not remuxed.exists() or not _has_faststart(remuxed):
            remuxed.unlink(missing_ok=True)

# Revisi 30: small, bounded in-memory MP4 header cache. Entries are only
# consulted AFTER guide/share authorization and metadata lookup.
# Never cache video bytes in the public web tree or in browser HTTP caches.
HEAD_CACHE_BYTES = 1024 * 1024
HEAD_CACHE_LIMIT = 32 * 1024 * 1024
_HEAD_CACHE = OrderedDict()
_HEAD_CACHE_USED = 0
_HEAD_CACHE_LOCK = threading.Lock()


def _head_get(key):
    with _HEAD_CACHE_LOCK:
        value = _HEAD_CACHE.get(key)
        if value is not None:
            _HEAD_CACHE.move_to_end(key)
        return value


def _head_put(key, data):
    global _HEAD_CACHE_USED
    if not data or len(data) > HEAD_CACHE_BYTES:
        return
    with _HEAD_CACHE_LOCK:
        previous = _HEAD_CACHE.get(key)
        if previous is not None and len(previous) >= len(data):
            _HEAD_CACHE.move_to_end(key)
            return
        if previous is not None:
            _HEAD_CACHE_USED -= len(previous)
        data = bytes(data)
        _HEAD_CACHE[key] = data
        _HEAD_CACHE_USED += len(data)
        _HEAD_CACHE.move_to_end(key)
        while _HEAD_CACHE_USED > HEAD_CACHE_LIMIT:
            _, old = _HEAD_CACHE.popitem(last=False)
            _HEAD_CACHE_USED -= len(old)


def _head_forget(gid, vid):
    global _HEAD_CACHE_USED
    with _HEAD_CACHE_LOCK:
        for key in list(_HEAD_CACHE):
            if key[0] == gid and key[1] == vid:
                _HEAD_CACHE_USED -= len(_HEAD_CACHE.pop(key))


def register_guide_videos(bp, app, db, upload_storage, *, get_guide, editable, visible, shared_guide, now):
    root = Path(app.config.get('TEMP_UPLOAD_DIR') or __import__('tempfile').gettempdir()) / 'ticketing-guide-video-v29'
    root.mkdir(parents=True, exist_ok=True)

    with db() as con:
        con.executescript('''
          CREATE TABLE IF NOT EXISTS knowledge_guide_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guide_id INTEGER NOT NULL,
            stored_name TEXT NOT NULL UNIQUE,
            original_name TEXT NOT NULL,
            caption TEXT NOT NULL DEFAULT '',
            size_bytes INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            is_primary INTEGER NOT NULL DEFAULT 0,
            allow_download INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(guide_id) REFERENCES knowledge_guides(id) ON DELETE CASCADE
          );
          CREATE INDEX IF NOT EXISTS idx_guide_videos_guide ON knowledge_guide_videos(guide_id, position);
        ''')

    def error(message, code=400):
        return jsonify(ok=False, error=message), code

    def rows(con, gid):
        return [dict(x) for x in con.execute('''SELECT id, original_name, caption, size_bytes, position,
               is_primary, allow_download FROM knowledge_guide_videos
               WHERE guide_id=? ORDER BY is_primary DESC,position,id''', (gid,)).fetchall()]

    def count(con, gid):
        return con.execute('SELECT COUNT(*) FROM knowledge_guide_videos WHERE guide_id=?', (gid,)).fetchone()[0]

    def edit_check(con, gid):
        guide = get_guide(con, gid)
        return guide is not None and editable(guide)

    def paths(token):
        if not TOKEN.fullmatch(token or ''):
            raise ValueError('Invalid upload ID')
        folder = root / token
        return folder, folder / 'state.json', folder / 'upload.part'

    def load(token):
        try:
            _, state, _ = paths(token)
            with state.open('r', encoding='utf-8') as f:
                obj = json.load(f)
            return obj if obj.get('id') == token else None
        except (ValueError, OSError, TypeError):
            return None

    def save(obj):
        folder, state, _ = paths(obj['id'])
        folder.mkdir(parents=True, exist_ok=True)
        temp = folder / 'state.tmp'
        obj['updated'] = time.time()
        with temp.open('w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, state)

    def scoped(token, gid):
        obj = load(token)
        if obj is None or obj.get('owner') != g.user['username'] or obj.get('guide_id') != gid:
            return None
        return obj

    def public_state(obj):
        return {'ok': True, 'upload_id': obj['id'], 'state': obj['state'], 'offset': obj['offset'],
                'size': obj['size'], 'chunk_size': CHUNK, 'name': obj['name'],
                'progress': obj.get('progress', 0), 'error': obj.get('error'), 'video_id': obj.get('video_id'),
                'qnap_bytes': obj.get('qnap_bytes', 0), 'qnap_total': obj.get('stored_size', obj['size']),
                'qnap_bps': obj.get('qnap_bps', 0), 'qnap_eta': obj.get('qnap_eta'),
                'faststart': obj.get('faststart', '')}

    def cleanup():
        cutoff = time.time() - KEEP
        for folder in root.iterdir():
            if not folder.is_dir() or not TOKEN.fullmatch(folder.name) or folder.name in RUNNING:
                continue
            try:
                obj = load(folder.name)
                if obj and (obj.get('updated', 0) >= cutoff or obj.get('state') in ('queued','optimizing','saving','verifying','indexing')):
                    continue
                shutil.rmtree(folder)
                _CHUNK_LOCKS.pop(folder.name, None)
            except OSError:
                app.logger.warning('Guide video spool cleanup failed', exc_info=True)

    def verify_head(spool):
        with spool.open('rb') as f:
            head = f.read(64)
        # ISO BMFF magic (ftyp) is a quick container check, not a codec validator.
        return len(head) >= 12 and head[4:8] == b'ftyp'

    def finalize(token):
        tmp_qnap = None
        with GUARD:
            obj = load(token)
            if not obj or obj['state'] == 'completed':
                RUNNING.discard(token)
                return
        try:
            folder, _, spool = paths(token)
            if not spool.is_file() or spool.stat().st_size != obj['size'] or not verify_head(spool):
                raise ValueError('MP4 file is incomplete or has an invalid header')
            with db() as con:
                if get_guide(con, obj['guide_id']) is None:
                    raise ValueError('Guide no longer exists')
                preexisting = con.execute('SELECT id FROM knowledge_guide_videos WHERE stored_name=?', (obj['stored'],)).fetchone()
                if preexisting:
                    with GUARD:
                        obj['video_id'] = preexisting['id'];obj['state']='completed';obj['progress']=100;save(obj)
                    return
                if count(con, obj['guide_id']) >= MAX_VIDEOS:
                    raise ValueError('Maximum 8 videos per Guide')
            destination_root = upload_storage.active_root()
            target_folder = upload_storage._folder(destination_root, 'guide', obj['guide_id'])
            target_folder.mkdir(parents=True, exist_ok=True)
            target = target_folder / obj['stored']
            # A restart after QNAP publish must adopt the reserved file rather
            # than remux again (FFmpeg could produce a different byte count).
            copy_source = spool
            target_size = int(obj.get('stored_size') or obj['size'])
            if not target.exists():
                with GUARD:
                    obj['state'] = 'optimizing'
                    obj['progress'] = 0
                    save(obj)
                copy_source, faststart_mode = _faststart_copy(spool, obj['size'], app.logger)
                target_size = copy_source.stat().st_size
                with GUARD:
                    obj['stored_size'] = target_size
                    obj['faststart'] = faststart_mode
                    save(obj)
            # A worker may restart after publish but before SQL commit. Reserved
            # names are random and belong to exactly one spool session.
            copied_here = False
            if not target.exists():
                tmp_qnap = target_folder / ('.' + obj['id'] + '.video.part')
                # On retry, discard only our unique unpublished partial file.
                tmp_qnap.unlink(missing_ok=True)
                with copy_source.open('rb') as source, tmp_qnap.open('xb') as dest:
                    copied = 0
                    last_report = 0
                    copy_begin = time.perf_counter()
                    with GUARD:
                        obj['state']='saving';obj['progress']=0
                        obj['qnap_bytes']=0;obj['qnap_bps']=0;obj['qnap_eta']=None
                        save(obj)
                    while True:
                        block = source.read(4 * 1024 * 1024)
                        if not block:
                            break
                        dest.write(block)
                        copied += len(block)
                        if copied - last_report >= 16 * 1024 * 1024 or copied == target_size:
                            elapsed = max(time.perf_counter() - copy_begin, 0.001)
                            rate = copied / elapsed
                            with GUARD:
                                obj['progress'] = min(98, int(copied * 100 / target_size))
                                obj['qnap_bytes'] = copied
                                obj['qnap_bps'] = int(rate)
                                obj['qnap_eta'] = max(0, int((target_size-copied)/rate)) if rate else None
                                save(obj)
                            last_report = copied
                    dest.flush()
                    os.fsync(dest.fileno())
                app.logger.info('Guide video QNAP write: guide=%s size=%s elapsed_ms=%.1f rate_mib_s=%.1f',
                                obj['guide_id'],target_size,(time.perf_counter()-copy_begin)*1000,
                                target_size/max(time.perf_counter()-copy_begin,0.001)/1048576)
                with GUARD:
                    obj['state']='verifying';obj['progress']=99;save(obj)
                if tmp_qnap.stat().st_size != target_size:
                    raise OSError('QNAP copy size mismatch')
                os.replace(tmp_qnap, target)
                copied_here = True
                tmp_qnap = None
                with GUARD:
                    obj['published'] = True
                    save(obj)
            if target.stat().st_size != target_size:
                raise OSError('Published QNAP video size mismatch')
            with GUARD:
                obj['state']='indexing';obj['progress']=99;save(obj)
            with db() as con:
                if get_guide(con,obj['guide_id']) is None:
                    # Only the uniquely reserved file created by this session.
                    # A concurrent Guide delete must not leave an orphan video.
                    target.unlink(missing_ok=True)
                    raise ValueError('Guide deleted during upload')
                con.execute('''INSERT OR IGNORE INTO knowledge_guide_videos
                    (guide_id,stored_name,original_name,size_bytes,position,is_primary,created_at)
                    VALUES (?,?,?,?,?,?,?)''',
                    (obj['guide_id'],obj['stored'],obj['name'],target_size,
                     count(con,obj['guide_id']), int(count(con,obj['guide_id']) == 0),now()))
                video = con.execute('SELECT id FROM knowledge_guide_videos WHERE stored_name=?', (obj['stored'],)).fetchone()
                if not video:
                    raise OSError('Could not index video after copying')
                obj['video_id'] = video['id']
            # Prime the first MiB from the local upload spool, not from SMB.
            # This accelerates the first play after a NEW upload without
            # persisting a second copy of the video on Windows.
            try:
                qstat = target.stat()
                cache_key = (obj['guide_id'], obj['video_id'], str(target), qstat.st_size, qstat.st_mtime_ns)
                # Recovery may reuse an already optimized QNAP file while
                # the original upload spool is still unoptimized. In that
                # case only cache bytes read from the actual published file.
                with (copy_source if copied_here else target).open('rb') as local_video:
                    _head_put(cache_key, local_video.read(HEAD_CACHE_BYTES))
            except OSError:
                app.logger.debug('Guide video initial cache prime skipped', exc_info=True)
            with GUARD:
                obj['state']='completed';obj['progress']=100;save(obj)
            try:
                spool.unlink(missing_ok=True)
                remuxed = spool.with_name('faststart.mp4')
                remuxed.unlink(missing_ok=True)
            except OSError:
                pass
        except Exception as exc:
            app.logger.exception('Guide video finalize failed: upload=%s', token)
            with GUARD:
                obj = load(token)
                if obj:
                    obj['state']='failed';obj['error']=str(exc)[:240];save(obj)
        finally:
            if tmp_qnap:
                try:tmp_qnap.unlink(missing_ok=True)
                except OSError:pass
            # Leave original spool for resumable failure/recovery. Remux output
            # is derived, so it is always safe to recreate on a later attempt.
            try:
                paths(token)[2].with_name('faststart.mp4').unlink(missing_ok=True)
            except OSError:
                pass
            with GUARD:RUNNING.discard(token)

    def launch(obj):
        if obj['id'] not in RUNNING:
            RUNNING.add(obj['id'])
            POOL.submit(finalize, obj['id'])

    @bp.post('/api/guides/<int:gid>/videos/init')
    def guide_video_init(gid):
        data = request.get_json(silent=True) or {}
        name = Path(str(data.get('name') or '').replace('\\','/')).name[:180]
        if not name or Path(name).suffix.lower() != '.mp4' or any(ord(c)<32 for c in name):
            return error('Only MP4 videos are supported')
        try:size = int(data.get('size'))
        except (TypeError,ValueError):return error('Invalid video size')
        if size < 12 or size > MAX_VIDEO:
            return error('Maximum video size is 4 GB',413)
        with db() as con:
            if not edit_check(con,gid):return error('Guide not found or access denied',404)
            if count(con,gid) >= MAX_VIDEOS:return error('Maximum 8 videos per Guide')
        with GUARD:
            cleanup()
            requested = data.get('upload_id')
            old = scoped(requested,gid) if isinstance(requested,str) else None
            if old:
                if old['size'] != size or old['name'] != name:
                    return error('Resume file does not match original',409)
                if old['state'] in ('queued','optimizing','saving','verifying','indexing'):
                    launch(old)
                return jsonify(public_state(old))
            active = 0
            for folder in root.iterdir():
                if not folder.is_dir() or not TOKEN.fullmatch(folder.name):continue
                m=load(folder.name)
                if m and m.get('owner')==g.user['username'] and m.get('state') in ('uploading','queued','optimizing','saving','verifying','indexing'):
                    active+=1
            if active >= MAX_SESSIONS:return error('Too many active video uploads',429)
            free=shutil.disk_usage(root).free
            if free < size + 512*1024*1024:
                return error('Insufficient free temporary disk space on server',507)
            token = secrets.token_hex(16)
            obj = {'id':token,'owner':g.user['username'],'guide_id':gid,'name':name,'size':size,
                   'offset':0,'state':'uploading','stored':secrets.token_hex(20)+'.mp4',
                   'updated':time.time(),'progress':0,'error':None}
            save(obj)
        return jsonify(public_state(obj)),201

    @bp.get('/api/guides/<int:gid>/videos/upload/<token>/status')
    def guide_video_status(gid,token):
        with GUARD:
            obj=scoped(token,gid)
            if not obj:return error('Upload session not found',404)
            if obj['state'] in ('queued','optimizing','saving','verifying','indexing'):
                launch(obj)
            return jsonify(public_state(obj))

    @bp.put('/api/guides/<int:gid>/videos/upload/<token>/chunk')
    def guide_video_chunk(gid,token):
        # Authenticate and prevalidate BEFORE consuming the body; do not hold
        # the global guard while reading up to 16 MiB from a slow browser.
        with GUARD:
            obj=scoped(token,gid)
            if not obj:return error('Upload session not found',404)
            if obj['state'] != 'uploading':return jsonify(public_state(obj)),409
            try: offset=int(request.headers.get('X-Upload-Offset','-1'))
            except ValueError:return error('Invalid chunk offset')
            if offset != obj['offset']:
                return jsonify(public_state(obj)),409
        if request.content_length is not None and request.content_length > CHUNK:
            return error('Chunk exceeds 16 MB',413)
        body=request.stream.read(CHUNK + 1)
        if not body or len(body)>CHUNK:
            return error('Chunk must be 1-16 MB',413)
        # A per-session lock protects against two simultaneous requests for
        # the same offset. Other sessions may continue writing concurrently.
        with _chunk_lock(token):
            with GUARD:
                obj=scoped(token,gid)
                if not obj:return error('Upload session not found',404)
                if obj['state'] != 'uploading' or offset != obj['offset']:
                    return jsonify(public_state(obj)),409
                if offset+len(body)>obj['size']:
                    return error('Chunk exceeds original file size',413)
            _,_,spool=paths(token)
            if spool.exists() and spool.stat().st_size != offset:
                if spool.stat().st_size < offset:
                    return error('Temporary upload is incomplete',409)
                with spool.open('r+b') as f:f.truncate(offset)
            disk_begin=time.perf_counter()
            with spool.open('ab') as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            elapsed_ms=(time.perf_counter()-disk_begin)*1000
            if elapsed_ms>=500:
                app.logger.info('Guide video chunk disk sync: guide=%s bytes=%s elapsed_ms=%.1f',
                                gid,len(body),elapsed_ms)
            with GUARD:
                obj['offset']+=len(body)
                save(obj)
                return jsonify(public_state(obj))

    @bp.post('/api/guides/<int:gid>/videos/upload/<token>/finish')
    def guide_video_finish(gid,token):
        with GUARD:
            obj=scoped(token,gid)
            if not obj:return error('Upload session not found',404)
            if obj['state']=='uploading':
                if obj['offset'] != obj['size']:return error('Upload is not yet complete',409)
                obj['state']='queued';save(obj)
            if obj['state'] in ('queued','optimizing','saving','verifying','indexing'):
                launch(obj)
            return jsonify(public_state(obj))

    @bp.put('/api/guides/<int:gid>/videos/layout')
    def guide_video_layout(gid):
        data=request.get_json(silent=True) or {}
        layout=data.get('videos')
        if not isinstance(layout,list):return error('Invalid video list')
        with db() as con:
            if not edit_check(con,gid):return error('Guide not found',404)
            existing={r['id'] for r in rows(con,gid)}
            if {r.get('id') for r in layout if isinstance(r,dict)} != existing or len(layout)!=len(existing):
                return error('Video layout does not match current Guide',409)
            for position,row in enumerate(layout):
                caption=row.get('caption','')
                if not isinstance(caption,str) or len(caption)>180:return error('Invalid video title')
                con.execute('''UPDATE knowledge_guide_videos SET caption=?,position=?,is_primary=?,allow_download=?
                   WHERE guide_id=? AND id=?''', (caption.strip(),position,int(bool(row.get('primary'))),
                   int(bool(row.get('allow_download'))),gid,row['id']))
            # Exactly one main video, independently of client behavior.
            main=next((r['id'] for r in layout if r.get('primary')), layout[0]['id'] if layout else None)
            if main is not None:
                con.execute('UPDATE knowledge_guide_videos SET is_primary=(id=?) WHERE guide_id=?',(main,gid))
        return jsonify(ok=True)

    @bp.delete('/api/guides/<int:gid>/videos/<int:vid>')
    def guide_video_delete(gid,vid):
        with db() as con:
            if not edit_check(con,gid):return error('Guide not found',404)
            video=con.execute('SELECT stored_name FROM knowledge_guide_videos WHERE guide_id=? AND id=?',(gid,vid)).fetchone()
            if not video:return error('Video not found',404)
            try:upload_storage.remove('guide',video['stored_name'],pid=gid)
            except OSError:
                app.logger.exception('Guide video removal failed')
                return error('QNAP unavailable; video not deleted',503)
            con.execute('DELETE FROM knowledge_guide_videos WHERE guide_id=? AND id=?',(gid,vid))
            _head_forget(gid,vid)
            new_main=con.execute('SELECT id FROM knowledge_guide_videos WHERE guide_id=? ORDER BY is_primary DESC,position,id LIMIT 1',(gid,)).fetchone()
            if new_main:con.execute('UPDATE knowledge_guide_videos SET is_primary=(id=?) WHERE guide_id=?',(new_main['id'],gid))
        return jsonify(ok=True)

    def serve_video(gid,vid,download=False):
        clock = time.perf_counter
        t0 = clock()
        with db() as con:
            row = con.execute('SELECT stored_name,original_name,allow_download FROM knowledge_guide_videos WHERE guide_id=? AND id=?', (gid, vid)).fetchone()
        t_db = clock()
        if not row:
            return error('Video not found', 404)
        if download and not row['allow_download']:
            return error('Video download is disabled', 403)
        try:
            path = upload_storage.resolve('guide', row['stored_name'], pid=gid)
        except OSError:
            path = None
        t_resolve = clock()
        if not path:
            return error('Video unavailable on QNAP', 503)
        try:
            metadata = path.stat()
            size = metadata.st_size
        except OSError:
            return error('Video unavailable on QNAP', 503)
        t_stat = clock()
        if size <= 0:
            return error('Video is empty', 503)

        start, end = 0, size - 1
        range_header = request.headers.get('Range', '')
        status = 200
        if range_header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header.strip())
            if not match or (not match[1] and not match[2]):
                return Response(status=416, headers={'Content-Range': f'bytes */{size}'})
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), size - 1) if match[2] else size - 1
            else:
                length = int(match[2])
                if length <= 0:
                    return Response(status=416, headers={'Content-Range': f'bytes */{size}'})
                start = max(0, size - length)
            if start >= size or end < start:
                return Response(status=416, headers={'Content-Range': f'bytes */{size}'})
            status = 206

        # File stat and authorization happen even when bytes are served from
        # RAM. Key includes size and modification time to avoid stale content.
        cache_key = (gid, vid, str(path), size, metadata.st_mtime_ns)
        head = None if download else _head_get(cache_key)
        headers = {
            'Accept-Ranges': 'bytes',
            'Content-Length': str(end - start + 1),
            'Cache-Control': 'private, no-store',
            'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'none'",
            'X-Guide-Video-Revision': '31',
            'Server-Timing': (
                f'video-db;dur={(t_db-t0)*1000:.1f}, '
                f'qnap-resolve;dur={(t_resolve-t_db)*1000:.1f}, '
                f'qnap-stat;dur={(t_stat-t_resolve)*1000:.1f}'),
        }
        if status == 206:
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        if download:
            filename = secure_filename(row['original_name']) or 'guide-video.mp4'
            headers['Content-Disposition'] = f'attachment; filename="{filename}"'

        def generate():
            position, first_byte = start, False
            head_capture = (bytearray(head or b'') if start == 0 and not download else None)
            stream_begin = clock()
            try:
                # Serve the first available bytes immediately from bounded
                # RAM cache; SMB open can take place AFTER this first yield.
                if head is not None and position < len(head):
                    part = head[position:min(end + 1, len(head))]
                    if part:
                        yield part
                        position += len(part)
                        first_byte = True
                        app.logger.info('Guide video first byte: guide=%s video=%s source=ram range_start=%s elapsed_ms=%.1f',
                                        gid, vid, start, (clock()-t0)*1000)
                if position > end:
                    return
                opening = clock()
                with path.open('rb') as video:
                    open_ms = (clock()-opening)*1000
                    video.seek(position)
                    while position <= end:
                        # Read only 64 KiB before yielding the FIRST block;
                        # older code waited for 512 KiB over SMB.
                        amount = min(64*1024 if not first_byte else 1024*1024, end-position+1)
                        reading = clock()
                        block = video.read(amount)
                        read_ms = (clock()-reading)*1000
                        if not block:
                            app.logger.warning('Guide video truncated during stream: guide=%s video=%s offset=%s',gid,vid,position)
                            break
                        if head_capture is not None and position == len(head_capture) and len(head_capture) < HEAD_CACHE_BYTES:
                            keep = min(HEAD_CACHE_BYTES - len(head_capture), len(block))
                            head_capture.extend(block[:keep])
                            _head_put(cache_key,head_capture)
                        if not first_byte:
                            first_byte = True
                            app.logger.info('Guide video first byte: guide=%s video=%s source=qnap range_start=%s '
                                            'setup_ms=%.1f open_ms=%.1f read_ms=%.1f total_ms=%.1f',
                                            gid,vid,start,(t_stat-t0)*1000,open_ms,read_ms,(clock()-t0)*1000)
                        yield block
                        position += len(block)
            except OSError:
                app.logger.exception('Guide video SMB stream interrupted: guide=%s video=%s offset=%s',gid,vid,position)
            finally:
                elapsed = (clock()-stream_begin)*1000
                if elapsed >= 3000:
                    app.logger.info('Guide video stream finished: guide=%s video=%s bytes_sent=%s elapsed_ms=%.1f',
                                    gid,vid,position-start,elapsed)

        return Response(stream_with_context(generate()), status=status,
                        mimetype='video/mp4', headers=headers)

    @bp.get('/api/guides/<int:gid>/videos/<int:vid>/stream')
    def guide_video_stream(gid,vid):
        with db() as con:
            guide=get_guide(con,gid)
            if not guide or not visible(guide):return error('Guide not found',404)
        return serve_video(gid,vid, request.args.get('download')=='1')

    @bp.get('/api/public/g/<token>/videos/<int:vid>/stream')
    def public_guide_video_stream(token,vid):
        with db() as con:
            guide=shared_guide(con,token)
            if not guide:return error('Guide link invalid or disabled',404)
        return serve_video(guide['id'],vid,request.args.get('download')=='1')

    return rows
