"""Schema001–015 + real PG16.6/pgvector0.8.6, no paid/semantic-model claims."""
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

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

pytestmark=pytest.mark.skipif(not os.environ.get('STAGE1_POSTGRES_ROOT'),
                            reason='marked independent PG16.6/pgvector0.8.6 required')
FACT='# 星槐验收计划\n星槐验收计划的独有核验代码是 RAGBATCH14729。活动地点是蓝榆实验室，每组限额17人。\n'
QUERY='星槐验收计划的独有核验代码'
header=FACT.encode()
filler=(b'\n# Synthetic appendix\nThis is unrelated isolated markdown appendix text, not a production document.\n'*1000)
MARKDOWN_77KB=header+filler[:77*1024-len(header)]
assert len(MARKDOWN_77KB)==78848


@pytest.fixture(autouse=True)
def forbid_paid_http(monkeypatch):
    def deny(*a,**kw): raise AssertionError('EXTERNAL_PROVIDER_FORBIDDEN')
    async def deny_async(*a,**kw): deny()
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',deny)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',deny_async)


@pytest.fixture
def pg(tmp_path):
    root=Path(os.environ['STAGE1_POSTGRES_ROOT']).resolve()
    assert root.parent==Path('/private/tmp') and root.name.startswith('ky-web-stage1-postgres.')
    assert (root/'stage1-isolated.marker').read_text()=='ky-web-stage1-local-only'
    assert root.stat().st_uid==os.getuid() and root.stat().st_mode & 0o077==0
    args=dict(host=str(root/'socket'),port=54329,user='stage1_fixture')
    name='stage1_batch1_rag_'+uuid4().hex
    with psycopg.connect(dbname='postgres',**args,autocommit=True) as c:
        assert c.execute('SHOW server_version_num').fetchone()[0]=='160006'
        assert c.execute('SHOW listen_addresses').fetchone()[0]==''
        assert Path(c.execute('SHOW data_directory').fetchone()[0])==root/'cluster'
        c.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
    store=POCStore(f'postgresql:///{name}?'+urlencode(args))
    try:
        assert len(migrate.migration_files())==15 and migrate.up(store)==0
        store.seed_demo_data()
        product=ProductStore(store)
        product.initialize()
        for tenant,role,mail in [('tenant-a','enterprise_admin','a'),('tenant-b','enterprise_admin','b'),
                                 ('tenant-a','member','member')]:
            product.create_user(tenant,f'{mail}@rag.invalid','isolated-only-password',mail,role)
        actors={key:product.user_by_email(f'{key}@rag.invalid') for key in ('a','b','member')}
        cfg=replace(settings,environment='test',database_url=store.database_url,
                    object_storage_provider='local',object_storage_dir=tmp_path/'objects',
                    embedding_provider='local-hash',embedding_model='local-hash-v1',embedding_dimension=128,
                    embedding_api_key='',bootstrap_demo_data=False,task_queue='local',
                    knowledge_allow_fallback=False,knowledge_query_guard_enabled=True)
        with store.connection() as c:
            assert c.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()['extversion']=='0.8.6'
        yield SimpleNamespace(store=store,product=product,cfg=cfg,actors=actors)
    finally:
        with psycopg.connect(dbname='postgres',**args,autocommit=True) as c:
            c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def schema(pg):
    with pg.store.connection() as c:
        return [[dict(r) for r in c.execute(statement)] for statement in (
            "SELECT table_name,column_name,data_type,udt_name,column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position",
            "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY indexname",
            "SELECT conname,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY conname,conrelid",
            "SELECT tgname,pg_get_triggerdef(oid) AS definition FROM pg_trigger WHERE NOT tgisinternal ORDER BY tgname,tgrelid",
            'SELECT * FROM schema_migrations ORDER BY version')]


def ingest(pg,body=FACT.encode(),tenant='tenant-a'):
    key=f'knowledge/{tenant}/{uuid4()}.md'
    storage_provider(pg.cfg).put(key,body,'text/markdown')
    actor=pg.actors['a' if tenant=='tenant-a' else 'b']
    file=pg.product.create_knowledge_file(tenant,actor['id'],'synthetic-77kb.md','text/markdown',len(body),key)
    asyncio.run(KnowledgeProcessingService(pg.product,pg.cfg).process(tenant,file['file_id'],raise_errors=True))
    return pg.product.knowledge_file(tenant,file['file_id'])


def test_77kb_markdown_processing_and_retrieval(pg):
    before=schema(pg)
    record=ingest(pg,MARKDOWN_77KB)
    assert record['status']=='ready' and record['chunk_count']>1
    assert 'RAGBATCH14729' in record['parsed_text'] and record['size_bytes']==78848
    assert storage_provider(pg.cfg).get(record['storage_key'])==MARKDOWN_77KB
    with pg.store.connection() as c:
        row=c.execute('SELECT count(*) AS n,min(vector_dims(embedding)) AS lo,max(vector_dims(embedding)) AS hi FROM knowledge_chunks WHERE file_id=?',(record['id'],)).fetchone()
        assert row['n']==record['chunk_count'] and row['lo']==row['hi']==128
        assert c.execute('SELECT count(*) AS n FROM knowledge_chunks c JOIN knowledge_files f ON f.id=c.file_id WHERE c.tenant_id<>f.tenant_id').fetchone()['n']==0
    service=KnowledgeRetrievalService(pg.product,pg.cfg)
    results=service.search('tenant-a',QUERY)
    assert results and results[0]['file_id']==record['id'] and results[0]['chunk_id']
    assert 'RAGBATCH14729' in results[0]['content']
    assert service.search('tenant-a',QUERY)==results
    assert service.search('tenant-b',QUERY)==[]
    assert service.search('tenant-a','unrelated ocean turbine blade aerodynamics')==[]
    assert schema(pg)==before
    print(json.dumps({'markdown_bytes':len(MARKDOWN_77KB),'sha256':hashlib.sha256(MARKDOWN_77KB).hexdigest(),
                      'chunks':record['chunk_count'],'top_hit':results[0],'schema_unchanged':True},ensure_ascii=False))


def test_schema015_missing_column_is_expected_and_no_ddl_needed(pg):
    with pytest.raises(psycopg.errors.UndefinedColumn) as exc, pg.store.connection() as c:
        c.execute('SELECT embedding_dimension FROM knowledge_chunks')
    assert exc.value.sqlstate=='42703'
    assert pg.product.knowledge_embedding_schema()=={'type':'vector','dimension':128,'dimension_column':False}
    before=schema(pg)
    assert ingest(pg)['status']=='ready'
    assert schema(pg)==before


def test_existing_optional_dimension_column_is_preserved(pg):
    with pg.store.connection() as c:
        c.execute('ALTER TABLE knowledge_chunks ADD COLUMN embedding_dimension INTEGER')
    before=schema(pg)
    record=ingest(pg)
    with pg.store.connection() as c:
        assert c.execute('SELECT embedding_dimension FROM knowledge_chunks WHERE file_id=?',(record['id'],)).fetchone()['embedding_dimension']==128
    assert KnowledgeRetrievalService(pg.product,pg.cfg).search('tenant-a',QUERY)
    assert schema(pg)==before


@pytest.mark.parametrize('fault',['tenant','base','dimension','nan','mid_insert'])
def test_chunk_transaction_and_ownership_preserve_existing_data(pg,fault):
    record=ingest(pg)
    before=pg.product.knowledge_chunks('tenant-a',record['id'])
    chunk=dict(knowledge_base_id=record['knowledge_base_id'],content='replacement',chunk_index=0,embedding=[.1]*128)
    tenant='tenant-a'
    if fault=='tenant': tenant='tenant-b'
    elif fault=='base': chunk['knowledge_base_id']='wrong-base'
    elif fault=='dimension': chunk['embedding']=[.1]*127
    elif fault=='nan': chunk['embedding']=[float('nan')]*128
    chunks=[chunk]
    if fault=='mid_insert': chunks.append(dict(chunk,chunk_index=0))  # real unique constraint rollback after DELETE/INSERT
    with pytest.raises((ValueError,psycopg.errors.UniqueViolation)):
        pg.product.replace_knowledge_chunks(tenant,record['id'],chunks)
    assert pg.product.knowledge_chunks('tenant-a',record['id'])==before


@pytest.mark.parametrize('dimension',[127,129])
def test_dimension_mismatch_rejected_before_embedding(pg,dimension,monkeypatch):
    ingest(pg)
    service=KnowledgeRetrievalService(pg.product,replace(pg.cfg,embedding_dimension=dimension))
    monkeypatch.setattr(service.embedding,'_embed',lambda text:pytest.fail('dimension must be checked first'))
    with pytest.raises(RuntimeError,match='SCHEMA_DIMENSION_MISMATCH'):
        service.search('tenant-a',QUERY)


def test_provider_batch_mismatch_does_not_destroy_ready_chunks(pg):
    record=ingest(pg)
    old=pg.product.knowledge_chunks('tenant-a',record['id'])
    service=KnowledgeProcessingService(pg.product,pg.cfg)
    async def wrong(texts): return []
    service.embedding.embed_documents=wrong
    with pytest.raises(RuntimeError,match='RESPONSE_DIMENSION_MISMATCH'):
        asyncio.run(service.process('tenant-a',record['id'],force=True,raise_errors=True))
    assert pg.product.knowledge_chunks('tenant-a',record['id'])==old


def test_pg_storage_failure_not_hidden_by_legacy_fallback(pg,monkeypatch):
    ingest(pg)
    with pg.store.connection() as c:
        c.execute('ALTER TABLE knowledge_chunks RENAME COLUMN embedding_model TO broken_model')
    monkeypatch.setattr(pg.store,'knowledge_search',lambda *a:pytest.fail('storage errors cannot masquerade as empty search'))
    with pytest.raises(RuntimeError,match='KNOWLEDGE_RETRIEVAL_STORAGE_UNAVAILABLE'):
        KnowledgeRetrievalService(pg.product,pg.cfg).search('tenant-a',QUERY)


def test_api_md_upload_failed_retry_and_tenant_permissions(pg,monkeypatch):
    from app import main
    from app.auth import UserPrincipal
    monkeypatch.setattr(main,'settings',pg.cfg)
    monkeypatch.setattr(main,'store',pg.store)
    monkeypatch.setattr(main,'product_store',pg.product)
    processor=KnowledgeProcessingService(pg.product,pg.cfg)
    monkeypatch.setattr(main,'knowledge_processing',processor)
    monkeypatch.setattr(main,'knowledge_retrieval',KnowledgeRetrievalService(pg.product,pg.cfg))
    def token(key):
        u=pg.actors[key]
        return main.sessions.issue(UserPrincipal(u['id'],u['tenant_id'],u['role'],main.sessions.credential_version(u['password_hash'])))
    async def wait(file_id):
        for _ in range(300):
            item=pg.product.knowledge_file('tenant-a',file_id)
            if item['status'] in {'ready','failed'}: return item
            await asyncio.sleep(.01)
        pytest.fail('local processing did not terminate')
    async def check():
        transport=httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport,base_url='http://isolated.local',cookies={'workbench_session':token('a')}) as client:
            original=processor.product.replace_knowledge_chunks
            def baseline_failure(*a,**kw):
                with pg.store.connection() as c: c.execute('SELECT embedding_dimension FROM knowledge_chunks')
            monkeypatch.setattr(processor.product,'replace_knowledge_chunks',baseline_failure)
            uploaded=await client.post('/api/v1/knowledge/files',files={'file':('prior-failed-77kb.md',MARKDOWN_77KB,'text/markdown')})
            assert uploaded.status_code==202,uploaded.text
            file_id=uploaded.json()['file_id']
            failed=await wait(file_id)
            assert failed['status']=='failed' and 'embedding_dimension' in failed['error_message']
            monkeypatch.setattr(processor.product,'replace_knowledge_chunks',original)
            assert (await client.post(f'/api/v1/knowledge/files/{file_id}/retry')).status_code==202
            ready=await wait(file_id)
            assert ready['status']=='ready' and ready['chunk_count']>1 and not ready['error_message']
            response=await client.post('/api/v1/knowledge/retrieval-test',json={'query':QUERY,'top_k':5})
            assert response.status_code==200 and response.json()['results'][0]['file_id']==file_id
            detail=await client.get(f'/api/v1/knowledge/files/{file_id}')
            assert detail.status_code==200 and detail.json()['chunks']
            count=ready['chunk_count']
            # Re-processing does not duplicate file/chunks, and preserves source.
            assert (await client.post(f'/api/v1/knowledge/files/{file_id}/retry')).status_code==202
            assert (await wait(file_id))['chunk_count']==count
            assert storage_provider(pg.cfg).get(ready['storage_key'])==MARKDOWN_77KB
            client.cookies.set('workbench_session',token('b'))
            assert (await client.get(f'/api/v1/knowledge/files/{file_id}')).status_code==404
            assert (await client.post(f'/api/v1/knowledge/files/{file_id}/retry')).status_code==404
            assert (await client.delete(f'/api/v1/knowledge/files/{file_id}')).status_code==404
            response=await client.post('/api/v1/knowledge/retrieval-test',json={'query':QUERY})
            assert response.status_code==200 and response.json()['results']==[]
            client.cookies.set('workbench_session',token('member'))
            assert (await client.get(f'/api/v1/knowledge/files/{file_id}')).status_code==403
    asyncio.run(check())


def test_untouched_other_tenant_and_local_not_semantic_claim(pg):
    record=ingest(pg,FACT.encode(),tenant='tenant-b')
    with pg.store.connection() as c:
        before=[dict(r) for r in c.execute('SELECT * FROM knowledge_chunks WHERE file_id=?',(record['id'],))]
    ingest(pg,MARKDOWN_77KB)
    with pg.store.connection() as c:
        assert [dict(r) for r in c.execute('SELECT * FROM knowledge_chunks WHERE file_id=?',(record['id'],))]==before
    assert runtime_diagnostic(pg.product,pg.cfg)['status']=='degraded'
    assert KnowledgeRetrievalService(pg.product,pg.cfg).search('tenant-b',QUERY)[0]['file_id']==record['id']


def test_historical_legacy_text_remains_unchanged(pg):
    pg.product.add_knowledge_text('tenant-b','Retained legacy reference','This retained legacy text must not be rebuilt.')
    with pg.store.connection() as c:
        before=[dict(r) for r in c.execute('SELECT * FROM knowledge_documents ORDER BY id')]
    ingest(pg,MARKDOWN_77KB)
    with pg.store.connection() as c:
        assert [dict(r) for r in c.execute('SELECT * FROM knowledge_documents ORDER BY id')]==before
