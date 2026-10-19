"""Preview-only, zero-model correction of explicitly pending benefit clauses."""
import os
import re

from app.activity_plan_runtime import semantic_guard
from app.document_generator import ActivityPlanContent


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
