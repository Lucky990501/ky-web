"""Preview task-bound text gate; existing ledger, no new schema or authority.

Controlled scopes are declared by the approved installer BEFORE admission.
Their immutable ledger declarations survive missing/unreadable configuration.
Every SDK process gets its own short-lived loopback credential and real Task
path via the existing manager gate hook; never select the latest running Task.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

from app.preview_budget import PreviewBudgetExceeded

FIELDS=('tenant_id','user_id','agent_id')

def denied():
    return PreviewBudgetExceeded('受控验收授权不可用或已关闭；未调用模型。','PREVIEW_CONTROLLED_ACCEPTANCE_DENIED')

def identity(task):
    return {k:str(task[k]) for k in ('id',*FIELDS)}

def private_authorization(path):
    """Validate the opened inode as the actual service uid, not root preflight."""
    path=Path(path)
    try:
        if path.is_symlink():raise denied()
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as stream:
            meta=os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode):raise denied()
            if os.name!='nt' and (meta.st_uid!=os.geteuid() or stat.S_IMODE(meta.st_mode)!=0o600):raise denied()
            content=stream.read(65537)
        if len(content)>65536:raise denied()
        value=json.loads(content)
        if not isinstance(value,dict):raise denied()
        return value
    except (OSError,ValueError,TypeError) as exc:
        raise denied() from exc

class PreviewTextGuard:
    def __init__(self,budget,path):self.budget,self.path=budget,Path(path)

    def _events(self,connection):
        return [json.loads(r[0]) for r in connection.execute('select payload from observations order by rowid')]

    def declare(self,scope):
        """Installer-only durable declaration, NOT a grant or renewal."""
        required=('authorization_id',*FIELDS)
        if not isinstance(scope,dict) or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,120}',str(scope.get(k,''))) for k in required):raise denied()
        record={'event':'controlled_text_scope',**{k:scope[k] for k in required}}
        with sqlite3.connect(self.budget.path,timeout=10) as c:
            c.execute('begin immediate');rows=[r for r in self._events(c) if r.get('event')=='controlled_text_scope']
            for prior in rows:
                if prior['authorization_id']==record['authorization_id']:
                    if prior!=record:raise denied()
                    return
            c.execute('insert into observations values(?,?)',(time.time(),json.dumps(record)))

    def bind(self,task):
        who=identity(task)
        with sqlite3.connect(self.budget.path,timeout=10) as c:
            c.execute('begin immediate');events=self._events(c)
            scopes=[e for e in events if e.get('event')=='controlled_text_scope' and all(e[k]==who[k] for k in FIELDS)]
            auth=scopes[-1]['authorization_id'] if scopes else None
            binding={'event':'preview_text_task_binding',**who,'authorization_id':auth}
            prior=[e for e in events if e.get('event')=='preview_text_task_binding' and e.get('id')==who['id']]
            if prior:
                if len(prior)!=1 or any(prior[0].get(k)!=v for k,v in who.items()):raise denied()
                binding=prior[0];auth=binding['authorization_id']
            if auth and any(e.get('event')=='preview_text_task_binding' and e.get('authorization_id')==auth and e.get('id')!=who['id'] for e in events):raise denied()
            if not prior:c.execute('insert into observations values(?,?)',(time.time(),json.dumps(binding)))
        # Binding is committed even when the grant is absent, revoked or broken.
        self.check(task)
        return binding

    def check(self,task):
        who=identity(task)
        with sqlite3.connect(self.budget.path) as c:
            events=self._events(c)
        rows=[e for e in events if e.get('event')=='preview_text_task_binding' and e.get('id')==who['id']]
        if len(rows)!=1 or any(rows[0].get(k)!=v for k,v in who.items()):raise denied()
        binding=rows[0]
        scopes=[e for e in events if e.get('event')=='controlled_text_scope' and all(e[k]==who[k] for k in FIELDS)]
        if not binding['authorization_id']:
            if scopes:raise denied()  # Never downgrade a previously declared identity.
            return None
        if not scopes or scopes[-1]['authorization_id']!=binding['authorization_id']:raise denied()
        control=private_authorization(self.path).get('controlled_text_acceptance')
        if (not isinstance(control,dict) or control.get('authorization_id')!=binding['authorization_id']
            or any(control.get(k)!=who[k] for k in FIELDS)
            or control.get('status') not in (None,'ACTIVE') or control.get('revoked') is True
            or type(control.get('max_requests')) is not int or not 1<=control['max_requests']<=4):raise denied()
        return {'authorization_id':binding['authorization_id'],'task_id':who['id'],'max_requests':control['max_requests']}

    def reserve(self,task,cost):
        control=self.check(task)
        self.budget.reserve('text',cost,agent_id=task['agent_id'],**({'controlled':control} if control else {}))
        return control

class PreviewTaskTextGate:
    """Reuse the manager's existing task gate extension, only in Preview."""
    requires_mcp_observations=False
    forbid_server_tools=True
    preview_flash_allowed=True
    def __init__(self,store,guard,base_url):
        self.store,self.guard,self.base_url=store,guard,base_url
        self.credentials={}

    def task(self,task_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}',str(task_id or '')):raise denied()
        with self.store.connection() as c:
            row=c.execute('select * from tasks where id=?',(task_id,)).fetchone()
        if not row or row['status'] not in {'queued','running'}:raise denied()
        return dict(row)

    async def bind(self,profile,task_id):
        task=self.task(task_id)
        if task['tenant_id']!=profile.tenant_id or task['agent_id']!=profile.agent_id:raise denied()
        self.guard.bind(task)
        old=self.credentials.get(task_id)
        if old and old[1]>time.monotonic() and old[2]==identity(task):
            return self.base_url+'/tasks/'+task_id,old[0]
        credential=secrets.token_urlsafe(32)
        self.credentials[task_id]=(credential,time.monotonic()+900,identity(task))
        return self.base_url+'/tasks/'+task_id,credential

    def resolve(self,task_id,credential):
        entry=self.credentials.get(task_id)
        if not entry or entry[1]<time.monotonic() or not secrets.compare_digest(entry[0],credential):raise denied()
        task=self.task(task_id)
        if identity(task)!=entry[2]:raise denied()
        self.guard.check(task)
        return task

    def protect_mcp_token(self,issuer,task_id,token):
        if not self.guard.check(self.task(task_id)):return token
        principal=issuer.verify(token,'enterprise_config:read')
        return issuer.issue(replace(principal,scopes=tuple(s for s in principal.scopes if s=='enterprise_config:read')))

    async def close(self):self.credentials.clear()
