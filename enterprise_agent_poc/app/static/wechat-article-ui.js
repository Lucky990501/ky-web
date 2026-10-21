/* Frontend-only manual delivery, ported from a4a15b5 onto Production base.
 * No WeChat endpoint, draft adapter, Agent qualification or model invocation.
 */
(() => {
  'use strict';
  const SLUG = 'wechat-official-account-writing';
  const tags = new Set('article section div p span h1 h2 h3 h4 h5 h6 strong b em i u s blockquote ul ol li br hr table thead tbody tr th td pre code figure figcaption img'.split(' '));
  const discard = new Set('script style iframe object embed link base meta form input button textarea select svg math template noscript'.split(' '));
  const properties = new Set('color background-color font-family font-size font-weight font-style text-align text-decoration line-height letter-spacing margin margin-top margin-bottom margin-left margin-right padding padding-top padding-bottom padding-left padding-right border border-top border-bottom border-left border-right border-radius border-color border-width border-style width max-width height overflow-wrap'.split(' '));
  for (const edge of ['top', 'bottom', 'left', 'right']) for (const detail of ['color', 'width', 'style']) properties.add(`border-${edge}-${detail}`);
  function safeArticleImage(value) {
    try {
      const url = new URL(value, location.origin);
      if (url.origin !== location.origin || url.search || url.hash || url.username || url.password) return '';
      if (window.WORKBENCH_PREVIEW_BASE) url.pathname = url.pathname.replace(/^\/preview(?=\/)/, '');
      const illustration = window.WORKBENCH_PREVIEW_BASE && /^\/api\/v1\/preview\/illustrations\/assets\/[0-9a-f]{32}$/.test(url.pathname);
      if ((!illustration && !/^\/api\/v1\/(?:storage\/(?:generated|assets)\/[A-Za-z0-9_./-]+|chat-images\/[A-Za-z0-9_-]+)$/.test(url.pathname)) || value.includes('..') || value.includes('%') || value.includes('\\')) return '';
      return window.workbenchPreviewUrl ? window.workbenchPreviewUrl(url.href) : url.href;
    } catch { return ''; }
  }
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
        const imageSource = tag === 'img' ? safeArticleImage(node.getAttribute('src') || '') : '';
        const imageAlt = tag === 'img' ? redactPaths(node.getAttribute('alt') || '文章图片').slice(0, 200) : '';
        if (tag === 'img' && !imageSource) { node.replaceWith(document.createTextNode('[图片来源未验证，请在公众号后台手动上传]')); continue; }
        clean(node, depth + 1);
        if (!tags.has(tag)) { node.replaceWith(...node.childNodes); continue; }
        const style = node.style;
        const safe = [];
        for (const property of [...style]) {
          const value = style.getPropertyValue(property).trim();
          // No URLs, CSS functions/escapes, custom props, positioning or overlays.
          const plain = value.replace(/\b(?:rgb|rgba|hsl|hsla)\([\d.,%\s+-]+\)/g, 'color');
          const font = property === 'font-family' && value.split(',').every(item => /^(?:Optima-Regular|PingFangTC-light|PingFang SC|Microsoft YaHei|sans-serif|system-ui)$/i.test(item.trim().replace(/^['"]|['"]$/g, '')));
          const bounded = ![...value.matchAll(/(-?\d+(?:\.\d+)?)px/g)].some(match => Number(match[1]) < 0 || Number(match[1]) > 64);
          if (property === 'font-size' && (!/^\d+(?:\.\d+)?px$/.test(value) || parseFloat(value) < 12 || parseFloat(value) > 32)) continue;
          if (property === 'line-height' && (!/^\d+(?:\.\d+)?$/.test(value) || Number(value) < 1.4 || Number(value) > 2.4)) continue;
          if (property === 'font-weight' && !/^(?:normal|bold|[1-9]00)$/.test(value)) continue;
          if (property === 'letter-spacing' && (!/^\d+(?:\.\d+)?px$/.test(value) || parseFloat(value) > 3)) continue;
          if (/^(?:margin|padding|border)/.test(property) && [...value.matchAll(/(-?\d+(?:\.\d+)?)(?:px|em|rem|%)/g)].some(match => Number(match[1]) < 0 || Number(match[1]) > 64)) continue;
          if (properties.has(property) && (font || property !== 'font-family' && /^[#a-zA-Z0-9.,%\s+-]+$/.test(plain)) && value.length < 180 && bounded) safe.push(`${property}:${value}`);
        }
        for (const attribute of [...node.attributes]) node.removeAttribute(attribute.name);
        if (safe.length) node.setAttribute('style', safe.join(';'));
        if (imageSource) { node.setAttribute('src', imageSource); node.setAttribute('alt', imageAlt); node.setAttribute('referrerpolicy', 'no-referrer'); }
      }
    };
    clean(template.content);
    return template.innerHTML;
  }
  function previewDocument(html, previewImages, loading=false, deferred=false) {
    const safe = sanitizeArticle(html);
    const parsed = document.createElement('template'); parsed.innerHTML = safe;
    if(deferred)for(const [index,node] of [...parsed.content.querySelectorAll('img')].entries()){
      node.dataset.previewSource=node.getAttribute('src');node.removeAttribute('src');node.loading=index<2?'eager':'lazy';node.decoding='async';node.style.aspectRatio='4/3';
    }
    if (previewImages&&!deferred) for (const node of parsed.content.querySelectorAll('img')) {
      const raster = previewImages.get(node.getAttribute('src'));
      if (typeof raster === 'string' && raster.length <= 14 * 1024 * 1024 && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(raster)) node.setAttribute('src', raster);
      else node.replaceWith(document.createTextNode(loading?'[图片正在安全加载…]':'[图片暂不可用，请在公众号后台手动上传]'));
    }
    const images = [...new Set([...parsed.content.querySelectorAll('img')].map(node => node.getAttribute('src')))];
    const imagePolicy = deferred?'data:':images.length ? previewImages ? 'data:' : images.join(' ') : "'none'";
    const documentHtml=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src ${imagePolicy}; base-uri 'none'; form-action 'none'"><style>html{overflow-wrap:anywhere}body{box-sizing:border-box;max-width:680px;margin:0 auto;padding:24px 20px;color:#3E3E3E;font:16px/1.6 system-ui,sans-serif}*{box-sizing:border-box;max-width:100%}img{height:auto}table{display:block;overflow:auto;border-collapse:collapse}pre{white-space:pre-wrap}a{color:inherit}</style></head><body>${parsed.innerHTML}</body></html>`;
    return window.WORKBENCH_PREVIEW_BASE?documentHtml.replace('</style>','body{font:17px/1.95 system-ui,sans-serif;color:#222;background:#fff}@media(max-width:480px){body{padding:20px 16px}h1,h2{font-size:28px!important}p{font-size:16px!important}}</style>'):documentHtml;
  }
  async function mountCachedPreview(frame,result,isCurrent){
    frame.setAttribute('sandbox','allow-same-origin');
    const html=sanitizeArticle(result.html),current=()=>frame.isConnected&&isCurrent();
    const revision=(frame._displayRevision||0)+1;frame._displayRevision=revision;
    if(!frame._displayReady){
      frame._displayReady=new Promise(resolve=>{frame.addEventListener('load',resolve,{once:true});});
      frame.srcdoc=previewDocument(html,null,false,true);frame._displayHtml=html;
    }
    await frame._displayReady;if(!current()||revision!==frame._displayRevision)return;
    const doc=frame.contentDocument;
    if(frame._displayHtml!==html){
      const next=document.createElement('template');next.innerHTML=html;
      for(const node of next.content.querySelectorAll('img')){node.dataset.previewSource=node.getAttribute('src');node.removeAttribute('src');node.style.aspectRatio='4/3';node.loading='lazy';node.decoding='async';}
      const oldImages=new Map([...doc.images].map(n=>[n.dataset.previewSource,n]));
      const patch=(parent,desired)=>{
        for(let index=0;index<desired.length;index++){
          const want=desired[index];let old=parent.childNodes[index];
          if(want.nodeType===Node.ELEMENT_NODE&&want.localName==='img'){
            const reused=oldImages.get(want.dataset.previewSource);
            if(reused){if(old!==reused)parent.insertBefore(reused,old||null);continue;}
          }
          if(!old){parent.append(want.cloneNode(true));continue;}
          if(old.nodeType!==want.nodeType||old.nodeName!==want.nodeName){old.replaceWith(want.cloneNode(true));continue;}
          if(want.nodeType===Node.TEXT_NODE){if(old.textContent!==want.textContent)old.textContent=want.textContent;continue;}
          for(const attr of [...old.attributes])if(!want.hasAttribute(attr.name))old.removeAttribute(attr.name);
          for(const attr of [...want.attributes])if(old.getAttribute(attr.name)!==attr.value)old.setAttribute(attr.name,attr.value);
          patch(old,[...want.childNodes]);
        }
        while(parent.childNodes.length>desired.length)parent.lastChild.remove();
      };
      patch(doc.body,[...next.content.childNodes]);frame._displayHtml=html;
    }
    frame.dataset.articleReady='false';
    await window.WorkbenchPreviewImages.verifyIdentity();if(!current()||revision!==frame._displayRevision)return;
    if(!frame._displayCleanup){
      const unsubscribe=window.WorkbenchPreviewImages.onClear(()=>{if(frame.isConnected){doc.body.replaceChildren();frame.dataset.articleReady='false';}});
      const observer=new MutationObserver(()=>{if(!frame.isConnected){observer.disconnect();unsubscribe();}});observer.observe(document.documentElement,{childList:true,subtree:true});frame._displayCleanup=true;
    }
    const sources=[...new Set([...doc.images].map(n=>n.dataset.previewSource))].filter(Boolean);
    // Full-resolution rasters remain in the rendered DOM for BOTH native
    // selection-copy and the copy button. Decode/encode once per private LRU
    // entry, not once per frame/open/poll; never copy low-resolution thumbnails.
    await Promise.all(sources.slice(0,12).map(async source=>{
      try{
        const image=await window.WorkbenchPreviewImages.raster(source);
        if(!current()||revision!==frame._displayRevision)return;
        for(const node of doc.images)if(node.dataset.previewSource===source){
          if(node.getAttribute('src')!==image.data)node.src=image.data;
          node.style.aspectRatio=image.width+'/'+image.height;node.width=image.width;node.height=image.height;
        }
      }catch{/* Keep the truthful unavailable placeholder; no automatic paid retry. */}
    }));
    if(current()&&revision===frame._displayRevision){
      await Promise.all([...doc.images].filter(n=>n.getAttribute('src')).map(n=>n.decode().catch(()=>{})));
      if(current()&&revision===frame._displayRevision)frame.dataset.articleReady='true';
    }
  }
  async function mountSafePreview(frame, result, isCurrent) {
    if(window.WORKBENCH_PREVIEW_BASE&&window.WorkbenchPreviewImages)return mountCachedPreview(frame,result,isCurrent);
    // Preview-only DOM access for native selection copying. This grants NO
    // scripts/forms/navigation: model HTML is still sanitized, CSP default
    // deny is unchanged, and allow-scripts is deliberately never granted.
    if(window.WORKBENCH_PREVIEW_BASE)frame.setAttribute('sandbox','allow-same-origin');
    frame.dataset.articleReady='false';
    const show=(html,ready)=>{
      frame.onload=()=>{if(frame.isConnected&&isCurrent())frame.dataset.articleReady=ready?'true':'false';};
      frame.srcdoc=html;
    };
    const parsed = document.createElement('template'); parsed.innerHTML = sanitizeArticle(result.html);
    const sources = [...new Set([...parsed.content.querySelectorAll('img')].map(node => node.getAttribute('src')))];
    if (!sources.length) { show(previewDocument(result.html),true); return; }
    // Opaque sandbox cannot use SameSite=Lax cookies. Read authorized bytes in
    // the host and decode/re-encode raster pixels for opaque preview only.
    // Incoming HTML still cannot contain data/blob/file or arbitrary URLs;
    // exported/clipboard HTML retains the authorized server address, not bytes.
    const images = new Map(), controller = new AbortController();
    show(previewDocument(result.html, images, Boolean(window.WORKBENCH_PREVIEW_BASE)),false);
    const current = () => frame.isConnected && isCurrent();
    const cleanup = () => { controller.abort(); clearTimeout(timer); observer.disconnect(); images.clear(); };
    const observer = new MutationObserver(() => { if (!current()) cleanup(); });
    observer.observe(document.documentElement, {childList: true, subtree: true});
    const timer = setTimeout(() => controller.abort(), window.WORKBENCH_PREVIEW_BASE?30000:10000);
    const loadImage=async url => {
      if (!current()) { cleanup(); return; }
      try {
        if (safeArticleImage(url) !== url) return;
        const response = await fetch(url, {method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal});
        const type = (response.headers.get('content-type') || '').split(';')[0].trim();
        if (!response.ok || !['image/png', 'image/jpeg', 'image/webp'].includes(type) || Number(response.headers.get('content-length')) > 10 * 1024 * 1024) { await response.body?.cancel(); return; }
        const reader = response.body.getReader(), parts = []; let size = 0;
        while (true) { const {done, value} = await reader.read(); if (done) break; size += value.byteLength; if (size > 10 * 1024 * 1024) { await reader.cancel(); throw Error('IMAGE_TOO_LARGE'); } parts.push(value); }
        if (!current()) { cleanup(); return; }
        if (size) {
          const bitmap = await createImageBitmap(new Blob(parts, {type}));
          try {
            if (bitmap.width > 4096 || bitmap.height > 4096 || bitmap.width * bitmap.height > 16777216) throw Error('IMAGE_TOO_LARGE');
            if (!current()) { cleanup(); return; }
            const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height;
            canvas.getContext('2d').drawImage(bitmap, 0, 0);
            const raster = canvas.toDataURL('image/png'); canvas.width = canvas.height = 0;
            if (raster.length <= 14 * 1024 * 1024) images.set(url, raster);
          } finally { bitmap.close(); }
        }
      } catch { /* Explicit unavailable image placeholder, never fake success. */ }
    };
    if(window.WORKBENCH_PREVIEW_BASE)await Promise.all(sources.slice(0,12).map(loadImage));
    else for(const url of sources.slice(0,12))await loadImage(url);
    clearTimeout(timer);
    if (current()) show(previewDocument(result.html, images),true); else cleanup();
  }
  async function copyRenderedArticle(frame) {
    if(!window.WORKBENCH_PREVIEW_BASE||frame?.dataset.articleReady!=='true')throw Error('ARTICLE_STILL_LOADING');
    const doc=frame.contentDocument,win=frame.contentWindow;
    if(!doc?.body||!doc.body.textContent.trim())throw Error('ARTICLE_STILL_LOADING');
    if([...doc.images].some(i=>!i.complete||!i.naturalWidth))throw Error('ARTICLE_IMAGE_UNAVAILABLE');
    // Exactly the same browser selection-copy path as selecting the rendered
    // article by hand, including the already decoded authorized raster images.
    const focus=document.activeElement,selection=win.getSelection(),range=doc.createRange();
    const prior=[...Array(selection.rangeCount)].map((_,i)=>selection.getRangeAt(i).cloneRange());
    try {
      win.focus();range.selectNodeContents(doc.body);selection.removeAllRanges();selection.addRange(range);
      if(doc.execCommand('copy'))return;
      // Native selection copy unavailable: serialize THIS rendered DOM with
      // its computed styles, not a separately simplified article template.
      if(!navigator.clipboard?.write||typeof ClipboardItem!=='function')throw Error('RICH_CLIPBOARD_UNAVAILABLE');
      const clone=doc.body.cloneNode(true),original=[doc.body,...doc.body.querySelectorAll('*')],nodes=[clone,...clone.querySelectorAll('*')];
      for(let i=0;i<nodes.length;i++){
        const style=win.getComputedStyle(original[i]);
        for(const name of properties)nodes[i].style.setProperty(name,style.getPropertyValue(name));
      }
      const section=document.createElement('section');section.setAttribute('style',clone.getAttribute('style')||'');section.append(...clone.childNodes);
      await navigator.clipboard.write([new ClipboardItem({'text/html':new Blob([section.outerHTML],{type:'text/html'}),'text/plain':new Blob([doc.body.innerText],{type:'text/plain'})})]);
    } finally {selection.removeAllRanges();for(const old of prior)selection.addRange(old);focus?.focus();}
  }
  function articleHtml(content, markdown) {
    if(window.previewArticleContent)content=window.previewArticleContent(content);
    const fenced = /^\s*```html\s*\n([\s\S]*?)\n```\s*$/i.exec(content);
    return fenced ? fenced[1] : /^\s*</.test(content) ? content : markdown(content);
  }
  const inlineDefaults = Object.freeze({
    section: 'margin:0;padding:0;max-width:100%;overflow-wrap:anywhere;color:#3E3E3E;font-family:Optima-Regular,PingFangTC-light,PingFang SC,Microsoft YaHei,sans-serif;font-size:16px;line-height:1.6;letter-spacing:0.544px;text-align:left',
    article: 'margin:0;padding:0', div: 'margin:0;padding:0',
    h1: 'font-size:24px;font-weight:700;line-height:1.5;margin:24px 0 16px',
    h2: 'font-size:20px;font-weight:700;line-height:1.5;margin:24px 0 12px',
    h3: 'font-size:18px;font-weight:700;line-height:1.5;margin:20px 0 12px',
    h4: 'font-size:16px;font-weight:700;line-height:1.6;margin:16px 0 8px',
    h5: 'font-size:16px;font-weight:700;line-height:1.6;margin:16px 0 8px',
    h6: 'font-size:16px;font-weight:700;line-height:1.6;margin:16px 0 8px',
    p: 'font-size:16px;line-height:1.6;margin:0 0 18px',
    strong: 'font-weight:700', b: 'font-weight:700', em: 'font-style:italic', i: 'font-style:italic',
    u: 'text-decoration:underline', s: 'text-decoration:line-through',
    blockquote: 'margin:28px 0;padding:18px 20px;border-left:4px solid #FFCA00;background-color:#fffdf4;line-height:1.6',
    figure: 'margin:28px 0', figcaption: 'font-size:12px;color:#A0A0A0;line-height:1.6;text-align:center', img: 'width:100%;height:auto',
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
    if(window.WORKBENCH_PREVIEW_BASE&&window.previewWechatStyle)window.previewWechatStyle(safe.content);
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
      const previous = node.style, kept = [...previous].map(property => `${property}:${previous.getPropertyValue(property)}`);
      node.removeAttribute('style');
      const style = [inlineDefaults[node.localName], ...kept].filter(Boolean).join(';');
      if (style) node.setAttribute('style', style);
    }
    // Keep export assembly in the template's INERT owner document. Adopting
    // its images into even a detached live-document element starts hidden
    // original-image GETs, bypassing the display cache on every open/export.
    const wrapper = (window.WORKBENCH_PREVIEW_BASE?safe.content.ownerDocument:document).createElement('section'); wrapper.setAttribute('style', window.WORKBENCH_PREVIEW_BASE&&window.previewWechatSectionStyle||inlineDefaults.section); wrapper.append(safe.content);
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
    if(window.WORKBENCH_PREVIEW_BASE){
      const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='发布建议';details.open=true;
      details.append(summary,slot.querySelector('.wechat-manual-metadata'));slot.querySelector('.wechat-manual-notice').remove();slot.append(details);
    }
    const feedback = slot.querySelector('[role="status"]'), buttons = [...slot.querySelectorAll('button')], preview = slot.querySelector('[data-article-preview]');
    let result, busy = false;
    try { result = buildManualExport(content, markdown, metadata); }
    catch(error) { buttons.forEach(button => button.disabled = true); feedback.textContent = error.message==='ARTICLE_BODY_UNCONFIRMED'?'该历史回复包含策划内容，正文边界不明确，未将其当作正文导出。原回复已保留；请明确要求仅返回完整文章正文。':'文章内容过大或无效，暂无法导出。'; return null; }
    if (inlineBody && (hasArticleHtml(content)||window.WORKBENCH_PREVIEW_BASE)) {
      const body = article.querySelector('.chat-message-content');
      if (body) {
        // Reuse the exact existing sandbox/CSP preview, now inside the reply.
        // Never insert raw model HTML into the host permission context.
        const frame = window.WORKBENCH_PREVIEW_BASE&&body.querySelector('.wechat-inline-article')||document.createElement('iframe'); frame.className = 'wechat-inline-article';
        frame.title = '公众号文章正文'; frame.setAttribute('sandbox', ''); frame.setAttribute('referrerpolicy', 'no-referrer');
        frame.style.height = `${Math.min(640, Math.max(160, 48 + result.text.split('\n').length * 28 + Math.ceil(result.text.length / 32) * 12))}px`;
        if(window.WORKBENCH_PREVIEW_BASE){
          const parsed=document.createElement('template');parsed.innerHTML=result.html;
          const columns=Math.max(12,Math.floor(((body.clientWidth||375)-48)/17));
          frame.style.height=Math.min(24000,Math.max(320,100+Math.ceil(result.text.length/columns)*34+parsed.content.querySelectorAll('p').length*24+parsed.content.querySelectorAll('h2').length*120+parsed.content.querySelectorAll('img').length*480))+'px';
        }
        if(frame.parentNode!==body)body.replaceChildren(frame); body.classList.remove('chat-message-content-markdown'); body.classList.add('wechat-inline-message-content');
        mountSafePreview(frame, result, current).catch(() => { if (current()) frame.srcdoc = previewDocument('<p>文章预览暂不可用，请重试。</p>'); });
        // The manual controls already provide safe rich/source/plain copying.
        // Keep regeneration, but remove the old raw model-source copy action.
        article.querySelector('[data-copy-response]')?.remove();
        const actions = article.querySelector('.message-actions'); if (actions && !actions.children.length) actions.hidden = true;
      }
    }
    slot.querySelector('[data-manual-title]').textContent = result.title || '请在公众号后台手动填写';
    const excerpt=document.createElement('template');excerpt.innerHTML=result.html;
    slot.querySelector('[data-manual-digest]').textContent = result.digest || (window.WORKBENCH_PREVIEW_BASE?excerpt.content.querySelector('p')?.textContent?.trim().slice(0,120):'') || '未提供，请在公众号后台手动填写';
    slot.querySelector('[data-manual-cover]').textContent = result.cover || '请在公众号后台手动上传';
    if(window.WORKBENCH_PREVIEW_BASE){
      const cover=slot.querySelector('[data-manual-cover]'),src=safeArticleImage(metadata.coverAsset?.url||'');
      if(src){
        const image=document.createElement('img');image.alt='文章发布封面';image.loading='lazy';
        if(window.WorkbenchPreviewImages&&/^[a-f0-9]{32}$/.test(metadata.coverAsset.id||''))window.WorkbenchPreviewImages.bind(image,window.workbenchPreviewUrl('/api/v1/preview/image-display/illustrations/'+metadata.coverAsset.id));else image.src=src;
        const link=document.createElement('a');link.href=src;link.download='公众号封面'+(metadata.coverAsset.extension||'.png');link.textContent='下载封面';link.className='button secondary';
        cover.replaceChildren(image,link);
      }else cover.textContent='独立封面准备中；正文阅读和导出不受影响。';
    }
    feedback.textContent = result.redacted ? '安全排版已准备；内部文件路径已移除，请核对正文。' : '安全排版已准备。粘贴到公众号后请核对内容、样式和图片，再手动保存草稿。';
    for (const [selector, mode] of [['[data-copy-rich]', 'rich'], ['[data-copy-source]', 'source'], ['[data-copy-plain]', 'plain']]) {
      slot.querySelector(selector).onclick = async () => {
        if (!current() || busy) return;
        busy = true; buttons.forEach(button => button.disabled = true); slot.setAttribute('aria-busy', 'true');
        feedback.textContent = '正在复制…'; slot.querySelector('[data-copy-recovery]').hidden = true;
        try {
          if(mode==='rich'&&window.WORKBENCH_PREVIEW_BASE)await copyRenderedArticle(article.querySelector('.wechat-inline-article'));
          else await copyManualExport(result, mode);
          if (current()) feedback.textContent = mode === 'rich' ? '已复制排版内容，请粘贴到公众号编辑器' : mode === 'source' ? '已复制HTML源码；这是源码文本，不是富文本粘贴。' : '已复制纯文本；不包含排版样式。';
        } catch (error) {
          if (!current()) return;
          feedback.textContent = error.message==='ARTICLE_STILL_LOADING'?'正文与图片仍在加载，请稍后再复制。':mode === 'rich' ? '富文本复制失败，未复制排版内容。可在正文中手动框选复制，或使用源码／纯文本。' : '复制失败；请手动选中下方内容并复制。';
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
      main.closest('#app').append(backdrop);
      mountSafePreview(backdrop.querySelector('iframe'), result, () => current() && backdrop.isConnected).catch(() => { if (current() && backdrop.isConnected) backdrop.querySelector('iframe').srcdoc = previewDocument('<p>文章预览暂不可用，请重试。</p>'); });
      const close = modal(backdrop, preview); backdrop.querySelector('[data-preview-close]').onclick = close;
    };
    return Object.freeze({result});
  }
  window.WorkbenchWechatArticle = Object.freeze({mountManual, buildManualExport, copyManualExport, copyRenderedArticle, downloadManualExport, hasArticleHtml, sanitizeArticle, previewDocument});
})();
