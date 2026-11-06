"""Dokumentasi dan File Center POLO; ticketing/dashboard tidak digunakan."""
import mimetypes, os, re, secrets, shutil, sqlite3, time
from pathlib import Path
from urllib.parse import urlsplit
from flask import Flask, abort, g, jsonify, redirect, request, send_file, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from auth_session_policy import ABSOLUTE_TIMEOUT_SECONDS, expiry_payload, remaining_seconds
from dashboard_upload_storage import DashboardUploadStorage

BASE=Path(__file__).resolve().parent
DB=Path(os.environ.get('POLO_DOCS_DB', BASE/'polo_docs.db'))
LEGACY=BASE/'tickets.db'; STATIC=BASE/'static'/'pages'
ROOT=Path(os.environ.get('FILE_CENTER_QNAP_SHARE', r'\\172.16.0.74\IT'))
TEMP=Path(os.environ.get('POLO_TEMP_UPLOAD_DIR', BASE/'.upload-temp')); TEMP.mkdir(exist_ok=True)
app=Flask(__name__); app.secret_key=(BASE/'secret.key').read_text(encoding='utf-8').strip() if (BASE/'secret.key').exists() else secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=True,SESSION_REFRESH_EACH_REQUEST=False,TEMP_UPLOAD_DIR=str(TEMP))
DASHBOARD_UPLOAD_STORAGE=DashboardUploadStorage(os.environ.get('DASHBOARD_QNAP_SHARE',r'\\172.16.0.74\IT'),str(BASE/'uploads'),username=os.environ.get('FILE_CENTER_QNAP_USERNAME',''),password=os.environ.get('FILE_CENTER_QNAP_PASSWORD',''),domain=os.environ.get('FILE_CENTER_QNAP_DOMAIN',''),secondary_share=os.environ.get('DASHBOARD_QNAP_SECONDARY_SHARE',r'\\172.16.0.99\IT'),private_relative=r'POLO\root_dashboard')

def db():
 c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def init_db():
 with db() as c:
  links_new=not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='resource_links'").fetchone()
  c.executescript('''CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY,display TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','user')),password_hash TEXT NOT NULL); CREATE TABLE IF NOT EXISTS file_items(id INTEGER PRIMARY KEY,relative_path TEXT UNIQUE NOT NULL,original_name TEXT NOT NULL,mime_type TEXT NOT NULL,size_bytes INTEGER NOT NULL,uploaded_by TEXT NOT NULL,created_at TEXT NOT NULL);''')
  c.execute('CREATE TABLE IF NOT EXISTS resource_links(id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, description TEXT NOT NULL DEFAULT "")')
  if links_new: c.execute('INSERT INTO resource_links(name,url,description) VALUES(?,?,?)',('QNAP','http://172.16.0.74:8080','Network storage'))
  if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone() and LEGACY.exists():
   try:
    with sqlite3.connect(LEGACY) as old:
     for u,d,r,p in old.execute('SELECT username,display,role,password_hash FROM users'): c.execute('INSERT OR IGNORE INTO users VALUES(?,?,?,?)',(u,d or u,'admin' if r=='admin' else 'user',p))
   except sqlite3.Error: pass
  if not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone() and os.environ.get('POLO_INITIAL_ADMIN_PASSWORD'):
   u=os.environ.get('POLO_INITIAL_ADMIN_USERNAME','admin').lower(); c.execute('INSERT INTO users VALUES(?,?,?,?)',(u,u,'admin',generate_password_hash(os.environ['POLO_INITIAL_ADMIN_PASSWORD'])))
init_db()

def csrf():
 session.setdefault('csrf',secrets.token_hex(16)); return session['csrf']
def safe_parts(value):
 parts=str(value or '').replace('\\','/').strip('/').split('/') if value else []
 if any(not x or x in ('.','..') or '\x00' in x for x in parts): raise ValueError('Invalid path.')
 return parts
def root():
 if not ROOT.is_dir(): raise OSError('QNAP File Center share unavailable.')
 return ROOT.resolve()
def path(value=''):
 r=root(); p=r.joinpath(*safe_parts(value)).resolve()
 if os.path.commonpath([str(r),str(p)])!=str(r): raise ValueError('Invalid path.')
 return p
def now(): return time.strftime('%Y-%m-%d %H:%M:%S')
def admin():
 if not g.user or g.user['role']!='admin': abort(403)
def index_file(relative,p):
 with db() as c:
  c.execute('INSERT INTO file_items(relative_path,original_name,mime_type,size_bytes,uploaded_by,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(relative_path) DO UPDATE SET original_name=excluded.original_name,mime_type=excluded.mime_type,size_bytes=excluded.size_bytes',(relative,p.name,mimetypes.guess_type(p.name)[0] or 'application/octet-stream',p.stat().st_size,g.user['username'],now()))
  return c.execute('SELECT id FROM file_items WHERE relative_path=?',(relative,)).fetchone()['id']

@app.before_request
def auth():
 g.user=None; csrf()
 if request.endpoint=='static' or request.path=='/login' or request.path.startswith(('/g/','/api/public/g/')): return
 if session.get('user'):
  idle,absolute=remaining_seconds(session)
  if idle>0 and absolute>0:
   with db() as c: g.user=c.execute('SELECT * FROM users WHERE username=?',(session['user'],)).fetchone()
 if not g.user:
  if request.path.startswith('/api/'): return jsonify(ok=False,error='Authentication required.'),401
  return redirect(url_for('login',next=request.full_path.rstrip('?')))
 if request.method in {'POST','PUT','PATCH','DELETE'} and not secrets.compare_digest(request.form.get('csrf','') or request.headers.get('X-CSRF-Token',''),session.get('csrf','')): abort(400)

@app.after_request
def headers(r): r.headers['X-Content-Type-Options']='nosniff'; r.headers['X-Frame-Options']='DENY'; r.headers['Cache-Control']='no-store'; return r
@app.get('/')
def home(): return send_from_directory(STATIC,'portal_dashboard.html')
@app.get('/links')
def links_page(): return send_from_directory(STATIC,'links.html')
@app.route('/api/links',methods=['GET','POST'])
def links():
 if request.method=='GET':
  with db() as c: return jsonify(ok=True,links=[dict(x) for x in c.execute('SELECT id,name,url,description FROM resource_links ORDER BY id')])
 admin(); data=request.get_json(silent=True) or {}
 name=str(data.get('name','')).strip(); url=str(data.get('url','')).strip(); description=str(data.get('description','')).strip()
 try: parsed=urlsplit(url); hostname=parsed.hostname
 except ValueError: return jsonify(ok=False,error='Invalid URL.'),400
 if not (1<=len(name)<=80 and len(url)<=2048 and len(description)<=160 and parsed.scheme in {'http','https'} and hostname and not parsed.username and not parsed.password) or re.search(r'[\s\\\x00-\x1f\x7f]',url): return jsonify(ok=False,error='Use a name and a valid HTTP or HTTPS URL without credentials.'),400
 with db() as c:
  cur=c.execute('INSERT INTO resource_links(name,url,description) VALUES(?,?,?)',(name,url,description))
 return jsonify(ok=True,id=cur.lastrowid),201
@app.delete('/api/links/<int:link_id>')
def delete_link(link_id):
 admin()
 with db() as c: deleted=c.execute('DELETE FROM resource_links WHERE id=?',(link_id,)).rowcount
 return (jsonify(ok=True) if deleted else (jsonify(ok=False,error='Link not found.'),404))
@app.route('/login',methods=['GET','POST'])
def login():
 if request.method=='POST':
  with db() as c: u=c.execute('SELECT * FROM users WHERE username=?',(request.form.get('username','').strip().lower(),)).fetchone()
  if u and check_password_hash(u['password_hash'],request.form.get('password','')):
   session.clear(); session.update(user=u['username'],csrf=secrets.token_hex(16),auth_started_at=time.time(),auth_last_active_at=time.time()); n=request.form.get('next','/guide'); return redirect(n if n.startswith('/') and not n.startswith('//') else '/guide')
  return redirect('/login?error=Invalid%20username%20or%20password.')
 return send_from_directory(STATIC,'login.html')
@app.post('/logout')
def logout(): session.clear(); return redirect('/login')
@app.route('/password',methods=['GET','POST'])
def password():
 if request.method=='GET': return send_from_directory(STATIC,'password.html')
 old,new,confirm=request.form.get('old',''),request.form.get('new',''),request.form.get('confirm','')
 if len(new)<12 or new!=confirm or not check_password_hash(g.user['password_hash'],old): return redirect('/password?error=Password%20change%20failed.')
 with db() as c: c.execute('UPDATE users SET password_hash=? WHERE username=?',(generate_password_hash(new),g.user['username']))
 return redirect('/guide')
@app.get('/api/session')
def api_session():
 if not g.user: return jsonify(ok=False,authenticated=False,csrf=csrf()),401
 return jsonify(ok=True,authenticated=True,csrf=csrf(),user={x:g.user[x] for x in ('username','display','role')})
@app.get('/api/session/security')
def api_session_security(): return jsonify(ok=True,session=expiry_payload(session))
@app.post('/api/session/heartbeat')
def heartbeat(): session['auth_last_active_at']=time.time(); return jsonify(ok=True,session=expiry_payload(session))
@app.route('/api/admin/users',methods=['GET','POST','DELETE'])
def users():
 admin()
 if request.method=='GET':
  with db() as c: return jsonify(ok=True,users=[dict(x) for x in c.execute('SELECT username,display,role FROM users ORDER BY username')])
 data=request.get_json(silent=True) or {}; username=str(data.get('username','')).strip().lower()
 if request.method=='POST':
  display=str(data.get('display','')).strip(); role=str(data.get('role','user')); password=str(data.get('password',''))
  if not re.fullmatch(r'[a-z0-9._-]{3,64}',username) or not display or role not in {'admin','user'} or len(password)<12: return jsonify(ok=False,error='Invalid user data.'),400
  with db() as c: c.execute('INSERT INTO users VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET display=excluded.display,role=excluded.role,password_hash=excluded.password_hash',(username,display,role,generate_password_hash(password)))
  return jsonify(ok=True)
 if username==g.user['username']: return jsonify(ok=False,error='Cannot delete current admin.'),400
 with db() as c:
  target=c.execute('SELECT role FROM users WHERE username=?',(username,)).fetchone()
  if target and target['role']=='admin' and c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]<=1: return jsonify(ok=False,error='Keep one admin.'),400
  c.execute('DELETE FROM users WHERE username=?',(username,))
 return jsonify(ok=True)
@app.get('/files')
def files(): return send_from_directory(STATIC,'file_center.html')
@app.get('/api/files/list')
def file_list():
 try:
  rel='/'.join(safe_parts(request.args.get('path',''))); folder=path(rel); folders=[]; files=[]
  for e in sorted(folder.iterdir(),key=lambda x:(not x.is_dir(),x.name.casefold())):
   if e.name.startswith('.'): continue
   item='/'.join(filter(None,[rel,e.name]))
   if e.is_dir(): folders.append({'id':item,'name':e.name,'created_by':'QNAP'})
   elif e.is_file(): files.append({'id':index_file(item,e),'original_name':e.name,'mime_type':mimetypes.guess_type(e.name)[0] or 'application/octet-stream','size_bytes':e.stat().st_size,'size_label':f'{e.stat().st_size} B','uploaded_display':'QNAP','created_at':now(),'kind':e.suffix[1:].upper() or 'FILE'})
  return jsonify(ok=True,path=rel,folders=folders,files=files)
 except (OSError,ValueError) as e: return jsonify(ok=False,error=str(e)),503
@app.get('/api/files/capacity')
def capacity():
 try:
  d=shutil.disk_usage(root()); return jsonify(ok=True,total_bytes=d.total,free_bytes=d.free,used_bytes=d.used)
 except OSError as e: return jsonify(ok=False,error=str(e)),503
@app.post('/files/folder')
def mkdir():
 try:
  data=request.get_json(silent=True) or request.form; name=safe_parts(data.get('name'))
  if len(name)!=1: raise ValueError('Invalid folder name.')
  path('/'.join([*safe_parts(data.get('parent_path','')),name[0]])).mkdir(); return jsonify(ok=True)
 except (OSError,ValueError) as e: return jsonify(ok=False,error=str(e)),400
@app.post('/api/files/upload')
def upload():
 try:
  f=request.files['file']; rel='/'.join([*safe_parts(request.form.get('path','')),safe_parts(f.filename)[-1]]); target=path(rel)
  if target.exists(): return jsonify(ok=False,error='File already exists.'),409
  f.save(target); return jsonify(ok=True,file={'id':index_file(rel,target),'name':target.name})
 except (KeyError,OSError,ValueError) as e: return jsonify(ok=False,error=str(e)),400
@app.get('/files/download/<int:fid>')
def download(fid):
 with db() as c: row=c.execute('SELECT * FROM file_items WHERE id=?',(fid,)).fetchone()
 if not row: abort(404)
 p=path(row['relative_path']);
 if not p.is_file(): abort(404)
 return send_file(p,as_attachment=True,download_name=row['original_name'],conditional=True)
@app.post('/api/files/mass-delete')
def remove():
 admin(); data=request.get_json(silent=True) or {}; 
 try:
  for rel in data.get('paths',[]):
   p=path(rel); shutil.rmtree(p) if p.is_dir() else p.unlink()
   with db() as c: c.execute('DELETE FROM file_items WHERE relative_path=? OR relative_path LIKE ?',(rel,str(rel).rstrip('/')+'/%'))
  return jsonify(ok=True)
 except (OSError,ValueError) as e: return jsonify(ok=False,error=str(e)),400
from guides import register_guides
register_guides(app,db,STATIC,upload_storage=DASHBOARD_UPLOAD_STORAGE)
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.environ.get('PORT','5000')))
