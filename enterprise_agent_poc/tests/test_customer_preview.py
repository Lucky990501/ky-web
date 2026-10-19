"""Only isolated counter/compatibility tests; never a real Provider PASS."""
from decimal import Decimal
import pytest
from app.preview_budget import PreviewBudget, PreviewBudgetExceeded, plaintext_history


@pytest.mark.parametrize(('path','method','blocked'),[
    ('/api/v1/agents/image-agent/runs','POST',True),
    ('/api/v1/agents/image-agent/runs','GET',False),
    ('/api/v1/agents/copywriting-agent/runs','POST',False),
    ('/api/v1/agents/campaign-agent/runs','POST',False),
    ('/api/v1/agents/preview-wechat-agent/runs','POST',False),
    ('/api/v1/conversations','GET',False),
])
def test_image_admission_stop_does_not_change_other_business_routes(path,method,blocked):
    from scripts.customer_preview import image_submission_blocked
    assert image_submission_blocked(path,method) is blocked


def test_plain_tool_cycle_preserves_real_call_and_result():
    call={"type":"function_call","call_id":"real-call","name":"knowledge_search","arguments":"{}"}
    result={"type":"function_call_output","call_id":"real-call","output":"真实工具返回"}
    normalized,dropped=plaintext_history({"input":[{"role":"user","content":"问题"},{"type":"reasoning","encrypted_content":"Fixture-only-opaque"},call,result],"include":["reasoning.encrypted_content"]})
    assert dropped==1 and normalized["input"][-2:]==[call,result]
    assert "encrypted_content" not in str(normalized)


@pytest.mark.parametrize("item",[{"type":"item_reference","id":"unknown"},{"type":"computer_call"},{"type":"message","encrypted_content":"Fixture"}])
def test_unknown_or_opaque_inputs_fail_closed(item):
    with pytest.raises(ValueError): plaintext_history({"input":[item]})


def test_budget_count_persists_across_restart(tmp_path):
    meter=PreviewBudget(tmp_path)
    for _ in range(46): meter.reserve("text",Decimal('0.1'))
    with pytest.raises(PreviewBudgetExceeded): PreviewBudget(tmp_path).reserve("text",Decimal('0.1'))
    assert PreviewBudget(tmp_path).summary()['text']['requests']==46


def test_embedding_batches_use_wire_bytes_without_loss():
    import httpx
    from app.preview_budget import bounded_embedding_batches
    texts=['中文段落'+('招生资料' * 180)+str(i) for i in range(124)]
    batches=bounded_embedding_batches(texts,'text-embedding-3-small')
    assert [x for batch in batches for x in batch]==texts
    for batch in batches:
        wire=httpx.Request('POST','https://llm-api.net/v1/embeddings',json={'model':'text-embedding-3-small','input':batch,'encoding_format':'float'}).content
        assert len(wire)<=8192 and len(batch)<=32


def test_embedding_oversized_single_chunk_is_rejected_without_payment():
    from app.preview_budget import bounded_embedding_batches
    with pytest.raises(PreviewBudgetExceeded) as raised: bounded_embedding_batches(['汉'*9000],'text-embedding-3-small')
    assert raised.value.code=='KNOWLEDGE_EMBEDDING_BATCH_TOO_LARGE'


def test_full_file_preflight_preserves_budget_and_rejects_partial_spend(tmp_path):
    meter=PreviewBudget(tmp_path)
    for _ in range(15):meter.reserve('embedding',Decimal('.05'))
    with pytest.raises(PreviewBudgetExceeded) as raised:meter.check_capacity('embedding',13,Decimal('.05'))
    assert raised.value.code=='PREVIEW_EMBEDDING_BUDGET_EXHAUSTED'
    assert meter.summary()['embedding']['requests']==15


def test_safe_error_categories_never_include_internal_text():
    from app.preview_budget import PREVIEW_ERRORS,preview_task_failure
    assert preview_task_failure({}, {}, {'reason':'PreviewBudgetExceeded'})=='PREVIEW_MODEL_BUDGET_EXHAUSTED'
    assert preview_task_failure({}, {'result_persistence_error_stage':'database'}, None)=='CONTENT_SAVE_FAILED'
    assert preview_task_failure({}, {'error':'secret=/opt/private'}, None)=='MODEL_TOOL_UNAVAILABLE'
    assert all('/opt/' not in value and 'secret=' not in value for value in PREVIEW_ERRORS.values())


def test_task_denial_uses_historical_observation_not_current_quota(tmp_path):
    from datetime import datetime,timezone,timedelta
    now=datetime.now(timezone.utc);meter=PreviewBudget(tmp_path)
    meter.observe({'event':'text_denied','task_id':'other-task','reason':'PreviewBudgetExceeded'})
    task={'id':'own-task','started_at':(now-timedelta(seconds=1)).isoformat(),'completed_at':(now+timedelta(seconds=1)).isoformat()}
    assert meter.task_denial(task) is None
    meter.observe({'event':'text_denied','task_id':'own-task','reason':'PreviewBudgetExceeded','code':'PREVIEW_MODEL_BUDGET_EXHAUSTED'})
    assert meter.task_denial(task)['code']=='PREVIEW_MODEL_BUDGET_EXHAUSTED'


def test_money_limit_reserves_unknown_outcome_and_never_refunds(tmp_path):
    meter=PreviewBudget(tmp_path);meter.reserve('image',Decimal('20'))
    with pytest.raises(PreviewBudgetExceeded): meter.reserve('image',Decimal('1'))
    assert meter.summary()['image']['reserved_ceiling_cny']=='20'


@pytest.mark.parametrize('cost',['0','-1','NaN','Infinity'])
def test_unbounded_or_free_assumptions_are_not_permission(tmp_path,cost):
    with pytest.raises(PreviewBudgetExceeded): PreviewBudget(tmp_path).reserve('text',Decimal(cost))


def test_preview_resume_uses_authorized_visible_history_not_sdk_rollout(monkeypatch):
    import asyncio
    from app.runtime.codex_provider import CodexRuntimeProvider
    provider=object.__new__(CodexRuntimeProvider);calls=[]
    async def create(profile,instructions,**kwargs):
        calls.append((profile,instructions,kwargs));return 'fresh-session'
    provider.create_session=create
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    result=asyncio.run(provider.resume_session('profile','old-thread',developer_instructions='Original Skill',recovery_context='user: 原需求\nassistant: 原文案',task_id='task'))
    assert result=='fresh-session' and len(calls)==1
    assert 'Original Skill' in calls[0][1] and '原文案' in calls[0][1]
    assert calls[0][2]=={'task_id':'task'}
