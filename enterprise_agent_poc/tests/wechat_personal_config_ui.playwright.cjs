// Deterministic Workbench browser acceptance: all APIs are fixtures, live calls 0.
const assert=require('node:assert/strict');
const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const staticDir=path.join(__dirname,'../app/static');
const secret='synthetic-ui-secret-never-returned';
const appid='wx1111111111111111';
async function main(){
  const server=http.createServer((request,response)=>{
    const route=new URL(request.url,'http://localhost').pathname;
    const file=route.startsWith('/static/')?path.join(staticDir,route.slice(8)):path.join(staticDir,'index.html');
    if(!file.startsWith(staticDir+path.sep)||!fs.existsSync(file)){response.writeHead(404).end();return;}
    response.writeHead(200,{'content-type':file.endsWith('.css')?'text/css':file.endsWith('.js')?'application/javascript':'text/html'});response.end(fs.readFileSync(file));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const browser=await chromium.launch({headless:true,...(process.env.WECHAT_UI_BROWSER?{executablePath:process.env.WECHAT_UI_BROWSER}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
  let state={wechat_app_id:null,account_display_name:null,app_secret_configured:false,verification_status:'unconfigured',verified_at:null,verification_error_code:null};
  let role='enterprise_admin',testMode='connected',releaseTest=null,deletes=0,saveCount=0;
  const payloads=[],errors=[],responseBodies=[];
  const agent={id:'11111111-1111-4111-8111-111111111111',slug:'wechat-official-account-writing',name:'公众号运营助手',icon:'file-text',description:'生成公众号文章',category:'内容创作',enabled:true};
  page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.origin!==origin)return route.abort();
    if(!url.pathname.startsWith('/api/'))return route.continue();
    const json=(value,status=200)=>{const body=JSON.stringify(value);responseBodies.push(body);return route.fulfill({status,contentType:'application/json',body});};
    if(url.pathname==='/api/v1/me')return json({user_id:'ui-admin',tenant_id:'ui-tenant',role,display_name:'测试用户',email:'ui@example.invalid'});
    if(url.pathname==='/api/v1/workspace')return json({agents:[agent],tenant_name:'测试企业',credit_balance:100});
    if(url.pathname.startsWith('/api/v1/conversations'))return json([]);
    if(url.pathname==='/api/v1/agents/wechat-official-account-writing')return json(agent);
    if(url.pathname==='/api/v1/profile/wechat-account'&&request.method()==='GET')return json(state);
    if(url.pathname==='/api/v1/profile/wechat-account'&&request.method()==='PUT'){
      const input=JSON.parse(request.postData());payloads.push(input);saveCount++;
      assert.equal(input.connected,undefined);assert.equal(input.tenant_id,undefined);
      state={...state,wechat_app_id:input.wechat_app_id,account_display_name:input.account_display_name||'微信公众号',app_secret_configured:true,verification_status:'unverified',verified_at:null,verification_error_code:null};
      return json(state);
    }
    if(url.pathname==='/api/v1/profile/wechat-account/test-connection'){
      assert.deepEqual(JSON.parse(request.postData()),{});
      await new Promise(resolve=>{releaseTest=resolve;});
      state={...state,verification_status:testMode,verified_at:'2026-10-08T00:00:00Z',verification_error_code:testMode==='failed'?'WECHAT_IP_NOT_ALLOWED':null};
      return json(state);
    }
    if(url.pathname==='/api/v1/profile/wechat-account'&&request.method()==='DELETE'){
      deletes++;state={wechat_app_id:null,account_display_name:null,app_secret_configured:false,verification_status:'unconfigured'};return json(state);
    }
    return json({error_code:'NOT_FOUND',user_message:'fixture missing'},404);
  });
  const feedback=()=>page.locator('.wechat-account-feedback');
  const card=()=>page.locator('#wechat-account-card');
  const waitRelease=async()=>{await page.waitForFunction(()=>document.querySelector('.wechat-account-feedback')?.textContent.includes('正在测试连接'));assert.ok(releaseTest);releaseTest();releaseTest=null;};
  try{
    await page.goto(`${origin}/profile`);
    await page.locator('#wechat-app-secret').waitFor();
    assert.equal(await page.locator('#wechat-app-secret').getAttribute('type'),'password');
    assert.equal(await page.locator('#wechat-app-secret').inputValue(),'');
    assert.ok((await card().textContent()).includes('未配置'));
    if(process.env.WECHAT_UI_EVIDENCE)await page.screenshot({path:path.join(process.env.WECHAT_UI_EVIDENCE,'wechat-unconfigured-1440.png'),fullPage:true});
    await page.locator('#wechat-display-name').fill('合成公众号');await page.locator('#wechat-app-id').fill(appid);await page.locator('#wechat-app-secret').fill(secret);
    await page.getByRole('button',{name:'保存配置',exact:true}).click();
    await feedback().getByText('配置已保存；请测试连接。').waitFor();
    assert.equal(saveCount,1);assert.equal(payloads[0].app_secret,secret);
    assert.equal(await page.locator('#wechat-app-secret').count(),0);
    assert.ok((await card().textContent()).includes('********'));
    assert.ok(!(await page.content()).includes(secret));
    await page.reload();await page.getByRole('button',{name:'修改配置'}).waitFor();
    assert.ok(!(await page.content()).includes(secret));
    await page.getByRole('button',{name:'修改配置'}).click();
    assert.equal(await page.locator('#wechat-app-secret').inputValue(),'');
    await page.locator('#wechat-app-id').fill('wx2222222222222222');await page.getByRole('button',{name:'保存配置',exact:true}).click();
    await feedback().getByText('配置已保存；请测试连接。').waitFor();assert.equal(payloads[1].app_secret,'');
    await page.getByRole('button',{name:'修改配置'}).click();await page.locator('#wechat-app-secret').fill(secret+'-rotation');
    await page.getByRole('button',{name:'保存配置',exact:true}).click();await feedback().getByText('配置已保存；请测试连接。').waitFor();
    assert.equal(payloads[2].app_secret,secret+'-rotation');
    await page.getByRole('button',{name:'测试连接',exact:true}).click();
    await feedback().getByText('正在测试连接…').waitFor();assert.equal(await page.getByRole('button',{name:'测试连接',exact:true}).isDisabled(),true);
    await waitRelease();await feedback().getByText('连接成功').waitFor();assert.ok((await card().textContent()).includes('连接正常'));
    if(process.env.WECHAT_UI_EVIDENCE)await page.screenshot({path:path.join(process.env.WECHAT_UI_EVIDENCE,'wechat-connected-1440.png'),fullPage:true});
    testMode='failed';await page.getByRole('button',{name:'重新验证'}).click();await feedback().getByText('正在测试连接…').waitFor();await waitRelease();
    await feedback().getByText('当前服务器IP未加入微信公众号IP白名单').waitFor();assert.ok((await card().textContent()).includes('验证失败'));
    page.once('dialog',dialog=>dialog.dismiss());await page.getByRole('button',{name:'解除绑定'}).click();assert.equal(deletes,0);
    page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'解除绑定'}).click();await feedback().getByText('已解除绑定。').waitFor();assert.equal(deletes,1);
    await page.setViewportSize({width:375,height:812});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.equal(await page.locator('#wechat-app-secret').inputValue(),'');
    if(process.env.WECHAT_UI_EVIDENCE)await page.screenshot({path:path.join(process.env.WECHAT_UI_EVIDENCE,'wechat-unconfigured-375.png'),fullPage:true});
    await page.goto(`${origin}/agents/wechat-official-account-writing`);await page.getByRole('button',{name:'去配置',exact:true}).waitFor();
    assert.ok((await page.locator('.wechat-config-hint').textContent()).includes('请先在个人中心配置微信公众号'));
    await page.getByRole('button',{name:'去配置',exact:true}).click();await page.locator('#wechat-app-secret').waitFor();assert.equal(new URL(page.url()).pathname,'/profile');
    role='member';state={...state,wechat_app_id:appid,app_secret_configured:true,verification_status:'connected'};
    await page.reload();await page.getByText('仅企业管理员可修改配置、测试连接或解除绑定。').waitFor();
    assert.equal(await page.getByRole('button',{name:'修改配置'}).count(),0);assert.equal(await page.getByRole('button',{name:'解除绑定'}).count(),0);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.ok(!responseBodies.some(body=>body.includes(secret)));
    assert.equal(await page.evaluate(value=>JSON.stringify(localStorage).includes(value)||JSON.stringify(sessionStorage).includes(value),secret),false);
    assert.deepEqual(errors,[]);
    process.stdout.write('Frontend acceptance 1-9 PASS; 1440/375 PASS; synthetic fixture APIs only; WeChat/Provider calls 0\n');
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
