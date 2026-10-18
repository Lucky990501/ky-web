// Explicit opt-in real Preview tasks. No retries and no replacement results.
const {chromium}=require('playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const dir=process.env.PREVIEW_REAL_EVIDENCE,kind=process.argv[2];if(!dir||!kind)throw Error('Explicit evidence directory and case required');fs.mkdirSync(dir,{recursive:true});
 const browser=await chromium.launchPersistentContext(dir+'/private-browser',{executablePath:process.env.WECHAT_UI_BROWSER,headless:true,permissions:['clipboard-read','clipboard-write'],viewport:{width:1440,height:960}});
 const page=await browser.newPage(),base='https://workbench.luckio.cn/preview';page.setDefaultTimeout(20000);
 const req=async(path,method='GET',body)=>page.evaluate(async({path,method,body})=>{const r=await fetch('/preview'+path,{method,headers:body?{'content-type':'application/json'}:{},body:body?JSON.stringify(body):undefined});return {status:r.status,body:await r.json()};},{path,method,body});
 const cases={copy:{agent:'copywriting-agent',route:'copywriting',message:'请先读取企业设置，再检索知识库“天文观察课 周三19点”，读取素材。根据实际资料写一段80字以内的招生宣传文案，保留课程时间，不编造价格。'},campaign:{agent:'campaign-agent',route:'campaign',message:'请生成完整的星河体验教育天文观察招生活动方案。活动时间2026年10月25日19:00-20:30，地点测试体验教室，对象6-9岁儿童及家长20组，内容天文知识介绍、星图手工、观察体验、咨询。预算800元，主办星河体验教育。请先读取企业设置并一次检索天文观察课资料，输出完整方案及邀请文案，不生成图片。'},wechat:{agent:'preview-wechat-agent',route:'preview-wechat-agent',message:'先调用enterprise_config_get，并knowledge_search检索天文观察课。根据真实设置和检索资料，写一篇400字以内的公众号文章，主题为亲子天文观察体验。交付完整安全HTML排版，标题、导语、段落、暖黄色重点、引用。不得虚构价格，不生成图片。'},image:{agent:'image-agent',route:'image',message:'为星河体验教育生成且仅生成一张1024x1024亲子天文观察体验海报。请先读取企业设置、检索知识和素材，再调用image_generation一次。画面是夜空星图和亲子观察剪影，中文主标题“亲子天文观察”，副标题“周三19点”，不要Logo，不要价格，输出1张即可。'}};
 try{
 for(let n=0;n<10;n++){const h=await browser.request.get(base+'/api/health');if(h.ok())break;await page.waitForTimeout(500);}
 await page.goto(base+'/agents/copywriting');await page.locator('#prompt').waitFor();
 if(kind==='semantic'){
  const previous=JSON.parse(fs.readFileSync(dir+'/prepare.json'));
  for(const file of previous.files)assert.equal((await req('/api/v1/knowledge/files/'+file.id,'DELETE')).status,200);
  await page.goto(base+'/knowledge');await page.locator('#preview-knowledge-file').setInputFiles(dir+'/星河体验课程_真实Word上传.docx');
  await page.locator('.trow').filter({hasText:'星河体验课程_真实Word上传.docx'}).waitFor({timeout:90000});
  const files=await req('/api/v1/knowledge/files');assert.ok(files.body.some(x=>x.status==='ready'));
  const result=await req('/api/v1/knowledge/retrieval-test','POST',{query:'亲子课程几点开始，适合几岁孩子？',top_k:5});assert.ok(result.body.results.length);
  await page.goto(base+'/enterprise-config');await page.locator('input[type=file]').first().setInputFiles(dir+'/preview-logo.png');
  // Existing Logo uploader performs POST and canonical config refresh.
  await page.waitForFunction(async()=>Boolean((await fetch('/preview/api/v1/enterprise-config').then(r=>r.json())).brand_logo),{timeout:20000});
  fs.writeFileSync(dir+'/semantic.json',JSON.stringify({files:files.body,retrieval:result.body,config:(await req('/api/v1/enterprise-config')).body},null,2));console.log('REAL_SEMANTIC_AND_LOGO_PASS');return;
 }
 if(kind==='prepare'){
  assert.equal((await req('/api/v1/enterprise-config','PUT',{payload:{brand_name:'星河体验教育',description:'独立体验验收资料，不是真实客户。',brand_tone:'清晰可信，不承诺学习效果。'}})).status,200);
  await page.goto(base+'/knowledge');await page.locator('#preview-knowledge-file').setInputFiles({name:'星河体验课程.md',mimeType:'text/markdown',buffer:Buffer.from('# 天文观察课\n\n星河体验教育的天文观察课为6-9岁亲子体验。时间为周三19点，地点为测试体验教室。包含星图手工和基础观察。课程仅供体验，不承诺效果，价格尚未确认。')});
  await page.locator('.trow').filter({hasText:'星河体验课程.md'}).waitFor();
  const files=await req('/api/v1/knowledge/files');assert.ok(files.body.some(x=>x.status==='ready'));
  const result=await req('/api/v1/knowledge/retrieval-test','POST',{query:'天文观察课周三19点',top_k:5});assert.ok(result.body.results.length);
  fs.writeFileSync(dir+'/prepare.json',JSON.stringify({files:files.body,retrieval:result.body},null,2));console.log('PREPARE_PASS');return;
 }
 if(kind==='inspect'){
  const rows=await req('/api/v1/conversations');console.log(JSON.stringify(rows));return;
 }
 if(kind==='updateCompany'){
  await page.goto(base+'/enterprise-config');await page.locator('[name=brand_name]').fill('星河体验教育（更新版）');await page.locator('[name=required_rules]').fill('不使用名额有限或效果承诺。文案末尾署名最新企业名称。');
  const saved=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().endsWith('/enterprise-config'));await page.locator('#config-form button[type=submit]').click();assert.equal((await saved).status(),200);
  await page.reload();await page.locator('[name=brand_name]').waitFor();assert.equal(await page.locator('[name=brand_name]').inputValue(),'星河体验教育（更新版）');
  const logo=page.locator('.brand-logo-preview img');await logo.waitFor();assert.ok(await logo.evaluate(x=>x.complete&&x.naturalWidth>0));assert.ok((await logo.getAttribute('src')).includes('/preview/'));
  fs.writeFileSync(dir+'/company-update.json',JSON.stringify({saved_refreshed:true,logo_loaded:true,config:(await req('/api/v1/enterprise-config')).body},null,2));console.log('COMPANY_UPDATE_LOGO_REFRESH_PASS');return;
 }
 if(kind==='exports'){
  const t=JSON.parse(fs.readFileSync(dir+'/wechat.json')).final;
  await page.goto(base+'/agents/preview-wechat-agent?conversation='+t.conversation_id+'&task='+t.id);await page.locator('[data-copy-rich]').waitFor();
  assert.equal(await page.locator('iframe.wechat-inline-article').count(),1);
  await page.locator('[data-copy-rich]').click();await page.getByText('已复制排版内容，请粘贴到公众号编辑器',{exact:true}).waitFor();
  const clipboard=await page.evaluate(async()=>{const data=await navigator.clipboard.read();return Promise.all(data.map(async item=>({types:item.types,html:item.types.includes('text/html')?await(await item.getType('text/html')).text():''})));});
  assert.ok(clipboard.some(x=>x.types.includes('text/html')&&x.types.includes('text/plain')));assert.ok(clipboard.some(x=>x.html.includes('天文')));assert.ok(clipboard.some(x=>/<h1\b/.test(x.html)));assert.ok(clipboard.every(x=>!x.html.includes('&lt;div')));
  await page.locator('[data-copy-source]').click();assert.match(await page.evaluate(()=>navigator.clipboard.readText()),/<(?:section|p)/);
  const downloading=page.waitForEvent('download');await page.locator('[data-download-html]').click();const file=await downloading;await file.saveAs(dir+'/公众号真实文章.html');assert.match(fs.readFileSync(dir+'/公众号真实文章.html','utf8'),/<!doctype html>/i);
  await page.reload();await page.locator('[data-copy-rich]').waitFor();await page.screenshot({path:dir+'/wechat-actual-preview.png'});
  for(const width of [375,1440,1920]){await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
  fs.writeFileSync(dir+'/wechat-export.json',JSON.stringify({dual_mime:true,real_article:true,refresh:true,html_download:true}));console.log('WECHAT_REAL_PREVIEW_COPY_DOWNLOAD_PASS');return;
 }
 cases.cycle={agent:'copywriting-agent',route:'copywriting',message:'使用本企业知识库中已上传的天文观察课资料，先真实调用企业配置、知识检索和素材工具。输出一段60字的周三课程报名文案，品牌名称取企业配置，课程时间取检索结果。不要输出工具调用源码。'};
 cases.imageApproved={...cases.image,message:'Owner已获知上次image_generation被托管审批429拒绝、图片Provider调用0次，现明确批准通过同一正式工具再验收一次，保持全部原审批、租户隔离及预算。请为星河体验教育生成仅1张1024x1024亲子天文观察海报，中文标题“亲子天文观察”，副标题“周三19点”，星空亲子剪影，不加Logo不写价格。只调用1次image_generation；若审批再次拒绝立即停止，不走其他接口。'};
 cases.copySecond={...cases.copy,conversation:JSON.parse(fs.readFileSync(dir+'/cycle.json')).final.conversation_id,message:'把上一版文案改为温和、自然、不催促的家长邀请，去掉未经资料确认的“名额有限”，保留品牌、周三19点和星图手工。请使用最新知识和企业设置，60字左右。'};
 cases.resumeDiagnostic={...cases.copySecond,message:'继续修改为温和邀请，保留周三19点，并读取本企业最新设置。'};
 cases.campaignQuestion={...cases.campaign,message:'我想为企业策划亲子天文招生活动，先帮我明确还需要提供哪些关键活动信息，不要编造时间地点。'};
 if(fs.existsSync(dir+'/campaignQuestion.json'))cases.campaign={...cases.campaign,conversation:JSON.parse(fs.readFileSync(dir+'/campaignQuestion.json')).final.conversation_id};
 cases.campaignVerified={...cases.campaign,message:'补充关键信息：活动为天文观察课招生活动，2026年10月25日19:00到20:30，测试体验教室，6到9岁亲子20组，预算800元。流程含星图手工、基础观察、课程咨询。先读取最新企业配置，并一次检索天文观察课。请输出简洁但完整的正式活动方案，表格和邀约文案完整，不生图。'};
 cases.campaignFinal={...cases.campaignVerified};
 const resumed=kind.startsWith('resume:'),name=resumed?kind.slice(7):kind,c=cases[name];if(!c)throw Error('Unknown case');const out=dir+'/'+name+'.json';if(fs.existsSync(out)&&!resumed)throw Error('Existing real case evidence; will not submit a duplicate Task');
 await page.goto(base+'/agents/'+c.route);await page.locator('#prompt').waitFor();
 if(c.conversation){await page.evaluate(({agent,id})=>selectAgentConversation(agent,id),{agent:c.agent,id:c.conversation});await page.locator('#prompt').waitFor();}
 let t;if(resumed)t=JSON.parse(fs.readFileSync(out)).accepted;else{
 const accepted=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes('/agents/')&&r.url().endsWith('/runs'));
 await page.locator('#prompt').fill(c.message);await page.locator('#composer-submit').click();const r=await accepted;t=await r.json();assert.equal(r.status(),202);assert.ok(t.id);}
 fs.writeFileSync(out,JSON.stringify({mode:'REAL_PREVIEW_PROVIDER',accepted:t},null,2));console.log('ACCEPTED',kind,t.id);
 // Navigate away without cancelling. The existing server Task must continue.
 await page.goto(base+'/enterprise-config');let final;
 for(let n=0;n<150;n++){
  const response=await req('/api/v1/tasks/'+t.id);assert.equal(response.status,200);final=response.body;
  if(['completed','failed','cancelled'].includes(final.status))break;
  if(n%5===0)console.log('TASK',kind,final.status,final.stage);await page.waitForTimeout(2000);
 }
 fs.writeFileSync(out,JSON.stringify({mode:'REAL_PREVIEW_PROVIDER',accepted:t,final,budget:(await req('/api/v1/preview-status')).body},null,2));
 console.log('FINAL',kind,final.status,final.error_code||'',String(final.final_response||'').slice(0,160));
 if(final.conversation_id){await page.goto(base+'/agents/'+c.route);await page.locator('#prompt').waitFor();await page.evaluate(({agent,id,task})=>selectAgentConversation(agent,id,{taskId:task}),{agent:c.agent,id:final.conversation_id,task:t.id});await page.locator('#prompt').waitFor();await page.screenshot({path:dir+'/'+name+'-result.png',fullPage:true});await page.reload();await page.locator('#prompt').waitFor();await page.screenshot({path:dir+'/'+name+'-refresh.png'});}
 assert.equal(final.status,'completed');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
