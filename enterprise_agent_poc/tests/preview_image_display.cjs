// FIXTURE ONLY. Same real Chrome, images and HTTP pacing before/after; no Provider.
const {chromium}=require('playwright'),fs=require('node:fs'),path=require('node:path'),http=require('node:http'),cp=require('node:child_process'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const out=process.env.PREVIEW_IMAGE_DISPLAY_OUT;assert.ok(out);fs.mkdirSync(out,{recursive:true});
const root=path.resolve(__dirname,'../app/static'),baseSource='cafb13fdff6310b0e4de6a7fe4288b8e920e332c';
const old=name=>cp.execFileSync('git',['show',baseSource+':enterprise_agent_poc/app/static/'+name],{encoding:'utf8',maxBuffer:2e6});
const baseline={article:old('wechat-article-ui.js'),gallery:old('preview-illustrations.js')};
cp.execFileSync('python',['-c',`from PIL import Image
from pathlib import Path
p=Path(${JSON.stringify(out)})
if not (p/'original.png').exists():
 im=Image.effect_noise((1536,1024),70).convert('RGB');im.save(p/'original.png')
 im.thumbnail((720,720));im.save(p/'thumbnail.webp',quality=80)
`]);
const original=fs.readFileSync(path.join(out,'original.png')),thumb=fs.readFileSync(path.join(out,'thumbnail.webp'));
let version='before',principal='a',progress=4;const requests=[];const ids=Array.from({length:7},(_,i)=>(i+1).toString(16).padStart(32,'0'));
const source=id=>'/api/v1/preview/illustrations/assets/'+id;
const asset=(id,index)=>({id,url:source(id),label:'FIXTURE 教育场景 '+index,width:1536,height:1024,filename:'fixture-'+index+'.png',position:index});
const article=n=>'<article><h1>FIXTURE 图片展示性能</h1>'+ids.slice(0,n).map((id,i)=>'<h2>章节 '+i+'</h2><p>真实浏览器测试，不是模型生成或公网验收。<strong>重点文字</strong></p><figure><img src="'+source(id)+'" alt="FIXTURE '+i+'"></figure>').join('')+'</article>';
const plan=()=>({id:'c'.repeat(32),kind:'wechat',status:progress===7?'completed':'generating',count:7,assets:ids.slice(0,progress).map(asset),html:article(progress),publication:{},outcomes:[]});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const server=http.createServer(async(req,res)=>{
 const p=new URL(req.url,'http://localhost').pathname;
 if(req.method!=='GET'){res.writeHead(405);return res.end();}
 const json=value=>{res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
 if(p.endsWith('/api/v1/me')){await sleep(20);return json({tenant_id:'preview-fixture-'+principal,user_id:'fixture-'+principal});}
 if(p.includes('/illustrations/tasks/'))return json({plan:plan()});
 if(p.includes('/illustrations/assets/')||p.includes('/image-display/')){
  requests.push({version,p,principal,thumbnail:p.includes('/image-display/')});
  if(principal!=='a'){res.writeHead(404);return res.end();}
  const data=p.includes('/image-display/')?thumb:original;await sleep(70);
  res.writeHead(200,{'Content-Type':p.includes('/image-display/')?'image/webp':'image/png','Content-Length':data.length,'Cache-Control':'private, no-store'});
  for(let i=0;i<data.length;i+=65536){res.write(data.subarray(i,i+65536));await sleep(6);}return res.end();
 }
 if(p.startsWith('/preview/static/')){
  const name=path.basename(p),content=version==='before'&&name==='wechat-article-ui.js'?baseline.article:version==='before'&&name==='preview-illustrations.js'?baseline.gallery:fs.readFileSync(path.join(root,name));
  res.writeHead(200,{'Content-Type':'application/javascript'});return res.end(content);
 }
 res.writeHead(200,{'Content-Type':'text/html'});
 res.end('<!doctype html><meta charset="utf-8"><title>FIXTURE image display</title><style>body{margin:0 auto;max-width:900px;font-family:sans-serif}iframe{width:100%;border:0}.preview-illustration-gallery{display:grid;grid-template-columns:repeat(3,1fr)}figure{margin:10px}img{max-width:100%;height:auto}</style><script>window.WORKBENCH_PREVIEW_BASE="/preview";window.workbenchPreviewUrl=v=>{const u=new URL(v,location.origin);if(!u.pathname.startsWith("/preview/"))u.pathname="/preview"+u.pathname;return u.href};window.bindModalDialog=()=>()=>{};</script>'+(version==='after'?'<script src="/preview/static/preview-image-display.js"></script>':'')+'<script src="/preview/static/wechat-article-ui.js"></script><main id="main" data-agent-id="preview-wechat-agent"></main>');
});
const p50=a=>[...a].sort((a,b)=>a-b)[Math.floor(a.length/2)];
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({executablePath:'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',headless:true});
 const report={mode:'FIXTURE_ONLY_LOCAL_HTTP_REAL_CHROME',public_browser:'NOT_VERIFIED',provider_calls:0,samples:[],original_bytes:original.length,thumbnail_bytes:thumb.length};
 try{
  for(version of ['before','after']){
   principal='a';const begin=requests.length;
   for(let sample=0;sample<3;sample++){
    const context=await browser.newContext({viewport:{width:1280,height:1000},permissions:['clipboard-read','clipboard-write']}),page=await context.newPage();page.setDefaultTimeout(30000);
    await page.addInitScript(()=>{
     window.displayMetrics={canvasMs:0,canvasCount:0,bitmapMs:0,bitmapCount:0,srcdocWrites:0};
     const encode=HTMLCanvasElement.prototype.toDataURL;HTMLCanvasElement.prototype.toDataURL=function(...args){const t=performance.now();try{return encode.apply(this,args);}finally{displayMetrics.canvasMs+=performance.now()-t;displayMetrics.canvasCount++;}};
     const decode=createImageBitmap;window.createImageBitmap=async(...args)=>{const t=performance.now();try{return await decode(...args);}finally{displayMetrics.bitmapMs+=performance.now()-t;displayMetrics.bitmapCount++;}};
     new MutationObserver(records=>{displayMetrics.srcdocWrites+=records.filter(r=>r.attributeName==='srcdoc').length;}).observe(document,{attributes:true,attributeFilter:['srcdoc'],subtree:true});
    });
    page.on('pageerror',error=>console.error('FIXTURE_PAGE_ERROR',error.message));await page.goto(base+'/preview/test');
    const mount=()=>page.evaluate(html=>{
     const main=document.querySelector('#main');main.innerHTML='<article data-assistant-message-id="fixture-article"><div class="chat-message-content"></div></article>';
     const a=main.firstChild;const view=window.WorkbenchWechatArticle.mountManual({article:a,main,agent:{slug:'wechat-official-account-writing'},content:html,messageId:'fixture-article',markdown:s=>s,modal:()=>{},isCurrent:()=>a.isConnected,inlineBody:true});window.fixtureHtml=view.result.html;return performance.now();
    },article(7));
    const beforeOpen=requests.length,started=await mount();
    await page.waitForFunction(()=>{const f=document.querySelector('.wechat-inline-article');return f?.contentDocument&&[...f.contentDocument.images].some(i=>i.complete&&i.naturalWidth>1);});
    const first=await page.evaluate(()=>performance.now());
    await page.waitForFunction(()=>{const f=document.querySelector('.wechat-inline-article');return f?.dataset.articleReady==='true'&&f.contentDocument.images.length===7&&[...f.contentDocument.images].every(i=>i.complete&&i.naturalWidth>1);});
    const all=await page.evaluate(()=>performance.now()),initial=await page.evaluate(()=>({...displayMetrics}));
    const initialRequests=requests.length-beforeOpen;
    const beforeReopen=requests.length,reopened=await mount();
    await page.waitForFunction(()=>{const f=document.querySelector('.wechat-inline-article');return f?.dataset.articleReady==='true'&&f.contentDocument.images.length===7&&[...f.contentDocument.images].every(i=>i.complete&&i.naturalWidth>1);});
    const end=await page.evaluate(()=>performance.now()),metrics=await page.evaluate(()=>({...displayMetrics}));
    report.samples.push({version,sample,first_ms:Math.round(first-started),all_ms:Math.round(all-started),reopen_ms:Math.round(end-reopened),initial_image_requests:initialRequests,reopen_image_requests:requests.length-beforeReopen,initial,after_reopen:metrics});
    if(version==='after'){assert.equal(requests.length-beforeReopen,0);assert.equal(metrics.canvasCount,7);assert.equal(metrics.bitmapCount,7);}
    if(sample===0){
     report[version+'_export_html_sha256']=crypto.createHash('sha256').update(await page.evaluate(()=>fixtureHtml)).digest('hex');
     await page.screenshot({path:path.join(out,version+'-article.png')});
     if(version==='after'){
      const frame=page.frameLocator('.wechat-inline-article');assert.equal(await frame.locator('img').first().evaluate(i=>i.naturalWidth),1536);
      await page.evaluate(()=>WorkbenchWechatArticle.copyRenderedArticle(document.querySelector('.wechat-inline-article')));
      const clipboard=await page.evaluate(async()=>{for(const item of await navigator.clipboard.read())if(item.types.includes('text/html'))return {types:item.types,html:await(await item.getType('text/html')).text()};});
      assert.ok(clipboard.types.includes('text/plain'));assert.equal((clipboard.html.match(/data:image\/png;base64,/g)||[]).length,7);
      const actualFrame=await(await page.locator('.wechat-inline-article').elementHandle()).contentFrame();
      await actualFrame.locator('body').click();await page.keyboard.press('Control+A');await page.keyboard.press('Control+C');
      const manual=await page.evaluate(async()=>{for(const item of await navigator.clipboard.read())if(item.types.includes('text/html'))return await(await item.getType('text/html')).text();});
      const normalize=html=>page.evaluate(html=>{const t=document.createElement('template');t.innerHTML=html;return {text:t.content.textContent.replace(/\s+/g,' ').trim(),sources:[...t.content.querySelectorAll('img')].map(n=>n.src)};},html);
      assert.deepEqual(await normalize(clipboard.html),await normalize(manual));report.copy='PASS_REAL_FIXTURE_NATIVE_CLIPBOARD_FULL_RESOLUTION';
      const invalid=await page.evaluate(()=>WorkbenchWechatArticle.sanitizeArticle('<p>正文</p><img src="https://evil.invalid/x"><script>window.PWNED=1</script>'));
      assert.ok(!invalid.includes('<script')&&!invalid.includes('<img'));report.sanitizer='PASS';
     }
     // Real progress polling: 4 -> 5 -> 7. Keep the same first 4 image nodes.
     progress=4;await page.evaluate(()=>{document.querySelector('#main').innerHTML='<article data-assistant-message-id="task:11111111-1111-4111-8111-111111111111:assistant"><div class="chat-message-content"></div></article>';});
     await page.addScriptTag({url:base+'/preview/static/preview-illustrations.js'});
     await page.waitForFunction(()=>document.querySelectorAll('.preview-illustration-gallery img').length===4);
     await page.evaluate(()=>{window.firstFour=[...document.querySelectorAll('.preview-illustration-gallery img')];window.firstFrame=document.querySelector('.wechat-inline-article');});
     progress=5;await page.waitForFunction(()=>document.querySelectorAll('.preview-illustration-gallery img').length===5);
     const reused=await page.evaluate(()=>firstFour.every((n,i)=>n===document.querySelectorAll('.preview-illustration-gallery img')[i]));
     report[version+'_4_to_5_dom_reused']=reused;if(version==='after')assert.equal(reused,true);
     progress=7;await page.waitForFunction(()=>document.querySelectorAll('.preview-illustration-gallery img').length===7);
     await page.evaluate(()=>window.scrollTo(0,document.body.scrollHeight));
     await page.waitForFunction(()=>[...document.querySelectorAll('.preview-illustration-gallery img')].every(i=>i.complete&&i.naturalWidth>1));
     if(version==='after'){
      assert.equal(await page.evaluate(()=>firstFrame===document.querySelector('.wechat-inline-article')),true);report.incremental_frame_reuse='PASS';
      const f=await page.locator('.wechat-inline-article').elementHandle();assert.ok(f);report.gallery='PASS_4_TO_5_INCREMENTAL';
      await page.screenshot({path:path.join(out,'after-gallery.png')});
      // Real result-card markup: thumbnail display, but original link/download.
      const workbench=fs.readFileSync(path.join(root,'workbench.js'),'utf8');
      const display=workbench.split('\n').find(s=>s.startsWith('const generationDisplayAttributes='));
      const card=workbench.split('\n').find(s=>s.startsWith('const image = '));
      const href=await page.evaluate(({display,card,url})=>{
       const create=new Function('escapeHtml','storageUrl','icon',display+'\n'+card+'\nreturn image;')(s=>String(s),()=>workbenchPreviewUrl(url),()=> '');
       const host=document.createElement('div');host.id='fixture-result-card';host.innerHTML=create({id:'task:12345678-1234-4234-8234-123456789abc:generation:1',storage_key:'FIXTURE'},'FIXTURE 高清图');document.querySelector('#main').append(host);host.scrollIntoView();return host.querySelector('a').href;
      },{display,card,url:source(ids[0])});
      await page.waitForFunction(()=>document.querySelector('#fixture-result-card img')?.naturalWidth===720);
      const download=await context.request.get(href);assert.equal(download.status(),200);assert.deepEqual(await download.body(),original);
      report.result_card_thumbnail='PASS';report.original_download='PASS_FIXTURE_BYTE_IDENTICAL';
      // Force a TTL expiry without waiting five minutes; then test LRU bounds.
      const cacheCases=await page.evaluate(async source=>{
       const clock=Date.now;Date.now=()=>clock()+301000;
       try{await WorkbenchPreviewImages.raster(workbenchPreviewUrl(source));}finally{Date.now=clock;}
       for(let i=30;i<56;i++)await WorkbenchPreviewImages.raster(workbenchPreviewUrl('/api/v1/preview/image-display/illustrations/'+i.toString(16).padStart(32,'0')));
       return WorkbenchPreviewImages.stats();
      },source(ids[0]));assert.ok(cacheCases.entries<=24&&cacheCases.bytes<=64*1024*1024);report.bounded_lru_ttl='PASS';
      principal='b';await page.evaluate(()=>WorkbenchPreviewImages.verifyIdentity());
      assert.equal(await page.evaluate(()=>document.querySelector('.wechat-inline-article').contentDocument.images.length),0);
      const denied=await page.evaluate(async url=>{try{await WorkbenchPreviewImages.raster(workbenchPreviewUrl(url));return false;}catch{return true;}},source(ids[0]));assert.equal(denied,true);
      await page.evaluate(()=>WorkbenchPreviewImages.setIdentity(null));
      report.identity_cache_invalidation='PASS';principal='a';
     }
    }
    await context.close();
   }
   report[version+'_network_image_requests']=requests.slice(begin).filter(r=>r.principal==='a').length;
   const samples=report.samples.filter(s=>s.version===version);
   for(const metric of ['first_ms','all_ms','reopen_ms'])report[version+'_'+metric+'_p50']=p50(samples.map(s=>s[metric]));
  }
  const sha=crypto.createHash('sha256').update(original).digest('hex');assert.equal(sha,crypto.createHash('sha256').update(fs.readFileSync(path.join(out,'original.png'))).digest('hex'));
  assert.equal(report.before_export_html_sha256,report.after_export_html_sha256);report.export_html_unchanged='PASS';
  for(const version of ['before','after'])report[version+'_open_and_reopen_image_requests']=p50(report.samples.filter(s=>s.version===version).map(s=>s.initial_image_requests+s.reopen_image_requests));
  report.candidate_files={};for(const file of ['app/static/customer-preview.js','app/static/preview-illustrations.js','app/static/wechat-article-ui.js','app/static/workbench.js','app/static/preview-image-display.js','app/preview_image_display.py','scripts/customer_preview.py','tests/test_preview_image_display.py','tests/preview_image_display.cjs'])report.candidate_files['enterprise_agent_poc/'+file]=crypto.createHash('sha256').update(fs.readFileSync(path.join(__dirname,'..',file))).digest('hex');
  report.original_fixture_hash='PASS';fs.writeFileSync(path.join(out,'performance.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close();server.closeAllConnections();server.close();}
})().catch(e=>{console.error(e);server.closeAllConnections();server.close();process.exitCode=1;});
