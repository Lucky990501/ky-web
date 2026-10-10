const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/wechat-article-ui.js'),'utf8');
function ui(){const context={window:{}};vm.createContext(context);vm.runInContext(source,context);return context.window.WorkbenchWechatArticle;}
const identity={agentId:'agent-1',messageId:'message-1'};
const receipt={status:'created',confirmed:true,draft_media_id:'confirmed_fixture_media',receipt_id:'fixture_receipt',agent_id:'agent-1',message_id:'message-1'};
test('default adapter is BACKEND_PENDING and never executes a guessed endpoint',async()=>{
  const component=ui();assert.equal((await component.pendingAdapter.availability()).state,'BACKEND_PENDING');
  await assert.rejects(component.pendingAdapter.createDraft(),error=>error.code==='BACKEND_PENDING');
  assert.ok(!source.includes('/api/v1/wechat/drafts'));assert.ok(!source.includes('/runs'));
});
test('only confirmed, exact-message server receipt can be rendered as created',()=>{
  assert.equal(ui().confirmedDraft(receipt,identity),true);
  for(const patch of [{confirmed:false},{status:'completed'},{status:'PERMISSION_RESOLVED_NOT_EXECUTED'},{draft_media_id:null},{receipt_id:null},{agent_id:'other'},{message_id:'other'}])assert.equal(ui().confirmedDraft({...receipt,...patch},identity),false);
});
test('unconfirmed and HTML-shaped draft IDs fail closed',()=>{
  for(const id of ['', '<script>alert(1)</script>', 'javascript:alert(1)', 'x'.repeat(257)])assert.equal(ui().confirmedDraft({...receipt,draft_media_id:id},identity),false);
});
test('preview has no script/origin/form allowance and blocks network with CSP',()=>{
  assert.ok(source.includes('sandbox=""'));assert.ok(source.includes('referrerpolicy="no-referrer"'));
  assert.ok(source.includes("default-src 'none'"));assert.ok(source.includes("form-action 'none'"));
  assert.ok(!source.includes('allow-scripts'));assert.ok(!source.includes('allow-same-origin'));
});
test('client draft adapter submits identity and explicit intent, not credentials or article data',()=>{
  assert.ok(source.includes('adapter.createDraft({...context, userConfirmed: true})'));
  assert.ok(source.includes('message_id !== messageId'));assert.ok(source.includes("permission.can_create_draft !== true"));
  assert.ok(source.includes('if (!await check(true) || !current()) return;'));
});
test('profile uses common modal lifecycle with inert background and scoped focus',()=>{
  const workbench=fs.readFileSync(path.join(__dirname,'../app/static/workbench.js'),'utf8');
  const profile=workbench.slice(workbench.indexOf('function openProfileEditor'),workbench.indexOf('function sideCredit'));
  assert.ok(profile.includes('close=bindModalDialog(dialog)'));assert.ok(profile.includes('aria-modal="true"'));
  assert.ok(workbench.includes('sibling.inert=true'));assert.ok(workbench.includes('node.inert=wasInert'));
});
test('entry points cover completed SSE/poll rendering and persisted history without name matching',()=>{
  const workbench=fs.readFileSync(path.join(__dirname,'../app/static/workbench.js'),'utf8');
  assert.ok(workbench.includes('mountWechatArticle(main,node,finalResponse,state.assistantMessageId)'));
  assert.ok(workbench.includes('mountWechatArticle(main,article,item.content,item.id)'));
  assert.ok(workbench.includes("context.agent.slug!=='wechat-official-account-writing'"));
  assert.ok(!source.includes('localStorage'));assert.ok(!source.includes('sessionStorage'));
});
