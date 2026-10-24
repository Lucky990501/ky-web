"""Preview-only, zero-model correction of explicitly pending benefit clauses."""
import os
import re

from app.activity_plan_runtime import semantic_guard
from app.document_generator import ActivityPlanContent

PREVIEW_PLANNING_INSTRUCTION = (
    '\n环节规划：普通活动默认组织为约6个完整主环节；复杂活动可扩展到7至10个，最多10个。'
    'activity_items中的每一项代表一个完整主环节及一张对应示意图需求，不把准备、通知、签到细节、转场、回访拆成大量独立项。'
    '将相邻且相关的小步骤整合进对应主环节的description，保留用户要求、执行动作、人员与待确认条件，不删减重要内容。'
    '若用户明确要求少于6个环节，遵从其要求；明确要求超过10个时，在最多10个主环节下保留子步骤，不能丢弃需求。'
    '\nPreview策划事实边界：策划方案不等于资源已经落实。区分用户已确认事实、工具核验事实、执行建议与待确认条件。'
    '人员、物料、服务、礼品与预算尚未确认时仍可提出完整建议，但相关环节须明确写为建议及执行条件。'
    '例如“建议安排工作人员接待，具体人数及排班待校区确认”；“建议课程演示，师资和时段确认后执行”；'
    '“可设计礼品环节，礼品及预算落实后执行，未落实则取消”。不得写成已安排、已落实或确定赠送。'
    'pending_items列出未确认资源，且引用该资源的环节描述也必须有相应条件，不得只在文末列待确认。'
    '保留用户明确的主题、日期、地点和对象，不把已确认的活动要求改成待确认。'
)


def planning_schema():
    """Preview wire constraint only; shared PLAN_SCHEMA is never mutated."""
    from copy import deepcopy
    from app.activity_plan_runtime import PLAN_SCHEMA
    schema=deepcopy(PLAN_SCHEMA)
    schema['properties']['activity_items']['maxItems']=10
    return schema


def annotate_pending_resources(plan, violations, grounding, tenant_id):
    """Condition only Guard-identified clauses; never repair invented facts.

    Complete schema + explicit pending resource + exact unambiguous field/clause
    are prerequisites. Time/place/title, prices, past claims and contradictions
    are deliberately not repairable by this zero-model annotation.
    """
    if os.environ.get('CUSTOMER_PREVIEW_MODE')!='true' or not tenant_id.startswith('preview-') or not violations:return None
    labels={'staff':'执行人员及排班','material':'相关物料','service':'相关服务与资源','benefit':'相关礼品与权益'}
    if any(v.type!='pending_dependency_committed' or v.dependency_kind not in labels
           or not v.pending_item or not v.evidence or not any(p.startswith(v.pending_item) for p in plan.pending_items)
           or not re.fullmatch(r'activity_items\.\d+\.(?:description|name)',v.field) for v in violations):return None
    data=plan.model_dump();changed=set()
    for field in {v.field for v in violations}:
        index=int(field.split('.')[1]);attribute=field.split('.')[2];parts=re.split(r'([。！？；;\n])',data['activity_items'][index][attribute])
        for n in range(0,len(parts),2):
            clause=parts[n];matches=[v for v in violations if v.field==field and clause.startswith(v.evidence)]
            if not matches:continue
            if any(sum(parts[j].startswith(v.evidence) for j in range(0,len(parts),2))!=1 for v in matches):return None
            # A whole negative resource assertion (e.g. a gift is NOT an
            # implemented benefit) is not a past factual claim. Preserve its
            # wording and add the same explicit pending marker; the full Guard
            # still revalidates the entire candidate below. Never apply this
            # exception to a mixed clause containing an affirmative promise.
            negative_resource = bool(re.fullmatch(
                r'\s*(?:礼品|赠品|奖品|权益|礼遇)(?:不|不得|不能)(?:作为|视为|当作|写成|标为)'
                r'已(?:确认|落实)(?:的)?(?:事项|权益|福利|礼品|赠品|奖品|礼遇|资源)\s*', clause))
            if (re.search(r'若|如果|假如|倘若|未.{0,25}仍|\d[\d,.]*\s*元|保证|百分之百|100%',clause)
                    or not negative_resource and re.search(r'已(?:确认|安排|提供|采购|准备|落实)',clause)):return None
            condition='、'.join(labels[k] for k in sorted({v.dependency_kind for v in matches}))
            parts[n]=('【待确认提案】' if attribute=='name' else '【待确认提案】建议仅在'+condition+'确认落实后执行，具体安排待校区确认，未落实则取消该项：')+clause
            changed.update((v.field,v.evidence) for v in matches)
        data['activity_items'][index][attribute]=''.join(parts)
    if changed!={(v.field,v.evidence) for v in violations}:return None
    candidate=ActivityPlanContent.model_validate(data)
    return candidate if not semantic_guard(candidate,grounding) else None


def annotate_pending_benefit(plan,violations,grounding,tenant_id):
    if os.environ.get('CUSTOMER_PREVIEW_MODE')!='true' or not tenant_id.startswith('preview-') or not violations:
        return None
    # Do not generalize this repair to staff/time, unknown schema, invented
    # priced benefits or missing facts that the model did not mark pending.
    if any(v.type!='pending_dependency_committed' or v.dependency_kind!='benefit'
           or not v.pending_item or not v.evidence
           or not any(p.startswith(v.pending_item) for p in plan.pending_items)
           or not re.fullmatch(r'activity_items\.\d+\.description',v.field) for v in violations):return None
    data=plan.model_dump();changed=set()
    for field in {v.field for v in violations}:
        index=int(field.split('.')[1]);parts=re.split(r'([。！？；;\n])',data['activity_items'][index]['description'])
        for number in range(0,len(parts),2):
            clause=parts[number]
            if not any(v.field==field and clause.startswith(v.evidence) for v in violations):continue
            # A contradictory negative condition is not repaired by wrapping it.
            if re.search(r'若|如果|假如|倘若|未.{0,25}仍',clause):return None
            parts[number]='【待确认提案：仅在相关礼品与权益核实并落实后执行，未确认则取消该项】'+clause
            changed.add(field)
        data['activity_items'][index]['description']=''.join(parts)
    if changed!={v.field for v in violations}:return None
    candidate=ActivityPlanContent.model_validate(data)
    return candidate if not semantic_guard(candidate,grounding) else None
