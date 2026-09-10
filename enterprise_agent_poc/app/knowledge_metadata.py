"""Explainable canonical metadata and lightweight intent for knowledge retrieval."""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any


RAG_INDEX_VERSION = "rag-index-v2"
METADATA_SCHEMA_VERSION = "knowledge-metadata-v1"
CANONICAL_SECTIONS = (
    "AI知识库",
    "活动场次",
    "嘉宾档案",
    "推荐阅读",
    "活动流程规则",
    "历史日期索引",
    "使用说明",
)


class KnowledgeQueryIntent(str, Enum):
    AI_KNOWLEDGE = "AI知识库"
    EVENT_SESSION = "活动场次"
    GUEST_PROFILE = "嘉宾档案"
    RECOMMENDED_READING = "推荐阅读"
    EVENT_WORKFLOW = "活动流程规则"
    HISTORICAL_DATE = "历史日期索引"
    USAGE = "使用说明"
    UNKNOWN = "unknown"


_INTENT_SIGNALS: dict[KnowledgeQueryIntent, tuple[tuple[str, float], ...]] = {
    KnowledgeQueryIntent.HISTORICAL_DATE: (("以往活动时间", 6), ("过往活动时间", 6), ("历史活动时间", 6), ("历史日期", 4), ("历史时间", 4), ("过往", 3), ("以往", 3), ("以前", 3), ("历史记录", 3), ("日期", 1)),
    KnowledgeQueryIntent.EVENT_WORKFLOW: (("流程规则", 4), ("执行规范", 4), ("活动流程", 3), ("组织步骤", 3), ("注意事项", 3), ("落地", 2), ("流程", 2), ("规范", 2), ("规则", 1)),
    KnowledgeQueryIntent.USAGE: (("使用说明", 4), ("使用规则", 4), ("阅读方法", 4), ("如何使用", 3), ("怎么用", 3), ("工作流", 2), ("隐私", 2), ("字段", 2), ("用途", 2)),
    KnowledgeQueryIntent.AI_KNOWLEDGE: (("问答知识库", 5), ("问答库", 4), ("知识库", 3), ("问答资料", 3), ("资料说明", 3), ("活动问答", 3), ("报名", 2)),
    KnowledgeQueryIntent.EVENT_SESSION: (("活动场次", 4), ("活动时间", 4), ("排期", 4), ("场次", 3), ("举行", 2), ("时间安排", 2), ("活动安排", 2)),
    KnowledgeQueryIntent.GUEST_PROFILE: (("嘉宾档案", 4), ("嘉宾介绍", 4), ("讲师人选", 4), ("嘉宾", 3), ("讲师", 3), ("专家", 2), ("人物", 2), ("老师", 1)),
    KnowledgeQueryIntent.RECOMMENDED_READING: (("推荐阅读", 5), ("延伸阅读", 4), ("预热阅读", 4), ("导读", 3), ("书目", 3), ("阅读", 2), ("推荐材料", 3)),
}


def normalize_for_matching(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def detect_query_intent(query: str) -> KnowledgeQueryIntent:
    """Classify only the knowledge category using transparent weighted phrases."""
    normalized = normalize_for_matching(query)
    scored = []
    for position, (intent, signals) in enumerate(_INTENT_SIGNALS.items()):
        score = sum(weight for phrase, weight in signals if normalize_for_matching(phrase) in normalized)
        scored.append((score, -position, intent))
    score, _, intent = max(scored)
    return intent if score >= 2 else KnowledgeQueryIntent.UNKNOWN


def metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _canonical_section(sheet_name: str, section: str, content: str) -> str | None:
    for value in (sheet_name, section):
        if value in CANONICAL_SECTIONS:
            return value
    explicit = re.search(r"(?m)^\s*[-*]?\s*一级分类[：:]\s*([^\n]+?)\s*$", content)
    if explicit:
        source_category = explicit.group(1).strip()
        if source_category in CANONICAL_SECTIONS:
            return source_category
    text = f"{sheet_name} {section} {content[:600]}"
    if re.fullmatch(r"记录\s*\d+", section) and all(
        re.search(rf"(?m)^\s*[-*]?\s*{field}[：:]", content)
        for field in ("原始日期", "标准日期", "年份", "序号")
    ):
        return KnowledgeQueryIntent.HISTORICAL_DATE.value
    if re.search(r"历史.{0,8}(?:日期|场次|时间)索引|(?:过往|历年).{0,8}(?:日期|场次)", text):
        return KnowledgeQueryIntent.HISTORICAL_DATE.value
    if re.search(r"(?:推荐阅读|导读|书目)|《[^》]{2,80}》", text):
        return KnowledgeQueryIntent.RECOMMENDED_READING.value
    if re.search(r"(?:简介|人物档案|嘉宾档案)$", section) or re.search(r"(?:姓名|人物)[：:]", content[:300]):
        return KnowledgeQueryIntent.GUEST_PROFILE.value
    if re.search(r"(?:活动规则|现场执行|现场准备|前期准备|中期准备|后续工作|宣传执行|流程|签到|布场)", section):
        return KnowledgeQueryIntent.EVENT_WORKFLOW.value
    if re.search(r"^(?:用途|隐私处理|工作流建议|主表字段|使用说明|源文件差异|旧版XLS说明)$", section):
        return KnowledgeQueryIntent.USAGE.value
    if re.search(r"(?:^|[｜|])\d{4}(?:[-年]|年)|活动时间|活动名称|活动场次|活动主线", section):
        return KnowledgeQueryIntent.EVENT_SESSION.value
    if re.search(r"(?:问答知识库|AI知识库|名师面对面是什么)", text, re.IGNORECASE):
        return KnowledgeQueryIntent.AI_KNOWLEDGE.value
    return None


def _person_name(section: str) -> str | None:
    match = re.match(r"^([\u4e00-\u9fff·]{2,10})(?:简介|介绍|档案)$", section.strip())
    return match.group(1) if match else None


def _year(section: str, content: str) -> int | None:
    match = re.search(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)", f"{section} {content[:300]}")
    return int(match.group(1)) if match else None


def build_chunk_metadata(
    *,
    source_file_id: str,
    source_filename: str,
    sheet_name: str | None,
    section: str | None,
    title: str | None,
    content: str,
) -> dict[str, Any]:
    """Build portable JSON metadata without changing or copying enterprise prose."""
    section_value = (section or "正文").strip()
    sheet_value = (sheet_name or "").strip()
    canonical = _canonical_section(sheet_value, section_value, content)
    person_name = _person_name(section_value)
    year = _year(section_value, content)
    if canonical == KnowledgeQueryIntent.GUEST_PROFILE.value:
        record_type, entity_type = "profile", "person"
    elif canonical == KnowledgeQueryIntent.EVENT_SESSION.value:
        record_type, entity_type = "event", "event"
    elif canonical == KnowledgeQueryIntent.EVENT_WORKFLOW.value:
        record_type, entity_type = "workflow", "process"
    elif canonical == KnowledgeQueryIntent.RECOMMENDED_READING.value:
        record_type, entity_type = "reading", "document"
    elif canonical == KnowledgeQueryIntent.HISTORICAL_DATE.value:
        record_type, entity_type = "date_index", "event"
    elif canonical == KnowledgeQueryIntent.USAGE.value:
        record_type, entity_type = "usage", "document"
    elif canonical == KnowledgeQueryIntent.AI_KNOWLEDGE.value:
        record_type, entity_type = "knowledge", "document"
    else:
        record_type, entity_type = None, None
    event_name = None
    if record_type == "event":
        segments = [item.strip() for item in re.split(r"[｜|]", section_value) if item.strip()]
        event_name = segments[-1] if segments else section_value
    return {
        "source": "enterprise_file",
        "source_file_id": source_file_id,
        "source_filename": source_filename,
        "sheet_name": sheet_value or None,
        "section": section_value,
        "canonical_section": canonical,
        "record_type": record_type,
        "entity_type": entity_type,
        "year": year,
        "person_name": person_name,
        "event_name": event_name,
        "index_version": RAG_INDEX_VERSION,
        "metadata_schema_version": METADATA_SCHEMA_VERSION,
        "document_title": title,
    }


def embedding_input(content: str, metadata: dict[str, Any]) -> str:
    """Add retrieval context while preserving the separately stored source prose."""
    labels = (
        ("知识分类", metadata.get("canonical_section")),
        ("工作表", metadata.get("sheet_name")),
        ("记录类型", metadata.get("record_type")),
        ("实体类型", metadata.get("entity_type")),
        ("人物", metadata.get("person_name")),
        ("年份", metadata.get("year")),
        ("活动", metadata.get("event_name")),
        ("标题", metadata.get("document_title")),
        ("章节", metadata.get("section")),
    )
    header = "\n".join(f"{label}：{value}" for label, value in labels if value not in (None, ""))
    return f"{header}\n\n{content}" if header else content


def query_embedding_input(query: str, intent: KnowledgeQueryIntent) -> str:
    return f"知识分类：{intent.value}\n查询：{query}" if intent is not KnowledgeQueryIntent.UNKNOWN else query


def metadata_score(intent: KnowledgeQueryIntent, metadata: dict[str, Any]) -> float:
    canonical = metadata.get("canonical_section")
    if intent is KnowledgeQueryIntent.UNKNOWN or not canonical:
        return 0.0
    return 1.0 if canonical == intent.value else -0.25


def keyword_score(query: str, *, title: str, section: str, content: str, metadata: dict[str, Any]) -> float:
    terms = set(re.findall(r"[\u4e00-\u9fff]{1,2}|[a-zA-Z0-9_]+", query.lower()))
    if not terms:
        return 0.0
    heading = f"{title} {section}".lower()
    structured = " ".join(
        str(metadata.get(name) or "")
        for name in ("sheet_name", "canonical_section", "record_type", "entity_type", "year", "person_name", "event_name")
    ).lower()
    body = content.lower()
    ratio = lambda text: sum(term in text for term in terms) / len(terms)
    legacy = ratio(f"{title} {content}".lower())
    field_aware = 0.35 * ratio(heading) + 0.20 * ratio(structured) + 0.45 * ratio(body)
    return min(1.0, max(legacy, field_aware))
