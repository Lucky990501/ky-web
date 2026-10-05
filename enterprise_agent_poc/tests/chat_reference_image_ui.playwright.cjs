// Deterministic browser acceptance for the chat reference-image composer.
// Run explicitly with a local Playwright installation; no model or provider calls.
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const staticDir = path.join(__dirname, '../app/static');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
const agent = {id:'image-agent',slug:'image',name:'图片生成智能体',icon:'image',description:'根据品牌和场景需求生成视觉内容',category:'image',credit_cost:20,enabled:true};

async function main() {
  const server = http.createServer((request,response)=>{
    const pathname = new URL(request.url,'http://localhost').pathname;
    const filename = pathname.startsWith('/static/')
      ? path.join(staticDir, pathname.slice('/static/'.length))
      : path.join(staticDir,'index.html');
    if (!filename.startsWith(staticDir + path.sep) || !fs.existsSync(filename)) {response.writeHead(404).end();return;}
    const type = filename.endsWith('.css')?'text/css':filename.endsWith('.js')?'application/javascript':filename.endsWith('.png')?'image/png':'text/html';
    response.writeHead(200,{'content-type':type});response.end(fs.readFileSync(filename));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser = await chromium.launch({headless:true,
    ...(process.env.CHAT_IMAGE_BROWSER_EXECUTABLE?{executablePath:process.env.CHAT_IMAGE_BROWSER_EXECUTABLE}:{})});
  const context = await browser.newContext({viewport:{width:1440,height:900}});
  const page = await context.newPage();
  const state = {uploads:0,deletes:0,runPayloads:[],hasConversation:false};
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const attachment = id=>({type:'image',id,filename:'reference.png',mime_type:'image/png',width:1,height:1,size_bytes:png.length,content_url:`/api/v1/chat-images/${id}`});
  const generation = {id:'generation-1',task_id:'task-1',storage_key:'generated/tenant-test/result.png',content_url:'/api/v1/storage/generated/tenant-test/result.png'};
  await page.route('**/api/v1/**',async route=>{
    const request=route.request(),url=new URL(request.url()),pathname=url.pathname,method=request.method();
    const json=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
    if(pathname==='/api/v1/me')return json({id:'user-1',tenant_id:'tenant-test',role:'member',display_name:'测试用户'});
    if(pathname==='/api/v1/workspace')return json({agents:[agent],tenant_name:'测试企业',brand_name:'测试企业',credit_balance:100});
    if(pathname==='/api/v1/conversations'&&method==='GET')return json(state.hasConversation?[{
      id:'conversation-1',agent_id:'image-agent',agent,project:{name:'图片项目',type:'图片生成项目'},
      latest_status:'completed',latest_task:{id:'task-1',status:'completed'},latest_generation:generation,latest_prompt:'把背景换成未来都市',
    }]:[]);
    if(pathname==='/api/v1/conversations/conversation-1')return json({
      id:'conversation-1',agent_id:'image-agent',agent,project:{name:'图片项目',type:'图片生成项目'},tasks:[],generations:[generation],
      messages:[{id:'task:task-1:user',role:'user',content:'把背景换成未来都市',attachments:[attachment('upload-3')]},
        {id:'task:task-1:assistant',role:'assistant',content:'图片已生成。',generation}],
    });
    if(pathname==='/api/v1/chat-images'&&method==='POST')return json(attachment(`upload-${++state.uploads}`),201);
    if(pathname.startsWith('/api/v1/chat-images/')&&method==='DELETE'){state.deletes++;return json({status:'deleted'});}
    if(pathname.startsWith('/api/v1/chat-images/')||pathname.startsWith('/api/v1/storage/'))return route.fulfill({status:200,contentType:'image/png',body:png});
    if(pathname==='/api/v1/agents/image-agent/runs'&&method==='POST'){
      const payload=JSON.parse(request.postData());state.runPayloads.push(payload);
      if(state.runPayloads.length===1)state.hasConversation=true;
      return json({id:state.runPayloads.length===1?'task-1':'task-2',conversation_id:'conversation-1',status:'queued'},202);
    }
    if(pathname==='/api/v1/tasks/task-1/events')return route.fulfill({status:200,headers:{'content-type':'text/event-stream'},body:
      `event: complete\ndata: ${JSON.stringify({status:'completed',conversation_id:'conversation-1',assistant_message_id:'task:task-1:assistant',final_response:'图片已生成。',generation})}\n\n`});
    if(pathname==='/api/v1/tasks/task-2/events')return route.fulfill({status:200,headers:{'content-type':'text/event-stream'},body:''});
    if(pathname==='/api/v1/tasks/task-1')return json({id:'task-1',status:'completed',conversation_id:'conversation-1',final_response:'图片已生成。',generation});
    return json({error_code:'NOT_FOUND',user_message:'fixture missing'},404);
  });
  const origin=`http://127.0.0.1:${server.address().port}`;
  try {
    await page.goto(`${origin}/agents/image`);
    await page.locator('#chat-image-add').waitFor();
    await page.locator('#chat-image-input').setInputFiles({name:'reference.png',mimeType:'image/png',buffer:png});
    await page.locator('.chat-image-chip small').getByText('已就绪').waitFor();
    assert.equal(await page.locator('.chat-image-chip img').count(),1);
    await page.getByRole('button',{name:'删除参考图片'}).click();
    assert.equal(await page.locator('.chat-image-chip').count(),0);

    await page.locator('#composer').evaluate((composer,bytes)=>{
      const data=new DataTransfer();data.items.add(new File([new Uint8Array(bytes)],'reference.png',{type:'image/png'}));
      composer.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:data}));
    },[...png]);
    await page.locator('.chat-image-chip small').getByText('已就绪').waitFor();
    await page.getByRole('button',{name:'删除参考图片'}).click();
    await page.locator('#composer').evaluate((composer,bytes)=>{
      const data=new DataTransfer();data.items.add(new File([new Uint8Array(bytes)],'reference.png',{type:'image/png'}));
      composer.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:data}));
    },[...png]);
    await page.locator('.chat-image-chip small').getByText('已就绪').waitFor();
    assert.equal(state.uploads,3);assert.equal(state.deletes,2);
    if(process.env.CHAT_IMAGE_UI_SCREENSHOT)await page.screenshot({
      path:process.env.CHAT_IMAGE_UI_SCREENSHOT.replace(/\.png$/, '-upload-1440.png'),fullPage:true,
    });

    await page.locator('#prompt').fill('把背景换成未来都市');
    await page.getByRole('button',{name:'发送消息'}).click();
    await page.locator('.message-generation img').waitFor();
    assert.deepEqual(state.runPayloads[0].attachments,[{type:'image',id:'upload-3'}]);
    assert.equal(await page.locator('.chat-user-attachment img').count(),1);
    assert.equal(await page.locator('.message-generation img').count(),1);
    assert.equal(await page.locator('.chat-image-chip').count(),0);
    assert.equal(await page.locator('.message-generation-actions a[download]').count(),1);

    await page.locator('[data-select-agent-conversation="conversation-1"]').click();
    await page.locator('.chat-user-attachment img').waitFor();
    assert.equal(await page.locator('.chat-user-attachment img').count(),1);
    assert.equal(await page.locator('.message-generation img').count(),1);
    assert.ok((await page.locator('#chat-body').textContent()).includes('把背景换成未来都市'));

    await page.setViewportSize({width:375,height:812});
    await page.locator('#chat-body').evaluate(element=>{element.scrollTop=element.scrollHeight;});
    await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
    const actions=await page.locator('.message-generation-actions').boundingBox();
    const composer=await page.locator('#composer').boundingBox();
    assert.ok(actions.y+actions.height<=composer.y+2,
      `mobile image actions must remain above the composer: ${JSON.stringify({actions,composer,scroll:await page.locator('#chat-body').evaluate(element=>({top:element.scrollTop,height:element.scrollHeight,client:element.clientHeight})),page:await page.evaluate(()=>({y:scrollY,height:document.documentElement.scrollHeight,client:document.documentElement.clientHeight}))})}`);
    if(process.env.CHAT_IMAGE_UI_SCREENSHOT)await page.screenshot({
      path:process.env.CHAT_IMAGE_UI_SCREENSHOT.replace(/\.png$/, '-result-375.png'),fullPage:true,
    });

    await page.setViewportSize({width:1440,height:900});
    await page.locator('#new-chat').click();
    await page.locator('#prompt').fill('普通文字生图');
    await page.getByRole('button',{name:'发送消息'}).click();
    assert.equal(state.runPayloads[1].attachments,undefined);

    await page.setViewportSize({width:375,height:812});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
    assert.deepEqual(errors,[]);
    process.stdout.write('UI A-F PASS; click/drop/paste upload PASS; 375px overflow PASS; Provider calls 0\n');
  } finally {
    await browser.close();await new Promise(resolve=>server.close(resolve));
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
