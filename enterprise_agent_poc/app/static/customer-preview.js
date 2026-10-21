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
  let imageCapability=null,imageConsent=null,editGeneration=null,confirming=false,confirmedSubmit=false;
  window.workbenchPreviewReferenceLimit=()=>imageCapability?.max_reference_images||1;
  window.fetch = (input, options) => {
    let target=input instanceof Request?new Request(url(input.url),input):url(input);
    const requested=new URL(input instanceof Request?input.url:String(target),location.href);
    const previewLogout=requested.origin===location.origin&&requested.pathname==='/preview/api/v1/auth/logout';
    if(previewLogout)target='/preview/api/v1/preview-auth/logout';
    if(options?.method==='POST'&&new URL(input instanceof Request?input.url:String(target),location.href).pathname==='/preview/api/v1/agents/image-agent/runs'&&imageConsent){
      const body=JSON.parse(options.body||'{}');
      if(body.message===imageConsent.prompt){
        const items=document.querySelector('#main')?._chatImage?.items||[];
        if(body.attachments)body.attachments=body.attachments.map(item=>({...item,role:items.find(x=>x.record?.id===item.id)?.role||'subject'}));
        if(editGeneration)body.edit_generation_id=editGeneration.id;
        options={...options,body:JSON.stringify({...body,preview_image_confirmed:true,preview_image_request_id:imageConsent.id,use_enterprise_context:imageConsent.useContext,
          ...(imageConsent.size?{size:imageConsent.size}:{}),...(imageConsent.quality?{quality:imageConsent.quality}:{})})};
      }
      imageConsent=null;
    }
    const pending=fetchOriginal(target,options);
    pending.then(response=>{
      if(previewLogout&&response.ok){location.replace('/preview/login');return;}
      if(response.status===401)response.clone().json().then(value=>{if(value.error_code==='PREVIEW_LOGIN_REQUIRED')location.replace('/preview/login');}).catch(()=>{});
    }).catch(()=>{});
    if(options?.method==='POST'&&String(target).includes('/agents/image-agent/runs'))pending.then(response=>{if(response.ok)editGeneration=null;}).catch(()=>{});
    if((!options?.method||options.method==='GET')&&/\/api\/v1\/tasks\/[^/?]+(?:\?.*)?$/.test(String(target))){
      pending.then(response=>response.ok?response.clone().json():null).then(task=>{if(task?.agent_id==='image-agent'&&['completed','failed','cancelled'].includes(task.status))refreshPreviewImageCapability();}).catch(()=>{});
    }
    return pending;
  };
  const Source = window.EventSource;
  window.EventSource = class extends Source { constructor(value, options) { super(url(value), options);for(const name of ['complete','cancelled','failed'])this.addEventListener(name,refreshPreviewImageCapability);this.addEventListener('error',event=>{if(event.data)refreshPreviewImageCapability();}); } };
  function refreshPreviewImageCapability(){
    fetchOriginal('/preview/api/health',{cache:'no-store'}).then(r=>r.json()).then(value=>{imageCapability=value.image_business;markImageUnavailable();}).catch(()=>{});
  }
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
  const enhanceCopywritingPrompt=()=>{
    const main=document.querySelector('#main');
    if(main?.dataset.agentId!=='copywriting-agent')return;
    const input=main.querySelector('#prompt');
    if(input)input.placeholder='告诉我你想创作什么，或直接说明如何修改、转换当前内容。';
  };
  const markImageUnavailable = () => {
    const enabled=imageCapability?.enabled===true;
    document.querySelectorAll('.side-nav [data-page="image"] span').forEach(label => {
      const text=enabled?'图片生成':'图片生成（暂不可用）';if(label.textContent!==text)label.textContent=text;
    });
    const main=document.querySelector('#main');if(main?.dataset.agentId!=='image-agent'){editGeneration=null;return;}
    if(editGeneration&&editGeneration.context!==main._conversationContext)editGeneration=null;
    const form=main.querySelector('#composer');if(!form)return;
    if(!form.querySelector('[data-preview-image-options]')){
      const fields=document.createElement('fieldset');fields.dataset.previewImageOptions='true';fields.className='preview-image-options';
      const legend=document.createElement('legend');legend.textContent='图片输出设置';fields.append(legend);
      const label=document.createElement('label');label.textContent='图片比例';
      const select=document.createElement('select');select.id='preview-image-size';
      for(const [value,text] of [['2048x2048','1:1'],['3840x2160','16:9（横版）'],['2160x3840','9:16（竖版）']]){
        const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);
      }
      label.append(select);fields.append(label);
      form.prepend(fields);
    }
    let notice=main.querySelector('[data-preview-image-notice]');
    if(!notice){notice=document.createElement('p');notice.dataset.previewImageNotice='true';notice.className='composer-run-feedback';notice.setAttribute('role','status');form.before(notice);}
    notice.id=enabled?'preview-image-direct-notice':'preview-image-unavailable';
    notice.hidden=enabled;
    const text=enabled?'':imageUnavailable;
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
    main.querySelectorAll('.message-generation[data-result-image]').forEach(card=>{
      if(card.querySelector('[data-preview-edit-image]')){
        const button=card.querySelector('[data-preview-edit-image]'),text=editGeneration?.id===card.dataset.resultImage?'取消此编辑基图':'基于此图继续修改';
        if(button.textContent!==text)button.textContent=text;return;
      }
      const button=document.createElement('button');button.type='button';button.className='button secondary';button.dataset.previewEditImage=card.dataset.resultImage;button.textContent='基于此图继续修改';
      button.onclick=()=>{editGeneration=editGeneration?.id===card.dataset.resultImage?null:{id:card.dataset.resultImage,context:main._conversationContext};main.querySelector('#prompt')?.focus();markImageUnavailable();};
      card.querySelector('.message-generation-actions')?.append(button);
    });
    const slot=main.querySelector('.reference-limit'),limitText=imageCapability?.multi_reference_status==='BLOCKED_CURRENT_WIRE_HTTP_400'?'多参考图尚未通过真实接口验收（HTTP400），暂限提交1张；2～5张仍可整理，但不会发起收费请求。':`最多${imageCapability?.max_reference_images||1}张参考图，按当前顺序与用途提交；用途通过提示词说明，不能保证局部完全不变。`;
    if(slot&&slot.textContent!==limitText)slot.textContent=limitText;
    main.querySelectorAll('[data-reference-role]').forEach(select=>{
      if(select.dataset.previewRoles)return;select.dataset.previewRoles='true';
      for(const [value,label] of [['style','风格参考'],['background','背景参考'],['composition','构图参考'],['brand','品牌素材']]){const option=document.createElement('option');option.value=value;option.textContent=label;select.append(option);}
      if(select.parentNode.firstChild?.nodeType===Node.TEXT_NODE)select.parentNode.firstChild.textContent='参考用途';
    });
  };
  document.addEventListener('submit',async event=>{
    if(event.target.id==='composer'&&document.querySelector('#main')?.dataset.agentId==='image-agent'){
      if(confirmedSubmit){confirmedSubmit=false;return;}
      event.preventDefault();event.stopImmediatePropagation();if(confirming)return;
      const prompt=document.querySelector('#prompt')?.value.trim();
      if(!imageCapability?.enabled||!prompt){event.preventDefault();event.stopImmediatePropagation();markImageUnavailable();return;}
      const main=document.querySelector('#main'),attachment=main._chatImage,context=main._conversationContext;
      const referenceKey=()=>JSON.stringify((main._chatImage?.items||[]).map(x=>[x.record?.id,x.role]));
      const references=referenceKey(),parent=editGeneration?.id;
      if(attachment&&!['idle','ready'].includes(attachment.status))return;
      if(editGeneration&&attachment?.items.length){alert('请选择已有基图或上传参考图，不要同时提交。');return;}
      confirming=true;
      try{
        const useContext=true;
        const size=main.querySelector('#preview-image-size')?.value||'2048x2048',quality='high';
        const response=await fetchOriginal('/preview/api/v1/agents/image-agent/plan',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message:prompt,use_enterprise_context:useContext,...(size?{size}:{}),...(quality?{quality}:{})})}),plan=await response.json();
        if(!response.ok)throw Error(plan.user_message||'图片计划校验失败，未提交生成。');
        if(!main.isConnected||main._conversationContext!==context||document.querySelector('#prompt')?.value.trim()!==prompt||size!==main.querySelector('#preview-image-size')?.value||references!==referenceKey()||parent!==editGeneration?.id)return;
        imageConsent={prompt,id:crypto.randomUUID(),useContext,size,quality};confirmedSubmit=true;event.target.requestSubmit();
      }catch(error){
        let feedback=main.querySelector('#preview-image-error');
        if(!feedback){feedback=document.createElement('p');feedback.id='preview-image-error';feedback.className='image-delivery-warning';feedback.setAttribute('role','alert');feedback.tabIndex=-1;event.target.before(feedback);}
        feedback.textContent=error.message;feedback.focus();
      }finally{confirming=false;}
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
      enhanceCopywritingPrompt();
    });
    observer.observe(document.querySelector('#app'),{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
    markImageUnavailable();
    enhanceCopywritingPrompt();
  });
})();
