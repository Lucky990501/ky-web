"""Task identity must reach both Runtime create and resume before admission."""
import asyncio
import pytest
from test_run_trace import FakeRuntime,product_task_fixture

def test_domain_fixture_accepts_the_same_optional_task_identity():
    from scripts.stage2_domain_fixture import _FixtureRuntime
    from types import SimpleNamespace
    session=asyncio.run(_FixtureRuntime().create_session(SimpleNamespace(id='fixture-profile'),'fixture',task_id='fixture-task'))
    assert session.profile_id=='fixture-profile'


@pytest.mark.parametrize('stage2',[False,True])
def test_create_and_resume_keep_real_task_identity(tmp_path,monkeypatch,stage2):
    class Runtime(FakeRuntime):
        def __init__(self):self.bound=[]
        async def create_session(self,*a,**kw):
            self.bound.append(('create',kw.get('task_id')))
            return await super().create_session(*a,**kw)
        async def resume_session(self,*a,**kw):
            self.bound.append(('resume',kw.get('task_id')))
            return await super().resume_session(*a,**kw)
    if stage2:monkeypatch.setenv('STAGE2_TWO_PROBE_AUTHORITY','EXPLICIT_FIXTURE_NO_SIGNING_OR_SEND')
    else:monkeypatch.delenv('STAGE2_TWO_PROBE_AUTHORITY',raising=False)
    runtime=Runtime()
    _,product,task,_,service=product_task_fixture(tmp_path,monkeypatch,'copywriting-agent',runtime)
    assert service._agents._settings.test_text_gate_enabled is False
    asyncio.run(service.execute(task))
    first=product.task_for_worker(task['id']);assert first['status']=='completed'
    second=product.create_task('tenant-a',task['user_id'],'copywriting-agent','继续合成测试',first['conversation_id'])
    asyncio.run(service.execute(second))
    assert product.task_for_worker(second['id'])['status']=='completed'
    assert runtime.bound==[('create',task['id'] if stage2 else None),('resume',second['id'] if stage2 else None)]
