// Actual local HTTP, SQLite, storage, DOCX/ZIP downloads; FIXTURE model images.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const base=process.env.ILLUSTRATION_FIXTURE_BASE,out=process.env.ILLUSTRATION_FIXTURE_OUT;
 assert.ok(base?.startsWith('http://127.0.0.1:'));assert.ok(out);fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({executablePath:'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true,permissions:['clipboard-read','clipboard-write']}),page=await context.newPage();page.setDefaultTimeout(20000);
 const report={mode:'FIXTURE_ONLY_LOCAL_HTTP_BROWSER',real_provider_calls:0,cases:[],errors:[],unprefixed:[]};
 page.on('pageerror',e=>report.errors.push(e.message));page.on('dialog',d=>{report.errors.push('Unexpected confirmation dialog');d.dismiss();});
 page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))report.unprefixed.push(new URL(r.url()).pathname);});
 try{
  for(const [route,kind,count] of [['campaign','activity',2],['preview-wechat-agent','wechat',3]]){
   if(process.env.ILLUSTRATION_ONLY_KIND&&process.env.ILLUSTRATION_ONLY_KIND!==kind)continue;
   await page.goto(base+'/agents/'+route);await page.locator('#prompt').fill('FIXTURE_ONLY 请生成完整方案与合法配图计划');
   const pending=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/runs'));
   await page.locator('#composer-submit').click();const accepted=await pending;assert.equal(accepted.status(),202,await accepted.text());const task=await accepted.json();
   const slot=page.locator('.preview-illustrations').last();
   await slot.locator('[role="status"]').filter({hasText:'配图生成中'}).waitFor();
   await page.screenshot({path:path.join(out,kind+'-progress.png')});
   await slot.locator('[role="status"]').filter({hasText:kind==='activity'?'可导出图文Word':'可复制/导出'}).waitFor();
   assert.equal(await page.locator('[data-illustration-briefs]').count(),0);
   assert.equal(await slot.getByRole('button',{name:/确认|选择示意图/}).count(),0);
   assert.equal(await slot.locator('figure img').count(),count);
   await page.waitForFunction(()=>[...document.querySelectorAll('.preview-illustration-gallery img')].every(i=>i.complete&&i.naturalWidth>0));
   const plan=await page.evaluate(async id=>(await fetch('/preview/api/v1/preview/illustrations/tasks/'+id).then(r=>r.json())).plan,task.id);
   assert.equal(plan.status,'completed');assert.equal(plan.assets.length,count+(kind==='wechat'?1:0));
   assert.ok(!(await slot.innerText()).includes('qwen-image-3.0'));
   const entry={kind,task:task.id,plan:plan.id,count,status:plan.status};report.cases.push(entry);
   const exportLink=slot.getByRole('link',{name:kind==='activity'?'下载图文 Word':'下载文章与配图 ZIP'});
   const exportResponse=await context.request.get(await exportLink.getAttribute('href'));
   assert.equal(exportResponse.status(),200,await exportResponse.text());
   entry.export_header=exportResponse.headers()['content-disposition'];
   entry.export_link=await exportLink.evaluate(n=>({href:n.href,download:n.download}));
   page.on('response',r=>{if(r.url().endsWith('/package'))entry.package_browser_status=r.status();});
   await exportLink.scrollIntoViewIfNeeded();entry.download_box=await exportLink.boundingBox();
   await page.screenshot({path:path.join(out,kind+'-before-download.png')});
   const downloadEvent=page.waitForEvent('download');
   await exportLink.click();
   const download=await downloadEvent;await download.saveAs(path.join(out,kind==='activity'?'fixture-activity.docx':'fixture-article.zip'));
   entry.download_bytes=fs.statSync(path.join(out,kind==='activity'?'fixture-activity.docx':'fixture-article.zip')).size;assert.ok(entry.download_bytes>100);
   if(kind==='wechat'){
    const frame=page.frameLocator('.wechat-inline-article');await frame.locator('img').first().waitFor();
    assert.equal(await frame.locator('img').count(),count);assert.equal(await page.evaluate(()=>!!window.PREVIEW_XSS),false);
    assert.equal(await page.locator('[data-preview-article-reference]').count(),0);
    assert.equal(await page.locator('.wechat-article-actions summary').innerText(),'发布建议');
    assert.equal(await page.locator('[data-manual-cover] img').count(),1);
    assert.ok(plan.publication.cover?.id);assert.equal(plan.assets.filter(x=>x.role==='cover').length,1);
    await page.waitForFunction(()=>document.querySelector('.wechat-inline-article')?.dataset.articleReady==='true');
    const handle=await page.locator('.wechat-inline-article').elementHandle(),actualFrame=await handle.contentFrame();
    assert.equal(await actualFrame.locator('script,iframe,form,object,svg').count(),0);
    const clipboard=()=>page.evaluate(async()=>{const items=await navigator.clipboard.read(),item=items.find(x=>x.types.includes('text/html'));return {types:item?.types,html:item?await(await item.getType('text/html')).text():''};});
    const normalized=value=>page.evaluate(html=>{const t=document.createElement('template');t.innerHTML=html;return {text:t.content.textContent.replace(/\s+/g,' ').trim(),images:[...t.content.querySelectorAll('img')].map(n=>n.src),headings:[...t.content.querySelectorAll('h1,h2')].map(n=>({text:n.textContent,style:n.getAttribute('style')})),paragraphs:[...t.content.querySelectorAll('p')].map(n=>n.getAttribute('style'))};},value.html);
    await actualFrame.locator('body').click();await page.keyboard.press('Control+A');await page.keyboard.press('Control+C');const manual=await clipboard();
    await page.locator('[data-copy-rich]').click();await page.locator('.wechat-article-feedback').filter({hasText:'已复制排版内容'}).waitFor();const button=await clipboard();
    assert.ok(button.types.includes('text/html')&&button.types.includes('text/plain'));
    assert.deepEqual(await normalized(button),await normalized(manual));
    const rich=await normalized(button);assert.equal(rich.images.length,count);assert.ok(rich.images.every(s=>s.startsWith('data:image/png;base64,')));
    const editor=await context.newPage();await editor.goto(base+'/api/health');await editor.setContent('<!doctype html><title>Independent FIXTURE paste editor</title><h1>FIXTURE_ONLY 独立粘贴编辑器</h1><div contenteditable="true" style="max-width:680px;margin:auto" id="editor"></div>');
    await editor.locator('#editor').click();await editor.keyboard.press('Control+V');await editor.locator('#editor img').first().waitFor();
    await editor.waitForFunction(()=>[...document.querySelectorAll('#editor img')].every(i=>i.complete&&i.naturalWidth>0));
    assert.equal(await editor.locator('#editor img').count(),count);await editor.screenshot({path:path.join(out,'wechat-independent-paste.png')});await editor.close();
    entry.native_selection_vs_button='MATCH';entry.dual_mime=true;entry.images_pasted_to_independent_editor=count;entry.wechat_official_editor='NOT_VERIFIED';
   }
   await page.reload();await page.locator('.preview-illustrations [role="status"]').filter({hasText:kind==='activity'?'可导出图文Word':'可复制/导出'}).waitFor();
   const restored=await page.evaluate(async id=>(await fetch('/preview/api/v1/preview/illustrations/tasks/'+id).then(r=>r.json())).plan,task.id);
   assert.equal(restored.id,plan.id);assert.deepEqual(restored.assets.map(x=>x.id),plan.assets.map(x=>x.id));entry.history=true;
   const peer=await browser.newContext();await peer.request.get(base+'/agents/campaign');
   for(const target of [plan.assets[0].url,`/api/v1/preview/illustrations/plans/${plan.id}`])assert.ok([403,404].includes((await peer.request.get(base.replace('/preview','')+'/preview'+target)).status()));
   await peer.close();entry.other_visitor_denied=true;
   for(const width of [375,1440,1920]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.locator('.preview-illustrations').last().screenshot({path:path.join(out,kind+'-'+width+'-widget.png')});await page.screenshot({path:path.join(out,kind+'-'+width+'.png')});}
  }
  await page.goto(base+'/agents/image');await page.locator('#preview-image-size').waitFor();
  assert.deepEqual(await page.locator('#preview-image-size option').allTextContents(),['1:1','16:9（横版）','9:16（竖版）']);
  assert.equal(await page.locator('#preview-image-quality,#preview-use-image-context').count(),0);
  assert.equal(await page.locator('[data-preview-image-notice]').isVisible(),false);
  await page.locator('#preview-image-size').selectOption('2160x3840');
  await page.locator('#prompt').fill('FIXTURE_ONLY 生成1张9:16教育活动现场图片，不要Logo。');
  const direct=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/agents/image-agent/runs'));
  await page.locator('#composer-submit').click();const directResponse=await direct;
  assert.equal(directResponse.status(),202,await directResponse.text());const submitted=directResponse.request().postDataJSON();
  assert.equal(submitted.size,'2160x3840');assert.equal(submitted.quality,'high');assert.equal(submitted.use_enterprise_context,true);
  await page.locator('.message-generation img').last().waitFor();
  report.image_page={direct_submit:true,confirmation_dialogs:report.errors.filter(x=>x.includes('confirmation')).length,size:submitted.size,quality:submitted.quality,default_context:submitted.use_enterprise_context};
  for(const width of [375,1440,1920]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'image-'+width+'.png')});}
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.unprefixed,[]);report.passed=true;
 }catch(e){report.failure=e.message;process.exitCode=1;console.error(e);}
 finally{fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2));await context.close();await browser.close();console.log(JSON.stringify(report));}
})().catch(e=>{console.error(e);process.exitCode=1;});
