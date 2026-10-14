const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const crypto=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'../app/static/wechat-article-ui.js'),'utf8');
function ui(extra={}){const context={window:{},Blob,...extra};vm.createContext(context);vm.runInContext(source,context);return context.window.WorkbenchWechatArticle;}
test('manual export writes both HTML and corresponding plain text in one ClipboardItem',async()=>{
  let items;
  const component=ui({navigator:{clipboard:{write:async value=>items=value}},ClipboardItem:class{constructor(data){this.data=data;}}});
  await component.copyManualExport({html:'<p>正文</p>',text:'正文'},'rich');
  assert.equal(items.length,1);assert.deepEqual(Object.keys(items[0].data),['text/html','text/plain']);
  assert.equal(await items[0].data['text/html'].text(),'<p>正文</p>');assert.equal(await items[0].data['text/plain'].text(),'正文');
});
test('rich-copy denial never silently falls back to plain-text success',async()=>{
  let textWrites=0;
  const component=ui({navigator:{clipboard:{write:async()=>{throw Error('denied');},writeText:async()=>textWrites++}},ClipboardItem:class{}});
  await assert.rejects(component.copyManualExport({html:'<p>正文</p>',text:'正文'},'rich'));
  assert.equal(textWrites,0);
});
test('missing ClipboardItem or clipboard API is an explicit rich failure',async()=>{
  await assert.rejects(ui({navigator:{}}).copyManualExport({html:'x',text:'x'},'rich'),/RICH_CLIPBOARD_UNAVAILABLE/);
  await assert.rejects(ui({navigator:{clipboard:{write:async()=>{}}}}).copyManualExport({html:'x',text:'x'},'rich'),/RICH_CLIPBOARD_UNAVAILABLE/);
});
test('source and plain-text actions are separate writes with exact payloads',async()=>{
  const writes=[],component=ui({navigator:{clipboard:{writeText:async value=>writes.push(value)}}});
  await component.copyManualExport({html:'<p>正文</p>',text:'正文'},'source');await component.copyManualExport({html:'<p>正文</p>',text:'正文'},'plain');
  assert.deepEqual(writes,['<p>正文</p>','正文']);
});
test('clipboard success is not returned until the browser write resolves',async()=>{
  let resolve,done=false;
  const component=ui({navigator:{clipboard:{write:()=>new Promise(value=>resolve=value)}},ClipboardItem:class{}});
  const pending=component.copyManualExport({html:'x',text:'x'},'rich').then(()=>done=true);
  await Promise.resolve();assert.equal(done,false);resolve();await pending;assert.equal(done,true);
});
test('active workbench path uses manual exporter and enforces current user/tenant/message context',()=>{
  const workbench=fs.readFileSync(path.join(__dirname,'../app/static/workbench.js'),'utf8');
  const mount=workbench.slice(workbench.indexOf('function mountWechatArticle'),workbench.indexOf('function openPasswordEditor'));
  assert.ok(mount.includes('.mountManual('));assert.ok(mount.includes('me?.user_id===context.userId'));assert.ok(mount.includes('me?.tenant_id===context.tenantId'));
  assert.ok(source.includes('article.dataset.assistantMessageId === messageId'));assert.ok(!mount.includes('request:'));assert.ok(!mount.includes('readAccount'));
});
test('manual exporter reuses sanitizer and cannot use a draft adapter or endpoint',()=>{
  const manual=source.slice(source.indexOf('const inlineDefaults'),source.indexOf('window.WorkbenchWechatArticle ='));
  assert.ok(manual.includes('sanitizeArticle(raw)'));assert.ok(manual.includes('mountSafePreview(frame, result, current)'));
  assert.ok(!manual.includes('fetch('));assert.ok(!manual.includes('/api/'));assert.ok(!manual.includes('createDraft'));
  assert.ok(!manual.includes('localStorage'));assert.ok(!manual.includes('sessionStorage'));
});
test('direct chat display is opt-in and reuses the existing sandboxed preview',()=>{
  const workbench=fs.readFileSync(path.join(__dirname,'../app/static/workbench.js'),'utf8');
  assert.ok(workbench.includes('modal:bindModalDialog,inlineBody:true'));
  assert.ok(source.includes('inlineBody = false'));assert.ok(source.includes("frame.setAttribute('sandbox', '')"));
  assert.ok(source.includes('frame.srcdoc = previewDocument(result.html)'));assert.ok(source.includes('body.replaceChildren(frame)'));
  assert.ok(source.includes("article.querySelector('[data-copy-response]')?.remove()"));
  assert.ok(!source.includes('allow-scripts'));assert.ok(!source.includes('allow-same-origin'));
});
test('reviewed formatter remains shared and downloads re-sanitize before making a full document',()=>{
  assert.ok(source.includes('sanitizeArticle(raw)'));assert.ok(source.includes('const safe = buildManualExport(result.html'));
  assert.ok(source.includes("previewDocument(safe.html)"));assert.ok(source.includes("new Blob([html], {type: 'text/html;charset=utf-8'})"));
  assert.ok(source.includes('URL.revokeObjectURL(url)'));
});
test('ported component exports manual functions only, with no dormant draft API or fake success',()=>{
  assert.deepEqual(Object.keys(ui()),['mountManual','buildManualExport','copyManualExport','downloadManualExport','hasArticleHtml','sanitizeArticle','previewDocument']);
  assert.ok(!source.includes('pendingAdapter'));assert.ok(!source.includes('createDraft'));assert.ok(!source.includes('草稿已创建'));
  assert.ok(!source.includes('/api/'));assert.ok(source.includes("fetch(url, {method: 'GET', credentials: 'same-origin'"));
  assert.ok(source.includes('safeArticleImage(url) !== url'));assert.ok(source.includes('bitmap.close()'));assert.ok(source.includes("canvas.toDataURL('image/png')"));
});
test('index keeps release JS/CSS placeholders and loads exact content-versioned component before workbench',()=>{
  const index=fs.readFileSync(path.join(__dirname,'../app/static/index.html'),'utf8');
  const digest=crypto.createHash('sha256').update(source).digest('hex').slice(0,16);
  const reference=`/static/wechat-article-ui.js?v=${digest}`;
  assert.equal(index.split(reference).length-1,1);assert.ok(index.indexOf(reference)<index.indexOf('/static/workbench.js'));
  assert.match(index,new RegExp(`<script src="${reference.replace('?','\\?')}" defer>`));
  for(const marker of ['__JS_SHA256_V1__','__CSS_SHA256_V1_'])assert.equal(index.split(marker).length-1,1);
});
test('frontend integration never invents Agent permission, metadata or a model invocation',()=>{
  const workbench=fs.readFileSync(path.join(__dirname,'../app/static/workbench.js'),'utf8');
  const mount=workbench.slice(workbench.indexOf('function mountWechatArticle'),workbench.indexOf('function openPasswordEditor'));
  assert.ok(workbench.includes('const canRun=agent?.enabled===true'));assert.ok(workbench.includes('智能体已停用，历史项目只读'));
  assert.ok(!mount.includes('enabled:true'));assert.ok(!mount.includes('/runs'));assert.ok(!mount.includes('api('));
});


test('HTML-only inline rendering does not replace normal Markdown or plain replies',()=>{
  const component=ui();
  for(const value of ['<h1>文章</h1><p>正文</p>','<!doctype html><html><body>正文</body></html>','```html\n<p>正文</p>\n```'])assert.equal(component.hasArticleHtml(value),true);
  for(const value of ['# 正常 Markdown\n\n正文','普通文本','介绍 https://example.com/business','<https://example.com>'])assert.equal(component.hasArticleHtml(value),false);
  assert.ok(source.includes('inlineBody && hasArticleHtml(content)'));
});
test('H08 exact generated paths removed while normal business links survive',()=>{
  const pattern=vm.runInNewContext(source.match(/const internalPathPattern = ([^\n]+);/)[1]);
  assert.equal('正面 /api/v1/storage/generated/fixture/a.png； https://example.com/business'.replace(pattern,'[内部路径已移除]'),'正面 [内部路径已移除]； https://example.com/business');
});
test('short Word followups use only current DOM trusted envelopes and do not ask a model for a path',()=>{
  const workbench=fs.readFileSync(path.join(__dirname,'../app/static/workbench.js'),'utf8');
  const value=workbench.match(/const isActivityWordExportRequest=([^\n]+);/)[1];
  const accepts=vm.runInNewContext(value);
  for(const text of ['我要一个word格式的','导出 Word','请给我一份docx文档','word'])assert.equal(accepts(text),true,text);
  for(const text of ['重新调整预算','Word文档里的知识请分析','我要一个Word格式的全新招生方案，地点在校区'])assert.equal(accepts(text),false,text);
  assert.ok(workbench.includes('activityPlanResultIsValid(activityPlanDocuments.get(id)?.structuredResult)'));
  assert.ok(workbench.includes('服务器文件路径不能作为下载附件'));
});
