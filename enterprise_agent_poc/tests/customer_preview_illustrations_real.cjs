// Explicitly bounded real acceptance: 2 text Tasks, at most 4+3 qwen images.
// New normal anonymous visitor only; no Cookie/token is printed or saved.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 assert.equal(process.env.ILLUSTRATION_REAL_APPROVAL,'owner-qwen-7-calls-0.20-cny-20261019');
 const source=process.env.ILLUSTRATION_REAL_SOURCE,out=process.env.ILLUSTRATION_REAL_OUT;
 assert.ok(source&&out);fs.mkdirSync(out,{recursive:true});
 assert.ok(!fs.existsSync(path.join(out,'visitor.json')),'Never rerun a paid acceptance directory');
 const browser=await chromium.launch({executablePath:'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true,permissions:['clipboard-read','clipboard-write']}),page=await context.newPage();page.setDefaultTimeout(20000);
 const report={mode:'REAL_QWEN_PUBLIC_BROWSER',source,maximum_images:7,maximum_cny:'1.40',cases:[],errors:[],unprefixed:[]};
 const save=()=>fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2));
 page.on('pageerror',e=>report.errors.push(e.message));page.on('dialog',d=>d.accept());
 page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))report.unprefixed.push(new URL(r.url()).pathname);});
 async function json(url){return page.evaluate(async target=>{const r=await fetch(target,{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);return r.json();},url);}
 try{
  await page.goto('https://workbench.luckio.cn/preview/agents/campaign');await page.locator('#prompt').waitFor();
  const me=await json('/preview/api/v1/me');assert.ok(me.tenant_id.startsWith('preview-'));
  report.before=await json('/preview/api/health');assert.equal(report.before.source,source);
  fs.writeFileSync(path.join(out,'visitor.json'),JSON.stringify({tenant_id:me.tenant_id,user_id:me.user_id,source}));
  console.log(JSON.stringify({stage:'WAITING_FOR_SCOPED_BUDGET',tenant_id:me.tenant_id,user_id:me.user_id,source}));
  let allowed=false;for(let wait=0;wait<300;wait++){
   const cap=await json('/preview/api/v1/preview/illustrations/capability');if(cap.enabled){allowed=true;break;}
   await page.waitForTimeout(2000);
  }
  assert.ok(allowed,'Acceptance visitor not authorized; no paid request sent');
  const scenarios=[
   {route:'campaign',kind:'activity',maximum:4,prompt:'这是独立测试活动，不是真实已举办事件。请直接生成完整结构化活动执行方案。主题：亲子科学体验开放日。时间：2026年10月25日14:00至16:00；地点：测试教室；对象：20组家长与孩子；预算：测试预算1000元；宣发：测试家长群与企业公众号。活动只包含四个环节：签到入场、科学课堂体验、亲子合作实验、反馈咨询。各环节说明与image_requirement都要具体、不同且可执行，图像需求分别展示签到区布置、课堂座位与演示、实验台协作、反馈交流区。保留完整邀约文案。以上信息已齐，不再追问，不编造招生价格、真实客户案例或师资承诺。不调用图片工具，也不检索知识库，配图由独立后续服务负责。'},
   {route:'preview-wechat-agent',kind:'wechat',maximum:3,prompt:'请写一篇约600字的教育公众号文章，主题是家长如何温和倾听孩子。输出完整、安全的内联HTML，包含一个h1和两个有实际内容的h2章节，保留Skill 1.0.1的暖黄色重点与引用块样式。不要编造机构价格、地址、教师资质或效果承诺。无需外部事实或知识库检索，不调用图片工具、不放任意外部图片URL。配图由独立后续服务生成；请完成正文，不追问。'}
  ];
  for(const scenario of scenarios){
   if(scenario.kind==='wechat'){await page.goto('https://workbench.luckio.cn/preview/agents/'+scenario.route);await page.locator('#prompt').waitFor();}
   const guard=path.join(out,scenario.kind+'-submission.guard');assert.ok(!fs.existsSync(guard));fs.writeFileSync(guard,'ONE_SUBMISSION_NO_PAID_RETRY');
   await page.locator('#prompt').fill(scenario.prompt);const pending=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/runs'));
   await page.locator('#composer-submit').click();const accepted=await pending;
   assert.equal(accepted.status(),202,await accepted.text());const task=await accepted.json();
   const entry={kind:scenario.kind,task:task.id};report.cases.push(entry);save();
   let done;for(let poll=0;poll<240;poll++){
    done=await json('/preview/api/v1/tasks/'+task.id);if(['completed','failed','cancelled'].includes(done.status))break;await page.waitForTimeout(2000);
   }
   entry.task_status=done.status;entry.run=done.run_id;save();
   if(done.status!=='completed'){entry.failure={code:done.error_code,message:done.user_message};save();continue;}
   if(scenario.kind==='activity'&&!done.structured_result){entry.failure={code:'STRUCTURED_RESULT_MISSING'};save();continue;}
   const slot=page.locator(`[data-assistant-message-id="${done.assistant_message_id}"] .preview-illustrations`);
   await slot.getByRole('button',{name:'查看自动配图计划'}).click();
   const create=slot.getByRole('button',{name:scenario.kind==='activity'?'确认配图并导出图文 Word':'确认生成文章配图'});await create.waitFor();
   let plan=(await json('/preview/api/v1/preview/illustrations/tasks/'+task.id)).plan;entry.plan=plan.id;entry.image_count=plan.count;save();
   if(plan.count<1||plan.count>scenario.maximum){entry.failure={code:'IMAGE_PLAN_OUTSIDE_APPROVED_SCOPE',count:plan.count};save();continue;}
   assert.ok(Number(plan.maximum_cost_cny)<=scenario.maximum*0.2+0.000001);
   const imageGuard=path.join(out,scenario.kind+'-images.guard');assert.ok(!fs.existsSync(imageGuard));fs.writeFileSync(imageGuard,JSON.stringify({plan:plan.id,count:plan.count,maximum_cost_cny:plan.maximum_cost_cny}));
   await create.click();
   for(let poll=0;poll<300;poll++){
    plan=await json('/preview/api/v1/preview/illustrations/plans/'+plan.id);if(['completed','failed'].includes(plan.status))break;await page.waitForTimeout(2000);
   }
   entry.illustration_status=plan.status;entry.assets=plan.assets;entry.error_code=plan.error_code;entry.safe_diagnostic=plan.safe_diagnostic;save();
   if(plan.status!=='completed'){entry.failure={code:plan.error_code||'STILL_PROCESSING'};save();break;}
   await slot.locator('[role="status"]').filter({hasText:'真实已保存资产已绑定'}).waitFor();
   for(const asset of plan.assets){const response=await context.request.get('https://workbench.luckio.cn/preview'+asset.url);assert.equal(response.status(),200);const data=await response.body();assert.ok(data.length>100);fs.writeFileSync(path.join(out,asset.filename),data);}
   const downloadEvent=page.waitForEvent('download');await slot.getByRole('link',{name:scenario.kind==='activity'?'下载图文 Word':'下载文章与配图 ZIP'}).click();
   const download=await downloadEvent;entry.download=download.suggestedFilename();await download.saveAs(path.join(out,entry.download));
   if(scenario.kind==='wechat'){
    const frame=page.frameLocator('.wechat-inline-article');await frame.locator('img').first().waitFor();assert.equal(await frame.locator('img').count(),plan.assets.length);
    await page.locator('[data-copy-rich]').click();entry.clipboard_mimes=await page.evaluate(async()=>{const items=await navigator.clipboard.read();return items.flatMap(item=>item.types);});
    assert.ok(entry.clipboard_mimes.includes('text/html')&&entry.clipboard_mimes.includes('text/plain'));
   }
   await page.reload();await page.locator('.preview-illustrations [role="status"]').filter({hasText:'真实已保存资产已绑定'}).waitFor();
   const restored=(await json('/preview/api/v1/preview/illustrations/tasks/'+task.id)).plan;assert.deepEqual(restored.assets.map(a=>a.id),plan.assets.map(a=>a.id));entry.history=true;
   const peer=await browser.newContext();await peer.request.get('https://workbench.luckio.cn/preview/agents/campaign');
   assert.ok([403,404].includes((await peer.request.get('https://workbench.luckio.cn/preview'+plan.assets[0].url)).status()));await peer.close();entry.other_visitor_denied=true;
   for(const width of [375,1440,1920]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.locator('.preview-illustrations').last().screenshot({path:path.join(out,scenario.kind+'-'+width+'.png')});}
   entry.passed=true;save();
  }
  report.after=await json('/preview/api/health');const cap=await json('/preview/api/v1/preview/illustrations/capability');report.illustration_budget=cap;
  assert.ok(cap.reserved_requests<=7&&Number(cap.reserved_ceiling_cny)<=1.400001);
  const health=await context.request.get('https://workbench.luckio.cn/api/health');report.production_health=health.ok()&&(await health.json()).status==='ok';
  report.passed=report.cases.length===2&&report.cases.every(entry=>entry.passed)&&report.production_health;
 }catch(error){report.failure=error.message;process.exitCode=1;console.error(error.message);}
 finally{save();await context.close();await browser.close();console.log(JSON.stringify(report));}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
