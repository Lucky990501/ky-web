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

fetch('/api/site-content', { cache: 'no-store' }).then(response => response.ok ? response.json() : {}).then(content => {
  Object.entries(content).forEach(([key, value]) => document.querySelectorAll(`[data-content="${key}"]`).forEach(element => { element.textContent = value; }));
}).catch(() => {});

const authTrigger = $('#auth-trigger'), authForm = $('#auth-form'), authMessage = $('#auth-message');
header.querySelector('.header-actions .open')?.remove();

const emailInput = authForm.elements.email;
const emailField = emailInput.closest('label');
const profilePhoneInput = authForm.elements.phone;
profilePhoneInput.name = 'contact_phone';
profilePhoneInput.autocomplete = 'tel';
profilePhoneInput.closest('label').firstChild.textContent = '联系电话（可选）';
emailField.classList.add('auth-email-field');
const phoneField = document.createElement('label');
phoneField.className = 'auth-phone-field';
phoneField.append('手机号');
const phoneInput = document.createElement('input');
phoneInput.name = 'login_phone';
phoneInput.type = 'tel';
phoneInput.inputMode = 'numeric';
phoneInput.autocomplete = 'tel';
phoneInput.placeholder = '请输入 11 位手机号';
phoneInput.maxLength = 20;
phoneField.append(phoneInput);
emailField.before(phoneField);

const identitySwitch = document.createElement('div');
identitySwitch.className = 'auth-identity-switch';
identitySwitch.setAttribute('role', 'group');
identitySwitch.setAttribute('aria-label', '登录方式');
identitySwitch.innerHTML = '<span>登录方式</span><button class="active" type="button" data-auth-identity="email" aria-pressed="true">邮箱</button><button type="button" data-auth-identity="phone" aria-pressed="false">手机号</button>';
document.querySelector('.auth-switch').after(identitySwitch);

let authMode = 'login', authIdentity = 'email';
function setAuthIdentity(identity) {
  authIdentity = identity;
  const usingPhone = identity === 'phone';
  emailField.hidden = usingPhone;
  phoneField.hidden = !usingPhone;
  emailInput.disabled = usingPhone;
  phoneInput.disabled = !usingPhone;
  emailInput.required = !usingPhone;
  phoneInput.required = usingPhone;
  document.querySelectorAll('[data-auth-identity]').forEach(button => {
    const active = button.dataset.authIdentity === identity;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  authMessage.textContent = '';
}
function setAuthMode(mode) {
  authMode = mode;
  authModal.dataset.mode = mode;
  $('#auth-title').textContent = mode === 'login' ? '登录您的账号' : '创建您的账号';
  $('#auth-note').textContent = mode === 'login' ? '登录后可保留您的客服会话与咨询资料。' : '注册后可保留客服会话与咨询资料。';
  $('#auth-submit').textContent = mode === 'login' ? '登录' : '创建账号';
  authForm.password.autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  document.querySelectorAll('[data-auth-mode]').forEach(button => button.classList.toggle('active', button.dataset.authMode === mode));
  authMessage.textContent = '';
}
document.querySelectorAll('[data-auth-mode]').forEach(button => button.onclick = () => setAuthMode(button.dataset.authMode));
document.querySelectorAll('[data-auth-identity]').forEach(button => button.onclick = () => setAuthIdentity(button.dataset.authIdentity));
authTrigger.onclick = () => {
  if (sessionStorage.getItem(tokenKey)) { sessionStorage.removeItem(tokenKey); syncAccount(); return; }
  setAuthMode('login'); openDialog(authModal);
};
authForm.onsubmit = async event => {
  event.preventDefault();
  const fields = Object.fromEntries(new FormData(authForm));
  const identity = authIdentity === 'phone' ? { phone: fields.login_phone } : { email: fields.email };
  const payload = authMode === 'login' ? { identity_type: authIdentity, ...identity, password: fields.password } : {
    identity_type: authIdentity, ...identity, password: fields.password, consent: fields.consent === 'on',
    profile: { name: fields.name, company: fields.company, phone: fields.contact_phone || fields.login_phone || '', job_title: fields.job_title }
  };
  authMessage.textContent = '正在验证…';
  try {
    const result = await request(`/api/auth/${authMode === 'login' ? 'login' : 'register'}`, { method: 'POST', body: JSON.stringify(payload) });
    sessionStorage.setItem(tokenKey, result.token);
    authModal.close(); authForm.reset(); setAuthIdentity(authIdentity); syncAccount(); loadChatHistory();
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
document.querySelectorAll('.industry').forEach(button => button.onclick = () => { document.querySelectorAll('.industry').forEach(item => item.classList.remove('active')); button.classList.add('active'); $('.industry-core strong').textContent = button.dataset.name; $('.industry-core i').textContent = button.dataset.desc; });
setAuthMode('login'); setAuthIdentity('email'); syncAccount();
