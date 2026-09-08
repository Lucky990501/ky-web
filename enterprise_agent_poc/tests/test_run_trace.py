from __future__ import annotations

import asyncio

from app.domain import RuntimeSession, RuntimeTurn
from app.service import AgentService
from app.settings import Settings
from app.store import POCStore


class FakeRuntime:
    async def create_session(self, profile, developer_instructions):
        return RuntimeSession(thread_id="thread-test-1", profile_id=profile.id)

    async def resume_session(self, profile, thread_id):
        return RuntimeSession(thread_id=thread_id, profile_id=profile.id)

    async def run_turn(self, session, message):
        return RuntimeTurn(
            thread_id=session.thread_id,
            text="已生成。",
            input_tokens=12,
            output_tokens=8,
            latency_ms=34,
            mcp_calls=(
                {"server": "platform", "tool": "enterprise_config_get", "input_summary": None, "output_summary": "brand", "status": "completed", "duration_ms": 1, "error": None},
                {"server": "platform", "tool": "knowledge_search", "input_summary": "秋季", "output_summary": "course", "status": "completed", "duration_ms": 1, "error": None},
                {"server": "platform", "tool": "asset_search", "input_summary": "logo", "output_summary": "logo", "status": "completed", "duration_ms": 1, "error": None},
                {"server": "platform", "tool": "image_generation", "input_summary": "poster prompt", "output_summary": "image", "status": "completed", "duration_ms": 5, "error": None},
            ),
        )


def test_run_trace_records_only_tool_observations(tmp_path, monkeypatch):
    monkeypatch.setenv("ENTERPRISE_POC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENTERPRISE_POC_DATABASE_URL", f"sqlite:///{tmp_path / 'poc.db'}")
    store = POCStore(tmp_path / "poc.db")
    store.seed_demo_data()
    service = AgentService(store, FakeRuntime(), Settings.from_env())

    result = asyncio.run(service.run("tenant-a", "image-agent", "帮我做一张秋季招生海报"))
    trace = store.run_trace(result.run_id, "tenant-a")
    assert trace and trace["status"] == "completed"
    assert trace["payload"]["skill_version"] == "1.0.0"
    assert [item["tool"] for item in trace["payload"]["mcp_calls"]] == [
        "enterprise_config_get", "knowledge_search", "asset_search", "image_generation"
    ]
    assert "reasoning" not in trace["payload"]
