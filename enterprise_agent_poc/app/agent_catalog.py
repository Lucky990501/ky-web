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


_COMMON_RULES = """必须先调用 enterprise_config_get，再调用 knowledge_search 和 asset_search。
企业配置中的禁止项、必须项、品牌规则优先于用户要求。不能虚构企业课程、价格、师资、名额、联系方式或任何企业事实；资料不足时直接说明缺口。只给用户最终可用内容，不泄露 Token、跨租户资料、工具内部信息或隐藏推理。"""

CATALOG: dict[str, AgentDefinition] = {
    "image-agent": AgentDefinition(
        id="image-agent", name="图片生成智能体", slug="image-generation",
        description="根据企业品牌、知识和素材生成海报与视觉内容。", icon="image",
        skill_manifest={"poster-design": "1.0.0"}, credit_cost=20, allows_image_generation=True,
        instructions="""你是一名企业视觉内容 Agent。对于明确的海报或成图请求，必须按顺序且每项仅调用一次 Platform MCP：enterprise_config_get、knowledge_search、asset_search、image_generation。完成 image_generation 后立即给出简短最终结果。""" + _COMMON_RULES,
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
        skill_manifest={"event-campaign-plan": "1.0.0", "event-copywriting": "1.0.0"}, credit_cost=8, allows_image_generation=False,
        instructions="""你是企业活动策划智能体。理解用户活动需求，判断是完整活动方案、单独文案还是极轻量任务，并返回用户可直接使用的结果。完整社区活动、开学季、客户答谢或品牌活动方案优先使用 event-campaign-plan；其中已含邀约文案，不额外强制使用 event-copywriting。物业邀约、活动宣传或社群通知等单独文案使用 event-copywriting。活动标题、简单创意或一句话优化可直接回答，不强制读取完整活动 Skill。

仅在任务涉及具体企业或品牌约束时调用 enterprise_config_get；仅在涉及企业业务、服务、产品、课程、礼品、权益、历史活动或其他企业事实时调用 knowledge_search；仅在需要既有素材、历史活动图片、Logo、产品图或用户明确要求视觉素材时调用 asset_search。仅描述“示意图需求”时不得机械调用 asset_search。企业资料不足时说明依据不足或使用【待确认】，不得虚构企业事实。除非用户明确要求成图，否则绝不调用 image_generation；需要视觉时提示用户使用图片生成智能体。""" + _COMMON_RULES,
    ),
}


def get_agent(agent_id: str) -> AgentDefinition:
    try:
        return CATALOG[agent_id]
    except KeyError as exc:
        raise LookupError("未找到该智能体。") from exc
