"""Two Runtime probes, bounded multi-turn HTTP sends. Not a vendor invoice guarantee.

Uses the existing finite-text envelope proof and SDK transport gate. Runtime
never initializes/resets the durable budget. No secret or prompt is audited.
"""
from __future__ import annotations
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import time
import re
import sys

from app.test_text_gate import TestTextGate, TestTextGateDenied, require, input_shape
from app.security import RuntimeTokenIssuer
from app.store import POCStore

BASELINE = '28948beb5e3af1ba490e62fbf080ae3ee6ebbc4f'
PROFILE = 'STAGE2_TWO_PROBE_MULTITURN_ADMISSION_V2'
LIMITS = dict(requests=8, requests_per_probe=4, input_each=65536, input_total=524288,
              output_each=8192, output_total=65536, cost_micro_cny=6490000)
_DENIAL_CONTEXT = ContextVar('stage2_denial_context',default=None)
# Worst documented peak/cache-miss price, CNY per million tokens. Integer
# micro-CNY per token is numerically the same; every reservation is rounded up.
INPUT_RATE = 9
OUTPUT_RATE = 27
PROMPTS = {
    'social-content-agent': '读取绑定 Skill 的 SKILL.md，按 Persona 用一句中文介绍能力。调用全部 required 工具；若绑定 optional enterprise_config_get，请读取企业配置。只返回最终结果。',
    'wechat-official-account-writing': '请写一篇关于社区开学季公益助学的微信公众号初稿。时间、地点和参与方式尚未确定；只使用已知信息，未知细节标为待确认，不要虚构具体事实。',
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, 'stage2_duplicate_json_key')
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs)


def protected(path):
    """Existing Native Owner raw records exported unchanged into a Root parent.

    Copies must be Root-owned read-only0444 (original archives remain0600).
    This reader does not issue, serialize, extend or fabricate a decision.
    """
    path = Path(path)
    require(os.name == 'posix' and path.is_absolute(), 'stage2_protected_authority')
    for parent in path.parents:
        s = parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'stage2_trusted_authority_parent')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and stat.S_IMODE(s.st_mode) == 0o444 and s.st_size <= 262144,
                'stage2_protected_authority_file')
        raw = f.read(262145)
        end = os.fstat(f.fileno())
    after = path.lstat()
    require((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns) ==
            (end.st_dev,end.st_ino,end.st_size,end.st_mtime_ns) ==
            (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns), 'stage2_authority_read_race')
    return raw


def authority(directory):
    """Consume the existing real Native Owner decision, not chat or Fixture."""
    directory = Path(directory)
    require(not os.path.lexists(directory/'revoked.json'), 'stage2_authority_revoked')
    request_raw, owner_raw = protected(directory/'request.json'), protected(directory/'owner.json')
    request, owner = parse(request_raw), parse(owner_raw)
    now = time.time()
    require(request['kind'] == 'B38_NATIVE_V2_MANUAL_ISSUANCE_V1' and request['fixture'] is False
            and owner['kind'] == 'ACTUAL_NATIVE_V2_HUMAN_DECISION' and owner['role'] == 'owner'
            and owner['principal'] == 'Lucky' and owner['uid'] == 0
            and owner['host'] == 'iv-yekqs6ez9cnu262hhsg0'
            and owner['tty'].startswith('/dev/') and type(owner['pid']) is int and owner['pid'] > 0
            and owner['request_sha256'] == digest(request_raw)
            and owner['original_sha256'] == digest(owner['original'].encode())
            and owner['issued_at'] <= now < owner['expires_at'] <= owner['issued_at']+86400,
            'stage2_real_owner_decision_required')
    original = parse(owner['original'])
    require(original['role'] == 'owner' and original['request'] == request
            and original['confirmation'].startswith('OWNER '+digest(request_raw)+' '),
            'stage2_original_scope_binding')
    fee = request['fees']['stage2_two_probe']
    require(fee['profile'] == PROFILE and fee['limits'] == LIMITS
            and fee['model'] == 'deepseek-v4-pro' and fee['reasoning'] == 'high'
            and fee['input_micro_cny_per_token'] == INPUT_RATE
            and fee['output_micro_cny_per_token'] == OUTPUT_RATE
            and fee['automatic_paid_retries'] == 0 and fee['other_paid_calls'] == 0
            and fee['price_verified_at'] <= now < fee['price_verified_at']+86400
            and request['fees']['automatic_paid_retries'] == 0,
            'stage2_exact_fee_profile')
    ledger = Path(fee['ledger_path'])
    require(ledger.is_absolute() and ledger.name=='stage2-two-probe.sqlite'
            and ledger.is_relative_to(Path('/opt/enterprise-agent-workbench-test/release-evidence'))
            and '..' not in ledger.parts and ledger.parent.resolve(strict=True)==ledger.parent, 'stage2_exact_ledger_path')
    root = Path(__file__).resolve().parents[2]
    actual = subprocess.check_output(['git','-C',str(root),'show','-s','--format=%H%n%T','HEAD'],text=True).splitlines()
    require(actual == [request['candidate']['source_commit'], request['candidate']['source_tree']]
            and not subprocess.check_output(['git','-C',str(root),'status','--porcelain'],text=True).strip(),
            'stage2_actual_source_tree')
    require(subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',BASELINE,actual[0]],
                           capture_output=True).returncode == 0, 'stage2_business_baseline')
    return dict(request_sha256=digest(request_raw), owner_sha256=digest(owner_raw),
                source=actual[0], tree=actual[1], expires_at=owner['expires_at'],
                directory=str(directory), ledger=fee['ledger_path'])


class ProbeBudget:
    def __init__(self, path, identity, validator):
        self.path, self.identity, self.validator = Path(path), identity, validator

    @classmethod
    def initialize(cls, path, identity, validator):
        budget = cls(path, identity, validator)
        require(validator() == identity, 'stage2_authority_drift')
        # Marker is separate and survives a missing DB; no reset/retry command.
        for p, mode, raw in ((budget.path.with_suffix('.initialized'),0o400,
                              json.dumps(identity,sort_keys=True).encode()), (budget.path,0o600,b'')):
            fd = os.open(p, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, mode)
            with os.fdopen(fd,'wb') as f:
                f.write(raw);f.flush();os.fsync(f.fileno())
        with budget.connect() as c:
            c.executescript('CREATE TABLE control(id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL, stopped TEXT);'
                            'CREATE TABLE denials(id INTEGER PRIMARY KEY,request_sha TEXT NOT NULL,reason TEXT NOT NULL,observed_at INTEGER NOT NULL);'
                            'CREATE TABLE tool_observations(task_id TEXT NOT NULL,call_id TEXT NOT NULL,server TEXT NOT NULL,tool TEXT NOT NULL,status TEXT NOT NULL,error_present INTEGER NOT NULL,content_sha TEXT,structured_sha TEXT,observed_at INTEGER NOT NULL,source TEXT NOT NULL,PRIMARY KEY(task_id,call_id));'
                            'CREATE TABLE attempts(id INTEGER PRIMARY KEY,probe TEXT NOT NULL,task_id TEXT NOT NULL,run_id TEXT NOT NULL,test_revision TEXT NOT NULL,test_fingerprint TEXT NOT NULL,request_sha TEXT NOT NULL,input_reserved INTEGER NOT NULL,output_reserved INTEGER NOT NULL,cost_reserved INTEGER NOT NULL,state TEXT NOT NULL,response_sha TEXT,input_actual INTEGER,output_actual INTEGER);')
            c.execute('INSERT INTO control VALUES(1,?,NULL)',(json.dumps(identity,sort_keys=True),))
        return budget

    @contextmanager
    def connect(self):
        require(self.validator() == self.identity, 'stage2_authority_drift')
        anchor = self.path.with_suffix('.initialized')
        for p in (self.path, anchor):
            s = p.lstat()
            require(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_nlink == 1
                    and not s.st_mode & 0o077, 'stage2_budget_metadata')
        require(parse(anchor.read_bytes()) == self.identity, 'stage2_budget_anchor')
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA synchronous=FULL')
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback();raise
        finally:
            c.close()

    def allow(self, task):
        require(task['tenant_id'] == 'stage25-test-tenant' and task.get('runtime_test_id')
                and task.get('test_revision') and task.get('test_fingerprint'), 'stage2_runtime_test_only')
        return task

    def check(self, task):
        self.allow(task)
        with self.connect() as c:
            row = c.execute('SELECT * FROM control WHERE id=1').fetchone()
            require(row and row['identity'] == json.dumps(self.identity,sort_keys=True)
                    and row['stopped'] is None, 'stage2_budget_stopped_or_missing')
            # An in-flight/unknown attempt blocks all subsequent requests.
            require(c.execute("SELECT count(*) FROM attempts WHERE state!='COMPLETE'").fetchone()[0] == 0,
                    'stage2_inflight_or_unknown')
        return task

    def reserve(self, task, raw, *, input_tokens, output_tokens):
        cost = input_tokens*INPUT_RATE + output_tokens*OUTPUT_RATE
        require(type(input_tokens) is int and 0 < input_tokens <= LIMITS['input_each']
                and type(output_tokens) is int and 0 < output_tokens <= LIMITS['output_each'],
                'stage2_per_request_tokens')
        self.allow(task)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            control = c.execute('SELECT * FROM control WHERE id=1').fetchone()
            require(control and control['stopped'] is None
                    and control['identity'] == json.dumps(self.identity,sort_keys=True),'stage2_budget_stopped')
            rows = c.execute('SELECT * FROM attempts').fetchall()
            require(all(r['state']=='COMPLETE' for r in rows),'stage2_inflight_or_unknown')
            require(task['_probe'] in PROMPTS, 'stage2_only_two_probe_identities')
            own = [r for r in rows if r['probe']==task['_probe']]
            require(len(rows) < LIMITS['requests'] and len(own) < LIMITS['requests_per_probe'],
                    'stage2_multiturn_http_limit')
            require(all((r['task_id'],r['run_id'],r['test_revision'],r['test_fingerprint']) ==
                        (task['id'],task['run_id'],task['test_revision'],task['test_fingerprint']) for r in own)
                    and not any(r['task_id']==task['id'] and r['probe']!=task['_probe'] for r in rows),
                    'stage2_probe_task_binding_conflict')
            require(not any(r['task_id']==task['id'] and r['request_sha']==digest(raw) for r in rows),
                    'stage2_identical_request_replay')
            require(sum(r['input_reserved'] for r in rows)+input_tokens <= LIMITS['input_total']
                    and sum(r['output_reserved'] for r in rows)+output_tokens <= LIMITS['output_total'],
                    'stage2_cumulative_tokens')
            require(sum(r['cost_reserved'] for r in rows)+cost <= LIMITS['cost_micro_cny'],
                    'stage2_cost_exhausted')
            c.execute('INSERT INTO attempts(probe,task_id,run_id,test_revision,test_fingerprint,request_sha,input_reserved,output_reserved,cost_reserved,state) VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (task['_probe'],task['id'],task['run_id'],task['test_revision'],task['test_fingerprint'],digest(raw),input_tokens,output_tokens,cost,'INFLIGHT'))
            return c.execute('SELECT last_insert_rowid()').fetchone()[0]

    def finish(self, attempt, response_sha, usage):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM attempts WHERE id=?',(attempt,)).fetchone()
            valid = usage and 0 < usage[0] <= row['input_reserved'] and 0 <= usage[1] <= row['output_reserved']
            if not valid:
                c.execute("UPDATE control SET stopped='RESPONSE_UNKNOWN_OR_EXCESS' WHERE id=1")
                c.execute("UPDATE attempts SET state='UNKNOWN' WHERE id=?",(attempt,))
            else:
                c.execute("UPDATE attempts SET state='COMPLETE',response_sha=?,input_actual=?,output_actual=? WHERE id=? AND state='INFLIGHT'",
                          (response_sha,*usage,attempt))
        require(valid,'stage2_response_unknown_or_excess')

    def stop(self):
        with self.connect() as c:
            c.execute("UPDATE control SET stopped='TRANSPORT_FAILED_OR_CANCELLED' WHERE id=1")

    def denied(self,raw,reason):
        with self.connect() as c:
            c.execute('INSERT INTO denials(request_sha,reason,observed_at) VALUES(?,?,?)',
                      (digest(raw),reason[:128],int(time.time())))


class Stage2TextGate(TestTextGate):
    __test__ = False
    forbid_server_tools = True
    requires_mcp_observations = True
    def __init__(self, settings, issuer: RuntimeTokenIssuer, directory):
        require(settings.environment == 'test' and not settings.test_text_gate_enabled
                and settings.agent_runtime_test_tenant_id == 'stage25-test-tenant'
                and settings.model_provider_id == 'deepseek' and settings.model_id == 'deepseek-v4-pro'
                and settings.reasoning_effort == 'high' and settings.model_wire_api == 'responses'
                and settings.model_base_url.rstrip('/') == 'https://api.deepseek.com'
                and settings.embedding_provider == 'local-hash', 'stage2_fixed_configuration')
        self.settings, self.issuer = settings, issuer
        identity = authority(directory)
        self.budget = ProbeBudget(identity['ledger'], identity, lambda:authority(directory))
        self.store, self.server, self.contexts = POCStore(settings.database_url), None, {}

    def task(self, tenant, task_id, agent, user=None, run_id=None):
        task = super().task(tenant,task_id,agent,user,run_id)
        with self.store.connection() as c:
            row = c.execute('SELECT a.slug,u.email FROM agent_templates a JOIN users u ON u.id=? WHERE a.id=?',
                            (task['user_id'],agent)).fetchone()
        require(row and row['email']=='runtime@stage2.test' and row['slug'] in PROMPTS
                and task['input_text']==PROMPTS[row['slug']], 'stage2_exact_probe_identity_and_input')
        with self.budget.connect() as audit:
            previous = audit.execute('SELECT task_id FROM attempts WHERE task_id!=?',(task_id,)).fetchall()
        with self.store.connection() as c:
            for prior in previous:
                state = c.execute('SELECT status FROM tasks WHERE id=?',(prior['task_id'],)).fetchone()
                if not state or state['status']!='completed':
                    self.budget.stop()
                    raise TestTextGateDenied('stage2_prior_runtime_not_completed')
        task['_probe'] = row['slug']
        return task

    async def forward(self, credential, raw):
        token=_DENIAL_CONTEXT.set({'phase':'pre_transform','reserved':False,'http_started':False,
                                  'mcp_checked':False,'task_id':None})
        try:return await self._admitted_forward(credential,raw)
        except TestTextGateDenied as exc:
            self._denial_snapshot(raw,exc)
            try:self.budget.denied(raw,str(exc))
            except Exception:pass  # original refusal remains; no fallback send
            raise
        finally:_DENIAL_CONTEXT.reset(token)

    def _denial_snapshot(self,raw,exc):
        """Private fixed-schema denial receipt; failure can never permit send."""
        destination=os.environ.get('STAGE2_FAILURE_EVIDENCE_DIR')
        if not destination:return
        try:
            from scripts.stage2_failure_snapshot import durable_json,secure_directory
            from uuid import uuid4
            directory=secure_directory(Path(destination))
            data=Path(self.settings.data_dir).resolve()
            private_root=data.parent if data.parent.parent==Path('/private/tmp') else data
            require(not directory.is_relative_to(private_root),'stage2_diagnostic_private_root')
            config=os.environ.get('STAGE25_REDIS_E2E_CONFIG')
            if config and Path(config).is_file():
                root=Path(parse(Path(config).read_bytes())['root'])
                require(not directory.is_relative_to(root),'stage2_diagnostic_private_root')
            context=_DENIAL_CONTEXT.get() or {}
            project=Path(__file__).resolve().parents[1]
            identity=subprocess.check_output(['git','-C',str(project),'show','-s','--format=%H%n%T','HEAD'],text=True,timeout=5).splitlines()
            runner=Path(sys.argv[0]).resolve()
            runner_sha=digest(runner.read_bytes()) if runner.is_file() and runner.stat().st_size<=16777216 else None
            runner_kind='CURRENT_PROCESS_ENTRY_NOT_ORCHESTRATOR'
            authority_dir=self.budget.identity.get('directory')
            if authority_dir:
                request=parse((Path(authority_dir)/'request.json').read_bytes())
                runner=self.budget.path.parent/'formal-stage2-attempt-01/runner.py'
                runner_sha=digest(runner.read_bytes())
                require(runner_sha==request['execution_orchestration']['runner_sha256'],'stage2_diagnostic_runner_hash')
                runner_kind='SCOPE_BOUND_ORCHESTRATOR'
            try:payload=parse(raw)
            except (ValueError,UnicodeError):payload=None
            task=context.get('task_id');observations=getattr(self,'_mcp_observations',{})
            linked=[v for (tid,_),v in observations.items() if tid==task]
            with sqlite3.connect('file:'+str(self.budget.path)+'?mode=ro',uri=True) as c:
                counts={'requests':c.execute('SELECT count(*) FROM attempts').fetchone()[0],
                        'http_complete':c.execute("SELECT count(*) FROM attempts WHERE state='COMPLETE'").fetchone()[0]}
            branch=getattr(exc,'branch_id',None)
            # Non-input denial categories are already fixed public constants.
            known={'stage2_tool_outcome_not_proven_success','stage2_per_request_tokens',
                   'stage2_multiturn_http_limit','stage2_cumulative_tokens','stage2_cost_exhausted',
                   'stage2_inflight_or_unknown','stage2_budget_stopped','stage2_response_unknown_or_excess',
                   'stage2_budget_stopped_or_missing','stage2_identical_request_replay',
                   'test_text_gate_request_too_large','test_text_gate_context_exceeded'}
            if branch is None:branch=str(exc) if str(exc) in known else 'other_guard_no_input_branch'
            phase=getattr(exc,'validation_stage',context.get('phase','request_bound'))
            require(phase in {'pre_transform','post_transform','request_bound','admission'},'stage2_diagnostic_phase')
            record={'kind':'STAGE2_INPUT_DENIAL_BRANCH_V1','observed_at':int(time.time()),
                    'source_commit':identity[0],'source_tree':identity[1],
                    'source_dirty':bool(subprocess.check_output(['git','-C',str(project),'status','--porcelain'],text=True,timeout=5).strip()),
                    'gate_sha256':{n:digest((project/'app'/n).read_bytes()) for n in ('test_text_gate.py','stage2_two_probe_gate.py')},
                    'runner_sha256':runner_sha,'runner_kind':runner_kind,
                    'request_sha256':digest(raw),'request_bytes':len(raw),
                    'validation_stage':phase,'branch_id':branch,
                    'structure':getattr(exc,'safe_structure',input_shape(payload)),
                    'controlled_bounds':getattr(exc,'controlled_bounds',context.get('bound',{})),
                    'limits':LIMITS,'mcp_task_observations':len(linked),
                    'mcp_typed_completed_no_error':sum(v[2]=='completed' and not v[3] for v in linked),
                    'mcp_status_identity_hash_checked':bool(context.get('mcp_checked')),
                    'admission_reserved':context.get('reserved',False),
                    'real_http_started_for_rejected_request':context.get('http_started',False),
                    'prior_attempts':counts}
            durable_json(directory,'input-denial-'+uuid4().hex+'.json',record)
        except Exception:
            # No original request, exception text, or user value may escape here.
            print('STAGE2_INPUT_DENIAL_SNAPSHOT_FAILED_REFUSAL_RETAINED',file=sys.stderr)

    def observe_mcp_tool(self, task_id, item):
        """Observe typed App Server events, never model-supplied output text."""
        from openai_codex.generated.v2_all import McpToolCallThreadItem
        if not isinstance(item,McpToolCallThreadItem):return
        require(any(c[1]==task_id for c in self.contexts.values()),'stage2_mcp_task_context')
        status=item.status.value
        content=item.result.content if item.result is not None else None
        record=(item.server,item.tool,status,item.error is not None,
                digest(json.dumps(content,sort_keys=True,ensure_ascii=True,allow_nan=False).encode()),
                digest(json.dumps(item.result.structured_content,sort_keys=True,ensure_ascii=True,allow_nan=False).encode())
                if item.result is not None and item.result.structured_content is not None else None)
        key=(task_id,item.id)
        observations=getattr(self,'_mcp_observations',{})
        prior=observations.get(key)
        if prior and (prior[:2]!=record[:2] or prior[2]!='inProgress' and prior!=record):
            self.budget.stop()
            raise TestTextGateDenied('stage2_mcp_observation_conflict')
        require(len(observations)<64 or key in observations,'stage2_mcp_observation_limit')
        observations[key]=record
        self._mcp_observations=observations
        with self.budget.connect() as c:
            c.execute('INSERT OR IGNORE INTO tool_observations VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (*key,*record,int(time.time()),'SDK_TYPED_ITEM_COMPLETED'))

    async def await_tool_observations(self,payload,task_id):
        # Notification delivery and the loopback model request have separate
        # readers. Allow bounded event delivery, never an HTTP retry.
        ids=[i.get('call_id') for i in payload.get('input',[]) if isinstance(i,dict)
             and i.get('type') in {'function_call_output','custom_tool_call_output'}
             and 'Process exited with code' not in str(i.get('output',''))]
        deadline=time.monotonic()+1
        while any(getattr(self,'_mcp_observations',{}).get((task_id,i),(None,None,'inProgress'))[2]=='inProgress' for i in ids):
            if time.monotonic()>=deadline:break
            await asyncio.sleep(.01)

    def tool_outcomes(self,payload,*,task_id=None):
        """SDK0.147 success envelopes only; never pay for tool-error repair.

        Do not change content/history. Unknown, running or failed tools stop
        the whole allowance before the next model HTTP reservation.
        """
        for item in payload.get('input',[]) if isinstance(payload.get('input'),list) else []:
            if item.get('type') not in {'function_call_output','custom_tool_call_output'}:continue
            value=item['output']
            text=value if isinstance(value,str) else ''.join(p['text'] for p in value)
            success=bool(re.search(r'(?m)^Process exited with code 0$',text))
            if 'Process exited with code' not in text and '\nOutput:\n' in text:
                try:
                    result=parse(text.split('\nOutput:\n',1)[1])
                    # SDK0.147 serializes structured MCP results as content
                    # blocks, omitting isError from the model history. Its
                    # typed McpToolCall status retains the protocol outcome.
                    observation=getattr(self,'_mcp_observations',{}).get((task_id,item.get('call_id')))
                    calls=[i for i in payload.get('input',[]) if isinstance(i,dict)
                           and i.get('type')=='function_call' and i.get('call_id')==item.get('call_id')]
                    content=result if isinstance(result,(list,dict)) else None
                    expected=digest(json.dumps(content,sort_keys=True,ensure_ascii=True,allow_nan=False).encode())
                    success=bool(observation and len(calls)==1
                        and observation[0]=='platform' and observation[1] in {'enterprise_config_get','knowledge_search','asset_search'}
                        and calls[0].get('name')=='mcp__platform__'+observation[1]
                        and observation[2]=='completed' and observation[3] is False
                        and content is not None and expected in observation[4:6])
                    if isinstance(result,dict):success=success and not result.get('isError',False)
                except (ValueError,TypeError,TestTextGateDenied):success=False
            if not success:
                self.budget.stop()
                raise TestTextGateDenied('stage2_tool_outcome_not_proven_success')

    async def _admitted_forward(self, credential, raw):
        principal = self.issuer.verify(credential,'test-text:request')
        context = self.contexts.get(principal.runtime_profile_id)
        require(context and principal.tenant_id==context[0] and principal.agent_id==context[2],
                'stage2_signed_context')
        diagnostic=_DENIAL_CONTEXT.get()
        if diagnostic is not None:diagnostic['task_id']=context[1]
        payload, namespaces = self.bounded_payload(raw,with_namespaces=True)
        if diagnostic is not None:diagnostic['phase']='post_transform'
        await self.await_tool_observations(payload,context[1])
        self.tool_outcomes(payload,task_id=context[1])
        if diagnostic is not None:diagnostic.update(mcp_checked=True,phase='request_bound')
        # Server-side Search/Fetch and any other independently priced tools
        # are forbidden, even when a future vendor starts billing them.
        require(all(t.get('type') in {'function','custom'} for t in payload.get('tools',[])),
                'stage2_additional_paid_tools_forbidden')
        task = self.task(*context[:3],user=context[3],run_id=context[4])
        bound = self.request_bound(payload)
        if diagnostic is not None:diagnostic.update(bound=bound,phase='admission')
        wire = json.dumps(payload,ensure_ascii=True,allow_nan=False).encode()
        key = os.environ.get(self.settings.codex_api_key_env)
        require(bool(key),'stage2_dedicated_credential_missing')
        attempt = self.budget.reserve(task,wire,input_tokens=bound['input_tokens_upper'],
                                      output_tokens=bound['output_tokens_upper'])
        if diagnostic is not None:diagnostic['reserved']=True
        try:
            import httpx
            async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(retries=0),
                                         trust_env=False,follow_redirects=False,timeout=120) as client:
                if diagnostic is not None:diagnostic['http_started']=True
                response = await client.post('https://api.deepseek.com/responses',content=wire,
                    headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'})
            kind = response.headers.get('content-type','application/json')
            usage = self.confirmed_usage(response.status_code,kind,response.content)
            self.budget.finish(attempt,digest(response.content),usage)
            body = self.sdk_tools(kind,response.content,namespaces)
            return response.status_code,kind,body
        except asyncio.CancelledError:
            self.budget.stop()
            raise TestTextGateDenied('stage2_cancelled_no_retry') from None
        except BaseException:
            self.budget.stop()
            raise

    async def _handle(self, reader, writer):
        try:
            await super()._handle(reader,writer)
        except BaseException:
            self.budget.stop()
            raise
