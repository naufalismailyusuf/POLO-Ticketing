/* Revisi 31 — shared public/private Guide playlist; resumable MP4 uploads. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const el = (tag, cls, value) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (value !== undefined) n.textContent = value;
    return n;
  };
  const mib = n => n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GB` : `${(n / 1024 ** 2).toFixed(1)} MB`;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const mbps = value => value > 0 ? `${(value / 1024 / 1024).toFixed(1)} MB/s` : 'Calculating speed...';
  const eta = seconds => Number.isFinite(seconds) && seconds >= 0 ?
    (seconds >= 60 ? `${Math.ceil(seconds / 60)} min remaining` : `${Math.ceil(seconds)} sec remaining`) : 'Estimating time...';
  const detail = (id, value) => {const target=$(id);if(target)target.textContent=value;};
  // XHR provides intra-chunk progress; fetch only reports completion of an
  // entire 16 MB request, which made slow uploads appear frozen.
  function sendChunk(url, token, offset, blob, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr=new XMLHttpRequest();
      xhr.open('PUT',url,true);
      xhr.withCredentials=true;
      xhr.timeout=300000;
      xhr.setRequestHeader('X-CSRF-Token',token);
      xhr.setRequestHeader('X-Upload-Offset',String(offset));
      xhr.setRequestHeader('Content-Type','application/octet-stream');
      let lastTick=0;
      xhr.upload.onprogress=ev=>{
        if(!ev.lengthComputable)return;
        const now=performance.now();
        if(now-lastTick<130 && ev.loaded<ev.total)return;
        lastTick=now;
        onProgress(ev.loaded);
      };
      xhr.onload=()=>{
        let body={};try{body=JSON.parse(xhr.responseText)}catch{}
        resolve({ok:xhr.status>=200&&xhr.status<300,status:xhr.status,data:body});
      };
      xhr.onerror=()=>reject(Error('Network connection interrupted'));
      xhr.ontimeout=()=>reject(Error('Chunk timed out'));
      xhr.onabort=()=>reject(Error('Upload cancelled'));
      xhr.send(blob);
    });
  }
  let guideId = null, videos = [], selectedId = null, busy = false, initialized = false, csrf = '';
  let publicBase = '';
  const isPublic = Boolean($('pg-videos-section'));
  const base = () => isPublic ? 'pg' : 'gd';
  const streamUrl = video => isPublic ? `${publicBase}/videos/${video.id}/stream` : `/api/guides/${guideId}/videos/${video.id}/stream`;
  const show = (id, visible) => { if ($(id)) $(id).hidden = !visible; };
  const status = message => {if ($('gd-video-status')) $('gd-video-status').textContent = message;};
  const pct = (id, label, value) => {
    const safe = Math.min(100, Math.max(0, Math.round(value)));
    if ($(id)) $(id).value = safe;
    if ($(label)) $(label).textContent = safe + '%';
  };
  async function json(url, method = 'GET', body) {
    const options = {method, credentials:'same-origin', headers: {'X-CSRF-Token':csrf}};
    if (body !== undefined) {options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw Error(data.error || `Request failed: HTTP ${response.status}`);
    return data;
  }
  function bindPlaybackFeedback(player) {
    if (!player || player.dataset.playbackBound) return;
    player.dataset.playbackBound = '1';
    const text = $(`${base()}-video-playback-status`);
    if (!text) return;
    let delay;
    const hide = () => {
      clearTimeout(delay);
      text.hidden = true;
      text.textContent = '';
      text.removeAttribute('data-state');
    };
    const pending = () => {
      clearTimeout(delay);
      delay = setTimeout(() => {
        if (player.readyState >= 3 || player.error) return;
        text.textContent = 'Menyiapkan video dari QNAP...';
        text.dataset.state = 'loading';
        text.hidden = false;
      }, 1200);
    };
    // Browser-side click-to-play latency visible in DevTools; no telemetry
    // or identifying link/token is sent anywhere.
    let clickAt=0;
    player.addEventListener('play',()=>{clickAt=performance.now();});
    player.addEventListener('playing',()=>{
      if(clickAt){console.info('Guide video startup (click to playing):',Math.round(performance.now()-clickAt),'ms');clickAt=0;}
    });
    for (const event of ['loadstart', 'waiting', 'stalled', 'seeking']) {
      player.addEventListener(event, pending);
    }
    for (const event of ['canplay', 'playing', 'pause', 'emptied']) {
      player.addEventListener(event, hide);
    }
    player.addEventListener('error', () => {
      clearTimeout(delay);
      if (!player.currentSrc) return;
      text.textContent = 'Video belum bisa diputar. Coba Refresh atau periksa koneksi QNAP.';
      text.dataset.state = 'error';
      text.hidden = false;
    });
  }

  function renderPlaylist() {
    const prefix = base();
    const section = $(`${prefix}-videos-section`);
    if (!section) return;
    const canEdit = !isPublic && Boolean(window.GuideVideos.canEdit);
    section.hidden = !videos.length && !canEdit;
    if (section.hidden) return;
    show(`${prefix}-video-layout`, videos.length > 0);
    if ($(`${prefix}-video-count`)) $(`${prefix}-video-count`).textContent = videos.length ? `${videos.length} video${videos.length===1?'':'s'}` : 'No videos yet';
    if (!isPublic) {
      show('gd-video-management', canEdit && videos.length > 0);
      show('gd-video-upload-zone', canEdit && videos.length < 8);
    }
    if (!videos.length) {
      const player=$(`${prefix}-video-player`);
      if (player) {player.pause();player.removeAttribute('src');player.load();}
      return;
    }
    if (!videos.some(v=>v.id===selectedId)) selectedId=(videos.find(v=>v.is_primary)||videos[0]).id;
    const current=videos.find(v=>v.id===selectedId);
    const player=$(`${prefix}-video-player`);
    bindPlaybackFeedback(player);
    // The first selected video begins buffering near the viewport instead of
    // waiting for the user to press Play. Respect reduced-data preference.
    if (!player.dataset.smartPreloadBound) {
      player.dataset.smartPreloadBound='1';
      const prebuffer=()=>{
        if (navigator.connection?.saveData) return;
        player.preload='auto';
      };
      player.addEventListener('mouseenter',prebuffer);
      player.addEventListener('focusin',prebuffer);
      player.addEventListener('touchstart',prebuffer,{passive:true});
      if ('IntersectionObserver' in window) {
        const watcher=new IntersectionObserver(entries=>{
          if(entries.some(entry=>entry.isIntersecting)) {prebuffer();watcher.disconnect();}
        },{rootMargin:'240px'});
        watcher.observe(player);
      }
    }
    const wanted=streamUrl(current);
    if (player.dataset.src !== wanted) {
      player.pause();player.src=wanted;player.dataset.src=wanted;player.load();
    }
    $(`${prefix}-video-title`).textContent=current.caption || current.original_name;
    $(`${prefix}-video-description`).textContent=`MP4 · ${mib(current.size_bytes)}`;
    const download=$(`${prefix}-video-download`);
    download.hidden=!current.allow_download;
    player.setAttribute('controlsList', current.allow_download ? '' : 'nodownload');
    if (current.allow_download) {
      download.href=wanted+'?download=1';
      download.setAttribute('download','guide-video.mp4');
    }
    const playlist=$(`${prefix}-video-playlist`);playlist.replaceChildren();
    videos.forEach((video,index)=>{
      const item=el('button','gd-video-playlist-item'+(video.id===selectedId?' is-selected':''));
      item.type='button';
      item.setAttribute('aria-label',`Play ${video.caption||video.original_name}`);
      item.append(el('span','gd-video-play-icon',video.id===selectedId?'▶':'▷'));
      const info=el('span','gd-video-play-info');
      info.append(el('strong','',video.caption||video.original_name),el('small','',`${mib(video.size_bytes)}${video.is_primary?' · Main video':''}`));
      item.append(info);
      item.addEventListener('click',()=>{selectedId=video.id;renderPlaylist();});
      playlist.append(item);
    });
    if (canEdit) renderEditor();
  }
  function renderEditor() {
    const box=$('gd-video-editor');if(!box)return;
    box.replaceChildren();
    videos.forEach((video,index)=>{
      const card=el('div','gd-video-edit-item');
      const title=el('input');title.type='text';title.maxLength=180;title.value=video.caption||'';
      title.placeholder='Video title (optional)';title.setAttribute('aria-label','Video title');
      title.addEventListener('input',()=>{video.caption=title.value;});
      const controls=el('div','gd-video-edit-controls');
      const primary=el('button','gd-button gd-video-secondary',video.is_primary?'★ Main':'☆ Set main');
      primary.type='button';primary.disabled=Boolean(video.is_primary);
      primary.addEventListener('click',()=>{videos.forEach(v=>v.is_primary=Number(v.id===video.id));renderEditor();});
      const download=el('label','gd-video-checkbox');const check=el('input');check.type='checkbox';check.checked=!!video.allow_download;
      check.addEventListener('change',()=>{video.allow_download=Number(check.checked)});
      download.append(check,el('span','', 'Allow download'));
      const remove=el('button','gd-button gd-video-remove','Remove');remove.type='button';
      remove.addEventListener('click',async()=>{
        if(!confirm(`Remove video ${video.original_name} from QNAP?`))return;
        try{remove.disabled=true;await json(`/api/guides/${guideId}/videos/${video.id}`,'DELETE');await load(guideId);}
        catch(err){alert(err.message);remove.disabled=false;}
      });
      const arrows=el('span','gd-video-reorder');
      for(const [direction,label] of [[-1,'↑'],[1,'↓']]){
        const move=el('button','gd-button gd-video-secondary',label);move.type='button';move.disabled=index+direction<0||index+direction>=videos.length;
        move.title=direction<0?'Move up':'Move down';
        move.addEventListener('click',()=>{const j=index+direction;[videos[index],videos[j]]=[videos[j],videos[index]];renderEditor();});
        arrows.append(move);
      }
      controls.append(primary,download,arrows,remove);
      card.append(title,controls);box.append(card);
    });
  }
  async function load(gid) {
    if (isPublic || !gid)return;
    guideId=Number(gid);
    try {
      const [session,data] = await Promise.all([
        fetch('/api/session',{credentials:'same-origin'}).then(r=>r.json()),
        json(`/api/guides/${guideId}`)]);
      csrf=session.csrf || '';
      window.GuideVideos.canEdit=!!data.can_edit;
      videos=(data.videos||[]).map(v=>({...v}));
      renderPlaylist();
      initPrivate();
    }catch(e){status('Unable to load video tutorials: '+e.message);}
  }
  function renderPublic(data, token) {
    if (!isPublic) return;
    publicBase=`/api/public/g/${encodeURIComponent(token)}`;
    videos=(data.videos||[]).map(v=>({...v}));
    renderPlaylist();
  }
  function initPrivate() {
    if (initialized || isPublic)return;
    initialized=true;
    $('gd-video-save-layout').addEventListener('click',async e=>{
      const button=e.currentTarget;button.disabled=true;
      try {
        await json(`/api/guides/${guideId}/videos/layout`,'PUT',{
          videos:videos.map(v=>({id:v.id, caption:v.caption||'',primary:!!v.is_primary,allow_download:!!v.allow_download}))});
        status('Video settings saved');await load(guideId);
      }catch(err){status(err.message);}
      finally{button.disabled=false;}
    });
    const file=$('gd-video-file'), zone=$('gd-video-upload-zone');
    file.addEventListener('change',async()=>{
      const selected=file.files?.[0];file.value='';
      if(selected)await upload(selected);
    });
    zone.addEventListener('dragover',ev=>{ev.preventDefault();zone.classList.add('is-dragover')});
    zone.addEventListener('dragleave',()=>zone.classList.remove('is-dragover'));
    zone.addEventListener('drop',async ev=>{
      ev.preventDefault();zone.classList.remove('is-dragover');
      if(ev.dataTransfer?.files?.[0])await upload(ev.dataTransfer.files[0]);
    });
  }
  function keyFor(file) {return `edp-guide-video-29:${guideId}:${file.name}:${file.size}:${file.lastModified}`;}
  async function upload(file) {
    if (busy)return;
    if (!/\.mp4$/i.test(file.name) || file.size < 12 || file.size > 4 * 1024 ** 3) {
      status('Only MP4 files up to 4 GB are supported.');show('gd-video-progress',true);return;
    }
    busy=true;show('gd-video-progress',true);show('gd-video-upload-zone',false);
    detail('gd-video-upload-speed','Preparing upload...');
    detail('gd-video-qnap-speed','Waiting for upload to finish');
    pct('gd-video-upload-progress','gd-video-percent',0);
    pct('gd-video-qnap-progress','gd-video-qnap-percent',0);
    const key=keyFor(file);let existing=null;
    try{existing=localStorage.getItem(key);}catch{}
    try {
      status('Preparing upload...');
      const init=await json(`/api/guides/${guideId}/videos/init`,'POST',{
        name:file.name,size:file.size,mtime:file.lastModified,upload_id:existing});
      const id=init.upload_id;
      try{localStorage.setItem(key,id);}catch{}
      let state=init;
      const uploadStarted=performance.now();
      if(state.state==='failed')throw Error(state.error||'Previous upload failed. Please contact IT Support.');
      while(state.state==='uploading' && state.offset<file.size){
        const end=Math.min(state.offset+16*1024*1024,file.size);
        const body=file.slice(state.offset,end);
        let sent=false;
        for(let retry=0;retry<4 && !sent;retry++){
          try{
            const start=state.offset;
            const response=await sendChunk(`/api/guides/${guideId}/videos/upload/${id}/chunk`,
              csrf,start,body,loaded=>{
                const sentBytes=Math.min(file.size,start+loaded);
                const elapsed=Math.max((performance.now()-uploadStarted)/1000,0.1);
                const speed=Math.max(0,(sentBytes-init.offset)/elapsed);
                pct('gd-video-upload-progress','gd-video-percent',Math.min(99,sentBytes/file.size*100));
                detail('gd-video-upload-speed',`${mbps(speed)} · ${eta((file.size-sentBytes)/speed)}`);
              });
            const data=response.data||{};
            if(response.ok && data.ok){state=data;sent=true;break;}
            if(response.status===409){state=await json(`/api/guides/${guideId}/videos/upload/${id}/status`);sent=true;break;}
            throw Error(data.error||`Upload error HTTP ${response.status}`);
          }catch(err){
            if(retry===3)throw err;
            await wait(600*(retry+1));
            state=await json(`/api/guides/${guideId}/videos/upload/${id}/status`);
            if(state.state!=='uploading'||state.offset>=end)sent=true;
          }
        }
        pct('gd-video-upload-progress','gd-video-percent',state.offset/file.size*100);
        status(`Uploading ${file.name}: ${Math.round(state.offset/file.size*100)}%`);
        const elapsed=Math.max((performance.now()-uploadStarted)/1000,0.1);
        const speed=Math.max(0,(state.offset-init.offset)/elapsed);
        detail('gd-video-upload-speed',`${mbps(speed)} · ${eta((file.size-state.offset)/speed)}`);
      }
      if(state.state==='uploading')state=await json(`/api/guides/${guideId}/videos/upload/${id}/finish`,'POST');
      pct('gd-video-upload-progress','gd-video-percent',100);
      detail('gd-video-upload-speed','Upload received by server');
      let polls=0;
      while(state.state!=='completed'){
        if(state.state==='failed')throw Error(state.error||'Saving to QNAP failed');
        if(++polls>3600)throw Error('Video processing is taking longer than expected. Reopen this Guide and select the same file to check status.');
        pct('gd-video-qnap-progress','gd-video-qnap-percent',state.progress||0);
        const phases={queued:'Waiting for QNAP...',optimizing:'Optimizing MP4 for faster playback...',
          saving:`Saving to QNAP: ${state.progress||0}%`,verifying:'Verifying saved video...',
          indexing:'Updating video library...'};
        status(phases[state.state]||'Processing video...');
        detail('gd-video-qnap-speed',state.state==='saving' ?
          `${mbps(state.qnap_bps||0)} · ${eta(state.qnap_eta)}` :
          state.state==='optimizing'?'Fast Start (if available), original file kept safe' :
          state.state==='queued'?'Waiting for server worker': 'Finishing...');
        await wait(1800);
        state=await json(`/api/guides/${guideId}/videos/upload/${id}/status`);
      }
      pct('gd-video-qnap-progress','gd-video-qnap-percent',100);
      status('Completed: video saved to QNAP.');
      detail('gd-video-qnap-speed',state.faststart==='optimized'?'Completed · Fast Start enabled':
        state.faststart==='already-faststart'?'Completed · MP4 already optimized':'Completed');
      try{localStorage.removeItem(key);}catch{}
      await load(guideId);
    }catch(err){status(`Upload interrupted: ${err.message}. Select the same file to resume.`);}
    finally{busy=false;show('gd-video-upload-zone',window.GuideVideos.canEdit && videos.length<8);}
  }
  window.GuideVideos={load,renderPublic,canEdit:false};
})();
