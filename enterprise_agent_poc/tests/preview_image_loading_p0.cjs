// Zero-cost browser fixture. Actual renderer/fallback/composer code, no Provider.
const {chromium}=require('playwright'),fs=require('node:fs'),path=require('node:path'),http=require('node:http'),cp=require('node:child_process'),assert=require('node:assert/strict');
const crypto=require('node:crypto');
const root=path.resolve(__dirname,'../app/static'),out=process.env.PREVIEW_IMAGE_LOADING_OUT;assert.ok(out);fs.mkdirSync(out,{recursive:true});
const png=Buffer.from(cp.execFileSync('python',['-c',"from PIL import Image;import io,base64;b=io.BytesIO();Image.new('RGB',(256,144),'steelblue').save(b,'PNG');print(base64.b64encode(b.getvalue()).decode())"],{encoding:'utf8'}).trim(),'base64');
const old=cp.execFileSync('git',['show','a0a1fe1c36cad25bc58ec2f1287defde4a828c4c:enterprise_agent_poc/app/static/workbench.js'],{encoding:'utf8',maxBuffer:2e6}),current=fs.readFileSync(path.join(root,'workbench.js'),'utf8');
const oldDisplay=cp.execFileSync('git',['show','a0a1fe1c36cad25bc58ec2f1287defde4a828c4c:enterprise_agent_poc/app/static/preview-image-display.js'],{encoding:'utf8'});
const fallback=s=>s.split('\n').find(l=>l.startsWith('const bindImageFallbacks = '));
const composer=current.slice(current.indexOf('function resizePreviewComposer('),current.indexOf('const refreshIcons = '));
const counts=new Map(),key='task:12345678-1234-1234-1234-123456789abc:generation';let scenario='ok';
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const server=http.createServer(async(req,res)=>{
 const p=new URL(req.url,'http://localhost').pathname;counts.set(p,(counts.get(p)||0)+1);
 if(p.endsWith('/api/v1/me')){res.writeHead(200,{'content-type':'application/json'});return res.end(JSON.stringify({tenant_id:'preview-fixture-a',user_id:'fixture-user-a'}));}
 if(p.includes('/image-display/')||p.includes('/storage/')){
  const thumb=p.includes('/image-display/');
  if(thumb&&scenario==='network'){req.socket.destroy();return;}
  if(thumb&&['503','401'].includes(scenario)||!thumb&&scenario==='404'){
   res.writeHead(thumb?Number(scenario):404,{'content-type':'application/json'});return res.end('{}');
  }
  if(thumb&&scenario==='404'){res.writeHead(503,{'content-type':'application/json'});return res.end('{}');}
  if(thumb&&scenario==='delay')await wait(600);
  res.writeHead(200,{'content-type':'image/png','content-length':png.length,'cache-control':'private, no-store'});return res.end(png);
 }
 if(p.endsWith('/display.js')||p.endsWith('/before-display.js')){res.writeHead(200,{'content-type':'application/javascript'});return res.end(p.endsWith('/before-display.js')?oldDisplay:fs.readFileSync(path.join(root,'preview-image-display.js')));}
 if(p.endsWith('/workbench.css')){res.writeHead(200,{'content-type':'text/css'});return res.end(fs.readFileSync(path.join(root,'workbench.css')));}
 const before=p.endsWith('/before');res.writeHead(200,{'content-type':'text/html'});
 res.end(`<!doctype html><html data-customer-preview="true"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FIXTURE 图片加载及输入框</title><link rel="stylesheet" href="/workbench.css"><style>body{margin:0}header{height:70px;padding:12px}#main{height:calc(100dvh - 70px)}.creation-layout{height:100%}.creation-main{display:flex;flex-direction:column}.chat-body{flex:1}.composer{width:100%}.fixture-card{max-width:360px}.fixture-card img{width:256px;height:144px;max-width:100%}</style><script>window.WORKBENCH_PREVIEW_BASE='/preview';window.icon=()=>'';</script><script src="${before?'/before-display.js':'/display.js'}"></script><body><header>隔离浏览器Fixture · 无模型调用 / 非Owner公网验收</header><main id="main" data-agent-id="image-agent"><div class="creation-layout chatgpt-conversation-layout"><aside></aside><section class="creation-main"><div id="chat-body" class="chat-body"><div id="cards"></div></div><form id="composer" class="composer"><textarea id="prompt" aria-label="输入提示词"></textarea><div><span>输入自然语言需求</span><p class="composer-run-feedback" id="composer-run-feedback" role="status"></p><button class="button primary" id="composer-submit" type="submit" aria-label="发送"><svg viewBox="0 0 24 24" width="18" height="18"><path d="M12 20V4m-6 6 6-6 6 6" fill="none" stroke="currentColor" stroke-width="2"/></svg></button></div></form></section></div></main><script>
 ${before?fallback(old):composer+fallback(current)};
 window.addFixtureImage=()=>{const cards=document.querySelector('#cards');cards.insertAdjacentHTML('beforeend','<div class="fixture-card"><a class="media-thumb" data-generation-link href="/preview/api/v1/storage/generated/preview-fixture-a/fixture.png"><span class="media-loading">加载中</span><img loading="lazy" data-generation-image data-preview-image-source="/preview/api/v1/preview/image-display/generations/${key}" alt="Fixture历史图片"></a></div>');bindImageFallbacks();};
 window.addFixtureImage();${before?'':'bindPreviewComposerResize();'}
 document.querySelector('#composer').onsubmit=e=>{e.preventDefault();document.querySelector('#prompt').value='';${before?'':'resizePreviewComposer(document.querySelector("#prompt"));'}};
 </script></body></html>`);
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({executablePath:'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',headless:true});
 const report={mode:'ISOLATED_REAL_CHROME_FIXTURE',public_owner_browser:'NOT_VERIFIED',new_paid_calls:0,cases:[]};
 try{
  let page=await browser.newPage({viewport:{width:1200,height:900}});await page.goto(base+'/before');
  assert.ok(await page.locator('.media-failed').count());assert.equal(await page.locator('img').first().isHidden(),true);report.baseline_premature_failure_reproduced=true;await page.close();
  for(scenario of ['delay','ok','503','404','401','network']){
   counts.clear();page=await browser.newPage({viewport:{width:1200,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(base+'/after',{waitUntil:'domcontentloaded'});
   if(scenario==='delay'){
    await page.waitForFunction(()=>document.querySelector('img')?.dataset.previewImageState==='decoding');
    assert.equal(await page.locator('.media-failed').count(),0);assert.equal(await page.locator('img').first().isHidden(),false);
   }
   const failed=['404','401'].includes(scenario);
   await page.waitForFunction(failed=>document.querySelector('img')?.dataset.previewImageState===(failed?'failed':'loaded'),failed);
   const original=[...counts].filter(([p])=>p.includes('/storage/')).reduce((n,[,v])=>n+v,0);
   if(scenario==='503'||scenario==='404'||scenario==='network')assert.equal(original,1);
   if(scenario==='401')assert.equal(original,0);
   if(!failed){assert.ok(await page.locator('img').first().evaluate(n=>n.naturalWidth));assert.equal(await page.locator('.media-failed').count(),0);}
   if(scenario==='ok'){
    const before=[...counts].filter(([p])=>p.includes('/image-display/')).reduce((n,[,v])=>n+v,0);
    await page.evaluate(()=>{document.querySelector('#cards').replaceChildren();window.addFixtureImage();});
    await page.waitForFunction(()=>document.querySelector('img')?.dataset.previewImageState==='loaded');
    assert.equal([...counts].filter(([p])=>p.includes('/image-display/')).reduce((n,[,v])=>n+v,0),before);
    // Viewer and download keep using the protected original, never a thumbnail.
    const [download]=await Promise.all([page.waitForEvent('download'),page.evaluate(()=>{const a=document.createElement('a');a.href='/preview/api/v1/storage/generated/preview-fixture-a/fixture.png';a.download='fixture.png';document.body.append(a);a.click();a.remove();})]);
    assert.ok((await download.path()));report.protected_original_download='PASS';
    const response=await page.request.get(base+'/preview/api/v1/storage/generated/preview-fixture-a/fixture.png');assert.deepEqual(await response.body(),png);
   }
   assert.deepEqual(errors,[]);report.cases.push({scenario,result:'PASS',original_requests:original});await page.close();
  }
  scenario='ok';
  for(const viewport of [{width:1200,height:900},{width:390,height:844},{width:844,height:390}]){
   page=await browser.newPage({viewport});await page.goto(base+'/after');const input=page.locator('#prompt');
   const height=()=>input.evaluate(n=>n.getBoundingClientRect().height);
   await input.fill('一行短文字');await page.waitForTimeout(150);const short=await height();
   await input.fill(Array(5).fill('五行文字').join('\n'));await page.waitForTimeout(150);const five=await height();assert.ok(five>short);
   await input.fill(Array(20).fill('很长的提示词，用于检查输入区域').join('\n'));await page.waitForTimeout(150);
   const long=await height(),cap=Math.min(viewport.width<=860?160:200,viewport.height*.3);assert.ok(long<=cap+1);
   assert.equal(await input.evaluate(n=>getComputedStyle(n).overflowY),'auto');
   const button=await page.locator('#composer-submit').boundingBox(),box=await input.boundingBox();assert.ok(button.y>=box.y+box.height-1);
   assert.ok(button.x+button.width>=box.x+box.width-12);
   assert.ok(button.y+button.height<=viewport.height+1);
   await input.fill('删除后恢复');await page.waitForTimeout(150);assert.ok(Math.abs((await height())-short)<1);
   await page.evaluate(()=>{const n=document.querySelector('#prompt');n.value=Array(8).fill('恢复的草稿').join('\n');bindPreviewComposerResize();});await page.waitForTimeout(150);assert.ok(await height()>short);
   await page.locator('#composer-submit').click();await page.waitForTimeout(150);assert.equal(await input.inputValue(),'');assert.ok(Math.abs((await height())-short)<1);
   for(const agent of ['image-agent','campaign-agent','copywriting-agent','preview-wechat-agent']){
    await page.evaluate(agent=>{document.querySelector('#main').dataset.agentId=agent;const n=document.querySelector('#prompt');n.value=Array(7).fill('当前草稿').join('\n');bindPreviewComposerResize();},agent);await page.waitForTimeout(150);assert.ok(await height()>short);assert.ok((await input.inputValue()).includes('当前草稿'));
   }
   await page.evaluate(()=>{const n=document.querySelector('#prompt'),value=Array(20).fill('粘贴输入不丢失').join('\n');n.value=value;n.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertFromPaste',data:value}));});await page.waitForTimeout(150);assert.ok(await height()<=cap+1);
   await page.evaluate(()=>{const n=document.querySelector('#prompt');n.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:'中'}));n.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true,data:'中文'}));n.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'中文'}));});assert.ok((await input.inputValue()).includes('粘贴输入不丢失'));
   await page.evaluate(()=>{const file=document.createElement('div');file.textContent='Fixture 已上传参考图';file.style.height='40px';document.querySelector('#composer').prepend(file);bindPreviewComposerResize();});await page.waitForTimeout(150);const footer=await page.locator('#composer-submit').boundingBox();assert.ok(footer.y+footer.height<=viewport.height+1);
   await page.screenshot({path:path.join(out,'fixture-'+viewport.width+'x'+viewport.height+'.png'),fullPage:true});
   report.cases.push({scenario:'composer-'+viewport.width+'x'+viewport.height,result:'PASS',short,five,long,cap});await page.close();
  }
  report.candidate_files={};for(const f of ['app/static/preview-image-display.js','app/static/preview-illustrations.js','app/static/workbench.js','app/static/workbench.css','tests/preview_image_loading_p0.cjs'])report.candidate_files['enterprise_agent_poc/'+f]=crypto.createHash('sha256').update(fs.readFileSync(path.resolve(__dirname,'..',f))).digest('hex');
  fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close();await new Promise(r=>server.close(r));}
})().catch(error=>{console.error(error);process.exitCode=1;});
