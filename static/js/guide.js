/* Guide knowledge base — Revisi 15. Plain JS; existing application headers remain unchanged. */
(async () => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const node = (tag, cls = '', text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const notice = (message, error = false) => {
    const el = $('#gd-notice'); if (!el) return;
    el.textContent = message || '';
    el.dataset.error = error ? 'true' : 'false';
  };
  let csrf = '', user = null, saved = null, editing = false;
  let guideShareInfo = null, guideMenuRequest = 0;
  const path = location.pathname;
  const isIndex = Boolean($('#gd-list'));
  const isNew = path.replace(/\/$/, '') === '/guide/new';
  const match = path.match(/^\/guide\/(\d+)\/?$/);
  const guideId = match ? Number(match[1]) : null;
  let allGuides = [];
  let workingId = guideId;
  let galleryMedia = [];
  let activeGallery = 0;
  let coverToken = null;
  let lightboxItems = [], lightboxIndex = 0, zoom = 1;
  const MAX_GALLERY = 12, MAX_STEP_MEDIA = 6, MAX_MEDIA_SIZE = 8 * 1024 * 1024;
  const accepted = ['image/png', 'image/jpeg', 'image/webp'];
  const imageUrl = (gid, id) => `/api/guides/${gid}/images/${id}`;
  const mediaSrc = item => item.file ? item.localUrl : imageUrl(workingId, item.id);
  let uid = 0;
  const toMedia = image => ({...image, token: `media-${++uid}`, caption:image.caption||''});
  const makePending = file => ({file, localUrl:URL.createObjectURL(file), token:`media-${++uid}`, caption:file.name.replace(/\.[^.]+$/,'').slice(0,150)});
  function discard(item) { if (item.localUrl) URL.revokeObjectURL(item.localUrl); }
  function checkFiles(files, remaining) {
    const list = Array.from(files);
    if (list.length > remaining) throw Error(`Maximum ${remaining} additional images allowed`);
    for (const file of list) {
      if (!accepted.includes(file.type) || !/\.(png|jpe?g|webp)$/i.test(file.name)) throw Error('Use PNG, JPG or WebP images');
      if (!file.size || file.size > MAX_MEDIA_SIZE) throw Error(`${file.name}: file must be between 1 byte and 8 MB`);
    }
    return list;
  }
  function imageElement(item, className='') {
    const img = node('img', className);
    img.src = mediaSrc(item); img.alt = item.caption || item.original_name || 'Guide image'; img.loading = 'lazy';
    return img;
  }

  function openLightbox(items, selected=0) {
    lightboxItems = items;
    lightboxIndex = selected;
    $('#gd-lightbox').hidden = false;
    document.body.classList.add('gd-lightbox-open');
    refreshLightbox();
    $('#gd-lightbox-close').focus();
  }
  function closeLightbox() {
    $('#gd-lightbox').hidden = true;
    document.body.classList.remove('gd-lightbox-open');
    $('#gd-lightbox-image').removeAttribute('src');
  }
  function refreshLightbox() {
    if (!lightboxItems.length) return;
    const item = lightboxItems[lightboxIndex];
    const img = $('#gd-lightbox-image');
    img.src = mediaSrc(item);
    img.alt = item.caption || item.original_name || 'Guide image';
    $('#gd-lightbox-label').textContent = item.caption || item.original_name || 'Guide image';
    $('#gd-lightbox-count').textContent = `${lightboxIndex+1} / ${lightboxItems.length}`;
    zoom=1; img.style.transform='scale(1)';
    $('#gd-lightbox-prev').disabled = lightboxItems.length<2;
    $('#gd-lightbox-next').disabled = lightboxItems.length<2;
  }
  function shiftLightbox(change) {
    if (lightboxItems.length < 2) return;
    lightboxIndex = (lightboxIndex + change + lightboxItems.length) % lightboxItems.length;
    refreshLightbox();
  }
  function setupLightbox() {
    $('#gd-lightbox-close').addEventListener('click', closeLightbox);
    $('#gd-lightbox-prev').addEventListener('click', () => shiftLightbox(-1));
    $('#gd-lightbox-next').addEventListener('click', () => shiftLightbox(1));
    $('#gd-lightbox-plus').addEventListener('click', () => {zoom=Math.min(3,zoom+.25);$('#gd-lightbox-image').style.transform=`scale(${zoom})`;});
    $('#gd-lightbox-minus').addEventListener('click', () => {zoom=Math.max(1,zoom-.25);$('#gd-lightbox-image').style.transform=`scale(${zoom})`;});
    $('#gd-lightbox').addEventListener('click', e => {if(e.target.id==='gd-lightbox') closeLightbox();});
    document.addEventListener('keydown', e => {
      if ($('#gd-lightbox').hidden) return;
      if (e.key === 'Escape') closeLightbox();
      if (e.key === 'ArrowLeft') shiftLightbox(-1);
      if (e.key === 'ArrowRight') shiftLightbox(1);
    });
  }
  function galleryImages() {
    return (saved?.images || []).filter(x => x.kind==='gallery').sort((a,b) => Number(b.is_primary)-Number(a.is_primary) || a.position-b.position);
  }
  function drawGallery() {
    const images=galleryImages();
    const panel=$('#gd-gallery-view');
    panel.hidden=!images.length;
    if (!images.length) return;
    activeGallery=Math.min(activeGallery, images.length-1);
    const selected=images[activeGallery], hero=$('#gd-gallery-hero'), thumbs=$('#gd-gallery-thumbs');
    hero.replaceChildren(); thumbs.replaceChildren();
    const large=node('button','gd-gallery-large');large.type='button';large.title='View full-size image';
    large.append(imageElement(selected), node('span','gd-expand-label','⤢  Fullscreen / Zoom'));
    large.addEventListener('click',()=>openLightbox(images,activeGallery));
    hero.append(large);
    if (selected.caption) hero.append(node('p','gd-gallery-caption',selected.caption));
    $('#gd-gallery-counter').textContent=`${images.length} image${images.length===1?'':'s'}`;
    images.forEach((item,index)=>{
      const button=node('button','gd-thumb'+(index===activeGallery?' gd-thumb-active':''));button.type='button';
      button.setAttribute('aria-label',`Show image ${index+1}: ${item.caption||item.original_name}`);
      button.append(imageElement(item));
      const footer=node('span','gd-thumb-footer');
      footer.append(node('span','',item.caption||item.original_name));
      if(item.is_primary) footer.append(node('span','gd-thumb-cover','Main'));
      button.append(footer);
      button.addEventListener('click',()=>{activeGallery=index;drawGallery()});
      thumbs.append(button);
    });
  }
  function renderMediaEditor(container, media, limit, onChange, gallery=false) {
    container.replaceChildren();
    if (!media.length) {container.append(node('p','gd-media-empty',gallery?'No topology images yet. Upload a network diagram or screenshot.':'No screenshots added yet.'));return;}
    media.forEach((item,index)=>{
      const card=node('div','gd-media-edit-card');
      const preview=node('button','gd-media-preview');preview.type='button';preview.title='Preview image';
      preview.append(imageElement(item));preview.addEventListener('click',()=>openLightbox(media,index));
      card.append(preview);
      const controls=node('div','gd-media-controls');
      const caption=node('input');caption.type='text';caption.maxLength=150;caption.placeholder='Image caption (optional)';caption.value=item.caption||'';
      caption.setAttribute('aria-label','Image caption');caption.addEventListener('input',()=>{item.caption=caption.value});
      controls.append(caption);
      const row=node('div','gd-media-actions');
      if (gallery) {
        const primary=node('button','gd-cover-action'+(coverToken===item.token?' gd-cover-selected':''),coverToken===item.token?'★ Main image':'☆ Set as main');
        primary.type='button';primary.addEventListener('click',()=>{coverToken=item.token;onChange()});row.append(primary);
      }
      if (gallery) {
        for (const [direction,symbol] of [[-1,'←'],[1,'→']]) {
          const move=node('button','gd-move-image ui-icon-arrow',symbol);move.type='button';
          move.title=direction===-1?'Move image left':'Move image right';
          move.setAttribute('aria-label',move.title);
          move.disabled=(index+direction<0 || index+direction>=media.length);
          move.addEventListener('click',()=>{
            const other=index+direction;
            [media[index],media[other]]=[media[other],media[index]];
            onChange();
          });
          row.append(move);
        }
      }
      const remove=node('button','gd-remove-image','Remove');remove.type='button';
      remove.addEventListener('click',()=>{
        media.splice(index,1);discard(item);
        if(gallery && coverToken===item.token) coverToken=media[0]?.token||null;
        onChange();
      });row.append(remove);
      controls.append(row);card.append(controls);container.append(card);
    });
  }
  function renderGalleryEditor(){renderMediaEditor($('#gd-edit-gallery'),galleryMedia,MAX_GALLERY,renderGalleryEditor,true)}
  async function uploadPendingMedia(gid, item, kind, stepPosition) {
    if(!item.file) return;
    const data=new FormData();data.append('image',item.file);data.append('kind',kind);data.append('caption',item.caption||'');
    if(kind==='step') data.append('step_position',String(stepPosition));
    const response=await fetch(`/api/guides/${gid}/images`,{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf},body:data});
    let result;
    try{result=await response.json()}catch{throw Error(`Image upload failed: ${item.file.name}`)}
    if(!response.ok||!result.ok)throw Error(`${item.file.name}: ${result.error||'Upload failed'}`);
    item.id=result.id;
    item.original_name=item.file.name;
    discard(item);
    delete item.file;delete item.localUrl;
  }
  async function saveImages(gid) {
    const documentation = document.querySelector('input[name="guide_format"]:checked')?.value === 'documentation';
    const rows = documentation ? [] : Array.from($('#gd-edit-steps').children);
    // When editing a legacy workflow as Documentation, preserve its step images.
    const retainedStepMedia = documentation ? (saved?.steps || []).map((_, index) =>
      (saved.images || []).filter(img => img.kind === 'step' && img.step_position === index + 1)
        .map(img => ({id:img.id,caption:img.caption || ''}))) : null;
    const initialSteps = documentation ? retainedStepMedia : rows.map(r =>
      r._media.filter(i => i.id).map(i => ({id:i.id,caption:i.caption || ''})));
    // Reconcile removals and step reordering first, so replacements respect size limits.
    await api(`/api/guides/${gid}/media-layout`,'PUT',{
      gallery:galleryMedia.filter(item=>item.id).map(item=>({id:item.id,caption:item.caption||''})),
      primary_id:galleryMedia.find(i=>i.token===coverToken && i.id)?.id || null,
      steps:initialSteps
    });
    for(const item of galleryMedia) await uploadPendingMedia(gid,item,'gallery');
    for(let i=0;i<rows.length;i++) for(const item of rows[i]._media) await uploadPendingMedia(gid,item,'step',i+1);
    const layout={gallery:galleryMedia.map(item=>({id:item.id,caption:item.caption||''})),
      primary_id:galleryMedia.find(i=>i.token===coverToken)?.id||null,
      steps:documentation ? retainedStepMedia : rows.map(r=>r._media.map(i=>({id:i.id,caption:i.caption||''})))};
    await api(`/api/guides/${gid}/media-layout`,'PUT',layout);
  }


  async function api(url, method = 'GET', payload) {
    const response = await fetch(url, {
      method, credentials: 'same-origin',
      headers: {'X-CSRF-Token': csrf, ...(payload !== undefined ? {'Content-Type': 'application/json'} : {})},
      body: payload !== undefined ? JSON.stringify(payload) : undefined
    });
    let result;
    try { result = await response.json(); }
    catch { throw Error('Unexpected server response'); }
    if (!response.ok || !result.ok) throw Error(result.error || 'Request failed');
    return result;
  }

  async function initializeSession() {
    const response = await fetch('/api/session', {credentials: 'same-origin'});
    const session = await response.json();
    if (!response.ok || !session.authenticated) {
      location.assign('/login?next=' + encodeURIComponent(location.pathname));
      throw Error('Please sign in to continue');
    }
    csrf = session.csrf;
    user = session.user;
    if ($('#user-label')) $('#user-label').textContent = user.display || user.username;
    if ($('#user-role')) $('#user-role').textContent = user.role;
    if ($('#profile-display')) $('#profile-display').textContent = user.display || user.username;
    if ($('#logout-csrf')) $('#logout-csrf').value = csrf;
    document.querySelectorAll('.admin-only').forEach((element) => {element.hidden = user.role !== 'admin';});
    // Mobile navigation is initialized independently by mobile-nav-v64.js.
  }

  function guideCard(guide) {
    const card = node('a', 'gd-guide-card');
    card.href = '/guide/' + guide.id;
    const top = node('div', 'gd-card-top');
    const category = node('span', 'gd-category', guide.category);
    const status = node('span', 'gd-pill ' + (guide.status === 'Published' ? 'gd-is-published' : 'gd-is-draft'), guide.status);
    top.append(category, status);
    card.append(top, node('h3', '', guide.title), node('p', 'gd-card-desc', guide.description || 'Step-by-step team reference'));
    const bottom = node('div', 'gd-card-bottom');
    const openLabel=node('span','gd-card-open','Open');openLabel.append(node('span','ui-inline-arrow','→'));
    bottom.append(node('span', '', guide.content_format === 'documentation' ? 'Documentation' : String(guide.step_count || 0) + ' steps'), node('span', '', 'by ' + guide.author), openLabel);
    card.append(bottom);
    return card;
  }

  function renderIndex() {
    const query = ($('#gd-search').value || '').trim().toLocaleLowerCase();
    const category = $('#gd-category').value;
    const filtered = allGuides.filter(g => (!category || g.category === category) &&
      [g.title, g.description, g.category, g.author].join(' ').toLocaleLowerCase().includes(query));
    const list = $('#gd-list'); list.replaceChildren();
    if (!filtered.length) {
      const empty = node('div', 'gd-empty');
      empty.append(node('div', 'gd-empty-icon', '◇'), node('h3', '', query || category ? 'No matching guide' : 'No guides yet'),
        node('p', '', 'Create a guide to document a workflow the team can reuse.'));
      list.append(empty);
    }
    filtered.forEach((g) => list.append(guideCard(g)));
    $('#gd-total').textContent = String(allGuides.length);
    const guideLabel = $('#gd-guide-label');
    if (guideLabel) guideLabel.textContent = allGuides.length === 1 ? 'guide' : 'guides';
    $('#gd-published').textContent = String(allGuides.filter(g => g.status === 'Published').length);
    const draftCount = allGuides.filter(g => g.status === 'Draft').length;
    $('#gd-drafts').textContent = String(draftCount);
    const draftLabel = $('#gd-draft-label');
    if (draftLabel) draftLabel.textContent = draftCount === 1 ? 'draft' : 'drafts';
    const draftSummary = $('#gd-draft-count');
    if (draftSummary) draftSummary.hidden = draftCount === 0;
  }

  async function initIndex() {
    $('#gd-search').addEventListener('input', renderIndex);
    $('#gd-category').addEventListener('change', renderIndex);
    const result = await api('/api/guides');
    allGuides = result.guides;
    renderIndex();
  }

  function formatDate(value) {
    if (!value) return '';
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return value;
    return date.toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: 'numeric'});
  }

  function humanSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    return bytes < 1024*1024 ? `${(bytes/1024).toFixed(1)} KB` : `${(bytes/1024/1024).toFixed(1)} MB`;
  }
  function renderDocuments() {
    const container=$('#gd-document-list'); if(!container) return;
    container.replaceChildren();
    const files=saved?.files || [];
    if (!files.length) container.append(node('p','gd-media-empty','No downloadable documents have been attached yet.'));
    files.forEach(file=>{
      const line=node('div','gd-document-item');
      const body=node('div','gd-document-name');
      body.append(node('span','gd-document-icon','▤'));
      const desc=node('div');desc.append(node('strong','',file.original_name),node('small','',humanSize(file.size_bytes)));
      body.append(desc);line.append(body);
      const actions=node('div','gd-document-actions');
      const download=node('a','gd-button','Download');download.href=`/api/guides/${workingId}/files/${file.id}`;
      download.setAttribute('download',file.original_name);actions.append(download);
      if(saved.can_edit){
        const remove=node('button','gd-button gd-danger','Remove');remove.type='button';
        remove.addEventListener('click',async()=>{
          if(!confirm(`Remove ${file.original_name} from QNAP?`))return;
          remove.disabled=true;
          try{await api(`/api/guides/${workingId}/files/${file.id}`,'DELETE');saved=await api(`/api/guides/${workingId}`);renderDocuments();notice('Documentation removed');}
          catch(err){notice(err.message,true);remove.disabled=false;}
        });actions.append(remove);
      }
      line.append(actions);container.append(line);
    });
    $('#gd-document-upload-zone').hidden=!saved?.can_edit;
  }
  async function uploadDocumentation(files){
    const list=Array.from(files);if(!list.length)return;
    if(list.length + (saved?.files?.length||0) > 15)throw Error('Maximum 15 files per Guide');
    const allowed=/\.(pdf|docx?|xlsx?|pptx?|txt|csv|zip|md)$/i;
    for (const file of list){if(!allowed.test(file.name)||!file.size||file.size>25*1024*1024)throw Error(`${file.name}: unsupported format or exceeds 25 MB`);}
    for (let i=0;i<list.length;i++){
      const file=list[i];$('#gd-document-upload-status').textContent=`Uploading ${i+1}/${list.length}: ${file.name}`;
      const form=new FormData();form.append('file',file);
      const response=await fetch(`/api/guides/${workingId}/files`,{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf},body:form});
      const result=await response.json().catch(()=>({error:'Unexpected server response'}));
      if(!response.ok||!result.ok)throw Error(result.error||'Documentation upload failed');
    }
    $('#gd-document-upload-status').textContent='Documentation saved to QNAP.';
    saved=await api(`/api/guides/${workingId}`);
    renderDocuments();
  }
  // Single canonical HTTPS link works for LAN and external clients when DNS is reachable.
  const PUBLIC_GUIDE_ORIGIN = 'https://tiket.pmj.my.id';
  function publicGuideUrl(token){
    return `${PUBLIC_GUIDE_ORIGIN}/g/${encodeURIComponent(token)}`;
  }
  async function showSharePanel(){
    const panel=$('#gd-share-panel');panel.hidden=false;
    try{
      const r=await api(`/api/guides/${workingId}/share`);
      guideShareInfo = r;
      const active=Boolean(r.enabled&&r.token), links=$('#gd-share-links');
      links.hidden=!active;
      $('#gd-share-url').value=active?publicGuideUrl(r.token):'';
      $('#gd-share-status').textContent=r.publish_required
        ? 'This Guide is still Draft. Edit its Visibility to Published, then save before creating a public link.'
        : active
          ? 'Public HTTPS link is active. Anyone with the link can view this Guide and download its attached files.'
          : r.can_manage
            ? 'No public link yet. Select Create link to allow read-only access without a dashboard login.'
            : 'No public link yet. Ask the author or an Admin to create one.';
      $('#gd-share-enable').hidden=active||r.publish_required||!r.can_manage;
      $('#gd-share-disable').hidden=!active||!r.can_manage;
    }catch(err){notice(err.message,true)}
  }
  async function copyField(id){
    const input=document.getElementById(id);const value=input.value;
    try {await navigator.clipboard.writeText(value);notice('Link copied');}
    catch{input.focus();input.select();try{document.execCommand('copy');notice('Link selected/copied');}catch{notice('Select the link and copy it manually',true)}}
  }

  function closeActions(focusTrigger = false) {
    const menu = $('#gd-action-menu');
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    guideMenuRequest += 1;
    $('#gd-more').setAttribute('aria-expanded', 'false');
    if (focusTrigger) $('#gd-more').focus();
  }
  function renderActions(shareData) {
    const canEdit = !!saved?.can_edit;
    const active = !!(shareData?.enabled && shareData?.token && saved?.guide?.status === 'Published');
    const canManage = !!shareData?.can_manage;
    $('#gd-action-copy').hidden = !active;
    $('#gd-action-open').hidden = !active;
    $('#gd-share-disable').hidden = !(active && canManage);
    $('#gd-menu-divider').hidden = !canEdit || !active;
    $('#gd-delete').hidden = !canEdit;
    $('#gd-menu-empty').hidden = active || canEdit;
    $('#gd-menu-loading').hidden = true;
  }
  async function openActions() {
    const menu = $('#gd-action-menu');
    if (!menu.hidden) {closeActions();return;}
    menu.hidden = false;
    const request = ++guideMenuRequest;
    $('#gd-more').setAttribute('aria-expanded', 'true');
    $('#gd-menu-loading').hidden = false;
    $('#gd-menu-empty').hidden = true;
    ['#gd-action-copy','#gd-action-open','#gd-share-disable','#gd-menu-divider','#gd-delete'].forEach(s => $(s).hidden = true);
    try {
      // Fetch the current backend state instead of relying on a potentially stale token.
      guideShareInfo = await api(`/api/guides/${workingId}/share`);
      if (menu.hidden || request !== guideMenuRequest) return;
      renderActions(guideShareInfo);
    } catch(error) {
      guideShareInfo = null;
      if (menu.hidden || request !== guideMenuRequest) return;
      renderActions(null);
      notice('Could not load public link status: ' + error.message, true);
    }
  }
  function activePublicUrl() {
    if (!guideShareInfo?.enabled || !guideShareInfo.token) return null;
    return publicGuideUrl(guideShareInfo.token);
  }
  async function copyPublicLink() {
    const value = activePublicUrl();
    closeActions();
    if (!value) {notice('No active public link. Open Share Guide to create one.', true);return;}
    try {
      if (!navigator.clipboard?.writeText) throw Error('Clipboard API unavailable');
      await navigator.clipboard.writeText(value);
      notice('Public Guide link copied');
    } catch {
      // Fallback for browsers where Clipboard API permission is unavailable.
      const input = node('textarea');
      input.value = value; input.setAttribute('readonly','');
      input.style.cssText = 'position:fixed;top:0;left:-9999px;opacity:0';
      document.body.append(input); input.select();
      let copied = false;
      try {copied = document.execCommand('copy');} catch {}
      input.remove();
      if (copied) notice('Public Guide link copied');
      else {$('#gd-share-panel').hidden = false;await showSharePanel();notice('Use the Copy button in Share Guide to copy the link.', true);}
    }
  }

  function drawDetail() {
    if (!saved) return;
    const guide = saved.guide;
    $('#gd-view-title').textContent = guide.title;
    $('#gd-view-category').textContent = guide.category + ' / TEAM GUIDE';
    $('#gd-view-desc').textContent = guide.description || 'No description provided.';
    $('#gd-view-state').textContent = guide.status;
    $('#gd-view-state').className = 'gd-pill ' + (guide.status === 'Published' ? 'gd-is-published' : 'gd-is-draft');
    $('#gd-view-author').textContent = 'Created by ' + guide.author;
    $('#gd-view-date').textContent = 'Updated ' + formatDate(guide.updated_at);
    const isDocumentation = guide.content_format === 'documentation';
    $('#gd-view-count').textContent = isDocumentation ? 'Documentation' : saved.steps.length + ' steps';
    $('#gd-doc-view').hidden = !isDocumentation;
    $('#gd-flow').closest('.gd-panel').hidden = isDocumentation;
    $('#gd-steps').closest('.gd-panel').hidden = isDocumentation;
    if (isDocumentation) window.PoloGuideMarkdown.render($('#gd-doc-body'), guide.content_markdown || '');
    $('#gd-edit').hidden = !saved.can_edit;
    $('#gd-action-wrap').hidden = !saved.can_edit && guide.status !== 'Published';
    $('#gd-share').hidden = !saved.can_edit && guide.status !== 'Published';
    guideShareInfo = null;
    closeActions();
    renderDocuments();
    window.GuideVideos?.load(workingId);
    drawGallery();
    const flow = $('#gd-flow'), instructions = $('#gd-steps');
    flow.replaceChildren(); instructions.replaceChildren();
    saved.steps.forEach((step, i) => {
      if (i > 0) {
        // One SVG connector: consistent line and small arrowhead on every viewport.
        const connector = node('span', 'gd-flow-connector');
        connector.setAttribute('aria-hidden', 'true');
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 46 18');
        svg.setAttribute('focusable', 'false');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', 'M1 9 H43 M37 3 L43 9 L37 15');
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', 'currentColor');
        path.setAttribute('stroke-width', '1.6');
        path.setAttribute('stroke-linecap', 'round');
        path.setAttribute('stroke-linejoin', 'round');
        svg.append(path);
        connector.append(svg);
        flow.append(connector);
      }
      const control = node('button', 'gd-flow-node'); control.type = 'button';
      const index = node('span', 'gd-flow-number', String(i + 1).padStart(2, '0'));
      control.append(index, node('span', 'gd-flow-title', step.title));
      control.addEventListener('click', () => {
        $('#gd-step-' + (i + 1)).scrollIntoView({behavior: 'smooth', block: 'center'});
      });
      flow.append(control);
      const article = node('article', 'gd-detail-step');
      article.id = 'gd-step-' + (i + 1);
      const bubble = node('span', 'gd-detail-step-number', String(i + 1).padStart(2, '0'));
      const content = node('div', 'gd-detail-step-content');
      content.append(node('h3', '', step.title));
      if (step.instruction) content.append(node('p', 'gd-preline', step.instruction));
      if (step.command) {
        const commandBox = node('div', 'gd-command');
        const code = node('pre', '', step.command);
        const copy = node('button', 'gd-copy', 'Copy'); copy.type = 'button';
        copy.addEventListener('click', async () => {
          try { await navigator.clipboard.writeText(step.command); copy.textContent = 'Copied'; }
          catch {copy.textContent = 'Select text to copy';}
        });
        commandBox.append(code, copy); content.append(commandBox);
      }
      if (step.reference_url) {
        const link = node('a', 'gd-reference', 'Open reference ↗');
        link.href = step.reference_url; link.target = '_blank'; link.rel = 'noopener noreferrer';
        content.append(link);
      }
      const attachments=(saved.images||[]).filter(x=>x.kind==='step' && x.step_position===i+1).sort((a,b)=>a.position-b.position);
      if(attachments.length){
        const screenshots=node('div','gd-step-images');
        attachments.forEach((item,index)=>{
          const btn=node('button','gd-step-image');btn.type='button';btn.title='Zoom image';
          btn.append(imageElement(item),node('span','gd-image-caption',item.caption||item.original_name));
          btn.addEventListener('click',()=>openLightbox(attachments,index));screenshots.append(btn);
        });
        content.append(screenshots);
      }
      article.append(bubble, content);
      instructions.append(article);
    });
  }

  function renumberSteps() {
    const rows = Array.from($('#gd-edit-steps').children);
    rows.forEach((row, i) => {
      row.querySelector('.gd-edit-step-num').textContent = 'Step ' + String(i + 1).padStart(2, '0');
      row.querySelector('[data-action="up"]').disabled = i === 0;
      row.querySelector('[data-action="down"]').disabled = i === rows.length - 1;
      row.querySelector('[data-action="remove"]').disabled = rows.length === 1;
      $('#gd-add-step').disabled = rows.length >= 40;
    });
  }

  function editInput(labelText, type, field, value, opts = {}) {
    const label = node('label', 'gd-field');
    const input = type === 'textarea' ? node('textarea') : node('input');
    if (type !== 'textarea') input.type = type;
    input.dataset.field = field;
    input.value = value || '';
    if (opts.rows) input.rows = opts.rows;
    if (opts.max) input.maxLength = opts.max;
    if (opts.required) input.required = true;
    if (opts.placeholder) input.placeholder = opts.placeholder;
    label.append(node('span', '', labelText), input);
    return label;
  }

  function addStep(step = {}) {
    const row = node('section', 'gd-edit-step');
    const heading = node('div', 'gd-edit-step-heading');
    const number = node('strong', 'gd-edit-step-num');
    const actions = node('div', 'gd-step-actions');
    for (const [action, label, title] of [['up', '↑', 'Move step up'], ['down', '↓', 'Move step down'], ['remove', '×', 'Remove step']]) {
      const b = node('button', action === 'remove' ? 'gd-icon gd-icon-danger' : 'gd-icon', label);
      b.type = 'button'; b.dataset.action = action; b.title = title; b.setAttribute('aria-label', title);
      b.addEventListener('click', () => {
        const container = $('#gd-edit-steps');
        if (action === 'remove') {row._media.forEach(discard);row.remove();}
        if (action === 'up' && row.previousElementSibling) container.insertBefore(row, row.previousElementSibling);
        if (action === 'down' && row.nextElementSibling) container.insertBefore(row.nextElementSibling, row);
        renumberSteps();
      });
      actions.append(b);
    }
    heading.append(number, actions);
    row.append(heading,
      editInput('Step title', 'text', 'title', step.title, {max: 120, required: true, placeholder: 'e.g. Prepare Windows LTSC ISO'}),
      editInput('Instructions', 'textarea', 'instruction', step.instruction, {rows: 3, max: 4000, placeholder: 'Write what the team should do...'}));
    const extra = node('details', 'gd-step-extra');
    const summary = node('summary', '', 'Command & reference (optional)');
    extra.append(summary, editInput('Command / configuration', 'textarea', 'command', step.command, {rows: 2, max: 2000, placeholder: 'Optional command to copy'}),
      editInput('Reference URL', 'url', 'reference_url', step.reference_url, {max: 700, placeholder: 'https://...'}));
    if (step.command || step.reference_url) extra.open = true;
    row.append(extra);
    row._media = (saved && step.position ? saved.images.filter(i => i.kind==='step' && i.step_position===step.position).sort((a,b)=>a.position-b.position).map(toMedia):[]);
    const imageHead=node('div','gd-step-media-heading');
    imageHead.append(node('span','','Step screenshots'));
    const uploadLabel=node('label','edp-dropzone edp-dropzone--compact gd-edp-step');
    uploadLabel.dataset.edpDropzone='';
    uploadLabel.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V4m-4 4 4-4 4 4M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/></svg><span class="edp-dropzone-content"><strong>Drop screenshots or browse</strong><small data-edp-drop-status>PNG, JPG, WebP · 8 MB each</small></span>';
    const fileInput=node('input');fileInput.type='file';fileInput.accept='image/png,image/jpeg,image/webp';fileInput.multiple=true;fileInput.className='edp-file-native';
    uploadLabel.append(fileInput);imageHead.append(uploadLabel);
    const imageList=node('div','gd-edit-step-media');
    const refresh=()=>renderMediaEditor(imageList,row._media,MAX_STEP_MEDIA,refresh,false);
    fileInput.addEventListener('change',()=>{
      try{checkFiles(fileInput.files,MAX_STEP_MEDIA-row._media.length).forEach(file=>row._media.push(makePending(file)));refresh();notice('');}
      catch(err){notice(err.message,true)}
      fileInput.value='';
    });
    row.append(imageHead,imageList);refresh();
    $('#gd-edit-steps').append(row);
    renumberSteps();
    row.querySelector('[data-field="title"]').focus({preventScroll: true});
  }

  function setGuideFormat(format) {
    const documentation = format === 'documentation';
    $('#gm-editor-doc').hidden = !documentation;
    $('#gm-workflow-editor').hidden = documentation;
    $('#gm-workflow-editor').querySelectorAll('[data-field="title"]').forEach(input => {
      input.required = !documentation;
    });
    $('#gd-editor-hint').textContent = documentation ? 'Write a document using Markdown.' : 'At least one step is required.';
    if (!documentation && !$('#gd-edit-steps').children.length) addStep();
  }
  function insertAtCursor(text) {
    const input = $('#gm-markdown'), start = input.selectionStart, end = input.selectionEnd;
    input.setRangeText(text, start, end, 'end');input.focus();
    if (!$('#gm-editor-preview').hidden) window.PoloGuideMarkdown.render($('#gm-editor-preview'), input.value);
  }

  function showEditor(newGuide = false) {
    editing = true;
    $('#gd-view').hidden = true; $('#gd-editor').hidden = false;
    $('#gd-editor-heading').textContent = newGuide ? 'Create Guide' : 'Edit Guide';
    const form = $('#gd-form'); form.reset();
    form.elements.title.value = newGuide ? '' : saved.guide.title;
    form.elements.description.value = newGuide ? '' : saved.guide.description;
    form.elements.category.value = newGuide ? 'Deployment' : saved.guide.category;
    form.elements.status.value = newGuide ? 'Draft' : saved.guide.status;
    galleryMedia.forEach(discard);
    galleryMedia=newGuide?[]:(saved.images||[]).filter(i=>i.kind==='gallery').sort((a,b)=>a.position-b.position).map(toMedia);
    coverToken=galleryMedia.find(i=>i.is_primary)?.token || galleryMedia[0]?.token || null;
    renderGalleryEditor();
    $('#gd-edit-steps').replaceChildren();
    const initialFormat = newGuide ? 'documentation' : (saved.guide.content_format || 'workflow');
    $('#gm-markdown').value = newGuide ? '' : (saved.guide.content_markdown || '');
    (newGuide ? [] : saved.steps).forEach(addStep);
    const option = document.querySelector(`input[name="guide_format"][value="${initialFormat}"]`);
    option.checked = true;
    setGuideFormat(initialFormat);
    $('#gm-editor-preview').hidden = true;
    $('#gm-preview-toggle').textContent = 'Preview';
  }

  function leaveEditor() {
    if (isNew) { location.assign('/workspace?tab=guides'); return; }
    editing = false;
    $('#gd-editor').hidden = true;
    $('#gd-view').hidden = false;
    drawDetail();
    notice('');
  }

  function collectSteps() {
    return Array.from($('#gd-edit-steps').children).map((row) => {
      const value = field => row.querySelector('[data-field="' + field + '"]').value;
      return {title: value('title'), instruction: value('instruction'), command: value('command'), reference_url: value('reference_url')};
    });
  }

  async function initDetail() {
    setupLightbox();
    $('#gd-gallery-files').addEventListener('change',event=>{
      try{checkFiles(event.target.files,MAX_GALLERY-galleryMedia.length).forEach(file=>galleryMedia.push(makePending(file)));
        if(!coverToken)coverToken=galleryMedia[0]?.token||null;
        renderGalleryEditor();notice('');}
      catch(error){notice(error.message,true)}
      event.target.value='';
    });
    $('#gd-cancel').addEventListener('click', leaveEditor);
    $('#gd-edit').addEventListener('click', () => {closeActions();showEditor(false)});
    $('#gd-share').addEventListener('click', () => {closeActions();showSharePanel()});
    $('#gd-more').addEventListener('click', (event) => {event.stopPropagation();openActions()});
    $('#gd-action-copy').addEventListener('click', copyPublicLink);
    $('#gd-action-open').addEventListener('click', () => {
      const href = activePublicUrl();
      closeActions();
      if (href) window.open(href, '_blank', 'noopener,noreferrer');
      else notice('No active public link. Open Share Guide to create one.', true);
    });
    document.addEventListener('click', event => {if(!event.target.closest('#gd-action-wrap')) closeActions()});
    document.addEventListener('keydown', event => {
      if ($('#gd-action-menu').hidden) return;
      if(event.key === 'Escape'){event.preventDefault();closeActions(true);return;}
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        const items = Array.from($('#gd-action-menu').querySelectorAll('[role=menuitem]:not([hidden])'));
        if (!items.length) return;
        const index = items.indexOf(document.activeElement);
        const next = event.key === 'ArrowDown' ? (index+1)%items.length : (index-1+items.length)%items.length;
        items[next].focus();event.preventDefault();
      }
    });
    $('#gd-share-close').addEventListener('click',()=>{$('#gd-share-panel').hidden=true;});
    $('#gd-share-open').addEventListener('click',()=>{
      const url=$('#gd-share-url').value;
      if(url)window.open(url,'_blank','noopener,noreferrer');
    });
    $('#gd-share-enable').addEventListener('click',async()=>{
      try{await api(`/api/guides/${workingId}/share`,'POST');guideShareInfo=null;await showSharePanel();notice('Share link created');}
      catch(e){notice(e.message,true)}
    });
    $('#gd-share-disable').addEventListener('click',async()=>{
      closeActions();
      if(!confirm('Disable this public Guide link? Previously shared links will stop working.'))return;
      try{await api(`/api/guides/${workingId}/share`,'DELETE');guideShareInfo=null;await showSharePanel();notice('Share link disabled');}
      catch(e){notice(e.message,true)}
    });
    document.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',()=>copyField(b.dataset.copy)));
    $('#gd-document-files').addEventListener('change',async event=>{
      const input=event.target;input.disabled=true;
      try{await uploadDocumentation(input.files);notice('Documentation uploaded successfully');}
      catch(err){$('#gd-document-upload-status').textContent='Upload incomplete';notice(err.message,true);saved=await api(`/api/guides/${workingId}`);renderDocuments();}
      finally{input.value='';input.disabled=false;}
    });
    // Native drag-and-drop uses the same authenticated documentation endpoint.
    const docDrop=$('#gd-document-upload-zone');
    docDrop.addEventListener('dragover',e=>{e.preventDefault();docDrop.classList.add('is-dragover')});
    docDrop.addEventListener('dragleave',()=>docDrop.classList.remove('is-dragover'));
    docDrop.addEventListener('drop',async e=>{
      e.preventDefault();docDrop.classList.remove('is-dragover');
      try{await uploadDocumentation(e.dataTransfer.files);notice('Documentation uploaded successfully');}
      catch(err){notice(err.message,true);saved=await api(`/api/guides/${workingId}`);renderDocuments();}
    });

    $('#gd-add-step').addEventListener('click', () => addStep());
    document.querySelectorAll('input[name="guide_format"]').forEach(input =>
      input.addEventListener('change', () => {if (input.checked) setGuideFormat(input.value);}));
    $('#gm-preview-toggle').addEventListener('click', () => {
      const p = $('#gm-editor-preview');p.hidden = !p.hidden;
      $('#gm-preview-toggle').textContent = p.hidden ? 'Preview' : 'Hide preview';
      if (!p.hidden) window.PoloGuideMarkdown.render(p, $('#gm-markdown').value);
    });
    $('#gm-markdown').addEventListener('input', () => {
      if (!$('#gm-editor-preview').hidden) window.PoloGuideMarkdown.render($('#gm-editor-preview'), $('#gm-markdown').value);
    });
    $('#gm-markdown').addEventListener('paste', event => {
      const image = Array.from(event.clipboardData?.items || []).find(item => item.type.startsWith('image/'));
      if (!image) return; // Ordinary Markdown remains a normal paste.
      event.preventDefault();
      try {
        const original = image.getAsFile();
        if (!original) throw Error('Clipboard does not contain a readable image');
        const suffix = image.type === 'image/png' ? '.png' : image.type === 'image/jpeg' ? '.jpg' : '.webp';
        const file = new File([original], `screenshot-${Date.now()}${suffix}`, {type:image.type});
        checkFiles([file], MAX_GALLERY - galleryMedia.length);
        galleryMedia.push(makePending(file));
        if (!coverToken) coverToken = galleryMedia[0].token;
        renderGalleryEditor();
        notice('Screenshot added to Topology gallery. Save Guide to upload it.');
      } catch (error) {notice(error.message, true);}
    });
    $('#gm-insert-code').addEventListener('click', () => insertAtCursor('\n```routeros\n\n```\n'));
    $('#gm-insert-table').addEventListener('click', () => insertAtCursor('\n| Kolom | Nilai |\n| --- | --- |\n| Contoh | Keterangan |\n'));
    $('#gd-delete').addEventListener('click', async () => {
      closeActions();
      if (!saved || !saved.can_edit || !confirm('Delete this guide, all steps and all images from QNAP? This cannot be undone.')) return;
      try { await api('/api/guides/' + guideId, 'DELETE'); location.assign('/workspace?tab=guides'); }
      catch (error) { notice(error.message, true); }
    });
    $('#gd-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const save = $('#gd-save');
      const format = document.querySelector('input[name="guide_format"]:checked').value;
      const steps = format === 'workflow' ? collectSteps() : [];
      const markdown = $('#gm-markdown').value;
      if (format === 'workflow' && !steps.every(s => s.title.trim())) {notice('Every step needs a title', true); return;}
      if (format === 'documentation' && !markdown.trim()) {notice('Documentation content is required', true); return;}
      const payload = {title: form.elements.title.value, category: form.elements.category.value,
        description: form.elements.description.value, status: form.elements.status.value,
        steps, content_format: format, content_markdown: markdown};
      save.disabled = true; save.textContent = 'Saving...';
      try {
        const result = await api(workingId ? '/api/guides/' + workingId : '/api/guides', workingId ? 'PUT' : 'POST', payload);
        workingId=result.id;
        save.textContent='Uploading images...';
        await saveImages(workingId);
        if (isNew) {location.assign('/guide/' + workingId);return;}
        saved = await api('/api/guides/' + workingId);
        activeGallery = 0;
        leaveEditor();
        notice('Guide saved successfully');
      } catch (error) {notice(error.message + (workingId ? ' Guide text may already be saved; retry to finish images.' : ''), true);}
      finally {save.disabled = false; save.textContent = 'Save Guide';}
    });
    if (isNew) { showEditor(true); return; }
    if (!guideId) { notice('Guide not found', true); return; }
    saved = await api('/api/guides/' + guideId);
    drawDetail();
    $('#gd-view').hidden = false;
  }

  try {
    await initializeSession();
    if (isIndex) await initIndex();
    else await initDetail();
  } catch (error) {notice(error.message, true);}
})();
