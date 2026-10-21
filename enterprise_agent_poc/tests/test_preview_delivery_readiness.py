"""FIXTURE_ONLY: bounded visitor capacity and exact request boundary; no Provider."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import sqlite3

import pytest

from scripts.customer_preview import admit_preview_tenant, MAX_PREVIEW_TENANTS
from app.auth import SessionIssuer, UserPrincipal, AuthenticationError
from app.preview_budget import preview_text_wire, bounded_text_output
from app.preview_request_context import compact_knowledge_history
from app.test_text_gate import MAX_REQUEST_BYTES, TestTextGate, TestTextGateDenied


def create_db(path,count):
    with sqlite3.connect(path) as c:
        c.execute('create table tenants(id text primary key,name text,poc_api_key text)')
        c.executemany('insert into tenants values(?,?,?)', [(f'preview-fixture-{i}','FIXTURE','FIXTURE') for i in range(count)])


def test_existing_thirty_retained_and_twenty_bounded_admissions(tmp_path):
    path=tmp_path/'fixture.sqlite';create_db(path,30)
    with sqlite3.connect(path) as c:before=c.execute('select * from tenants order by id').fetchall()
    for i in range(30,50):
        with sqlite3.connect(path) as c:admit_preview_tenant(c,f'preview-fixture-{i}','FIXTURE')
    with sqlite3.connect(path) as c:
        with pytest.raises(PermissionError):admit_preview_tenant(c,'preview-over-limit','FIXTURE')
    with sqlite3.connect(path) as c:
        assert c.execute('select count(*) from tenants').fetchone()[0]==MAX_PREVIEW_TENANTS==50
        assert all(c.execute('select * from tenants where id=?',(r[0],)).fetchone()==r for r in before)


def test_concurrent_admission_cannot_overrun_last_slot(tmp_path):
    path=tmp_path/'fixture.sqlite';create_db(path,49)
    def submit(i):
        try:
            with sqlite3.connect(path,timeout=10) as c:admit_preview_tenant(c,f'preview-concurrent-{i}','FIXTURE')
            return True
        except PermissionError:return False
    with ThreadPoolExecutor(max_workers=6) as workers:assert sum(workers.map(submit,range(6)))==1
    with sqlite3.connect(path) as c:assert c.execute('select count(*) from tenants').fetchone()[0]==50


def test_non_preview_admission_rejected_and_partial_creation_rolled_back(tmp_path):
    path=tmp_path/'fixture.sqlite';create_db(path,30)
    with sqlite3.connect(path) as c:
        with pytest.raises(PermissionError):admit_preview_tenant(c,'production','FIXTURE')
    with pytest.raises(RuntimeError):
        with sqlite3.connect(path) as c:
            admit_preview_tenant(c,'preview-rollback','FIXTURE');raise RuntimeError('FIXTURE failed config creation')
    with sqlite3.connect(path) as c:assert c.execute('select count(*) from tenants').fetchone()[0]==30


def test_session_expiry_requires_real_recovery_not_claimed_silent_refresh(monkeypatch):
    issuer=SessionIssuer('FIXTURE_ONLY_NOT_LIVE_SECRET')
    monkeypatch.setattr('app.auth.time.time',lambda:1000)
    token=issuer.issue(UserPrincipal('fixture-user','preview-fixture','enterprise_admin'),72*3600)
    monkeypatch.setattr('app.auth.time.time',lambda:1001)
    assert issuer.verify(token).tenant_id=='preview-fixture'
    monkeypatch.setattr('app.auth.time.time',lambda:1000+72*3600)
    with pytest.raises(AuthenticationError):issuer.verify(token)


@pytest.mark.parametrize('character',['x','学'])
def test_exact_encoded_boundary_preserves_history_and_rejects_overflow(character):
    body={'model':'deepseek-flash','max_output_tokens':16384,'reasoning':{'effort':'low'},'instructions':'FIXTURE_ONLY',
          'input':[{'role':'user','content':'first confirmed facts'},{'role':'assistant','content':'last full draft'},
                   {'role':'user','content':''}]}
    unit=len(json.dumps(character,ensure_ascii=True))-2
    overhead=len(json.dumps(body,ensure_ascii=True).encode())
    body['input'][-1]['content']=character*((MAX_REQUEST_BYTES-overhead)//unit)
    before=copy.deepcopy(body)
    bound=TestTextGate.request_bound(body)
    assert bound['input_tokens_upper']>0
    assert body==before
    preview_text_wire(body)
    out,cost=bounded_text_output(body,{'text_input_cny_per_million':'2','text_output_cny_per_million':'8'},'preview-wechat-agent')
    assert out['input']==body['input'] and out['max_output_tokens']==16384 and cost<=5
    body['input'][-1]['content']+=character
    with pytest.raises(TestTextGateDenied):TestTextGate.request_bound(body)


def test_long_multiple_sources_compact_only_identical_evidence_and_preserve_user_history():
    sources=[{'file_id':'fixture-file','chunk_id':str(i),'filename':'FIXTURE.md','content':('原始事实'+str(i))*1200} for i in range(3)]
    items=[{'role':'user','content':'FIXTURE original request'},{'role':'assistant','content':'FIXTURE complete previous draft'}]
    for i in range(8):
        items.extend([{'type':'function_call','name':'mcp__platform__knowledge_search','call_id':str(i),'arguments':'{}'},
                      {'type':'function_call_output','call_id':str(i),'output':json.dumps({'result':sources},ensure_ascii=False)}])
    body={'input':items};before=copy.deepcopy(body)
    out,stats=compact_knowledge_history(body)
    assert body==before and out['input'][:4]==body['input'][:4]
    assert stats['repeated_source_bodies']==21 and stats['redundant_search_tail']==7
    assert [i.get('call_id') for i in out['input']]==[i.get('call_id') for i in items]
    assert len(json.dumps(out,ensure_ascii=True))<262144
    assert all(source['content'] in out['input'][3]['output'] for source in sources)
