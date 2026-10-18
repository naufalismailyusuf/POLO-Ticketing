(async()=>{'use strict';let csrf='',me='',admin=false,projects=[],active=null;const $=s=>document.querySelector(s),notice=s=>{$('#project-notice').textContent=s};const el=(tag,cls,text)=>{const x=document.createElement(tag);if(cls)x.className=cls;if(text!==undefined)x.textContent=text;return x};async function api(url,method='GET',data){const r=await fetch(url,{method,credentials:'same-origin',headers:{'X-CSRF-Token':csrf,...(data?{'Content-Type':'application/json'}:{})},body:data?JSON.stringify(data):undefined});let v=await r.json();if(!r.ok||!v.ok)throw Error(v.error||'Request failed');return v}function percent(p){const total=Number(p.stage_count)||0,done=Number(p.stage_done)||0;return total>0?Math.round(Math.min(total,Math.max(0,done))/total*100):0}function card(p){
  const row=el('div','project-row');
  const link=el('a','project-row-main');
  link.href='/our-project/'+encodeURIComponent(p.id);
  link.setAttribute('aria-label','Open project: '+p.title);

  const summary=el('div','project-row-summary');
  summary.append(el('h3','project-row-title',p.title));
  summary.append(el('p','project-row-description',p.description||'No description yet'));

  const status=el('span','project-row-status');
  status.classList.add('project-status-'+String(p.status||'').toLowerCase().replace(/[^a-z0-9]+/g,'-'));
  const dot=el('i','project-status-dot');
  dot.setAttribute('aria-hidden','true');
  status.append(dot,el('span','',p.status));

  const progress=el('div','project-row-progress');
  const value=percent(p);
  const track=el('span','project-row-progress-track');
  track.setAttribute('role','progressbar');
  track.setAttribute('aria-label','Completed project stages');
  track.setAttribute('aria-valuemin','0');
  track.setAttribute('aria-valuemax','100');
  track.setAttribute('aria-valuenow',String(value));
  const fill=el('i','project-row-progress-fill');
  fill.style.width=value+'%';
  track.append(fill);
  progress.append(track,el('span','project-progress-value',value+'%'));

  const activity=el('div','project-row-activity');
  activity.append(el('span','project-owner',p.owner||'Unassigned'));
  activity.append(el('span','project-activity-details',String(p.stage_done||0)+'/'+String(p.stage_count||0)+' stages · '+String(p.comment_count||0)+' comments'));
  link.append(summary,status,progress,activity);

  const menu=el('details','project-row-actions');
  const trigger=el('summary','project-actions-trigger','⋯');
  trigger.setAttribute('aria-label','Actions for '+p.title);
  trigger.title='Project actions';
  const items=el('div','project-actions-menu');
  const open=el('a','project-action-link','Open project');
  open.href=link.href;
  items.append(open);
  if(p.can_delete){
    const remove=el('button','project-action-delete','Delete project');
    remove.type='button';
    remove.onclick=async e=>{e.preventDefault();e.stopPropagation();menu.open=false;await deleteProject(p)};
    items.append(remove);
  }
  menu.append(trigger,items);
  row.append(link,menu);
  return row;
}
async function deleteProject(p){const title=prompt('Delete project "'+p.title+'"?\n\nIt will disappear from the workspace. Tasks, comments and QNAP files are retained for recovery.\n\nType the exact project title to confirm:');if(title===null)return;if(title!==p.title){alert('Project title does not match. Nothing deleted.');return}try{await api('/api/our-projects/'+p.id,'DELETE',{title});await load();notice('Project deleted: '+p.title)}catch(e){alert(e.message)}}function render(){const list=$('#project-list');list.replaceChildren();const q=$('#project-search').value.toLowerCase(),filter=$('#project-filter').value;const visible=projects.filter(p=>(!filter||p.status===filter)&&[p.title,p.description,p.owner].join(' ').toLowerCase().includes(q));for(const p of visible)list.append(card(p));if(!visible.length)list.append(el('p','project-list-empty','No projects found. Create a project to get started.'));$('#project-shown').textContent=String(visible.length);$('#count-total').textContent=projects.length;$('#count-progress').textContent=projects.filter(p=>p.status==='In Progress').length;$('#count-discussion').textContent=projects.filter(p=>p.status==='Discussion').length;$('#count-completed').textContent=projects.filter(p=>p.status==='Completed').length}async function load(){const d=await api('/api/our-projects');projects=d.projects;admin=d.is_admin;render()}async function openProject(id){try{const d=await api('/api/our-projects/'+id);active=d.project;$('#detail-title').textContent=active.title;$('#detail-description').textContent=active.description||'No description';$('#detail-owner').textContent='Created by '+active.owner;$('#detail-priority').textContent='Priority: '+active.priority;$('#detail-status').value=active.status;$('#detail-status').disabled=!(admin||active.owner===me);const tasks=$('#detail-tasks');tasks.replaceChildren();let done=0;for(const t of d.tasks){if(t.done)done++;const row=el('div','task-row'),check=el('input');check.type='checkbox';check.checked=!!t.done;check.disabled=!(admin||active.owner===me||t.assignee===me);check.onchange=async()=>{try{await api('/api/our-projects/'+id+'/tasks/'+t.id,'PATCH',{done:check.checked});await openProject(id);await load()}catch(e){alert(e.message);check.checked=!check.checked}};row.append(check,el('span','',t.title),el('small','',t.assignee));tasks.append(row)}$('#detail-progress').textContent=d.tasks.length?Math.round(done/d.tasks.length*100)+'% complete':'No tasks';$('#task-form').hidden=!(admin||active.owner===me);const comments=$('#detail-comments');comments.replaceChildren();for(const c of d.comments){const row=el('div','comment-row');row.append(el('strong','',c.author),el('p','',c.body),el('small','',c.created_at));comments.append(row)}if(!d.comments.length)comments.append(el('p','','No discussion yet.'));if(!$('#detail-dialog').open)$('#detail-dialog').showModal()}catch(e){notice(e.message)}}try{const r=await fetch('/api/session',{credentials:'same-origin'});const s=await r.json();if(!r.ok||!s.authenticated)throw Error('Please sign in again');csrf=s.csrf;me=s.user.username;admin=s.user.role==='admin';$('#user-label').textContent=s.user.display||me;$('#user-role').textContent=s.user.role;$('#logout-csrf').value=csrf;document.querySelectorAll('.admin-only').forEach(x=>{x.hidden=!admin});await load()}catch(e){notice(e.message)}async function loadSupportChoices(){const box=$('#project-support-options');box.replaceChildren();try{const result=await api('/api/our-projects/support-users');if(!result.users.length){box.append(el('span','','No support accounts available'));return}result.users.forEach(u=>{const label=el('label','project-member-option'),check=el('input');check.type='checkbox';check.name='members';check.value=u.username;label.append(check,el('span','',u.display||u.username));box.append(label)})}catch(e){box.append(el('span','',e.message))}}
window.addEventListener('pageshow',e=>{if(e.persisted)load().catch(err=>notice(err.message))});$('#new-project').onclick=()=>{loadSupportChoices();$('#project-dialog').showModal()};document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$('#'+b.dataset.close).close());document.addEventListener('click',e=>{document.querySelectorAll('.project-row-actions[open]').forEach(d=>{if(!d.contains(e.target))d.open=false})});document.addEventListener('keydown',e=>{if(e.key==='Escape')document.querySelectorAll('.project-row-actions[open]').forEach(d=>d.open=false)});$('#project-search').oninput=render;$('#project-filter').onchange=render;$('#project-create-form').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{const fd=new FormData(f),body=Object.fromEntries(fd);body.members=fd.getAll('members');const v=await api('/api/our-projects','POST',body);f.reset();$('#project-dialog').close();await load();location.assign("/our-project/"+v.id)}catch(err){alert(err.message)}};$('#detail-status').onchange=async e=>{try{await api('/api/our-projects/'+active.id,'PATCH',{status:e.target.value});await load()}catch(err){alert(err.message);e.target.value=active.status}};$('#task-form').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{await api('/api/our-projects/'+active.id+'/tasks','POST',Object.fromEntries(new FormData(f)));f.reset();await openProject(active.id);await load()}catch(err){alert(err.message)}};$('#comment-form').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{await api('/api/our-projects/'+active.id+'/comments','POST',Object.fromEntries(new FormData(f)));f.reset();await openProject(active.id);await load()}catch(err){alert(err.message)}};})();
