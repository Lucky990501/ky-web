"""Real service/MCP/SDK/finalization with fake HTTP + fake OSS, never providers."""
import asyncio
import base64
import io
import os
import time
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from openai_codex.generated.v2_all import McpToolCallResult
from PIL import Image
from mcp.types import CallToolResult, TextContent
from mcp.server.fastmcp.exceptions import ToolError

from app.domain import RuntimeSession
from app.platform_mcp.service import PlatformMCPService
from app.product_service import TaskService
from app.product_store import ProductStore
from app.runtime.codex_provider import CodexRuntimeProvider
from app.security import RuntimePrincipal, RuntimeTokenIssuer
from app.service import AgentService
from app.settings import Settings
from app.storage import OSSStorage
from app.store import POCStore
from app.tool_dependencies import resolve_dependencies


def execute_sequence(tmp_path, monkeypatch, sequence=('validation','400','success'), *, attached=True, forged=False):
    monkeypatch.setenv('ENTERPRISE_POC_DATABASE_URL', f'sqlite:///{tmp_path / "retry.db"}')
    monkeypatch.setenv('ENTERPRISE_POC_DATA_DIR', str(tmp_path/'data'))
    monkeypatch.setenv('RETRY_IMAGE_TEST_KEY', 'fixture-only-key')
    settings=replace(Settings.from_env(), image_api_key_env='RETRY_IMAGE_TEST_KEY',
                     object_storage_provider='oss', object_storage_dir=tmp_path/'absent-local',
                     oss_access_key_id='fixture-id', oss_access_key_secret='fixture-secret',
                     oss_endpoint='https://oss.invalid', oss_bucket_name='fixture')
    store=POCStore(tmp_path/'retry.db');store.seed_demo_data()
    product=ProductStore(store);product.initialize()
    product.create_user('tenant-a','retry@test.invalid','unused','Fixture','member')
    user=product.user_by_email('retry@test.invalid')
    output=io.BytesIO();Image.new('RGB',(12,10),(20,90,160)).save(output,format='PNG');picture=output.getvalue()
    objects={'chat-images/tenant-a/source.png':picture};puts=[];posts=[];reads=[]
    class Bucket:
        def get_object(self,key):
            reads.append(key)
            return io.BytesIO(objects[key])
        def put_object(self,key,content,headers):
            puts.append(key);objects[key]=content
    monkeypatch.setattr(OSSStorage,'_bucket',lambda _:Bucket())
    attachment=product.create_chat_image_attachment('tenant-a',user['id'],'chat-images/tenant-a/source.png',
        'reference.png','image/png',len(picture),12,10) if attached else None
    task=product.create_task('tenant-a',user['id'],'image-agent','single reference edit',None,
                             attachment_ids=[attachment['id']] if attachment else [])
    issuer=RuntimeTokenIssuer('retry-fixture-runtime-secret-long-enough')
    token=issuer.issue(RuntimePrincipal('tenant-a','image-agent','profile-a',('image:generate',),int(time.time())+300))
    scope='runtime:'+issuer.issue_task_scope('tenant-a',task['id'])
    from app.platform_mcp import server
    monkeypatch.setattr(server,'service',PlatformMCPService(store,issuer,settings))
    monkeypatch.setattr(server,'_bearer_from_context',lambda _:token)
    monkeypatch.setattr(server,'_execution_scope_from_context',lambda *_:scope)
    expected=[step for step in sequence if step not in ('validation','invalid')]
    class Client:
        def __init__(self,**_):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
        async def post(self,url,**kwargs):
            posts.append(url)
            assert url=='https://llm-api.net/v1/images/'+('edits' if attached else 'generations')
            if attached:
                assert kwargs['data']['size']=='1024x1024'
                assert kwargs['files']['image']==('reference.png',picture,'image/png')
            step=expected.pop(0)
            if step=='400':
                return httpx.Response(400,json={'error':{'message':'PRIVATE-PROVIDER-DETAIL'}})
            return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(picture).decode()}]})
    monkeypatch.setattr(httpx,'AsyncClient',Client)
    native=server.create_mcp()
    class Runtime:
        turns=0
        async def create_session(self,profile,*_args,**_kwargs):return RuntimeSession('retry-fixture-thread',profile.id)
        async def resume_session(self,profile,thread_id,**_kwargs):return RuntimeSession(thread_id,profile.id)
        async def run_turn(self,session,_message):
            self.turns+=1;items=[];receipt=None
            for index,step in enumerate(sequence):
                args={'prompt':f'fixture requested style {index} '+('3:4' if step=='validation' else '4:5'),
                      'references':[],'aspect_ratio':'3:4' if step=='validation' else '4:5'}
                if step=='invalid':
                    args['retry_of']='rtr1_forged'
                elif receipt and step=='400':
                    args['retry_of']='rtr1_forged' if forged else receipt
                elif receipt and step=='success' and '400' not in sequence:
                    args['retry_of']=receipt
                try:
                    raw=await native.call_tool('image_generation',args)
                except ToolError as error:
                    # In-process FastMCP raises; the actual MCP HTTP adapter
                    # serializes this same failure as an isError result.
                    raw=CallToolResult(isError=True, content=[TextContent(type='text', text=str(error))])
                if isinstance(raw,(tuple,list)):
                    content,structured=raw
                    sdk=McpToolCallResult(content=[x.model_dump() for x in content],structured_content=structured)
                    failed=False
                else:
                    failed=raw.isError
                    sdk=McpToolCallResult.model_validate(raw.model_dump(by_alias=True))
                    receipt=(raw.structuredContent or {}).get('retry_of') or receipt
                items.append(SimpleNamespace(id=f'call-{index}',server='platform',tool='image_generation',
                    arguments=args,result=sdk,status='failed' if failed else 'completed',error=None))
            final=SimpleNamespace(items=items,usage=None,final_response='参考图片编辑完成。',status='completed',
                                  error=None,duration_ms=1,turn_id='fixture-turn')
            return CodexRuntimeProvider(None)._runtime_turn_from_result(session,SimpleNamespace(),final)
    runtime=Runtime();agents=AgentService(store,runtime,settings);service=TaskService(product,agents)
    asyncio.run(service.execute(task))
    saved=product.task_for_worker(task['id']);trace=store.run_trace(saved['run_id'],'tenant-a')
    return SimpleNamespace(store=store,product=product,task=saved,trace=trace,service=service,
                           posts=posts,puts=puts,reads=reads,objects=objects,runtime=runtime,attachment=attachment)


def test_real_failure_sequence_closes_or_reproduces_original_baseline(tmp_path,monkeypatch):
    result=execute_sequence(tmp_path,monkeypatch)
    calls=result.trace['payload']['mcp_calls']
    assert calls, {'fixture_error': result.trace['payload'].get('error'), 'mock_posts': len(result.posts)}
    assert [c['status'] for c in calls]==['failed','failed','completed']
    assert len(result.posts)==2 and len(result.puts)==1
    if os.environ.get('REFERENCE_DEPENDENCY_BASELINE')=='1':
        assert result.task['status']==result.trace['status']=='failed'
        assert result.task['error_code']=='required_tool_dependency_error'
        assert sum(not g['satisfied'] for g in result.trace['payload']['logical_tool_dependencies'])==2
        return
    assert result.task['status']==result.trace['status']=='completed'
    assert len(result.trace['payload']['logical_tool_dependencies'])==1
    group=result.trace['payload']['logical_tool_dependencies'][0]
    assert group['satisfied'] and group['active_attempt_id']==calls[-1]['attempt_id']
    assert calls[1]['retry_receipt_validated'] is True and calls[1]['provider_http_status']==400
    assert calls[1]['failure_category']=='provider_http_error' and not calls[1].get('lineage_error')
    assert calls[0]['provider_invoked'] is False
    assert calls[0]['superseded_by']==calls[1]['attempt_id']  # Preserve explicit edge.
    assert calls[1]['superseded_by']==calls[2]['attempt_id']
    assert all(c['dependency_satisfied_by']==calls[2]['attempt_id'] for c in calls[:2])
    assert len({c['request_fingerprint'] for c in calls})==3
    assert all(c['logical_dependency_id']==group['dependency_id'] for c in calls)
    assert result.trace['payload']['tool_calls_completed'] is False
    assert result.trace['payload']['required_tool_calls_completed'] is True


@pytest.mark.parametrize('sequence,completed,posts',[
    (('validation','success'),True,1), (('400','success'),True,2),
    (('validation','400','success'),True,2), (('validation',),False,0),
    (('400',),False,1), (('success',),True,1), (('success','400'),True,2),
])
def test_bound_reference_result_attempts(tmp_path,monkeypatch,sequence,completed,posts):
    result=execute_sequence(tmp_path,monkeypatch,sequence)
    assert result.task['status']==('completed' if completed else 'failed')
    assert len(result.posts)==posts
    if not completed:
        assert result.task['error_code']=='required_tool_dependency_error'


def test_success_is_persisted_once_and_history_uses_selected_oss_result(tmp_path,monkeypatch):
    r=execute_sequence(tmp_path,monkeypatch)
    assert r.task['status']=='completed'
    user=r.product.user_by_email('retry@test.invalid')
    detail=r.product.conversation_detail('tenant-a',user['id'],r.task['conversation_id'])
    assistant=next(x for x in detail['messages'] if x['id']=='task:'+r.task['id']+':assistant')
    assert assistant['generation']['storage_key']==r.puts[0]
    assert len(detail['generations'])==1 and detail['generations'][0]['mime_type']=='image/png'
    assert r.service._persist_result(r.task,r.trace)['replayed']
    asyncio.run(r.service.execute(r.task))
    assert r.runtime.turns==1 and len(r.puts)==1 and len(r.posts)==2
    with r.store.connection() as conn:
        for table in ('generations','task_results','credit_transactions'):
            assert conn.execute(f'SELECT count(*) n FROM {table} WHERE task_id=?',(r.task['id'],)).fetchone()['n']==1
        assert conn.execute('SELECT count(*) n FROM messages WHERE id=?',('task:'+r.task['id']+':assistant',)).fetchone()['n']==1
    assert not r.service._agents._settings.object_storage_dir.exists()


def test_generations_first_success_unchanged(tmp_path,monkeypatch):
    result=execute_sequence(tmp_path,monkeypatch,('success',),attached=False)
    assert result.task['status']=='completed' and len(result.posts)==len(result.puts)==1
    assert 'image_dependency' not in result.trace['payload']['mcp_calls'][0]


def test_invalid_receipt_cannot_be_closed_by_later_image(tmp_path,monkeypatch):
    r=execute_sequence(tmp_path,monkeypatch,('validation','invalid','success'))
    assert r.task['status']=='failed' and r.task['error_code']=='required_tool_dependency_error'
    assert r.trace['payload']['mcp_calls'][1]['failure_category']=='invalid_retry_lineage'


def test_separate_groups_tools_and_scopes_do_not_close_each_other(tmp_path,monkeypatch):
    from app.tool_dependencies import reference_image_dependency, attempt_observation, fingerprint
    r=execute_sequence(tmp_path,monkeypatch)
    first,second,success=r.trace['payload']['mcp_calls']
    args={'prompt':'independent task request','references':[],'aspect_ratio':'1:1'}
    proof=reference_image_dependency('tenant-a','other-task','other-reference')
    packet={'results':[{'storage_key':'generated/tenant-a/other.png','format':'png','mime_type':'image/png',
                        'width':12,'height':10,'reference_images_used':['other-reference']}],
            '_tool_dependency':{'contract':'required-tool-input-retry-v1.1','status':'completed','provider_invoked':True,
                                'image_dependency':proof,'request_fingerprint':fingerprint(args)}}
    other={'server':'platform','tool':'image_generation','status':'completed',
           **attempt_observation(args,{'structuredContent':packet},scope=success['execution_scope'],
                                 tool_call_id='other',server='platform',tool='image_generation')}
    calls,groups,required=resolve_dependencies([first,second,other])
    assert len(groups)==2 and not required['image_generation']['satisfied']
    _,groups,required=resolve_dependencies([first,second,success,other])
    assert len(groups)==2 and all(g['satisfied'] for g in groups) and required['image_generation']['satisfied']
    for change in ({'tool':'asset_search'},{'server':'foreign'},{'execution_scope':'different-turn'}):
        unrelated={**success,**change,'attempt_id':'unrelated'}
        _,groups,_=resolve_dependencies([first,second,unrelated])
        assert len(groups)==2 and not groups[0]['satisfied']


@pytest.mark.parametrize('damage',['missing','foreign_reference','invalid_mime','invalid_group','request_mismatch'])
def test_bad_result_contract_or_binding_cannot_close_group(tmp_path,monkeypatch,damage):
    from app.tool_dependencies import attempt_observation,tool_result_payload
    r=execute_sequence(tmp_path,monkeypatch)
    first,second,success=r.trace['payload']['mcp_calls']
    # Reconstruct a response-contract fixture independently of persistence.
    args=success['submitted_args'];proof=success['image_dependency']
    packet={'results':[{'storage_key':'generated/tenant-a/output.png','format':'png','mime_type':'image/png',
                        'width':12,'height':10,'reference_images_used':[proof['attachment_id']]}],
            '_tool_dependency':{'contract':'required-tool-input-retry-v1.1','status':'completed','provider_invoked':True,
                                'image_dependency':proof,'request_fingerprint':success['request_fingerprint']}}
    if damage=='missing':packet['results']=[]
    elif damage=='foreign_reference':packet['results'][0]['reference_images_used']=['foreign']
    elif damage=='invalid_mime':packet['results'][0]['mime_type']='image/gif'
    elif damage=='invalid_group':packet['_tool_dependency']['image_dependency']={**proof,'group_id':'0'*64}
    else:packet['_tool_dependency']['request_fingerprint']='0'*64
    damaged={**success,**attempt_observation(args,{'structuredContent':packet},scope=success['execution_scope'],tool_call_id='damaged',server='platform',tool='image_generation')}
    damaged.pop('image_dependency',None)
    damaged.update(attempt_observation(args,{'structuredContent':packet},scope=success['execution_scope'],tool_call_id='damaged',server='platform',tool='image_generation'))
    _,groups,required=resolve_dependencies([first,second,damaged])
    assert not required['image_generation']['satisfied']


def test_success_contract_is_only_recognized_for_actual_tool_identity(tmp_path,monkeypatch):
    from app.tool_dependencies import attempt_observation
    r=execute_sequence(tmp_path,monkeypatch)
    success=r.trace['payload']['mcp_calls'][-1]
    packet={'results':[{'storage_key':'generated/tenant-a/success.png','format':'png','mime_type':'image/png',
                        'width':12,'height':10,'reference_images_used':[r.attachment['id']]}],
            '_tool_dependency':{'contract':'required-tool-input-retry-v1.1','status':'completed','provider_invoked':True,
                                'image_dependency':success['image_dependency'],
                                'request_fingerprint':success['request_fingerprint']}}
    for server,tool in [('foreign','image_generation'),('platform','asset_search')]:
        observation=attempt_observation(success['submitted_args'],{'structuredContent':packet},
            scope=success['execution_scope'],tool_call_id='foreign',server=server,tool=tool)
        assert 'image_dependency' not in observation


def test_failed_http_receipt_never_leaks_provider_response_or_credentials(tmp_path,monkeypatch,caplog):
    r=execute_sequence(tmp_path,monkeypatch)
    payload=r.trace['payload']
    assert payload['mcp_calls'][1]['provider_http_status']==400
    assert 'PRIVATE-PROVIDER-DETAIL' not in str(payload) and 'fixture-only-key' not in str(payload)
    assert 'PRIVATE-PROVIDER-DETAIL' not in caplog.text
    assert 'fixture-only-key' not in caplog.text


def test_bad_explicit_retry_cannot_merge_different_attested_requirement(tmp_path,monkeypatch):
    from app.tool_dependencies import reference_image_dependency
    r=execute_sequence(tmp_path,monkeypatch)
    first,second,_=r.trace['payload']['mcp_calls']
    wrong={**second,'image_dependency':reference_image_dependency('tenant-a','foreign-task','foreign-reference')}
    calls,groups,required=resolve_dependencies([first,wrong])
    assert calls[1]['lineage_error']=='invalid_retry_lineage'
    assert len(groups)==2 and not required['image_generation']['satisfied']


def test_latest_valid_success_selected_and_failed_attempts_never_persist(tmp_path,monkeypatch):
    r=execute_sequence(tmp_path,monkeypatch,('validation','400','success','success'))
    assert r.task['status']=='completed'
    group=r.trace['payload']['logical_tool_dependencies'][0]
    assert group['active_attempt_id']==r.trace['payload']['mcp_calls'][-1]['attempt_id']
    with r.store.connection() as conn:
        rows=conn.execute('SELECT storage_key FROM generations WHERE task_id=?',(r.task['id'],)).fetchall()
        assert len(rows)==1 and rows[0]['storage_key']==r.puts[-1]
        assert conn.execute('SELECT count(*) n FROM messages WHERE id=?',('task:'+r.task['id']+':assistant',)).fetchone()['n']==1
