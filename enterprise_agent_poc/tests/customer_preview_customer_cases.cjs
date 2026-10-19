// Real, separately authorized text cases only. No image action or photo upload.
const {chromium}=require('playwright'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const out=process.env.PREVIEW_CUSTOMER_CASE_EVIDENCE,base='https://workbench.luckio.cn/preview';assert.ok(out);fs.mkdirSync(out,{recursive:true});
 assert.ok(!fs.existsSync(path.join(out,'started.json')),'No automatic paid repeat');
 const browser=await chromium.launch({executablePath:process.env.WECHAT_UI_BROWSER,headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:960},acceptDownloads:true,permissions:['clipboard-read','clipboard-write']});
 const page=await context.newPage();page.setDefaultTimeout(20000);const report={mode:'REAL_TEXT_PROVIDER_PUBLIC_BROWSER',cases:[],image_calls:0};
 const save=()=>fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2));
 async function run(prompt){
  await page.waitForFunction(()=>{const b=document.querySelector('#composer-submit');return b?.type==='submit'&&!b.disabled&&!document.querySelector('#task-status')});
  await page.locator('#prompt').fill(prompt);const received=page.waitForResponse(r=>r.request().method()==='POST'&&/\/agents\/[^/]+\/runs$/.test(new URL(r.url()).pathname));
  await page.locator('#composer-submit').click();const response=await received;assert.equal(response.status(),202,await response.text());const task=await response.json();
  const entry={id:task.id,prompt};report.cases.push(entry);save();
  for(let n=0;n<240;n++){
   entry.result=await page.evaluate(id=>fetch('/preview/api/v1/tasks/'+id).then(r=>r.json()),task.id);
   if(['completed','failed','cancelled'].includes(entry.result.status))break;
   await new Promise(resolve=>setTimeout(resolve,2000));
  }
  save();return entry.result;
 }
 try{
  await page.goto(base+'/agents/campaign');await page.locator('#prompt').waitFor();
  report.before=await page.evaluate(()=>fetch('/preview/api/health').then(r=>r.json()));
  fs.writeFileSync(path.join(out,'started.json'),JSON.stringify({at:new Date().toISOString(),source:report.before.source,no_images:true}));
  const first=await run('做一个元旦活动方案，学科课程、1月1日当天、门店/校区、面向高中学生家长。');report.campaign_first=first;
  if(first.status==='completed'){
   const second=await run('活动内容：3周年校庆 活动地点：线下门店');report.campaign_second=second;
   if(second.status==='completed'&&second.structured_result?.type==='activity_plan'){
    await page.locator(`[data-document-export="${second.assistant_message_id}"]`).waitFor();
    const created=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes('/documents/activity-plan'));
    await page.locator(`[data-document-export="${second.assistant_message_id}"]`).click();const response=await created;report.word_http=response.status();report.word=await response.json();
    if(response.status()===201){const pending=page.waitForEvent('download');await page.locator(`[data-document-download="${second.assistant_message_id}"]`).first().click();await(await pending).saveAs(path.join(out,'元旦周年校庆方案.docx'));report.word_download=true;}
    await page.reload();await page.locator(`[data-document-download="${second.assistant_message_id}"]`).first().waitFor();report.word_history=true;
   }
  }
  await page.goto(base+'/agents/preview-wechat-agent');await page.locator('#prompt').waitFor();
  const article=await run('写一篇图文并茂的劝学生学习的公众号，600字');report.wechat=article;
  if(article.status==='completed'){
   await page.locator('[data-copy-rich]').last().waitFor();await page.locator('[data-copy-rich]').last().click();
   await page.waitForFunction(()=>[...document.querySelectorAll('.wechat-article-feedback')].some(x=>x.textContent.includes('已复制排版内容')));
   report.clipboard_types=await page.evaluate(async()=>{const values=await navigator.clipboard.read();return values.flatMap(x=>x.types)});
   const download=page.waitForEvent('download');await page.locator('[data-download-html]').last().click();await(await download).saveAs(path.join(out,'劝学生学习_真实文章.html'));
   report.html_download=true;await page.reload();await page.locator('[data-copy-rich]').last().waitFor();report.wechat_history=true;
  }
  report.after=await page.evaluate(()=>fetch('/preview/api/health').then(r=>r.json()));
  await page.screenshot({path:path.join(out,'wechat-1440.png')});await page.setViewportSize({width:375,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'wechat-375.png')});
  report.done=true;save();console.log(JSON.stringify({source:report.after.source,cases:report.cases.map(x=>({id:x.id,status:x.result.status,error:x.result.error_code})),word:report.word_download||false,rich_copy:report.clipboard_types||[],budget:report.after.budget}));
 }catch(error){report.error=String(error);save();throw error;}
 finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1});
