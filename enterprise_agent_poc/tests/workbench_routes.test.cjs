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
  const downloads = [];
  const sessionValues = new Map();
  const sessionStorage={get length(){return sessionValues.size;},key(index){return [...sessionValues.keys()][index]||null;},getItem(key){return sessionValues.get(key)||null;},setItem(key,value){sessionValues.set(key,String(value));},removeItem(key){sessionValues.delete(key);}};
  const timers = [];let nextTimerId=1;
  const navs = ['workspace', 'image', 'profile', 'platform-skills', 'platform-agents'].map(page => ({dataset:{page}, classList:{active:false, toggle(name,value){this.active=value;}}}));
  const document = {
    prompt:{value:'',focused:false,focus(){this.focused=true;}},
    taskStatus:null,
    querySelector(selector){if(selector === '#main')return this.main; if(selector==='#side-credit')return null; if(selector==='#prompt')return this.prompt; if(selector==='#task-status')return this.taskStatus; return {};},
    querySelectorAll(selector){return selector === '[data-page]' ? navs : [];},
    addEventListener(){},
    body:{appendChild(){}},
    createElement(tag){
      if(tag==='a')return {href:'',download:'',hidden:false,click(){downloads.push({href:this.href,filename:this.download});},remove(){}};
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
  const location = {pathname,origin:'http://localhost'};
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
  class BrowserURL extends URL {}
  BrowserURL.createObjectURL=()=>`blob:http://localhost/document-${downloads.length+1}`;
  BrowserURL.revokeObjectURL=()=>{};
  const context = vm.createContext({document,window,location,sessionStorage,console,Map,Set,Date,URL:BrowserURL,EventSource,FormData:class{constructor(){this.items=[];}append(name,file,filename){this.items.push({name,file,filename});}},setTimeout(fn,delay=0){const timer={id:nextTimerId++,fn,delay,cancelled:false};timers.push(timer);return timer.id;},clearTimeout(id){const timer=timers.find(item=>item.id===id);if(timer)timer.cancelled=true;},
    fetch(url,options){return new Promise((resolve,reject)=>pending.push({url,options,resolve(body){resolve({ok:true,json:async()=>body});},resolveResponse:resolve,reject}));},
  });
  vm.runInContext(source,context);
  vm.runInContext("me={user_id:'user-test',tenant_id:'tenant-test',is_platform_admin:true,display_name:'Test',email:'test@example.invalid',tenant_name:'Test',role:'member'}", context);
  const run = code => vm.runInContext(code,context);
  function respond(url, body){const request=pending.find(x=>x.url===url&&!x.done); assert.ok(request,`expected request ${url}`); request.done=true; request.resolve(body);}
  function respondError(url,status){const request=pending.find(x=>x.url===url&&!x.done);assert.ok(request,`expected request ${url}`);request.done=true;request.resolveResponse({ok:false,status,headers:{get(){return '';}},json:async()=>({})});}
  function respondDownload(url){const request=pending.find(x=>x.url===url&&!x.done);assert.ok(request,`expected request ${url}`);request.done=true;request.resolveResponse({ok:true,headers:{get:()=> 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},blob:async()=>new Blob(['docx bytes'],{type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'})});}
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
  return {run,respond,respondError,respondDownload,settle,consistent,pending,timers,runTimers,eventSources,downloads,sessionStorage,document,history,location,navs};
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

test('P1 customer agent UX keeps shared greeting for image and copywriting, plus response actions',()=>{
  const h=harness('/workspace');
  for(const id of ['image-agent','copywriting-agent']){
    const html=h.run(`agentGreetingHtml({id:'${id}',name:'测试智能体',description:'说明'})`);
    assert.ok(html.includes('class="agent-greeting"'));
    assert.ok(!html.includes('适合做什么'));
    assert.ok(!html.includes('data-example-prompt'));
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
    {id:'image-agent',name:'图片生成智能体',description:'制作企业视觉内容',category:'视觉创作',enabled:true,credit_cost:20,icon:'image'},
    {id:'social-content-agent',name:'未开放智能体',description:'不应展示',enabled:false,credit_cost:3,icon:'bot'}
  ],recent_conversations:[],recent_generations:[]});
  await pending;
  assert.ok(h.document.main.innerHTML.includes('视觉创作'));
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
  assert.match(index,/workbench\.css\?v=image-result-ui-status-v1/);
  assert.match(index,/workbench\.js\?v=image-result-ui-status-v1/);
  assert.ok(!source.includes('workspace-greeting-banner-v1.png'));
});

test('live task state stays compact and uses customer-facing thinking copy',()=>{
  const h=harness('/agents/campaign');
  const css=fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8');
  assert.equal(h.run("taskStageLabel('starting_runtime')"),'正在处理你的请求');
  assert.equal(h.run("taskStageLabel('generating')"),'正在处理你的请求');
  assert.equal(h.run("activityDisplayLabel('generating','started')"),'正在生成内容');
  assert.ok(source.includes('streaming-message'));
  assert.ok(source.includes("source.addEventListener('delta'"));
  assert.match(css,/max-width:820px/);
  assert.match(css,/\.streaming-message/);
  assert.match(css,/\.thinking-summary/);
  assert.ok(source.includes("source.addEventListener('activity'"));
});

function imageRailHarness(){
  const h=harness('/agents/image');
  h.document.main.dataset={page:'image',agentId:'image-agent'};
  const badge={className:'history-status failed',textContent:'失败'};
  const row={dataset:{selectAgentConversation:'conversation-1'},querySelector:()=>badge};
  const rail={innerHTML:'old failed list',scrollTop:18,querySelectorAll:()=>[row]};
  h.document.main.nodes.set('.conversation-rail',rail);
  h.run("activeConversationId='conversation-1'");
  return {...h,rail,badge};
}
const imageProject=(status,taskId='task-2',generationTaskId=taskId)=>({id:'conversation-1',agent_id:'image-agent',project:{name:'图片项目',type:'图片生成项目'},latest_status:status,latest_task:{id:taskId,status},latest_generation:generationTaskId?{task_id:generationTaskId,storage_key:'generated/tenant-test/image.png'}:null});

test('image preview is bounded and keeps original-image viewer and download URLs',()=>{
  const h=harness('/agents/image'),css=fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8');
  const html=h.run("messageGenerationHtml({task_id:'task-1',storage_key:'generated/tenant-test/portrait.png'})");
  assert.match(css,/\.chatgpt-conversation-layout \.message-generation\{width:min\(360px,100%\);/);
  assert.match(css,/\.message-generation \.media-thumb\{width:100%;height:280px;max-height:280px;aspect-ratio:auto;/);
  assert.match(css,/\.message-generation \.media-thumb img\{[^}]*object-fit:contain/);
  assert.ok(!css.includes('.message-generation{width:min(820px,100%)}'));
  assert.ok(html.includes('data-open-message-generation="/api/v1/storage/generated/tenant-test/portrait.png"'));
  assert.ok(html.includes('href="/api/v1/storage/generated/tenant-test/portrait.png" download'));
});

test('image project labels preserve real task status and distinguish a later failure from an older image',()=>{
  const h=harness('/agents/image');
  for(const [project,expected] of [[imageProject('completed'),'已完成'],[imageProject('failed','failed',null),'失败'],[imageProject('failed','new-failed','old-success'),'最近一次失败'],[imageProject('failed','same-task','same-task'),'失败']]){
    h.run(`project=${JSON.stringify(project)}`);
    assert.equal(h.run('conversationStatusLabel(project)'),expected);
    assert.ok(h.run("conversationRowHtml(project,'data-select-agent-conversation')").includes(`>${expected}</i>`));
    assert.ok(h.run('historyCardHtml(project)').includes(`>${expected}</i>`));
    const notice=h.run('imageConversationStatusHtml(project)');
    if(project.latest_status==='completed')assert.equal(notice,'');
    else assert.ok(notice.includes(expected==='最近一次失败'?'此前已生成的图片仍可查看和下载':'最近一次生成失败'));
  }
});

test('authoritative completion replaces a stale failed badge and refreshes the image rail without rerendering chat',async()=>{
  const h=imageRailHarness();h.document.main.innerHTML='conversation text must stay';
  const pending=h.run("refreshConversationRail(document.main,'image-agent',{id:'task-2',conversation_id:'conversation-1',status:'completed'})");
  assert.equal(h.badge.textContent,'已完成');
  h.respond('/api/v1/conversations',[imageProject('completed'),{id:'other',agent_id:'copywriting-agent',latest_status:'failed'}]);await pending;
  assert.ok(h.rail.innerHTML.includes('history-status completed'));
  assert.ok(!h.rail.innerHTML.includes('history-status failed'));
  assert.ok(!h.rail.innerHTML.includes('data-select-agent-conversation="other"'));
  assert.equal(h.document.main.innerHTML,'conversation text must stay');assert.equal(h.rail.scrollTop,18);
});

test('SSE and polling terminal completion both refresh stale project status',async()=>{
  for(const transport of ['sse','polling']){
    const h=imageRailHarness();
    h.run("imageState=createStreamingState();beginConversationRun(imageState,{taskId:'task-2',agentId:'image-agent',phase:ConversationRunPhase.RUNNING});imageNode={innerHTML:'',dataset:{},removeAttribute(){},classList:{remove(){}},querySelector(){return null}};");
    if(transport==='sse')h.run("finishStreamTask(imageNode,imageState,{id:'task-2',status:'completed',conversation_id:'conversation-1',final_response:'图片已生成'},'image-agent',document.main,'生成需求')");
    else{h.run("pollAgentTaskV2('task-2','image-agent',document.main,'生成需求',imageNode,imageState)");h.respond('/api/v1/tasks/task-2',{id:'task-2',status:'completed',conversation_id:'conversation-1',final_response:'图片已生成'});await flush();}
    assert.equal(h.badge.textContent,'已完成',transport);h.respond('/api/v1/conversations',[imageProject('completed')]);await flush();
    assert.ok(h.rail.innerHTML.includes('history-status completed'),transport);
  }
});

test('image completion reuses the result card only for the matching real generation task',()=>{
  for(const generationTaskId of ['image-task','unrelated-task']){
    const h=harness('/agents/image');
    h.run(`liveImageState=createStreamingState();beginConversationRun(liveImageState,{taskId:'image-task',agentId:'image-agent',phase:ConversationRunPhase.RUNNING});liveImageNode={innerHTML:'',dataset:{},inserted:'',insertAdjacentHTML(position,html){this.inserted+=html;},removeAttribute(){},classList:{remove(){}},querySelector(){return null}};finishStreamTask(liveImageNode,liveImageState,{status:'completed',final_response:'图片已生成',generation:{task_id:'${generationTaskId}',storage_key:'generated/tenant-test/live.png'}},'image-agent',document.main,'需求')`);
    assert.equal(h.run('liveImageNode.inserted.includes("message-generation")'),generationTaskId==='image-task');
    if(generationTaskId==='image-task')assert.ok(h.run('liveImageNode.inserted').includes('data-open-message-generation'));
  }
});

test('failed and cancelled terminal states refresh from backend without promoting old images to success',async()=>{
  for(const [terminal,status] of [['error','failed'],['cancelled','cancelled']]){
    const h=imageRailHarness();
    h.run(`terminalState=createStreamingState();beginConversationRun(terminalState,{taskId:'task-2',agentId:'image-agent',phase:ConversationRunPhase.RUNNING});settleConversationRun(document.main,terminalState,'${terminal}')`);
    h.respond('/api/v1/conversations',[imageProject(status,'task-2','old-success')]);await flush();
    assert.ok(h.rail.innerHTML.includes(`history-status ${status}`));assert.ok(!h.rail.innerHTML.includes('history-status completed'));
  }
});

test('late rail refresh cannot overwrite a newer terminal state or another route',async()=>{
  const h=imageRailHarness(),first=h.run("refreshConversationRail(document.main,'image-agent')"),second=h.run("refreshConversationRail(document.main,'image-agent')");
  h.pending[1].done=true;h.pending[1].resolve([imageProject('failed','new-failed','old-success')]);await second;
  h.pending[0].done=true;h.pending[0].resolve([imageProject('completed')]);await first;
  assert.ok(h.rail.innerHTML.includes('最近一次失败'));assert.ok(!h.rail.innerHTML.includes('history-status completed'));
  const original=h.rail.innerHTML,third=h.run("refreshConversationRail(document.main,'image-agent')");
  h.document.main.dataset.page='profile';h.respond('/api/v1/conversations',[imageProject('completed')]);await third;assert.equal(h.rail.innerHTML,original);
});

test('unavailable rail refresh retains the real completion badge instead of the stale failure',async()=>{
  const h=imageRailHarness(),pending=h.run("refreshConversationRail(document.main,'image-agent',{id:'task-2',conversation_id:'conversation-1',status:'completed'})");
  h.respondError('/api/v1/conversations',503);await pending;assert.equal(h.badge.textContent,'已完成');
});

test('terminal image status stays associated with its task conversation after selection changes',async()=>{
  const h=imageRailHarness(),notice={innerHTML:'current project status'};
  h.document.main.nodes.set('[data-conversation-result-status]',notice);
  h.run("terminalState=createStreamingState();beginConversationRun(terminalState,{taskId:'task-2',agentId:'image-agent',conversationId:'conversation-1',phase:ConversationRunPhase.RUNNING});activeConversationId='conversation-2';settleConversationRun(document.main,terminalState,'completed')");
  assert.equal(h.badge.textContent,'已完成');assert.equal(notice.innerHTML,'current project status');
  h.respondError('/api/v1/conversations',503);await flush();
});

test('R2 four agents share Registry-driven greeting without name matching',()=>{
  const h=harness('/agents/campaign');
  const cases=[
    ['campaign-agent','campaign-planning','活动策划智能体'],
    ['copywriting-agent','copywriting','文案创作智能体'],
    ['image-agent','image-generation','图片生成智能体'],
    ['productized-uuid','wechat-official-account-writing','公众号编写智能体'],
  ];
  for(const [id,slug,name] of cases){
    const html=h.run(`agentGreetingHtml({id:${JSON.stringify(id)},slug:${JSON.stringify(slug)},name:${JSON.stringify(name)},description:'Registry 说明',icon:'newspaper',category:'content'})`);
    assert.match(html,/^<div class="agent-greeting"><span class="agent-greeting-icon" aria-hidden="true">/);
    assert.ok(html.includes(`<h1>你好，我是${name}</h1>`));
    assert.ok(html.includes('<p>Registry 说明</p>'));
    assert.ok(html.includes('>content</span>'));
    assert.ok(html.includes('data-lucide="newspaper"'));
    for(const removed of ['适合做什么','data-example-prompt','快捷推荐','积分 / 次','历史项目'])assert.ok(!html.includes(removed));
  }
  assert.equal((source.match(/const agentGreetingHtml=/g)||[]).length,1);
  assert.ok(source.includes(':agentGreetingHtml(agent)'));
  assert.ok(!source.includes('agentGreetingConfig'));
  assert.ok(!source.includes('agentFirstUse'));
  const v2=source.slice(source.indexOf('async function agentWorkspaceV2('),source.indexOf('async function submitAgentTaskV2('));
  assert.ok(!v2.includes('class="agent-heading"'));
  assert.ok(!v2.includes('history-shortcut" data-go="history"'));
  assert.ok(!v2.includes('发送前可先选择示例'));
});

test('G6-G9 greeting uses one responsive style scale and mobile-safe text wrapping',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8');
  assert.match(css,/\.agent-greeting\{--agent-greeting-icon-size:48px;--agent-greeting-title-size:22px;--agent-greeting-description-size:15px;/);
  assert.match(css,/\.agent-greeting-copy\{min-width:0;overflow-wrap:anywhere\}/);
  assert.match(css,/@media\(max-width:860px\)\{\.agent-greeting\{--agent-greeting-icon-size:44px;--agent-greeting-title-size:20px;/);
  assert.ok(!css.includes('.campaign-opening'));
  assert.ok(source.includes('id="composer"'));
  assert.ok(source.includes('id="chat-body"'));
});

test('R3 R4 R5 R8 renamed and new Registry agents retain stable routes',async()=>{
  const h=harness('/workspace');const pending=h.run("render('workspace')");
  h.respond('/api/v1/workspace',{agents:[
    {id:'campaign-agent',slug:'campaign-planning',name:'活动策划智能体',description:'活动方案',icon:'calendar-days',category:'general',enabled:true,credit_cost:8},
    {id:'copywriting-agent',slug:'copywriting',name:'文案创作智能体',description:'传播文案',icon:'type',category:'general',enabled:true,credit_cost:3},
    {id:'image-agent',slug:'image-generation',name:'图片生成智能体',description:'视觉内容',icon:'image',category:'general',enabled:true,credit_cost:20},
    {id:'wechat-uuid',slug:'wechat-official-account-writing',name:'公众号运营助手',description:'新的 Registry 说明',icon:'newspaper',category:'content',enabled:true,definition_source:'productized',credit_cost:5},
    {id:'mock-uuid',slug:'future-agent',name:'新增测试智能体',description:'自动出现的能力',icon:'bot',category:'experimental',enabled:true,definition_source:'productized',credit_cost:1},
  ],credit_balance:100});await pending;
  const html=h.document.main.innerHTML;
  for(const slug of ['campaign-agent','copywriting-agent','image-agent','wechat-official-account-writing','future-agent'])assert.ok(html.includes(`data-agent="${slug}"`));
  for(const label of ['活动策划智能体','文案创作智能体','图片生成智能体','公众号运营助手','新增测试智能体','新的 Registry 说明','experimental'])assert.ok(html.includes(label));
  assert.ok(!html.includes('data-agent="wechat-uuid"'));
  assert.ok(!html.includes('data-agent="mock-uuid"'));
  assert.equal(h.run("agentPage('wechat-official-account-writing')"),'agent:wechat-official-account-writing');
  assert.ok(h.run("conversationRowHtml({id:'old',agent_id:'wechat-uuid',agent:{slug:'wechat-official-account-writing',name:'公众号编写智能体'}},'data-agent-conversation')").includes('data-agent-id="wechat-official-account-writing"'));
});

test('R6 R7 registry cards keep mobile and wide responsive layout rules',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8');
  assert.match(css,/\.agent-card\{min-width:0;overflow-wrap:anywhere\}/);
  assert.match(css,/@media\(max-width:860px\)[^\n]*\.agent-grid/);
  assert.match(css,/\.agent-category,\.agent-greeting-category\{[^\n]*overflow-wrap:anywhere/);
  assert.match(css,/\.agent-grid\{display:grid;grid-template-columns:repeat\(3,1fr\)/);
});

test('U4-U6 sidebar uses one shared scale without conversation-route typography overrides',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8');
  assert.match(css,/\.sidebar\{--sidebar-nav-size:14px;/);
  assert.match(css,/\.sidebar \.brand-lockup b\{font-size:22px;font-weight:700;line-height:1\.1\}/);
  assert.match(css,/\.sidebar \.side-nav button\{height:38px;gap:11px;padding:0 10px;font-size:var\(--sidebar-nav-size\);font-weight:500;line-height:var\(--sidebar-nav-leading\)\}/);
  assert.match(css,/\.sidebar \.side-nav button svg\{flex:none;width:var\(--sidebar-icon-size\);height:var\(--sidebar-icon-size\)\}/);
  assert.ok(!css.includes('.app-shell:has(.chatgpt-conversation-layout) .side-nav button{'));
});

test('U7-U8 login remember-me stays a native clickable label with inline checkbox',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8');
  assert.match(source,/<label class="checkbox"><input type="checkbox" checked> 记住我<\/label>/);
  assert.match(css,/\.login-card \.checkbox\{display:flex;align-items:center;gap:8px;/);
  assert.match(css,/\.login-card \.checkbox input\[type="checkbox"\]\{flex:none;width:16px!important;height:16px;margin:0!important;padding:0;/);
});

test('desktop conversation layout keeps navigation and long replies inside stable viewport regions',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8');
  assert.match(css,/\.app-shell:has\(\.chatgpt-conversation-layout\)\{height:100dvh;min-height:0;overflow:hidden\}/);
  assert.match(css,/\.app-shell:has\(\.chatgpt-conversation-layout\) \.sidebar\{min-height:0;overflow:hidden\}/);
  assert.match(css,/\.app-shell:has\(\.chatgpt-conversation-layout\) \.shell-main\{display:grid;grid-template-rows:70px minmax\(0,1fr\);height:100dvh;min-height:0;overflow:hidden\}/);
  assert.match(css,/\.chatgpt-conversation-layout\{height:100%;min-height:0;margin:0;overflow:hidden\}/);
  assert.match(css,/\.chatgpt-conversation-layout \.chat-body\{min-height:0;margin:0 auto;padding:26px 0 32px;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable\}/);
  assert.match(css,/\.chatgpt-conversation-layout \.composer\{position:relative;bottom:auto;flex:0 0 auto;margin:0 auto\}/);
  assert.match(css,/@media\(min-width:861px\) and \(max-height:760px\)/);
});

test('streaming conversation state orders deltas and accepts the authoritative completion',()=>{
  const h=harness('/agents/campaign');
  h.run('streamState=createStreamingState()');
  assert.equal(h.run("applyStreamingEvent(streamState,'progress',{stage:'loading_context'})"),true);
  assert.equal(h.run('streamState.status'),'正在整理相关信息');
  assert.equal(h.run("applyStreamingEvent(streamState,'delta',{sequence:2,text:'正文'})"),false);
  assert.equal(h.run("applyStreamingEvent(streamState,'delta',{sequence:1,text:'# 标题\\n\\n'})"),true);
  assert.equal(h.run('streamState.text'),'# 标题\n\n正文');
  assert.equal(h.run("applyStreamingEvent(streamState,'delta',{sequence:2,text:'重复正文'})"),false);
  assert.equal(h.run('streamState.text'),'# 标题\n\n正文');
  assert.equal(h.run("applyStreamingEvent(streamState,'complete',{final_response:'## 最终正文'})"),true);
  assert.equal(h.run('streamState.finalResponse'),'## 最终正文');
  assert.equal(h.run('streamState.completedAt'),null,'duration ends after the final DOM render');
  assert.ok(h.run("streamingFinalContentHtml(streamState.finalResponse,'原始需求')").includes('<h2>最终正文</h2>'));
  assert.ok(h.run("streamingMessageHtml('正在思考')").includes('data-stream-content'));
  assert.ok(h.run("streamingMessageHtml('正在思考')").includes('data-thinking'));
});

test('safe activity events render only real friendly stages and thinking auto-collapses on the first real delta',()=>{
  const h=harness('/agents/campaign');
  h.run("activityState=createStreamingState();applyStreamingEvent(activityState,'activity',{sequence:1,stage:'asset_retrieving',status:'started',created_at:'2026-09-18T00:00:00Z'});applyStreamingEvent(activityState,'activity',{sequence:2,stage:'asset_retrieving',status:'completed',created_at:'2026-09-18T00:00:01Z'});applyStreamingEvent(activityState,'activity',{sequence:3,stage:'generating',status:'started',created_at:'2026-09-18T00:00:02Z'});");
  assert.equal(h.run('activityState.currentActivity.label'),'正在生成内容');
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(activityState.activities.map(item=>item.label))')),['可用素材已查找','正在生成内容']);
  assert.equal(h.run('activityState.activities[0].status'),'completed');
  assert.equal(h.run("applyStreamingEvent(activityState,'activity',{sequence:4,stage:'unsafe_debug',status:'started'})"),false);
  assert.equal(h.run("applyStreamingEvent(activityState,'delta',{sequence:1,text:'第一段'})"),true);
  assert.ok(h.run('activityState.firstDeltaAt')>0);
  assert.equal(h.run('activityState.completedAt'),null);
  assert.equal(h.run('activityState.activities.length'),2,'delta must not fabricate a generating activity');
  assert.ok(!h.run("streamingThinkingHtml('正在准备')").includes('查看进度'));
  h.run("thinkingTitle={textContent:''};thinkingList={innerHTML:''};thinkingDetails={open:true,querySelector(selector){return selector==='[data-thinking-title]'?thinkingTitle:selector==='[data-thinking-list]'?thinkingList:null;}};thinkingNode={querySelector(selector){return selector==='[data-thinking]'?thinkingDetails:null;}};activityState.conversationStartedAt=Date.now()-2100;renderThinkingSummary(thinkingNode,activityState);thinkingDetails.open=true;renderThinkingSummary(thinkingNode,activityState);");
  assert.match(h.run('thinkingTitle.textContent'),/^正在思考 · [1-9]\d* 秒$/);
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
  assert.ok(h.run("markdownHtml(parts.stable)").includes('<pre><code>const answer = 42;</code></pre>'));
  h.run("openCode=streamingMarkdownParts('```js\\nconst answer = 42;')");
  assert.equal(h.run('openCode.inCode'),true);
  assert.ok(h.run("streamingActiveHtml(openCode.active,'正在思考')").includes('<pre'));
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
  h.run("cancelInput={disabled:true};cancelSubmit={classList:{toggle(){}},setAttribute(){},innerHTML:'',onclick:null};cancelFeedback={textContent:''};cancelMain={querySelector(selector){return selector==='#prompt'?cancelInput:selector==='#composer-submit'?cancelSubmit:selector==='#composer-run-feedback'?cancelFeedback:null;}};cancelCaret={removed:false,remove(){this.removed=true;}};cancelActive={innerHTML:'已有部分回复'};cancelLabel={textContent:''};cancelStatus={hidden:true,classList:{add(){}},querySelector(selector){return selector==='span'?cancelLabel:selector==='svg'||selector==='i'?{remove(){}}:null;}};cancelContent={querySelector(selector){return selector==='[data-stream-active]'?cancelActive:selector==='[data-stream-caret]'?cancelCaret:null;},querySelectorAll(){return [cancelCaret];}};cancelNode={id:'task-status',_streamState:createStreamingState(),removeAttribute(name){if(name==='id'){this.id='';document.taskStatus=null;}},classList:{add(){}},querySelector(selector){return selector==='[data-stream-content]'?cancelContent:selector==='[data-stream-status]'?cancelStatus:null;}};cancelNode._streamState.text='已有部分回复以及未显示字符';cancelNode._streamState.displayText='已有部分回复';cancelNode._streamState.visualPending='以及未显示字符';beginConversationRun(cancelNode._streamState,{taskId:'task-2',agentId:'campaign-agent',phase:ConversationRunPhase.SUBMITTING});beginConversationRun(cancelNode._streamState,{taskId:'task-2',agentId:'campaign-agent',phase:ConversationRunPhase.RUNNING});document.taskStatus=cancelNode;cancelStreamingMessage(cancelNode,cancelNode._streamState,cancelMain);");
  assert.equal(h.run('cancelCaret.removed'),true);
  assert.equal(h.run('cancelLabel.textContent'),'已停止生成 · 部分回复未保存');
  assert.equal(h.run('cancelNode.id'),'');
  assert.equal(h.run('cancelActive.innerHTML'),'已有部分回复');
  assert.equal(h.run('cancelNode._streamState.visualPending'),'');
  assert.equal(h.run('cancelNode._streamState.text'),'已有部分回复');
  assert.equal(h.run('cancelNode._streamState.run.phase'),'IDLE');
  assert.equal(h.run('cancelInput.disabled'),false);
  assert.match(h.run('cancelSubmit.innerHTML'),/data-lucide="send"/);
});

test('generating activity always supersedes the last real tool stage, including refresh recovery',()=>{
  const h=harness('/agents/campaign');
  h.run("timelineState=createStreamingState();setStreamingTaskStartedAt(timelineState,{started_at:'2026-09-18T01:00:00Z'});applyStreamingEvent(timelineState,'activity',{sequence:1,stage:'knowledge_retrieving',status:'started'});applyStreamingEvent(timelineState,'activity',{sequence:2,stage:'knowledge_retrieving',status:'completed'});applyStreamingEvent(timelineState,'activity',{sequence:3,stage:'asset_retrieving',status:'started'});applyStreamingEvent(timelineState,'activity',{sequence:4,stage:'asset_retrieving',status:'completed'});applyStreamingEvent(timelineState,'activity',{sequence:5,stage:'generating',status:'started'});");
  assert.equal(h.run('timelineState.currentActivity.stage'),'generating');
  assert.equal(h.run('timelineState.currentActivity.label'),'正在生成内容');
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(timelineState.activities.map(item=>[item.label,item.status]))')),[['相关知识已查找','completed'],['可用素材已查找','completed'],['正在生成内容','started']]);
  h.run("applyStreamingEvent(timelineState,'activity',{sequence:6,stage:'generating',status:'completed'})");
  assert.equal(h.run('timelineState.currentActivity.label'),'内容已生成');
  assert.equal(h.run('timelineState.activities.at(-1).status'),'completed');
  h.run("recoveredState=createStreamingState();setStreamingTaskStartedAt(recoveredState,{started_at:'2026-09-18T01:00:00Z'});applyStreamingEvent(recoveredState,'activity',{sequence:99,stage:'generating',status:'started'});");
  assert.equal(h.run('recoveredState.currentActivity.label'),'正在生成内容');
  assert.equal(h.run('recoveredState.taskStartedAt'),Date.parse('2026-09-18T01:00:00Z'));
  h.run("duplicateState=createStreamingState();applyStreamingEvent(duplicateState,'activity',{sequence:1,stage:'knowledge_retrieving',status:'started'});applyStreamingEvent(duplicateState,'activity',{sequence:2,stage:'knowledge_retrieving',status:'started'});applyStreamingEvent(duplicateState,'activity',{sequence:3,stage:'knowledge_retrieving',status:'completed'});");
  assert.equal(h.run('duplicateState.activities.length'),1);
  assert.equal(h.run('duplicateState.activities[0].count'),2);
  assert.equal(h.run('duplicateState.currentActivity.sequence'),3);
  assert.equal(h.run('duplicateState.currentActivity.status'),'completed');
});

test('cancel terminal invalidates queued visual work, removes the caret, and leaves generating unfinished',()=>{
  const h=harness('/agents/campaign');
  h.run("frameCallbacks=[];requestAnimationFrame=callback=>{frameCallbacks.push(callback);return frameCallbacks.length};cancelAnimationFrame=()=>{};raceInput={disabled:true};raceSubmit={classList:{toggle(){}},setAttribute(){},innerHTML:'',onclick:null};raceFeedback={textContent:''};raceMain={querySelector(selector){return selector==='#prompt'?raceInput:selector==='#composer-submit'?raceSubmit:selector==='#composer-run-feedback'?raceFeedback:null;}};raceTitle={textContent:''};raceList={innerHTML:'',hidden:false};raceThinkingClass={thinking:true,toggle(name,value){this[name]=value;}};raceThinking={open:true,classList:raceThinkingClass,querySelector(selector){return selector==='[data-thinking-title]'?raceTitle:selector==='[data-thinking-list]'?raceList:null;}};raceCaret={removed:false,remove(){this.removed=true;}};raceActive={innerHTML:'已显示文本'};raceStatusLabel={textContent:''};raceStatus={hidden:true,classList:{add(){}},querySelector(selector){return selector==='span'?raceStatusLabel:selector==='svg'||selector==='i'?{remove(){}}:null;}};raceContent={querySelector(selector){return selector==='[data-stream-stable]'?{innerHTML:''}:selector==='[data-stream-active]'?raceActive:selector==='[data-stream-caret]'?raceCaret:null;},querySelectorAll(){return [raceCaret];}};raceNode={id:'task-status',removeAttribute(name){if(name==='id')this.id='';},classList:{add(){}},querySelector(selector){return selector==='[data-thinking]'?raceThinking:selector==='[data-stream-content]'?raceContent:selector==='[data-stream-status]'?raceStatus:null;}};raceState=createStreamingState();raceState.displayText='已显示文本';raceState.text='已显示文本尚未展示';raceState.visualPending='尚未展示';beginConversationRun(raceState,{taskId:'task-race',agentId:'campaign-agent',phase:ConversationRunPhase.SUBMITTING});beginConversationRun(raceState,{taskId:'task-race',agentId:'campaign-agent',phase:ConversationRunPhase.RUNNING});applyStreamingEvent(raceState,'activity',{sequence:1,stage:'generating',status:'started'});scheduleStreamingRender(raceNode,raceState);capturedGeneration=raceState.visualGeneration;cancelStreamingMessage(raceNode,raceState,raceMain);frameCallbacks[0]();");
  assert.equal(h.run('raceState.visualPending'),'');
  assert.equal(h.run('raceState.text'),'已显示文本');
  assert.equal(h.run('raceState.currentActivity.label'),'正在生成内容');
  assert.equal(h.run('raceState.activities.at(-1).status'),'started');
  assert.equal(h.run('canRenderStreamingVisual(raceState,capturedGeneration)'),false);
  assert.equal(h.run('raceCaret.removed'),true);
  assert.equal(h.run('raceActive.innerHTML'),'已显示文本');
  assert.equal(h.run('raceStatusLabel.textContent'),'已停止生成 · 部分回复未保存');
  assert.equal(h.run('raceThinkingClass["is-thinking"]'),false);
  assert.equal(h.run('raceState.run.phase'),'IDLE');
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

test('streaming follow targets the fixed conversation pane instead of the page root',()=>{
  const h=harness('/agents/campaign');
  const pane={scrollHeight:1400,scrollTop:900,clientHeight:500,listeners:{},addEventListener(name,listener){this.listeners[name]=listener;},removeEventListener(name){delete this.listeners[name];}};
  h.document.scrollingElement={scrollHeight:800,scrollTop:0,clientHeight:800};
  h.document.querySelector=((original)=>selector=>selector==='#chat-body'?pane:original(selector))(h.document.querySelector.bind(h.document));
  h.run('paneState=createStreamingState();watchStreamingFollow(paneState)');
  assert.equal(h.run('paneState.followRoot'),pane);
  pane.scrollHeight=1500;h.run('scrollStreamToBottom(true,paneState,true)');
  assert.equal(pane.scrollTop,1500);
  pane.scrollTop=300;pane.listeners.scroll();
  assert.equal(h.run('paneState.follow'),false);
  h.run('scrollStreamToBottom(paneState.follow,paneState,true)');
  assert.equal(pane.scrollTop,300);
});

test('matching completion removes only the streaming affordances while authoritative completion still wins',()=>{
  const h=harness('/agents/campaign');
  h.run("completionStable={innerHTML:''};completionActive={innerHTML:''};caret={removed:false,remove(){this.removed=true;}};completionContent={querySelector(selector){if(selector==='[data-stream-stable]')return completionStable;if(selector==='[data-stream-active]')return completionActive;if(selector==='[data-stream-caret]')return caret;return null;}};completionStatus={hidden:false,querySelector(){return {textContent:''}}};completionActions={hidden:true,innerHTML:''};completionNode={id:'task-status',innerHTML:'',removeAttribute(name){if(name==='id')this.id='';},classList:{remove(){}},querySelector(selector){if(selector==='[data-stream-content]')return completionContent;if(selector==='[data-stream-actions]')return completionActions;if(selector==='[data-stream-status]')return completionStatus;return null;}};completionState=createStreamingState();completionState.text='同一份最终正文';completionState.finalResponse='同一份最终正文';completeStreamingMessage(completionNode,completionState,'原始需求',document.main)");
  assert.equal(h.run('completionNode.innerHTML'),'');
  assert.equal(h.run('completionNode.id'),'');
  assert.equal(h.run('caret.removed'),true);
  assert.ok(h.run('completionContent.innerHTML').includes('同一份最终正文'));
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

const activityPlanResult={type:'activity_plan',version:'1',data:{
  document_title:'秋季社区活动方案',activity_theme:'秋季社区活动',activity_time:'2026-10-10',activity_location:'社区中心',
  target_audience:'社区居民',promotion_channels:['社区公告'],activity_items:[{phase:'准备',name:'签到',description:'签到接待',image_requirement:'无需'}],
  invitation_copy:'欢迎参加秋季社区活动',pending_items:['场地待确认'],
}};
const documentResponse=(id,filename='秋季社区活动方案.docx')=>({document_id:id,filename,content_type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',download_url:`/api/v1/documents/activity-plan/${id}`});

test('activity plan export is hidden for Markdown-only or malformed results and appears only for a completed validated message',()=>{
  const h=harness('/agents/campaign');
  assert.ok(!h.run("messageHtml({id:'simple',role:'assistant',content:'五个活动标题'},'给我想5个活动标题','campaign-agent')").includes('导出 Word'));
  assert.ok(!h.run("streamingMessageHtml('正在思考')").includes('导出 Word'));
  h.run(`rememberActivityPlanResult('plan-1',${JSON.stringify(activityPlanResult)})`);
  assert.ok(h.run("messageHtml({id:'plan-1',role:'assistant',content:'方案正文'},'完整活动方案','campaign-agent')").includes('导出 Word'));
  assert.ok(!h.run("messageHtml({id:'plan-1',role:'assistant',content:'方案正文'},'完整活动方案','copywriting-agent')").includes('导出 Word'));
  assert.equal(h.run("rememberActivityPlanResult('bad',{type:'activity_plan',version:'1',data:{document_title:'标题'}})"),null);
  assert.equal(h.run("documentActionHtml('bad')"),'');
});

test('activity plan export posts the exact structured data, downloads the returned Chinese filename, and never re-posts a ready message',async()=>{
  const h=harness('/agents/campaign');h.run(`rememberActivityPlanResult('plan-1',${JSON.stringify(activityPlanResult)})`);
  const generating=h.run("handleActivityPlanDocumentAction(document.main,'plan-1')");
  assert.equal(h.run("activityPlanDocuments.get('plan-1').state"),'generating');
  assert.ok(h.run("documentActionHtml('plan-1')").includes('正在生成 Word...'));
  assert.ok(h.run("documentResultHtml('plan-1')").includes('正在生成 Word 文档...'));
  assert.ok(h.run("documentActionHtml('plan-1')").includes('disabled'));
  assert.equal(h.pending.length,1);assert.equal(h.pending[0].options.method,'POST');
  assert.deepEqual(JSON.parse(h.pending[0].options.body).content,activityPlanResult.data);
  h.respond('/api/v1/documents/activity-plan',documentResponse('doc-1'));await generating;
  assert.equal(h.run("activityPlanDocuments.get('plan-1').state"),'ready');
  assert.equal(h.run("documentActionHtml('plan-1')"),'');
  assert.ok(h.run("documentResultHtml('plan-1')").includes('已整理成正式 Word 活动方案'));
  assert.ok(h.run("documentResultHtml('plan-1')").includes('document-download-link'));
  assert.ok(h.run("documentResultHtml('plan-1')").includes('document-file-card'));
  assert.ok(h.run("documentResultHtml('plan-1')").includes('秋季社区活动方案.docx'));
  const download=h.run("handleActivityPlanDocumentAction(document.main,'plan-1')");
  assert.equal(h.pending.at(-1).url,'/api/v1/documents/activity-plan/doc-1');
  assert.equal(h.pending.at(-1).options.credentials,'same-origin');
  h.respondDownload('/api/v1/documents/activity-plan/doc-1');await download;
  assert.deepEqual(h.downloads,[{href:'blob:http://localhost/document-1',filename:'秋季社区活动方案.docx'}]);
  assert.equal(h.pending.filter(request=>request.options.method==='POST').length,1);
});

test('ready document survives same-tab history reload without crossing user identity',async()=>{
  const h=harness('/agents/campaign');h.run(`rememberActivityPlanResult('plan-1',${JSON.stringify(activityPlanResult)})`);
  const generated=h.run("handleActivityPlanDocumentAction(document.main,'plan-1')");h.respond('/api/v1/documents/activity-plan',documentResponse('doc-saved'));await generated;
  assert.equal(h.sessionStorage.length,1);
  h.run(`activityPlanDocuments.clear();rememberActivityPlanResult('plan-1',${JSON.stringify(activityPlanResult)})`);
  assert.equal(h.run("activityPlanDocuments.get('plan-1').state"),'ready');
  assert.equal(h.run("activityPlanDocuments.get('plan-1').document.document_id"),'doc-saved');
  h.run(`me.user_id='other-user';activityPlanDocuments.clear();rememberActivityPlanResult('plan-1',${JSON.stringify(activityPlanResult)})`);
  assert.equal(h.run("activityPlanDocuments.get('plan-1').state"),'idle');
  h.run('clearActivityPlanSessionDocuments()');assert.equal(h.sessionStorage.length,0);
});

test('activity plan export errors stay local to a message and can be retried',async()=>{
  const h=harness('/agents/campaign');h.run(`rememberActivityPlanResult('plan-1',${JSON.stringify(activityPlanResult)})`);
  const failed=h.run("handleActivityPlanDocumentAction(document.main,'plan-1')");h.respondError('/api/v1/documents/activity-plan',500);await failed;
  assert.equal(h.run("activityPlanDocuments.get('plan-1').state"),'error');
  assert.equal(h.run("activityPlanDocuments.get('plan-1').error"),'Word 生成失败，请重试');
  assert.ok(h.run("documentActionHtml('plan-1')").includes('重试导出 Word'));
  assert.ok(h.run("documentResultHtml('plan-1')").includes('Word 生成失败，请重试'));
  const retried=h.run("handleActivityPlanDocumentAction(document.main,'plan-1')");h.respond('/api/v1/documents/activity-plan',documentResponse('doc-retry'));await retried;
  assert.equal(h.run("activityPlanDocuments.get('plan-1').state"),'ready');
  assert.equal(h.run("activityPlanDocuments.get('plan-1').error"),'');
  for(const [status,message] of [[400,'方案数据无效'],[401,'请先登录'],[403,'没有权限'],[404,'文档不存在'],[413,'方案内容过大']]){
    assert.ok(h.run(`documentExportError({status:${status}})`).includes(message));
  }
  assert.equal(h.run('documentExportError(new TypeError())'),'网络异常，请稍后重试');
  const network=harness('/agents/campaign');network.run(`rememberActivityPlanResult('plan-network',${JSON.stringify(activityPlanResult)})`);
  const failedNetwork=network.run("handleActivityPlanDocumentAction(document.main,'plan-network')");network.pending[0].done=true;network.pending[0].reject(new TypeError('offline'));await failedNetwork;
  assert.equal(network.run("activityPlanDocuments.get('plan-network').state"),'error');
  assert.equal(network.run("activityPlanDocuments.get('plan-network').error"),'网络异常，请稍后重试');
  const retriedNetwork=network.run("handleActivityPlanDocumentAction(document.main,'plan-network')");network.respond('/api/v1/documents/activity-plan',documentResponse('doc-network'));await retriedNetwork;
  assert.equal(network.run("activityPlanDocuments.get('plan-network').state"),'ready');
  const stale=network.run("handleActivityPlanDocumentAction(document.main,'plan-network')");network.respondError('/api/v1/documents/activity-plan/doc-network',404);await stale;
  assert.equal(network.run("activityPlanDocuments.get('plan-network').state"),'error');
  assert.equal(network.sessionStorage.length,0);
});

test('two activity-plan assistant messages own independent document state and payloads',async()=>{
  const h=harness('/agents/campaign'),second=structuredClone(activityPlanResult);second.data.activity_theme='冬季客户答谢';
  h.run(`rememberActivityPlanResult('plan-a',${JSON.stringify(activityPlanResult)})`);
  h.run(`rememberActivityPlanResult('plan-b',${JSON.stringify(second)})`);
  const first=h.run("handleActivityPlanDocumentAction(document.main,'plan-a')");
  const other=h.run("handleActivityPlanDocumentAction(document.main,'plan-b')");
  assert.equal(h.pending.length,2);
  assert.equal(JSON.parse(h.pending[0].options.body).content.activity_theme,'秋季社区活动');
  assert.equal(JSON.parse(h.pending[1].options.body).content.activity_theme,'冬季客户答谢');
  h.pending[1].done=true;h.pending[1].resolve(documentResponse('doc-b','冬季客户答谢.docx'));await other;
  assert.equal(h.run("activityPlanDocuments.get('plan-a').state"),'generating');
  assert.equal(h.run("activityPlanDocuments.get('plan-b').document.document_id"),'doc-b');
  h.pending[0].done=true;h.pending[0].resolve(documentResponse('doc-a'));await first;
  assert.equal(h.run("activityPlanDocuments.get('plan-a').document.document_id"),'doc-a');
});

test('SSE completion binds export to the matching assistant message; history restoration verifies task association',async()=>{
  const h=harness('/agents/campaign');
  h.run('completionNode={innerHTML:"",dataset:{},removeAttribute(){},classList:{remove(){}},querySelector(){return null}};completionState=createStreamingState()');
  h.run(`finishStreamTask(completionNode,completionState,{status:'completed',assistant_message_id:'task:t1:assistant',structured_result:${JSON.stringify(activityPlanResult)},final_response:'完整活动方案'},'campaign-agent',document.main,'完整活动方案')`);
  assert.ok(h.run('completionNode.innerHTML').includes('导出 Word'));
  assert.equal(h.run('completionNode.dataset.assistantMessageId'),'task:t1:assistant');
  assert.equal(h.run("activityPlanDocuments.get('task:t1:assistant').state"),'idle');
  h.run("hydrateHistoryActivityPlans(document.main,[{id:'task:t2:assistant',task_id:'t2',role:'assistant'}],'campaign-agent')");
  assert.equal(h.pending.at(-1).url,'/api/v1/tasks/t2');
  h.respond('/api/v1/tasks/t2',{status:'completed',assistant_message_id:'task:t2:assistant',structured_result:activityPlanResult});await flush();
  assert.ok(h.run("documentActionHtml('task:t2:assistant')").includes('导出 Word'));
  h.run("hydrateHistoryActivityPlans(document.main,[{id:'task:t3:assistant',task_id:'t3',role:'assistant'}],'campaign-agent')");
  h.respond('/api/v1/tasks/t3',{status:'completed',assistant_message_id:'other-message',structured_result:activityPlanResult});await flush();
  assert.equal(h.run("documentActionHtml('task:t3:assistant')"),'');
});

test('activity plan export actions remain wrapping and touch-sized on mobile',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8');
  assert.match(css,/\.chat-message \.message-actions\{flex-wrap:wrap;min-width:0\}/);
  assert.match(css,/@media\(max-width:860px\)\{\.chat-message \.message-actions \.button\{min-height:44px\}/);
  assert.match(css,/\.document-file-details\{[^}]*min-width:0/);
  assert.match(css,/\.document-file-details strong\{[^}]*overflow-wrap:anywhere/);
  assert.match(css,/\.document-download-link\{[^}]*min-height:44px/);
  assert.match(css,/\.chatgpt-conversation-layout \.chat-message,\.chatgpt-conversation-layout \.chat-message-content-markdown\{min-width:0\}/);
});

test('business progress labels follow real stages without inventing semantic validation',()=>{
  const h=harness('/agents/campaign');
  h.run("simpleProgress=createStreamingState();applyStreamingEvent(simpleProgress,'progress',{stage:'starting_runtime'});applyStreamingEvent(simpleProgress,'activity',{sequence:1,stage:'generating',status:'started'});applyStreamingEvent(simpleProgress,'activity',{sequence:2,stage:'generating',status:'completed'});");
  assert.equal(h.run('simpleProgress.status'),'内容已生成');
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(simpleProgress.activities.map(x=>x.label))')),['内容已生成']);
  assert.ok(!h.run('JSON.stringify(simpleProgress.activities)').includes('活动方案'));
  assert.ok(!h.run('JSON.stringify(simpleProgress.activities)').includes('校验'));
  assert.equal(h.run("activityDisplayLabel('generating','started','full')"),'正在生成活动方案');
  assert.equal(h.run("activityDisplayLabel('generating','completed','full')"),'活动方案已生成');
  assert.equal(h.run("taskStageLabel('persisting_result')"),'正在整理最终结果');
});

test('activity-plan Markdown keeps headings, lists, and escaped-pipe tables readable',()=>{
  const h=harness('/agents/campaign');
  const markdown='# 活动方案\n\n- 宣发渠道\n\n| 环节 | 说明 |\n| --- | --- |\n| 签到 | 执行\\|接待<br>待确认 |\n\n结束';
  const html=h.run(`markdownHtml(${JSON.stringify(markdown)})`);
  assert.match(html,/<h1>活动方案<\/h1>/);
  assert.match(html,/<li>宣发渠道<\/li>/);
  assert.match(html,/<table>/);
  assert.match(html,/<td>执行\|接待<br>待确认<\/td>/);
  assert.ok(!html.includes('&lt;br&gt;'));
  assert.ok(h.run(`markdownHtml(${JSON.stringify('| a | b |\n| --- | --- |\n| <script> | safe |')})`).includes('&lt;script&gt;'));
  h.run(`finalTableState=createStreamingState();finalTableState.text=${JSON.stringify(markdown)};finalTableState.finalResponse=finalTableState.text;finalTableContent={innerHTML:'',querySelector(){return null}};finalTableActions={hidden:true,innerHTML:''};finalTableNode={removeAttribute(){},classList:{remove(){}},querySelector(selector){return selector==='[data-stream-content]'?finalTableContent:selector==='[data-stream-actions]'?finalTableActions:null}};completeStreamingMessage(finalTableNode,finalTableState,'需求',document.main)`);
  assert.match(h.run('finalTableContent.innerHTML'),/<table>/);
  assert.match(h.run('finalTableContent.innerHTML'),/<li>宣发渠道<\/li>/);
});

test('thinking duration measures send to complete, not first delta, with minute formatting',()=>{
  const h=harness('/agents/campaign');
  assert.equal(h.run('formatThinkingDuration(42300)'),'42 秒');
  assert.equal(h.run('formatThinkingDuration(88400)'),'1 分 28 秒');
  assert.equal(h.run('formatThinkingDuration(700)'),'不足 1 秒');
  h.run("durationState=createStreamingState();durationState.conversationStartedAt=0;applyStreamingEvent(durationState,'delta',{sequence:1,text:'第一段'});durationState.completedAt=42300;");
  assert.equal(h.run('durationState.completed'),false);
  assert.equal(h.run('thinkingDuration(durationState)'),'42 秒');
  h.run("durationState.completedAt=88400;durationState.completed=true");
  assert.equal(h.run('thinkingDuration(durationState)'),'1 分 28 秒');
  h.run("durationTitle={textContent:''};durationList={innerHTML:'',hidden:false};durationDetails={open:true,classList:{toggle(){}},querySelector(selector){return selector==='[data-thinking-title]'?durationTitle:selector==='[data-thinking-list]'?durationList:null;}};durationNode={querySelector(selector){return selector==='[data-thinking]'?durationDetails:null;}};renderThinkingSummary(durationNode,durationState)");
  assert.equal(h.run('durationTitle.textContent'),'思考了 1 分 28 秒');
  assert.equal(h.run('durationDetails.open'),false);
  h.run('durationDetails.open=true;renderThinkingSummary(durationNode,durationState)');
  assert.equal(h.run('durationDetails.open'),true);
});

test('ready document result is message-scoped, hides internal IDs, and exposes two GET-only entry points',()=>{
  const h=harness('/agents/campaign');
  h.run(`rememberActivityPlanResult('message-one',${JSON.stringify(activityPlanResult)});activityPlanDocuments.get('message-one').state='ready';activityPlanDocuments.get('message-one').document=${JSON.stringify(documentResponse('private-doc-1'))}`);
  const block=h.run("documentResultHtml('message-one')");
  assert.match(block,/Word 文档 · DOCX/);
  assert.match(block,/下载《秋季社区活动方案》/);
  assert.equal((block.match(/data-document-download=/g)||[]).length,2);
  assert.ok(!block.includes('document_id'));
  assert.ok(!block.includes('storage_key'));
  assert.ok(h.run("messageHtml({id:'message-one',role:'assistant',content:'正文'},'方案','campaign-agent')").includes('document-file-card'));
});

test('validated full-plan completion relabels only its real generating activity',()=>{
  const h=harness('/agents/campaign');
  h.run("fullState=createStreamingState();applyStreamingEvent(fullState,'activity',{sequence:1,stage:'generating',status:'started'});applyStreamingEvent(fullState,'activity',{sequence:2,stage:'generating',status:'completed'});fullNode={innerHTML:'',dataset:{},removeAttribute(){},classList:{remove(){}},querySelector(){return null}};");
  h.run(`finishStreamTask(fullNode,fullState,{status:'completed',assistant_message_id:'full-message',structured_result:${JSON.stringify(activityPlanResult)},final_response:'活动方案'},'campaign-agent',document.main,'完整活动方案')`);
  assert.equal(h.run('fullState.planMode'),'full');
  assert.equal(h.run('fullState.activities[0].label'),'活动方案已生成');
  assert.ok(h.run('fullState.completedAt')>0);
  assert.equal(h.run('fullState.activities.length'),1);
  assert.ok(h.run('fullNode.innerHTML').includes('导出 Word'));
  assert.ok(!h.run('fullNode.innerHTML').includes('正在校验方案内容'));
});

test('full-plan thinking uses only the real ordered validation and conditional correction activities',()=>{
  const h=harness('/agents/campaign');
  h.run("planStages=createStreamingState();for(const [stage,status] of [['full_plan_generating','started'],['full_plan_generating','completed'],['structured_validating','started'],['structured_validating','completed'],['semantic_validating','started'],['semantic_validating','completed'],['result_rendering','started'],['result_rendering','completed']])applyStreamingEvent(planStages,'activity',{sequence:planStages.activitySequence+1,stage,status});");
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(planStages.activities.map(item=>item.label))')),['活动方案已生成','方案结构已校验','方案内容已校验','最终结果已整理']);
  assert.ok(!h.run('JSON.stringify(planStages.activities)').includes('待确认内容'));
  h.run("correctionStages=createStreamingState();for(const stage of ['full_plan_generating','structured_validating','semantic_validating','semantic_correcting','structured_validating','semantic_validating','result_rendering']){applyStreamingEvent(correctionStages,'activity',{sequence:correctionStages.activitySequence+1,stage,status:'started'});applyStreamingEvent(correctionStages,'activity',{sequence:correctionStages.activitySequence+1,stage,status:'completed'});}");
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(correctionStages.activities.map(item=>item.stage))')),['full_plan_generating','structured_validating','semantic_validating','semantic_correcting','structured_validating','semantic_validating','result_rendering']);
  assert.equal(h.run("activityDisplayLabel('semantic_correcting','started')"),'正在优化待确认内容');
  assert.equal(h.run("activityDisplayLabel('result_rendering','started')"),'正在整理最终结果');
  assert.equal(h.run("activityDisplayLabel('generating','started')"),'正在生成内容');
  assert.equal(h.run("applyStreamingEvent(correctionStages,'activity',{sequence:99,stage:'invented_stage',status:'started'})"),false);
});

test('network completion drains only received delta text across frames before visual completion',()=>{
  const h=harness('/agents/campaign');
  h.run("drainNode={innerHTML:'',dataset:{},removeAttribute(){},classList:{remove(){}},querySelector(){return null}};drainState=createStreamingState();drainState.conversationStartedAt=Date.now()-2000;for(let sequence=1;sequence<=12;sequence++)applyStreamingEvent(drainState,'delta',{sequence,text:'真实内容'.repeat(20)});drainState.lastRenderAt=Date.now();scheduleStreamingRender(drainNode,drainState);finishStreamTask(drainNode,drainState,{status:'completed',final_response:drainState.text},'campaign-agent',document.main,'需求');");
  assert.equal(h.run('drainState.networkComplete'),true);
  assert.equal(h.run('drainState.completed'),false);
  assert.equal(h.run('drainState.completedAt'),null);
  assert.equal(h.run('drainState.displayText.length'),0);
  assert.equal(h.run('drainState.run.terminal'),null);
  assert.equal(h.run("applyStreamingEvent(drainState,'delta',{sequence:13,text:'伪造追加'})"),false);
  const visible=[];
  for(let frame=0;frame<100&&!h.run('drainState.completed');frame++){h.runTimers();visible.push(h.run('drainState.displayText.length'));}
  assert.ok(visible.filter((value,index)=>value>(visible[index-1]||0)).length>1);
  assert.equal(h.run('drainState.completed'),true);
  assert.ok(h.run('drainState.completedAt')>=h.run('drainState.networkCompletedAt'));
  assert.equal(h.run('drainState.run.phase'),'IDLE');
  assert.equal(h.run('drainState.displayText'),h.run('drainState.text'));
});

test('brand Logo missing, existing, permission and tenant-safe card states',()=>{
  const h=harness('/enterprise-config');
  h.run("brandState={logo:null,phase:'idle',error:'',previewVersion:1,canEdit:true}");
  const missing=h.run('brandLogoCardHtml(brandState)');
  assert.match(missing,/尚未上传品牌 Logo/);
  assert.match(missing,/当前 Word 导出仍可使用/);
  assert.ok(!missing.includes('Word 暂无法生成'));
  assert.match(missing,/仅支持透明背景 PNG/);
  assert.match(missing,/512–2048px/);
  assert.match(missing,/最大 5MB/);
  assert.match(missing,/最大 4096 × 4096/);
  assert.match(missing,/accept="image\/png,\.png"/);
  assert.match(missing,/上传 Logo/);
  h.run("brandState.logo={filename:'brand.png',width:1024,height:512,content_type:'image/png',download_url:'/api/v1/enterprise-config/brand-logo',asset_id:'private-id',storage_key:'private-path'}");
  const existing=h.run('brandLogoCardHtml(brandState)');
  assert.match(existing,/更换 Logo/);assert.match(existing,/1024 × 512px/);
  assert.match(existing,/当前企业品牌 Logo/);
  assert.ok(!existing.includes('private-id')&&!existing.includes('private-path'));
  h.run('brandState.canEdit=false');
  assert.ok(!h.run('brandLogoCardHtml(brandState)').includes('brand-logo-upload'));
});

test('brand Logo error codes provide precise safe guidance, including opaque and fake PNG',()=>{
  const h=harness('/enterprise-config');
  for(const [code,expected] of [['LOGO_TRANSPARENCY_REQUIRED','没有透明背景'],['LOGO_FORMAT_NOT_SUPPORTED','仅支持 PNG'],['LOGO_INVALID_PNG','图片文件无效'],['LOGO_TOO_LARGE','不能超过 5MB'],['LOGO_DIMENSIONS_TOO_LARGE','4096 × 4096'],['LOGO_PERMISSION_DENIED','无权修改'],['LOGO_STORAGE_FAILED','保存失败']]){
    assert.ok(h.run(`brandLogoErrorMessage({error_code:'INVALID_INPUT',detail:'${code}'})`).includes(expected));
  }
  assert.equal(h.run("brandLogoErrorMessage({detail:'traceback /secret/path'})"),'Logo 上传失败，请稍后重试');
});

test('brand Logo upload waits for formal config refresh and replaces preview only after success',async()=>{
  const h=harness('/enterprise-config');
  h.run("logoCard={innerHTML:'',querySelector(){return null}};logoMain={querySelector(selector){return selector==='#brand-logo-card'?logoCard:null}};logoFile={name:'transparent.png'};logoState={logo:null,phase:'idle',error:'',previewVersion:0,canEdit:true};logoUpload=uploadBrandLogo(logoMain,logoState,logoFile)");
  assert.equal(h.run('logoState.phase'),'uploading');
  assert.match(h.run('logoCard.innerHTML'),/正在上传\.\.\./);
  assert.match(h.run('logoCard.innerHTML'),/disabled aria-busy="true"/);
  assert.equal(h.pending[0].url,'/api/v1/enterprise-config/brand-logo');
  assert.equal(h.pending[0].options.method,'POST');
  assert.equal(h.pending[0].options.body.items[0].name,'file');
  h.respond('/api/v1/enterprise-config/brand-logo',{brand_logo:{filename:'transparent.png'}});await flush();
  assert.equal(h.run('logoState.phase'),'uploading','POST metadata alone must not become the saved preview');
  assert.equal(h.pending[1].url,'/api/v1/enterprise-config');
  h.respond('/api/v1/enterprise-config',{brand_logo:{filename:'saved.png',width:512,height:512,content_type:'image/png',download_url:'/api/v1/enterprise-config/brand-logo'}});
  await h.run('logoUpload');
  assert.equal(h.run('logoState.phase'),'success');
  assert.equal(h.run('logoState.logo.filename'),'saved.png');
  assert.match(h.run('logoCard.innerHTML'),/上传成功/);
  assert.match(h.run('logoCard.innerHTML'),/更换 Logo/);
});

test('brand Logo replacement failure retains the old preview and permits retry',async()=>{
  const h=harness('/enterprise-config');
  h.run("oldLogo={filename:'old.png',width:512,height:512,content_type:'image/png',download_url:'/api/v1/enterprise-config/brand-logo'};logoCard={innerHTML:'',querySelector(){return null}};logoMain={querySelector(selector){return selector==='#brand-logo-card'?logoCard:null}};logoState={logo:oldLogo,phase:'idle',error:'',previewVersion:1,canEdit:true};logoUpload=uploadBrandLogo(logoMain,logoState,{name:'opaque.png'})");
  h.pending[0].done=true;h.pending[0].resolveResponse({ok:false,status:422,json:async()=>({error_code:'INVALID_INPUT',detail:'LOGO_TRANSPARENCY_REQUIRED'})});await h.run('logoUpload');
  assert.equal(h.run('logoState.phase'),'error');
  assert.equal(h.run('logoState.logo.filename'),'old.png');
  assert.match(h.run('logoCard.innerHTML'),/没有透明背景/);
  assert.match(h.run('logoCard.innerHTML'),/更换 Logo/);
  const denied=h.run("logoState.canEdit=false;uploadBrandLogo(logoMain,logoState,{name:'blocked.png'})");await denied;
  assert.equal(h.pending.length,1,'read-only state must not issue another POST');
});

test('brand Logo mobile card scales within 375px and has a touch-sized button',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../app/static/workbench.css'),'utf8');
  assert.match(css,/\.brand-logo-preview\{[^}]*width:min\(100%,340px\)/);
  assert.match(css,/\.brand-logo-preview img\{[^}]*max-width:100%/);
  assert.match(css,/\.brand-logo-upload-button\{min-height:44px\}/);
  assert.match(css,/@media\(max-width:860px\)\{\.brand-logo-preview\{width:100%\}/);
});
