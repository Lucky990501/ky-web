// Existing real Task results only. These checks never submit a Run.
const {chromium}=require('playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const dir=process.env.PREVIEW_REAL_EVIDENCE,mode=process.argv[2],base='https://workbench.luckio.cn/preview';
 const context=await chromium.launchPersistentContext(dir+'/private-browser',{executablePath:process.env.WECHAT_UI_BROWSER,headless:true,permissions:['clipboard-read','clipboard-write'],viewport:{width:1440,height:960}});
 const page=await context.newPage();page.setDefaultTimeout(25000);let runPosts=0;
 page.on('request',r=>{if(r.method()==='POST'&&/\/agents\/[^/]+\/runs$/.test(new URL(r.url()).pathname))runPosts++});
 const load=async name=>JSON.parse(fs.readFileSync(dir+'/'+name+'.json')).final;
 try{
 if(mode==='history'){
  const first=await load('cycle'),second=await load('closureCopy');assert.equal(first.conversation_id,second.conversation_id);assert.equal(second.status,'completed');
  await page.goto(base+'/conversations');const row=page.locator(`[data-history-conversation="${second.conversation_id}"]`);await row.waitFor();await row.click();
  await page.locator('#chat-body').getByText('参考文件：星河体验课程_真实Word上传.docx',{exact:true}).waitFor();assert.ok((await page.locator('#chat-body').innerText()).includes('星河体验教育（更新版）'));
  const detail=await page.evaluate(id=>fetch('/preview/api/v1/conversations/'+id).then(r=>r.json()),second.conversation_id);assert.ok(detail.tasks.some(x=>x.id===second.id&&x.status==='completed'));assert.ok(detail.tasks.some(x=>x.id===first.id&&x.status==='completed'));
  await page.reload();await page.locator('#chat-body').getByText('参考文件：星河体验课程_真实Word上传.docx',{exact:true}).waitFor();
  await page.locator('[data-copy-response]').last().click();assert.match(await page.evaluate(()=>navigator.clipboard.readText()),/星河体验教育（更新版）/);
  const source=page.locator('#chat-body').getByText('参考文件：星河体验课程_真实Word上传.docx',{exact:true});await source.scrollIntoViewIfNeeded();
  await page.screenshot({path:dir+'/closure-history-reopened.png'});await page.setViewportSize({width:375,height:900});await source.scrollIntoViewIfNeeded();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:dir+'/closure-copy-mobile.png'});
  fs.writeFileSync(dir+'/closure-history-proof.json',JSON.stringify({conversation_id:second.conversation_id,first_task:first.id,continued_task:second.id,reopened:true,history_click:true,refresh:true,copy:true,new_run_posts:runPosts}));
 }else if(mode==='word'){
  const t=await load('closurePlanExport');assert.equal(t.status,'completed');assert.equal(t.structured_result?.type,'activity_plan');assert.ok(t.structured_result.data.activity_items.length);
  await page.goto(base+'/agents/campaign?conversation='+t.conversation_id+'&task='+t.id);const action=page.locator(`[data-document-export="${t.assistant_message_id}"]`);await action.waitFor();
  const creating=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/documents/activity-plan'));await action.click();const made=await creating;assert.equal(made.status(),201);const document=await made.json();
  const card=page.locator(`[data-document-download="${t.assistant_message_id}"]`).first();await card.waitFor();
  const http=page.waitForResponse(r=>r.request().method()==='GET'&&r.url().includes('/documents/activity-plan/'+document.document_id));const downloading=page.waitForEvent('download');await card.click();const response=await http;assert.equal(response.status(),200);assert.equal(response.headers()['content-type'].split(';')[0],'application/vnd.openxmlformats-officedocument.wordprocessingml.document');assert.match(response.headers()['content-disposition'],/attachment/);
  const download=await downloading;assert.match(download.suggestedFilename(),/\.docx$/i);await download.saveAs(dir+'/真实活动方案.docx');assert.ok(fs.statSync(dir+'/真实活动方案.docx').size>1000);
  await page.reload();const restored=page.locator(`[data-document-download="${t.assistant_message_id}"]`).first();await restored.waitFor();const again=page.waitForEvent('download');await restored.click();await(await again).saveAs(dir+'/真实活动方案_历史重下.docx');assert.deepEqual(fs.readFileSync(dir+'/真实活动方案.docx'),fs.readFileSync(dir+'/真实活动方案_历史重下.docx'));
  await page.screenshot({path:dir+'/closure-word-card.png'});
  fs.writeFileSync(dir+'/closure-word-proof.json',JSON.stringify({task_id:t.id,conversation_id:t.conversation_id,document,download_http:200,mime:response.headers()['content-type'],filename:download.suggestedFilename(),history_download:true,new_run_posts:runPosts},null,2));
 }else if(mode==='image'){
  const t=await load('imageApproved');assert.equal(t.status,'completed');assert.equal(t.generations.length,1);const g=t.generations[0];
  await page.goto(base+'/agents/image?conversation='+t.conversation_id+'&task='+t.id);const tile=page.locator(`[data-result-image="${g.id}"]`);await tile.waitFor();const image=tile.locator('img');await image.waitFor();await page.waitForFunction(id=>document.querySelector(`[data-result-image="${id}"] img`)?.naturalWidth>0,g.id);
  await tile.getByRole('button',{name:'查看大图'}).click();await page.locator('#generation-viewer img').waitFor();await page.locator('#generation-viewer .dialog-close').click();
  const http=page.waitForResponse(r=>r.request().method()==='GET'&&r.url().includes('/storage/'));const downloading=page.waitForEvent('download');await tile.getByRole('link',{name:'下载',exact:true}).click();const response=await http;assert.equal(response.status(),200);assert.match(response.headers()['content-type'],/^image\/(png|jpeg)/);const file=await downloading;await file.saveAs(dir+'/真实生成图片'+(/png/.test(response.headers()['content-type'])?'.png':'.jpg'));
  await page.reload();await page.locator(`[data-result-image="${g.id}"] img`).waitFor();await page.screenshot({path:dir+'/closure-image-history.png'});
  fs.writeFileSync(dir+'/closure-image-proof.json',JSON.stringify({task_id:t.id,generation_id:g.id,download_http:200,mime:response.headers()['content-type'],history:true,new_run_posts:runPosts},null,2));
 }else throw Error('Unknown mode');
 assert.equal(runPosts,0);console.log('CLOSURE_BROWSER_PASS',mode);
 }finally{await context.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
