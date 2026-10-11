// Isolated browser fixtures against actual workbench assets. No real business run,
// Provider, production/test endpoint, database or remote image request is allowed.
const assert=require('node:assert/strict');
const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const staticDir=path.resolve(__dirname,'../app/static');
const evidenceDir=path.resolve(process.env.IMAGE_GALLERY_EVIDENCE_DIR||path.join(__dirname,'../../.image-gallery-fixture-evidence'));
const agent={id:'image-agent',slug:'image',name:'图片生成智能体',icon:'image',description:'根据品牌和场景需求，快速生成符合要求的视觉内容。',category:'image',enabled:true};
const artifact=index=>({id:`fixture-artifact-${index}`,task_id:'fixture-task',storage_key:`generated/fixture-tenant/${index}.svg`,content_url:`/api/v1/storage/generated/fixture-tenant/${index}.svg`});
const reply='正面（封面+封底）\n/api/v1/storage/generated/fixture-tenant/1.svg\n\n反面（内页三折）\n[反面设计](/api/v1/storage/generated/fixture-tenant/2.svg)\n\n源文件 /opt/app/runtime-data/fixture/workspace/file.png\n参考：[正常业务链接](https://example.invalid/workspace/guide)';
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function svg(index){
  const portrait=index%2===1,width=portrait?640:1080,height=portrait?960:720;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><rect width="100%" height="100%" fill="${portrait?'#eaf1ff':'#eef8f4'}"/><circle cx="${width*.78}" cy="${height*.22}" r="${width*.28}" fill="${portrait?'#cbdafe':'#cae8db'}"/><rect x="48" y="${height*.48}" width="${width-96}" height="${height*.38}" rx="20" fill="white"/><text x="70" y="${height*.58}" font-family="sans-serif" font-size="${portrait?32:42}" fill="#284d8a">FIXTURE IMAGE ${index}</text><text x="70" y="${height*.68}" font-family="sans-serif" font-size="22" fill="#52657f">${portrait?'Portrait':'Landscape'} preview</text><text x="70" y="${height*.77}" font-family="sans-serif" font-size="18" fill="#52657f">Isolated UI acceptance / not AI output</text></svg>`;
}
async function main(){
  fs.mkdirSync(evidenceDir,{recursive:true});
  let state={count:2,exists:false,mode:'sse',blocked:false,posts:0,storageRequests:[]};
  const results=[],browserErrors=[],externalAttempts=[];
  const generations=()=>Array.from({length:state.count},(_,index)=>state.mode==='missing-resource'&&index===1?{id:'fixture-unavailable',task_id:'fixture-task'}:state.mode==='legacy-remainder'&&index===1?{...artifact(2),content_url:artifact(1).content_url}:artifact(index+1));
  const payload=()=>['legacy','legacy-remainder'].includes(state.mode)?{generation:artifact(1)}:{generations:generations()};
  const complete=()=>({id:'fixture-task',status:'completed',conversation_id:'fixture-conversation',assistant_message_id:'fixture-assistant',final_response:reply,...payload()});
  const project=()=>({id:'fixture-conversation',agent_id:'image-agent',agent,project:{name:'Fixture 多图项目',type:'图片生成项目'},latest_status:'completed',latest_task:{id:'fixture-task',status:'completed'},latest_generation:artifact(1),latest_prompt:'Fixture 设计正反两面'});
  const server=http.createServer(async(request,response)=>{
    const url=new URL(request.url,'http://fixture.local'),pathname=url.pathname;
    const json=(data,status=200)=>{response.writeHead(status,{'content-type':'application/json'});response.end(JSON.stringify(data));};
    if(pathname==='/api/v1/me')return json({id:'fixture-user',user_id:'fixture-user',tenant_id:'fixture-tenant',role:'member',display_name:'Fixture 用户'});
    if(pathname==='/api/v1/workspace')return json({agents:[{...agent,enabled:!state.blocked}],tenant_name:'Fixture 企业',credit_balance:100});
    if(pathname==='/api/v1/conversations')return json(state.exists?[project()]:[]);
    if(pathname==='/api/v1/conversations/fixture-conversation')return json({id:'fixture-conversation',agent_id:'image-agent',agent,project:project().project,tasks:[],generations:generations(),messages:[{id:'fixture-user-message',role:'user',content:'Fixture 设计正反两面'},{id:'fixture-assistant',role:'assistant',task_id:'fixture-task',content:reply,...payload()}]});
    if(pathname==='/api/v1/agents/image-agent/runs'){
      state.posts++;if(state.blocked)return json({error_code:'AGENT_UNAVAILABLE'},404);
      for await(const ignored of request){}state.exists=true;
      return json({id:'fixture-task',status:'queued',conversation_id:'fixture-conversation'},202);
    }
    if(pathname==='/api/v1/tasks/fixture-task')return json(complete());
    if(pathname==='/api/v1/tasks/fixture-task/cancel')return json({status:'cancelled'});
    if(pathname==='/api/v1/tasks/fixture-task/events'){
      if(state.mode==='polling'){response.writeHead(503).end();return;}
      response.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-cache'});
      const emit=(kind,value)=>{if(!response.destroyed)response.write(`event: ${kind}\ndata: ${JSON.stringify(value)}\n\n`);};
      emit('activity',{sequence:1,stage:'generating',status:'started'});
      const chunks=['正面（封面+封底）\n/api/v1/sto','rage/generated/fixture-tenant/1.svg','\n\n反面（内页三折）\n','/opt/app/runtime-data/fixture/workspace/file.png','\n设计说明保留。'];
      for(let index=0;index<chunks.length;index++){emit('delta',{sequence:index+1,text:chunks[index]});await sleep(80);}
      if(state.mode==='cancelled'){emit('cancelled',{status:'cancelled'});response.end();return;}
      if(state.mode==='failed'){emit('error',{message:'Fixture 生成中断 /opt/private/file.png'});response.end();return;}
      emit('complete',complete());response.end();return;
    }
    if(pathname.startsWith('/api/v1/storage/generated/fixture-tenant/')){
      state.storageRequests.push(pathname);const index=Number(pathname.match(/(\d+)\.svg$/)?.[1]);
      if(state.mode==='partial-failure'&&index===2&&!url.searchParams.has('retry')){response.writeHead(404).end();return;}
      response.writeHead(200,{'content-type':'image/svg+xml','content-disposition':`inline; filename="fixture-image-${index}.svg"`});response.end(svg(index));return;
    }
    if(pathname.startsWith('/api/'))return json({error_code:'NOT_FOUND',user_message:'Fixture missing'},404);
    const filename=pathname.startsWith('/static/')?path.resolve(staticDir,pathname.slice(8)):path.join(staticDir,'index.html');
    if(!filename.startsWith(staticDir+path.sep)||!fs.existsSync(filename)){response.writeHead(404).end();return;}
    response.writeHead(200,{'content-type':filename.endsWith('.js')?'application/javascript':filename.endsWith('.css')?'text/css':'text/html'});response.end(fs.readFileSync(filename));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  let browser;
  const check=(id,detail,fn)=>fn().then(()=>{results.push({id,status:'PASS_FIXTURE_BROWSER',detail});});
  try{
    browser=await chromium.launch({headless:true,...(process.env.CHAT_IMAGE_BROWSER_EXECUTABLE?{executablePath:process.env.CHAT_IMAGE_BROWSER_EXECUTABLE}:{})});
    for(const width of [1440,1920,375]){
      for(const count of [1,2,3]){
        state={count,exists:false,mode:count===1?'legacy':'sse',blocked:false,posts:0,storageRequests:[]};
        const context=await browser.newContext({viewport:{width,height:width===375?812:1080},permissions:['clipboard-read','clipboard-write'],acceptDownloads:true});
        await context.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();externalAttempts.push(route.request().url());return route.abort();});
        const page=await context.newPage();page.on('pageerror',error=>browserErrors.push(error.message));
        await page.goto(`${origin}/agents/image`);await page.locator('#prompt').waitFor();
        await page.evaluate(()=>{
          window.fixtureTextLeaks=[];const root=document.querySelector('#chat-body');
          new MutationObserver(()=>{for(const node of root.querySelectorAll('.chat-message-assistant .chat-message-content,.streaming-failure')){const text=node.textContent;if(/\/api\/v1\/storage|\/opt\/|runtime-data\/|workspace\//.test(text.replace(/https:\/\/example.invalid\/workspace\/guide/g,'')))window.fixtureTextLeaks.push(text);}}).observe(root,{subtree:true,childList:true,characterData:true});
        });
        await page.locator('#prompt').fill('Fixture 设计正反两面');await page.getByRole('button',{name:'发送消息'}).click();
        await page.locator('.message-generation').last().waitFor();
        await check(`LIVE_${width}_${count}`,'Actual SSE/DOM renderer, fixture artifacts only',async()=>{
          assert.equal(await page.locator('.message-generation').count(),count);
          assert.equal(await page.locator('.message-generation-actions [data-open-message-generation]').count(),count);
          assert.equal(await page.locator('.message-generation-actions a[download]').count(),count);
          const sources=await page.locator('.message-generation img').evaluateAll(nodes=>nodes.map(node=>new URL(node.src).pathname));
          assert.deepEqual(sources,Array.from({length:count},(_,index)=>artifact(index+1).content_url));
          const columns=await page.locator('.message-generations-grid').evaluate(node=>getComputedStyle(node).gridTemplateColumns.split(' ').length);
          assert.equal(columns,width===375||count===1?1:2);
          assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
          for(const box of await page.locator('.message-generation .media-thumb').evaluateAll(nodes=>nodes.map(node=>({w:node.getBoundingClientRect().width,h:node.getBoundingClientRect().height,fit:getComputedStyle(node.querySelector('img')).objectFit})))){assert.ok(box.w<=360);assert.ok(box.h<=281);assert.equal(box.fit,'contain');}
          assert.deepEqual(await page.evaluate(()=>window.fixtureTextLeaks),[]);
        });
        await check(`COPY_${width}_${count}`,'Real browser clipboard click; display redaction, not authorization',async()=>{
          await page.locator('[data-copy-response]').click();
          await page.getByRole('button',{name:'已复制',exact:true}).waitFor();
          const text=await page.evaluate(()=>navigator.clipboard.readText());
          assert.ok(!/\/api\/v1\/storage\/generated\/|\/opt\/|runtime-data\//.test(text));
          assert.ok(text.includes('正面（封面+封底）'));assert.ok(text.includes('反面（内页三折）'));assert.ok(text.includes('https://example.invalid/workspace/guide'));
          assert.equal(await page.locator('.chat-message-content a[href="https://example.invalid/workspace/guide"]').count(),1);
        });
        await check(`ACTIONS_${width}_${count}`,'Each fixture image uses its own viewer and GET download',async()=>{
          for(let index=0;index<count;index++){
            await page.locator('[data-open-message-generation]').nth(index).click();
            assert.ok((await page.locator('.viewer-canvas img').getAttribute('src')).endsWith(`/${index+1}.svg`));
            await page.getByRole('button',{name:'关闭大图预览'}).click();
            const waiting=page.waitForEvent('download');await page.locator('.message-generation-actions a[download]').nth(index).click();const download=await waiting;
            assert.equal(await download.failure(),null);assert.ok(download.url().endsWith(`/${index+1}.svg`));
            assert.ok(fs.statSync(await download.path()).size>0);
          }
        });
        if(count===2){await page.locator('#chat-body').evaluate(node=>{node.scrollTop=node.scrollHeight;});await page.screenshot({path:path.join(evidenceDir,`gallery-2-${width}.png`)});}
        await page.reload();
        if(width===375){await page.goto(`${origin}/conversations`);await page.locator('[data-history-conversation="fixture-conversation"]').click();}
        else await page.locator('[data-select-agent-conversation="fixture-conversation"]').click();
        await page.locator('.message-generation').last().waitFor();
        await check(`HISTORY_${width}_${count}`,'Fresh reload/reopen uses message arrays; duplicate conversation artifacts omitted',async()=>{
          assert.equal(await page.locator('.message-generation').count(),count);
          const text=await page.locator('.chat-message-assistant').innerText();assert.ok(!/\/api\/v1\/storage\/generated\/|\/opt\/|runtime-data\//.test(text));
          assert.equal(await page.locator('.conversation-artifacts').count(),0);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        });
        if(count===2){await page.locator('#chat-body').evaluate(node=>{node.scrollTop=node.scrollHeight;});await page.screenshot({path:path.join(evidenceDir,`history-2-${width}.png`)});}
        if(width===1920&&count===3){await page.screenshot({path:path.join(evidenceDir,'gallery-3-1920-top.png')});await page.locator('#chat-body').evaluate(node=>{node.scrollTop=node.scrollHeight;});await page.screenshot({path:path.join(evidenceDir,'history-3-1920-bottom.png')});}
        await context.close();
      }
    }
    for(const mode of ['partial-failure','missing-resource','polling','failed','cancelled','legacy-remainder']){
      state={count:3,exists:false,mode,blocked:false,posts:0,storageRequests:[]};
      const context=await browser.newContext({viewport:{width:1440,height:1080}});
      await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
      const page=await context.newPage();page.on('pageerror',error=>browserErrors.push(error.message));
      await page.goto(`${origin}/agents/image`);await page.locator('#prompt').fill('Fixture 状态验证');await page.getByRole('button',{name:'发送消息'}).click();
      await check(`STATE_${mode}`,'Isolated transport/error/terminal branches',async()=>{
        if(mode==='failed'||mode==='cancelled'){
          await page.getByText(mode==='failed'?'Fixture 生成中断':'已停止生成 · 部分回复未保存',{exact:false}).waitFor();
          assert.equal(await page.locator('.message-generation').count(),0);assert.equal(await page.locator('.streaming-caret').count(),0);
          const text=await page.locator('#chat-body').innerText();assert.ok(!/\/opt\/|\/api\/v1\/storage\/generated\/|runtime-data\//.test(text));
        }else if(mode==='legacy-remainder'){
          await page.locator('.message-generation').waitFor();assert.equal(await page.locator('.message-generation').count(),1);
          await page.reload();await page.locator('[data-select-agent-conversation="fixture-conversation"]').click();
          await page.waitForFunction(()=>document.querySelectorAll('.message-generation').length===3);
          assert.equal(await page.locator('.conversation-artifacts .message-generation').count(),2);
          assert.equal(await page.locator('.message-generation-actions a[download]').count(),3);
        }else{
          await page.locator('.message-generation').last().waitFor();assert.equal(await page.locator('.message-generation').count(),3);
          if(mode==='partial-failure'){
            await page.getByText('图片加载失败',{exact:true}).waitFor();assert.equal(await page.locator('.message-generation .media-loaded').count(),2);
            await page.locator('.media-failed').click();await page.waitForFunction(()=>document.querySelectorAll('.message-generation .media-loaded').length===3);
          }
          if(mode==='missing-resource'){assert.equal(await page.getByText('图片暂不可用',{exact:true}).count(),1);assert.equal(await page.locator('[data-open-message-generation]').count(),2);}
        }
      });await context.close();
    }
    state={count:2,exists:true,mode:'sse',blocked:true,posts:0,storageRequests:[]};
    const context=await browser.newContext({viewport:{width:1440,height:900}});await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());const page=await context.newPage();
    await page.goto(`${origin}/agents/image`);
    await check('DISABLED_NO_RUN','Disabled Agent unchanged; fixture metadata only',async()=>{await page.getByText('该智能体暂未启用。',{exact:true}).waitFor();assert.equal(await page.locator('#composer').count(),0);assert.equal(state.posts,0);});
    await context.close();assert.deepEqual(browserErrors,[]);
    const report={status:'PASS_FIXTURE_BROWSER',executed_at:new Date().toISOString(),results,total:results.length,failures:0,skips:0,browser_errors:browserErrors,blocked_external_resource_attempts:[...new Set(externalAttempts)],real_business_task_runs:0,provider_calls:0,production_changes:0,test_changes:0,database_changes:0,backend_integration:'PENDING_01_GENERATIONS_CONTRACT',screenshots:fs.readdirSync(evidenceDir).filter(name=>name.endsWith('.png'))};
    fs.writeFileSync(path.join(evidenceDir,'browser-results.json'),JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify(report,null,2));
  }finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
