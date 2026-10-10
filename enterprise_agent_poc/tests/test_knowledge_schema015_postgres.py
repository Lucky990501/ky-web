"""Real PG/pgvector functional tests, NOT semantic-provider or live Native acceptance."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4
import time

import httpx
import psycopg
from psycopg import sql
import pytest

from app.knowledge import KnowledgeProcessingService, KnowledgeRetrievalService, runtime_diagnostic
from app.product_store import ProductStore
from app.settings import settings
from app.storage import storage_provider
from app.store import POCStore
from scripts import migrate

pytestmark = pytest.mark.skipif(not os.environ.get('STAGE1_POSTGRES_ROOT'), reason='marked independent PG16.6 cluster required')
FACT = '# 星槐计划\n星槐计划的独有核验代码是 RAGZETA4729。星槐计划在蓝榆实验室开展，每组限额17人。'
QUERY = '星槐计划的独有核验代码'


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('EXTERNAL_HTTP_FORBIDDEN_IN_RAG_TEST')
    async def deny_async(*args, **kwargs):
        deny()
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', deny)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', deny_async)


@pytest.fixture
def pg(tmp_path):
    root = Path(os.environ['STAGE1_POSTGRES_ROOT']).resolve()
    assert root.parent == Path('/private/tmp') and root.name.startswith('ky-web-stage1-postgres.')
    assert (root/'stage1-isolated.marker').read_text() == 'ky-web-stage1-local-only'
    assert root.stat().st_uid == os.getuid() and (root/'socket').stat().st_mode & 0o077 == 0
    args = dict(host=str(root/'socket'), port=54329, user='stage1_fixture')
    name = 'stage1_rag_' + uuid4().hex
    with psycopg.connect(dbname='postgres', **args, autocommit=True) as conn:
        assert conn.execute('SHOW server_version_num').fetchone()[0] == '160006'
        assert conn.execute('SHOW listen_addresses').fetchone()[0] == ''
        assert Path(conn.execute('SHOW data_directory').fetchone()[0]) == root/'cluster'
        conn.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
    store = POCStore(f'postgresql:///{name}?' + urlencode(args))
    try:
        assert len(migrate.migration_files()) == 15 and migrate.up(store) == 0
        store.seed_demo_data()
        product = ProductStore(store)
        product.initialize()
        product.create_user('tenant-a', 'rag@fixture.invalid', 'unused-fixture-password', 'Synthetic', 'enterprise_admin')
        actor = product.user_by_email('rag@fixture.invalid')
        cfg = replace(settings, environment='test', database_url=store.database_url,
                      object_storage_provider='local', object_storage_dir=tmp_path/'objects',
                      embedding_provider='local-hash', embedding_model='local-hash-v1', embedding_dimension=128,
                      embedding_api_key='', bootstrap_demo_data=False, task_queue='local',
                      knowledge_allow_fallback=False, knowledge_query_guard_enabled=True)
        yield SimpleNamespace(store=store, product=product, cfg=cfg, actor=actor)
    finally:
        with psycopg.connect(dbname='postgres', **args, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def ingest(pg, text=FACT, tenant='tenant-a'):
    key = f'knowledge/{tenant}/{uuid4()}.txt'
    storage_provider(pg.cfg).put(key, text.encode(), 'text/plain')
    record = pg.product.create_knowledge_file(tenant, pg.actor['id'], 'unique-rag-fact.txt', 'text/plain', len(text.encode()), key)
    asyncio.run(KnowledgeProcessingService(pg.product, pg.cfg).process(tenant, record['file_id'], raise_errors=True))
    return pg.product.knowledge_file(tenant, record['file_id'])


def schema_snapshot(pg):
    with pg.store.connection() as conn:
        return [dict(r) for r in conn.execute("SELECT table_name,column_name,data_type,udt_name,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position")], [dict(r) for r in conn.execute("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY indexname")], [dict(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]


def test_original_42703_and_no_schema_or_history_mutation(pg):
    before = schema_snapshot(pg)
    with pytest.raises(psycopg.errors.UndefinedColumn) as error, pg.store.connection() as conn:
        conn.execute('SELECT embedding_dimension FROM knowledge_chunks')
    assert error.value.sqlstate == '42703'
    assert pg.product.knowledge_embedding_schema() == {'type': 'vector', 'dimension': 128, 'dimension_column': False}
    old = pg.product.add_knowledge_text('tenant-b', 'retained historical source', 'Do not rebuild or delete this record.')
    with pg.store.connection() as conn:
        retained = dict(conn.execute('SELECT * FROM knowledge_files WHERE id=?', (old['id'],)).fetchone())
        assert conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()['extversion'] in {'0.6.0', '0.8.6'}
    record = ingest(pg)
    assert record['status'] == 'ready' and record['chunk_count'] > 0
    results = KnowledgeRetrievalService(pg.product, pg.cfg).search('tenant-a', QUERY)
    assert results and results[0]['file_id'] == record['id'] and results[0]['chunk_id']
    assert 'RAGZETA4729' in results[0]['content']
    assert schema_snapshot(pg) == before
    with pg.store.connection() as conn:
        assert dict(conn.execute('SELECT * FROM knowledge_files WHERE id=?', (old['id'],)).fetchone()) == retained


def test_tenant_query_guard_and_provider_model_isolation(pg):
    ingest(pg)
    search = KnowledgeRetrievalService(pg.product, pg.cfg)
    assert search.search('tenant-b', QUERY) == []
    assert search.search('tenant-a', '不存在课程的保证提升成绩价格是多少') == []
    assert search.search('tenant-a', 'ocean turbine blade aerodynamics') == []
    other = KnowledgeRetrievalService(pg.product, replace(pg.cfg, embedding_model='different-model'))
    assert other.search('tenant-a', QUERY) == []


@pytest.mark.parametrize('dimension', [127, 129, 1536])
def test_configured_dimension_rejected_before_provider_or_ddl(pg, dimension, monkeypatch):
    ingest(pg)
    before = schema_snapshot(pg)
    cfg = replace(pg.cfg, embedding_dimension=dimension)
    retrieval = KnowledgeRetrievalService(pg.product, cfg)
    monkeypatch.setattr(retrieval.embedding, '_embed', lambda _: pytest.fail('provider called before schema check'))
    assert 'KNOWLEDGE_EMBEDDING_SCHEMA_DIMENSION_MISMATCH' in runtime_diagnostic(pg.product, cfg)['reasons']
    with pytest.raises(RuntimeError, match='SCHEMA_DIMENSION_MISMATCH'):
        retrieval.search('tenant-a', QUERY)
    with pytest.raises(RuntimeError, match='SCHEMA_DIMENSION_MISMATCH'):
        pg.product.ensure_pgvector_schema(dimension)
    assert schema_snapshot(pg) == before


@pytest.mark.parametrize('bad', [[1.0]*127, [float('nan')]*128, [float('inf')]*128, []])
def test_bad_vector_write_keeps_previous_chunks(pg, bad):
    record = ingest(pg)
    before = pg.product.knowledge_chunks('tenant-a', record['id'])
    with pytest.raises(ValueError, match='DIMENSION_MISMATCH'):
        pg.product.replace_knowledge_chunks('tenant-a', record['id'], [dict(knowledge_base_id=record['knowledge_base_id'], content='invalid', chunk_index=0, embedding=bad)])
    assert pg.product.knowledge_chunks('tenant-a', record['id']) == before


def test_wrong_tenant_write_rejected(pg):
    record = ingest(pg)
    with pytest.raises(ValueError, match='OWNERSHIP_MISMATCH'):
        pg.product.replace_knowledge_chunks('tenant-b', record['id'], [])
    assert pg.product.knowledge_chunks('tenant-a', record['id'])


def test_optional_dimension_column_remains_backward_compatible(pg):
    # Explicit isolated old-production-shaped fixture, NOT online DDL.
    with pg.store.connection() as conn:
        conn.execute('ALTER TABLE knowledge_chunks ADD COLUMN embedding_dimension INTEGER')
    record = ingest(pg)
    with pg.store.connection() as conn:
        assert conn.execute('SELECT embedding_dimension FROM knowledge_chunks WHERE file_id=?', (record['id'],)).fetchone()['embedding_dimension'] == 128
    assert KnowledgeRetrievalService(pg.product, pg.cfg).search('tenant-a', QUERY)


@pytest.mark.parametrize('response', [[], [[0.1]*127], [[0.1]*128, [0.1]*128]])
def test_bad_embedding_batch_preserves_ready_reindex(pg, response):
    record = ingest(pg)
    before = pg.product.knowledge_chunks('tenant-a', record['id'])
    processor = KnowledgeProcessingService(pg.product, pg.cfg)
    async def wrong(texts):
        return response
    processor.embedding.embed_documents = wrong
    with pytest.raises(RuntimeError, match='RESPONSE_DIMENSION_MISMATCH'):
        asyncio.run(processor.process('tenant-a', record['id'], force=True, raise_errors=True))
    assert pg.product.knowledge_chunks('tenant-a', record['id']) == before


def test_local_hash_health_does_not_claim_semantic_readiness(pg):
    diagnostic = runtime_diagnostic(pg.product, pg.cfg)
    assert diagnostic['status'] == 'degraded'
    assert diagnostic['embedding_schema']['dimension'] == 128
    assert any('不是正式' in reason for reason in diagnostic['reasons'])


def test_pg_storage_error_is_not_silent_legacy_fallback(pg, monkeypatch):
    ingest(pg)
    with pg.store.connection() as conn:
        conn.execute('ALTER TABLE knowledge_chunks RENAME COLUMN embedding_model TO broken_model')
    monkeypatch.setattr(pg.store, 'knowledge_search', lambda *a: pytest.fail('silent fallback'))
    with pytest.raises(RuntimeError, match='KNOWLEDGE_RETRIEVAL_STORAGE_UNAVAILABLE'):
        KnowledgeRetrievalService(pg.product, pg.cfg).search('tenant-a', QUERY)


def test_formal_sql_path_with_mocked_128_embedding_adapter(pg, monkeypatch):
    from app.knowledge import OpenAICompatibleEmbeddingProvider, LocalHashEmbeddingProvider
    cfg = replace(pg.cfg, embedding_provider='openai-compatible', embedding_model='synthetic-mock-128',
                  embedding_api_key='isolated-placeholder', embedding_base_url='https://unused.invalid')
    local = LocalHashEmbeddingProvider(128)
    async def documents(self, texts):
        return [local._embed(text) for text in texts]
    monkeypatch.setattr(OpenAICompatibleEmbeddingProvider, 'embed_documents', documents)
    monkeypatch.setattr(OpenAICompatibleEmbeddingProvider, 'embed_query_sync', lambda self, text: local._embed(text))
    pg.cfg = cfg
    record = ingest(pg)
    assert KnowledgeRetrievalService(pg.product, cfg).search('tenant-a', QUERY)[0]['file_id'] == record['id']


def test_txt_http_upload_parser_storage_and_search(pg, monkeypatch):
    from app import main
    from app.auth import UserPrincipal
    monkeypatch.setattr(main, 'settings', pg.cfg)
    monkeypatch.setattr(main, 'store', pg.store)
    monkeypatch.setattr(main, 'product_store', pg.product)
    monkeypatch.setattr(main, 'knowledge_processing', KnowledgeProcessingService(pg.product, pg.cfg))
    monkeypatch.setattr(main, 'knowledge_retrieval', KnowledgeRetrievalService(pg.product, pg.cfg))
    token = main.sessions.issue(UserPrincipal(pg.actor['id'], 'tenant-a', 'enterprise_admin', main.sessions.credential_version(pg.actor['password_hash'])))
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://fixture.local', cookies={'workbench_session': token}) as client:
            response = await client.post('/api/v1/knowledge/files', files={'file': ('fact.txt', FACT.encode(), 'text/plain')})
            assert response.status_code == 202, response.text
            file_id = response.json()['file_id']
            for _ in range(100):
                await asyncio.sleep(.01)
                detail = pg.product.knowledge_file('tenant-a', file_id)
                if detail['status'] in {'ready', 'failed'}:
                    break
            assert detail['status'] == 'ready' and detail['chunk_count'] > 0
            result = await client.post('/api/v1/knowledge/retrieval-test', json={'query': QUERY, 'top_k': 5})
            assert result.status_code == 200 and result.json()['results'][0]['file_id'] == file_id
    asyncio.run(check())


@pytest.mark.parametrize('agent', ['copywriting-agent', 'campaign-agent'])
def test_agent_service_consumes_real_mcp_retrieval_with_scripted_runtime(pg, agent):
    """No prompt injection: the runtime double calls the real signed MCP service.

    Proves tool/result/trace plumbing only, not model skill behavior or quality.
    """
    from app.domain import RuntimeSession, RuntimeTurn
    from app.runtime.base import RuntimeProvider
    from app.platform_mcp.service import PlatformMCPService
    from app.security import RuntimeTokenIssuer, RuntimePrincipal
    from app.service import AgentService
    record = ingest(pg)
    issuer = RuntimeTokenIssuer('isolated-rag-runtime-token-secret')
    mcp = PlatformMCPService(pg.store, issuer, pg.cfg)

    class ToolRuntime(RuntimeProvider):
        async def create_session(self, profile, developer_instructions):
            self.token = issuer.issue(RuntimePrincipal(profile.tenant_id, profile.agent_id, profile.id,
                ('enterprise_config:read', 'knowledge:search', 'assets:search'), int(time.time())+60))
            return RuntimeSession('isolated-'+uuid4().hex, profile.id)
        async def resume_session(self, *a, **kw):
            raise AssertionError('new conversation only')
        async def run_turn(self, session, message):
            assert '蓝榆实验室' not in message and 'RAGZETA4729' not in message
            context = mcp.enterprise_config_get(self.token)
            results = mcp.knowledge_search(self.token, message)
            assets = mcp.asset_search(self.token, message)
            assert results[0]['file_id'] == record['id']
            self.results = results
            calls = tuple({'server': 'platform', 'tool': tool, 'status': 'completed', 'output_summary': value}
                          for tool, value in [('enterprise_config_get', str(context)), ('knowledge_search', results[0]['content']), ('asset_search', str(assets))])
            return RuntimeTurn(session.thread_id, results[0]['content'], mcp_calls=calls)
        async def close(self):
            pass

    runtime = ToolRuntime()
    result = asyncio.run(AgentService(pg.store, runtime, pg.cfg).run('tenant-a', agent, QUERY))
    assert '蓝榆实验室' in result.text and 'RAGZETA4729' in result.text
    trace = pg.store.run_trace(result.run_id, 'tenant-a')['payload']
    assert trace['knowledge_calls'] and trace['artifacts']['knowledge_context_used']
    other = issuer.issue(RuntimePrincipal('tenant-b', agent, 'other', ('knowledge:search',), int(time.time())+60))
    assert mcp.knowledge_search(other, QUERY) == []
