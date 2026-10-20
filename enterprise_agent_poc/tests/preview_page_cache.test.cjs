// Actual API function in an isolated VM. No HTTP, Provider, Cookie or storage.
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/static/workbench.js'),'utf8');
function harness(preview=true){
 const pending=[],c=vm.createContext({me:{tenant_id:'tenant-a',user_id:'user-a',role:'enterprise_admin'},sessionViewVersion:0,window:{WORKBENCH_PREVIEW_BASE:preview?'/preview':undefined},Map,Date,Boolean,JSON,Error,fetch:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve}))});
 vm.runInContext(source.slice(source.indexOf('const principalSnapshot'),source.indexOf('const apiForm')),c);
 return {run:s=>vm.runInContext(s,c),pending,respond(i,data){pending[i].resolve({ok:true,json:async()=>data});}};
}
test('Preview coalesces only same principal read and keeps a short memory cache',async()=>{
 const h=harness();const one=h.run("api('/api/v1/workspace')"),two=h.run("api('/api/v1/workspace')");assert.equal(one,two);assert.equal(h.pending.length,1);h.respond(0,{brand_name:'A'});await one;
 assert.equal((await h.run("api('/api/v1/workspace')")).brand_name,'A');assert.equal(h.pending.length,1);
});
test('mutation invalidates in-flight cache insertion without repeating the mutation',async()=>{
 const h=harness();const old=h.run("api('/api/v1/workspace')"),write=h.run("api('/api/v1/example',{method:'POST'})");h.respond(1,{});await write;h.respond(0,{brand_name:'old'});await old;
 const fresh=h.run("api('/api/v1/workspace')");assert.equal(h.pending.length,3);h.respond(2,{brand_name:'new'});assert.equal((await fresh).brand_name,'new');
 assert.equal(h.pending.filter(x=>x.options.method==='POST').length,1);
});
test('Principal change rejects late data and never coalesces another Tenant',async()=>{
 const h=harness();const old=h.run("api('/api/v1/conversations')");const rejected=assert.rejects(old,/登录会话已变化/);
 h.run("me={tenant_id:'tenant-b',user_id:'user-b',role:'enterprise_admin'}");const fresh=h.run("api('/api/v1/conversations')");assert.equal(h.pending.length,2);h.respond(0,[{id:'private-a'}]);await rejected;h.respond(1,[{id:'private-b'}]);assert.equal((await fresh)[0].id,'private-b');
 assert.equal(h.run("[...pageCache.values()].every(x=>x.scope===principalCacheScope())"),true);
});
test('no-store Agent authorization reads are fresh on each visit',async()=>{
 const h=harness();const a=h.run("api('/api/v1/agents/campaign-agent',{cache:'no-store'})"),b=h.run("api('/api/v1/agents/campaign-agent',{cache:'no-store'})");assert.notEqual(a,b);assert.equal(h.pending.length,2);h.respond(0,{enabled:true});h.respond(1,{enabled:false});assert.equal((await b).enabled,false);await a;
});
test('non-Preview API does not adopt Preview coalescing',async()=>{
 const h=harness(false);const a=h.run("api('/api/v1/workspace')"),b=h.run("api('/api/v1/workspace')");assert.equal(h.pending.length,2);h.respond(0,{});h.respond(1,{});await Promise.all([a,b]);
});
