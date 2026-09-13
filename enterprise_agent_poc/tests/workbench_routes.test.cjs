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
    querySelector(selector){if(selector === '#main')return this.main; return {};},
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

test('Stage 1 Agent management direct/refresh prefers pathname over old profile state',async()=>{
  const h=harness('/platform/agents',{page:'profile'});
  assert.equal(h.run('pageFromNavigation()'),'platform-agents');
  await Promise.all([h.run('render(pageFromNavigation())'),h.settle()]);
  h.consistent('platform-agents','Agent 管理');
  assert.ok(h.document.main.innerHTML.includes('Runtime Test Pending'));
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
