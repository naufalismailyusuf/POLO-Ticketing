/* Documentation File Center: capacity, identity and complete grid rows. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const systemFolder = name => ['@recycle','@recently-snapshot'].includes(String(name).toLowerCase());
  let csrf = '', path = '', isAdmin = false, listing = {folders:[],files:[]};
  async function api(url, options = {}) {
    const timeout = !options.method || options.method === 'GET' ? AbortSignal.timeout(15000) : undefined;
    const response = await fetch(url, {credentials:'same-origin',cache:'no-store',signal:timeout,...options});
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) { location.assign('/login?next='+encodeURIComponent(location.pathname+location.search)); throw Error('Authentication required.'); }
    if (!response.ok || data.ok === false) throw Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  const toast = message => { $('#file-toast').textContent = message; };
  function bytes(value) {
    if (!Number.isFinite(value) || value < 0) return '—';
    const units = ['B','KiB','MiB','GiB','TiB','PiB']; let unit = 0;
    while (value >= 1024 && unit < units.length-1) { value /= 1024; unit++; }
    return `${value.toLocaleString(undefined,{maximumFractionDigits:1})} ${units[unit]}`;
  }
  function render() {
    const query = $('#file-search').value.trim().toLocaleLowerCase();
    const folders = listing.folders.filter(item => !systemFolder(item.name) && item.name.toLocaleLowerCase().includes(query));
    const files = listing.files.filter(item => item.original_name.toLocaleLowerCase().includes(query));
    $('#file-breadcrumbs').innerHTML = '<a href="/files">Files</a>'+path.split('/').filter(Boolean).map((part,index,parts) => ` / <a href="/files?path=${encodeURIComponent(parts.slice(0,index+1).join('/'))}">${esc(part)}</a>`).join('');
    $('#fc-list-count').textContent = `${folders.length} folders · ${files.length} files`;
    $('#file-search-status').textContent = query ? `${folders.length+files.length} matches` : '';
    function row(item, folder) {
      const name = folder ? item.name : item.original_name;
      const href = folder ? '/files?path='+encodeURIComponent(path ? path+'/'+name : name) : '/files/download/'+item.id;
      const icon = folder ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7V5h7l2 3h9v12H3z"/></svg>' : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6zM14 3v5h4"/></svg>';
      const itemPath = path ? path+'/'+name : name;
      return `<div class="fc-row"><span aria-hidden="true"></span><a class="fc-item-main" href="${href}"><span class="fc-item-icon${folder?' folder':''}">${icon}</span><span class="fc-item-text"><b>${esc(name)}</b></span></a><span class="fc-meta">${folder?'—':esc(item.size_label||bytes(item.size_bytes))}</span><span class="fc-meta">—</span><span class="fc-actions"><a class="file-download" href="${href}">${folder?'Open':'Download'}</a>${isAdmin ? `<button type="button" data-delete-path="${esc(itemPath)}" data-delete-folder="${folder}" aria-label="Delete ${esc(name)}" title="Delete ${esc(name)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5"/></svg></button>` : ''}</span></div>`;
    }
    $('#fc-list-body').innerHTML = [...folders.map(item=>row(item,true)),...files.map(item=>row(item,false))].join('') || `<p class="fc-empty">${query?'No matching files or folders.':'Folder empty.'}</p>`;
  }
  async function load() {
    const data = await api('/api/files/list?path='+encodeURIComponent(path));
    path = data.path || ''; listing = data; render();
  }
  async function capacity() {
    try {
      const data = await api('/api/files/capacity');
      const total = data.total_bytes, used = data.used_bytes, free = data.free_bytes;
      if (![total,used,free].every(value=>Number.isFinite(value)&&value>=0) || total <= 0) throw Error('Invalid capacity response.');
      const percentage = Math.min(100,Math.max(0,used/total*100));
      $('#fc-capacity-used').textContent = 'Used: '+bytes(used);
      $('#fc-capacity-free').textContent = 'Free: '+bytes(free);
      $('#fc-capacity-total').textContent = 'Total: '+bytes(total);
      $('#fc-capacity-summary').textContent = `${percentage.toFixed(1)}% used`;
      $('#fc-capacity-fill').style.width = percentage+'%';
      $('#fc-capacity-fill').classList.toggle('fc-capacity-high',percentage>=85);
      $('#fc-capacity-bar').setAttribute('aria-valuenow',percentage.toFixed(1));
    } catch(error) {
      $('#fc-capacity-summary').textContent = 'Capacity unavailable';
      for (const [id,label] of [['used','Used'],['free','Free'],['total','Total']]) $('#fc-capacity-'+id).textContent=label+': —';
      $('#fc-capacity-fill').style.width='0%';
      $('#fc-capacity-bar').removeAttribute('aria-valuenow');
      throw error;
    }
  }
  async function refresh() {
    const button = $('#qnap-refresh'); button.disabled = true;
    $('#qnap-status-title').textContent = 'Checking…';
    $('#fc-capacity-summary').textContent = 'Checking capacity…';
    const results = await Promise.allSettled([load(),capacity()]);
    const failure = results.find(result=>result.status==='rejected');
    $('#qnap-status-title').textContent = failure ? 'Unavailable' : 'Connected';
    $('#qnap-status').dataset.state = failure ? 'offline' : 'online';
    $('#qnap-status').classList.toggle('is-ok',!failure);
    if (failure) toast(failure.reason.message); else toast('');
    button.disabled = false;
  }
  async function uploadFile(file) {
    const panel = $('#upload-panel'), list = $('#upload-panel-list');
    panel.hidden = false;
    const entry = document.createElement('div');
    entry.className = 'fc-upload-entry';
    entry.innerHTML = `<div class="fc-upload-entry-head"><span title="${esc(file.name)}">${esc(file.name)}</span><strong>Uploading…</strong></div><div class="fc-upload-stage"><span>Uploading</span><span class="fc-upload-percent">0%</span></div><div class="fc-upload-track" role="progressbar" aria-label="Upload progress for ${esc(file.name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><span style="width:0%"></span></div>`;
    list.append(entry);
    const label = entry.querySelector('.fc-upload-entry-head strong');
    const percent = entry.querySelector('.fc-upload-percent');
    const track = entry.querySelector('.fc-upload-track');
    const fill = track.querySelector('span');
    $('#upload-panel-state').textContent = `Uploading ${file.name}`;
    const form = new FormData(); form.append('path',path); form.append('file',file);
    await new Promise((resolve,reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST','/api/files/upload',true);
      xhr.setRequestHeader('X-CSRF-Token',csrf);
      xhr.upload.onprogress = event => {
        if (!event.lengthComputable) return;
        const value = Math.min(100,Math.round(event.loaded/event.total*100));
        fill.style.width = value+'%'; percent.textContent = value+'%';
        track.setAttribute('aria-valuenow',String(value));
      };
      xhr.upload.onload = () => { fill.style.width = '100%'; percent.textContent = '100%'; track.setAttribute('aria-valuenow','100'); label.textContent = 'Saving to QNAP…'; $('#upload-panel-state').textContent = `Saving to QNAP: ${file.name}`; };
      xhr.onload = () => {
        const data = (() => { try { return JSON.parse(xhr.responseText||'{}'); } catch (_) { return {}; } })();
        if (xhr.status >= 200 && xhr.status < 300 && data.ok !== false) {
          fill.style.width = '100%'; percent.textContent = '100%';
          track.setAttribute('aria-valuenow','100'); label.textContent = 'Completed';
          $('#upload-panel-state').textContent = `Uploaded ${data.file?.name||file.name}`;
          resolve();
        } else {
          const error = new Error(data.error||`Upload failed (HTTP ${xhr.status}).`);
          if (xhr.status === 401) location.assign('/login?next='+encodeURIComponent(location.pathname+location.search));
          entry.classList.add('is-failed'); label.textContent = 'Failed';
          $('#upload-panel-state').textContent = `${file.name}: ${error.message}`;
          reject(error);
        }
      };
      xhr.onerror = () => { const error = Error('Network error during upload.'); entry.classList.add('is-failed'); label.textContent = 'Failed'; $('#upload-panel-state').textContent = `${file.name}: ${error.message}`; reject(error); };
      xhr.ontimeout = () => { const error = Error('Upload timed out.'); entry.classList.add('is-failed'); label.textContent = 'Failed'; $('#upload-panel-state').textContent = `${file.name}: ${error.message}`; reject(error); };
      xhr.send(form);
    });
  }
  async function upload(files) {
    for (const file of files) await uploadFile(file);
    await refresh(); toast('Upload complete.');
  }
  async function boot() {
    const session = await api('/api/session'); csrf = session.csrf;
    const user = session.user; isAdmin = user.role === 'admin';
    for (const id of ['user-label','profile-display']) $('#'+id).textContent = user.display || user.username;
    $('#user-role').textContent = user.role;
    document.querySelectorAll('.csrf-field').forEach(element=>element.value=csrf);
    path = new URL(location).searchParams.get('path') || '';
    $('#upload-panel-close').onclick = () => { $('#upload-panel').hidden = true; };
    $('#file-upload-btn').onclick = () => $('#file-upload-input').click();
    $('#file-upload-input').onchange = event => upload([...event.target.files]).catch(error=>toast(error.message));
    $('#file-upload-folder-btn').onclick = () => $('#file-upload-folder-input').click();
    $('#file-upload-folder-input').onchange = event => upload([...event.target.files]).catch(error=>toast(error.message));
    const form = $('#new-folder-form'), toggle = $('#fc-new-folder-toggle');
    function folderPanel(open) {form.hidden=!open;toggle.setAttribute('aria-expanded',String(open));if(open)$('#new-folder-name').focus();}
    toggle.onclick = () => folderPanel(form.hidden);
    $('#fc-new-folder-cancel').onclick = () => folderPanel(false);
    form.onsubmit = async event => {
      event.preventDefault(); const name = $('#new-folder-name').value.trim(); if (!name) return;
      try {
        await api('/files/folder',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({name,parent_path:path})});
        form.reset(); folderPanel(false); await refresh();
      } catch(error) {toast(error.message);}
    };
    $('#file-search').addEventListener('input',render);
    $('#fc-list-body').addEventListener('click', async event => {
      const button = event.target.closest('[data-delete-path]');
      if (!button || !isAdmin) return;
      const itemPath = button.dataset.deletePath;
      const warning = button.dataset.deleteFolder === 'true' ? ' and all its contents' : '';
      if (!window.confirm(`Delete "${itemPath}"${warning}? This cannot be undone.`)) return;
      button.disabled = true;
      try {
        await api('/api/files/mass-delete',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({paths:[itemPath]})});
        await refresh();
      } catch(error) { toast(error.message); button.disabled = false; }
    });
    $('#qnap-refresh').onclick = refresh;
    const card = $('#files-card');
    card.addEventListener('dragover',event=>{event.preventDefault();card.classList.add('fc-dragging');});
    card.addEventListener('dragleave',event=>{if(!card.contains(event.relatedTarget))card.classList.remove('fc-dragging');});
    card.addEventListener('drop',event=>{event.preventDefault();card.classList.remove('fc-dragging');upload([...event.dataTransfer.files]).catch(error=>toast(error.message));});
    await refresh();
  }
  boot().catch(error=>toast(error.message));
})();
