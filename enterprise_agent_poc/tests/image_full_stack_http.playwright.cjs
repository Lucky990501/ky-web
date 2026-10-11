// Real application HTTP and private PostgreSQL, not API route mocks.
// Python injects ONLY explicit model/Gateway Fixtures and uses the actual image tool.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {chromium,request}=require('playwright');
const evidenceDir=path.resolve(process.env.IMAGE_FULL_STACK_EVIDENCE_DIR||'');
assert.ok(process.env.IMAGE_FULL_STACK_EVIDENCE_DIR,'Explicit evidence directory required');
const ready=JSON.parse(fs.readFileSync(path.join(evidenceDir,'http-ready.json'),'utf8'));
const origin=ready.origin,url=new URL(origin);
assert.equal(ready.fixture,true);assert.equal(url.hostname,'127.0.0.1');
assert.ok(![80,443,18100,18101].includes(Number(url.port))&&Number(url.port)>1024);
const password='FixtureOnly!2026'; // Matches the isolated synthetic users, no real credential.
const results=[],tasks=[],errors=[],externalAttempts=[];
const passed=(id,detail)=>results.push({id,status:'PASS_ISOLATED_HTTP_BROWSER',detail});
const apiJson=async(response,status=200)=>{assert.equal(response.status(),status);return response.json();};
const hash=buffer=>crypto.createHash('sha256').update(buffer).digest('hex');
const hasInternal=text=>/\/api\/v1\/storage\/generated\/|\/opt\/|runtime-data\//.test(text);
async function instrument(context){
  await context.addInitScript(()=>{
    window.imageAcceptance={complete:[],delta:[],leaks:[]};
    const Native=window.EventSource;
    window.EventSource=class extends Native{
      constructor(url,options){super(url,options);
        this.addEventListener('complete',event=>window.imageAcceptance.complete.push({url,payload:JSON.parse(event.data)}));
        this.addEventListener('delta',event=>window.imageAcceptance.delta.push({url,payload:JSON.parse(event.data)}));
      }
    };
    const observe=()=>new MutationObserver(()=>{
      for(const node of document.querySelectorAll('.chat-message-assistant .chat-message-content,.streaming-failure')){
        if(/\/api\/v1\/storage\/generated\/|\/opt\/|runtime-data\//.test(node.textContent))window.imageAcceptance.leaks.push(node.textContent);
      }
    }).observe(document.body,{subtree:true,childList:true,characterData:true});
    document.readyState==='loading'?document.addEventListener('DOMContentLoaded',observe):observe();
  });
  await context.route('**/*',route=>{
    if(new URL(route.request().url()).origin===origin)return route.continue();
    externalAttempts.push(new URL(route.request().url()).hostname);return route.abort();
  });
}
async function login(page){
  await page.goto(`${origin}/agents/image`);
  await page.locator('#login [name="account"]').fill('owner@fixture.test');
  await page.locator('#login [name="password"]').fill(password);
  const loginResponse=page.waitForResponse(response=>response.url().endsWith('/api/v1/auth/login'));
  await page.locator('#login [type="submit"]').click();assert.equal((await loginResponse).status(),200);
  await page.locator('#prompt').waitFor();
}
async function main(){
  let browser,failure;
  try{
    browser=await chromium.launch({headless:true,...(process.env.CHAT_IMAGE_BROWSER_EXECUTABLE?{executablePath:process.env.CHAT_IMAGE_BROWSER_EXECUTABLE}:{})});
    for(const width of [1440,1920,375])for(const count of [1,2,3]){
      const context=await browser.newContext({viewport:{width,height:width===375?812:1080},permissions:['clipboard-read','clipboard-write'],acceptDownloads:true});
      await instrument(context);const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
      await login(page);
      await apiJson(await context.request.get(`${origin}/api/v1/agents/image-agent`));
      passed(`LOGIN_METADATA_${width}_${count}`,'Actual form login/session and Agent Metadata HTTP 200');
      const runResponse=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/api/v1/agents/image-agent/runs'));
      await page.locator('#prompt').fill(`Fixture 隔离图片验收 ${width} / ${count} 张；FIXTURE_IMAGES=${count}`);
      await page.getByRole('button',{name:'发送消息'}).click();
      const submitted=await apiJson(await runResponse,202);
      await page.waitForFunction(n=>document.querySelectorAll('.message-generation').length===n&&!document.querySelector('#task-status'),count);
      const task=await apiJson(await context.request.get(`${origin}/api/v1/tasks/${submitted.id}`));
      assert.equal(task.status,'completed');assert.equal(task.generations.length,count);
      assert.deepEqual(task.generation,task.generations[0]);
      const observation=await page.evaluate(()=>window.imageAcceptance);
      assert.equal(observation.complete.length,1);assert.ok(observation.delta.length>0);assert.deepEqual(observation.leaks,[]);
      assert.deepEqual(observation.complete[0].payload.generations,task.generations);
      assert.deepEqual(observation.complete[0].payload.generation,task.generation);
      assert.ok(task.generations.every(item=>item.id&&item.task_id===submitted.id&&item.available&&item.mime_type==='image/png'&&item.width&&item.height));
      passed(`TASK_SSE_${width}_${count}`,'Real Task -> effective tools -> ordered DTO; browser received matching SSE complete and delta');
      const domSources=await page.locator('.message-generation img').evaluateAll(nodes=>nodes.map(node=>new URL(node.src).pathname));
      assert.deepEqual(domSources,task.generations.map(item=>item.content_url));
      await page.waitForFunction(()=>[...document.querySelectorAll('.message-generation img')].every(node=>node.complete&&node.naturalWidth>0));
      const columns=await page.locator('.message-generations-grid').evaluate(node=>getComputedStyle(node).gridTemplateColumns.split(' ').length);
      assert.equal(columns,width===375||count===1?1:2);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      const visible=await page.locator('#chat-body').innerText();assert.ok(!hasInternal(visible));assert.ok(visible.includes('正面（封面+封底）'));
      passed(`GALLERY_${width}_${count}`,'Real protected image GETs, stable array order, bounded portrait/landscape previews, no horizontal overflow');
      await page.locator('[data-copy-response]').click();await page.getByRole('button',{name:'已复制',exact:true}).waitFor();
      const copied=await page.evaluate(()=>navigator.clipboard.readText());assert.ok(!hasInternal(copied));assert.ok(copied.includes('https://example.invalid/workspace/guide'));
      passed(`REDACTION_COPY_${width}_${count}`,'Streaming MutationObserver/final DOM and actual clipboard hide internal paths, preserve ordinary links');
      const downloads=[];
      for(let index=0;index<count;index++){
        const expected=task.generations[index];
        await page.locator('[data-open-message-generation]').nth(index).click();
        assert.equal(new URL(await page.locator('.viewer-canvas img').getAttribute('src'),origin).pathname,expected.content_url);
        await page.getByRole('button',{name:'关闭大图预览'}).click();
        const imageResponse=await context.request.get(origin+expected.content_url);assert.equal(imageResponse.status(),200);
        assert.ok(imageResponse.headers()['content-type'].startsWith('image/png'));const bytes=await imageResponse.body();
        assert.ok(bytes.length>0);assert.equal(bytes.subarray(0,8).toString('hex'),'89504e470d0a1a0a');
        const waiting=page.waitForEvent('download');await page.locator('.message-generation-actions a[download]').nth(index).click();
        const download=await waiting;assert.equal(await download.failure(),null);
        assert.equal(hash(fs.readFileSync(await download.path())),hash(bytes));
        downloads.push({generation_id:expected.id,http_status:200,mime:imageResponse.headers()['content-type'],bytes:bytes.length,sha256:hash(bytes)});
      }
      passed(`PREVIEW_DOWNLOAD_${width}_${count}`,'Independent original viewer/download uses session-authenticated actual HTTP bytes');
      const history=await apiJson(await context.request.get(`${origin}/api/v1/conversations/${task.conversation_id}`));
      const message=history.messages.find(item=>item.id===task.assistant_message_id);
      assert.deepEqual(message.generations,task.generations);assert.deepEqual(history.generations,task.generations);
      assert.equal(history.image_count,count);
      await page.reload();
      if(width===375){await page.goto(`${origin}/conversations`);await page.locator(`[data-history-conversation="${task.conversation_id}"]`).click();}
      else await page.locator(`[data-select-agent-conversation="${task.conversation_id}"]`).click();
      await page.waitForFunction(n=>document.querySelectorAll('.message-generation').length===n,count);
      for(let index=0;index<count;index++)await page.locator('.message-generation img').nth(index).scrollIntoViewIfNeeded();
      await page.waitForFunction(n=>document.querySelectorAll('.message-generation .media-loaded').length===n&&[...document.querySelectorAll('.message-generation img')].every(node=>node.complete&&node.naturalWidth>0),count);
      assert.deepEqual(await page.locator('.message-generation img').evaluateAll(nodes=>nodes.map(node=>new URL(node.src).pathname)),domSources);
      assert.equal(await page.locator('.conversation-artifacts').count(),0);
      assert.ok(!hasInternal(await page.locator('#chat-body').innerText()));
      passed(`HISTORY_${width}_${count}`,'Fresh reload/reopen from actual PG history, complete gallery without duplicate cards');
      if(count===2||width===1920&&count===3){
        await page.locator('#chat-body').evaluate(node=>{node.scrollTop=node.scrollHeight;});
        await page.screenshot({path:path.join(evidenceDir,`http-history-${count}-${width}.png`)});
      }
      tasks.push({task_id:submitted.id,conversation_id:task.conversation_id,assistant_message_id:task.assistant_message_id,expected_status:'completed',expected_images:count,
        viewport:width,generation_ids:task.generations.map(item=>item.id),downloads,browser_delta_count:observation.delta.length,api_sse_history_match:true});
      await context.close();
    }
    // Actual session/auth checks against Task, History and every image, not mocked denials.
    for(const account of ['other@fixture.test','foreign@fixture.test',null]){
      const client=await request.newContext({baseURL:origin});
      if(account)assert.equal((await client.post('/api/v1/auth/login',{data:{account,password}})).status(),200);
      for(const item of tasks){
        assert.equal((await client.get(`/api/v1/tasks/${item.task_id}`)).status(),account?404:401);
        assert.equal((await client.get(`/api/v1/conversations/${item.conversation_id}`)).status(),account?404:401);
        const owner=await request.newContext({baseURL:origin});
        await owner.post('/api/v1/auth/login',{data:{account:'owner@fixture.test',password}});
        const task=await apiJson(await owner.get(`/api/v1/tasks/${item.task_id}`));await owner.dispose();
        for(const image of task.generations)assert.equal((await client.get(image.content_url)).status(),account?404:401);
      }
      passed(`AUTH_${account||'anonymous'}`,'Actual denied Task/History/storage access for other user, other tenant or unauthenticated client');await client.dispose();
    }
    for(const fault of ['FIXTURE_RUNTIME_FAILED','FIXTURE_CORRUPT']){
      const context=await browser.newContext({viewport:{width:1440,height:1080}});await instrument(context);const page=await context.newPage();
      await login(page);const waiting=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/api/v1/agents/image-agent/runs'));
      await page.locator('#prompt').fill(`Fixture 失败验证 FIXTURE_IMAGES=3 ${fault}`);await page.getByRole('button',{name:'发送消息'}).click();
      const submitted=await apiJson(await waiting,202);await page.locator('.task-failure').waitFor();
      const task=await apiJson(await context.request.get(`${origin}/api/v1/tasks/${submitted.id}`));assert.equal(task.status,'failed');assert.deepEqual(task.generations,[]);
      assert.equal(await page.locator('.message-generation').count(),0);assert.equal(await page.locator('.streaming-caret').count(),0);
      assert.ok(!hasInternal(await page.locator('#chat-body').innerText()));
      tasks.push({task_id:submitted.id,conversation_id:task.conversation_id,expected_status:'failed',expected_images:0,fault});
      passed(`FAILURE_${fault}`,'Real HTTP/PG failed terminal cannot become gallery success; no result or charge (DB assertions follow)');await context.close();
    }
    // A later preview transport failure is not a generation failure or a successful download.
    const context=await browser.newContext({viewport:{width:1440,height:1080}});await instrument(context);
    // Install before login/sidebar thumbnails can load the first artifact into cache.
    await context.route('**/api/v1/storage/generated/**',route=>route.abort('failed'));
    const page=await context.newPage();await login(page);
    const last=tasks.find(item=>item.expected_images===3);
    await page.locator(`[data-select-agent-conversation="${last.conversation_id}"]`).click();
    await page.locator('.message-generation').last().scrollIntoViewIfNeeded();
    await page.waitForFunction(()=>document.querySelectorAll('.message-generation .media-failed').length===3);
    assert.equal(await page.locator('.message-generation .media-failed').count(),3);
    assert.ok(!(await page.locator('#chat-body').innerText()).includes('下载成功'));
    assert.equal((await apiJson(await context.request.get(`${origin}/api/v1/tasks/${last.task_id}`))).status,'completed');
    passed('PREVIEW_TRANSPORT_FAILURE','Explicit browser transport fault displays per-image errors; does not forge a download receipt or override true Task status');
    await context.close();
    const client=await request.newContext({baseURL:origin});
    const html=await client.get('/agents/image');assert.ok(html.headers()['cache-control'].includes('no-cache'));
    const assets={};for(const asset of ['workbench.js','workbench.css'])assets[asset]=hash(await (await client.get(`/static/${asset}`)).body());
    assert.deepEqual(assets,ready.assets_sha256);await client.dispose();passed('STATIC_SOURCE_CACHE','Actual HTTP assets match exact candidate Git bytes; existing HTML revalidation and release cache mechanism retained');
    assert.deepEqual(errors,[]);
  }catch(error){failure=String(error.stack||error);throw error;}
  finally{
    await browser?.close();
    fs.writeFileSync(path.join(evidenceDir,'http-browser-results.json'),JSON.stringify({status:failure?'FAIL_ISOLATED_HTTP_BROWSER':'PASS_ISOLATED_HTTP_BROWSER',
      source_tree:ready.source_tree,executed_at:new Date().toISOString(),total:results.length,results,tasks,error:failure||null,browser_errors:errors,
      external_requests_blocked:[...new Set(externalAttempts)],real_provider_calls:0,real_image_calls:0,api_route_mocks:0},null,2)+'\n');
    console.log(JSON.stringify({status:failure?'FAIL':'PASS',cases:results.length,tasks:tasks.length,real_provider_calls:0}));
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
