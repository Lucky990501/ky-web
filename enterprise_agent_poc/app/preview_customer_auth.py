"""Small, Preview-only fixed-account login; no Production auth route changes."""
from collections import defaultdict, deque
from contextlib import contextmanager
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from app.auth import hash_password, verify_password, UserPrincipal
from app.preview_credit_target import NEW_VISITOR_OPERATION, top_up_preview_credits

COOKIE='workbench_preview_session'
SETUP_COOKIE='workbench_preview_setup'
SESSION_SECONDS=72*3600
SCHEMA='''CREATE TABLE IF NOT EXISTS preview_customer_accounts(
 operation_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL UNIQUE,user_id TEXT NOT NULL UNIQUE,
 account TEXT NOT NULL UNIQUE,initial_password_pending INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)'''


class PreviewCustomerAuth:
    def __init__(self,main,root,local_http=False):
        self.main=main;self.path=Path(root)/'preview.sqlite';self.secure=not local_http
        self.attempts=defaultdict(deque)
        self.dummy_hash=hash_password(secrets.token_urlsafe(24))
        with self.db() as c:c.execute(SCHEMA)

    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
        try:yield c;c.commit()
        except Exception:c.rollback();raise
        finally:c.close()

    def enabled(self):
        with self.db() as c:return c.execute('select 1 from preview_customer_accounts limit 1').fetchone() is not None

    def customer(self,user_id):
        with self.db() as c:
            row=c.execute('select * from preview_customer_accounts where user_id=?',(user_id,)).fetchone()
        return dict(row) if row else None

    def issue(self,user,setup=False):
        return self.main.sessions.issue(UserPrincipal(user['id'],user['tenant_id'],user['role'],
            ('setup:' if setup else '')+self.main.sessions.credential_version(user['password_hash'])),lifetime_seconds=600 if setup else SESSION_SECONDS)

    def cookie(self,response,token,setup=False):
        response.set_cookie(SETUP_COOKIE if setup else COOKIE,token,max_age=600 if setup else SESSION_SECONDS,
                            path='/preview/',secure=self.secure,httponly=True,samesite='lax')
        response.delete_cookie(COOKIE if setup else SETUP_COOKIE,path='/preview/',secure=self.secure,httponly=True,samesite='lax')
        return response

    async def route(self,scope,receive,send):
        path=scope['path'];request=Request(scope,receive)
        if path=='/login' and scope['method']=='GET':
            return await FileResponse(self.main.STATIC_DIR/'preview-login.html',headers={
                'Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
                'Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'})(scope,receive,send)
        def response(message,status=400):return JSONResponse({'user_message':message},status,headers={'Cache-Control':'no-store'})
        if scope['method']!='POST':return await response('此接口仅接受POST。',405)(scope,receive,send)
        if request.headers.get('origin')!=str(request.base_url).rstrip('/'):
            return await response('请从当前Preview登录页面操作。',403)(scope,receive,send)
        if path=='/api/v1/preview-auth/logout':
            result=response('已退出。',200)
            for name in (COOKIE,SETUP_COOKIE):result.delete_cookie(name,path='/preview/',secure=self.secure,httponly=True,samesite='lax')
            return await result(scope,receive,send)
        raw=bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw)>4096:return await response('登录请求过大。',413)(scope,receive,send)
        try:
            payload=json.loads(raw)
            if not isinstance(payload,dict):raise ValueError()
        except (ValueError,UnicodeError):return await response('登录请求格式错误。')(scope,receive,send)
        if path=='/api/v1/preview-auth/login':
            if set(payload)!={'account','password'} or not all(isinstance(v,str) for v in payload.values()):
                return await response('请输入账号和密码。')(scope,receive,send)
            account=payload['account'].strip().lower();password=payload['password']
            if not 1<=len(account)<=254 or not 1<=len(password)<=128:
                return await response('账号或密码不正确。',401)(scope,receive,send)
            key=(request.client.host if request.client else 'unknown',account)
            if len(self.attempts)>256:self.attempts.clear()
            attempts=self.attempts[key];now=time.monotonic()
            while attempts and attempts[0]<=now-60:attempts.popleft()
            if len(attempts)>=6:return await response('尝试次数过多，请一分钟后再试。',429)(scope,receive,send)
            attempts.append(now)
            user=self.main.product_store.user_by_email(account)
            record=self.customer(user['id']) if user else None
            verified=verify_password(password,user['password_hash'] if user and record else self.dummy_hash)
            if not verified or not record or record['tenant_id']!=user['tenant_id'] or user['account_status']!='enabled':
                return await response('账号或密码不正确。',401)(scope,receive,send)
            attempts.clear();setup=bool(record['initial_password_pending'])
            result=JSONResponse({'password_change_required':setup,'next':'/preview/'},headers={'Cache-Control':'no-store'})
            return await self.cookie(result,self.issue(user,setup),setup)(scope,receive,send)
        if path=='/api/v1/preview-auth/password':
            if set(payload)!={'new_password'} or not isinstance(payload['new_password'],str) or not 12<=len(payload['new_password'])<=128:
                return await response('新密码需为12至128个字符。')(scope,receive,send)
            try:
                principal=self.main.sessions.verify(request.cookies.get(SETUP_COOKIE,''))
                user=self.main.product_store.user_by_email(self.main.product_store.user_by_id(principal.user_id,principal.tenant_id)['email'])
                record=self.customer(principal.user_id)
                if (not record or record['tenant_id']!=principal.tenant_id or not record['initial_password_pending']
                    or user['account_status']!='enabled' or principal.auth_version!='setup:'+self.main.sessions.credential_version(user['password_hash'])):raise PermissionError()
                if verify_password(payload['new_password'],user['password_hash']):return await response('请设置不同于初始密码的新密码。')(scope,receive,send)
                new_hash=hash_password(payload['new_password'])
                with self.db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    current=c.execute('select initial_password_pending from preview_customer_accounts where user_id=?',(user['id'],)).fetchone()
                    if not current or not current[0]:raise PermissionError()
                    changed=c.execute('update users set password_hash=? where id=? and tenant_id=? and password_hash=?',
                                      (new_hash,user['id'],user['tenant_id'],user['password_hash'])).rowcount
                    if changed!=1:raise PermissionError()
                    c.execute('update preview_customer_accounts set initial_password_pending=0 where user_id=?',(user['id'],))
                user['password_hash']=new_hash
                return await self.cookie(JSONResponse({'next':'/preview/'},headers={'Cache-Control':'no-store'}),self.issue(user))(scope,receive,send)
            except Exception:
                return await response('初始登录已失效，请重新登录。',401)(scope,receive,send)
        return await response('登录接口不存在。',404)(scope,receive,send)


def provision(root,*,operation_id,account,password_hash):
    """Explicit operator-only provisioning; never reachable from HTTP."""
    root=Path(root).resolve();path=root/'preview.sqlite'
    if root.name!='customer-ai-preview-data' or not path.is_file():raise PermissionError('Preview database required')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',operation_id) or not re.fullmatch(r'[a-z0-9._+-]+@preview\.luckio\.cn',account):raise ValueError('Invalid customer identity')
    if not re.fullmatch(r'pbkdf2_sha256\$310000\$[A-Za-z0-9_=-]{24}\$[A-Za-z0-9_=-]{44}',password_hash):raise ValueError('Password hash required')
    @contextmanager
    def connection():
        c=sqlite3.connect(path,timeout=10);c.row_factory=sqlite3.Row
        try:yield c;c.commit()
        except Exception:c.rollback();raise
        finally:c.close()
    with connection() as c:
        c.execute('BEGIN IMMEDIATE');c.execute(SCHEMA)
        prior=c.execute('select * from preview_customer_accounts where operation_id=?',(operation_id,)).fetchone()
        if prior:
            if prior['account']!=account:raise PermissionError('Operation identity mismatch')
            row=c.execute('select id,tenant_id,email,account_status from users where id=?',(prior['user_id'],)).fetchone()
            if not row or row['tenant_id']!=prior['tenant_id'] or row['email']!=account or row['account_status']!='enabled':raise PermissionError('Customer identity mismatch')
            tenant,user=prior['tenant_id'],prior['user_id'];created=False
        else:
            if c.execute('select 1 from users where email=?',(account,)).fetchone():raise PermissionError('Existing account not explicitly provisioned')
            tenant='preview-customer-'+uuid4().hex;user=str(uuid4());created=True
            c.execute('insert into tenants values(?,?,?)',(tenant,'客户独立企业空间',secrets.token_urlsafe(32)))
            c.execute('insert into enterprise_configs values(?,?)',(tenant,json.dumps({'brand_name':'客户独立企业空间','brand_logo':None},ensure_ascii=False)))
            c.execute("insert into users(id,tenant_id,email,password_hash,display_name,role) values(?,?,?,?,?,'enterprise_admin')",(user,tenant,account,password_hash,'客户管理员'))
            c.execute('insert into credit_accounts(tenant_id,balance) values(?,0)',(tenant,))
            c.execute('insert into preview_customer_accounts(operation_id,tenant_id,user_id,account) values(?,?,?,?)',(operation_id,tenant,user,account))
        for agent in ('copywriting-agent','campaign-agent','preview-wechat-agent','image-agent'):
            c.execute("insert into tenant_agent_instances(tenant_id,agent_id,status) values(?,?,'enabled') on conflict(tenant_id,agent_id) do update set status='enabled'",(tenant,agent))
    from types import SimpleNamespace
    store=SimpleNamespace(database_path=path,is_postgres=False,connection=connection)
    credits=top_up_preview_credits(store,operation=NEW_VISITOR_OPERATION,tenant_id=tenant)[0]
    return {'tenant_id':tenant,'user_id':user,'account':account,'created':created,'credits':credits}
