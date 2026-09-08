const app = document.querySelector('#app');
let me = null;
let activeConversationId = null;

const api = (path, options = {}) => fetch(path, {
  credentials: 'same-origin',
  headers: { 'content-type': 'application/json', ...(options.headers || {}) },
  ...options,
}).then(async response => {
  const body = await response.json();
  if (!response.ok) throw Error(body.detail || '请求失败');
  return body;
});

const escapeHtml = value => String(value || '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
const imageUrl = generation => `/api/v1/storage/${String(generation.storage_key).split('/').map(encodeURIComponent).join('/')}`;
const preview = (generation, label = '生成图片') => generation
  ? `<a class="image-preview" href="${imageUrl(generation)}" target="_blank" rel="noopener" aria-label="预览${escapeHtml(label)}"><img loading="lazy" src="${imageUrl(generation)}" alt="${escapeHtml(label)}"></a>`
  : '<div class="image-preview placeholder" aria-label="暂无图片预览">暂无图片</div>';

function login() {
  app.innerHTML = `<section class="card login"><p class="eyebrow">Enterprise Agent Workbench</p><h1>登录企业工作台</h1><form id="login"><label>账号<input name="account" type="email" autocomplete="username" required placeholder="name@company.com"></label><label>密码<input name="password" type="password" autocomplete="current-password" required></label><button class="primary" type="submit">登录</button><p class="muted">请使用企业管理员分配的账号登录。</p><p class="error" role="alert"></p></form></section>`;
  document.querySelector('#login').onsubmit = async event => {
    event.preventDefault();
    try { await api('/api/v1/auth/login', { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); boot(); }
    catch (error) { document.querySelector('.error').textContent = error.message; }
  };
}

function shell() {
  app.innerHTML = `<div class="shell"><nav class="nav" aria-label="主导航"><p class="brand">Agent Workbench</p><button data-page="workspace">工作台</button><button data-page="image">图片智能体</button><button data-page="conversations">会话历史</button><button data-page="generations">我的生成</button>${me.role === 'enterprise_admin' ? '<button data-page="enterprise">企业配置</button><button data-page="knowledge">知识库</button><button data-page="assets">素材库</button>' : ''}<button id="logout">退出登录</button></nav><section id="main" class="content" tabindex="-1"></section></div>`;
  document.querySelectorAll('[data-page]').forEach(button => button.onclick = () => render(button.dataset.page));
  document.querySelector('#logout').onclick = async () => { await api('/api/v1/auth/logout', { method: 'POST' }); login(); };
  render(location.pathname.includes('/agents/image') ? 'image' : 'workspace');
}

function thinkingMarkup(stage = '正在理解创作需求，请稍等。') {
  return `<section class="thinking-card" id="status" role="status" aria-live="polite" aria-busy="true"><p class="thinking-kicker">正在思考</p><p class="thinking-copy" data-stage>${escapeHtml(stage)}</p><div class="thinking-grid" aria-hidden="true"></div><p class="thinking-eta">正在结合企业资料生成图片，通常需要约 1–2 分钟。</p></section>`;
}
function stageCopy(stage) { return ({ queued: '正在排队准备创作。', loading_context: '正在读取企业品牌与规则。', retrieving_knowledge: '正在检索企业知识。', retrieving_assets: '正在准备品牌素材。', generating: '正在生成更详细的图片，请稍等。', saving_asset: '正在保存生成结果。' }[stage] || '正在理解创作需求，请稍等。'); }

async function render(page) {
  const main = document.querySelector('#main');
  if (page === 'workspace') {
    const data = await api('/api/v1/workspace');
    main.innerHTML = `<header class="top"><div><p class="eyebrow">${escapeHtml(data.tenant_name)}</p><h1>${escapeHtml(data.brand_name || data.tenant_name)} 工作台</h1></div><button class="primary" id="start">新建图片任务</button></header><div class="grid"><article class="card agent"><h2>图片生成智能体</h2><p class="muted">基于企业品牌、知识与素材，生成可继续修改的海报。</p><button class="primary" id="open">开始创作</button></article><article class="card"><p class="eyebrow">企业积分</p><p class="credit">${data.credit_balance}</p><p class="muted">每次海报任务 20 积分</p></article></div><section class="recent-section"><div class="section-heading"><h2>最近生成</h2><button class="text-button" id="all-generations">查看全部</button></div><div class="preview-strip">${data.recent_generations.length ? data.recent_generations.map(item => `<article class="mini-generation">${preview(item, '最近生成的海报')}<p>${escapeHtml(String(item.created_at).replace('T', ' ').slice(0, 16))}</p></article>`).join('') : '<p class="muted">生成的图片会在这里显示预览。</p>'}</div></section>`;
    ['start', 'open'].forEach(id => document.querySelector(`#${id}`).onclick = () => render('image'));
    document.querySelector('#all-generations').onclick = () => render('generations');
    return;
  }
  if (page === 'image') {
    const conversations = await api('/api/v1/conversations');
    main.innerHTML = `<header><p class="eyebrow">图片生成智能体</p><h1>创建企业视觉内容</h1></header><div class="chat"><aside class="history"><button id="new" class="new-conversation">＋ 新建会话</button><h2>历史会话</h2>${conversations.length ? conversations.map(item => `<article class="history-item"><button data-conversation="${item.id}" aria-pressed="${activeConversationId === item.id}">${escapeHtml(item.title)}</button>${preview(item.latest_generation, `${item.title} 的最新图片`)}</article>`).join('') : '<p class="muted">还没有会话</p>'}</aside><section class="card"><div class="messages" id="messages"><div class="empty">描述一张海报、配图或视觉改版需求。生成过程与结果会显示在这里。</div></div><form class="composer" id="compose"><label class="skip" for="message">任务需求</label><textarea id="message" required placeholder="例如：帮我做一张秋季招生海报"></textarea><button class="primary">发送</button></form></section></div>`;
    document.querySelector('#new').onclick = () => { activeConversationId = null; render('image'); };
    document.querySelectorAll('[data-conversation]').forEach(button => button.onclick = () => { activeConversationId = button.dataset.conversation; render('image'); });
    document.querySelector('#compose').onsubmit = submit;
    return;
  }
  if (page === 'conversations') {
    const conversations = await api('/api/v1/conversations');
    main.innerHTML = `<header><p class="eyebrow">会话历史</p><h1>继续你的图片创作</h1></header><div class="history-gallery">${conversations.length ? conversations.map(item => `<article class="history-card">${preview(item.latest_generation, `${item.title} 的最新图片`)}<div><h2>${escapeHtml(item.title)}</h2><p class="muted">${escapeHtml(String(item.created_at).replace('T', ' ').slice(0, 16))}</p><button data-open="${item.id}" class="primary">继续创作</button><button data-rename="${item.id}" class="secondary">重命名</button><button data-delete="${item.id}" class="text-button danger">删除</button></div></article>`).join('') : '<p class="muted">暂无会话记录。</p>'}</div>`;
    document.querySelectorAll('[data-open]').forEach(button => button.onclick = () => { activeConversationId = button.dataset.open; render('image'); });
    document.querySelectorAll('[data-delete]').forEach(button => button.onclick = async () => { await api(`/api/v1/conversations/${button.dataset.delete}`, { method: 'DELETE' }); if (activeConversationId === button.dataset.delete) activeConversationId = null; render('conversations'); });
    document.querySelectorAll('[data-rename]').forEach(button => button.onclick = async () => { const title = prompt('会话名称'); if (title) { await api(`/api/v1/conversations/${button.dataset.rename}`, { method: 'PATCH', body: JSON.stringify({ title }) }); render('conversations'); } });
    return;
  }
  if (page === 'generations') {
    const generations = await api('/api/v1/generations');
    main.innerHTML = `<header><p class="eyebrow">我的生成</p><h1>图片历史</h1></header><div class="generation-gallery">${generations.length ? generations.map(item => `<article class="generation-card">${preview(item, 'AI 生成海报')}<div><p class="muted">${escapeHtml(String(item.created_at).replace('T', ' ').slice(0, 16))}</p><a href="${imageUrl(item)}" download>下载图片</a><button data-save="${item.id}" class="secondary">保存到素材库</button><button data-delete="${item.id}" class="text-button danger">删除</button></div></article>`).join('') : '<p class="muted">暂无生成记录。</p>'}</div>`;
    document.querySelectorAll('[data-delete]').forEach(button => button.onclick = async () => { await api(`/api/v1/generations/${button.dataset.delete}`, { method: 'DELETE' }); render('generations'); });
    document.querySelectorAll('[data-save]').forEach(button => button.onclick = async event => { await api(`/api/v1/generations/${event.currentTarget.dataset.save}/save-to-assets`, { method: 'POST', body: JSON.stringify({ name: 'AI 生成海报' }) }); event.currentTarget.textContent = '已保存'; event.currentTarget.disabled = true; });
    return;
  }
  if (page === 'enterprise') {
    const data = await api('/api/v1/enterprise-config');
    main.innerHTML = `<h1>企业配置</h1><p class="muted">修改将影响后续图片任务。</p><form id="config"><label>企业名称<input name="brand_name" value="${escapeHtml(data.brand_name || '')}"></label><label>主色<input name="primary_color" value="${escapeHtml(data.primary_color || '')}"></label><label>辅助色<input name="secondary_color" value="${escapeHtml(data.secondary_color || '')}"></label><label>Slogan<input name="slogan" value="${escapeHtml(data.slogan || '')}"></label><button class="primary">保存配置</button></form>`;
    document.querySelector('#config').onsubmit = async event => { event.preventDefault(); await api('/api/v1/enterprise-config', { method: 'PUT', body: JSON.stringify({ payload: Object.fromEntries(new FormData(event.target)) }) }); alert('已保存'); };
    return;
  }
  if (page === 'knowledge') {
    const rows = await api('/api/v1/knowledge/files');
    main.innerHTML = `<h1>知识库</h1><form id="knowledge"><label>标题<input name="name" required></label><label>知识内容<textarea name="content" required></textarea></label><button class="primary">上传并处理</button></form><div class="card">${rows.map(item => `<p>${escapeHtml(item.name)} · ${escapeHtml(item.status)} <button data-delete="${item.id}" class="text-button danger">删除</button></p>`).join('') || '<p class="muted">暂无知识文件</p>'}</div>`;
    document.querySelector('#knowledge').onsubmit = async event => { event.preventDefault(); await api('/api/v1/knowledge/text', { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); render('knowledge'); };
    document.querySelectorAll('[data-delete]').forEach(button => button.onclick = async () => { await api(`/api/v1/knowledge/files/${button.dataset.delete}`, { method: 'DELETE' }); render('knowledge'); });
    return;
  }
  if (page === 'assets') {
    const rows = await api('/api/v1/assets');
    main.innerHTML = `<h1>企业素材库</h1><form id="asset"><label>名称<input name="name" required></label><label>分类<input name="asset_type" value="poster_reference" required></label><label>URL<input name="url" type="url" required></label><label>描述<textarea name="description"></textarea></label><button class="primary">添加素材</button></form><div class="card">${rows.map(item => `<p>${escapeHtml(item.name)} · ${escapeHtml(item.asset_type)} <button data-delete="${item.id}" class="text-button danger">删除</button></p>`).join('') || '<p class="muted">暂无企业素材</p>'}</div>`;
    document.querySelector('#asset').onsubmit = async event => { event.preventDefault(); const payload = Object.fromEntries(new FormData(event.target)); payload.tags = []; await api('/api/v1/assets', { method: 'POST', body: JSON.stringify(payload) }); render('assets'); };
    document.querySelectorAll('[data-delete]').forEach(button => button.onclick = async () => { await api(`/api/v1/assets/${button.dataset.delete}`, { method: 'DELETE' }); render('assets'); });
  }
}

async function submit(event) {
  event.preventDefault();
  const text = document.querySelector('#message').value.trim();
  if (!text) return;
  const button = event.target.querySelector('button');
  button.disabled = true;
  try {
    const payload = { message: text };
    if (activeConversationId) payload.conversation_id = activeConversationId;
    const task = await api('/api/v1/agents/image-agent/runs', { method: 'POST', body: JSON.stringify(payload) });
    document.querySelector('#messages').innerHTML = `<div class="message">${escapeHtml(text)}</div>${thinkingMarkup()}`;
    poll(task.id);
  } catch (error) { document.querySelector('#messages').insertAdjacentHTML('beforeend', `<p class="error">${escapeHtml(error.message)}</p>`); }
  finally { button.disabled = false; }
}

async function poll(id) {
  try {
    const task = await api(`/api/v1/tasks/${id}`);
    const status = document.querySelector('#status');
    if (!status) return;
    if (task.status === 'completed') {
      activeConversationId = task.conversation_id || activeConversationId;
      const generation = (await api('/api/v1/generations')).find(item => item.task_id === id);
      status.outerHTML = `<section class="result-card"><strong>生成完成</strong><p>${escapeHtml(task.final_response || '图片已生成。')}</p>${generation ? `${preview(generation, '本次生成的海报')}<p><a href="${imageUrl(generation)}" download>下载图片</a> · <button class="primary" data-save-generation="${generation.id}">保存到企业素材库</button></p>` : ''}</section>`;
      document.querySelector('[data-save-generation]')?.addEventListener('click', async event => { await api(`/api/v1/generations/${event.currentTarget.dataset.saveGeneration}/save-to-assets`, { method: 'POST', body: JSON.stringify({ name: 'AI 生成海报' }) }); event.currentTarget.textContent = '已保存'; event.currentTarget.disabled = true; });
      return;
    }
    if (task.status === 'failed') { status.outerHTML = `<div class="error">${escapeHtml(task.user_message || '生成失败，请重新尝试')}</div>`; return; }
    status.querySelector('[data-stage]')?.replaceChildren(document.createTextNode(stageCopy(task.stage)));
    setTimeout(() => poll(id), 1000);
  } catch {
    document.querySelector('#status')?.querySelector('[data-stage]')?.replaceChildren(document.createTextNode('状态更新暂不可用，正在重试。'));
    setTimeout(() => poll(id), 2500);
  }
}

async function boot() { try { me = await api('/api/v1/me'); shell(); } catch { login(); } }
boot();
