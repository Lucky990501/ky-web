const $ = (selector) => document.querySelector(selector);
const modal = $('#modal'), authModal = $('#auth-modal'), header = $('header');
const dots = $('.dots'), sections = [...document.querySelectorAll('.panel')], links = [...document.querySelectorAll('.dots a')];
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const tokenKey = 'kunyuan_user_token', chatKey = 'kunyuan_chat_session';

async function request(path, options = {}, authenticated = false) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (authenticated && sessionStorage.getItem(tokenKey)) headers.Authorization = `Bearer ${sessionStorage.getItem(tokenKey)}`;
  const response = await fetch(path, { ...options, headers });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(result.error || '请求失败，请稍后再试。');
  return result;
}

function openDialog(dialog) { if (!dialog.open) dialog.showModal(); }
document.querySelectorAll('.open').forEach(button => button.onclick = () => openDialog(modal));
modal.querySelector('.close').onclick = () => modal.close();
authModal.querySelector('.close').onclick = () => authModal.close();
[modal, authModal].forEach(dialog => dialog.onclick = event => { if (event.target === dialog) dialog.close(); });

$('#modal form').onsubmit = async event => {
  event.preventDefault();
  const form = event.currentTarget, message = form.querySelector('.message');
  const [name, company, contact, challenge] = [...form.querySelectorAll('input:not([type=checkbox]),textarea')].map(field => field.value.trim());
  message.textContent = '正在提交…';
  try {
    const result = await request('/api/leads', { method: 'POST', body: JSON.stringify({ name, company, contact, challenge }) });
    message.textContent = result.message;
    form.reset();
  } catch (error) { message.textContent = error.message; }
};

const siteContentTargets={hero_title:'#s1 h1',hero_summary:'[data-content="hero_summary"]',hero_cta:'#s1 .copy .button',problem_title:'#s2 h2',problem_1:'#s2 .problems article:nth-child(1) p',problem_2:'#s2 .problems article:nth-child(2) p',problem_3:'#s2 .problems article:nth-child(3) p',problem_4:'#s2 .problems article:nth-child(4) p',path_title:'#s3 h2',path_1_title:'#s3 .steps article:nth-child(1) h3',path_1_desc:'#s3 .steps article:nth-child(1) p',path_2_title:'#s3 .steps article:nth-child(2) h3',path_2_desc:'#s3 .steps article:nth-child(2) p',path_3_title:'#s3 .steps article:nth-child(3) h3',path_3_desc:'#s3 .steps article:nth-child(3) p',sprint_title:'#s4 h2',sprint_summary:'#s4 .sprint-copy>p:not(.kicker)',method_title:'#s5 h2',industry_title:'#s6 h2',industry_1:'#s6 .industry:nth-child(1)',industry_2:'#s6 .industry:nth-child(2)',industry_3:'#s6 .industry:nth-child(3)',industry_4:'#s6 .industry:nth-child(4)',cta_title:'#s7 h2',cta_summary:'[data-content="cta_summary"]'};
fetch('/api/site-content',{cache:'no-store'}).then(response=>response.ok?response.json():{}).then(content=>{Object.entries(content).forEach(([key,value])=>{document.querySelectorAll(`[data-content="${key}"]`).forEach(element=>{element.textContent=value});const element=document.querySelector(siteContentTargets[key]);if(element)element.textContent=value})}).catch(()=>{});

const authTrigger = $('#auth-trigger'), authForm = $('#auth-form'), authMessage = $('#auth-message');
header.querySelector('.header-actions .open')?.remove();

const emailInput = authForm.elements.email;
const emailField = emailInput.closest('label');
authForm.elements.password.minLength = 9;
const phoneInput = authForm.elements.phone;
const phoneField = phoneInput.closest('label');
const nicknameInput = authForm.elements.name;
nicknameInput.closest('label').firstChild.textContent = '昵称 *';
nicknameInput.autocomplete = 'nickname';
nicknameInput.maxLength = 50;
nicknameInput.placeholder = '请输入您希望展示的昵称';
phoneField.firstChild.textContent = '手机号 *';
phoneInput.type = 'tel';
phoneInput.inputMode = 'numeric';
phoneInput.autocomplete = 'tel';
phoneInput.placeholder = '请输入 11 位手机号';
phoneInput.maxLength = 20;
phoneInput.pattern = '1[3-9]\\d{9}';
['company', 'job_title'].forEach(name => authForm.elements[name].closest('label').remove());
authForm.querySelector('input[name="consent"]').closest('label').remove();
const referralField = document.createElement('label');
referralField.className = 'auth-profile';
referralField.append('推荐码（可选）');
const referralInput = document.createElement('input');
referralInput.name = 'referral_code';
referralInput.autocomplete = 'off';
referralInput.maxLength = 64;
referralInput.placeholder = '如有推荐码，请填写';
referralField.append(referralInput);
authForm.querySelector('#auth-submit').before(referralField);

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const phonePattern = /^1[3-9]\d{9}$/;
emailField.firstChild.textContent = '邮箱 *';
authForm.elements.password.closest('label').firstChild.textContent = '密码 *';
function validateIdentityField(field) {
  const value = field.value.trim();
  const valid = field === emailInput ? emailPattern.test(value) : phonePattern.test(value.replace(/[\s-]/g, '').replace(/^(\+86|0086)/, ''));
  field.setCustomValidity(valid || !value ? '' : field === emailInput ? '请输入有效的邮箱地址。' : '请输入有效的 11 位中国大陆手机号。');
}
[emailInput, phoneInput].forEach(field => {
  field.addEventListener('input', () => validateIdentityField(field));
  field.addEventListener('blur', () => validateIdentityField(field));
});

let authMode = 'login';
function setAuthMode(mode) {
  authMode = mode;
  authModal.dataset.mode = mode;
  $('#auth-title').textContent = mode === 'login' ? '登录您的账号' : '创建您的账号';
  $('#auth-note').textContent = mode === 'login' ? '登录后可保留您的客服会话与咨询资料。' : '注册后可保留客服会话与咨询资料。';
  $('#auth-submit').textContent = mode === 'login' ? '登录' : '创建账号';
  authForm.password.autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  nicknameInput.required = mode === 'register';
  nicknameInput.disabled = mode === 'login';
  phoneInput.required = mode === 'register';
  phoneInput.disabled = mode === 'login';
  referralInput.disabled = mode === 'login';
  document.querySelectorAll('[data-auth-mode]').forEach(button => button.classList.toggle('active', button.dataset.authMode === mode));
  authMessage.textContent = '';
}
document.querySelectorAll('[data-auth-mode]').forEach(button => button.onclick = () => setAuthMode(button.dataset.authMode));
authTrigger.onclick = () => {
  if (sessionStorage.getItem(tokenKey)) { sessionStorage.removeItem(tokenKey); syncAccount(); return; }
  setAuthMode('login'); openDialog(authModal);
};
authForm.onsubmit = async event => {
  event.preventDefault();
  [emailInput, phoneInput].forEach(field => { if (!field.disabled) validateIdentityField(field); });
  if (!authForm.reportValidity()) return;
  const fields = Object.fromEntries(new FormData(authForm));
  const payload = authMode === 'login' ? { identity_type: 'email', email: fields.email, password: fields.password } : {
    email: fields.email, phone: fields.phone, password: fields.password, referral_code: fields.referral_code || '',
    profile: { name: fields.name, phone: fields.phone }
  };
  authMessage.textContent = '正在验证…';
  try {
    const result = await request(`/api/auth/${authMode === 'login' ? 'login' : 'register'}`, { method: 'POST', body: JSON.stringify(payload) });
    sessionStorage.setItem(tokenKey, result.token);
    authModal.close(); authForm.reset(); setAuthMode(authMode); syncAccount(); loadChatHistory();
  } catch (error) { authMessage.textContent = error.message; }
};
async function syncAccount() {
  const token = sessionStorage.getItem(tokenKey);
  if (!token) { authTrigger.textContent = '登录'; return; }
  try {
    const { user } = await request('/api/auth/me', {}, true);
    authTrigger.textContent = `${user.name || user.email || user.login_phone} · 退出`;
  } catch (_) { sessionStorage.removeItem(tokenKey); authTrigger.textContent = '登录'; }
}

const chatPanel = $('#chat-panel'), chatTrigger = $('#chat-trigger'), chatMessages = $('#chat-messages'), chatForm = $('#chat-form'), chatInput = $('#chat-input');
let chatSession = localStorage.getItem(chatKey);
if (!chatSession) { chatSession = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, ''); localStorage.setItem(chatKey, chatSession); }
function addMessage(role, content) {
  const message = document.createElement('div');
  message.className = `chat-message ${role === 'user' ? 'user' : ''}`;
  message.textContent = content;
  chatMessages.append(message); chatMessages.scrollTop = chatMessages.scrollHeight;
}
async function loadChatHistory() {
  try {
    const token = sessionStorage.getItem(tokenKey);
    const response = await fetch(`/api/chat/history?session=${encodeURIComponent(chatSession)}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    const data = await response.json();
    chatMessages.replaceChildren();
    if (data.items?.length) data.items.forEach(item => addMessage(item.role, item.content));
    else addMessage('assistant', '你好，我是锟元AI智能客服。你可以告诉我所在行业、当前业务问题，或直接咨询 AI 转型诊断。');
  } catch (_) { if (!chatMessages.children.length) addMessage('assistant', '你好，我是锟元AI智能客服。请告诉我你想了解什么。'); }
}
chatTrigger.onclick = () => { const opening = chatPanel.hidden; chatPanel.hidden = !opening; chatTrigger.setAttribute('aria-expanded', String(opening)); if (opening) { loadChatHistory(); chatInput.focus(); } };
$('#chat-close').onclick = () => { chatPanel.hidden = true; chatTrigger.setAttribute('aria-expanded', 'false'); };
document.querySelectorAll('.chat-suggestions button').forEach(button => button.onclick = () => { chatInput.value = button.textContent; chatForm.requestSubmit(); });
chatForm.onsubmit = async event => {
  event.preventDefault(); const message = chatInput.value.trim(); if (!message) return;
  chatInput.value = ''; addMessage('user', message); chatInput.disabled = true;
  try { const result = await request('/api/chat', { method: 'POST', body: JSON.stringify({ session_id: chatSession, message }) }, Boolean(sessionStorage.getItem(tokenKey))); addMessage('assistant', result.reply); }
  catch (error) { addMessage('assistant', error.message); } finally { chatInput.disabled = false; chatInput.focus(); }
};

const activate = section => { header.dataset.theme = section.dataset.theme; dots.classList.toggle('dark', section.dataset.theme === 'dark'); links.forEach(link => link.classList.toggle('active', link.getAttribute('href') === `#${section.id}`)); };
if (reduced) sections.forEach(section => section.classList.add('visible'));
else { const observer = new IntersectionObserver(entries => entries.forEach(entry => { if (entry.isIntersecting) { entry.target.classList.add('visible'); activate(entry.target); } }), { threshold: .55 }); sections.forEach(section => observer.observe(section)); }
const heroGraph = $('.hero-graph'), knowledgeGraph = $('.knowledge-graph');
if (!reduced && heroGraph && knowledgeGraph && matchMedia('(min-width: 841px)').matches) {
  const clamp = value => Math.max(0, Math.min(1, value));
  const ease = value => value * value * (3 - 2 * value);
  const paths = [...knowledgeGraph.querySelectorAll('.graph-lines path')];
  const terminals = [...knowledgeGraph.querySelectorAll('.graph-node:not(.node-core)')];
  const keys = ['knowledge', 'process', 'rule', 'data', 'agent', 'eval'];
  let frame = 0;

  heroGraph.classList.add('graph-scroll-ready');
  paths.forEach((path, index) => {
    path.setAttribute('pathLength', '1');
    if (index < keys.length) path.dataset.link = keys[index];
  });
  terminals.forEach((node, index) => {
    node.dataset.graphNode = keys[index];
    node.addEventListener('pointerenter', () => { knowledgeGraph.dataset.active = keys[index]; });
    node.addEventListener('pointerleave', () => { delete knowledgeGraph.dataset.active; });
  });

  const renderGraph = () => {
    frame = 0;
    const rect = heroGraph.getBoundingClientRect();
    const travel = Math.max(window.innerHeight * .9, rect.height - window.innerHeight * .25);
    const progress = ease(clamp(-rect.top / travel));
    const graphScale = .74 + progress * .26;
    knowledgeGraph.style.setProperty('--graph-scale', graphScale.toFixed(3));
    knowledgeGraph.style.setProperty('--graph-x', `${(progress - .5) * 10}px`);
    knowledgeGraph.style.setProperty('--graph-y', `${(1 - progress) * 12}px`);
    knowledgeGraph.style.setProperty('--core-scale', (.82 + progress * .18).toFixed(3));

    paths.forEach((path, index) => {
      const delay = index < keys.length ? .10 + index * .075 : .46 + (index - keys.length) * .1;
      const draw = ease(clamp((progress - delay) / .36));
      path.style.setProperty('--line-progress', draw.toFixed(3));
      path.style.setProperty('--line-opacity', (index < keys.length ? draw * .86 : draw * .38).toFixed(3));
    });
    terminals.forEach((node, index) => {
      const reveal = ease(clamp((progress - (.27 + index * .07)) / .25));
      node.style.setProperty('--node-reveal', reveal.toFixed(3));
      node.style.setProperty('--node-y', `${(1 - reveal) * 28}px`);
      node.style.setProperty('--node-scale', (.78 + reveal * .22).toFixed(3));
      node.style.pointerEvents = reveal > .88 ? 'auto' : 'none';
    });
  };
  const scheduleGraph = () => { if (!frame) frame = requestAnimationFrame(renderGraph); };
  addEventListener('scroll', scheduleGraph, { passive: true });
  addEventListener('resize', scheduleGraph, { passive: true });
  renderGraph();
}
document.querySelectorAll('.industry').forEach(button => button.onclick = () => { document.querySelectorAll('.industry').forEach(item => item.classList.remove('active')); button.classList.add('active'); $('.industry-core strong').textContent = button.dataset.name; $('.industry-core i').textContent = button.dataset.desc; });
setAuthMode('login'); syncAccount();
