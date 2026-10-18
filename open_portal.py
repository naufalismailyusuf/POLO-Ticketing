import os
from flask import abort, g, request, session, send_from_directory

def _allow_open_without_login(app):
    hooks = app.before_request_funcs.get(None, [])
    if getattr(app, "_open_portal_auth_patched", False):
        return
    for index, hook in enumerate(list(hooks)):
        if getattr(hook, "__name__", "") != "load_user":
            continue
        original = hook
        def open_aware_load_user(original_hook=original):
            if request.path.rstrip("/") in {"/open", "/open/success"}:
                g._request_started = __import__("time").perf_counter(); g.user = None
                if not session.get("csrf"): session["csrf"] = __import__("secrets").token_hex(16)
                if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                    csrf_token=request.form.get("csrf", "") or request.headers.get("X-CSRF-Token", "")
                    if not csrf_token or not __import__("secrets").compare_digest(csrf_token, session.get("csrf", "")): abort(400)
                return
            return original_hook()
        open_aware_load_user.__name__="load_user_open_aware"
        hooks[index]=open_aware_load_user; app._open_portal_auth_patched=True; return

OPEN_CSS = """
*{box-sizing:border-box}
:root{--ink:#101828;--muted:#667085;--line:#e4e7ec;--soft:#f8fafc;--brand:#17202a;--focus:#344054;--danger:#b42318}
html,body{margin:0;padding:0;background:#f4f6f8;color:var(--ink);font-family:Inter,Segoe UI,Arial,sans-serif;-webkit-font-smoothing:antialiased}
body{min-height:100vh;overflow-x:hidden}
body:before{content:"";position:fixed;inset:-180px -120px auto auto;width:420px;height:420px;border-radius:50%;background:radial-gradient(circle,rgba(23,32,42,.07),rgba(23,32,42,0) 68%);pointer-events:none}
.wrap{width:min(820px,calc(100% - 32px));margin:0 auto;padding-bottom:34px}
.top{padding:42px 0 22px;animation:rise .45s ease both}
.brand-row{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.brand-mark{width:30px;height:30px;border:1.5px solid #98a2b3;border-radius:9px;display:grid;place-items:center;font-size:12px;font-weight:800;color:#344054;background:rgba(255,255,255,.72)}
.brand{font-size:12px;font-weight:800;letter-spacing:.16em;color:#667085;text-transform:uppercase}
h1{margin:0 0 8px;font-size:32px;line-height:1.12;letter-spacing:-.035em}
.sub{max-width:650px;margin:0;color:var(--muted);font-size:14px;line-height:1.6}
.card{position:relative;background:rgba(255,255,255,.94);border:1px solid var(--line);border-radius:20px;box-shadow:0 12px 36px rgba(16,24,40,.07);padding:26px;animation:rise .5s .06s ease both}
.card:after{content:"";position:absolute;left:24px;right:24px;top:0;height:1px;background:linear-gradient(90deg,transparent,rgba(23,32,42,.16),transparent)}
.section-title{display:flex;align-items:center;gap:9px;margin:0 0 18px;font-size:13px;font-weight:800;color:#344054}
.section-dot{width:7px;height:7px;border-radius:50%;background:#17202a;box-shadow:0 0 0 5px #eef1f4}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:17px}
.field{display:flex;flex-direction:column;gap:7px;min-width:0}
.field.full{grid-column:1/-1}
label{font-size:12px;font-weight:750;color:#344054}
.required{color:#98a2b3;font-weight:500}
input,select,textarea{width:100%;border:1px solid #d0d5dd;border-radius:11px;background:#fff;color:#101828;padding:11px 12px;font:inherit;font-size:14px;outline:none;transition:border-color .18s ease,box-shadow .18s ease,transform .18s ease,background .18s ease}
input::placeholder,textarea::placeholder{color:#98a2b3}
input:hover,select:hover,textarea:hover{border-color:#b8c0cc}
input:focus,select:focus,textarea:focus{border-color:#667085;box-shadow:0 0 0 4px rgba(102,112,133,.10);transform:translateY(-1px)}
select{appearance:auto;cursor:pointer}
textarea{min-height:142px;resize:vertical;line-height:1.55}
.hint{font-size:11px;color:#98a2b3;line-height:1.45}
.text-meta{display:flex;justify-content:space-between;gap:10px;margin-top:2px}
.file-wrap{position:relative}
.file{padding:8px 10px;cursor:pointer}
.file::file-selector-button{border:0;border-radius:8px;background:#f2f4f7;color:#344054;padding:7px 10px;margin-right:8px;font:inherit;font-size:12px;font-weight:700;cursor:pointer}
.file-name{margin-top:5px;min-height:16px;font-size:11px;color:#667085;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.actions{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:22px;padding-top:19px;border-top:1px solid #eaecf0}
.actions-copy{font-size:11px;line-height:1.45;color:#98a2b3}
button{border:0;border-radius:11px;background:var(--brand);color:#fff;padding:12px 20px;min-width:142px;font:inherit;font-size:13px;font-weight:750;cursor:pointer;transition:transform .18s ease,box-shadow .18s ease,background .18s ease}
button:hover{background:#0f151b;transform:translateY(-1px);box-shadow:0 7px 16px rgba(16,24,40,.14)}
button:active{transform:translateY(0);box-shadow:none}
button:disabled{opacity:.7;cursor:wait;transform:none}
.alert{display:flex;align-items:flex-start;gap:9px;border-radius:11px;padding:11px 13px;margin-bottom:16px;font-size:13px;background:#fff3f2;border:1px solid #fecdca;color:var(--danger);animation:shake .28s ease both}
.alert:before{content:"!";display:grid;place-items:center;width:18px;height:18px;flex:0 0 18px;border-radius:50%;background:#fee4e2;font-weight:800;font-size:11px}
.ok{background:#ecfdf3;border-color:#abefc6;color:#067647}
.ticket{font-size:34px;font-weight:800;letter-spacing:-.035em;margin:8px 0 7px;word-break:break-word}
.small{font-size:13px;color:#667085;line-height:1.5}
.success-icon{width:44px;height:44px;border-radius:14px;background:#ecfdf3;border:1px solid #abefc6;color:#067647;display:grid;place-items:center;font-weight:900;font-size:20px;margin-bottom:14px;animation:pop .38s ease both}
@keyframes rise{from{opacity:0;transform:translateY(9px)}to{opacity:1;transform:none}}
@keyframes pop{0%{opacity:0;transform:scale(.75)}70%{transform:scale(1.06)}100%{opacity:1;transform:scale(1)}}
@keyframes shake{0%,100%{transform:translateX(0)}30%{transform:translateX(-3px)}70%{transform:translateX(3px)}}
@media(max-width:620px){.wrap{width:min(100% - 20px,820px);padding-bottom:22px}.top{padding:27px 0 17px}.brand-row{margin-bottom:11px}h1{font-size:26px}.sub{font-size:13px}.card{padding:18px;border-radius:16px}.grid{grid-template-columns:1fr;gap:15px}.field.full{grid-column:auto}.actions{align-items:stretch;flex-direction:column}.actions-copy{width:100%}button{width:100%}.file{font-size:13px}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
"""

def register_open_portal(app):
    """Keep public-portal authentication and success page, without a duplicate /open handler."""
    # Revisi 38: /open GET/POST is owned exclusively by app.open_ticket.
    open_routes = [rule for rule in app.url_map.iter_rules() if rule.rule == "/open"]
    if (len(open_routes) != 1 or open_routes[0].endpoint != "open_ticket"
            or not {"GET", "POST"}.issubset(open_routes[0].methods)):
        raise RuntimeError("Open Ticket route conflict: expected exactly one /open GET/POST handler (open_ticket)")

    _allow_open_without_login(app)
    if "open_success" in app.view_functions:
        return

    @app.get("/open/success")
    def open_success():
        return send_from_directory(os.path.join(app.static_folder, "pages"), "open_success.html")
