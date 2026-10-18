(() => {
  'use strict';
  const items = new Map();
  let active = null, sequence = 0;
  const reduce = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const text = element => [...(element?.childNodes || [])].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
  function close(immediate = false) {
    if (!active) return;
    const item = active; active = null;
    item.trigger.setAttribute('aria-expanded','false');
    item.trigger.removeAttribute('aria-activedescendant');
    item.wrapper.classList.remove('is-open');
    item.menu.classList.add('is-closing');
    const finish = () => { item.timer = null; if (typeof item.menu.hidePopover === 'function' && item.menu.matches(':popover-open')) item.menu.hidePopover(); item.menu.hidden = true; item.menu.classList.remove('is-closing'); };
    if (immediate || reduce()) finish(); else item.timer = window.setTimeout(finish, 140);
  }
  function uiScale() { return Number.parseFloat(window.getComputedStyle?.(document.documentElement).zoom || '1') || 1; }
  function position(item) {
    const box = item.trigger.getBoundingClientRect(), below = window.innerHeight - box.bottom - 12, above = box.top - 12;
    const up = below < 180 && above > below, scale = uiScale();
    item.menu.style.width = `${Math.max(120, box.width) / scale}px`;
    item.menu.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - Math.max(120, box.width) - 8)) / scale}px`;
    item.menu.style.maxHeight = `${Math.max(80, Math.min(280, up ? above : below)) / scale}px`;
    item.menu.style.top = up ? 'auto' : `${(box.bottom + 6) / scale}px`;
    item.menu.style.bottom = up ? `${(window.innerHeight - box.top + 6) / scale}px` : 'auto';
    item.menu.dataset.direction = up ? 'up' : 'down';
  }
  function highlight(item, index) {
    const option = item.select.options[index];
    if (!option || option.disabled || option.parentElement?.disabled) return;
    item.index = index;
    [...item.menu.children].forEach((node, i) => node.classList.toggle('is-pointed',i === index));
    const node = item.menu.children[index];
    item.trigger.setAttribute('aria-activedescendant',node.id);
    node.scrollIntoView?.({block:'nearest'});
  }
  function sync(item) {
    const select = item.select, option = select.options[select.selectedIndex];
    item.value.textContent = option?.textContent || 'Choose';
    item.trigger.disabled = select.disabled || select.options.length === 0;
    item.trigger.setAttribute('aria-label',`${item.label}: ${item.value.textContent}`);
    item.trigger.setAttribute('aria-required',String(select.required));
    item.trigger.setAttribute('aria-invalid',String(!select.validity.valid));
    const signature = JSON.stringify([...select.options].map(o => [o.textContent,o.value,o.disabled || o.parentElement?.disabled, o.hidden]));
    if (signature !== item.signature) {
      item.signature = signature;
      item.menu.replaceChildren();
      [...select.options].forEach((o,i) => {
        const row = document.createElement('div'); row.id = `${item.menu.id}-${i}`;
        row.className = 'ui-select-option'; row.setAttribute('role','option');
        row.textContent = o.textContent; row.hidden = o.hidden;
        row.setAttribute('aria-disabled',String(Boolean(o.disabled || o.parentElement?.disabled)));
        row.addEventListener('pointerdown',e => e.preventDefault());
        row.addEventListener('pointermove',() => highlight(item,i));
        row.addEventListener('click',() => choose(item,i));
        item.menu.appendChild(row);
      });
    }
    [...item.menu.children].forEach((row,i) => row.setAttribute('aria-selected',String(i === select.selectedIndex)));
    if (active === item) { if (item.trigger.disabled) close(true); else position(item); }
  }
  function available(item) { return [...item.select.options].map((o,i) => ({o,i})).filter(({o}) => !o.disabled && !o.hidden && !o.parentElement?.disabled).map(({i}) => i); }
  function open(item) {
    if (item.trigger.disabled) return;
    close(true);
    window.clearTimeout(item.timer); item.timer = null;
    sync(item); active = item;
    const dialog = item.select.closest('dialog');
    (dialog?.open ? dialog : document.body).appendChild(item.menu);
    item.menu.hidden = false; item.menu.classList.remove('is-closing');
    if (typeof item.menu.showPopover === 'function') item.menu.showPopover();
    item.wrapper.classList.add('is-open'); item.trigger.setAttribute('aria-expanded','true');
    position(item);
    const enabled = available(item);
    highlight(item,enabled.includes(item.select.selectedIndex) ? item.select.selectedIndex : enabled[0]);
  }
  function choose(item,index) {
    const o = item.select.options[index];
    if (!o || o.disabled || o.hidden || o.parentElement?.disabled) return;
    const changed = item.select.selectedIndex !== index;
    item.select.selectedIndex = index;
    sync(item); close();
    if (changed) item.select.dispatchEvent(new Event('change',{bubbles:true}));
    item.trigger.focus();
  }
  function keys(item,event) {
    const key = event.key, list = available(item);
    if (key === 'Tab') { close(true); return; }
    if (key === 'Escape') { if (active === item) { event.preventDefault(); event.stopPropagation(); close(); } return; }
    if (['ArrowDown','ArrowUp','Home','End'].includes(key)) {
      event.preventDefault(); const wasOpen = active === item;
      if (!wasOpen) open(item);
      let pos = list.indexOf(item.index);
      if (key === 'Home') pos = 0;
      else if (key === 'End') pos = list.length - 1;
      else if (wasOpen) pos = Math.max(0,Math.min(list.length - 1,pos + (key === 'ArrowDown' ? 1 : -1)));
      highlight(item,list[pos]); return;
    }
    if (key === 'Enter' || key === ' ') { event.preventDefault(); active === item ? choose(item,item.index) : open(item); return; }
    if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault(); if (active !== item) open(item);
      const now = Date.now(); item.search = now - item.searchTime > 650 ? key : item.search + key; item.searchTime = now;
      const index = list.find(i => item.select.options[i].textContent.trim().toLowerCase().startsWith(item.search.toLowerCase()));
      if (index !== undefined) highlight(item,index);
    }
  }
  function enhance(select) {
    if (items.has(select) || select.hidden || select.hasAttribute('data-native-select') || select.id === 'wf-status' || select.dataset.enhanced === 'true' || select.multiple || select.size > 1) return;
    const wrapper = document.createElement('span'); wrapper.className = 'ui-select-control';
    select.parentNode.insertBefore(wrapper,select); wrapper.appendChild(select);
    const label = select.getAttribute('aria-label') || text(select.labels?.[0]) || 'Select';
    const trigger = document.createElement('button'); trigger.type = 'button'; trigger.className = 'ui-select-trigger';
    trigger.setAttribute('role','combobox'); trigger.setAttribute('aria-haspopup','listbox'); trigger.setAttribute('aria-expanded','false');
    const value = document.createElement('span'); value.className = 'ui-select-value'; trigger.appendChild(value);
    const arrow = document.createElement('span'); arrow.className = 'ui-select-arrow'; arrow.setAttribute('aria-hidden','true'); trigger.appendChild(arrow); wrapper.appendChild(trigger);
    const menu = document.createElement('div'); menu.id = `ui-select-${++sequence}`; menu.className = 'ui-select-menu'; menu.hidden = true;
    menu.setAttribute('role','listbox'); menu.setAttribute('aria-label',label);
    if (typeof menu.showPopover === 'function') menu.setAttribute('popover','manual');
    trigger.setAttribute('aria-controls',menu.id);
    select.dataset.enhanced = 'true';
    document.body.appendChild(menu);
    select.classList.add('ui-select-native'); select.tabIndex = -1; select.setAttribute('aria-hidden','true');
    const item = {select,wrapper,trigger,value,menu,label,index:0,signature:'',timer:null,search:'',searchTime:0}; items.set(select,item);
    trigger.addEventListener('click',() => active === item ? close() : open(item)); trigger.addEventListener('keydown',event => keys(item,event));
    select.addEventListener('change',() => sync(item)); select.addEventListener('focus',() => trigger.focus());
    select.addEventListener('invalid',event => { event.preventDefault(); sync(item); trigger.focus(); });
    new MutationObserver(() => sync(item)).observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','selected','label','value','hidden','required']});
    sync(item);
  }
  function syncSelects() { document.querySelectorAll('select').forEach(enhance); for (const item of items.values()) { if (item.select.isConnected) sync(item); else { if (active === item) close(true); item.menu.remove(); items.delete(item.select); } } }
  function init() {
    syncSelects();
    new MutationObserver(records => { if (records.some(r => [...r.addedNodes].some(n => n.nodeType === 1 && (n.matches?.('select') || n.querySelector?.('select'))))) syncSelects(); }).observe(document.body,{childList:true,subtree:true});
    document.addEventListener('pointerdown',e => { if (active && !active.wrapper.contains(e.target) && !active.menu.contains(e.target)) close(); });
    document.addEventListener('reset',() => window.setTimeout(syncSelects,0));
    document.addEventListener('scroll',e => { if (active && !active.menu.contains(e.target)) close(true); },true);
    document.addEventListener('close',() => close(true),true);
    window.addEventListener('resize',() => { if (active) position(active); });
  }
  window.TicketUI = {syncSelects,uiScale};
  document.addEventListener('DOMContentLoaded',init);
})();

(()=> {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
  let active = null;
  function closeDialog(result) {
    if (!active) return;
    const current = active;
    active = null;
    current.root.classList.remove('is-open');
    window.setTimeout(() => {
      current.root.remove();
      current.resolve(result);
    }, 170);
  }
  function base(options = {}) {
    if (active) closeDialog(null);
    const root = document.createElement('div');
    root.className = 'app-dialog';
    root.innerHTML = `<div class="app-dialog__panel" role="dialog" aria-modal="true" aria-labelledby="app-dialog-title">
      <div class="app-dialog__head"><h2 id="app-dialog-title">${esc(options.title || '')}</h2>${options.message ? `<p>${esc(options.message)}</p>` : ''}</div>
      <form class="app-dialog__form">
        ${options.input ? `<input class="app-dialog__input" name="value" type="text" maxlength="180" value="${esc(options.value || '')}" autocomplete="off" autofocus>` : ''}
        <div class="app-dialog__error" hidden></div>
        <div class="app-dialog__actions"><button class="button" type="button" data-cancel>${esc(options.cancelText || 'Cancel')}</button><button class="button primary" type="submit">${esc(options.okText || 'OK')}</button></div>
      </form>
    </div>`;
    document.body.appendChild(root);
    return new Promise(resolve => {
      active = {root, resolve};
      const panel = root.querySelector('.app-dialog__panel');
      const form = root.querySelector('form');
      const input = root.querySelector('input');
      const error = root.querySelector('.app-dialog__error');
      const showError = message => {
        error.textContent = message;
        error.hidden = !message;
      };
      root.addEventListener('pointerdown', event => { if (!panel.contains(event.target)) closeDialog(null); });
      root.querySelector('[data-cancel]').addEventListener('click', () => closeDialog(null));
      root.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
          event.preventDefault();
          closeDialog(null);
        }
      });
      form.addEventListener('submit', event => {
        event.preventDefault();
        if (!options.input) return closeDialog(true);
        const value = input.value.trim();
        if (!value) return showError(options.emptyText || 'This field is required.');
        const validation = options.validate?.(value);
        if (validation) return showError(validation);
        closeDialog(value);
      });
      requestAnimationFrame(() => {
        root.classList.add('is-open');
        (input || root.querySelector('button[type="submit"]'))?.focus();
        input?.select();
      });
    });
  }
  window.AppDialog = {
    message(message, title = 'Notice') {
      return base({title, message, okText:'OK', cancelText:'Close'});
    },
    ask(message, options = {}) {
      return base({title:options.title || 'Confirm', message, okText:options.okText || 'Confirm', cancelText:options.cancelText || 'Cancel'});
    },
    input(message, options = {}) {
      return base({title:options.title || message, message:options.title ? message : '', input:true, value:options.value || '', okText:options.okText || 'Save', cancelText:options.cancelText || 'Cancel', emptyText:options.emptyText, validate:options.validate});
    }
  };
})();
