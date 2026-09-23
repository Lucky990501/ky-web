"""Isolated live DeepSeek acceptance check; prints no prompts, content or secrets."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT = Path(__file__).resolve().parents[1]
ROOT_ENV = PROJECT.parent / ".env"
sys.path.insert(0, str(PROJECT))


def _load_key() -> None:
    for line in ROOT_ENV.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*DEEPSEEK_API_KEY\s*=\s*(.+?)\s*$", line)
        if match:
            value = match.group(1).strip().strip('"\'')
            if not value:
                raise RuntimeError("DEEPSEEK_API_KEY is empty")
            os.environ["DEEPSEEK_API_KEY"] = value
            return
    raise RuntimeError("DEEPSEEK_API_KEY unavailable")


async def _run() -> None:
    _load_key()
    isolated = Path(tempfile.mkdtemp(prefix="activity-plan-live-"))
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
        from app.agent_catalog import CATALOG
        from app.product_service import TaskService
        from app.product_store import ProductStore
        from app.runtime.codex_provider import CodexRuntimeManager, CodexRuntimeProvider
        from app.security import RuntimeTokenIssuer
        from app.service import AgentService
        from app.settings import Settings
        from app.skills import SkillDeployment
        from app.store import POCStore
        from app.activity_plan_runtime import is_full_activity_plan

        if not Path(sys.modules["app"].__file__).resolve().is_relative_to(PROJECT):
            raise RuntimeError("live verification imported the wrong source tree")

        settings = Settings.from_env()
        store = POCStore(settings.database_url)
        store.seed_demo_data()
        if store.enterprise_config("tenant-a").get("data_classification") != "synthetic_gate2_test_data":
            raise RuntimeError("live verification requires synthetic tenant data")
        product = ProductStore(store)
        product.initialize()
        from app.auth import hash_password
        product.create_user("tenant-a", "live-bridge@tenant-a.test", hash_password("TemporaryLocal!2026"), "联调成员", "member")
        member = product.user_by_email("live-bridge@tenant-a.test")
        server = subprocess.Popen(
            [sys.executable, "-m", "app.platform_mcp.server"], cwd=PROJECT,
            env={**os.environ, "PYTHONPATH": str(PROJECT)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        manager = CodexRuntimeManager(settings, SkillDeployment(PROJECT / "skill_packages"), RuntimeTokenIssuer(settings.token_secret))
        try:
            for _ in range(50):
                if server.poll() is not None:
                    raise RuntimeError("isolated MCP server exited")
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.2)
            else:
                raise RuntimeError("isolated MCP server did not start")
            runtime = CodexRuntimeProvider(manager)
            agents = AgentService(store, runtime, settings, lambda agent_id: CATALOG[agent_id].skill_manifest)
            service = TaskService(product, agents)
            prompt = "请为社区教师节设计完整活动方案，包含主题、时间、地点、对象、宣发、执行表和邀约文案；未知事实保留待确认。"
            if not is_full_activity_plan("campaign-agent", prompt):
                raise RuntimeError("full-plan request was not classified as structured")
            task = product.create_task(
                "tenant-a", member["id"], "campaign-agent",
                prompt,
                None,
            )
            await asyncio.wait_for(service.execute(task), timeout=240)
            saved = product.task(task["id"], "tenant-a", member["id"])
            trace = store.run_trace(saved["run_id"], "tenant-a") if saved.get("run_id") else None
            result = saved.get("structured_result")
            from app import main
            main.product_store = product
            from app.auth import SessionIssuer
            main.sessions = SessionIssuer(settings.token_secret)
            token = main.sessions.issue(main.UserPrincipal(
                member["id"], "tenant-a", "member", main.sessions.credential_version(member["password_hash"])
            ))
            client = TestClient(main.app)
            try:
                client.cookies.set("workbench_session", token)
                with client.stream("GET", f"/api/v1/tasks/{task['id']}/events") as stream:
                    body = "".join(stream.iter_text())
                terminal = json.loads(body.split("event: complete\ndata: ", 1)[1].split("\n\n", 1)[0]) if "event: complete" in body else {}
                docx_ok = False
                if result:
                    response = client.post("/api/v1/documents/activity-plan", json={"content": result["data"]})
                    if response.status_code == 201:
                        docx_ok = client.get(response.json()["download_url"]).content.startswith(b"PK")
            finally:
                client.close()
            summary = {
                "model": "deepseek-v4-pro", "reasoning_effort": "high",
                "status": saved["status"], "structured_result": bool(result),
                "structured_sha256": hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest() if result else None,
                "assistant_message_match": terminal.get("assistant_message_id") == saved.get("assistant_message_id"),
                "sse_structured_match": terminal.get("structured_result") == result,
                "docx_ok": docx_ok,
                "diagnostic": (trace or {}).get("payload", {}).get("structured_result_diagnostic"),
                "task_error_code": saved.get("error_code"),
            }
            print(json.dumps(summary, ensure_ascii=False))
            if (saved["status"] != "completed" or not result or not docx_ok
                    or not summary["assistant_message_match"] or not summary["sse_structured_match"]
                    or summary["diagnostic"] is not None):
                raise RuntimeError("live bridge verification failed")
        finally:
            await manager.close()
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
    finally:
        import shutil
        temporary_root = Path(tempfile.gettempdir()).resolve()
        if not isolated.resolve().is_relative_to(temporary_root) or not isolated.name.startswith("activity-plan-live-"):
            raise RuntimeError("unexpected isolated directory; refusing cleanup")
        shutil.rmtree(isolated)
        os.environ.pop("DEEPSEEK_API_KEY", None)


if __name__ == "__main__":
    asyncio.run(_run())
