(()=>{const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)],esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));const tid=Number(location.pathname.split('/').filter(Boolean).pop());let session,data,queue=[];let nextFolderSelection=null;let folderMissingNotice=false;
async function get(u,o={}){const r=await fetch(u,{credentials:'same-origin',cache:'no-store',...o});let d={};try{d=await r.json()}catch{}if(!r.ok)throw Error(d.error||`HTTP ${r.status}`);return d}
function setCsrf(c){$$('.csrf-field').forEach(x=>x.value=c)}
function render(d){const t=d.ticket;$('#comment-form').action=`/ticket/${tid}/comment`;$('#ticket-title').textContent=t.masalah;$('#ticket-subtitle').innerHTML=`<span class="ticket-no">${esc(t.no_tiket)}</span><span class="badge status-${String(t.status||'').toLowerCase().replace(/[^a-z0-9]+/g,'-')}">${esc(t.status)}</span><span class="badge priority-${String(t.priority||'').toLowerCase().replace(/[^a-z0-9]+/g,'-')}">${esc(t.priority)}</span><span class="badge">${esc(t.category)}</span>`;$('#comment-meta').textContent=`${d.comment_meta.count||0} updates`;$$('.admin-only').forEach(x=>x.hidden=!d.permissions.is_admin);$('#progress-form-wrap').hidden=!d.permissions.can_comment;const det=[['Reporter',t.pelapor],['Support',t.support_id||d.unassigned],['Division',t.division||'Unspecified'],['Case occurred',`${t.case_tanggal||t.tanggal} ${t.case_jam||t.jam}`],['Created',`${t.tanggal} ${t.jam}`],['Priority',t.priority],['Category',t.category],['Status',t.status],['SLA',t.sla_label||''],['WhatsApp',t.whatsapp||'']];$('#ticket-details').innerHTML=det.map(x=>`<div class="ticket-detail-row"><span>${esc(x[0])}</span><b class="${x[0]==='SLA'&&/breach/i.test(x[1])?'detail-sla-breach':''}">${esc(x[1])}</b></div>`).join('');$('#admin-tools').hidden=!d.permissions.is_admin;$('#admin-case-time-form').action=`/admin/edit/${tid}/case-time`;$('#admin-case-at').value=`${t.case_tanggal||t.tanggal}T${t.case_jam||t.jam}`;$('#legacy-action').hidden=!t.tindakan;$('#legacy-action-text').textContent=t.tindakan||'';let buttons='';if(t.foto)buttons+=`<a class="ticket-btn" href="/foto/${encodeURIComponent(t.foto)}" target="_blank" rel="noopener">Initial evidence</a>`;if(t.completion_foto)buttons+=`<a class="ticket-btn" href="/foto/${encodeURIComponent(t.completion_foto)}" target="_blank" rel="noopener">Completion evidence</a>`;if(d.public_link)buttons+='<button class="ticket-btn" id="copy-public-link" type="button">Copy link</button>';if(d.permissions.can_claim)buttons+='<button class="ticket-btn" id="claim-btn">Take ticket</button>';if(t.whatsapp)buttons+=`<a class="ticket-wa" href="https://wa.me/${encodeURIComponent(t.whatsapp)}" target="_blank" rel="noopener noreferrer">WhatsApp</a>`;$('#ticket-detail-buttons').innerHTML=buttons;$('#claim-btn')?.addEventListener('click',claim);$('#copy-public-link')?.addEventListener('click',()=>copyPublicLink(d.public_link));renderPublicTools(d);renderComments(d);renderFolders(d);$('#status').innerHTML=d.statuses.map(s=>`<option value="${esc(s)}" ${s===t.status?'selected':''}>${esc(s)}</option>`).join('');$('#status').disabled=!d.permissions.can_change_status;window.TicketUI?.syncSelects();}

async function copyPublicLink(link){if(!link)return;try{await navigator.clipboard.writeText(link);await AppDialog.message('Public link copied.')}catch(_){await AppDialog.input('Copy public link',{title:'Copy link',value:link,okText:'Close'})}}
function renderPublicTools(d){const state=$('#public-link-state'),toggle=$('#public-token-toggle'),regen=$('#public-token-regenerate');if(!state||!toggle||!regen)return;state.textContent=d.public_token_enabled?'Enabled':'Disabled';toggle.textContent=d.public_token_enabled?'Disable':'Enable';toggle.onclick=()=>updatePublicToken(d.public_token_enabled?'disable':'enable');regen.onclick=async()=>{if(await AppDialog.ask('Regenerate this public link? The old link will stop working.',{title:'Regenerate link',okText:'Regenerate'}))updatePublicToken('regenerate')}}
async function updatePublicToken(action){const r=await get(`/ticket/${tid}/public-token`,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-CSRF-Token':session.csrf},body:new URLSearchParams({action})});if(r.public_link&&action!=='disable'){try{await navigator.clipboard.writeText(r.public_link)}catch(_){}}await load()}

function renderCommentAttachment(a){
  const name=String(a.original_name||'Attachment');
  const ext=name.includes('.')?name.split('.').pop().toLowerCase():'';
  const isImage=['jpg','jpeg','png','gif','webp','bmp','heic','avif'].includes(ext)||String(a.mime_type||'').startsWith('image/');
  const state=a.storage_state;
  const unavailable=state==='missing'||state==='unavailable';
  const stateLabel=state==='missing'?'Missing on QNAP':state==='unavailable'?'QNAP unavailable':'';
  const bytes=Number(a.size_bytes)||0;
  const size=bytes?(bytes>=1024*1024?(bytes/1048576).toFixed(2)+' MB':Math.max(1,Math.ceil(bytes/1024))+' KB'):'';
  const icon=isImage?'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 16-5.5-5.5L5 21"/></svg>':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h6"/></svg>';
  const contents=`<span class="comment-file-icon">${icon}</span><span class="comment-file-name" title="${esc(name)}">${esc(name)}</span>${size?`<small class="comment-file-size">${esc(size)}</small>`:''}${unavailable?`<small class="comment-file-state">${stateLabel}</small>`:''}`;
  if(unavailable)return `<span class="comment-file ticket-file-${esc(state)}" title="${esc(name)} · ${stateLabel}" aria-label="${esc(name)} · ${stateLabel}">${contents}</span>`;
  const url=a.qnap_path?'/comment-file-id/'+encodeURIComponent(a.id):'/comment-file/'+encodeURIComponent(a.filename);
  if(isImage)return `<a class="comment-inline-image" href="${esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="View image ${esc(name)}"><img src="${esc(url)}" alt="Screenshot in progress update" loading="lazy" decoding="async"></a>`;
  return `<a class="comment-file" href="${esc(url)}" title="Open ${esc(name)}" aria-label="Open ${esc(name)}" target="_blank" rel="noopener noreferrer">${contents}</a>`;
}
function renderComments(d){
  $('#comment-thread').innerHTML=d.comments?.length?d.comments.map(c=>`<article class="comment-card"><div class="comment-head"><div class="comment-head-main"><span class="comment-user">${esc(c.display)}</span><span class="comment-time">${esc(c.created_at)}</span></div>${d.permissions.can_delete_ticket_attachments?`<button type="button" class="ticket-comment-delete" data-comment-id="${c.id}" title="Delete comment" aria-label="Delete comment by ${esc(c.display)}"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m5 4v8m4-8v8"/></svg></button>`:''}</div>${String(c.comment||'').trim()==='Files attached'&&c.attachments?.length?'':`<div class="comment-text">${esc(c.comment||'')}</div>`}${c.attachments?.length?`<div class="comment-evidence">${c.attachments.map(renderCommentAttachment).join('')}</div>`:''}</article>`).join(''):'<div class="comment-empty">No progress updates.</div>';
  $$('.ticket-comment-delete').forEach(b=>b.onclick=async()=>{
    if(!await AppDialog.ask('Delete this comment and its files from QNAP?',{title:'Delete comment',okText:'Delete'}))return;
    b.disabled=true;
    try{await get(`/ticket/${tid}/comment-delete/${b.dataset.commentId}`,{method:'POST',headers:{'X-CSRF-Token':session.csrf}});await load()}
    catch(e){await AppDialog.message(e.message,'Error');b.disabled=false}
  });
}
function syncEvidenceDropzone(){
  const input=$('#attachments'),zone=$('#evidence-dropzone'),sel=$('#storage_section');
  if(!input||!zone||!sel)return;
  const available=data?.ticket_storage_available!==false;
  const validFolder=Boolean(sel.value)&&[...sel.options].some(o=>o.value===sel.value);
  const hasQueue=queue.length>0;
  const enabled=available&&validFolder&&!hasQueue;
  input.disabled=!enabled;
  zone.classList.toggle('is-disabled',!enabled);
  zone.classList.remove('is-dragover');
  zone.setAttribute('aria-disabled',String(!enabled));
  const hint=!available?'QNAP unavailable · retry when connected':!validFolder?'Choose or create an evidence folder first':hasQueue?'Files selected · post update to continue':'Optional · up to 4 files';
  $('#ticket-evidence-hint').textContent=hint;
  zone.title=hint;
}
function renderFolders(d){
  const menu=$('#ticket-folder-menu'),sel=$('#storage_section');
  const previous=nextFolderSelection??sel.value;nextFolderSelection=null;
  if(d.ticket_storage_available===false){
    $('#ticket-folder-label').textContent='QNAP unavailable';
    $('#ticket-folder-trigger').disabled=true;
    $('#create-ticket-folder').disabled=true;
    syncEvidenceDropzone();
    return;
  }
  const folders=d.ticket_folders||[];
  const exists=folders.some(f=>f.path===previous);
  // Never silently select the first folder when the previously chosen one was removed.
  sel.innerHTML='<option value="">Choose folder</option>'+folders.map(f=>`<option value="${esc(f.path)}">${esc(f.name)}</option>`).join('');
  sel.value=exists?previous:'';
  menu.innerHTML=folders.map(f=>`<button type="button" class="ticket-folder-option" role="option" aria-selected="${String(f.path===sel.value)}" data-value="${esc(f.path)}" data-label="${esc(f.name)}">${esc(f.name)}</button>`).join('')||'<div class="ticket-folder-empty">No folders. Create one to attach evidence.</div>';
  $('#ticket-folder-label').textContent=sel.selectedOptions[0]?.textContent||'Choose folder';
  $('#ticket-folder-trigger').disabled=queue.some(x=>x.status!=='failed');
  $('#create-ticket-folder').disabled=queue.some(x=>x.status!=='failed');
  $$('.ticket-folder-option').forEach(o=>{const selected=o.dataset.value===sel.value;o.setAttribute('aria-selected',String(selected));o.classList.toggle('selected',selected);
    o.onclick=()=>{sel.value=o.dataset.value;$('#ticket-folder-label').textContent=o.dataset.label;folderMissingNotice=false;closeFolderMenu();$$('.ticket-folder-option').forEach(b=>{b.setAttribute('aria-selected',String(b===o));b.classList.toggle('selected',b===o)});syncEvidenceDropzone();$('#ticket-folder-trigger').focus()};});
  $('#ticket-folder-trigger').onclick=()=>{const open=$('#ticket-folder-picker').classList.toggle('open');$('#ticket-folder-trigger').setAttribute('aria-expanded',String(open))};
  if(previous&&!exists&&!folderMissingNotice){folderMissingNotice=true;AppDialog.message('Previously selected evidence folder no longer exists on QNAP. Please choose or create a folder.','Evidence folder unavailable')}
  syncEvidenceDropzone();
}
function closeFolderMenu(){ $('#ticket-folder-picker').classList.remove('open');$('#ticket-folder-trigger').setAttribute('aria-expanded','false')}document.addEventListener('click',e=>{if(!e.target.closest('#ticket-folder-picker'))closeFolderMenu()});$('#ticket-folder-picker').addEventListener('keydown',e=>{const options=$$('.ticket-folder-option'),index=options.indexOf(document.activeElement);if(e.key==='Escape'){closeFolderMenu();$('#ticket-folder-trigger').focus()}else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();$('#ticket-folder-picker').classList.add('open');$('#ticket-folder-trigger').setAttribute('aria-expanded','true');options[(index+(e.key==='ArrowDown'?1:-1)+options.length)%options.length]?.focus()}});async function claim(){await get(`/claim/${tid}`,{method:'POST',headers:{'X-CSRF-Token':session.csrf}});await load()}
function uploadFiles(){
  const input=$('#attachments'),zone=$('#evidence-dropzone');
  async function chooseFiles(files,fromClipboard=false){
    if(!files.length)return;
    if(!fromClipboard&&input.disabled){input.value='';return;}
    if(queue.some(x=>x.status!=='uploaded')){await AppDialog.message('Wait for the current upload to finish.');input.value='';return;}
    if(queue.length+files.length>4){await AppDialog.message('Maximum 4 files per update.');input.value='';return;}
    const folder=$('#storage_section');
    if(!folder.value){
      // Clipboard screenshots use the first already-existing evidence folder if none is selected.
      const available=(data?.ticket_folders||[]);
      if(fromClipboard&&available.length){const chosen=available[0];folder.value=chosen.path;$('#ticket-folder-label').textContent=chosen.name;}
      else{await AppDialog.message('Choose or create an evidence folder before pasting a screenshot.');return;}
    }
    queue.push(...files.map(f=>({file:f,status:'waiting',progress:0,id:null,error:'',preview:f.type.startsWith('image/')?URL.createObjectURL(f):null})));
    input.value='';renderQueue();pump();
  }
  input.addEventListener('change',()=>chooseFiles([...input.files]));
  $('#comment').addEventListener('paste',e=>{
    const items=[...(e.clipboardData?.items||[])];
    const images=items.filter(item=>item.kind==='file'&&item.type.startsWith('image/'));
    if(!images.length)return;
    e.preventDefault();
    const files=images.map((item,i)=>{const blob=item.getAsFile();if(!blob)return null;const ext=(blob.type.split('/')[1]||'png').replace('jpeg','jpg');return new File([blob],`screenshot-${Date.now()}-${i+1}.${ext}`,{type:blob.type||'image/png'});}).filter(Boolean);
    chooseFiles(files,true);
  });
  zone.addEventListener('dragenter',e=>{e.preventDefault();if(!input.disabled)zone.classList.add('is-dragover')});
  zone.addEventListener('dragover',e=>{e.preventDefault();e.dataTransfer.dropEffect=input.disabled?'none':'copy';zone.classList.toggle('is-dragover',!input.disabled)});
  zone.addEventListener('dragleave',e=>{if(!zone.contains(e.relatedTarget))zone.classList.remove('is-dragover')});
  zone.addEventListener('drop',e=>{e.preventDefault();zone.classList.remove('is-dragover');if(!input.disabled)chooseFiles([...e.dataTransfer.files]);});
}
function renderQueue(){const locked=queue.some(x=>x.status!=='failed');$('#ticket-folder-trigger').disabled=locked;$('#create-ticket-folder').disabled=locked;$('#post-update-btn').disabled=queue.some(x=>x.status!=='uploaded');$('#attachment-list').textContent=queue.map(x=>x.file.name).join(', ');const box=$('#attachment-queue');box.hidden=!queue.length;box.innerHTML=queue.map((x,i)=>`<div class="ticket-upload-item">${x.preview?`<img class="ticket-paste-preview" src="${esc(x.preview)}" alt="Screenshot preview">`:""}<div class="ticket-upload-head"><span class="ticket-upload-name">${esc(x.file.name)}</span><span class="ticket-upload-status">${esc(x.status==='uploaded'?'Uploaded':x.status)}</span></div><div class="ticket-upload-bar"><div class="ticket-upload-progress" style="width:${x.progress}%"></div></div><div class="ticket-upload-meta"><span>${x.error?esc(x.error):x.progress+'%'}</span>${x.status==='failed'?`<button type="button" data-retry="${i}" class="ticket-upload-retry">Retry</button>`:''}</div></div>`).join('');$$('[data-retry]').forEach(b=>b.onclick=()=>{queue[Number(b.dataset.retry)].status='waiting';queue[Number(b.dataset.retry)].error='';pump()});syncEvidenceDropzone()}
let active=0;async function pump(){while(active<2){const x=queue.find(x=>x.status==='waiting');if(!x)break;active++;uploadOne(x).finally(()=>{active--;pump()})}renderQueue()}
async function uploadOne(x){try{const folder=$('#storage_section').value;if(!folder)throw Error('Select an evidence folder first.');x.status='preparing';renderQueue();const p=await get(`/ticket/${tid}/attachment-prepare`,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-CSRF-Token':session.csrf},body:new URLSearchParams({folder_path:folder})});x.status='uploading';renderQueue();const r=await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST',`/ticket/${tid}/attachment-upload`);xhr.setRequestHeader('X-CSRF-Token',session.csrf);xhr.setRequestHeader('X-File-Name',encodeURIComponent(x.file.name));xhr.setRequestHeader('X-File-Size',String(x.file.size));xhr.setRequestHeader('X-Target-Path',encodeURIComponent(p.folder_path));xhr.upload.onprogress=e=>{if(e.lengthComputable){x.progress=Math.round(e.loaded/e.total*100);renderQueue()}};xhr.onload=()=>{let d={};try{d=JSON.parse(xhr.responseText)}catch{};xhr.status>=200&&xhr.status<300&&d.ok?resolve(d):reject(Error(d.error||`HTTP ${xhr.status}`))};xhr.onerror=()=>reject(Error('Connection error'));xhr.send(x.file)});x.id=r.file.id;x.status='uploaded';x.progress=100;const ids=queue.filter(q=>q.id).map(q=>q.id);$('#uploaded-attachment-ids').value=JSON.stringify(ids);renderQueue()}catch(e){x.status='failed';x.error=e.message;renderQueue()}}
$('#comment-form').addEventListener('submit',async e=>{if(queue.some(x=>['waiting','preparing','uploading'].includes(x.status))||queue.some(x=>x.status==='failed')){e.preventDefault();await AppDialog.message('Wait for uploads to finish and retry failed files.');return;}$('#storage_section').disabled=!queue.some(x=>x.id);$('#attachments').value='';});
$('#apply-age').onclick=()=>{const d=new Date();d.setMinutes(d.getMinutes()-(Math.max(0,Number($('#age-days').value)||0)*1440+Math.max(0,Number($('#age-hours').value)||0)*60+Math.max(0,Number($('#age-minutes').value)||0)));d.setMinutes(d.getMinutes()-d.getTimezoneOffset());$('#admin-case-at').value=d.toISOString().slice(0,16)};
$('#create-ticket-folder').onclick=async()=>{const names=(data?.ticket_folders||[]).map(f=>String(f.name||'').toLowerCase());const name=await AppDialog.input('New folder name',{title:'New folder',validate:value=>names.includes(value.toLowerCase())?'Folder already exists.':''});if(!name)return;try{const created=await get(`/ticket/${tid}/attachment-folders`,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-CSRF-Token':session.csrf},body:new URLSearchParams({name})});nextFolderSelection=created.folder?.path||null;folderMissingNotice=false;await load()}catch(e){await AppDialog.message(e.message,'Error')}};
async function load(){data=await get(`/api/tickets/${tid}`);render(data)}
setInterval(async()=>{
  if(document.hidden||!session||queue.length||!data)return;
  try{
    const updated=await get(`/api/tickets/${tid}`);
    if(updated.ticket_storage_available===false)return;
    data=updated;
    renderComments(updated);
    renderFolders(updated);
    $('#comment-meta').textContent=`${updated.comment_meta.count||0} updates`;
  }catch(_){/* Do not clear UI on transient SMB/network errors. */}
},30000);
(async()=>{try{session=await get('/api/session');window.renderSessionMessages?.(session);setCsrf(session.csrf);window.renderWorkspaceUser?.(session.user);uploadFiles();await load()}catch(e){await AppDialog.message(e.message,'Error');if(e.message.includes('401'))location='/login'}})();})();
