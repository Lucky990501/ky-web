const app = document.querySelector('#app');
let me, activeConversationId = null, activeAgentId = 'image-agent', mobileDrawerController = () => {};
const pageCache = new Map();
const activityPlanDocuments = new Map();
const activityPlanDocumentStoragePrefix = 'activity-plan-document-v1:';
let activityPlanBrandName = '';
const cacheablePaths = new Set(['/api/v1/me','/api/v1/workspace','/api/v1/conversations','/api/v1/generations','/api/v1/knowledge/files','/api/v1/assets','/api/v1/enterprise-config','/api/v1/platform/skills']);
const customerErrorMessages = {AUTH_REQUIRED:'请先登录或重新登录。',ACCOUNT_DISABLED:'账号已停用，请联系企业管理员。',FORBIDDEN:'你没有权限访问此功能。',AGENT_UNAVAILABLE:'当前智能体暂不可用。',TASK_FAILED:'任务处理失败，请重试。',TASK_TIMEOUT:'任务处理时间较长，请稍后重试。',KNOWLEDGE_PROCESSING_FAILED:'资料处理失败，请重新上传。',INVALID_INPUT:'输入内容不符合要求，请检查后重试。',INSTANCE_NOT_ENABLED:'当前智能体尚未为该企业启用。',SERVICE_TEMPORARILY_UNAVAILABLE:'服务暂时不可用，请稍后重试。',INSTANCE_MUST_BE_DISABLED_BEFORE_RECONFIGURE:'请先停用该智能体，再修改配置。'};
const fallbackErrorCode = status => status===401?'AUTH_REQUIRED':status===403?'FORBIDDEN':status===408||status===504?'TASK_TIMEOUT':status>=500?'SERVICE_TEMPORARILY_UNAVAILABLE':'INVALID_INPUT';
const api = (path, options = {}) => {
  const method=(options.method||'GET').toUpperCase(),canCache=method==='GET'&&cacheablePaths.has(path),cached=pageCache.get(path);
  if(canCache&&cached&&Date.now()-cached.at<15000)return Promise.resolve(cached.value);
  return fetch(path,{credentials:'same-origin',headers:{'content-type':'application/json',...(options.headers||{})},...options}).then(async response=>{
    let body={};try{body=await response.json();}catch{}
    if(!response.ok){const code=body.error_code||fallbackErrorCode(response.status),error=Error(body.user_message||customerErrorMessages[code]||'请求失败，请稍后重试。');error.code=code;error.status=response.status;error.requestId=body.request_id||response.headers?.get?.('x-request-id')||'';throw error;}
    if(canCache)pageCache.set(path,{at:Date.now(),value:body});if(method!=='GET')pageCache.clear();return body;
  });
};
const apiForm=(path,form)=>fetch(path,{method:'POST',credentials:'same-origin',body:form}).then(async response=>{let body={};try{body=await response.json();}catch{}if(!response.ok){const code=body.error_code||fallbackErrorCode(response.status),error=Error(body.user_message||customerErrorMessages[code]||'上传失败，请稍后重试。');error.code=code;error.requestId=body.request_id||response.headers?.get?.('x-request-id')||'';throw error;}pageCache.clear();return body;});
const escapeHtml = v => String(v || '').replace(/[&<>'"]/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;' })[c]);
const icon = (name, size = 18) => `<i data-lucide="${name}" width="${size}" height="${size}"></i>`;
const avatarMarkup = (className = 'avatar', label = '用户头像') => me?.avatar_url ? `<img class="${className} avatar-image" src="${escapeHtml(me.avatar_url)}" alt="${escapeHtml(label)}">` : `<span class="${className}">${escapeHtml((me?.display_name || '我')[0])}</span>`;
const syncAvatarViews = () => { if (!me?.avatar_url) return; document.querySelectorAll('span.avatar').forEach(node => { const imageNode = document.createElement('img'); imageNode.className = `${node.className} avatar-image`; imageNode.src = me.avatar_url; imageNode.alt = '用户头像'; node.replaceWith(imageNode); }); };
const storageUrl = item => item?.content_url || item?.image_url || `/api/v1/storage/${String(item?.storage_key||'').split('/').map(encodeURIComponent).join('/')}`;
const image = (item, label = '图片') => item?.storage_key || item?.content_url || item?.image_url ? `<a class="media-thumb" data-generation-link href="${escapeHtml(storageUrl(item))}" target="_blank" rel="noopener"><span class="media-loading" aria-hidden="true">${icon('loader-circle',20)}</span><img loading="lazy" decoding="async" data-generation-image src="${escapeHtml(storageUrl(item))}" alt="${escapeHtml(label)}"></a>` : `<div class="media-thumb media-empty">${icon('image-off',24)}<span>暂无图片</span></div>`;
const bindImageFallbacks = () => document.querySelectorAll('img[data-generation-image]:not([data-image-bound])').forEach(node=>{node.dataset.imageBound='true';const wrap=node.closest('.media-thumb')||node.parentElement;if(!wrap)return;const loading=wrap.querySelector('.media-loading'),source=node.currentSrc||node.src;const clearFailure=()=>{wrap.classList.remove('media-failed');wrap.classList.add('media-loaded');wrap.removeAttribute('aria-live');wrap.querySelector('.media-error')?.remove();node.hidden=false;if(loading)loading.hidden=true;};const showFailure=()=>{wrap.classList.remove('media-loaded');wrap.classList.add('media-failed');node.hidden=true;if(loading)loading.hidden=true;if(!wrap.matches('.media-thumb')){wrap.innerHTML=`${icon('image-off',24)}<span class="sr-only">图片加载失败</span>`;window.lucide?.createIcons({attrs:{'stroke-width':1.9}});return;}if(!wrap.querySelector('.media-error'))wrap.insertAdjacentHTML('beforeend',`<span class="media-error"><i data-lucide="image-off" aria-hidden="true"></i><b>图片加载失败</b><small>点击重新加载</small></span>`);wrap.setAttribute('aria-live','polite');window.lucide?.createIcons({attrs:{'stroke-width':1.9}});};const retry=event=>{if(!wrap.classList.contains('media-failed')||!wrap.matches('.media-thumb'))return;event.preventDefault();event.stopImmediatePropagation();wrap.classList.remove('media-failed','media-loaded');wrap.querySelector('.media-error')?.remove();node.hidden=false;if(loading)loading.hidden=false;node.src=`${source}${source.includes('?')?'&':'?'}retry=${Date.now()}`;};node.addEventListener('load',clearFailure);node.addEventListener('error',showFailure);wrap.addEventListener('click',retry,{capture:true});wrap.addEventListener('keydown',event=>{if((event.key==='Enter'||event.key===' ')&&wrap.classList.contains('media-failed'))retry(event);},{capture:true});if(node.complete){if(node.naturalWidth)clearFailure();else showFailure();}});
const refreshIcons = () => { window.lucide?.createIcons({ attrs: { 'stroke-width': 1.9 } }); bindGenerationViewers(); bindImageFallbacks(); };

function login() {
  activityPlanDocuments.clear();activityPlanBrandName='';clearActivityPlanSessionDocuments();
  app.innerHTML = `<div class="login-layout"><section class="login-hero"><div class="brand-lockup">${icon('bot',35)}<div><b>AI Workbench</b><span>企业 AI 智能体工作台</span></div></div><div class="hero-copy"><h1>让 AI 成为<br>每一位团队成员的<span>创造力引擎</span></h1><p>在 AI 的陪伴下，激发灵感，高效创作，共同成长</p><div class="hero-values"><span>${icon('zap')} 高效创作</span><span>${icon('users')} 协同共建</span><span>${icon('book-open')} 专业可信</span></div></div></section><section class="login-side"><nav class="login-links">产品官网　 |　 帮助中心　 |　 下载客户端</nav><form class="login-card" id="login"><div class="brand-lockup small">${icon('bot',28)}<div><b>AI Workbench</b><span>企业 AI 智能体工作台</span></div></div><h2>欢迎登录 AI Workbench</h2><p>开启高效、智能、协同的创作新体验</p><label><span>账号</span><input name="account" type="email" required autocomplete="username" placeholder="请输入账号 / 邮箱"></label><label><span>密码</span><input name="password" type="password" required autocomplete="current-password" placeholder="请输入密码"></label><label class="checkbox"><input type="checkbox" checked> 记住我</label><button class="button primary full" type="submit">登 录</button><p class="form-error" role="alert"></p></form></section></div>`;
  document.querySelector('.login-links').innerHTML = '<button type="button" class="login-help" id="login-help">帮助中心</button>';
  document.querySelector('#login-help').onclick = showHelpDialog;
  document.querySelector('.login-disabled')?.remove();
  document.querySelector('#login').onsubmit = async e => { e.preventDefault(); const err = document.querySelector('.form-error'); try { await api('/api/v1/auth/login',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))}); boot(); } catch (error) { err.textContent = error.message; } };
  refreshIcons();
}

const memberNavs = [['workspace','layout-dashboard','工作台'],['image','wand-sparkles','AI 创作'],['history','history','历史记录'],['generations','folder-open','我的生成']];
const enterpriseAdminNavs = [['knowledge','book-open','知识库'],['assets','image','素材库'],['recent-tasks','list-checks','最近任务'],['members','users','成员管理'],['enterprise','settings','企业设置']];
const platformAdminNavs = [['platform-skills','package-check','Skill 管理'],['platform-agents','bot','Agent 管理']];
const profileNav = ['profile','user-round','个人中心'];
const navigationForUser = user => [...memberNavs,...(user?.role==='enterprise_admin'?enterpriseAdminNavs:[]),...(user?.is_platform_admin?platformAdminNavs:[]),profileNav];
const pageRoutes = Object.freeze({workspace:'/workspace',image:'/agents/image',copywriting:'/agents/copywriting',campaign:'/agents/campaign',history:'/conversations',generations:'/generations',knowledge:'/knowledge',assets:'/assets','recent-tasks':'/recent-tasks',members:'/members',enterprise:'/enterprise-config',profile:'/profile','platform-skills':'/platform/skills','platform-agents':'/platform/agents'});
const pageFromPath = path => Object.entries(pageRoutes).find(([,route])=>route===path)?.[0]||( /^\/agents\/[a-zA-Z0-9-]+$/.test(path) ? `agent:${path.slice(8)}` : null);
const navigationState = page => ({page,activeAgentId,activeConversationId});
const allowedPage = page => ['platform-skills','platform-agents'].includes(page)&&!me?.is_platform_admin?'workspace':['knowledge','assets','recent-tasks','members','enterprise','billing'].includes(page)&&me?.role!=='enterprise_admin'?'workspace':page;
function navigate(page,{replace=false}={}){
  const target=allowedPage(page),path=pageRoutes[target]||(target.startsWith('agent:')?`/agents/${encodeURIComponent(target.slice(6))}`:location.pathname),state=navigationState(target),current=window.history.state||{};
  const same=location.pathname===path&&current.page===state.page&&current.activeAgentId===state.activeAgentId&&current.activeConversationId===state.activeConversationId;
  window.history[replace||same?'replaceState':'pushState'](state,'',path);
  return render(target);
}
function pageFromNavigation(state=window.history.state){
  const page=allowedPage(pageFromPath(location.pathname)||state?.page||'workspace');
  if(state&&Object.prototype.hasOwnProperty.call(state,'activeAgentId'))activeAgentId=state.activeAgentId||'image-agent';
  if(state&&Object.prototype.hasOwnProperty.call(state,'activeConversationId'))activeConversationId=state.activeConversationId||null;
  if(page.startsWith('agent:'))activeAgentId=page.slice(6);
  return page;
}
window.addEventListener('popstate',event=>{mobileDrawerController(false);if(me)render(pageFromNavigation(event.state));});
const roleName = role => role === 'enterprise_admin' ? '企业管理员' : '成员';
function shell(initialPage = 'workspace') {
  const navs=navigationForUser(me),navMarkup=navs.map(([id,i,label])=>`<button data-page="${id}">${icon(i)}<span>${label}</span></button>`).join('');
  app.innerHTML = `<div class="app-shell"><aside class="sidebar"><div class="brand-lockup">${icon('bot',30)}<div><b>AI Workbench</b><span>Create · Collaborate · Grow</span></div></div><div class="tenant-summary" aria-label="当前企业">${icon('building-2')}<span><b>${escapeHtml(me.tenant_name || me.tenant_id || '企业工作台')}</b><small>${me.is_platform_admin?'平台管理':'企业版'}</small></span></div><nav class="side-nav">${navMarkup}</nav><section class="side-credit" id="side-credit"></section><button type="button" class="side-user" data-go="profile" aria-label="进入个人中心">${avatarMarkup('avatar side-avatar')}<span><b>${escapeHtml(me.display_name)}</b><small>${me.is_platform_admin?'平台管理员':roleName(me.role)}</small></span></button></aside><div class="mobile-drawer-backdrop" id="mobile-drawer-backdrop" hidden></div><aside class="mobile-drawer" id="mobile-drawer" aria-label="主导航" aria-hidden="true"><header><div class="brand-lockup">${icon('bot',26)}<div><b>AI Workbench</b><span>${escapeHtml(me.tenant_name||'企业工作台')}</span></div></div><button class="icon-button" id="mobile-menu-close" aria-label="关闭主导航">${icon('x')}</button></header><nav class="side-nav">${navMarkup}</nav></aside><main class="shell-main"><header class="topbar"><button class="top-icon mobile-menu-button" id="mobile-menu-button" aria-label="打开主导航" aria-controls="mobile-drawer" aria-expanded="false">${icon('menu')}</button><div class="top-actions"><button class="top-icon" id="top-help" aria-label="帮助与反馈">${icon('circle-help')}</button><div class="account-menu-wrap" id="account-menu-wrap"><button class="account-trigger" id="account-trigger" aria-haspopup="menu" aria-expanded="false">${avatarMarkup()}<b>${escapeHtml(me.display_name)}</b>${icon('chevron-down',16)}</button><div class="account-dropdown" id="account-dropdown" role="menu" hidden><div class="account-summary">${avatarMarkup('avatar account-avatar')}<div><b>${escapeHtml(me.display_name)}</b><small class="role-badge">${me.is_platform_admin?'平台管理员':roleName(me.role)}</small><em>${escapeHtml(me.tenant_name || me.tenant_id || '企业工作台')}</em></div></div><div class="account-menu-group"><button role="menuitem" data-account-action="profile">${icon('user-round')}个人中心</button><button role="menuitem" data-account-action="security">${icon('shield-check')}账号与安全</button><button role="menuitem" data-account-action="help">${icon('circle-help')}帮助与反馈</button></div><div class="account-menu-divider"></div><button role="menuitem" class="account-logout" data-account-action="logout">${icon('log-out')}退出登录</button></div></div></div></header><section class="page" id="main"></section></main></div>`;
  const drawer=document.querySelector('#mobile-drawer'),backdrop=document.querySelector('#mobile-drawer-backdrop'),trigger=document.querySelector('#mobile-menu-button');
  const setDrawer=open=>{drawer.classList.toggle('open',open);backdrop.hidden=!open;drawer.setAttribute('aria-hidden',String(!open));trigger.setAttribute('aria-expanded',String(open));document.body.classList.toggle('drawer-open',open);if(open)drawer.querySelector('[data-page]')?.focus();else trigger.focus();};
  mobileDrawerController=setDrawer;drawer.onkeydown=event=>{if(event.key==='Escape')setDrawer(false);};
  trigger.onclick=()=>setDrawer(true);document.querySelector('#mobile-menu-close').onclick=()=>setDrawer(false);backdrop.onclick=()=>setDrawer(false);
  document.querySelectorAll('[data-page]').forEach(x => x.onclick = () => {if(x.closest('.mobile-drawer'))setDrawer(false);navigate(x.dataset.page);});
  document.querySelector('#top-help').onclick = showHelpDialog;
  bindAccountMenu();
  syncAvatarViews(); const rendered=render(initialPage); refreshIcons(); return rendered;
}

const header = (title, description, iconName, className = '') => `<header class="page-header ${className}"><div class="title-row">${icon(iconName,32)}<div><h1>${title === '企业配置' ? '企业设置' : title}</h1><p>${description}</p></div></div></header>`;
const button = (copy, iconName, cls='secondary', attrs='') => `<button type="button" class="button ${cls}" ${attrs}>${icon(iconName,17)}${copy}</button>`;
const stageMap = {queued:'正在思考',loading_context:'正在思考',retrieving_knowledge:'正在思考',retrieving_assets:'正在思考',generating:'正在思考',saving_asset:'正在思考'};
const pageLoading = () => `<section class="page-loading" aria-live="polite" aria-busy="true"><div class="skeleton-title"><i></i><span></span></div><div class="skeleton-subtitle"></div><div class="skeleton-layout"><article><div class="skeleton-banner"></div><div class="skeleton-row"><i></i><i></i><i></i></div><div class="skeleton-panel"></div></article><aside><div class="skeleton-card"></div><div class="skeleton-card"></div></aside></div><p>正在加载页面内容…</p></section>`;

async function render(page) {
  // Give each route its own DOM: late responses can only update a detached view.
  const previousMain = document.querySelector('#main'), main = previousMain.cloneNode(false);
  previousMain.replaceWith(main);
  main.dataset.page = page;
  document.querySelectorAll('[data-page]').forEach(x => x.classList.toggle('active',x.dataset.page === (page.startsWith('agent:')?'image':page)));
  main.innerHTML = pageLoading();
  try {
    if (page === 'workspace') return await workspaceV2(main);
    if (page === 'image') return await agentWorkspaceV2(main,'image-agent');
    if (page === 'copywriting') return await agentWorkspaceV2(main,'copywriting-agent');
    if (page === 'campaign') return await agentWorkspaceV2(main,'campaign-agent');
    if (page.startsWith('agent:')) return await agentWorkspaceV2(main,page.slice(6));
    if (page === 'history') return await conversationHistory(main);
    if (page === 'generations') return await generations(main);
    if (page === 'knowledge') return await knowledgeV3(main);
    if (page === 'assets') return await assets(main);
    if (page === 'recent-tasks') return await recentTasks(main);
    if (page === 'enterprise') return await enterprise(main);
    if (page === 'members') return await members(main);
    if (page === 'billing') return await billing(main);
    if (page === 'profile') return await profileV2(main);
    if (page === 'platform-skills' && me.is_platform_admin) return await platformSkills(main);
    if (page === 'platform-agents' && me.is_platform_admin) return await platformAgents(main);
  } catch (error) { main.innerHTML = `<section class="page-load-error" role="alert"><b>${icon('circle-alert',24)}页面加载失败</b><p>${escapeHtml(error.message || '请稍后重试。')}</p><button class="button primary" data-retry-page="${escapeHtml(page)}">${icon('refresh-cw')}重新加载</button></section>`; main.querySelector('[data-retry-page]').onclick = () => render(page); refreshIcons(); }
}

async function workspaceV2(main) {
  const data = await api('/api/v1/workspace'); sideCredit(data.credit_balance);
  if(main.dataset.page!=='workspace')return;
  const cards=(data.agents||[]).filter(agent=>agent.enabled).map(agent=>`<article class="agent-card" data-agent-id="${escapeHtml(agent.id)}" data-agent-slug="${escapeHtml(agent.slug)}">${icon(agent.icon||'bot',30)}<h3>${escapeHtml(agent.name)}</h3><p>${escapeHtml(agent.description)}</p><span class="agent-category">${escapeHtml(agentCategoryLabel(agent.category))}</span><div class="agent-card-footer"><small>${agent.credit_cost} 积分 / 次</small>${button('进入智能体','arrow-right','primary',`data-agent="${escapeHtml(agent.definition_source==='productized'?agent.slug:agent.id)}"`)}</div></article>`).join('');
  const shortcuts=me.role==='enterprise_admin'?[['history','history','历史记录'],['recent-tasks','list-checks','最近任务'],['knowledge','book-open','知识库'],['assets','image','素材库'],['members','users','成员管理'],['enterprise','settings','企业设置']]:[['history','history','历史记录'],['generations','folder-open','我的生成'],['profile','user-round','个人中心']];
  const greeting=`<header class="workspace-greeting-banner" aria-label="工作台欢迎信息"><div class="workspace-greeting-copy"><p class="workspace-greeting-kicker">${escapeHtml(data.brand_name||data.tenant_name)} · 今日工作台</p><h1>你好，${escapeHtml(me.display_name)}！</h1><p>从这里开始完成今天的第一项 AI 创作。</p></div></header>`;
  main.innerHTML=`${greeting}<div class="workspace-grid"><section><div class="welcome-banner"><div><b>${escapeHtml(data.brand_name||data.tenant_name)}</b><h2>让 AI 帮你更快完成日常创作</h2><p>选择一个已启用的智能体，描述业务需求，即可开始。</p></div></div><div class="section-title"><h2>可使用的智能体</h2><span class="muted">${(data.agents||[]).filter(agent=>agent.enabled).length} 个可用</span></div><div class="agent-grid">${cards||'<div class="empty-state">暂时没有可使用的智能体，请联系企业管理员。</div>'}</div><div class="two-panels"><section class="panel recent-history"><div class="section-title"><h2>最近项目</h2><a href="/conversations" data-go="history">查看全部</a></div>${(data.recent_conversations||[]).slice(0,3).map(x=>conversationRowHtml(x,'data-agent-conversation')).join('')||'<div class="empty-state">还没有创作项目，选择一个智能体开始第一次 AI 对话。</div>'}</section><section class="panel"><div class="section-title"><h2>最近生成</h2><a data-go="generations">查看全部</a></div><div class="compact-generations">${(data.recent_generations||[]).slice(0,3).map(x=>`<article>${image(x,x.project?.name||'最近生成')}<span><b>${escapeHtml(x.project?.name||'图片生成项目')}</b><small>${escapeHtml(formatHistoryTime(x.created_at))}</small></span></article>`).join('')||'<div class="empty-state">还没有图片生成，使用图片生成智能体开始创作。</div>'}</div></section></div></section><aside class="right-rail"><section class="panel credit-panel"><h3>我的积分</h3><b class="credit-num">${data.credit_balance} <small>积分</small></b><div class="meter"><i style="width:${Math.min(data.credit_balance/10,100)}%"></i></div></section><section class="panel"><h3>快速入口</h3><div class="quick-grid">${shortcuts.map(x=>`<button data-go="${x[0]}">${icon(x[1],24)}<span>${x[2]}</span>${icon('arrow-right',15)}</button>`).join('')}</div></section></aside></div>`;
  main.querySelectorAll('[data-agent]').forEach(node=>node.onclick=()=>{activeAgentId=node.dataset.agent;activeConversationId=null;navigate(agentPage(activeAgentId));});
  main.querySelectorAll('[data-agent-conversation]').forEach(node=>node.onclick=()=>{activeAgentId=node.dataset.agentId;activeConversationId=node.dataset.agentConversation;navigate(agentPage(activeAgentId));}); bindNavigation(); refreshIcons();
}

async function conversationHistory(main,offset=0,loaded=[]){
  const pageSize=50,batch=await api(`/api/v1/conversations?limit=${pageSize}&offset=${offset}`),items=[...loaded,...batch];
  if(main.dataset.page!=='history')return;
  const agents=[...new Map(items.map(item=>[item.agent_id,item.agent||{id:item.agent_id,name:item.agent_id,icon:'bot'}])).values()];
  const groups=agents.map(agent=>{const projects=items.filter(item=>item.agent_id===agent.id);return `<section class="history-group" data-history-group="${escapeHtml(agent.id)}"><header><span>${icon(agent.icon||'bot',24)}</span><div><h2>${escapeHtml(projects[0]?.project?.type||agent.name)}</h2><p>${escapeHtml(agent.name)} · ${projects.length} 个项目</p></div></header><div class="history-list">${projects.map(historyCardHtml).join('')}</div></section>`;}).join('');
  main.innerHTML=`${header('历史记录','按创作项目和智能体归类，快速找回需求、正文与生成图片。','history')}<div class="history-toolbar"><label>${icon('search')}<input id="history-search" aria-label="搜索历史项目" placeholder="搜索项目名称或最近需求"></label><label><span>智能体</span><select id="history-agent-filter" aria-label="按智能体筛选"><option value="">全部智能体</option>${agents.map(agent=>`<option value="${escapeHtml(agent.id)}">${escapeHtml(agent.name)}</option>`).join('')}</select></label></div><div class="history-groups">${groups||'<div class="empty-state large">还没有历史项目，开始第一次 AI 对话后会显示在这里。</div>'}<div class="empty-state large history-filter-empty" id="history-filter-empty" aria-live="polite" hidden>没有找到匹配的历史项目，请调整搜索或筛选条件。</div></div>${batch.length===pageSize?`<div class="history-load-more"><button class="button secondary" id="history-load-more">${icon('chevrons-down')}加载更早项目</button><small>已显示 ${items.length} 个项目</small></div>`:''}`;
  main.querySelector('.page-header')?.insertAdjacentHTML('beforebegin','<nav class="history-mobile-nav" aria-label="历史页导航"><a class="button secondary" href="/workspace" data-go="workspace">'+icon('arrow-left')+'返回工作台</a></nav>');
  const applyFilter=()=>{const query=main.querySelector('#history-search').value.trim().toLowerCase(),agentId=main.querySelector('#history-agent-filter').value;let visible=0;main.querySelectorAll('[data-history-conversation]').forEach(card=>{card.hidden=Boolean((agentId&&card.dataset.historyAgent!==agentId)||(query&&!card.dataset.historySearch.includes(query)));if(!card.hidden)visible+=1;});main.querySelectorAll('[data-history-group]').forEach(group=>{group.hidden=!Array.from(group.querySelectorAll('[data-history-conversation]')).some(card=>!card.hidden);});main.querySelector('#history-filter-empty').hidden=visible!==0||items.length===0;};
  main.querySelector('#history-search').oninput=applyFilter;main.querySelector('#history-agent-filter').onchange=applyFilter;
  main.querySelectorAll('[data-history-conversation]').forEach(node=>node.onclick=()=>{activeAgentId=node.dataset.historyAgent;activeConversationId=node.dataset.historyConversation;navigate(agentPage(activeAgentId));});
  main.querySelector('#history-load-more')?.addEventListener('click',()=>conversationHistory(main,offset+pageSize,items));
  bindNavigation();refreshIcons();
}
const agentPage=id=>id==='image-agent'?'image':id==='copywriting-agent'?'copywriting':id==='campaign-agent'?'campaign':`agent:${id}`;
const agentPlaceholder=agentId=>agentId==='image-agent'?'描述你想创作的图片，例如：帮我做一张秋季招生海报':agentId==='copywriting-agent'?'描述需要的文案，例如：写一篇秋季招生公众号推文':'描述需要策划的活动，例如：制定国庆招生开放日方案';
const formatHistoryTime=value=>String(value||'').replace('T',' ').slice(0,16);
const compactHistoryText=(value,maxLength=10)=>Array.from(String(value||'')).slice(0,maxLength).join('');
const taskStatusLabel=status=>({queued:'排队中',running:'处理中',completed:'已完成',failed:'失败',cancelled:'已取消'})[status]||'暂无任务';
const conversationStatusLabel=item=>item.agent_id==='image-agent'&&item.latest_status==='failed'&&item.latest_generation?.task_id&&item.latest_task?.id&&item.latest_generation.task_id!==item.latest_task.id?'最近一次失败':taskStatusLabel(item.latest_status);
const imageConversationStatusHtml=item=>item?.latest_status==='failed'?`<p>${conversationStatusLabel(item)==='最近一次失败'?'最近一次生成失败。此前已生成的图片仍可查看和下载。':'最近一次生成失败，请重试。'}</p>`:item?.latest_status==='cancelled'?'<p>已停止生成。已保存的历史结果仍可查看。</p>':'';
const conversationRowHtml=(item,attribute,selected=false)=>{const agent=item.agent||{name:item.agent_id,icon:'bot'},project=item.project||{name:item.title,type:'智能创作项目'},name=project.name||item.title||'未命名项目',prompt=item.latest_prompt||'尚无任务内容';return `<button class="conversation-row ${selected?'selected':''}" ${attribute}="${escapeHtml(item.id)}" data-agent-id="${escapeHtml(item.agent?.slug||item.agent_id)}"><span class="conversation-kind">${item.latest_generation?`<img data-generation-image src="${escapeHtml(storageUrl(item.latest_generation))}" alt="">`:icon(agent.icon||'bot',19)}</span><span class="conversation-copy"><span class="conversation-meta"><em>${escapeHtml(project.type)}</em><i class="history-status ${escapeHtml(item.latest_status||'none')}">${escapeHtml(conversationStatusLabel(item))}</i></span><b>${escapeHtml(compactHistoryText(name))}</b><small class="conversation-preview">${escapeHtml(compactHistoryText(prompt))}</small></span></button>`;};
const historyCardHtml=item=>{const agent=item.agent||{name:item.agent_id,icon:'bot'},project=item.project||{name:item.title,type:'智能创作项目'},name=project.name||'未命名项目',prompt=item.latest_prompt||'尚无任务内容',search=[project.name,project.type,agent.name,item.latest_prompt].join(' ').toLowerCase();return `<button class="history-card" data-history-conversation="${escapeHtml(item.id)}" data-history-agent="${escapeHtml(item.agent?.slug||item.agent_id)}" data-history-search="${escapeHtml(search)}"><span class="history-card-visual ${item.latest_generation?'has-image':''}">${item.latest_generation?`<img loading="lazy" decoding="async" data-generation-image src="${escapeHtml(storageUrl(item.latest_generation))}" alt="${escapeHtml(name)}的生成图片">`:icon(agent.icon||'bot',30)}</span><span class="history-card-body"><span class="conversation-meta"><em>${escapeHtml(project.type)}</em><i class="history-status ${escapeHtml(item.latest_status||'none')}">${escapeHtml(conversationStatusLabel(item))}</i></span><b>${escapeHtml(compactHistoryText(name))}</b><span>${escapeHtml(compactHistoryText(prompt))}</span><small>${escapeHtml(agent.name||item.agent_id)}</small></span>${icon('chevron-right',18)}</button>`;};
const agentCategoryLabel=category=>category==='general'?'通用创作':category||'通用创作';
const agentConversationRailHtml=(items,selectedId)=>`<button class="button primary full" id="new-chat">${icon('plus')}新建项目</button><div class="conversation-rail-heading"><h3>${escapeHtml(items[0]?.project?.type||'创作项目')}</h3><button class="link-button" data-go="history">全部历史</button></div>${items.map(item=>conversationRowHtml(item,'data-select-agent-conversation',selectedId===item.id)).join('')||'<div class="empty-state">暂无历史项目</div>'}`;
function bindConversationRail(main,agentId){
  main.querySelector('#new-chat').onclick=()=>{activeConversationId=null;navigate(agentPage(agentId));};
  main.querySelectorAll('[data-select-agent-conversation]').forEach(node=>node.onclick=()=>{activeConversationId=node.dataset.selectAgentConversation;navigate(agentPage(agentId));});
  bindNavigation();
}
const chatImageComposerFields=()=>`<div id="chat-image-attachment" class="chat-image-attachment" aria-live="polite"></div><input id="chat-image-input" type="file" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" hidden>`;
const chatImageComposerButton=()=>`<button class="chat-image-add" id="chat-image-add" type="button" aria-label="添加参考图片" title="添加参考图片">${icon('plus',18)}</button>`;
function bindChatImageComposer(main){
  const picker=main.querySelector('#chat-image-input'),slot=main.querySelector('#chat-image-attachment'),composer=main.querySelector('#composer');
  if(!picker||!slot||!composer)return;
  const attachment={status:'idle',record:null,file:null,error:'',previewUrl:null,revision:0,locked:false};main._chatImage=attachment;
  const releasePreview=()=>{if(attachment.previewUrl)URL.revokeObjectURL(attachment.previewUrl);attachment.previewUrl=null;};
  const removeUploaded=record=>{if(record?.id)api(`/api/v1/chat-images/${encodeURIComponent(record.id)}`,{method:'DELETE'}).catch(()=>{});};
  const render=()=>{
    if(main._chatImage!==attachment||main.querySelector('#chat-image-attachment')!==slot)return;
    const {status,record,file,error,previewUrl}=attachment;
    slot.innerHTML=status==='idle'?'':`<div class="chat-image-chip ${status==='failed'?'failed':''}">${previewUrl||record?.content_url?`<img src="${escapeHtml(previewUrl||record.content_url)}" alt="待发送的参考图片">`:icon('image',24)}<span><b>${escapeHtml(file?.name||record?.filename||'参考图片')}</b><small>${status==='uploading'?'正在上传…':status==='ready'?'已就绪':escapeHtml(error||'上传失败')}</small></span>${status==='failed'?'<button type="button" data-chat-image-retry aria-label="重试上传图片">重试</button>':''}<button type="button" data-chat-image-remove aria-label="删除参考图片">${icon('x',16)}</button></div>`;
    const send=main.querySelector('#composer-submit');if(send&&send.type==='submit')send.disabled=status==='uploading'||status==='failed';
    slot.querySelectorAll('button').forEach(button=>{button.disabled=attachment.locked;});
    slot.querySelector('[data-chat-image-remove]')?.addEventListener('click',()=>clear(true));
    slot.querySelector('[data-chat-image-retry]')?.addEventListener('click',()=>attachment.file&&selectFile(attachment.file));
    refreshIcons();
  };
  const clear=deleteUpload=>{attachment.revision++;if(deleteUpload)removeUploaded(attachment.record);releasePreview();Object.assign(attachment,{status:'idle',record:null,file:null,error:''});picker.value='';render();};
  const selectFile=async file=>{
    if(!file||attachment.locked||main.querySelector('#task-status'))return;
    const oldRecord=attachment.record;attachment.revision++;const revision=attachment.revision;removeUploaded(oldRecord);releasePreview();
    Object.assign(attachment,{file,record:null,error:'',status:'uploading'});
    if(!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>10*1024*1024||!file.size){attachment.status='failed';attachment.error='仅支持不超过 10MB 的 JPEG、PNG 或 WebP 图片。';render();return;}
    attachment.previewUrl=URL.createObjectURL(file);render();
    const form=new FormData();form.append('file',file,file.name||'参考图片');
    try{
      const record=await apiForm('/api/v1/chat-images',form);
      if(revision!==attachment.revision||main._chatImage!==attachment){removeUploaded(record);return;}
      attachment.record=record;attachment.status='ready';attachment.error='';releasePreview();render();
    }catch(error){if(revision!==attachment.revision)return;attachment.status='failed';attachment.error=error.message||'上传失败，请重试。';render();}
  };
  main.querySelector('#chat-image-add').onclick=()=>picker.click();
  picker.onchange=()=>{if(picker.files?.length)selectFile(picker.files[0]);picker.value='';};
  composer.addEventListener('dragover',event=>{if([...event.dataTransfer?.types||[]].includes('Files'))event.preventDefault();});
  composer.addEventListener('drop',event=>{const files=[...event.dataTransfer?.files||[]];if(!files.length)return;event.preventDefault();if(files.length>1){attachment.status='failed';attachment.error='每次最多上传一张参考图片。';render();return;}selectFile(files[0]);});
  composer.addEventListener('paste',event=>{const files=[...event.clipboardData?.files||[]];if(!files.length)return;event.preventDefault();if(files.length>1){attachment.status='failed';attachment.error='每次最多上传一张参考图片。';render();return;}selectFile(files[0]);});
  attachment.consume=()=>{attachment.locked=false;clear(false);};
  attachment.render=render;
  render();
}
async function refreshConversationRail(main,agentId,terminalTask={}){
  for(const path of ['/api/v1/conversations','/api/v1/generations','/api/v1/workspace'])pageCache.delete(path);
  if(!main||main.dataset?.page!==agentPage(agentId)||document.querySelector('#main')!==main)return;
  const rail=main.querySelector('.conversation-rail');if(!rail)return;
  const token={},identity=main.dataset.agentId;main._conversationRailRefresh=token;
  // Only a real terminal task may update the existing badge immediately.
  // An old generation thumbnail never turns a failed task into a success.
  if(terminalTask.id&&terminalTask.conversation_id&&['completed','failed','cancelled'].includes(terminalTask.status)){
    const row=[...rail.querySelectorAll('[data-select-agent-conversation]')].find(item=>item.dataset.selectAgentConversation===terminalTask.conversation_id),badge=row?.querySelector('.history-status');
    if(badge){badge.className=`history-status ${terminalTask.status}`;badge.textContent=taskStatusLabel(terminalTask.status);}
    const notice=main.querySelector('[data-conversation-result-status]');if(notice&&activeConversationId===terminalTask.conversation_id)notice.innerHTML=imageConversationStatusHtml({latest_status:terminalTask.status});
  }
  try{
    const items=await api('/api/v1/conversations');
    if(document.querySelector('#main')!==main||main.dataset.page!==agentPage(agentId)||main.dataset.agentId!==identity||main._conversationRailRefresh!==token)return;
    const scrollTop=rail.scrollTop;rail.innerHTML=agentConversationRailHtml(items.filter(item=>item.agent_id===identity),activeConversationId);rail.scrollTop=scrollTop;
    const notice=main.querySelector('[data-conversation-result-status]');if(notice)notice.innerHTML=imageConversationStatusHtml(items.find(item=>item.id===activeConversationId));
    bindConversationRail(main,agentId);refreshIcons();
  }catch{/* Keep the last authoritative badge if the list refresh is unavailable. */}
}
const agentGreetingHtml=agent=>{
  return `<div class="agent-greeting"><span class="agent-greeting-icon" aria-hidden="true">${icon(agent.icon||'bot',40)}</span><div class="agent-greeting-copy"><h1>你好，我是${escapeHtml(agent.name||'智能体')}</h1><p>${escapeHtml(agent.description||'')}</p><span class="agent-greeting-category">${escapeHtml(agentCategoryLabel(agent.category))}</span></div></div>`;
};
const taskReferencesHtml = references => {const knowledge=references?.knowledge||[];return knowledge.length?`<section class="result-references"><b>${icon('book-open',16)}参考资料</b><ul>${knowledge.map(item=>`<li>${escapeHtml(item.name)}</li>`).join('')}</ul></section>`:'';};
const messageGenerationHtml = generation => {
  if(!generation)return '';
  const url=escapeHtml(storageUrl(generation));
  return `<section class="message-generation"><div class="message-generation-label">${icon('image',16)}已生成图片</div>${image(generation,'本次生成图片')}<div class="message-generation-actions"><button class="button secondary" type="button" data-open-message-generation="${url}">${icon('expand',15)}查看大图</button><a class="button secondary" href="${url}" download>${icon('download',15)}下载</a></div></section>`;
};
const userMessageAttachmentHtml=attachments=>(attachments||[]).filter(item=>item?.type==='image'&&item.content_url).slice(0,1).map(item=>`<span class="chat-user-attachment"><img src="${escapeHtml(item.content_url)}" alt="${escapeHtml(item.filename||'参考图片')}" loading="lazy"><span>${escapeHtml(item.filename||'参考图片')}</span></span>`).join('');
const markdownInlineHtml = value => {
  let html=escapeHtml(String(value||''));
  html=html.replace(/`([^`]+)`/g,'<code>$1</code>');
  html=html.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/__([^_]+)__/g,'<strong>$1</strong>');
  html=html.replace(/\*([^*]+)\*/g,'<em>$1</em>').replace(/_([^_]+)_/g,'<em>$1</em>');
  return html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noreferrer">$1</a>');
};
function markdownTableCells(line){
  const value=String(line||'').trim().replace(/^\|/,'').replace(/\|$/,'');let cell='',cells=[];
  for(let index=0;index<value.length;index++){
    if(value[index]==='\\'&&value[index+1]==='|'){cell+='|';index++;}
    else if(value[index]==='|'){cells.push(cell.trim());cell='';}
    else cell+=value[index];
  }
  cells.push(cell.trim());return cells;
}
const markdownTableSeparator=line=>{const cells=markdownTableCells(line);return cells.length>1&&cells.every(cell=>/^:?-{3,}:?$/.test(cell));};
const markdownTableCellHtml=value=>markdownInlineHtml(value).replace(/&lt;br\s*\/?&gt;/gi,'<br>');
const markdownHtml = content => {
  const blocks=[],paragraph=[],lines=String(content||'').replace(/\r\n?/g,'\n').split('\n');let listType='',codeLines=null;
  const closeList=()=>{if(listType){blocks.push(`</${listType}>`);listType='';}};
  const flushParagraph=()=>{if(paragraph.length){blocks.push(`<p>${paragraph.splice(0).map(markdownInlineHtml).join('<br>')}</p>`);}};
  for(let index=0;index<lines.length;index++){const raw=lines[index],line=raw.trim(),heading=line.match(/^(#{1,3})\s+(.+)$/),ordered=line.match(/^\d+[.)]\s+(.+)$/),bullet=line.match(/^[-*+]\s+(.+)$/),quote=line.match(/^>\s?(.+)$/);
    if(codeLines){if(/^```/.test(line)){blocks.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`);codeLines=null;}else codeLines.push(raw);continue;}
    if(/^```/.test(line)){flushParagraph();closeList();codeLines=[];continue;}
    if(!line){flushParagraph();closeList();continue;}
    if(line.includes('|')&&index+1<lines.length&&markdownTableSeparator(lines[index+1])){
      flushParagraph();closeList();const headers=markdownTableCells(line);index++;
      const rows=[];while(index+1<lines.length&&lines[index+1].trim().includes('|'))rows.push(markdownTableCells(lines[++index]));
      blocks.push(`<div class="markdown-table-scroll"><table><thead><tr>${headers.map(value=>`<th>${markdownTableCellHtml(value)}</th>`).join('')}</tr></thead><tbody>${rows.map(cells=>`<tr>${headers.map((_,cellIndex)=>`<td>${markdownTableCellHtml(cells[cellIndex]||'')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);continue;
    }
    if(heading){flushParagraph();closeList();blocks.push(`<h${heading[1].length}>${markdownInlineHtml(heading[2])}</h${heading[1].length}>`);continue;}
    if(quote){flushParagraph();closeList();blocks.push(`<blockquote>${markdownInlineHtml(quote[1])}</blockquote>`);continue;}
    if(/^(---+|\*\*\*+|___+)$/.test(line)){flushParagraph();closeList();blocks.push('<hr>');continue;}
    if(ordered||bullet){flushParagraph();const nextType=ordered?'ol':'ul';if(listType!==nextType){closeList();blocks.push(`<${nextType}>`);listType=nextType;}blocks.push(`<li>${markdownInlineHtml((ordered||bullet)[1])}</li>`);continue;}
    closeList();paragraph.push(raw);
  }
  flushParagraph();closeList();if(codeLines)blocks.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`);return blocks.join('')||'<p>暂无内容。</p>';
};
const activityPlanResultIsValid = result => {
  if(!result||result.type!=='activity_plan'||result.version!=='1'||!result.data||typeof result.data!=='object'||Array.isArray(result.data))return false;
  const data=result.data,strings=['document_title','activity_theme','activity_time','activity_location','target_audience','invitation_copy'];
  return strings.every(key=>typeof data[key]==='string'&&data[key].trim())
    &&['promotion_channels','pending_items'].every(key=>Array.isArray(data[key])&&data[key].every(value=>typeof value==='string'))
    &&Array.isArray(data.activity_items)&&data.activity_items.every(item=>item&&['phase','name','description','image_requirement'].every(key=>typeof item[key]==='string'));
};
const activityPlanDocumentStorageKey=messageId=>me?.tenant_id&&me?.user_id?`${activityPlanDocumentStoragePrefix}${me.tenant_id}:${me.user_id}:${messageId}`:'';
function clearActivityPlanSessionDocuments(){
  try{for(let index=sessionStorage.length-1;index>=0;index--){const key=sessionStorage.key(index);if(key?.startsWith(activityPlanDocumentStoragePrefix))sessionStorage.removeItem(key);}}catch{}
}
function storedActivityPlanDocument(messageId){
  const key=activityPlanDocumentStorageKey(messageId);if(!key)return null;
  try{const value=JSON.parse(sessionStorage.getItem(key)||'null');return validDocumentResponse(value)?value:null;}catch{return null;}
}
function saveActivityPlanDocument(messageId,document){
  const key=activityPlanDocumentStorageKey(messageId);if(!key)return;
  try{if(document)sessionStorage.setItem(key,JSON.stringify(document));else sessionStorage.removeItem(key);}catch{}
}
function rememberActivityPlanResult(messageId,result){
  if(typeof messageId!=='string'||!messageId||!activityPlanResultIsValid(result))return null;
  const previous=activityPlanDocuments.get(messageId);
  if(previous&&JSON.stringify(previous.structuredResult.data)===JSON.stringify(result.data))return previous;
  const document=storedActivityPlanDocument(messageId),entry={structuredResult:result,state:document?'ready':'idle',document,error:''};activityPlanDocuments.set(messageId,entry);return entry;
}
const documentActionDetails=entry=>{const generating=entry.state==='generating';return {generating,label:generating?'正在生成 Word...':entry.state==='error'?'重试导出 Word':'导出 Word',iconName:generating?'loader-circle':'file-text'};};
function documentActionHtml(messageId){
  const entry=activityPlanDocuments.get(messageId);if(!entry||entry.state==='ready')return '';
  const {generating,label,iconName}=documentActionDetails(entry);
  return `<span class="document-action-slot" data-document-action="${escapeHtml(messageId)}"><button type="button" class="button secondary document-export-button" data-document-export="${escapeHtml(messageId)}" ${generating?'disabled aria-busy="true"':''}><i data-lucide="${iconName}" width="15" height="15" aria-hidden="true"></i>${label}</button></span>`;
}
function documentResultHtml(messageId){
  const entry=activityPlanDocuments.get(messageId);if(!entry)return '';
  if(entry.state==='generating')return '<div class="document-result document-result-generating" role="status" aria-live="polite"><i data-lucide="loader-circle" width="18" height="18" aria-hidden="true"></i>正在生成 Word 文档...</div>';
  if(entry.state==='error')return `<div class="document-result document-result-error" role="alert">${escapeHtml(entry.error||'Word 生成失败，请重试')}</div>`;
  if(entry.state!=='ready'||!entry.document)return '';
  const filename=escapeHtml(entry.document.filename),url=escapeHtml(entry.document.download_url),id=escapeHtml(messageId);
  return `<section class="document-result document-result-ready" aria-label="Word 文档已生成"><p class="document-result-success"><i data-lucide="circle-check" width="18" height="18" aria-hidden="true"></i>已整理成正式 Word 活动方案。</p><a class="document-download-link" href="${url}" download="${filename}" data-document-download="${id}">下载《${filename.replace(/\.docx$/i,'')}》</a><a class="document-file-card" href="${url}" download="${filename}" data-document-download="${id}" aria-label="下载 Word 文档 ${filename}"><span class="document-file-icon" aria-hidden="true"><i data-lucide="file-text" width="22" height="22"></i></span><span class="document-file-details"><strong>${filename}</strong><small>Word 文档 · DOCX</small></span><i data-lucide="download" width="18" height="18" aria-hidden="true"></i></a>${entry.error?`<p class="document-action-error" role="alert">${escapeHtml(entry.error)}</p>`:''}</section>`;
}
const documentResultSlotHtml=messageId=>activityPlanDocuments.has(messageId)?`<div class="document-result-slot" data-document-result-slot="${escapeHtml(messageId)}">${documentResultHtml(messageId)}</div>`:'';
const messageActionsHtml=(content,sourcePrompt='',messageId='')=>`${button('复制','copy','secondary',`data-copy-response="${escapeHtml(content)}"`)}${sourcePrompt?button('重新生成','refresh-cw','secondary',`data-regenerate="${escapeHtml(sourcePrompt)}"`):''}${documentActionHtml(messageId)}`;
const messageHtml = (item, sourcePrompt='',agentId='') => {
  const isUser=item.role==='user';
  if(!isUser&&agentId==='campaign-agent')rememberActivityPlanResult(item.id,item.structured_result);
  return `<article class="chat-message ${isUser?'chat-message-user':'chat-message-assistant'}" ${!isUser&&item.id?`data-assistant-message-id="${escapeHtml(item.id)}"`:''}><b>${isUser?'你':'智能体'}</b><div class="chat-message-content ${isUser?'chat-message-content-user':'chat-message-content-markdown'}">${isUser?`${userMessageAttachmentHtml(item.attachments)}${escapeHtml(item.content).replace(/\n/g,'<br>')}`:markdownHtml(item.content)}</div>${!isUser?`<div class="message-actions">${messageActionsHtml(item.content,sourcePrompt,agentId==='campaign-agent'?item.id:'')}</div>${agentId==='campaign-agent'?documentResultSlotHtml(item.id):''}${taskReferencesHtml(item.references)}`:''}${messageGenerationHtml(item.generation)}<small>${escapeHtml(formatHistoryTime(item.created_at))}</small></article>`;
};
function syncActivityPlanAction(main,messageId){
  const article=[...(main?.querySelectorAll?.('[data-assistant-message-id]')||[])].find(node=>node.dataset.assistantMessageId===messageId);
  const actions=article?.querySelector?.('.message-actions');if(!actions)return;
  const slot=[...(actions.querySelectorAll?.('[data-document-action]')||[])].find(node=>node.dataset.documentAction===messageId);
  const entry=activityPlanDocuments.get(messageId);
  if(slot)slot.remove();
  if(entry&&entry.state!=='ready')actions.insertAdjacentHTML('beforeend',documentActionHtml(messageId));
  const resultSlot=article.querySelector?.('[data-document-result-slot]')||article.querySelector?.('[data-stream-document-result]');
  if(resultSlot)resultSlot.innerHTML=documentResultHtml(messageId);
  else if(entry)actions.insertAdjacentHTML('afterend',documentResultSlotHtml(messageId));
  bindConversationActions(main);refreshIcons();
}
function documentExportError(error){
  if(error?.status===400)return '方案数据无效，无法生成 Word';
  if(error?.status===401)return customerErrorMessages.AUTH_REQUIRED;
  if(error?.status===403)return customerErrorMessages.FORBIDDEN;
  if(error?.status===404)return '文档不存在或已失效';
  if(error?.status===413)return '方案内容过大，暂无法生成';
  if(error?.status)return 'Word 生成失败，请重试';
  return '网络异常，请稍后重试';
}
function validDocumentResponse(document){
  if(!document||typeof document.document_id!=='string'||!document.document_id||typeof document.filename!=='string'||!document.filename.endsWith('.docx')||document.content_type!=='application/vnd.openxmlformats-officedocument.wordprocessingml.document'||typeof document.download_url!=='string')return false;
  try{const url=new URL(document.download_url,location.origin);return url.origin===location.origin&&url.pathname===`/api/v1/documents/activity-plan/${encodeURIComponent(document.document_id)}`;}catch{return false;}
}
async function downloadActivityPlanDocument(entry){
  const response=await fetch(entry.document.download_url,{credentials:'same-origin'});
  if(!response.ok){const error=Error('download failed');error.status=response.status;throw error;}
  if(response.headers?.get?.('content-type')?.split(';')[0]!==entry.document.content_type){const error=Error('invalid document content');error.status=500;throw error;}
  const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
  try{link.href=url;link.download=entry.document.filename;link.hidden=true;document.body.appendChild(link);link.click();}finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);}
}
async function handleActivityPlanDocumentAction(main,messageId){
  const entry=activityPlanDocuments.get(messageId);if(!entry||entry.state==='generating')return;
  if(entry.state==='ready'&&entry.document){
    try{await downloadActivityPlanDocument(entry);entry.error='';}
    catch(error){entry.error=documentExportError(error);if(error?.status===404){entry.state='error';entry.document=null;saveActivityPlanDocument(messageId,null);}}
    syncActivityPlanAction(main,messageId);return;
  }
  entry.state='generating';entry.error='';syncActivityPlanAction(main,messageId);
  try{
    const presentation=activityPlanBrandName&&activityPlanBrandName.length<=120?{brand_name:activityPlanBrandName}:{};
    const document=await api('/api/v1/documents/activity-plan',{method:'POST',body:JSON.stringify({content:entry.structuredResult.data,presentation})});
    if(!validDocumentResponse(document)){const error=Error('invalid document response');error.status=500;throw error;}
    entry.document={document_id:document.document_id,filename:document.filename,content_type:document.content_type,download_url:document.download_url};entry.state='ready';saveActivityPlanDocument(messageId,entry.document);
  }catch(error){entry.state='error';entry.error=documentExportError(error);}
  syncActivityPlanAction(main,messageId);
}
function hydrateHistoryActivityPlans(main,history,agentId){
  if(agentId!=='campaign-agent')return;
  for(const item of history){
    if(item.role!=='assistant'||!item.id||!item.task_id||activityPlanDocuments.has(item.id))continue;
    api(`/api/v1/tasks/${encodeURIComponent(item.task_id)}`).then(task=>{
      if(task.status!=='completed'||task.assistant_message_id!==item.id||!rememberActivityPlanResult(item.id,task.structured_result))return;
      syncActivityPlanAction(main,item.id);
    }).catch(()=>{});
  }
}
function bindConversationActions(main){
  main.querySelectorAll('[data-example-prompt]').forEach(node=>node.onclick=()=>{const input=main.querySelector('#prompt');if(!input)return;input.value=node.dataset.examplePrompt;input.focus();});
  main.querySelectorAll('[data-copy-response]').forEach(node=>node.onclick=async()=>{const original=node.innerHTML;try{await navigator.clipboard.writeText(node.dataset.copyResponse);node.textContent='已复制';setTimeout(()=>{if(node.isConnected)node.innerHTML=original;refreshIcons();},1600);}catch{node.textContent='复制失败';setTimeout(()=>{if(node.isConnected)node.innerHTML=original;refreshIcons();},1600);}});
  main.querySelectorAll('[data-regenerate]').forEach(node=>node.onclick=()=>{const input=main.querySelector('#prompt');if(!input)return;input.value=node.dataset.regenerate;input.focus();});
  main.querySelectorAll('[data-document-export]').forEach(node=>node.onclick=()=>handleActivityPlanDocumentAction(main,node.dataset.documentExport));
  main.querySelectorAll('[data-document-download]').forEach(node=>node.onclick=event=>{event.preventDefault();handleActivityPlanDocumentAction(main,node.dataset.documentDownload);});
  main.querySelectorAll('[data-open-message-generation]').forEach(node=>node.onclick=()=>openGenerationViewer(node.dataset.openMessageGeneration,'本次生成图片',node));
}
const safeActivityLabels=Object.freeze({queued:true,context_loading:true,enterprise_config_loading:true,knowledge_retrieving:true,asset_retrieving:true,tool_running:true,generating:true,full_plan_generating:true,structured_validating:true,semantic_validating:true,semantic_correcting:true,result_rendering:true,persisting:true,completed:true,failed:true,cancelled:true});
const activityDisplayLabel=(stage,status,planMode='generic')=>{
  const done=status==='completed';
  const labels={
    queued:['正在理解你的需求','需求已接收'],
    context_loading:['正在整理相关信息','相关信息已准备'],
    enterprise_config_loading:['正在读取品牌信息','品牌信息已读取'],
    knowledge_retrieving:['正在查找相关知识','相关知识已查找'],
    asset_retrieving:['正在查找可用素材','可用素材已查找'],
    tool_running:['正在处理所需资料','所需资料已处理'],
    generating:planMode==='full'?['正在生成活动方案','活动方案已生成']:['正在生成内容','内容已生成'],
    full_plan_generating:['正在生成活动方案','活动方案已生成'],
    structured_validating:['正在校验方案结构','方案结构已校验'],
    semantic_validating:['正在校验方案内容','方案内容已校验'],
    semantic_correcting:['正在优化待确认内容','待确认内容已优化'],
    result_rendering:['正在整理最终结果','最终结果已整理'],
    persisting:['正在整理最终结果','最终结果已整理'],
    completed:['已完成','已完成'],failed:['执行失败','执行失败'],cancelled:['已停止','已停止'],
  };
  return labels[stage]?.[done?1:0]||'';
};
const taskStageLabel=stage=>({queued:'正在理解你的需求',loading_context:'正在整理相关信息',starting_runtime:'正在处理你的请求',persisting_result:'正在整理最终结果',completed:'已完成',cancelling:'正在停止生成'})[stage]||'正在处理你的请求';
const STREAM_VISUAL_FRAME_MS=40,STREAM_SCROLL_FLUSH_MS=80,STREAM_FOLLOW_STOP_PX=96,STREAM_FOLLOW_RESUME_PX=32;
const ConversationRunPhase=Object.freeze({IDLE:'IDLE',SUBMITTING:'SUBMITTING',RUNNING:'RUNNING',STOPPING:'STOPPING',COMPLETED:'COMPLETED',ERROR:'ERROR',CANCELLED:'CANCELLED'});
const conversationRunTransitions=Object.freeze({IDLE:new Set(['SUBMITTING']),SUBMITTING:new Set(['RUNNING','ERROR']),RUNNING:new Set(['STOPPING','COMPLETED','ERROR','CANCELLED']),STOPPING:new Set(['RUNNING','COMPLETED','ERROR','CANCELLED']),COMPLETED:new Set(['IDLE']),ERROR:new Set(['IDLE']),CANCELLED:new Set(['IDLE'])});
const createConversationRunState=()=>({phase:ConversationRunPhase.IDLE,taskId:null,agentId:null,terminal:null,stopError:''});
function transitionConversationRun(run,next){
  if(!run||!conversationRunTransitions[run.phase]?.has(next))return false;
  run.phase=next;return true;
}
const createStreamingState = () => ({lastSequence:0,pending:new Map(),text:'',displayText:'',visualPending:'',status:'正在思考',networkComplete:false,networkCompletedAt:null,completed:false,error:null,renderedStable:null,renderedActive:null,lastRenderAt:0,lastScrollAt:0,follow:true,activities:[],activityEvents:[],activitySequence:0,currentActivity:null,conversationStartedAt:Date.now(),taskStartedAt:null,firstDeltaAt:null,completedAt:null,thinkingAutoCollapsed:false,thinkingTimer:null,planMode:'generic',visualGeneration:0,visualStopped:false,run:createConversationRunState()});
const streamingActionsHtml = (content,sourcePrompt='',messageId='') => messageActionsHtml(content,sourcePrompt,messageId);
const streamingThinkingHtml = () => `<details class="thinking-summary" data-thinking open><summary><span data-thinking-title>正在思考 · 不足 1 秒</span></summary><ol data-thinking-list hidden></ol></details>`;
const streamingMessageHtml = status => `<article class="chat-message chat-message-assistant streaming-message" id="task-status" aria-live="polite" aria-busy="true">${streamingThinkingHtml(status)}<div class="chat-message-content chat-message-content-markdown" data-stream-content><div data-stream-stable></div><div data-stream-active><p class="streaming-placeholder">${escapeHtml(status)}</p></div></div><div class="message-actions" data-stream-actions hidden></div><div data-stream-document-result></div><div class="streaming-status" data-stream-status>${icon('loader-circle',15)}<span>${escapeHtml(status)}</span></div></article>`;
const streamingFinalContentHtml = (content,sourcePrompt='',messageId='') => `<div class="chat-message-content chat-message-content-markdown" data-stream-content>${markdownHtml(content)}</div><div class="message-actions">${streamingActionsHtml(content,sourcePrompt,messageId)}</div>${documentResultSlotHtml(messageId)}`;
const streamingFailureHtml = (partial,message,diagnosticId='') => `<section class="task-failure streaming-failure" role="alert">${partial?`<div class="streaming-partial"><small>部分回复（未保存）</small><div class="chat-message-content chat-message-content-markdown">${markdownHtml(partial)}</div></div>`:''}<b>${icon('circle-alert')} ${escapeHtml(message||customerErrorMessages.TASK_FAILED)}</b><p>${partial?'生成已中断。你可以保留这段内容，或重新尝试。':'你可以把原需求放回输入框，检查后再次提交。'}</p>${diagnosticId?`<small>诊断 ID：${escapeHtml(diagnosticId)}</small>`:''}<button class="button secondary" type="button">${icon('refresh-cw')}重新尝试</button></section>`;
function acceptStreamingDelta(state,payload){
  const sequence=Number(payload?.sequence),text=typeof payload?.text==='string'?payload.text:'';
  if(!Number.isInteger(sequence)||sequence<1||!text||sequence<=state.lastSequence||state.pending.has(sequence))return false;
  state.pending.set(sequence,text);let changed=false;
  while(state.pending.has(state.lastSequence+1)){const next=state.pending.get(state.lastSequence+1);state.text+=next;state.visualPending+=next;state.pending.delete(state.lastSequence+1);state.lastSequence+=1;changed=true;}
  return changed;
}
function applyStreamingActivity(state,payload={}){
  const sequence=Number(payload.sequence),stage=String(payload.stage||''),status=String(payload.status||'');
  if(!Number.isInteger(sequence)||sequence<1||sequence<=state.activitySequence||!safeActivityLabels[stage]||!['started','completed'].includes(status))return false;
  const activity={sequence,stage,status,label:activityDisplayLabel(stage,status,state.planMode),created_at:typeof payload.created_at==='string'?payload.created_at:''};
  state.activitySequence=sequence;state.activityEvents.push(activity);
  const currentGroup=state.activities[state.activities.length-1];
  if(currentGroup?.stage===stage&&status==='completed'&&currentGroup.status==='started'){
    currentGroup.status='completed';currentGroup.sequence=sequence;currentGroup.label=activity.label;currentGroup.created_at=activity.created_at;
  }else if(currentGroup?.stage===stage&&status==='started'&&currentGroup.status==='started'){
    currentGroup.count=(currentGroup.count||1)+1;currentGroup.sequence=sequence;currentGroup.label=activity.label;currentGroup.created_at=activity.created_at;
  }else state.activities.push({...activity,count:status==='started'?1:0});
  state.currentActivity=activity;state.status=activity.label;return true;
}
function markFirstVisibleDelta(state){if(state.firstDeltaAt===null)state.firstDeltaAt=Date.now();}
function applyStreamingEvent(state,kind,payload={}){
  if(state.completed||state.networkComplete||state.error||state.run?.terminal==='cancelled')return false;
  if(kind==='activity')return applyStreamingActivity(state,payload);
  if(kind==='progress'){state.status=taskStageLabel(payload.stage);return true;}
  if(kind==='delta'){const changed=acceptStreamingDelta(state,payload);if(changed)markFirstVisibleDelta(state);return changed;}
  if(kind==='complete'){state.networkComplete=true;state.networkCompletedAt=Date.now();state.finalResponse=typeof payload.final_response==='string'?payload.final_response:state.text;return true;}
  if(kind==='error'){state.error=payload.message||customerErrorMessages.TASK_FAILED;state.diagnosticId=payload.diagnostic_id||'';return true;}
  return false;
}
function streamingScrollRoot(){
  const conversation=document.querySelector?.('#chat-body');
  return conversation&&typeof conversation.scrollTop==='number'&&typeof conversation.scrollHeight==='number'?conversation:document.scrollingElement||document.documentElement||document.body;
}
function streamingBottomDistance(root=streamingScrollRoot()){
  if(!root)return 0;
  const viewport=root.clientHeight||(typeof window!=='undefined'&&window.innerHeight)||0;
  return Math.max(0,root.scrollHeight-root.scrollTop-viewport);
}
function shouldAutoFollowStream(){return streamingBottomDistance()<=STREAM_FOLLOW_STOP_PX;}
function syncStreamingFollow(state){
  if(!state)return shouldAutoFollowStream();
  const distance=streamingBottomDistance();
  if(state.follow&&distance>STREAM_FOLLOW_STOP_PX)state.follow=false;
  else if(!state.follow&&distance<=STREAM_FOLLOW_RESUME_PX)state.follow=true;
  return state.follow;
}
function watchStreamingFollow(state){
  const root=streamingScrollRoot();
  if(!state||!root||state.followRoot===root||typeof root.addEventListener!=='function')return;
  state.followRoot=root;state.followListener=()=>syncStreamingFollow(state);root.addEventListener('scroll',state.followListener,{passive:true});syncStreamingFollow(state);
}
function stopWatchingStreamingFollow(state){
  if(state?.followRoot&&state.followListener&&typeof state.followRoot.removeEventListener==='function')state.followRoot.removeEventListener('scroll',state.followListener);
  if(state){state.followRoot=null;state.followListener=null;}
}
function scrollStreamToBottom(follow,state=null,force=false){
  if(!follow)return;
  const root=streamingScrollRoot(),now=Date.now();
  if(!root||state&&!force&&now-state.lastScrollAt<STREAM_SCROLL_FLUSH_MS)return;
  if(state)state.lastScrollAt=now;root.scrollTop=root.scrollHeight;
}
function streamingMarkdownParts(text){
  const value=String(text||'').replace(/\r\n?/g,'\n');let boundary=0,inCode=false,offset=0;
  for(const line of value.match(/[^\n]*(?:\n|$)/g)||[]){
    if(!line)continue;
    const end=offset+line.length,trimmed=line.trim();
    if(/^```/.test(trimmed)){inCode=!inCode;if(!inCode)boundary=end;}
    else if(!inCode&&!trimmed)boundary=end;
    offset=end;
  }
  const active=value.slice(boundary);
  return {stable:value.slice(0,boundary),active,inCode:/^```/.test(active.trimStart())};
}
const streamingCaretHtml = () => '<span class="streaming-caret" data-stream-caret aria-label="正在生成"></span>';
function streamingActiveHtml(text,status,hasVisibleText=Boolean(text)){
  const parts=streamingMarkdownParts(text);
  if(!parts.active)return hasVisibleText?streamingCaretHtml():`<p class="streaming-placeholder">${escapeHtml(status)}</p>`;
  if(parts.inCode){const newline=parts.active.indexOf('\n'),code=newline<0?'':parts.active.slice(newline+1);return `<pre class="streaming-active-code"><code data-stream-active-text>${escapeHtml(code)}</code></pre>${streamingCaretHtml()}`;}
  return `<span class="streaming-active-text" data-stream-active-text>${escapeHtml(parts.active)}</span>${streamingCaretHtml()}`;
}
function clearStreamingVisualWork(state,{discard=false}={}){
  if(state?.renderTimer)clearTimeout(state.renderTimer);
  if(state?.renderFrame&&typeof cancelAnimationFrame==='function')cancelAnimationFrame(state.renderFrame);
  if(state){state.visualGeneration=Number(state.visualGeneration||0)+1;state.renderTimer=null;state.renderFrame=null;state.renderPending=false;if(discard){state.visualPending='';state.pending?.clear?.();state.visualStopped=true;}}
  stopWatchingStreamingFollow(state);
}
function stopThinkingClock(state){if(state?.thinkingTimer)clearInterval(state.thinkingTimer);if(state)state.thinkingTimer=null;}
function startThinkingClock(node,state){
  if(!state||state.thinkingTimer||typeof setInterval!=='function')return;
  state.thinkingTimer=setInterval(()=>{if(state.completed||state.error||state.run?.terminal){stopThinkingClock(state);return;}renderThinkingSummary(node,state);},1000);
}
function composerRunControls(main){
  return {input:main?.querySelector?.('#prompt'),submit:main?.querySelector?.('#composer-submit'),feedback:main?.querySelector?.('#composer-run-feedback')};
}
function updateComposerRunState(main,state){
  const run=state?.run;if(!run)return;
  const {input,submit,feedback}=composerRunControls(main),stopping=run.phase===ConversationRunPhase.STOPPING,running=run.phase===ConversationRunPhase.RUNNING,draining=state.networkComplete&&!state.completed;
  if(input)input.disabled=run.phase===ConversationRunPhase.SUBMITTING||stopping||draining;
  if(submit){
    submit.disabled=run.phase===ConversationRunPhase.SUBMITTING||stopping||draining||(!running&&['uploading','failed'].includes(main?._chatImage?.status));
    submit.type=running||stopping?'button':'submit';
    submit.classList?.toggle?.('composer-stop-button',running||stopping);
    submit.setAttribute?.('aria-label',stopping?'正在停止生成':running?'停止生成':draining?'正在显示回复':'发送消息');
    submit.setAttribute?.('title',stopping?'正在停止生成':running?'停止生成':draining?'正在显示回复':'发送');
    submit.innerHTML=running||stopping?`<span class="stop-generation-glyph" aria-hidden="true"></span><span class="sr-only">${stopping?'正在停止生成':'停止生成'}</span>`:icon('send');
    submit.onclick=running?()=>requestStopGeneration(main,state):null;
  }
  const imageAdd=main?.querySelector?.('#chat-image-add');if(imageAdd)imageAdd.disabled=run.phase!==ConversationRunPhase.IDLE;
  if(feedback)feedback.textContent=run.stopError||'';
  refreshIcons();
}
function beginConversationRun(state,{taskId=null,agentId=null,conversationId=activeConversationId,phase=ConversationRunPhase.SUBMITTING,restored=false}={}){
  state.run=state.run||createConversationRunState();state.run.taskId=taskId;state.run.agentId=agentId;state.run.terminal=null;state.run.stopError='';
  if(agentId==='image-agent')state.run.conversationId=conversationId;
  if(state.run.phase!==phase&&!transitionConversationRun(state.run,phase)&&restored)state.run.phase=phase;
  return state.run;
}
function closeStreamingSource(state){
  state?.streamSource?.close?.();if(state)state.streamSource=null;
}
function settleConversationRun(main,state,terminal){
  const run=state?.run;if(!run)return;
  run.terminal=terminal;
  if(run.agentId==='image-agent')refreshConversationRail(main,run.agentId,{id:run.taskId,conversation_id:run.conversationId,status:terminal==='error'?'failed':terminal});
  const phase=ConversationRunPhase[String(terminal||'').toUpperCase()];
  if(phase&&run.phase!==phase)transitionConversationRun(run,phase);
  updateComposerRunState(main,state);
  if(run.phase!==ConversationRunPhase.IDLE)transitionConversationRun(run,ConversationRunPhase.IDLE);
  run.taskId=null;run.stopError='';updateComposerRunState(main,state);
}
async function requestStopGeneration(main,state){
  const run=state?.run;
  if(!run||run.phase!==ConversationRunPhase.RUNNING||!run.taskId)return;
  transitionConversationRun(run,ConversationRunPhase.STOPPING);updateComposerRunState(main,state);
  try{
    const task=await api(`/api/v1/tasks/${encodeURIComponent(run.taskId)}/cancel`,{method:'POST'});
    if(run.terminal)return;
    if(task.status==='completed')return finishStreamTask(document.querySelector('#task-status'),state,task,run.agentId,main,state.sourcePrompt||'');
    if(task.status==='cancelled')return cancelStreamingMessage(document.querySelector('#task-status'),state,main);
    if(task.status==='cancelling')return;
    transitionConversationRun(run,ConversationRunPhase.RUNNING);run.stopError='停止请求未被确认，请重试。';updateComposerRunState(main,state);
  }catch(error){
    if(run.terminal)return;
    transitionConversationRun(run,ConversationRunPhase.RUNNING);run.stopError=error.message||'停止请求失败，请重试。';updateComposerRunState(main,state);
  }
}
function setStreamingTaskStartedAt(state,task={}){
  const startedAt=Date.parse(task.started_at||'');
  if(Number.isFinite(startedAt)&&startedAt>0)state.taskStartedAt=startedAt;
  if(state.restored&&task.created_at){
    const value=String(task.created_at).replace(' ','T'),createdAt=Date.parse(/(?:Z|[+-]\d\d:\d\d)$/.test(value)?value:`${value}Z`);
    if(Number.isFinite(createdAt)&&createdAt>0)state.conversationStartedAt=createdAt;
  }
}
function formatThinkingDuration(milliseconds){
  const seconds=Math.floor(Math.max(0,milliseconds)/1000);
  if(seconds<1)return '不足 1 秒';
  if(seconds<60)return `${seconds} 秒`;
  return `${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;
}
function thinkingDuration(state){return formatThinkingDuration((state.completedAt??Date.now())-(state.conversationStartedAt??Date.now()));}
function renderThinkingSummary(node,state){
  const details=node?.querySelector?.('[data-thinking]'),title=details?.querySelector?.('[data-thinking-title]'),list=details?.querySelector?.('[data-thinking-list]');if(!details)return;
  const done=Boolean(state.completed||state.error||state.run?.terminal==='cancelled');
  details.classList?.toggle?.('is-thinking',!done);
  if(title)title.textContent=state.completed?`思考了 ${thinkingDuration(state)}`:state.run?.terminal==='cancelled'?'已停止生成':state.error?'生成中断':`正在思考 · ${thinkingDuration(state)}`;
  if(list){const cancelled=state.run?.terminal==='cancelled',markup=[...state.activities.map(item=>{const latest=item.sequence===state.currentActivity?.sequence,className=item.status==='completed'?'is-complete':cancelled?'is-stopped':!latest?'is-previous':done?'is-current':'is-active',label=!latest&&item.status==='started'?item.label.replace(/^正在/,''):item.label,count=item.count>1?` · ${item.count} 次`:'';return `<li class="thinking-activity ${className}">${escapeHtml(label)}${escapeHtml(count)}</li>`;}),cancelled?'<li class="thinking-terminal">已停止生成</li>':''].join('');list.hidden=!markup;if(list.innerHTML!==markup)list.innerHTML=markup;}
  if((state.firstDeltaAt!==null||done)&&!state.thinkingAutoCollapsed){details.open=false;state.thinkingAutoCollapsed=true;}
}
function consumeStreamingVisualBuffer(state){
  if(!state.visualPending)return false;
  const count=Math.max(24,Math.min(160,Math.ceil(state.visualPending.length/3))),chunk=state.visualPending.slice(0,count);
  state.displayText+=chunk;state.visualPending=state.visualPending.slice(chunk.length);return true;
}
function renderStreamingActive(active,state,parts,activeKey){
  if(state.renderedActive===activeKey)return;
  const kind=parts.inCode?'code':'text',textNode=typeof active.querySelector==='function'?active.querySelector('[data-stream-active-text]'):null;
  if(textNode&&active.dataset?.streamKind===kind){textNode.textContent=parts.inCode?(parts.active.slice(parts.active.indexOf('\n')+1)||''):parts.active;}
  else {active.innerHTML=streamingActiveHtml(parts.active,state.status,Boolean(state.displayText));if(active.dataset)active.dataset.streamKind=kind;}
  state.renderedActive=activeKey;
}
function renderStreamingMessage(node,state){
  if(!node||state.error)return;
  consumeStreamingVisualBuffer(state);watchStreamingFollow(state);const follow=syncStreamingFollow(state),content=typeof node.querySelector==='function'?node.querySelector('[data-stream-content]'):null,status=typeof node.querySelector==='function'?node.querySelector('[data-stream-status]'):null,parts=streamingMarkdownParts(state.displayText);
  const stable=content&&typeof content.querySelector==='function'?content.querySelector('[data-stream-stable]'):null,active=content&&typeof content.querySelector==='function'?content.querySelector('[data-stream-active]'):null;
  const activeKey=parts.active||!state.displayText?`${parts.active}\u0000${state.status}`:parts.active;
  if(stable&&active){if(state.renderedStable!==parts.stable){stable.innerHTML=parts.stable?markdownHtml(parts.stable):'';state.renderedStable=parts.stable;}renderStreamingActive(active,state,parts,activeKey);}
  else if(content)content.innerHTML=streamingActiveHtml(state.displayText,state.status);
  if(status){status.hidden=Boolean(state.displayText);if(!status.hidden){const label=status.querySelector('span');if(label)label.textContent=state.status;}}
  renderThinkingSummary(node,state);state.lastRenderAt=Date.now();scrollStreamToBottom(follow,state);
}
function canRenderStreamingVisual(state,generation){return Boolean(state&&generation===state.visualGeneration&&!state.visualStopped&&!state.completed&&!state.error&&!state.run?.terminal);}
function scheduleStreamingRender(node,state){
  if(state.renderPending||!canRenderStreamingVisual(state,state.visualGeneration))return;
  const generation=state.visualGeneration,flush=()=>{if(!canRenderStreamingVisual(state,generation))return;state.renderPending=false;state.renderTimer=null;state.renderFrame=null;renderStreamingMessage(node,state);if(state.visualPending)scheduleStreamingRender(node,state);else if(state.networkComplete)state.onVisualDrained?.();},queueFrame=()=>{if(!canRenderStreamingVisual(state,generation))return;if(typeof requestAnimationFrame==='function')state.renderFrame=requestAnimationFrame(flush);else state.renderTimer=setTimeout(flush,0);},wait=Math.max(0,STREAM_VISUAL_FRAME_MS-(Date.now()-state.lastRenderAt));
  state.renderPending=true;if(wait)state.renderTimer=setTimeout(typeof requestAnimationFrame==='function'?queueFrame:flush,wait);else queueFrame();
}
function completeStreamingMessage(node,state,sourcePrompt,main){
  if(!node)return;
  clearStreamingVisualWork(state,{discard:true});stopThinkingClock(state);closeStreamingSource(state);state.completed=true;state.displayText=state.text;const follow=syncStreamingFollow(state),finalResponse=state.finalResponse??state.text,content=typeof node.querySelector==='function'?node.querySelector('[data-stream-content]'):null,actions=typeof node.querySelector==='function'?node.querySelector('[data-stream-actions]'):null,status=typeof node.querySelector==='function'?node.querySelector('[data-stream-status]'):null;
  if(content&&actions){
    content.querySelector?.('[data-stream-caret]')?.remove?.();content.innerHTML=markdownHtml(finalResponse);
    if(status)status.hidden=true;actions.hidden=false;actions.innerHTML=streamingActionsHtml(finalResponse,sourcePrompt,state.assistantMessageId);
    const result=node.querySelector?.('[data-stream-document-result]');if(result)result.innerHTML=documentResultSlotHtml(state.assistantMessageId);
  }else node.innerHTML=streamingFinalContentHtml(finalResponse,sourcePrompt,state.assistantMessageId);
  if(state.assistantMessageId&&node.dataset)node.dataset.assistantMessageId=state.assistantMessageId;
  mountWechatArticle(main,node,finalResponse,state.assistantMessageId);
  if(state.generation&&!node.querySelector?.('.message-generation'))node.insertAdjacentHTML?.('beforeend',messageGenerationHtml(state.generation));
  node.removeAttribute('aria-busy');node.removeAttribute('id');node.classList.remove('streaming-message');bindConversationActions(main);refreshIcons();scrollStreamToBottom(follow,state,true);state.completedAt??=Date.now();renderThinkingSummary(node,state);stopWatchingStreamingFollow(state);
}
function cancelStreamingMessage(node,state,main){
  if(!node||state?.run?.terminal==='cancelled')return;
  clearStreamingVisualWork(state,{discard:true});stopThinkingClock(state);closeStreamingSource(state);state.text=state.displayText;state.run=state.run||createConversationRunState();state.run.terminal='cancelled';renderThinkingSummary(node,state);
  const content=node.querySelector?.('[data-stream-content]'),active=content?.querySelector?.('[data-stream-active]'),caret=content?.querySelector?.('[data-stream-caret]'),status=node.querySelector?.('[data-stream-status]');
  caret?.remove?.();
  content?.querySelectorAll?.('[data-stream-caret],.streaming-caret')?.forEach?.(item=>item.remove?.());
  if(!state.displayText&&active)active.innerHTML='';
  if(status){status.hidden=false;status.classList?.add?.('stream-cancelled');status.querySelector?.('svg')?.remove?.();status.querySelector?.('i')?.remove?.();const label=status.querySelector?.('span');if(label)label.textContent='已停止生成 · 部分回复未保存';}
  node.removeAttribute?.('aria-busy');node.removeAttribute?.('id');node.classList?.add?.('stream-cancelled');
  settleConversationRun(main,state,'cancelled');
}
function replaceTaskFailure(node,message,retryText,diagnosticId='',main=null){
  if(!node)return;
  const partial=node._streamState?.displayText||node._streamState?.text||'';clearStreamingVisualWork(node._streamState,{discard:true});stopThinkingClock(node._streamState);
  const card=document.createElement('section');card.innerHTML=streamingFailureHtml(partial,message,diagnosticId);
  const failure=card.firstElementChild;node.replaceWith(failure);failure.querySelector('button').onclick=()=>{const input=document.querySelector('#prompt');if(!input)return;input.value=retryText;input.focus();};refreshIcons();
  if(node._streamState)settleConversationRun(main,node._streamState,'error');
}
async function agentWorkspaceV2(main, agentId) {
  activeAgentId=agentId;
  const data=await api('/api/v1/workspace');activityPlanBrandName=typeof data.brand_name==='string'?data.brand_name:'';
  let agent=agentPage(agentId).startsWith('agent:')?await api(`/api/v1/agents/${encodeURIComponent(agentId)}`).catch(()=>null):(data.agents||[]).find(x=>x.id===agentId);
  if(main.dataset.page!==agentPage(agentId))return;
  if((!agent||!agent.enabled)&&!activeConversationId){main.innerHTML='<section class="empty-state">该智能体暂未启用。</section>';return;}
  const allConversations=await api('/api/v1/conversations');
  let detail=activeConversationId?await api(`/api/v1/conversations/${activeConversationId}`).catch(()=>null):null;
  if(main.dataset.page!==agentPage(agentId))return;
  const internalId=agent?.id||(detail&&(detail.agent?.slug===agentId||detail.agent_id===agentId)?detail.agent_id:agentId);
  main.dataset.agentId=internalId;
  const conversations=allConversations.filter(x=>x.agent_id===internalId);
  if(activeConversationId&&detail?.agent_id!==internalId){activeConversationId=null;detail=null;}
  const canRun=agent?.enabled===true;
  if(!canRun&&!detail){main.innerHTML='<section class="empty-state">该智能体暂未启用。</section>';return;}
  agent={...agent,...detail?.agent,enabled:canRun};
  const history=detail?.messages||[],attachedIds=new Set(history.map(item=>item.generation?.id).filter(Boolean));
  const remainingImages=(detail?.generations||[]).filter(item=>!attachedIds.has(item.id));
  const historicalImages=remainingImages.length?`<section class="conversation-artifacts"><h3>${icon('images',18)}本项目生成图片 <span>${remainingImages.length}</span></h3><div>${remainingImages.map(item=>image(item,detail?.project?.name||'历史生成图片')).join('')}</div></section>`:'';
  const historyMarkup=history.length?`${history.map((item,index)=>messageHtml(item,item.role==='assistant'?[...history.slice(0,index)].reverse().find(previous=>previous.role==='user')?.content||'':'',agentId)).join('')}${historicalImages}`:agentGreetingHtml(agent);
  main.innerHTML=`<div class="creation-layout chatgpt-conversation-layout"><aside class="conversation-rail">${agentConversationRailHtml(conversations,activeConversationId)}</aside><section class="creation-main"><div id="chat-body" class="chat-body" aria-live="polite">${agentId==='image-agent'?`<div class="conversation-result-status" data-conversation-result-status>${imageConversationStatusHtml(conversations.find(item=>item.id===activeConversationId))}</div>`:''}${historyMarkup}</div><form class="composer" id="composer"><textarea id="prompt" required placeholder="${escapeHtml(agentPlaceholder(agentId))}"></textarea><div><span>${detail?'继续在当前项目里提出修改，系统会保留上文。':'可按需使用企业资料与品牌素材。'}</span><p class="composer-run-feedback" id="composer-run-feedback" role="status"></p><button class="button primary" id="composer-submit" type="submit" aria-label="发送消息" title="发送">${icon('send')}</button></div></form></section><aside class="creation-right"><section class="panel"><h3>当前智能体</h3><p><b>${escapeHtml(agent.name)}</b></p><p class="muted">${escapeHtml(agent.description||'协助完成业务创作。')}</p></section><section class="panel"><h3>${detail?'当前项目':'本次执行'}</h3><p><b>${escapeHtml(detail?.project?.name||'新建项目')}</b></p><p class="muted">${detail?`最近保存：${escapeHtml(formatHistoryTime(detail.tasks?.at(-1)?.completed_at||detail.created_at))}`:'直接描述你的业务需求即可。'}</p></section></aside></div>`;
  if(agentId==='image-agent'){
    const composer=main.querySelector('#composer');
    composer.insertAdjacentHTML('afterbegin',chatImageComposerFields());
    composer.querySelector(':scope > div:last-of-type').insertAdjacentHTML('afterbegin',chatImageComposerButton());
    bindChatImageComposer(main);
  }
  main._wechatArticleContext={agent,route:agentId};
  if(agent.slug==='wechat-official-account-writing')for(const item of history){
    if(item.role!=='assistant')continue;
    const article=[...main.querySelectorAll('[data-assistant-message-id]')].find(node=>node.dataset.assistantMessageId===item.id);
    if(article)mountWechatArticle(main,article,item.content,item.id);
  }
  renderWechatAgentSetupHint(main,agent);bindConversationRail(main,agentId); main.querySelector('#composer').onsubmit=event=>submitAgentTaskV2(event,agentId,main); bindConversationActions(main); hydrateHistoryActivityPlans(main,history,agentId); restoreActiveConversationTask(main,agentId,detail); refreshIcons();
  if(agent.placeholder)main.querySelector('#prompt').placeholder=agent.placeholder;
  if(!canRun){main.querySelector('#new-chat').disabled=true;main.querySelector('#composer').innerHTML='<p role="status">智能体已停用，历史项目只读；不能创建任务或继续执行。</p>';main.querySelector('#composer').onsubmit=event=>event.preventDefault();}
  bindNavigation();
}
async function submitAgentTaskV2(event,agentId,main){
  event.preventDefault();const input=document.querySelector('#prompt'),text=input.value.trim(),attachment=agentId==='image-agent'?main._chatImage:null;if(!text||document.querySelector('#task-status')||attachment&&attachment.status!=='idle'&&attachment.status!=='ready')return;
  const submittedAt=Date.now();if(attachment?.record){attachment.locked=true;attachment.render();}
  input.value='';const body=document.querySelector('#chat-body'),follow=shouldAutoFollowStream();
  body.insertAdjacentHTML('beforeend',`<article class="chat-message chat-message-user"><b>你</b><div class="chat-message-content chat-message-content-user">${userMessageAttachmentHtml(attachment?.record?[attachment.record]:[])}${escapeHtml(text)}</div></article>${streamingMessageHtml('正在思考')}`);
  const node=document.querySelector('#task-status'),state=createStreamingState();state.conversationStartedAt=submittedAt;state.follow=follow;state.sourcePrompt=text;beginConversationRun(state,{agentId,phase:ConversationRunPhase.SUBMITTING});node._streamState=state;updateComposerRunState(main,state);watchStreamingFollow(state);startThinkingClock(node,state);refreshIcons();scrollStreamToBottom(follow,state,true);
  const payload={message:text};if(activeConversationId)payload.conversation_id=activeConversationId;if(attachment?.record)payload.attachments=[{type:'image',id:attachment.record.id}];
  try{const task=await api(`/api/v1/agents/${agentId}/runs`,{method:'POST',body:JSON.stringify(payload)});if(attachment?.record)attachment.consume();setStreamingTaskStartedAt(state,task);beginConversationRun(state,{taskId:task.id,agentId,conversationId:task.conversation_id,phase:ConversationRunPhase.RUNNING});updateComposerRunState(main,state);streamTask(task.id,agentId,main,text,node,state);}catch(error){if(attachment){attachment.locked=false;attachment.render();}replaceTaskFailure(node,error.message,text,error.requestId,main);}
}
function streamPayload(event){try{return JSON.parse(event.data||'{}');}catch{return {};}}
function finishStreamTask(node,state,done,agentId,main,retryText){
  if(state?.run?.terminal==='cancelled'||state?.networkComplete||state?.completed)return;
  if(done.status==='cancelled'){cancelStreamingMessage(node,state,main);return;}
  if(done.status!=='completed'){applyStreamingEvent(state,'error',done);replaceTaskFailure(node,state.error,retryText,state.diagnosticId,main);return;}
  state.assistantMessageId=typeof done.assistant_message_id==='string'?done.assistant_message_id:'';
  if(agentId==='image-agent'&&state.run?.taskId&&done.generation?.task_id===state.run.taskId)state.generation=done.generation;
  if(agentId==='campaign-agent'&&rememberActivityPlanResult(state.assistantMessageId,done.structured_result)){
    state.planMode='full';
    for(const activity of state.activities)activity.label=activityDisplayLabel(activity.stage,activity.status,state.planMode);
  }
  state.run=state.run||createConversationRunState();if(agentId==='image-agent')state.run.conversationId=done.conversation_id||state.run.conversationId;applyStreamingEvent(state,'complete',done);transitionConversationRun(state.run,ConversationRunPhase.COMPLETED);updateComposerRunState(main,state);activeConversationId=done.conversation_id||activeConversationId;
  pageCache.delete('/api/v1/conversations');pageCache.delete('/api/v1/generations');pageCache.delete('/api/v1/workspace');
  state.onVisualDrained=()=>{if(state.completed||state.run?.terminal==='cancelled'||state.visualPending)return;state.onVisualDrained=null;completeStreamingMessage(node,state,retryText,main);settleConversationRun(main,state,'completed');};
  if(state.visualPending){if(!state.renderPending)scheduleStreamingRender(node,state);}else state.onVisualDrained();
}
function streamTask(taskId,agentId,main,retryText,node,state){
  const source=new EventSource(`/api/v1/tasks/${taskId}/events`);state.streamSource=source;let settled=false;
  const finish=handler=>{if(settled||state?.run?.terminal)return;settled=true;source.close();handler();};
  const fallback=()=>{if(settled||state?.run?.terminal)return;settled=true;closeStreamingSource(state);pollAgentTaskV2(taskId,agentId,main,retryText,node,state);};
  source.addEventListener('activity',event=>{if(!settled&&applyStreamingEvent(state,'activity',streamPayload(event)))scheduleStreamingRender(node,state);});
  source.addEventListener('progress',event=>{if(!settled&&applyStreamingEvent(state,'progress',streamPayload(event)))scheduleStreamingRender(node,state);});
  source.addEventListener('delta',event=>{if(!settled&&applyStreamingEvent(state,'delta',streamPayload(event)))scheduleStreamingRender(node,state);});
  source.addEventListener('complete',event=>finish(()=>finishStreamTask(node,state,streamPayload(event),agentId,main,retryText)));
  source.addEventListener('cancelled',event=>finish(()=>cancelStreamingMessage(node,state,main,streamPayload(event))));
  source.addEventListener('error',event=>{const failure=streamPayload(event);if(failure.message){finish(()=>{applyStreamingEvent(state,'error',failure);replaceTaskFailure(node,state.error,retryText,state.diagnosticId,main);});return;}fallback();});
  source.onerror=fallback;
}
async function pollAgentTaskV2(taskId,agentId,main,retryText,node=document.querySelector('#task-status'),state=node?node._streamState:createStreamingState()){
  try{const task=await api(`/api/v1/tasks/${taskId}`);setStreamingTaskStartedAt(state,task);if(task.current_activity&&applyStreamingEvent(state,'activity',task.current_activity))scheduleStreamingRender(node,state);if(task.status==='completed'||task.status==='cancelled'){finishStreamTask(node,state,task,agentId,main,retryText);return;}if(task.status==='cancelling'){if(state.run?.phase===ConversationRunPhase.RUNNING)transitionConversationRun(state.run,ConversationRunPhase.STOPPING);updateComposerRunState(main,state);setTimeout(()=>pollAgentTaskV2(taskId,agentId,main,retryText,node,state),1000);return;}if(task.status==='failed'){applyStreamingEvent(state,'error',task);replaceTaskFailure(node,state.error||task.user_message,retryText,state.diagnosticId||task.diagnostic_id,main);return;}setTimeout(()=>pollAgentTaskV2(taskId,agentId,main,retryText,node,state),1000);}catch{setTimeout(()=>pollAgentTaskV2(taskId,agentId,main,retryText,node,state),2500);}
}
function activeConversationTask(detail){
  return [...(detail?.tasks||[])].reverse().find(task=>['queued','running','cancelling'].includes(task.status))||null;
}
function restoreActiveConversationTask(main,agentId,detail){
  const task=activeConversationTask(detail);if(!task)return;
  const body=main.querySelector?.('#chat-body');if(!body||main.querySelector?.('#task-status'))return;
  body.insertAdjacentHTML?.('beforeend',streamingMessageHtml(task.stage==='cancelling'?'正在停止生成':'正在恢复生成'));
  const node=main.querySelector?.('#task-status')||document.querySelector('#task-status');if(!node)return;
  const state=createStreamingState();state.restored=true;state.sourcePrompt=task.input_text||'';state.status=task.stage==='cancelling'?'正在停止生成':'正在恢复生成';state.follow=shouldAutoFollowStream();setStreamingTaskStartedAt(state,task);beginConversationRun(state,{taskId:task.id,agentId,phase:task.status==='cancelling'?ConversationRunPhase.STOPPING:ConversationRunPhase.RUNNING,restored:true});node._streamState=state;updateComposerRunState(main,state);watchStreamingFollow(state);startThinkingClock(node,state);api(`/api/v1/tasks/${task.id}`).then(current=>{if(node._streamState!==state||state.run?.terminal)return;setStreamingTaskStartedAt(state,current);if(current.current_activity&&applyStreamingEvent(state,'activity',current.current_activity))scheduleStreamingRender(node,state);if(['completed','cancelled','failed'].includes(current.status))finishStreamTask(node,state,current,agentId,main,state.sourcePrompt);}).catch(()=>{});streamTask(task.id,agentId,main,state.sourcePrompt,node,state);
}

async function agentWorkspace(main, agentId) {
  activeAgentId=agentId; const data=await api('/api/v1/workspace'); const agent=(data.agents||[]).find(x=>x.id===agentId); if(!agent||!agent.enabled){main.innerHTML='<section class="empty-state">该智能体暂未启用。</section>';return;}
  const conversations=(await api('/api/v1/conversations')).filter(x=>x.agent_id===agentId);
  main.innerHTML=`<div class="creation-layout"><aside class="conversation-rail"><button class="button primary full" id="new-chat">${icon('plus')}新建对话</button><h3>对话记录</h3>${conversations.map(x=>`<button class="conversation-row ${activeConversationId===x.id?'selected':''}" data-select-agent-conversation="${x.id}">${icon('file-pen-line')}<span><b>${escapeHtml(x.title)}</b><small>${escapeHtml(String(x.created_at||'').slice(0,16))}</small></span></button>`).join('')||'<div class="empty-state">暂无历史会话</div>'}</aside><section class="creation-main"><div class="agent-heading"><div>${icon(agent.icon||'bot',28)}<div><h1>${activeConversationId?'继续'+escapeHtml(agent.name):escapeHtml(agent.name)}</h1><p>${escapeHtml(agent.description)} · ${agent.credit_cost} 积分 / 次</p></div></div></div><div id="chat-body" class="chat-body"><div class="assistant-intro">${icon('bot',24)}<div><b>你好，我是${escapeHtml(agent.name)}</b><p>我会在企业品牌规范、知识库和素材范围内完成本次创作。</p></div></div></div><form class="composer" id="composer"><textarea id="prompt" required placeholder="${escapeHtml(agentPlaceholder(agentId))}"></textarea><div><span>将自动使用企业品牌、知识库与素材库</span><button class="button primary" type="submit">${icon('send')}发送</button></div></form></section><aside class="creation-right"><section class="panel"><h3>当前智能体</h3><p><b>${escapeHtml(agent.name)}</b></p><p class="muted">Skill：${escapeHtml(Object.keys(JSON.parse(agent.skill_manifest||'{}')).join(' / '))}</p></section><section class="panel"><h3>能力边界</h3><p>${agent.allows_image_generation?'可按需求生成图片。':'默认仅输出文本；明确需要视觉时请使用图片生成智能体。'}</p></section></aside></div>`;
  main.querySelector('#new-chat').onclick=()=>{activeConversationId=null;agentWorkspace(main,agentId);}; main.querySelectorAll('[data-select-agent-conversation]').forEach(node=>node.onclick=()=>{activeConversationId=node.dataset.selectAgentConversation;agentWorkspace(main,agentId);}); main.querySelector('#composer').onsubmit=event=>submitAgentTask(event,agentId); refreshIcons();
}
async function submitAgentTask(event,agentId){event.preventDefault();const text=document.querySelector('#prompt').value.trim();if(!text)return;const body=document.querySelector('#chat-body');const payload={message:text};if(activeConversationId)payload.conversation_id=activeConversationId;body.insertAdjacentHTML('beforeend',`<div class="user-message">${escapeHtml(text)}</div><section class="task-card" id="task-status"><div class="task-progress"><span class="done">${icon('check')}</span><b>正在理解需求</b><i></i><span class="spin">${icon('loader-circle')}</span><b>正在读取企业资料</b><i></i><span>${icon('sparkles')}</span><b>正在创作内容</b></div><p>正在结合企业资料生成，请稍等。</p></section>`);refreshIcons();try{const task=await api(`/api/v1/agents/${agentId}/runs`,{method:'POST',body:JSON.stringify(payload)});pollAgentTask(task.id,agentId);}catch(error){document.querySelector('#task-status').outerHTML=`<p class="error">${escapeHtml(error.message)}</p>`;}}
async function pollAgentTask(id,agentId){try{const task=await api(`/api/v1/tasks/${id}`),card=document.querySelector('#task-status');if(!card)return;if(task.status==='completed'){activeConversationId=task.conversation_id||activeConversationId;const isImage=agentId==='image-agent';const items=isImage?await api('/api/v1/generations'):[];const g=items.find(x=>x.task_id===id);card.outerHTML=`<section class="generation-result"><b>${icon('check-circle')} 创作完成</b><p>${escapeHtml(task.final_response||'内容已生成')}</p>${g?`${image(g,'本次生成图片')}<div><button class="button secondary" data-open-generation="${storageUrl(g)}">${icon('search')}查看大图</button><a class="button secondary" href="${storageUrl(g)}" download>${icon('download')}下载</a></div>`:''}</section>`;document.querySelector('[data-open-generation]')?.addEventListener('click',e=>openGenerationViewer(e.currentTarget.dataset.openGeneration,'生成图片'));refreshIcons();return;}if(task.status==='failed'){card.outerHTML=`<p class="error">${escapeHtml(task.user_message||'任务执行失败')}</p>`;return;}card.querySelector('p').textContent=stageMap[task.stage]||'正在创作，请稍等。';setTimeout(()=>pollAgentTask(id,agentId),1000);}catch{setTimeout(()=>pollAgentTask(id,agentId),2500);}}

async function imageAgent(main) {
  const conversations = await api('/api/v1/conversations');
  main.innerHTML = `<div class="creation-layout"><aside class="conversation-rail"><button class="button primary full" id="new-chat">${icon('plus')}新建对话</button><h3>对话记录</h3>${conversations.map(x=>`<button class="conversation-row ${activeConversationId===x.id?'selected':''}" data-select-conversation="${x.id}">${icon('file-pen-line')}<span><b>${escapeHtml(x.title)}</b><small>${escapeHtml(String(x.created_at||'').slice(0,16))}</small></span></button>`).join('')||'<div class="empty-state">暂无历史会话</div>'}</aside><section class="creation-main"><div class="agent-heading"><div>${icon('wand-sparkles',28)}<div><h1>${activeConversationId?'继续图片创作':'图片智能体'}</h1><p>基于企业资料创建可信的视觉内容</p></div></div>${button('导出','download')}</div><div id="chat-body" class="chat-body"><div class="assistant-intro">${icon('bot',24)}<div><b>你好，我是图片生成智能体</b><p>告诉我想制作的海报、配图或视觉内容，我会结合企业品牌与素材完成创作。</p></div></div></div><form class="composer" id="composer"><textarea id="prompt" required placeholder="描述你想创作的图片，例如：帮我做一张秋季招生海报"></textarea><div><span>将自动使用企业品牌、知识库与素材库</span><button class="button primary" type="submit">${icon('send')}发送</button></div></form></section><aside class="creation-right"><section class="panel"><h3>当前对话</h3>${conversations.slice(0,5).map(x=>`<button class="conversation-row" data-select-conversation="${x.id}">${icon('file-pen-line')}<span><b>${escapeHtml(x.title)}</b></span></button>`).join('')||'<p class="muted">新建对话后将显示在这里。</p>'}</section><section class="panel"><div class="section-title"><h3>相关素材</h3><a data-go="assets">查看全部</a></div><div id="related-assets" class="asset-mini"><span class="muted">正在加载素材…</span></div></section><section class="panel"><h3>快捷工具</h3><div class="quick-grid compact"><button disabled>${icon('image-plus')}文生图</button><button disabled>${icon('palette')}图像编辑</button><button disabled>${icon('crop')}尺寸转换</button></div></section></aside></div>`;
  const assetRoot = document.querySelector('#related-assets'); api('/api/v1/assets').then(items => { assetRoot.innerHTML = items.slice(0,6).map(x => `<article>${x.url?`<img src="${escapeHtml(x.url)}" alt="${escapeHtml(x.name)}">`:icon('image',28)}<small>${escapeHtml(x.name)}</small></article>`).join('') || '<span class="muted">暂无企业素材</span>'; refreshIcons(); });
  document.querySelector('#new-chat').onclick = () => { activeConversationId=null; imageAgent(main); };
  document.querySelectorAll('[data-select-conversation]').forEach(x=>x.onclick=()=>{activeConversationId=x.dataset.selectConversation; imageAgent(main);});
  document.querySelector('#composer').onsubmit = submitTask; bindNavigation(); refreshIcons();
}

async function submitTask(event) { event.preventDefault(); const text=document.querySelector('#prompt').value.trim(); if(!text)return; const body=document.querySelector('#chat-body'); const payload={message:text};if(activeConversationId)payload.conversation_id=activeConversationId; body.insertAdjacentHTML('beforeend',`<div class="user-message">${escapeHtml(text)}</div><section class="task-card" id="task-status"><div class="task-progress"><span class="done">${icon('check')}</span><b>正在理解需求</b><i></i><span class="spin">${icon('loader-circle')}</span><b>正在读取企业资料</b><i></i><span>${icon('image')}</span><b>正在生成图片</b></div><p>正在结合企业资料生成，请稍等。</p></section>`);refreshIcons(); try{const task=await api('/api/v1/agents/image-agent/runs',{method:'POST',body:JSON.stringify(payload)}); pollTask(task.id);}catch(error){document.querySelector('#task-status').outerHTML=`<p class="error">${escapeHtml(error.message)}</p>`;}}
async function pollTask(id){try{const task=await api(`/api/v1/tasks/${id}`),card=document.querySelector('#task-status');if(!card)return;if(task.status==='completed'){activeConversationId=task.conversation_id||activeConversationId;const items=await api('/api/v1/generations'),g=items.find(x=>x.task_id===id);card.outerHTML=`<section class="generation-result"><b>${icon('check-circle')} 生成完成</b><p>${escapeHtml(task.final_response||'图片已生成')}</p>${g?`${image(g,'本次生成图片')}<div>${button('查看大图','search','secondary',`onclick="window.open('${storageUrl(g)}','_blank')"`)}<a class="button secondary" href="${storageUrl(g)}" download>${icon('download')}下载</a>${button('保存到企业素材库','folder-plus','primary',`data-save="${g.id}"`)}</div>`:''}</section>`;document.querySelector('[data-save]')?.addEventListener('click',async e=>{await api(`/api/v1/generations/${e.currentTarget.dataset.save}/save-to-assets`,{method:'POST',body:JSON.stringify({name:'AI 生成图片'})});e.currentTarget.disabled=true;e.currentTarget.textContent='已保存';});refreshIcons();return;}if(task.status==='failed'){card.outerHTML=`<p class="error">${escapeHtml(task.user_message||'任务生成失败')}</p>`;return;}card.querySelector('p').textContent=stageMap[task.stage]||'正在生成，请稍等。';setTimeout(()=>pollTask(id),1000);}catch{setTimeout(()=>pollTask(id),2500);}}

async function generations(main){const items=await api('/api/v1/generations');if(main.dataset.page!=='generations')return;main.innerHTML=`${header('我的生成','按创作项目管理全部图片，随时返回对应历史记录。','folder-open')}<div class="toolbar"><span class="muted">共 ${items.length} 条生成记录</span>${button('开始新的创作','plus','primary','data-go="image"')}</div><div class="filter-row"><label>${icon('search')}<input id="generation-filter" aria-label="搜索生成记录" placeholder="搜索项目或生成需求…"></label></div><div class="gallery" id="generation-gallery">${items.map(x=>`<article class="generation-tile" data-generation-search="${escapeHtml(`${x.project?.name||''} ${x.prompt||''}`.toLowerCase())}">${image(x,x.project?.name||'生成图片')}<div><span class="generation-project-type">${escapeHtml(x.project?.type||'图片生成项目')}</span><h3>${escapeHtml(x.project?.name||'已删除的历史项目')}</h3><p>${escapeHtml(x.prompt||'暂无需求摘要')}</p><small>${escapeHtml(formatHistoryTime(x.created_at))}</small><div>${x.project_available?`<button class="button secondary" data-open-project="${escapeHtml(x.conversation_id)}" data-agent-id="${escapeHtml(x.agent?.id||'image-agent')}">${icon('history')}查看历史</button>`:'<span class="muted">历史项目已删除</span>'}<a class="button secondary" href="${escapeHtml(storageUrl(x))}" download>${icon('download')}下载</a><button class="icon-button" aria-label="删除生成记录" title="删除生成记录" data-delete-generation="${escapeHtml(x.id)}">${icon('trash-2')}</button></div></div></article>`).join('')||'<div class="empty-state">还没有图片生成，开始第一次 AI 创作吧。</div>'}</div>`;document.querySelectorAll('[data-delete-generation]').forEach(x=>x.onclick=async()=>{if(!confirm('确定删除这条生成记录吗？'))return;await api(`/api/v1/generations/${x.dataset.deleteGeneration}`,{method:'DELETE'});generations(main);});document.querySelectorAll('[data-open-project]').forEach(node=>node.onclick=()=>{activeConversationId=node.dataset.openProject;activeAgentId=node.dataset.agentId;navigate(agentPage(activeAgentId));});document.querySelector('#generation-filter').oninput=e=>document.querySelectorAll('.generation-tile').forEach(x=>x.hidden=!x.dataset.generationSearch.includes(e.target.value.trim().toLowerCase()));bindNavigation();refreshIcons();}

async function knowledge(main){const items=await api('/api/v1/knowledge/files');main.innerHTML=`${header('企业知识库','上传企业文档，构建专属知识库，让 AI 更懂你的业务','book-open')}<div class="content-split"><section><div class="upload-zone"><div>${icon('cloud-upload',42)}<h3>上传知识文件</h3><p>当前版本支持文本知识录入；文件上传与解析待后端能力接入。</p>${button('录入文本知识','plus','primary','id="add-knowledge"')}</div></div><section class="panel table-panel"><div class="section-title"><h2>文件列表 <small>（共 ${items.length} 个）</small></h2><label class="small-search">${icon('search')}<input id="knowledge-filter" placeholder="搜索文件名"></label></div><div class="data-table"><div class="thead"><span>文件名称</span><span>状态</span><span>上传时间</span><span>操作</span></div>${items.map(x=>`<div class="trow"><span>${icon('file-text')}<b>${escapeHtml(x.name)}</b></span><span class="status ready">${icon('check-circle',15)}${statusText(x.status)}</span><span>${escapeHtml(x.created_at||'')}</span><button class="link-button danger" data-delete-knowledge="${x.id}">删除</button></div>`).join('')||'<div class="empty-state">暂无知识文件</div>'}</div></section></section><aside class="right-rail"><section class="panel"><h3>知识库概览</h3><div class="stat-grid"><div><b>${items.length}</b><small>文件总数</small></div><div><b>${items.filter(x=>x.status==='ready').length}</b><small>可用文件</small></div></div></section><section class="panel note"><h3>${icon('lightbulb')} 小提示</h3><p>上传结构清晰的企业资料，可提升 AI 的理解效果。</p></section></aside></div>`;document.querySelector('#add-knowledge').onclick=()=>textKnowledge(main);document.querySelectorAll('[data-delete-knowledge]').forEach(x=>x.onclick=async()=>{await api(`/api/v1/knowledge/files/${x.dataset.deleteKnowledge}`,{method:'DELETE'});knowledge(main);});refreshIcons();}
function textKnowledge(main){main.querySelector('.upload-zone').innerHTML=`<form id="knowledge-form"><label>标题<input name="name" required placeholder="例如：课程介绍"></label><label>知识内容<textarea name="content" required placeholder="输入企业知识内容…"></textarea></label><button class="button primary" type="submit">提交并处理</button></form>`;main.querySelector('#knowledge-form').onsubmit=async e=>{e.preventDefault();await api('/api/v1/knowledge/text',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});knowledge(main);};}
const statusText=s=>({uploaded:'上传完成',parsing:'解析中',indexing:'索引中',ready:'可用',failed:'解析失败'})[s]||s;

const assetTags=value=>{let tags=value;if(typeof tags==='string'){try{tags=JSON.parse(tags);}catch{tags=tags.split(/[、,]/);}}return (Array.isArray(tags)?tags:[tags]).map(tag=>String(tag||'').trim()).filter(Boolean).slice(0,4);};
const assetTagMarkup=value=>{const tags=assetTags(value);return tags.length?`<span class="asset-tags" aria-label="素材标签">${tags.map(tag=>`<span class="asset-tag">${escapeHtml(tag)}</span>`).join('')}</span>`:'<span class="asset-tags"><span class="asset-tag">企业素材</span></span>';};
async function assets(main){const items=await api('/api/v1/assets');if(main.dataset.page!=='assets')return;main.innerHTML=`${header('企业素材库','统一管理品牌素材、教学资源和各类创意内容，支持团队协作与高效复用。','image')}<div class="toolbar"><span class="muted">共 ${items.length} 份素材</span>${button('添加素材','plus','primary','id="add-asset"')}</div><div class="filter-row"><label>${icon('search')}<input id="asset-filter" aria-label="搜索素材" placeholder="搜索素材名称、标签或描述…"></label><select id="asset-type" aria-label="按素材类型筛选"><option value="">全部类型</option><option value="logo">Logo</option><option value="poster_reference">海报参考</option><option value="product_image">产品图片</option><option value="teacher_image">人物素材</option></select></div><div class="gallery asset-gallery" id="asset-gallery">${items.map(x=>`<article class="generation-tile asset-tile">${x.url?`<a class="media-thumb" href="${escapeHtml(x.url)}" target="_blank" rel="noopener" title="预览 ${escapeHtml(x.name)}"><img loading="lazy" src="${escapeHtml(x.url)}" alt="${escapeHtml(x.name)}"></a>`:`<div class="media-thumb media-empty" aria-label="没有可预览文件">${icon('image')}</div>`}<div><h3>${escapeHtml(x.name)}</h3><p class="asset-meta"><span>${escapeHtml(typeText(x.asset_type))}</span><time>${escapeHtml(formatHistoryTime(x.created_at)||'上传时间未知')}</time></p>${assetTagMarkup(x.tags)}<p>${escapeHtml(x.description||'暂无描述')}</p><div>${x.url?`<button class="button secondary" data-preview-url="${escapeHtml(x.url)}" aria-label="预览 ${escapeHtml(x.name)}" title="预览 ${escapeHtml(x.name)}">${icon('eye')}预览</button>`:''}<button class="icon-button" data-delete-asset="${x.id}" aria-label="删除 ${escapeHtml(x.name)}" title="删除 ${escapeHtml(x.name)}">${icon('trash-2')}</button></div></div></article>`).join('')||'<div class="empty-state">还没有素材。添加第一份企业素材，供团队创作时使用。</div>'}</div>`;document.querySelector('#add-asset').onclick=()=>assetForm(main);document.querySelectorAll('[data-delete-asset]').forEach(x=>x.onclick=async()=>{if(!confirm('确定删除这份素材吗？'))return;await api(`/api/v1/assets/${x.dataset.deleteAsset}`,{method:'DELETE'});assets(main);});document.querySelectorAll('[data-preview-url]').forEach(x=>x.onclick=()=>window.open(x.dataset.previewUrl,'_blank'));document.querySelector('#asset-filter').oninput=filterAssets;document.querySelector('#asset-type').onchange=filterAssets;refreshIcons();}
function filterAssets(){const q=document.querySelector('#asset-filter').value.toLowerCase(),type=document.querySelector('#asset-type').value;document.querySelectorAll('.asset-tile').forEach(x=>{x.hidden=!(x.textContent.toLowerCase().includes(q)&&(!type||x.textContent.includes(typeText(type))));});}
function assetForm(main){main.querySelector('.toolbar').insertAdjacentHTML('afterend',`<form class="inline-form" id="asset-form"><label>名称<input name="name" required></label><label>类型<select name="asset_type"><option value="poster_reference">海报参考</option><option value="logo">Logo</option><option value="product_image">产品图片</option><option value="teacher_image">人物素材</option><option value="other">其他</option></select></label><label>URL<input name="url" type="url" required></label><label>描述<input name="description"></label><button class="button primary">保存素材</button></form>`);main.querySelector('#asset-form').onsubmit=async e=>{e.preventDefault();const p=Object.fromEntries(new FormData(e.target));p.tags=[];await api('/api/v1/assets',{method:'POST',body:JSON.stringify(p)});assets(main);};}
const typeText=x=>({logo:'Logo',poster_reference:'海报参考',product_image:'产品图片',teacher_image:'人物素材',other:'其他'})[x]||x;

const brandLogoPath='/api/v1/enterprise-config/brand-logo';
const brandLogoErrorMessages=Object.freeze({
  LOGO_FILE_REQUIRED:'请选择 Logo 文件',
  LOGO_FORMAT_NOT_SUPPORTED:'仅支持 PNG 格式 Logo',
  LOGO_INVALID_PNG:'图片文件无效或已损坏，请重新上传',
  LOGO_TRANSPARENCY_REQUIRED:'检测到当前 Logo 没有透明背景，请上传透明背景 PNG。',
  LOGO_TOO_LARGE:'Logo 文件不能超过 5MB',
  LOGO_DIMENSIONS_TOO_LARGE:'Logo 图片尺寸过大，请使用不超过 4096 × 4096 的图片',
  LOGO_STORAGE_FAILED:'Logo 保存失败，请稍后重试',
  LOGO_CONFIG_UPDATE_FAILED:'Logo 保存失败，请稍后重试',
  LOGO_PERMISSION_DENIED:'当前账号无权修改企业 Logo',
  FORBIDDEN:'当前账号无权修改企业 Logo',
});
function brandLogoErrorMessage(payload={}){
  const code=[payload.error_code,payload.detail,payload.user_message,payload.code].find(value=>Object.hasOwn(brandLogoErrorMessages,value));
  return brandLogoErrorMessages[code]||'Logo 上传失败，请稍后重试';
}
const brandLogoMetadata=value=>value&&value.download_url===brandLogoPath&&value.content_type==='image/png'&&typeof value.filename==='string'?value:null;
function brandLogoCardHtml(state){
  const logo=brandLogoMetadata(state.logo),uploading=state.phase==='uploading',canEdit=state.canEdit;
  const preview=logo?`<div class="brand-logo-preview"><img src="${brandLogoPath}?v=${state.previewVersion}" alt="当前企业品牌 Logo"></div><div class="brand-logo-file"><strong>${escapeHtml(logo.filename)}</strong>${Number.isInteger(logo.width)&&Number.isInteger(logo.height)?`<span>${logo.width} × ${logo.height}px</span>`:''}</div>`:`<div class="brand-logo-preview brand-logo-empty">${icon('image',28)}<span>尚未上传品牌 Logo</span></div><p class="brand-logo-missing">品牌 Logo 尚未配置。当前 Word 导出仍可使用；配置 Logo 后可用于后续品牌模板。</p>`;
  return `<h2>${icon('image')} 品牌 Logo</h2><p class="brand-logo-description">上传后可用于后续品牌 Word 模板的页眉和水印。</p>${preview}<p class="brand-logo-requirements">仅支持透明背景 PNG · 最大 5MB · 最大 4096 × 4096 · 建议长边 512–2048px</p>${canEdit?`<input id="brand-logo-file" type="file" accept="image/png,.png" aria-label="选择透明背景 PNG Logo" hidden ${uploading?'disabled':''}><button class="button secondary brand-logo-upload-button" type="button" id="brand-logo-upload" ${uploading?'disabled aria-busy="true"':''}>${icon(uploading?'loader-circle':'upload',16)}${uploading?'正在上传...':logo?'更换 Logo':'上传 Logo'}</button>`:''}${state.phase==='success'?'<p class="brand-logo-feedback" role="status">上传成功，当前预览已更新。</p>':''}${state.phase==='error'?`<p class="brand-logo-feedback brand-logo-feedback-error" role="alert">${escapeHtml(state.error)}</p>`:''}`;
}
function renderBrandLogoCard(main,state){
  const card=main.querySelector?.('#brand-logo-card');if(!card)return;
  card.innerHTML=brandLogoCardHtml(state);
  const picker=card.querySelector?.('#brand-logo-file'),button=card.querySelector?.('#brand-logo-upload');
  if(button&&picker){button.onclick=()=>picker.click();picker.onchange=()=>{const file=picker.files?.[0];if(file)uploadBrandLogo(main,state,file);};}
  refreshIcons();
}
async function uploadBrandLogo(main,state,file){
  if(!state.canEdit||state.phase==='uploading')return;
  if(!file){state.phase='error';state.error=brandLogoErrorMessages.LOGO_FILE_REQUIRED;renderBrandLogoCard(main,state);return;}
  state.phase='uploading';state.error='';renderBrandLogoCard(main,state);
  try{
    const form=new FormData();form.append('file',file,file.name);
    const response=await fetch(brandLogoPath,{method:'POST',credentials:'same-origin',body:form});
    let payload={};try{payload=await response.json();}catch{}
    if(!response.ok)throw payload;
    pageCache.delete('/api/v1/enterprise-config');
    const config=await api('/api/v1/enterprise-config'),saved=brandLogoMetadata(config.brand_logo);
    if(!saved)throw {code:'LOGO_REFRESH_FAILED'};
    state.logo=saved;state.previewVersion=Date.now();state.phase='success';
  }catch(error){state.phase='error';state.error=error?.code==='LOGO_REFRESH_FAILED'?'Logo 已上传，但未能读取最新配置，请刷新页面确认。':brandLogoErrorMessage(error);}
  renderBrandLogoCard(main,state);
}
async function enterprise(main){
  const data=await api('/api/v1/enterprise-config');
  main.innerHTML=`${header('企业配置','设置企业的品牌信息与内容规则，AI 将基于这些配置创作内容。','settings')}<div class="config-layout"><form id="config-form"><section class="panel form-section"><h2>${icon('building-2')} 企业信息</h2><div class="form-grid"><label>企业名称<input name="brand_name" value="${escapeHtml(data.brand_name||'')}"></label><label>企业简介<textarea name="company_intro" placeholder="介绍企业的定位与业务范围">${escapeHtml(data.company_intro||'')}</textarea></label></div></section><section class="panel form-section"><h2>${icon('palette')} 品牌设置</h2><div class="form-grid"><label>品牌主色<input name="primary_color" value="${escapeHtml(data.primary_color||'#2563EB')}"></label><label>品牌辅色<input name="secondary_color" value="${escapeHtml(data.secondary_color||'#F59E0B')}"></label><label>品牌 Slogan<input name="slogan" value="${escapeHtml(data.slogan||'')}"></label><label>品牌标签<input name="brand_tags" value="${escapeHtml((data.brand_tags||[]).join('、'))}"></label></div></section><section class="panel form-section brand-logo-card" id="brand-logo-card" aria-label="品牌 Logo"></section><section class="panel form-section"><h2>${icon('shield-check')} 内容规则</h2><div class="form-grid"><label>目标用户<input name="target_users" value="${escapeHtml(data.target_users||'')}"></label><label>必须遵循的规则<textarea name="required_rules">${escapeHtml((data.required_rules||[]).join('\n'))}</textarea></label><label>禁止内容<textarea name="forbidden_claims">${escapeHtml((data.forbidden_claims||[]).join('\n'))}</textarea></label><label>自定义规则<textarea name="custom_rules">${escapeHtml((data.custom_rules||[]).join('\n'))}</textarea></label></div></section><div class="form-actions">${button('取消','x')}<button class="button primary" type="submit">${icon('save')}保存配置</button></div></form><aside class="panel brand-preview"><h3>品牌效果预览</h3><div class="brand-preview-art" style="--brand:${escapeHtml(data.primary_color||'#2563EB')};--accent:${escapeHtml(data.secondary_color||'#F59E0B')}"><b>${escapeHtml(data.brand_name||'企业品牌')}</b><span>${escapeHtml(data.slogan||'让 AI 成为创造力')}</span></div><h3>品牌信息</h3><p><b>主色</b> ${escapeHtml(data.primary_color||'未设置')}</p><p><b>辅色</b> ${escapeHtml(data.secondary_color||'未设置')}</p><p><b>规则</b> 后续 AI 创作将自动遵循</p></aside></div>`;
  renderBrandLogoCard(main,{logo:data.brand_logo,phase:'idle',error:'',previewVersion:Date.now(),canEdit:me?.role==='enterprise_admin'});
  main.querySelector('#config-form').onsubmit=async e=>{e.preventDefault();const p=Object.fromEntries(new FormData(e.target));['brand_tags','required_rules','forbidden_claims','custom_rules'].forEach(k=>p[k]=p[k].split(/[\n、,]/).map(x=>x.trim()).filter(Boolean));await api('/api/v1/enterprise-config',{method:'PUT',body:JSON.stringify({payload:p})});alert('企业配置已保存');};refreshIcons();
}

async function members(main){
  const items=await api('/api/v1/members');if(main.dataset.page!=='members')return;
  const admins=items.filter(item=>item.role==='enterprise_admin').length,enabled=items.filter(item=>item.status==='enabled').length;
  main.innerHTML=`${header('成员管理','创建首客成员、分配基础角色并管理账号状态。','users')}<p class="page-feedback" role="status" aria-live="polite"></p><div class="stat-row"><article><b>${items.length}</b><span>成员总数</span></article><article><b>${admins}</b><span>企业管理员</span></article><article><b>${enabled}</b><span>已启用</span></article></div><section class="panel member-panel"><div class="section-title"><div><h2>企业成员</h2><p>临时密码只在创建或重置后显示一次。</p></div><button class="button primary" id="add-member">${icon('user-plus')}新增成员</button></div><div class="member-list">${items.map(item=>`<article class="member-row" data-member-id="${escapeHtml(item.id)}"><div><b>${escapeHtml(item.display_name)}</b><small>${escapeHtml(item.email)}</small><time>${escapeHtml(formatHistoryTime(item.created_at))}</time></div><span class="status ${item.status==='enabled'?'ready':'disabled'}">${icon(item.status==='enabled'?'check-circle':'circle-off',15)}${item.status==='enabled'?'已启用':'已停用'}</span><label><span class="sr-only">${escapeHtml(item.display_name)}的角色</span><select data-member-role ${item.id===me.user_id?'disabled':''}><option value="member" ${item.role==='member'?'selected':''}>成员</option><option value="enterprise_admin" ${item.role==='enterprise_admin'?'selected':''}>企业管理员</option></select></label><div><button class="button secondary" data-save-member-role ${item.id===me.user_id?'disabled':''}>保存角色</button><button class="button secondary" data-member-status="${item.status==='enabled'?'disabled':'enabled'}" ${item.id===me.user_id?'disabled':''}>${item.status==='enabled'?'停用':'启用'}</button><button class="button secondary" data-reset-member ${item.id===me.user_id?'disabled':''}>重置密码</button></div></article>`).join('')||'<div class="empty-state">还没有成员，创建第一位成员开始协作。</div>'}</div></section>`;
  const feedback=main.querySelector('.page-feedback'),refresh=()=>members(main);
  main.querySelector('#add-member').onclick=()=>openMemberCreator(main,refresh);
  main.querySelectorAll('[data-save-member-role]').forEach(button=>button.onclick=async()=>{const row=button.closest('[data-member-id]'),role=row.querySelector('[data-member-role]').value;button.disabled=true;feedback.textContent='';try{const result=await api(`/api/v1/members/${encodeURIComponent(row.dataset.memberId)}/role`,{method:'PUT',body:JSON.stringify({role})});await refresh();main.querySelector('.page-feedback').textContent=result.user_message;}catch(error){feedback.textContent=error.message;button.disabled=false;}});
  main.querySelectorAll('[data-member-status]').forEach(button=>button.onclick=async()=>{const row=button.closest('[data-member-id]'),status=button.dataset.memberStatus,verb=status==='disabled'?'停用':'启用';if(!confirm(`确定${verb}该成员吗？`))return;button.disabled=true;feedback.textContent='';try{const result=await api(`/api/v1/members/${encodeURIComponent(row.dataset.memberId)}/status`,{method:'PUT',body:JSON.stringify({status})});await refresh();main.querySelector('.page-feedback').textContent=result.user_message;}catch(error){feedback.textContent=error.message;button.disabled=false;}});
  main.querySelectorAll('[data-reset-member]').forEach(button=>button.onclick=async()=>{if(!confirm('确定为该成员生成新的临时密码吗？原密码将立即失效。'))return;button.disabled=true;feedback.textContent='';try{const result=await api(`/api/v1/members/${encodeURIComponent(button.closest('[data-member-id]').dataset.memberId)}/reset-password`,{method:'POST'});showTemporaryCredential('密码已重置',result);button.disabled=false;}catch(error){feedback.textContent=error.message;button.disabled=false;}});
  refreshIcons();
}
function openMemberCreator(main,refresh){
  app.insertAdjacentHTML('beforeend',`<div class="dialog-backdrop" id="member-creator" role="presentation"><form class="profile-editor" aria-labelledby="member-creator-title"><button type="button" class="dialog-close" aria-label="关闭新增成员">${icon('x')}</button><h2 id="member-creator-title">新增成员</h2><p>系统将生成只显示一次的临时密码。</p><label>姓名<input name="display_name" required maxlength="80"></label><label>邮箱<input name="email" type="email" required maxlength="254"></label><label>角色<select name="role"><option value="member">成员</option><option value="enterprise_admin">企业管理员</option></select></label><p class="form-error" role="alert"></p><div class="editor-actions"><button type="button" class="button secondary" data-close-editor>取消</button><button class="button primary" type="submit">创建成员</button></div></form></div>`);
  const dialog=document.querySelector('#member-creator'),form=dialog.querySelector('form'),close=bindModalDialog(dialog);
  dialog.querySelector('[data-close-editor]').onclick=close;
  form.onsubmit=async event=>{event.preventDefault();const submit=form.querySelector('[type="submit"]'),error=form.querySelector('.form-error');submit.disabled=true;error.textContent='';try{const result=await api('/api/v1/members',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(form)))});close();await refresh();showTemporaryCredential('成员已创建',result);}catch(err){error.textContent=err.message;submit.disabled=false;}};
  refreshIcons();
}
function showTemporaryCredential(title,result){
  app.insertAdjacentHTML('beforeend',`<div class="dialog-backdrop" id="temporary-credential" role="presentation"><section class="help-dialog credential-dialog" role="dialog" aria-modal="true" aria-labelledby="temporary-credential-title"><button type="button" class="dialog-close" aria-label="关闭临时密码">${icon('x')}</button><h2 id="temporary-credential-title">${escapeHtml(title)}</h2><p>${escapeHtml(result.user_message)}</p><code>${escapeHtml(result.temporary_password)}</code><button class="button primary" data-copy-credential>${icon('copy')}复制临时密码</button></section></div>`);
  const dialog=document.querySelector('#temporary-credential');bindModalDialog(dialog);dialog.querySelector('[data-copy-credential]').onclick=async event=>{await navigator.clipboard.writeText(result.temporary_password);event.currentTarget.textContent='已复制';};refreshIcons();
}
async function billing(main){const [data, gens]=await Promise.all([api('/api/v1/workspace'),api('/api/v1/generations')]);main.innerHTML=`${header('使用与计费','查看积分使用情况，管理套餐与充值，让 AI 创作更高效。','badge-cent')}<div class="billing-layout"><section><div class="balance-card"><div><small>当前积分余额</small><b>${data.credit_balance}<em>积分</em></b><p>已接入真实图片生成积分扣减。</p><button class="button primary" disabled>${icon('zap')}充值功能暂未开放</button></div>${icon('coins',88)}</div><div class="stat-row"><article><b>${data.credit_balance}</b><span>剩余积分</span></article><article><b>${gens.length}</b><span>图片生成记录</span></article><article><b>—</b><span>成员使用</span></article></div><section class="panel"><h2>图片生成记录</h2><div class="data-table"><div class="thead"><span>时间</span><span>类型</span><span>内容</span><span>状态</span></div>${gens.map(x=>`<div class="trow"><span>${escapeHtml(x.created_at||'')}</span><span>图片生成</span><span>AI 生成图片</span><span class="status ready">已完成</span></div>`).join('')||'<div class="empty-state">暂无积分使用记录</div>'}</div></section></section><aside class="right-rail"><section class="panel"><h3>积分说明</h3><p>图片生成成功后将从企业积分中扣除。</p><p>在线充值、套餐和趋势统计等待计费 API 接入。</p></section></aside></div>`;sideCredit(data.credit_balance);refreshIcons();}

async function profile(main){const workspaceData=await api('/api/v1/workspace');const enterpriseName=workspaceData.brand_name||workspaceData.tenant_name||me.tenant_id;main.innerHTML=`${header('个人中心','管理个人信息与账号安全','user-round')}<div class="profile-layout"><section class="profile-card"><div class="profile-card-heading"><div><h2>基本资料</h2><p>完善个人资料，便于团队协作</p></div><button class="button secondary" disabled title="个人资料编辑 API 尚未接入">${icon('pencil')}编辑资料</button></div><div class="profile-identity"><span class="profile-avatar">${escapeHtml((me.display_name||'我')[0])}</span><div><h2>${escapeHtml(me.display_name)}</h2><span class="role-badge">${roleName(me.role)}</span><p>${escapeHtml(enterpriseName)}</p></div></div><dl class="profile-list"><div><dt>${icon('user-round')}姓名</dt><dd>${escapeHtml(me.display_name)}</dd></div>${me.email?`<div><dt>${icon('mail')}邮箱</dt><dd>${escapeHtml(me.email)}</dd></div>`:''}<div><dt>${icon('building-2')}所属企业</dt><dd>${escapeHtml(enterpriseName)}</dd></div><div><dt>${icon('users')}角色</dt><dd>${roleName(me.role)}</dd></div></dl></section><section class="profile-card" id="security"><div class="profile-card-heading"><div><h2>账号安全</h2><p>保护你的登录账号与数据</p></div></div><dl class="profile-list security-list"><div><dt>${icon('lock-keyhole')}登录密码</dt><dd>********</dd><button class="button secondary" disabled title="修改密码 API 尚未接入">修改密码</button></div>${me.email?`<div><dt>${icon('mail')}邮箱</dt><dd>${escapeHtml(me.email)}</dd><span class="binding-status">${icon('check-circle',16)}已绑定</span></div>`:''}</dl><p class="backend-dependency">资料编辑、手机号和密码修改将在普通产品账号 API 接入后开放；企业、角色、权限与积分始终只读。</p></section></div>`;refreshIcons();}

async function profileV2(main){const workspaceData=await api('/api/v1/workspace');const enterpriseName=workspaceData.brand_name||workspaceData.tenant_name||me.tenant_name||me.tenant_id;main.innerHTML=`${header('个人中心','管理个人信息与账号安全','user-round')}<p class="page-feedback" role="status" aria-live="polite"></p><div class="profile-layout"><section class="profile-card"><div class="profile-card-heading"><div><h2>基本资料</h2><p>完善个人资料，便于团队协作</p></div><button class="button secondary" id="edit-profile">${icon('pencil')}编辑资料</button></div><div class="profile-identity">${avatarMarkup('profile-avatar','当前头像')}<div><h2>${escapeHtml(me.display_name)}</h2><span class="role-badge">${roleName(me.role)}</span><p>${escapeHtml(enterpriseName)}</p></div></div><dl class="profile-list"><div><dt>${icon('user-round')}姓名</dt><dd>${escapeHtml(me.display_name)}</dd></div><div><dt>${icon('mail')}邮箱</dt><dd>${escapeHtml(me.email)}</dd></div><div><dt>${icon('building-2')}所属企业</dt><dd>${escapeHtml(enterpriseName)}</dd></div><div><dt>${icon('users')}角色</dt><dd>${roleName(me.role)}</dd></div></dl></section><section class="profile-card" id="security"><div class="profile-card-heading"><div><h2>账号安全</h2><p>保护你的登录账号与数据</p></div></div><dl class="profile-list security-list"><div><dt>${icon('lock-keyhole')}登录密码</dt><dd>********</dd><button class="button secondary" id="change-password">修改密码</button></div><div><dt>${icon('mail')}邮箱</dt><dd>${escapeHtml(me.email)}</dd><span class="binding-status">${icon('check-circle',16)}已绑定</span></div></dl><p class="backend-dependency">企业、角色、权限与积分保持只读。</p></section></div>`;main.querySelector('#edit-profile').onclick=()=>openProfileEditor();main.querySelector('#change-password').onclick=()=>openPasswordEditor(main);renderWechatAccountSettings(main);refreshIcons();}
const wechatAccountPath='/api/v1/profile/wechat-account';
const wechatStatusLabels={unconfigured:'未配置',unverified:'已配置 · 未验证',connected:'连接正常',failed:'验证失败'};
const wechatSafeMessages={WECHAT_CREDENTIAL_INVALID:'AppID或AppSecret验证失败',WECHAT_IP_NOT_ALLOWED:'当前服务器IP未加入微信公众号IP白名单',WECHAT_SERVICE_UNAVAILABLE:'微信公众号服务暂时不可用，请稍后重试',WECHAT_CONNECTION_TIMEOUT:'连接微信公众号超时',WECHAT_CONNECTION_DISABLED:'微信公众号连接验证尚未启用，请联系平台管理员。',WECHAT_SECRET_INPUT_INVALID:'请检查AppID；首次配置或轮换时请输入有效AppSecret。',WECHAT_CONFIG_PERMISSION_REQUIRED:'仅企业管理员可管理微信公众号配置。',WECHAT_SECRET_BACKEND_BLOCKED:'微信公众号凭据服务暂时不可用，请联系管理员。',WECHAT_SECRET_VERSION_CONFLICT:'配置已发生变化，请刷新后重新操作。',WECHAT_ACCOUNT_CONFIG_BLOCKED:'微信公众号配置不完整，请检查AppID和AppSecret。',WECHAT_CREDENTIAL_REFERENCE_UNAVAILABLE:'微信公众号配置不完整，请重新配置。'};
Object.assign(customerErrorMessages,wechatSafeMessages);
const maskedWechatAppId=value=>value?`${String(value).slice(0,2)}********${String(value).slice(-4)}`:'尚未配置';
function wechatAccountCardHtml(value={},manage=false,editing=false){
  const configured=Boolean(value.app_secret_configured),status=wechatStatusLabels[value.verification_status]||wechatStatusLabels.unconfigured;
  const editor=manage&&(!configured||editing);
  return `<div class="profile-card-heading"><div><small class="muted">应用配置</small><h2>${icon('message-circle',20)}微信公众号</h2><p>用于将公众号 Agent 生成的文章上传到微信公众号草稿箱。配置由当前企业共享。</p></div><span class="wechat-account-status ${escapeHtml(value.verification_status||'unconfigured')}">${escapeHtml(status)}</span></div>${configured?`<dl class="wechat-account-summary"><div><dt>公众号名称</dt><dd>${escapeHtml(value.account_display_name||'微信公众号')}</dd></div><div><dt>AppID</dt><dd>${escapeHtml(maskedWechatAppId(value.wechat_app_id))}</dd></div><div><dt>AppSecret</dt><dd>******** <span>已配置</span></dd></div></dl>`:''}${editor?`<form id="wechat-account-form" class="wechat-account-form"><label for="wechat-display-name">公众号名称（选填）<input id="wechat-display-name" name="account_display_name" maxlength="120" value="${escapeHtml(value.account_display_name||'')}" autocomplete="off"></label><label for="wechat-app-id">AppID<input id="wechat-app-id" name="wechat_app_id" required maxlength="128" value="${escapeHtml(value.wechat_app_id||'')}" autocomplete="off" autocapitalize="none" spellcheck="false"></label><label for="wechat-app-secret">AppSecret${configured?'（留空保留现有凭据）':'（首次必填）'}<input id="wechat-app-secret" name="app_secret" type="password" ${configured?'':'required'} maxlength="512" autocomplete="new-password" aria-describedby="wechat-secret-help" placeholder="${configured?'输入新AppSecret以轮换，留空则保持不变':'请输入AppSecret'}"></label><p id="wechat-secret-help" class="muted">保存后不会回显AppSecret。请勿将凭据填写到对话或文章中。</p><div class="wechat-account-actions"><button class="button primary" type="submit">保存配置</button>${configured?'<button class="button secondary" type="button" data-wechat-cancel-edit>取消修改</button>':''}</div></form>`:''}<div class="wechat-account-actions">${manage&&configured?`<button class="button secondary" type="button" data-wechat-test>${value.verification_status==='connected'?'重新验证':'测试连接'}</button>${!editing?'<button class="button secondary" type="button" data-wechat-edit>修改配置</button>':''}<button class="button secondary" type="button" data-wechat-unlink>解除绑定</button>`:''}</div>${!manage?'<p class="muted">仅企业管理员可修改配置、测试连接或解除绑定。</p>':''}<p class="wechat-whitelist-help">如测试连接提示 IP 白名单问题，请在微信公众平台中将平台服务器出口 IP 加入白名单。</p><p class="wechat-account-feedback" role="status" aria-live="polite">${escapeHtml(wechatSafeMessages[value.verification_error_code]||'')}</p>`;
}
async function renderWechatAccountSettings(main){
  const layout=main.querySelector('.profile-layout');if(!layout?.insertAdjacentHTML)return;
  layout.insertAdjacentHTML('beforeend','<section class="profile-card wechat-account-card" id="wechat-account-card" aria-label="应用配置 · 微信公众号"><h2>应用配置</h2><p role="status">正在读取微信公众号配置…</p></section>');
  const card=main.querySelector('#wechat-account-card'),manage=me.role==='enterprise_admin';let state={},editing=false,busy=false;
  const current=()=>document.querySelector('#main')===main&&main.querySelector('#wechat-account-card')===card;
  const feedback=text=>{if(current())card.querySelector('.wechat-account-feedback').textContent=text;};
  const lock=(value,text='')=>{busy=value;if(!current())return;card.querySelectorAll('button,input').forEach(node=>node.disabled=value);if(text)feedback(text);};
  const draw=()=>{
    if(!current())return;card.innerHTML=wechatAccountCardHtml(state,manage,editing);refreshIcons();
    const form=card.querySelector('#wechat-account-form');
    if(form)form.onsubmit=async event=>{
      event.preventDefault();if(busy)return;
      const secret=card.querySelector('#wechat-app-secret'),payload={account_display_name:card.querySelector('#wechat-display-name').value.trim(),wechat_app_id:card.querySelector('#wechat-app-id').value.trim(),app_secret:secret.value};
      const body=JSON.stringify(payload);secret.value='';payload.app_secret='';lock(true,'正在安全保存配置…');
      try{state=await api(wechatAccountPath,{method:'PUT',body});editing=false;draw();feedback('配置已保存；请测试连接。');}
      catch(error){feedback(wechatSafeMessages[error.code]||error.message||'保存失败，请重试。');}
      finally{lock(false);}
    };
    card.querySelector('[data-wechat-edit]')?.addEventListener('click',()=>{if(!busy){editing=true;draw();card.querySelector('#wechat-app-id').focus();}});
    card.querySelector('[data-wechat-cancel-edit]')?.addEventListener('click',()=>{editing=false;draw();});
    card.querySelector('[data-wechat-test]')?.addEventListener('click',async()=>{
      if(busy)return;if(editing){feedback('请先保存修改，再测试连接。');return;}lock(true,'正在测试连接…');
      try{state=await api(`${wechatAccountPath}/test-connection`,{method:'POST',body:'{}'});draw();feedback(state.verification_status==='connected'?'连接成功':wechatSafeMessages[state.verification_error_code]||'验证失败，请检查配置。');}
      catch(error){feedback(wechatSafeMessages[error.code]||error.message||'测试连接失败，请重试。');}
      finally{lock(false);}
    });
    card.querySelector('[data-wechat-unlink]')?.addEventListener('click',async()=>{
      if(busy||!confirm('确定解除微信公众号绑定吗？凭据将被撤销，历史文章和任务会保留。'))return;
      lock(true,'正在解除绑定…');try{state=await api(wechatAccountPath,{method:'DELETE'});editing=false;draw();feedback('已解除绑定。');}
      catch(error){feedback(wechatSafeMessages[error.code]||error.message||'解除绑定失败，请重试。');}finally{lock(false);}
    });
  };
  try{state=await api(wechatAccountPath);draw();}catch(error){if(current())card.innerHTML=`<h2>微信公众号</h2><p role="alert">${escapeHtml(wechatSafeMessages[error.code]||error.message||'配置读取失败，请刷新后重试。')}</p>`;}
}
async function renderWechatAgentSetupHint(main,agent){
  if(agent?.slug!=='wechat-official-account-writing')return;
  const composer=main.querySelector('#composer');if(!composer?.insertAdjacentHTML)return;
  try{const state=await api(wechatAccountPath);if(document.querySelector('#main')!==main||main.querySelector('#composer')!==composer||state.app_secret_configured&&state.verification_status==='connected')return;
    const hint=state.app_secret_configured?'公众号尚未通过真实连接验证，请先到个人中心测试连接。':'请先在个人中心配置微信公众号，然后再创建草稿。';
    composer.insertAdjacentHTML('beforeend',`<p class="wechat-config-hint" role="status">${hint}<button class="button secondary" type="button" data-go="profile">去配置</button></p>`);bindNavigation();
  }catch{}
}
function mountWechatArticle(main,article,content,messageId){
  const context=main?._wechatArticleContext;
  if(!context||context.agent.slug!=='wechat-official-account-writing'||!article)return;
  window.WorkbenchWechatArticle?.mount({main,article,content,messageId,...context,request:api,markdown:markdownHtml,modal:bindModalDialog,
    adapter:window.WorkbenchWechatArticle.serverAdapter(api),
    readAccount:()=>main._wechatArticleAccountPromise ||= api(wechatAccountPath)});
}
function openPasswordEditor(main){
  app.insertAdjacentHTML('beforeend',`<div class="dialog-backdrop" id="password-editor" role="presentation"><form class="profile-editor" aria-labelledby="password-editor-title"><button type="button" class="dialog-close" aria-label="关闭修改密码">${icon('x')}</button><h2 id="password-editor-title">修改密码</h2><p>修改后，下次登录请使用新密码。</p><label>当前密码<input name="current_password" type="password" autocomplete="current-password" required minlength="8"></label><label>新密码<input name="new_password" type="password" autocomplete="new-password" required minlength="8"></label><label>确认新密码<input name="confirm_password" type="password" autocomplete="new-password" required minlength="8"></label><p class="form-error" role="alert"></p><div class="editor-actions"><button type="button" class="button secondary" data-close-editor>取消</button><button class="button primary" type="submit">保存新密码</button></div></form></div>`);
  const dialog=document.querySelector('#password-editor'),form=dialog.querySelector('form'),close=bindModalDialog(dialog);dialog.querySelector('[data-close-editor]').onclick=close;
  form.onsubmit=async event=>{event.preventDefault();const data=Object.fromEntries(new FormData(form)),error=form.querySelector('.form-error'),submit=form.querySelector('[type="submit"]');error.textContent='';if(data.new_password!==data.confirm_password){error.textContent='两次输入的新密码不一致。';return;}submit.disabled=true;try{const result=await api('/api/v1/me/password',{method:'PUT',body:JSON.stringify({current_password:data.current_password,new_password:data.new_password})});close();alert(result.user_message);me=null;activeConversationId=null;login();}catch(err){error.textContent=err.message;submit.disabled=false;}};refreshIcons();
}
function openProfileEditor(){
  if(document.querySelector('#profile-editor'))return;
  app.insertAdjacentHTML('beforeend',`<div class="dialog-backdrop" id="profile-editor" role="presentation"><form class="profile-editor" id="profile-form" role="dialog" aria-modal="true" aria-labelledby="profile-editor-title"><button type="button" class="dialog-close" aria-label="关闭编辑资料">${icon('x')}</button><h2 id="profile-editor-title">编辑资料</h2><p>可更新头像、姓名和邮箱；所属企业与角色保持只读。</p><label class="avatar-field"><span id="avatar-preview">${avatarMarkup('editor-avatar','头像预览')}</span><span><b>头像</b><small>PNG、JPG、WebP，最大 2MB</small><input id="avatar-input" type="file" accept="image/png,image/jpeg,image/webp"></span></label><label>姓名<input name="display_name" required maxlength="80" value="${escapeHtml(me.display_name)}"></label><label>邮箱<input name="email" type="email" required maxlength="254" value="${escapeHtml(me.email)}"></label><p class="form-error" role="alert"></p><div class="editor-actions"><button type="button" class="button secondary" data-close-editor>取消</button><button class="button primary" type="submit">${icon('save')}保存资料</button></div></form></div>`);
  const dialog=document.querySelector('#profile-editor'),form=dialog.querySelector('#profile-form'),input=dialog.querySelector('#avatar-input'),close=bindModalDialog(dialog);
  let avatarDataUrl=null;
  dialog.querySelector('[data-close-editor]').onclick=close;
  input.onchange=()=>{const file=input.files?.[0],error=form.querySelector('.form-error');if(!file)return;if(file.size>2*1024*1024||!['image/png','image/jpeg','image/webp'].includes(file.type)){error.textContent='请选择不超过 2MB 的 PNG、JPG 或 WebP 图片。';input.value='';return;}const reader=new FileReader();reader.onload=()=>{if(!dialog.isConnected)return;avatarDataUrl=String(reader.result);dialog.querySelector('#avatar-preview').innerHTML=`<img class="editor-avatar avatar-image" src="${avatarDataUrl}" alt="新头像预览">`;};reader.readAsDataURL(file);};
  form.onsubmit=async event=>{event.preventDefault();const submit=form.querySelector('[type="submit"]'),error=form.querySelector('.form-error');submit.disabled=true;error.textContent='';try{const data=Object.fromEntries(new FormData(form));if(avatarDataUrl)data.avatar_data_url=avatarDataUrl;me=await api('/api/v1/me',{method:'PUT',body:JSON.stringify(data)});if(!dialog.isConnected)return;close();await shell('profile');const currentMain=document.querySelector('#main');if(currentMain?.dataset.page==='profile')currentMain.querySelector('#edit-profile')?.focus();}catch(err){if(!dialog.isConnected)return;error.textContent=err.message||'保存失败，请稍后重试。';submit.disabled=false;}};refreshIcons();
}
function sideCredit(balance){const el=document.querySelector('#side-credit');if(!el)return;el.innerHTML=`<div class="side-billing">${icon('badge-cent')}<span>积分余额</span></div><div class="side-meter"><i style="--p:${Math.min(balance/10,100)}%"></i><span>${balance} / 1,000 <small>积分</small></span></div>`;refreshIcons();}
function bindAccountMenu(){const wrap=document.querySelector('#account-menu-wrap'),trigger=document.querySelector('#account-trigger'),menu=document.querySelector('#account-dropdown');let pinned=false,closeTimer;const open=()=>{clearTimeout(closeTimer);menu.hidden=false;trigger.setAttribute('aria-expanded','true');};const close=()=>{if(pinned)return;menu.hidden=true;trigger.setAttribute('aria-expanded','false');};const scheduleClose=()=>{clearTimeout(closeTimer);closeTimer=setTimeout(close,180);};wrap.onmouseenter=open;wrap.onmouseleave=scheduleClose;trigger.onclick=()=>{pinned=!pinned;if(pinned)open();else {menu.hidden=true;trigger.setAttribute('aria-expanded','false');}};document.addEventListener('click',event=>{if(!wrap.contains(event.target)){pinned=false;menu.hidden=true;trigger.setAttribute('aria-expanded','false');}});document.querySelectorAll('[data-account-action]').forEach(item=>item.onclick=async()=>{const action=item.dataset.accountAction;pinned=false;menu.hidden=true;trigger.setAttribute('aria-expanded','false');if(action==='profile')return navigate('profile');if(action==='security'){await navigate('profile');document.querySelector('#security')?.scrollIntoView({behavior:'smooth',block:'start'});return;}if(action==='help')return showHelpDialog();if(action==='logout')return logout();});}
function bindModalDialog(container,trigger=document.activeElement){
  const requestedOrigin=trigger instanceof HTMLElement?trigger:document.activeElement,hiddenParent=requestedOrigin instanceof HTMLElement?requestedOrigin.closest('[hidden]'):null,origin=hiddenParent?.previousElementSibling instanceof HTMLElement?hiddenParent.previousElementSibling:requestedOrigin;
  const focusableSelector='a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),iframe,summary,[tabindex]:not([tabindex="-1"])';
  const inerted=[];let closed=false;
  for(let branch=container;branch.parentElement;branch=branch.parentElement){
    for(const sibling of branch.parentElement.children){if(sibling!==branch&&sibling instanceof HTMLElement){inerted.push([sibling,sibling.inert]);sibling.inert=true;}}
    if(branch.parentElement===document.body)break;
  }
  const focusables=()=>[...container.querySelectorAll(focusableSelector)].filter(node=>!node.hidden&&!node.closest('[inert]')&&node.getClientRects().length);
  const topmost=()=>[...document.querySelectorAll('.dialog-backdrop,.generation-viewer')].at(-1)===container;
  const close=()=>{
    if(closed)return;closed=true;
    document.removeEventListener('keydown',onKeydown);document.removeEventListener('focusin',onFocus);
    container.remove();for(const [node,wasInert]of inerted)node.inert=wasInert;
    if(!document.querySelector('.dialog-backdrop,.generation-viewer'))document.body.classList.remove('dialog-open');
    if(origin instanceof HTMLElement&&origin.isConnected&&!origin.closest('[hidden],[inert]'))origin.focus();
  };
  const onKeydown=event=>{
    if(!container.isConnected||!topmost())return;
    if(event.key==='Escape'){event.preventDefault();close();return;}
    if(event.key!=='Tab')return;
    const focusable=focusables(),first=focusable[0],last=focusable.at(-1);
    if(!first){event.preventDefault();return;}
    if(!container.contains(document.activeElement)){event.preventDefault();(event.shiftKey?last:first).focus();}
    else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  };
  const onFocus=event=>{if(container.isConnected&&topmost()&&!container.contains(event.target))focusables()[0]?.focus();};
  container.onclick=event=>{if(event.target===container&&topmost())close();};
  container.querySelector('.dialog-close')?.addEventListener('click',close);
  document.addEventListener('keydown',onKeydown);document.addEventListener('focusin',onFocus);document.body.classList.add('dialog-open');focusables()[0]?.focus();return close;
}
function showHelpDialog(){const current=document.querySelector('#help-dialog');if(current){current.querySelector('.dialog-close')?.focus();return;}app.insertAdjacentHTML('beforeend',`<div class="dialog-backdrop" id="help-dialog" role="presentation"><section class="help-dialog" role="dialog" aria-modal="true" aria-labelledby="help-title"><button type="button" class="dialog-close" aria-label="关闭帮助弹窗">${icon('x')}</button>${icon('circle-help',30)}<h2 id="help-title">帮助与反馈</h2><p>如在使用过程中遇到问题，请联系企业管理员或平台服务人员。</p></section></div>`);const dialog=document.querySelector('#help-dialog');bindModalDialog(dialog);refreshIcons();}
function bindGenerationViewers(){document.querySelectorAll('[data-generation-link]:not([data-viewer-bound])').forEach(link=>{link.dataset.viewerBound='true';link.addEventListener('click',event=>{event.preventDefault();if(link.classList.contains('media-failed'))return;openGenerationViewer(link.href,link.querySelector('img')?.alt||'生成图片',link);});});document.querySelectorAll('.generation-result [onclick]:not([data-viewer-bound])').forEach(button=>{button.dataset.viewerBound='true';button.addEventListener('click',event=>{event.preventDefault();event.stopImmediatePropagation();const result=button.closest('.generation-result'),link=result?.querySelector('.media-thumb');if(link&&!link.classList.contains('media-failed'))openGenerationViewer(link.href,link.querySelector('img')?.alt||'生成图片',button);},{capture:true});});}
function openGenerationViewer(source,alt,trigger=null){if(document.querySelector('#generation-viewer'))return;const origin=trigger||document.activeElement;app.insertAdjacentHTML('beforeend',`<div class="generation-viewer" id="generation-viewer" role="presentation"><section class="generation-viewer-panel" role="dialog" aria-modal="true" aria-label="${escapeHtml(alt)}大图预览"><header><b>${icon('image',18)}生成图片</b><div><a href="${escapeHtml(source)}" download class="viewer-download">${icon('download',18)}下载</a><button type="button" class="dialog-close" aria-label="关闭大图预览">${icon('x')}</button></div></header><div class="viewer-canvas"><img src="${escapeHtml(source)}" alt="${escapeHtml(alt)}"></div></section></div>`);const viewer=document.querySelector('#generation-viewer');bindModalDialog(viewer,origin);refreshIcons();}
async function logout(){await api('/api/v1/auth/logout',{method:'POST'});me=null;activeConversationId=null;activityPlanDocuments.clear();activityPlanBrandName='';clearActivityPlanSessionDocuments();login();}
function bindNavigation(){document.querySelectorAll('[data-go]').forEach(x=>x.onclick=event=>{const modified=event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey;if(x.matches('a[href]')&&modified)return;event.preventDefault();navigate(x.dataset.go);});document.querySelectorAll('[data-conversation]').forEach(x=>x.onclick=()=>{activeAgentId='image-agent';activeConversationId=x.dataset.conversation;navigate('image');});}
async function boot(){try{me=await api('/api/v1/me');const requested=pageFromNavigation(),path=pageRoutes[requested]||location.pathname;window.history.replaceState(navigationState(requested),'',path);shell(requested);}catch{login();}}
const knowledgeStatus = value => ({uploaded:'已上传',queued:'排队中',parsing:'知识处理中',chunking:'知识处理中',embedding:'知识处理中',indexing:'知识处理中',ready:'已完成',failed:'失败'})[value]||value;
const recentTaskStatus = value => ({queued:'排队中',running:'处理中',completed:'已完成',failed:'失败'})[value]||'处理中';
async function recentTasks(main){
  const agents=await api('/api/v1/agents');
  let filters={days:'7',status:'all',agent_id:''};
  const draw=async()=>{
    const query=new URLSearchParams({days:filters.days,status:filters.status});if(filters.agent_id)query.set('agent_id',filters.agent_id);
    const data=await api(`/api/v1/admin/recent-tasks?${query}`);if(main.dataset.page!=='recent-tasks')return;
    const summary=data.summary||{},rows=data.tasks||[];
    main.innerHTML=`${header('最近任务','查看本企业近期任务和基础积分使用情况。','list-checks')}<section class="usage-summary"><article><b>${summary.task_count||0}</b><span>任务数</span></article><article><b>${summary.completed_count||0}</b><span>成功数</span></article><article><b>${summary.failed_count||0}</b><span>失败数</span></article><article><b>${summary.credit_used||0}</b><span>积分消耗</span></article></section><section class="panel recent-task-panel"><div class="recent-task-filters"><label><span>时间</span><select id="recent-days"><option value="7" ${filters.days==='7'?'selected':''}>最近 7 天</option><option value="30" ${filters.days==='30'?'selected':''}>最近 30 天</option></select></label><label><span>状态</span><select id="recent-status"><option value="all" ${filters.status==='all'?'selected':''}>全部</option><option value="processing" ${filters.status==='processing'?'selected':''}>处理中</option><option value="completed" ${filters.status==='completed'?'selected':''}>已完成</option><option value="failed" ${filters.status==='failed'?'selected':''}>失败</option></select></label><label><span>智能体</span><select id="recent-agent"><option value="">全部</option>${agents.map(agent=>`<option value="${escapeHtml(agent.id)}" ${filters.agent_id===agent.id?'selected':''}>${escapeHtml(agent.name)}</option>`).join('')}</select></label></div><div class="recent-task-list">${rows.map(item=>`<article><time>${escapeHtml(formatHistoryTime(item.completed_at||item.started_at||item.created_at))}</time><div><b>${escapeHtml(item.member_name||'成员')}</b><small>${escapeHtml(item.agent_name||item.agent_id)}</small></div><span class="status ${escapeHtml(item.status)}">${escapeHtml(recentTaskStatus(item.status))}</span><span>${item.credit_used||0} 积分</span><div class="recent-task-reason">${item.status==='failed'?`<b>${escapeHtml(item.user_message||customerErrorMessages.TASK_FAILED)}</b>${item.diagnostic_id?`<small>诊断 ID：${escapeHtml(item.diagnostic_id)}</small>`:''}`:'—'}</div></article>`).join('')||'<div class="empty-state">当前筛选范围内暂无任务。</div>'}</div></section>`;
    const update=()=>{filters={days:main.querySelector('#recent-days').value,status:main.querySelector('#recent-status').value,agent_id:main.querySelector('#recent-agent').value};draw();};
    main.querySelectorAll('#recent-days,#recent-status,#recent-agent').forEach(node=>node.onchange=update);refreshIcons();
  };
  await draw();
}
async function knowledgeV2(main){const files=await api('/api/v1/knowledge/files');main.innerHTML=`${header('企业知识库','上传企业文件后，系统会异步解析、切分、索引，并供所有智能体检索。','book-open')}<div class="knowledge-layout"><section><form class="upload-zone" id="knowledge-upload"><input id="knowledge-file" name="file" type="file" accept=".pdf,.docx,.txt,.md" required><div>${icon('cloud-upload',36)}<h2>上传企业知识文件</h2><p>支持 PDF、DOCX、TXT、MD；上传后后台处理。</p><button class="button primary" type="submit">${icon('upload')}选择并上传</button></div><p class="form-error" role="alert"></p></form><section class="panel"><div class="section-title"><h2>文件列表</h2><span>${files.length} 个文件</span></div><div class="knowledge-file-list">${files.map(item=>`<article><div>${icon(item.status==='ready'?'file-check-2':'file-clock',22)}<span><b>${escapeHtml(item.name)}</b><small>${escapeHtml(item.mime_type||'文件')} · ${item.size_bytes?Math.ceil(item.size_bytes/1024)+' KB':''}</small></span></div><span class="status ${item.status}">${escapeHtml(knowledgeStatus(item.status))}</span><div>${button('详情','eye','secondary',`data-knowledge-detail="${item.id}"`)}${item.status==='failed'?button('重试','refresh-cw','secondary',`data-knowledge-retry="${item.id}"`):''}${button('删除','trash-2','secondary',`data-knowledge-delete="${item.id}"`)}</div>${item.status==='failed'?`<p class="error">${customerErrorMessages.KNOWLEDGE_PROCESSING_FAILED}</p>`:''}</article>`).join('')||'<div class="empty-state">暂无知识文件。上传后即可被所有智能体检索。</div>'}</div></section></section><aside class="right-rail"><section class="panel"><h3>检索测试</h3><form id="knowledge-test"><label>搜索问题<input name="query" required placeholder="例如：秋季课程有哪些特点？"></label><button class="button secondary" type="submit">${icon('search')}测试检索</button></form><div id="knowledge-test-results" class="retrieval-results"></div></section></aside></div>`;main.querySelector('#knowledge-upload').onsubmit=async event=>{event.preventDefault();const file=main.querySelector('#knowledge-file').files?.[0],err=main.querySelector('#knowledge-upload .form-error');if(!file)return;const form=new FormData();form.append('file',file);try{await apiForm('/api/v1/knowledge/files',form);knowledgeV2(main);}catch(error){err.textContent=error.message;}};main.querySelectorAll('[data-knowledge-delete]').forEach(node=>node.onclick=async()=>{if(!confirm('确定删除该文件及其索引吗？'))return;await api(`/api/v1/knowledge/files/${node.dataset.knowledgeDelete}`,{method:'DELETE'});knowledgeV2(main);});main.querySelectorAll('[data-knowledge-retry]').forEach(node=>node.onclick=async()=>{await api(`/api/v1/knowledge/files/${node.dataset.knowledgeRetry}/retry`,{method:'POST'});knowledgeV2(main);});main.querySelectorAll('[data-knowledge-detail]').forEach(node=>node.onclick=async()=>{const item=await api(`/api/v1/knowledge/files/${node.dataset.knowledgeDetail}`);showKnowledgeDetail(item);});main.querySelector('#knowledge-test').onsubmit=async event=>{event.preventDefault();const query=new FormData(event.target).get('query'),out=main.querySelector('#knowledge-test-results');out.textContent='正在检索…';const result=await api('/api/v1/knowledge/retrieval-test',{method:'POST',body:JSON.stringify({query})});out.innerHTML=result.results?.length?result.results.map(item=>`<article><b>${escapeHtml(item.filename||item.title||'来源')}</b><small>评分 ${escapeHtml(item.score)} · ${escapeHtml(item.section||'正文')}${item.page?' · 第 '+item.page+' 页':''}</small><p>${escapeHtml(item.content)}</p></article>`).join(''):'<p class="muted">无可靠相关知识。</p>';};refreshIcons();}
const fileSizeLabel = bytes => bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.ceil(bytes / 1024))} KB`;
async function knowledgeV3(main){
  const files=await api('/api/v1/knowledge/files');
  if(main.dataset.page!=='knowledge')return;
  const fileRow=item=>`<article><div>${icon(item.status==='ready'?'file-check-2':'file-clock',22)}<span><b>${escapeHtml(item.name)}</b><small>${escapeHtml(item.mime_type||'文件')} · ${item.size_bytes?fileSizeLabel(item.size_bytes):'大小未知'}</small></span></div><span class="status ${escapeHtml(item.status)}">${escapeHtml(knowledgeStatus(item.status))}</span><div>${item.status==='failed'?button('重新处理','refresh-cw','secondary',`data-knowledge-retry="${item.id}"`):''}${button('删除','trash-2','secondary',`aria-label="删除 ${escapeHtml(item.name)}" data-knowledge-delete="${item.id}"`)}</div>${item.status==='failed'?`<p class="error">${customerErrorMessages.KNOWLEDGE_PROCESSING_FAILED}${item.diagnostic_id?` 诊断 ID：${escapeHtml(item.diagnostic_id)}`:''}</p>`:''}</article>`;
  main.innerHTML=`${header('企业知识库','上传企业资料后，处理完成的文件可被智能体用于创作。','book-open')}<div class="knowledge-layout"><section><form class="upload-zone knowledge-upload-flow" id="knowledge-upload"><input id="knowledge-file" name="file" type="file" accept=".pdf,.docx,.txt,.md" hidden><div>${icon('cloud-upload',36)}<h2>上传企业资料</h2><p>支持 PDF、DOCX、TXT、MD。</p><p id="knowledge-file-summary" class="file-selection" role="status">尚未选择文件</p><div class="upload-actions"><button class="button secondary" id="knowledge-choose" type="button">${icon('folder-open')}选择文件</button><button class="button primary" id="knowledge-process" type="submit" disabled>${icon('upload')}上传并处理</button></div></div><p class="form-error" role="alert"></p></form><section class="panel"><div class="section-title"><h2>文件列表</h2><span>${files.length} 个文件</span></div><div class="knowledge-file-list">${files.map(fileRow).join('')||'<div class="empty-state">暂无企业资料。选择文件并处理后，会显示在这里。</div>'}</div></section></section><aside class="right-rail"><section class="panel"><h3>处理说明</h3><p class="muted">文件会依次经历等待处理、处理中、已完成或处理失败。</p><p class="muted">处理失败时可重新上传或重新处理。</p></section></aside></div>`;
  const input=main.querySelector('#knowledge-file'),summary=main.querySelector('#knowledge-file-summary'),process=main.querySelector('#knowledge-process'),error=main.querySelector('#knowledge-upload .form-error');
  main.querySelector('#knowledge-choose').onclick=()=>input.click();
  input.onchange=()=>{const file=input.files?.[0];error.textContent='';if(!file){summary.textContent='尚未选择文件';process.disabled=true;return;}const suffix=file.name.includes('.')?file.name.split('.').pop().toUpperCase():'文件';summary.textContent=`已选择：${file.name} · ${suffix} · ${fileSizeLabel(file.size)}`;process.disabled=false;};
  main.querySelector('#knowledge-upload').onsubmit=async event=>{event.preventDefault();const file=input.files?.[0];if(!file)return;process.disabled=true;error.textContent='';const form=new FormData();form.append('file',file);try{await apiForm('/api/v1/knowledge/files',form);await knowledgeV3(main);}catch(err){error.textContent=err.message;process.disabled=false;}};
  main.querySelectorAll('[data-knowledge-delete]').forEach(node=>node.onclick=async()=>{if(!confirm('确定删除该企业资料吗？'))return;await api(`/api/v1/knowledge/files/${node.dataset.knowledgeDelete}`,{method:'DELETE'});knowledgeV3(main);});
  main.querySelectorAll('[data-knowledge-retry]').forEach(node=>node.onclick=async()=>{await api(`/api/v1/knowledge/files/${node.dataset.knowledgeRetry}/retry`,{method:'POST'});knowledgeV3(main);});
  refreshIcons();
}
function showKnowledgeDetail(item){if(document.querySelector('#knowledge-detail'))return;app.insertAdjacentHTML('beforeend',`<div class="dialog-backdrop" id="knowledge-detail" role="presentation"><section class="help-dialog" role="dialog" aria-modal="true" aria-labelledby="knowledge-detail-title"><button type="button" class="dialog-close" aria-label="关闭文件详情">${icon('x')}</button><h2 id="knowledge-detail-title">${escapeHtml(item.name)}</h2><p>状态：${escapeHtml(knowledgeStatus(item.status))}</p><p>文件大小：${item.size_bytes?fileSizeLabel(item.size_bytes):'—'}</p>${item.status==='ready'?`<details><summary>查看文件内容摘要</summary><p>${escapeHtml((item.parsed_text||'').slice(0,4000))}</p></details>`:''}</section></div>`);const dialog=document.querySelector('#knowledge-detail');bindModalDialog(dialog);refreshIcons();}

async function platformSkills(main){
  const [skills,agents]=await Promise.all([api('/api/v1/platform/skills'),api('/api/v1/agents')]);
  const agentNames=Object.fromEntries(agents.map(agent=>[agent.id,agent.name]));
  const agentOptions=`<option value="">请选择智能体…</option>${agents.map(agent=>`<option value="${escapeHtml(agent.id)}">${escapeHtml(agent.name)}</option>`).join('')}`;
  const cards=skills.map(skill=>`<article class="skill-card" data-bound-agents="${escapeHtml(skill.bindings.map(item=>item.agent_id).join(','))}"><header><div><h2>${escapeHtml(skill.name)}</h2><p><code>${escapeHtml(skill.slug)}</code> · ${escapeHtml(skill.description||'原生 Codex Skill')}</p></div><select aria-label="选择绑定智能体" data-skill-agent>${agentOptions}</select></header><div class="skill-bindings">${skill.bindings.length?skill.bindings.map(item=>`<span>${escapeHtml(agentNames[item.agent_id]||item.agent_id)} → ${escapeHtml(item.version)} <button class="icon-button" title="解除绑定" aria-label="解除 ${escapeHtml(item.agent_id)} 与 ${escapeHtml(skill.slug)} 的绑定" data-skill-unbind data-agent-id="${escapeHtml(item.agent_id)}" data-skill-slug="${escapeHtml(skill.slug)}">${icon('x',14)}</button></span>`).join(''):'<span>尚未绑定 Agent</span>'}</div><div class="skill-versions">${skill.versions.map(version=>`<section><div><b>${escapeHtml(version.version)}</b><span class="status ${escapeHtml(version.status)}">${escapeHtml(version.status)}</span><small>${escapeHtml(String(version.checksum).slice(0,12))}</small></div><div><button class="button secondary" data-skill-test="${version.id}">${icon('flask-conical')}测试</button>${version.status==='draft'?`<button class="button primary" data-skill-publish="${version.id}">${icon('send')}发布</button>`:''}${version.status==='published'?`<button class="button secondary" data-skill-bind="${version.id}" data-skill-slug="${escapeHtml(skill.slug)}" data-skill-version="${escapeHtml(version.version)}">${icon('link')}绑定 / 切换</button><button class="button secondary" data-skill-deprecate="${version.id}">${icon('archive')}废弃</button>`:''}</div></section>`).join('')}</div></article>`).join('');
  main.innerHTML=`${header('Skill Registry','导入、测试、发布并将原生 Codex Skill 精确绑定到 Agent','package-check')}<section class="skill-import panel"><h2>导入原生 Skill ZIP</h2><form id="skill-import"><label>名称<input name="name" required maxlength="120"></label><label>Slug<input name="slug" required pattern="[a-z0-9]+(?:-[a-z0-9]+)*" placeholder="poster-design"></label><label>版本<input name="version" required pattern="[0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?" placeholder="1.1.0"></label><label>ZIP<input name="file" type="file" accept=".zip" required></label><label class="skill-description">说明<input name="description" maxlength="500"></label><button class="button primary" type="submit">${icon('upload')}导入草稿</button><p class="form-error" role="alert"></p></form></section><section class="skill-registry-list">${cards||'<div class="empty-state">尚无 Skill。</div>'}</section>`;
  const refresh=()=>platformSkills(main);
  main.querySelector('#skill-import').onsubmit=async event=>{event.preventDefault();const error=event.target.querySelector('.form-error'),submit=event.target.querySelector('[type=submit]');error.textContent='';submit.disabled=true;try{await apiForm('/api/v1/platform/skills/import',new FormData(event.target));await refresh();}catch(err){error.textContent=err.message;submit.disabled=false;}};
  main.querySelectorAll('[data-skill-test]').forEach(node=>node.onclick=async()=>{const result=await api(`/api/v1/platform/skill-versions/${node.dataset.skillTest}/test`,{method:'POST'});alert(`测试通过：${result.file_count} 个文件，SHA-256 ${result.sha256.slice(0,12)}…`);});
  main.querySelectorAll('[data-skill-publish]').forEach(node=>node.onclick=async()=>{await api(`/api/v1/platform/skill-versions/${node.dataset.skillPublish}/publish`,{method:'POST'});await refresh();});
  main.querySelectorAll('[data-skill-bind]').forEach(node=>node.onclick=async()=>{const card=node.closest('.skill-card'),select=card.querySelector('[data-skill-agent]'),agentId=select.value;if(!agentId){alert('请先明确选择要绑定的智能体。');select.focus();return;}const existing=card.dataset.boundAgents.split(',').filter(Boolean).includes(agentId),agentName=agentNames[agentId]||agentId;if(!existing&&!confirm(`该 Skill 尚未绑定“${agentName}”。确认新增绑定？`))return;await api(`/api/v1/platform/agents/${encodeURIComponent(agentId)}/skills/${encodeURIComponent(node.dataset.skillSlug)}`,{method:'PUT',body:JSON.stringify({version:node.dataset.skillVersion,allow_new_binding:!existing})});await refresh();});
  main.querySelectorAll('[data-skill-unbind]').forEach(node=>node.onclick=async()=>{const agentName=agentNames[node.dataset.agentId]||node.dataset.agentId;if(!confirm(`确认解除“${agentName}”与此 Skill 的绑定？Agent 下次运行将不再加载该 Skill。`))return;await api(`/api/v1/platform/agents/${encodeURIComponent(node.dataset.agentId)}/skills/${encodeURIComponent(node.dataset.skillSlug)}`,{method:'DELETE'});await refresh();});
  main.querySelectorAll('[data-skill-deprecate]').forEach(node=>node.onclick=async()=>{await api(`/api/v1/platform/skill-versions/${node.dataset.skillDeprecate}/deprecate`,{method:'POST'});await refresh();});
  refreshIcons();
}
const outputPolicyName=value=>value==='image_required'?'必须生成图片':'文本输出';
const runtimeStatusName=value=>({passed:'已通过',queued:'排队中',running:'执行中',failed:'未通过','Runtime Test Pending':'未执行'})[value]||value;
const instanceActionsForStatus=status=>status==='unconfigured'?['configure']:status==='configured'?['configure','enable']:status==='enabled'?['disable']:status==='disabled'?['configure','enable']:['disable'];
function configurationSummaryHtml(summary,title='当前保存配置'){
  const skills=summary.skills?.length?summary.skills.map(item=>`<li>${escapeHtml(item.slug)}@${escapeHtml(item.version)}</li>`).join(''):'<li class="empty-value">未绑定 Skill</li>';
  const tools=summary.tools?.length?summary.tools.map(item=>`<li>${escapeHtml(item.id)} · ${escapeHtml(item.requirement)}</li>`).join(''):'<li class="empty-value">未绑定 Tool</li>';
  return `<section class="configuration-summary" data-configuration-fingerprint="${escapeHtml(summary.configuration_fingerprint||'')}"><h3>${escapeHtml(title)}</h3><dl><div><dt>Persona</dt><dd>${escapeHtml(summary.persona||'未设置')}</dd></div><div><dt>Model</dt><dd>${escapeHtml(summary.model?.label||summary.model?.id||'未设置')}</dd></div><div><dt>Output</dt><dd>${escapeHtml(outputPolicyName(summary.output_policy))}</dd></div><div><dt>Credit</dt><dd>${escapeHtml(summary.credit_cost)} 积分 / 次</dd></div><div><dt>Skills</dt><dd><ul>${skills}</ul></dd></div><div><dt>Tools</dt><dd><ul>${tools}</ul></dd></div><div><dt>Runtime Test</dt><dd>${escapeHtml(runtimeStatusName(summary.runtime_test_status))}</dd></div></dl></section>`;
}
function validationSummaryHtml(summary){
  const label=summary?.status==='passed'?'已通过':summary?.status==='failed'?'未通过':'未执行';
  const errors=summary?.errors?.length?`<ul>${summary.errors.map(item=>`<li>${escapeHtml(item)}</li>`).join('')}</ul>`:'<p>当前保存配置没有 Validation 错误。</p>';
  return `<section class="validation-summary ${escapeHtml(summary?.status||'pending')}"><h3>Validation 摘要</h3><p><strong>${escapeHtml(label)}</strong></p>${summary?.status==='failed'?errors:summary?.status==='passed'?errors:'<p>执行 Validation 后将在此显示结果。</p>'}</section>`;
}
function technicalRevisionHtml(version){
  return `<details class="technical-details"><summary>技术详情</summary><p>Fingerprint：<code>${escapeHtml(version.configuration_fingerprint)}</code></p><h4>Validation / Runtime records</h4>${version.tests?.map(test=>`<p>${escapeHtml(test.test_type)} · ${escapeHtml(test.status)} · ${escapeHtml(test.configuration_fingerprint.slice(0,12))}</p><pre>${escapeHtml(test.result_json)}</pre>`).join('')||'<p>暂无技术记录</p>'}</details>`;
}
async function platformAgents(main) {
  const root='/api/v1/platform/agents';
  const [templates,options,skills]=await Promise.all([api(root),api(`${root}/options`),api('/api/v1/platform/skills')]);
  main.innerHTML=`${header('Agent 管理','配置、验证、发布并安全启停企业智能体','bot')}<section class="panel agent-control"><p role="status">只有服务端保存配置的真实 Runtime Test 通过，才允许生产发布和 Tenant Enable。</p><form id="agent-create"><h2>创建 Template</h2><label>名称<input name="name" required maxlength="120"></label><label>Slug<input name="slug" required maxlength="100" pattern="[a-z0-9]+(?:-[a-z0-9]+)*"></label><label>分类<input name="category" value="general" required maxlength="80"></label><label>说明<input name="description" maxlength="2000"></label><button class="button primary">创建 Template</button></form><p id="agent-error" class="form-error" role="alert"></p></section><section class="panel agent-control"><h2>Agent 列表</h2>${templates.map(t=>`<button class="button secondary" data-agent-template="${escapeHtml(t.id)}">${escapeHtml(t.name)} · ${escapeHtml(t.definition_source)} · ${escapeHtml(t.lifecycle_status)}</button>`).join('')}</section><section id="agent-detail" class="panel agent-control"><p>选择 Agent 查看配置。</p></section>`;
  const error=main.querySelector('#agent-error');
  let detailGeneration=0;
  const perform=async action=>{error.textContent='';try{return await action();}catch(err){error.textContent=err.requestId?`${err.message}（诊断 ID：${err.requestId}）`:err.message;throw err;}};
  main.querySelector('#agent-create').onsubmit=event=>{event.preventDefault();const form=event.target,submit=form.querySelector('button');submit.disabled=true;perform(async()=>{await api(root,{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(form)))});await platformAgents(main);}).catch(()=>{}).finally(()=>{submit.disabled=false;});};
  const select=(name,values,value,disabled=false)=>`<select name="${name}" ${disabled?'disabled':''}>${values.map(item=>`<option value="${escapeHtml(item.id||item)}" ${(item.id||item)===value?'selected':''}>${escapeHtml(item.label||item)}</option>`).join('')}</select>`;

  async function showDetail(templateId,revisionId=null){
    const generation=++detailGeneration,t=await api(`${root}/${encodeURIComponent(templateId)}`);
    if(generation!==detailGeneration)return;
    main.querySelectorAll('[data-agent-template]').forEach(node=>{if(node.dataset.agentTemplate===t.id)node.textContent=`${t.name} · ${t.definition_source} · ${t.lifecycle_status}`;});
    const box=main.querySelector('#agent-detail');
    if(t.definition_source==='legacy'){box.innerHTML=`<h2>${escapeHtml(t.name)}</h2><p>当前为稳定运行中的 Legacy Agent。本批次保持其运行路径不变。</p>`;return;}
    const v=t.versions.find(item=>item.id===revisionId)||t.versions[0],editable=v?.status==='draft',endpoint=v?`${root}/${encodeURIComponent(t.id)}/versions/${encodeURIComponent(v.id)}`:null;
    const revisionButtons=t.versions.map(item=>`<button class="button secondary" data-agent-revision="${escapeHtml(item.id)}">Revision ${item.revision} · ${escapeHtml(item.status)}${item.publication_scope?' · '+escapeHtml(item.publication_scope):''}</button>`).join('');
    const editForms=v&&editable?`<form id="agent-draft"><label>名称<input name="name" value="${escapeHtml(v.name)}" required maxlength="120"></label><label>说明<input name="description" value="${escapeHtml(v.description)}" maxlength="2000"></label><label>分类<input name="category" value="${escapeHtml(v.category)}" required maxlength="80"></label><label>Persona<textarea name="persona" maxlength="16000" rows="5">${escapeHtml(v.persona)}</textarea></label><label>Model Config${select('model_config_id',options.model_configs,v.model_config_id)}</label>${[['enterprise_config_requirement','企业配置'],['knowledge_requirement','知识库'],['asset_requirement','素材']].map(([key,label])=>`<label>${label} Requirement${select(key,['none','optional','required'],v[key])}</label>`).join('')}<label>Output Policy${select('output_policy',['text','image_required'],v.output_policy)}</label><label>Credit Cost<input name="credit_cost" type="number" min="1" max="1000000" value="${v.credit_cost}" required></label><button class="button primary">保存 Draft</button></form><form id="agent-skill-bindings"><h3>Skill</h3>${skills.map(skill=>`<label>${escapeHtml(skill.name)}<select data-agent-skill="${escapeHtml(skill.id)}"><option value="">未绑定</option>${skill.versions.filter(sv=>sv.status==='published'||v.skills.some(binding=>binding.skill_version_id===sv.id)).map(sv=>`<option value="${escapeHtml(sv.id)}" ${v.skills.some(binding=>binding.skill_version_id===sv.id)?'selected':''}>${escapeHtml(sv.version)} · ${escapeHtml(sv.status)}</option>`).join('')}</select></label>`).join('')}<button class="button secondary">保存 Skill</button></form><form id="agent-tool-bindings"><h3>Tool</h3>${options.tool_capabilities.map(tool=>`<label>${escapeHtml(tool.name)}${select(tool.id,tool.implemented?['denied','optional','required']:['denied'],v.tools.find(binding=>binding.tool_capability_id===tool.id)?.invocation_requirement||'denied',!tool.implemented)}${tool.implemented?'':' · 未实现'}</label>`).join('')}<button class="button secondary">保存 Tool</button></form>`:'';
    box.innerHTML=`<h2>${escapeHtml(t.name)}</h2><button class="button secondary" id="agent-new-revision">创建新 Draft ${v?'（复制当前 Revision）':''}</button>${revisionButtons}${v?`<h3>Revision ${v.revision} · ${escapeHtml(v.status)}</h3>${configurationSummaryHtml(v.configuration_summary,v.status==='published'?'已发布配置（只读）':'当前保存配置')}${validationSummaryHtml(v.validation_summary)}${editForms}${editable?`<section class="validation-panel">${configurationSummaryHtml(v.configuration_summary,'本次验证配置')}<button id="agent-validate" class="button secondary">执行 Validation</button><button id="agent-runtime-test" class="button secondary">执行真实 Runtime Test（隔离 Tenant）</button><button id="agent-production-publish" class="button primary" ${v.production_ready===true?'':'disabled'}>Production Publish</button>${options.local_test_publish_allowed?'<button id="agent-local-publish" class="button secondary">仅本地测试发布</button>':''}</section>`:''}${technicalRevisionHtml(v)}${v.status==='published'?'<section id="agent-instance-management"></section><button id="agent-deprecate" class="button secondary">Deprecated（不切换现有 Instance）</button>':''}`:'<p>尚无 Revision。</p>'}`;
    const reload=()=>generation===detailGeneration?showDetail(t.id,v?.id):Promise.resolve();

    if(v?.status==='published')setupInstanceManagement(box,t,v,root,perform);
    box.querySelector('#agent-new-revision').onclick=()=>perform(async()=>{const next=await api(`${root}/${t.id}/versions`,{method:'POST',body:JSON.stringify(v?{from_version_id:v.id}:{})});if(generation===detailGeneration)await showDetail(t.id,next.versions[0].id);}).catch(()=>{});
    box.querySelectorAll('[data-agent-revision]').forEach(node=>node.onclick=()=>perform(()=>showDetail(t.id,node.dataset.agentRevision)).catch(()=>{}));
    if(editable){
      box.querySelector('#agent-draft').onsubmit=event=>{event.preventDefault();const data=Object.fromEntries(new FormData(event.target));data.credit_cost=Number(data.credit_cost);perform(async()=>{await api(endpoint,{method:'PATCH',body:JSON.stringify(data)});await reload();}).catch(()=>{});};
      box.querySelector('#agent-skill-bindings').onsubmit=event=>{event.preventDefault();const bindings=[...box.querySelectorAll('[data-agent-skill]')].filter(node=>node.value).map(node=>({skill_id:node.dataset.agentSkill,skill_version_id:node.value}));perform(async()=>{await api(`${endpoint}/skills`,{method:'PUT',body:JSON.stringify({bindings})});await reload();}).catch(()=>{});};
      box.querySelector('#agent-tool-bindings').onsubmit=event=>{event.preventDefault();const bindings=[...event.target.querySelectorAll('select')].filter(node=>node.value!=='denied').map(node=>({tool_capability_id:node.name,invocation_requirement:node.value}));perform(async()=>{await api(`${endpoint}/tools`,{method:'PUT',body:JSON.stringify({bindings})});await reload();}).catch(()=>{});};
      box.querySelector('#agent-validate').onclick=()=>perform(async()=>{await api(`${endpoint}/validation`,{method:'POST'});await reload();}).catch(()=>{});
      box.querySelector('#agent-runtime-test').onclick=event=>{const button=event.currentTarget;button.disabled=true;perform(async()=>{await api(`${endpoint}/test`,{method:'POST',body:JSON.stringify({configuration_fingerprint:v.configuration_fingerprint})});await reload();}).catch(()=>{}).finally(()=>{button.disabled=false;});};
      if(['queued','running'].includes(v.runtime_test_status))setTimeout(()=>{if(generation===detailGeneration&&main.isConnected!==false&&main.dataset.page==='platform-agents')perform(reload).catch(()=>{});},1000);
      box.querySelector('#agent-production-publish').onclick=()=>openProductionPublishConfirmation(v,async()=>{await perform(async()=>{await api(`${endpoint}/publish`,{method:'POST',body:JSON.stringify({mode:'production'})});await reload();});});
      box.querySelector('#agent-local-publish')?.addEventListener('click',()=>{if(!confirm('确认仅本地测试发布？发布后 Revision 将不可修改，也不可生产运行。'))return;perform(async()=>{await api(`${endpoint}/publish`,{method:'POST',body:JSON.stringify({mode:'local_test'})});await reload();}).catch(()=>{});});
    }
    box.querySelector('#agent-deprecate')?.addEventListener('click',()=>perform(async()=>{await api(`${endpoint}/deprecate`,{method:'POST'});await reload();}).catch(()=>{}));
    refreshIcons();
  }

  function setupInstanceManagement(box,template,version,rootPath,performAction){
    const section=box.querySelector('#agent-instance-management');
    section.innerHTML=`<h3>企业启用状态</h3><form id="instance-lookup"><label>Tenant ID<input id="agent-instance-tenant" required placeholder="输入 Tenant ID"></label><button class="button secondary">读取当前状态</button></form><div id="instance-state" class="instance-state"><p>输入 Tenant ID 后显示当前 Revision、状态和 Workspace 可见性。</p></div>`;
    const form=section.querySelector('#instance-lookup'),input=section.querySelector('#agent-instance-tenant'),state=section.querySelector('#instance-state');
    const pathFor=()=>`${rootPath}/${encodeURIComponent(template.id)}/instances/${encodeURIComponent(input.value.trim())}`;
    const load=async message=>{if(!input.value.trim())return;const current=await api(pathFor());renderInstance(current,message);};
    const renderInstance=(current,message='')=>{
      const labels={unconfigured:'未配置',configured:'Configured',enabled:'Enabled',disabled:'Disabled',blocked:'Blocked'};
      const actions=instanceActionsForStatus(current.status);
      state.innerHTML=`${message?`<p class="instance-feedback success">${escapeHtml(message)}</p>`:''}<dl><div><dt>Tenant</dt><dd>${escapeHtml(current.tenant_name||current.tenant_id)}</dd></div><div><dt>当前 Revision</dt><dd>${current.revision?`Revision ${escapeHtml(current.revision)} · ${escapeHtml(current.revision_name||'')}`:'未配置'}</dd></div><div><dt>Instance status</dt><dd><span class="instance-badge ${escapeHtml(current.status)}">${escapeHtml(labels[current.status]||current.status)}</span></dd></div><div><dt>Workspace visibility</dt><dd>${current.workspace_visible?'可见':'不可见'}</dd></div></dl><div class="instance-actions">${actions.includes('configure')?'<button class="button secondary" data-instance-action="configure">修改配置</button>':''}${actions.includes('enable')?'<button class="button primary" data-instance-action="enable">启用</button>':''}${actions.includes('disable')?'<button class="button secondary" data-instance-action="disable">停用</button>':''}</div>`;
      if(current.status==='unconfigured')state.querySelector('[data-instance-action="configure"]').textContent='配置';
      state.querySelectorAll('[data-instance-action]').forEach(button=>button.onclick=()=>act(button.dataset.instanceAction).catch(()=>{}));
    };
    const act=async action=>{await performAction(async()=>{if(action==='configure')await api(pathFor(),{method:'PUT',body:JSON.stringify({agent_template_version_id:version.id,overrides:{}})});else await api(`${pathFor()}/${action}`,{method:'POST'});pageCache.clear();await load(action==='configure'?'配置已保存，当前尚未出现在 Workspace。':action==='enable'?'已启用，将出现在 Workspace。':'已停用，已从 Workspace 移除。');});};
    form.onsubmit=event=>{event.preventDefault();performAction(()=>load()).catch(()=>{});};input.onchange=()=>performAction(()=>load()).catch(()=>{});
  }

  main.querySelectorAll('[data-agent-template]').forEach(node=>node.onclick=()=>perform(()=>showDetail(node.dataset.agentTemplate)).catch(()=>{}));
  refreshIcons();
}
function openProductionPublishConfirmation(version,onConfirm){
  const summary=version.configuration_summary;
  app.insertAdjacentHTML('beforeend',`<div class="dialog-backdrop" id="production-publish-confirmation" role="presentation"><section class="publish-confirmation" role="dialog" aria-modal="true" aria-labelledby="publish-confirm-title"><button type="button" class="dialog-close" aria-label="关闭发布确认">${icon('x')}</button><h2 id="publish-confirm-title">最终冻结配置</h2><p>以下内容来自服务端当前保存状态。发布后该 Revision 不可编辑。</p>${configurationSummaryHtml(summary,'Production Publish 配置')}${validationSummaryHtml(version.validation_summary)}<details class="technical-details"><summary>技术校验</summary><p>Runtime Test：${escapeHtml(runtimeStatusName(summary.runtime_test_status))}</p><p>Fingerprint：<code>${escapeHtml(summary.configuration_fingerprint)}</code></p></details><label class="publish-confirm-check"><input type="checkbox" id="publish-final-confirm">我已核对 Persona、Model、Output、Credit、Skill、Tool 和 Runtime Test。</label><button class="button primary" id="publish-final-submit" disabled>确认发布</button></section></div>`);
  const dialog=document.querySelector('#production-publish-confirmation'),close=bindModalDialog(dialog),check=dialog.querySelector('#publish-final-confirm'),submit=dialog.querySelector('#publish-final-submit');
  check.onchange=()=>{submit.disabled=!check.checked||version.production_ready!==true;};
  submit.onclick=async()=>{submit.disabled=true;try{await onConfirm();close();}catch{submit.disabled=false;}};
  refreshIcons();
}

boot();
