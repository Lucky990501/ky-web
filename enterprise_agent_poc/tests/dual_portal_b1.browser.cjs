// Isolated browser fixture: real shipped HTML/JS/CSS, mocked HTTP, no Provider.
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../app/static'),out=process.env.B1_EVIDENCE_DIR;
const outputs=['高考冲刺招生\n\n一起梳理复习节奏。\n\n欢迎了解。','画面：复习桌前\n\n台词：先找到自己的复习节奏。\n\n欢迎了解。','先找到自己的复习节奏。\n\n一起梳理复习安排。\n\n欢迎了解。','先找到自己的复习节奏。\n\n看看哪里卡住，再一步一步整理。\n\n欢迎了解。','越临近考试，越要稳住自己的节奏。\n\n看看哪里卡住，再一步一步整理。\n\n欢迎了解。'];
const prompts=['写一篇高考冲刺招生文案。','把它改成短视频拍摄脚本。','只要口播台词。','第二段不够自然，重新写一下。','换一个开头，其余保持不变。'];
(async()=>{
  if(out)fs.mkdirSync(path.join(out,'downloads'),{recursive:true});
  const browser=await chromium.launch({headless:true,...(out?{downloadsPath:path.join(out,'downloads')}:{}),...(process.env.B1_CHROME?{executablePath:process.env.B1_CHROME}:{channel:'chrome'})});
  let downloadServer;
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true,permissions:['clipboard-read','clipboard-write']});
    const page=await context.newPage(),errors=[],requests=[];let logged=false,role='member',platform=false;
    const agent={id:'copywriting-agent',slug:'copywriting',name:'文案创作智能体',description:'专业文案策划与编辑',icon:'type',enabled:true,credit_cost:3};
    const tasks=[],messages=[];
    // Chrome downloads bypass route fulfillment in some installed builds.
    // Serve only synthetic TXT bytes on loopback; every other route stays mocked.
    downloadServer=require('node:http').createServer((req,res)=>{
      const task=tasks.find(t=>req.url===`/api/v1/tasks/${t.id}/text`);
      if(!task){res.writeHead(404);res.end();return;}
      res.writeHead(200,{'Content-Type':'text/plain; charset=utf-8','Content-Disposition':'attachment; filename="copywriting.txt"'});res.end(task.final_response);
    });
    await new Promise((resolve,reject)=>{downloadServer.once('error',reject);downloadServer.listen(18766,'127.0.0.1',resolve);});
    const conversation=()=>({id:'b1-fixture-conversation',agent_id:agent.id,agent,title:'高考冲刺招生',project:{name:'高考冲刺招生'},messages,tasks,created_at:'2026-10-22T00:00:00Z',latest_status:'completed'});
    const me=()=>({user_id:'b1-fixture-user',tenant_id:'b1-fixture-tenant',tenant_name:'隔离 Fixture 企业',email:'b1@example.invalid',display_name:'B1 测试',role,is_platform_admin:platform});
    page.on('pageerror',e=>errors.push(e.message));
    page.on('requestfailed',req=>{if(req.url().startsWith('http://127.0.0.1:18766'))console.error('FIXTURE_REQUEST_FAILED',req.url(),req.failure());});
    await context.route('**/*',async route=>{
      const req=route.request(),url=new URL(req.url()),p=url.pathname;
      if(url.origin!=='http://127.0.0.1:18766')return route.abort();
      const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
      if(p==='/api/v1/auth/login'){logged=true;return json(me());}
      if(p.startsWith('/api/')&&!logged)return json({error_code:'AUTH_REQUIRED'},401);
      if(p==='/api/v1/me')return json(me());
      if(p==='/api/v1/workspace')return json({brand_name:'隔离 Fixture 企业',credit_balance:100,agents:[agent,{id:'image-agent',enabled:true}],customer_capabilities:[{...agent,runnable:true,state:'ready'},{id:'image-agent',state:'not_migrated',reason:'尚未开放'}],recent_conversations:tasks.length?[conversation()]:[]});
      if(p==='/api/v1/workspace/settings')return json({tenant_id:me().tenant_id,usage:{credit_balance:100,credits_spent:15,tasks:[{agent_id:agent.id,status:'completed',count:5}]},agents:[{...agent,state:'ready',reason:'已开通'}, {id:'image-agent',name:'图片生成',state:'not_migrated',reason:'尚未迁移'}]});
      if(p==='/api/v1/platform/tenants')return json([{id:'synthetic-a',name:'Fixture企业A',members:3},{id:'synthetic-b',name:'Fixture企业B',members:2}]);
      if(p==='/api/v1/enterprise-config')return json({brand_name:'隔离 Fixture 企业'});
      if(p==='/api/v1/members')return json([{id:'synthetic-member',display_name:'合成员工',email:'member@example.invalid',role:'member',status:'enabled',created_at:'2026-10-24T00:00:00Z'}]);
      if(p==='/api/v1/knowledge/files')return json([{id:'synthetic-file',name:'合成企业资料.md',status:'ready',mime_type:'text/markdown',size_bytes:128}]);
      if(p==='/api/v1/agents/copywriting-agent')return json(agent);
      if(p==='/api/v1/conversations')return json(tasks.length?[conversation()]:[]);
      if(p==='/api/v1/conversations/b1-fixture-conversation')return json(conversation());
      if(p==='/api/v1/tasks/orphans')return json({tasks:[],next_cursor:null});
      if(p==='/api/v1/agents/copywriting-agent/runs'){
        const payload=req.postDataJSON(),i=tasks.length;requests.push(payload);
        const id=`00000000-0000-4000-8000-${String(i+1).padStart(12,'0')}`;
        const task={id,agent_id:agent.id,conversation_id:'b1-fixture-conversation',status:'completed',stage:'completed',input_text:payload.message,final_response:outputs[i],assistant_message_id:`task:${id}:assistant`,created_at:'2026-10-22T00:00:00Z'};
        tasks.push(task);messages.push({id:`task:${id}:user`,role:'user',content:payload.message},{id:task.assistant_message_id,role:'assistant',content:task.final_response});return json(task,202);
      }
      if(p.startsWith('/api/v1/tasks/')){
        const task=tasks.find(t=>p.includes(t.id));
        if(p.endsWith('/text'))return route.continue();
        if(p.endsWith('/events'))return route.fulfill({contentType:'text/event-stream',body:`event: complete\ndata: ${JSON.stringify(task)}\n\n`});
        return json(task||{items:[]});
      }
      if(p.startsWith('/api/'))return json([]);
      if(p.startsWith('/static/')){
        const file=path.resolve(root,p.slice('/static/'.length));
        if(!file.startsWith(root+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:''});
        return route.fulfill({path:file});
      }
      return route.fulfill({path:path.join(root,'index.html'),contentType:'text/html'});
    });
    await page.goto('http://127.0.0.1:18766/workspace');
    await page.locator('input[name=account]').fill('b1@example.invalid');
    await page.locator('input[name=password]').fill('FixtureOnly!2026');
    await page.locator('#login button[type=submit]').click();
    await page.getByRole('heading',{name:'AI Workspace',exact:true}).waitFor();
    assert.equal(await page.locator('.sidebar [data-page=members]').count(),0);
    assert.equal(await page.locator('.sidebar [data-page=settings-home]').count(),0);
    await page.locator('[data-agent="copywriting-agent"]').click();
    await page.locator('#prompt').waitFor().catch(async error=>{console.error({url:page.url(),errors,body:await page.locator('#main').innerText()});throw error;});
    for(let i=0;i<prompts.length;i++){
      await page.locator('#prompt').fill(prompts[i]);await page.locator('#composer-submit').click();
      await page.locator(`[data-assistant-message-id="task:00000000-0000-4000-8000-${String(i+1).padStart(12,'0')}:assistant"] [data-edit-draft]`).waitFor({timeout:15000});
      assert.equal(requests[i].message,prompts[i]);assert.ok(requests[i].client_request_id);
      if(i)assert.equal(requests[i].conversation_id,'b1-fixture-conversation');
    }
    const last=page.locator('[data-copy-response]').last();await last.click();
    assert.equal((await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n'),outputs.at(-1));
    const downloadEvent=page.waitForEvent('download');await page.getByRole('link',{name:'导出 TXT'}).last().click();
    const download=await downloadEvent;assert.equal(fs.readFileSync(await download.path(),'utf8'),outputs.at(-1));
    await page.locator('[data-edit-draft]').last().click();assert.equal(await page.locator('#prompt').evaluate(n=>n===document.activeElement),true);
    await page.locator('#prompt').fill('待修改草稿\n'.repeat(25));
    const desktop=await page.locator('#prompt').boundingBox();assert.ok(desktop.height<=200&&desktop.height>100);
    if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,'b1-copywriting-desktop-FIXTURE.png'),fullPage:true});}
    await page.locator('.sidebar [data-page=workspace]').click();await page.getByRole('heading',{name:'AI Workspace',exact:true}).waitFor();
    await page.locator('[data-agent="copywriting-agent"]').click();await page.locator('#prompt').waitFor();
    assert.ok((await page.locator('#prompt').inputValue()).startsWith('待修改草稿'));
    await page.reload();await page.locator('[data-edit-draft]').last().waitFor();assert.equal(tasks.length,5);
    await page.setViewportSize({width:375,height:812});await page.locator('#prompt').fill('移动端长输入\n'.repeat(25));
    const mobile=await page.locator('#prompt').boundingBox();assert.ok(mobile.height<=160&&mobile.height>100);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
    if(out)await page.screenshot({path:path.join(out,'b1-copywriting-mobile-FIXTURE.png'),fullPage:true});
    role='enterprise_admin';await page.setViewportSize({width:1440,height:1000});await page.goto('http://127.0.0.1:18766/workspace/settings');
    await page.getByRole('heading',{name:'企业设置',exact:true}).waitFor();
    assert.equal(await page.locator('.sidebar [data-page=copywriting]').count(),1);
    assert.equal(await page.locator('.sidebar [data-page=platform-skills]').count(),0);
    assert.equal(await page.locator('.workspace-settings-nav [data-go=members]').count(),1);
    if(out)await page.screenshot({path:path.join(out,'b1-enterprise-settings-desktop-FIXTURE.png'),fullPage:true});
    await page.setViewportSize({width:375,height:812});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
    if(out)await page.screenshot({path:path.join(out,'b1-enterprise-settings-mobile-FIXTURE.png'),fullPage:true});
    for(const [section,heading] of [['members','成员管理'],['knowledge','企业知识库'],['enterprise','企业设置'],['assets','企业素材库']]){
      await page.locator(`.workspace-settings-nav [data-go=${section}]`).click();
      await page.getByRole('heading',{name:heading,exact:true}).waitFor().catch(async error=>{console.error({fixture:true,section,url:page.url(),errors,body:await page.locator('#main').innerText()});throw error;});
      assert.equal(await page.locator('.workspace-settings-nav').count(),1);
      if(out)await page.screenshot({path:path.join(out,`b1-settings-${section}-mobile-FIXTURE.png`),fullPage:true});
      const overflow=await page.evaluate(()=>[...document.querySelectorAll('#main *')].filter(n=>n.getBoundingClientRect().right>innerWidth+1).slice(0,8).map(n=>({tag:n.tagName,className:n.className,width:n.getBoundingClientRect().width,right:n.getBoundingClientRect().right})));
      assert.deepEqual(overflow,[],section+' mobile overflow');
    }
    await page.setViewportSize({width:1440,height:1000});role='member';platform=true;
    await page.goto('http://127.0.0.1:18766/manage');await page.getByRole('heading',{name:'Agent Workbench',exact:true}).waitFor();
    assert.equal(await page.locator('.sidebar [data-page=copywriting]').count(),0);
    assert.equal(await page.locator('.sidebar [data-page=platform-tenants]').count(),1);
    await page.locator('.sidebar [data-page=platform-tenants]').click();await page.getByRole('heading',{name:'企业与开通',exact:true}).waitFor();
    if(out)await page.screenshot({path:path.join(out,'b1-management-FIXTURE.png'),fullPage:true});
    logged=false;await page.goto('http://127.0.0.1:18766/workspace');
    await page.locator('#login input[name=account]').waitFor();
    assert.equal(await page.locator('.workspace-settings-nav').count(),0);
    assert.deepEqual(errors,[]);
    const result={fixture:true,provider_calls:0,turns:requests.length,copy:true,export:true,draft_restore:true,desktop_height:desktop.height,mobile_height:mobile.height,roles:true,expired_login:true,settings_sections:['members','knowledge','brand','assets'],mobile_overflow:false,errors};
    if(out)fs.writeFileSync(path.join(out,'browser-fixture-results.json'),JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify(result,null,2));
  }finally{await browser.close();if(downloadServer)await new Promise(resolve=>downloadServer.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
