"""Installed SDK/CLI -> actual loopback gate -> mocked upstream only.

Not a live Agent/Worker/Native or paid Provider qualification.
"""
import asyncio
import json
import os
import types

import httpx
import pytest
from openai_codex import AsyncCodex, CodexConfig

from app.runtime.codex_provider import CodexRuntimeManager
from app.test_text_gate import TestTextGate
from test_test_text_gate import fixture


@pytest.mark.parametrize('failure',['http500','broken_sse'])
def test_installed_sdk_no_hidden_http_or_stream_retry(fixture,monkeypatch,failure):
    settings,_,task,budget,gate,profile=fixture
    calls=[]
    def handler(req):
        calls.append(req)
        if failure=='http500':
            return httpx.Response(500,json={'error':{'message':'isolated upstream failure'}})
        return httpx.Response(200,headers={'content-type':'text/event-stream'},
            content=b'event: response.created\ndata: {"type":"response.created","response":'
                    b'{"id":"isolated","object":"response","status":"in_progress","output":[]}}\n\n')
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kw:httpx.MockTransport(handler))
    manager=CodexRuntimeManager(settings,types.SimpleNamespace(deploy=lambda *a:None),gate.issuer)

    async def run():
        url,credential=await gate.bind(profile,task['id'])
        home,workspace,_,_=manager._prepare_profile(profile,task_id=task['id'],gate_url=url)
        # Keep actual generated provider configuration. Only omit MCP transport
        # in this wire/retry diagnostic; production Task and auth tests are separate.
        config=(home/'config.toml').read_text().split('[mcp_servers.platform]')[0]
        (home/'config.toml').write_text(config)
        env={key:'' for key in os.environ
             if any(word in key.upper() for word in ('KEY','TOKEN','SECRET','PASSWORD','PROXY'))}
        env.update(CODEX_HOME=str(home),PYTHONDONTWRITEBYTECODE='1')
        env[settings.codex_api_key_env]=credential
        async with AsyncCodex(CodexConfig(cwd=str(workspace),env=env)) as codex:
            thread=await codex.thread_start(base_instructions='Synthetic SDK retry diagnostic.',ephemeral=True)
            turn=await thread.turn('Reply OK. Do not execute tools.')
            with pytest.raises(RuntimeError):
                async with asyncio.timeout(30):
                    await turn.run()
        await gate.close()
        await manager.close()
    asyncio.run(run())
    assert len(calls)==1
    final=json.loads(calls[0].content)
    assert isinstance(final['input'],list) and final['instructions']
    assert final['tools'] and final['max_output_tokens']==8192
    assert final['reasoning']['effort']=='high'
    assert TestTextGate.request_bound(final)['input_tokens_upper']>1024
    with budget.connect() as c:
        assert c.execute('SELECT spent FROM budget').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM attempts').fetchone()[0]==1
