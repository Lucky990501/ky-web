// Local actual HTTP/SQLite/browser tests. NO real Provider evidence.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const base=process.env.PREVIEW_REMAINING_BASE,out=process.env.PREVIEW_REMAINING_EVIDENCE;
 assert.ok(base?.startsWith('http://127.0.0.1:')&&out);fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({executablePath:process.env.WECHAT_UI_BROWSER,headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:960}}),page=await context.newPage(),errors=[],bad=[],passed=[];
 page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(new URL(r.url()).origin===new URL(base).origin&&new URL(r.url()).pathname.startsWith('/api/'))bad.push(r.url());});
 const check=(name,value)=>{assert.ok(value,name);passed.push(name);};
 async function json(url,options={}){return page.evaluate(async({url,options})=>{const r=await fetch(url,options);return {status:r.status,data:await r.json()};},{url:base+url,options});}
 try{
  await page.goto(base+'/agents/preview-wechat-agent');await page.locator('[data-preview-article-reference]').waitFor();
  const before=await json('/api/health');check('no login and exact Preview paths',before.status===200);
  const content='<article><h1 style="color:#ffc600;line-height:1.8">对标标题</h1><p>独立测试的参考正文。</p><script>window.__BAD=true</script><img src="https://untrusted.invalid/x"></article>';
  await page.getByLabel('上传对标文章').setInputFiles({name:'对标.html',mimeType:'text/html',buffer:Buffer.from(content)});
  await page.waitForFunction(()=>document.querySelector('[data-preview-article-reference] [role=status]')?.textContent.includes('已选择'));
  check('article uploaded through real HTTP',await page.locator('[data-preview-article-reference]').innerText().then(t=>t.includes('对标.html')));
  check('uploaded scripts never executed',await page.evaluate(()=>!window.__BAD));
  for(const width of [375,1440,1920]){await page.setViewportSize({width,height:960});check('layout '+width,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'article-'+width+'.png')});}
  await page.locator('#prompt').fill('FIXTURE_ONLY 请参考排版');
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/agents/preview-wechat-agent/runs'));
  await page.locator('#composer-submit').click();const accepted=await response;check('accepted Task uses existing API',accepted.status()===202);
  const task=await accepted.json();let done;
  for(let i=0;i<30;i++){done=(await json('/api/v1/tasks/'+task.id)).data;if(['completed','failed'].includes(done.status))break;await page.waitForTimeout(100);}
  check('binding visible before executor starts '+JSON.stringify({status:done.status,error:done.error_code,response:done.final_response}),done.status==='completed'&&done.final_response?.includes('对标.html'));
  const metadata=(await json('/api/v1/preview/article-references?conversation_id='+done.conversation_id)).data.reference;
  check('owned history reference survives',metadata?.filename==='对标.html');check('no private storage key in metadata',!('storage_key' in metadata));
  const peer=await browser.newContext(),other=await peer.newPage();await other.goto(base+'/agents/preview-wechat-agent');await other.locator('#prompt').waitFor();
  const historyDenied=await peer.request.get(base+'/api/v1/preview/article-references?conversation_id='+done.conversation_id);
  // Bypass the UI decorator, so this tests the SERVER ownership boundary.
  const bindDenied=await peer.request.post(base+'/api/v1/agents/preview-wechat-agent/runs',{headers:{origin:new URL(base).origin},data:{message:'Must not create',preview_article_reference_id:metadata.id}});
  const isolation=[historyDenied.status(),bindDenied.status()];check('other visitor cannot read or bind article '+JSON.stringify(isolation),isolation.every(x=>x===404));await peer.close();
  await page.reload();await page.locator('[data-preview-article-reference]').waitFor();await page.waitForFunction(()=>document.querySelector('[data-preview-article-reference] [role=status]')?.textContent.includes('对标.html'));check('browser refresh restores attachment',true);
  await page.getByText('移除待发送附件',{exact:true}).click();await page.locator('#prompt').fill('FIXTURE_ONLY 不再参考附件');
  const cleared=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/agents/preview-wechat-agent/runs'));
  await page.locator('#composer-submit').click();const clearTask=await (await cleared).json();
  for(let i=0;i<30;i++){done=(await json('/api/v1/tasks/'+clearTask.id)).data;if(['completed','failed'].includes(done.status))break;await page.waitForTimeout(100);}
  check('remove clears reference for continuation',done.status==='completed'&&done.final_response?.includes('无附件'));
  const badUpload=await context.request.post(base+'/api/v1/preview/article-references',{headers:{origin:new URL(base).origin},multipart:{file:{name:'fake.png',mimeType:'text/plain',buffer:Buffer.from('bad')}}});check('invalid extension rejected',badUpload.status()===422);
  const csrf=await context.request.post(base+'/api/v1/preview/article-references',{headers:{origin:'https://foreign.invalid'},multipart:{file:{name:'x.md',mimeType:'text/plain',buffer:Buffer.from('bad')}}});check('cross origin upload rejected',csrf.status()===403);
  await page.goto(base+'/agents/image');await page.locator('#preview-use-image-context').waitFor();check('image context defaults off',!(await page.locator('#preview-use-image-context').isChecked()));
  await page.locator('#preview-use-image-context').check();await page.locator('#prompt').fill('生成1张1024x1024海报');await page.locator('#composer-submit').click();await page.locator('#preview-image-error').waitFor();check('unready semantic context blocks before payment',await page.locator('#preview-image-error').innerText().then(t=>t.length>0));
  await page.locator('#preview-use-image-context').uncheck();page.once('dialog',d=>{check('fee confirmation names actual count',d.message().includes('1张'));d.dismiss();});await page.locator('#composer-submit').click();
  const after=(await json('/api/health')).data;check('no paid calls or budget counter mutation',JSON.stringify(before.data.budget)===JSON.stringify(after.budget));check('no production API requests',bad.length===0);check('no browser errors',errors.length===0);
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({mode:'FIXTURE_ONLY_ACTUAL_HTTP_SQLITE_BROWSER',passed:passed.length,cases:passed,paid_calls:0,errors,bad},null,2));console.log(JSON.stringify({passed:passed.length,failed:0,mode:'FIXTURE_ONLY',paid_calls:0}));
 }catch(error){await page.screenshot({path:path.join(out,'failure.png')});console.error(JSON.stringify({url:page.url(),main:await page.locator('#main').innerText().catch(()=>''),errors}));throw error;}
 finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
