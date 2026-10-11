// Explicit isolated fixtures. Actual frontend functions; no Provider, task API or DB.
const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/workbench.js'),'utf8').replace(/boot\(\);\s*$/,'');
function harness(){
  const context=vm.createContext({URL,console,Date,setTimeout,clearTimeout,clearInterval,
    location:{origin:'https://fixture.invalid'},window:{addEventListener(){}},document:{querySelector(){return null;},querySelectorAll(){return [];}}});
  vm.runInContext(source,context);return code=>vm.runInContext(code,context);
}
const artifact=(index,extra={})=>({id:`fixture-image-${index}`,task_id:'fixture-task',content_url:`/api/v1/storage/generated/fixture-tenant/${index}.png`,...extra});
test('fixture 1/2/3/5 results preserve backend order and independent actions',()=>{
  for(const count of [1,2,3,5]){
    const run=harness(),items=Array.from({length:count},(_,index)=>artifact(count-index));
    const html=run(`messageGenerationsHtml(resultGenerations(${JSON.stringify({generations:items})}))`);
    assert.equal((html.match(/class="message-generation"/g)||[]).length,count);
    assert.equal((html.match(/data-open-message-generation=/g)||[]).length,count);
    assert.equal((html.match(/ download /g)||[]).length,count);
    if(count>1)assert.ok(html.includes(`已生成 ${count} 张图片`));
    for(let index=1;index<count;index++)assert.ok(html.indexOf(`/${count-index+1}.png`)<html.indexOf(`/${count-index}.png`));
  }
});
test('legacy scalar is supported; explicit empty/invalid arrays never resurrect scalar',()=>{
  const run=harness();
  for(const result of [{generation:artifact(1)},{generations:[artifact(1)],generation:artifact(2)}])assert.equal(run(`resultGenerations(${JSON.stringify(result)}).length`),1);
  for(const generations of [[],null,{},'bad'])assert.equal(run(`resultGenerations(${JSON.stringify({generations,generation:artifact(1)})}).length`),0);
});
test('current task association rejects unrelated/missing task IDs',()=>{
  const run=harness(),items=[artifact(1),artifact(2,{task_id:'other'}),artifact(3,{task_id:undefined})];
  assert.equal(run(`resultGenerations(${JSON.stringify({generations:items})},'fixture-task').length`),1);
});
test('URL prose does not create an artifact or inflate a count',()=>{
  const run=harness(),result={role:'assistant',content:'正面 /api/v1/storage/generated/fixture/1.png\n反面 /api/v1/storage/generated/fixture/2.png'};
  const html=run(`messageHtml(${JSON.stringify(result)})`);
  assert.ok(!html.includes('class="message-generation"'));assert.ok(!html.includes('/api/v1/storage/generated/'));
  assert.ok(html.includes('正面'));assert.ok(html.includes('反面'));
});
test('malformed records rejected, valid unavailable records retain a placeholder',()=>{
  const run=harness(),result={generations:[null,'url',[],{},artifact(1,{content_url:undefined}),artifact(2,{content_url:'javascript:alert(1)'})]};
  assert.equal(run(`resultGenerations(${JSON.stringify(result)}).length`),2);
  const html=run(`messageGenerationsHtml(resultGenerations(${JSON.stringify(result)}))`);
  assert.equal((html.match(/图片暂不可用/g)||[]).length,2);assert.ok(!html.includes('javascript:'));
});
test('artifact addresses removed from prose, links, code blocks and copy payload',()=>{
  const run=harness(),paths=['/api/v1/storage/generated/fixture/a.png','https://fixture.invalid/api/v1/storage/generated/a.png','/opt/app/runtime-data/run/workspace/文件.png','runtime-data/file.png','workspace/file.png','/workspace/file.png','/runtime-data/file.png','workspace\\file.png','C:\\runtime\\workspace\\a.png','file:///C:/runtime/a.png','\\\\fixture-server\\share\\a.png','/opt/','/OPT/file.png'];
  for(const value of paths){
    const prose=`正面（封面+封底）\n[查看说明](${value})\n\`\`\`\n${value}\n\`\`\`\n请检查颜色。`;
    const clean=run(`assistantDisplayText(${JSON.stringify(prose)})`);
    assert.ok(!clean.includes(value),value);assert.ok(clean.includes('查看说明'));assert.ok(clean.includes('正面（封面+封底）'));assert.ok(clean.includes('请检查颜色。'));
    assert.ok(!run(`messageActionsHtml(${JSON.stringify(prose)})`).includes(value),`copy: ${value}`);
  }
});
test('business prose and ordinary external/relative documentation links unchanged',()=>{
  const run=harness(),text='工作空间设计：保留层次。 [参考](https://example.com/workspace/guide)\nhttps://example.com/opt/manual\n[资料](/help/guide)\n标题、列表、图片下载说明。';
  assert.equal(run(`assistantDisplayText(${JSON.stringify(text)})`),text);
  assert.ok(run(`assistantMarkdownHtml(${JSON.stringify(text)})`).includes('href="https://example.com/workspace/guide"'));
});
test('each streaming character prefix withholds split internal paths without changing raw state',()=>{
  const run=harness();
  for(const value of ['/api/v1/storage/generated/fixture/a.png','/opt/app/workspace/a.png','runtime-data/a.png','workspace/a.png','file:///C:/workspace/a.png','https://fixture.invalid/api/v1/storage/generated/a.png']){
    for(let length=1;length<=value.length;length++){
      const raw=`正面： ${value.slice(0,length)}`,clean=run(`assistantDisplayText(${JSON.stringify(raw)},{streaming:true})`);
      assert.equal(clean,'正面： ',`${value} prefix ${length}`);
    }
  }
  run("s=createStreamingState();applyStreamingEvent(s,'delta',{sequence:1,text:'图片 /api/v1/storage/generated/fixture/a.png'});");
  assert.ok(run('s.text').includes('/api/v1/storage/generated/'));assert.equal(run('s.visualPending'),run('s.text'));
});
test('stream path followed by punctuation/next paragraph never leaks; business tail still renders',()=>{
  const run=harness(),raw='正面 /api/v1/storage/generated/fixture/a.png。\n\n反面设计保持原样。';
  const clean=run(`assistantDisplayText(${JSON.stringify(raw)},{streaming:true})`);
  assert.ok(!clean.includes('/api/v1/storage'));assert.ok(clean.includes('反面设计保持原样。'));
});
test('history and final render/copy share the same redaction with resource URLs retained only in attributes',()=>{
  const run=harness(),result={role:'assistant',task_id:'fixture-task',content:'反面 /api/v1/storage/generated/fixture/a.png',generations:[artifact(1),artifact(2)]};
  const html=run(`messageHtml(${JSON.stringify(result)})`);
  assert.equal((html.match(/class="message-generation"/g)||[]).length,2);
  const visible=html.replace(/<[^>]*>/g,'');assert.ok(!visible.includes('/api/v1/storage'));assert.ok(visible.includes('反面'));
  assert.ok(html.includes('src="/api/v1/storage/generated/fixture-tenant/1.png"'));
  assert.ok(!run(`streamingFinalContentHtml(${JSON.stringify(result.content)})`).includes('/api/v1/storage'));
  assert.ok(!run(`streamingFailureHtml(${JSON.stringify(result.content)},'失败')`).includes('/api/v1/storage'));
});
test('SSE and polling share terminal handler; terminal task owns new arrays',()=>{
  const run=harness();
  const items=[artifact(1),artifact(2)];
  run(`s=createStreamingState();beginConversationRun(s,{taskId:'fixture-task',agentId:'image-agent',phase:ConversationRunPhase.RUNNING});node={innerHTML:'',inserted:'',dataset:{},querySelector(){return null;},insertAdjacentHTML(p,h){this.inserted+=h;},removeAttribute(){},classList:{remove(){}}};finishStreamTask(node,s,${JSON.stringify({status:'completed',final_response:'结果 /opt/private/a.png',generations:items})},'image-agent',{querySelector(){return null;},querySelectorAll(){return [];}},'需求');`);
  assert.equal(run('s.generations.length'),2);assert.equal(run('s.completed'),true);assert.ok(!run('node.innerHTML').includes('/opt/'));
  assert.equal((run('node.inserted').match(/class="message-generation"/g)||[]).length,2);
});
test('cancelled terminal rejects late failure or completion records',()=>{
  const run=harness();run('replaceTaskFailure=()=>{};');
  for(const status of ['failed','completed']){
    run(`s=createStreamingState();s.run.terminal='cancelled';finishStreamTask(null,s,${JSON.stringify({status,generations:[artifact(1)]})},'image-agent',null,'需求')`);
    assert.equal(run('s.generations'),undefined);assert.equal(run('s.completed'),false);
  }
});
