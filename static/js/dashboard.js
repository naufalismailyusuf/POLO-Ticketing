// Keep the fixed blue app header fully visible without hard-coding its height.
(function syncAppHeaderHeight(){
  const header = document.querySelector('header');
  if(!header) return;
  const setHeight = () => {
    const h = Math.ceil(header.getBoundingClientRect().height);
    document.documentElement.style.setProperty('--app-header-height', `${h}px`);
  };
  setHeight();
  window.addEventListener('resize', setHeight, { passive: true });
})();

document.querySelectorAll('.staff-card').forEach(card=>{card.addEventListener('toggle',()=>{if(!card.open)return;document.querySelectorAll('.staff-card[open]').forEach(other=>{if(other!==card)other.open=false;});});});
document.querySelectorAll('.open-queue-item[data-ticket-target]').forEach(link=>{link.addEventListener('click',function(e){const id=this.dataset.ticketTarget;const ticket=document.getElementById(id);if(!ticket)return;e.preventDefault();const staff=ticket.closest('.staff-card');document.querySelectorAll('.staff-card[open]').forEach(other=>{if(other!==staff)other.open=false;});if(staff)staff.open=true;ticket.open=true;setTimeout(()=>ticket.scrollIntoView({behavior:'smooth',block:'center'}),40);});});
document.querySelectorAll('.notif-item[data-ticket-target]').forEach(link=>{link.addEventListener('click',function(e){const id=this.dataset.ticketTarget;if(!id||id==='ticket-')return;const ticket=document.getElementById(id);if(!ticket)return;e.preventDefault();document.querySelector('.notif-wrap')?.removeAttribute('open');const staff=ticket.closest('.staff-card');document.querySelectorAll('.staff-card[open]').forEach(other=>{if(other!==staff)other.open=false;});if(staff)staff.open=true;ticket.open=true;setTimeout(()=>ticket.scrollIntoView({behavior:'smooth',block:'center'}),40);});});

const chatFeed=document.getElementById('chatFeed');
const chatRefresh=document.getElementById('chatRefresh');
function renderChat(items){
  if(!chatFeed)return;
  if(!items.length){chatFeed.innerHTML='<div class="chat-empty">No messages yet. Start the internal support conversation.</div>';return;}
  const mine=document.body.dataset.user || '';
  chatFeed.innerHTML='';
  items.forEach(m=>{
    const box=document.createElement('div'); box.className='chat-msg'+(m.username===mine?' me':'');
    const head=document.createElement('div'); head.className='chat-msg-head';
    const user=document.createElement('span'); user.className='chat-msg-user'; user.textContent=m.display;
    const tm=document.createElement('span'); tm.className='chat-msg-time'; tm.textContent=m.created_at;
    const body=document.createElement('div'); body.className='chat-msg-text'; body.textContent=m.message;
    head.append(user,tm); box.append(head,body); chatFeed.appendChild(box);
  });
  chatFeed.scrollTop=chatFeed.scrollHeight;
}
async function refreshChat(){try{const res=await fetch('/chat/messages',{cache:'no-store'});if(res.ok)renderChat(await res.json());}catch(e){}}
if(chatRefresh)chatRefresh.addEventListener('click',refreshChat);
setInterval(refreshChat,10000);

// v36: opening Notification Center automatically marks all notifications as read.
(function(){
  const wrap=document.querySelector('.notif-wrap');
  if(!wrap) return;
  let posted=false;
  function setReadUI(){
    document.querySelectorAll('.notif-item.unread').forEach(el=>el.classList.remove('unread'));
    const label=document.querySelector('.notif-unread-label');
    if(label) label.textContent='0 unread';
    const statCount=document.querySelector('.notification-stat-count');
    if(statCount) statCount.textContent='0';
    const statLabel=document.querySelector('.notification-stat-label');
    if(statLabel) statLabel.textContent='All caught up';
    document.querySelector('.notif-count')?.remove();
    document.querySelector('.notif-mark-form')?.remove();
  }
  async function markAllRead(){
    const form=document.querySelector('.notif-mark-form');
    const csrf=form?.querySelector('input[name="csrf"]')?.value;
    if(!csrf || posted) { if(posted) setReadUI(); return; }
    posted=true;
    try{
      const res=await fetch('/notifications/read',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'fetch'},body:new URLSearchParams({csrf})});
      if(res.ok || res.redirected) setReadUI();
    }catch(e){}
  }
  wrap.addEventListener('toggle',()=>{ if(wrap.open) markAllRead(); });
  const btn=document.querySelector('.notif-mark');
  if(btn) btn.addEventListener('click',(e)=>{e.preventDefault();markAllRead();});
})();

// Bulk ticket assignment is handled here instead of inline in dashboard.html.
// The handlers use event delegation so they continue working after SPA navigation
// replaces <main class="wrap">.
(function initBulkTicketAssignment(){
  if(window.__poloBulkAssignmentV2) return;
  window.__poloBulkAssignmentV2=true;

  let processing=false;
  let modalTimer=null;
  let lastBulkAction='';
  let lastBulkWasSuccessful=false;

  const q=(selector, root=document)=>root.querySelector(selector);
  const qa=(selector, root=document)=>[...root.querySelectorAll(selector)];
  const selectedIds=()=>qa('.queue-ticket-check:checked')
    .map(el=>Number(el.value))
    .filter(Number.isInteger);

  function idleLabel(){
    const action=q('#bulk-action');
    if(!action) return 'Assign';
    return action.value==='close'?'Close':action.value==='assign_close'?'Assign + Close':'Assign';
  }

  function syncBulkUI(){
    const bar=q('#bulk-action-bar');
    const count=q('#bulk-selected-count');
    const action=q('#bulk-action');
    const support=q('#bulk-assign-owner');
    const button=q('#bulk-action-btn');
    const selectAll=q('#queue-select-all');
    if(!bar || !action || !button) return;

    const ids=selectedIds();
    const needsOwner=action.value!=='close';
    if(count) count.textContent=String(ids.length);
    bar.hidden=ids.length===0;

    if(support){
      support.hidden=!needsOwner;
      support.disabled=!needsOwner;
    }
    button.disabled=processing || !ids.length || (needsOwner && !support?.value);
    button.textContent=processing?'Processing…':idleLabel();

    const checks=qa('.queue-ticket-check');
    if(selectAll){
      selectAll.checked=checks.length>0 && ids.length===checks.length;
      selectAll.indeterminate=ids.length>0 && ids.length<checks.length;
    }
  }

  function resetBulkControls(){
    processing=false;
    qa('.queue-ticket-check').forEach(el=>{el.checked=false;});
    const selectAll=q('#queue-select-all');
    if(selectAll){selectAll.checked=false;selectAll.indeterminate=false;}
    syncBulkUI();
  }

  function hardReloadDashboard(){
    const url=new URL(window.location.href);
    url.searchParams.set('_ticket_refresh',String(Date.now()));
    window.location.replace(url.href);
  }

  function closeBulkResult(options={}){
    clearTimeout(modalTimer);
    const modal=q('#bulk-result-modal');
    if(modal){modal.hidden=true;modal.setAttribute('aria-hidden','true');}
    document.body.classList.remove('drawer-open');

    const reload=options.reload ?? (lastBulkWasSuccessful && (lastBulkAction==='assign' || lastBulkAction==='assign_close'));
    const shouldReload=reload;
    lastBulkWasSuccessful=false;
    lastBulkAction='';
    if(shouldReload) hardReloadDashboard();
  }

  function showBulkResult(data, transportError){
    const modal=q('#bulk-result-modal');
    if(!modal) return;
    const icon=q('#bulk-result-icon');
    const title=q('#bulk-result-title');
    const summary=q('#bulk-result-summary');
    const reasonsBox=q('#bulk-result-reasons');
    const okCount=Number(data?.succeeded||0);
    const failCount=Number(data?.failed||0);
    const failedItems=Array.isArray(data?.failed_items)?data.failed_items:[];
    const failed=Boolean(transportError || failCount>0 || !data?.ok);

    modal.classList.toggle('is-success',!failed);
    modal.classList.toggle('is-failure',failed);
    if(icon){
      icon.innerHTML=failed
        ? '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>'
        : '<svg viewBox="0 0 24 24"><path d="M5 13l4 4L19 7"/></svg>';
    }
    if(title) title.textContent=failed?'Gagal':'Berhasil';
    if(summary){
      summary.textContent=transportError
        ? (data?.error || 'Server tidak dapat menyelesaikan aksi.')
        : `${okCount} berhasil, ${failCount} gagal`;
    }
    if(reasonsBox){
      reasonsBox.innerHTML='';
      const reasons=failedItems.slice(0,10);
      if(transportError && data?.error) reasons.push({ticket_id:'Server',reason:data.error});
      reasons.forEach(item=>{
        const row=document.createElement('div');
        const label=document.createElement('b');
        const reason=document.createElement('span');
        label.className='bulk-result-bad';
        label.textContent=item.no_tiket || item.ticket_id || 'Ticket';
        reason.textContent=': '+(item.reason || 'Unknown server error.');
        row.append(label,reason);
        reasonsBox.appendChild(row);
      });
      reasonsBox.hidden=reasons.length===0;
    }
    modal.hidden=false;
    modal.setAttribute('aria-hidden','false');
    document.body.classList.add('drawer-open');
    clearTimeout(modalTimer);
    modalTimer=setTimeout(()=>closeBulkResult(),5000);
  }

  function updateAssignedQueueItems(items, fallbackOwner){
    const updates=Array.isArray(items)?items:[];
    updates.forEach(item=>{
      const ticketId=Number(item?.ticket_id);
      if(!Number.isInteger(ticketId)) return;
      const owner=String(item?.support_id || fallbackOwner || '').trim();
      if(!owner) return;
      const wrap=document.querySelector(`.open-queue-item-wrap[data-ticket-id="${ticketId}"]`);
      if(!wrap) return;

      const ownerBadge=wrap.querySelector('.open-owner, .unassigned-badge');
      if(ownerBadge){
        ownerBadge.className='open-owner';
        ownerBadge.textContent=owner;
      }else{
        const top=wrap.querySelector('.open-queue-top');
        const sla=top?.querySelector('.sla-badge');
        if(top){
          const badge=document.createElement('span');
          badge.className='open-owner';
          badge.textContent=owner;
          if(sla) top.insertBefore(badge,sla); else top.appendChild(badge);
        }
      }
    });
  }

  async function executeBulkAction(){
    if(processing) return;
    const action=q('#bulk-action');
    const support=q('#bulk-assign-owner');
    const result=q('#bulk-action-result');
    if(!action) return;

    const ids=selectedIds();
    const mode=action.value;
    if(!ids.length || (mode!=='close' && !support?.value)){
      syncBulkUI();
      return;
    }

    processing=true;
    lastBulkAction=mode;
    lastBulkWasSuccessful=false;
    syncBulkUI();
    if(result){result.textContent='';result.classList.remove('has-error');}

    try{
      const payload={ticket_ids:ids,action:mode};
      if(mode!=='close') payload.support_id=support.value;

      const res=await fetch('/api/tickets/bulk',{
        method:'POST',
        headers:{
          'Content-Type':'application/json',
          'X-CSRF-Token':q('input[name="csrf"]')?.value || '{{ csrf }}',
          'Accept':'application/json'
        },
        credentials:'same-origin',
        cache:'no-store',
        body:JSON.stringify(payload)
      });

      const data=await res.json().catch(()=>({ok:false,error:`HTTP ${res.status} without JSON response`}));
      if(!res.ok || !data.ok){
        if(result){result.textContent=data.error || `Server rejected the bulk action (HTTP ${res.status}).`;result.classList.add('has-error');}
        processing=false;
        syncBulkUI();
        showBulkResult(data,true);
        return;
      }

      const okCount=Number(data.succeeded||0);
      const failCount=Number(data.failed||0);
      const successItems=Array.isArray(data.successful_items)?data.successful_items:[];

      if(result){
        result.textContent=`${okCount} berhasil, ${failCount} gagal.`;
        result.classList.toggle('has-error',failCount>0);
      }

      if(mode==='assign' || mode==='assign_close'){
        updateAssignedQueueItems(successItems, support?.value || '');
      }

      if(mode==='close' || mode==='assign_close'){
        const closedIds=new Set(successItems.map(item=>Number(item.ticket_id)).filter(Number.isInteger));
        qa('.open-queue-item-wrap').forEach(wrap=>{
          const id=Number(q('.queue-ticket-check',wrap)?.value || wrap.dataset.ticketId);
          if(closedIds.has(id)) wrap.remove();
        });
      }

      resetBulkControls();
      lastBulkWasSuccessful=okCount>0 && failCount===0;
      showBulkResult(data,false);
    }catch(err){
      const message=err?.message || 'Network error while sending bulk action.';
      if(result){result.textContent=message;result.classList.add('has-error');}
      processing=false;
      syncBulkUI();
      showBulkResult({ok:false,error:message,succeeded:0,failed:ids.length,failed_items:ids.map(id=>({ticket_id:id,reason:message}))},true);
    }
  }

  document.addEventListener('change',event=>{
    const target=event.target;
    if(target.matches('.queue-ticket-check')) syncBulkUI();
    if(target.matches('#queue-select-all')){
      qa('.queue-ticket-check').forEach(el=>{el.checked=target.checked;});
      syncBulkUI();
    }
    if(target.matches('#bulk-action, #bulk-assign-owner')) syncBulkUI();
  });

  document.addEventListener('click',event=>{
    if(event.target.closest('#bulk-action-btn')){
      event.preventDefault();
      executeBulkAction();
      return;
    }
    if(event.target.closest('#bulk-result-ok')){
      event.preventDefault();
      closeBulkResult();
      return;
    }
    const modal=event.target.closest('#bulk-result-modal');
    if(modal && event.target===modal){
      closeBulkResult();
    }
  });

  document.addEventListener('keydown',event=>{
    const modal=q('#bulk-result-modal');
    if(event.key==='Escape' && modal && !modal.hidden) closeBulkResult();
  });

  syncBulkUI();
})();
