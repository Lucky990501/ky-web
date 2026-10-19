"""FIXTURE_ONLY real ASGI routes/SQLite; execution held, no Provider."""
import asyncio
import httpx


def test_four_agent_posts_are_independent_bounded_and_tenant_owned(tmp_path,monkeypatch):
    from scripts.customer_preview import prepare,create_preview
    root=tmp_path/'fixture-six'/'customer-ai-preview-data';root.mkdir(parents=True)
    prepare(root,18974,18975)
    from app.product_service import TaskService
    async def hold(self,task):pass
    monkeypatch.setattr(TaskService,'execute',hold)
    app=create_preview(root,18974,18975,local_http=True,credentials={'DEEPSEEK_API_KEY':'FIXTURE_ONLY'},pricing={'text_pricing_verified':True})
    from app import main
    assert main.settings.database_url=='sqlite:///'+str(root.resolve()/'preview.sqlite')
    async def check():
        transport=httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,base_url='http://fixture.local') as a,httpx.AsyncClient(transport=transport,base_url='http://fixture.local') as b:
            owner=(await a.get('/preview/api/v1/me')).json();other=(await b.get('/preview/api/v1/me')).json()
            assert owner['tenant_id']!=other['tenant_id']
            agents=['copywriting-agent','campaign-agent','preview-wechat-agent','copywriting-agent']
            replies=await asyncio.gather(*(a.post('/preview/api/v1/agents/'+agent+'/runs',json={'message':'FIXTURE_ONLY independent task'}) for agent in agents))
            assert [r.status_code for r in replies]==[202]*4,[r.text for r in replies]
            tasks=[r.json() for r in replies];assert len({t['id'] for t in tasks})==4
            fifth=await a.post('/preview/api/v1/agents/campaign-agent/runs',json={'message':'FIXTURE_ONLY over bound'})
            assert fifth.status_code==429 and fifth.json()['error_code']=='PREVIEW_TASK_CAPACITY'
            for task in tasks:
                assert (await a.get('/preview/api/v1/tasks/'+task['id'])).status_code==200
                assert (await b.get('/preview/api/v1/tasks/'+task['id'])).status_code in {403,404}
            with main.store.connection() as c:
                assert c.execute('select count(*) from tasks').fetchone()[0]==4
            assert main.manager._test_gate is not None
    asyncio.run(check())
