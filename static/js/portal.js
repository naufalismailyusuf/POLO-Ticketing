(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const notice = message => { const el = $('#portal-notice'); if (el) el.textContent = message; };
  let csrf = '', admin = false;
  async function api(url, options = {}) {
    const response = await fetch(url, {credentials:'same-origin',cache:'no-store',...options});
    if (response.status === 401) { location.assign('/login?next='+encodeURIComponent(location.pathname)); throw Error('Authentication required.'); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  function linkCard(link) {
    const card = document.createElement('article'); card.className = 'portal-link';
    const anchor = document.createElement('a'); anchor.href = link.url; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
    const title = document.createElement('strong'); title.textContent = link.name;
    const url = document.createElement('span'); url.textContent = link.url;
    anchor.append(title, url);
    if (link.description) { const description = document.createElement('p'); description.textContent = link.description; anchor.append(description); }
    card.append(anchor);
    if (admin && $('#link-editor')) {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Remove'; button.className = 'portal-remove';
      button.setAttribute('aria-label', 'Remove '+link.name);
      button.addEventListener('click', async () => {
        if (!confirm(`Remove ${link.name}?`)) return;
        button.disabled = true;
        try { await api('/api/links/'+link.id,{method:'DELETE',headers:{'X-CSRF-Token':csrf}}); await loadLinks(); notice('Link removed.'); }
        catch (error) { button.disabled = false; notice(error.message); }
      });
      card.append(button);
    }
    return card;
  }
  async function loadLinks() {
    const {links} = await api('/api/links');
    const list = $('#portal-links'); list.replaceChildren(...links.map(linkCard));
    if (!links.length) list.textContent = 'No links yet.';
    if ($('#link-count')) $('#link-count').textContent = `${links.length} links`;
  }
  async function boot() {
    const session = await api('/api/session'); csrf = session.csrf; admin = session.user.role === 'admin';
    for (const id of ['user-label','profile-display']) if ($('#'+id)) $('#'+id).textContent = session.user.display || session.user.username;
    if ($('#user-role')) $('#user-role').textContent = session.user.role;
    if ($('#logout-csrf')) $('#logout-csrf').value = csrf;
    if ($('#link-editor')) $('#link-editor').hidden = !admin;
    $('#theme-toggle')?.addEventListener('click', () => {
      const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = theme;
      try { localStorage.setItem('polo-theme', theme); localStorage.setItem('theme', theme); } catch (_) {}
    });
    $('#link-form')?.addEventListener('submit', async event => {
      event.preventDefault(); const form=event.currentTarget; const button=form.querySelector('button[type=submit]'); button.disabled=true;
      try {
        await api('/api/links',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(Object.fromEntries(new FormData(form)))});
        form.reset(); await loadLinks(); notice('Link added.');
      } catch (error) { notice(error.message); } finally { button.disabled=false; }
    });
    await loadLinks();
  }
  boot().catch(error => notice(error.message));
})();
