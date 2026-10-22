"""Preview-only brand facts plus a small, visible conversation confirmation step."""
import os,re
from pathlib import Path

AGENTS=frozenset({'copywriting-agent','preview-wechat-agent','campaign-agent'})
TENANT='preview-74397425ff1141bb881db1d97451735d'
PATH=Path(__file__).resolve().parents[1]/'preview_prompts/yiming-brand-topics.md'
NAMES=('易明长青总部','易明教育赋能中心（定制化伴学）','长明会')
PREFIX='开始创作前，请先确认这次要写哪个主题：'
QUESTION=PREFIX+'\n1. 易明长青总部——整体品牌与陪伴式教育生态\n2. 易明教育赋能中心（定制化伴学）——孩子的学习成长\n3. 长明会——成长型个体的能力、影响力与事业人生\n请回复名称或序号；如需综合介绍，可明确选择多项。我会保留你刚才的需求，确认后继续。'

def policy():
    text=PATH.read_text(encoding='utf-8').strip()
    if not text.startswith('# 易明长青品牌主题与创作前确认'):raise ValueError('Preview brand policy missing')
    return text

def install():
    if os.environ.get('CUSTOMER_PREVIEW_MODE')!='true':raise RuntimeError('Preview only')
    policy()  # Validate at startup; facts are scoped per session/task below.

def session_instructions(main,task_context,instructions):
    tid=task_context.get();task=main.product_store.task_for_worker(tid) if tid else None
    if not task or task['agent_id'] not in AGENTS or task['tenant_id']!=TENANT:return instructions
    return instructions if policy() in instructions else instructions+'\n\n'+policy()

def choice(text):
    text=text.strip()
    if text in {'全部','都要','三个都要','综合品牌','综合介绍','综合'}:return (1,2,3)
    if re.fullmatch(r'(?:选|选择|第)?[123一二三](?:个|项)?(?:\s*[、,/+和及]\s*[123一二三])*[。！!]?\s*',text):
        return tuple(sorted({int(x.translate(str.maketrans('一二三','123'))) for x in re.findall('[123一二三]',text)}))
    if re.search(r'(?:不是|不选|不要|别写).{0,6}(?:[123一二三]|易明|总部|长明会|伴学)',text):return ()
    selected=[]
    for i,pattern in enumerate(('易明长青|总部','易明教育(?:赋能中心)?|定制化伴学','长明会'),1):
        if re.search(pattern,text):selected.append(i)
    return tuple(selected)

def decision(messages,current):
    """Read only visible, already-owned messages, never model drafts as facts."""
    pending=False;confirmed=();previous_user='';candidate=()
    for message in messages:
        text=message['content']
        if message['role']=='assistant' and PREFIX in text:
            pending=True;confirmed=();candidate=choice(previous_user)
        elif message['role']=='user':
            if pending:
                selected=choice(text)
                if not selected and candidate and text.strip() in {'对','是','是的','确认','确定','没错','可以','就这个'}:selected=candidate
                if selected:confirmed=selected;pending=False
            previous_user=text
    if confirmed:
        # A deliberate new subject needs its own confirmation, not a silent
        # name change. General editing instructions never reset the subject.
        if re.search(r'换成|改为|这次写|接下来写|另写|另外写',current):
            newer=choice(current)
            if newer and newer!=confirmed:return (),QUESTION
        return confirmed,None
    selected=choice(current)
    if pending:
        if not selected and candidate and current.strip() in {'对','是','是的','确认','确定','没错','可以','就这个'}:selected=candidate
        if selected:return selected,None
    suffix=('\n你已提到“'+'、'.join(NAMES[x-1] for x in selected)+'”，请确认是否按此主题创作。') if selected else ''
    return (),QUESTION+suffix

def for_task(main,task):
    if task['agent_id'] not in AGENTS:return (),None
    if not task['tenant_id'].startswith('preview-'):raise PermissionError('Preview topic scope invalid')
    messages=[]
    if task.get('conversation_id'):
        with main.store.connection() as c:
            owned=c.execute('select 1 from conversation_owners o join conversations v on v.id=o.conversation_id where o.conversation_id=? and o.user_id=? and v.tenant_id=? and v.agent_id=? and o.deleted_at is null',
                (task['conversation_id'],task['user_id'],task['tenant_id'],task['agent_id'])).fetchone()
        if not owned:raise PermissionError('Preview topic conversation mismatch')
        messages=main.store.conversation_messages(task['conversation_id'],task['tenant_id'])
    return decision(messages,task['input_text'])

def provider_request(body,task,main):
    if task['agent_id'] not in AGENTS or task['tenant_id']!=TENANT:return body
    if os.environ.get('CUSTOMER_PREVIEW_MODE')!='true':raise RuntimeError('Preview only')
    selected,question=for_task(main,task)
    if question:raise PermissionError('Brand topic confirmation required before Provider dispatch')
    text=body.get('instructions') or ''
    if policy() not in text:text+='\n\n'+policy()
    marker='本会话已由用户确认的创作主题：'
    text='\n'.join(line for line in text.splitlines() if not line.startswith(marker))
    text+='\n'+marker+'、'.join(NAMES[x-1] for x in selected)+'。沿用当前会话原需求完成本轮交付，不再重复询问。'
    return {**body,'instructions':text}

def install_runtime(main,task_context):
    if os.environ.get('CUSTOMER_PREVIEW_MODE')!='true':raise RuntimeError('Preview only')
    from app.domain import RuntimeStreamEvent,RuntimeTurn
    original=getattr(main.runtime,'_preview_brand_original_stream',main.runtime.stream_turn)
    main.runtime._preview_brand_original_stream=original
    async def stream(session,message):
        tid=task_context.get();task=main.product_store.task_for_worker(tid) if tid else None
        if task and task['agent_id'] in AGENTS and task['tenant_id']==TENANT:
            _,question=for_task(main,task)
            if question:
                yield RuntimeStreamEvent.visible_delta(question)
                yield RuntimeStreamEvent.completed(RuntimeTurn(session.thread_id,question,input_tokens=0,output_tokens=0,
                    structured_result_status='needs_brand_topic',structured_model_calls=0,
                    lifecycle_events=({'event':'preview_brand_topic_question','provider_invoked':False},)))
                return
        async for event in original(session,message):yield event
    main.runtime.stream_turn=stream

def is_question(task):
    return task.get('tenant_id')==TENANT and task.get('agent_id') in AGENTS and PREFIX in str(task.get('final_response') or '')
