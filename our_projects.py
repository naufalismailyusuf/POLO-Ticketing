"""Collaborative project workspace; tables are isolated from ticketing tables."""
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from flask import Blueprint, g, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename


def register_our_projects(app, db, page_dir, upload_storage=None):
    bp = Blueprint('our_projects', __name__)

    def now():
        return datetime.now().astimezone().isoformat(timespec='seconds')

    # Keep uploads outside /static; files are served only after session authorization.
    if upload_storage is None:
        raise RuntimeError('Project requires QNAP dashboard upload storage')
    # For legacy posts the stored_name stays unchanged; only bytes move to NAS.
    allowed_types = {
        '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.webp': 'image/webp', '.gif': 'image/gif',
        '.pdf': 'application/pdf', '.txt': 'text/plain', '.csv': 'text/csv',
        '.doc': 'application/msword',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.xls': 'application/vnd.ms-excel',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.ppt': 'application/vnd.ms-powerpoint',
        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        '.zip': 'application/zip',
    }
    max_attachment_size = 8 * 1024 * 1024  # 8 MB per attachment
    max_attachments = 5

    def image_matches_signature(ext, blob):
        if ext == '.png': return blob.startswith(b'\x89PNG\r\n\x1a\n')
        if ext in ('.jpg', '.jpeg'): return blob.startswith(b'\xff\xd8\xff')
        if ext == '.gif': return blob.startswith((b'GIF87a', b'GIF89a'))
        if ext == '.webp': return len(blob) >= 12 and blob[:4] == b'RIFF' and blob[8:12] == b'WEBP'
        return True

    with db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS team_projects (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Idea',
          priority TEXT NOT NULL DEFAULT 'Medium', owner TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS team_project_comments (
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
          author TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(project_id) REFERENCES team_projects(id));
        CREATE TABLE IF NOT EXISTS team_project_tasks (
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
          title TEXT NOT NULL, assignee TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES team_projects(id));
        CREATE INDEX IF NOT EXISTS idx_team_comments_project ON team_project_comments(project_id);
        CREATE INDEX IF NOT EXISTS idx_team_tasks_project ON team_project_tasks(project_id);
        ''')

    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS team_project_stages (
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
          title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          assignee TEXT NOT NULL DEFAULT '', due_date TEXT NOT NULL DEFAULT '',
          position INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'Pending',
          created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_team_stages_project ON team_project_stages(project_id,position);
        CREATE TABLE IF NOT EXISTS team_project_activity (
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
          author TEXT NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        cols={r['name'] for r in conn.execute('PRAGMA table_info(team_project_tasks)')}
        if 'stage_id' not in cols:
            conn.execute('ALTER TABLE team_project_tasks ADD COLUMN stage_id INTEGER')

    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS team_project_members (
          project_id INTEGER NOT NULL, username TEXT NOT NULL,
          PRIMARY KEY(project_id, username),
          FOREIGN KEY(project_id) REFERENCES team_projects(id));
        """)

    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS team_project_comment_attachments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL,
          comment_id INTEGER NOT NULL,
          original_name TEXT NOT NULL,
          stored_name TEXT NOT NULL UNIQUE,
          content_type TEXT NOT NULL,
          size_bytes INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(project_id) REFERENCES team_projects(id),
          FOREIGN KEY(comment_id) REFERENCES team_project_comments(id));
        CREATE INDEX IF NOT EXISTS idx_project_comment_attachments
          ON team_project_comment_attachments(project_id, comment_id);
        """)

    # Newly created projects use immutable readable NAS folder names. Existing
    # projects keep storage_folder=NULL and continue reading numeric folders
    # until the optional verified migration is run.
    with db() as conn:
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(team_projects)')}
        if 'storage_folder' not in columns:
            conn.execute('ALTER TABLE team_projects ADD COLUMN storage_folder TEXT')
        # Clear History hides earlier events in the UI; keep audit records intact.
        if 'activity_hidden_through_id' not in columns:
            conn.execute('ALTER TABLE team_projects ADD COLUMN activity_hidden_through_id INTEGER NOT NULL DEFAULT 0')
        # A deleted project remains in SQLite and QNAP for disaster recovery.
        # Queries and mutations reject it using project_or_404().
        if 'deleted_at' not in columns:
            conn.execute('ALTER TABLE team_projects ADD COLUMN deleted_at TEXT')
        if 'deleted_by' not in columns:
            conn.execute('ALTER TABLE team_projects ADD COLUMN deleted_by TEXT')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_team_projects_deleted_at ON team_projects(deleted_at)')

    # Non-destructive schema upgrades: older project-wide comments retain stage_id=NULL.
    with db() as conn:
        comment_cols = {row['name'] for row in conn.execute('PRAGMA table_info(team_project_comments)')}
        if 'stage_id' not in comment_cols:
            conn.execute('ALTER TABLE team_project_comments ADD COLUMN stage_id INTEGER')
        task_cols = {row['name'] for row in conn.execute('PRAGMA table_info(team_project_tasks)')}
        if 'due_date' not in task_cols:
            conn.execute("ALTER TABLE team_project_tasks ADD COLUMN due_date TEXT NOT NULL DEFAULT ''")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_team_comments_stage '
                     'ON team_project_comments(project_id, stage_id, id)')

    @bp.get('/api/our-projects/support-users')
    def project_support_users():
        with db() as conn:
            users=conn.execute("SELECT username,display FROM users WHERE role='support' ORDER BY display").fetchall()
        return jsonify(ok=True,users=[dict(u) for u in users])

    def log(conn,pid,action):
        conn.execute('INSERT INTO team_project_activity(project_id,author,action,created_at) VALUES(?,?,?,?)',
                     (pid,g.user['username'],action,now()))

    def can_manage(p):
        # Compare account names case-insensitively (display casing may differ from stored username).
        return g.user['role']=='admin' or str(p['owner']).casefold()==str(g.user['username']).casefold()

    def can_collaborate(conn, p):
        # Assignment enables project-status changes and roadmap ordering only.
        # Creation, deletion and editing stages/tasks remain restricted to can_manage().
        if can_manage(p):
            return True
        return conn.execute(
            'SELECT 1 FROM team_project_members WHERE project_id=? AND username=? COLLATE NOCASE LIMIT 1',
            (p['id'], g.user['username'])
        ).fetchone() is not None

    def rowdict(r):
        return dict(r) if r else None

    def project_or_404(conn, pid):
        p=conn.execute('SELECT * FROM team_projects WHERE id=? AND deleted_at IS NULL',(pid,)).fetchone()
        return p

    @bp.get('/our-project')
    def page():
        return send_from_directory(page_dir, 'our_project.html')

    @bp.get('/our-project/<int:pid>')
    def detail_page(pid):
        return send_from_directory(page_dir, 'our_project_detail.html')

    @bp.get('/api/our-projects')
    def listing():
        with db() as conn:
            rows=conn.execute('''SELECT p.*, (SELECT COUNT(*) FROM team_project_tasks t WHERE t.project_id=p.id) task_count,
                (SELECT COUNT(*) FROM team_project_tasks t WHERE t.project_id=p.id AND t.done=1) task_done,
                (SELECT COUNT(*) FROM team_project_stages s WHERE s.project_id=p.id) stage_count,
                (SELECT COUNT(*) FROM team_project_stages s WHERE s.project_id=p.id AND s.status='Completed') stage_done,
                (SELECT COUNT(*) FROM team_project_comments c WHERE c.project_id=p.id) comment_count
                FROM team_projects p WHERE p.deleted_at IS NULL
                ORDER BY p.updated_at DESC''').fetchall()
        projects=[]
        for row in rows:
            item=dict(row)
            item['can_delete']=can_manage(row)
            projects.append(item)
        return jsonify(ok=True, projects=projects, is_admin=g.user['role']=='admin')

    @bp.post('/api/our-projects')
    def create():
        data=request.get_json(silent=True) or {}
        title=str(data.get('title','')).strip()[:120]
        desc=str(data.get('description','')).strip()[:4000]
        priority=str(data.get('priority','Medium'))
        if not title:return jsonify(ok=False,error='Project title is required'),400
        if priority not in ('Low','Medium','High','Critical'):return jsonify(ok=False,error='Invalid priority'),400
        members=data.get('members',[])
        if not isinstance(members,list) or len(members)>100 or any(not isinstance(u,str) for u in members):
            return jsonify(ok=False,error='Invalid support selection'),400
        members=list(dict.fromkeys(members))
        stamp=now()
        project_path=None
        try:
            with db() as conn:
                valid={r['username'] for r in conn.execute("SELECT username FROM users WHERE role='support'")}
                if any(u not in valid for u in members):
                    return jsonify(ok=False,error='Unknown support account'),400
                cur=conn.execute('INSERT INTO team_projects(title,description,priority,owner,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                                 (title,desc,priority,g.user['username'],stamp,stamp))
                pid=cur.lastrowid
                # QNAP must confirm directory creation before SQLite commits.
                # A failed SMB write rolls back the entire new project.
                folder_name,project_path=upload_storage.ensure_project_folder(title,pid)
                conn.execute('UPDATE team_projects SET storage_folder=? WHERE id=?',(folder_name,pid))
                conn.executemany('INSERT INTO team_project_members(project_id,username) VALUES(?,?)',[(pid,u) for u in members])
        except OSError:
            if project_path is not None:
                try: project_path.rmdir()  # only if still empty
                except OSError: pass
            app.logger.exception('QNAP unavailable while creating project folder')
            return jsonify(ok=False,error='Unable to create project folder on QNAP. Project was not saved.'),503
        except Exception:
            if project_path is not None:
                try: project_path.rmdir()
                except OSError: pass
            raise
        return jsonify(ok=True,id=pid),201

    @bp.get('/api/our-projects/<int:pid>')
    def detail(pid):
        with db() as conn:
            p=project_or_404(conn,pid)
            if not p:return jsonify(ok=False,error='Project not found'),404
            comments=conn.execute('SELECT * FROM team_project_comments WHERE project_id=? ORDER BY id',(pid,)).fetchall()
            attachment_rows=conn.execute(
                'SELECT id,comment_id,original_name,content_type,size_bytes FROM team_project_comment_attachments '
                'WHERE project_id=? ORDER BY id',(pid,)).fetchall()
            comment_files={}
            for attachment in attachment_rows:
                a=dict(attachment)
                a['url']=f'/api/our-projects/{pid}/comments/attachments/{a["id"]}'
                a['is_image']=a['content_type'].startswith('image/')
                comment_files.setdefault(a['comment_id'],[]).append(a)
            comments_data=[]
            for comment in comments:
                entry=dict(comment)
                entry['attachments']=comment_files.get(comment['id'],[])
                entry['can_delete']=(can_manage(p) or
                                     str(comment['author']).casefold()==str(g.user['username']).casefold())
                comments_data.append(entry)
            tasks=conn.execute('SELECT * FROM team_project_tasks WHERE project_id=? ORDER BY id',(pid,)).fetchall()
            stages=conn.execute('SELECT * FROM team_project_stages WHERE project_id=? ORDER BY position,id',(pid,)).fetchall()
            activity=conn.execute(
                'SELECT * FROM team_project_activity WHERE project_id=? AND id>? ORDER BY id DESC LIMIT 80',
                (pid, p['activity_hidden_through_id'] or 0)).fetchall()
            members=conn.execute('SELECT username FROM team_project_members WHERE project_id=? ORDER BY username',(pid,)).fetchall()
            can_manage_project=can_manage(p)
            can_collaborate_project=can_collaborate(conn,p)
        return jsonify(ok=True,project=dict(p),comments=comments_data,tasks=[dict(x) for x in tasks],stages=[dict(x) for x in stages],activity=[dict(x) for x in activity],members=[x['username'] for x in members],is_admin=g.user['role']=='admin',can_manage=can_manage_project,can_clear_activity=can_manage_project,can_update_status=can_collaborate_project,can_reorder_stages=can_collaborate_project,can_delete=can_manage_project)

    @bp.delete('/api/our-projects/<int:pid>')
    def delete_project(pid):
        # Reversible soft deletion. Keep tasks, discussions, audit events and
        # QNAP evidence intact; hidden projects are unavailable to regular APIs.
        data=request.get_json(silent=True)
        if not isinstance(data,dict):
            return jsonify(ok=False,error='Confirmation is required'),400
        with db() as conn:
            p=project_or_404(conn,pid)
            if not p:
                return jsonify(ok=False,error='Project not found'),404
            if not can_manage(p):
                return jsonify(ok=False,error='Only Admin or the project creator can delete a project'),403
            if data.get('title')!=p['title']:
                return jsonify(ok=False,error='Project title does not match. Nothing deleted.'),400
            timestamp=now()
            conn.execute('UPDATE team_projects SET deleted_at=?,deleted_by=?,updated_at=? '
                         'WHERE id=? AND deleted_at IS NULL',
                         (timestamp,g.user['username'],timestamp,pid))
            log(conn,pid,'Deleted project (recoverable): '+p['title'])
        app.logger.warning('Project soft-deleted id=%s actor=%s',pid,g.user['username'])
        return jsonify(ok=True,deleted=True,id=pid)

    @bp.post('/api/our-projects/<int:pid>/activity/clear')
    def clear_activity(pid):
        # Only an administrator or this project's creator can clear visible
        # history. Do not hard-delete the audit trail from SQLite.
        with db() as conn:
            project=project_or_404(conn,pid)
            if not project:
                return jsonify(ok=False,error='Project not found'),404
            if not can_manage(project):
                return jsonify(ok=False,error='Only Admin or the project creator can clear activity history'),403
            latest_id=conn.execute(
                'SELECT COALESCE(MAX(id),0) FROM team_project_activity WHERE project_id=?',
                (pid,)).fetchone()[0]
            previous=project['activity_hidden_through_id'] or 0
            conn.execute(
                'UPDATE team_projects SET activity_hidden_through_id=? WHERE id=?',
                (max(previous,latest_id),pid))
        app.logger.info('Project activity history hidden project_id=%s by=%s through_id=%s',
                        pid,g.user['username'],latest_id)
        return jsonify(ok=True,hidden_through_id=max(previous,latest_id))

    @bp.patch('/api/our-projects/<int:pid>')
    def change(pid):
        data=request.get_json(silent=True) or {}
        status=data.get('status')
        if status not in ('Idea','Discussion','Approved','In Progress','Completed'):
            return jsonify(ok=False,error='Invalid status'),400
        with db() as conn:
            p=project_or_404(conn,pid)
            if not p:return jsonify(ok=False,error='Project not found'),404
            if not can_collaborate(conn,p):
                return jsonify(ok=False,error='Only the owner, Admin or assigned Support may update project status'),403
            conn.execute('UPDATE team_projects SET status=?,updated_at=? WHERE id=?',(status,now(),pid))
            log(conn,pid,'Changed project status to '+status)
        return jsonify(ok=True)

    @bp.post('/api/our-projects/<int:pid>/comments')
    def comment(pid):
        # Backward compatible with existing JSON-only comments.
        is_multipart=request.mimetype=='multipart/form-data'
        if is_multipart:
            body=str(request.form.get('body','')).strip()
            raw_stage_id=request.form.get('stage_id')
            scope=request.form.get('scope')
            uploads=[f for f in request.files.getlist('attachments') if f and f.filename]
        else:
            comment_data=request.get_json(silent=True) or {}
            body=str(comment_data.get('body','')).strip()
            raw_stage_id=comment_data.get('stage_id')
            scope=comment_data.get('scope')
            uploads=[]
        # Older project-wide comments and API clients remain compatible. New clients
        # must declare their destination: missing stage_id can never silently
        # redirect a requested stage comment/attachment to General Discussion.
        if scope not in (None,'stage','general'):
            return jsonify(ok=False,error='Invalid discussion scope'),400
        if raw_stage_id is None or raw_stage_id == '':
            stage_id=None
        else:
            try:
                stage_id=int(raw_stage_id)
                if stage_id<=0:raise ValueError()
            except (TypeError,ValueError):
                return jsonify(ok=False,error='Invalid stage'),400
        if scope=='stage' and stage_id is None:
            return jsonify(ok=False,error='Select a stage before posting'),400
        if scope=='general' and stage_id is not None:
            return jsonify(ok=False,error='General comments cannot have a stage'),400
        if len(body)>3000:
            return jsonify(ok=False,error='Comment exceeds 3000 characters'),400
        if len(uploads)>max_attachments:
            return jsonify(ok=False,error='Maximum 5 attachments per comment'),400
        if not body and not uploads:
            return jsonify(ok=False,error='Write a comment or attach a file'),400
        if is_multipart and request.content_length and request.content_length>(max_attachments*max_attachment_size+1024*1024):
            return jsonify(ok=False,error='Attachments are too large'),413
        saved=[]
        try:
            # Validate all uploads before inserting the comment or touching the database.
            parsed=[]
            for upload in uploads:
                safe_name=secure_filename(upload.filename or '')
                if not safe_name:
                    return jsonify(ok=False,error='Invalid attachment filename'),400
                safe_name=safe_name[:140]
                ext=Path(safe_name).suffix.lower()
                content_type=allowed_types.get(ext)
                if not content_type:
                    return jsonify(ok=False,error='File type not allowed: '+ext),400
                blob=upload.stream.read(max_attachment_size+1)
                if not blob or len(blob)>max_attachment_size:
                    return jsonify(ok=False,error='Each attachment must be 1 byte to 8 MB'),400
                if content_type.startswith('image/') and not image_matches_signature(ext,blob):
                    return jsonify(ok=False,error='Invalid image file: '+safe_name),400
                parsed.append((safe_name,ext,content_type,blob))
            with db() as conn:
                project=project_or_404(conn,pid)
                if not project:
                    return jsonify(ok=False,error='Project not found'),404
                project_folder=project['storage_folder']
                if stage_id is not None and not conn.execute(
                        'SELECT 1 FROM team_project_stages WHERE project_id=? AND id=?',
                        (pid,stage_id)).fetchone():
                    return jsonify(ok=False,error='Stage not found'),404
                cur=conn.execute(
                    'INSERT INTO team_project_comments(project_id,author,body,created_at,stage_id) VALUES(?,?,?,?,?)',
                    (pid,g.user['username'],body,now(),stage_id))
                cid=cur.lastrowid
                if parsed:
                    for safe_name,ext,content_type,blob in parsed:
                        storage_name=uuid4().hex+ext
                        target=upload_storage.write('project',storage_name,blob,pid=pid,project_folder=project_folder)
                        saved.append(target)
                        conn.execute("""INSERT INTO team_project_comment_attachments
                            (project_id,comment_id,original_name,stored_name,content_type,size_bytes,created_at)
                            VALUES(?,?,?,?,?,?,?)""",
                            (pid,cid,safe_name,storage_name,content_type,len(blob),now()))
                conn.execute('UPDATE team_projects SET updated_at=? WHERE id=?',(now(),pid))
                log(conn,pid,'Added a '+('stage' if stage_id is not None else 'project-wide')+' comment'+(' with attachments' if parsed else ''))
        except OSError:
            for file in saved:
                file.unlink(missing_ok=True)
            app.logger.exception('Failed to store project discussion attachment')
            return jsonify(ok=False,error='Unable to save attachment'),500
        except Exception:
            for file in saved:
                file.unlink(missing_ok=True)
            raise
        return jsonify(ok=True,id=cid,stage_id=stage_id,
                       scope='stage' if stage_id is not None else 'general'),201

    @bp.post('/api/our-projects/<int:pid>/comments/bulk-delete')
    def bulk_delete_comments(pid):
        """Atomically delete authorized comments in exactly one discussion scope."""
        data=request.get_json(silent=True) or {}
        ids=data.get('comment_ids')
        scope=data.get('scope')
        raw_stage=data.get('stage_id')
        if not isinstance(ids,list) or not 1<=len(ids)<=200 or any(
                type(value) is not int or value<=0 for value in ids) or len(set(ids))!=len(ids):
            return jsonify(ok=False,error='Select 1–200 unique comments to delete'),400
        if scope not in ('stage','general'):
            return jsonify(ok=False,error='Invalid discussion scope'),400
        if scope=='general':
            if raw_stage is not None:
                return jsonify(ok=False,error='General scope cannot include a stage'),400
            stage_id=None
        else:
            if type(raw_stage) is not int or raw_stage<=0:
                return jsonify(ok=False,error='Select a valid stage'),400
            stage_id=raw_stage
        file_names=[]
        project_folder=None
        with db() as conn:
            project=project_or_404(conn,pid)
            if not project:
                return jsonify(ok=False,error='Project not found'),404
            project_folder=project['storage_folder']
            if stage_id is not None and not conn.execute(
                    'SELECT 1 FROM team_project_stages WHERE project_id=? AND id=?',
                    (pid,stage_id)).fetchone():
                return jsonify(ok=False,error='Stage not found'),404
            placeholders=','.join('?' for _ in ids)
            selected_rows=conn.execute(
                'SELECT id,author,stage_id FROM team_project_comments '
                'WHERE project_id=? AND id IN ('+placeholders+')',
                [pid,*ids]).fetchall()
            # All-or-nothing authorization and stage isolation before any DELETE.
            if len(selected_rows)!=len(ids):
                return jsonify(ok=False,error='One or more comments no longer exist'),409
            if any((row['stage_id'] is not None if scope=='general' else
                    row['stage_id']!=stage_id) for row in selected_rows):
                return jsonify(ok=False,error='Selected comments belong to another discussion'),409
            if not can_manage(project) and any(
                    str(row['author']).casefold()!=str(g.user['username']).casefold()
                    for row in selected_rows):
                return jsonify(ok=False,error='You can delete only your own comments'),403
            file_names=[r['stored_name'] for r in conn.execute(
                'SELECT stored_name FROM team_project_comment_attachments '
                'WHERE project_id=? AND comment_id IN ('+placeholders+')',
                [pid,*ids]).fetchall()]
            conn.execute('DELETE FROM team_project_comment_attachments '
                         'WHERE project_id=? AND comment_id IN ('+placeholders+')',
                         [pid,*ids])
            conn.execute('DELETE FROM team_project_comments '
                         'WHERE project_id=? AND id IN ('+placeholders+')',
                         [pid,*ids])
            conn.execute('UPDATE team_projects SET updated_at=? WHERE id=?',(now(),pid))
            log(conn,pid,'Deleted '+str(len(ids))+' '+scope+' discussion comments')
        # Delete files after the DB commit, same as single-comment deletion.
        # Stale private orphan bytes are safer than dangling database records.
        for stored_name in file_names:
            if Path(stored_name).name!=stored_name:
                app.logger.warning('Skipped unsafe attachment path')
                continue
            try:
                upload_storage.remove('project',stored_name,pid=pid,project_folder=project_folder)
            except OSError:
                app.logger.warning('Unable to remove orphan comment attachment %s',stored_name)
        return jsonify(ok=True,deleted=len(ids),scope=scope,stage_id=stage_id)

    @bp.delete('/api/our-projects/<int:pid>/comments/<int:cid>')
    def delete_comment(pid,cid):
        # A commenter can delete their own comment. The project owner and Admin
        # can moderate either Stage or General Discussion. Never trust UI checks.
        file_names=[]
        project_folder=None
        with db() as conn:
            p=project_or_404(conn,pid)
            if not p:
                return jsonify(ok=False,error='Project not found'),404
            project_folder=p['storage_folder']
            comment_row=conn.execute(
                'SELECT id,author,stage_id FROM team_project_comments '
                'WHERE project_id=? AND id=?',(pid,cid)).fetchone()
            if not comment_row:
                return jsonify(ok=False,error='Comment not found'),404
            if not (can_manage(p) or
                    str(comment_row['author']).casefold()==str(g.user['username']).casefold()):
                return jsonify(ok=False,error='Not allowed to delete this comment'),403
            file_names=[r['stored_name'] for r in conn.execute(
                'SELECT stored_name FROM team_project_comment_attachments '
                'WHERE project_id=? AND comment_id=?',(pid,cid)).fetchall()]
            conn.execute('DELETE FROM team_project_comment_attachments '
                         'WHERE project_id=? AND comment_id=?',(pid,cid))
            conn.execute('DELETE FROM team_project_comments WHERE project_id=? AND id=?',(pid,cid))
            conn.execute('UPDATE team_projects SET updated_at=? WHERE id=?',(now(),pid))
            log(conn,pid,'Deleted a '+('stage' if comment_row['stage_id'] is not None
                                      else 'general')+' discussion comment')
        # Remove attachment bytes only after database commit. Failure to unlink
        # leaves a private orphan file, never a broken live comment reference.
        for stored_name in file_names:
            if Path(stored_name).name!=stored_name:
                app.logger.warning('Skipped unsafe attachment path')
                continue
            try:
                upload_storage.remove('project',stored_name,pid=pid,project_folder=project_folder)
            except OSError:
                app.logger.warning('Unable to remove orphan comment attachment %s',stored_name)
        return jsonify(ok=True,stage_id=comment_row['stage_id'])

    @bp.get('/api/our-projects/<int:pid>/comments/attachments/<int:aid>')
    def comment_attachment(pid,aid):
        with db() as conn:
            project=project_or_404(conn,pid)
            if not project:
                return jsonify(ok=False,error='Project not found'),404
            project_folder=project['storage_folder']
            attachment=conn.execute("""SELECT stored_name,original_name,content_type
                FROM team_project_comment_attachments WHERE project_id=? AND id=?""",(pid,aid)).fetchone()
        if not attachment:
            return jsonify(ok=False,error='Attachment not found'),404
        image=attachment['content_type'].startswith('image/')
        target=upload_storage.resolve('project',attachment['stored_name'],pid=pid,project_folder=project_folder)
        if target is None:
            return jsonify(ok=False,error='Attachment unavailable'),404
        response=send_file(
            target, as_attachment=not image,
            download_name=attachment['original_name'],
            mimetype=attachment['content_type'], conditional=True)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Cache-Control']='private, max-age=300'
        if image:
            response.headers['Content-Security-Policy']="default-src 'none'; sandbox"
        return response

    @bp.post('/api/our-projects/<int:pid>/tasks/bulk-delete')
    def bulk_delete_project_tasks(pid):
        """All-or-nothing checklist deletion, scoped to one existing stage."""
        data=request.get_json(silent=True) or {}
        task_ids=data.get('task_ids')
        stage_id=data.get('stage_id')
        if (not isinstance(task_ids,list) or not 1 <= len(task_ids) <= 200 or
                any(type(tid) is not int or tid <= 0 for tid in task_ids) or
                len(task_ids)!=len(set(task_ids))):
            return jsonify(ok=False,error='Select 1–200 unique checklist tasks'),400
        if type(stage_id) is not int or stage_id <= 0:
            return jsonify(ok=False,error='Select a valid stage'),400
        with db() as conn:
            project=project_or_404(conn,pid)
            if not project:
                return jsonify(ok=False,error='Project not found'),404
            if not can_manage(project):
                return jsonify(ok=False,error='Not authorized to delete checklist tasks'),403
            if not conn.execute('SELECT 1 FROM team_project_stages WHERE project_id=? AND id=?',
                                (pid,stage_id)).fetchone():
                return jsonify(ok=False,error='Stage not found'),404
            placeholders=','.join('?' for _ in task_ids)
            tasks=conn.execute('SELECT id,stage_id FROM team_project_tasks '
                               'WHERE project_id=? AND id IN ('+placeholders+')',
                               [pid,*task_ids]).fetchall()
            # Validate the complete batch before executing any DELETE.
            if len(tasks)!=len(task_ids):
                return jsonify(ok=False,error='Some tasks no longer exist. Refresh the checklist'),409
            if any(task['stage_id'] != stage_id for task in tasks):
                return jsonify(ok=False,error='Tasks must belong to the selected stage'),409
            conn.execute('DELETE FROM team_project_tasks '
                         'WHERE project_id=? AND stage_id=? AND id IN ('+placeholders+')',
                         [pid,stage_id,*task_ids])
            conn.execute('UPDATE team_projects SET updated_at=? WHERE id=?',(now(),pid))
            log(conn,pid,'Deleted '+str(len(task_ids))+' checklist tasks from stage '+str(stage_id))
        return jsonify(ok=True,deleted=len(task_ids),stage_id=stage_id)

    @bp.post('/api/our-projects/<int:pid>/tasks')
    def task(pid):
        data=request.get_json(silent=True) or {}
        title=str(data.get('title','')).strip()[:180]
        assignee=str(data.get('assignee','')).strip()[:48]
        due_date=str(data.get('due_date','')).strip()
        if due_date:
            try:
                if len(due_date)!=10 or datetime.strptime(due_date,'%Y-%m-%d').strftime('%Y-%m-%d')!=due_date:
                    raise ValueError()
            except ValueError:
                return jsonify(ok=False,error='Invalid task due date'),400
        if not title:return jsonify(ok=False,error='Task title is required'),400
        stage_id=data.get('stage_id')
        if stage_id is not None:
            try:stage_id=int(stage_id)
            except (TypeError,ValueError):return jsonify(ok=False,error='Invalid stage'),400
        with db() as conn:
            p=project_or_404(conn,pid)
            if not p:return jsonify(ok=False,error='Project not found'),404
            if g.user['role']!='admin' and p['owner']!=g.user['username']:
                return jsonify(ok=False,error='Only project owner or admin can add tasks'),403
            if stage_id is not None and not conn.execute('SELECT 1 FROM team_project_stages WHERE id=? AND project_id=?',(stage_id,pid)).fetchone():
                return jsonify(ok=False,error='Stage not found'),404
            conn.execute('INSERT INTO team_project_tasks(project_id,title,assignee,created_at,stage_id,due_date) VALUES(?,?,?,?,?,?)',(pid,title,assignee,now(),stage_id,due_date))
            log(conn,pid,'Added task: '+title)
            conn.execute('UPDATE team_projects SET updated_at=? WHERE id=?',(now(),pid))
        return jsonify(ok=True),201

    @bp.patch('/api/our-projects/<int:pid>/tasks/<int:tid>')
    def toggle_task(pid,tid):
        data=request.get_json(silent=True) or {}
        if not isinstance(data.get('done'),bool):return jsonify(ok=False,error='done must be boolean'),400
        with db() as conn:
            p=project_or_404(conn,pid)
            if not p:return jsonify(ok=False,error='Project not found'),404
            task=conn.execute('SELECT * FROM team_project_tasks WHERE id=? AND project_id=?',(tid,pid)).fetchone()
            if not task:return jsonify(ok=False,error='Task not found'),404
            if g.user['role']!='admin' and p['owner']!=g.user['username'] and task['assignee']!=g.user['username']:
                return jsonify(ok=False,error='Not authorized to update task'),403
            conn.execute('UPDATE team_project_tasks SET done=? WHERE id=?',(int(data['done']),tid))
            log(conn,pid,('Completed' if data['done'] else 'Reopened')+' task: '+task['title'])
            conn.execute('UPDATE team_projects SET updated_at=? WHERE id=?',(now(),pid))
        return jsonify(ok=True)


    @bp.delete('/api/our-projects/<int:pid>/tasks/<int:tid>')
    def delete_project_task(pid,tid):
        with db() as conn:
            p=project_or_404(conn,pid)
            if not p:return jsonify(ok=False,error='Project not found'),404
            if not can_manage(p):return jsonify(ok=False,error='Not authorized'),403
            task=conn.execute('SELECT title FROM team_project_tasks WHERE id=? AND project_id=?',(tid,pid)).fetchone()
            if not task:return jsonify(ok=False,error='Task not found'),404
            conn.execute('DELETE FROM team_project_tasks WHERE id=? AND project_id=?',(tid,pid))
            log(conn,pid,'Deleted task: '+task['title'])
            conn.execute('UPDATE team_projects SET updated_at=? WHERE id=?',(now(),pid))
        return jsonify(ok=True)


    STAGE_STATUSES=('Pending','In Progress','Completed')

    @bp.post('/api/our-projects/<int:pid>/stages')
    def create_stage(pid):
        d=request.get_json(silent=True) or {}
        title=str(d.get('title','')).strip()[:120]
        if not title:return jsonify(ok=False,error='Stage title is required'),400
        with db() as conn:
            p=project_or_404(conn,pid)
            if not p:return jsonify(ok=False,error='Project not found'),404
            if not can_manage(p):return jsonify(ok=False,error='Not authorized'),403
            pos=conn.execute('SELECT COALESCE(MAX(position),0)+1 FROM team_project_stages WHERE project_id=?',(pid,)).fetchone()[0]
            conn.execute('INSERT INTO team_project_stages(project_id,title,description,assignee,due_date,position,created_at) VALUES(?,?,?,?,?,?,?)',
                (pid,title,str(d.get('description',''))[:2000],str(d.get('assignee',''))[:48],str(d.get('due_date',''))[:10],pos,now()))
            log(conn,pid,'Created stage: '+title)
            conn.execute('UPDATE team_projects SET updated_at=? WHERE id=?',(now(),pid))
        return jsonify(ok=True),201

    @bp.patch('/api/our-projects/<int:pid>/stages/<int:sid>')
    def update_stage(pid,sid):
        d=request.get_json(silent=True) or {}
        allowed={'title':120,'description':2000,'assignee':48,'due_date':10,'status':24}
        fields={k:str(v).strip()[:n] for k,n in allowed.items() if (v:=d.get(k)) is not None}
        if not fields:return jsonify(ok=False,error='No changes'),400
        if 'status' in fields and fields['status'] not in STAGE_STATUSES:return jsonify(ok=False,error='Invalid status'),400
        if 'title' in fields and not fields['title']:return jsonify(ok=False,error='Title required'),400
        with db() as conn:
            p=project_or_404(conn,pid)
            if not p:return jsonify(ok=False,error='Project not found'),404
            if not can_manage(p):return jsonify(ok=False,error='Not authorized'),403
            st=conn.execute('SELECT * FROM team_project_stages WHERE id=? AND project_id=?',(sid,pid)).fetchone()
            if not st:return jsonify(ok=False,error='Stage not found'),404
            conn.execute('UPDATE team_project_stages SET '+','.join(k+'=?' for k in fields)+' WHERE id=? AND project_id=?',(*fields.values(),sid,pid))
            log(conn,pid,'Updated stage: '+st['title'])
            conn.execute('UPDATE team_projects SET updated_at=? WHERE id=?',(now(),pid))
        return jsonify(ok=True)

    @bp.delete('/api/our-projects/<int:pid>/stages/<int:sid>')
    def delete_stage(pid,sid):
        with db() as conn:
            p=project_or_404(conn,pid)
            if not p:return jsonify(ok=False,error='Project not found'),404
            if not can_manage(p):return jsonify(ok=False,error='Not authorized'),403
            st=conn.execute('SELECT * FROM team_project_stages WHERE id=? AND project_id=?',(sid,pid)).fetchone()
            if not st:return jsonify(ok=False,error='Stage not found'),404
            if conn.execute('SELECT 1 FROM team_project_tasks WHERE stage_id=? AND project_id=? LIMIT 1',(sid,pid)).fetchone():
                return jsonify(ok=False,error='Remove or reassign stage tasks first'),409
            if conn.execute('SELECT 1 FROM team_project_comments WHERE stage_id=? AND project_id=? LIMIT 1',(sid,pid)).fetchone():
                return jsonify(ok=False,error='This stage has discussion comments. Keep the stage to preserve its history'),409
            conn.execute('DELETE FROM team_project_stages WHERE id=? AND project_id=?',(sid,pid))
            log(conn,pid,'Deleted stage: '+st['title'])
        return jsonify(ok=True)

    @bp.put('/api/our-projects/<int:pid>/stages/order')
    def reorder_stages(pid):
        d=request.get_json(silent=True) or {}
        ids=d.get('ids')
        if not isinstance(ids,list) or not all(type(x)==int for x in ids) or len(ids)!=len(set(ids)):
            return jsonify(ok=False,error='Invalid stage order'),400
        with db() as conn:
            p=project_or_404(conn,pid)
            if not p:return jsonify(ok=False,error='Project not found'),404
            if not can_collaborate(conn,p):return jsonify(ok=False,error='Not authorized to reorder stages'),403
            existing={r[0] for r in conn.execute('SELECT id FROM team_project_stages WHERE project_id=?',(pid,))}
            if set(ids)!=existing:return jsonify(ok=False,error='Stage list mismatch'),409
            for pos,sid in enumerate(ids,1):conn.execute('UPDATE team_project_stages SET position=? WHERE id=? AND project_id=?',(pos,sid,pid))
            conn.execute('UPDATE team_projects SET updated_at=? WHERE id=?',(now(),pid))
            log(conn,pid,'Reordered workflow stages')
        return jsonify(ok=True)

    app.register_blueprint(bp)
