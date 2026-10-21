/* Preview-only credentials are never persisted to browser storage or URLs. */
(() => {
  const feedback=document.querySelector('#feedback'),login=document.querySelector('#login-form'),setup=document.querySelector('#setup-form');
  async function send(path,payload){
    const response=await fetch('/preview/api/v1/preview-auth/'+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const value=await response.json();if(!response.ok)throw Error(value.user_message||'操作未完成，请稍后再试。');return value;
  }
  async function submit(form,run){const button=form.querySelector('[type=submit]');button.disabled=true;feedback.textContent='正在验证…';try{await run();}catch(error){feedback.textContent=error.message;}finally{button.disabled=false;}}
  login.addEventListener('submit',event=>{event.preventDefault();submit(login,async()=>{
    const password=document.querySelector('#password');
    let result;try{result=await send('login',{account:document.querySelector('#account').value,password:password.value});}finally{password.value='';}
    if(result.password_change_required){login.hidden=true;setup.hidden=false;feedback.textContent='';document.querySelector('#new-password').focus();}
    else location.replace('/preview/');
  });});
  setup.addEventListener('submit',event=>{event.preventDefault();submit(setup,async()=>{
    const password=document.querySelector('#new-password'),repeat=document.querySelector('#repeat-password');
    if(password.value!==repeat.value)throw Error('两次输入的密码不一致。');
    try{await send('password',{new_password:password.value});}finally{password.value='';repeat.value='';}
    location.replace('/preview/');
  });});
  document.querySelector('#show-password').addEventListener('click',event=>{const input=document.querySelector('#password'),show=input.type==='password';input.type=show?'text':'password';event.currentTarget.textContent=show?'隐藏':'显示';event.currentTarget.setAttribute('aria-pressed',String(show));});
  document.querySelector('#logout').addEventListener('click',async()=>{try{await send('logout',{});location.replace('/preview/login');}catch(error){feedback.textContent=error.message;}});
})();
