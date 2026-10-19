/* R85 — Actual user activity, not background API polling, extends the idle session.
   Server-side signed session timestamps remain authoritative. */
(() => {
  'use strict';
  const tag = document.querySelector('script[src*="/static/js/session-activity-v85.js"]');
  if (!tag || !tag.dataset.csrf) return;
  const csrf = tag.dataset.csrf;
  const bus = (() => {
    try { return new BroadcastChannel('polo-session-r85'); }
    catch (_) { return null; }
  })();
  let idleDeadline = 0, absoluteDeadline = 0;
  let lastSent = 0, pending = false, verifying = false, redirecting = false;

  const now = () => performance.now();
  const updateDeadlines = state => {
    if (!state || !Number.isFinite(Number(state.idle_seconds_remaining)) ||
        !Number.isFinite(Number(state.absolute_seconds_remaining))) return false;
    const mark = now();
    idleDeadline = mark + Math.max(0, Number(state.idle_seconds_remaining)) * 1000;
    absoluteDeadline = mark + Math.max(0, Number(state.absolute_seconds_remaining)) * 1000;
    return true;
  };
  const initial = {
    idle_seconds_remaining: Number(tag.dataset.idleSeconds),
    absolute_seconds_remaining: Number(tag.dataset.absoluteSeconds)
  };
  if (!updateDeadlines(initial)) return;

  function toLogin() {
    if (redirecting) return;
    redirecting = true;
    window.location.replace('/login?expired=1');
  }

  async function verifySession() {
    if (redirecting || verifying) return;
    verifying = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/api/session/security', {
        method: 'GET', credentials: 'same-origin', cache: 'no-store',
        signal: controller.signal,
        headers: {'Accept':'application/json'}
      });
      if (response.status === 401 || response.status === 403) { toLogin(); return; }
      if (!response.ok) return;
      const data = await response.json();
      if (!data.ok || !updateDeadlines(data.session)) { toLogin(); return; }
    } catch (_) {
      // The server may be temporarily unreachable. Do not renew locally.
      if (now() >= idleDeadline || now() >= absoluteDeadline) toLogin();
    } finally {
      clearTimeout(timer);
      verifying = false;
    }
  }

  async function heartbeat() {
    if (pending || redirecting) return;
    if (now() >= idleDeadline || now() >= absoluteDeadline) {
      void verifySession();
      return;
    }
    pending = true;
    lastSent = now();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/api/session/heartbeat', {
        method:'POST', credentials:'same-origin', cache:'no-store',
        signal:controller.signal,
        headers:{'Accept':'application/json', 'X-CSRF-Token':csrf}
      });
      if (response.status === 401 || response.status === 403) { toLogin(); return; }
      if (!response.ok) { lastSent = 0; return; }
      const data = await response.json();
      if (!data.ok || !updateDeadlines(data.session)) { toLogin(); return; }
      bus?.postMessage({type:'alive', session:data.session});
    } catch (_) {
      lastSent = 0; // Retry on the next deliberate interaction.
    } finally {
      clearTimeout(timer);
      pending = false;
    }
  }

  function userActivity(event) {
    if (!event.isTrusted || document.hidden || redirecting) return;
    if (now() >= idleDeadline || now() >= absoluteDeadline) {
      void verifySession();
      return;
    }
    // One activity proof per 60 seconds; mouse movement and background polls
    // do not extend the idle timer.
    if (now() - lastSent >= 60000 || lastSent === 0) void heartbeat();
  }

  ['pointerdown', 'keydown', 'wheel', 'touchstart', 'input'].forEach(type => {
    document.addEventListener(type, userActivity, {capture:true, passive:true});
  });

  setInterval(() => {
    if (redirecting) return;
    if (now() >= idleDeadline || now() >= absoluteDeadline) void verifySession();
  }, 15000);

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) void verifySession();
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) void verifySession();
  });
  document.addEventListener('submit', event => {
    if (event.target.matches?.('form.logout-form')) {
      bus?.postMessage({type:'signed-out'});
    }
  }, true);
  if (bus) {
    bus.onmessage = event => {
      if (event.data?.type === 'signed-out') toLogin();
      else if (event.data?.type === 'alive' && event.data.session) {
        // Another tab may extend idle, but never absolute session lifetime.
        updateDeadlines(event.data.session);
      }
    };
  }
})();
