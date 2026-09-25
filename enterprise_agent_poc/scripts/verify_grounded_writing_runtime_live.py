"""Opt-in isolated DeepSeek V4 Pro Grounding acceptance; emits metadata only.

Run only with GROUNDED_LIVE=1. The script never contacts Production storage,
never prints prompts, articles, evidence, or credentials, and deletes only its
own marked temporary SQLite/MCP runtime directory.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


PROJECT = Path(__file__).resolve().parents[1]
ROOT_ENV = PROJECT.parent / ".env"
sys.path.insert(0, str(PROJECT))


def _load_key() -> None:
    if os.environ.get("GROUNDED_LIVE") != "1":
        raise RuntimeError("Explicit GROUNDED_LIVE=1 required")
    for line in ROOT_ENV.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*DEEPSEEK_API_KEY\s*=\s*(.+?)\s*$", line)
        if match:
            value = match.group(1).strip().strip('"\'')
            if value:
                os.environ["DEEPSEEK_API_KEY"] = value
                return
    raise RuntimeError("DEEPSEEK_API_KEY unavailable")


async def _run() -> None:
    _load_key()
    isolated = Path(tempfile.mkdtemp(prefix="grounded-live-"))
    (isolated / "grounded-isolated.marker").write_text("synthetic-only", encoding="utf-8")
    server = None
    manager = None
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        os.environ.update({
            "APP_ENV": "test",
            "ENTERPRISE_POC_DATA_DIR": str(isolated / "data"),
            "ENTERPRISE_POC_DATABASE_URL": f"sqlite:///{isolated / 'product.db'}",
            "ENTERPRISE_POC_OBJECT_STORAGE_DIR": str(isolated / "objects"),
            "ENTERPRISE_POC_MCP_URL": f"http://127.0.0.1:{port}/mcp",
            "ENTERPRISE_POC_MCP_PORT": str(port),
            "ENTERPRISE_POC_TASK_QUEUE": "local",
            "ENTERPRISE_POC_MODEL_PROVIDER_ID": "deepseek",
            "ENTERPRISE_POC_MODEL_ID": "deepseek-v4-pro",
            "ENTERPRISE_POC_REASONING_EFFORT": "high",
        })
        from app.agent_execution import ExecutionResolver
        from app.agent_productization import AgentProductization
        from app.product_service import TaskService
        from app.product_store import ProductStore
        from app.runtime.codex_provider import CodexRuntimeManager, CodexRuntimeProvider
        from app.security import RuntimeTokenIssuer
        from app.service import AgentService
        from app.settings import Settings
        from app.skill_registry import SkillRegistry
        from app.skills import SkillDeployment
        from app.store import POCStore

        settings = Settings.from_env()
        store = POCStore(settings.database_url)
        store.seed_demo_data()
        if store.enterprise_config("tenant-a").get("data_classification") != "synthetic_gate2_test_data":
            raise RuntimeError("Live check requires synthetic tenant data")
        product = ProductStore(store)
        product.initialize()
        product.create_user("tenant-a", "grounded-live@tenant-a.test", "unused", "Grounding Test", "enterprise_admin")
        actor = product.user_by_email("grounded-live@tenant-a.test")["id"]
        registry = SkillRegistry(store, isolated / "registry", PROJECT / "skill_packages")
        registry.initialize()
        registry.grant_platform_admin(actor)
        control = AgentProductization(store)
        control.initialize()
        with store.connection() as conn:
            conn.execute("UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,advanced_at=CURRENT_TIMESTAMP,advanced_by_release_id='isolated-grounding',advanced_by_source_commit=?,advance_origin='controlled_advance' WHERE scope='agent_data_contract'", ("f" * 40,))
            skill = dict(conn.execute("SELECT v.skill_id,v.id AS skill_version_id FROM skill_versions v JOIN skills s ON s.id=v.skill_id WHERE v.status='published' AND s.slug='event-copywriting' AND v.version='1.0.0'").fetchone())
        resolver = ExecutionResolver(store, registry, control, settings, "tenant-a")
        resolver.initialize_local()
        control.execution_resolver = resolver
        product.execution_resolver = resolver
        template = control.create_template({"slug": "grounded-live-synthetic", "name": "Grounded Live Synthetic"}, actor)
        template_id = template["id"]
        revision = control.create_version(template_id, {"persona": "你是基于给定材料写作的助手。严格执行 Runtime 的内部结构化生成要求。", "grounding_policy": {"enabled": True, "mode": "claim_audit_v1", "max_corrections": 1}}, actor)
        revision_id = revision["versions"][0]["id"]
        control.bind_skills(template_id, revision_id, [skill], actor)
        with store.connection() as conn:
            conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id,overrides_json) VALUES ('tenant-a',?,'configured',?,?,'{}')", (template_id, str(uuid4()), revision_id))

        server = subprocess.Popen([sys.executable, "-m", "app.platform_mcp.server"], cwd=PROJECT,
                                  env={**os.environ, "PYTHONPATH": str(PROJECT)},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if server.poll() is not None:
                raise RuntimeError("Isolated MCP server exited")
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.2)
        else:
            raise RuntimeError("Isolated MCP server did not start")
        manager = CodexRuntimeManager(settings, SkillDeployment(PROJECT / "skill_packages"), RuntimeTokenIssuer(settings.token_secret))
        agents = AgentService(store, CodexRuntimeProvider(manager), settings)
        service = TaskService(product, agents)
        cases = [{"id": "smoke", "prompt": "请根据以下已确认材料写一段简短的社区阅读活动文案：社区计划举办阅读活动，时间和地点尚未确定。不要编造参与人数、活动成效或后续安排。", "forbidden": ["9月12日", "百人参与"]}]
        cases_path = os.environ.get("GROUNDED_LIVE_CASES_PATH")
        if cases_path:
            loaded = json.loads(Path(cases_path).read_text(encoding="utf-8"))
            if not isinstance(loaded, list) or any(set(item) != {"id", "prompt", "forbidden"} for item in loaded):
                raise RuntimeError("Invalid isolated case fixture")
            cases = loaded
        selected = os.environ.get("GROUNDED_LIVE_CASE_ID")
        if selected:
            cases = [case for case in cases if case["id"] == selected]
            if len(cases) != 1:
                raise RuntimeError("Requested isolated case not found")
        failed_cases = []
        for case in cases:
            task = product.create_task("tenant-a", actor, template_id, case["prompt"], None,
                                       _test_revision=revision_id, _test_id=str(uuid4()))
            await asyncio.wait_for(service.execute(task), timeout=360)
            saved = product.task_for_worker(task["id"])
            trace = store.run_trace(saved["run_id"], "tenant-a") if saved.get("run_id") else None
            payload = trace["payload"] if trace else {}
            final = payload.get("final_result") or ""
            events = product.task_events_since(task["id"], "tenant-a", actor)
            deltas = [json.loads(event["message"])["text"] for event in events if event["stage"] == "delta"]
            check = {
                "id": case["id"], "status": saved["status"], "model": "deepseek-v4-pro",
                "reasoning_effort": "high", "grounding": payload.get("grounding"),
                "delta_count": len(deltas), "reconstruction_exact": "".join(deltas) == final,
                "forbidden_visible": any(term in final for term in case["forbidden"]),
                "final_sha256": hashlib.sha256(final.encode()).hexdigest() if final else None,
                "error_code": saved.get("error_code"),
            }
            print(json.dumps(check, ensure_ascii=False), flush=True)
            if (check["status"] != "completed" or not payload.get("grounding", {}).get("final_pass")
                    or not check["reconstruction_exact"] or check["forbidden_visible"]):
                failed_cases.append(case["id"])
        if failed_cases:
            raise RuntimeError("Isolated grounded live gate failed: " + ", ".join(failed_cases))
    finally:
        if manager is not None:
            await manager.close()
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        temporary_root = Path(tempfile.gettempdir()).resolve()
        if (not isolated.resolve().is_relative_to(temporary_root)
                or not isolated.name.startswith("grounded-live-")
                or (isolated / "grounded-isolated.marker").read_text(encoding="utf-8") != "synthetic-only"):
            raise RuntimeError("Unexpected isolated directory; refusing cleanup")
        shutil.rmtree(isolated)
        os.environ.pop("DEEPSEEK_API_KEY", None)


if __name__ == "__main__":
    asyncio.run(_run())
