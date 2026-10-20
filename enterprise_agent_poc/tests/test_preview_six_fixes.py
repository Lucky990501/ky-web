"""FIXTURE_ONLY focused six-fault regression; no external Provider."""
import asyncio
import copy
import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.preview_request_context import compact_knowledge_history
from app.preview_task_admission import require_task_capacity
from app.preview_budget import bounded_text_output
from app.test_text_gate import TestTextGate


def knowledge_body():
    source={'file_id':'fixture-file','chunk_id':'fixture-chunk','filename':'FIXTURE.md','content':'事实与引用完整保留。'*300}
    items=[{'role':'user','content':'FIXTURE_ONLY 活动需求'}]
    for i in range(25):
        items.extend([{'type':'function_call','name':'mcp__platform__knowledge_search','call_id':f'c{i}','arguments':'{}'},
            {'type':'function_call_output','call_id':f'c{i}','output':'Output:\n'+json.dumps({'result':[source]},ensure_ascii=False)}])
    return {'model':'deepseek-v4-pro','max_output_tokens':8192,'input':items,'tools':[{'type':'function','name':'mcp__platform__knowledge_search','parameters':{'type':'object'}}]}


def test_repeated_knowledge_preserves_first_source_all_call_ids_and_citations():
    original=knowledge_body();saved=copy.deepcopy(original)
    compacted,stats=compact_knowledge_history(original)
    assert original==saved and stats['repeated_source_bodies']==24
    assert compacted['input'][:3]==original['input'][:3]
    assert [i.get('call_id') for i in compacted['input']]==[i.get('call_id') for i in original['input']]
    assert 'fixture-file' in compacted['input'][-1]['output'] and 'fixture-chunk' in compacted['input'][-1]['output']
    assert 'earlier tool call c0' in compacted['input'][-1]['output']
    assert len(json.dumps(compacted,ensure_ascii=True))<262144
    bounds=TestTextGate.request_bound(compacted)
    assert bounds['input_tokens_upper']>0


def test_distinct_or_nonknowledge_results_and_user_history_never_truncated():
    body=knowledge_body();body['input'][3]['name']='mcp__platform__enterprise_config_get'
    body['input'][4]['output']='x'*10000
    result,stats=compact_knowledge_history(body)
    assert result['input'][4]['output']==body['input'][4]['output']
    assert result['input'][0]==body['input'][0]
    assert stats['repeated_source_bodies']==23


@pytest.mark.parametrize('bad',['not json','[{"content":"no source identity"}]','[]','[{"file_id":"f","chunk_id":"c","content":null}]'])
def test_unknown_tool_shapes_unchanged(bad):
    body=knowledge_body();body['input'][2]['output']=bad
    result,_=compact_knowledge_history(body)
    assert result['input'][2]['output']==bad


@pytest.mark.parametrize('active',[0,1,2,3,4,5])
def test_bounded_independent_task_admission(active):
    connection=sqlite3.connect(':memory:');connection.execute('create table tasks(status text)')
    connection.executemany('insert into tasks values(?)',[('running',)]*active+[('completed',),('failed',)])
    @contextmanager
    def connect():yield connection
    store=SimpleNamespace(connection=connect)
    if active<4:require_task_capacity(store)
    else:
        with pytest.raises(PermissionError):require_task_capacity(store)
    connection.close()


def test_reader_system_caption_removed_author_caption_preserved():
    from app.preview_illustration_html import ArticleTree
    tree=ArticleTree('<h1>FIXTURE</h1><p>正文</p><figure><figcaption>作者明确图注</figcaption></figure>')
    html=tree.html([{'url':'/preview/api/image.png','filename':'image.png','position':0,'label':'课堂'}])
    assert 'AI配图示意' not in html and '非真实活动照片' not in html
    assert '作者明确图注' in html and 'image.png' in html


def test_flash_catalog_only_supported_in_preview_gate():
    from app.runtime.codex_provider import CodexRuntimeManager
    with pytest.raises(ValueError):CodexRuntimeManager._deepseek_model_catalog('deepseek-flash')
    assert CodexRuntimeManager._deepseek_model_catalog('deepseek-v4-pro')['slug']=='deepseek-v4-pro'
    assert CodexRuntimeManager._deepseek_model_catalog('deepseek-flash',preview_flash=True)['slug']=='deepseek-flash'


@pytest.mark.parametrize('agent',['copywriting-agent','campaign-agent','preview-wechat-agent'])
def test_flash_actual_peak_rates_complete_output_and_existing_cap(agent):
    body=knowledge_body();body,_=compact_knowledge_history(body);body['model']='deepseek-flash'
    accepted,cost=bounded_text_output(body,{'text_input_cny_per_million':'2','text_output_cny_per_million':'8'},agent)
    assert accepted['max_output_tokens']==8192 and Decimal('0')<cost<=5


def test_preview_navigation_parallel_and_production_branch_retained():
    source=(Path(__file__).parents[1]/'app/static/workbench.js').read_text(encoding='utf-8')
    target=source[source.index('async function agentWorkspaceV2'):source.index('async function submitAgentTaskV2')]
    assert "window.WORKBENCH_PREVIEW_BASE==='/preview'" in target
    import re
    assert 'Promise.all([' in target and re.search(r'<textarea[^>]*\bdisabled',target)
    assert "principalIsCurrent(owner)" in target and 'selectedTask.id!==selectedTaskId' in target


def test_image_exception_group_safe_classification_source():
    source=(Path(__file__).parents[1]/'app/preview_image_execution.py').read_text(encoding='utf-8')
    assert "'leaf_error_types'" in source and "getattr(value,'exceptions',None)" in source
    assert 'repr(error)' not in source


def test_successful_empty_image_search_is_not_a_tool_failure():
    from app.preview_image_context import collect_context
    from unittest.mock import Mock
    class Session:
        async def call_tool(self,name,args):
            return SimpleNamespace(isError=False,structuredContent={'result':[]},content=[])
    result,audit=asyncio.run(collect_context(Session(),{'id':'fixture-task','tenant_id':'preview-fixture','user_id':'fixture-user','input_text':'FIXTURE_ONLY poster'},Mock(),has_knowledge=True,enterprise_config={}))
    assert audit['knowledge_search_performed'] and audit['sources']==[]
    assert result.startswith('FIXTURE_ONLY poster')


def test_failed_image_search_still_stops_before_provider():
    from app.preview_image_context import collect_context
    from unittest.mock import Mock
    class Session:
        async def call_tool(self,name,args):return SimpleNamespace(isError=True,structuredContent=None,content=[])
    with pytest.raises(RuntimeError,match='preview_context_tool_failed'):
        asyncio.run(collect_context(Session(),{'input_text':'FIXTURE_ONLY'},Mock(),has_knowledge=True,enterprise_config={}))
