// FIXTURE_ONLY isolated HTTP + real headless Chrome, never public credentials.
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),cp=require('node:child_process'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../app/static'),out=process.env.PREVIEW_PERF_OUT;
assert.ok(out);fs.mkdirSync(out,{recursive:true});
const baseline=cp.execFileSync('git',['show','2f77e48d5cc0c8c226d2d95e1e00958a2af0da39:enterprise_agent_poc/app/static/workbench.js'],{encoding:'utf8',maxBuffer:2e6});
let version='before',principal='a',historyDelay=300,agentEnabled=true,running=false;
const requests=[],errors=[];
const ids=['copywriting-agent','campaign-agent','image-agent','preview-wechat-agent'];
const agent=id=>({id,slug:id==='preview-wechat-agent'?'wechat-official-account-writing':id,name:'FIXTURE '+id,description:'独立本地验收',enabled:agentEnabled,icon:'bot'});
const conversations=()=>ids.map((id,i)=>({id:'fixture-'+id,agent_id:id,title:'FIXTURE 历史'+i,created_at:'2026-10-20T01:00:00Z',latest_status:running&&i===0?'running':'completed',project:{name:'FIXTURE项目',type:'创作项目'}}));
const runningTask=()=>({id:'fixture-running',agent_id:'copywriting-agent',conversation_id:'fixture-copywriting-agent',tenant_id:'preview-fixture-'+principal,user_id:'fixture-'+principal,status:'running',stage:'generating',input_text:'FIXTURE 原任务',created_at:'2026-10-20T01:00:00Z',started_at:'2026-10-20T01:00:01Z'});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const server=http.createServer(async(req,res)=>{
 const u=new URL(req.url,'http://localhost'),p=u.pathname;requests.push({path:p,method:req.method,principal});
 const json=(value,status=200)=>{res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify(value));};
 if(req.method!=='GET')return json({error_code:'FIXTURE_MUTATION_FORBIDDEN'},403);
 if(p.startsWith('/preview/static/')){
  const name=path.basename(p);if(name==='workbench.js'&&version==='before'){res.setHeader('content-type','application/javascript');return res.end(baseline);}
 const file=path.join(root,name);if(!fs.existsSync(file)){res.writeHead(404);return res.end();}
  res.setHeader('content-type',name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'application/octet-stream');return res.end(fs.readFileSync(file));
 }
 if(p==='/preview/api/health')return json({status:'preview',image_business:{enabled:true,max_reference_images:5}});
 if(p==='/preview/api/v1/me')return json({user_id:'fixture-'+principal,tenant_id:'preview-fixture-'+principal,tenant_name:'FIXTURE',display_name:'本地测试',role:'enterprise_admin',is_platform_admin:false});
 if(p==='/preview/api/v1/workspace'){await sleep(80);return json({agents:ids.map(agent),credit_balance:100000,tenant_name:'FIXTURE',brand_name:'FIXTURE'});}
 if(p==='/preview/api/v1/enterprise-config')return json({brand_name:'FIXTURE',brand_logo:null});
 if(p==='/preview/api/v1/conversations'){await sleep(historyDelay);return json(conversations());}
 if(p.startsWith('/preview/api/v1/conversations/')){
  const id=decodeURIComponent(p.split('/').at(-1)),a=ids.find(x=>id==='fixture-'+x)||'copywriting-agent';await sleep(90);
  return json({id,agent_id:a,agent:agent(a),project:{name:'FIXTURE原会话'},messages:[{id:'message-'+a,role:'assistant',content:'FIXTURE 已保存内容'}],tasks:running&&a==='copywriting-agent'?[runningTask()]:[],generations:[]});
 }
 if(p.startsWith('/preview/api/v1/agents/')){await sleep(60);return agentEnabled?json(agent(p.split('/').at(-1))):json({error_code:'AGENT_UNAVAILABLE'},404);}
 if(p==='/preview/api/v1/tasks/orphans')return json({tasks:[],next_cursor:null});
 if(p==='/preview/api/v1/tasks/fixture-running')return json(runningTask());
 if(p==='/preview/api/v1/tasks/fixture-running/events'){res.writeHead(200,{'content-type':'text/event-stream'});return res.end('event: activity\ndata: {"stage":"generating","status":"running"}\n\n');}
 if(p.startsWith('/preview/api/'))return json([]);
 if(p.startsWith('/preview/')){
  let html=fs.readFileSync(path.join(root,'index.html'),'utf8').replaceAll('/static/','/preview/static/');
  html=html.replace('<head>','<head><script src="/preview/static/customer-preview.js"></script><script src="/preview/static/preview-wechat-style.js"></script>');
  res.setHeader('content-type','text/html');return res.end(html);
 }
 res.writeHead(404);res.end();
});
const percentile=(a,p)=>[...a].sort((x,y)=>x-y)[Math.ceil(a.length*p)-1];
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port+'/preview';
 const browser=await chromium.launch({executablePath:'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',headless:true});
 const report={mode:'FIXTURE_ONLY_LOCAL_HTTP_REAL_BROWSER',network_delays_ms:{workspace:80,agent:60,history:300,detail:90},samples:[],real_provider_calls:0,public_performance:'NOT_VERIFIED'};
 try{
  for(version of ['before','after']){
   principal='a';historyDelay=300;agentEnabled=true;
   const context=await browser.newContext({viewport:{width:1440,height:1000},permissions:['clipboard-read','clipboard-write']}),page=await context.newPage();page.setDefaultTimeout(12000);
   page.on('pageerror',e=>errors.push(e.message));
   await page.goto(base+'/agents/copywriting');await page.locator('#composer-submit').waitFor();
   for(const target of ['campaign','image','copywriting','agent:preview-wechat-agent','campaign','image','copywriting','agent:preview-wechat-agent','campaign','image','copywriting','agent:preview-wechat-agent']){
    const start=await page.evaluate(()=>performance.now());await page.locator('button[data-page="'+target+'"]:visible').first().click();
    await page.waitForFunction(target=>document.querySelector('#main')?.dataset.page===target&&document.querySelector('#prompt')&&!document.querySelector('#prompt').disabled,target);
    const input=await page.evaluate(()=>performance.now());await page.locator('#composer-submit').waitFor();
    if(version==='after')await page.waitForFunction(()=>document.querySelector('#main').dataset.historyReady==='true');
    const end=await page.evaluate(()=>performance.now());
    report.samples.push({version,target,input_ms:Math.round(input-start),history_ms:Math.round(end-start),timings:await page.evaluate(()=>document.querySelector('#main')._previewTimings||null)});
   }
   for(const [name,p] of [['p50',.50],['p95',.95]])report[version+'_'+name+'_ms']=percentile(report.samples.filter(s=>s.version===version).map(s=>s.input_ms),p);
   await page.screenshot({path:path.join(out,version+'-desktop.png')});
   if(version==='after'){
    // New project and a known conversation must not await the history list.
    historyDelay=900;await page.evaluate(()=>{pageCache.clear();void selectAgentConversation('copywriting-agent',null,{newProject:true});});
    await page.locator('#composer-submit').waitFor();assert.notEqual(await page.locator('#main').getAttribute('data-history-ready'),'true',JSON.stringify(await page.evaluate(()=>({timings:document.querySelector('#main')._previewTimings,state:history.state,route:document.querySelector('#main').dataset.page}))));
    await page.locator('#prompt').fill('FIXTURE 正在输入，不应因历史加载丢失');await page.waitForFunction(()=>document.querySelector('#main').dataset.historyReady==='true');assert.equal(await page.locator('#prompt').inputValue(),'FIXTURE 正在输入，不应因历史加载丢失');
    await page.evaluate(()=>{pageCache.clear();void selectAgentConversation('copywriting-agent','fixture-copywriting-agent');});await page.locator('#composer-submit').waitFor();assert.ok(await page.getByText('FIXTURE 已保存内容',{exact:true}).count());
    // Out-of-order route hydration cannot replace the current route/draft.
    await page.evaluate(()=>{pageCache.clear();void navigate('campaign');void navigate('image');});await page.waitForFunction(()=>document.querySelector('#main').dataset.page==='image'&&!!document.querySelector('#composer-submit'));await sleep(1000);
    assert.equal(await page.locator('#main').getAttribute('data-page'),'image');
    agentEnabled=false;await page.evaluate(()=>{void navigate('campaign',{newProject:true,conversationId:null});});await page.getByText('该智能体暂未启用。',{exact:true}).waitFor();assert.equal(await page.locator('#composer-submit').count(),0);agentEnabled=true;
    running=true;await page.evaluate(()=>{pageCache.clear();void selectAgentConversation('copywriting-agent','fixture-copywriting-agent');});await page.locator('#task-status').waitFor();
    await page.evaluate(()=>{void navigate('campaign',{newProject:true,conversationId:null});});await page.locator('#composer-submit').waitFor();
    await page.evaluate(()=>{void selectAgentConversation('copywriting-agent','fixture-copywriting-agent');});await page.locator('#task-status').waitFor();
    assert.equal(runningTask().status,'running');report.running_task_navigation='PASS_FIXTURE_GET_RESTORE_NO_CANCEL_OR_RUN';running=false;
    // A Principal change clears scoped pending reads and cached histories.
    principal='b';await page.evaluate(()=>boot());await page.waitForFunction(()=>me?.tenant_id==='preview-fixture-b'&&!!document.querySelector('#composer-submit'));
    assert.equal(await page.evaluate(()=>[...pageCache.values()].every(x=>x.scope===principalCacheScope())),true);
    // Exercise actual safe render/export and native rendered-selection copy.
    await page.evaluate(()=>{void navigate('agent:preview-wechat-agent',{newProject:true,conversationId:null});});await page.locator('#composer-submit').waitFor();
    const emphasis=await page.evaluate(()=>{
     const result=WorkbenchWechatArticle.buildManualExport('# 正式标题\n\n普通正文，**重要结论**。\n\n## 章节\n\n其他事实不变。',markdownHtml,{});
     const frame=document.createElement('iframe');frame.id='fixture-article';frame.className='wechat-inline-article';frame.style.cssText='width:100%;height:520px;border:0';frame.setAttribute('sandbox','allow-same-origin');frame.srcdoc=WorkbenchWechatArticle.previewDocument(result.html,[],true);document.querySelector('#chat-body').replaceChildren(frame);window.fixtureExport=result;return result.html;
    });assert.match(emphasis,/color:\s*#FFCA00|color:\s*rgb\(255, 202, 0\)/i);
    const frame=page.frameLocator('#fixture-article');await frame.locator('strong').waitFor();assert.equal(await frame.locator('strong').evaluate(n=>getComputedStyle(n).color),'rgb(255, 202, 0)');assert.equal(await frame.locator('p').first().evaluate(n=>getComputedStyle(n).color),'rgb(34, 34, 34)');
    await page.evaluate(async()=>{const f=document.querySelector('#fixture-article');f.dataset.articleReady='true';await WorkbenchWechatArticle.copyRenderedArticle(f);});
    const clipboard=await page.evaluate(async()=>{const items=await navigator.clipboard.read();for(const item of items)if(item.types.includes('text/html'))return await (await item.getType('text/html')).text();return '';});assert.match(clipboard,/255, 202, 0|#FFCA00/i);
    report.richtext_rendered_copy='PASS_FIXTURE_REAL_CLIPBOARD';report.highlight='PASS_FIXTURE';report.scope_and_stale_route='PASS_FIXTURE';report.draft_preserved='PASS_FIXTURE';
    await page.screenshot({path:path.join(out,'after-highlight-desktop.png')});await page.setViewportSize({width:375,height:850});await page.screenshot({path:path.join(out,'after-highlight-mobile.png')});
   }
   await context.close();
  }
  assert.ok(report.after_p50_ms<report.before_p50_ms);assert.ok(report.after_p95_ms<report.before_p95_ms);
  assert.deepEqual(errors,[]);assert.equal(requests.filter(r=>r.method!=='GET').length,0);
  report.requests=requests.length;report.mutations=0;report.errors=errors;fs.writeFileSync(path.join(out,'performance.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close();server.closeAllConnections();server.close();}
})().catch(e=>{console.error(e);server.closeAllConnections();server.close();process.exitCode=1;});
