"""Fixture-only Qwen scope, price and ordered Word mapping. No real calls."""
import asyncio
from decimal import Decimal
import io
import json
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from PIL import Image
import pytest

from test_preview_image_execution import image_fixture
from test_preview_illustrations import setup, completed_task, GRANT
from app.preview_illustration_provider import IllustrationError, validated_image
from app.preview_credit_target import EXPERIENCE_REFRESH_OPERATION,top_up_preview_credits
from test_preview_credit_target import credits


def experience(service,actors):
    service.authorization={**GRANT,'request_max_cny':'5.00','estimated_request_cny':'0.20',
        'max_requests':200,'max_total_cny':'200.00','max_images_per_task':10,
        'agent_max_images_per_task':{'campaign-agent':10,'preview-wechat-agent':4},
        'agent_max_plan_cny':{'campaign-agent':'5.00','preview-wechat-agent':'5.00'},
        'allowed_tenant_ids':sorted({a.tenant_id for a in actors}),
        'task_scoped_tenant_ids':[],
        # Historical authorization retained, but no longer applied to tenant-level grants.
        'tenant_task_restrictions':[{'tenant_id':actors[0].tenant_id,'task_id':'OLD_ARTICLE','max_images':3,'max_reserved_cny':'0.60'}]}


def activity(service,actor,count):
    data=json.loads((Path(__file__).parent/'fixtures/activity_plan_d1.json').read_text(encoding='utf-8'))
    base=data['activity_items'][0]
    data['activity_items']=[{**base,'name':f'FIXTURE独立环节{i+1}','image_requirement':f'FIXTURE独立示意需求{i+1}'} for i in range(count)]
    return completed_task(service,actor,content=data)


@pytest.mark.parametrize('count',[4,7,10])
def test_all_items_admitted_and_exact_fee_reserved_once(setup,count):
    service,actors,old,budget=setup;experience(service,actors);actor=actors[0]
    task=activity(service,actor,count);plan=service.plan(actor,task['id'])
    assert plan['can_generate'] and plan['count']==count
    assert Decimal(plan['maximum_cost_cny'])==Decimal('.20')*count
    assert [i['position'] for i in plan['items']]==list(range(count))
    assert service.confirm(actor,plan['id'],plan['digest'],True)['status']=='queued'
    service.confirm(actor,plan['id'],plan['digest'],True)
    with service.db() as c:
        reservations=c.execute('select item_index,ceiling from illustration_requests order by item_index').fetchall()
    assert len(reservations)==count and [r[0] for r in reservations]==list(range(count))
    assert sum((Decimal(r[1]) for r in reservations),Decimal(0))==Decimal('.20')*count
    assert old==[] and budget.summary()['image']['requests']==0


def test_eleven_rejected_in_get_and_confirm_without_reservation(setup):
    service,actors,old,budget=setup;experience(service,actors)
    task=activity(service,actors[0],11);plan=service.plan(actors[0],task['id'])
    assert plan['count']==11 and not plan['can_generate'] and plan['blocker_code']=='ILLUSTRATION_TASK_LIMIT'
    assert '10' in plan['user_message']
    with pytest.raises(IllustrationError,match='10') as error:service.confirm(actors[0],plan['id'],plan['digest'],True)
    assert error.value.code=='ILLUSTRATION_TASK_LIMIT' and service.capability()['reserved_requests']==0


@pytest.mark.parametrize('actor_index,kind',[(0,'activity'),(0,'wechat'),(1,'activity'),(1,'wechat'),(2,'wechat')])
def test_current_tenants_authorized_not_old_article_scoped(setup,actor_index,kind):
    service,actors,old,budget=setup;experience(service,actors);actor=actors[actor_index]
    task=completed_task(service,actor,kind);plan=service.plan(actor,task['id'])
    assert plan['can_generate'] and service.task_restriction(actor,task['id']) is None
    assert service.confirm(actor,plan['id'],plan['digest'],True)['status']=='queued'
    assert service.plan_limits('wechat')==(4,Decimal('5.00'))
    assert old==[] and budget.summary()['image']['requests']==0
    with pytest.raises(LookupError):service.get(actors[(actor_index+1)%3],plan['id'])


def test_global_ceilings_old_reservations_and_unknown_tenant(setup):
    from types import SimpleNamespace
    service,actors,old,budget=setup;experience(service,actors)
    with service.db() as c:
        for i in range(15):c.execute('insert into illustration_requests(id,plan_id,item_index,ceiling,status,created) values(?,?,?,?,?,?)',(str(i),'OLD_PLAN',i,'.20','failed',i))
        before=[tuple(r) for r in c.execute('select * from illustration_requests order by id')]
    cap=service.capability(actors[0])
    assert cap['remaining_requests']==185 and Decimal(cap['reserved_ceiling_cny'])==3
    assert cap['request_max_cny']=='5.00' and cap['estimated_request_cny']=='0.20'
    assert not service.capability(SimpleNamespace(tenant_id='preview-not-authorized'))['enabled']
    with service.db() as c:assert [tuple(r) for r in c.execute('select * from illustration_requests order by id')]==before
    task=activity(service,actors[0],7);plan=service.plan(actors[0],task['id'])
    service.authorization['max_requests']=21
    assert not service.get(actors[0],plan['id'])['can_generate']
    with pytest.raises(IllustrationError):service.confirm(actors[0],plan['id'],plan['digest'],True)
    service.authorization['max_requests']=200;service.authorization['max_total_cny']='4.00'
    assert not service.get(actors[0],plan['id'])['can_generate']
    with pytest.raises(IllustrationError):service.confirm(actors[0],plan['id'],plan['digest'],True)


def test_request_hard_ceiling_and_plan_cost_still_enforced(setup):
    service,actors,_,_=setup;experience(service,actors)
    service.authorization['estimated_request_cny']='5.01';assert service.limits() is None
    service.authorization['estimated_request_cny']='0.60'
    task=activity(service,actors[0],10);plan=service.plan(actors[0],task['id'])
    assert not plan['can_generate'] and plan['blocker_code']=='ILLUSTRATION_PLAN_COST_LIMIT'
    with pytest.raises(IllustrationError) as error:service.confirm(actors[0],plan['id'],plan['digest'],True)
    assert error.value.code=='ILLUSTRATION_PLAN_COST_LIMIT' and service.capability()['reserved_requests']==0


def test_seven_distinct_images_embedded_in_order_history_never_regenerates(setup,monkeypatch):
    service,actors,old,budget=setup;experience(service,actors);actor=actors[0]
    task=activity(service,actor,7);plan=service.plan(actor,task['id']);calls=[]
    async def generate(prompt,record_http):
        index=len(calls);calls.append(prompt);record_http(200,'FIXTURE-'+str(index))
        image=Image.new('RGB',(120,80),(index*30,40,100));out=io.BytesIO();image.save(out,format='PNG')
        return out.getvalue(),validated_image(out.getvalue())
    monkeypatch.setattr(service.provider,'generate',generate)
    service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']))
    done=service.current(actor,task['id']);assert done['status']=='completed'
    assert [a['position'] for a in done['assets']]==list(range(7)) and len({a['sha256'] for a in done['assets']})==7
    from app.document_generator import ActivityPlanDocumentService
    content=ActivityPlanDocumentService(service.storage).get(actor.tenant_id,actor.user_id,done['document']['document_id']).content
    with ZipFile(io.BytesIO(content)) as z:
        media=[n for n in z.namelist() if n.startswith('word/media/')]
        assert len(media)==7
        tree=ET.fromstring(z.read('word/document.xml'));ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
        rows=tree.findall('.//w:tbl/w:tr',ns)
        for i in range(7):
            matching=[r for r in rows if f'FIXTURE独立环节{i+1}' in ''.join(r.itertext())]
            assert len(matching)==1 and len(matching[0].findall('.//a:blip',ns))==1
    service.get(actor,plan['id']);service.confirm(actor,plan['id'],plan['digest'],True)
    assert len(calls)==7 and old==[] and budget.summary()['image']['requests']==0


def test_authorized_refresh_only_difference_and_once(credits):
    store,_=credits
    first=top_up_preview_credits(store,operation=EXPERIENCE_REFRESH_OPERATION)
    assert len(first)==5 and sum(r['added']>0 for r in first)==3
    assert all(r['balance']>=100000 for r in first)
    assert all(r['added']==0 and r['already_applied'] for r in top_up_preview_credits(store,operation=EXPERIENCE_REFRESH_OPERATION))
