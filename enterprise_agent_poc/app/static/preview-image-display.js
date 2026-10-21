/* Private Preview display cache. Memory only, bounded, cleared on identity change. */
(() => {
  'use strict';
  if (!window.WORKBENCH_PREVIEW_BASE) return;
  const cache=new Map(),pending=new Map(),controllers=new Set(),listeners=new Set();
  const LIMIT=64*1024*1024,MAX_ENTRIES=24,TTL=5*60*1000;
  let identity='',epoch=0,bytes=0,checking=null,hits=0,misses=0,lastClear='';
  function limiter(limit){let active=0;const waiters=[];return async work=>{while(active>=limit)await new Promise(resolve=>waiters.push(resolve));active++;try{return await work();}finally{active--;waiters.shift()?.();}};}
  // Display bytes only, NOT Provider concurrency: bound transfers separately
  // from large bitmap allocations so decode does not serialize all downloads.
  const displaySlot=limiter(8),decodeSlot=limiter(2);
  const safe=url=>{
    try{const u=new URL(url,location.origin);return u.origin===location.origin&&!u.search&&!u.hash&&!u.username&&!u.password&&
      /^\/preview\/api\/v1\/(?:preview\/illustrations\/assets\/[a-f0-9]{32}|preview\/image-display\/(?:illustrations\/[a-f0-9]{32}|generations\/(?:[a-f0-9-]{36}|task:[a-f0-9-]{36}:generation(?::[0-9]{1,3})?))|storage\/(?:generated|assets)\/[A-Za-z0-9_./-]+|chat-images\/[A-Za-z0-9_-]+)$/.test(u.pathname)&&!url.includes('..')&&!url.includes('%')&&!url.includes('\\')?u.href:'';}catch{return '';}
  };
  function clear(reason='explicit'){lastClear=typeof reason==='string'?reason:reason.type;epoch++;cache.clear();pending.clear();bytes=0;for(const c of controllers)c.abort();controllers.clear();for(const fn of listeners)fn();}
  function setIdentity(me){const next=me?.tenant_id&&me?.user_id?JSON.stringify([me.tenant_id,me.user_id]):'';if(next!==identity){clear('identity');identity=next;}}
  async function verifyIdentity(){
    if(!checking)checking=fetch('/preview/api/v1/me',{credentials:'same-origin',cache:'no-store',redirect:'error'})
      .then(async r=>{if(!r.ok){setIdentity(null);throw Error('IMAGE_SESSION_INVALID');}setIdentity(await r.json());return identity;})
      .finally(()=>{checking=null;});
    return checking;
  }
  async function raster(source){
    const url=safe(source);if(!url||!identity)throw Error('IMAGE_SOURCE_INVALID');
    const key=identity+':'+url,now=Date.now(),hit=cache.get(key);
    if(hit&&now-hit.time<TTL){hits++;cache.delete(key);cache.set(key,hit);return hit;}
    if(hit){bytes-=hit.bytes;cache.delete(key);}
    if(pending.has(key))return pending.get(key);
    misses++;
    const version=epoch,controller=new AbortController();controllers.add(controller);
    const timer=setTimeout(()=>controller.abort(),30000);
    const work=displaySlot(async()=>{
      if(version!==epoch)throw Error('IMAGE_SESSION_CHANGED');
      const response=await fetch(url,{credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal});
      const type=(response.headers.get('content-type')||'').split(';')[0].trim();
      if(!response.ok||!['image/png','image/jpeg','image/webp'].includes(type)||Number(response.headers.get('content-length'))>10*1024*1024){await response.body?.cancel();throw Error('IMAGE_INVALID');}
      const reader=response.body.getReader(),parts=[];let size=0;
      while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>10*1024*1024){await reader.cancel();throw Error('IMAGE_TOO_LARGE');}parts.push(value);}
      return decodeSlot(async()=>{
      if(version!==epoch)throw Error('IMAGE_SESSION_CHANGED');
      const bitmap=await createImageBitmap(new Blob(parts,{type}));
      let data,width=bitmap.width,height=bitmap.height;
      try{
        if(!width||!height||width>4096||height>4096||width*height>16777216)throw Error('IMAGE_TOO_LARGE');
        const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
        canvas.getContext('2d').drawImage(bitmap,0,0);data=canvas.toDataURL('image/png');canvas.width=canvas.height=0;
      }finally{bitmap.close();}
      if(version!==epoch||data.length>14*1024*1024)throw Error('IMAGE_SESSION_CHANGED');
      const result={data,width,height,time:Date.now(),bytes:data.length*2};
      while(cache.size&&(cache.size>=MAX_ENTRIES||bytes+result.bytes>LIMIT)){const first=cache.keys().next().value;bytes-=cache.get(first).bytes;cache.delete(first);}
      if(result.bytes<=LIMIT){cache.set(key,result);bytes+=result.bytes;}
      return result;
      });
    }).finally(()=>{clearTimeout(timer);controllers.delete(controller);if(version===epoch)pending.delete(key);});
    pending.set(key,work);return work;
  }
  const observed=new WeakSet(),bound=new Set();
  async function load(node){
    const source=node.dataset.previewImageSource;let version=epoch;
    try{if(!identity)await verifyIdentity();version=epoch;const owner=epoch,result=await raster(source);if(node.isConnected&&owner===epoch&&node.dataset.previewImageSource===source){if(node.src!==result.data)node.src=result.data;node.style.aspectRatio=result.width+'/'+result.height;}}
    catch{if(node.isConnected&&version===epoch){
      // The existing full-image viewer can display originals larger than the
      // memory-cache decoder limit. Fall back ONLY to its unchanged, private,
      // authenticated source, never to an external/arbitrary model URL.
      if(node.dataset.previewOriginalFallback==='true'&&safe(source))node.src=source;
      else node.dispatchEvent(new Event('error'));
    }}
  }
  const lazy=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){lazy.unobserve(entry.target);void load(entry.target);}},{rootMargin:'400px'});
  function bind(node,source){
    if(!safe(source))return;
    if(node.dataset.previewImageSource===source&&observed.has(node))return;
    node.dataset.previewImageSource=source;observed.add(node);bound.add(node);node.decoding='async';
    if(node.closest('#generation-viewer'))node.dataset.previewOriginalFallback='true';
    if(node.loading==='lazy')lazy.observe(node);else void load(node);
  }
  function enhance(){for(const node of document.querySelectorAll('img[data-preview-image-source]'))if(!observed.has(node))bind(node,node.dataset.previewImageSource);for(const node of bound)if(!node.isConnected){lazy.unobserve(node);bound.delete(node);}}
  listeners.add(()=>{for(const node of bound)node.removeAttribute('src');});
  new MutationObserver(enhance).observe(document.documentElement,{childList:true,subtree:true});
  addEventListener('pagehide',clear);
  addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
  window.WorkbenchPreviewImages=Object.freeze({raster,verifyIdentity,setIdentity,clear,bind,stats:()=>({entries:cache.size,bytes,epoch,pending:pending.size,hits,misses,lastClear}),onClear(fn){listeners.add(fn);return()=>listeners.delete(fn);}});
})();
