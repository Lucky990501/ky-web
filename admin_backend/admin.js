const $=selector=>document.querySelector(selector);
const api=(path,options={})=>fetch(path,{headers:{'Content-Type':'application/json',...(options.headers||{})},...options}).then(async response=>{const data=await response.json();if(!response.ok)throw Error(data.error||'操作失败');return data});
const stamp=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—';
const escapeHtml=value=>{const element=document.createElement('div');element.textContent=value??'';return element.innerHTML};
const initials=value=>(value||'用户').trim().slice(0,2).toUpperCase();
let contentState={items:[],groups:{},active:'hero'};
let usersById={};
function renderContent(){const group=contentState.active;$('#content-groups').innerHTML=Object.entries(contentState.groups).map(([id,label])=>`<button type="button" class="${id===group?'active':''}" data-content-group="${id}">${escapeHtml(label)}</button>`).join('');$('#content-fields').innerHTML=contentState.items.filter(item=>item.group===group).map(item=>`<label>${escapeHtml(item.label)}<textarea name="${escapeHtml(item.content_key)}" rows="${item.content_value.length>60?4:2}">${escapeHtml(item.content_value)}</textarea></label>`).join('');document.querySelectorAll('[data-content-group]').forEach(button=>button.onclick=()=>{contentState.active=button.dataset.contentGroup;renderContent()})}
function showPanel(panel){document.querySelectorAll('.panel').forEach(element=>{element.hidden=element.dataset.panel!==panel});document.querySelectorAll('[data-panel-target]').forEach(button=>button.classList.toggle('active',button.dataset.panelTarget===panel))}

function renderUsers(users){
  $('#user-count').textContent=users.total;
  usersById=Object.fromEntries(users.items.map(user=>[user.id,user]));
  $('#users').innerHTML=users.items.map(user=>{
    const name=user.name||'未设置昵称';
    const phone=user.phone||user.login_phone||'未留手机号';
    const email=user.email||'未留邮箱';
    const company=[user.company,user.job_title].filter(Boolean).join(' · ')||'暂未补充企业信息';
    return `<tr><td><div class="person"><span class="avatar" aria-hidden="true">${escapeHtml(initials(name))}</span><div><strong>${escapeHtml(name)}</strong><button class="field-edit" data-field="name" data-user-id="${user.id}" type="button">编辑</button><small>用户 #${escapeHtml(user.id)}</small></div></div></td><td>${escapeHtml(phone)}<button class="field-edit" data-field="phone" data-user-id="${user.id}" type="button">编辑</button><small>${escapeHtml(email)}<button class="field-edit" data-field="email" data-user-id="${user.id}" type="button">编辑</button></small></td><td>${escapeHtml(company)}</td><td>${stamp(user.created_at)}</td><td>${stamp(user.last_login_at)}</td></tr>`;
  }).join('')||'<tr><td class="empty" colspan="6">暂未有注册用户。</td></tr>';
  document.querySelectorAll('.field-edit').forEach(button=>button.onclick=()=>editUserField(usersById[button.dataset.userId],button.dataset.field));
}

async function editUser(user){const name=prompt('昵称',user.name||'');if(name===null)return;const email=prompt('邮箱',user.email||'');if(email===null)return;const phone=prompt('手机号',user.phone||user.login_phone||'');if(phone===null)return;const company=prompt('企业名称',user.company||'');if(company===null)return;const job_title=prompt('职位',user.job_title||'');if(job_title===null)return;try{await api(`/admin/api/users/${user.id}`,{method:'PUT',body:JSON.stringify({email,phone,profile:{name,phone,company,job_title}})});await refresh()}catch(error){alert(error.message)}}
async function editUserField(user,field){const labels={name:'昵称',phone:'手机号',email:'邮箱'};const current=field==='phone'?(user.phone||user.login_phone||''):(user[field]||'');const value=prompt(`修改${labels[field]}`,current);if(value===null)return;const name=field==='name'?value:(user.name||'未设置昵称');const phone=field==='phone'?value:(user.phone||user.login_phone||'');const email=field==='email'?value:(user.email||'');try{await api(`/admin/api/users/${user.id}`,{method:'PUT',body:JSON.stringify({email,phone,profile:{name,phone,company:user.company||'',job_title:user.job_title||''}})});await refresh()}catch(error){alert(error.message)}}

function renderLeads(leads){
  $('#lead-count').textContent=leads.items.length;
  $('#leads').innerHTML=leads.items.map(lead=>`<tr><td>${escapeHtml(lead.name)}<small>${escapeHtml(lead.company)}</small></td><td>${escapeHtml(lead.contact)}</td><td><small>${escapeHtml(lead.challenge)}</small></td><td>${stamp(lead.created_at)}</td><td><select data-id="${escapeHtml(lead.id)}" aria-label="${escapeHtml(lead.name)}的线索状态"><option value="new">待跟进</option><option value="contacted">已联系</option><option value="closed">已完成</option></select></td></tr>`).join('')||'<tr><td class="empty" colspan="5">暂未收到预约。</td></tr>';
  document.querySelectorAll('select[data-id]').forEach(select=>{const lead=leads.items.find(item=>String(item.id)===select.dataset.id);select.value=lead.status;select.onchange=async()=>{select.disabled=true;try{await api(`/admin/api/leads/${select.dataset.id}`,{method:'PUT',body:JSON.stringify({status:select.value})})}catch(error){select.value=lead.status;alert(error.message)}finally{select.disabled=false}}});
}

async function refresh(){
  const [users,leads,status,content]=await Promise.all([api('/admin/api/users'),api('/admin/api/leads'),api('/admin/api/status'),api('/admin/api/content')]);
  renderUsers(users);renderLeads(leads);
  $('#release').textContent=status.release.split('/').pop().slice(0,8);
  contentState={items:content.items,groups:content.groups,active:contentState.groups[contentState.active]?contentState.active:Object.keys(content.groups)[0]};renderContent();
}

$('#refresh-users').onclick=()=>refresh().catch(error=>$('#deploy-output').textContent=error.message);
$('#refresh-leads').onclick=()=>refresh().catch(error=>$('#deploy-output').textContent=error.message);
document.querySelectorAll('[data-panel-target]').forEach(button=>button.onclick=()=>showPanel(button.dataset.panelTarget));showPanel('overview');
$('#content-form').onsubmit=async event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.currentTarget));try{await api('/admin/api/content',{method:'PUT',body:JSON.stringify({values})});contentState.items.forEach(item=>{if(values[item.content_key]!==undefined)item.content_value=values[item.content_key]});$('#content-message').textContent='已保存，官网刷新后生效。'}catch(error){$('#content-message').textContent=error.message}};
$('#deploy').onclick=async()=>{if(!confirm('确认部署服务器已接收的最新版本？'))return;$('#deploy').disabled=true;try{const result=await api('/admin/api/deploy',{method:'POST',body:'{}'});$('#deploy-output').textContent=result.output;await refresh()}catch(error){$('#deploy-output').textContent=error.message}finally{$('#deploy').disabled=false}};
refresh().catch(error=>$('#deploy-output').textContent=error.message);
