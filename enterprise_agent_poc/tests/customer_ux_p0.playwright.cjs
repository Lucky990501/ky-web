// Isolated browser fixtures ONLY. Not real PREPARE/WeChat/Provider evidence.
const assert=require('node:assert/strict');
const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const staticDir=path.resolve(__dirname,'../app/static');
const slug='wechat-official-account-writing';
const agent={id:'fixture-wechat-agent',slug,name:'公众号运营助手',icon:'file-text',description:'生成公众号文章',category:'内容创作',enabled:true};
const hostile='<section style="color:#315fe5;padding:12px;background-image:url(https://untrusted.invalid/a)"><h1 onclick="top.__injected=1">可信文章标题</h1><p>第一段正文。</p><blockquote>业务要点</blockquote><script>top.__injected=1;fetch("/api/v1/me")</script><img src="https://untrusted.invalid/a" onerror="top.__injected=1"><iframe src="/profile"></iframe><svg onload="top.__injected=1"><foreignObject>危险内容</foreignObject></svg><a href="javascript:top.__injected=1">链接文字</a><form action="/api/v1/me"><input autofocus></form><style>@import url(https://untrusted.invalid/a)</style><meta http-equiv="refresh" content="0;url=/profile"></section>';
async function main(){
  const evidence=process.env.CUSTOMER_UX_EVIDENCE;
  if(evidence)fs.mkdirSync(evidence,{recursive:true});
  const server=http.createServer((request,response)=>{
    const pathname=new URL(request.url,'http://localhost').pathname;
    const file=pathname.startsWith('/static/')?path.resolve(staticDir,pathname.slice(8)):path.join(staticDir,'index.html');
    if(!file.startsWith(staticDir+path.sep)||!fs.existsSync(file)){response.writeHead(404).end();return;}
    response.writeHead(200,{'content-type':file.endsWith('.css')?'text/css':file.endsWith('.js')?'application/javascript':'text/html'});response.end(fs.readFileSync(file));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  let browser;
  const result={test_mode:'ISOLATED_BROWSER_AND_ADAPTER_FIXTURES',base_source:'28e061a114118a27609330125c756e193d3248a6',checks:[],external_calls:0,provider_calls:0,wechat_calls:0,real_create_draft:false};
  try{
    browser=await chromium.launch({headless:true,...(process.env.WECHAT_UI_BROWSER?{executablePath:process.env.WECHAT_UI_BROWSER}:{})});
    const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();page.setDefaultTimeout(8000);
    let metadataStatus=200,account={app_secret_configured:true,verification_status:'connected'},role='member',content=hostile,profileSaves=0;
    const errors=[],mutations=[];let externalRequests=0,blockedFontRequests=0;
    page.on('pageerror',error=>errors.push(error.message));
    await context.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.origin!==origin){if(url.hostname==='fonts.googleapis.com')blockedFontRequests++;else externalRequests++;return route.abort();}
      if(!url.pathname.startsWith('/api/'))return route.continue();
      const json=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
      if(request.method()!=='GET')mutations.push({method:request.method(),path:url.pathname});
      if(url.pathname==='/api/v1/me'){
        if(request.method()==='PUT'){profileSaves++;const payload=JSON.parse(request.postData());assert.equal(payload.role,undefined);assert.equal(payload.tenant_id,undefined);}
        return json({user_id:'fixture-member',tenant_id:'fixture-tenant',role,display_name:'验收用户',email:'fixture@example.invalid'});
      }
      if(url.pathname==='/api/v1/workspace')return json({agents:[agent],credit_balance:100,tenant_name:'隔离测试企业'});
      if(url.pathname===`/api/v1/agents/${slug}`)return metadataStatus===200?json(agent):json({user_message:'该智能体暂不可用。'},metadataStatus);
      if(url.pathname==='/api/v1/profile/wechat-account')return json(account);
      if(url.pathname==='/api/v1/conversations')return json([{id:'fixture-conversation',agent_id:agent.id,agent,project:{name:'公众号文章',type:'内容创作'},latest_status:'completed'}]);
      if(url.pathname==='/api/v1/conversations/fixture-conversation')return json({id:'fixture-conversation',agent_id:agent.id,agent,project:{name:'公众号文章'},messages:[{id:'fixture-user-message',role:'user',content:'写一篇公众号文章'},{id:'fixture-message',role:'assistant',content,created_at:'2026-10-10T00:00:00Z'}],tasks:[]});
      if(url.pathname===`/api/v1/agents/${slug}/runs`)return json({id:'fixture-task',conversation_id:'fixture-conversation',created_at:'2026-10-10T00:00:00Z'});
      if(url.pathname==='/api/v1/tasks/fixture-task/events')return route.fulfill({contentType:'text/event-stream',body:'event: delta\ndata: '+JSON.stringify({sequence:1,text:'# 新生成文章\n\n正文内容。'})+'\n\nevent: complete\ndata: '+JSON.stringify({status:'completed',final_response:'# 新生成文章\n\n正文内容。',assistant_message_id:'fixture-stream-message',conversation_id:'fixture-conversation'})+'\n\n'});
      return json({error_code:'FIXTURE_ROUTE_UNAVAILABLE'},404);
    });
    const check=async(name,fn)=>{console.log('CHECK '+name);await fn();result.checks.push({name,status:'PASS',observed_at:new Date().toISOString()});};
    const screenshot=async(name)=>{if(evidence)await page.screenshot({path:path.join(evidence,name+'.png'),fullPage:false});};
    const noOverflow=()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
    const feedback=()=>page.locator('.wechat-article-feedback');
    const draft=()=>page.locator('[data-create-draft]');
    const openArticle=async()=>{
      await page.goto(`${origin}/agents/${slug}`);await page.locator('[data-select-agent-conversation]').waitFor();
      await page.locator('[data-select-agent-conversation]').click();await feedback().waitFor();await page.waitForFunction(()=>!document.querySelector('.wechat-article-feedback')?.textContent.includes('正在读取'));
    };
    const installAdapter=async(mode='success',permission='READY')=>{
      await page.evaluate(({mode,permission})=>{
        const main=document.querySelector('#main'),article=main.querySelector('[data-assistant-message-id="fixture-message"]');article.querySelector('.wechat-article-actions')?.remove();
        window.fixtureDraft={calls:0,checks:0,mode,permission,release:null};
        const adapter={
          availability:async context=>{window.fixtureDraft.checks++;return {state:window.fixtureDraft.permission,can_create_draft:true,agent_id:context.agentId,message_id:context.messageId};},
          createDraft:async context=>{
            const state=window.fixtureDraft;state.calls++;if(context.userConfirmed!==true)throw Error('confirmation missing');
            if(state.mode==='delay')await new Promise(resolve=>{state.release=resolve;});
            if(state.mode==='error')throw {status:503,code:'REQUEST_FAILED',message:'PRIVATE_ERROR_MUST_NOT_RENDER'};
            if(state.mode==='forbidden')throw {status:403,code:'FORBIDDEN'};
            const result={status:'created',confirmed:true,draft_media_id:'FIXTURE_MEDIA_ONLY',receipt_id:'FIXTURE_RECEIPT_ONLY',agent_id:context.agentId,message_id:context.messageId};
            if(state.mode==='unconfirmed')result.confirmed=false;
            if(state.mode==='wrong-message')result.message_id='other-message';
            if(state.mode==='unsafe-id')result.draft_media_id='<script>alert(1)</script>';
            return result;
          },
        };
        WorkbenchWechatArticle.mount({main,article,agent:main._wechatArticleContext.agent,route:main._wechatArticleContext.route,messageId:'fixture-message',content:'<h1>合成文章</h1><p>隔离接口测试</p>',request:api,markdown:markdownHtml,modal:bindModalDialog,adapter});
      },{mode,permission});
      await page.waitForFunction(()=>!document.querySelector('.wechat-article-feedback')?.textContent.includes('正在读取'));
    };
    const confirmDraft=async()=>{await draft().click();await page.locator('[data-draft-confirm]').waitFor();await page.locator('[data-draft-confirm]').click();};
    await check('UI-05 Escape closes and restores trigger',async()=>{
      await page.goto(`${origin}/profile`);await page.locator('#edit-profile').click();await page.locator('#profile-editor').waitFor();
      assert.equal(await page.locator('#profile-form').getAttribute('aria-modal'),'true');await page.keyboard.press('Escape');assert.equal(await page.locator('#profile-editor').count(),0);assert.equal(await page.locator('#edit-profile').evaluate(node=>node===document.activeElement),true);
    });
    await check('UI-05 Tab/Shift-Tab trap and inert background',async()=>{
      await page.locator('#edit-profile').click();
      for(const key of ['Tab','Shift+Tab'])for(let i=0;i<20;i++){await page.keyboard.press(key);assert.equal(await page.evaluate(()=>document.querySelector('#profile-editor').contains(document.activeElement)),true);}
      assert.equal(await page.locator('#main').evaluate(node=>Boolean(node.closest('[inert]'))),true);
      await page.evaluate(()=>document.querySelector('#edit-profile').focus());assert.equal(await page.evaluate(()=>document.querySelector('#profile-editor').contains(document.activeElement)),true);
      await screenshot('profile-focus-1440');await page.locator('[data-close-editor]').click();assert.equal(await page.locator('#main').evaluate(node=>node.inert),false);assert.equal(await page.locator('#edit-profile').evaluate(node=>node===document.activeElement),true);
    });
    await check('UI-05 mask closes without click-through and preserves prior inert state',async()=>{
      await page.evaluate(()=>document.querySelector('.skip').inert=true);await page.locator('#edit-profile').click();await page.mouse.click(8,8);assert.equal(await page.locator('#profile-editor').count(),0);assert.equal(profileSaves,0);assert.equal(await page.locator('.skip').evaluate(node=>node.inert),true);await page.evaluate(()=>document.querySelector('.skip').inert=false);
    });
    await check('adjacent password/help dialog Escape and focus unchanged',async()=>{
      await page.locator('#change-password').click();await page.keyboard.press('Escape');assert.equal(await page.locator('#password-editor').count(),0);assert.equal(await page.locator('#change-password').evaluate(node=>node===document.activeElement),true);
      await page.locator('#top-help').click();await page.keyboard.press('Escape');assert.equal(await page.locator('#help-dialog').count(),0);assert.equal(await page.locator('#top-help').evaluate(node=>node===document.activeElement),true);
      await page.evaluate(()=>{const trigger=document.querySelector('#edit-profile');trigger.focus();openGenerationViewer('/static/images/site-logo.png','相邻大图测试',trigger);});await page.keyboard.press('Escape');assert.equal(await page.locator('#generation-viewer').count(),0);assert.equal(await page.locator('#edit-profile').evaluate(node=>node===document.activeElement),true);
    });
    await check('profile 375px and normal form save use original API',async()=>{
      await page.setViewportSize({width:375,height:812});await page.locator('#edit-profile').click();assert.equal(await noOverflow(),true);await screenshot('profile-focus-375');await page.locator('[name="display_name"]').fill('验收用户');await page.getByRole('button',{name:'保存资料',exact:true}).click();await page.locator('#edit-profile').waitFor();assert.equal(profileSaves,1);assert.equal(await page.locator('#main').evaluate(node=>node.inert),false);assert.equal(await page.locator('#edit-profile').evaluate(node=>node===document.activeElement),true);await page.setViewportSize({width:1440,height:1000});
    });
    await check('WX-05 persisted reply preview entry and backend-pending button',async()=>{
      await openArticle();assert.equal(await draft().isDisabled(),true);assert.match(await feedback().textContent(),/BACKEND_PENDING/);assert.equal(await page.locator('[data-article-preview]').count(),1);await screenshot('article-pending-1440');
    });
    await check('WX-05 HTML XSS stripped and sandbox cannot access host',async()=>{
      await page.locator('[data-article-preview]').click();const iframe=page.locator('#wechat-article-preview iframe');assert.equal(await iframe.getAttribute('sandbox'),'');assert.equal(await iframe.getAttribute('referrerpolicy'),'no-referrer');
      const frame=await (await iframe.elementHandle()).contentFrame();await frame.waitForSelector('h1');
      assert.equal(await frame.locator('h1').textContent(),'可信文章标题');assert.equal(await frame.locator('script,img,iframe,svg,form,input,a,style:not(head style)').count(),0);
      assert.equal(await frame.locator('[onclick],[onerror],[onload],[href],[src]').count(),0);
      assert.equal(await frame.evaluate(()=>{try{void parent.document;return false;}catch{return true;}}),true);
      assert.equal(await page.evaluate(()=>window.__injected),undefined);assert.equal(externalRequests,0);
      assert.equal(await frame.locator('section').evaluate(node=>node.style.color),'rgb(49, 95, 229)');
      assert.equal(await frame.locator('section').evaluate(node=>node.style.backgroundImage),'');
      await screenshot('article-preview-1440');await page.locator('[data-preview-close]').click();assert.equal(await page.locator('[data-article-preview]').evaluate(node=>node===document.activeElement),true);
    });
    await check('WX-05 malformed/namespace/CSS/URL XSS corpus',async()=>{
      const payloads=['<math><mtext><img src=x onerror=alert(1)></mtext></math><p>ok</p>','<svg><foreignObject><iframe srcdoc="<script>alert(1)</script>"></iframe></foreignObject></svg><p>ok</p>','<a href="java&#x73;cript:alert(1)" target="_top">ok</a>','<div style="background:url(/api/v1/me);position:fixed;inset:0"><p>ok</p></div>','<p style="color:red;--secret:url(x);font-size:var(--secret)">ok</p>','<template><script>alert(1)</script></template><p id="app" name="constructor" onclick="alert(1)">ok</p>'];
      const cleaned=await page.evaluate(values=>values.map(value=>WorkbenchWechatArticle.sanitizeArticle(value)),payloads);
      for(const value of cleaned){assert.ok(!/<(?:script|iframe|img|svg|math|template)\b/i.test(value));assert.ok(!/\b(?:href|src|id|name|onclick)=/i.test(value));assert.ok(!/url\(|position:|var\(|--secret/.test(value));}
      assert.ok(cleaned.every(value=>value.includes('ok')));
      assert.equal(await page.evaluate(()=>{try{WorkbenchWechatArticle.sanitizeArticle('x'.repeat(262145));return false;}catch{return true;}}),true);
    });
    for(const size of [{width:1440,height:1000},{width:1920,height:1080},{width:375,height:812},{width:812,height:375}])await check(`WX-05 preview and fixed layout ${size.width}x${size.height}`,async()=>{
      await page.setViewportSize(size);assert.equal(await noOverflow(),true);await page.locator('[data-article-preview]').click();assert.equal(await noOverflow(),true);
      const frame=await (await page.locator('#wechat-article-preview iframe').elementHandle()).contentFrame();await frame.waitForSelector('h1');
      const panel=await page.locator('.wechat-preview-dialog').boundingBox();assert.ok(panel.width<=size.width);assert.ok(panel.height<=size.height);assert.ok(panel.y>=0&&panel.y+panel.height<=size.height);
      await screenshot('article-preview-'+size.width+'x'+size.height);await page.locator('[data-preview-close]').click();
    });
    await page.setViewportSize({width:1440,height:1000});
    await check('long article scrolls inside sandbox without growing modal',async()=>{
      content='<h1>长文章</h1>'+Array.from({length:80},(_,index)=>`<p>第${index+1}段，清晰可信的业务文章正文。</p>`).join('');await openArticle();await page.locator('[data-article-preview]').click();
      const frame=await (await page.locator('#wechat-article-preview iframe').elementHandle()).contentFrame();await frame.waitForSelector('h1');assert.equal(await frame.evaluate(()=>document.documentElement.scrollHeight>innerHeight),true);assert.ok((await page.locator('.wechat-preview-dialog').boundingBox()).height<=1000);await page.locator('[data-preview-close]').click();content=hostile;
    });
    await check('metadata 404 does not fake publication or run controls',async()=>{
      metadataStatus=404;await page.evaluate(reference=>{activeConversationId=null;return navigate('agent:'+reference);},slug);await page.getByText('该智能体暂未启用。',{exact:true}).waitFor();assert.equal(await page.locator('#composer').count(),0);assert.equal(await draft().count(),0);metadataStatus=200;
    });
    await check('missing config and unverified connection are explicit',async()=>{
      account={app_secret_configured:false,verification_status:'unconfigured'};await openArticle();assert.equal(await draft().isDisabled(),true);assert.match(await feedback().textContent(),/个人中心配置/);
      account={app_secret_configured:true,verification_status:'unverified'};await openArticle();assert.equal(await draft().isDisabled(),true);assert.match(await feedback().textContent(),/真实连接验证/);assert.equal(await page.getByRole('button',{name:'去配置',exact:true}).count(),1);
      account={app_secret_configured:true,verification_status:'connected'};await openArticle();
    });
    await check('mock permission denied does not expose submit capability',async()=>{
      await installAdapter('success','DENIED');assert.equal(await draft().isDisabled(),true);assert.match(await feedback().textContent(),/操作权限/);assert.equal(await page.evaluate(()=>fixtureDraft.calls),0);
    });
    await check('mock confirmation cancel and Escape cause zero draft requests',async()=>{
      await installAdapter();await draft().click();await page.locator('[data-draft-confirm]').waitFor();assert.equal(await page.evaluate(()=>fixtureDraft.calls),0);await page.locator('[data-draft-cancel]').click();
      await draft().click();await page.locator('[data-draft-confirm]').waitFor();await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>fixtureDraft.calls),0);assert.equal(await draft().evaluate(node=>node===document.activeElement),true);
    });
    await check('mock duplicate submit locked; confirmed receipt only succeeds once',async()=>{
      await installAdapter('delay');await confirmDraft();await page.waitForFunction(()=>fixtureDraft.calls===1);assert.equal(await draft().isDisabled(),true);
      await page.evaluate(()=>document.querySelector('[data-create-draft]').click());assert.equal(await page.evaluate(()=>fixtureDraft.calls),1);
      await page.evaluate(()=>fixtureDraft.release());await page.waitForFunction(()=>document.querySelector('.wechat-article-feedback').textContent.includes('微信草稿已创建'));
      assert.match(await feedback().textContent(),/FIXTURE_MEDIA_ONLY/);assert.equal(await draft().isDisabled(),true);assert.equal(await page.evaluate(()=>fixtureDraft.calls),1);assert.ok(await page.evaluate(()=>fixtureDraft.checks>=3));
    });
    await check('mock failure is sanitized and manual retry requires confirmation',async()=>{
      await installAdapter('error');await confirmDraft();await page.waitForFunction(()=>document.querySelector('.wechat-article-feedback').textContent.includes('创建草稿失败'));
      assert.ok(!(await page.content()).includes('PRIVATE_ERROR_MUST_NOT_RENDER'));assert.equal(await page.evaluate(()=>fixtureDraft.calls),1);
      await page.evaluate(()=>fixtureDraft.mode='success');await draft().click();await page.locator('[data-draft-confirm]').waitFor();assert.equal(await page.evaluate(()=>fixtureDraft.calls),1);await page.locator('[data-draft-confirm]').click();await page.waitForFunction(()=>document.querySelector('.wechat-article-feedback').textContent.includes('微信草稿已创建'));assert.equal(await page.evaluate(()=>fixtureDraft.calls),2);
    });
    for(const mode of ['unconfirmed','wrong-message','unsafe-id'])await check('mock '+mode+' receipt cannot show success',async()=>{
      await installAdapter(mode);await confirmDraft();await page.waitForFunction(()=>document.querySelector('.wechat-article-feedback').textContent.includes('结果未确认'));assert.ok(!(await feedback().textContent()).includes('微信草稿已创建'));
    });
    await check('mock API403 locks action and refreshed metadata404 prevents submit',async()=>{
      await installAdapter('forbidden');await confirmDraft();await page.waitForFunction(()=>document.querySelector('.wechat-article-feedback').textContent.includes('操作权限'));assert.equal(await draft().isDisabled(),true);
      await installAdapter();metadataStatus=404;await draft().click();await page.waitForFunction(()=>document.querySelector('.wechat-article-feedback').textContent.includes('暂未启用'));assert.equal(await page.evaluate(()=>fixtureDraft.calls),0);metadataStatus=200;
    });
    await check('late mock draft callback cannot mutate another view',async()=>{
      await installAdapter('delay');await confirmDraft();await page.waitForFunction(()=>fixtureDraft.calls===1);await page.evaluate(()=>navigate('profile'));await page.locator('#edit-profile').waitFor();await page.evaluate(()=>fixtureDraft.release());await page.waitForTimeout(100);assert.equal(await page.locator('.wechat-article-actions').count(),0);assert.equal(await page.locator('#edit-profile').count(),1);
    });
    await check('completed SSE reply mounts real-content entry without extra action request',async()=>{
      await page.goto(`${origin}/agents/${slug}`);await page.locator('#prompt').fill('写一篇公众号文章');await page.locator('#composer-submit').click();await page.locator('[data-assistant-message-id="fixture-stream-message"] [data-article-preview]').waitFor();
      assert.equal(await page.locator('[data-create-draft]').isDisabled(),true);assert.equal(mutations.filter(item=>item.path.endsWith('/runs')).length,1);
      await page.locator('[data-article-preview]').click();const frame=await (await page.locator('#wechat-article-preview iframe').elementHandle()).contentFrame();await frame.waitForSelector('h1');assert.equal(await frame.locator('h1').textContent(),'新生成文章');await page.locator('[data-preview-close]').click();
    });
    await check('all transport is isolated; no credentials, permissions writes or real calls',async()=>{
      assert.equal(externalRequests,0);assert.deepEqual(errors,[]);assert.ok(mutations.every(item=>item.path==='/api/v1/me'||item.path===`/api/v1/agents/${slug}/runs`));
      assert.equal(await page.evaluate(()=>JSON.stringify(localStorage).includes('FIXTURE_MEDIA_ONLY')||JSON.stringify(sessionStorage).includes('FIXTURE_MEDIA_ONLY')),false);
    });
    result.completed_at=new Date().toISOString();result.total=result.checks.length;result.failed=0;result.fixture_mutations=mutations;result.blocked_font_requests=blockedFontRequests;result.result='PASS_AUTOMATED_ONLY';
    if(evidence)fs.writeFileSync(path.join(evidence,'browser-result.json'),JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify(result,null,2));
  }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
