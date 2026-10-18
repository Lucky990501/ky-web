// Public HTTPS read-only browser/security checks; no Provider calls.
const {chromium}=require('playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{const dir=process.env.PREVIEW_REAL_EVIDENCE,browser=await chromium.launchPersistentContext(dir+'/private-browser',{executablePath:process.env.WECHAT_UI_BROWSER,headless:true,viewport:{width:1440,height:960}});try{
 const page=await browser.newPage(),base='https://workbench.luckio.cn/preview',bad=[],errors=[];page.setDefaultTimeout(20000);
 page.on('request',r=>{const u=new URL(r.url());if(u.origin==='https://workbench.luckio.cn'&&!u.pathname.startsWith('/preview/'))bad.push(u.pathname)});page.on('pageerror',e=>errors.push(e.message));
 const article=JSON.parse(fs.readFileSync(dir+'/wechat.json')).final,copy=JSON.parse(fs.readFileSync(dir+'/cycle.json')).final;
 await page.goto(base+'/agents/preview-wechat-agent?conversation='+article.conversation_id+'&task='+article.id);await page.locator('iframe.wechat-inline-article').waitFor();
 const frame=await page.locator('iframe.wechat-inline-article').contentFrame();await frame.locator('h1').waitFor();assert.ok(await frame.locator('h1').count());assert.ok(!(await frame.locator('body').innerText()).includes('<div'));
 assert.equal(await page.locator('iframe.wechat-inline-article').getAttribute('sandbox'),'');
 const safe=await page.evaluate(()=>{const raw='<section id="article"><p>&lt;div id="article"&gt;&lt;h1&gt;测试标题&lt;/h1&gt;&lt;p onclick="alert(1)"&gt;正文&lt;/p&gt;&lt;script&gt;window.PREVIEW_XSS=1&lt;/script&gt;&lt;/div&gt;</p></section>';return WorkbenchWechatArticle.buildManualExport(raw,()=>'',{}).html;});assert.ok(safe.includes('<h1'));assert.ok(!/script|onclick|window.PREVIEW_XSS/.test(safe));
 await page.screenshot({path:dir+'/wechat-safe-rendered.png'});
 await page.goto(base+'/agents/copywriting?conversation='+copy.conversation_id+'&task='+copy.id);await page.locator('#chat-body').getByText(copy.final_response,{exact:true}).waitFor();
 const own=await page.evaluate(()=>fetch('/preview/api/v1/me').then(r=>r.json()));assert.equal(own.user_id,copy.user_id);
 const peer=await browser.browser().newContext(),p2=await peer.newPage();await p2.goto(base+'/');await p2.locator('#prompt').waitFor();
 for(const path of ['/api/v1/tasks/'+copy.id,'/api/v1/conversations/'+copy.conversation_id,'/api/v1/enterprise-config/brand-logo'])assert.equal(await p2.evaluate(path=>fetch('/preview'+path).then(r=>r.status),path),404);
 await peer.close();
 for(const route of ['agents/image','agents/copywriting','agents/campaign','agents/preview-wechat-agent','knowledge','enterprise-config','conversations']){
  await page.goto(base+'/'+route);await page.locator('#main').waitFor();await page.waitForFunction(()=>document.querySelector('#main')?.textContent.length>80);
  for(const width of [375,1440,1920]){await page.setViewportSize({width,height:960});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
 }
 await page.goto(base+'/enterprise-config');await page.locator('.brand-logo-preview img').waitFor();await page.waitForFunction(()=>document.querySelector('.brand-logo-preview img')?.naturalWidth>0);assert.equal(await page.locator('[name=brand_name]').inputValue(),'星河体验教育（更新版）');
 assert.deepEqual(bad,[]);assert.deepEqual(errors,[]);
 fs.writeFileSync(dir+'/public-browser-safety.json',JSON.stringify({public_https:true,no_login:true,reopened_own_history:true,cross_visitor_denied:true,safe_render:true,xss_blocked:true,seven_routes:true,widths:[375,1440,1920],production_api_requests:bad,page_errors:errors}));console.log('PUBLIC_BROWSER_SAFETY_PASS');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
