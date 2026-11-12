document.addEventListener('click',function(e){document.querySelectorAll('.admin-nav-dropdown[open],.profile-dropdown[open]').forEach(function(menu){if(!menu.contains(e.target))menu.removeAttribute('open')})});document.addEventListener('keydown',function(e){if(e.key==='Escape')document.querySelectorAll('.admin-nav-dropdown[open],.profile-dropdown[open]').forEach(function(menu){menu.removeAttribute('open');menu.querySelector('summary')?.focus()})});document.querySelectorAll('.admin-nav-dropdown,.profile-dropdown').forEach(function(menu){menu.addEventListener('toggle',function(){if(!menu.open)return;document.querySelectorAll('.admin-nav-dropdown[open],.profile-dropdown[open]').forEach(function(other){if(other!==menu)other.open=false})})});

/* Glass navigation loads only after its stylesheet is available. */
(()=>{const script=document.createElement('script');script.src='/static/js/glass-dock.js?v=3';document.body.append(script)})();

// Mirror session identity in the profile dropdown without delaying initial navigation.
const profileLabel=document.getElementById('user-label');if(profileLabel){new MutationObserver(()=>{const heading=document.getElementById('profile-display');if(heading)heading.textContent=profileLabel.textContent||'Account'}).observe(profileLabel,{childList:true,characterData:true,subtree:true})}


/* REVISI 7 — adaptive theme menu label shared across all modern app headers. */
(()=>{
  'use strict';
  function currentTheme(){return document.documentElement.dataset.theme==='dark'?'dark':'light'}
  function updateThemeAction(){
    const button=document.getElementById('theme-toggle');if(!button)return;
    const next=currentTheme()==='dark'?'Light Mode':'Dark Mode';
    const target=currentTheme()==='dark'?'light':'dark';
    button.dataset.modeTarget=target;
    if(button.textContent!==next)button.textContent=next;
    button.setAttribute('aria-label','Switch to '+next);
    button.title='Switch to '+next;
  }
  function init(){
    const button=document.getElementById('theme-toggle');if(!button)return;
    updateThemeAction();
    new MutationObserver(updateThemeAction).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    // Restore the descriptive text when older page-specific theme scripts
    // write glyph-only labels. No event propagation changes to those scripts.
    new MutationObserver(updateThemeAction).observe(button,{childList:true,characterData:true,subtree:true});
    if(/^(?:\/our-project(?:\/\d+)?|\/guide(?:\/(?:new|\d+))?)\/?$/.test(location.pathname)){
      // Project pages don't load workspace.js/dashboard-static.js theme handlers.
      button.addEventListener('click',()=>{
        const next=currentTheme()==='dark'?'light':'dark';
        document.documentElement.dataset.theme=next;
        try{localStorage.setItem('polo-theme',next);localStorage.setItem('theme',next)}catch(_){}
      });
    }
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
  else init();
})();


/* Revisi 16: online indicator participates in the dropdown layout. */
(()=>{
  const initPresence=()=>{
    document.querySelectorAll('.profile-dropdown .profile-heading').forEach(heading=>{
      if(heading.querySelector('.profile-presence'))return;
      const badge=document.createElement('span');
      badge.className='profile-presence';
      badge.setAttribute('aria-label','Current session active');
      const dot=document.createElement('span');dot.className='profile-presence-dot';dot.setAttribute('aria-hidden','true');
      badge.append(dot,document.createTextNode('Online'));
      heading.append(badge);
    });
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initPresence,{once:true});
  else initPresence();
})();


/* Revisi 60: Role-based Tools is part of each header markup, no dynamic injection.
   Keep hidden until authenticated session explicitly confirms Support. */
(()=>{
  'use strict';
  async function setupTools(){
    const menus=document.querySelectorAll('.support-only.mail-tools-dropdown');
    if(!menus.length)return;
    try{
      const response=await fetch('/api/session',{credentials:'same-origin',cache:'no-store'});
      if(!response.ok)return;
      const data=await response.json();
      const show=data?.authenticated===true && data?.user?.role==='support';
      menus.forEach(menu=>{
        menu.hidden=!show;
        if(!show)menu.open=false;
        if(show && location.pathname==='/tools/email')menu.classList.add('active');
      });
    }catch(error){
      console.warn('Tools navigation unavailable: session request failed.',error);
    }
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setupTools,{once:true});
  else setupTools();
})();
