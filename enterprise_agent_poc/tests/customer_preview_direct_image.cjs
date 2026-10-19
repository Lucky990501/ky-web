// Exactly one browser submission. Fixture and real modes have separate evidence.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
(async()=>{
 const real=process.env.PREVIEW_IMAGE_MODE==='real',out=process.env.PREVIEW_IMAGE_EVIDENCE,base=process.env.PREVIEW_IMAGE_BASE;
 assert.ok(out&&base);fs.mkdirSync(out,{recursive:true});
 if(real){assert.equal(process.env.PREVIEW_REAL_IMAGE_APPROVED,'owner-authorized-one-image-v1');assert.ok(!fs.existsSync(path.join(out,'submitted-once.json')),'Do not replay a real submission');}
 const browser=await chromium.launch({executablePath:process.env.WECHAT_UI_BROWSER,headless:true});let context;
 try{
 context=await browser.newContext({viewport:{width:1440,height:960},acceptDownloads:true});const page=await context.newPage();page.setDefaultTimeout(15000);
 const posts=[],bad=[],errors=[];page.on('request',r=>{const u=new URL(r.url());if(u.origin===new URL(base).origin&&!u.pathname.startsWith('/preview/'))bad.push(u.pathname);if(r.method()==='POST'&&u.pathname.endsWith('/agents/image-agent/runs'))posts.push(r.postDataJSON());});page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/agents/image');await page.locator('#preview-image-direct-notice').waitFor();await page.waitForFunction(()=>!document.querySelector('#prompt').disabled);
 const before=await page.evaluate(()=>fetch('/preview/api/health').then(r=>r.json()));assert.equal(before.image_business.enabled,true);
 const me=await page.evaluate(()=>fetch('/preview/api/v1/me').then(r=>r.json()));assert.ok(me.tenant_id.startsWith('preview-'));
 if(real)await context.storageState({path:path.join(out,'private-browser-state.json')});
 if(!real){const rejected=await context.request.post(base+'/api/v1/agents/image-agent/runs',{headers:{origin:'https://foreign.invalid'},data:{message:'不得执行',preview_image_confirmed:true,preview_image_request_id:crypto.randomUUID()}});assert.equal(rejected.status(),403);}
 const prompt='请生成1张1024x1024正方形图片：温暖的亲子阅读场景，家长与孩子在明亮安静的图书角共同阅读，柔和自然光，蓝色与暖黄色点缀，简洁可信的教育宣传插画。不要Logo，不写价格、网址或效果承诺。';
 await page.locator('#prompt').fill(prompt);
 // First cancel is zero cost and must retain the input without submitting.
 page.once('dialog',dialog=>dialog.dismiss());await page.locator('#composer-submit').click();assert.equal(posts.length,0);assert.equal(await page.locator('#prompt').inputValue(),prompt);
 fs.writeFileSync(path.join(out,'submitted-once.json'),JSON.stringify({mode:real?'REAL_PROVIDER_ONE_REQUEST':'FIXTURE_ONLY',at:new Date().toISOString(),tenant:me.tenant_id,prompt_sha256:crypto.createHash('sha256').update(prompt).digest('hex')},null,2));
 const acceptedPromise=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/agents/image-agent/runs'));
 page.once('dialog',dialog=>dialog.accept());await page.locator('#composer-submit').click();const accepted=await acceptedPromise;
 assert.equal(accepted.status(),202);const task=await accepted.json();assert.ok(task.id);
 fs.writeFileSync(path.join(out,'accepted-task.json'),JSON.stringify({id:task.id,tenant:me.tenant_id},null,2));
 let done;
 for(let read=0;read<90;read++){
   done=await page.evaluate(id=>fetch('/preview/api/v1/tasks/'+id,{cache:'no-store'}).then(r=>r.json()),task.id);
   if(['completed','failed','cancelled'].includes(done.status))break;
   await new Promise(resolve=>setTimeout(resolve,2000));
 }
 fs.writeFileSync(path.join(out,'task-result.json'),JSON.stringify(done,null,2));
 assert.equal(done.status,'completed',JSON.stringify({status:done.status,error:done.error_code,message:done.user_message}));assert.equal(done.generations.length,1);
 await page.locator('.message-image-gallery img').first().waitFor({timeout:20000});assert.ok(await page.locator('.message-image-gallery img').first().evaluate(n=>n.complete&&n.naturalWidth>0));
 const img=done.generations[0];await page.screenshot({path:path.join(out,'image-1440.png'),fullPage:false});
 const link=page.locator('.message-image-gallery [data-generation-link]').first();await link.click();await page.locator('#generation-viewer').waitFor();assert.ok(await page.locator('#generation-viewer img').evaluate(n=>n.complete&&n.naturalWidth>0));
 const downloadEvent=page.waitForEvent('download');await page.locator('#generation-viewer a[download]').click();const download=await downloadEvent;const downloaded=path.join(out,download.suggestedFilename()||'generated-image');await download.saveAs(downloaded);assert.ok(fs.statSync(downloaded).size>100);
 const assetPath=new URL(await page.locator('#generation-viewer img').getAttribute('src'),base).pathname;
 await page.locator('#generation-viewer .dialog-close').click();await page.reload();await page.locator('.message-image-gallery img').first().waitFor();
 assert.ok(await page.locator('.message-image-gallery img').first().evaluate(n=>n.complete&&n.naturalWidth>0));
 const current=await page.evaluate(()=>fetch('/preview/api/v1/me').then(r=>r.json()));assert.equal(current.user_id,me.user_id);
 for(const width of [1920,375]){await page.setViewportSize({width,height:960});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'image-'+width+'.png')});}
 const savedState=await context.storageState();await context.close();context=await browser.newContext({storageState:savedState});const reopened=await context.newPage();await reopened.goto(base+'/agents/image?conversation='+done.conversation_id);
 const history=await reopened.evaluate(id=>fetch('/preview/api/v1/conversations/'+id).then(r=>r.json()),done.conversation_id);assert.ok(history.generations.some(g=>g.task_id===task.id));
 const peer=await browser.newContext();const other=await peer.newPage();await other.goto(base+'/agents/image');await other.locator('.agent-greeting').waitFor();
 const denied=await other.evaluate(async({id,asset})=>({task:(await fetch('/preview/api/v1/tasks/'+id)).status,image:(await fetch(asset)).status}),{id:task.id,asset:assetPath});assert.ok([403,404].includes(denied.task));assert.ok([403,404].includes(denied.image));await peer.close();
 const after=await reopened.evaluate(()=>fetch('/preview/api/health').then(r=>r.json()));assert.equal(after.budget.image.requests-before.budget.image.requests,1);assert.equal(after.budget.text.requests,before.budget.text.requests);assert.equal(posts.length,1);assert.deepEqual(bad,[]);assert.deepEqual(errors,[]);
 const report={mode:real?'REAL_PROVIDER_PUBLIC_BROWSER':'FIXTURE_ONLY_HTTP_BROWSER',task:task.id,run:done.run_id,conversation:done.conversation_id,generation:img,tenant:me.tenant_id,downloaded_file:path.basename(downloaded),download_bytes:fs.statSync(downloaded).size,download_sha256:crypto.createHash('sha256').update(fs.readFileSync(downloaded)).digest('hex'),preview:true,large_preview:true,download:true,refresh:true,reopen_history:true,visitor_isolation:denied,submitted_posts:posts.length,confirmation_present:posts[0].preview_image_confirmed,budget_before:before.budget,budget_after:after.budget,unprefixed_requests:bad,page_errors:errors};
 fs.writeFileSync(path.join(out,'browser-result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await context?.close();await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
