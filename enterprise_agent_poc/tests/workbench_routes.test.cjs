const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

// Execute the actual frontend without auto-boot, using only a small DOM/history
// boundary double. No renderer or routing function is replaced in route tests.
const source = fs.readFileSync(process.env.WORKBENCH_JS_SOURCE || path.join(__dirname, '../app/static/workbench.js'), 'utf8').replace(/boot\(\);\s*$/, '');

test('generic agent pathname wins over stale agent and page history state',()=>{
  const h=harness('/agents/social-content-agent',{page:'campaign',activeAgentId:'campaign-agent'});
  assert.equal(h.run('pageFromNavigation()'),'agent:social-content-agent');
  assert.equal(h.run('activeAgentId'),'social-content-agent');
  assert.equal(h.run("agentPage('social-content-agent')"),'agent:social-content-agent');
  assert.equal(h.run("agentPage('unknown')"),'agent:unknown');
  for(const [id,page] of [['image-agent','image'],['copywriting-agent','copywriting'],['campaign-agent','campaign']])assert.equal(h.run(`agentPage('${id}')`),page);
});

test('generic direct refresh routes request dynamic metadata and do not render Campaign',async()=>{
  const h=harness('/agents/unknown');
  const pending=h.run('render(pageFromNavigation())');
  h.respond('/api/v1/workspace',{agents:[],credit_balance:100});await flush();
  h.respond('/api/v1/agents/unknown',null);await pending;
  assert.equal(h.document.main.dataset.page,'agent:unknown');
  assert.deepEqual(h.navs.filter(x=>x.classList.active).map(x=>x.dataset.page),['image']);
  assert.ok(h.document.main.innerHTML.includes('该智能体暂未启用'));
  assert.ok(!h.document.main.innerHTML.includes('活动策划'));
});
const flush = () => new Promise(resolve => setImmediate(resolve));
test('workspace advertises public Productized slug without changing Legacy references',async()=>{
  const h=harness('/workspace');const pending=h.run("render('workspace')");
  h.respond('/api/v1/workspace',{agents:[
    {id:'image-agent',slug:'image-generation',name:'Legacy',enabled:true,credit_cost:20},
    {id:'internal-uuid',slug:'social-content-agent',conversation_path:'/agents/social-content-agent',definition_source:'productized',name:'Social',enabled:true,credit_cost:3}
  ],credit_balance:100});await pending;
  assert.ok(h.document.main.innerHTML.includes('data-agent="image-agent"'),h.document.main.innerHTML);
  assert.ok(h.document.main.innerHTML.includes('data-agent="social-content-agent"'));
  assert.ok(!h.document.main.innerHTML.includes('data-agent="internal-uuid"'));
});
test('saved Productized project navigation uses public slug while preserving internal identity',()=>{
  const h=harness();h.run("saved={id:'saved',agent_id:'internal-uuid',agent:{name:'Social',slug:'social-content-agent'}}");
  assert.ok(h.run("conversationRowHtml(saved,'data-agent-conversation')").includes('data-agent-id="social-content-agent"'));
  assert.ok(h.run('historyCardHtml(saved)').includes('data-history-agent="social-content-agent"'));
});
test('disabled productized conversation remains readable without run controls',async()=>{
  const h=harness('/agents/fourth',{activeConversationId:'saved'});
  const pending=h.run('render(pageFromNavigation())');
  h.respond('/api/v1/workspace',{agents:[]});await flush();
  h.respond('/api/v1/agents/fourth',null);await flush();
  h.respond('/api/v1/conversations',[]);await flush();
  h.respond('/api/v1/conversations/saved',{agent_id:'fourth',agent:{name:'固定第四智能体',icon:'bot',skill_manifest:'{}'},project:{name:'已保存项目'},messages:[{role:'assistant',content:'已持久化正文'}]});
  await pending;
  assert.ok(h.document.main.innerHTML.includes('已持久化正文'));
  assert.equal(h.document.main.querySelector('#new-chat').disabled,true);
  assert.ok(h.document.main.querySelector('#composer').innerHTML.includes('历史项目只读'));
});
function harness(pathname = '/platform/skills', state = null) {
  const pending = [];
  const navs = ['workspace', 'image', 'profile', 'platform-skills', 'platform-agents'].map(page => ({dataset:{page}, classList:{active:false, toggle(name,value){this.active=value;}}}));
  const document = {
    querySelector(selector){if(selector === '#main')return this.main; if(selector==='#side-credit')return null; return {};},
    querySelectorAll(selector){return selector === '[data-page]' ? navs : [];},
    addEventListener(){},
  };
  class View {
    constructor(){this.dataset={}; this.innerHTML=''; this.nodes=new Map();}
    cloneNode(){return new View();}
    replaceWith(view){assert.equal(document.main,this); document.main=view;}
    querySelector(selector){if(!this.nodes.has(selector))this.nodes.set(selector,{});return this.nodes.get(selector);}
    querySelectorAll(){return [];}
  }
  document.main = new View();
  const location = {pathname};
  const stack = [{path:pathname,state}], listeners = {};
  let index = 0;
  const history = {
    get state(){return stack[index].state;},
    pushState(state, _, path){stack.splice(index+1); stack.push({path,state}); index++; location.pathname=path;},
    replaceState(state, _, path){stack[index]={path,state}; location.pathname=path;},
    move(delta){index+=delta; location.pathname=stack[index].path; listeners.popstate({state:stack[index].state});},
  };
  const window = {history, addEventListener(name,fn){listeners[name]=fn;}};
  const context = vm.createContext({document,window,location,console,Map,Set,Date,setTimeout,
    fetch(url){return new Promise((resolve,reject)=>pending.push({url,resolve(body){resolve({ok:true,json:async()=>body});},reject}));},
  });
  vm.runInContext(source,context);
  vm.runInContext("me={is_platform_admin:true,display_name:'Test',email:'test@example.invalid',tenant_name:'Test',role:'member'}", context);
  const run = code => vm.runInContext(code,context);
  function respond(url, body){const request=pending.find(x=>x.url===url&&!x.done); assert.ok(request,`expected request ${url}`); request.done=true; request.resolve(body);}
  async function settle(){
    for(let i=0;i<5;i++){
      for(const request of pending.filter(x=>!x.done)){
        request.done=true;
        request.resolve(request.url==='/api/v1/workspace'?{brand_name:'Test',credit_balance:100,agents:[],recent_conversations:[]}:request.url==='/api/v1/platform/agents/options'?{model_configs:[],tool_capabilities:[]}:[]);
      }
      await flush();
    }
  }
  function consistent(page,heading){
    assert.equal(location.pathname,page === 'profile'?'/profile':page==='platform-agents'?'/platform/agents':'/platform/skills');
    assert.equal(document.main.dataset.page,page);
    assert.ok(document.main.innerHTML.includes(`<h1>${heading}</h1>`));
    assert.deepEqual(navs.filter(x=>x.classList.active).map(x=>x.dataset.page),[page]);
  }
  return {run,respond,settle,consistent,pending,document,history,location,navs};
}

test('profile <-> skills uses distinct URLs and matching navigation/view',async()=>{
  const h=harness();
  await Promise.all([h.run("navigate('profile')"),h.settle()]); h.consistent('profile','个人中心');
  await Promise.all([h.run("navigate('platform-skills')"),h.settle()]); h.consistent('platform-skills','Skill Registry');
  await Promise.all([h.run("navigate('profile')"),h.settle()]); h.consistent('profile','个人中心');
});
for(const [pathname,wrongPage,page,heading] of [['/platform/skills','profile','platform-skills','Skill Registry'],['/profile','platform-skills','profile','个人中心']]){
  test(`direct/refresh/re-activated tab: ${pathname} wins over stale history ${wrongPage}`,async()=>{
    const h=harness(pathname,{page:wrongPage});
    assert.equal(h.run('pageFromNavigation()'),page);
    await Promise.all([h.run('render(pageFromNavigation())'),h.settle()]); h.consistent(page,heading);
  });
  test(`actual boot repairs stale history on ${pathname} before rendering`,async()=>{
    const h=harness(pathname,{page:wrongPage});
    const started=h.run('boot()');
    h.respond('/api/v1/me',{is_platform_admin:true,display_name:'Test',email:'test@example.invalid',tenant_name:'Test',role:'member'});
    await started; await h.settle(); h.consistent(page,heading);
    assert.equal(h.history.state.page,page);
  });
}
test('popstate/back/forward reparses pathname and keeps active navigation consistent',async()=>{
  const h=harness();
  await Promise.all([h.run("navigate('platform-skills',{replace:true})"),h.settle()]);
  await Promise.all([h.run("navigate('profile')"),h.settle()]);
  h.history.move(-1); await h.settle(); h.consistent('platform-skills','Skill Registry');
  h.history.move(1); await h.settle(); h.consistent('profile','个人中心');
});
test('late profile fetch cannot overwrite the newer skills view',async()=>{
  const h=harness(); const old=h.run("navigate('profile')");
  const current=h.run("navigate('platform-skills')");
  h.respond('/api/v1/platform/skills',[]); h.respond('/api/v1/agents',[]); await current;
  h.consistent('platform-skills','Skill Registry');
  h.respond('/api/v1/workspace',{brand_name:'Test'}); await old;
  h.consistent('platform-skills','Skill Registry');
});
test('late skills fetch cannot overwrite the newer profile view',async()=>{
  const h=harness(); const old=h.run("navigate('platform-skills')");
  const current=h.run("navigate('profile')"); h.respond('/api/v1/workspace',{brand_name:'Test'}); await current;
  h.respond('/api/v1/platform/skills',[]); h.respond('/api/v1/agents',[]); await old;
  h.consistent('profile','个人中心');
});
test('rapid profile/skills/profile switching isolates stale success and stale failure',async()=>{
  const h=harness(); const first=h.run("navigate('profile')"); const second=h.run("navigate('platform-skills')"); const last=h.run("navigate('profile')");
  const profiles=h.pending.filter(x=>x.url==='/api/v1/workspace');
  profiles[1].done=true; profiles[1].resolve({brand_name:'Current'}); await last;
  profiles[0].done=true; profiles[0].reject(new Error('stale request failed')); await first;
  h.respond('/api/v1/platform/skills',[]); h.respond('/api/v1/agents',[]); await second;
  h.consistent('profile','个人中心'); assert.ok(!h.document.main.innerHTML.includes('stale request failed'));
});
test('existing agent conversation restoration and platform authorization remain unchanged',()=>{
  const h=harness('/agents/copywriting',{page:'profile',activeAgentId:'copywriting-agent',activeConversationId:'conversation-test'});
  assert.equal(h.run('pageFromNavigation()'),'copywriting');
  assert.equal(h.run('activeConversationId'),'conversation-test');
  assert.equal(h.run('activeAgentId'),'copywriting-agent');
  h.location.pathname='/platform/skills'; h.run('me.is_platform_admin=false');
  assert.equal(h.run('pageFromNavigation()'),'workspace');
});

test('member, enterprise admin, and platform admin share one filtered navigation source',()=>{
  const h=harness('/workspace');
  const ids=user=>JSON.parse(h.run(`JSON.stringify(navigationForUser(${JSON.stringify(user)}).map(item=>item[0]))`));
  assert.deepEqual(ids({role:'member',is_platform_admin:false}),['workspace','image','history','generations','profile']);
  assert.deepEqual(ids({role:'enterprise_admin',is_platform_admin:false}),['workspace','image','history','generations','knowledge','assets','recent-tasks','members','enterprise','profile']);
  assert.deepEqual(ids({role:'enterprise_admin',is_platform_admin:true}),['workspace','image','history','generations','knowledge','assets','recent-tasks','members','enterprise','platform-skills','platform-agents','profile']);
  h.run("me={role:'member',is_platform_admin:false}");
  assert.equal(h.run("allowedPage('members')"),'workspace');
  assert.equal(h.run("pageRoutes.members"),'/members');
});

test('saved Agent configuration summary makes empty bindings explicit',()=>{
  const h=harness('/platform/agents');
  const html=h.run("configurationSummaryHtml({persona:'Business persona',model:{label:'Approved model'},output_policy:'text',credit_cost:3,skills:[],tools:[],runtime_test_status:'passed',configuration_fingerprint:'f'.repeat(64)},'本次验证配置')");
  assert.ok(html.includes('本次验证配置'));
  assert.ok(html.includes('Business persona'));
  assert.ok(html.includes('3 积分 / 次'));
  assert.ok(html.includes('未绑定 Skill'));
  assert.ok(html.includes('未绑定 Tool'));
  const validation=h.run("validationSummaryHtml({status:'passed',errors:[]})");
  assert.ok(validation.includes('Validation 摘要'));
  assert.ok(validation.includes('已通过'));
});

test('production publish requires an explicit in-page final configuration confirmation',()=>{
  assert.ok(source.includes('id="production-publish-confirmation"'));
  assert.ok(source.includes('id="publish-final-confirm"'));
  assert.ok(source.includes('version.production_ready!==true'));
  assert.ok(source.includes("configurationSummaryHtml(summary,'Production Publish 配置')"));
  assert.ok(source.includes('validationSummaryHtml(version.validation_summary)'));
});

test('Tenant Instance actions enforce disable-before-reconfigure',()=>{
  const h=harness('/platform/agents');
  const actions=status=>JSON.parse(h.run(`JSON.stringify(instanceActionsForStatus('${status}'))`));
  assert.deepEqual(actions('unconfigured'),['configure']);
  assert.deepEqual(actions('configured'),['configure','enable']);
  assert.deepEqual(actions('enabled'),['disable']);
  assert.deepEqual(actions('disabled'),['configure','enable']);
});

test('customer error fallback never uses a raw backend detail',()=>{
  const h=harness('/workspace');
  assert.equal(h.run('fallbackErrorCode(503)'),'SERVICE_TEMPORARILY_UNAVAILABLE');
  assert.equal(h.run("customerErrorMessages[fallbackErrorCode(503)]"),'服务暂时不可用，请稍后重试。');
  assert.equal(h.run('customerErrorMessages.ACCOUNT_DISABLED'),'账号已停用，请联系企业管理员。');
});

test('member status UI exposes enable and disable without deleting identities',()=>{
  assert.ok(source.includes("item.status==='enabled'?'已启用':'已停用'"));
  assert.ok(source.includes("data-member-status=\"${item.status==='enabled'?'disabled':'enabled'}\""));
  assert.ok(source.includes('/status`,{method:\'PUT\''));
  assert.ok(!source.includes('data-delete-member'));
});

test('P1 customer agent UX keeps examples, copy, regeneration and real reference rendering in the business UI',()=>{
  const h=harness('/workspace');
  for(const id of ['image-agent','copywriting-agent','campaign-agent']){
    const html=h.run(`agentFirstUseHtml({id:'${id}',name:'测试智能体',description:'说明'})`);
    assert.ok(html.includes('适合做什么'));
    assert.ok(html.includes('data-example-prompt'));
  }
  const message=h.run("messageHtml({role:'assistant',content:'最终回复',created_at:'2026-09-15T00:00:00',references:{knowledge:[{id:'file-1',name:'品牌规范.pdf'}]}},'原始需求')");
  assert.ok(message.includes('data-copy-response'));
  assert.ok(message.includes('data-regenerate'));
  assert.ok(message.includes('参考资料'));
  assert.ok(message.includes('品牌规范.pdf'));
  assert.ok(source.includes("input.value=node.dataset.regenerate"));
  assert.ok(!source.includes('asset_retrievals'));
});

test('P1 recent task navigation remains enterprise-admin only',()=>{
  const h=harness('/workspace');
  const enterprise=JSON.parse(h.run("JSON.stringify(navigationForUser({role:'enterprise_admin',is_platform_admin:false}).map(item=>item[0]))"));
  assert.ok(enterprise.includes('recent-tasks'));
  h.run("me={role:'member',is_platform_admin:false}");
  assert.equal(h.run("allowedPage('recent-tasks')"),'workspace');
  assert.equal(h.run("pageRoutes['recent-tasks']"),'/recent-tasks');
});

test('Polish batch keeps the customer workspace focused and renders asset tags as chips',async()=>{
  const h=harness('/workspace');
  const pending=h.run("render('workspace')");
  h.respond('/api/v1/workspace',{brand_name:'测试企业',credit_balance:100,agents:[
    {id:'image-agent',name:'图片生成智能体',description:'制作企业视觉内容',enabled:true,credit_cost:20,icon:'image'},
    {id:'social-content-agent',name:'未开放智能体',description:'不应展示',enabled:false,credit_cost:3,icon:'bot'}
  ],recent_conversations:[],recent_generations:[]});
  await pending;
  assert.ok(h.document.main.innerHTML.includes('适用场景'));
  assert.ok(h.document.main.innerHTML.includes('开始第一次 AI 对话'));
  assert.ok(!h.document.main.innerHTML.includes('未开放智能体'));
  assert.ok(!h.document.main.innerHTML.includes('即将上线'));
  assert.equal(h.run("taskStatusLabel('running')"),'处理中');
  assert.equal(h.run("knowledgeStatus('embedding')"),'知识处理中');
  const tags=h.run("assetTagMarkup('[\\\"brand\\\",\\\"logo\\\"]')");
  assert.ok(tags.includes('asset-tag'));
  assert.ok(tags.includes('brand'));
  assert.ok(tags.includes('logo'));
});

test('Workspace banner aligns agent cards, limits recent lists, and exposes profile navigation',async()=>{
  const h=harness('/workspace');
  const pending=h.run("render('workspace')");
  h.respond('/api/v1/workspace',{brand_name:'测试企业',credit_balance:100,agents:[
    {id:'image-agent',name:'图片生成智能体',description:'制作企业视觉内容',enabled:true,credit_cost:20,icon:'image'},
    {id:'copywriting-agent',name:'文案创作智能体',description:'制作企业文案',enabled:true,credit_cost:3,icon:'type'},
    {id:'campaign-agent',name:'活动策划智能体',description:'制作企业活动',enabled:true,credit_cost:8,icon:'calendar-days'}
  ],recent_conversations:[
    {id:'one',agent_id:'image-agent',title:'项目一'},
    {id:'two',agent_id:'image-agent',title:'项目二'},
    {id:'three',agent_id:'image-agent',title:'项目三'},
    {id:'four',agent_id:'image-agent',title:'项目四'}
  ],recent_generations:[
    {project:{name:'生成一'}},{project:{name:'生成二'}},{project:{name:'生成三'}},{project:{name:'生成四'}}
  ]});
  await pending;
  const markup=h.document.main.innerHTML;
  assert.ok(markup.includes('workspace-greeting-banner'));
  assert.ok(markup.includes('workspace-greeting-banner-v1.png'));
  assert.ok(!markup.includes('agent-card featured'));
  assert.match(fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8'),/\.topbar\{justify-content:flex-end\}/);
  assert.equal((markup.match(/agent-card-footer/g)||[]).length,3);
  assert.equal((markup.match(/data-agent-conversation=/g)||[]).length,3);
  assert.ok(markup.includes('生成三'));
  assert.ok(!markup.includes('生成四'));
  assert.match(source,/class="side-user" data-go="profile" aria-label="进入个人中心"/);
});

test('mobile Drawer controls exist and sidebar has a responsive replacement',()=>{
  assert.match(source,/mobile-menu-button/);
  assert.match(source,/mobile-drawer/);
  const css=fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8');
  assert.match(css,/\.mobile-drawer\.open\{transform:translateX\(0\)\}/);
  assert.match(css,/@media\(max-width:860px\)/);
});

test('Workspace banner uses a new static asset version',()=>{
  const index=fs.readFileSync(path.join(__dirname,'../app/static/index.html'),'utf8');
  assert.match(index,/workbench\.css\?v=workspace-banner-v3/);
  assert.match(index,/workbench\.js\?v=workspace-banner-v3/);
});

test('Stage 1 Agent management direct/refresh prefers pathname over old profile state',async()=>{
  const h=harness('/platform/agents',{page:'profile'});
  assert.equal(h.run('pageFromNavigation()'),'platform-agents');
  await Promise.all([h.run('render(pageFromNavigation())'),h.settle()]);
  h.consistent('platform-agents','Agent 管理');
  assert.ok(h.document.main.innerHTML.includes('服务端保存配置'));
  assert.ok(h.document.main.innerHTML.includes('真实 Runtime Test'));
});
test('Agent management profile navigation/back/forward remains route-consistent',async()=>{
  const h=harness('/platform/agents');
  await Promise.all([h.run("navigate('platform-agents',{replace:true})"),h.settle()]);
  await Promise.all([h.run("navigate('profile')"),h.settle()]);
  h.history.move(-1);await h.settle();h.consistent('platform-agents','Agent 管理');
  h.history.move(1);await h.settle();h.consistent('profile','个人中心');
});
test('late Agent control-plane fetch cannot overwrite newer profile view',async()=>{
  const h=harness('/platform/agents'),old=h.run("navigate('platform-agents')");
  const current=h.run("navigate('profile')");h.respond('/api/v1/workspace',{brand_name:'Test'});await current;
  await h.settle();await old;h.consistent('profile','个人中心');
});
test('Stage 1 management navigation remains platform-admin only',()=>{
  const h=harness('/platform/agents');h.run('me.is_platform_admin=false');
  assert.equal(h.run('pageFromNavigation()'),'workspace');
});
