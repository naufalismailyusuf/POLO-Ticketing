/* Revisi 26 — isolated, read-only viewer; no login/session/admin API. */
(async () => {
  'use strict';
  const $ = id => document.getElementById(id);
  const el = (tag, cls='', text) => {
    const result=document.createElement(tag);
    if(cls)result.className=cls;
    if(text!==undefined)result.textContent=text;
    return result;
  };
  const path=location.pathname.match(/^\/g\/([A-Za-z0-9_-]{43})\/?$/);
  if(!path){$('pg-error').hidden=false;$('pg-error').textContent='Invalid Guide link.';return;}
  const token=path[1], root=`/api/public/g/${encodeURIComponent(token)}`;
  const imageUrl=id=>`${root}/media/${id}`;
  const showError=message=>{$('pg-content').hidden=true;$('pg-error').hidden=false;$('pg-error').textContent=message;};
  const humanSize=bytes=>bytes<1024?`${bytes} B`:bytes<1048576?`${(bytes/1024).toFixed(1)} KB`:`${(bytes/1048576).toFixed(1)} MB`;
  const gallery=[];
  let selected=0;
  function preview(image){
    $('pg-preview-image').src=imageUrl(image.id);
    $('pg-preview-image').alt=image.caption||image.original_name||'Guide image';
    $('pg-preview').hidden=false;
    $('pg-preview-close').focus();
  }
  function drawGallery(){
    const box=$('pg-gallery-section');box.hidden=!gallery.length;
    if(!gallery.length)return;
    selected=Math.min(selected,gallery.length-1);
    const current=gallery[selected];
    $('pg-gallery-image').src=imageUrl(current.id);
    $('pg-gallery-image').alt=current.caption||current.original_name||'Guide image';
    $('pg-gallery-caption').textContent=current.caption||current.original_name||'';
    $('pg-gallery-count').textContent=`${gallery.length} image${gallery.length===1?'':'s'}`;
    const thumbs=$('pg-gallery-thumbs');thumbs.replaceChildren();
    gallery.forEach((img,index)=>{
      const button=el('button','gd-thumb'+(index===selected?' gd-thumb-active':''));button.type='button';
      button.setAttribute('aria-label',`Show image ${index+1}`);
      const picture=el('img');picture.src=imageUrl(img.id);picture.alt=img.caption||img.original_name||'Gallery preview';picture.loading='lazy';
      const footer=el('span','gd-thumb-footer',img.caption||img.original_name||`Image ${index+1}`);
      button.append(picture,footer);button.addEventListener('click',()=>{selected=index;drawGallery();});thumbs.append(button);
    });
  }
  function render(data){
    const guide=data.guide;
    document.title=`${guide.title} | User Guide`;
    $('pg-category').textContent=`${guide.category} / USER GUIDE`;
    $('pg-title').textContent=guide.title;
    $('pg-description').textContent=guide.description||'';
    $('pg-updated').textContent=`Updated ${new Date(guide.updated_at).toLocaleDateString('en-GB',{year:'numeric',month:'short',day:'numeric'})}`;
    gallery.push(...data.images.filter(i=>i.kind==='gallery').sort((a,b)=>Number(b.is_primary)-Number(a.is_primary)||a.position-b.position));
    drawGallery();
    window.GuideVideos?.renderPublic(data, token);
    $('pg-gallery-main').addEventListener('click',()=>preview(gallery[selected]));
    const isDocumentation = guide.content_format === 'documentation';
    $('pg-doc-section').hidden = !isDocumentation;
    $('pg-flow-section').hidden = isDocumentation;
    $('pg-steps-section').hidden = isDocumentation;
    if (isDocumentation) window.PoloGuideMarkdown.render($('pg-doc-body'), guide.content_markdown || '');
    const flow=$('pg-flow'),steps=$('pg-steps');
    data.steps.forEach((step,i)=>{
      if(i){
        const connector=el('span','gd-flow-connector');connector.setAttribute('aria-hidden','true');
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 46 18');
        const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d','M1 9 H43 M37 3 L43 9 L37 15');
        path.setAttribute('fill','none');path.setAttribute('stroke','currentColor');path.setAttribute('stroke-width','1.6');
        svg.append(path);connector.append(svg);flow.append(connector);
      }
      const control=el('button','gd-flow-node');control.type='button';
      control.append(el('span','gd-flow-number',String(i+1).padStart(2,'0')),el('span','gd-flow-title',step.title));
      control.addEventListener('click',()=>document.getElementById(`pg-step-${i+1}`).scrollIntoView({behavior:'smooth',block:'center'}));
      flow.append(control);
      const article=el('article','gd-detail-step');article.id=`pg-step-${i+1}`;
      const content=el('div','gd-detail-step-content');content.append(el('h3','',step.title));
      if(step.instruction)content.append(el('p','gd-preline',step.instruction));
      if(step.command){
        const box=el('div','gd-command');const pre=el('pre','',step.command);const copy=el('button','gd-copy','Copy');copy.type='button';
        copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(step.command);copy.textContent='Copied';}catch{copy.textContent='Select text to copy';}});
        box.append(pre,copy);content.append(box);
      }
      if(step.reference_url){
        const link=el('a','gd-reference','Open reference ↗');link.href=step.reference_url;
        link.target='_blank';link.rel='noopener noreferrer';content.append(link);
      }
      const images=data.images.filter(img=>img.kind==='step'&&img.step_position===i+1).sort((a,b)=>a.position-b.position);
      if(images.length){
        const thumbnails=el('div','gd-step-images');
        images.forEach(img=>{
          const button=el('button','gd-step-image');button.type='button';
          const picture=el('img');picture.src=imageUrl(img.id);picture.alt=img.caption||img.original_name||'Screenshot';picture.loading='lazy';
          button.append(picture,el('span','gd-image-caption',img.caption||img.original_name||''));
          button.addEventListener('click',()=>preview(img));thumbnails.append(button);
        });content.append(thumbnails);
      }
      article.append(el('span','gd-detail-step-number',String(i+1).padStart(2,'0')),content);steps.append(article);
    });
    const files=data.files||[];
    $('pg-files-section').hidden=!files.length;
    files.forEach(file=>{
      const row=el('div','gd-document-item');const body=el('div','gd-document-name');
      const content=el('div');content.append(el('strong','',file.original_name),el('small','',humanSize(file.size_bytes)));
      body.append(el('span','gd-document-icon','▤'),content);
      const link=el('a','gd-button','Download');link.href=`${root}/files/${file.id}`;link.setAttribute('download',file.original_name);
      row.append(body,link);$('pg-files').append(row);
    });
    $('pg-content').hidden=false;
  }
  $('pg-preview-close').addEventListener('click',()=>{$('pg-preview').hidden=true;$('pg-preview-image').removeAttribute('src');});
  $('pg-preview').addEventListener('click',event=>{if(event.target===$('pg-preview'))$('pg-preview-close').click();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('pg-preview').hidden)$('pg-preview-close').click();});
  try{
    const response=await fetch(root,{credentials:'omit',cache:'no-store'});
    const result=await response.json().catch(()=>({error:'Unable to load this Guide'}));
    if(!response.ok||!result.ok)throw Error(result.error||'Guide link expired or disabled');
    render(result);
  }catch(error){showError(error.message||'Unable to open Guide');}
})();
