// Actual frontend + unchanged document API/auth + fresh local DB/object store.
// Agent/Task/history are explicit Fixtures; no Provider or customer E2E claim.
const assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),readline=require('node:readline'),{chromium}=require('playwright');
const staticDir=path.resolve(__dirname,'../app/static'),content=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/activity_plan_d1.json'),'utf8'));
const structured={type:'activity_plan',version:'1',data:content},agent={id:'campaign-agent',slug:'campaign',name:'活动策划智能体',enabled:true,description:'Fixture metadata only',icon:'calendar-days',category:'Fixture'};
async function run(){
  assert.ok(process.env.UI_FIXTURE_PYTHON,'UI_FIXTURE_PYTHON must point to an installed interpreter');
  const evidence=process.env.WORD_UI_EVIDENCE;if(evidence)fs.mkdirSync(evidence,{recursive:true});
  const report={task:'CUSTOMER_HTML_WORD_P0_FIX_V1',mode:'ACTUAL_FRONTEND_AND_DOCUMENT_API_WITH_SYNTHETIC_LOCAL_DB',checks:[],provider_calls:0,production_changes:0,test_changes:0,source_task_run:'FIXTURE_ONLY',word_desktop_open:'NOT_VERIFIED'};
  const bridge=spawn(process.env.UI_FIXTURE_PYTHON,['-u',path.join(__dirname,'activity_word_api_bridge.py')],{env:{...process.env,PYTHONIOENCODING:'utf-8'},stdio:['pipe','pipe','pipe']});
  let stderr='',closed=false;bridge.stderr.on('data',data=>stderr+=data.toString());
  const lines=[],pending=[];readline.createInterface({input:bridge.stdout}).on('line',line=>{try{const data=JSON.parse(line);const waiting=pending.shift();if(waiting)waiting.resolve(data);else lines.push(data);}catch{stderr+=line+'\n';}});
  bridge.on('exit',code=>{closed=true;for(const waiter of pending.splice(0))waiter.reject(Error('Bridge exit '+code+': '+stderr.slice(-2500)));});
  const receive=()=>lines.length?Promise.resolve(lines.shift()):new Promise((resolve,reject)=>{if(closed)return reject(Error(stderr));const timeout=setTimeout(()=>reject(Error('Bridge timeout: '+stderr.slice(-2500))),20000);pending.push({resolve:value=>{clearTimeout(timeout);value.bridge_error?reject(Error(JSON.stringify(value))):resolve(value);},reject:error=>{clearTimeout(timeout);reject(error);}});});
  const call=command=>{bridge.stdin.write(JSON.stringify(command)+'\n');return receive();};
  let server,browser,principal,historyValid=true,historyDirect=true,documentPosts=0,runPosts=0,external=0,failNextDocument=false;
  try{
    const ready=await receive();assert.equal(ready.ready,true);principal=(await call({method:'GET',path:'/api/v1/me'})).json;
    server=http.createServer((req,res)=>{const url=new URL(req.url,'http://localhost'),file=url.pathname.startsWith('/static/')?path.resolve(staticDir,url.pathname.slice(8)):path.join(staticDir,'index.html');if(!file.startsWith(staticDir+path.sep)||!fs.existsSync(file)){res.writeHead(404).end();return;}res.writeHead(200,{'content-type':file.endsWith('.css')?'text/css':file.endsWith('.js')?'application/javascript':'text/html'});res.end(fs.readFileSync(file));});
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin=`http://127.0.0.1:${server.address().port}`;
    browser=await chromium.launch({headless:true,...(process.env.WECHAT_UI_BROWSER?{executablePath:process.env.WECHAT_UI_BROWSER}:{})});
    const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();page.setDefaultTimeout(10000);
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await context.route('**/*',async route=>{
      const req=route.request(),url=new URL(req.url());if(url.origin!==origin){if(url.hostname!=='fonts.googleapis.com')external++;return route.abort();}
      if(!url.pathname.startsWith('/api/'))return route.continue();
      const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
      if(url.pathname==='/api/v1/me'||url.pathname.startsWith('/api/v1/documents/activity-plan')){
        if(req.method()==='POST'){
          documentPosts++;const payload=JSON.parse(req.postData());assert.deepEqual(payload.content,content);assert.deepEqual(payload.presentation,{});
          if(failNextDocument){failNextDocument=false;return json({detail:'Fixture temporary storage unavailable'},503);}
        }
        const result=await call({method:req.method(),path:url.pathname,...(req.postData()?{body:JSON.parse(req.postData())}:{})});
        return route.fulfill({status:result.status,headers:result.headers,body:result.base64?Buffer.from(result.base64,'base64'):JSON.stringify(result.json)});
      }
      if(req.method()!=='GET'){runPosts++;return json({error_code:'FIXTURE_TASK_CALL_NOT_ALLOWED'},400);}
      if(url.pathname==='/api/v1/workspace')return json({agents:[agent],credit_balance:100,tenant_name:'Synthetic local fixture'});
      if(url.pathname==='/api/v1/agents/campaign-agent')return json(agent);
      if(url.pathname==='/api/v1/conversations')return json([{id:'fixture-plan-conversation',agent_id:agent.id,agent,project:{name:'Fixture完整活动方案'},latest_status:'completed'}]);
      if(url.pathname==='/api/v1/conversations/fixture-plan-conversation')return json({id:'fixture-plan-conversation',agent_id:agent.id,agent,project:{name:'Fixture完整活动方案'},messages:[{id:'fixture-plan-message',role:'assistant',task_id:'fixture-plan-task',content:'# '+content.document_title+'\n\n'+content.invitation_copy,...(historyDirect?{structured_result:historyValid?structured:null}:{})}],tasks:[]});
      if(url.pathname==='/api/v1/tasks/fixture-plan-task')return json({id:'fixture-plan-task',status:'completed',assistant_message_id:'fixture-plan-message',structured_result:historyValid?structured:null});
      return json({error_code:'FIXTURE_ROUTE_UNAVAILABLE'},404);
    });
    const check=async(name,fn)=>{console.log('CHECK '+name);await fn();report.checks.push({name,status:'PASS_ISOLATED',observed_at:new Date().toISOString()});};
    const open=async()=>{await page.goto(origin+'/agents/campaign');await page.locator('[data-select-agent-conversation]').click();await page.locator('[data-assistant-message-id="fixture-plan-message"]').waitFor();};
    const clear=async()=>{await page.evaluate(()=>sessionStorage.clear());};
    const shot=async name=>{if(evidence)await page.screenshot({path:path.join(evidence,name+'.png')});};
    let documentUrl,downloadBytes;
    await check('W01 valid trusted plan offers the existing Word entry',async()=>{await open();await page.locator('[data-document-export]').waitFor();assert.equal(await page.locator('[data-document-export]').textContent(),'导出 Word');assert.equal(documentPosts,0);});
    await check('W02 actual API persists DOCX and returns a real downloadable card',async()=>{await page.locator('[data-document-export]').click();await page.locator('.document-file-card').waitFor();assert.equal(documentPosts,1);documentUrl=await page.locator('.document-file-card').getAttribute('href');assert.match(documentUrl,/^\/api\/v1\/documents\/activity-plan\/[a-f0-9-]+$/);assert.match(await page.locator('.document-file-card strong').textContent(),/\.docx$/);await shot('word-card-1440');});
    await check('W03 actual browser authorized download is nonempty DOCX with full content',async()=>{const pending=page.waitForEvent('download');await page.locator('.document-file-card').click();const download=await pending;assert.match(download.suggestedFilename(),/\.docx$/);downloadBytes=fs.readFileSync(await download.path());assert.ok(downloadBytes.length>30000&&downloadBytes.subarray(0,2).equals(Buffer.from('PK')));if(evidence)await download.saveAs(path.join(evidence,'activity-plan-fixture.docx'));assert.equal(documentPosts,1);});
    await check('W04 history reload restores the same download without another export',async()=>{await page.reload();await page.locator('.document-file-card').waitFor();assert.equal(await page.locator('.document-file-card').getAttribute('href'),documentUrl);const response=await call({method:'GET',path:documentUrl});assert.equal(response.status,200);assert.equal(Buffer.from(response.base64,'base64').length,downloadBytes.length);assert.equal(documentPosts,1);await shot('word-history-1440');});
    await check('W05 explicit Word follow-up reuses the trusted plan, not a model run',async()=>{await clear();await page.reload();await page.locator('[data-document-export]').waitFor();await page.locator('#prompt').fill('我要一个word格式的');await page.locator('#composer-submit').click();await page.locator('.document-file-card').waitFor();assert.equal(documentPosts,2);assert.equal(runPosts,0);assert.match(await page.locator('.document-intent-feedback').textContent(),/Word 文档已生成/);});
    await check('W06 null artifact refuses fake attachments or server-path downloads',async()=>{historyValid=false;await clear();await open();assert.equal(await page.locator('[data-document-export],.document-file-card').count(),0);await page.locator('#prompt').fill('我要一个word格式的');await page.locator('#composer-submit').click();await page.waitForFunction(()=>document.querySelector('.document-intent-feedback')?.textContent.includes('尚未形成'));assert.equal(documentPosts,2);assert.equal(runPosts,0);assert.equal(await page.locator('.document-file-card').count(),0);assert.ok(!(await page.locator('#main').textContent()).includes('/opt/'));await shot('word-missing-artifact');historyValid=true;});
    await check('W07 history Task hydration uses matching real contract, not Markdown parsing',async()=>{historyDirect=false;await clear();await open();await page.locator('[data-document-export]').waitFor();historyDirect=true;});
    await check('W08 failed export has clear safe retry and no fake download card',async()=>{await clear();await open();failNextDocument=true;await page.locator('[data-document-export]').click();await page.locator('.document-result-error').waitFor();assert.equal(await page.locator('.document-file-card').count(),0);assert.match(await page.locator('[data-document-export]').textContent(),/重试/);await page.locator('[data-document-export]').click();await page.locator('.document-file-card').waitFor();assert.equal(documentPosts,4);});
    for(const width of [1440,1920,375])await check('W09 Word card/composer layout '+width,async()=>{await page.setViewportSize({width,height:width===375?812:1080});await page.locator('.document-file-card').scrollIntoViewIfNeeded();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.equal(await page.locator('#composer-submit').isVisible(),true);assert.ok(!(await page.locator('.document-result-ready').textContent()).includes('/opt/'));await shot('word-card-'+width);});
    await check('W10 real same-tenant other user and cross-tenant user both receive 404',async()=>{for(const alias of ['peer_a','admin_b']){await call({operation:'login',alias});const denied=await call({method:'GET',path:documentUrl});assert.equal(denied.status,404);assert.ok(!JSON.stringify(denied.json).includes('/opt/'));}await call({operation:'login',alias:'member_a'});});
    await check('W11 real logged-out document access is rejected with 401',async()=>{await call({operation:'logout'});assert.equal((await call({method:'GET',path:documentUrl})).status,401);await call({operation:'login',alias:'member_a'});});
    await check('W12 no external calls, new Agent runs, or browser errors',async()=>{assert.equal(external,0);assert.equal(runPosts,0);assert.deepEqual(errors,[]);});
    report.api=await call({operation:'summary'});report.total=report.checks.length;report.failed=0;report.skipped=0;report.status='PASS_ISOLATED_FRONTEND_AND_REAL_DOCUMENT_API';report.completed_at=new Date().toISOString();
    if(evidence)fs.writeFileSync(path.join(evidence,'word-browser-api-result.json'),JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify(report,null,2));await context.close();
  }finally{
    if(browser)await browser.close();if(server)await new Promise(resolve=>server.close(resolve));
    if(!closed){try{await call({operation:'stop'});}finally{bridge.stdin.end();}}
  }
}
run().catch(error=>{console.error(error);process.exitCode=1;});
