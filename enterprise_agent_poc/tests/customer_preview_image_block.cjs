// Admission-stop validation only: no Run or Provider request may be accepted.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.WECHAT_UI_BROWSER,headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:960}}),base='https://workbench.luckio.cn/preview';let runPosts=0;
  page.on('request',r=>{if(r.method()==='POST'&&/\/agents\/[^/]+\/runs$/.test(new URL(r.url()).pathname))runPosts++});
  await page.goto(base+'/agents/image');await page.locator('#preview-image-unavailable').waitFor();
  assert.equal(await page.locator('.sidebar [data-page="image"] span').innerText(),'图片生成（暂不可用）');
  assert.equal(await page.locator('#composer-submit').isDisabled(),true);assert.equal(await page.locator('#prompt').isDisabled(),true);
  await page.evaluate(()=>document.querySelector('#composer').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));assert.equal(runPosts,0);
  for(const width of [1440,375]){await page.setViewportSize({width,height:960});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
  const before=await page.evaluate(()=>fetch('/preview/api/v1/preview-status').then(r=>r.json()));
  // The installed server must reject before Task creation, even without JS.
  const rejected=await page.evaluate(async()=>{const r=await fetch('/preview/api/v1/agents/image-agent/runs',{method:'POST',headers:{'content-type':'application/json'},body:'{}'});return {status:r.status,body:await r.json()}});
  assert.equal(rejected.status,503);assert.equal(rejected.body.error_code,'IMAGE_APPROVAL_EXTERNAL_BLOCKED');assert.ok(!rejected.body.id);
  const after=await page.evaluate(()=>fetch('/preview/api/v1/preview-status').then(r=>r.json()));assert.deepEqual(after.budget,before.budget);
  await page.screenshot({path:process.env.PREVIEW_REAL_EVIDENCE+'/image-unavailable-375.png'});
  const report={mode:'ADMISSION_STOP_ONLY_NOT_IMAGE_E2E',visible_unavailable:true,submit_disabled:true,captured_form_posts:0,server_rejection:rejected,budget_unchanged:true,new_provider_calls:0};
  fs.writeFileSync(process.env.PREVIEW_REAL_EVIDENCE+'/image-unavailable-proof.json',JSON.stringify(report,null,2));console.log('IMAGE_ADMISSION_STOP_PASS');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
