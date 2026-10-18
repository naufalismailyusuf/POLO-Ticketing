(async()=>{'use strict';const $=s=>document.querySelector(s),id=Number(location.pathname.match(/\/(\d+)\/?$/)?.[1]);let csrf='',me='',admin=false,data=null,selected=null,edit=null;const E=(tag,cls,txt)=>{const x=document.createElement(tag);if(cls)x.className=cls;if(txt!==undefined)x.textContent=txt;return x};const api=async(path,method='GET',body)=>{const r=await fetch(path,{method,credentials:'same-origin',headers:{'X-CSRF-Token':csrf,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'Request failed');return d};const base='/api/our-projects/'+id;const notice=s=>$('#wf-notice').textContent=s;const allowed=()=>Boolean(data?.can_manage??(admin||String(data?.project?.owner||'').toLowerCase()===String(me).toLowerCase()));const canStatus=()=>Boolean(data?.can_update_status??allowed());const canReorder=()=>Boolean(data?.can_reorder_stages??allowed());let suppressClickUntil=0;const pct=()=>data.stages.length?Math.round(data.stages.filter(s=>s.status==='Completed').length/data.stages.length*100):0;const btn=(text,fn,cls='wf-btn')=>{const b=E('button',cls,text);b.type='button';b.onclick=fn;return b};let jumpToLatestComment=false,selectedFiles=[],discussionScope='stage',lastDiscussionKey='',discussionDrafts=new Map(),bulkMode=false,bulkSelected=new Set(),bulkDeleting=false,taskBulkMode=false,taskBulkSelected=new Set(),taskBulkDeleting=false;
const formatCommentDate=value=>{const d=new Date(value);return Number.isNaN(d.getTime())?String(value||''):new Intl.DateTimeFormat('id-ID',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(d)};function discussionKey(){return discussionScope==='general'?'general':'stage:'+String(selected||'none')}
function saveDiscussionDraft(){const input=$('#wf-comment-body');if(input)discussionDrafts.set(discussionKey(),input.value)}
function loadDiscussionDraft(){const input=$('#wf-comment-body');if(input)input.value=discussionDrafts.get(discussionKey())||''}
function selectStage(stageId){
  if(selected===stageId&&discussionScope==='stage')return;
  saveDiscussionDraft();selected=stageId;discussionScope='stage';bulkMode=false;bulkSelected.clear();taskBulkMode=false;taskBulkSelected.clear();selectedFiles=[];renderSelectedFiles();
  render();loadDiscussionDraft();
}
async function refresh(){data=await api(base);if(selected&&!data.stages.some(s=>s.id===selected))selected=null;if(!selected&&data.stages.length)selected=data.stages[0].id;render()};async function act(path,method,body){try{await api(path,method,body);notice('Saved successfully');await refresh();return true}catch(e){notice(e.message);return false}}function render(){const p=data.project;$('#wf-title').textContent=p.title;$('#wf-description').textContent=p.description||'No description provided';$('#wf-owner').textContent='Created by '+p.owner;$('#wf-priority').textContent='Priority: '+p.priority;$('#wf-priority').dataset.priority=String(p.priority||'').toLowerCase();$('#wf-status').value=p.status;$('#wf-status-badge').textContent=p.status==='Completed'?'Done':p.status;$('#wf-status-badge').dataset.status=String(p.status||'Idea').toLowerCase().replace(/\s+/g,'-');$('#wf-status').disabled=!canStatus();$('#wf-delete-project').hidden=!allowed();$('#wf-status-help').textContent=canStatus()?'Change status to save automatically':'Only the owner, Admin or assigned Support may update status';$('#wf-add-stage').hidden=!allowed()||!data.stages.length;$('#wf-create-first').hidden=!allowed();$('#wf-readonly-note').hidden=allowed();$('#wf-roadmap-empty').hidden=!!data.stages.length;$('#wf-roadmap-hint').hidden=!data.stages.length;$('#wf-progress-percent').textContent=pct()+'%';$('#wf-progress-fill').style.width=pct()+'%';$('#wf-progress-label').textContent=data.stages.filter(s=>s.status==='Completed').length+' of '+data.stages.length+' stages completed';const list=$('#wf-stages');list.replaceChildren();const rail=E('div','wf-roadmap-rail');rail.setAttribute('aria-hidden','true');rail.append(E('span','wf-roadmap-rail-fill'));list.append(rail);data.stages.forEach((s,i)=>{
  const card=E('button','wf-step'+(selected===s.id?' selected':'')+(s.status==='Completed'?' complete':''));
  card.type='button';card.dataset.id=String(s.id);card.draggable=canReorder();
  card.classList.toggle('is-draggable',canReorder());
  card.setAttribute('aria-label','Stage '+(i+1)+': '+s.title+', '+s.status);
  card.title=canReorder()?'Click to view · drag to change order':'Click to view stage details';
  if(canReorder()){
    card.setAttribute('aria-keyshortcuts','Alt+ArrowLeft Alt+ArrowRight');
    const grip=E('span','wf-step-grip');grip.setAttribute('aria-hidden','true');
    grip.title='Drag stage from the circle';card.append(grip);
    let activeTouch=null;
    grip.onpointerdown=e=>{
      if(e.pointerType==='mouse'||!canReorder())return;
      e.preventDefault();e.stopPropagation();
      suppressClickUntil=Date.now()+400;
      activeTouch={pointer:e.pointerId,from:s.id,to:null};
      card.classList.add('is-dragging');
      try{grip.setPointerCapture(e.pointerId)}catch(_){}
    };
    grip.onpointermove=e=>{
      if(!activeTouch||activeTouch.pointer!==e.pointerId)return;
      e.preventDefault();
      // Permit dragging toward a horizontally scrolled stage on small screens.
      const bounds=list.getBoundingClientRect();
      if(e.clientX<bounds.left+30)list.scrollLeft-=18;
      if(e.clientX>bounds.right-30)list.scrollLeft+=18;
      const target=document.elementFromPoint(e.clientX,e.clientY)?.closest('.wf-step');
      list.querySelectorAll('.is-drop-target').forEach(n=>n.classList.remove('is-drop-target'));
      if(target&&target!==card){target.classList.add('is-drop-target');activeTouch.to=Number(target.dataset.id)}
      else activeTouch.to=null;
    };
    grip.onpointerup=e=>{
      if(!activeTouch||activeTouch.pointer!==e.pointerId)return;
      const from=activeTouch.from,to=activeTouch.to;activeTouch=null;
      card.classList.remove('is-dragging');
      list.querySelectorAll('.is-drop-target').forEach(n=>n.classList.remove('is-drop-target'));
      suppressClickUntil=Date.now()+350;
      if(to&&to!==from)reorderTo(from,to);
      else {selectStage(s.id)}
    };
    grip.onpointercancel=()=>{
      activeTouch=null;card.classList.remove('is-dragging');
      list.querySelectorAll('.is-drop-target').forEach(n=>n.classList.remove('is-drop-target'));
    };
    card.onkeydown=e=>{
      if(e.altKey&&(e.key==='ArrowLeft'||e.key==='ArrowRight')){
        e.preventDefault();const target=data.stages[i+(e.key==='ArrowLeft'?-1:1)];
        if(target)reorderTo(s.id,target.id);
      }
    };
  }
  card.append(E('span','wf-step-circle',s.status==='Completed'?'✓':String(i+1)),E('strong','',s.title),E('small','',s.status));
  card.classList.toggle('active-stage',s.status==='In Progress');
  card.onclick=()=>{if(Date.now()<suppressClickUntil)return;selectStage(s.id)};
  card.ondragstart=e=>{
    if(!canReorder()||!e.dataTransfer){e.preventDefault();return}
    e.dataTransfer.setData('text/plain',String(s.id));e.dataTransfer.effectAllowed='move';
    card.classList.add('is-dragging');
  };
  card.ondragover=e=>{if(canReorder()){e.preventDefault();e.dataTransfer.dropEffect='move';card.classList.add('is-drop-target')}};
  card.ondragleave=e=>{if(!card.contains(e.relatedTarget))card.classList.remove('is-drop-target')};
  card.ondragend=()=>{
    suppressClickUntil=Date.now()+300;
    list.querySelectorAll('.is-dragging,.is-drop-target').forEach(n=>n.classList.remove('is-dragging','is-drop-target'));
  };
  card.ondrop=e=>{
    e.preventDefault();
    const from=Number(e.dataTransfer?.getData('text/plain'));
    suppressClickUntil=Date.now()+300;
    list.querySelectorAll('.is-dragging,.is-drop-target').forEach(n=>n.classList.remove('is-dragging','is-drop-target'));
    if(from&&from!==s.id)reorderTo(from,s.id);
  };
  list.append(card);
});
const updateRail=()=>{
 const nodes=[...list.querySelectorAll('.wf-step')],line=rail;
 if(nodes.length<2){line.hidden=true;return}line.hidden=false;
 const first=nodes[0],last=nodes[nodes.length-1];
 const x1=first.offsetLeft+first.offsetWidth/2, x2=last.offsetLeft+last.offsetWidth/2;
 line.style.left=x1+'px';line.style.width=Math.max(0,x2-x1)+'px';
 const complete=data.stages.filter(s=>s.status==='Completed').length;
 const portion=Math.min(1,Math.max(0,complete/data.stages.length));
 line.firstElementChild.style.width=(portion*100)+'%';
};
requestAnimationFrame(updateRail);
const s=data.stages.find(x=>x.id===selected),panel=$('#wf-stage-detail');panel.replaceChildren();if(!s){panel.append(E('p','wf-muted','Select a stage to view details.'))}else{const i=data.stages.indexOf(s);$('#wf-stage-number').textContent='Stage '+(i+1)+' of '+data.stages.length;panel.append(E('h3','',s.title),E('p','wf-muted',s.description||'No stage description'));const meta=E('div','wf-meta');meta.append(E('span','', 'PIC: '+(s.assignee||'Unassigned')),E('span','','Due: '+(s.due_date||'Not set')));panel.append(meta);const controls=E('div','wf-stage-actions');const sel=E('select');['Pending','In Progress','Completed'].forEach(v=>{const o=E('option','',v);o.value=v;sel.append(o)});sel.value=s.status;sel.disabled=!allowed();sel.onchange=()=>act(base+'/stages/'+s.id,'PATCH',{status:sel.value});const statusGroup=E('div','wf-status-control');statusGroup.append(E('label','wf-field-label','Status'),sel);controls.append(statusGroup);if(allowed()){const actionGroup=E('div','wf-action-buttons');const editBtn=btn('Edit Stage',()=>openDialog(s),'wf-edit-stage-btn');editBtn.title='Edit stage details';editBtn.setAttribute('aria-label','Edit stage details');const delBtn=btn('×',async()=>{if(!confirm('Delete this stage? Stages with tasks cannot be deleted.'))return;await act(base+'/stages/'+s.id,'DELETE')},'wf-icon-btn wf-danger');delBtn.title='Delete stage';delBtn.setAttribute('aria-label','Delete stage');actionGroup.append(editBtn,delBtn);controls.append(actionGroup)}panel.append(controls);const tasks=E('div','wf-tasks');
const items=data.tasks.filter(t=>t.stage_id===s.id).sort((a,b)=>Number(a.done)-Number(b.done)||a.id-b.id);
const taskHeading=E('div','wf-task-heading');
const taskActions=E('div','wf-task-heading-actions');
taskActions.append(E('span','wf-task-count',items.filter(t=>t.done).length+' / '+items.length+' completed'));
if(allowed()&&items.length){
  const bulkToggle=btn(taskBulkMode?'Cancel selection':'Select',()=>{
    if(taskBulkDeleting)return;
    taskBulkMode=!taskBulkMode;taskBulkSelected.clear();render();
  },'wf-task-select-toggle');
  bulkToggle.setAttribute('aria-pressed',String(taskBulkMode));
  bulkToggle.setAttribute('aria-label',taskBulkMode?'Cancel checklist selection':'Select checklist tasks to delete');
  taskActions.append(bulkToggle);
}
taskHeading.append(E('h3','','Checklist'),taskActions);tasks.append(taskHeading);
if(!items.length){taskBulkMode=false;taskBulkSelected.clear();tasks.append(E('p','wf-muted','No tasks in this stage.'))}
if(taskBulkMode&&allowed()&&items.length){
  const validIds=new Set(items.map(t=>t.id));
  for(const value of [...taskBulkSelected])if(!validIds.has(value))taskBulkSelected.delete(value);
  const toolbar=E('div','wf-task-bulk-toolbar');
  const allLabel=E('label','wf-task-bulk-all');
  const allInput=E('input');allInput.type='checkbox';
  allInput.checked=taskBulkSelected.size===items.length;
  allInput.indeterminate=taskBulkSelected.size>0&&taskBulkSelected.size<items.length;
  allInput.disabled=taskBulkDeleting;
  allInput.setAttribute('aria-label','Select all tasks in this stage');
  allInput.onchange=()=>{taskBulkSelected.clear();if(allInput.checked)items.forEach(t=>taskBulkSelected.add(t.id));render()};
  allLabel.append(allInput,E('span','','Select all'));
  const selectedCount=E('span','wf-task-bulk-count',taskBulkSelected.size+' selected');
  const bulkDelete=btn(taskBulkDeleting?'Deleting…':'Delete selected',async()=>{
    const ids=[...taskBulkSelected],stageId=s.id;
    if(taskBulkDeleting||!ids.length)return;
    if(!confirm('Permanently delete '+ids.length+' selected checklist task(s) from "'+s.title+'"? This cannot be undone.'))return;
    taskBulkDeleting=true;bulkDelete.disabled=true;bulkDelete.textContent='Deleting…';
    try{
      const response=await api(base+'/tasks/bulk-delete','POST',{stage_id:stageId,task_ids:ids});
      if(response.deleted!==ids.length)throw Error('Deletion count mismatch. Reload the page.');
      taskBulkMode=false;taskBulkSelected.clear();await refresh();notice(ids.length+' checklist task(s) deleted');
    }catch(err){notice(err.message);await refresh()}
    finally{taskBulkDeleting=false;}
  },'wf-task-bulk-delete');
  bulkDelete.disabled=taskBulkDeleting||!taskBulkSelected.size;
  toolbar.append(allLabel,selectedCount,bulkDelete);tasks.append(toolbar);
}
items.forEach(t=>{
  const row=E(taskBulkMode?'div':'label','wf-task'+(t.done?' is-done':'')+(taskBulkSelected.has(t.id)?' is-selected':''));
  if(taskBulkMode&&allowed()){
    const select=E('input','wf-task-bulk-check');select.type='checkbox';select.value=String(t.id);
    select.checked=taskBulkSelected.has(t.id);select.disabled=taskBulkDeleting;
    select.setAttribute('aria-label','Select task '+t.title+' for deletion');
    select.onchange=()=>{if(select.checked)taskBulkSelected.add(t.id);else taskBulkSelected.delete(t.id);render()};
    row.append(select);
  }
  const check=E('input','wf-task-check');check.type='checkbox';check.checked=!!t.done;
  check.disabled=!(allowed()||t.assignee===me)||taskBulkMode;
  check.setAttribute('aria-label',(t.done?'Mark incomplete: ':'Mark completed: ')+t.title);
  check.onchange=()=>act(base+'/tasks/'+t.id,'PATCH',{done:check.checked});
  row.append(check,E('span','wf-task-title',t.title));
  if(t.assignee||t.due_date){
    const tags=E('span','wf-task-metadata');
    if(t.assignee)tags.append(E('small','wf-task-pic',t.assignee));
    if(t.due_date)tags.append(E('small','wf-task-due','Due '+t.due_date));
    row.append(tags);
  }
  if(allowed()&&!taskBulkMode){
    const del=E('button','wf-task-delete');del.type='button';del.title='Delete task';
    del.setAttribute('aria-label','Delete '+t.title);
    const trash=document.createElementNS('http://www.w3.org/2000/svg','svg');
    trash.setAttribute('viewBox','0 0 24 24');trash.setAttribute('width','16');trash.setAttribute('height','16');
    trash.setAttribute('fill','none');trash.setAttribute('stroke','currentColor');trash.setAttribute('stroke-width','1.8');
    trash.setAttribute('stroke-linecap','round');trash.setAttribute('stroke-linejoin','round');trash.setAttribute('aria-hidden','true');
    for(const d of ['M3 6h18','M8 6V4h8v2','M5 6l1 15h12l1-15','M10 10v7','M14 10v7']){
      const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',d);trash.append(path)
    }
    del.append(trash);
    del.onclick=e=>{e.preventDefault();e.stopPropagation();if(confirm('Delete checklist task: '+t.title+'?'))act(base+'/tasks/'+t.id,'DELETE')};
    row.append(del);
  }
  tasks.append(row);
});if(allowed()){
  const add=btn('+ Add task',()=>{add.hidden=true;form.hidden=false;title.focus()},'wf-add-task-open');
  add.setAttribute('aria-label','Add checklist task');
  const form=E('form','wf-task-form wf-inline-add');form.hidden=true;
  const row=E('div','wf-task-entry');const title=E('input');title.required=true;title.maxLength=180;title.placeholder='Describe a task…';title.setAttribute('aria-label','Task title');
  const save=E('button','wf-btn wf-primary','Add');save.type='submit';
  const cancel=btn('Cancel',()=>{form.reset();form.hidden=true;add.hidden=false},'wf-add-task-cancel');
  row.append(title,save,cancel);form.append(row);
  const extra=E('details','wf-task-extras');const summary=E('summary','','PIC & due date (optional)');
  const fields=E('div','wf-task-extra-fields');const who=E('input','wf-task-pic-input');who.name='assignee';who.maxLength=48;who.placeholder='Assign PIC';who.setAttribute('aria-label','Assign PIC');
  const due=E('input','wf-task-date');due.name='due_date';due.type='date';due.setAttribute('aria-label','Task deadline');
  fields.append(who,due);extra.append(summary,fields);form.append(extra);
  form.onsubmit=async e=>{e.preventDefault();
    const result=await act(base+'/tasks','POST',{title:title.value,assignee:who.value,due_date:due.value,stage_id:s.id});
  };
  tasks.append(add,form)
}panel.append(tasks)}const comments=$('#wf-comments'),discussionId=discussionKey(),scopeChanged=discussionId!==lastDiscussionKey,oldScroll=comments.scrollTop,initialComments=!comments.dataset.ready;
lastDiscussionKey=discussionId;
const generalCount=data.comments.filter(c=>c.stage_id==null).length;
const selectedStage=data.stages.find(x=>x.id===selected);
const visibleComments=data.comments.filter(c=>discussionScope==='general'?c.stage_id==null:c.stage_id===selected);
$('#wf-discussion-title').textContent=discussionScope==='general'?'General Discussion':'Stage Discussion';
$('#wf-discussion-subtitle').textContent=discussionScope==='general'?'Project-wide updates and comments from earlier versions.':selectedStage?'Discuss '+selectedStage.title+' with your team.':'Create or select a stage to discuss it.';
const generalBtn=$('#wf-general-discussion');generalBtn.textContent=discussionScope==='general'?'← Back to stage':'General ('+generalCount+')';
generalBtn.setAttribute('aria-pressed',String(discussionScope==='general'));
$('#wf-comment-count').textContent=visibleComments.length+' comment'+(visibleComments.length===1?'':'s');
   const permittedComments=visibleComments.filter(c=>c.can_delete===true);
   const modeButton=$('#wf-bulk-start'), bulkToolbar=$('#wf-bulk-toolbar');
   modeButton.hidden=bulkMode||!permittedComments.length;
   bulkToolbar.hidden=!bulkMode;
   // Selection is always limited to the currently displayed and deletable comments.
   const permittedIds=new Set(permittedComments.map(c=>c.id));
   for(const commentId of [...bulkSelected])if(!permittedIds.has(commentId))bulkSelected.delete(commentId);
   const bulkCount=$('#wf-bulk-count'),bulkAll=$('#wf-bulk-all'),bulkDelete=$('#wf-bulk-delete');
   bulkCount.textContent=bulkSelected.size+' selected';
   bulkDelete.disabled=bulkDeleting||bulkSelected.size===0;
   bulkDelete.textContent=bulkDeleting?'Deleting…':'Delete selected ('+bulkSelected.size+')';
   bulkAll.checked=permittedIds.size>0&&bulkSelected.size===permittedIds.size;
   bulkAll.indeterminate=bulkSelected.size>0&&bulkSelected.size<permittedIds.size;
   bulkAll.disabled=bulkDeleting||!permittedIds.size;
$('#wf-comment-form').classList.toggle('is-disabled',discussionScope==='stage'&&!selected);
$('#wf-comment-body').disabled=discussionScope==='stage'&&!selected;
$('#wf-post-comment').disabled=discussionScope==='stage'&&!selected;
$('#wf-comment-body').placeholder=discussionScope==='general'?'Share a project-wide update…':'Discuss this stage or attach a file…';
const composerLabel=$('.wf-comment-label');
if(composerLabel)composerLabel.textContent=discussionScope==='general'?'General Discussion · New comment':'Comment on: '+(selectedStage?.title||'Select a stage');
comments.replaceChildren();
if(!visibleComments.length){const empty=E('div','wf-comments-empty');empty.append(E('strong','',discussionScope==='general'?'No project-wide comments yet':'No stage comments yet'),E('p','',selectedStage||discussionScope==='general'?'Start a conversation with an update or attachment.':'Select a stage first.'));comments.append(empty)}
visibleComments.forEach(c=>{
  const selectable=bulkMode&&c.can_delete===true;
  const row=E('article','wf-comment'+(selectable?' is-selectable':'')+(bulkSelected.has(c.id)?' is-selected':''));
  if(selectable){
    const choose=E('input','wf-comment-bulk-check');choose.type='checkbox';choose.value=String(c.id);
    choose.checked=bulkSelected.has(c.id);choose.disabled=bulkDeleting;
    choose.setAttribute('aria-label','Select comment by '+c.author);
    choose.onchange=()=>{if(choose.checked)bulkSelected.add(c.id);else bulkSelected.delete(c.id);
      row.classList.toggle('is-selected',choose.checked);
      bulkCount.textContent=bulkSelected.size+' selected';
      bulkDelete.disabled=bulkDeleting||bulkSelected.size===0;
      bulkDelete.textContent='Delete selected ('+bulkSelected.size+')';
      bulkAll.checked=bulkSelected.size===permittedIds.size;
      bulkAll.indeterminate=bulkSelected.size>0&&bulkSelected.size<permittedIds.size;
    };row.append(choose);
  }
  const avatar=E('div','wf-comment-avatar',String(c.author||'?').slice(0,1).toUpperCase());avatar.setAttribute('aria-hidden','true');
  const content=E('div','wf-comment-content');
  const header=E('div','wf-comment-byline');
  header.append(E('strong','',c.author));
  const stamp=E('time','',formatCommentDate(c.created_at));stamp.dateTime=c.created_at;stamp.title=c.created_at;
  header.append(stamp);
  if(c.can_delete===true&&!bulkMode){
    const remove=E('button','wf-comment-delete');remove.type='button';
    remove.title='Delete comment';remove.setAttribute('aria-label','Delete comment by '+c.author);
    const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');
    icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('width','15');icon.setAttribute('height','15');
    icon.setAttribute('fill','none');icon.setAttribute('stroke','currentColor');icon.setAttribute('stroke-width','1.8');
    icon.setAttribute('stroke-linecap','round');icon.setAttribute('stroke-linejoin','round');icon.setAttribute('aria-hidden','true');
    for(const d of ['M3 6h18','M8 6V4h8v2','M5 6l1 15h12l1-15','M10 10v7','M14 10v7']){
      const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',d);icon.append(path)
    }
    remove.append(icon);
    remove.onclick=async()=>{
      if(!confirm('Delete this comment and all attached files? This cannot be undone.'))return;
      remove.disabled=true;
      try{await api(base+'/comments/'+c.id,'DELETE');await refresh();notice('Comment deleted')}
      catch(err){remove.disabled=false;notice(err.message)}
    };
    header.append(remove)
  }
  content.append(header);
  if(c.body)content.append(E('p','wf-comment-text',c.body));
  if(c.attachments?.length){const attachments=E('div','wf-comment-attachments');
    c.attachments.forEach(a=>{const link=E('a',a.is_image?'wf-comment-image-link':'wf-comment-file-link');link.href=a.url;link.target='_blank';link.rel='noopener noreferrer';link.title=a.original_name;
      if(a.is_image){const img=E('img');img.src=a.url;img.alt=a.original_name;img.loading='lazy';link.append(img)}
      else {const fileIcon=E('span','wf-comment-file-icon','↧');fileIcon.setAttribute('aria-hidden','true');link.append(fileIcon,E('span','wf-comment-file-name',a.original_name))}
      attachments.append(link)});
    content.append(attachments)
  }
  row.append(avatar,content);comments.append(row)
});
comments.dataset.ready='1';
comments.scrollTop=jumpToLatestComment||initialComments||scopeChanged?comments.scrollHeight:oldScroll;
jumpToLatestComment=false;$('#wf-clear-activity').hidden=!(data.can_clear_activity===true);$('#wf-activity-count').textContent=data.activity.length+' recent event'+(data.activity.length===1?'':'s');const hist=$('#wf-activity');hist.replaceChildren();if(!data.activity.length)hist.append(E('p','wf-muted','No activity yet.'));data.activity.forEach(a=>{const row=E('div','wf-history-row');row.append(E('strong','',a.author),E('span','',a.action),E('small','',formatCommentDate(a.created_at)));row.lastChild.title=a.created_at;hist.append(row)})}async function reorderTo(from,to){
  if(!canReorder()||from===to)return;
  const ids=data.stages.map(s=>s.id),a=ids.indexOf(from),b=ids.indexOf(to);
  if(a<0||b<0)return;
  ids.splice(a,1);ids.splice(b,0,from);
  await act(base+'/stages/order','PUT',{ids});
}function openDialog(s){edit=s||null;$('#wf-dialog-title').textContent=s?'Edit Stage':'Add Stage';const f=$('#wf-stage-form');f.reset();for(const k of ['title','description','assignee','due_date'])f.elements[k].value=s?.[k]||'';$('#wf-edit-status').hidden=!s;f.elements.status.value=s?.status||'Pending';$('#wf-stage-dialog').showModal()}$('#wf-delete-project').onclick=async()=>{
  if(!data||!allowed())return;
  const title=data.project.title;
  const entered=prompt('Delete project "'+title+'"?\n\nIt will be hidden from Project and cannot be opened normally. Tasks, history and QNAP files stay safe for recovery.\n\nType the exact project title to confirm:');
  if(entered===null)return;
  if(entered!==title){notice('Project title does not match. Nothing deleted.');return}
  const action=$('#wf-delete-project');action.disabled=true;action.textContent='Deleting…';
  try{await api(base,'DELETE',{title:entered});location.assign('/our-project')}
  catch(err){notice(err.message);action.disabled=false;action.textContent='Delete Project'}
};
$('#wf-clear-activity').onclick=async()=>{
  if(!data||data.can_clear_activity!==true)return;
  if(!confirm('Clear the visible activity history for this project? Earlier records will be hidden but retained for audit.'))return;
  const control=$('#wf-clear-activity');control.disabled=true;
  try{await api(base+'/activity/clear','POST');await refresh();notice('Activity history cleared from view. Audit records are retained.');}
  catch(err){notice(err.message)}finally{control.disabled=false}
};$('#wf-add-stage').onclick=()=>openDialog(null);$('#wf-create-first').onclick=()=>openDialog(null);$('#wf-dialog-close').onclick=()=>$('#wf-stage-dialog').close();$('#wf-stage-form').onsubmit=async e=>{e.preventDefault();const body=Object.fromEntries(new FormData(e.target));try{const d=await api(edit?base+'/stages/'+edit.id:base+'/stages',edit?'PATCH':'POST',body);$('#wf-stage-dialog').close();await refresh();notice('Stage saved')}catch(e){notice(e.message)}};$('#wf-status').onchange=async e=>{const control=e.target;const next=control.value;const hint=$('#wf-status-help');control.disabled=true;hint.textContent='Saving status…';hint.dataset.state='saving';try{await api(base,'PATCH',{status:next});await refresh();hint.textContent='Status saved';hint.dataset.state='success';notice('Project status updated')}catch(err){control.value=data?.project?.status||'Idea';hint.textContent='Failed to save: '+err.message;hint.dataset.state='error';notice(err.message)}finally{control.disabled=!canStatus()}};const commentForm=$('#wf-comment-form'),attachInput=$('#wf-attach-input'),fileList=$('#wf-comment-files');
$('#wf-general-discussion').onclick=()=>{
  saveDiscussionDraft();discussionScope=discussionScope==='general'?'stage':'general';
  bulkMode=false;bulkSelected.clear();selectedFiles=[];renderSelectedFiles();render();loadDiscussionDraft();
};
  $('#wf-bulk-start').onclick=()=>{bulkMode=true;bulkSelected.clear();render()};
  $('#wf-bulk-cancel').onclick=()=>{if(bulkDeleting)return;bulkMode=false;bulkSelected.clear();render()};
  $('#wf-bulk-all').onchange=e=>{
    const current=data.comments.filter(c=>c.can_delete===true&&
      (discussionScope==='general'?c.stage_id==null:c.stage_id===selected));
    bulkSelected.clear();if(e.target.checked)current.forEach(c=>bulkSelected.add(c.id));
    render();
  };
  $('#wf-bulk-delete').onclick=async()=>{
    if(bulkDeleting||!bulkSelected.size)return;
    const scope=discussionScope,stage=scope==='stage'?selected:null,ids=[...bulkSelected];
    const target=scope==='general'?'General Discussion':
      'stage "'+(data.stages.find(s=>s.id===stage)?.title||'')+'"';
    if(!confirm('Permanently delete '+ids.length+' selected comment(s) from '+target+
      ' and their attached files? This cannot be undone.'))return;
    bulkDeleting=true;$('#wf-bulk-delete').disabled=true;$('#wf-bulk-delete').textContent='Deleting…';
    try{
      const payload={comment_ids:ids,scope};if(scope==='stage')payload.stage_id=stage;
      const result=await api(base+'/comments/bulk-delete','POST',payload);
      if(result.deleted!==ids.length)throw Error('Deletion count mismatch; refresh the page');
      bulkMode=false;bulkSelected.clear();await refresh();
      notice(result.deleted+' comment(s) deleted from '+target);
    }catch(err){notice(err.message);await refresh()}
    finally{bulkDeleting=false;const action=$('#wf-bulk-delete');if(action){action.disabled=!bulkSelected.size;action.textContent='Delete selected ('+bulkSelected.size+')'}const all=$('#wf-bulk-all');if(all)all.disabled=false}
  };
const attachmentLimit=8*1024*1024, maxFiles=5;
function renderSelectedFiles(){fileList.replaceChildren();fileList.hidden=!selectedFiles.length;
  selectedFiles.forEach((f,i)=>{const chip=E('div','wf-selected-file');chip.append(E('span','wf-selected-file-name',f.name));const size=E('small','',(f.size/1024/1024).toFixed(1)+' MB');chip.append(size);
    const rm=E('button','wf-remove-attachment','×');rm.type='button';rm.setAttribute('aria-label','Remove '+f.name);rm.title='Remove file';rm.onclick=()=>{selectedFiles.splice(i,1);renderSelectedFiles()};chip.append(rm);fileList.append(chip)})
}
attachInput.onchange=()=>{const incoming=[...attachInput.files];
  for(const f of incoming){if(selectedFiles.length>=maxFiles){notice('Maximum 5 attachments per comment');break}
    if(f.size>attachmentLimit){notice(f.name+' exceeds 8 MB');continue}
    if(f.size===0){notice('Empty files are not supported');continue}
    if(selectedFiles.some(x=>x.name===f.name&&x.size===f.size)){continue}
    selectedFiles.push(f)
  }
  attachInput.value='';renderSelectedFiles()
};
commentForm.onsubmit=async e=>{e.preventDefault();
  // Capture the destination before the async upload. Scope never defaults to
  // General when a selected stage was intended, including file-only comments.
  const postingScope=discussionScope;
  const postingStage=postingScope==='stage'?Number(selected):null;
  if(postingScope==='stage'&&(!Number.isInteger(postingStage)||postingStage<=0)){
    notice('Select a stage before posting');return
  }
  const draftKey=discussionKey(),text=$('#wf-comment-body').value.trim();
  if(!text&&!selectedFiles.length){notice('Write a comment or attach a file');return}
  const submit=$('#wf-post-comment');submit.disabled=true;submit.textContent='Posting…';
  const body=new FormData();body.append('body',text);body.append('scope',postingScope);
  if(postingScope==='stage')body.append('stage_id',String(postingStage));
  selectedFiles.forEach(f=>body.append('attachments',f,f.name));
  try{const resp=await fetch(base+'/comments',{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf},body});
    const result=await resp.json();if(!resp.ok||!result.ok)throw Error(result.error||'Unable to post comment');
    // Detect stale server code instead of silently telling users that stage
    // uploads were successful when the backend stored them in General.
    if(result.scope!==postingScope||
       (postingScope==='stage'&&Number(result.stage_id)!==postingStage)){
      throw Error('Comment destination could not be verified. Check server version before reposting.')
    }
    // Do not clear another stage's draft if the user navigated during upload.
    discussionDrafts.set(draftKey,'');
    if(discussionKey()===draftKey){commentForm.reset();selectedFiles=[];renderSelectedFiles()}
    jumpToLatestComment=discussionKey()===draftKey;
    await refresh();notice(postingScope==='stage'?'Stage comment posted':'General comment posted')
  }catch(err){notice(err.message)}finally{submit.disabled=false;submit.textContent='Post Comment'}
};try{const r=await fetch('/api/session',{credentials:'same-origin'}),s=await r.json();if(!r.ok||!s.authenticated)throw Error('Please sign in again');csrf=s.csrf;me=s.user.username;admin=s.user.role==='admin';$('#user-label').textContent=s.user.display||me;$('#user-role').textContent=s.user.role;$('#logout-csrf').value=csrf;document.querySelectorAll('.admin-only').forEach(x=>x.hidden=!admin);await refresh()}catch(e){notice(e.message);$('#wf-title').textContent='Unable to load project'}})();
