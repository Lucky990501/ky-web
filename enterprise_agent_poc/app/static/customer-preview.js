/* Only loaded by the separate Preview launcher; never the Production HTML. */
(() => {
  const base = '/preview';
  window.WORKBENCH_PREVIEW_BASE = base;
  document.documentElement.dataset.customerPreview = 'true';
  const url = value => {
    if (value == null || /^(?:data:|blob:|#)/i.test(String(value))) return value;
    let parsed;
    try { parsed = new URL(String(value), location.href); } catch { return ''; }
    if (parsed.origin !== location.origin || parsed.pathname === base || parsed.pathname.startsWith(base + '/')) return value;
    parsed.pathname = base + parsed.pathname;
    return parsed.href;
  };
  window.workbenchPreviewUrl = url;
  const fetchOriginal = window.fetch.bind(window);
  window.fetch = (input, options) => fetchOriginal(input instanceof Request ? new Request(url(input.url), input) : url(input), options);
  const Source = window.EventSource;
  window.EventSource = class extends Source { constructor(value, options) { super(url(value), options); } };
  for (const name of ['pushState', 'replaceState']) {
    const original = history[name].bind(history);
    history[name] = (state, title, value) => original(state, title, value == null ? value : url(value));
  }
  const openOriginal = window.open.bind(window);
  window.open = (value, ...args) => openOriginal(url(value), ...args);
  document.addEventListener('click', event => {
    const anchor = event.target.closest?.('a[href]');
    if (anchor) anchor.href = url(anchor.href);
  }, true);
  window.enhancePreviewKnowledge = (main, items) => {
    const zone = main.querySelector('.upload-zone');
    if (!zone) return;
    zone.innerHTML = '<div><h3>上传知识文件</h3><p>支持 PDF、DOCX、TXT、MD，最大2MB。使用现有解析与本地索引；不使用文本直接ready接口。</p><input type="file" id="preview-knowledge-file" accept=".pdf,.docx,.txt,.md" aria-label="选择知识文件"><p role="status" id="preview-upload-status"></p></div>';
    const panel = document.createElement('section');panel.className='panel';
    panel.innerHTML='<h3>测试知识检索</h3><label>问题<input id="preview-query" placeholder="输入资料中的具体问题"></label><button type="button" class="button secondary" id="preview-search">检索</button><div id="preview-search-result" role="status"></div>';
    main.querySelector('.right-rail').prepend(panel);
    const owner=principalCacheScope(),current=()=>main.isConnected&&main.dataset.page==='knowledge'&&principalCacheScope()===owner;
    main.querySelector('#preview-knowledge-file').onchange=async event=>{
      const input=event.target,file=input.files?.[0],feedback=main.querySelector('#preview-upload-status');if(!file)return;
      if(file.size>2*1024*1024){feedback.textContent='文件超过2MB，请选择较小的测试资料。';return;}
      input.disabled=true;
      try{
        const form=new FormData();form.append('file',file);const accepted=await apiForm('/api/v1/knowledge/files',form);
        for(let i=0;i<30&&current();i++){
          const item=await api('/api/v1/knowledge/files/'+encodeURIComponent(accepted.file_id),{cache:'no-store'});
          if(!current())return;feedback.textContent='文件状态：'+statusText(item.status);
          if(item.status==='ready'){await knowledge(main);return;}
          if(item.status==='failed')throw Error('资料处理失败，请检查文件后重新上传。');
          await new Promise(resolve=>setTimeout(resolve,1000));
        }
        if(current())feedback.textContent='资料仍在处理中；稍后重新打开知识库查看真实状态。';
      }catch(error){if(current())feedback.textContent=error.message;}
      finally{if(current())input.disabled=false;}
    };
    main.querySelector('#preview-search').onclick=async event=>{
      const query=main.querySelector('#preview-query').value.trim(),result=main.querySelector('#preview-search-result');if(!query)return;
      event.currentTarget.disabled=true;
      try{const data=await api('/api/v1/knowledge/retrieval-test',{method:'POST',body:JSON.stringify({query,top_k:5})});if(current())result.innerHTML=(data.results||[]).map(item=>'<p>'+escapeHtml(item.content||item.text||'')+'</p>').join('')||'未找到匹配资料，请确认文件已ready。';}
      catch(error){if(current())result.textContent=error.message;}
      finally{if(current())main.querySelector('#preview-search').disabled=false;}
    };
    main.querySelectorAll('.trow .status').forEach((node,i)=>{node.className='status '+(items[i]?.status==='ready'?'ready':'');});
  };
  document.addEventListener('DOMContentLoaded',()=>{
    const observer=new MutationObserver(()=>{
      const main=document.querySelector('#main');
      if(main?.dataset.page==='knowledge'&&main.querySelector('.upload-zone')&&!main.querySelector('#preview-knowledge-file'))window.enhancePreviewKnowledge(main,[]);
    });
    observer.observe(document.querySelector('#app'),{childList:true,subtree:true});
  });
})();
