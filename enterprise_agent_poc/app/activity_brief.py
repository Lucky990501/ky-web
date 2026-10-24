"""Minimal activity prerequisites from current and explicitly provided user facts."""
from __future__ import annotations

import json
import re
import os

CONTEXT_PREFIX = "[ACTIVITY_USER_CONTEXT_V1]\n"
QUESTION_PREFIX = "还需要确认："
LABELS = {"content": "活动内容", "time": "活动时间", "location": "活动地点", "audience": "活动对象"}


def with_user_context(current: str, messages: list[dict]) -> str:
    previous = [item["content"] for item in messages if item["role"] == "user"][-12:]
    if previous and previous[-1] == current:
        previous.pop()
    waiting = any(item["role"] == "assistant" and item["content"].startswith(QUESTION_PREFIX) for item in messages[-3:])
    if not waiting and os.environ.get('CUSTOMER_PREVIEW_MODE')=='true':
        from app.activity_plan_runtime import is_full_activity_plan,_SIMPLE,_PLAN_DISCUSSION
        # Continue the latest visible planning request, never another project
        # or a user's explicit small-copy/discussion request.
        anchors=[i for i,text in enumerate(previous) if is_full_activity_plan('campaign-agent',text)]
        if anchors and not is_full_activity_plan('campaign-agent',current) and not (_SIMPLE.search(current) or _PLAN_DISCUSSION.search(current)):
            previous=previous[anchors[-1]:];waiting=True
    if not previous or not waiting:
        return current
    return CONTEXT_PREFIX + json.dumps({"current": current, "previous": previous}, ensure_ascii=False)


def assess_activity(message: str) -> dict | None:
    previous = []
    current = message
    if message.startswith(CONTEXT_PREFIX):
        try:
            value = json.loads(message[len(CONTEXT_PREFIX):])
            current, previous = value["current"], value["previous"]
            if not isinstance(current, str) or not isinstance(previous, list) or not all(isinstance(item, str) for item in previous):
                return None
        except (ValueError, KeyError, TypeError):
            return None
    from app.activity_plan_runtime import _SIMPLE,_PLAN_DISCUSSION,is_full_activity_plan
    if _SIMPLE.search(current) or _PLAN_DISCUSSION.search(current):
        return None
    if re.search(r"(?:标题|几个主题|一句|短文案|邀约文案|是什么|什么意思)", current) and not re.search(r"完整|正式|执行方案", current):
        return None
    if not previous and re.search(r"^(?:(?:请问|请解释|请|帮我|麻烦)\s*)*(?:如何|怎么|怎样|为什么|是否|什么是|修改|改写|润色|调整|优化|补充|解释|点评|分析)|^(?:这个|这份|上述|刚才|已有|原有|既有|他们)", current):
        return None
    if not previous and re.search(r"活动.{0,8}(?:已经|已).{0,8}(?:策划好|完成)|(?:请|帮我)(?:点评|分析)", current):
        return None
    text = "\n".join([*previous, current])
    intent = bool(previous or is_full_activity_plan('campaign-agent',current) or re.search(r"(?:策划|筹备|组织|准备|做|制作|生成|出|设计|制定).{0,20}(?:活动|方案)|活动.{0,12}(?:完整方案|正式方案|执行方案)", current))
    if not intent or not re.search(r"活动|亲子|开学季|客户答谢|策划案", text):
        return None
    # Match facts expressed in ordinary prose as well as labels. Unknown
    # labelled lines never count as confirmed. No calendar facts are inferred.
    unknown = r"未定|待确认|待定|不知道|未提供|未确定|未确认|还没定|没定|没想好|暂无"
    facts = re.sub(r"(?:时间|日期|地点|场地|对象|内容)[：:]?[^\n。；，,]*(?:" + unknown + r")[^\n。；，,]*", "", text)
    known = {
        "content": bool(re.search(r"亲子|助学|阅读|读书|公益|开学季|国庆|教师节|元旦|节日|答谢|感恩|招生|培训|主题[：:].+|内容[：:].+", facts)),
        "time": bool(re.search(r"\d{1,4}[年/月日]|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[:：]\d{2}|明天|后天|本周末|周末|下周|周[一二三四五六日天]|日期[：:].+|时间[：:].+", facts)),
        "location": bool(re.search(r"(?:在|地点[：:]?|场地[：:]?)[^\n。；，,]{0,36}(?:广场|活动室|教室|大厅|校区|门店|公园|社区|会场|礼堂|中心|操场|图书馆|楼|门)|(?:地点|场地)[：:][^\n。；，,]{2,40}", facts)),
        "audience": bool(re.search(r"亲子|家长|孩子|学生|学员|客户|业主|居民|员工|参与对象[：:].+|活动对象[：:].+", facts)),
    }
    # The last explicit user correction wins over an older confirmed field.
    for field, label in (("content", "内容|主题"), ("time", "时间|日期"), ("location", "地点|场地"), ("audience", "对象")):
        mentions = list(re.finditer(r"(?:" + label + r")[：:]?([^\n。；，,]*)", text))
        if mentions and re.search(unknown, mentions[-1].group(1)):
            known[field] = False
    missing = [LABELS[key] for key, present in known.items() if not present]
    return {"missing": missing, "source": text, "ready": not missing,
            "question": QUESTION_PREFIX + "、".join(missing) + "。请补充这些信息，已提供的内容无需重复。" if missing else None}


def illustration_briefs(envelope: dict) -> list[dict]:
    """Pure briefs for a separately authorized image-agent Task; never calls it."""
    if envelope.get("type") != "activity_plan" or envelope.get("version") != "1":
        return []
    from app.document_generator import ActivityPlanContent
    try:
        plan = ActivityPlanContent.model_validate(envelope["data"])
    except (ValueError, KeyError, TypeError):
        return []
    return [{"purpose": purpose, "aspect_ratio": ratio,
             "prompt": f"为《{plan.document_title}》制作{purpose}。活动主题：{plan.activity_theme}；场地：{plan.activity_location}；对象：{plan.target_audience}。"
                       f"参考方案环节：{'；'.join(item.name for item in plan.activity_items[:6])}。只作为策划示意，不声称真实现场；待确认信息不得画成确定文字，不编造企业权益或Logo。",
             "execution": "requires_separate_image_agent_task_and_budget"}
            for purpose, ratio in (("会场示意图", "16:9"), ("签到区示意图", "3:2"), ("活动主视觉", "4:5"))]
