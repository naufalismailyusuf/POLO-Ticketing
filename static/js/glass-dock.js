(()=>{
  'use strict';
  const original=document.querySelector('.app-header .main-nav');
  if(!original)return;
  const icons={'/':'M3 11 12 4l9 7v9h-6v-6H9v6H3z','/guide':'M5 4h14v16H5zM8 8h8M8 12h8M8 16h5','/files':'M3 6h7l2 3h9v10H3z','/links':'M10 13a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-2 2M14 11a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l2-2'};
  const dock=document.createElement('nav');dock.className='polo-dock';dock.setAttribute('aria-label','Navigasi utama');dock.id='polo-dock';
  original.querySelectorAll('a[href]').forEach(source=>{
    const link=source.cloneNode(true), key=new URL(link.href,location.href).pathname;
    if(icons[key]){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),path=document.createElementNS('http://www.w3.org/2000/svg','path');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');path.setAttribute('d',icons[key]);svg.append(path);link.prepend(svg)}
    if(link.classList.contains('active'))link.setAttribute('aria-current','page');dock.append(link);
  });
  if(!dock.children.length)return;
  const handle=document.createElement('button');handle.type='button';handle.className='polo-dock-handle';handle.setAttribute('aria-label','Tampilkan navigasi');handle.setAttribute('aria-controls',dock.id);handle.setAttribute('aria-expanded','false');handle.hidden=true;
  document.body.append(dock,handle);document.body.classList.add('polo-dock-ready');let timer;
  function hide(){if(dock.contains(document.activeElement)||dock.matches(':hover'))return;dock.classList.add('is-hidden');dock.inert=true;dock.setAttribute('aria-hidden','true');handle.hidden=false}
  function schedule(){clearTimeout(timer);timer=setTimeout(hide,5000)}
  handle.addEventListener('click',()=>{dock.classList.remove('is-hidden');dock.inert=false;dock.removeAttribute('aria-hidden');handle.hidden=true;dock.querySelector('.active, a').focus();schedule()});
  dock.addEventListener('pointerenter',()=>clearTimeout(timer));dock.addEventListener('pointerleave',schedule);dock.addEventListener('focusin',()=>clearTimeout(timer));dock.addEventListener('focusout',schedule);dock.addEventListener('click',schedule);document.addEventListener('pointerdown',e=>{if(!dock.contains(e.target)&&e.target!==handle)schedule()});schedule();
})();
