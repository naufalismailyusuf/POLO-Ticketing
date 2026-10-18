/* Consistent accessible file drop zones. Upload requests remain owned by each page's existing onchange handler. */
(()=>{'use strict';
  const selector='[data-edp-dropzone]';
  const inputFor=zone=>zone.querySelector('input[type="file"]') || (zone.dataset.edpDropzoneInput?document.getElementById(zone.dataset.edpDropzoneInput):null);
  const active=(zone,input)=>input && !input.disabled && zone.getAttribute('aria-disabled')!=='true' && !zone.classList.contains('is-disabled');
  const accepts=(file,accept)=>{
    if(!accept || accept.trim()==='*/*')return true;
    const filename=file.name.toLowerCase(),mime=(file.type||'').toLowerCase();
    return accept.split(',').some(raw=>{
      const token=raw.trim().toLowerCase();
      if(!token || token==='*/*')return true;
      if(token.startsWith('.'))return filename.endsWith(token);
      if(token.endsWith('/*'))return mime.startsWith(token.slice(0,-1));
      return mime===token;
    });
  };
  const message=(zone,value,invalid=false)=>{
    const status=zone.querySelector('[data-edp-drop-status]');
    if(status)status.textContent=value;
    zone.classList.toggle('is-invalid',invalid);
    if(invalid)zone.classList.remove('has-files');
  };
  const defaultStatus=new WeakMap();
  function update(zone,input){
    const el=zone.querySelector('[data-edp-drop-status]');
    if(!el)return;
    if(!defaultStatus.has(el))defaultStatus.set(el,el.textContent);
    if(!input.files?.length){if(!zone.classList.contains('is-invalid')){zone.classList.remove('has-files');el.textContent=defaultStatus.get(el)}return}
    const files=[...input.files];zone.classList.remove('is-invalid');zone.classList.add('has-files');
    el.textContent=files.length===1 ? files[0].name : files.length+' files selected';
  }
  const registered=new WeakSet();
  function attach(zone){
    if(registered.has(zone))return;registered.add(zone);
    const input=inputFor(zone);if(!input)return;
    const status=zone.querySelector('[data-edp-drop-status]');if(status)defaultStatus.set(status,status.textContent);
    const reset=()=>zone.classList.remove('is-dragover');
    zone.addEventListener('dragenter',event=>{if(!event.dataTransfer?.types?.includes('Files'))return;event.preventDefault();if(active(zone,input))zone.classList.add('is-dragover')});
    zone.addEventListener('dragover',event=>{if(!event.dataTransfer?.types?.includes('Files'))return;event.preventDefault();event.dataTransfer.dropEffect=active(zone,input)?'copy':'none';zone.classList.toggle('is-dragover',!!active(zone,input))});
    zone.addEventListener('dragleave',event=>{if(!zone.contains(event.relatedTarget))reset()});
    zone.addEventListener('drop',event=>{
      if(!event.dataTransfer?.files?.length){reset();return}
      event.preventDefault();event.stopPropagation();reset();
      if(!active(zone,input))return;
      const incoming=[...event.dataTransfer.files];
      if(incoming.some(file=>!accepts(file,input.accept))){message(zone,'File type not supported. Choose an accepted format.',true);return}
      if(!input.multiple && incoming.length>1){message(zone,'Select one file only.',true);return}
      try{
        const transfer=new DataTransfer();incoming.forEach(file=>transfer.items.add(file));
        input.files=transfer.files;
        input.dispatchEvent(new Event('change',{bubbles:true}));
        update(zone,input);
      }catch(error){message(zone,'Drag and drop unavailable. Click to browse files.',true)}
    });
    input.addEventListener('change',()=>update(zone,input));
    input.addEventListener('edp-dropzone-reset',()=>{zone.classList.remove('is-invalid','has-files');update(zone,input)});
    // Label-to-file-input activation is native and works with any existing click handlers.
    if(zone.tagName!=='LABEL'){
      zone.addEventListener('click',event=>{if(active(zone,input) && !event.target.closest('button,a,input'))input.click()});
      zone.setAttribute('role','button');if(!zone.hasAttribute('tabindex'))zone.tabIndex=0;
    }
    else {zone.setAttribute('role','button');zone.tabIndex=0}
    zone.addEventListener('keydown',event=>{if(event.key!=='Enter'&&event.key!==' ')return;if(!active(zone,input))return;event.preventDefault();input.click()});
  }
  const scan=root=>{
    if(root?.matches?.(selector))attach(root);
    root?.querySelectorAll?.(selector).forEach(attach);
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>scan(document),{once:true});else scan(document);
  const observer=new MutationObserver(records=>{
    for(const record of records)for(const node of record.addedNodes)if(node.nodeType===1)scan(node);
  });
  observer.observe(document.documentElement,{subtree:true,childList:true});
})();
