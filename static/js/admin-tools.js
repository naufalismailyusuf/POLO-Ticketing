(()=>{const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)],esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));let session=null;
function toast(message,type='ok'){const stack=$('#toast-stack');if(!stack){console[type==='err'?'error':'log'](message);return}const item=document.createElement('div');item.className=`toast toast-${type}`;item.innerHTML=`<span class="toast-message">${esc(message)}</span><button type="button" class="toast-close" aria-label="Close notification">×</button>`;item.querySelector('button').onclick=()=>item.remove();stack.appendChild(item);setTimeout(()=>item.remove(),4200)}
async function api(url,options={}){const r=await fetch(url,{cache:'no-store',...options});let d={};try{d=await r.json()}catch{}if(!r.ok||d.ok===false)throw Error(d.error||`HTTP ${r.status}`);return d}
function initTheme(){const btn=$('#theme-toggle');btn?.addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;try{localStorage.setItem('polo-theme',theme);localStorage.setItem('theme',theme)}catch(_){}});const header=$('.app-header');const sync=()=>header?.classList.toggle('is-scrolled',window.scrollY>8);sync();window.addEventListener('scroll',sync,{passive:true});$('#mobile-menu-toggle')?.addEventListener('click',e=>{const nav=$('#mobile-nav');const open=nav.hidden;nav.hidden=!open;e.currentTarget.setAttribute('aria-expanded',String(open))})}
function render(data){$('#tools-state').textContent='Saved';$('#support-settings').innerHTML=(data.support_users||[]).map(name=>{const checked=data.settings?.[name]?'checked':'';return `<div class="support-setting-row" data-support="${esc(name)}"><div><strong>${esc(name)}</strong><span>${data.settings?.[name]?'Evidence required':'Old rule'}</span></div><label class="switch-control"><input type="checkbox" ${checked} aria-label="Require Evidence for ${esc(name)}"><span></span></label></div>`}).join('');$$('.support-setting-row input').forEach(input=>input.addEventListener('change',()=>save(input)))}
async function save(input){const row=input.closest('.support-setting-row');const support_id=row.dataset.support;input.disabled=true;$('#tools-state').textContent='Saving';try{const data=await api('/api/admin/tools/evidence',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({support_id,require_evidence:input.checked})});render(data);toast(`${support_id} updated.`)}catch(e){input.checked=!input.checked;toast(e.message,'err');$('#tools-state').textContent='Error'}finally{input.disabled=false}}

function renderDivisions(data){
  const host=$('#division-settings');
  host.innerHTML=(data.divisions||[]).map(d=>`<div class="division-setting-row" data-id="${Number(d.id)}"><div class="division-setting-info"><strong>${esc(d.name)}</strong><small>${d.is_active?'Active':'Inactive'}</small></div><div class="division-setting-actions"><button type="button" class="button" data-division-action="rename">Rename</button><button type="button" class="button" data-division-action="toggle">${d.is_active?'Deactivate':'Activate'}</button></div></div>`).join('')||'<p class="division-admin-hint">No divisions configured.</p>';
  host.querySelectorAll('[data-division-action]').forEach(button=>button.onclick=()=>editDivision(button,data.divisions));
}
async function editDivision(button,divisions){
  const row=button.closest('[data-id]');const item=divisions.find(d=>d.id===Number(row.dataset.id));if(!item)return;
  const action=button.dataset.divisionAction;
  let payload;
  if(action==='rename'){
    const name=window.prompt('Rename Division',item.name);
    if(name===null||name.trim()===item.name)return;
    payload={name:name.trim()};
  }else{
    if(!window.confirm(`${item.is_active?'Deactivate':'Activate'} ${item.name}? Existing tickets will retain their division.`))return;
    payload={is_active:!item.is_active};
  }
  button.disabled=true;
  try{const data=await api(`/api/admin/divisions/${item.id}`,{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify(payload)});renderDivisions(data);toast(data.message||'Division updated.');}catch(e){toast(e.message,'err');button.disabled=false}
}
async function initDivisions(){
  renderDivisions(await api('/api/admin/divisions'));
  $('#division-add-form').addEventListener('submit',async event=>{
    event.preventDefault();const input=$('#division-name-new');const button=$('#division-add-form button[type="submit"]');button.disabled=true;
    try{const d=await api('/api/admin/divisions',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({name:input.value.trim()})});input.value='';renderDivisions(d);toast(d.message||'Division added.')}catch(e){toast(e.message,'err')}finally{button.disabled=false}
  });
}

document.addEventListener('DOMContentLoaded',async()=>{initTheme();try{session=await api('/api/session');if(session.user.role!=='admin'){location='/';return}$$('.csrf-field').forEach(x=>x.value=session.csrf);$('#logout-csrf').value=session.csrf;$$('.admin-only').forEach(x=>x.hidden=false);$('#user-label').textContent=session.user.display;$('#user-role').textContent=session.user.role;(session.messages||[]).forEach(([type,msg])=>toast(msg,type));render(await api('/api/admin/tools/evidence'));await initDivisions()}catch(e){if(String(e.message).includes('Authentication'))location='/login?next='+encodeURIComponent(location.pathname);else toast(e.message,'err')}})})();
