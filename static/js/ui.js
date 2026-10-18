(function () {
  'use strict';

  const SELECTOR = 'select:not([hidden]):not([data-native-select]):not([data-enhanced])';
  const menus = new Set();
  let openUi = null;

  function closeOpen() {
    if (!openUi) return;
    openUi.menu.classList.remove('is-open');
    openUi.trigger.setAttribute('aria-expanded', 'false');
    openUi.shell.classList.remove('is-open');
    openUi = null;
  }

  function placeMenu(ui) {
    const rect = ui.trigger.getBoundingClientRect();
    const gap = 6;
    const viewportPadding = 8;
    const maxHeight = Math.min(300, window.innerHeight - viewportPadding * 2);
    let top = rect.bottom + gap;

    ui.menu.style.width = rect.width + 'px';
    ui.menu.style.maxHeight = maxHeight + 'px';
    ui.menu.style.left = Math.max(viewportPadding, Math.min(rect.left, window.innerWidth - rect.width - viewportPadding)) + 'px';

    if (top + Math.min(maxHeight, 300) > window.innerHeight - viewportPadding) {
      const above = rect.top - gap;
      ui.menu.style.top = Math.max(viewportPadding, above) + 'px';
      ui.menu.dataset.openDirection = 'up';
      ui.menu.style.transformOrigin = 'bottom center';
    } else {
      ui.menu.style.top = top + 'px';
      ui.menu.dataset.openDirection = 'down';
      ui.menu.style.transformOrigin = 'top center';
    }
  }

  function syncUi(ui) {
    const option = ui.select.options[ui.select.selectedIndex];
    ui.label.textContent = option ? option.textContent.trim() : 'Select...';
    ui.options.forEach(item => {
      item.button.classList.toggle('is-selected', option && item.value === option.value);
      item.button.setAttribute('aria-selected', String(option && item.value === option.value));
    });
  }

  function openUiMenu(ui) {
    if (ui.select.disabled) return;
    if (openUi && openUi !== ui) closeOpen();
    openUi = ui;
    placeMenu(ui);
    ui.shell.classList.add('is-open');
    ui.trigger.setAttribute('aria-expanded', 'true');
    ui.menu.classList.add('is-open');
  }

  function choose(ui, optionIndex) {
    const option = ui.select.options[optionIndex];
    if (!option || option.disabled) return;
    ui.select.value = option.value;
    ui.select.dispatchEvent(new Event('change', { bubbles: true }));
    syncUi(ui);
    closeOpen();
  }

  function build(select) {
    if (select.dataset.enhanced === 'true') return;
    select.dataset.enhanced = 'true';

    const shell = document.createElement('div');
    shell.className = 'select-ui';
    select.parentNode.insertBefore(shell, select);
    shell.appendChild(select);

    select.classList.add('select-native-hidden');
    select.tabIndex = -1;

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'select-trigger';
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.disabled = select.disabled;

    const label = document.createElement('span');
    label.className = 'select-trigger-label';

    const icon = document.createElement('span');
    icon.className = 'select-chevron';
    icon.setAttribute('aria-hidden', 'true');

    trigger.append(label, icon);
    shell.appendChild(trigger);

    const menu = document.createElement('div');
    menu.className = 'select-menu';
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-label', select.getAttribute('aria-label') || select.name || 'Select option');
    document.body.appendChild(menu);
    menus.add(menu);

    const items = [];
    Array.from(select.options).forEach((option, index) => {
      const itemButton = document.createElement('button');
      itemButton.type = 'button';
      itemButton.className = 'select-option';
      itemButton.setAttribute('role', 'option');
      itemButton.setAttribute('aria-selected', 'false');
      itemButton.disabled = option.disabled;
      itemButton.textContent = option.textContent.trim();
      itemButton.addEventListener('click', () => choose(ui, index));
      menu.appendChild(itemButton);
      items.push({ button: itemButton, value: option.value, index });
    });

    const ui = { shell, select, trigger, label, icon, menu, options: items };
    syncUi(ui);

    trigger.addEventListener('click', () => {
      if (openUi === ui) closeOpen();
      else openUiMenu(ui);
    });

    trigger.addEventListener('keydown', event => {
      const key = event.key;
      if (key === 'Enter' || key === ' ' || key === 'ArrowDown' || key === 'ArrowUp') {
        event.preventDefault();
        if (openUi !== ui) openUiMenu(ui);
        const current = select.selectedIndex >= 0 ? select.selectedIndex : 0;
        let next = current;
        if (key === 'ArrowDown') next = Math.min(select.options.length - 1, current + 1);
        if (key === 'ArrowUp') next = Math.max(0, current - 1);
        if (next !== current && openUi === ui) choose(ui, next);
      } else if (key === 'Escape') {
        event.preventDefault();
        closeOpen();
      }
    });

    select.addEventListener('change', () => syncUi(ui));
    return ui;
  }

  function toastStack() {
    let stack = document.getElementById('toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.id = 'toast-stack';
      stack.className = 'toast-stack';
      stack.setAttribute('aria-live', 'polite');
      stack.setAttribute('aria-atomic', 'false');
      document.body.appendChild(stack);
    }
    return stack;
  }

  function dismissToast(toast) {
    if (!toast || toast.dataset.dismissed === '1') return;
    toast.dataset.dismissed = '1';
    toast.classList.add('is-leaving');
    window.setTimeout(() => {
      toast.remove();
      const stack = document.getElementById('toast-stack');
      if (stack && !stack.querySelector('.toast')) stack.remove();
    }, 180);
  }

  function bindToast(toast) {
    if (!toast || toast.dataset.bound === '1') return;
    toast.dataset.bound = '1';
    let timer = window.setTimeout(() => dismissToast(toast), 3000);
    const close = toast.querySelector('.toast-close');
    close?.addEventListener('click', () => { window.clearTimeout(timer); dismissToast(toast); });
  }

  function showToast(message, type='ok') {
    const text = String(message || '').trim();
    if (!text) return;
    const stack = toastStack();
    stack.style.display = 'flex';
    const toast = document.createElement('div');
    toast.className = `toast toast-${type === 'err' ? 'err' : 'ok'}`;
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    const span = document.createElement('span');
    span.className = 'toast-message';
    span.textContent = text;
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'toast-close';
    close.setAttribute('aria-label', 'Close notification');
    close.textContent = '×';
    toast.append(span, close);
    stack.appendChild(toast);
    bindToast(toast);
  }

  window.fileCenterToast = showToast;

  function initToasts() {
    const stack = document.getElementById('toast-stack');
    const toasts = stack ? [...stack.querySelectorAll('.toast')] : [];
    if (!toasts.length) {
      if (stack) stack.remove();
      return;
    }
    stack.style.display = 'flex';
    toasts.forEach(bindToast);
  }

  function initTheme() {
    let saved;
    try { saved = localStorage.getItem('polo-theme') || localStorage.getItem('theme'); } catch (_) {}
    document.documentElement.dataset.theme = saved === 'dark' ? 'dark' : 'light';
    document.querySelectorAll('#ticket-theme, #themeBtn').forEach(button => {
      button.addEventListener('click', () => {
        const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.dataset.theme = theme;
        try { localStorage.setItem('polo-theme', theme); localStorage.setItem('theme', theme); } catch (_) {}
      });
    });
  }
  window.renderSessionMessages = data => (data.messages || []).forEach(([type, message]) => showToast(message, type));

  function init() {
    initTheme();
    document.querySelectorAll(SELECTOR).forEach(build);
    initToasts();
  }

  document.addEventListener('click', event => {
    if (!openUi) return;
    if (openUi.trigger.contains(event.target) || openUi.menu.contains(event.target)) return;
    closeOpen();
  });

  window.addEventListener('resize', () => { if (openUi) placeMenu(openUi); });
  window.addEventListener('scroll', closeOpen, true);
  document.addEventListener('DOMContentLoaded', init);
})();
