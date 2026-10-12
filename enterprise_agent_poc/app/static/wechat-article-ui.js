/* Frontend-only manual delivery, ported from a4a15b5 onto Production base.
 * No WeChat endpoint, draft adapter, Agent qualification or model invocation.
 */
(() => {
  'use strict';
  const SLUG = 'wechat-official-account-writing';
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
  const inlineDefaults = Object.freeze({
    section: 'margin:0;padding:0;max-width:100%;overflow-wrap:anywhere;color:#25334b;font-size:16px;line-height:1.8;text-align:left',
    article: 'margin:0;padding:0', div: 'margin:0;padding:0',
    h1: 'font-size:24px;font-weight:700;line-height:1.5;margin:24px 0 16px',
    h2: 'font-size:20px;font-weight:700;line-height:1.5;margin:24px 0 12px',
    h3: 'font-size:18px;font-weight:700;line-height:1.5;margin:20px 0 12px',
    h4: 'font-size:16px;font-weight:700;line-height:1.6;margin:16px 0 8px',
    h5: 'font-size:16px;font-weight:700;line-height:1.6;margin:16px 0 8px',
    h6: 'font-size:16px;font-weight:700;line-height:1.6;margin:16px 0 8px',
    p: 'font-size:16px;line-height:1.8;margin:0 0 16px',
    strong: 'font-weight:700', b: 'font-weight:700', em: 'font-style:italic', i: 'font-style:italic',
    u: 'text-decoration:underline', s: 'text-decoration:line-through',
    blockquote: 'margin:16px 0;padding:8px 16px;border-left:3px solid #315fe5;background-color:#f5f8ff;line-height:1.8',
    ul: 'margin:12px 0 16px;padding-left:24px;line-height:1.8',
    ol: 'margin:12px 0 16px;padding-left:24px;line-height:1.8', li: 'margin:0 0 8px',
    table: 'width:100%;border-collapse:collapse;font-size:14px;line-height:1.6',
    th: 'padding:8px;border:1px solid #dce5f7;font-weight:700;background-color:#f5f8ff',
    td: 'padding:8px;border:1px solid #dce5f7',
    pre: 'white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.6;margin:16px 0',
    code: 'font-size:14px', hr: 'border:0;border-top:1px solid #dce5f7;margin:20px 0',
  });
  const internalPathPattern = /(?:(?:(?:https?:)?\/\/[^\s/<>"']+)?\/api\/v1\/storage\/generated\/|file:[\\/]{2,3}|(?<![a-z0-9])[a-z]:[\\/]|\\\\[^\s\\/]+[\\/]|\/(?:opt|var|home|tmp|srv|etc|root|proc|mnt|workspace|usr|data|run|private|users)\/)[^\s<>"'，。；、）)\]]*/gi;
  function redactPaths(value) {
    // Display text is untrusted too; never turn internal paths into attachments.
    return String(value || '').replace(internalPathPattern, '[内部路径已移除]');
  }
  function plainArticle(root) {
    const blocks = new Set('section article div p h1 h2 h3 h4 h5 h6 blockquote ul ol table thead tbody tr pre'.split(' '));
    const walk = node => {
      if (node.nodeType === Node.TEXT_NODE) return node.textContent;
      const tag = node.localName;
      if (tag === 'br') return '\n';
      if (tag === 'hr') return '\n';
      const value = [...node.childNodes].map(walk).join('');
      if (tag === 'li') {
        const siblings = [...node.parentNode.children].filter(item => item.localName === 'li');
        return `\n${node.parentNode.localName === 'ol' ? siblings.indexOf(node) + 1 + '.' : '•'} ${value.trim()}\n`;
      }
      if (tag === 'td' || tag === 'th') return value + '\t';
      return blocks.has(tag) ? `\n${value}\n` : value;
    };
    return redactPaths(walk(root)).replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  }
  function buildManualExport(content, markdown, metadata = {}) {
    const raw = articleHtml(content, markdown);
    const input = document.createElement('template'); input.innerHTML = raw;
    const imageCount = input.content.querySelectorAll('img,picture').length;
    // Reuse the reviewed sanitizer. Do not export the preview's stylesheet.
    const safe = document.createElement('template'); safe.innerHTML = sanitizeArticle(raw);
    // Match over contiguous DOM text as well: inline tags/entities must not
    // split an internal path and evade removal. Only text nodes are rewritten.
    const nodes = [], chunks = [], blockTags = new Set('section article div p h1 h2 h3 h4 h5 h6 blockquote ul ol li table tr th td pre br hr'.split(' '));
    let position = 0;
    const boundary = () => { chunks.push('\n'); position++; };
    const collect = parent => { for (const node of parent.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) { nodes.push({node, offset: position}); chunks.push(node.textContent); position += node.textContent.length; }
      else { const block = blockTags.has(node.localName); if (block) boundary(); collect(node); if (block) boundary(); }
    } };
    collect(safe.content);
    const joined = chunks.join('');
    const ranges = [...joined.matchAll(internalPathPattern)].map(match => [match.index, match.index + match[0].length]);
    const redacted = ranges.length > 0;
    for (const {node, offset} of nodes) {
      const original = node.textContent, end = offset + original.length; let cursor = 0, value = '';
      for (const [start, finish] of ranges) {
        if (finish <= offset || start >= end) continue;
        const first = Math.max(0, start - offset), last = Math.min(original.length, finish - offset);
        value += original.slice(cursor, first) + (start >= offset ? '[内部路径已移除]' : ''); cursor = last;
      }
      node.textContent = value + original.slice(cursor);
    }
    for (const node of safe.content.querySelectorAll('*')) {
      const previous = node.style, kept = [];
      for (const property of ['color', 'background-color', 'font-weight', 'font-style', 'text-align', 'text-decoration']) {
        const value = previous.getPropertyValue(property); if (value) kept.push(`${property}:${value}`);
      }
      const size = previous.fontSize, height = previous.lineHeight;
      if (/^\d+(?:\.\d+)?px$/.test(size) && parseFloat(size) >= 12 && parseFloat(size) <= 32) kept.push(`font-size:${size}`);
      if (/^\d+(?:\.\d+)?$/.test(height) && Number(height) >= 1.4 && Number(height) <= 2.4) kept.push(`line-height:${height}`);
      node.removeAttribute('style');
      const style = [inlineDefaults[node.localName], ...kept].filter(Boolean).join(';');
      if (style) node.setAttribute('style', style);
    }
    const wrapper = document.createElement('section'); wrapper.setAttribute('style', inlineDefaults.section); wrapper.append(safe.content);
    const text = plainArticle(wrapper);
    if (!text) throw new Error('ARTICLE_INVALID');
    const field = value => redactPaths(String(value || '').replace(/[\u0000-\u001f]/g, ' ').trim());
    return Object.freeze({html: wrapper.outerHTML, text, imageCount, redacted,
      title: field(metadata.title || wrapper.querySelector('h1')?.textContent).slice(0, 120),
      digest: field(metadata.digest).slice(0, 120), cover: field(metadata.cover).slice(0, 500)});
  }
  async function copyManualExport(result, mode) {
    const clipboard = navigator.clipboard;
    if (mode === 'rich') {
      if (!clipboard?.write || typeof ClipboardItem !== 'function') throw new Error('RICH_CLIPBOARD_UNAVAILABLE');
      // Called directly in the click handler, before any asynchronous preflight.
      await clipboard.write([new ClipboardItem({
        'text/html': new Blob([result.html], {type: 'text/html'}),
        'text/plain': new Blob([result.text], {type: 'text/plain'}),
      })]);
    } else {
      if (!clipboard?.writeText) throw new Error('TEXT_CLIPBOARD_UNAVAILABLE');
      await clipboard.writeText(mode === 'source' ? result.html : result.text);
    }
  }
  function hasArticleHtml(content) {
    if (typeof content !== 'string') return false;
    return /^\s*```html\s*\n[\s\S]*\n```\s*$/i.test(content)
      || /^\s*(?:<!doctype\s+html\b|<(?:html|body|article|section|div|p|h[1-6])(?:\s|>))/i.test(content);
  }
  function downloadManualExport(result) {
    const safe = buildManualExport(result.html, () => '', {title: result.title});
    const title = safe.title || '公众号文章';
    const escapedTitle = title.replace(/[&<>"']/g, value => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[value]));
    const html = previewDocument(safe.html).replace('<head>', '<head><title>' + escapedTitle + '</title>');
    let stem = title.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').replace(/^[.\s]+|[.\s]+$/g, '').slice(0, 80);
    if (!stem || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(stem)) stem = '公众号文章';
    const blob = new Blob([html], {type: 'text/html;charset=utf-8'});
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    try {
      link.href = url; link.download = stem + '.html'; link.hidden = true;
      document.body.append(link); link.click();
      return {filename: link.download, content_type: blob.type, size_bytes: blob.size};
    } finally {
      link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000);
    }
  }
  function mountManual({article, main, agent, content, messageId, markdown, modal, isCurrent, metadata = {}, inlineBody = false}) {
    if (agent?.slug !== SLUG || !messageId || !content?.trim() || typeof isCurrent !== 'function' || article.querySelector('.wechat-article-actions')) return null;
    const current = () => main.isConnected && article.isConnected && article.dataset.assistantMessageId === messageId && isCurrent();
    if (!current()) return null;
    const slot = document.createElement('section'); slot.className = 'wechat-article-actions';
    slot.setAttribute('aria-label', '公众号文章人工导出');
    slot.innerHTML = '<div class="wechat-article-buttons"><button type="button" class="button secondary" data-article-preview>预览文章</button><button type="button" class="button primary" data-copy-rich>复制富文本</button><button type="button" class="button secondary" data-copy-source>复制HTML源码</button><button type="button" class="button secondary" data-copy-plain>复制纯文本</button><button type="button" class="button secondary" data-download-html>下载 HTML</button></div><dl class="wechat-manual-metadata"><div><dt>建议标题</dt><dd data-manual-title></dd></div><div><dt>摘要</dt><dd data-manual-digest></dd></div><div><dt>封面</dt><dd data-manual-cover></dd></div></dl><p class="wechat-article-feedback" role="status" aria-live="polite"></p><p class="wechat-manual-notice">请自行打开目标公众号后台，粘贴正文后保存草稿。标题、摘要和封面需单独填写／上传；正文图片请在后台手动上传。复制不会连接公众号或创建草稿。</p><div data-copy-recovery hidden><label>复制失败，可选中下方内容后按 Ctrl+C（Mac：⌘C）<textarea readonly aria-label="手动复制内容"></textarea></label></div>';
    article.append(slot);
    const feedback = slot.querySelector('[role="status"]'), buttons = [...slot.querySelectorAll('button')], preview = slot.querySelector('[data-article-preview]');
    let result, busy = false;
    try { result = buildManualExport(content, markdown, metadata); }
    catch { buttons.forEach(button => button.disabled = true); feedback.textContent = '文章内容过大或无效，暂无法导出。'; return null; }
    if (inlineBody && hasArticleHtml(content)) {
      const body = article.querySelector('.chat-message-content');
      if (body) {
        // Reuse the exact existing sandbox/CSP preview, now inside the reply.
        // Never insert raw model HTML into the host permission context.
        const frame = document.createElement('iframe'); frame.className = 'wechat-inline-article';
        frame.title = '公众号文章正文'; frame.setAttribute('sandbox', ''); frame.setAttribute('referrerpolicy', 'no-referrer');
        frame.style.height = `${Math.min(640, Math.max(160, 48 + result.text.split('\n').length * 28 + Math.ceil(result.text.length / 32) * 12))}px`;
        frame.srcdoc = previewDocument(result.html);
        body.replaceChildren(frame); body.classList.remove('chat-message-content-markdown'); body.classList.add('wechat-inline-message-content');
        // The manual controls already provide safe rich/source/plain copying.
        // Keep regeneration, but remove the old raw model-source copy action.
        article.querySelector('[data-copy-response]')?.remove();
        const actions = article.querySelector('.message-actions'); if (actions && !actions.children.length) actions.hidden = true;
      }
    }
    slot.querySelector('[data-manual-title]').textContent = result.title || '请在公众号后台手动填写';
    slot.querySelector('[data-manual-digest]').textContent = result.digest || '未提供，请在公众号后台手动填写';
    slot.querySelector('[data-manual-cover]').textContent = result.cover || '请在公众号后台手动上传';
    feedback.textContent = result.redacted ? '安全排版已准备；内部文件路径已移除，请核对正文。' : '安全排版已准备。粘贴到公众号后请核对内容、样式和图片，再手动保存草稿。';
    for (const [selector, mode] of [['[data-copy-rich]', 'rich'], ['[data-copy-source]', 'source'], ['[data-copy-plain]', 'plain']]) {
      slot.querySelector(selector).onclick = async () => {
        if (!current() || busy) return;
        busy = true; buttons.forEach(button => button.disabled = true); slot.setAttribute('aria-busy', 'true');
        feedback.textContent = '正在复制…'; slot.querySelector('[data-copy-recovery]').hidden = true;
        try {
          await copyManualExport(result, mode);
          if (current()) feedback.textContent = mode === 'rich' ? '已复制排版内容，请粘贴到公众号编辑器' : mode === 'source' ? '已复制HTML源码；这是源码文本，不是富文本粘贴。' : '已复制纯文本；不包含排版样式。';
        } catch {
          if (!current()) return;
          feedback.textContent = mode === 'rich' ? '富文本复制失败，未复制排版内容。请使用“复制HTML源码”或“复制纯文本”，或手动复制下方内容。' : '复制失败；请手动选中下方内容并复制。';
          const recovery = slot.querySelector('[data-copy-recovery]'), area = recovery.querySelector('textarea');
          recovery.hidden = false; area.value = mode === 'plain' ? result.text : result.html; area.focus(); area.select();
        } finally {
          busy = false; if (current()) { buttons.forEach(button => button.disabled = false); slot.setAttribute('aria-busy', 'false'); }
        }
      };
    }
    slot.querySelector('[data-download-html]').onclick = () => {
      if (!current() || busy) return;
      try {
        const file = downloadManualExport(result);
        if (current()) feedback.textContent = '已开始下载安全 HTML 文件：' + file.filename + '。图片需在公众号后台手动上传。';
      } catch {
        if (current()) feedback.textContent = 'HTML 下载失败，请重试或使用“复制HTML源码”。';
      }
    };
    preview.onclick = () => {
      if (!current() || document.querySelector('#wechat-article-preview')) return;
      const backdrop = document.createElement('div'); backdrop.className = 'dialog-backdrop'; backdrop.id = 'wechat-article-preview';
      backdrop.innerHTML = '<section class="wechat-preview-dialog" role="dialog" aria-modal="true" aria-labelledby="wechat-preview-title"><button type="button" class="dialog-close" aria-label="关闭文章预览">×</button><header><h2 id="wechat-preview-title">文章排版预览</h2><p>安全正文预览，不是微信草稿。粘贴后请核对样式；图片需手动上传。</p></header><iframe title="公众号文章安全预览" sandbox="" referrerpolicy="no-referrer"></iframe><footer><button type="button" class="button secondary" data-preview-close>返回文章</button></footer></section>';
      backdrop.querySelector('iframe').srcdoc = previewDocument(result.html);
      main.closest('#app').append(backdrop);
      const close = modal(backdrop, preview); backdrop.querySelector('[data-preview-close]').onclick = close;
    };
    return Object.freeze({result});
  }
  window.WorkbenchWechatArticle = Object.freeze({mountManual, buildManualExport, copyManualExport, downloadManualExport, hasArticleHtml, sanitizeArticle, previewDocument});
})();
