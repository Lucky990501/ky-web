// Actual private ASGI responses over SSH stdio, never a shared HTTP service.
// Runtime/Provider are explicit doubles in the Python bridge. No paid calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const http=require('node:http'),readline=require('node:readline'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');
const remote=process.env.BUSINESS_P0_REMOTE_ROOT;
if(!/^\/tmp\/aq-integration\.[A-Za-z0-9]+$/.test(remote||''))throw Error('Explicit marked isolated fixture root required');
const key=process.env.BUSINESS_P0_SSH_IDENTITY;
if(!key)throw Error('Explicit approved Test SSH identity path required');
const project=remote+'/source/enterprise_agent_poc';
const processBridge=spawn('ssh',['-F','none','-i',key,'-o','IdentitiesOnly=yes','-o','IdentityAgent=none','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','lucky@115.190.56.61',
  `systemd-run --user --quiet --wait --pipe --collect --unit=aq-browser-${path.posix.basename(remote)} --slice=enterprise-agent-test.slice -p CPUQuota=100% -p MemoryMax=1G -p TasksMax=128 --setenv=TMPDIR=${remote}/tmp /opt/enterprise-agent-workbench-test/shared/runtime/python311/bin/python -I -B -u ${project}/tests/business_p0_integration_bridge.py`],{stdio:['pipe','pipe','inherit']});
const lines=readline.createInterface({input:processBridge.stdout});
const pending=[];let readyResolve,readyReject,ready=false;
const started=new Promise((resolve,reject)=>{readyResolve=resolve;readyReject=reject;});
lines.on('line',line=>{try{const data=JSON.parse(line);if(!ready){ready=true;readyResolve(data);}else{const callback=pending.shift();if(callback)callback.resolve(data);}}catch(error){readyReject(Error('Non-JSON bridge output: '+line.slice(0,200)));}});
processBridge.on('exit',code=>{if(!ready)readyReject(Error('Bridge exited '+code));for(const item of pending.splice(0))item.reject(Error('Bridge exited '+code));});
const request=(method,url)=>new Promise((resolve,reject)=>{pending.push({resolve,reject});processBridge.stdin.write(JSON.stringify({method,path:url})+'\n');});
const counts=()=>new Promise((resolve,reject)=>{pending.push({resolve,reject});processBridge.stdin.write(JSON.stringify({operation:'counts'})+'\n');});
const staticRoot=path.resolve(__dirname,'../app/static');
let writes=0,browser,server;
const report={mode:'REAL_CHROME_ACTUAL_PRIVATE_ASGI_SQLITE_PROVIDER_DOUBLE',checks:[],real_provider_calls:0};
async function check(name,run){await run();report.checks.push(name);console.log('PASS '+name);}
async function run(){
  const data=await started;assert.equal(data.ready,true);report.backend_checks=data.checks;report.stub_http_requests=data.stub_http_requests;
  server=http.createServer(async(req,res)=>{try{
    if(req.url.startsWith('/api/')){
      if(req.method!=='GET')writes++;
      const response=await request(req.method,req.url);(report.requests??=[]).push({path:req.url,status:response.status});res.writeHead(response.status,response.headers);res.end(Buffer.from(response.body,'base64'));return;
    }
    const pathname=new URL(req.url,'http://localhost').pathname;
    const target=pathname.startsWith('/static/')?path.resolve(staticRoot,pathname.slice(8)):path.join(staticRoot,'index.html');
    if(!target.startsWith(staticRoot+path.sep)||!fs.existsSync(target)){res.writeHead(404).end();return;}
    res.writeHead(200,{'content-type':target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':'text/html'});res.end(fs.readFileSync(target));
  }catch(error){res.writeHead(500).end('isolated bridge error');console.error(error);}});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
  browser=await chromium.launch({headless:true,executablePath:process.env.WECHAT_UI_BROWSER});
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();page.setDefaultTimeout(12000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  async function open(task){const route={'image-agent':'image','campaign-agent':'campaign'}[task.agent_id]||task.agent_id;await page.goto(`${origin}/agents/${route}?conversation=${task.conversation_id}`);try{await page.locator('.creation-layout').waitFor();}catch(error){report.page_text=await page.locator('body').innerText();report.page_errors=errors;throw error;}}
  await check('actual Task/history API reaches two separate original-Logo cards',async()=>{
    await open(data.normal);assert.equal(await page.locator('[data-result-image]').count(),2);
    assert.match(await page.locator('.message-generation-label').first().textContent(),/正面/);
    assert.match(await page.locator('.message-generation-label').last().textContent(),/背面/);
    assert.ok((await page.locator('.image-result-dimensions').first().textContent()).includes('1536 × 1024'));
  });
  await check('both actual authorized downloads decode to saved original pixels',async()=>{
    for(let i=0;i<2;i++){
      const pendingDownload=page.waitForEvent('download');await page.locator('.message-generation-actions a').nth(i).click();
      const file=await(await pendingDownload).path();assert.ok(fs.statSync(file).size>100);
      const buffer=fs.readFileSync(file);assert.equal(buffer.readUInt32BE(16),1536);assert.equal(buffer.readUInt32BE(20),1024);
      await page.locator('[data-open-message-generation]').nth(i).click();assert.equal(await page.locator('#generation-viewer img').count(),1);await page.keyboard.press('Escape');
    }
  });
  await check('real partial Task preserves successful image and honest delivery warning',async()=>{
    await open(data.partial);assert.equal(await page.locator('[data-result-image]').count(),1);assert.match(await page.locator('.image-delivery-warning').textContent(),/已完成 1 张，失败 1 张/);
  });
  await check('requested 9:16 and actual square are not presented as a matching result',async()=>{
    await open(data.mismatch);const text=await page.locator('.image-result-dimensions').textContent();assert.match(text,/1024 × 1024/);assert.match(text,/864.*1536/);assert.match(text,/不一致|不匹配/);
  });
  await check('real no-inline-JSON history restores valid plan, Word card and illustration briefs',async()=>{
    await open(data.campaign);await page.locator('[data-illustration-briefs]').waitFor();assert.equal(await page.locator('[data-illustration-choice] option').count(),3);assert.equal(await page.locator('[data-document-export]').count(),1);
    await page.reload();await page.locator('[data-illustration-briefs]').waitFor();assert.equal(await page.locator('[data-illustration-briefs]').count(),1);
  });
  await check('displaying briefs and reviewing fee never POST an image task',async()=>{
    assert.equal(writes,0);await page.locator('[data-illustration-review]').click();await page.locator('#illustration-confirm').waitFor();assert.match(await page.locator('#illustration-confirm').textContent(),/积分/);assert.equal(writes,0);await page.keyboard.press('Escape');
  });
  await check('actual SSE contains full ordered result array',async()=>{
    const response=await request('GET',`/api/v1/tasks/${data.normal.id}/events`),text=Buffer.from(response.body,'base64').toString();
    const final=JSON.parse(text.split('event: complete\ndata: ')[1].split('\n\n')[0]);assert.equal(final.generations.length,2);assert.deepEqual(final.generations.map(item=>item.purpose),['正面','背面']);
  });
  await check('read-only browser recovery creates no Run or paid request',async()=>{assert.equal(writes,0);assert.deepEqual(errors,[]);});
  await check('actual Python Skill-style renderer survives frontend sanitizer, iframe and authorized image fetch',async()=>{
    // Component integration only, not a published WeChat Agent/model result.
    await page.evaluate(content=>{
      const main=document.querySelector('#main'),article=document.createElement('article');article.dataset.assistantMessageId='renderer-component-fixture';
      article.innerHTML='<div class="chat-message-content"></div>';main.append(article);
      window.WorkbenchWechatArticle.mountManual({main,article,agent:{slug:'wechat-official-account-writing'},content,messageId:'renderer-component-fixture',inlineBody:true,isCurrent:()=>main.isConnected,modal:()=>()=>{}});
    },data.rendered_article);
    const frame=await(await page.locator('.wechat-inline-article').elementHandle()).contentFrame();
    await frame.locator('blockquote').waitFor();assert.equal(await frame.locator('strong').evaluate(node=>getComputedStyle(node).color),'rgb(255, 202, 0)');
    assert.equal(await frame.locator('blockquote').evaluate(node=>getComputedStyle(node).borderLeftWidth),'4px');
    assert.equal(await frame.locator('p').first().evaluate(node=>getComputedStyle(node).marginBottom),'28px');
    await frame.waitForFunction(()=>document.querySelector('img')?.naturalWidth===1536);
    assert.equal(await frame.locator('img').evaluate(node=>node.naturalHeight),1024);assert.equal(writes,0);
  });
  await check('database confirms browser history and SSE made no new Task, Run, generation or credit debit',async()=>{
    const after=await counts();assert.deepEqual(after,data.counts);report.database_counts_before=data.counts;report.database_counts_after=after;
  });
}
run().then(()=>{report.result='PASS';}).catch(error=>{report.result='FAIL';report.error=String(error.stack||error);process.exitCode=1;console.error(error);}).finally(async()=>{
  if(browser)await browser.close();if(server)await new Promise(resolve=>server.close(resolve));
  processBridge.stdin.end(JSON.stringify({operation:'stop'})+'\n');
  if(process.env.BUSINESS_P0_INTEGRATION_REPORT)fs.writeFileSync(process.env.BUSINESS_P0_INTEGRATION_REPORT,JSON.stringify(report,null,2));
  console.log(JSON.stringify({result:report.result,backend_checks:report.backend_checks?.length,browser_checks:report.checks.length,error:report.error}));
});
