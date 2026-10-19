// FIXTURE_ONLY actual local HTTP/browser navigation. No generation submissions.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
(async()=>{
 const base=process.env.SIX_FIXTURE_BASE,out=process.env.SIX_FIXTURE_OUT;
 assert.ok(base?.startsWith('http://127.0.0.1:'));assert.ok(out);fs.mkdirSync(out,{recursive:true});
 const old=cp.execFileSync('git',['show','75599da1a07774b0ee010756899617e68fe46693:enterprise_agent_poc/app/static/workbench.js'],{encoding:'utf8',maxBuffer:2e6});
 const browser=await chromium.launch({executablePath:'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',headless:true});
 const result={mode:'FIXTURE_ONLY_LOCAL_HTTP_BROWSER',injected_read_latency_ms:150,paid_calls:0,samples:[]};
 try{
  for(const version of ['before','after']){
   const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
   page.setDefaultTimeout(20000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/preview/static/workbench.js?*',async route=>version==='before'?route.fulfill({contentType:'application/javascript',body:old}):route.continue());
   await page.route('**/preview/api/v1/**',async route=>{
    assert.equal(route.request().method(),'GET','Navigation must never create tasks');
    if(/\/(?:workspace|conversations|agents\/[^/]+)(?:\?|$)/.test(route.request().url()))await new Promise(resolve=>setTimeout(resolve,150));
    await route.continue();
   });
   await page.goto(base+'/agents/copywriting');await page.locator('#prompt').waitFor();
   for(const target of ['campaign','copywriting','image','campaign']){
    const started=Date.now();await page.locator('button[data-page="'+target+'"]:visible').first().click();
    await page.locator('#prompt').waitFor();
    result.samples.push({version,target,ready_ms:Date.now()-started});
   }
   assert.deepEqual(errors,[]);await page.screenshot({path:path.join(out,version+'-1440.png'),fullPage:true});
   await page.setViewportSize({width:375,height:850});await page.screenshot({path:path.join(out,version+'-375.png'),fullPage:true});
   await context.close();
  }
  for(const version of ['before','after']){
   const values=result.samples.filter(v=>v.version===version).map(v=>v.ready_ms).sort((a,b)=>a-b);
   result[version+'_median_ms']=(values[1]+values[2])/2;
  }
  assert.ok(result.after_median_ms<result.before_median_ms);
  fs.writeFileSync(path.join(out,'navigation-result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
