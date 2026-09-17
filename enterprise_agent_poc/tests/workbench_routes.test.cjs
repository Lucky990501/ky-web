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
  const eventSources = [];
  const timers = [];let nextTimerId=1;
  const navs = ['workspace', 'image', 'profile', 'platform-skills', 'platform-agents'].map(page => ({dataset:{page}, classList:{active:false, toggle(name,value){this.active=value;}}}));
  const document = {
    prompt:{value:'',focused:false,focus(){this.focused=true;}},
    taskStatus:null,
    querySelector(selector){if(selector === '#main')return this.main; if(selector==='#side-credit')return null; if(selector==='#prompt')return this.prompt; if(selector==='#task-status')return this.taskStatus; return {};},
    querySelectorAll(selector){return selector === '[data-page]' ? navs : [];},
    addEventListener(){},
    createElement(){
      const retryButton={};
      return {set innerHTML(value){this._html=value;this.firstElementChild={id:'',querySelector(selector){return selector==='button'?retryButton:null;}};}};
    },
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
  class EventSource {constructor(url){this.url=url;this.listeners={};this.closed=false;eventSources.push(this);}addEventListener(name,listener){this.listeners[name]=listener;}close(){this.closed=true;}}
  const context = vm.createContext({document,window,location,console,Map,Set,Date,EventSource,setTimeout(fn,delay=0){const timer={id:nextTimerId++,fn,delay,cancelled:false};timers.push(timer);return timer.id;},clearTimeout(id){const timer=timers.find(item=>item.id===id);if(timer)timer.cancelled=true;},
    fetch(url,options){return new Promise((resolve,reject)=>pending.push({url,options,resolve(body){resolve({ok:true,json:async()=>body});},reject}));},
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
  function runTimers(){for(const timer of timers.splice(0)){if(!timer.cancelled)timer.fn();}}
  return {run,respond,settle,consistent,pending,timers,runTimers,eventSources,document,history,location,navs};
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
  const imageMessage=h.run("messageHtml({role:'assistant',content:'图片已生成',created_at:'2026-09-15T00:00:00',generation:{storage_key:'tenant-a/image.png'}})");
  assert.ok(imageMessage.includes('message-generation-actions'));
  assert.ok(imageMessage.includes('data-open-message-generation'));
  const userMessage=h.run("messageHtml({role:'user',content:'请调整方案',created_at:'2026-09-15T00:00:00'})");
  assert.ok(userMessage.includes('chat-message-user'));
  assert.ok(userMessage.includes('chat-message-content-user'));
  const markdown=h.run(`markdownHtml(${JSON.stringify('# 标题\n\n**重点**\n- 第一项\n- 第二项\n\n> 引用内容\n\n<script>alert(1)</script>')})`);
  assert.ok(markdown.includes('<h1>标题</h1>'));
  assert.ok(markdown.includes('<strong>重点</strong>'));
  assert.ok(markdown.includes('<ul><li>第一项</li><li>第二项</li></ul>'));
  assert.ok(markdown.includes('<blockquote>引用内容</blockquote>'));
  assert.ok(markdown.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
  const css=fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8');
  assert.match(css,/\.chat-message-assistant \.chat-message-content\{max-height:none;overflow:visible/);
  assert.match(css,/\.chatgpt-conversation-layout \.chat-message-assistant>b/);
  assert.ok(source.includes("input.value=node.dataset.regenerate"));
  assert.ok(!source.includes('asset_retrievals'));
});

test('history rows keep titles and descriptions to ten characters',()=>{
  const h=harness('/workspace');
  assert.equal(h.run("compactHistoryText('这是一个超过十个字的项目名称')"),'这是一个超过十个字的');
  const row=h.run("conversationRowHtml({id:'conversation-1',agent_id:'campaign-agent',project:{name:'这是一个超过十个字的项目名称',type:'活动策划项目'},latest_prompt:'这是一个超过十个字的项目描述',latest_status:'completed'},'data-agent-conversation')");
  assert.ok(row.includes('这是一个超过十个字的'));
  assert.ok(!row.includes('这是一个超过十个字的项目名称'));
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
  assert.ok(!markup.includes('workspace-greeting-banner-v1.png'));
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

test('Workspace greeting uses the color block without a banner image',()=>{
  const index=fs.readFileSync(path.join(__dirname,'../app/static/index.html'),'utf8');
  assert.match(index,/workbench\.css\?v=chatgpt-style-conversation-ux-v2/);
  assert.match(index,/workbench\.js\?v=chatgpt-style-conversation-ux-v2/);
  assert.ok(!source.includes('workspace-greeting-banner-v1.png'));
});

test('live task state stays compact and uses customer-facing thinking copy',()=>{
  const h=harness('/agents/campaign');
  const css=fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8');
  assert.equal(h.run("taskStageLabel('starting_runtime')"),'正在思考');
  assert.equal(h.run("taskStageLabel('generating')"),'正在生成回答');
  assert.ok(source.includes('streaming-message'));
  assert.ok(source.includes("source.addEventListener('delta'"));
  assert.match(css,/max-width:820px/);
  assert.match(css,/\.streaming-message/);
  assert.match(css,/\.thinking-summary/);
  assert.ok(source.includes("source.addEventListener('activity'"));
});

test('streaming conversation state orders deltas and accepts the authoritative completion',()=>{
  const h=harness('/agents/campaign');
  h.run('streamState=createStreamingState()');
  assert.equal(h.run("applyStreamingEvent(streamState,'progress',{stage:'loading_context'})"),true);
  assert.equal(h.run('streamState.status'),'正在思考');
  assert.equal(h.run("applyStreamingEvent(streamState,'delta',{sequence:2,text:'正文'})"),false);
  assert.equal(h.run("applyStreamingEvent(streamState,'delta',{sequence:1,text:'# 标题\\n\\n'})"),true);
  assert.equal(h.run('streamState.text'),'# 标题\n\n正文');
  assert.equal(h.run("applyStreamingEvent(streamState,'delta',{sequence:2,text:'重复正文'})"),false);
  assert.equal(h.run('streamState.text'),'# 标题\n\n正文');
  assert.equal(h.run("applyStreamingEvent(streamState,'complete',{final_response:'## 最终正文'})"),true);
  assert.equal(h.run('streamState.finalResponse'),'## 最终正文');
  assert.ok(h.run("streamingFinalContentHtml(streamState.finalResponse,'原始需求')").includes('<h2>最终正文</h2>'));
  assert.ok(h.run("streamingMessageHtml('正在思考')").includes('data-stream-content'));
  assert.ok(h.run("streamingMessageHtml('正在思考')").includes('data-thinking'));
});

test('safe activity events render only friendly stages and thinking auto-collapses on the first real delta',()=>{
  const h=harness('/agents/campaign');
  h.run("activityState=createStreamingState();activityCases=[['queued','正在准备'],['context_loading','正在理解上下文'],['knowledge_retrieving','正在检索企业知识'],['tool_running','正在调用工具'],['generating','正在生成回答']];activityCases.forEach(([stage],index)=>applyStreamingEvent(activityState,'activity',{sequence:index+1,stage,status:'started',created_at:'2026-09-18T00:00:00Z'}));applyStreamingEvent(activityState,'activity',{sequence:6,stage:'context_loading',status:'completed',created_at:'2026-09-18T00:00:01Z'});");
  assert.equal(h.run('activityState.currentActivity.label'),'正在理解上下文');
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(activityState.activities.map(item=>item.label))')),['正在准备','正在理解上下文','正在检索企业知识','正在调用工具','正在生成回答']);
  assert.equal(h.run('activityState.activities[1].status'),'completed');
  assert.equal(h.run("applyStreamingEvent(activityState,'activity',{sequence:7,stage:'unsafe_debug',status:'started'})"),false);
  assert.equal(h.run("applyStreamingEvent(activityState,'delta',{sequence:1,text:'第一段'})"),true);
  assert.ok(h.run('activityState.thinkingFinishedAt')>0);
  assert.ok(h.run("streamingThinkingHtml('正在准备')").includes('查看进度'));
  h.run("thinkingTitle={textContent:''};thinkingList={innerHTML:''};thinkingDetails={open:true,querySelector(selector){return selector==='[data-thinking-title]'?thinkingTitle:selector==='[data-thinking-list]'?thinkingList:null;}};thinkingNode={querySelector(selector){return selector==='[data-thinking]'?thinkingDetails:null;}};activityState.thinkingStartedAt=Date.now()-2100;renderThinkingSummary(thinkingNode,activityState);thinkingDetails.open=true;renderThinkingSummary(thinkingNode,activityState);");
  assert.match(h.run('thinkingTitle.textContent'),/^思考了 [3-9]\d* 秒$/);
  assert.equal(h.run('thinkingDetails.open'),true);
});

test('visual buffer consumes real pending deltas in bounded frames and final markdown remains authoritative',()=>{
  const h=harness('/agents/campaign');
  h.run("bufferState=createStreamingState();for(let sequence=1;sequence<=1000;sequence+=1)applyStreamingEvent(bufferState,'delta',{sequence,text:'x'});");
  assert.equal(h.run('bufferState.text.length'),1000);
  assert.equal(h.run('bufferState.displayText.length'),0);
  h.run('consumeStreamingVisualBuffer(bufferState)');
  assert.ok(h.run('bufferState.displayText.length')>0);
  assert.ok(h.run('bufferState.displayText.length')<1000);
  h.run("markdownState=createStreamingState();applyStreamingEvent(markdownState,'delta',{sequence:1,text:'# 标题\\n\\n- 条目\\n\\n正文\\n\\n```js\\nconst answer = 42;\\n```'});markdownState.displayText=markdownState.text;parts=streamingMarkdownParts(markdownState.displayText)");
  assert.ok(h.run("markdownHtml(parts.stable)").includes('<h1>标题</h1>'));
  assert.ok(h.run("markdownHtml(parts.stable)").includes('<li>条目</li>'));
  assert.ok(h.run("markdownHtml(parts.active)").includes('<pre><code>const answer = 42;</code></pre>'));
});

test('streaming failure keeps partial text outside persisted history and retains retry UI',()=>{
  const h=harness('/agents/campaign');
  h.run("failureState=createStreamingState();applyStreamingEvent(failureState,'delta',{sequence:1,text:'已生成部分正文'});applyStreamingEvent(failureState,'error',{message:'生成中断',diagnostic_id:'diagnostic-1'})");
  assert.equal(h.run('failureState.text'),'已生成部分正文');
  assert.equal(h.run('failureState.error'),'生成中断');
  const failure=h.run("streamingFailureHtml(failureState.text,failureState.error,failureState.diagnosticId)");
  assert.ok(failure.includes('部分回复（未保存）'));
  assert.ok(failure.includes('已生成部分正文'));
  assert.ok(failure.includes('重新尝试'));
  assert.ok(!failure.includes('conversation_id'));
  assert.ok(source.includes('pollAgentTaskV2(taskId,agentId,main,retryText,node,state)'));
  assert.ok(source.includes("source.addEventListener('error'"));
});

test('streaming complete also supports providers that emit no deltas',()=>{
  const h=harness('/agents/campaign');
  h.run("directComplete=createStreamingState();applyStreamingEvent(directComplete,'complete',{final_response:'无需 delta 的最终正文'})");
  assert.equal(h.run('directComplete.text'),'');
  assert.equal(h.run('directComplete.finalResponse'),'无需 delta 的最终正文');
});

test('conversation run state makes one cancel request and keeps STOPPING until the backend confirms a terminal event',async()=>{
  const h=harness('/agents/campaign');
  h.run("stopInput={disabled:false};stopSubmit={classList:{toggle(){}},setAttribute(){},innerHTML:'',onclick:null};stopFeedback={textContent:''};stopMain={querySelector(selector){return selector==='#prompt'?stopInput:selector==='#composer-submit'?stopSubmit:selector==='#composer-run-feedback'?stopFeedback:null;}};stopState=createStreamingState();beginConversationRun(stopState,{taskId:'task-1',agentId:'campaign-agent',phase:ConversationRunPhase.SUBMITTING});beginConversationRun(stopState,{taskId:'task-1',agentId:'campaign-agent',phase:ConversationRunPhase.RUNNING});");
  const stop=h.run('requestStopGeneration(stopMain,stopState)');
  assert.equal(h.run('stopState.run.phase'),'STOPPING');
  assert.equal(h.pending.length,1);
  assert.equal(h.pending[0].url,'/api/v1/tasks/task-1/cancel');
  assert.equal(h.pending[0].options.method,'POST');
  await h.run('requestStopGeneration(stopMain,stopState)');
  assert.equal(h.pending.length,1);
  h.respond('/api/v1/tasks/task-1/cancel',{status:'cancelling'});await stop;
  assert.equal(h.run('stopState.run.phase'),'STOPPING');
});

test('cancelled terminal preserves only rendered partial content, clears the task guard, and restores send state',()=>{
  const h=harness('/agents/campaign');
  h.run("cancelInput={disabled:true};cancelSubmit={classList:{toggle(){}},setAttribute(){},innerHTML:'',onclick:null};cancelFeedback={textContent:''};cancelMain={querySelector(selector){return selector==='#prompt'?cancelInput:selector==='#composer-submit'?cancelSubmit:selector==='#composer-run-feedback'?cancelFeedback:null;}};cancelCaret={removed:false,remove(){this.removed=true;}};cancelActive={innerHTML:'已有部分回复'};cancelLabel={textContent:''};cancelStatus={hidden:true,classList:{add(){}},querySelector(selector){return selector==='span'?cancelLabel:selector==='svg'||selector==='i'?{remove(){}}:null;}};cancelContent={querySelector(selector){return selector==='[data-stream-active]'?cancelActive:selector==='[data-stream-caret]'?cancelCaret:null;}};cancelNode={id:'task-status',_streamState:createStreamingState(),removeAttribute(name){if(name==='id'){this.id='';document.taskStatus=null;}},classList:{add(){}},querySelector(selector){return selector==='[data-stream-content]'?cancelContent:selector==='[data-stream-status]'?cancelStatus:null;}};cancelNode._streamState.text='已有部分回复';beginConversationRun(cancelNode._streamState,{taskId:'task-2',agentId:'campaign-agent',phase:ConversationRunPhase.SUBMITTING});beginConversationRun(cancelNode._streamState,{taskId:'task-2',agentId:'campaign-agent',phase:ConversationRunPhase.RUNNING});document.taskStatus=cancelNode;cancelStreamingMessage(cancelNode,cancelNode._streamState,cancelMain);");
  assert.equal(h.run('cancelCaret.removed'),true);
  assert.equal(h.run('cancelLabel.textContent'),'已停止生成 · 部分回复未保存');
  assert.equal(h.run('cancelNode.id'),'');
  assert.equal(h.run('cancelActive.innerHTML'),'已有部分回复');
  assert.equal(h.run('cancelNode._streamState.run.phase'),'IDLE');
  assert.equal(h.run('cancelInput.disabled'),false);
  assert.match(h.run('cancelSubmit.innerHTML'),/data-lucide="send"/);
});

test('cancelled SSE event stops the source and leaves a non-persisted partial marker',()=>{
  const h=harness('/agents/campaign');
  h.run("sseInput={disabled:true};sseSubmit={classList:{toggle(){}},setAttribute(){},innerHTML:'',onclick:null};sseFeedback={textContent:''};sseMain={querySelector(selector){return selector==='#prompt'?sseInput:selector==='#composer-submit'?sseSubmit:selector==='#composer-run-feedback'?sseFeedback:null;}};sseCaret={remove(){this.removed=true;}};sseLabel={textContent:''};sseStatus={hidden:true,classList:{add(){}},querySelector(selector){return selector==='span'?sseLabel:selector==='svg'||selector==='i'?{remove(){}}:null;}};sseContent={querySelector(selector){return selector==='[data-stream-caret]'?sseCaret:selector==='[data-stream-active]'?{innerHTML:'部分正文'}:null;}};sseNode={id:'task-status',removeAttribute(name){if(name==='id')this.id='';},classList:{add(){}},querySelector(selector){return selector==='[data-stream-content]'?sseContent:selector==='[data-stream-status]'?sseStatus:null;}};sseState=createStreamingState();sseState.text='部分正文';beginConversationRun(sseState,{taskId:'task-sse',agentId:'campaign-agent',phase:ConversationRunPhase.SUBMITTING});beginConversationRun(sseState,{taskId:'task-sse',agentId:'campaign-agent',phase:ConversationRunPhase.RUNNING});streamTask('task-sse','campaign-agent',sseMain,'需求',sseNode,sseState);");
  assert.equal(h.eventSources[0].url,'/api/v1/tasks/task-sse/events');
  h.eventSources[0].listeners.cancelled({data:'{"status":"cancelled"}'});
  assert.equal(h.eventSources[0].closed,true);
  assert.equal(h.run('sseState.run.phase'),'IDLE');
  assert.equal(h.run('sseLabel.textContent'),'已停止生成 · 部分回复未保存');
});

test('cancelled wins the frontend race over a later complete, and refresh state restores active task controls',()=>{
  const h=harness('/agents/campaign');
  h.run("raceState=createStreamingState();beginConversationRun(raceState,{taskId:'task-3',agentId:'campaign-agent',phase:ConversationRunPhase.SUBMITTING});beginConversationRun(raceState,{taskId:'task-3',agentId:'campaign-agent',phase:ConversationRunPhase.RUNNING});raceState.run.terminal='cancelled';finishStreamTask(null,raceState,{status:'completed',final_response:'不应覆盖'},'campaign-agent',null,'需求');");
  assert.equal(h.run('raceState.finalResponse'),undefined);
  assert.equal(h.run("activeConversationTask({tasks:[{id:'done',status:'completed'},{id:'running',status:'running'}]}).id"),'running');
  h.run("restored=createStreamingState();beginConversationRun(restored,{taskId:'task-4',agentId:'campaign-agent',phase:ConversationRunPhase.STOPPING,restored:true});");
  assert.equal(h.run('restored.run.phase'),'STOPPING');
});

test('streaming visual buffer coalesces deltas and keeps completed markdown blocks stable',()=>{
  const h=harness('/agents/campaign');
  h.run("stableNode={innerHTML:''};activeNode={innerHTML:''};statusNode={hidden:false,querySelector(){return {textContent:''}}};contentNode={querySelector(selector){return selector==='[data-stream-stable]'?stableNode:selector==='[data-stream-active]'?activeNode:null;}};visualNode={querySelector(selector){return selector==='[data-stream-content]'?contentNode:selector==='[data-stream-status]'?statusNode:null;}};visualState=createStreamingState();visualState.lastRenderAt=Date.now();for(let sequence=1;sequence<=1000;sequence+=1){applyStreamingEvent(visualState,'delta',{sequence,text:'x'});scheduleStreamingRender(visualNode,visualState)}");
  assert.equal(h.timers.length,1);
  assert.ok(h.timers[0].delay>=0&&h.timers[0].delay<=40);
  h.runTimers();
  assert.equal(h.run('visualState.text.length'),1000);
  assert.ok(h.run('activeNode.innerHTML').includes('streaming-caret'));
  assert.equal(h.run('statusNode.hidden'),true);
  h.run("parts=streamingMarkdownParts('# 标题\\n\\n- 第一项\\n- 第二项\\n\\n最后一段')");
  assert.ok(h.run('parts.stable').includes('# 标题'));
  assert.ok(h.run('parts.stable').includes('- 第二项'));
  assert.equal(h.run('parts.active'),'最后一段');
});

test('streaming follow stops for reading and resumes at the bottom threshold',()=>{
  const h=harness('/agents/campaign');
  const root={scrollHeight:1000,scrollTop:500,clientHeight:500,listeners:{},addEventListener(name,listener){this.listeners[name]=listener;},removeEventListener(name){delete this.listeners[name];}};
  h.document.scrollingElement=root;
  h.run('followState=createStreamingState();followState.follow=true;watchStreamingFollow(followState)');
  root.scrollTop=300;root.listeners.scroll();
  assert.equal(h.run('followState.follow'),false);
  root.scrollTop=468;root.listeners.scroll();
  assert.equal(h.run('followState.follow'),true);
});

test('matching completion removes only the streaming affordances while authoritative completion still wins',()=>{
  const h=harness('/agents/campaign');
  h.run("completionStable={innerHTML:''};completionActive={innerHTML:''};caret={removed:false,remove(){this.removed=true;}};completionContent={querySelector(selector){if(selector==='[data-stream-stable]')return completionStable;if(selector==='[data-stream-active]')return completionActive;if(selector==='[data-stream-caret]')return caret;return null;}};completionStatus={hidden:false,querySelector(){return {textContent:''}}};completionActions={hidden:true,innerHTML:''};completionNode={id:'task-status',innerHTML:'',removeAttribute(name){if(name==='id')this.id='';},classList:{remove(){}},querySelector(selector){if(selector==='[data-stream-content]')return completionContent;if(selector==='[data-stream-actions]')return completionActions;if(selector==='[data-stream-status]')return completionStatus;return null;}};completionState=createStreamingState();completionState.text='同一份最终正文';completionState.finalResponse='同一份最终正文';completeStreamingMessage(completionNode,completionState,'原始需求',document.main)");
  assert.equal(h.run('completionNode.innerHTML'),'');
  assert.equal(h.run('completionNode.id'),'');
  assert.equal(h.run('caret.removed'),true);
  assert.equal(h.run('completionStatus.hidden'),true);
  assert.ok(h.run('completionActions.innerHTML').includes('复制'));
  h.run("authoritativeNode={innerHTML:'',removeAttribute(){},classList:{remove(){}}};authoritativeState=createStreamingState();authoritativeState.text='流式草稿';authoritativeState.finalResponse='服务端最终正文';completeStreamingMessage(authoritativeNode,authoritativeState,'原始需求',document.main)");
  assert.ok(h.run('authoritativeNode.innerHTML').includes('服务端最终正文'));
});

test('final task lifecycle releases the submit guard after streaming, polling, and failure',()=>{
  const h=harness('/agents/campaign');
  h.run("streamComplete=createStreamingState();applyStreamingEvent(streamComplete,'complete',{final_response:'流式完成正文'});streamNode={id:'task-status',removeAttribute(name){if(name==='id'){this.id='';document.taskStatus=null;}},classList:{remove(){}},innerHTML:''};document.taskStatus=streamNode;completeStreamingMessage(streamNode,streamComplete,'原始需求',document.main)");
  assert.equal(h.run("document.querySelector('#task-status')"),null);
  assert.equal(h.run('streamNode.id'),'');
  assert.ok(h.run('streamNode.innerHTML').includes('流式完成正文'));

  h.run("pollComplete=createStreamingState();pollNode={id:'task-status',removeAttribute(name){if(name==='id'){this.id='';document.taskStatus=null;}},classList:{remove(){}},innerHTML:''};document.taskStatus=pollNode;finishStreamTask(pollNode,pollComplete,{status:'completed',final_response:'轮询完成正文',conversation_id:'conversation-1'},'campaign-agent',document.main,'轮询需求')");
  assert.equal(h.run("document.querySelector('#task-status')"),null);
  assert.equal(h.run('pollNode.id'),'');

  h.run("failedNode={id:'task-status',_streamState:{text:'已生成的部分内容'},replaceWith(failure){this.failure=failure;document.taskStatus=null;}};document.taskStatus=failedNode;replaceTaskFailure(failedNode,'生成中断','重试需求','diagnostic-1');failedNode.failure.querySelector('button').onclick()");
  assert.equal(h.run("document.querySelector('#task-status')"),null);
  assert.equal(h.run('failedNode.failure.id'),'');
  assert.equal(h.document.prompt.value,'重试需求');
  assert.equal(h.document.prompt.focused,true);
  assert.ok(source.includes("if(!text||document.querySelector('#task-status'))return;"));
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
