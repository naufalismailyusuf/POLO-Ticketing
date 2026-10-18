/* POLO Revisi 64 — responsive navigation. No API dependency. */
(() => {
  'use strict';
  function init() {
    const button = document.getElementById('mobile-menu-toggle');
    const menu = document.getElementById('mobile-nav');
    if (!button || !menu) return;

    const setOpen = (open) => {
      menu.hidden = !open;
      button.setAttribute('aria-expanded', String(open));
      button.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    };
    setOpen(false);
    button.addEventListener('click', () => setOpen(menu.hidden));
    document.addEventListener('click', (event) => {
      if (!menu.hidden && !menu.contains(event.target) && !button.contains(event.target)) setOpen(false);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !menu.hidden) {setOpen(false); button.focus();}
    });
    menu.addEventListener('click', (event) => {
      if (event.target.closest('a[href]')) setOpen(false);
    });
    const wide = window.matchMedia('(min-width: 901px)');
    const syncWidth = (event) => {if (event.matches) setOpen(false);};
    if (wide.addEventListener) wide.addEventListener('change', syncWidth);
    else if (wide.addListener) wide.addListener(syncWidth);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
})();
