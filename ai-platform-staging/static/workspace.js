const state = { agents: [], runs: [], templates: [], activeAgent: null, quota: { balance: 0, unit: '次' } };
const $ = selector => document.querySelector(selector);
const createDialog = $('#create-dialog');
const agentForm = $('#agent-form');
const taskForm = $('#task-form');
const taskInput = $('#task-input');
const sendButton = taskForm.querySelector('button');

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || '请求失败，请稍后重试。');
  return result;
}

function initial(value) { return (value || 'K').trim().slice(0, 1).toUpperCase(); }
function templateFor(key) { return state.templates.find(template => template.key === key) || { name: '自定义智能体', description: '' }; }
function templateMark(key) { return ({ research: '◈', sales: '↗', service: '◌', custom: '◇' })[key] || '◇'; }
function formatTime(value) {
  if (!value) return '刚刚';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '刚刚';
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
}
function makeText(tag, text, className = '') { const element = document.createElement(tag); element.textContent = text; if (className) element.className = className; return element; }
function renderQuota() { $('#quota-balance').textContent = `${Number(state.quota.balance || 0)} ${state.quota.unit || '次'}`; }

function renderAgents() {
  const list = $('#agent-list'); const grid = $('#agent-grid');
  list.replaceChildren(); grid.replaceChildren(); $('#agent-count').textContent = state.agents.length;
  if (!state.agents.length) {
    list.append(makeText('p', '还没有智能体。点击上方创建第一个。', 'empty-sidebar'));
    const empty = document.createElement('div'); empty.className = 'empty-card';
    const copy = makeText('div', '从一个业务角色开始，\n把经验变成智能体。'); const button = makeText('button', '创建智能体 →');
    button.type = 'button'; button.onclick = openCreateDialog; empty.append(copy, button); grid.append(empty); return;
  }
  state.agents.forEach(agent => {
    const side = document.createElement('button'); side.type = 'button'; side.className = `side-agent${state.activeAgent?.id === agent.id ? ' active' : ''}`;
    const mark = makeText('span', templateMark(agent.template_key), 'agent-mark'); const copy = document.createElement('span'); copy.append(makeText('b', agent.name), makeText('small', templateFor(agent.template_key).name)); side.append(mark, copy); side.onclick = () => openAgent(agent); list.append(side);
    const card = document.createElement('article'); card.className = 'agent-card'; card.tabIndex = 0;
    const top = document.createElement('div'); top.className = 'agent-card-top'; top.append(makeText('span', templateMark(agent.template_key), 'agent-icon'), makeText('span', templateFor(agent.template_key).name, 'agent-template'));
    const footer = document.createElement('footer'); footer.append(makeText('span', `创建于 ${formatTime(agent.created_at)}`), makeText('b', '→'));
    card.append(top, makeText('h3', agent.name), makeText('p', agent.description || templateFor(agent.template_key).description), footer);
    card.onclick = () => openAgent(agent); card.onkeydown = event => { if (event.key === 'Enter') openAgent(agent); }; grid.append(card);
  });
}

function renderRuns() {
  const list = $('#run-list'); list.replaceChildren();
  if (!state.runs.length) { list.append(makeText('div', '智能体开始执行后，运行记录会显示在这里。', 'empty-runs')); return; }
  state.runs.forEach(run => {
    const row = document.createElement('div'); row.className = 'run-row';
    const status = run.status === 'completed' ? '已完成' : run.status === 'failed' ? '未完成' : '运行中';
    row.append(makeText('strong', run.agent_name || '智能体'), makeText('span', run.runtime === 'preview' ? '预览运行' : 'Harness'), makeText('span', formatTime(run.completed_at || run.created_at)), makeText('span', status, `run-state${run.status === 'failed' ? ' failed' : ''}`));
    list.append(row);
  });
}

function renderTemplates() {
  const picker = $('#template-picker'); picker.replaceChildren();
  state.templates.forEach((template, index) => {
    const label = document.createElement('label'); label.className = 'template-option';
    const input = document.createElement('input'); input.type = 'radio'; input.name = 'template_key'; input.value = template.key; input.checked = index === 0;
    const card = document.createElement('span'); card.append(makeText('b', template.name), document.createTextNode(template.description)); label.append(input, card); picker.append(label);
  });
  picker.onchange = () => {
    const selected = templateFor(new FormData(agentForm).get('template_key'));
    agentForm.elements.name.value = selected.name; agentForm.elements.description.value = selected.description;
    const source = { research: '你是一名严谨的行业研究助手。先澄清研究范围，再按结论、证据、风险和下一步输出。', sales: '你是一名企业销售策略助手。围绕客户目标、决策角色、价值假设和下一步行动给出可执行建议。', service: '你是一名专业客户服务助手。回答准确、简洁，无法确认的信息要明确说明并建议人工跟进。', custom: '你是一个企业智能体。遵守用户设定的角色、边界和交付格式。' };
    agentForm.elements.instructions.value = source[selected.key] || source.custom;
  };
  picker.onchange();
}

function showWorkspace() { $('#workspace-view').classList.remove('hidden'); $('#chat-view').classList.add('hidden'); $('#page-title').textContent = '工作台'; document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.page === 'workspace')); }
function showChat() { $('#workspace-view').classList.add('hidden'); $('#chat-view').classList.remove('hidden'); $('#page-title').textContent = '智能体对话'; }

function renderMessage(role, content) {
  const message = document.createElement('article'); message.className = `message ${role}`;
  const avatar = makeText('span', role === 'user' ? '你' : templateMark(state.activeAgent.template_key), 'message-avatar');
  message.append(avatar, makeText('div', content, 'message-body')); $('#conversation').append(message);
}

async function openAgent(agent) {
  state.activeAgent = agent; renderAgents(); showChat();
  $('#chat-agent-name').textContent = agent.name; $('#chat-template').textContent = templateFor(agent.template_key).name.toUpperCase(); taskInput.disabled = true; sendButton.disabled = true;
  const conversation = $('#conversation'); conversation.replaceChildren(); conversation.append(makeText('p', `${agent.description || templateFor(agent.template_key).description}\n\n直接描述一项需要完成的业务任务。`, 'agent-intro'));
  try {
    const result = await api(`/api/workspace/agents/${agent.id}/messages`);
    if (result.items.length) { conversation.replaceChildren(); result.items.forEach(item => renderMessage(item.role, item.content)); }
    taskInput.disabled = false; sendButton.disabled = false; taskInput.focus();
  } catch (error) { conversation.append(makeText('p', error.message, 'agent-intro')); }
}

function openCreateDialog() { $('#form-message').textContent = ''; agentForm.reset(); renderTemplates(); if (!createDialog.open) createDialog.showModal(); }
function closeCreateDialog() { createDialog.close(); }

async function bootstrap() {
  const data = await api('/api/workspace/bootstrap');
  state.agents = data.agents; state.runs = data.runs; state.templates = data.templates; state.quota = data.quota || state.quota;
  const displayName = data.user.name || data.user.email || data.user.login_phone || '锟元用户'; $('#profile-name').textContent = displayName; $('#profile-avatar').textContent = initial(displayName);
  renderQuota(); renderTemplates(); renderAgents(); renderRuns();
}

$('#create-agent').onclick = openCreateDialog; document.querySelectorAll('[data-open-create]').forEach(button => button.onclick = openCreateDialog); document.querySelectorAll('[data-show-agents]').forEach(button => button.onclick = () => { window.scrollTo({ top: document.querySelector('.agent-grid-section').offsetTop - 30, behavior: 'smooth' }); });
$('.close-dialog').onclick = closeCreateDialog; $('#back-to-workspace').onclick = showWorkspace;
document.querySelectorAll('.nav-item').forEach(button => button.onclick = () => { if (button.dataset.page === 'workspace') showWorkspace(); else if (button.dataset.page === 'agents') window.scrollTo({ top: document.querySelector('.agent-grid-section').offsetTop - 30, behavior: 'smooth' }); else window.scrollTo({ top: document.querySelector('.runs-section').offsetTop - 30, behavior: 'smooth' }); });

agentForm.onsubmit = async event => {
  event.preventDefault(); const message = $('#form-message'); const form = new FormData(agentForm); const payload = Object.fromEntries(form.entries()); message.textContent = '正在创建智能体…';
  try {
    const result = await api('/api/workspace/agents', { method: 'POST', body: JSON.stringify(payload) });
    state.agents.unshift(result.agent); createDialog.close(); renderAgents(); await openAgent(result.agent);
  } catch (error) { message.textContent = error.message; }
};

taskForm.onsubmit = async event => {
  event.preventDefault(); const message = taskInput.value.trim(); if (!message || !state.activeAgent) return;
  if (Number(state.quota.balance || 0) < 1) { renderMessage('assistant', 'AI 使用额度不足，请联系管理员配置额度后再试。'); return; }
  taskInput.value = ''; taskInput.disabled = true; sendButton.disabled = true; renderMessage('user', message);
  try {
    const result = await api(`/api/workspace/agents/${state.activeAgent.id}/messages`, { method: 'POST', body: JSON.stringify({ message }) });
    renderMessage('assistant', result.reply); state.quota = result.quota || state.quota; renderQuota(); state.runs.unshift({ ...result.run, agent_name: state.activeAgent.name, completed_at: result.created_at }); renderRuns();
  } catch (error) { renderMessage('assistant', error.message); }
  finally { taskInput.disabled = false; sendButton.disabled = false; taskInput.focus(); }
};

bootstrap().catch(error => { $('#profile-name').textContent = '连接失败'; $('#agent-grid').append(makeText('p', error.message, 'empty-runs')); });
