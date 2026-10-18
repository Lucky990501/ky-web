// Actual running Preview API/SQLite/Chrome; no model or fabricated Task results.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.WECHAT_UI_BROWSER,headless:true});
 try{
 const origin=process.env.PREVIEW_TEST_ORIGIN||'http://127.0.0.1:18920',base=origin+'/preview',a=await browser.newContext(),b=await browser.newContext(),page=await a.newPage(),bad=[];page.setDefaultTimeout(12000);
 page.on('request',request=>{const u=new URL(request.url());if(u.origin===origin&&!u.pathname.startsWith('/preview/'))bad.push(u.pathname);});
 await a.addCookies([{name:'workbench_session',value:'Production-cookie-must-not-be-used',domain:new URL(origin).hostname,path:'/'}]);
 await page.goto(base+'/');await page.locator('.agent-greeting').waitFor();
 assert.equal(await page.locator('.sidebar .side-nav button').count(),7);assert.equal(await page.locator('.side-credit').isVisible(),false);
 const me=await page.evaluate(()=>fetch('/preview/api/v1/me').then(r=>r.json()));assert.ok(me.tenant_id.startsWith('preview-'));assert.equal(me.is_platform_admin,false);
 const p2=await b.newPage();await p2.goto(base+'/');await p2.locator('.agent-greeting').waitFor();
 const other=await p2.evaluate(()=>fetch('/preview/api/v1/me').then(r=>r.json()));assert.notEqual(me.tenant_id,other.tenant_id);
 assert.ok((await a.cookies()).some(cookie=>cookie.name==='workbench_session'&&cookie.value==='Production-cookie-must-not-be-used'));
 const workspace=await page.evaluate(()=>fetch('/preview/api/v1/workspace').then(r=>r.json()));assert.equal(workspace.agents.length,4);assert.ok(workspace.agents.every(agent=>agent.enabled));
 const saved=await page.evaluate(()=>fetch('/preview/api/v1/enterprise-config',{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({payload:{brand_name:'Preview验收企业A',description:'独立体验资料'}})}).then(r=>r.status));assert.equal(saved,200);
 assert.notEqual((await p2.evaluate(()=>fetch('/preview/api/v1/enterprise-config').then(r=>r.json()))).brand_name,'Preview验收企业A');
 await page.goto(base+'/knowledge');await page.locator('#preview-knowledge-file').waitFor();
 await page.locator('#preview-knowledge-file').setInputFiles({name:'Preview实际上传.md',mimeType:'text/markdown',buffer:Buffer.from('# 星河体验课程\n\n课程名称为天文观察课，周三晚上19点开始，地点为模拟教室。报名不承诺保过。此文件是隔离体验测试材料，并非真实客户资料。')});
 await page.waitForFunction(async()=>{const rows=await fetch('/preview/api/v1/knowledge/files').then(r=>r.json());return rows.length&&rows.every(item=>item.status==='ready');});
 await page.locator('.trow').filter({hasText:'Preview实际上传.md'}).waitFor();
 await page.locator('#preview-query').fill('天文观察课周三晚上19点');await page.locator('#preview-search').click();await page.waitForFunction(()=>document.querySelector('#preview-search-result')?.textContent);assert.match(await page.locator('#preview-search-result').innerText(),/天文观察课/);
 const files=await page.evaluate(()=>fetch('/preview/api/v1/knowledge/files').then(r=>r.json()));
 const peerFiles=await p2.evaluate(()=>fetch('/preview/api/v1/knowledge/files').then(r=>r.json()));assert.equal(peerFiles.length,0);
 const forbidden=await page.evaluate(()=>fetch('/preview/api/v1/platform/skills').then(r=>r.status));assert.equal(forbidden,403);
 await page.reload();await page.locator('#preview-knowledge-file').waitFor();assert.equal((await page.evaluate(()=>fetch('/preview/api/v1/me').then(r=>r.json()))).user_id,me.user_id);
 for(const width of [1440,1920,375]){await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
 assert.deepEqual(bad,[]);
 const report={mode:'ACTUAL_PREVIEW_HTTP_SQLITE_BROWSER_NO_PROVIDER',auto_session:true,private_tenants:true,production_cookie_untouched:true,agents:workspace.agents.map(x=>x.id),knowledge_files:files,enterprise_save:true,refresh_same_user:true,unprefixed_requests:bad,real_provider_calls:0,history_business_e2e:'PENDING_REAL_MODEL_TASK'};
 if(process.env.PREVIEW_TEST_EVIDENCE){fs.mkdirSync(process.env.PREVIEW_TEST_EVIDENCE,{recursive:true});fs.writeFileSync(process.env.PREVIEW_TEST_EVIDENCE+'/local-preview.json',JSON.stringify(report,null,2));await page.screenshot({path:process.env.PREVIEW_TEST_EVIDENCE+'/knowledge-375.png'});}
 console.log(JSON.stringify({pass:true,knowledge:files.map(x=>({id:x.file_id||x.id,status:x.status})),private_tenants:true,unprefixed_requests:0}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
