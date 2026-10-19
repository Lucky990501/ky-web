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
  // The existing server Markdown formatter can wrap an HTML-only reply as
  // escaped text. Recover only that whole-document shape, then pass it through
  // the unchanged reviewed sanitizer. Never insert the decoded HTML into DOM.
  window.previewArticleContent = content => {
    const parsed=document.createElement('template');parsed.innerHTML=content;
    const root=parsed.content.querySelector('section#article');
    if(!root||root.querySelector('h1,h2,h3,article,section,div,iframe,img'))return content;
    const text=root.textContent.trim().replace(/^```html\s*\n?/i,'').replace(/\n?```\s*$/,'').trim();
    return /^<(?:div|section|article)(?:\s|>)/i.test(text)&&/<\/(?:div|section|article)>\s*$/i.test(text)?text:content;
  };
  const fetchOriginal = window.fetch.bind(window);
  let imageCapability=null,imageConsent=null;
  window.fetch = (input, options) => {
    const target=input instanceof Request?new Request(url(input.url),input):url(input);
    if(options?.method==='POST'&&new URL(input instanceof Request?input.url:String(target),location.href).pathname==='/preview/api/v1/agents/image-agent/runs'&&imageConsent){
      const body=JSON.parse(options.body||'{}');
      if(body.message===imageConsent.prompt)options={...options,body:JSON.stringify({...body,preview_image_confirmed:true,preview_image_request_id:imageConsent.id})};
      imageConsent=null;
    }
    return fetchOriginal(target,options);
  };
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
  const imageUnavailable = '图片生成暂不可用：未取得独立业务授权或体验额度已用完。';
  const markImageUnavailable = () => {
    const enabled=imageCapability?.enabled===true;
    document.querySelectorAll('.side-nav [data-page="image"] span').forEach(label => {
      const text=enabled?'图片生成':'图片生成（暂不可用）';if(label.textContent!==text)label.textContent=text;
    });
    const main=document.querySelector('#main');if(main?.dataset.agentId!=='image-agent')return;
    const form=main.querySelector('#composer');if(!form)return;
    let notice=main.querySelector('[data-preview-image-notice]');
    if(!notice){notice=document.createElement('p');notice.dataset.previewImageNotice='true';notice.className='composer-run-feedback';notice.setAttribute('role','status');form.before(notice);}
    notice.id=enabled?'preview-image-direct-notice':'preview-image-unavailable';
    const text=enabled?`独立图片体验：每次1张，单次费用预留不超过¥${imageCapability.request_max_cny}；提交前需确认，不自动重试。暂不支持参考图编辑。`:imageUnavailable;
    if(notice.textContent!==text)notice.textContent=text;
    const send=form.querySelector('#composer-submit');
    if(send?.type==='button')return; // Never disable an active Stop control.
    form.querySelectorAll('button,input,textarea').forEach(control=>{
      if(!enabled&&!control.disabled){control.dataset.previewDisabled='true';control.disabled=true;}
      if(enabled&&control.dataset.previewDisabled){delete control.dataset.previewDisabled;control.disabled=false;}
      control.setAttribute('aria-describedby',notice.id);
    });
    if(send&&!enabled){send.title=imageUnavailable;send.setAttribute('aria-label','图片生成暂不可用');}
    if(send&&enabled&&send.title===imageUnavailable){send.title='发送';send.setAttribute('aria-label','发送消息');}
  };
  document.addEventListener('submit',event=>{
    if(event.target.id==='composer'&&document.querySelector('#main')?.dataset.agentId==='image-agent'){
      const prompt=document.querySelector('#prompt')?.value.trim();
      if(!imageCapability?.enabled||!prompt){event.preventDefault();event.stopImmediatePropagation();markImageUnavailable();return;}
      if(!window.confirm(`确认生成1张图片？单次费用预留不超过¥${imageCapability.request_max_cny}。请求失败或结果未知时不会自动重试。`)){
        event.preventDefault();event.stopImmediatePropagation();return;
      }
      imageConsent={prompt,id:crypto.randomUUID()};
    }
  },true);
  window.enhancePreviewKnowledge = (main, items) => {
    const zone = main.querySelector('.upload-zone');
    if (!zone) return;
    zone.innerHTML = '<div><h3>上传知识文件</h3><p>支持 PDF、DOCX、TXT、MD，最大2MB。使用现有解析与本地索引；不使用文本直接ready接口。</p><input type="file" id="preview-knowledge-file" accept=".pdf,.docx,.txt,.md" aria-label="选择知识文件"><p role="status" id="preview-upload-status"></p></div>';
    const panel = document.createElement('section');panel.className='panel';
    panel.innerHTML='<h3>测试知识检索</h3><label>问题<input id="preview-query" placeholder="输入资料中的具体问题"></label><button type="button" class="button secondary" id="preview-search">检索</button><div id="preview-search-result" role="status"></div>';
    main.querySelector('.right-rail').prepend(panel);
    const owner=principalCacheScope(),current=()=>main.isConnected&&main.dataset.page==='knowledge'&&principalCacheScope()===owner;
    const waitForFile=async(id,feedback)=>{
      for(let i=0;i<90&&current();i++){
        const item=await api('/api/v1/knowledge/files/'+encodeURIComponent(id),{cache:'no-store'});
        if(!current())return;feedback.textContent='文件状态：'+statusText(item.status);
        if(item.status==='ready'){await knowledge(main);return;}
        if(item.status==='failed')throw Error(item.user_message||'资料处理失败，原文件已保留，可重新处理。');
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
      if(current())feedback.textContent='资料仍在处理中；稍后重新打开知识库查看真实状态。';
    };
    api('/api/v1/knowledge/files',{cache:'no-store'}).then(files=>{
      if(!current())return;
      main.querySelectorAll('.trow').forEach(row=>{
        const id=row.querySelector('[data-delete-knowledge]')?.dataset.deleteKnowledge,item=files.find(x=>x.id===id);
        if(!item)return;
        const status=row.querySelector('.status');status.className='status '+(item.status==='ready'?'ready':'');status.textContent=statusText(item.status);
        if(item.status!=='failed')return;
        const feedback=document.createElement('p');feedback.className='form-error';feedback.setAttribute('role','alert');feedback.textContent=item.user_message||'资料处理失败，原文件已保留。';row.after(feedback);
        const retry=document.createElement('button');retry.type='button';retry.className='link-button';retry.textContent='重新处理';retry.dataset.previewKnowledgeRetry=id;
        const remove=row.querySelector('[data-delete-knowledge]'),actions=document.createElement('span');remove.replaceWith(actions);actions.append(retry,remove);
        retry.onclick=async()=>{
          retry.disabled=true;
          try{await api('/api/v1/knowledge/files/'+encodeURIComponent(id)+'/retry',{method:'POST'});await waitForFile(id,feedback);}
          catch(error){if(current())feedback.textContent=error.message;}
          finally{if(current())retry.disabled=false;}
        };
      });
    }).catch(error=>{if(current())main.querySelector('#preview-upload-status').textContent='文件状态查询失败：'+error.message;});
    main.querySelector('#preview-knowledge-file').onchange=async event=>{
      const input=event.target,file=input.files?.[0],feedback=main.querySelector('#preview-upload-status');if(!file)return;
      if(file.size>2*1024*1024){feedback.textContent='文件超过2MB，请选择较小的测试资料。';return;}
      input.disabled=true;
      try{
        const form=new FormData();form.append('file',file);const accepted=await apiForm('/api/v1/knowledge/files',form);
        await waitForFile(accepted.file_id,feedback);
      }catch(error){if(current())feedback.textContent=error.message;}
      finally{if(current())input.disabled=false;}
    };
    main.querySelector('#preview-search').onclick=async event=>{
      const query=main.querySelector('#preview-query').value.trim(),result=main.querySelector('#preview-search-result');if(!query)return;
      event.currentTarget.disabled=true;
      try{const data=await api('/api/v1/knowledge/retrieval-test',{method:'POST',body:JSON.stringify({query,top_k:5})});if(current())result.innerHTML=(data.results||[]).map(item=>'<article><b>'+escapeHtml(item.filename||item.title||'来源文件')+'</b><p>'+escapeHtml(item.content||item.text||'')+'</p></article>').join('')||'未找到匹配资料，请确认文件已ready。';}
      catch(error){if(current())result.textContent=error.message;}
      finally{if(current())main.querySelector('#preview-search').disabled=false;}
    };
    main.querySelectorAll('.trow .status').forEach((node,i)=>{node.className='status '+(items[i]?.status==='ready'?'ready':'');});
  };
  document.addEventListener('DOMContentLoaded',()=>{
    fetchOriginal('/preview/api/health',{cache:'no-store'}).then(r=>r.json()).then(value=>{imageCapability=value.image_business;markImageUnavailable();}).catch(()=>{imageCapability=null;markImageUnavailable();});
    const observer=new MutationObserver(()=>{
      const main=document.querySelector('#main');
      if(main?.dataset.page==='knowledge'&&main.querySelector('.upload-zone')&&!main.querySelector('#preview-knowledge-file'))window.enhancePreviewKnowledge(main,[]);
      markImageUnavailable();
    });
    observer.observe(document.querySelector('#app'),{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
    markImageUnavailable();
  });
})();
