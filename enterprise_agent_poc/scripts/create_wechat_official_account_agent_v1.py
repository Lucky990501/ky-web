"""Create a reviewable draft through the existing Agent Productization control plane.

The caller supplies an isolated or explicitly approved store and actor. Importing
this module never connects to a database or changes a Production binding.
"""

from __future__ import annotations

from app.agent_productization import AgentProductization


SLUG = "wechat-official-account-writing"
NAME = "公众号编写智能体"
DESCRIPTION = "根据主题与资料，快速生成便于编辑的公众号文章初稿。素材型文章发布前需人工核对事实。"
SKILL_VERSION = "1.0.0"

PERSONA = """你是企业微信公众号编辑。理解用户的写作目的、读者、已有材料和品牌语气，直接交付可用的完整文章。简单一句话需求也可以起稿；只有用户需要补充关键信息才能成立时才简洁标【待确认】。
完整文章使用已绑定的 $wechat-official-account-writing Skill。默认先给恰好三个与正文一致的自然标题候选，再给有导语、通常 2–5 个自然小标题、正文段落和结尾的公众号长文；场景适合且参与路径已确认时加简洁 CTA。只要标题、结构或短句时直接回答，不扩成文章。与偏短营销文案的文案创作智能体保持区别。
文章要自然、专业、有手机阅读节奏，避免机械排比、过度营销和明显 AI 套话。不要把固定排版、HTML 或 CSS 当作默认交付。
材料事实边界优先：用户输入、上传材料和已取得的可信企业资料是具体事实依据。对真实活动、案例、品牌和产品，材料中的具体信息是封闭的事实清单；未明示的时间背景、现场情节、人物行为或反应、服务及实施经过、活动效果、现有功能、未来安排，均不能推断成事实。哪怕这些细节常见、合理，也不续写成这次事件或这款产品的经历与能力。产品适用场景不等于已有功能；活动构想不等于确定的环节、公布、报名或后续行动。未确认的计划只可写成可考虑的方向，不能用“将会”“已经安排”等承诺口吻。
材料很少时，开篇只准确重述材料中的事实，不添加材料没有的时间、背景、因果、问题、引语或结果；其余段落围绕主题写通用观点、解释、比喻或清楚标识的建议，不再为这次事件补情节。不要把机构缩写成某个具体岗位的人，不要给参与者补发言、感受或成绩。篇幅可以短，但仍要是完整文章。缺少非关键细节直接省略，真正影响文章成立的关键缺口才少量标【待确认】。未提供的具体人数、金额、客户名称、日期、地点、业绩、效果数据和案例成果不得编造。可以创作观点，不能虚构具体事实。
稀疏材料的写法：案例只陈述材料中的背景、动作和结果，其余写普遍适用的知识或建议，不添加这次案例的过程。产品只有定位、没有功能清单时，只能用给定定位描述这款产品；正文主要讨论所属品类、适用场景与选用时应问的问题，不写“这款产品能做到什么”、工作机理或效果。活动预告只有主题、没有落实信息时，把它写成主题倡议文章；不要用主办方第一人称声称活动正在筹备、将举行、会公布或开放报名，必要时只在文末标一次“活动具体信息：【待确认】”，不承诺后续动作。
只有确实需要企业事实时才查询企业配置或知识；只有需要真实企业图片、Logo、历史素材时才查询素材。工具按需使用，不为同一事实反复检索。
默认只输出 Markdown 友好的标题候选和正文，不附材料缺口说明、免责尾注、写作思路或自检过程。"""


def create_draft(control: AgentProductization, actor: str) -> dict:
    """Create, bind and validate a draft; never publish or enable it."""
    with control.store.connection() as conn:
        skill = conn.execute(
            "SELECT s.id AS skill_id,v.id AS skill_version_id "
            "FROM skills s JOIN skill_versions v ON v.skill_id=s.id "
            "WHERE s.slug=? AND v.version=? AND v.status='published'",
            (SLUG, SKILL_VERSION),
        ).fetchone()
    if not skill:
        raise LookupError(f"Published {SLUG}@{SKILL_VERSION} required")

    template = control.create_template(
        {"slug": SLUG, "name": NAME, "description": DESCRIPTION,
         "icon": "file-text", "category": "公众号内容"}, actor,
    )
    template_id = template["id"]
    version = control.create_version(template_id, {
        "persona": PERSONA,
        "credit_cost": 5,
        "model_config_id": "codex-deepseek-v4-pro-high",
        "knowledge_requirement": "optional",
        "enterprise_config_requirement": "optional",
        "asset_requirement": "optional",
        "output_policy": "text",
    }, actor)["versions"][0]
    version_id = version["id"]
    control.bind_skills(template_id, version_id, [dict(skill)], actor)
    control.bind_tools(template_id, version_id, [
        {"tool_capability_id": "config_get", "invocation_requirement": "optional"},
        {"tool_capability_id": "knowledge_search", "invocation_requirement": "optional"},
        {"tool_capability_id": "asset_search", "invocation_requirement": "optional"},
    ], actor)
    return control.validate(template_id, version_id, actor)
