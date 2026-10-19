// Actual local HTTP, SQLite, storage, DOCX/ZIP downloads; FIXTURE model images.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const base=process.env.ILLUSTRATION_FIXTURE_BASE,out=process.env.ILLUSTRATION_FIXTURE_OUT;
 assert.ok(base?.startsWith('http://127.0.0.1:'));assert.ok(out);fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({executablePath:'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true}),page=await context.newPage();page.setDefaultTimeout(15000);
 const report={mode:'FIXTURE_ONLY_LOCAL_HTTP_BROWSER',real_provider_calls:0,cases:[],errors:[],unprefixed:[]};
 page.on('pageerror',e=>report.errors.push(e.message));page.on('dialog',d=>d.accept());
 page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))report.unprefixed.push(new URL(r.url()).pathname);});
 try{
  for(const [route,kind,count] of [['campaign','activity',2],['preview-wechat-agent','wechat',3]]){
   await page.goto(base+'/agents/'+route);await page.locator('#prompt').fill('FIXTURE_ONLY 请生成完整方案与合法配图计划');
   const pending=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/runs'));
   await page.locator('#composer-submit').click();const accepted=await pending;assert.equal(accepted.status(),202,await accepted.text());const task=await accepted.json();
   const slot=page.locator('.preview-illustrations').last();await slot.getByRole('button',{name:'查看自动配图计划'}).click();
   const confirm=slot.getByRole('button',{name:kind==='activity'?'确认配图并导出图文 Word':'确认生成文章配图'});await confirm.waitFor();
   assert.equal(await slot.locator('li').count(),count);
   await confirm.click();await slot.locator('[role="status"]').filter({hasText:'真实已保存资产已绑定'}).waitFor();
   assert.equal(await slot.locator('figure img').count(),count);
   await page.waitForFunction(()=>[...document.querySelectorAll('.preview-illustration-gallery img')].every(i=>i.complete&&i.naturalWidth>0));
   const plan=await page.evaluate(async id=>(await fetch('/preview/api/v1/preview/illustrations/tasks/'+id).then(r=>r.json())).plan,task.id);
   assert.equal(plan.status,'completed');assert.equal(plan.assets.length,count);
   const entry={kind,task:task.id,plan:plan.id,count,status:plan.status};report.cases.push(entry);
   const downloadEvent=page.waitForEvent('download');
   await slot.getByRole('link',{name:kind==='activity'?'下载图文 Word':'下载文章与配图 ZIP'}).click();
   const download=await downloadEvent;await download.saveAs(path.join(out,kind==='activity'?'fixture-activity.docx':'fixture-article.zip'));
   entry.download_bytes=fs.statSync(path.join(out,kind==='activity'?'fixture-activity.docx':'fixture-article.zip')).size;assert.ok(entry.download_bytes>100);
   if(kind==='wechat'){
    const frame=page.frameLocator('.wechat-inline-article');await frame.locator('img').first().waitFor();
    assert.equal(await frame.locator('img').count(),count);assert.equal(await page.evaluate(()=>!!window.PREVIEW_XSS),false);
    assert.equal(await frame.locator('h2').first().evaluate(n=>n.style.backgroundColor),'rgb(255, 243, 196)');
   }
   await page.reload();await page.locator('.preview-illustrations [role="status"]').filter({hasText:'真实已保存资产已绑定'}).waitFor();
   const restored=await page.evaluate(async id=>(await fetch('/preview/api/v1/preview/illustrations/tasks/'+id).then(r=>r.json())).plan,task.id);
   assert.equal(restored.id,plan.id);assert.deepEqual(restored.assets.map(x=>x.id),plan.assets.map(x=>x.id));entry.history=true;
   const peer=await browser.newContext();await peer.request.get(base+'/agents/campaign');
   for(const target of [plan.assets[0].url,`/api/v1/preview/illustrations/plans/${plan.id}`])assert.ok([403,404].includes((await peer.request.get(base.replace('/preview','')+'/preview'+target)).status()));
   await peer.close();entry.other_visitor_denied=true;
   for(const width of [375,1440,1920]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.locator('.preview-illustrations').last().screenshot({path:path.join(out,kind+'-'+width+'-widget.png')});await page.screenshot({path:path.join(out,kind+'-'+width+'.png')});}
  }
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.unprefixed,[]);report.passed=true;
 }catch(e){report.failure=e.message;process.exitCode=1;console.error(e);}
 finally{fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2));await context.close();await browser.close();console.log(JSON.stringify(report));}
})().catch(e=>{console.error(e);process.exitCode=1;});
