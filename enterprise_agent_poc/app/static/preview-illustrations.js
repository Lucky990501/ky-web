/* Preview-only automatic progress. History GET never starts paid work. */
(() => {
  'use strict';
  if (!window.WORKBENCH_PREVIEW_BASE) return;
  const prefix='/api/v1/preview/illustrations',mounted=new WeakSet();
  const unavailable='正文已完成，当前配图未执行：缺少当前Tenant的图片授权或预算。';
  async function api(path,body) {
    const response=await fetch(path,{credentials:'same-origin',cache:'no-store',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const value=await response.json();if(!response.ok)throw Error(value.user_message||'配图状态查询失败；正文和纯文导出仍可使用。');return value;
  }
  function mount(article,main,taskId) {
    const slot=document.createElement('section');slot.className='preview-illustrations';slot.setAttribute('aria-label','自动配图进度');
    const title=document.createElement('strong');title.textContent='自动配图';
    const feedback=document.createElement('p');feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');feedback.setAttribute('aria-atomic','true');
    const actions=document.createElement('div'),assets=document.createElement('div');assets.className='preview-illustration-gallery';
    slot.append(title,feedback,actions,assets);article.append(slot);
    let plan=null,timer=null,polls=0,emptyPolls=0,renderSignature='';
    const assetNodes=new Map();
    const current=()=>slot.isConnected&&main.isConnected&&article.closest('#main')===main;
    const observer=new MutationObserver(()=>{if(!current()){clearTimeout(timer);observer.disconnect();}});observer.observe(document.documentElement,{childList:true,subtree:true});
    function link(label,url,filename) {
      const node=document.createElement('a');node.className='button secondary';node.textContent=label;
      node.href=window.workbenchPreviewUrl(url);node.download=filename;
      node.onclick=async event=>{
        event.preventDefault();if(node.dataset.downloading)return;node.dataset.downloading='true';
        try {
          const response=await fetch(node.href,{credentials:'same-origin',cache:'no-store'});
          if(!response.ok)throw Error('下载失败；正文和已有图片仍可使用。');
          const blob=await response.blob(),objectUrl=URL.createObjectURL(blob),save=document.createElement('a');
          save.href=objectUrl;save.download=filename;save.hidden=true;document.body.append(save);save.click();save.remove();
          setTimeout(()=>URL.revokeObjectURL(objectUrl),60000);
        }catch(error){feedback.textContent=error.message;}finally{delete node.dataset.downloading;}
      };return node;
    }
    function updateArticle() {
      const version=plan?.html&&plan.id+':'+plan.assets.map(a=>a.id).join(',');
      if(!version||article.dataset.illustrationPlan===version||!window.WorkbenchWechatArticle||typeof window.bindModalDialog!=='function')return;
      article.dataset.illustrationPlan=version;article.querySelector('.wechat-article-actions')?.remove();
      window.WorkbenchWechatArticle.mountManual({article,main,agent:{slug:'wechat-official-account-writing'},
        content:plan.html,messageId:article.dataset.assistantMessageId,markdown:s=>s,modal:window.bindModalDialog,isCurrent:current,inlineBody:true,
        metadata:{title:plan.publication?.title,digest:plan.publication?.digest,coverAsset:plan.publication?.cover}});
    }
    function again() {
      if(timer||polls>=300||!current())return;
      timer=setTimeout(()=>{timer=null;polls++;load();},1500);
    }
    function render() {
      if(!current())return;
      const signature=JSON.stringify(plan);
      if(signature===renderSignature){if(plan&&['queued','generating'].includes(plan.status))again();return;}
      renderSignature=signature;actions.replaceChildren();
      const visible=(plan?.assets||[]).filter(asset=>asset.role!=='cover'),ids=new Set(visible.map(asset=>asset.id));
      for(const [id,node] of assetNodes)if(!ids.has(id)){node.remove();assetNodes.delete(id);}
      for(const [index,asset] of visible.entries()) {
        let figure=assetNodes.get(asset.id);
        if(!figure){
          figure=document.createElement('figure');figure.dataset.assetId=asset.id;
          const image=document.createElement('img'),caption=document.createElement('figcaption');
          image.alt=asset.label;image.loading=index<2?'eager':'lazy';image.decoding='async';image.width=asset.width;image.height=asset.height;
          image.dataset.previewImageOriginal=window.workbenchPreviewUrl(asset.url);
          caption.textContent=asset.label;
          image.onerror=()=>{caption.textContent=asset.label+' · 图片暂不可用；未触发重新生图。';};
          if(window.WorkbenchPreviewImages)window.WorkbenchPreviewImages.bind(image,window.workbenchPreviewUrl('/api/v1/preview/image-display/illustrations/'+asset.id));
          else image.src=window.workbenchPreviewUrl(asset.url);
          figure.append(image,caption,link('下载配图',asset.url,asset.filename));assetNodes.set(asset.id,figure);
        }
        if(assets.children[index]!==figure)assets.insertBefore(figure,assets.children[index]||null);
      }
      updateArticle();
      const running=plan&&['queued','generating'].includes(plan.status);slot.setAttribute('aria-busy',running?'true':'false');
      if(running) {
        feedback.textContent=`正文已完成 → 配图生成中 ${plan.assets.length}/${plan.count}；已保存图片即时展示。`;
        if(polls<300)again();else feedback.textContent+=' 后台继续处理，可稍后刷新历史查看。';return;
      }
      if(plan?.status==='completed') {
        feedback.textContent=plan.kind==='activity'?'正文已完成 → 配图已完成 → 可导出图文Word':'正文已完成 → 配图已完成 → 可复制/导出';
        if(plan.document)actions.append(link('下载图文 Word',plan.document.download_url,plan.document.filename));
        if(plan.html_url)actions.append(link('下载图文 HTML',plan.html_url,'article.html'),link('下载文章与配图 ZIP',plan.package_url,'article-images.zip'));
      } else if(plan?.status==='failed') {
        feedback.textContent=`正文已完成，部分配图未生成（${plan.assets.length}/${plan.count}，${plan.error_code||'结果未确认'}）；正文及纯文版仍可导出。`;
        const rejected=(plan.outcomes||[]).filter(o=>o.state==='REJECTED');
        if(rejected.length)feedback.textContent+=` 供应商拒绝${rejected.length}张（HTTP ${[...new Set(rejected.map(o=>o.http_status))].join('/')}）。`;
        if(plan.assets.length===plan.count&&plan.count) {
          const retry=document.createElement('button');retry.type='button';retry.className='button secondary';retry.textContent='仅重试文档整理（不重新生图）';
          retry.onclick=async()=>{retry.disabled=true;try{plan=await api(prefix+'/plans/'+plan.id+'/export',{});render();}catch(e){feedback.textContent=e.message;retry.disabled=false;}};actions.append(retry);
        } else if(plan.can_recover_missing) {
          const recover=document.createElement('button');recover.type='button';recover.className='button secondary';recover.textContent='补齐缺失图片';
          recover.onclick=async()=>{
            if(recover.disabled)return;recover.disabled=true;feedback.textContent='正在检查缺图及已有预留…';
            try{plan=await api(prefix+'/plans/'+plan.id+'/recover',{digest:plan.digest,confirmed:true});polls=0;render();}
            catch(e){feedback.textContent=e.message;recover.disabled=false;}
          };actions.append(recover);
        }
        if(plan.unresolved_missing)feedback.textContent+=` ${plan.unresolved_missing}张请求结果未确认或需恢复原资产，未重新生图。`;
      } else if(plan?.status==='skipped') {
        feedback.textContent=/AUTHORIZ|BUDGET/.test(plan.error_code||'')?unavailable:'正文已完成，当前配图未执行：'+(plan.error_code==='ILLUSTRATION_NO_REQUIREMENTS'?'没有有效配图需求。':plan.error_code==='ILLUSTRATION_TASK_LIMIT'&&plan.kind==='activity'?plan.user_message:'配图条件未满足，纯文版仍可使用。');
      } else feedback.textContent='正文已完成，历史配图未执行；保留原正文和纯文字导出，不因刷新自动收费。';
    }
    async function load() {
      try {
        const value=await api(prefix+'/tasks/'+encodeURIComponent(taskId));if(!current())return;plan=value.plan;
        if(!plan&&emptyPolls++<5){feedback.textContent='正文已完成 → 正在准备自动配图…';again();return;}
        if(!plan){const cap=await api(prefix+'/capability');if(!current())return;if(!cap.enabled){feedback.textContent=unavailable;return;}}
        render();
      }catch(error){if(current())feedback.textContent=error.message;}
    }
    feedback.textContent='正文已完成 → 正在查询自动配图进度…';load();
  }
  function enhance() {
    const main=document.querySelector('#main');if(!main||!['campaign-agent','preview-wechat-agent'].includes(main.dataset.agentId))return;
    main.querySelectorAll('[data-illustration-briefs]').forEach(n=>n.remove());
    for(const article of main.querySelectorAll('[data-assistant-message-id]')) {
      const match=/^task:([0-9a-f-]{36}):assistant$/.exec(article.dataset.assistantMessageId||'');
      if(!match||mounted.has(article)||article.querySelector('.streaming-cursor,.thinking-spinner'))continue;
      mounted.add(article);mount(article,main,match[1]);
    }
  }
  new MutationObserver(enhance).observe(document.querySelector('#app')||document.body,{childList:true,subtree:true});enhance();
})();
