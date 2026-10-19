// Fixture and real public browser runs are explicitly distinguished.
// Real mode: one reference edit, one follow-up edit, one two-output task.
// No paid request is automatically retried.
const {chromium}=require('playwright'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const real=process.env.PREVIEW_CAPABILITY_MODE==='real',base=process.env.PREVIEW_CAPABILITY_BASE,out=process.env.PREVIEW_CAPABILITY_EVIDENCE;
 assert.ok(base&&out);fs.mkdirSync(out,{recursive:true});
 if(real){assert.equal(process.env.PREVIEW_CAPABILITY_APPROVAL,'owner-v2-four-image-requests');assert.ok(!fs.existsSync(path.join(out,'submission-guard.json')),'Never repeat the paid acceptance run');}
 const browser=await chromium.launch({executablePath:process.env.WECHAT_UI_BROWSER,headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:960},acceptDownloads:true});
 const page=await context.newPage(),report={mode:real?'REAL_PROVIDER':'FIXTURE_ONLY',tasks:[],errors:[]};
 page.setDefaultTimeout(20000);page.on('pageerror',e=>report.errors.push(e.message));
 page.on('dialog',dialog=>dialog.accept());
 let posts=0;page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.endsWith('/agents/image-agent/runs'))posts++;});
 const persist=()=>fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2));
 async function submit(prompt){
   await page.waitForFunction(()=>{const b=document.querySelector('#composer-submit');return b?.type==='submit'&&!b.disabled&&!document.querySelector('#task-status')});
   await page.locator('#prompt').fill(prompt);
   const accepted=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/agents/image-agent/runs'));
   await page.locator('#composer-submit').click();const response=await accepted;
   assert.equal(response.status(),202,await response.text());const task=await response.json();
   const entry={id:task.id,prompt};report.tasks.push(entry);persist();
   let result;
   for(let read=0;read<330;read++){
     result=await page.evaluate(id=>fetch('/preview/api/v1/tasks/'+id,{cache:'no-store'}).then(r=>r.json()),task.id);
     if(['completed','failed','cancelled'].includes(result.status))break;
     await new Promise(resolve=>setTimeout(resolve,2000));
   }
   entry.result=result;persist();assert.equal(result.status,'completed',JSON.stringify({error:result.error_code,message:result.user_message}));
   for(const image of result.generations){
     await page.locator(`[data-result-image="${image.id}"] img`).waitFor();
     await page.waitForFunction(id=>{const n=[...document.querySelectorAll('[data-result-image]')].find(x=>x.dataset.resultImage===id)?.querySelector('img');return n?.complete&&n.naturalWidth>0},image.id);
     const link=page.locator(`[data-result-image="${image.id}"] a[download]`),download=page.waitForEvent('download');
     await link.click();const item=await download;await item.saveAs(path.join(out,item.suggestedFilename()));
   }
   return result;
 }
 try{
   await page.goto(base+'/agents/image');await page.waitForFunction(()=>document.querySelector('#prompt')&&!document.querySelector('#prompt').disabled);
   report.before=await page.evaluate(()=>fetch('/preview/api/health').then(r=>r.json()));
   report.me=await page.evaluate(()=>fetch('/preview/api/v1/me').then(r=>r.json()));
   assert.equal(report.before.image_business.max_outputs,5);
   await page.locator('#chat-image-input').setInputFiles(process.env.PREVIEW_REFERENCE_FILE);
   await page.waitForFunction(()=>document.querySelector('#main')._chatImage?.status==='ready');
   await page.locator('[data-reference-role]').selectOption('subject');
   report.reference=await page.evaluate(()=>document.querySelector('#main')._chatImage.record);
   fs.writeFileSync(path.join(out,'submission-guard.json'),JSON.stringify({mode:report.mode,at:new Date().toISOString(),maximum_image_requests:4,tenant:report.me.tenant_id}));
   const first=await submit('根据这张人物原始照片创作1张易明教育教师招生海报，9:16竖版长图，标题“用心陪伴，启发成长”。保留原图人物五官、发型、黑色服装和姿势，不要换人，不要编造师资资质和承诺。不要Logo。');
   report.first=first;assert.equal(first.generations.length,1);
   await page.locator(`[data-preview-edit-image="${first.generations[0].id}"]`).click();
   const edited=await submit('只将标题改为“秋季招生”，保留人物五官、发型、黑色服装和姿势以及上一张海报的排版和9:16比例。不要Logo。');
   report.followup=edited;assert.equal(edited.generations[0].edit_parent_generation_id,first.generations[0].id);
   const multi=await submit('生成2张不同用途的1024x1024易明教育招生插画，正面是亲子阅读，背面是自主探索。每面单独一张，不要拼图，不要Logo。');
   report.multi=multi;assert.equal(multi.generations.length,2);
   await page.reload();for(const image of multi.generations)await page.locator(`[data-result-image="${image.id}"] img`).waitFor();
   report.history=true;
   for(const width of [375,1440,1920]){await page.setViewportSize({width,height:960});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'gallery-'+width+'.png')});}
   const peer=await browser.newContext();const other=await peer.newPage();await other.goto(base+'/agents/image');await other.locator('#prompt').waitFor();
   const target=first.generations[0];report.isolation=await other.evaluate(async({id,key})=>({task:(await fetch('/preview/api/v1/tasks/'+id)).status,image:(await fetch('/preview/api/v1/storage/'+key)).status}),{id:first.id,key:target.storage_key});
   assert.ok(Object.values(report.isolation).every(x=>[403,404].includes(x)));await peer.close();
   report.after=await page.evaluate(()=>fetch('/preview/api/health').then(r=>r.json()));report.run_posts=posts;assert.equal(posts,3);
   report.pass=true;persist();console.log(JSON.stringify({mode:report.mode,pass:true,tasks:report.tasks.map(x=>x.id),image_requests:report.after.budget.image.requests-report.before.budget.image.requests}));
 }catch(error){report.failure=String(error);await page.screenshot({path:path.join(out,'failure.png')}).catch(()=>{});persist();throw error;}
 finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1});
