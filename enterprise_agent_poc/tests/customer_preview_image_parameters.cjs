// Real mode is exactly two Owner-approved requests at most; zero paid retries.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
(async()=>{
 const real=process.env.PREVIEW_PARAMETER_MODE==='real',base=process.env.PREVIEW_PARAMETER_BASE,out=process.env.PREVIEW_PARAMETER_EVIDENCE,fixtures=process.env.PREVIEW_PARAMETER_FIXTURES;
 assert.ok(base&&out&&fixtures);fs.mkdirSync(out,{recursive:true});
 if(real){assert.equal(process.env.PREVIEW_PARAMETER_APPROVAL,'owner-two-image-calls-20261019');assert.ok(!fs.existsSync(path.join(out,'submission-guard.json')),'Never rerun a paid acceptance after submission');}
 const browser=await chromium.launch({executablePath:process.env.WECHAT_UI_BROWSER,headless:true}),context=await browser.newContext({viewport:{width:1440,height:960},acceptDownloads:true}),page=await context.newPage();page.setDefaultTimeout(20000);
 const report={mode:real?'REAL_PROVIDER_PUBLIC_BROWSER':'FIXTURE_ONLY_HTTP_BROWSER',cases:[],errors:[],unprefixed:[]};let posts=0;
 page.on('pageerror',e=>report.errors.push(e.message));page.on('request',r=>{const p=new URL(r.url()).pathname;if(p.startsWith('/api/'))report.unprefixed.push(p);if(r.method()==='POST'&&p.endsWith('/agents/image-agent/runs'))posts++;});
 page.on('dialog',d=>d.accept());
 const persist=()=>fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2));
 try{
  await page.goto(base+'/agents/image');await page.locator('#preview-image-size option[value="2048x2048"]').waitFor({state:'attached'});
  report.before=await page.evaluate(()=>fetch('/preview/api/health').then(r=>r.json()));
  const approved=[{refs:2,size:'2048x2048',quality:'high'},{refs:0,size:'3840x2160',quality:'high'}];
  const chosen=process.env.PREVIEW_PARAMETER_CASE;
  const scenarios=real?(chosen==='multi'?[approved[0]]:chosen==='4k'?[approved[1]]:approved):[{refs:2,size:'2048x2048',quality:'high'},{refs:3,size:'2160x3840',quality:'medium'},{refs:5,size:'1024x1024',quality:'low'}];
  if(real)assert.ok(report.before.image_business.remaining_requests>=scenarios.length);
  for(const [index,scenario] of scenarios.entries()){
   if(index){await page.locator('#new-chat').click();await page.locator('#preview-image-size').waitFor();}
   if(scenario.refs){
    await page.locator('#chat-image-input').setInputFiles(Array.from({length:scenario.refs},(_,i)=>path.join(fixtures,i%2?'circle.png':'square.png')));
    await page.waitForFunction(count=>document.querySelector('#main')?._chatImage?.items.length===count&&document.querySelector('#main')._chatImage.status==='ready',scenario.refs);
    await page.locator('[data-reference-role]').first().selectOption('style');
    if(scenario.refs>1){await page.locator('[data-reference-role]').nth(1).selectOption('composition');await page.locator('[data-reference-down]').first().click();}
   }
   const order=await page.evaluate(()=>document.querySelector('#main')._chatImage.items.map(item=>({id:item.record.id,role:item.role})));
   await page.locator('#preview-image-size').selectOption(scenario.size);await page.locator('#preview-image-quality').selectOption(scenario.quality);
   const prompt=scenario.refs?'生成1张教育活动视觉概念图，仅参考本次上传的蓝色方形、暖黄色圆形及配色，布局简洁、有留白、明亮自然。不要Logo、人物、文字、数字或网址。':'生成1张明亮简洁的自然风景横幅：远处青绿色群山、平静湖面与暖色晨光，留白充足，可信、自然、适合教育品牌背景。不要Logo、人物、文字、数字或网址。';
   await page.locator('#prompt').fill(prompt);
   if(real)fs.writeFileSync(path.join(out,'submission-guard.json'),JSON.stringify({accepted_cases:report.cases.map(x=>x.task),next:index,maximum_requests:2,no_automatic_retry:true}));
   const accepted=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/agents/image-agent/runs'));
   await page.locator('#composer-submit').click();const response=await accepted;
   const submitted=response.request().postDataJSON();assert.equal(response.status(),202,await response.text());
   assert.equal(submitted.size,scenario.size);assert.equal(submitted.quality,scenario.quality);assert.deepEqual((submitted.attachments||[]).map(x=>({id:x.id,role:x.role})),order);
   const task=await response.json(),entry={...scenario,task:task.id,ordered_references:order,source:report.before.source,submitted_once:true};report.cases.push(entry);persist();
   let done;for(let read=0;read<330;read++){
    done=await page.evaluate(id=>fetch('/preview/api/v1/tasks/'+id,{cache:'no-store'}).then(r=>r.json()),task.id);
    if(['completed','failed','cancelled'].includes(done.status))break;
    await page.waitForTimeout(2000);
   }
   entry.task_result=done;persist();assert.equal(done.status,'completed',JSON.stringify({code:done.error_code,message:done.user_message}));assert.equal(done.generations.length,1);
   const image=done.generations[0],card=page.locator(`[data-result-image="${image.id}"]`);await card.waitFor();
   await page.waitForFunction(id=>{const image=document.querySelector(`[data-result-image="${id}"] img`);return image?.complete&&image.naturalWidth>0;},image.id);
   entry.actual_size=image.width+'x'+image.height;entry.native_size_match=entry.actual_size===scenario.size;
   await card.locator('[data-open-message-generation]').click();await page.locator('#generation-viewer img').waitFor();await page.locator('#generation-viewer .dialog-close').click();entry.large_preview=true;
   const download=page.waitForEvent('download');await card.locator('a[download]').click();const file=await download;const filename=path.join(out,'case-'+index+'-'+file.suggestedFilename());await file.saveAs(filename);
   entry.download={filename:path.basename(filename),bytes:fs.statSync(filename).size,sha256:crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex')};assert.ok(entry.download.bytes>100);
   await page.reload();await page.locator(`[data-result-image="${image.id}"]`).waitFor();
   const history=await page.evaluate(id=>fetch('/preview/api/v1/conversations/'+id).then(r=>r.json()),done.conversation_id);
   entry.history_restored=history.generations.some(g=>g.id===image.id);assert.ok(entry.history_restored);
   const user=history.messages.find(m=>m.role==='user'&&m.task_id===task.id);assert.deepEqual((user?.attachments||[]).map(x=>({id:x.id,role:x.role})),order);
   if(order.length){const forbiddenDelete=await context.request.delete(base+'/api/v1/chat-images/'+order.at(-1).id);assert.equal(forbiddenDelete.status(),409);entry.bound_upload_delete_rejected=true;}
   const peer=await browser.newContext();await peer.request.get(base+'/agents/image');
   const denied=await peer.request.get(base+'/api/v1/tasks/'+task.id);assert.ok([403,404].includes(denied.status()));await peer.close();entry.other_visitor_denied=true;
   for(const width of [375,1440,1920]){await page.setViewportSize({width,height:960});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,`case-${index}-${width}.png`)});}
   persist();assert.ok(entry.native_size_match,'Provider actual pixels differ: '+entry.actual_size+' requested '+scenario.size+'; no resize/upscale or retry');
  }
  report.after=await page.evaluate(()=>fetch('/preview/api/health').then(r=>r.json()));assert.equal(report.after.budget.image.requests-report.before.budget.image.requests,scenarios.length);assert.equal(report.after.budget.text.requests,report.before.budget.text.requests);assert.equal(report.after.budget.embedding.requests,report.before.budget.embedding.requests);assert.equal(posts,scenarios.length);
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.unprefixed,[]);report.passed=true;report.actual_supplier_bill='NOT_AVAILABLE_RESERVATIONS_ARE_NOT_INVOICES';persist();console.log(JSON.stringify({mode:report.mode,passed:true,cases:report.cases.length,requests:posts,actual_sizes:report.cases.map(x=>x.actual_size)}));
 }catch(error){report.failure=error.message;persist();console.error(error);process.exitCode=1;}
 finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
