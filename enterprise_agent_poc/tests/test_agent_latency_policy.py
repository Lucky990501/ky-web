from app.agent_catalog import CATALOG


def test_all_legacy_agents_use_conditional_parallel_context_policy():
    """Protect the latency policy without weakening fact-grounding safeguards."""
    for agent in CATALOG.values():
        instructions = agent.instructions
        assert "每轮创作先调用一次 enterprise_config_get" in instructions
        assert "最终回答依赖企业专属事实" in instructions
        assert "需要使用或确认企业 Logo、图片、历史视觉或其他素材" in instructions
        assert "同一轮并行调用" in instructions
        assert "不要重复调用同一工具" in instructions
        assert "不能虚构企业课程、价格、师资、名额、联系方式或任何企业事实" in instructions
        assert "必须先调用 enterprise_config_get，再调用 knowledge_search 和 asset_search" not in instructions
        assert "必须按顺序且每项仅调用一次 Platform MCP" not in instructions
