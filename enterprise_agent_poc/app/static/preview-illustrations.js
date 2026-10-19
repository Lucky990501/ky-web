/* Preview-only illustration orchestration. GET/refresh NEVER starts generation. */
(() => {
  'use strict';
  if (!window.WORKBENCH_PREVIEW_BASE) return;
  const prefix='/api/v1/preview/illustrations', mounted=new WeakSet();
  async function api(path,body) {
    const response=await fetch(path,{credentials:'same-origin',cache:'no-store',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const value=await response.json();if(!response.ok)throw Error(value.user_message||'配图状态查询失败，请保留当前正文。');return value;
  }
  function mount(article,main,taskId) {
    const slot=document.createElement('section');slot.className='preview-illustrations';slot.setAttribute('aria-label','独立AI配图');
    const title=document.createElement('strong');title.textContent='AI配图 · qwen-image-3.0';
    const feedback=document.createElement('p');feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');
    const actions=document.createElement('div'),items=document.createElement('ul'),assets=document.createElement('div');assets.className='preview-illustration-gallery';
    const notice=document.createElement('p');notice.textContent='配图为AI示意，不是真实现场照片；正文与纯文字导出仍可使用。';
    slot.append(title,feedback,items,actions,assets,notice);article.append(slot);
    let plan=null,timer=null,polls=0,busy=false;
    const current=()=>slot.isConnected&&main.isConnected&&article.closest('#main')===main;
    const stop=()=>{clearTimeout(timer);observer.disconnect();};
    const observer=new MutationObserver(()=>{if(!current())stop();});observer.observe(document.documentElement,{childList:true,subtree:true});
    function button(label,handler,disabled=false) {
      const node=document.createElement('button');node.type='button';node.className='button secondary';node.textContent=label;node.disabled=disabled;
      node.onclick=async()=>{if(busy||!current())return;busy=true;node.disabled=true;
        try{await handler();}catch(error){if(current())feedback.textContent=error.message;}
        finally{busy=false;if(current())render();}};actions.append(node);return node;
    }
    function link(label,url,filename) {
      const node=document.createElement('a');node.className='button secondary';node.textContent=label;
      node.href=window.workbenchPreviewUrl(url);node.download=filename;return node;
    }
    function render() {
      if(!current())return;
      actions.replaceChildren();items.replaceChildren();assets.replaceChildren();
      if(!plan){button('查看自动配图计划',async()=>{plan=await api(prefix+'/plans',{task_id:taskId});render();});return;}
      for(const item of plan.items){const li=document.createElement('li');li.textContent=item.label+'：'+item.requirement;items.append(li);}
      for(const asset of plan.assets){
        const figure=document.createElement('figure'),image=document.createElement('img'),caption=document.createElement('figcaption');
        image.src=window.workbenchPreviewUrl(asset.url);image.alt=asset.label;image.loading='lazy';image.width=asset.width;image.height=asset.height;
        image.onerror=()=>{caption.textContent=asset.label+' · 图片暂不可用；未触发重新生图。';};
        caption.textContent=asset.label+' · '+asset.width+'×'+asset.height+' · AI示意图';
        figure.append(image,caption,link('下载配图',asset.url,asset.filename));assets.append(figure);
      }
      if(['queued','generating'].includes(plan.status)) {
        feedback.textContent=`正在生成并保存配图 ${plan.assets.length}/${plan.count}；不要重复提交。`;slot.setAttribute('aria-busy','true');
        if(!timer&&polls<180)timer=setTimeout(async()=>{timer=null;polls++;try{plan=await api(prefix+'/plans/'+plan.id);render();}catch(error){if(current())feedback.textContent=error.message;}},2000);
        else if(polls>=180)feedback.textContent='后台仍在处理；可稍后从历史记录查看，不会重新创建生图任务。';
        return;
      }
      slot.setAttribute('aria-busy','false');
      if(plan.status==='completed') {
        feedback.textContent=`${plan.assets.length}张真实已保存资产已绑定。${plan.kind==='activity'?'图文Word已生成。':'图片私有，公众号后台需手动上传；可下载HTML与图片包。'}`;
        if(plan.document)actions.append(link('下载图文 Word',plan.document.download_url,plan.document.filename));
        if(plan.html){
          actions.append(link('下载图文 HTML',plan.html_url,'article.html'),link('下载文章与配图 ZIP',plan.package_url,'article-images.zip'));
          if(article.dataset.illustrationPlan!==plan.id&&window.WorkbenchWechatArticle&&typeof window.bindModalDialog==='function'){
            article.dataset.illustrationPlan=plan.id;article.querySelector('.wechat-article-actions')?.remove();
            window.WorkbenchWechatArticle.mountManual({article,main,agent:{slug:'wechat-official-account-writing'},
              content:plan.html,messageId:article.dataset.assistantMessageId,markdown:s=>s,modal:window.bindModalDialog,
              isCurrent:current,inlineBody:true});
          }
        }
      } else if(plan.status==='failed') {
        feedback.textContent=`配图未完整完成（${plan.error_code||'结果未确认'}），已保存${plan.assets.length}/${plan.count}张。没有自动重试或把部分结果称为完整成功。`;
        if(plan.assets.length===plan.count&&plan.count)button('仅重试文档整理（不重新生图）',async()=>{plan=await api(prefix+'/plans/'+plan.id+'/export',{});});
      } else {
        feedback.textContent=plan.capability.enabled?`共${plan.count}张，最高费用预留¥${plan.maximum_cost_cny}；预留不是供应商实际账单。`:plan.user_message;
        button(plan.kind==='activity'?'确认配图并导出图文 Word':'确认生成文章配图',async()=>{
          if(!window.confirm(`本计划共${plan.count}张配图，最多预留¥${plan.maximum_cost_cny}。使用qwen-image-3.0，保留水印，无自动付费重试。是否一次确认整个计划？`))return;
          plan=await api(prefix+'/plans/'+plan.id+'/generate',{confirmed:true,digest:plan.digest});render();
        },!plan.capability.enabled||!plan.count);
      }
    }
    feedback.textContent='正在查询当前文章的已保存配图…';
    api(prefix+'/tasks/'+encodeURIComponent(taskId)).then(async value=>{
      if(!current())return;plan=value.plan;
      if(!plan){const capability=await api(prefix+'/capability');if(current())feedback.textContent=capability.user_message;}
      render();
    }).catch(error=>{if(current()){feedback.textContent=error.message;render();}});
  }
  function enhance() {
    const main=document.querySelector('#main');if(!main||!['campaign-agent','preview-wechat-agent'].includes(main.dataset.agentId))return;
    for(const article of main.querySelectorAll('[data-assistant-message-id]')){
      const match=/^task:([0-9a-f-]{36}):assistant$/.exec(article.dataset.assistantMessageId||'');
      if(!match||mounted.has(article)||article.querySelector('.streaming-cursor,.thinking-spinner'))continue;
      mounted.add(article);mount(article,main,match[1]);
    }
  }
  const observer=new MutationObserver(enhance);observer.observe(document.querySelector('#app')||document.body,{childList:true,subtree:true});enhance();
})();
