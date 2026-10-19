"""Isolated fixtures only. Never evidence of a real image Provider success."""
import asyncio
import io
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest

from app.preview_image_execution import PreviewImageExecution, AUTHORIZATION_ID, MODE
from app.preview_budget import PreviewBudget, PreviewBudgetExceeded
from app.product_store import ProductStore
from app.security import RuntimeTokenIssuer
from app.settings import Settings
from app.storage import storage_provider
from app.store import POCStore


@pytest.fixture
def image_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv('ENTERPRISE_POC_OBJECT_STORAGE_PROVIDER', 'local')
    monkeypatch.setenv('ENTERPRISE_POC_OBJECT_STORAGE_DIR', str(tmp_path/'objects'))
    settings = Settings.from_env()
    store = POCStore(tmp_path/'fixture.sqlite');store.initialize()
    with store.connection() as conn:
        for tenant in ['preview-fixture-a','preview-fixture-b']:
            conn.execute('insert into tenants values(?,?,?)',(tenant,'Isolated fixture',tenant+'-fixture'))
            conn.execute('insert into enterprise_configs values(?,?)',(tenant,'{}'))
    products = ProductStore(store);products.initialize()
    actors = []
    for tenant, email in [('preview-fixture-a','a@fixture.invalid'),('preview-fixture-b','b@fixture.invalid'),('preview-fixture-a','c@fixture.invalid')]:
        products.create_user(tenant,email,'fixture-not-a-login-secret','Fixture','enterprise_admin')
        user = products.user_by_email(email)
        actors.append(SimpleNamespace(tenant_id=tenant,user_id=user['id']))
    budget = PreviewBudget(tmp_path)
    main = SimpleNamespace(settings=settings,store=store,product_store=products,token_issuer=RuntimeTokenIssuer('isolated-fixture-only-token-signing'))
    grant = dict(authorization_id=AUTHORIZATION_ID,approved=True,execution_mode=MODE,model=settings.image_model_id,max_provider_calls=2,request_max_cny='1')
    executor = PreviewImageExecution(main,budget,dict(image_pricing_verified=True,image_request_max_cny='1'),grant)
    calls = []
    async def fixture_image(task, plan):
        calls.append(task['id']);budget.reserve('image',Decimal('1'))
        stream=io.BytesIO();Image.new('RGB',(1024,1024),(20,90,160)).save(stream,format='PNG')
        key=f"generated/{task['tenant_id']}/{uuid4().hex}.png"
        storage_provider(settings).put(key,stream.getvalue(),'image/png')
        return {'provider':'image-gateway','model':settings.image_model_id,
                'results':[{'storage_key':key,'width':1024,'height':1024,'requested_size':plan['size'],'purpose':'Fixture image','dimension_match':True}],
                'delivery':{'status':'completed','requested':1,'generated':1,'failed':0}}
    executor.call_image=fixture_image
    return executor,actors,calls


def payload(**extra):
    return {'message':'生成1张1024x1024星空图片，不要Logo。','preview_image_confirmed':True,
            'preview_image_request_id':str(uuid4()),**extra}


def test_explicit_grant_and_click_required(image_fixture):
    executor,actors,calls=image_fixture
    with pytest.raises(PermissionError):executor.submit(actors[0],payload(preview_image_confirmed=False))
    executor.authorization={}
    with pytest.raises(PermissionError):executor.submit(actors[0],payload())
    assert calls==[] and executor.budget.summary()['image']['requests']==0


@pytest.mark.parametrize('extra',[{'tenant_id':'preview-fixture-b'},{'storage_key':'/opt/private'},
                                  {'message':'生成2张图片'},{'attachments':[{'id':'foreign'}]}])
def test_untrusted_identity_paths_batch_and_attachments_rejected(image_fixture,extra):
    executor,actors,calls=image_fixture
    with pytest.raises(ValueError):executor.submit(actors[0],payload(**extra))
    assert calls==[] and executor.budget.summary()['image']['requests']==0


def test_task_run_generation_history_and_no_duplicate_dispatch(image_fixture):
    executor,actors,calls=image_fixture;actor=actors[0];request=payload()
    task=executor.submit(actor,request)
    assert executor.submit(actor,request)['id']==task['id']
    asyncio.run(executor.execute(task));asyncio.run(executor.execute(task))
    done=executor.main.product_store.task(task['id'],actor.tenant_id,actor.user_id)
    assert done['status']=='completed' and len(done['generations'])==1
    assert done['generations'][0]['width']==1024 and len(calls)==1
    trace=executor.main.store.run_trace(done['run_id'],actor.tenant_id)
    assert trace['codex_thread_id'] is None
    assert trace['payload']['codex_runtime_executed'] is False
    assert trace['payload']['execution_mode']==MODE
    reopened=executor.main.product_store.conversation_detail(actor.tenant_id,actor.user_id,done['conversation_id'])
    assert len(reopened['generations'])==1
    assert executor.submit(actor,request)['id']==task['id']
    assert executor.budget.summary()['image']['requests']==1


@pytest.mark.parametrize('other_index',[1,2])
def test_task_history_and_download_are_private(image_fixture,other_index):
    executor,actors,calls=image_fixture;actor=actors[0];other=actors[other_index]
    task=executor.submit(actor,payload());asyncio.run(executor.execute(task))
    done=executor.main.product_store.task(task['id'],actor.tenant_id,actor.user_id)
    assert executor.main.product_store.task(task['id'],other.tenant_id,other.user_id) is None
    assert executor.main.product_store.conversation_detail(other.tenant_id,other.user_id,done['conversation_id']) is None
    key=executor.main.product_store.generations(actor.tenant_id,actor.user_id)[0]['storage_key']
    assert executor.main.product_store.readable_generation(other.tenant_id,other.user_id,key) is None
    with pytest.raises(LookupError):executor.submit(other,payload(conversation_id=done['conversation_id']))


def test_idempotency_key_cannot_change_prompt(image_fixture):
    executor,actors,calls=image_fixture;request=payload();executor.submit(actors[0],request)
    with pytest.raises(ValueError):executor.submit(actors[0],{**request,'message':'不同需求'})


def test_disabled_agent_fails_before_task_or_payment(image_fixture):
    executor,actors,calls=image_fixture;actor=actors[0]
    with executor.main.store.connection() as conn:
        conn.execute("update tenant_agent_instances set status='disabled' where tenant_id=? and agent_id='image-agent'",(actor.tenant_id,))
    with pytest.raises(PermissionError):executor.submit(actor,payload())
    assert calls==[]


def test_exhaustion_does_not_create_new_task(image_fixture):
    executor,actors,calls=image_fixture
    executor.budget.reserve('image',Decimal('1'));executor.budget.reserve('image',Decimal('1'))
    with pytest.raises(PreviewBudgetExceeded):executor.submit(actors[0],payload())
    with executor.main.store.connection() as conn:
        assert conn.execute('select count(*) from tasks').fetchone()[0]==0


def test_failed_unknown_outcome_never_retries(image_fixture):
    executor,actors,calls=image_fixture
    async def unknown(task,plan):
        calls.append(task['id']);executor.budget.reserve('image',Decimal('1'));raise TimeoutError()
    executor.call_image=unknown
    task=executor.submit(actors[0],payload())
    asyncio.run(executor.execute(task));asyncio.run(executor.execute(task))
    assert len(calls)==1 and executor.budget.summary()['image']['requests']==1
    assert executor.main.product_store.task_for_worker(task['id'])['status']=='failed'


def test_cancelled_task_does_not_dispatch(image_fixture):
    executor,actors,calls=image_fixture;actor=actors[0]
    task=executor.submit(actor,payload());executor.main.product_store.cancel_task(task['id'],actor.tenant_id,actor.user_id)
    asyncio.run(executor.execute(task))
    assert calls==[]
