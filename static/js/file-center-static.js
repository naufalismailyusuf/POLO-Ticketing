(()=>{const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)],esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));let session,path='',isAdmin=false,clipboard=null;try{clipboard=JSON.parse(sessionStorage.getItem('polo-file-clipboard')||'null')}catch(_){}async function get(u,o={}){const r=await fetch(u,{credentials:'same-origin',cache:'no-store',...o});let d={};try{d=await r.json()}catch{}if(!r.ok)throw Error(d.error||`HTTP ${r.status}`);return d}function fcBytes(bytes){
  const n=Number(bytes);
  if(!Number.isFinite(n)||n<0)return '—';
  const units=['B','KB','MB','GB','TB','PB'];
  if(n<1024)return `${n} B`;
  const unit=Math.min(Math.floor(Math.log(n)/Math.log(1024)),5);
  return `${(n/Math.pow(1024,unit)).toFixed(unit>=3?2:1)} ${units[unit]}`;
}
async function fcLoadCapacity(force=false){
  const summary=$('#fc-capacity-summary'),bar=$('#fc-capacity-bar'),fill=$('#fc-capacity-fill');
  if(!summary||!bar||!fill)return;
  try{
    const response=await get('/api/files/capacity'+(force?'?refresh=1':''));
    if(!response.ok)throw Error(response.error||'Capacity unavailable');
    const total=Number(response.total_bytes),free=Number(response.free_bytes),used=Number(response.used_bytes);
    if(!Number.isFinite(total)||total<=0||!Number.isFinite(free)||free<0||free>total||!Number.isFinite(used))throw Error('Invalid capacity');
    const percent=Math.max(0,Math.min(100,Math.round(1000*(total-free)/total)/10));
    summary.textContent=`${percent.toFixed(1)}% used`;
    $('#fc-capacity-used').textContent=`Used: ${fcBytes(total-free)}`;
    $('#fc-capacity-free').textContent=`Free: ${fcBytes(free)}`;
    $('#fc-capacity-total').textContent=`Total: ${fcBytes(total)}`;
    fill.style.width=percent+'%';
    fill.classList.toggle('fc-capacity-high',percent>=90);
    bar.setAttribute('aria-valuenow',String(percent));
  }catch(_){
    summary.textContent='Capacity unavailable';
    $('#fc-capacity-used').textContent='Used: —';
    $('#fc-capacity-free').textContent='Free: —';
    $('#fc-capacity-total').textContent='Total: —';
    fill.style.width='0%';fill.classList.remove('fc-capacity-high');
    bar.removeAttribute('aria-valuenow');
  }
}
function csrf(){return session.csrf}function report(e){window.fileCenterToast?.(e.message,'err')}function siblingNames(row){return $$('.fc-row').filter(x=>x!==row).map(x=>x.dataset.name.toLowerCase())}
async function askRename(button){const row=button.closest('.fc-row');const current=button.dataset.name;const name=await AppDialog.input('New name',{title:'Rename item',value:current,validate:value=>value.toLowerCase()!==current.toLowerCase()&&siblingNames(row).includes(value.toLowerCase())?'An item with this name already exists.':''});if(!name||name===current)return;const u=button.dataset.rename==='folder'?`/api/files/rename-folder/${button.dataset.id}`:`/api/files/rename/${button.dataset.id}`;await post(u,{name});await load()}
function render(d){path=d.path||'';$('#qnap-status').classList.toggle('is-ok',true);$('#qnap-status-title').textContent='Connected';$('#qnap-status').title='QNAP connected';$('#file-breadcrumbs').innerHTML='<a href="/files">Files</a>'+path.split('/').filter(Boolean).map((part,i,parts)=>'<span aria-hidden="true">/</span><a href="/files?path='+encodeURIComponent(parts.slice(0,i+1).join('/'))+'">'+esc(part)+'</a>').join('');$('#fc-list-count').textContent=`${d.folders.length} folders · ${d.files.length} files`;const folderIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7V5.5A1.5 1.5 0 0 1 4.5 4h5l2 3h8A1.5 1.5 0 0 1 21 8.5v10a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5Z"/></svg>';function item(f,kind){const folder=kind==='folder',name=folder?f.name:f.original_name,id=Number(f.id),itemPath=path?path+'/'+name:name;const actions=`<details class="item-menu"><summary aria-label="Actions for ${esc(name)}">⋯</summary><div class="item-menu-panel">${folder?'':`<a class="menu-download" href="/files/download/${id}">Download</a>`}${isAdmin?`<button type="button" data-rename="${kind}" data-id="${id}" data-name="${esc(name)}">Rename</button><button type="button" ${folder?'data-delete-folder':'data-delete-file'}="${id}" class="danger">Delete</button>`:''}</div></details>`;return `<div class="fc-row ${kind}-row" data-kind="${kind}" data-item-id="${id}" data-name="${esc(name)}" data-path="${esc(itemPath)}">${isAdmin?`<input type="checkbox" class="fc-select" aria-label="Select ${esc(name)}">`:'<span class="fc-selection-space"></span>'}<a class="fc-item-main" href="${folder?'/files?path='+encodeURIComponent(itemPath):'/files/download/'+id}"><span class="fc-item-icon ${folder?'folder':''}">${folder?folderIcon:esc(f.kind?.slice(0,5)||'FILE')}</span><span class="fc-item-text"><b>${esc(name)}</b><small>${esc(folder?(f.created_by||'QNAP'):(f.uploaded_display||f.mime_type||''))}</small></span></a><span class="fc-meta fc-size">${folder?'Folder':esc(f.size_label)}</span><span class="fc-meta fc-date">${esc(f.created_at||'—')}</span><div class="fc-actions">${folder?'':`<a class="file-download" href="/files/download/${id}">Download</a>`}${(isAdmin||!folder)?actions:''}</div></div>`}$('#fc-list-body').innerHTML=[...d.folders.map(f=>item(f,'folder')),...d.files.map(f=>item(f,'file'))].join('')||'<div class="fc-empty">No files or folders.</div>';bindRows()}
function bindRows(){$$('[data-delete-folder]').forEach(b=>b.onclick=async()=>{if(!await AppDialog.ask('Delete folder?',{title:'Delete folder',okText:'Delete'}))return;await deleteRow(b)});$$('[data-delete-file]').forEach(b=>b.onclick=async()=>{if(!await AppDialog.ask('Delete file?',{title:'Delete file',okText:'Delete'}))return;await deleteRow(b)});$$('[data-rename]').forEach(b=>b.onclick=()=>askRename(b));$$('[data-rename],[data-delete-file],[data-delete-folder]').forEach(button=>{const handler=button.onclick;button.onclick=()=>Promise.resolve(handler()).catch(report)})}
async function post(u,body={}){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify(body)});const d=await r.json().catch(()=>({}));if(!r.ok||d.ok===false)throw Error(d.error||d.message||'Request failed: HTTP '+r.status);return d}
async function load(forceRefresh=false){try{const url='/api/files/list?path='+encodeURIComponent(path)+(forceRefresh?'&refresh=1':'');const d=await get(url);render(d);$('#fc-select-all').checked=false;updateMass();$('#file-search').dispatchEvent(new Event('input'))}catch(e){$('#qnap-status').classList.remove('is-ok');$('#qnap-status-title').textContent='Refresh failed';$('#qnap-status').title=e.message;throw e}}
async function deleteRow(button){await post('/api/files/mass-delete',{paths:[button.closest('.fc-row').dataset.path]});await load()}
(async()=>{try{session=await get('/api/session');window.renderSessionMessages?.(session);$$('.csrf-field').forEach(x=>x.value=session.csrf);window.renderWorkspaceUser?.(session.user);isAdmin=session.user.role==='admin';$$('.admin-only').forEach(x=>x.hidden=!isAdmin);$('#fc-paste-here').hidden=!(isAdmin&&clipboard);const u=new URL(location.href);path=u.searchParams.get('path')||'';await load();void fcLoadCapacity();fcShowResumeHint()}catch(e){report(e);if(e.message.includes('401'))location='/login?next=/files'}})();
// Revisi 22 — simplified File Center toolbar; upload input IDs and handlers remain unchanged.
const fcNewFolderToggle=$('#fc-new-folder-toggle'),fcNewFolderPanel=$('#new-folder-form'),fcNewMenu=$('#fc-new-menu');
function fcSetFolderPanel(show){
  fcNewFolderPanel.hidden=!show;
  fcNewFolderToggle.setAttribute('aria-expanded',String(show));
  if(show){fcNewMenu.open=false;$('#new-folder-name').focus();}
  else {$('#new-folder-error').hidden=true;$('#new-folder-error').textContent='';}
}
// Only one floating menu should be visible at any moment.
const fcOtherMenus = () => document.querySelectorAll('.profile-dropdown[open],.admin-nav-dropdown[open]');
fcNewMenu.addEventListener('toggle', () => {
  if(fcNewMenu.open){
    fcOtherMenus().forEach(menu => menu.open=false);
    if(!fcNewFolderPanel.hidden) fcSetFolderPanel(false);
  }
});
document.querySelectorAll('.profile-dropdown,.admin-nav-dropdown').forEach(menu => {
  menu.addEventListener('toggle', () => { if(menu.open) fcNewMenu.open=false; });
});
fcNewFolderToggle.addEventListener('click',()=>fcSetFolderPanel(fcNewFolderPanel.hidden));
$('#fc-new-folder-cancel').addEventListener('click',()=>fcSetFolderPanel(false));
document.addEventListener('pointerdown',event=>{
  if(fcNewMenu.open&&!fcNewMenu.contains(event.target))fcNewMenu.open=false;
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'){
    if(fcNewMenu.open){fcNewMenu.open=false;fcNewMenu.querySelector('summary')?.focus();}
    else if(!fcNewFolderPanel.hidden){fcSetFolderPanel(false);fcNewFolderToggle.focus();}
  }
});
$('#new-folder-form').addEventListener('submit',async e=>{e.preventDefault();const input=$('#new-folder-name'),error=$('#new-folder-error'),name=input.value.trim();function show(message){error.textContent=message;error.hidden=!message}show('');if(!name){show('Folder name is required.');input.focus();return}const exists=$$('.fc-row').some(r=>r.dataset.kind==='folder'&&r.dataset.name.toLowerCase()===name.toLowerCase());if(exists){show('Folder already exists.');input.focus();return}try{await post('/files/folder',{name,parent_path:path});input.value='';fcSetFolderPanel(false);await load()}catch(e){show(e.message)}});
$('#qnap-refresh').onclick=()=>{load(true).catch(report);void fcLoadCapacity(true);};$('#file-search').oninput=()=>{const q=$('#file-search').value.toLowerCase();let count=0;$$('.fc-row').forEach(r=>{r.hidden=!r.dataset.name.toLowerCase().includes(q);if(!r.hidden)count++});$('#file-search-status').textContent=q?`${count} results`:''};$('#file-upload-btn').onclick=()=>{fcNewMenu.open=false;$('#file-upload-input').click()};$('#file-upload-input').onchange=()=>upload([...$('#file-upload-input').files]).catch(report);$('#file-upload-folder-btn').onclick=()=>{fcNewMenu.open=false;$('#file-upload-folder-input').click()};$('#file-upload-folder-input').onchange=()=>upload([...$('#file-upload-folder-input').files]).catch(report);
// Revisi 21 — File Center chunked & resumable upload. Legacy endpoint remains on server.
const FC_CHUNK_BYTES = 16 * 1024 * 1024;
const FC_RESUME_STORE = 'polo-file-center-resume-v21';
let fcUploadRunning = false;
const fcSleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function fcSavedSessions(){
  try { return JSON.parse(localStorage.getItem(FC_RESUME_STORE) || '{}') || {}; }
  catch(_) { return {}; }
}
function fcSaveSessions(s){
  try { localStorage.setItem(FC_RESUME_STORE, JSON.stringify(s)); } catch(_) {}
}
function fcResumeKey(file, folder){
  return [session.user.username, folder, file.webkitRelativePath || '', file.name, file.size, file.lastModified].join('\u001f');
}
// A small sampled fingerprint guards accidental resume with a different local file.
// This is NOT a cryptographic integrity guarantee; chunks are validated server-side.
async function fcFingerprint(file){
  const sample=64*1024;
  const first=new Uint8Array(await file.slice(0,Math.min(sample,file.size)).arrayBuffer());
  const last=new Uint8Array(await file.slice(Math.max(0,file.size-sample),file.size).arrayBuffer());
  let hash=0xcbf29ce484222325n;
  for (const bytes of [first,last]) for (const b of bytes){
    hash=BigInt.asUintN(64,(hash ^ BigInt(b))*0x100000001b3n);
  }
  return hash.toString(16).padStart(16,'0');
}
async function fcApi(url, method='GET', body=null){
  const json=body!==null;
  const r=await fetch(url,{
    method,credentials:'same-origin',cache:'no-store',
    headers:{'Accept':'application/json','X-CSRF-Token':session.csrf,...(json?{'Content-Type':'application/json'}:{})},
    ...(json?{body:JSON.stringify(body)}:{}),
  });
  const data=await r.json().catch(()=>({}));
  if(!r.ok||data.ok===false){
    const e=new Error(data.error||`HTTP ${r.status}`);
    e.code=data.code||'';
    e.status=r.status;
    throw e;
  }
  return data;
}
function fcChunkRequest(id, file, from, update){
  const end=Math.min(from+FC_CHUNK_BYTES,file.size);
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();
    xhr.open('PUT',`/api/files/resumable/${encodeURIComponent(id)}/chunk`);
    xhr.timeout=120000;
    xhr.setRequestHeader('X-CSRF-Token',session.csrf);
    xhr.setRequestHeader('X-Chunk-Offset',String(from));
    xhr.setRequestHeader('Content-Type','application/octet-stream');
    xhr.upload.onprogress=e=>{
      if(e.lengthComputable)update(from+e.loaded);
    };
    xhr.onload=()=>{
      let data={};
      try{data=JSON.parse(xhr.responseText||'{}')}catch(_){}
      if(xhr.status>=200&&xhr.status<300&&data.ok===true)resolve(data);
      else{
        const err=new Error(data.error||`Chunk upload failed (HTTP ${xhr.status}).`);
        err.status=xhr.status;err.code=data.code||'';
        reject(err);
      }
    };
    xhr.onerror=()=>reject(Error('Network interrupted during chunk transfer.'));
    xhr.ontimeout=()=>reject(Error('Chunk request timed out.'));
    xhr.onabort=()=>reject(Error('Chunk request interrupted.'));
    xhr.send(file.slice(from,end));
  });
}
function fcShowResumeHint(){
  const saved=fcSavedSessions();
  const current=session?.user?.username;
  const pending=Object.keys(saved).filter(k=>k.startsWith(current+'\u001f'));
  if(pending.length&&!fcUploadRunning){
    const panel=$('#upload-panel');
    panel.hidden=false;
    $('#upload-panel-state').textContent=`${pending.length} unfinished upload(s). Select the same file(s) again to resume.`;
    $('#upload-panel-list').innerHTML='';
  }
}
const FC_QNAP_STATUS = {
  queued: 'Queued for QNAP',
  preparing: 'Preparing destination',
  checking_storage: 'Checking QNAP storage',
  waiting_qnap: 'Waiting for QNAP',
  copying: 'Saving to QNAP',
  flushing: 'Finishing QNAP write',
  verifying: 'Verifying file size',
  verifying_existing: 'Verifying existing file',
  publishing: 'Publishing file',
  indexing: 'Updating file index',
  completed: 'Completed',
  failed: 'QNAP save failed',
};
function fcQnapProgressText(state){
  const stage=FC_QNAP_STATUS[state.qnap_phase]||'Saving to QNAP';
  if(state.qnap_phase==='copying'){
    const percent=state.qnap_total>0?Math.min(100,Math.floor((state.qnap_bytes||0)/state.qnap_total*100)):100;
    return `${stage} ${percent}%`;
  }
  return stage;
}
async function fcResumeUpload(file, folder, progress, phase, saving){
  const key=fcResumeKey(file,folder);
  const saved=fcSavedSessions();
  const fingerprint=await fcFingerprint(file);
  const relative=file.webkitRelativePath||'';
  const initialize={name:file.name,size:file.size,path:folder,relative_path:relative,fingerprint};
  let state;
  if(saved[key]){
    try{
      state=await fcApi('/api/files/resumable/init','POST',{...initialize,upload_id:saved[key].id});
    }catch(err){
      if(err.code==='resume_not_found'){
        // Never blindly restart on an unrecognized session: the file may
        // already be published to QNAP. Ask the user to verify first.
        throw Error('Saved upload session cannot be resumed. Check QNAP before starting a new upload.');
      }
      throw err;
    }
  }else{
    state=await fcApi('/api/files/resumable/init','POST',initialize);
    saved[key]={id:state.upload_id,name:file.name,size:file.size,folder};
    fcSaveSessions(saved);
  }
  const id=state.upload_id;
  progress(state.offset||0,file.size);
  if(state.state==='failed'){
    phase('Retrying QNAP save…');
    state=await fcApi(`/api/files/resumable/${id}/retry`,'POST');
  }
  while(state.state==='uploading' && state.offset<file.size){
    const from=state.offset;
    let sent=false;
    for(let attempt=1;attempt<=4&&!sent;attempt++){
      phase(`Uploading ${Math.round(from/file.size*100)}% · chunk ${Math.floor(from/FC_CHUNK_BYTES)+1}/${Math.ceil(file.size/FC_CHUNK_BYTES)}`);
      try{
        state=await fcChunkRequest(id,file,from,bytes=>progress(bytes,file.size));
        progress(state.offset,file.size);
        sent=true;
      }catch(err){
        // Re-check the committed offset: a lost HTTP response doesn't imply
        // that the server failed to save the chunk.
        try{
          state=await fcApi(`/api/files/resumable/${id}/status`);
          progress(state.offset,file.size);
          if(state.offset>from || state.state!=='uploading'){
            sent=true;break;
          }
        }catch(statusError){
          if(statusError.code==='temp_missing')throw statusError;
        }
        if(err.status && ![408,409,429,500,502,503,504].includes(err.status))throw err;
        if(attempt===4)throw Error(`Chunk upload failed after 4 attempts. Reselect this file to resume. ${err.message}`);
        phase(`Connection interrupted · retry ${attempt}/3`);
        await fcSleep(Math.min(1000*2**(attempt-1),8000));
      }
    }
  }
  if(state.state==='uploading'){
    progress(file.size,file.size);
    phase('Assembling file…');
    state=await fcApi(`/api/files/resumable/${id}/complete`,'POST');
  }
  let statusFailures=0;
  while(['queued','finalizing'].includes(state.state)){
    saving(state);
    await fcSleep(1200);
    try{
      state=await fcApi(`/api/files/resumable/${id}/status`);
      statusFailures=0;
    }catch(err){
      // Do not incorrectly declare a published file failed because one
      // status-poll response was dropped by the network.
      if(err.status && err.status<500 && ![408,429].includes(err.status))throw err;
      if(++statusFailures>=5)throw Error('QNAP status temporarily unreachable. Reselect this file to resume checking without uploading it again.');
      phase(`Reconnecting to QNAP status (${statusFailures}/5)…`);
      await fcSleep(Math.min(1000*statusFailures,4000));
    }
  }
  if(state.state==='completed')saving(state);
  if(state.state==='failed')throw Error(state.error||'QNAP saving failed. Reselect file to retry saving; do not start a duplicate upload.');
  if(state.state!=='completed')throw Error(`Unexpected upload state: ${state.state}`);
  delete saved[key];
  // Refresh may have updated the same localStorage key while waiting.
  const latest=fcSavedSessions();delete latest[key];fcSaveSessions(latest);
  progress(file.size,file.size);
  return state;
}
async function upload(files){
  if(!files.length)return;
  if(fcUploadRunning){
    report(Error('An upload is already running. Please wait until it finishes.'));
    return;
  }
  fcUploadRunning=true;
  const panel=$('#upload-panel'), panelState=$('#upload-panel-state'), panelList=$('#upload-panel-list');
  const folder=path;
  panel.hidden=false;
  panelList.innerHTML=files.map((file,index)=>`<div class="fc-upload-entry"><div class="fc-upload-entry-head"><span title="${esc(file.webkitRelativePath||file.name)}">${esc(file.webkitRelativePath||file.name)}</span><strong id="upload-progress-${index}">Queued</strong></div><div class="fc-upload-stage"><span>Browser to server</span><span id="upload-pct-${index}">0%</span></div><div class="fc-upload-track"><span id="upload-track-${index}" style="width:0%"></span></div><div class="fc-upload-stage fc-upload-qnap-stage"><span id="qnap-stage-${index}">QNAP pending</span><span id="qnap-pct-${index}">0%</span></div><div class="fc-upload-track fc-upload-qnap-track"><span id="qnap-track-${index}" style="width:0%"></span></div></div>`).join('');
  let success=0,failed=0;
  try{
    for(const [index,file] of files.entries()){
      const label=$('#upload-progress-'+index),bar=$('#upload-track-'+index);
      const browserPct=$('#upload-pct-'+index),qnapPct=$('#qnap-pct-'+index);
      const qnapBar=$('#qnap-track-'+index),qnapStage=$('#qnap-stage-'+index);
      const phase=text=>{panelState.textContent=`${file.name}: ${text}`;label.textContent=text;};
      const progress=(bytes,total)=>{
        const percentage=total?Math.min(100,Math.round(bytes/total*100)):100;
        bar.style.width=percentage+'%';
        browserPct.textContent=percentage+'%';
        if(percentage<100)label.textContent=`Uploading ${percentage}%`;
      };
      const saving=state=>{
        const total=Number(state.qnap_total)||0;
        const copied=Math.max(0,Number(state.qnap_bytes)||0);
        const percent=total?Math.min(100,Math.floor(copied/total*100)):(state.state==='completed'?100:0);
        const finished=state.state==='completed';
        qnapBar.style.width=(finished?100:percent)+'%';
        qnapPct.textContent=(finished?100:percent)+'%';
        qnapStage.textContent=fcQnapProgressText(state);
        const text=finished?'Completed':fcQnapProgressText(state);
        panelState.textContent=`${file.name}: ${text}`;
        label.textContent=text;
      };
      try{
        const result=await fcResumeUpload(file,folder,progress,phase,saving);
        success++;
        saving(result);
        label.textContent='Completed';
        panelState.textContent=`Saved to QNAP: ${result.file?.name||file.name}`;
      }catch(error){
        failed++;
        label.textContent='Paused / Failed';
        label.title=error.message;
        panelState.textContent=`${file.name}: ${error.message}`;
        report(error);
      }
    }
    if(success){
      panelState.textContent=`${success} file(s) saved. Refreshing folder…`;
      try{
        await load(true);
        void fcLoadCapacity(true);
        panelState.textContent=failed?`${success} completed; ${failed} need attention. Reselect failed files to resume.`:`Completed: ${success} file(s) saved to QNAP.`;
      }catch(err){
        panelState.textContent=`${success} saved to QNAP; folder refresh failed. Click Refresh.`;
        report(err);
      }
    }else if(failed){
      panelState.textContent=`${failed} upload(s) interrupted. Reselect the same file(s) to resume; check QNAP first if the session expired.`;
    }
  }finally{
    fcUploadRunning=false;
    $('#file-upload-input').value='';
    $('#file-upload-input').dispatchEvent(new Event('edp-dropzone-reset'));
    $('#file-upload-folder-input').value='';
  }
}
$('#upload-panel-close').onclick=()=>$('#upload-panel').hidden=true;$('#fc-select-all')?.addEventListener('change',e=>{$$('.fc-select').forEach(c=>c.checked=e.target.checked);updateMass()});document.addEventListener('change',e=>{if(e.target.matches('.fc-select'))updateMass()});$('#fc-mass-copy').onclick=()=>transfer('copy');$('#fc-mass-cut').onclick=()=>transfer('cut');$('#fc-mass-delete').onclick=massDelete;$('#fc-mass-cancel').onclick=()=>{$$('.fc-select').forEach(x=>x.checked=false);updateMass()};$('#fc-paste-here').onclick=paste;function selectedItems(){return $$('.fc-select:checked').map(x=>({path:x.closest('.fc-row').dataset.path,kind:x.closest('.fc-row').dataset.kind,name:x.closest('.fc-row').dataset.name}))}function updateMass(){const items=selectedItems();$('#fc-mass-bar').hidden=!items.length;$('#fc-mass-count').textContent=`${items.length} selected`;$('#fc-mass-rename').disabled=items.length!==1}
async function massDelete(){const items=selectedItems();if(!items.length||!await AppDialog.ask(`Delete ${items.length} items?`,{title:'Delete selected',okText:'Delete'}))return;const d=await get('/api/files/mass-delete',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({paths:items.map(x=>x.path)})});if(!d.ok&&d.failed)report(Error(d.message||'Some items failed'));await load()}
async function transfer(op){const items=selectedItems();if(!items.length)return;clipboard={operation:op,items};sessionStorage.setItem('polo-file-clipboard',JSON.stringify(clipboard));$('#fc-paste-here').hidden=false;$('#fc-mass-bar').hidden=true;$('#fc-mass-cancel').click()}
async function paste(){if(!clipboard)return;const d=await get('/api/files/transfer',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({operation:clipboard.operation,items:clipboard.items.map(x=>({path:x.path})),destination_path:path})});if(!d.ok){report(Error(d.message||'Transfer failed'));return}clipboard=null;sessionStorage.removeItem('polo-file-clipboard');$('#fc-paste-here').hidden=true;await load()}
$('#fc-mass-rename').onclick=()=>{const selected=$$('.fc-select:checked');if(selected.length===1)selected[0].closest('.fc-row').querySelector('[data-rename]').click()};for(const id of ['fc-mass-delete','fc-paste-here']){const button=$('#'+id),handler=button.onclick;button.onclick=()=>Promise.resolve(handler()).catch(report)}const drop=$('#files-card');let dragDepth=0;drop.addEventListener('dragenter',e=>{if(![...e.dataTransfer.types].includes('Files'))return;e.preventDefault();dragDepth++;drop.classList.add('fc-dragging')});drop.addEventListener('dragover',e=>{if([...e.dataTransfer.types].includes('Files'))e.preventDefault()});drop.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;drop.classList.remove('fc-dragging')}});drop.addEventListener('drop',e=>{
  if(![...(e.dataTransfer?.types||[])].includes('Files'))return;
  e.preventDefault();dragDepth=0;drop.classList.remove('fc-dragging');
  const items=[...(e.dataTransfer.items||[])];
  const hasFolder=items.some(item=>item.kind==='file' && item.webkitGetAsEntry?.()?.isDirectory);
  if(hasFolder){
    report(Error('To upload a folder, select Upload, then Upload folder to preserve its structure.'));
    return;
  }
  if(e.dataTransfer.files.length)upload([...e.dataTransfer.files]).catch(report);
})})();
