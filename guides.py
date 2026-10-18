"""Authenticated Guide knowledge base with workflow and Markdown documentation.

All persistent data stays in the existing SQLite database, in isolated tables.
Guide media is stored privately on QNAP; Ticket and Project data remain untouched.
"""
from datetime import datetime
import base64
import hashlib
import hmac
import secrets
import mimetypes
from pathlib import Path
from uuid import uuid4
from flask import Blueprint, g, jsonify, request, send_file, send_from_directory

CATEGORIES = ('Network', 'System', 'Deployment', 'Software', 'Other')
STATUSES = ('Draft', 'Published')
MAX_STEPS = 40
MAX_MARKDOWN_CHARS = 75000
FORMATS = ('workflow', 'documentation')
MAX_GALLERY = 12
MAX_STEP_IMAGES = 6
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
# Documentation files are downloaded as attachments, never executed or rendered inline.
ALLOWED_DOCUMENTS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv', '.zip', '.md'}
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_DOCUMENTS = 15


def image_signature_valid(suffix, data):
    if suffix == '.png':
        return data.startswith(b'\x89PNG\r\n\x1a\n')
    if suffix in ('.jpg', '.jpeg'):
        return data.startswith(b'\xff\xd8\xff')
    if suffix == '.webp':
        return len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP'
    return False


def register_guides(app, db, page_dir, upload_storage=None):
    bp = Blueprint('guides', __name__)
    if upload_storage is None:
        raise RuntimeError('Guide images require QNAP dashboard upload storage')

    with db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS knowledge_guides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'Other',
                status TEXT NOT NULL DEFAULT 'Draft',
                author TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS knowledge_guide_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guide_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                instruction TEXT NOT NULL DEFAULT '',
                command TEXT NOT NULL DEFAULT '',
                reference_url TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (guide_id) REFERENCES knowledge_guides(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS knowledge_guide_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guide_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('gallery','step')),
                step_position INTEGER,
                position INTEGER NOT NULL DEFAULT 0,
                caption TEXT NOT NULL DEFAULT '',
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL UNIQUE,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (guide_id) REFERENCES knowledge_guides(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS knowledge_guide_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guide_id INTEGER NOT NULL,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (guide_id) REFERENCES knowledge_guides(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_guide_files_guide ON knowledge_guide_files(guide_id);
            CREATE TABLE IF NOT EXISTS knowledge_guide_shares (
                guide_id INTEGER PRIMARY KEY,
                nonce TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (guide_id) REFERENCES knowledge_guides(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_guide_images_guide ON knowledge_guide_images(guide_id,kind,position);
            CREATE INDEX IF NOT EXISTS idx_knowledge_guides_updated ON knowledge_guides(updated_at);
            CREATE INDEX IF NOT EXISTS idx_knowledge_steps_guide ON knowledge_guide_steps(guide_id,position);
        ''')
        # Existing guides remain Workflow. Adding columns preserves all rows.
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(knowledge_guides)')}
        if 'content_format' not in columns:
            conn.execute("ALTER TABLE knowledge_guides ADD COLUMN content_format TEXT NOT NULL DEFAULT 'workflow'")
        if 'content_markdown' not in columns:
            conn.execute("ALTER TABLE knowledge_guides ADD COLUMN content_markdown TEXT NOT NULL DEFAULT ''")

    def now():
        return datetime.now().astimezone().isoformat(timespec='seconds')

    def fail(message, status):
        return jsonify(ok=False, error=message), status

    def editable(guide):
        return g.user['role'] == 'admin' or guide['author'].casefold() == g.user['username'].casefold()

    def visible(guide):
        return guide['status'] == 'Published' or editable(guide)

    def validate(data, default_format='workflow', previous_markdown=''):
        if not isinstance(data, dict):
            raise ValueError('Invalid guide data')
        title = data.get('title')
        description = data.get('description', '')
        category = data.get('category', 'Other')
        status = data.get('status', 'Draft')
        steps = data.get('steps')
        content_format = data.get('content_format', default_format)
        content_markdown = data.get('content_markdown', previous_markdown)
        if content_format not in FORMATS:
            raise ValueError('Invalid content format')
        if not isinstance(content_markdown, str) or len(content_markdown) > MAX_MARKDOWN_CHARS:
            raise ValueError('Documentation exceeds 75,000 characters')
        if content_format == 'documentation' and not content_markdown.strip():
            raise ValueError('Documentation must contain text')
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
            raise ValueError('Guide title must be 1–120 characters')
        if not isinstance(description, str) or len(description) > 3000:
            raise ValueError('Description must be under 3,000 characters')
        if category not in CATEGORIES or status not in STATUSES:
            raise ValueError('Invalid category or visibility')
        if content_format == 'workflow':
            if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
                raise ValueError('Each guide needs 1–40 workflow steps')
        elif steps is None:
            steps = []
        elif not isinstance(steps, list) or len(steps) > MAX_STEPS:
            raise ValueError('Invalid workflow steps')
        safe_steps = []
        for index, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                raise ValueError(f'Step {index} is invalid')
            title_step = step.get('title')
            instruction = step.get('instruction', '')
            command = step.get('command', '')
            reference = step.get('reference_url', '')
            if not isinstance(title_step, str) or not 1 <= len(title_step.strip()) <= 120:
                raise ValueError(f'Step {index} needs a title (up to 120 characters)')
            if not isinstance(instruction, str) or len(instruction) > 4000:
                raise ValueError(f'Step {index} instruction must be under 4,000 characters')
            if not isinstance(command, str) or len(command) > 2000:
                raise ValueError(f'Step {index} command must be under 2,000 characters')
            if not isinstance(reference, str) or len(reference) > 700:
                raise ValueError(f'Step {index} link is too long')
            reference = reference.strip()
            if reference and not reference.lower().startswith(('https://', 'http://')):
                raise ValueError(f'Step {index} link must start with https:// or http://')
            # Disallow misleading URL whitespace / control chars. Browser still renders via safe DOM.
            if any(c.isspace() or ord(c) < 32 for c in reference):
                raise ValueError(f'Step {index} link contains invalid characters')
            safe_steps.append((index, title_step.strip(), instruction, command, reference))
        return title.strip(), description, category, status, safe_steps, content_format, content_markdown

    def get_guide(conn, gid):
        return conn.execute('SELECT * FROM knowledge_guides WHERE id=?', (gid,)).fetchone()

    def share_token(gid, nonce):
        secret = app.secret_key
        if isinstance(secret, str):
            secret = secret.encode('utf-8')
        value = hmac.new(secret, f'guide-share:v1:{gid}:{nonce}'.encode('utf-8'), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')

    def share_record(conn, gid):
        return conn.execute('SELECT * FROM knowledge_guide_shares WHERE guide_id=?', (gid,)).fetchone()

    def shared_guide(conn, token):
        # A long unguessable bearer token; no anonymous list/search endpoint exists.
        if not isinstance(token, str) or len(token) != 43 or not all(c.isalnum() or c in '-_' for c in token):
            return None
        digest = hashlib.sha256(token.encode('ascii')).hexdigest()
        return conn.execute('''SELECT g.* FROM knowledge_guide_shares s
            JOIN knowledge_guides g ON g.id=s.guide_id
            WHERE s.token_hash=? AND s.enabled=1 AND g.status='Published' ''', (digest,)).fetchone()

    def _guide_video_rows(gid):
        with db() as video_conn:
            return video_rows(video_conn, gid)

    def guide_files(conn, gid):
        return [dict(x) for x in conn.execute('''SELECT id, original_name, size_bytes, created_at
            FROM knowledge_guide_files WHERE guide_id=? ORDER BY id''', (gid,)).fetchall()]

    def read_media(gid, row, download=False):
        try:
            source = upload_storage.resolve('guide', row['stored_name'], pid=gid)
        except OSError:
            source = None
        if source is None:
            return fail('File unavailable on QNAP', 503)
        response = send_file(source, mimetype=('application/octet-stream' if download else row['mime_type']),
                             as_attachment=download, conditional=True, download_name=row['original_name'])
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response


    @bp.get('/guide')
    def guide_page():
        return send_from_directory(page_dir, 'guide.html')

    @bp.get('/guide/new')
    @bp.get('/guide/<int:gid>')
    def guide_detail_page(gid=None):
        return send_from_directory(page_dir, 'guide_detail.html')

    @bp.get('/api/guides')
    def list_guides():
        with db() as conn:
            rows = conn.execute('''
                SELECT g.*,
                    (SELECT COUNT(*) FROM knowledge_guide_steps s WHERE s.guide_id=g.id) AS step_count,
                    (SELECT id FROM knowledge_guide_images i WHERE i.guide_id=g.id
                       AND i.kind='gallery' ORDER BY i.is_primary DESC,i.position,i.id LIMIT 1) AS cover_image_id
                FROM knowledge_guides g
                ORDER BY g.updated_at DESC, g.id DESC
            ''').fetchall()
        items = [dict(row) for row in rows if visible(row)]
        return jsonify(ok=True, guides=items)

    @bp.get('/api/guides/<int:gid>')
    def guide_detail(gid):
        with db() as conn:
            guide = get_guide(conn, gid)
            if guide is None or not visible(guide):
                return fail('Guide not found', 404)
            steps = conn.execute('''SELECT id,position,title,instruction,command,reference_url
                FROM knowledge_guide_steps WHERE guide_id=? ORDER BY position,id''', (gid,)).fetchall()
            guide_files_snapshot = guide_files(conn, gid)
            images = conn.execute('''SELECT id,guide_id,kind,step_position,position,caption,
                original_name,mime_type,size_bytes,is_primary FROM knowledge_guide_images
                WHERE guide_id=? ORDER BY kind,position,id''', (gid,)).fetchall()
        return jsonify(ok=True, guide=dict(guide), steps=[dict(step) for step in steps],
                       images=[dict(image) for image in images], files=guide_files_snapshot,
                       videos=_guide_video_rows(gid),
                       can_edit=editable(guide))

    @bp.post('/api/guides')
    def create_guide():
        try:
            title, description, category, status, steps, content_format, markdown = validate(request.get_json(silent=True))
        except ValueError as exc:
            return fail(str(exc), 400)
        stamp = now()
        with db() as conn:
            new = conn.execute('''INSERT INTO knowledge_guides
                (title,description,category,status,author,created_at,updated_at,content_format,content_markdown)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (title, description, category, status, g.user['username'], stamp, stamp, content_format, markdown))
            gid = new.lastrowid
            if content_format == 'workflow':
                conn.executemany('''INSERT INTO knowledge_guide_steps
                    (guide_id,position,title,instruction,command,reference_url) VALUES (?,?,?,?,?,?)''',
                    [(gid, *step) for step in steps])
        return jsonify(ok=True, id=gid), 201

    @bp.put('/api/guides/<int:gid>')
    def save_guide(gid):
        with db() as conn:
            guide = get_guide(conn, gid)
            if not guide:
                return fail('Guide not found', 404)
            if not editable(guide):
                return fail('Only the author or Admin may edit this guide', 403)
            try:
                title, description, category, status, steps, content_format, markdown = validate(
                    request.get_json(silent=True), guide['content_format'], guide['content_markdown'])
            except ValueError as exc:
                return fail(str(exc), 400)
            conn.execute('''UPDATE knowledge_guides
                SET title=?,description=?,category=?,status=?,updated_at=?,content_format=?,content_markdown=? WHERE id=?''',
                (title, description, category, status, now(), content_format, markdown, gid))
            if status == 'Draft':
                # Draft cannot be shared. Disable existing bearer token permanently
                # so republishing cannot unexpectedly re-enable an old link.
                conn.execute('UPDATE knowledge_guide_shares SET enabled=0,updated_at=? WHERE guide_id=?', (now(),gid))
            # Retain legacy workflow steps when switching to Documentation.
            # Switching back restores the prior steps and their associated media.
            if content_format == 'workflow':
                conn.execute('DELETE FROM knowledge_guide_steps WHERE guide_id=?', (gid,))
                conn.executemany('''INSERT INTO knowledge_guide_steps
                    (guide_id,position,title,instruction,command,reference_url) VALUES (?,?,?,?,?,?)''',
                    [(gid, *step) for step in steps])
        return jsonify(ok=True, id=gid)

    @bp.delete('/api/guides/<int:gid>')
    def delete_guide(gid):
        with db() as conn:
            guide = get_guide(conn, gid)
            if not guide:
                return fail('Guide not found', 404)
            if not editable(guide):
                return fail('Only the author or Admin may delete this guide', 403)
            images = conn.execute('SELECT id,stored_name FROM knowledge_guide_images WHERE guide_id=?', (gid,)).fetchall()
            # Remove private QNAP uploads before removing their SQL ownership metadata.
            for image in images:
                try:
                    upload_storage.remove('guide', image['stored_name'], pid=gid)
                except OSError:
                    app.logger.exception('Guide media QNAP removal failed for guide %s', gid)
                    return fail('QNAP unavailable. Guide deletion could not finish. Please retry.', 503)
                conn.execute('DELETE FROM knowledge_guide_images WHERE guide_id=? AND id=?', (gid, image['id']))
            conn.execute('DELETE FROM knowledge_guide_images WHERE guide_id=?', (gid,))
            videos = conn.execute('SELECT id,stored_name FROM knowledge_guide_videos WHERE guide_id=?', (gid,)).fetchall()
            for video in videos:
                try:
                    upload_storage.remove('guide', video['stored_name'], pid=gid)
                except OSError:
                    app.logger.exception('Guide video QNAP removal failed for guide %s', gid)
                    return fail('QNAP unavailable. Video removal could not finish.', 503)
                conn.execute('DELETE FROM knowledge_guide_videos WHERE id=? AND guide_id=?', (video['id'],gid))
            documents = conn.execute('SELECT id,stored_name FROM knowledge_guide_files WHERE guide_id=?', (gid,)).fetchall()
            for document in documents:
                try:
                    upload_storage.remove('guide', document['stored_name'], pid=gid)
                except OSError:
                    app.logger.exception('Guide documentation QNAP removal failed for guide %s', gid)
                    return fail('QNAP unavailable. Guide deletion could not finish. Please retry.', 503)
                conn.execute('DELETE FROM knowledge_guide_files WHERE id=? AND guide_id=?', (document['id'],gid))
            conn.execute('DELETE FROM knowledge_guide_shares WHERE guide_id=?', (gid,))
            conn.execute('DELETE FROM knowledge_guide_steps WHERE guide_id=?', (gid,))
            conn.execute('DELETE FROM knowledge_guides WHERE id=?', (gid,))
        return jsonify(ok=True)


    def image_rows(conn, gid):
        return conn.execute('''SELECT id,guide_id,kind,step_position,position,caption,
             original_name,mime_type,size_bytes,is_primary FROM knowledge_guide_images
             WHERE guide_id=? ORDER BY kind,position,id''', (gid,)).fetchall()

    @bp.post('/api/guides/<int:gid>/images')
    def upload_guide_image(gid):
        with db() as conn:
            guide = get_guide(conn, gid)
            if not guide:
                return fail('Guide not found', 404)
            if not editable(guide):
                return fail('Only the author or Admin may upload images', 403)
            kind = request.form.get('kind', '')
            if kind not in ('gallery', 'step'):
                return fail('Invalid image type', 400)
            step_position = None
            if kind == 'step':
                try:
                    step_position = int(request.form.get('step_position', ''))
                except ValueError:
                    return fail('Invalid step position', 400)
                steps = conn.execute('SELECT COUNT(*) FROM knowledge_guide_steps WHERE guide_id=?', (gid,)).fetchone()[0]
                if step_position < 1 or step_position > steps:
                    return fail('Step does not exist', 400)
            count = conn.execute('''SELECT COUNT(*) FROM knowledge_guide_images WHERE guide_id=?
                AND kind=? AND (?='gallery' OR step_position=?)''',
                (gid, kind, kind, step_position)).fetchone()[0]
            if count >= (MAX_GALLERY if kind == 'gallery' else MAX_STEP_IMAGES):
                return fail('Image limit reached for this section', 400)
            if request.content_length and request.content_length > MAX_IMAGE_BYTES + 128 * 1024:
                return fail('Maximum image size is 8 MB', 413)
            upload = request.files.get('image')
            if not upload or not upload.filename:
                return fail('Select an image to upload', 400)
            original_name = Path(upload.filename.replace('\\', '/')).name[:180]
            suffix = Path(original_name).suffix.casefold()
            if suffix not in ALLOWED_IMAGES:
                return fail('Supported images: PNG, JPG and WebP', 400)
            data = upload.stream.read(MAX_IMAGE_BYTES + 1)
            if not data or len(data) > MAX_IMAGE_BYTES:
                return fail('Maximum image size is 8 MB', 413)
            if not image_signature_valid(suffix, data):
                return fail('Invalid image file', 400)
            caption = request.form.get('caption', '').strip()
            if len(caption) > 150:
                return fail('Image caption is too long (150 characters max)', 400)
            stored_name = uuid4().hex + suffix
            try:
                upload_storage.write('guide', stored_name, data, pid=gid)
            except OSError:
                app.logger.exception('QNAP guide image upload failed for %s', gid)
                return fail('QNAP unavailable. Image was not saved.', 503)
            try:
                position = conn.execute('SELECT COALESCE(MAX(position),0)+1 FROM knowledge_guide_images WHERE guide_id=? AND kind=?',
                                        (gid, kind)).fetchone()[0]
                is_primary = int(kind == 'gallery' and count == 0)
                cur = conn.execute('''INSERT INTO knowledge_guide_images
                    (guide_id,kind,step_position,position,caption,original_name,stored_name,mime_type,size_bytes,is_primary,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (gid,kind,step_position,position,caption,original_name,stored_name,ALLOWED_IMAGES[suffix],len(data),is_primary,now()))
            except Exception:
                try:
                    upload_storage.remove('guide', stored_name, pid=gid)
                except OSError:
                    app.logger.exception('Could not clean up orphan Guide image on QNAP')
                raise
        return jsonify(ok=True, id=cur.lastrowid), 201

    @bp.get('/api/guides/<int:gid>/images/<int:image_id>')
    def download_guide_image(gid, image_id):
        with db() as conn:
            guide = get_guide(conn, gid)
            if not guide or not visible(guide):
                return fail('Guide not found', 404)
            item = conn.execute('SELECT stored_name,mime_type,original_name FROM knowledge_guide_images WHERE guide_id=? AND id=?',
                                (gid,image_id)).fetchone()
            if item is None:
                return fail('Image not found', 404)
        try:
            source = upload_storage.resolve('guide', item['stored_name'], pid=gid)
        except OSError:
            source = None
        if source is None:
            return fail('Image unavailable on QNAP', 503)
        response = send_file(source, mimetype=item['mime_type'], as_attachment=False, conditional=True,
                             download_name=item['original_name'])
        response.headers['Cache-Control'] = 'private, max-age=60'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
        return response

    @bp.put('/api/guides/<int:gid>/media-layout')
    def save_media_layout(gid):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('gallery'), list) or not isinstance(data.get('steps'), list):
            return fail('Invalid image layout', 400)
        with db() as conn:
            guide = get_guide(conn, gid)
            if guide is None:
                return fail('Guide not found', 404)
            if not editable(guide):
                return fail('Only author or Admin may manage images', 403)
            step_count = conn.execute('SELECT COUNT(*) FROM knowledge_guide_steps WHERE guide_id=?', (gid,)).fetchone()[0]
            if len(data['gallery']) > MAX_GALLERY or len(data['steps']) != step_count:
                return fail('Image count or step count is invalid', 400)
            incoming = []
            changes = []
            for idx, item in enumerate(data['gallery'], 1):
                if not isinstance(item, dict) or type(item.get('id')) is not int or not isinstance(item.get('caption',''),str) or len(item.get('caption','')) > 150:
                    return fail('Invalid gallery image', 400)
                incoming.append(item['id'])
                changes.append((item['id'],'gallery',None,idx,item.get('caption','').strip()))
            for step_index, group in enumerate(data['steps'], 1):
                if not isinstance(group,list) or len(group) > MAX_STEP_IMAGES:
                    return fail('Invalid step image list',400)
                for idx,item in enumerate(group, 1):
                    if not isinstance(item,dict) or type(item.get('id')) is not int or not isinstance(item.get('caption',''),str) or len(item.get('caption','')) > 150:
                        return fail('Invalid step image',400)
                    incoming.append(item['id'])
                    changes.append((item['id'],'step',step_index,idx,item.get('caption','').strip()))
            if len(incoming) != len(set(incoming)):
                return fail('Duplicate image references',400)
            existing = conn.execute('SELECT id,stored_name FROM knowledge_guide_images WHERE guide_id=?', (gid,)).fetchall()
            db_ids = {row['id'] for row in existing}
            if set(incoming) - db_ids:
                return fail('Image does not belong to this guide',400)
            primary = data.get('primary_id')
            gallery_ids = [i['id'] for i in data['gallery']]
            if primary is not None and primary not in gallery_ids:
                return fail('Primary image must be in the topology gallery',400)
            if gallery_ids and primary is None:
                primary = gallery_ids[0]
            to_remove = [row for row in existing if row['id'] not in incoming]
            # Do not remove any image metadata if QNAP cannot confirm the deletion.
            for row in to_remove:
                try:
                    upload_storage.remove('guide', row['stored_name'], pid=gid)
                except OSError:
                    app.logger.exception('QNAP guide image delete failed: guide=%s image=%s',gid,row['id'])
                    return fail('QNAP unavailable. Image removal was not completed.',503)
                conn.execute('DELETE FROM knowledge_guide_images WHERE guide_id=? AND id=?', (gid,row['id']))
            for iid,kind,step_position,position,caption in changes:
                conn.execute('''UPDATE knowledge_guide_images SET kind=?,step_position=?,position=?,caption=?,is_primary=?
                    WHERE guide_id=? AND id=?''',
                    (kind,step_position,position,caption,int(iid == primary and kind == 'gallery'),gid,iid))
        return jsonify(ok=True)

    @bp.get('/api/guides/<int:gid>/share')
    def get_share(gid):
        with db() as conn:
            guide = get_guide(conn, gid)
            if guide is None or not visible(guide):
                return fail('Guide not found', 404)
            share = share_record(conn, gid)
            token = share_token(gid, share['nonce']) if share and share['enabled'] and guide['status'] == 'Published' else None
            return jsonify(ok=True, enabled=bool(token), token=token,
                           publish_required=guide['status'] != 'Published', can_manage=editable(guide))

    @bp.post('/api/guides/<int:gid>/share')
    def enable_share(gid):
        with db() as conn:
            guide = get_guide(conn, gid)
            if guide is None or not editable(guide):
                return fail('Guide not found', 404)
            if guide['status'] != 'Published':
                return fail('Publish this guide before sharing', 400)
            current = share_record(conn, gid)
            if current and current['enabled']:
                return jsonify(ok=True, token=share_token(gid, current['nonce']), enabled=True)
            nonce = secrets.token_hex(32)
            token = share_token(gid, nonce)
            token_hash = hashlib.sha256(token.encode('ascii')).hexdigest()
            conn.execute('''INSERT INTO knowledge_guide_shares (guide_id,nonce,token_hash,enabled,created_at,updated_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(guide_id) DO UPDATE SET
                nonce=excluded.nonce,token_hash=excluded.token_hash,enabled=1,updated_at=excluded.updated_at''',
                (gid,nonce,token_hash,1,now(),now()))
        return jsonify(ok=True,token=token,enabled=True)

    @bp.delete('/api/guides/<int:gid>/share')
    def disable_share(gid):
        with db() as conn:
            guide = get_guide(conn, gid)
            if guide is None or not editable(guide):
                return fail('Guide not found', 404)
            conn.execute('UPDATE knowledge_guide_shares SET enabled=0,updated_at=? WHERE guide_id=?', (now(),gid))
        return jsonify(ok=True, enabled=False)

    @bp.post('/api/guides/<int:gid>/files')
    def upload_document(gid):
        with db() as conn:
            guide = get_guide(conn, gid)
            if guide is None or not editable(guide):
                return fail('Guide not found', 404)
            if conn.execute('SELECT COUNT(*) FROM knowledge_guide_files WHERE guide_id=?', (gid,)).fetchone()[0] >= MAX_DOCUMENTS:
                return fail('Maximum 15 documentation files per guide', 400)
            file = request.files.get('file')
            if file is None or not file.filename:
                return fail('Select a documentation file', 400)
            original = Path(file.filename.replace('\\', '/')).name[:180]
            suffix = Path(original).suffix.lower()
            if suffix not in ALLOWED_DOCUMENTS:
                return fail('Supported formats: PDF, Office, TXT, CSV, Markdown, ZIP', 400)
            if request.content_length and request.content_length > MAX_DOCUMENT_BYTES + 1024 * 1024:
                return fail('Maximum file size: 25 MB', 413)
            data = file.stream.read(MAX_DOCUMENT_BYTES + 1)
            if not data or len(data) > MAX_DOCUMENT_BYTES:
                return fail('Maximum file size: 25 MB', 413)
            # Randomized stored filename, not client-provided path, prevents traversal and collision.
            stored_name = secrets.token_hex(18) + suffix
            try:
                upload_storage.write('guide', stored_name, data, pid=gid)
            except OSError:
                app.logger.exception('Guide documentation upload failed: guide=%s',gid)
                return fail('QNAP unavailable. File not saved.', 503)
            try:
                cur = conn.execute('''INSERT INTO knowledge_guide_files (guide_id,original_name,stored_name,size_bytes,created_at)
                    VALUES (?,?,?,?,?)''', (gid,original,stored_name,len(data),now()))
            except Exception:
                try:
                    upload_storage.remove('guide', stored_name, pid=gid)
                except OSError:
                    app.logger.exception('Orphaned guide document cleanup failed: guide=%s', gid)
                raise
        return jsonify(ok=True,id=cur.lastrowid), 201

    @bp.delete('/api/guides/<int:gid>/files/<int:file_id>')
    def delete_document(gid,file_id):
        with db() as conn:
            guide = get_guide(conn, gid)
            if guide is None or not editable(guide):
                return fail('Guide not found', 404)
            row = conn.execute('SELECT stored_name FROM knowledge_guide_files WHERE guide_id=? AND id=?', (gid,file_id)).fetchone()
            if row is None:
                return fail('File not found', 404)
            try:
                upload_storage.remove('guide',row['stored_name'],pid=gid)
            except OSError:
                app.logger.exception('Guide file delete failed: guide=%s file=%s',gid,file_id)
                return fail('QNAP unavailable. File not deleted.',503)
            conn.execute('DELETE FROM knowledge_guide_files WHERE id=? AND guide_id=?', (file_id,gid))
        return jsonify(ok=True)

    @bp.get('/api/guides/<int:gid>/files/<int:file_id>')
    def download_document(gid,file_id):
        with db() as conn:
            guide = get_guide(conn, gid)
            if guide is None or not visible(guide):
                return fail('Guide not found',404)
            row = conn.execute('SELECT stored_name,original_name FROM knowledge_guide_files WHERE guide_id=? AND id=?', (gid,file_id)).fetchone()
        if row is None:
            return fail('File not found', 404)
        return read_media(gid,row,True)

    @bp.get('/g/<token>')
    def public_guide_page(token):
        with db() as conn:
            if shared_guide(conn,token) is None:
                return 'Guide link is invalid or has been disabled.',404
        response = send_from_directory(page_dir, 'public_guide.html')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; connect-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        return response

    @bp.get('/api/public/g/<token>')
    def public_guide_data(token):
        with db() as conn:
            guide = shared_guide(conn,token)
            if guide is None:
                return fail('Guide link is invalid or disabled',404)
            gid=guide['id']
            steps=[dict(row) for row in conn.execute('''SELECT position,title,instruction,command,reference_url FROM knowledge_guide_steps
                WHERE guide_id=? ORDER BY position,id''', (gid,)).fetchall()]
            images=[dict(row) for row in conn.execute('''SELECT id,kind,step_position,position,caption,original_name,is_primary
                FROM knowledge_guide_images WHERE guide_id=? ORDER BY kind,position,id''', (gid,)).fetchall()]
            documents=guide_files(conn,gid)
        response=jsonify(ok=True,guide={'title':guide['title'],'description':guide['description'],
           'category':guide['category'],'updated_at':guide['updated_at'],
           'content_format':guide['content_format'],'content_markdown':guide['content_markdown']},
           steps=steps, images=images, files=documents, videos=_guide_video_rows(gid))
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='no-referrer'
        return response

    @bp.get('/api/public/g/<token>/media/<int:image_id>')
    def public_guide_media(token,image_id):
        with db() as conn:
            guide=shared_guide(conn,token)
            if guide is None:
                return fail('Guide not found',404)
            item=conn.execute('''SELECT stored_name,mime_type,original_name FROM knowledge_guide_images
                WHERE guide_id=? AND id=?''', (guide['id'],image_id)).fetchone()
        if item is None:
            return fail('Image not found',404)
        return read_media(guide['id'],item)

    @bp.get('/api/public/g/<token>/files/<int:file_id>')
    def public_guide_document(token,file_id):
        with db() as conn:
            guide=shared_guide(conn,token)
            if guide is None:
                return fail('Guide not found',404)
            item=conn.execute('''SELECT stored_name,original_name FROM knowledge_guide_files
                WHERE guide_id=? AND id=?''', (guide['id'],file_id)).fetchone()
        if item is None:
            return fail('File not found',404)
        return read_media(guide['id'],item,True)

    from guide_videos import register_guide_videos
    video_rows = register_guide_videos(bp, app, db, upload_storage,
        get_guide=get_guide, editable=editable, visible=visible,
        shared_guide=shared_guide, now=now)

    app.register_blueprint(bp)
