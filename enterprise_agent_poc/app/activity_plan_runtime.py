"""Bounded structured output and semantic checks for full activity plans."""
from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass

from pydantic import ValidationError

from app.document_generator import ActivityPlanContent


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        **{key: {"type": "string"} for key in (
            "document_title", "activity_theme", "activity_time", "activity_location", "target_audience", "invitation_copy"
        )},
        "promotion_channels": {"type": "array", "items": {"type": "string"}},
        "activity_items": {"type": "array", "items": {
            "type": "object", "properties": {key: {"type": "string"} for key in (
                "phase", "name", "description", "image_requirement"
            )}, "required": ["phase", "name", "description", "image_requirement"], "additionalProperties": False,
        }},
        "pending_items": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "document_title", "activity_theme", "activity_time", "activity_location", "target_audience",
        "promotion_channels", "activity_items", "invitation_copy", "pending_items",
    ],
    "additionalProperties": False,
}

STRUCTURED_INSTRUCTION = (
    "本轮需要完整活动方案的机器可读结果供文档生成。请按 event-campaign-plan 的事实边界完成策划，"
    "最终只输出一个严格 JSON object，字段使用 Activity Plan Structured Contract。"
    "不要 Markdown 代码块、解释文字或未确认事项的确定承诺；未知事实保留【待确认】。"
)
SAFE_FALLBACK = "本次方案暂无法生成可靠的可导出文档，请调整需求后重试。"
_FULL_PLAN = re.compile(r"(?:完整|详细|正式).{0,12}(?:活动方案|活动策划|执行方案)|(?:活动方案|活动策划|执行方案).{0,8}(?:完整|详细|全套)|(?:社区|节日|教师节|客户答谢|开学季).{0,8}(?:活动方案|执行方案)")
_SIMPLE = re.compile(r"(?:想|写|给我|提供).{0,6}(?:\d+|几|五|三).{0,8}(?:标题|主题|句邀约|条文案)|(?:标题|主题|文案).{0,8}(?:几个|几条)")
_CONDITIONAL = re.compile(r"建议|拟|可考虑|计划|待确认|(?<!未)确认后|视.{0,12}情况|以.{0,18}为准|在.{0,18}确认后")
_IF_CLAUSE = re.compile(r"(?:^|[，,])\s*(?:若|如果|如|假如|倘若)([^，,]{1,60})")
_COMMITTED = re.compile(r"将|会(?=在|于|由|安排|提供|开展|进行|有|赠送|领取|设置|组织|负责)|已(?:确认|安排|提供|采购|准备|落实)|现场.{0,4}(?:有|设|提供|安排)|(?<!未)(?<!不)提供|(?<!未)(?<!不)安排|确保|赠送|免费|可获得|可领取")
_DISCLOSURE = re.compile(r"(?:将|会)?(?:另行通知|待通知|待公布|待确认|待定)")
_BENEFIT = re.compile(r"(?:价值|售价|价格|优惠|权益|赠送|免费).{0,15}\d[\d,]*\s*元|\d[\d,]*\s*元.{0,15}(?:课程|礼品|权益|服务)")
_DEPENDENCIES = {
    "staff": (r"工作人员|执行人员|志愿者|师资|讲师|顾问", r"工作人员|讲师|顾问|志愿者|一对一跟进|专人"),
    "material": (r"物料|材料|道具|手作|花材|桌椅|设备", r"手作|物料|材料|道具|花材|桌椅|设备|手作区"),
    "benefit": (r"权益|礼品|赠品|奖品|礼遇|预算|采购", r"权益|礼品|赠品|奖品|礼遇|优惠|免费|赠送|领取"),
    "location": (r"地点|场地|会场", r"地点|场地|会场"),
    "time": (r"时间|日期|档期", r"时间|日期|档期|开始|结束"),
    "service": (r"服务|课程演示|课程讲解|外部资源|合作方", r"服务|课程演示|课程讲解|外部资源|合作方"),
}


def _conditional_clause(clause: str) -> bool:
    if _CONDITIONAL.search(clause):
        return True
    premise = _IF_CLAUSE.search(clause)
    if not premise:
        return False
    condition = premise.group(1)
    if re.search(r"未|不足|无法|不能|缺少", condition):
        # A missing dependency permits a fallback, not a promise to perform
        # the same dependent activity anyway.
        return bool(re.search(r"调整|改为|替代|取消|顺延|不开展|转为", clause))
    return bool(re.search(r"确认|到位|备齐|落实|获批|可用|采购完成|充足|有保障", condition))


def is_full_activity_plan(agent_id: str, message: str) -> bool:
    return agent_id == "campaign-agent" and bool(_FULL_PLAN.search(message)) and not bool(_SIMPLE.search(message))


def parse_plan(raw: str) -> ActivityPlanContent | None:
    """Only a whole provider JSON object is accepted, never a Markdown extraction."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return ActivityPlanContent.model_validate(data)
    except (ValueError, TypeError, ValidationError):
        return None


@dataclass(frozen=True)
class Violation:
    type: str
    field: str
    evidence: str
    pending_item: str = ""
    dependency_kind: str = ""

    def public(self) -> dict[str, str]:
        return {"type": self.type, "field": self.field, "evidence": self.evidence,
                "pending_item": self.pending_item, "dependency_kind": self.dependency_kind}


def semantic_guard(plan: ActivityPlanContent, grounding: tuple[str, ...] = ()) -> list[Violation]:
    violations: list[Violation] = []
    fields = {"invitation_copy": plan.invitation_copy, "activity_time": plan.activity_time,
              "activity_location": plan.activity_location}
    for index, item in enumerate(plan.activity_items):
        fields[f"activity_items.{index}.name"] = item.name
        fields[f"activity_items.{index}.description"] = item.description
    for pending in plan.pending_items:
        for kind, (pending_pattern, use_pattern) in _DEPENDENCIES.items():
            if not re.search(pending_pattern, pending):
                continue
            for field, value in fields.items():
                if (field == "activity_time" and kind != "time"
                        or field == "activity_location" and kind != "location"
                        or kind in {"time", "location"} and field not in
                        {"activity_time", "activity_location", "invitation_copy"}):
                    continue
                for clause in re.split(r"[。！？；;\n]", value):
                    if _conditional_clause(clause):
                        continue
                    checked = _DISCLOSURE.sub("", clause)
                    if re.search(use_pattern, checked) and _COMMITTED.search(checked):
                        violations.append(Violation("pending_dependency_committed", field, clause[:180], pending[:180], kind))
    evidence = " ".join(grounding)
    for field, value in fields.items():
        for clause in re.split(r"[。！？；;\n]", value):
            match = _BENEFIT.search(clause)
            if match and not _conditional_clause(clause) and match.group() not in evidence:
                violations.append(Violation("unverified_enterprise_benefit", field, match.group()[:180], dependency_kind="benefit"))
    return list(dict.fromkeys(violations))


def correction_prompt(plan: ActivityPlanContent | None, violations: list[Violation]) -> str:
    if plan is None:
        return STRUCTURED_INSTRUCTION + " 上轮输出不是完整合法的 JSON object；请重新输出，不要代码块或附言。"
    required = {
        "pending_dependency_committed": "仅在该字段中把未确认的人、物料、时间、场地或服务表述为条件性提案、明确待确认，或删除确定性承诺。",
        "unverified_enterprise_benefit": "仅在该字段中删除未经核实的企业权益，或明确改为待确认提案；不得编造新权益。",
    }
    details = json.dumps([
        {**item.public(), "required_correction": required.get(item.type, "仅修正该字段的已标记冲突。")}
        for item in violations
    ], ensure_ascii=False)
    return (
        "请修正上一轮活动方案 JSON 中的语义冲突，并仍只返回完整严格 JSON object。"
        "只修改下面列出的冲突字段；document_title、activity_theme、target_audience、已有活动创意、"
        "已验证企业事实和其他字段必须逐字保留，不可增加企业事实。"
        "Runtime 会只提取冲突字段的修正并重新执行完整验证。冲突：" + details
        + "\n原始 JSON：" + json.dumps(plan.model_dump(), ensure_ascii=False)
    )


def merge_targeted_correction(original: ActivityPlanContent, corrected: ActivityPlanContent,
                              violations: list[Violation]) -> ActivityPlanContent | None:
    """Keep every original field except model-authored fixes to named conflicts."""
    before, after = original.model_dump(), corrected.model_dump()
    if len(before["activity_items"]) != len(after["activity_items"]):
        return None
    for violation in violations:
        field = violation.field
        if field in before and field != "activity_items":
            before[field] = after[field]
            continue
        match = re.fullmatch(r"activity_items\.(\d+)\.(phase|name|description|image_requirement)", field)
        if match:
            index, child = int(match.group(1)), match.group(2)
            if index >= len(before["activity_items"]):
                return None
            before["activity_items"][index][child] = after["activity_items"][index][child]
    return ActivityPlanContent.model_validate(before)


def safe_violations(violations: list[Violation]) -> list[dict[str, str]]:
    """Diagnostic identities only; never persist model or enterprise prose."""
    safe = []
    for item in violations:
        identity = "|".join((item.type, item.field, item.evidence, item.pending_item, item.dependency_kind))
        safe.append({
            "type": item.type, "field": item.field, "dependency_kind": item.dependency_kind,
            "evidence_sha256": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        })
    return safe


def result_envelope(plan: ActivityPlanContent) -> dict:
    return {"type": "activity_plan", "version": "1", "data": plan.model_dump()}


def validated_envelope(value: object, response: str, grounding: tuple[str, ...] = ()) -> dict | None:
    if not isinstance(value, dict) or value.get("type") != "activity_plan" or value.get("version") != "1":
        return None
    try:
        plan = ActivityPlanContent.model_validate(value.get("data"))
    except (ValueError, TypeError, ValidationError):
        return None
    if semantic_guard(plan, grounding) or response != render_markdown(plan):
        return None
    return result_envelope(plan)


def render_markdown(plan: ActivityPlanContent) -> str:
    def cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", "<br>")

    lines = [f"# {plan.document_title}", "", "## 一、活动主题", plan.activity_theme,
             "", "## 二、活动时间", plan.activity_time, "", "## 三、活动地点", plan.activity_location,
             "", "## 四、活动对象", plan.target_audience, "", "## 五、活动宣发途径"]
    lines.extend(f"- {value}" for value in plan.promotion_channels)
    lines.extend(["", "## 六、活动说明", "| 环节 | 名称 | 说明 | 示意图需求 |", "| --- | --- | --- | --- |"])
    lines.extend("| " + " | ".join(cell(value) for value in (item.phase, item.name, item.description, item.image_requirement)) + " |"
                 for item in plan.activity_items)
    lines.extend(["", "## 附件一：活动邀约文案（参考）", plan.invitation_copy])
    if plan.pending_items:
        lines.extend(["", "## 待确认事项"])
        lines.extend(f"- {value}" for value in plan.pending_items)
    return "\n".join(lines).strip()


def chunk_validated_markdown(markdown: str, max_chars: int = 120) -> tuple[str, ...]:
    """Split only already-validated text, preferring intact Markdown lines/rows.

    No pacing is applied. Python string slices preserve Unicode code points,
    and concatenation must reproduce the deterministic renderer exactly.
    """
    if max_chars < 40:
        raise ValueError("max_chars must be at least 40")
    chunks: list[str] = []
    current = ""
    for line in markdown.splitlines(keepends=True):
        remaining = line
        while remaining:
            if len(remaining) <= max_chars:
                piece, remaining = remaining, ""
            else:
                cut = remaining.rfind("|", 40, max_chars + 1)
                if cut < 40:
                    cut = remaining.rfind(" ", 40, max_chars + 1)
                cut = cut + 1 if cut >= 40 else max_chars
                piece, remaining = remaining[:cut], remaining[cut:]
            if current and len(current) + len(piece) > max_chars:
                chunks.append(current)
                current = ""
            current += piece
    if current:
        chunks.append(current)
    if "".join(chunks) != markdown:
        raise RuntimeError("validated Markdown chunk reconstruction mismatch")
    return tuple(chunks)
