// Isolated browser integration fixture. No real session or Provider endpoint.
const {chromium}=require('playwright'),http=require('node:http'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../app/static'),out=process.env.PREVIEW_IMAGE_INTENT_OUT;assert.ok(out);fs.mkdirSync(out,{recursive:true});
const calls=[];const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const server=http.createServer(async(req,res)=>{
 const p=new URL(req.url,'http://localhost').pathname;
 const json=value=>{res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(value));};
 if(p==='/preview/api/health')return json({image_business:{enabled:true,max_reference_images:5}});
 if(req.method==='POST'){
  let body='';for await(const b of req)body+=b;const data=JSON.parse(body);calls.push({p,data});
  if(p.endsWith('/plan')){await sleep(40);return json({count:3,size:'3840x2160',quality:'high',plan_digest:'fixture-canonical-plan'});}
  if(p.endsWith('/runs'))return json({id:'fixture-task',status:'queued'});
  res.writeHead(404);return res.end();
 }
 if(p==='/preview/static/customer-preview.js'){res.writeHead(200,{'content-type':'application/javascript'});return res.end(fs.readFileSync(path.join(root,'customer-preview.js')));}
 res.writeHead(200,{'content-type':'text/html'});
 res.end(`<!doctype html><meta charset="utf-8"><title>FIXTURE — 图片自然语言输入</title><style>body{font:17px sans-serif;max-width:900px;margin:50px auto}textarea{width:95%;height:130px}button{padding:12px}aside{color:#666}</style><div id="app"><h1>图片创作 · 隔离浏览器Fixture</h1><aside>仅验证真实前端脚本与请求衔接，不是公网业务或模型生成验收。</aside><main id="main" data-agent-id="image-agent"><form id="composer"><textarea id="prompt"></textarea><button type="submit" id="send">发送</button></form></main></div><script>
 const main=document.querySelector('#main');main._conversationContext={conversationId:'fixture-existing-project',agentId:'image-agent'};main._chatImage={status:'idle',items:[]};
 document.querySelector('#composer').addEventListener('submit',async event=>{event.preventDefault();await fetch('/api/v1/agents/image-agent/runs',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message:document.querySelector('#prompt').value,conversation_id:main._conversationContext.conversationId,attachments:main._chatImage.items.map(x=>({type:'image',id:x.record.id}))})});window.fixtureSubmitted=true;});
 </script><script src="/preview/static/customer-preview.js"></script>`);
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({executablePath:'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:800}}),errors=[];let dialogs=0;
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',async d=>{dialogs++;await d.dismiss();});
  await page.goto(base+'/preview/');await page.waitForFunction(()=>!document.querySelector('#send').disabled);
  await page.fill('#prompt','做3张横版教育海报，第一张招生，第二张课程介绍，第三张品牌宣传');
  await page.click('#send');await page.waitForFunction(()=>window.fixtureSubmitted);
  assert.equal(calls.length,2);assert.ok(calls[0].p.endsWith('/plan')&&calls[1].p.endsWith('/runs'));
  const plan=calls[0].data,run=calls[1].data;
  assert.equal(plan.conversation_id,'fixture-existing-project');assert.equal(run.conversation_id,plan.conversation_id);
  assert.equal(run.preview_plan_digest,'fixture-canonical-plan');assert.equal(run.quality,'high');
  assert.equal(run.use_enterprise_context,true);assert.equal(run.preview_image_confirmed,true);
  assert.ok(!('size' in plan)&&!('size' in run));assert.equal(await page.locator('select').count(),0);assert.equal(dialogs,0);
  calls.length=0;
  await page.evaluate(()=>{window.fixtureSubmitted=false;document.querySelector('#main')._chatImage={status:'ready',items:[{record:{id:'fixture-ref-a'},role:'style'},{record:{id:'fixture-ref-b'},role:'subject'}]};});
  await page.fill('#prompt','上传2张参考图，生成1张方图');await page.click('#send');await page.waitForFunction(()=>window.fixtureSubmitted);
  assert.deepEqual(calls[0].data.attachments,calls[1].data.attachments);assert.equal(calls[1].data.attachments.length,2);
  assert.equal(calls.length,2);assert.equal(dialogs,0);assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(out,'image-intent-fixture.png'),fullPage:true});
  fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({mode:'ISOLATED_REAL_CHROME_FIXTURE',frontend_context_and_digest:'PASS',multi_reference_order:'PASS',no_size_or_quality_controls:'PASS',no_confirmation_dialogs:'PASS',public_browser:'NOT_VERIFIED',new_paid_calls:0,errors},null,2));
  console.log('PASS: isolated real Chrome, context/digest, ordered references, no selectors/dialogs. Provider calls=0.');
 }finally{await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
