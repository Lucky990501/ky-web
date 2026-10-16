"""One versioned customer Test pilot, never Production authority or billing hard cap.

Runtime never creates or resets this ledger. Operator initialization is separate.
Every HTTP attempt reserves a durable slot before the only upstream send point.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from uuid import UUID, uuid4

from app.security import RuntimePrincipal, RuntimeTokenIssuer
from app.store import POCStore


# The approved currency amounts are risk acceptance, not an invoice cap.
MAX_OUTPUT_TOKENS = 8192  # Responses cap includes reasoning, not just visible output.
MAX_REQUEST_BYTES = 262144


class TestTextGateDenied(PermissionError):
    __test__ = False


def require(ok, code):
    if not ok:
        raise TestTextGateDenied(code)


def uuid(value):
    require(isinstance(value, str) and str(UUID(value)) == value, "test_text_gate_identity")
    return value


GATE_CODE_FILES = {'app/test_text_gate.py','scripts/test_text_request_gate.py',
                   'app/runtime/codex_provider.py','app/platform_mcp/service.py',
                   'app/settings.py','app/service.py','app/product_service.py'}


def read_technical_scope(path):
    """Consume an existing Test Root scope; never issue one or trust a Fixture."""
    path=Path(path)
    require(os.name=='posix' and path.is_absolute(), 'protected_test_context_required')
    for node in (path,*path.parents):
        st=node.lstat()
        require(not stat.S_ISLNK(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022,
                'root_protected_test_context')
    st=path.stat()
    require(stat.S_ISREG(st.st_mode) and st.st_nlink==1 and st.st_size<=65536
            and stat.S_IMODE(st.st_mode) in (0o440,0o444),'root_protected_test_context')
    raw=path.read_bytes(); context=json.loads(raw)
    revoked=path.parent/'REVOKED'
    require(context['kind']=='ZHIYE_BATCH1_TEST_PILOT_TECHNICAL_SCOPE_V1'
            and context['status']=='APPROVED' and context['production_authority'] is False
            and context['limits']==RequestBudget.LIMITS
            and context['issued_at']<=time.time()<context['expires_at']
            and not revoked.exists() and not revoked.is_symlink(),
            'current_test_technical_authority_required')
    require(set(context['gate_code_sha256'])==GATE_CODE_FILES,'complete_pilot_code_pins')
    root=Path(__file__).resolve().parents[1]
    for name,pin in context['gate_code_sha256'].items():
        candidate=root/name
        require(candidate.is_file() and not candidate.is_symlink()
                and hashlib.sha256(candidate.read_bytes()).hexdigest()==pin,'pilot_code_pin')
    return context,hashlib.sha256(raw).hexdigest()


class RequestBudget:
    """One exact customer pilot. Failed/unknown HTTP attempts are never refunded.

    Token capacity is reserved before send. Only a complete authenticated
    Provider response with valid usage can settle it to observed usage; HTTP
    slots and Task slots are permanent. Restart cannot initialize this ledger.
    """
    VERSION = "ZHIYE_BATCH1_TEST_PILOT_V1"
    LIMITS = {"text": 20, "image": 3, "customer": 6, "runtime": 1,
              "input": 131072, "output": 65536}

    def __init__(self, path: Path, database_url: str):
        self.path = Path(path).absolute()
        self.database_sha = hashlib.sha256(database_url.encode()).hexdigest()

    @property
    def anchor_path(self):
        return self.path.with_name(self.path.name+".initialized")

    @contextmanager
    def connect(self):
        for p, mode in ((self.path, 0o600), (self.anchor_path, 0o400)):
            st = p.lstat()
            require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1
                    and p.resolve(strict=True) == p.absolute(), "pilot_budget_file")
            if os.name == "posix":
                require(st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == mode,
                        "pilot_budget_permissions")
        require(self.anchor_path.stat().st_size <= 8192, "pilot_budget_anchor")
        if os.name == "posix":
            require(not self.path.parent.stat().st_mode & 0o022, "pilot_budget_directory")
        c = sqlite3.connect(self.path.as_uri()+"?mode=rw", uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA synchronous=FULL")
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @classmethod
    def initialize(cls, path, database_url, tenant_id, operation_id, expires_at, *,
                   member_id, operator_id, wechat_agent_id, revision_id, fingerprint,
                   owner_approved_at, owner_event_sha256, scope_sha256, context_path):
        for value in (operation_id, member_id, operator_id, wechat_agent_id, revision_id):
            uuid(value)
        for value in (fingerprint, owner_event_sha256, scope_sha256):
            require(isinstance(value,str) and len(value)==64
                    and all(ch in "0123456789abcdef" for ch in value), "pilot_budget_hash")
        require(tenant_id == "zhiy-e-intelligence" and member_id != operator_id,
                "pilot_budget_customer_identity")
        require(type(owner_approved_at) in (int,float) and type(expires_at) in (int,float)
                and owner_approved_at <= time.time() < expires_at
                <= owner_approved_at+86400, "pilot_budget_owner_window")
        budget = cls(Path(path), database_url)
        identity = {"version":cls.VERSION,"environment":"test","tenant_id":tenant_id,
                    "operation_id":operation_id,"database_sha":budget.database_sha,
                    "member_id":member_id,"operator_id":operator_id,
                    "wechat_agent_id":wechat_agent_id,"revision_id":revision_id,
                    "fingerprint":fingerprint,"owner_approved_at":owner_approved_at,
                    "expires_at":expires_at,"owner_event_sha256":owner_event_sha256,
                    "scope_sha256":scope_sha256,"context_path":str(Path(context_path).absolute()),"limits":cls.LIMITS}
        # The separate marker survives missing/lost mutable state. Never reset.
        for target, mode, data in ((budget.anchor_path,0o400,json.dumps(identity,sort_keys=True).encode()),
                                   (budget.path,0o600,b"")):
            fd = os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,mode)
            with os.fdopen(fd,"wb") as f:
                f.write(data); f.flush(); os.fsync(f.fileno())
        with budget.connect() as c:
            c.executescript("""
                CREATE TABLE budget(id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)));
                CREATE TABLE allowed_tasks(task_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,run_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('customer','runtime')));
                CREATE TABLE attempts(id INTEGER PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('text','image')),
                    task_id TEXT NOT NULL,run_id TEXT NOT NULL,request_sha TEXT NOT NULL,
                    item INTEGER NOT NULL,at REAL NOT NULL,input_reserved INTEGER NOT NULL,
                    output_reserved INTEGER NOT NULL,input_debit INTEGER NOT NULL,
                    output_debit INTEGER NOT NULL,settled_sha TEXT,
                    UNIQUE(kind,task_id,item),UNIQUE(kind,task_id,request_sha));
            """)
            c.execute("INSERT INTO budget(id,identity) VALUES(1,?)",(json.dumps(identity,sort_keys=True),))
        if os.name == "posix":
            fd=os.open(budget.path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        return budget

    def scope(self,c):
        row=c.execute("SELECT * FROM budget WHERE id=1").fetchone()
        require(row and not row['revoked'],"pilot_budget_revoked_or_missing")
        identity=json.loads(row['identity'])
        require(identity == json.loads(self.anchor_path.read_bytes())
                and identity['version']==self.VERSION and identity['environment']=='test'
                and identity['database_sha']==self.database_sha
                and identity['limits']==self.LIMITS
                and identity['owner_approved_at'] <= time.time() < identity['expires_at'],
                "pilot_budget_scope_or_expiry")
        authority,scope_sha=read_technical_scope(identity['context_path'])
        require(scope_sha==identity['scope_sha256']
                and authority['operation_id']==identity['operation_id']
                and authority['expires_at']==identity['expires_at'], 'pilot_current_scope_required')
        for kind,limit in self.LIMITS.items():
            if kind in ('text','image'):
                used=c.execute("SELECT count(*) FROM attempts WHERE kind=?",(kind,)).fetchone()[0]
            elif kind in ('customer','runtime'):
                used=c.execute("SELECT count(*) FROM allowed_tasks WHERE kind=?",(kind,)).fetchone()[0]
            else:
                used=c.execute("SELECT COALESCE(sum("+kind+"_debit),0) FROM attempts").fetchone()[0]
            require(0 <= used <= limit,"pilot_budget_inconsistent")
        return identity

    def _task_kind(self, task, identity):
        require(task['tenant_id']==identity['tenant_id'],"pilot_budget_tenant")
        if task.get('runtime_test_id'):
            require(task['user_id']==identity['operator_id']
                    and task['agent_id']==identity['wechat_agent_id']
                    and task.get('test_revision')==identity['revision_id']
                    and task.get('test_fingerprint')==identity['fingerprint'],"pilot_budget_runtime_identity")
            return 'runtime'
        require(task['user_id']==identity['member_id'] and task['agent_id'] in
                {'image-agent','campaign-agent','copywriting-agent',identity['wechat_agent_id']},
                "pilot_budget_member_or_agent")
        return 'customer'

    def allow(self,task):
        for key in ('id','user_id','run_id'): uuid(task[key])
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            identity=self.scope(c)
            kind=self._task_kind(task,identity)
            require(task['status'] in {'queued','running'},"pilot_budget_task_status")
            values=(task['id'],task['user_id'],task['agent_id'],task['run_id'],kind)
            prior=c.execute("SELECT * FROM allowed_tasks WHERE task_id=?",(task['id'],)).fetchone()
            require(not prior or tuple(prior)==values,"pilot_budget_task_drift")
            if not prior:
                count=c.execute("SELECT count(*) FROM allowed_tasks WHERE kind=?",(kind,)).fetchone()[0]
                require(count < self.LIMITS[kind],"pilot_budget_task_limit")
                c.execute("INSERT INTO allowed_tasks VALUES(?,?,?,?,?)",values)

    def check(self,task):
        with self.connect() as c:
            identity=self.scope(c)
            kind=self._task_kind(task,identity)
            row=c.execute("SELECT * FROM allowed_tasks WHERE task_id=?",(task['id'],)).fetchone()
            require(row and tuple(row)==(task['id'],task['user_id'],task['agent_id'],task['run_id'],kind)
                    and task['status']=='running',"pilot_budget_task_context")
        return task

    def reserve(self,task,raw,*,kind='text',input_tokens=0,output_tokens=0,item=None):
        self.check(task)
        require(kind in ('text','image') and type(input_tokens) is int
                and type(output_tokens) is int and input_tokens >= 0 and output_tokens >= 0,
                "pilot_budget_reservation")
        require((kind=='text' and input_tokens>0 and 0<output_tokens<=MAX_OUTPUT_TOKENS)
                or (kind=='image' and input_tokens==output_tokens==0 and type(item) is int
                    and 0<=item<2 and task['agent_id']=='image-agent'
                    and not task.get('runtime_test_id')),"pilot_budget_reservation_kind")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self.scope(c)
            used=c.execute("SELECT count(*) FROM attempts WHERE kind=?",(kind,)).fetchone()[0]
            require(used<self.LIMITS[kind],"pilot_budget_http_exhausted")
            totals=c.execute("SELECT COALESCE(sum(input_debit),0),COALESCE(sum(output_debit),0) FROM attempts").fetchone()
            require(totals[0]+input_tokens<=self.LIMITS['input']
                    and totals[1]+output_tokens<=self.LIMITS['output'],"pilot_budget_tokens_exhausted")
            if kind=='text':
                item=c.execute("SELECT count(*)+1 FROM attempts WHERE kind='text' AND task_id=?",(task['id'],)).fetchone()[0]
            try:
                cur=c.execute("INSERT INTO attempts(kind,task_id,run_id,request_sha,item,at,input_reserved,output_reserved,input_debit,output_debit) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (kind,task['id'],task['run_id'],hashlib.sha256(raw).hexdigest(),item,time.time(),
                     input_tokens,output_tokens,input_tokens,output_tokens))
            except sqlite3.IntegrityError:
                raise TestTextGateDenied('pilot_budget_paid_replay') from None
            return cur.lastrowid

    def settle(self,attempt_id,response_sha,*,input_tokens,output_tokens):
        # This is not a retry/refund. No HTTP/Task row is removed or reset.
        # Missing/failed/truncated responses never reach this function.
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self.scope(c)
            row=c.execute("SELECT * FROM attempts WHERE id=?",(attempt_id,)).fetchone()
            require(row and row['kind']=='text' and row['settled_sha'] is None
                    and type(input_tokens) is int and type(output_tokens) is int
                    and 0<=input_tokens<=row['input_reserved']
                    and 0<=output_tokens<=row['output_reserved'],"pilot_budget_usage_not_bounded")
            c.execute("UPDATE attempts SET input_debit=?,output_debit=?,settled_sha=? WHERE id=?",
                      (input_tokens,output_tokens,response_sha,attempt_id))


class TestTextGate:
    __test__ = False

    def __init__(self, settings, issuer: RuntimeTokenIssuer):
        require(settings.environment == "test" and settings.model_provider_id == "deepseek"
                and settings.model_id == "deepseek-v4-pro" and settings.reasoning_effort == "high"
                and settings.model_wire_api == "responses"
                and settings.model_base_url.rstrip('/') == "https://api.deepseek.com"
                and settings.image_provider_id == "image-gateway"
                and settings.embedding_provider == "local-hash", "test_text_gate_configuration")
        require(settings.test_text_gate_state is not None, "test_text_gate_state_required")
        self.settings, self.issuer = settings, issuer
        self.budget = RequestBudget(settings.test_text_gate_state, settings.database_url)
        self.store = POCStore(settings.database_url)
        self.server = None
        self.contexts = {}

    def task(self, tenant, task_id, agent, user=None, run_id=None):
        uuid(task_id)
        # Read actual accepted Task and live Run, never a client-supplied owner.
        with self.store.connection() as c:
            row = c.execute("SELECT t.*,r.tenant_id AS run_tenant,r.agent_id AS run_agent,r.status AS run_status, "
                            "z.id AS runtime_test_id,z.agent_template_version_id AS test_revision, "
                            "z.configuration_fingerprint AS test_fingerprint "
                            "FROM tasks t JOIN run_traces r ON r.run_id=t.run_id "
                            "LEFT JOIN agent_template_tests z ON z.task_id=t.id AND z.test_type='runtime' "
                            "JOIN users u ON u.id=t.user_id AND u.tenant_id=t.tenant_id AND u.account_status='enabled' "
                            "WHERE t.id=?", (task_id,)).fetchone()
            require(row is not None, "test_text_gate_task_missing")
            task = dict(row)
            require(task["tenant_id"] == task["run_tenant"] == tenant
                    and task["agent_id"] == task["run_agent"] == agent
                    and task["run_status"] == "running" and task["status"] == "running"
                    and (user is None or task["user_id"] == user)
                    and (run_id is None or task["run_id"] == run_id), "test_text_gate_task_context")
        self.budget.allow(task)
        return self.budget.check(task)

    async def bind(self, profile, task_id):
        require(profile.model_provider_id == "deepseek" and profile.model_id == "deepseek-v4-pro"
                and profile.reasoning_effort == "high", "test_text_gate_profile")
        task = self.task(profile.tenant_id, task_id, profile.agent_id)
        if self.server is None:
            self.server = await asyncio.start_server(self._handle, '127.0.0.1', 0, limit=65536)
        context = uuid4().hex
        self.contexts[context] = (profile.tenant_id,task_id,profile.agent_id,task["user_id"],task["run_id"])
        credential = self.issuer.issue(RuntimePrincipal(profile.tenant_id,profile.agent_id,context,
                                                       ("test-text:request",),int(time.time())+900))
        port = self.server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}", credential

    @staticmethod
    def bounded_payload(raw):
        p = json.loads(raw)
        require(isinstance(p,dict) and p.get("model") == "deepseek-v4-pro"
                and isinstance(p.get("reasoning"),dict) and p['reasoning'].get('effort') == 'high'
                and set(p['reasoning']) <= {'effort','summary'},
                "test_text_gate_model")
        output = p.get('max_output_tokens',MAX_OUTPUT_TOKENS)
        require(type(output) is int and 0 < output <= MAX_OUTPUT_TOKENS, 'test_text_gate_output_exceeded')
        p["max_output_tokens"] = output
        TestTextGate.request_bound(p)
        return p

    @staticmethod
    def request_bound(p):
        """Bound the FINAL finite text envelope, not a user's text token estimate.

        Proof/scope: deploy/MINIMAL_TEST_TEXT_REQUEST_GATE_V1.md. ASCII JSON
        dominates UTF-8 content/JSON rendering; 4x covers retained/copied fields,
        4096 covers high-effort/tools/format framing, 256 per item/part/tool
        covers role/transition/invoke/result wrappers; 128 per decoded argument
        covers DSML parameter wrappers. BPE cannot exceed rendered UTF-8 bytes.
        Opaque/remote/multimodal/unknown input is refused, never guessed.
        """
        allowed = {'model','input','instructions','tools','tool_choice','parallel_tool_calls',
                   'reasoning','max_output_tokens','stream','store','metadata','include',
                   'prompt_cache_key','text','client_metadata'}
        require(set(p) <= allowed, 'test_text_gate_input_bound_not_proven')
        require(p.get('instructions') is None or isinstance(p['instructions'],str),
                'test_text_gate_input_bound_not_proven')
        items = p.get('input',[])
        if isinstance(items,str):
            items = [{'role':'user','content':items}]
        require(isinstance(items,list) and (items or p.get('instructions')),
                'test_text_gate_input_bound_not_proven')
        units, arguments = len(items), 0

        def text_parts(value, types):
            if isinstance(value,str):
                return 0
            require(isinstance(value,list), 'test_text_gate_input_bound_not_proven')
            for part in value:
                require(isinstance(part,dict) and part.get('type') in types
                        and isinstance(part.get('text'),str)
                        and set(part) <= {'type','text','annotations'}, 'test_text_gate_input_bound_not_proven')
                require(not part.get('annotations'), 'test_text_gate_input_bound_not_proven')
            return len(value)

        for item in items:
            require(isinstance(item,dict), 'test_text_gate_input_bound_not_proven')
            kind = item.get('type','message' if 'role' in item else None)
            common = {'type','id','status'}
            if kind == 'message':
                require(set(item) <= common|{'role','content'} and item.get('role') in
                        {'system','developer','user','assistant'}, 'test_text_gate_input_bound_not_proven')
                units += text_parts(item.get('content'), {'input_text','output_text'})
            elif kind == 'function_call':
                require(set(item) <= common|{'call_id','name','arguments'}
                        and all(isinstance(item.get(k),str) for k in ('call_id','name','arguments')),
                        'test_text_gate_input_bound_not_proven')
                try:
                    args = json.loads(item['arguments'])
                except (ValueError,TypeError):
                    raise TestTextGateDenied('test_text_gate_input_bound_not_proven') from None
                require(isinstance(args,dict), 'test_text_gate_input_bound_not_proven')
                arguments += len(args)
            elif kind in {'function_call_output','custom_tool_call_output'}:
                require(set(item) <= common|{'call_id','output'} and isinstance(item.get('call_id'),str),
                        'test_text_gate_input_bound_not_proven')
                units += text_parts(item.get('output'), {'input_text','output_text'})
            elif kind == 'custom_tool_call':
                require(set(item) <= common|{'call_id','name','input'} and item.get('name') == 'apply_patch'
                        and isinstance(item.get('input'),str) and isinstance(item.get('call_id'),str),
                        'test_text_gate_input_bound_not_proven')
            elif kind == 'reasoning':
                require(set(item) <= common|{'content','summary'} and not item.get('summary'),
                        'test_text_gate_input_bound_not_proven')
                units += text_parts(item.get('content',[]), {'reasoning_text'})
            else:
                raise TestTextGateDenied('test_text_gate_input_bound_not_proven')

        tools = p.get('tools',[])
        require(isinstance(tools,list), 'test_text_gate_input_bound_not_proven')
        for tool in tools:
            require(isinstance(tool,dict) and tool.get('type') in {'function','custom','namespace','web_search'},
                    'test_text_gate_input_bound_not_proven')
            if tool['type'] == 'custom':
                require(tool.get('name') == 'apply_patch', 'test_text_gate_input_bound_not_proven')
            # namespace and web_search definitions are ignored by this exact
            # stateless provider protocol, but ALL their bytes are still charged
            # in the bound. No web_search_call/history or remote input admitted.
        units += len(tools)
        encoded = json.dumps(p,ensure_ascii=True,allow_nan=False).encode('ascii')
        require(len(encoded) <= MAX_REQUEST_BYTES, 'test_text_gate_request_too_large')
        input_bound = 4096 + 4*len(encoded) + 256*units + 128*arguments
        output = p['max_output_tokens']
        require(input_bound + output <= 1048576, 'test_text_gate_context_exceeded')
        return {'input_tokens_upper':input_bound,'output_tokens_upper':output}


    async def forward(self, credential, raw):
        principal = self.issuer.verify(credential, "test-text:request")
        context = self.contexts.get(principal.runtime_profile_id)
        require(context and principal.tenant_id == context[0] and principal.agent_id == context[2],
                "test_text_gate_signed_context")
        payload = self.bounded_payload(raw)
        task = self.task(*context[:3], user=context[3], run_id=context[4])
        # No injected URL, headers, user credentials, redirects or HTTP retries.
        import httpx
        key = os.environ.get(self.settings.codex_api_key_env)
        require(bool(key), "test_text_gate_credential_missing")
        # Recheck and hash exactly what will be sent, including injected cap.
        self.request_bound(payload)
        wire = json.dumps(payload,ensure_ascii=True,allow_nan=False).encode()
        bound=self.request_bound(payload)
        attempt=self.budget.reserve(task,wire,input_tokens=bound["input_tokens_upper"],
                                    output_tokens=bound["output_tokens_upper"])
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(retries=0),
                                     trust_env=False, follow_redirects=False, timeout=120) as client:
            response = await client.post("https://api.deepseek.com/responses",content=wire,
                                         headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"})
        usage=self.confirmed_usage(response.status_code,response.headers.get("content-type",""),response.content)
        if usage:
            self.budget.settle(attempt,hashlib.sha256(response.content).hexdigest(),
                               input_tokens=usage[0],output_tokens=usage[1])
        return response.status_code, response.headers.get("content-type","application/json"), response.content

    @staticmethod
    def confirmed_usage(status,content_type,raw):
        """Only a unique successful terminal response can settle reservations."""
        if not 200<=status<300 or len(raw)>16777216:
            return None
        try:
            if content_type.startswith('text/event-stream'):
                if not raw.endswith((b'\n\n',b'\r\n\r\n')):
                    return None
                terminal=[]
                for block in raw.decode().replace('\r\n','\n').split('\n\n'):
                    lines=[x[5:].lstrip() for x in block.splitlines() if x.startswith('data:')]
                    if not lines: continue
                    text='\n'.join(lines)
                    if text=='[DONE]': continue
                    data=json.loads(text)
                    if data.get('type') in {'response.failed','response.incomplete','error'}: return None
                    if data.get('type')=='response.completed': terminal.append(data['response'])
                if len(terminal)!=1: return None
                obj=terminal[0]
            else:
                obj=json.loads(raw)
            usage=obj['usage']
            a,b,total=usage['input_tokens'],usage['output_tokens'],usage['total_tokens']
            reasoning=usage.get('output_tokens_details',{}).get('reasoning_tokens',0)
            if obj.get('status')!='completed' or any(type(x) is not int for x in (a,b,total,reasoning)):
                return None
            if a<=0 or min(b,reasoning)<0 or total!=a+b or reasoning>b: return None
            return a,b
        except (ValueError,KeyError,TypeError,UnicodeError,AttributeError):
            return None

    def reserve_image(self,tenant,task_id,agent,raw,*,item):
        task=self.task(tenant,task_id,agent)
        payload=json.loads(raw)
        require(set(payload)=={'prompt','model','provider','size','n','output_format','response_format'}
                and isinstance(payload['prompt'],str) and 0<len(payload['prompt'])<=20000
                and payload['model']==self.settings.image_model_id=='gpt-image-2.5-sunburst-c'
                and payload['provider']=={'sort':'success_rate'} and type(payload['n']) is int
                and payload['n']==1 and payload['size'] in {'1536x1024','1536x864'}
                and payload['output_format']=='jpeg' and payload['response_format']=='b64_json',
                'pilot_budget_image_request')
        return self.budget.reserve(task,raw,kind='image',item=item)

    async def _handle(self, reader, writer):
        status, kind, body = 400, "application/json", b'{"error":{"message":"test_text_gate_denied"}}'
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),10)
            lines = header.decode('ascii').split('\r\n')
            require(lines[0] == 'POST /responses HTTP/1.1', "test_text_gate_http_path")
            pairs = [line.split(':',1) for line in lines[1:] if line]
            headers = {k.lower():v.strip() for k,v in pairs}
            require(len(headers) == len(pairs) and 'transfer-encoding' not in headers,
                    "test_text_gate_http_framing")
            length = int(headers['content-length'])
            require(0 < length <= MAX_REQUEST_BYTES and headers.get('authorization','').startswith('Bearer '),
                    "test_text_gate_http_request")
            raw = await asyncio.wait_for(reader.readexactly(length),10)
            status,kind,body = await self.forward(headers['authorization'][7:],raw)
            kind = "text/event-stream" if kind.startswith("text/event-stream") else "application/json"
        except TestTextGateDenied as exc:
            body = json.dumps({"error":{"message":str(exc),"type":"test_text_gate_denied"}}).encode()
        except Exception:
            body = b'{"error":{"message":"test_text_gate_unavailable_or_attempt_spent"}}'
        try:
            writer.write(f"HTTP/1.1 {status} Gate\r\nContent-Type: {kind}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()+body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        self.contexts.clear()
