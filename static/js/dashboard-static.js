(() => {
  'use strict';

  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const state = {
    q: '', support_status: 'all', support_owner: 'all', ticket_start: '', ticket_end: '', ticket_all: false,
    chart_start: '', chart_end: '', chart_all: false
  };
  let session = null;
  let latestData = null;
  let modalMode = '';
  let modalOwner = '';
  let modalSignature = '';
  const selectedIds = new Set();
  let loading = false;
  let pendingRefresh = false;
  let refreshTimer = null;
  const rendered = new Map();
  const motionPreference = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  const counters = new Map();
  let countFrame = null;
  let closeTimer = null;
  let animateWorkload = false;

  function reducedMotion() { return Boolean(motionPreference?.matches); }
  function setKpi(selector, value) {
    const el = $(selector), target = Math.max(0, Number(value) || 0);
    if (el.dataset.countTarget === String(target)) return;
    el.dataset.countTarget = String(target);
    if (reducedMotion() || document.hidden || !window.requestAnimationFrame) {
      counters.delete(el); el.textContent = target; return;
    }
    counters.set(el, { from:Number(el.textContent) || 0, target, start:performance.now() });
    if (countFrame === null) countFrame = window.requestAnimationFrame(tickCounts);
  }
  function tickCounts(now) {
    countFrame = null;
    for (const [el, item] of counters) {
      const progress = reducedMotion() || document.hidden ? 1 : Math.min(1, (now - item.start) / 480);
      const value = Math.round(item.from + (item.target - item.from) * (1 - Math.pow(1 - progress, 3)));
      if (String(el.textContent) !== String(value)) el.textContent = value;
      if (progress >= 1) counters.delete(el);
    }
    if (counters.size) countFrame = window.requestAnimationFrame(tickCounts);
  }
  function transitionWorkload() {
    if (animateWorkload && !reducedMotion()) $('#support-team-content').animate?.(
      [{opacity:.45, transform:'translateY(3px)'}, {opacity:1, transform:'translateY(0)'}],
      {duration:180, easing:'ease-out'}
    );
    animateWorkload = false;
  }

  function renderChanged(key, value, render) {
    const signature = JSON.stringify(value);
    if (rendered.get(key) === signature) return;
    render();
    rendered.set(key, signature);
  }
  function scheduleRefresh() {
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(loadDashboard, 150);
  }

  function esc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[c]));
  }
  function localDateTimeInput() {
    const d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 16);
  }
  function todayISO() { return localDateTimeInput().slice(0, 10); }
  function toast(message, type = 'ok') {
    const stack = $('#toast-stack');
    const item = document.createElement('div');
    item.className = `toast ${type === 'err' ? 'err' : 'ok'}`;
    item.textContent = message;
    stack.appendChild(item);
    setTimeout(() => item.remove(), 3200);
  }
  function setCsrf(token) {
    $$('.csrf-field').forEach(x => x.value = token || '');
    const logout = $('#logout-csrf');
    if (logout) logout.value = token || '';
  }
  async function api(url, options = {}) {
    const opts = { credentials: 'same-origin', cache: 'no-store', ...options };
    opts.headers = { Accept: 'application/json', ...(options.headers || {}) };
    const response = await fetch(url, opts);
    if (response.status === 401) throw new Error('AUTH_REQUIRED');
    const data = await response.json().catch(() => ({ ok: false, error: `HTTP ${response.status}` }));
    if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  function adminUi(isAdmin) { $$('.admin-only').forEach(el => { el.hidden = !isAdmin; }); }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try{localStorage.setItem('polo-theme', theme);localStorage.setItem('theme', theme)}catch(_){}
    $('#theme-toggle').textContent = theme === 'dark' ? '☀' : '◐';
  }
  function initTheme() {
    let saved;try{saved=localStorage.getItem('polo-theme')||localStorage.getItem('theme')}catch(_){}
    applyTheme(saved === 'dark' ? 'dark' : 'light');
    $('#theme-toggle')?.addEventListener('click', () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  }

  function renderAging(data) {
    const buckets = [
      ['0–1 day', '#age-bar-01', '0–1 days'],
      ['2–3 days', '#age-bar-23', '2–3 days'],
      ['4–7 days', '#age-bar-47', '4–7 days'],
      ['8+ days', '#age-bar-8', '8+ days']
    ];
    const values = buckets.map(([key]) => Math.max(0, Number(data.aging?.[key]) || 0));
    const total = values.reduce((sum, value) => sum + value, 0);
    const breached = Math.max(0, Number(data.sla_breach_count) || 0);
    const activeLabel = $('#aging-open-count');
    const breachLabel = $('#aging-breach-count');
    if (activeLabel) activeLabel.textContent = `${total} active`;
    if (breachLabel) breachLabel.textContent = `${breached} breached`;
    buckets.forEach(([, selector, label], index) => {
      const bar = $(selector);
      if (!bar) return;
      const percentage = total ? values[index] / total * 100 : 0;
      // Width and flex-basis are set together to handle legacy dashboard CSS.
      bar.style.width = `${percentage}%`;
      bar.style.flexBasis = `${percentage}%`;
      bar.title = `${label}: ${values[index]} ticket(s) (${Math.round(percentage)}%)`;
    });
    const distribution = $('#age-distribution');
    if (distribution) {
      distribution.setAttribute('aria-label', `Open ticket age: ${values[0]} at 0–1 days, ${values[1]} at 2–3 days, ${values[2]} at 4–7 days, ${values[3]} at 8+ days; ${total} active ticket(s) in total, ${breached} with SLA breach (4+ days)`);
    }
  }

  function statusClass(status) { return `status-${String(status || '').toLowerCase().replaceAll(' ', '-')}`; }
  function priorityClass(priority) { return `priority-${String(priority || '').toLowerCase()}`; }
  function queueRow(r, data) {
    const id = Number(r.id);
    const support = r.support_id || data.unassigned || 'Unassigned';
    const claim = r.support_id === data.unassigned && session?.user?.role === 'support'
      ? `<form class="queue-claim" data-claim-id="${id}"><button type="submit">Take ticket</button></form>` : '';
    const check = data.is_admin ? `<label class="queue-check"><input type="checkbox" class="queue-ticket-check" value="${id}" ${selectedIds.has(id) ? 'checked' : ''} aria-label="Select ${esc(r.no_tiket)}"></label>` : '';
    const status = String(r.status || 'Open');
    const statusKey = ['open', 'in-progress', 'waiting-for-user', 'resolved', 'closed', 'done'].includes(statusClass(status).slice(7))
      ? statusClass(status) : 'status-open';
    const priority = String(r.priority || '').trim();
    const priorityKey = ['critical', 'high', 'medium', 'low'].includes(priority.toLowerCase())
      ? priority.toLowerCase() : 'other';
    const priorityHint = ['critical','high'].includes(priorityKey)
      ? `<span class="queue-priority queue-priority-${priorityKey}" title="${esc(priority)} priority">${esc(priority)}</span>` : '';
    const slaKey = ['danger', 'warn', 'ok'].includes(String(r.sla_level)) ? String(r.sla_level) : 'ok';
    const slaText = String(r.sla_label || 'Not available').replace(/^SLA\s+/i, '');
    const reporter = String(r.pelapor || 'Not specified');
    const issue = String(r.masalah || 'Untitled ticket');
    const opened = [r.case_tanggal || r.tanggal, r.case_jam || r.jam].filter(Boolean).join(' ');
    return `<div class="queue-row-wrap" data-ticket-id="${id}">${check}
      <a class="queue-item" href="/ticket/${id}" target="_blank" rel="noopener noreferrer" aria-label="${esc(r.no_tiket)}: ${esc(issue)}. Status ${esc(status)}. Reporter ${esc(reporter)}. Support ${esc(support)}. SLA ${esc(slaText)}">
        <span class="queue-main">
          <span class="queue-summary">
            <span class="queue-identity"><span class="ticket-no">${esc(r.no_tiket)}</span>${r.category ? `<span class="queue-category">${esc(r.category)}</span>` : ''}${priorityHint}</span>
          </span>
          <span class="queue-title" title="${esc(issue)}">${esc(issue)}</span>
          <span class="queue-context">
            <span class="queue-people"><span class="queue-person"><span class="queue-detail-label">Reporter</span><span class="queue-detail-value" title="${esc(reporter)}">${esc(reporter)}</span></span><span class="queue-person"><span class="queue-detail-label">Support</span><span class="queue-detail-value" title="${esc(support)}">${esc(support)}</span></span></span>
          </span>
        </span>
        <span class="queue-side">
            <span class="queue-status ${statusKey}">${esc(status)}</span>
            <span class="queue-sla queue-sla-${slaKey}" title="SLA: ${esc(slaText)}"><span class="queue-sla-label">SLA</span><span class="queue-sla-value">${esc(slaText)}</span></span>
        </span>
      </a>${claim}</div>`;
  }

  function queueHtml(rows) {
    return rows.length ? rows.map(r => queueRow(r, latestData)).join('') : '<div class="empty">No open tickets</div>';
  }
  // Naufal R40: prioritize the complete queue even if an older API still sends a
  // truncated eight-ticket open_queue preview. Keep select-all in sync.
  function queueRows(data) {
    return Array.isArray(data?.open_queue_all) ? data.open_queue_all : (data?.open_queue || []);
  }
  function syncBulk() {
    $$('.queue-ticket-check').forEach(box => { box.checked = selectedIds.has(Number(box.value)); });
    $('#bulk-selected-count').textContent = selectedIds.size;
    $('#modal-selected-count').textContent = selectedIds.size;
    $('#bulk-action-bar').hidden = !latestData?.is_admin || selectedIds.size === 0;
    $('#modal-bulk-bar').hidden = modalMode !== 'queue' || !latestData?.is_admin;
    for (const [selector, rows] of [ ['#queue-select-all', queueRows(latestData)], ['#modal-select-all', queueRows(latestData)] ]) {
      const master = $(selector), count = rows.filter(r => selectedIds.has(Number(r.id))).length;
      master.checked = rows.length > 0 && count === rows.length;
      master.indeterminate = count > 0 && count < rows.length;
    }
  }
  // Naufal R41: hint is visible only while more rows remain below the fold.
  function updateQueueScrollHint() {
    const list = $('#open-queue-list'), hint = $('#queue-scroll-hint');
    if (!list || !hint) return;
    const canScroll = list.scrollHeight > list.clientHeight + 2;
    const moreBelow = list.scrollTop + list.clientHeight < list.scrollHeight - 2;
    hint.hidden = !(canScroll && moreBelow);
  }
  function renderQueue(data) {
    const rows = queueRows(data), available = new Set(rows.map(r => Number(r.id)));
    for (const id of selectedIds) if (!available.has(id)) selectedIds.delete(id);
    setKpi('#queue-count', data.all_open_count ?? rows.length);
    $('#queue-count').title = `${data.unassigned_open_count || 0} unassigned`;
    const list = $('#open-queue-list'), scrollTop = list.scrollTop;
    list.innerHTML = queueHtml(rows); list.scrollTop = scrollTop;
    syncBulk();
    updateQueueScrollHint();
  }
  async function claimTicket(form) {
    const button = $('button', form); button.disabled = true;
    try {
      const response = await fetch(`/claim/${Number(form.dataset.claimId)}`, { method:'POST', headers:{ 'X-CSRF-Token':session.csrf }, credentials:'same-origin' });
      if (response.status === 401) throw new Error('AUTH_REQUIRED');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      toast('Ticket claimed.'); await loadDashboard();
    } catch (err) { toast(err.message, 'err'); button.disabled = false; }
  }

  let chartWidth = 0;
  let chartHeight = 0;
  let chartResizeTimer = null;
  function measureChart() { return Math.max(280, ($('#chart-wrap').clientWidth || 1140) - 40); }
  function measureChartHeight() { return Math.max(250, ($('#chart-wrap').clientHeight || 288) - 18); }
  function renderChart(data, animate = true) {
    if (state.chart_all) { $('#chart-start').value = ''; $('#chart-end').value = ''; }
    else { if (!$('#chart-start').value) $('#chart-start').value = data.chart_start || ''; if (!$('#chart-end').value) $('#chart-end').value = data.chart_end || ''; }
    $('#chart-all-dates').classList.toggle('is-active', state.chart_all);
    $('#chart-all-dates').setAttribute('aria-pressed', String(state.chart_all));
    const bars = data.bars || [], values = bars.map(b => [Number(b.done || 0), Number(b.open_normal || 0), Number(b.breach || 0)]);
    const max = Math.max(1, ...values.flat());
    const roughStep = max / 4, power = Math.pow(10, Math.floor(Math.log10(roughStep)));
    const tickStep = Math.max(1, [1,2,5,10].find(n => n * power >= roughStep) * power);
    const ticks = Math.ceil(max / tickStep), axisMax = ticks * tickStep;
    const viewWidth = measureChart(), viewHeight = measureChartHeight(), left = 32, right = viewWidth - 12, base = viewHeight - 38, plotHeight = base - 30;
    chartWidth = viewWidth; chartHeight = viewHeight;
    const step = (right - left) / Math.max(1, bars.length), width = Math.min(38, step * .19), gap = Math.max(3, width * .32);
    let svg = `<svg viewBox="0 0 ${viewWidth} ${viewHeight}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg" role="group" aria-label="Team performance">`;
    for (let i = 0; i <= ticks; i++) {
      const y = base - plotHeight * i / ticks;
      svg += `<line class="chart-gridline ${i === 0 ? 'chart-baseline' : ''}" x1="${left}" y1="${y}" x2="${right}" y2="${y}"/><text class="chart-label" x="22" y="${y + 4}" text-anchor="end">${tickStep * i}</text>`;
    }
    bars.forEach((b, i) => {
      const center = left + step * (i + .5), groupWidth = width * 3 + gap * 2;
      const label = `${b.name}: ${values[i][0]} completed, ${values[i][1]} open, ${values[i][2]} SLA breach`;
      svg += `<g class="chart-member" tabindex="0" role="button" data-chart-index="${i}" aria-label="${esc(label)}"><rect class="chart-hit" x="${center - step / 2 + 2}" y="20" width="${Math.max(1, step - 4)}" height="${viewHeight - 30}" rx="8"/><g class="chart-bars ${animate ? '' : 'is-static'}" style="transform-origin:0 ${base}px">`;
      values[i].forEach((value, segment) => {
        if (value <= 0) return;
        const height = value / axisMax * plotHeight, x = center - groupWidth / 2 + segment * (width + gap), y = base - height, radius = Math.min(5, width / 2, height / 2);
        svg += `<path class="chart-bar" d="M${x} ${base}V${y + radius}Q${x} ${y} ${x + radius} ${y}H${x + width - radius}Q${x + width} ${y} ${x + width} ${y + radius}V${base}Z" fill="var(--${['success','open','danger'][segment]})"/>`;
      });
      svg += `</g><text class="chart-label chart-name" x="${center}" y="${viewHeight - 12}" text-anchor="middle">${esc(b.name)}</text></g>`;
    });
    $('#chart-wrap').innerHTML = svg + '</svg><div id="chart-tooltip" class="chart-tooltip" role="status" hidden></div>';
    $('#chart-wrap').setAttribute('aria-label', `Team performance, ${data.chart_start || ''} to ${data.chart_end || ''}, ${data.chart_total || 0} tickets`);
  }
  function hideChartTooltip() { const tooltip = $('#chart-tooltip'); if (tooltip) tooltip.hidden = true; }
  function showChartTooltip(member) {
    const index = Number(member.dataset.chartIndex), bar = latestData?.bars?.[index], tooltip = $('#chart-tooltip');
    if (!bar || !tooltip) return;
    tooltip.innerHTML = `<b>${esc(bar.name)}</b><span><i class="done"></i>Completed<strong>${Number(bar.done || 0)}</strong></span><span><i class="open"></i>Open<strong>${Number(bar.open_normal || 0)}</strong></span><span><i class="breach"></i>SLA breach<strong>${Number(bar.breach || 0)}</strong></span>`;
    tooltip.hidden = false;
    const wrap = $('#chart-wrap'), bounds = wrap.getBoundingClientRect(), item = member.getBoundingClientRect();
    const scale = window.TicketUI?.uiScale() || 1;
    tooltip.style.left = `${Math.max(10, Math.min(wrap.clientWidth - tooltip.offsetWidth - 10, (item.left - bounds.left + item.width / 2) / scale - tooltip.offsetWidth / 2))}px`;
  }
  function initChartInteractions() {
    const wrap = $('#chart-wrap');
    wrap.addEventListener('pointerover', event => { const member = event.target.closest('[data-chart-index]'); if (member) showChartTooltip(member); });
    wrap.addEventListener('pointerleave', hideChartTooltip);
    wrap.addEventListener('focusin', event => { const member = event.target.closest('[data-chart-index]'); if (member) showChartTooltip(member); });
    wrap.addEventListener('focusout', hideChartTooltip);
    wrap.addEventListener('click', event => { const member = event.target.closest('[data-chart-index]'); if (member) showChartTooltip(member); else hideChartTooltip(); });
    wrap.addEventListener('keydown', event => {
      if (event.key === 'Escape') { hideChartTooltip(); return; }
      if (event.key === 'Enter' || event.key === ' ') { const member = event.target.closest('[data-chart-index]'); if (member) { event.preventDefault(); showChartTooltip(member); } }
    });
    document.addEventListener('pointerdown', event => { if (!wrap.contains(event.target)) hideChartTooltip(); });
    if (window.ResizeObserver) new window.ResizeObserver(() => {
      if (latestData && (measureChart() !== chartWidth || measureChartHeight() !== chartHeight)) renderChart(latestData, false);
    }).observe(wrap);
    window.addEventListener('resize', () => {
      window.clearTimeout(chartResizeTimer);
      chartResizeTimer = window.setTimeout(() => { if (latestData && (measureChart() !== chartWidth || measureChartHeight() !== chartHeight)) renderChart(latestData, false); }, 120);
    });
  }

  function supportTicket(r, data) {
    const updates = Number(data.comment_meta?.[r.id]?.count || 0);
    return `<a class="support-ticket" href="/ticket/${Number(r.id)}" target="_blank" rel="noopener noreferrer"><div class="support-ticket-top"><span class="ticket-no">${esc(r.no_tiket)}</span><span class="badge ${statusClass(r.status)}">${esc(r.status)}</span><span class="badge ${priorityClass(r.priority)}">${esc(r.priority)}</span></div><div class="support-ticket-title">${esc(r.masalah)}</div><div class="support-ticket-meta"><span>${esc(r.pelapor)}</span><span>${esc(r.category)}</span><span>${updates} updates</span><span>${esc(r.case_tanggal || r.tanggal)} ${esc(r.case_jam || r.jam)}</span></div></a>`;
  }
  function renderSupport(data) {
    const owners = [...(data.support_users || []), data.unassigned];
    $('#support-owner-filter').innerHTML = '<option value="all">All Support</option>' + (data.support_users || []).map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join('');
    $('#support-owner-filter').value = state.support_owner;
    $('#support-team-content').innerHTML = owners.filter(owner => (data.per?.[owner] || []).length > 0).map(owner => {
      const rows = data.per[owner], st = data.stat?.[owner] || {};
      const latest = [...rows].sort((a, b) => `${b.tanggal} ${b.jam}`.localeCompare(`${a.tanggal} ${a.jam}`) || Number(b.id) - Number(a.id))[0];
      return `<tr><td><button class="owner-button" type="button" data-owner="${esc(owner)}"><i class="owner-dot" style="--owner-color:${esc(data.support_colors?.[owner] || '#94a3b8')}" aria-hidden="true"></i>${esc(owner)}</button></td><td>${st.total || 0}</td><td>${st.open || 0}</td><td>${st.done || 0}</td><td><a class="latest-ticket" href="/ticket/${Number(latest.id)}" target="_blank" rel="noopener noreferrer" title="${esc(latest.masalah)}"><b>${esc(latest.no_tiket)}</b>${esc(latest.masalah)}</a></td></tr>`;
    }).join('') || '<tr><td colspan="5">No tickets</td></tr>';
    transitionWorkload();
  }
  function ownerHtml() {
    const rows = latestData?.per?.[modalOwner] || [], completed = latestData?.completed_statuses || [];
    const active = rows.filter(r => !completed.includes(r.status)), done = rows.filter(r => completed.includes(r.status));
    return `<h3>Open · ${active.length}</h3>${active.map(r => supportTicket(r, latestData)).join('') || '<div class="empty">No open tickets</div>'}${done.length ? `<h3>Resolved · ${done.length}</h3>${done.map(r => supportTicket(r, latestData)).join('')}` : ''}`;
  }
  function refreshModal() {
    if (!$('#details-modal').open) return;
    const content = $('#modal-content'), scrollTop = content.scrollTop;
    const html = modalMode === 'queue' ? queueHtml(queueRows(latestData)) : ownerHtml();
    if (modalSignature !== html) { content.innerHTML = html; content.scrollTop = scrollTop; modalSignature = html; }
    syncBulk();
  }
  async function openModal(mode, owner = '') {
    window.clearTimeout(closeTimer); closeTimer = null;
    $('#details-modal').classList.remove('is-closing');
    modalMode = mode; modalOwner = owner;
    $('#modal-title').textContent = mode === 'queue' ? 'Open Ticket Queue' : owner;
    $('#modal-content').innerHTML = ''; modalSignature = '';
    if (!$('#details-modal').open) $('#details-modal').showModal();
    document.body.classList.add('modal-open');
    refreshModal();
  }
  function closeModal() {
    const dialog = $('#details-modal');
    if (!dialog.open || closeTimer !== null) return;
    if (reducedMotion()) { dialog.close(); return; }
    dialog.classList.add('is-closing');
    closeTimer = window.setTimeout(() => {
      dialog.close(); dialog.classList.remove('is-closing'); closeTimer = null;
    }, 150);
  }


  function populateForm(data) {
    function fill(selector, values, fallback, placeholder) {
      const select = $(selector), previous = select.value;
      select.innerHTML = (placeholder ? '<option value="">Choose Support</option>' : '') + values.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join('');
      select.value = values.includes(previous) ? previous : fallback;
    }
    fill('#new-ticket-support', data.allowed || [], data.unassigned);
    fill('#bulk-assign-owner', data.support_users || [], '', true);
    fill('#modal-bulk-owner', data.support_users || [], '', true);
    fill('#new-priority', data.priorities || [], 'Medium');
    fill('#new-category', data.categories || [], 'Other');
    const divisionSelect = $('#new-division');
    if (divisionSelect) {
      const previous = divisionSelect.value;
      const divisions = data.divisions || [];
      divisionSelect.innerHTML = '<option value="">Select division</option>' + divisions.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
      divisionSelect.value = divisions.includes(previous) ? previous : '';
    }
    if (!$('#case-at').value) $('#case-at').value = localDateTimeInput();
    syncEvidenceRequirement();
  }
  function selectedSupportRequiresEvidence() {
    const support = $('#new-ticket-support')?.value || '';
    return !!latestData?.support_require_evidence?.[support];
  }
  function syncEvidenceRequirement() {
    const field = $('#new-ticket-evidence-field');
    const input = $('#new-ticket-evidence');
    const hint = $('#new-ticket-evidence-hint');
    const button = $('#new-ticket-form button[type="submit"]');
    if (!field || !input || !hint || !button) return;
    const required = selectedSupportRequiresEvidence();
    input.required = required;
    field.classList.toggle('is-required', required);
    hint.textContent = required ? 'Required for selected Support' : 'Optional';
    button.disabled = required && !input.files?.length;
  }
  function updateExports() {
    const params = new URLSearchParams({ ticket_start:state.ticket_start, ticket_end:state.ticket_end, ticket_all:state.ticket_all ? '1' : '0', q:state.q, support_status:state.support_status, support_owner:state.support_owner });
    $('#support-export').href = `/export?${params}`;
    $('#support-export-all').href = `/export?ticket_all=1&q=${encodeURIComponent(state.q)}&support_status=${encodeURIComponent(state.support_status)}&support_owner=${encodeURIComponent(state.support_owner)}`;
  }
  function syncFilterUI() {
    $$('#support-filters button').forEach(b => b.classList.toggle('is-active', b.dataset.supportFilter === state.support_status));
    const today = todayISO();
    const selected = [ ['#support-today-date',!state.ticket_all && state.ticket_start === today && state.ticket_end === today], ['#support-all-dates',state.ticket_all], ['#chart-today',!state.chart_all && state.chart_start === today && state.chart_end === today], ['#chart-all-dates',state.chart_all] ];
    selected.forEach(([selector,isSelected]) => { const button = $(selector); button.classList.toggle('is-active',isSelected); button.setAttribute('aria-pressed',String(isSelected)); });
  }
  function fillFilterFields() {
    $('#support-q').value = state.q;
    $('#support-from').value = state.ticket_start;
    $('#support-to').value = state.ticket_end;
  }

  async function loadDashboard() {
    syncFilterUI();
    if (loading) { pendingRefresh = true; return; }
    loading = true;
    $('#refresh-dashboard')?.setAttribute('aria-busy', 'true');
    try {
      const qs = new URLSearchParams({ q:state.q, support_status:state.support_status, support_owner:state.support_owner });
      if (state.ticket_all) qs.set('ticket_all','1'); else { qs.set('ticket_start',state.ticket_start); qs.set('ticket_end',state.ticket_end); }
      if (state.chart_all) { qs.set('chart_start','0001-01-01'); qs.set('chart_end','9999-12-31'); }
      else { if (state.chart_start) qs.set('chart_start',state.chart_start); if (state.chart_end) qs.set('chart_end',state.chart_end); }
      const data = await api(`/api/dashboard?${qs}`);
      latestData = data;
      setKpi('#stat-total', data.total || 0);
      setKpi('#stat-open', data.all_open_count || 0);
      setKpi('#stat-breach', data.sla_breach_count || 0);
      setKpi('#stat-done', data.n_done || 0);
      setKpi('#stat-today', data.today_total || 0);
      $('#total-kpi').title = data.ticket_filter_label || '';
      $('#completed-kpi').title = data.ticket_filter_label || '';
      setKpi('#resolved-today', data.resolved_today || 0);
      setKpi('#age-01', data.aging?.['0–1 day'] || 0);
      setKpi('#age-23', data.aging?.['2–3 days'] || 0);
      setKpi('#age-47', data.aging?.['4–7 days'] || 0);
      setKpi('#age-8', data.aging?.['8+ days'] || 0);
      // Render Ticket Age independently of queue/chart/support rendering;
      // another widget's failure must not leave the age bar gray.
      renderAging(data);
      renderChanged('queue', [queueRows(data), data.all_open_count, data.unassigned_open_count, data.is_admin, data.unassigned], () => renderQueue(data));
      renderChanged('chart', [data.bars, data.chart_start, data.chart_end, data.chart_total, state.chart_all], () => renderChart(data));
      renderChanged('support', [data.per, data.stat, data.comment_meta, data.support_users, data.support_colors, data.completed_statuses, data.unassigned, state.support_owner], () => renderSupport(data));
      renderChanged('form', [data.allowed, data.support_users, data.support_require_evidence, data.priorities, data.categories, data.divisions, data.unassigned], () => populateForm(data));
      updateExports(); syncFilterUI(); refreshModal(); window.TicketUI?.syncSelects(); syncEvidenceRequirement();
    } catch (err) {
      if (err.message === 'AUTH_REQUIRED') location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
      else toast(err.message || 'Failed to load dashboard.', 'err');
    } finally { loading = false; $('#refresh-dashboard')?.setAttribute('aria-busy', 'false'); if (pendingRefresh) { pendingRefresh = false; scheduleRefresh(); } }
  }

  async function submitNewTicket(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $('button[type="submit"]', form); button.disabled = true;
    try {
      const payload = Object.fromEntries(new FormData(form).entries());
      if (selectedSupportRequiresEvidence() && !$('#new-ticket-evidence')?.files?.length) throw Error('Evidence is required for selected Support.');
      const file = $('#new-ticket-evidence')?.files?.[0];
      const options = file ? { method:'POST', headers:{ 'X-CSRF-Token':session.csrf }, body:new FormData(form) } : { method:'POST', headers:{ 'Content-Type':'application/json', 'X-CSRF-Token':session.csrf }, body:JSON.stringify(payload) };
      const data = await api('/api/tickets/create', options);
      form.reset(); $('#case-at').value = localDateTimeInput(); $('#new-priority').value='Medium'; $('#new-category').value='Other'; $('#new-ticket-support').value=latestData.unassigned; window.TicketUI?.syncSelects(); syncEvidenceRequirement(); toast(data.message || 'Ticket created.'); await loadDashboard();
    } catch (err) { toast(err.message || 'Failed to create ticket.', 'err'); }
    finally { button.disabled = false; syncEvidenceRequirement(); }
  }

  async function executeBulk(inModal = false) {
    const ids = [...selectedIds]; if (!ids.length) return;
    const action = $(inModal ? '#modal-bulk-action' : '#bulk-action').value; const support_id = $(inModal ? '#modal-bulk-owner' : '#bulk-assign-owner').value;
    if (action !== 'close' && !support_id) { toast('Choose Support first.', 'err'); return; }
    const button = $(inModal ? '#modal-bulk-submit' : '#bulk-action-btn'); button.disabled = true;
    try { const data = await api('/api/tickets/bulk-action', { method:'POST', headers:{ 'Content-Type':'application/json', 'X-CSRF-Token':session.csrf }, body:JSON.stringify({ticket_ids:ids, action, support_id}) }); toast(data.message || `${data.succeeded || 0} ticket processed.`); selectedIds.clear(); syncBulk(); await loadDashboard(); }
    catch (err) { toast(err.message, 'err'); }
    finally { button.disabled = false; }
  }

  function initHeader() {
    const header = $('.app-header');
    const update = () => header?.classList.toggle('is-scrolled', window.scrollY > 12);
    update();
    window.addEventListener('scroll', update, {passive:true});
  }

  function applyWorkloadInputs() {
    state.q = $('#support-q').value.trim();
    state.support_owner = $('#support-owner-filter').value || 'all';
    state.ticket_start = $('#support-from').value; state.ticket_end = $('#support-to').value;
    if (state.ticket_start || state.ticket_end) state.ticket_all = false;
  }
  function allDates() {
    applyWorkloadInputs();
    state.ticket_start = state.ticket_end = ''; state.ticket_all = true;
    state.chart_start = state.chart_end = ''; state.chart_all = true;
    $('#chart-start').value = ''; $('#chart-end').value = ''; fillFilterFields();
    loadDashboard();
  }
  function initInteractions() {
    const queueList = $('#open-queue-list');
    queueList?.addEventListener('scroll', updateQueueScrollHint, {passive:true});
    window.addEventListener('resize', updateQueueScrollHint);
    if (queueList && window.ResizeObserver) {
      const queueResizeObserver = new ResizeObserver(updateQueueScrollHint);
      queueResizeObserver.observe(queueList);
    }
    $('#queue-view-all').addEventListener('click', () => openModal('queue'));
    $('#modal-close').addEventListener('click', closeModal);
    $('#details-modal').addEventListener('cancel', event => { event.preventDefault(); closeModal(); });
    $('#details-modal').addEventListener('close', () => { document.body.classList.remove('modal-open'); modalMode = ''; syncBulk(); });
    $('#details-modal').addEventListener('click', event => { if (event.target === $('#details-modal')) closeModal(); });
    $('#modal-bulk-submit').addEventListener('click', () => executeBulk(true));
    $('#modal-bulk-action').addEventListener('change', event => { $('#modal-bulk-submit').textContent = event.target.value === 'close' ? 'Close' : event.target.value === 'assign_close' ? 'Assign + Close' : 'Assign'; });
    document.addEventListener('change', event => {
      const target = event.target;
      if (target.matches('.queue-ticket-check')) {
        const id = Number(target.value); target.checked ? selectedIds.add(id) : selectedIds.delete(id); syncBulk();
      } else if (target.matches('[data-select-scope]')) {
        const rows = queueRows(latestData);
        rows.forEach(row => target.checked ? selectedIds.add(Number(row.id)) : selectedIds.delete(Number(row.id))); syncBulk();
      }
    });
    document.addEventListener('click', event => { const button = event.target.closest('[data-owner]'); if (button) openModal('owner', button.dataset.owner); });
    document.addEventListener('submit', event => { if (event.target.matches('.queue-claim')) { event.preventDefault(); claimTicket(event.target); } });
    $('#refresh-dashboard')?.addEventListener('click', loadDashboard);
    $('#new-ticket-form')?.addEventListener('submit', submitNewTicket);
    $('#new-ticket-support')?.addEventListener('change', syncEvidenceRequirement);
    $('#new-ticket-evidence')?.addEventListener('change', syncEvidenceRequirement);
    $('#bulk-action-btn')?.addEventListener('click', () => executeBulk());
    $('#bulk-action')?.addEventListener('change', e => { $('#bulk-action-btn').textContent = e.target.value === 'close' ? 'Close' : e.target.value === 'assign_close' ? 'Assign + Close' : 'Assign'; });
    ['#support-from','#support-to'].forEach(selector => $(selector).addEventListener('change', () => { applyWorkloadInputs(); state.ticket_all=!state.ticket_start && !state.ticket_end; loadDashboard(); }));
    $('#support-q').addEventListener('input', () => { applyWorkloadInputs(); scheduleRefresh(); });
    $('#support-q').addEventListener('search', () => { applyWorkloadInputs(); scheduleRefresh(); });
    $('#support-today-date')?.addEventListener('click', () => { applyWorkloadInputs(); const t=todayISO(); state.ticket_start=state.ticket_end=t; state.ticket_all=false; state.chart_start=state.chart_end=t; state.chart_all=false; $('#chart-start').value=t; $('#chart-end').value=t; fillFilterFields(); loadDashboard(); });
    $('#support-all-dates')?.addEventListener('click', allDates);
    $('#support-owner-filter')?.addEventListener('change', () => { applyWorkloadInputs(); loadDashboard(); });
    $$('#support-filters button').forEach(b => b.addEventListener('click', () => { animateWorkload=state.support_status !== b.dataset.supportFilter; state.support_status=b.dataset.supportFilter; applyWorkloadInputs(); syncFilterUI(); loadDashboard(); }));
    ['#chart-start','#chart-end'].forEach(selector => $(selector).addEventListener('change', () => { state.chart_start=$('#chart-start').value; state.chart_end=$('#chart-end').value; state.chart_all=!state.chart_start && !state.chart_end; loadDashboard(); }));
    $('#chart-today')?.addEventListener('click', () => { const today=todayISO(); state.chart_start=state.chart_end=today; state.chart_all=false; $('#chart-start').value=today; $('#chart-end').value=today; loadDashboard(); });
    $('#chart-all-dates')?.addEventListener('click', () => { state.chart_start=state.chart_end=''; state.chart_all=true; $('#chart-start').value=''; $('#chart-end').value=''; loadDashboard(); });
    $('#support-toolbar-toggle')?.addEventListener('click', e => { const panel=$('#support-toolbar-content'); const open=panel.classList.toggle('is-open'); e.currentTarget.setAttribute('aria-expanded', String(open)); });
    $('#mobile-menu-toggle')?.addEventListener('click', e => { const nav=$('#mobile-nav'); const open=nav.hidden; nav.hidden=!open; e.currentTarget.setAttribute('aria-expanded', String(open)); });
  }

  async function init() {
    initTheme(); initHeader(); initInteractions(); initChartInteractions();
    try {
      session = await api('/api/session'); (session.messages || []).forEach(([type, message]) => toast(message, type)); setCsrf(session.csrf); adminUi(session.user.role === 'admin'); $('#user-label').textContent = session.user.display; $('#user-role').textContent = session.user.role; 
      const today=todayISO(); state.ticket_start=today; state.ticket_end=today; state.chart_start=today; state.chart_end=today; $('#case-at').value=localDateTimeInput(); fillFilterFields();
      await loadDashboard();
      window.addEventListener('focus', scheduleRefresh);
      window.addEventListener('pageshow', event => { if (event.persisted) scheduleRefresh(); });
      document.addEventListener('visibilitychange', () => { if (!document.hidden) scheduleRefresh(); });
      window.setInterval(() => { if (!document.hidden) scheduleRefresh(); }, 30000);
    } catch (err) { if (err.message === 'AUTH_REQUIRED') location.href=`/login?next=${encodeURIComponent(location.pathname+location.search)}`; else toast(err.message,'err'); }
  }
  document.addEventListener('DOMContentLoaded', init);
})();
