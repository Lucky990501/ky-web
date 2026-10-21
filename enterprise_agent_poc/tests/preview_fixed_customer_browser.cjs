// FIXTURE_ONLY local HTTP + real Chrome. No real account, cookie or Provider.
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../app/static'),out=process.env.PREVIEW_FIXED_OUT;
assert.ok(out);fs.mkdirSync(out,{recursive:true});let changed=false;const errors=[];
const server=http.createServer(async(req,res)=>{
 const p=new URL(req.url,'http://fixture').pathname;
 if(p==='/preview/login'){res.setHeader('Content-Type','text/html');return res.end(fs.readFileSync(path.join(root,'preview-login.html')));}
 if(p.startsWith('/preview/static/')){const name=path.basename(p);assert.ok(['preview-login.css','preview-login.js'].includes(name));res.setHeader('Content-Type',name.endsWith('.css')?'text/css':'application/javascript');return res.end(fs.readFileSync(path.join(root,name)));}
 if(p==='/preview/'){res.setHeader('Content-Type','text/html');return res.end('<h1>FIXTURE fixed enterprise space</h1>');}
 let raw='';for await(const chunk of req)raw+=chunk;const b=JSON.parse(raw||'{}');res.setHeader('Content-Type','application/json');
 if(p.endsWith('/login')){
  if(b.password!==(changed?'FIXTURE_NEW_PASSWORD':'FIXTURE_INITIAL_PASSWORD')){res.statusCode=401;return res.end(JSON.stringify({user_message:'账号或密码不正确。'}));}
  return res.end(JSON.stringify({password_change_required:!changed,next:'/preview/'}));
 }
 if(p.endsWith('/password')){changed=true;return res.end(JSON.stringify({next:'/preview/'}));}
 res.statusCode=404;res.end('{}');
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const base='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({executablePath:'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',headless:true});
 try{
  for(const width of [1440,375]){
   const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
   await page.goto(base+'/preview/login');
   assert.equal(await page.locator('#account').getAttribute('autocomplete'),'username');
   assert.equal(await page.locator('#password').getAttribute('autocomplete'),'current-password');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   await page.screenshot({path:path.join(out,'login-'+width+'.png'),fullPage:true});
   await page.locator('#account').fill('fixture@preview.luckio.cn');await page.locator('#password').fill('wrong');await page.locator('#login-form [type=submit]').click();
   await page.getByText('账号或密码不正确。',{exact:true}).waitFor();
   await page.locator('#password').fill(changed?'FIXTURE_NEW_PASSWORD':'FIXTURE_INITIAL_PASSWORD');await page.locator('#login-form [type=submit]').click();
   if(!changed){
    await page.locator('#setup-form:visible').waitFor();await page.locator('#new-password').fill('FIXTURE_NEW_PASSWORD');await page.locator('#repeat-password').fill('MISMATCH_PASSWORD');await page.locator('#setup-form [type=submit]').click();
    await page.getByText('两次输入的密码不一致。',{exact:true}).waitFor();await page.locator('#repeat-password').fill('FIXTURE_NEW_PASSWORD');await page.locator('#setup-form [type=submit]').click();
   }
   await page.waitForURL(base+'/preview/');assert.equal(await page.locator('h1').textContent(),'FIXTURE fixed enterprise space');
   await context.close();
  }
  assert.deepEqual(errors,[]);console.log(JSON.stringify({mode:'FIXTURE_ONLY_REAL_CHROME',desktop_mobile:'PASS',wrong_password:'PASS',first_password_change:'PASS',new_browser_login:'PASS',horizontal_overflow:false,js_errors:0,new_paid_calls:0}));
 }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;server.close();});
