/* Customer UI only. 28e has no public PREPARE artifact or CREATE_DRAFT API.
 * The production adapter therefore fails closed; no guessed URL or Chat proxy.
 * An eventual server adapter must supply identity-bound authorization/receipts.
 */
(() => {
  'use strict';
  const SLUG = 'wechat-official-account-writing';
  const pendingAdapter = Object.freeze({
    availability: async () => ({state: 'BACKEND_PENDING'}),
    createDraft: async () => { throw Object.assign(new Error('BACKEND_PENDING'), {code: 'BACKEND_PENDING'}); },
  });
  const tags = new Set('article section div p span h1 h2 h3 h4 h5 h6 strong b em i u s blockquote ul ol li br hr table thead tbody tr th td pre code'.split(' '));
  const discard = new Set('script style iframe object embed link base meta form input button textarea select svg math template noscript'.split(' '));
  const properties = new Set('color background-color font-size font-weight font-style text-align text-decoration line-height letter-spacing margin margin-top margin-bottom margin-left margin-right padding padding-top padding-bottom padding-left padding-right border border-top border-bottom border-left border-right border-radius border-color border-width border-style'.split(' '));
  function sanitizeArticle(html) {
    if (typeof html !== 'string' || !html.trim() || html.length > 256 * 1024) throw new Error('ARTICLE_INVALID');
    // Template is inert: resources/scripts are never inserted into the host DOM.
    const template = document.createElement('template');
    template.innerHTML = html;
    let count = 0;
    const clean = (parent, depth = 0) => {
      if (depth > 64) throw new Error('ARTICLE_INVALID');
      for (const node of [...parent.childNodes]) {
        if (++count > 10000) throw new Error('ARTICLE_INVALID');
        if (node.nodeType === Node.COMMENT_NODE) { node.remove(); continue; }
        if (node.nodeType !== Node.ELEMENT_NODE) continue;
        const tag = node.localName.toLowerCase();
        if (discard.has(tag)) { node.remove(); continue; }
        clean(node, depth + 1);
        if (!tags.has(tag)) { node.replaceWith(...node.childNodes); continue; }
        const style = node.style;
        const safe = [];
        for (const property of [...style]) {
          const value = style.getPropertyValue(property).trim();
          // No URLs, CSS functions/escapes, custom props, positioning or overlays.
          const plain = value.replace(/\b(?:rgb|rgba|hsl|hsla)\([\d.,%\s+-]+\)/g, 'color');
          if (properties.has(property) && /^[#a-zA-Z0-9.,%\s+-]+$/.test(plain) && value.length < 100) safe.push(`${property}:${value}`);
        }
        for (const attribute of [...node.attributes]) node.removeAttribute(attribute.name);
        if (safe.length) node.setAttribute('style', safe.join(';'));
      }
    };
    clean(template.content);
    return template.innerHTML;
  }
  function previewDocument(html) {
    const safe = sanitizeArticle(html);
    return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'"><style>html{overflow-wrap:anywhere}body{box-sizing:border-box;max-width:680px;margin:0 auto;padding:24px 20px;color:#25334b;font:16px/1.8 system-ui,sans-serif}*{box-sizing:border-box;max-width:100%}h1,h2,h3{line-height:1.45}h1{font-size:24px}h2{font-size:20px}p{margin:0 0 16px}blockquote{margin:16px 0;padding:8px 16px;border-left:3px solid #315fe5;background:#f5f8ff}table{display:block;overflow:auto;border-collapse:collapse}td,th{padding:8px;border:1px solid #dce5f7}pre{white-space:pre-wrap}a{color:inherit}</style></head><body>${safe}</body></html>`;
  }
  function articleHtml(content, markdown) {
    const fenced = /^\s*```html\s*\n([\s\S]*?)\n```\s*$/i.exec(content);
    return fenced ? fenced[1] : /^\s*</.test(content) ? content : markdown(content);
  }
  const messages = {
    BACKEND_PENDING: 'BACKEND_PENDING · 正式草稿接口尚未接入，当前不能创建微信草稿。',
    AUTH_REQUIRED: '请重新登录后再操作。',
    FORBIDDEN: '你没有创建微信草稿的操作权限。',
    AGENT_UNAVAILABLE: '公众号智能体暂未启用，不能创建草稿。',
    ACCOUNT_UNCONFIGURED: '请先在个人中心配置微信公众号。',
    ACCOUNT_UNVERIFIED: '公众号尚未通过真实连接验证，请先到个人中心测试连接。',
    RESULT_UNCONFIRMED: '草稿结果未确认，不能显示成功。请先核对草稿箱，再决定是否重试。',
    REQUEST_FAILED: '创建草稿失败。请先核对草稿箱，再确认重试；系统不会自动重复提交。',
  };
  function confirmedDraft(result, context) {
    const id = value => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,256}$/.test(value);
    return result?.status === 'created' && result.confirmed === true && id(result.draft_media_id)
      && id(result.receipt_id) && result.agent_id === context.agentId && result.message_id === context.messageId;
  }
  function mount({article, main, agent, route, content, messageId, request, markdown, modal, adapter = pendingAdapter, readAccount}) {
    if (agent?.slug !== SLUG || !messageId || !content?.trim() || article.querySelector('.wechat-article-actions')) return null;
    const context = Object.freeze({agentId: agent.id, messageId});
    const slot = document.createElement('section'); slot.className = 'wechat-article-actions';
    slot.setAttribute('aria-label', '公众号文章操作');
    slot.innerHTML = '<div class="wechat-article-buttons"><button type="button" class="button secondary" data-article-preview>预览文章</button><button type="button" class="button primary" data-create-draft disabled>创建微信草稿</button></div><p class="wechat-article-feedback" role="status" aria-live="polite">正在读取草稿操作状态…</p>';
    article.append(slot);
    const preview = slot.querySelector('[data-article-preview]'), draft = slot.querySelector('[data-create-draft]'), feedback = slot.querySelector('[role="status"]');
    let busy = false, checking = true, succeeded = false, ready = false, confirmation = null;
    const current = () => main.isConnected && article.isConnected && document.querySelector('#main') === main;
    const say = code => { if (current()) feedback.textContent = messages[code] || messages.REQUEST_FAILED; };
    const sync = () => { if (!current()) return; draft.disabled = busy || checking || !ready || succeeded; draft.setAttribute('aria-busy', String(busy || checking)); draft.textContent = busy ? '正在创建草稿…' : succeeded ? '微信草稿已创建' : '创建微信草稿'; };
    async function check(fresh = false) {
      checking = true; ready = false; sync();
      try {
        const metadata = fresh ? await request(`/api/v1/agents/${encodeURIComponent(route)}`) : agent;
        if (!current()) return false;
        if (metadata?.id !== context.agentId || metadata.slug !== SLUG || metadata.enabled !== true) throw {code: 'AGENT_UNAVAILABLE'};
        const account = !fresh && readAccount ? await readAccount() : await request('/api/v1/profile/wechat-account');
        if (!account?.app_secret_configured) throw {code: 'ACCOUNT_UNCONFIGURED'};
        if (account.verification_status !== 'connected') throw {code: 'ACCOUNT_UNVERIFIED'};
        const permission = await adapter.availability(context);
        if (!current()) return false;
        if (permission?.state === 'DENIED') throw {code: 'FORBIDDEN'};
        if (permission?.state !== 'READY') throw {code: 'BACKEND_PENDING'};
        if (permission.can_create_draft !== true || permission.agent_id !== context.agentId || permission.message_id !== messageId) throw {code: 'FORBIDDEN'};
        ready = true; feedback.textContent = '创建前需确认；仅保存到草稿箱，不发布、不群发。'; return true;
      } catch (error) {
        say(error?.status === 401 ? 'AUTH_REQUIRED' : error?.status === 403 ? 'FORBIDDEN' : error?.status === 404 ? 'AGENT_UNAVAILABLE' : error?.code);
        return false;
      } finally { checking = false; sync(); }
    }
    preview.onclick = () => {
      if (!current() || document.querySelector('#wechat-article-preview')) return;
      let doc;
      try { doc = previewDocument(articleHtml(content, markdown)); } catch { feedback.textContent = '文章内容过大或无效，暂无法预览。'; return; }
      const backdrop = document.createElement('div'); backdrop.className = 'dialog-backdrop'; backdrop.id = 'wechat-article-preview';
      backdrop.innerHTML = '<section class="wechat-preview-dialog" role="dialog" aria-modal="true" aria-labelledby="wechat-preview-title"><button type="button" class="dialog-close" aria-label="关闭文章预览">×</button><header><h2 id="wechat-preview-title">文章排版预览</h2><p>生成正文的安全预览；尚未完成 PREPARE 校验或上传。外部图片、链接和脚本已禁用。</p></header><iframe title="公众号文章安全预览" sandbox="" referrerpolicy="no-referrer"></iframe><footer><button type="button" class="button secondary" data-preview-close>返回对话</button></footer></section>';
      backdrop.querySelector('iframe').srcdoc = doc;
      document.querySelector('#app').append(backdrop);
      const close = modal(backdrop, preview); backdrop.querySelector('[data-preview-close]').onclick = close;
    };
    draft.onclick = async () => {
      if (!current() || busy || checking || succeeded || confirmation) return;
      if (!await check(true) || !current()) return;
      const backdrop = document.createElement('div'); backdrop.className = 'dialog-backdrop'; backdrop.id = 'wechat-draft-confirm';
      backdrop.innerHTML = '<section class="wechat-draft-dialog" role="dialog" aria-modal="true" aria-labelledby="wechat-draft-title"><button type="button" class="dialog-close" aria-label="关闭草稿确认">×</button><h2 id="wechat-draft-title">确认创建微信草稿？</h2><p>将这篇文章保存到当前企业已连接的微信公众号草稿箱。不会发布、群发或删除任何内容。</p><div class="wechat-article-buttons"><button type="button" class="button secondary" data-draft-cancel>取消</button><button type="button" class="button primary" data-draft-confirm>确认创建草稿</button></div></section>';
      document.querySelector('#app').append(backdrop);
      const dismiss = modal(backdrop, draft);
      confirmation = backdrop;
      const close = () => { dismiss(); confirmation = null; };
      // Modal Escape/mask/close also clears the pending-confirmation sentinel.
      const watch = new MutationObserver(() => { if (!backdrop.isConnected) { confirmation = null; watch.disconnect(); } });
      watch.observe(backdrop.parentNode, {childList: true});
      backdrop.querySelector('[data-draft-cancel]').onclick = close;
      backdrop.querySelector('[data-draft-confirm]').onclick = async () => {
        if (busy || succeeded || !current()) return;
        busy = true; close(); sync(); feedback.textContent = '正在创建微信草稿，请勿重复提交…';
        try {
          if (!await check(true) || !current()) return;
          // Identity only. No HTML, credential, tenant, executable or storage path.
          const result = await adapter.createDraft({...context, userConfirmed: true});
          if (!current()) return;
          if (!confirmedDraft(result, context)) throw {code: 'RESULT_UNCONFIRMED'};
          succeeded = true; feedback.textContent = `微信草稿已创建 · ${result.draft_media_id} · 回执 ${result.receipt_id}`;
        } catch (error) {
          ready = false;
          say(error?.status === 401 ? 'AUTH_REQUIRED' : error?.status === 403 ? 'FORBIDDEN' : error?.status === 404 ? 'AGENT_UNAVAILABLE' : error?.code || 'REQUEST_FAILED');
          // Retry is always a new explicit click + confirmation; never auto-send.
          if (error?.status !== 401 && error?.status !== 403 && error?.status !== 404) ready = true;
        } finally { busy = false; sync(); }
      };
    };
    check();
    return Object.freeze({refresh: check});
  }
  window.WorkbenchWechatArticle = Object.freeze({mount, sanitizeArticle, previewDocument, confirmedDraft, pendingAdapter});
})();
