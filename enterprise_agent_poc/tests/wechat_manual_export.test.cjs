const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
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
  const manual=source.slice(source.indexOf('const inlineDefaults'),source.indexOf('const messages ='));
  assert.ok(manual.includes('sanitizeArticle(raw)'));assert.ok(manual.includes('previewDocument(result.html)'));
  assert.ok(!manual.includes('fetch('));assert.ok(!manual.includes('/api/'));assert.ok(!manual.includes('createDraft'));
  assert.ok(!manual.includes('localStorage'));assert.ok(!manual.includes('sessionStorage'));
});
