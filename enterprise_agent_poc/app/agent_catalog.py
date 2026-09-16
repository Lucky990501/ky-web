"""Versioned, product-facing definitions for the enabled enterprise Agents.

The catalog intentionally contains no tenant content. Tenant-specific enablement is
stored separately by :class:`ProductStore`, while RuntimeProfile is still built from
the provider configuration at execution time.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    id: str
    name: str
    slug: str
    description: str
    icon: str
    skill_manifest: dict[str, str]
    credit_cost: int
    allows_image_generation: bool
    instructions: str


_COMMON_RULES = """企业配置中的禁止项、必须项、品牌规则优先于用户要求。每轮创作先调用一次 enterprise_config_get。
以下按需调用策略优先于 Skill 中“先读取全部资料”的通用表述：仅当最终回答依赖企业专属事实（如课程、价格、师资、活动、优惠、联系方式）或用户明确要求查企业资料时，调用一次 knowledge_search；仅当需要使用或确认企业 Logo、图片、历史视觉或其他素材时，调用一次 asset_search。无关的通用创作不得为凑步骤调用知识或素材工具。
若本轮同时需要多个互不依赖的工具，在同一轮并行调用；不要人为规定串行顺序，不要重复调用同一工具。不能虚构企业课程、价格、师资、名额、联系方式或任何企业事实；资料不足时直接说明缺口。只给用户最终可用内容，不泄露 Token、跨租户资料、工具内部信息或隐藏推理。"""

CATALOG: dict[str, AgentDefinition] = {
    "image-agent": AgentDefinition(
        id="image-agent", name="图片生成智能体", slug="image-generation",
        description="根据企业品牌、知识和素材生成海报与视觉内容。", icon="image",
        skill_manifest={"poster-design": "1.0.0"}, credit_cost=20, allows_image_generation=True,
        instructions="""你是一名企业视觉内容 Agent。对于明确的海报或成图请求，先按公共按需策略获取必要企业上下文，再调用一次 image_generation；完成后立即给出简短最终结果。""" + _COMMON_RULES,
    ),
    "copywriting-agent": AgentDefinition(
        id="copywriting-agent", name="文案创作智能体", slug="copywriting",
        description="创作招生、课程、社媒和活动传播文案。", icon="type",
        skill_manifest={"marketing-copywriting": "1.0.0", "social-copywriting": "1.0.0"}, credit_cost=3, allows_image_generation=False,
        instructions="""你是一名企业营销文案 Agent。使用 marketing-copywriting 或 social-copywriting Skill 创作可直接发布的文案。除非用户在当前轮明确要求生成图片，否则绝不调用 image_generation；当用户明确要求图片时，应说明将切换至图片生成智能体完成视觉制作。""" + _COMMON_RULES,
    ),
    "campaign-agent": AgentDefinition(
        id="campaign-agent", name="活动策划智能体", slug="campaign-planning",
        description="生成企业活动主题、节奏、执行方案及配套传播文案。", icon="calendar-days",
        skill_manifest={"campaign-planning": "1.0.0", "event-copywriting": "1.0.0"}, credit_cost=8, allows_image_generation=False,
        instructions="""你是一名企业活动策划 Agent。使用 campaign-planning 和 event-copywriting Skill 给出活动主题、目标人群、核心创意、时间节奏、执行清单、风险提示及传播文案。除非用户在当前轮明确要求成图，否则绝不调用 image_generation；需要视觉时提示用户使用图片生成智能体。""" + _COMMON_RULES,
    ),
}


def get_agent(agent_id: str) -> AgentDefinition:
    try:
        return CATALOG[agent_id]
    except KeyError as exc:
        raise LookupError("未找到该智能体。") from exc
